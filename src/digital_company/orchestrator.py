"""The autonomous observe-decide-authorize-execute-record loop."""

from __future__ import annotations

from pathlib import Path

from digital_company.agents import AgentEngine
from digital_company.models import ActionType
from digital_company.policy import Governor
from digital_company.store import CompanyStore


class CompanyOrchestrator:
    """Coordinate one company's durable agent loop.

    The orchestrator owns control flow; the CEO owns only the choice of proposed
    work. This distinction prevents model output from bypassing policy, budget,
    approval, persistence, or artifact path checks.
    """
    def __init__(self, store: CompanyStore, artifacts_dir: Path, engine: AgentEngine | None = None):
        self.store = store
        self.artifacts_dir = artifacts_dir
        settings = store.get_settings()
        self.engine = engine or AgentEngine(settings["model_mode"], settings["local_model"])
        self.governor = Governor()

    def run(self, max_cycles: int = 8) -> dict:
        """Run bounded cycles and return on pause, stop, approval, or cycle limit.

        Pause/stop are cooperative: they are checked between atomic agent steps.
        An in-flight model request or database write is allowed to finish safely.
        """
        for cycle in range(1, max_cycles + 1):
            control = self.store.get_control()
            if control["state"] in {"paused", "stopped"}:
                return {"status": control["state"], "cycles": cycle - 1}
            snapshot = self.store.snapshot()
            if snapshot.pending_approvals:
                return {"status": "waiting_for_approval", "cycles": cycle - 1,
                        "approval": snapshot.pending_approvals[0]}

            proposal = self.engine.decide(snapshot)
            # A stakeholder response becomes durable before the proposed task is
            # evaluated, so the UI can show how the CEO handled the intervention.
            self.store.address_messages(
                proposal.stakeholder_message_ids_considered,
                proposal.stakeholder_response,
            )
            policy = self.governor.evaluate(proposal, snapshot.remaining_budget_eur)
            task_id = self.store.create_task(proposal, "proposed")

            if policy.outcome == "deny":
                self.store.audit("task.denied", {"task_id": task_id, "reason": policy.reason})
                continue
            if policy.outcome == "require_approval":
                approval_id = self.store.request_approval(task_id, proposal, policy.reason)
                return {"status": "waiting_for_approval", "cycles": cycle,
                        "approval_id": approval_id, "task": proposal.model_dump(mode="json")}
            if proposal.action == ActionType.STOP:
                return {"status": "stopped", "cycles": cycle, "reason": proposal.rationale}

            result = self.engine.execute(proposal, snapshot, self._artifact_context(proposal.specialist))
            if result.artifact_path and result.artifact_content:
                target = (self.artifacts_dir / result.artifact_path).resolve()
                root = self.artifacts_dir.resolve()
                # Never trust an LLM-supplied path. Resolve it and prove that the
                # final destination remains inside this company's artifact root.
                if root not in target.parents and target != root:
                    raise RuntimeError("Specialist returned an unsafe artifact path.")
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(result.artifact_content, encoding="utf-8")
            self.store.complete_task(task_id, result, proposal.estimated_cost_eur)

        return {"status": "cycle_limit_reached", "cycles": max_cycles}

    def _artifact_context(self, specialist: str) -> dict | None:
        """Provide QA with the current MVP and cheap deterministic preflight data."""
        if specialist != "qa":
            return None
        target = self.artifacts_dir / "mvp" / "index.html"
        if not target.exists():
            return {"exists": False, "preflight": {"passed": False, "reason": "MVP artifact missing"}}
        content = target.read_text(encoding="utf-8")
        checks = {
            "has_html_document": "<html" in content.lower(),
            "has_javascript": "<script" in content.lower(),
            "has_local_persistence": "localStorage" in content,
            "has_invoice_workflow": "invoice" in content.lower() or "faktur" in content.lower(),
            "has_reminder_workflow": "reminder" in content.lower() or "podsjet" in content.lower(),
        }
        return {
            "exists": True,
            "path": str(target),
            "size_bytes": len(content.encode("utf-8")),
            "preflight": {"passed": all(checks.values()), "checks": checks},
            "content": content,
        }
