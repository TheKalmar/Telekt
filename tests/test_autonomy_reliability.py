from __future__ import annotations

from pathlib import Path

import pytest

from digital_company.models import ActionType, SpecialistResult, TaskProposal
from digital_company.orchestrator import CompanyOrchestrator
from digital_company.store import CompanyStore


def proposal(action: ActionType, specialist: str, title: str, cost: float = 0) -> TaskProposal:
    return TaskProposal(
        action=action,
        title=title,
        objective=f"Execute {title.lower()}",
        rationale="This is the cheapest reversible next step",
        expected_evidence=["Observable result"],
        estimated_cost_eur=cost,
        specialist=specialist,
    )


class ScriptedEngine:
    """Deterministic agent boundary used to exercise the real control loop."""

    def __init__(self, proposals: list[TaskProposal], *, fail_once: bool = False):
        self.proposals = list(proposals)
        self.fail_once = fail_once
        self.executions: list[ActionType] = []

    def decide(self, _snapshot):
        return self.proposals.pop(0)

    def execute(self, assigned, _snapshot, _artifact_context=None, _skills=None):
        self.executions.append(assigned.action)
        if self.fail_once:
            self.fail_once = False
            raise TimeoutError("injected transient specialist timeout")
        if assigned.action == ActionType.BUILD_MVP:
            return SpecialistResult(
                status="completed",
                summary="Functional validation asset built",
                evidence=["Self-contained HTML produced"],
                artifact_path="mvp/index.html",
                artifact_content="<!doctype html><html><body><button>Validate</button></body></html>",
                recommendation="Run adversarial QA",
            )
        return SpecialistResult(
            status="completed",
            summary=f"Completed {assigned.action.value}",
            evidence=[f"Evidence for {assigned.action.value}"],
            recommendation="Continue with the cheapest evidence-backed step",
        )


class FakeExecutionRuntime:
    def __init__(self):
        self.calls = []

    def checkpoint(self, company_id, task_id, artifact_path, content, idempotency_key):
        self.calls.append({
            "company_id": company_id, "task_id": task_id, "artifact_path": artifact_path,
            "content": content, "idempotency_key": idempotency_key,
        })
        return {
            "status": "committed", "branch": f"agent/{task_id[:12]}",
            "commit": "abc123", "cached": False,
        }


def initialized_store(tmp_path: Path) -> CompanyStore:
    store = CompanyStore(tmp_path / "company.db")
    store.initialize("Validate a profitable B2B product", 1000)
    store.set_control("running", "Reliability scenario started")
    return store


def test_goal_to_validated_mvp_and_human_decision_runs_through_real_loop(tmp_path: Path):
    store = initialized_store(tmp_path)
    engine = ScriptedEngine([
        proposal(ActionType.RESEARCH_MARKET, "research", "Research painful workflow", 2),
        proposal(ActionType.DEFINE_PRODUCT, "product", "Define narrow paid pilot", 2),
        proposal(ActionType.BUILD_MVP, "development", "Build validation MVP", 4),
        proposal(ActionType.QA_MVP, "qa", "Verify validation MVP", 2),
        proposal(ActionType.EXTERNAL_OUTREACH, "growth", "Contact pilot candidates", 10),
    ])

    result = CompanyOrchestrator(store, tmp_path / "artifacts", engine=engine).run(max_cycles=5)

    assert result == {"status": "cycle_limit_reached", "cycles": 5}
    assert [item.value for item in engine.executions] == [
        "research_market", "define_product", "build_mvp", "qa_mvp",
    ]
    assert (tmp_path / "artifacts" / "mvp" / "index.html").is_file()
    assert len(store.snapshot().completed_tasks) == 4
    assert len(store.list_approvals()) == 1
    assert store.list_approvals()[0]["status"] == "pending"


def test_transient_failure_recovers_frozen_task_without_duplicate_work(tmp_path: Path):
    store = initialized_store(tmp_path)
    execution_key = "company:execution:failure-injection"
    store.begin_activity(execution_key)
    engine = ScriptedEngine([
        proposal(ActionType.DEFINE_PRODUCT, "product", "Define recovery-safe pilot", 3),
    ], fail_once=True)
    orchestrator = CompanyOrchestrator(
        store, tmp_path / "artifacts", engine=engine, execution_key=execution_key,
    )

    with pytest.raises(TimeoutError, match="injected transient"):
        orchestrator.run(max_cycles=1)
    result = orchestrator.run(max_cycles=1)

    assert result["status"] == "recovered_task_completed"
    assert engine.executions == [ActionType.DEFINE_PRODUCT, ActionType.DEFINE_PRODUCT]
    assert store.db.execute("SELECT count(*) AS value FROM tasks").fetchone()["value"] == 1
    assert store.db.execute("SELECT count(*) AS value FROM ledger").fetchone()["value"] == 1


def test_structured_specialist_failure_is_visible_to_ceo_and_not_billed_as_success(tmp_path: Path):
    store = initialized_store(tmp_path)
    engine = ScriptedEngine([
        proposal(ActionType.RESEARCH_MARKET, "research", "Research with unavailable search", 8),
    ])
    engine.execute = lambda *_args, **_kwargs: SpecialistResult(
        status="failed",
        summary="Hosted search capability is unavailable",
        evidence=["No external source was queried"],
        recommendation="Switch routing or choose an offline validation method",
    )

    CompanyOrchestrator(store, tmp_path / "artifacts", engine=engine).run(max_cycles=1)
    snapshot = store.snapshot()

    assert snapshot.completed_tasks == []
    assert snapshot.recent_failures[0]["result"]["summary"] == "Hosted search capability is unavailable"
    assert snapshot.recent_failures[0]["result"]["evidence"] == ["No external source was queried"]
    assert snapshot.spent_eur == 0


def test_development_artifact_is_checkpointed_in_isolated_repository(tmp_path: Path):
    store = initialized_store(tmp_path)
    execution = FakeExecutionRuntime()
    engine = ScriptedEngine([
        proposal(ActionType.BUILD_MVP, "development", "Build isolated MVP", 4),
    ])

    CompanyOrchestrator(
        store, tmp_path / "artifacts", engine=engine, company_id="company-1",
        execution_runtime=execution,
    ).run(max_cycles=1)

    assert execution.calls[0]["company_id"] == "company-1"
    assert execution.calls[0]["artifact_path"] == "mvp/index.html"
    events = store.operations_data()["recent_events"]
    checkpoint = next(item for item in events if item["event_type"] == "execution.repository_checkpointed")
    assert checkpoint["payload"]["commit"] == "abc123"
