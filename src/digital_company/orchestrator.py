"""The autonomous observe-decide-authorize-execute-record loop."""

from __future__ import annotations

from pathlib import Path
from urllib.parse import urlparse

from digital_company.agents import AgentEngine
from digital_company.computer_use import BrowserMissionRunner, browser_runtime_request
from digital_company.email_service import ApprovalMailer
from digital_company.models import ActionType, SpecialistResult
from digital_company.policy import Governor
from digital_company.store import CompanyStore
from digital_company.workspace import WorkspaceRuntime


class CompanyOrchestrator:
    """Coordinate one company's durable agent loop.

    The orchestrator owns control flow; the CEO owns only the choice of proposed
    work. This distinction prevents model output from bypassing policy, budget,
    approval, persistence, or artifact path checks.
    """
    def __init__(self, store: CompanyStore, artifacts_dir: Path, engine: AgentEngine | None = None,
                 company_id: str | None = None, mailer: ApprovalMailer | None = None):
        self.store = store
        self.artifacts_dir = artifacts_dir
        self.workspace = WorkspaceRuntime(artifacts_dir)
        settings = store.get_settings()
        self.engine = engine or AgentEngine(
            settings["model_mode"], settings["local_model"],
            bool(settings["allow_cloud_fallback"]),
            reporter=self._report,
            remaining_budget=lambda: self.store.snapshot().remaining_budget_eur,
        )
        self.governor = Governor()
        self.company_id = company_id
        self.mailer = mailer or ApprovalMailer()

    def _report(self, event: str, payload: dict) -> None:
        if event == "model.usage":
            self.store.record_model_usage(payload)
        self.store.audit(event, payload)

    def run(self, max_cycles: int = 8) -> dict:
        """Run bounded cycles and return on pause, stop, approval, or cycle limit.

        Pause/stop are cooperative: they are checked between atomic agent steps.
        An in-flight model request or database write is allowed to finish safely.
        """
        for cycle in range(1, max_cycles + 1):
            control = self.store.get_control()
            if control["state"] in {"paused", "stopped"}:
                return {"status": control["state"], "cycles": cycle - 1}

            approved = self.store.claim_approved_task()
            if approved:
                task_id, proposal = approved
                return self._execute_claimed(task_id, proposal, cycle)

            snapshot = self.store.snapshot()
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
                self.store.set_task_status(task_id, "denied")
                self.store.audit("task.denied", {"task_id": task_id, "reason": policy.reason})
                continue
            if proposal.action == ActionType.REQUEST_HUMAN_HANDOFF:
                handoff_id = self.store.create_handoff(task_id, proposal)
                return {
                    "status": "waiting_for_human", "cycles": cycle,
                    "handoff_id": handoff_id, "task": proposal.model_dump(mode="json"),
                }
            if policy.outcome == "require_approval":
                if self.store.has_pending_equivalent_approval(proposal):
                    self.store.set_task_status(task_id, "superseded")
                    self.store.audit("company.blocked_by_pending_approval", {"task_id": task_id})
                    return {"status": "waiting_for_approval", "cycles": cycle,
                            "reason": "No useful autonomous work remains; pending human decision is blocking"}
                if proposal.action == ActionType.REQUEST_PLATFORM_ACCESS:
                    self.store.request_integration(
                        proposal.platform_candidate or "unknown",
                        proposal.required_capabilities,
                    )
                approval_id = self.store.request_approval(task_id, proposal, policy.reason)
                self.store.audit("approval.queued_without_pause", {"approval_id": approval_id})
                continue
            if proposal.action == ActionType.STOP:
                self.store.set_task_status(task_id, "stopped")
                return {"status": "stopped", "cycles": cycle, "reason": proposal.rationale}

            result = self.engine.execute(proposal, snapshot, self._artifact_context(proposal.specialist))
            self._persist_result(task_id, proposal, result)

        return {"status": "cycle_limit_reached", "cycles": max_cycles}

    def _execute_claimed(self, task_id: str, proposal, cycle: int) -> dict:
        """Execute one human-approved frozen proposal without asking the CEO again."""
        snapshot = self.store.snapshot()
        policy = self.governor.evaluate(proposal, snapshot.remaining_budget_eur)
        if policy.outcome == "deny":
            self.store.fail_task(task_id, "Approved task no longer passes policy: " + policy.reason)
            return {"status": "failed", "cycles": cycle, "task_id": task_id}
        try:
            if proposal.action == ActionType.BROWSER_OPERATE:
                return self._execute_browser_mission(task_id, proposal, cycle)
            result = self.engine.execute(proposal, snapshot, self._artifact_context(proposal.specialist))
            self._persist_result(task_id, proposal, result)
        except Exception as exc:
            self.store.fail_task(task_id, f"{type(exc).__name__}: {exc}")
            raise
        return {"status": "approved_task_completed", "cycles": cycle, "task_id": task_id}

    def _execute_browser_mission(self, task_id: str, proposal, cycle: int) -> dict:
        """Run an approved mission, but create a fresh handoff for any human checkpoint."""
        hostname = urlparse(proposal.handoff_url).hostname
        if not hostname:
            raise RuntimeError("Browser mission has no valid starting hostname")
        browser_runtime_request("PUT", f"/sessions/{self.company_id}", {
            "url": proposal.handoff_url, "allowed_domains": [hostname],
        })
        max_steps = max(1, min(30, int(__import__("os").getenv("COMPUTER_USE_MAX_STEPS", "12"))))
        self.store.audit("browser.mission_started", {
            "task_id": task_id, "objective": proposal.objective,
            "allowed_domains": [hostname], "max_steps": max_steps,
        })
        outcome = BrowserMissionRunner(
            reporter=lambda event, payload: self.store.audit(event, {"task_id": task_id, **payload}),
            control_state=lambda: self.store.get_control()["state"],
        ).run(
            self.company_id,
            proposal.objective,
            max_steps=max_steps,
        )
        if outcome.status in {"waiting_human", "blocked"}:
            handoff = proposal.model_copy(update={
                "handoff_instructions": [outcome.summary, "Use the browser cockpit or normal browser to resolve it"],
                "resume_evidence": ["Describe exactly what was completed and what access is now available"],
            })
            handoff_id = self.store.create_handoff(task_id, handoff)
            self.store.audit("browser.mission_handoff", {
                "task_id": task_id, "handoff_id": handoff_id, "reason": outcome.summary,
            })
            return {"status": "waiting_for_human", "cycles": cycle, "task_id": task_id,
                    "handoff_id": handoff_id, "reason": outcome.summary}
        if outcome.status != "completed":
            self.store.fail_task(task_id, outcome.summary)
            status = outcome.status if outcome.status in {"paused", "stopped"} else "failed"
            return {"status": status, "cycles": cycle, "task_id": task_id,
                    "mission_status": outcome.status}
        result = SpecialistResult(
            status="completed",
            summary=outcome.summary,
            evidence=[f"Computer Use mission executed {outcome.steps} audited step(s) on {hostname}"],
            recommendation="CEO should inspect the mission evidence and choose the next reversible action",
        )
        self._persist_result(task_id, proposal, result)
        return {"status": "approved_task_completed", "cycles": cycle, "task_id": task_id,
                "mission_status": outcome.status}

    def _persist_result(self, task_id: str, proposal, result) -> None:
        """Persist a specialist result and confine any model-provided artifact path."""
        if result.artifact_path and result.artifact_content:
            try:
                metadata = self.workspace.write_text(result.artifact_path, result.artifact_content)
            except ValueError as exc:
                raise RuntimeError(f"Specialist returned an unsafe artifact: {exc}") from exc
            self.store.audit("artifact.written", {
                "task_id": task_id,
                "role": proposal.specialist,
                **metadata,
            })
            result.evidence.append(
                f"Workspace validation {'passed' if metadata['checks']['passed'] else 'failed'}; "
                f"sha256={metadata['sha256'][:12]}, bytes={metadata['size_bytes']}"
            )
        self.store.complete_task(task_id, result, proposal.estimated_cost_eur)

    def _artifact_context(self, specialist: str) -> dict | None:
        """Provide QA with the current MVP and cheap deterministic preflight data."""
        if specialist != "qa":
            return None
        target = self.workspace.resolve("mvp/index.html")
        if not target.exists():
            return {"exists": False, "preflight": {"passed": False, "reason": "MVP artifact missing"}}
        content = target.read_text(encoding="utf-8")
        checks = self.workspace.validate("mvp/index.html", content)
        return {
            "exists": True,
            "path": str(target),
            "size_bytes": len(content.encode("utf-8")),
            "preflight": {"passed": checks["passed"], "checks": checks},
            "content": content,
        }
