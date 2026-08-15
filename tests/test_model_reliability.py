from types import SimpleNamespace

from agents import ModelBehaviorError

from digital_company.agents import AgentEngine
from digital_company.models import CompanySnapshot, TaskProposal


def engine_for_test(events, fallback=False):
    engine = AgentEngine.__new__(AgentEngine)
    engine.cloud_model = "cloud-model"
    engine.local_model = object()
    engine.allow_cloud_fallback = fallback
    engine.reporter = lambda event, payload: events.append((event, payload))
    engine.max_turns = 3
    engine.structured_retries = 1
    return engine


def test_invalid_structured_output_is_repaired_once(monkeypatch):
    events = []
    engine = engine_for_test(events)
    local_agent = SimpleNamespace(model=engine.local_model, output_type=TaskProposal)
    calls = []

    def run_sync(agent, prompt, max_turns):
        calls.append(prompt)
        if len(calls) == 1:
            raise ModelBehaviorError("invalid JSON")
        return SimpleNamespace(final_output={"valid": True})

    monkeypatch.setattr("digital_company.agents.Runner.run_sync", run_sync)
    assert engine._run(local_agent, None, "original", "ceo") == {"valid": True}
    assert len(calls) == 2
    assert calls[0] == "original"
    assert "STRUCTURED OUTPUT REPAIR" in calls[1]
    assert '"research_market"' in calls[1]
    assert "```" not in calls[1]
    assert [event for event, _ in events] == [
        "model.started", "model.structured_output_error", "model.succeeded",
    ]


def test_cloud_fallback_requires_explicit_setting_and_api_key(monkeypatch):
    events = []
    engine = engine_for_test(events, fallback=True)
    local_agent = SimpleNamespace(model=engine.local_model)
    cloud_agent = SimpleNamespace(model="cloud-model")
    monkeypatch.setenv("OPENAI_API_KEY", "configured-for-test")
    calls = []

    def run_sync(agent, prompt, max_turns):
        calls.append(agent)
        if agent is local_agent:
            raise ModelBehaviorError("invalid JSON")
        return SimpleNamespace(final_output="cloud result")

    monkeypatch.setattr("digital_company.agents.Runner.run_sync", run_sync)
    assert engine._run(local_agent, cloud_agent, "prompt", "research") == "cloud result"
    assert calls[-1] is cloud_agent
    assert any(event == "model.cloud_fallback" for event, _ in events)


def test_local_agents_disable_long_reasoning_by_default(monkeypatch):
    monkeypatch.delenv("LOCAL_THINKING", raising=False)
    monkeypatch.delenv("LOCAL_MAX_OUTPUT_TOKENS", raising=False)

    engine = AgentEngine(mode="local")

    assert engine.ceo.model_settings.extra_body == {"think": False}
    assert engine.ceo.model_settings.max_tokens == 768


def test_ceo_context_uses_compact_skill_routing_metadata(monkeypatch):
    engine = AgentEngine.__new__(AgentEngine)
    engine.ceo = object()
    engine.ceo_fallback = None
    captured = {}
    expected = TaskProposal(
        action="research_market", title="Research market", objective="Find demand",
        rationale="Need evidence", expected_evidence=["Sources"], specialist="research",
    )
    def capture_run(agent, fallback, prompt, role):
        captured["prompt"] = prompt
        return expected

    engine._run = capture_run
    snapshot = CompanySnapshot(
        goal="Test", initial_budget_eur=10, spent_eur=0, remaining_budget_eur=10,
        completed_tasks=[], pending_approvals=[], recent_evidence=[],
        skills=[{"id": "market", "name": "Market", "roles": ["research"],
                 "actions": ["research_market"], "status": "available",
                 "instructions": "very long instructions that CEO does not need"}],
    )

    engine.decide(snapshot)

    assert "very long instructions" not in captured["prompt"]
    assert '"id":"market"' in captured["prompt"]


def test_adapter_model_id_is_used_for_usage_accounting():
    adapter = SimpleNamespace(model="configured-model-id")
    agent = SimpleNamespace(model=adapter)
    assert AgentEngine._model_id(agent) == "configured-model-id"
