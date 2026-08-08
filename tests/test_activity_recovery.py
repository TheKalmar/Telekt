from pathlib import Path

from digital_company.models import ActionType, SpecialistResult, TaskProposal
from digital_company.store import CompanyStore


def proposal() -> TaskProposal:
    return TaskProposal(
        action=ActionType.BUILD_MVP,
        title="Build recovery-safe MVP",
        objective="Create one bounded internal artifact",
        rationale="Prove activity recovery",
        expected_evidence=["Artifact"],
        estimated_cost_eur=12,
        specialist="development",
    )


def initialized_store(tmp_path: Path) -> CompanyStore:
    store = CompanyStore(tmp_path / "company.db")
    store.initialize("Build safely", 1000)
    return store


def test_completed_activity_returns_cached_result(tmp_path: Path):
    store = initialized_store(tmp_path)
    key = "company:execution:1"
    result = {"status": "cycle_limit_reached", "cycles": 1}

    assert store.begin_activity(key) is None
    store.complete_activity(key, result)

    assert store.begin_activity(key) == result


def test_completed_linked_task_is_recovered_without_duplicate_ledger_cost(tmp_path: Path):
    store = initialized_store(tmp_path)
    key = "company:execution:2"
    assert store.begin_activity(key) is None
    task_id = store.create_task(proposal(), "proposed", key)
    store.complete_task(task_id, SpecialistResult(
        status="completed", summary="Done", evidence=["Artifact"], recommendation="Continue",
    ), 12)

    recovered = store.begin_activity(key)

    assert recovered == {
        "status": "recovered_task_completed", "cycles": 1,
        "task_id": task_id, "recovered": True,
    }
    assert store.db.execute("SELECT count(*) AS value FROM ledger").fetchone()["value"] == 1


def test_unfinished_activity_recovers_its_frozen_proposal(tmp_path: Path):
    store = initialized_store(tmp_path)
    key = "company:execution:3"
    expected = proposal()
    assert store.begin_activity(key) is None
    task_id = store.create_task(expected, "proposed", key)

    assert store.begin_activity(key) is None
    recovered_id, recovered_proposal, status = store.recover_activity_task(key)

    assert recovered_id == task_id
    assert recovered_proposal == expected
    assert status == "proposed"


def test_linking_legacy_approved_task_backfills_frozen_proposal(tmp_path: Path):
    store = initialized_store(tmp_path)
    key = "company:execution:4"
    expected = proposal()
    task_id = store.create_task(expected, "proposed")
    store.db.execute("UPDATE tasks SET status='executing',proposal_json=NULL WHERE id=?", (task_id,))
    store.db.commit()
    assert store.begin_activity(key) is None

    store.link_activity_task(key, task_id, expected)

    assert store.recover_activity_task(key)[1] == expected


def test_final_failure_closes_task_and_caches_error(tmp_path: Path):
    store = initialized_store(tmp_path)
    key = "company:execution:5"
    assert store.begin_activity(key) is None
    task_id = store.create_task(proposal(), "proposed", key)

    outcome = store.fail_activity(key, "worker exhausted retries")

    assert outcome["status"] == "error"
    assert store.begin_activity(key) == outcome
    task = store.db.execute("SELECT status FROM tasks WHERE id=?", (task_id,)).fetchone()
    assert task["status"] == "failed"
