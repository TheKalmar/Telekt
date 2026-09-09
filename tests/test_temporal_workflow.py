import asyncio

from digital_company import temporal_gateway, web
from digital_company.temporal_worker import _apply_result_state
from digital_company.temporal_workflow import (
    TASK_QUEUE,
    AgentLoopWorkflow,
    CompanyLoopWorkflow,
    agent_execution_key,
    waits_for_external_signal,
)


def test_company_workflow_signals_are_restartable():
    workflow = CompanyLoopWorkflow()

    asyncio.run(workflow.start("first_start"))
    assert workflow.state() == {
        "running": True,
        "paused": False,
        "shutdown": False,
        "cycles": 0,
        "wake_count": 1,
        "last_status": "idle",
        "last_wake_reason": "first_start",
    }

    asyncio.run(workflow.pause("owner_pause"))
    assert workflow.state()["paused"] is True
    assert workflow.state()["running"] is False

    asyncio.run(workflow.resume("owner_resume"))
    assert workflow.state()["running"] is True
    assert workflow.state()["wake_count"] == 2

    asyncio.run(workflow.stop("owner_stop"))
    assert workflow.state()["last_status"] == "stopped"
    assert workflow.state()["shutdown"] is False

    asyncio.run(workflow.start("restart_after_stop"))
    assert workflow.state()["running"] is True
    assert workflow.state()["wake_count"] == 3


def test_waiting_results_require_a_signal_but_completed_cycle_does_not():
    for status in [
        "waiting_for_approval",
        "waiting_for_human",
        "paused",
        "stopped",
        "error",
        "failed",
    ]:
        assert waits_for_external_signal(status) is True
    assert waits_for_external_signal("cycle_limit_reached") is False
    assert waits_for_external_signal("approved_task_completed") is False


def test_temporal_gateway_is_optional_without_infrastructure(monkeypatch):
    monkeypatch.delenv("TEMPORAL_ADDRESS", raising=False)
    assert temporal_gateway.signal_company("company-1", "start", "test") is False
    assert temporal_gateway.signal_agent("company-1", "agent-1", "start", "test") is False


def test_workflow_ids_do_not_replay_incompatible_history():
    assert temporal_gateway.workflow_id("abc") == "company-loop-v4-abc"
    assert TASK_QUEUE == "digital-company-v4"
    assert temporal_gateway.agent_workflow_id("abc", "writer") == "agent-loop-v3-abc-writer"
    assert temporal_gateway.legacy_agent_workflow_id("abc", "writer") == "agent-loop-v2-abc-writer"


def test_agent_activity_keys_are_namespaced_without_breaking_v2_replay():
    legacy = agent_execution_key("company", "writer", 1)
    current = agent_execution_key("company", "writer", 1, "v3")

    assert legacy == "company:agent:writer:execution:1"
    assert current == "company:agent:writer:v3:execution:1"
    assert current != legacy


def test_agent_gateway_starts_v3_namespace_and_retires_v2():
    class Handle:
        def __init__(self, workflow_id):
            self.workflow_id = workflow_id
            self.signals = []

        async def signal(self, name, reason):
            self.signals.append((name, reason))

    class Client:
        def __init__(self):
            self.starts = []
            self.handles = {}

        async def start_workflow(self, run, payload, **options):
            self.starts.append((run, payload, options))

        def get_workflow_handle(self, workflow_id):
            return self.handles.setdefault(workflow_id, Handle(workflow_id))

    client = Client()
    handle = asyncio.run(temporal_gateway.ensure_agent_workflow(client, "company", "writer"))

    assert handle.workflow_id == "agent-loop-v3-company-writer"
    assert client.starts[0][1]["execution_namespace"] == "v3"
    assert client.starts[0][2]["id"] == "agent-loop-v3-company-writer"
    assert client.handles["agent-loop-v2-company-writer"].signals == [
        ("shutdown", "superseded_by_agent_loop_v3"),
    ]


def test_agent_workflow_has_independent_restartable_lifecycle():
    workflow = AgentLoopWorkflow()
    asyncio.run(workflow.start("content_start"))
    assert workflow.state()["running"] is True
    asyncio.run(workflow.pause("review"))
    assert workflow.state()["paused"] is True
    asyncio.run(workflow.resume("continue"))
    assert workflow.state()["running"] is True
    asyncio.run(workflow.stop("owner_stop"))
    assert workflow.state()["last_status"] == "stopped"
    assert workflow.state()["next_wake_at"] is None


class FakeStore:
    def __init__(self):
        self.control = None

    def set_control(self, state, detail=None):
        self.control = (state, detail)

    def get_control(self):
        return {"state": self.control[0], "detail": self.control[1]}


def test_activity_exit_state_projection():
    store = FakeStore()
    _apply_result_state(store, {"status": "waiting_for_approval"})
    assert store.control == ("waiting_approval", "Human decision required")

    _apply_result_state(store, {"status": "waiting_for_human", "reason": "Login required"})
    assert store.control == ("waiting_human", "Login required")


def test_web_control_persists_then_signals_temporal(monkeypatch):
    store = FakeStore()
    calls = []
    monkeypatch.setattr(web.registry, "active_id", lambda: "company-1")
    monkeypatch.setattr(web, "get_store", lambda company_id=None: store)
    monkeypatch.setattr(
        web, "runtime_preflight", lambda value, force=False: {"ready": True, "blockers": []}
    )
    monkeypatch.setattr(web, "signal_temporal", lambda *args: calls.append(args) or True)

    web.control("start")

    assert store.control == ("running", "Queued for autonomous worker")
    assert calls == [("company-1", "start", "operator_start")]


class RecoveryStore(FakeStore):
    def __init__(self):
        super().__init__()
        self.control = ("error", "provider timeout")
        self.events = []
        self.closed_model_runs = []

    def close_orphaned_model_runs(self, reason):
        self.closed_model_runs.append(reason)

    def audit(self, event, payload):
        self.events.append((event, payload))


def test_web_recovery_resumes_from_committed_state(monkeypatch):
    store = RecoveryStore()
    calls = []
    monkeypatch.setattr(web.registry, "active_id", lambda: "company-1")
    monkeypatch.setattr(web, "get_store", lambda company_id=None: store)
    monkeypatch.setattr(
        web,
        "runtime_preflight",
        lambda value, force=False: {
            "ready": True,
            "blockers": [],
        },
    )
    monkeypatch.setattr(web, "signal_temporal", lambda *args: calls.append(args) or True)

    result = web.retry_from_checkpoint()

    assert result == {"status": "running", "strategy": "resume_from_committed_state"}
    assert store.control == ("running", "Recovery queued from last committed checkpoint")
    assert calls == [("company-1", "start", "operator_recovery_retry")]
    assert store.events == [
        (
            "recovery.operator_retry_requested",
            {"strategy": "resume_from_committed_state"},
        )
    ]
