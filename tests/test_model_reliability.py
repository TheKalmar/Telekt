from types import SimpleNamespace

from agents import AgentOutputSchema, ModelBehaviorError

from digital_company.agents import AgentEngine
from digital_company.models import (
    ActionType,
    CompanySnapshot,
    SpecialistResult,
    SpecialistResultDraft,
    TaskProposal,
    TaskProposalDraft,
)


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
        "model.started",
        "model.structured_output_error",
        "model.succeeded",
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


def test_cross_field_contract_error_is_repaired_with_precise_feedback(monkeypatch):
    events = []
    engine = engine_for_test(events)
    agent = SimpleNamespace(model=engine.local_model, output_type=TaskProposalDraft)
    invalid = TaskProposalDraft(
        action=ActionType.SAVE_CONTENT_DRAFT,
        title="Save WordPress draft",
        objective="Save the prepared draft",
        rationale="The content is ready",
        expected_evidence=["Unpublished draft"],
        specialist="growth",
    )
    valid = TaskProposalDraft(
        action=ActionType.CREATE_CONTENT_DRAFT,
        title="Create content draft",
        objective="Prepare a reviewable article",
        rationale="Research is ready",
        expected_evidence=["Markdown draft"],
        specialist="growth",
    )
    calls = []

    def run_sync(agent, prompt, max_turns):
        calls.append(prompt)
        return SimpleNamespace(final_output=invalid if len(calls) == 1 else valid)

    monkeypatch.setattr("digital_company.agents.Runner.run_sync", run_sync)
    result = engine._run(
        agent,
        None,
        "company state",
        "ceo",
        output_validator=engine._task_proposal_validator,
    )

    assert result.action == ActionType.CREATE_CONTENT_DRAFT
    assert len(calls) == 2
    assert "WordPress content operations require browser execution_mode" in calls[1]
    assert '"action":"save_content_draft"' in calls[1]
    assert any(
        event == "model.structured_output_error"
        and payload.get("category") == "application_contract"
        for event, payload in events
    )


def test_repair_attempts_have_distinct_usage_records(monkeypatch):
    events = []
    engine = engine_for_test(events)
    agent = SimpleNamespace(model=engine.local_model, output_type=TaskProposalDraft)
    invalid = TaskProposalDraft(
        action=ActionType.SAVE_CONTENT_DRAFT,
        title="Save WordPress draft",
        objective="Save the prepared draft",
        rationale="The content is ready",
        expected_evidence=["Unpublished draft"],
        specialist="growth",
    )
    valid = TaskProposalDraft(
        action=ActionType.CREATE_CONTENT_DRAFT,
        title="Create content draft",
        objective="Prepare a reviewable article",
        rationale="Research is ready",
        expected_evidence=["Markdown draft"],
        specialist="growth",
    )
    outputs = iter((invalid, valid))
    usage = SimpleNamespace(
        requests=1,
        input_tokens=10,
        output_tokens=5,
        total_tokens=15,
        input_tokens_details=None,
        output_tokens_details=None,
    )

    monkeypatch.setattr(
        "digital_company.agents.Runner.run_sync",
        lambda *args, **kwargs: SimpleNamespace(
            final_output=next(outputs),
            context_wrapper=SimpleNamespace(usage=usage),
        ),
    )
    engine._run(
        agent,
        None,
        "company state",
        "ceo",
        output_validator=engine._task_proposal_validator,
    )

    usage_events = [payload for event, payload in events if event == "model.usage"]
    assert len(usage_events) == 2
    assert usage_events[0]["run_id"].endswith(":attempt:1")
    assert usage_events[1]["run_id"].endswith(":attempt:2")
    assert usage_events[0]["parent_run_id"] == usage_events[1]["parent_run_id"]


def test_local_agents_disable_long_reasoning_by_default(monkeypatch):
    monkeypatch.delenv("LOCAL_THINKING", raising=False)
    monkeypatch.delenv("LOCAL_MAX_OUTPUT_TOKENS", raising=False)

    engine = AgentEngine(mode="local")

    assert engine.ceo.model_settings.extra_body == {"think": False}
    assert engine.ceo.model_settings.max_tokens == 768


def test_cloud_ceo_uses_grammar_safe_draft_schema():
    engine = AgentEngine(mode="cloud")

    assert engine.ceo.output_type is TaskProposalDraft


def test_specialists_use_strict_model_output_without_runtime_dicts():
    engine = AgentEngine(mode="cloud")

    for specialist in engine.specialists.values():
        assert specialist.output_type is SpecialistResultDraft
        assert AgentOutputSchema(specialist.output_type).is_strict_json_schema() is True
    promoted = engine._specialist_result_validator(
        SpecialistResultDraft(
            status="completed",
            summary="Done",
            evidence=["Verified"],
            recommendation="Continue",
        )
    )
    assert isinstance(promoted, SpecialistResult)
    assert promoted.quality_report is None


def test_ceo_context_uses_compact_skill_routing_metadata(monkeypatch):
    engine = AgentEngine.__new__(AgentEngine)
    engine.ceo = object()
    engine.ceo_fallback = None
    captured = {}
    expected = TaskProposal(
        action="research_market",
        title="Research market",
        objective="Find demand",
        rationale="Need evidence",
        expected_evidence=["Sources"],
        specialist="research",
    )

    def capture_run(agent, fallback, prompt, role, output_validator=None):
        captured["prompt"] = prompt
        captured["role"] = role
        return output_validator(expected) if output_validator else expected

    engine._run = capture_run
    snapshot = CompanySnapshot(
        goal="Test",
        initial_budget_eur=10,
        spent_eur=0,
        remaining_budget_eur=10,
        completed_tasks=[],
        pending_approvals=[],
        recent_evidence=[],
        skills=[
            {
                "id": "market",
                "name": "Market",
                "roles": ["research"],
                "actions": ["research_market"],
                "status": "available",
                "instructions": "very long instructions that CEO does not need",
            }
        ],
    )

    engine.decide(snapshot)

    assert "very long instructions" not in captured["prompt"]
    assert '"id":"market"' in captured["prompt"]
    assert captured["role"] == "ceo"

    engine.decide(snapshot, {"id": "agent-1", "name": "Content & SEO"})
    assert captured["role"] == "planner"


def test_adapter_model_id_is_used_for_usage_accounting():
    adapter = SimpleNamespace(model="configured-model-id")
    agent = SimpleNamespace(model=adapter)
    assert AgentEngine._model_id(agent) == "configured-model-id"
