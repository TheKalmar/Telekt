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
