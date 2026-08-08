from types import SimpleNamespace

from agents import ModelBehaviorError

from digital_company.agents import AgentEngine


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
    local_agent = SimpleNamespace(model=engine.local_model)
    calls = []

    def run_sync(agent, prompt, max_turns):
        calls.append(prompt)
        if len(calls) == 1:
            raise ModelBehaviorError("invalid JSON")
        return SimpleNamespace(final_output={"valid": True})

    monkeypatch.setattr("digital_company.agents.Runner.run_sync", run_sync)
    assert engine._run(local_agent, None, "original", "ceo") == {"valid": True}
    assert len(calls) == 2
    assert "failed schema validation" in calls[1]
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
