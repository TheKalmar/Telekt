from pathlib import Path

import pytest

from digital_company.models import ActionType, SpecialistResult, TaskProposal
from digital_company.orchestrator import CompanyOrchestrator
from digital_company.store import CompanyStore


def external_proposal() -> TaskProposal:
    return TaskProposal(
        action=ActionType.EXTERNAL_OUTREACH,
        title="Contact pilot customer",
        objective="Validate demand with one pilot customer",
        rationale="Internal evidence is sufficient for a controlled outreach",
        expected_evidence=["Customer response"],
        estimated_cost_eur=0,
        specialist="growth",
    )


class FrozenTaskEngine:
    def __init__(self):
        self.executed = []

    def decide(self, snapshot):
        raise AssertionError("CEO must not make a new decision after human approval")

    def execute(self, proposal, snapshot, artifact_context=None, skill_context=None):
        self.executed.append(proposal)
        return SpecialistResult(
            status="completed",
            summary="Prepared the exact approved pilot outreach",
            evidence=["Frozen approval payload executed"],
            recommendation="Review customer response",
        )


def test_approved_frozen_payload_executes_once_without_new_ceo_decision(tmp_path: Path):
    store = CompanyStore(tmp_path / "company.db")
    store.initialize("Find a profitable B2B product", 1000)
    proposal = external_proposal()
    task_id = store.create_task(proposal, "proposed")
    approval_id = store.request_approval(task_id, proposal, "Human approval required")
    store.set_control("waiting_approval", "Human decision required")

    store.approve(approval_id)
    assert store.get_control()["state"] == "running"

    engine = FrozenTaskEngine()
    result = CompanyOrchestrator(store, tmp_path / "artifacts", engine=engine).run(max_cycles=1)
    assert result["status"] == "approved_task_completed"
    assert engine.executed == [proposal]
    assert store.claim_approved_task() is None
    assert store.list_approvals()[0]["status"] == "executed"
    task = store.db.execute("SELECT status FROM tasks WHERE id=?", (task_id,)).fetchone()
    assert task["status"] == "completed"


def test_rejected_task_is_never_claimable_and_ceo_can_reconsider(tmp_path: Path):
    store = CompanyStore(tmp_path / "company.db")
    store.initialize("Find a profitable B2B product", 1000)
    proposal = external_proposal()
    task_id = store.create_task(proposal, "proposed")
    approval_id = store.request_approval(task_id, proposal, "Human approval required")
    store.set_control("waiting_approval", "Human decision required")

    store.reject(approval_id, "The target segment is too broad")
    assert store.claim_approved_task() is None
    assert store.get_control()["state"] == "running"
    task = store.db.execute("SELECT status FROM tasks WHERE id=?", (task_id,)).fetchone()
    assert task["status"] == "rejected"


def test_resolved_approval_cannot_be_used_twice(tmp_path: Path):
    store = CompanyStore(tmp_path / "company.db")
    store.initialize("Find a profitable B2B product", 1000)
    proposal = external_proposal()
    task_id = store.create_task(proposal, "proposed")
    approval_id = store.request_approval(task_id, proposal, "Human approval required")
    store.approve(approval_id)

    with pytest.raises(RuntimeError):
        store.approve(approval_id)
