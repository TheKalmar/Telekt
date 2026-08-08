from pathlib import Path

from digital_company.models import ActionType, TaskProposal
from digital_company.orchestrator import CompanyOrchestrator
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

    def execute(self, proposal, snapshot, artifact_context=None):
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
