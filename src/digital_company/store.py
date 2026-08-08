"""SQLite persistence for one isolated digital company.

SQLite is the POC durability layer. The schema deliberately separates company
facts, tasks, approvals, budget ledger entries, stakeholder messages, runtime
control, model settings, and append-only audit events. A production deployment
can preserve these interfaces while replacing SQLite with PostgreSQL.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from digital_company.models import CompanySnapshot, SpecialistResult, TaskProposal


def utc_now() -> str:
    """Return an ISO-8601 UTC timestamp suitable for durable records."""
    return datetime.now(timezone.utc).isoformat()


class CompanyStore:
    """Repository for all canonical state belonging to exactly one company."""
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self.db = sqlite3.connect(path, timeout=30)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA busy_timeout=30000")
        self._migrate()

    def _migrate(self) -> None:
        """Create additive POC tables and idempotent singleton defaults."""
        self.db.executescript("""
        CREATE TABLE IF NOT EXISTS company (
          id INTEGER PRIMARY KEY CHECK (id = 1), goal TEXT NOT NULL,
          initial_budget_eur REAL NOT NULL, created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS tasks (
          id TEXT PRIMARY KEY, action TEXT NOT NULL, title TEXT NOT NULL,
          specialist TEXT NOT NULL, objective TEXT NOT NULL, rationale TEXT NOT NULL,
          estimated_cost_eur REAL NOT NULL, status TEXT NOT NULL,
          result_json TEXT, created_at TEXT NOT NULL, completed_at TEXT
        );
        CREATE TABLE IF NOT EXISTS approvals (
          id TEXT PRIMARY KEY, task_id TEXT NOT NULL, payload_json TEXT NOT NULL,
          status TEXT NOT NULL, reason TEXT NOT NULL, created_at TEXT NOT NULL,
          resolved_at TEXT
        );
        CREATE TABLE IF NOT EXISTS ledger (
          id TEXT PRIMARY KEY, task_id TEXT, amount_eur REAL NOT NULL,
          description TEXT NOT NULL, created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS audit_events (
          id TEXT PRIMARY KEY, event_type TEXT NOT NULL, payload_json TEXT NOT NULL,
          created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS runtime_control (
          id INTEGER PRIMARY KEY CHECK (id = 1), state TEXT NOT NULL,
          detail TEXT, updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS stakeholder_messages (
          id TEXT PRIMARY KEY, kind TEXT NOT NULL, content TEXT NOT NULL,
          status TEXT NOT NULL, response TEXT, created_at TEXT NOT NULL,
          addressed_at TEXT
        );
        CREATE TABLE IF NOT EXISTS runtime_settings (
          id INTEGER PRIMARY KEY CHECK (id = 1), model_mode TEXT NOT NULL,
          local_model TEXT NOT NULL, updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS company_profile (
          id INTEGER PRIMARY KEY CHECK (id = 1), profile_json TEXT NOT NULL,
          updated_at TEXT NOT NULL
        );
        """)
        approval_columns = {row["name"] for row in self.db.execute("PRAGMA table_info(approvals)")}
        if "decision_comment" not in approval_columns:
            self.db.execute("ALTER TABLE approvals ADD COLUMN decision_comment TEXT")
        if "decided_by" not in approval_columns:
            self.db.execute("ALTER TABLE approvals ADD COLUMN decided_by TEXT")
        settings_columns = {row["name"] for row in self.db.execute("PRAGMA table_info(runtime_settings)")}
        if "allow_cloud_fallback" not in settings_columns:
            self.db.execute(
                "ALTER TABLE runtime_settings ADD COLUMN allow_cloud_fallback INTEGER NOT NULL DEFAULT 0"
            )
        self.db.execute(
            "INSERT OR IGNORE INTO runtime_control(id,state,detail,updated_at) VALUES(1,'stopped','Ready',?)",
            (utc_now(),),
        )
        self.db.execute(
            "INSERT OR IGNORE INTO runtime_settings(id,model_mode,local_model,updated_at) "
            "VALUES(1,'local','deepseek-company:8b',?)", (utc_now(),)
        )
        self.db.commit()

    def initialize(self, goal: str, budget: float, profile: dict | None = None) -> None:
        """Create or replace the singleton company header and optional profile."""
        self.db.execute(
            "INSERT OR REPLACE INTO company(id, goal, initial_budget_eur, created_at) VALUES(1,?,?,?)",
            (goal, budget, utc_now()),
        )
        if profile is not None:
            self.db.execute(
                "INSERT OR REPLACE INTO company_profile(id,profile_json,updated_at) VALUES(1,?,?)",
                (json.dumps(profile), utc_now()),
            )
            self.db.commit()
        self.audit("company.initialized", {"goal": goal, "budget": budget})

    def is_initialized(self) -> bool:
        """Return whether the database contains a company header."""
        return self.db.execute("SELECT 1 FROM company WHERE id=1").fetchone() is not None

    def snapshot(self) -> CompanySnapshot:
        """Build the compact, typed context supplied to agents.

        Large artifact bodies are replaced with references so every reasoning
        cycle does not resend generated HTML and exhaust context/token budgets.
        """
        company = self.db.execute("SELECT * FROM company WHERE id=1").fetchone()
        if not company:
            raise RuntimeError("Company is not initialized. Run `digital-company init` first.")
        spent = float(self.db.execute("SELECT COALESCE(SUM(amount_eur),0) FROM ledger").fetchone()[0])
        tasks = [dict(row) for row in self.db.execute(
            "SELECT action,title,specialist,status,result_json FROM tasks WHERE status='completed' ORDER BY created_at"
        )]
        for task in tasks:
            if task["result_json"]:
                result = json.loads(task.pop("result_json"))
                if result.get("artifact_content"):
                    result["artifact_content"] = "[stored artifact omitted from decision context]"
                task["result"] = result
        approvals = [dict(row) for row in self.db.execute(
            "SELECT id,task_id,status,reason FROM approvals WHERE status='pending' ORDER BY created_at"
        )]
        evidence = []
        for task in tasks[-5:]:
            result = task.get("result") or {}
            evidence.extend({"task": task["title"], "evidence": item} for item in result.get("evidence", []))
        messages = [dict(row) for row in self.db.execute(
            "SELECT id,kind,content,status,response,created_at,addressed_at FROM stakeholder_messages "
            "ORDER BY created_at DESC LIMIT 20"
        )]
        return CompanySnapshot(
            goal=company["goal"], initial_budget_eur=company["initial_budget_eur"],
            spent_eur=spent, remaining_budget_eur=company["initial_budget_eur"] - spent,
            completed_tasks=tasks, pending_approvals=approvals, recent_evidence=evidence[-12:],
            stakeholder_messages=messages,
            profile=self.get_profile(),
        )

    def get_profile(self) -> dict:
        """Return user-configurable company creation parameters."""
        row = self.db.execute("SELECT profile_json FROM company_profile WHERE id=1").fetchone()
        return json.loads(row["profile_json"]) if row else {}

    def create_task(self, proposal: TaskProposal, status: str) -> str:
        """Persist a CEO proposal before authorization or execution."""
        task_id = str(uuid4())
        self.db.execute(
            "INSERT INTO tasks VALUES(?,?,?,?,?,?,?,?,?,?,?)",
            (task_id, proposal.action.value, proposal.title, proposal.specialist,
             proposal.objective, proposal.rationale, proposal.estimated_cost_eur,
             status, None, utc_now(), None),
        )
        self.db.commit()
        self.audit("task.created", {"task_id": task_id, "proposal": proposal.model_dump(mode="json")})
        return task_id

    def complete_task(self, task_id: str, result: SpecialistResult, cost: float) -> None:
        """Atomically complete a task, its approval, and authorized ledger cost."""
        current = self.db.execute("SELECT status FROM tasks WHERE id=?", (task_id,)).fetchone()
        if not current or current["status"] not in {"proposed", "executing"}:
            raise RuntimeError("Task is not executable or has already been finalized.")
        self.db.execute(
            "UPDATE tasks SET status='completed', result_json=?, completed_at=? "
            "WHERE id=? AND status IN ('proposed','executing')",
            (result.model_dump_json(), utc_now(), task_id),
        )
        self.db.execute(
            "UPDATE approvals SET status='executed' WHERE task_id=? AND status='executing'",
            (task_id,),
        )
        if cost:
            self.db.execute(
                "INSERT INTO ledger VALUES(?,?,?,?,?)",
                (str(uuid4()), task_id, cost, "Authorized task cost", utc_now()),
            )
        self.db.commit()
        self.audit("task.completed", {"task_id": task_id, "result": result.model_dump(mode="json")})

    def fail_task(self, task_id: str, reason: str) -> None:
        """Finalize a claimed task as failed so it cannot execute twice."""
        now = utc_now()
        result = SpecialistResult(
            status="failed", summary=reason, evidence=[], recommendation="Human review required",
        )
        self.db.execute(
            "UPDATE tasks SET status='failed',result_json=?,completed_at=? "
            "WHERE id=? AND status='executing'",
            (result.model_dump_json(), now, task_id),
        )
        self.db.execute(
            "UPDATE approvals SET status='execution_failed' WHERE task_id=? AND status='executing'",
            (task_id,),
        )
        self.db.commit()
        self.audit("task.failed", {"task_id": task_id, "reason": reason})

    def set_task_status(self, task_id: str, status: str) -> None:
        """Set a deterministic terminal/intermediate status for orchestration."""
        if status not in {"denied", "stopped", "superseded"}:
            raise ValueError("Invalid direct task status")
        self.db.execute("UPDATE tasks SET status=?,completed_at=? WHERE id=?", (status, utc_now(), task_id))
        self.db.commit()

    def request_approval(self, task_id: str, proposal: TaskProposal, reason: str) -> str:
        """Freeze the exact proposed payload as a pending human approval."""
        approval_id = str(uuid4())
        self.db.execute(
            "INSERT INTO approvals(id,task_id,payload_json,status,reason,created_at,resolved_at) "
            "VALUES(?,?,?,?,?,?,?)",
            (approval_id, task_id, proposal.model_dump_json(), "pending", reason, utc_now(), None),
        )
        self.db.execute("UPDATE tasks SET status='waiting_approval' WHERE id=?", (task_id,))
        self.db.commit()
        self.audit("approval.requested", {"approval_id": approval_id, "task_id": task_id})
        return approval_id

    def list_approvals(self) -> list[dict]:
        """Return approval history, newest first."""
        return [dict(row) for row in self.db.execute("SELECT * FROM approvals ORDER BY created_at DESC")]

    def approve(self, approval_id: str, comment: str = "", decided_by: str = "dashboard") -> None:
        """Approve one still-pending payload; approvals are single-use."""
        row = self.db.execute("SELECT task_id,status FROM approvals WHERE id=?", (approval_id,)).fetchone()
        if not row:
            raise RuntimeError("Approval not found.")
        if row["status"] != "pending":
            raise RuntimeError("Approval has already been resolved.")
        now = utc_now()
        self.db.execute(
            "UPDATE approvals SET status='approved',resolved_at=?,decision_comment=?,decided_by=? WHERE id=?",
            (now, comment.strip() or None, decided_by, approval_id),
        )
        self.db.execute("UPDATE tasks SET status='approved' WHERE id=?", (row["task_id"],))
        self.db.execute(
            "UPDATE runtime_control SET state='running',detail=?,updated_at=? WHERE id=1",
            ("Approved task queued for execution", now),
        )
        self.db.commit()
        self.audit("approval.approved", {"approval_id": approval_id, "task_id": row["task_id"]})
        if comment.strip():
            self.add_approval_feedback(comment, approval_id, decided_by, "approved")

    def claim_approved_task(self) -> tuple[str, TaskProposal] | None:
        """Atomically claim the oldest frozen approved payload exactly once."""
        self.db.execute("BEGIN IMMEDIATE")
        try:
            row = self.db.execute(
                "SELECT a.task_id,a.payload_json FROM approvals a JOIN tasks t ON t.id=a.task_id "
                "WHERE a.status='approved' AND t.status='approved' ORDER BY a.created_at LIMIT 1"
            ).fetchone()
            if not row:
                self.db.commit()
                return None
            changed = self.db.execute(
                "UPDATE tasks SET status='executing' WHERE id=? AND status='approved'", (row["task_id"],)
            ).rowcount
            if changed != 1:
                self.db.rollback()
                return None
            self.db.execute(
                "UPDATE approvals SET status='executing' WHERE task_id=? AND status='approved'",
                (row["task_id"],),
            )
            self.db.commit()
        except Exception:
            self.db.rollback()
            raise
        proposal = TaskProposal.model_validate_json(row["payload_json"])
        self.audit("approval.execution_claimed", {"task_id": row["task_id"]})
        return row["task_id"], proposal

    def reject(self, approval_id: str, comment: str, decided_by: str = "dashboard") -> None:
        """Reject one still-pending payload and its associated task."""
        if not comment.strip():
            raise ValueError("A decline reason is required.")
        row = self.db.execute("SELECT task_id,status FROM approvals WHERE id=?", (approval_id,)).fetchone()
        if not row or row["status"] != "pending":
            raise RuntimeError("Pending approval not found.")
        now = utc_now()
        self.db.execute(
            "UPDATE approvals SET status='rejected',resolved_at=?,decision_comment=?,decided_by=? WHERE id=?",
            (now, comment.strip(), decided_by, approval_id),
        )
        self.db.execute("UPDATE tasks SET status='rejected' WHERE id=?", (row["task_id"],))
        self.db.execute(
            "UPDATE runtime_control SET state='running',detail=?,updated_at=? WHERE id=1",
            ("Rejected task; queued for CEO reconsideration", now),
        )
        self.db.commit()
        self.audit("approval.rejected", {"approval_id": approval_id, "task_id": row["task_id"]})
        self.add_approval_feedback(comment, approval_id, decided_by, "rejected")

    def add_approval_feedback(self, comment: str, approval_id: str, author: str, decision: str) -> None:
        """Put human decision context into the CEO/specialist canonical snapshot."""
        message_id = str(uuid4())
        content = f"Approval {decision} by {author}: {comment.strip()}"
        self.db.execute(
            "INSERT INTO stakeholder_messages VALUES(?,?,?,?,?,?,?)",
            (message_id, "approval_feedback", content, "pending", None, utc_now(), None),
        )
        self.db.commit()
        self.audit("approval.feedback", {"approval_id": approval_id, "message_id": message_id})

    def get_email_settings(self) -> dict:
        """Return non-secret, per-company email notification preferences."""
        profile = self.get_profile()
        return {
            "enabled": bool(profile.get("approval_email_enabled", False)),
            "approvers": profile.get("approval_emails", []),
            "sender_name": profile.get("approval_sender_name", profile.get("name", "Digital Company")),
        }

    def set_email_settings(self, enabled: bool, approvers: list[str], sender_name: str) -> dict:
        """Persist recipient preferences; SMTP credentials remain environment secrets."""
        profile = self.get_profile()
        profile["approval_email_enabled"] = enabled
        profile["approval_emails"] = approvers
        profile["approval_sender_name"] = sender_name.strip() or profile.get("name", "Digital Company")
        self.db.execute(
            "INSERT OR REPLACE INTO company_profile(id,profile_json,updated_at) VALUES(1,?,?)",
            (json.dumps(profile), utc_now()),
        )
        self.db.commit()
        self.audit("email.settings", {"enabled": enabled, "approver_count": len(approvers)})
        return self.get_email_settings()

    def get_control(self) -> dict:
        """Read cooperative runtime state for this company."""
        return dict(self.db.execute("SELECT state,detail,updated_at FROM runtime_control WHERE id=1").fetchone())

    def set_control(self, state: str, detail: str | None = None) -> None:
        """Set runtime state and emit a corresponding audit event."""
        if state not in {"running", "paused", "stopped", "waiting_approval", "error"}:
            raise ValueError("Invalid runtime state")
        self.db.execute("UPDATE runtime_control SET state=?,detail=?,updated_at=? WHERE id=1",
                        (state, detail, utc_now()))
        self.db.commit()
        self.audit("runtime." + state, {"detail": detail})

    def add_stakeholder_message(self, content: str, kind: str = "directive") -> str:
        """Persist an owner message and supersede stale approvals for directives.

        A directive changes the decision context, so an approval produced before
        that intervention must not remain executable with stale assumptions.
        """
        if kind not in {"directive", "question"}:
            raise ValueError("Invalid stakeholder message kind")
        message_id = str(uuid4())
        self.db.execute("INSERT INTO stakeholder_messages VALUES(?,?,?,?,?,?,?)",
                        (message_id, kind, content, "pending", None, utc_now(), None))
        if kind == "directive":
            pending = list(self.db.execute(
                "SELECT id,task_id FROM approvals WHERE status IN ('pending','approved')"
            ))
            for row in pending:
                self.db.execute("UPDATE approvals SET status='superseded',resolved_at=? WHERE id=?", (utc_now(), row["id"]))
                self.db.execute("UPDATE tasks SET status='superseded' WHERE id=?", (row["task_id"],))
        self.db.commit()
        self.audit("stakeholder.message", {"message_id": message_id, "kind": kind})
        return message_id

    def address_messages(self, message_ids: list[str], response: str | None) -> None:
        """Mark messages the CEO explicitly considered and save its response."""
        now = utc_now()
        for message_id in message_ids:
            self.db.execute(
                "UPDATE stakeholder_messages SET status='addressed',response=?,addressed_at=? "
                "WHERE id=? AND status='pending'", (response, now, message_id)
            )
        self.db.commit()

    def dashboard_data(self) -> dict:
        """Return a UI-oriented projection of canonical state and recent audit."""
        snapshot = self.snapshot().model_dump(mode="json")
        snapshot["control"] = self.get_control()
        snapshot["settings"] = self.get_settings()
        snapshot["profile"] = self.get_profile()
        snapshot["approvals"] = self.list_approvals()[:20]
        snapshot["recent_tasks"] = [dict(row) for row in self.db.execute(
            "SELECT id,action,title,specialist,status,created_at,completed_at FROM tasks "
            "ORDER BY created_at DESC LIMIT 20"
        )]
        snapshot["audit"] = [dict(row) for row in self.db.execute(
            "SELECT event_type,payload_json,created_at FROM audit_events ORDER BY created_at DESC LIMIT 30"
        )]
        snapshot["operations"] = self.operations_data()
        return snapshot

    def operations_data(self) -> dict:
        """Project audit/task state into compact operational telemetry."""
        rows = [dict(row) for row in self.db.execute(
            "SELECT event_type,payload_json,created_at FROM audit_events "
            "ORDER BY created_at DESC LIMIT 250"
        )]
        events = []
        for row in rows:
            try:
                payload = json.loads(row["payload_json"])
            except (TypeError, json.JSONDecodeError):
                payload = {}
            events.append({"event_type": row["event_type"], "created_at": row["created_at"], "payload": payload})

        model_events = [event for event in events if event["event_type"].startswith("model.")]
        completed_run_ids = {
            event["payload"].get("run_id") for event in model_events
            if event["event_type"] in {"model.succeeded", "model.failed", "model.fallback_failed"}
        }
        active = next((
            event for event in model_events
            if event["event_type"] == "model.started"
            and event["payload"].get("run_id") not in completed_run_ids
        ), None)
        successes = [event for event in model_events if event["event_type"] == "model.succeeded"]
        latencies = sorted(
            event["payload"]["latency_ms"] for event in successes
            if isinstance(event["payload"].get("latency_ms"), (int, float))
        )
        provider_counts: dict[str, int] = {}
        for event in successes:
            provider = event["payload"].get("provider", "unknown")
            provider_counts[provider] = provider_counts.get(provider, 0) + 1
        task_counts = {
            row["status"]: row["count"] for row in self.db.execute(
                "SELECT status,COUNT(*) AS count FROM tasks GROUP BY status"
            )
        }
        estimated_spend = float(self.db.execute(
            "SELECT COALESCE(SUM(amount_eur),0) FROM ledger"
        ).fetchone()[0])
        return {
            "active_model_run": active,
            "model": {
                "successful_calls": len(successes),
                "structured_errors": sum(e["event_type"] == "model.structured_output_error" for e in model_events),
                "provider_errors": sum(e["event_type"] == "model.provider_error" for e in model_events),
                "failed_calls": sum(e["event_type"] in {"model.failed", "model.fallback_failed"} for e in model_events),
                "fallbacks": sum(e["event_type"] == "model.cloud_fallback" for e in model_events),
                "average_latency_ms": round(sum(latencies) / len(latencies)) if latencies else None,
                "p95_latency_ms": latencies[max(0, int(len(latencies) * .95) - 1)] if latencies else None,
                "provider_counts": provider_counts,
                "token_usage": None,
            },
            "tasks_by_status": task_counts,
            "estimated_spend_eur": estimated_spend,
            "recent_events": events[:40],
        }

    def get_settings(self) -> dict:
        """Return per-company model routing settings."""
        return dict(self.db.execute(
            "SELECT model_mode,local_model,allow_cloud_fallback,updated_at FROM runtime_settings WHERE id=1"
        ).fetchone())

    def set_model_mode(self, mode: str) -> None:
        """Select local, hybrid, or cloud routing for future cycles."""
        current = self.get_settings()
        self.set_model_settings(mode, current["local_model"], bool(current["allow_cloud_fallback"]))

    def set_model_settings(self, mode: str, local_model: str, allow_cloud_fallback: bool = False) -> None:
        """Atomically select routing mode and the local model used by this company."""
        if mode not in {"local", "hybrid", "cloud"}:
            raise ValueError("Invalid model mode")
        local_model = local_model.strip()
        if not local_model or len(local_model) > 200:
            raise ValueError("Local model name must contain 1 to 200 characters")
        self.db.execute(
            "UPDATE runtime_settings SET model_mode=?,local_model=?,allow_cloud_fallback=?,updated_at=? WHERE id=1",
            (mode, local_model, int(allow_cloud_fallback), utc_now()),
        )
        self.db.commit()
        self.audit("runtime.model_settings", {
            "mode": mode, "local_model": local_model,
            "allow_cloud_fallback": allow_cloud_fallback,
        })

    def audit(self, event_type: str, payload: dict) -> None:
        """Append an immutable event describing a meaningful state transition."""
        self.db.execute(
            "INSERT INTO audit_events VALUES(?,?,?,?)",
            (str(uuid4()), event_type, json.dumps(payload), utc_now()),
        )
        self.db.commit()
