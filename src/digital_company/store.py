"""SQLite persistence for one isolated digital company.

SQLite is the POC durability layer. The schema deliberately separates company
facts, tasks, approvals, budget ledger entries, stakeholder messages, runtime
control, model settings, and append-only audit events. A production deployment
can preserve these interfaces while replacing SQLite with PostgreSQL.
"""

from __future__ import annotations

import json
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from digital_company.models import CompanySnapshot, SpecialistResult, TaskProposal
from digital_company.postgres_compat import PostgresCompat, company_schema


def utc_now() -> str:
    """Return an ISO-8601 UTC timestamp suitable for durable records."""
    return datetime.now(timezone.utc).isoformat()


class CompanyStore:
    """Repository for all canonical state belonging to exactly one company."""
    def __init__(self, path: Path, database_url: str | None = None, company_id: str | None = None):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        if database_url:
            if not company_id:
                raise ValueError("company_id is required for PostgreSQL stores")
            self.db = PostgresCompat(database_url, company_schema(company_id))
        else:
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
        CREATE TABLE IF NOT EXISTS model_usage (
          run_id TEXT PRIMARY KEY, provider TEXT NOT NULL, model TEXT NOT NULL,
          requests INTEGER NOT NULL, input_tokens INTEGER NOT NULL, cached_tokens INTEGER NOT NULL,
          output_tokens INTEGER NOT NULL, reasoning_tokens INTEGER NOT NULL, total_tokens INTEGER NOT NULL,
          estimated_usd REAL, estimated_budget_cost REAL, pricing_status TEXT NOT NULL, created_at TEXT NOT NULL
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
        CREATE TABLE IF NOT EXISTS integrations (
          provider TEXT PRIMARY KEY, status TEXT NOT NULL,
          config_json TEXT NOT NULL, required_secrets_json TEXT NOT NULL,
          updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS human_handoffs (
          id TEXT PRIMARY KEY, task_id TEXT NOT NULL, payload_json TEXT NOT NULL,
          status TEXT NOT NULL, outcome TEXT, created_at TEXT NOT NULL,
          resolved_at TEXT
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
            "INSERT INTO company(id, goal, initial_budget_eur, created_at) VALUES(1,?,?,?) "
            "ON CONFLICT(id) DO UPDATE SET goal=excluded.goal,initial_budget_eur=excluded.initial_budget_eur,created_at=excluded.created_at",
            (goal, budget, utc_now()),
        )
        if profile is not None:
            self.db.execute(
                "INSERT INTO company_profile(id,profile_json,updated_at) VALUES(1,?,?) "
                "ON CONFLICT(id) DO UPDATE SET profile_json=excluded.profile_json,updated_at=excluded.updated_at",
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
        spent = float(self.db.execute("SELECT COALESCE(SUM(amount_eur),0) AS value FROM ledger").fetchone()["value"])
        spent += float(self.db.execute("SELECT COALESCE(SUM(estimated_budget_cost),0) AS value FROM model_usage").fetchone()["value"])
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
            capabilities=self.list_integrations(),
            human_handoffs=self.list_handoffs(status="pending"),
        )

    def get_profile(self) -> dict:
        """Return user-configurable company creation parameters."""
        row = self.db.execute("SELECT profile_json FROM company_profile WHERE id=1").fetchone()
        return json.loads(row["profile_json"]) if row else {}

    def upsert_integration(self, provider: str, status: str, config: dict,
                           required_secrets: list[str]) -> dict:
        """Persist non-secret capability metadata and references to environment secrets."""
        now = utc_now()
        self.db.execute(
            "INSERT INTO integrations(provider,status,config_json,required_secrets_json,updated_at) "
            "VALUES(?,?,?,?,?) ON CONFLICT(provider) DO UPDATE SET status=excluded.status, "
            "config_json=excluded.config_json, required_secrets_json=excluded.required_secrets_json, "
            "updated_at=excluded.updated_at",
            (provider, status, json.dumps(config), json.dumps(required_secrets), now),
        )
        self.db.commit()
        self.audit("integration.configured", {
            "provider": provider, "status": status, "config": config,
            "required_secrets": required_secrets,
        })
        return next(item for item in self.list_integrations() if item["provider"] == provider)

    def request_integration(self, provider: str, capabilities: list[str]) -> dict:
        """Record a CEO-requested platform without treating access as granted."""
        existing = next((item for item in self.list_integrations() if item["provider"] == provider), None)
        if existing:
            return existing
        return self.upsert_integration(provider, "requested", {"capabilities": capabilities}, [])

    def list_integrations(self) -> list[dict]:
        """Return capability status while revealing only secret presence, never values."""
        result = []
        for row in self.db.execute("SELECT * FROM integrations ORDER BY provider"):
            required = json.loads(row["required_secrets_json"])
            secret_status = {name: bool(os.getenv(name)) for name in required}
            configured = row["status"] == "configured"
            effective = "ready" if configured and all(secret_status.values()) else row["status"]
            if configured and required and not all(secret_status.values()):
                effective = "missing_secrets"
            result.append({
                "provider": row["provider"], "status": effective,
                "config": json.loads(row["config_json"]),
                "required_secrets": required, "secret_status": secret_status,
                "updated_at": row["updated_at"],
            })
        return result

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

    def create_handoff(self, task_id: str, proposal: TaskProposal) -> str:
        """Pause execution on a precise human-only browser/account checkpoint."""
        handoff_id = str(uuid4())
        now = utc_now()
        self.db.execute(
            "INSERT INTO human_handoffs VALUES(?,?,?,?,?,?,?)",
            (handoff_id, task_id, proposal.model_dump_json(), "pending", None, now, None),
        )
        self.db.execute("UPDATE tasks SET status='waiting_human' WHERE id=?", (task_id,))
        self.db.execute(
            "UPDATE approvals SET status='waiting_human' WHERE task_id=? AND status='executing'",
            (task_id,),
        )
        self.db.execute(
            "UPDATE runtime_control SET state='waiting_human',detail=?,updated_at=? WHERE id=1",
            (proposal.title, now),
        )
        self.db.commit()
        self.audit("handoff.requested", {
            "handoff_id": handoff_id, "task_id": task_id,
            "url": proposal.handoff_url,
            "instructions": proposal.handoff_instructions,
            "resume_evidence": proposal.resume_evidence,
        })
        return handoff_id

    def list_handoffs(self, status: str | None = None) -> list[dict]:
        query = "SELECT * FROM human_handoffs"
        params: tuple = ()
        if status:
            query += " WHERE status=?"
            params = (status,)
        query += " ORDER BY created_at DESC LIMIT 30"
        return [dict(row) for row in self.db.execute(query, params)]

    def resolve_handoff(self, handoff_id: str, outcome: str, completed: bool) -> None:
        """Record human evidence and resume the CEO without pretending the agent did it."""
        if not outcome.strip():
            raise ValueError("A handoff outcome is required")
        row = self.db.execute(
            "SELECT task_id,status,payload_json FROM human_handoffs WHERE id=?", (handoff_id,)
        ).fetchone()
        if not row or row["status"] != "pending":
            raise RuntimeError("Pending handoff not found")
        now = utc_now()
        status = "completed" if completed else "cancelled"
        result = SpecialistResult(
            status="completed" if completed else "failed",
            summary=f"Human handoff {status}: {outcome.strip()}",
            evidence=[f"Stakeholder-reported handoff outcome: {outcome.strip()}"],
            recommendation="CEO should verify the evidence and choose the next action",
        )
        self.db.execute(
            "UPDATE human_handoffs SET status=?,outcome=?,resolved_at=? WHERE id=?",
            (status, outcome.strip(), now, handoff_id),
        )
        self.db.execute(
            "UPDATE tasks SET status=?,result_json=?,completed_at=? WHERE id=?",
            ("completed" if completed else "rejected", result.model_dump_json(), now, row["task_id"]),
        )
        self.db.execute(
            "UPDATE approvals SET status=? WHERE task_id=? AND status='waiting_human'",
            ("executed" if completed else "execution_cancelled", row["task_id"]),
        )
        message_id = str(uuid4())
        self.db.execute(
            "INSERT INTO stakeholder_messages VALUES(?,?,?,?,?,?,?)",
            (message_id, "handoff_result", outcome.strip(), "pending", None, now, None),
        )
        self.db.execute(
            "UPDATE runtime_control SET state='running',detail=?,updated_at=? WHERE id=1",
            (f"Human handoff {status}; CEO reconsideration queued", now),
        )
        self.db.commit()
        self.audit(f"handoff.{status}", {
            "handoff_id": handoff_id, "task_id": row["task_id"], "message_id": message_id,
        })

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

    def has_pending_equivalent_approval(self, proposal: TaskProposal) -> bool:
        """Prevent repeated stakeholder requests for the same external action."""
        for row in self.db.execute("SELECT payload_json FROM approvals WHERE status='pending'"):
            existing = TaskProposal.model_validate_json(row["payload_json"])
            if (existing.action == proposal.action
                    and existing.platform_candidate == proposal.platform_candidate
                    and existing.objective.strip().lower() == proposal.objective.strip().lower()):
                return True
        return False

    def stakeholder_notification_allowed(self, hours: int = 24) -> bool:
        """Rate-limit outbound attention requests while keeping dashboard state current."""
        row = self.db.execute(
            "SELECT created_at FROM audit_events WHERE event_type='stakeholder.notification_sent' "
            "ORDER BY created_at DESC LIMIT 1"
        ).fetchone()
        if not row:
            return True
        sent = datetime.fromisoformat(row["created_at"])
        return (datetime.now(timezone.utc) - sent).total_seconds() >= hours * 3600

    def list_approvals(self) -> list[dict]:
        """Return approval history, newest first."""
        return [dict(row) for row in self.db.execute("SELECT * FROM approvals ORDER BY created_at DESC")]

    def pending_approval_details(self) -> list[dict]:
        """Return frozen pending proposals ready for a consolidated brief."""
        result = []
        for row in self.db.execute("SELECT * FROM approvals WHERE status='pending' ORDER BY created_at"):
            item = dict(row)
            item["proposal"] = TaskProposal.model_validate_json(item.pop("payload_json"))
            result.append(item)
        return result

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
            "INSERT INTO company_profile(id,profile_json,updated_at) VALUES(1,?,?) "
            "ON CONFLICT(id) DO UPDATE SET profile_json=excluded.profile_json,updated_at=excluded.updated_at",
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
        if state not in {"running", "paused", "stopped", "waiting_approval", "waiting_human", "error"}:
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
            handoffs = list(self.db.execute(
                "SELECT id,task_id FROM human_handoffs WHERE status='pending'"
            ))
            for row in handoffs:
                self.db.execute(
                    "UPDATE human_handoffs SET status='superseded',resolved_at=? WHERE id=?",
                    (utc_now(), row["id"]),
                )
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
        snapshot["human_handoffs"] = self.list_handoffs()
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
        authorized_spend = float(self.db.execute(
            "SELECT COALESCE(SUM(amount_eur),0) AS value FROM ledger"
        ).fetchone()["value"])
        usage = dict(self.db.execute(
            "SELECT COALESCE(SUM(requests),0) requests,COALESCE(SUM(input_tokens),0) input_tokens,"
            "COALESCE(SUM(cached_tokens),0) cached_tokens,COALESCE(SUM(output_tokens),0) output_tokens,"
            "COALESCE(SUM(reasoning_tokens),0) reasoning_tokens,COALESCE(SUM(total_tokens),0) total_tokens,"
            "COALESCE(SUM(estimated_usd),0) estimated_usd,COALESCE(SUM(estimated_budget_cost),0) estimated_budget_cost,"
            "SUM(CASE WHEN pricing_status='unknown_model' THEN 1 ELSE 0 END) unpriced_calls FROM model_usage"
        ).fetchone())
        # Browser mission state is a read model rebuilt from append-only audit
        # events. The runner therefore needs no second mutable status record that
        # could drift from the task/approval lifecycle after a crash.
        browser_events = [event for event in events if event["event_type"].startswith("browser.mission_")]
        latest_start = next((event for event in browser_events
                             if event["event_type"] == "browser.mission_started"), None)
        mission = None
        if latest_start:
            task_id = latest_start["payload"].get("task_id")
            related = [event for event in browser_events if event["payload"].get("task_id") == task_id]
            terminal = next((event for event in related if event["event_type"] in {
                "browser.mission_completed", "browser.mission_handoff"
            } or (event["event_type"] == "browser.mission_stopped"
                  and event["payload"].get("status") != "step_limit")), None)
            last_action = next((event for event in related
                                if event["event_type"] == "browser.mission_action"), None)
            last_stop = next((event for event in related
                              if event["event_type"] == "browser.mission_stopped"), None)
            mission = {
                "task_id": task_id,
                "status": (last_stop["payload"].get("status") if last_stop else
                           "completed" if terminal and terminal["event_type"] == "browser.mission_completed" else
                           "waiting_human" if terminal and terminal["event_type"] == "browser.mission_handoff" else
                           "running"),
                "objective": latest_start["payload"].get("objective"),
                "allowed_domains": latest_start["payload"].get("allowed_domains", []),
                "max_steps": latest_start["payload"].get("max_steps"),
                "step": (last_action or last_stop or {"payload": {}})["payload"].get("step",
                         (last_stop or {"payload": {}})["payload"].get("steps", 0)),
                "last_action": last_action["payload"] if last_action else None,
                "detail": last_stop["payload"].get("summary") if last_stop else None,
                "started_at": latest_start["created_at"],
            }
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
                "token_usage": usage,
            },
            "tasks_by_status": task_counts,
            "authorized_spend_eur": authorized_spend,
            "api_spend_eur": usage["estimated_budget_cost"],
            "estimated_spend_eur": authorized_spend + usage["estimated_budget_cost"],
            "recent_events": events[:40],
            "browser_mission": mission,
        }

    def record_model_usage(self, payload: dict) -> None:
        """Persist one idempotent usage record without prompts or secrets."""
        self.db.execute(
            "INSERT INTO model_usage VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(run_id) DO NOTHING",
            (payload["run_id"], payload["provider"], payload["model"], payload["requests"],
             payload["input_tokens"], payload["cached_tokens"], payload["output_tokens"],
             payload["reasoning_tokens"], payload["total_tokens"], payload.get("estimated_usd"),
             payload.get("estimated_budget_cost"), payload["pricing_status"], utc_now()),
        )
        self.db.commit()

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
