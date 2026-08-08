import asyncio

from digital_company import temporal_gateway, web
from digital_company.temporal_worker import _apply_result_state
from digital_company.temporal_workflow import CompanyLoopWorkflow, TASK_QUEUE, waits_for_external_signal


def test_company_workflow_signals_are_restartable():
    workflow = CompanyLoopWorkflow()

    asyncio.run(workflow.start("first_start"))
    assert workflow.state() == {
        "running": True, "paused": False, "shutdown": False, "cycles": 0,
        "wake_count": 1, "last_status": "idle", "last_wake_reason": "first_start",
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
    for status in ["waiting_for_approval", "waiting_for_human", "paused", "stopped", "error", "failed"]:
        assert waits_for_external_signal(status) is True
    assert waits_for_external_signal("cycle_limit_reached") is False
    assert waits_for_external_signal("approved_task_completed") is False


def test_temporal_gateway_is_optional_without_infrastructure(monkeypatch):
    monkeypatch.delenv("TEMPORAL_ADDRESS", raising=False)
    assert temporal_gateway.signal_company("company-1", "start", "test") is False


def test_v3_workflow_id_does_not_replay_pre_idempotency_history():
    assert temporal_gateway.workflow_id("abc") == "company-loop-v3-abc"
    assert TASK_QUEUE == "digital-company-v3"


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
    monkeypatch.setattr(web, "runtime_preflight", lambda value, force=False: {"ready": True, "blockers": []})
    monkeypatch.setattr(web, "signal_temporal", lambda *args: calls.append(args) or True)

    web.control("start")

    assert store.control == ("running", "Queued for autonomous worker")
    assert calls == [("company-1", "start", "operator_start")]
