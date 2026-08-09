from pathlib import Path

from fastapi.testclient import TestClient

from digital_company import web
from digital_company.models import ActionType, TaskProposal
from digital_company.orchestrator import CompanyOrchestrator
from digital_company.registry import CompanyRegistry
from digital_company.store import CompanyStore


def handoff_proposal() -> TaskProposal:
    return TaskProposal(
        action=ActionType.REQUEST_HUMAN_HANDOFF,
        title="Complete Upwork login checkpoint",
        objective="Enable contractor shortlist research",
        rationale="Authentication and CAPTCHA require account-owner presence",
        expected_evidence=["Authenticated account confirmed"],
        specialist="operations",
        execution_mode="manual",
        handoff_url="https://www.upwork.com/ab/account-security/login",
        handoff_instructions=["Open the URL", "Sign in", "Complete CAPTCHA or 2FA if shown"],
        resume_evidence=["Confirm login succeeded", "Report the visible workspace name"],
    )


class HandoffEngine:
    def decide(self, snapshot):
        return handoff_proposal()

    def execute(self, proposal, snapshot, artifact_context=None, skill_context=None):
        raise AssertionError("A human handoff must not execute a specialist")


def test_handoff_pauses_then_resumes_with_human_evidence(tmp_path: Path):
    store = CompanyStore(tmp_path / "company.db")
    store.initialize("Find a UI specialist", 1000, {"company_type": "SaaS"})
    store.set_control("running", "Test run")

    result = CompanyOrchestrator(
        store, tmp_path / "artifacts", engine=HandoffEngine()
    ).run(max_cycles=1)

    assert result["status"] == "waiting_for_human"
    handoff = store.list_handoffs("pending")[0]
    assert store.get_control()["state"] == "waiting_human"
    store.resolve_handoff(handoff["id"], "Logged in; workspace is Telekt", completed=True)

    assert store.get_control()["state"] == "running"
    assert store.list_handoffs()[0]["status"] == "completed"
    assert any("Logged in" in item["content"] for item in store.snapshot().stakeholder_messages)


def test_handoff_requires_precise_resume_contract():
    try:
        TaskProposal(
            action=ActionType.REQUEST_HUMAN_HANDOFF,
            title="Help with login", objective="Get access", rationale="Blocked",
            expected_evidence=["Access"], specialist="operations", execution_mode="manual",
        )
    except ValueError as exc:
        assert "instructions" in str(exc)
    else:
        raise AssertionError("Vague handoff must be rejected")


def test_stakeholder_directive_supersedes_pending_handoff(tmp_path: Path):
    store = CompanyStore(tmp_path / "company.db")
    store.initialize("Find a UI specialist", 1000)
    task_id = store.create_task(handoff_proposal(), "proposed")
    store.create_handoff(task_id, handoff_proposal())

    store.add_stakeholder_message("Do not use Upwork; evaluate an agency instead", "directive")

    assert store.list_handoffs()[0]["status"] == "superseded"
    task = store.db.execute("SELECT status FROM tasks WHERE id=?", (task_id,)).fetchone()
    assert task["status"] == "superseded"


def test_guided_browser_opens_the_frozen_pending_handoff(tmp_path: Path, monkeypatch):
    registry = CompanyRegistry(tmp_path / ".company")
    company = registry.create({
        "name": "Browser Test", "company_type": "SaaS", "concept": "Test handoffs",
        "description": "", "goal": "Test", "budget": 1000, "currency": "EUR",
        "target_market": "B2B", "customer_type": "B2B", "time_horizon_days": 30,
        "risk_tolerance": "medium", "autonomy_level": "balanced", "constraints": [],
        "success_criteria": ["Guided takeover"],
    })
    store = registry.store_for(company["id"])
    proposal = handoff_proposal().model_copy(update={
        "handoff_allowed_domains": ["accounts.google.com"],
    })
    task_id = store.create_task(proposal, "proposed")
    handoff_id = store.create_handoff(task_id, proposal)
    calls = []

    def fake_browser(method, path, payload=None, timeout=40):
        calls.append((method, path, payload, timeout))
        return b'{"status":"open","url":"https://www.upwork.com/ab/account-security/login"}', "application/json"

    monkeypatch.setattr(web, "registry", registry)
    monkeypatch.setattr(web, "browser_runtime_request", fake_browser)
    response = TestClient(web.app).post(f"/api/handoffs/{handoff_id}/browser")

    assert response.status_code == 200
    assert calls[0][0:2] == ("PUT", f"/sessions/{company['id']}")
    assert calls[0][2] == {
        "url": proposal.handoff_url,
        "allowed_domains": ["www.upwork.com", "accounts.google.com"],
    }
    assert any(
        row["event_type"] == "handoff.browser_opened"
        for row in store.db.execute("SELECT event_type FROM audit_events")
    )


def test_guided_browser_rejects_a_resolved_handoff(tmp_path: Path, monkeypatch):
    registry = CompanyRegistry(tmp_path / ".company")
    company = registry.create({
        "name": "Browser Test", "company_type": "SaaS", "concept": "Test handoffs",
        "description": "", "goal": "Test", "budget": 1000, "currency": "EUR",
        "target_market": "B2B", "customer_type": "B2B", "time_horizon_days": 30,
        "risk_tolerance": "medium", "autonomy_level": "balanced", "constraints": [],
        "success_criteria": ["Guided takeover"],
    })
    store = registry.store_for(company["id"])
    proposal = handoff_proposal()
    task_id = store.create_task(proposal, "proposed")
    handoff_id = store.create_handoff(task_id, proposal)
    store.resolve_handoff(handoff_id, "Login completed", completed=True)
    monkeypatch.setattr(web, "registry", registry)

    response = TestClient(web.app).post(f"/api/handoffs/{handoff_id}/browser")

    assert response.status_code == 404
    assert response.json()["detail"] == "Pending handoff not found"
