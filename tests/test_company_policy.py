from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from digital_company.models import ActionType, PolicyDocument, TaskProposal
from digital_company.policy import Governor
from digital_company.store import CompanyStore


def proposal(action: ActionType, cost: float = 0) -> TaskProposal:
    return TaskProposal(
        action=action,
        title="Governed company action",
        objective="Exercise the active company policy",
        rationale="The deterministic policy must decide before execution",
        expected_evidence=["Policy result"],
        estimated_cost_eur=cost,
        specialist="research",
    )


def test_policy_versions_are_immutable_and_contract_ban_cannot_be_removed(tmp_path: Path):
    store = CompanyStore(tmp_path / "company.db")
    assert store.schema_version() == 12
    initial = store.get_policy()

    updated = store.set_policy(
        {
            **initial["document"],
            "deny_actions": [],
            "approval_quorum": 2,
        },
        "test-owner",
    )

    assert initial["version"] == 1
    assert updated["version"] == 2
    assert updated["document"]["approval_quorum"] == 2
    assert ActionType.SIGN_CONTRACT.value in updated["document"]["deny_actions"]
    old = store.db.execute("SELECT status FROM policy_versions WHERE version=1").fetchone()
    assert old["status"] == "superseded"


def test_policy_can_allow_small_spend_but_never_overspend_or_sign():
    governor = Governor(PolicyDocument(autonomous_spend_limit_eur=25))

    assert governor.evaluate(proposal(ActionType.SPEND_MONEY, 20), 100).outcome == "allow"
    assert (
        governor.evaluate(proposal(ActionType.SPEND_MONEY, 30), 100).outcome == "require_approval"
    )
    assert governor.evaluate(proposal(ActionType.SPEND_MONEY, 20), 10).outcome == "deny"
    assert governor.evaluate(proposal(ActionType.SIGN_CONTRACT), 100).outcome == "deny"


def test_distinct_approvers_must_reach_quorum(tmp_path: Path):
    store = CompanyStore(tmp_path / "company.db")
    store.initialize("Test approvals", 100)
    task = proposal(ActionType.EXTERNAL_OUTREACH)
    task_id = store.create_task(task, "proposed")
    approval_id = store.request_approval(
        task_id,
        task,
        "External action",
        required_approvals=2,
        ttl_hours=24,
    )

    first = store.approve(approval_id, "Looks good", "alice@example.com")
    assert first == {"status": "pending", "approval_count": 1, "required_approvals": 2}
    assert store.claim_approved_task() is None
    with pytest.raises(RuntimeError, match="already voted"):
        store.approve(approval_id, "Again", "ALICE@example.com")

    second = store.approve(approval_id, "Approved", "bob@example.com")
    assert second["status"] == "approved"
    assert store.claim_approved_task()[0] == task_id


def test_expired_approval_cannot_release_task(tmp_path: Path):
    store = CompanyStore(tmp_path / "company.db")
    store.initialize("Test expiry", 100)
    task = proposal(ActionType.EXTERNAL_OUTREACH)
    task_id = store.create_task(task, "proposed")
    approval_id = store.request_approval(task_id, task, "External action", ttl_hours=1)
    expired = (datetime.now(UTC) - timedelta(seconds=1)).isoformat()
    store.db.execute("UPDATE approvals SET expires_at=? WHERE id=?", (expired, approval_id))
    store.db.commit()

    with pytest.raises(RuntimeError, match="resolved"):
        store.approve(approval_id, decided_by="alice@example.com")
    approval = store.list_approvals()[0]
    assert approval["status"] == "expired"
    task_row = store.db.execute("SELECT status FROM tasks WHERE id=?", (task_id,)).fetchone()
    assert task_row["status"] == "rejected"


def test_concurrent_distinct_votes_release_exactly_once(tmp_path: Path):
    path = tmp_path / "company.db"
    with CompanyStore(path) as store:
        store.initialize("Test concurrent quorum", 100)
        task = proposal(ActionType.EXTERNAL_OUTREACH)
        task_id = store.create_task(task, "proposed")
        approval_id = store.request_approval(
            task_id,
            task,
            "External action",
            required_approvals=2,
        )

    def vote(voter: str) -> dict:
        with CompanyStore(path) as thread_store:
            return thread_store.approve(approval_id, decided_by=voter)

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(vote, ["alice@example.com", "bob@example.com"]))

    with CompanyStore(path) as store:
        approval = store.list_approvals()[0]
        assert approval["status"] == "approved"
        assert approval["approval_count"] == 2
        assert store.claim_approved_task()[0] == task_id
        assert store.claim_approved_task() is None
    assert sorted(result["status"] for result in results) == ["approved", "pending"]
