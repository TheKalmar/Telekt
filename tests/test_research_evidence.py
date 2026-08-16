from types import SimpleNamespace

from digital_company.agents import AgentEngine
from digital_company.models import ActionType, CompanySnapshot, SpecialistResult, TaskProposal


def proposal():
    return TaskProposal(
        action=ActionType.RESEARCH_MARKET,
        title="Research a verifiable market problem",
        objective="Find evidence of a recurring B2B problem",
        rationale="Evidence is required before product work",
        expected_evidence=["Primary sources", "Competitor pricing"],
        specialist="research",
    )


def snapshot():
    return CompanySnapshot(
        goal="Build an evidence-backed product", initial_budget_eur=1000,
        spent_eur=0, remaining_budget_eur=1000, completed_tasks=[],
        pending_approvals=[], recent_evidence=[],
    )


def content_proposal():
    return TaskProposal(
        action=ActionType.RESEARCH_CONTENT,
        title="Research the next distinct legal topic",
        objective="Choose a topic outside the durable queue",
        rationale="The active pipeline is below target",
        expected_evidence=["Official sources"],
        specialist="research",
    )


def content_snapshot():
    return CompanySnapshot(
        goal="Grow legal search visibility", initial_budget_eur=1000,
        spent_eur=0, remaining_budget_eur=1000, completed_tasks=[],
        pending_approvals=[], recent_evidence=[],
        work_queue=[{
            "id": "topic-1", "topic": "Povrat PDV-a na prvu nekretninu",
            "status": "draft_saved",
        }],
    )


def bare_engine(local: bool):
    engine = AgentEngine.__new__(AgentEngine)
    engine.local_model = object()
    selected_model = engine.local_model if local else "cloud-model"
    engine.specialists = {"research": SimpleNamespace(model=selected_model)}
    engine.specialist_fallbacks = {"research": None}
    return engine


def test_local_research_fails_closed_without_web_access():
    result = bare_engine(local=True).execute(proposal(), snapshot())
    assert result.status == "failed"
    assert result.sources == []
    assert "no web-search capability" in result.summary


def test_cloud_research_requires_two_verifiable_urls(monkeypatch):
    engine = bare_engine(local=False)
    monkeypatch.setattr(engine, "_run", lambda *_args: SpecialistResult(
        status="completed", summary="Market exists", evidence=["Unverified claim"],
        sources=["https://example.com/one"], recommendation="Build it",
    ))
    result = engine.execute(proposal(), snapshot())
    assert result.status == "failed"
    assert "fewer than two" in result.summary


def test_cloud_research_passes_with_distinct_sources(monkeypatch):
    engine = bare_engine(local=False)
    expected = SpecialistResult(
        status="completed", summary="Evidence found", evidence=["Two sources agree"],
        sources=["https://example.com/one", "https://vendor.example/pricing"],
        recommendation="Continue validation",
    )
    monkeypatch.setattr(engine, "_run", lambda *_args: expected)
    assert engine.execute(proposal(), snapshot()) == expected


def test_content_research_requires_a_canonical_topic(monkeypatch):
    engine = bare_engine(local=False)
    monkeypatch.setattr(engine, "_run", lambda *_args: SpecialistResult(
        status="completed", summary="Evidence found", evidence=["Verified"],
        sources=["https://example.com/one", "https://example.com/two"],
        recommendation="Continue",
    ))

    result = engine.execute(content_proposal(), content_snapshot())

    assert result.status == "failed"
    assert "researched_topic is missing" in result.summary


def test_content_research_rejects_an_existing_topic(monkeypatch):
    engine = bare_engine(local=False)
    monkeypatch.setattr(engine, "_run", lambda *_args: SpecialistResult(
        status="completed", summary="Repeated evidence", evidence=["Verified"],
        sources=["https://example.com/one", "https://example.com/two"],
        researched_topic="Povrat PDV-a na prvu nekretninu za kupce u Banjoj Luci i RS",
        recommendation="Continue",
    ))

    result = engine.execute(content_proposal(), content_snapshot())

    assert result.status == "failed"
    assert "duplicated an existing topic" in result.summary


def test_content_research_accepts_a_distinct_topic(monkeypatch):
    engine = bare_engine(local=False)
    expected = SpecialistResult(
        status="completed", summary="Distinct evidence", evidence=["Verified"],
        sources=["https://example.com/one", "https://example.com/two"],
        researched_topic="Nasljeđivanje digitalne imovine u BiH",
        recommendation="Create a draft",
    )
    monkeypatch.setattr(engine, "_run", lambda *_args: expected)

    assert engine.execute(content_proposal(), content_snapshot()) == expected


def test_content_discovery_prompt_omits_prior_completed_articles(monkeypatch):
    engine = bare_engine(local=False)
    captured = {}
    state = content_snapshot().model_copy(update={
        "completed_tasks": [{"title": "Large prior article", "result": {"summary": "old"}}],
        "recent_evidence": [{"evidence": "old article evidence"}],
    })
    expected = SpecialistResult(
        status="completed", summary="Fresh evidence", evidence=["Verified"],
        sources=["https://example.com/one", "https://example.com/two"],
        researched_topic="Nasljeđivanje digitalne imovine u BiH",
        recommendation="Create a draft",
    )

    def capture(_agent, _fallback, prompt, _role):
        captured["prompt"] = prompt
        return expected

    monkeypatch.setattr(engine, "_run", capture)

    assert engine.execute(content_proposal(), state) == expected
    assert '"completed_tasks": []' in captured["prompt"]
    assert "Large prior article" not in captured["prompt"]
    assert "old article evidence" not in captured["prompt"]
