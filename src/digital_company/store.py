"""SQLite persistence for one isolated digital company.

SQLite is the POC durability layer. The schema deliberately separates company
facts, tasks, approvals, budget ledger entries, stakeholder messages, runtime
control, model settings, and append-only audit events. A production deployment
can preserve these interfaces while replacing SQLite with PostgreSQL.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlparse
from uuid import uuid4

from digital_company.company_schema import migrate_company_database
from digital_company.capability_plugins import BUILTIN_PLUGINS, validate_plugin_config
from digital_company.agent_templates import validate_agent_config
from digital_company.models import ActionType, CompanySnapshot, PolicyDocument, SpecialistResult, TaskProposal
from digital_company.operations_projection import decode_audit_events, project_operations
from digital_company.postgres_compat import PostgresCompat, company_schema
from digital_company.skill_catalog import BUILTIN_SKILLS
from digital_company.skill_selection import select_skills
from digital_company.integration_connectors import (
    ADAPTERS as INTEGRATION_ADAPTERS,
    secret_name as integration_secret_name,
    validate_connection,
)
from digital_company.runtime_secrets import get_secret


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

    def close(self) -> None:
        """Release the owned database connection."""
        self.db.close()

    def __enter__(self) -> CompanyStore:
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        self.close()

    def _migrate(self) -> None:
        """Create additive POC tables and idempotent singleton defaults."""
        migrate_company_database(self.db, utc_now())
        self._ensure_default_policy()
        self._sync_builtin_skills()
        self._sync_builtin_plugins()
        # Existing single-loop companies need a projected CEO during the
        # additive migration. A brand-new company database is intentionally
        # left without agents so organization creation and hiring are separate.
        if (
            self.is_initialized()
            and self.get_profile().get("_agent_bootstrap_mode") != "explicit"
        ):
            self._ensure_legacy_ceo_agent()

    def _ensure_default_policy(self) -> None:
        """Bootstrap a safe policy without rewriting an existing company's rules."""
        self.db.execute(
            "INSERT OR IGNORE INTO policy_versions(version,document_json,status,created_at,created_by) "
            "VALUES(1,?,'active',?,'system')",
            (PolicyDocument().model_dump_json(), utc_now()),
        )
        self.db.commit()

    def get_policy(self) -> dict:
        """Return the active immutable policy version and its validated document."""
        row = self.db.execute(
            "SELECT * FROM policy_versions WHERE status='active' ORDER BY version DESC LIMIT 1"
        ).fetchone()
        if not row:
            self._ensure_default_policy()
            row = self.db.execute(
                "SELECT * FROM policy_versions WHERE status='active' ORDER BY version DESC LIMIT 1"
            ).fetchone()
        document = PolicyDocument.model_validate_json(row["document_json"])
        return {
            "version": row["version"], "status": row["status"],
            "created_at": row["created_at"], "created_by": row["created_by"],
            "document": document.model_dump(mode="json"),
        }

    def set_policy(self, document: PolicyDocument | dict, created_by: str = "dashboard") -> dict:
        """Create a new policy version; prior versions remain available for audit."""
        validated = PolicyDocument.model_validate(document)
        if ActionType.SIGN_CONTRACT not in validated.deny_actions:
            validated.deny_actions.append(ActionType.SIGN_CONTRACT)
        self.db.execute("BEGIN IMMEDIATE")
        try:
            if getattr(self.db, "is_postgres", False):
                self.db.execute("SELECT pg_advisory_xact_lock(hashtext('telekt-policy-version'))")
            row = self.db.execute(
                "SELECT COALESCE(MAX(version),0) AS value FROM policy_versions"
            ).fetchone()
            version = int(row["value"]) + 1
            now = utc_now()
            self.db.execute("UPDATE policy_versions SET status='superseded' WHERE status='active'")
            self.db.execute(
                "INSERT INTO policy_versions(version,document_json,status,created_at,created_by) "
                "VALUES(?,?,'active',?,?)",
                (version, validated.model_dump_json(), now, created_by.strip() or "dashboard"),
            )
            self.db.commit()
        except Exception:
            self.db.rollback()
            raise
        self.audit("policy.version_created", {"version": version, "created_by": created_by})
        return self.get_policy()

    def initialize(
        self, goal: str, budget: float, profile: dict | None = None,
        bootstrap_legacy_agent: bool = True,
    ) -> None:
        """Create or replace the singleton company header and optional profile."""
        self.db.execute(
            "INSERT INTO company(id, goal, initial_budget_eur, created_at) VALUES(1,?,?,?) "
            "ON CONFLICT(id) DO UPDATE SET goal=excluded.goal,initial_budget_eur=excluded.initial_budget_eur,created_at=excluded.created_at",
            (goal, budget, utc_now()),
        )
        if profile is not None:
            profile = dict(profile)
            if not bootstrap_legacy_agent:
                # Durable marker: reopening this brand-new company must not make
                # the migration path mistake it for a legacy single-loop tenant.
                profile["_agent_bootstrap_mode"] = "explicit"
            self.db.execute(
                "INSERT INTO company_profile(id,profile_json,updated_at) VALUES(1,?,?) "
                "ON CONFLICT(id) DO UPDATE SET profile_json=excluded.profile_json,updated_at=excluded.updated_at",
                (json.dumps(profile), utc_now()),
            )
            self.db.commit()
        if bootstrap_legacy_agent:
            self._ensure_legacy_ceo_agent()
        self.audit("company.initialized", {"goal": goal, "budget": budget})

    def is_initialized(self) -> bool:
        """Return whether the database contains a company header."""
        return self.db.execute("SELECT 1 FROM company WHERE id=1").fetchone() is not None

    def schema_version(self) -> int:
        """Return the highest recorded company schema version."""
        row = self.db.execute(
            "SELECT COALESCE(MAX(version),0) AS value FROM company_schema_versions"
        ).fetchone()
        return int(row["value"])

    def snapshot(self, agent_id: str | None = None) -> CompanySnapshot:
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
        failures = [dict(row) for row in self.db.execute(
            "SELECT id,action,title,specialist,status,result_json,completed_at FROM tasks "
            "WHERE status='failed' ORDER BY completed_at DESC LIMIT 8"
        )]
        for failure in failures:
            if failure["result_json"]:
                failure["result"] = json.loads(failure.pop("result_json"))
        self.expire_pending_approvals()
        approvals_query = (
            "SELECT a.id,a.task_id,a.status,a.reason,a.required_approvals,a.expires_at,"
            "(SELECT COUNT(*) FROM approval_votes v WHERE v.approval_id=a.id AND v.decision='approve') "
            "AS approval_count FROM approvals a JOIN tasks t ON t.id=a.task_id "
            "WHERE a.status='pending'"
        )
        approval_params: tuple = ()
        if agent_id:
            approvals_query += " AND t.agent_id=?"
            approval_params = (agent_id,)
        approvals_query += " ORDER BY a.created_at"
        approvals = [dict(row) for row in self.db.execute(approvals_query, approval_params)]
        evidence = []
        for task in tasks[-5:]:
            result = task.get("result") or {}
            evidence.extend({"task": task["title"], "evidence": item} for item in result.get("evidence", []))
        message_query = (
            "SELECT id,kind,content,status,response,created_at,addressed_at,agent_id "
            "FROM stakeholder_messages"
        )
        message_params: tuple = ()
        if agent_id:
            message_query += " WHERE agent_id=? OR agent_id IS NULL"
            message_params = (agent_id,)
        message_query += " ORDER BY created_at DESC LIMIT 20"
        messages = [dict(row) for row in self.db.execute(message_query, message_params)]
        return CompanySnapshot(
            goal=company["goal"], initial_budget_eur=company["initial_budget_eur"],
            spent_eur=spent, remaining_budget_eur=company["initial_budget_eur"] - spent,
            completed_tasks=tasks, pending_approvals=approvals, recent_evidence=evidence[-12:],
            recent_failures=failures,
            stakeholder_messages=messages,
            profile=self.get_profile(),
            capabilities=self.capability_snapshot(),
            human_handoffs=self.list_handoffs(status="pending", agent_id=agent_id),
            skills=self.list_skills(),
        )

    def _sync_builtin_skills(self) -> None:
        for skill in BUILTIN_SKILLS:
            self.db.execute(
                "INSERT INTO agent_skills(id,name,version,definition_json,status,updated_at) VALUES(?,?,?,?,?,?) "
                "ON CONFLICT(id) DO UPDATE SET name=excluded.name,version=excluded.version,"
                "definition_json=excluded.definition_json,updated_at=excluded.updated_at",
                (skill["id"], skill["name"], skill["version"], json.dumps(skill), "available", utc_now()),
            )
        self.db.commit()

    def _sync_builtin_plugins(self) -> None:
        """Install trusted plugin definitions without changing per-agent grants."""
        now = utc_now()
        for plugin in BUILTIN_PLUGINS:
            self.db.execute(
                "INSERT INTO capability_plugins(id,name,version,description,definition_json,status,created_at,updated_at) "
                "VALUES(?,?,?,?,?,'available',?,?) ON CONFLICT(id) DO UPDATE SET "
                "name=excluded.name,version=excluded.version,description=excluded.description,"
                "definition_json=excluded.definition_json,updated_at=excluded.updated_at",
                (
                    plugin["id"], plugin["name"], plugin["version"], plugin["description"],
                    json.dumps(plugin), now, now,
                ),
            )
        self.db.commit()

    def _ensure_legacy_ceo_agent(self) -> None:
        """Represent the existing company loop as an explicit agent instance."""
        if not self.is_initialized():
            return
        settings = self.get_settings()
        connection_id = (
            settings["local_connection_id"] if settings["model_mode"] == "local"
            else settings["cloud_connection_id"]
        )
        now = utc_now()
        goal = self.db.execute("SELECT goal FROM company WHERE id=1").fetchone()["goal"]
        self.db.execute(
            "INSERT INTO agent_instances(id,name,role,agent_type,purpose,instructions,model_connection_id,status,"
            "autonomy_mode,token_limit,spend_limit_eur,schedule_json,config_json,created_at,updated_at) "
            "VALUES('legacy-ceo','CEO','ceo','ceo',?,? ,?,'stopped','governed',NULL,NULL,'{}','{}',?,?) "
            "ON CONFLICT(id) DO UPDATE SET agent_type='ceo'",
            (
                goal,
                "Choose and coordinate the next useful company action within policy.",
                connection_id, now, now,
            ),
        )
        self.db.commit()

    def list_skills(self) -> list[dict]:
        result = []
        for row in self.db.execute("SELECT definition_json,status FROM agent_skills ORDER BY id"):
            item = json.loads(row["definition_json"])
            item["status"] = row["status"]
            result.append(item)
        return result

    def resolve_skills(self, skill_ids: list[str], specialist: str, action: str,
                       strict: bool = True) -> list[dict]:
        selection = select_skills(
            self.list_skills(), skill_ids, specialist, action, strict=strict,
        )
        if selection.rejected_ids:
            self.audit("skills.selection_adjusted", {
                "specialist": specialist, "action": action,
                "rejected": selection.rejected_ids,
                "assigned": [item["id"] for item in selection.assigned],
            })
        return selection.assigned

    def close_orphaned_model_runs(self, reason: str) -> list[str]:
        """Append terminal events for model calls left open by process failure."""
        operations = self.operations_data()
        active = operations.get("active_model_run")
        if not active or not active.get("payload", {}).get("run_id"):
            return []
        run_id = active["payload"]["run_id"]
        self.audit("model.abandoned", {"run_id": run_id, "reason": reason})
        return [run_id]

    def set_skill_status(self, skill_id: str, status: str) -> None:
        if status not in {"available", "disabled", "missing_access"}:
            raise ValueError("Invalid skill status")
        changed = self.db.execute("UPDATE agent_skills SET status=?,updated_at=? WHERE id=?",
                                  (status, utc_now(), skill_id)).rowcount
        self.db.commit()
        if changed != 1:
            raise KeyError(skill_id)
        self.audit("skill.status_changed", {"skill_id": skill_id, "status": status})

    def list_agents(self) -> list[dict]:
        """Return independent agent instances with grants and metered usage."""
        result = []
        for row in self.db.execute("SELECT * FROM agent_instances ORDER BY created_at"):
            item = dict(row)
            item["schedule"] = json.loads(item.pop("schedule_json"))
            item["config"] = json.loads(item.pop("config_json"))
            item["plugins"] = self.list_agent_plugins(item["id"])
            usage = self.db.execute(
                "SELECT COALESCE(SUM(total_tokens),0) AS tokens,"
                "COALESCE(SUM(estimated_budget_cost),0) AS cost FROM model_usage WHERE agent_id=?",
                (item["id"],),
            ).fetchone()
            item["usage"] = {"tokens": int(usage["tokens"]), "estimated_cost": float(usage["cost"])}
            last_run = self.db.execute(
                "SELECT id,status,started_at,completed_at,error FROM agent_runs "
                "WHERE agent_id=? ORDER BY started_at DESC LIMIT 1", (item["id"],),
            ).fetchone()
            item["last_run"] = dict(last_run) if last_run else None
            item["tasks_by_status"] = {
                task["status"]: int(task["count"])
                for task in self.db.execute(
                    "SELECT status,COUNT(*) AS count FROM tasks WHERE agent_id=? GROUP BY status",
                    (item["id"],),
                )
            }
            item["attention"] = {
                "approvals": int(self.db.execute(
                    "SELECT COUNT(*) AS value FROM approvals a JOIN tasks t ON t.id=a.task_id "
                    "WHERE t.agent_id=? AND a.status='pending'", (item["id"],),
                ).fetchone()["value"]),
                "handoffs": int(self.db.execute(
                    "SELECT COUNT(*) AS value FROM human_handoffs h JOIN tasks t ON t.id=h.task_id "
                    "WHERE t.agent_id=? AND h.status='pending'", (item["id"],),
                ).fetchone()["value"]),
            }
            result.append(item)
        return result

    def get_agent(self, agent_id: str) -> dict:
        item = next((agent for agent in self.list_agents() if agent["id"] == agent_id), None)
        if not item:
            raise KeyError(agent_id)
        return item

    def create_agent(self, value: dict) -> dict:
        """Create one independently configurable digital employee."""
        agent_id = value.get("id") or str(uuid4())
        agent_type = value.get("agent_type", "custom")
        config = validate_agent_config(agent_type, value.get("config") or {})
        now = utc_now()
        self.db.execute(
            "INSERT INTO agent_instances(id,name,role,agent_type,purpose,instructions,model_connection_id,status,"
            "autonomy_mode,token_limit,spend_limit_eur,schedule_json,config_json,created_at,updated_at) "
            "VALUES(?,?,?,?,?,?,?,'stopped',?,?,?,?,?,?,?)",
            (
                agent_id, value["name"].strip(), value["role"].strip(), agent_type,
                value["purpose"].strip(),
                value.get("instructions", "").strip(), value["model_connection_id"],
                value.get("autonomy_mode", "governed"), value.get("token_limit"),
                value.get("spend_limit_eur"), json.dumps(value.get("schedule") or {}),
                json.dumps(config), now, now,
            ),
        )
        self.db.commit()
        self._adopt_explicit_agent_platform()
        self.audit("agent.created", {"agent_id": agent_id, "role": value["role"]})
        return self.get_agent(agent_id)

    def update_agent(self, agent_id: str, value: dict) -> dict:
        """Update configuration while preserving run and usage history."""
        if not self.db.execute("SELECT 1 FROM agent_instances WHERE id=?", (agent_id,)).fetchone():
            raise KeyError(agent_id)
        agent_type = value.get("agent_type", "custom")
        config = validate_agent_config(agent_type, value.get("config") or {})
        self.db.execute(
            "UPDATE agent_instances SET name=?,role=?,agent_type=?,purpose=?,instructions=?,model_connection_id=?,"
            "autonomy_mode=?,token_limit=?,spend_limit_eur=?,schedule_json=?,config_json=?,updated_at=? "
            "WHERE id=?",
            (
                value["name"].strip(), value["role"].strip(), agent_type, value["purpose"].strip(),
                value.get("instructions", "").strip(), value["model_connection_id"],
                value.get("autonomy_mode", "governed"), value.get("token_limit"),
                value.get("spend_limit_eur"), json.dumps(value.get("schedule") or {}),
                json.dumps(config), utc_now(), agent_id,
            ),
        )
        self.db.commit()
        if agent_id != "legacy-ceo":
            self._adopt_explicit_agent_platform()
        self.audit("agent.updated", {"agent_id": agent_id})
        return self.get_agent(agent_id)

    def _adopt_explicit_agent_platform(self) -> None:
        """Finish migration once the owner configures a real agent instance.

        The legacy CEO is only a compatibility projection. Remove it when it has
        no scoped history, but preserve it if it ever performed agent-owned work.
        The durable profile marker prevents a later store reopen from recreating
        the projection.
        """
        profile = self.get_profile()
        if profile.get("_agent_bootstrap_mode") != "explicit":
            profile["_agent_bootstrap_mode"] = "explicit"
            self.db.execute(
                "UPDATE company_profile SET profile_json=?,updated_at=? WHERE id=1",
                (json.dumps(profile, ensure_ascii=False), utc_now()),
            )
        references = 0
        for table in (
            "tasks", "ledger", "model_usage", "activity_executions",
            "stakeholder_messages", "agent_runs", "agent_plugin_grants",
        ):
            references += int(self.db.execute(
                f"SELECT COUNT(*) AS value FROM {table} WHERE agent_id='legacy-ceo'",
            ).fetchone()["value"])
        if references == 0:
            self.db.execute("DELETE FROM agent_instances WHERE id='legacy-ceo'")
        self.db.commit()

    def set_agent_status(self, agent_id: str, status: str) -> dict:
        """Persist an agent lifecycle projection.

        Operators normally select running/paused/stopped. Durable execution also
        projects waiting and error states so the UI never claims an agent is
        actively working while Temporal is actually waiting for an external
        event.
        """
        if status not in {
            "running", "paused", "stopped", "waiting_approval",
            "waiting_human", "error",
        }:
            raise ValueError("Invalid agent status")
        changed = self.db.execute(
            "UPDATE agent_instances SET status=?,updated_at=? WHERE id=?",
            (status, utc_now(), agent_id),
        ).rowcount
        self.db.commit()
        if changed != 1:
            raise KeyError(agent_id)
        self.audit("agent." + status, {"agent_id": agent_id})
        return self.get_agent(agent_id)

    def agent_remaining_budget(self, agent_id: str) -> float:
        """Return this agent's model-spend envelope, independent of company capital.

        Company capital describes business money. Agent spend limits describe AI
        inference cost, so a service company with no investment budget can still
        run a deliberately capped content agent.
        """
        agent = self.get_agent(agent_id)
        if agent["spend_limit_eur"] is None:
            return float("inf")
        spent = float(agent["usage"]["estimated_cost"])
        return float(agent["spend_limit_eur"]) - spent

    def agent_remaining_tokens(self, agent_id: str) -> int | None:
        agent = self.get_agent(agent_id)
        if agent["token_limit"] is None:
            return None
        return int(agent["token_limit"]) - int(agent["usage"]["tokens"])

    def begin_agent_run(self, agent_id: str, execution_key: str) -> str:
        """Create an auditable run identity; execution keys prevent duplicate starts."""
        existing = self.db.execute(
            "SELECT id FROM agent_runs WHERE execution_key=?", (execution_key,),
        ).fetchone()
        if existing:
            return existing["id"]
        run_id = str(uuid4())
        self.db.execute(
            "INSERT INTO agent_runs(id,agent_id,status,trigger_type,execution_key,started_at,completed_at,error) "
            "VALUES(?,?,'running','temporal',?,?,NULL,NULL)",
            (run_id, agent_id, execution_key, utc_now()),
        )
        self.db.commit()
        self.audit("agent.run_started", {"agent_id": agent_id, "run_id": run_id})
        return run_id

    def complete_agent_run(self, run_id: str, status: str, error: str | None = None) -> None:
        if status not in {"completed", "waiting", "paused", "stopped", "failed"}:
            raise ValueError("Invalid agent run status")
        self.db.execute(
            "UPDATE agent_runs SET status=?,completed_at=?,error=? WHERE id=? AND status='running'",
            (status, utc_now(), error, run_id),
        )
        self.db.commit()
        self.audit("agent.run_" + status, {"run_id": run_id, "error": error})

    def fail_agent_run_by_execution(self, execution_key: str, error: str) -> None:
        self.complete_agent_run_by_execution(execution_key, "failed", error)

    def complete_agent_run_by_execution(
        self, execution_key: str, status: str, error: str | None = None,
    ) -> None:
        """Close the one idempotent agent run owned by an activity execution."""
        row = self.db.execute(
            "SELECT id FROM agent_runs WHERE execution_key=? AND status='running'",
            (execution_key,),
        ).fetchone()
        if row:
            self.complete_agent_run(row["id"], status, error)

    def list_capability_plugins(self) -> list[dict]:
        result = []
        for row in self.db.execute("SELECT * FROM capability_plugins ORDER BY name"):
            item = dict(row)
            item["definition"] = json.loads(item.pop("definition_json"))
            result.append(item)
        return result

    def list_agent_plugins(self, agent_id: str) -> list[dict]:
        result = []
        rows = self.db.execute(
            "SELECT g.*,p.name,p.version,p.description,p.definition_json FROM agent_plugin_grants g "
            "JOIN capability_plugins p ON p.id=g.plugin_id WHERE g.agent_id=? ORDER BY p.name",
            (agent_id,),
        )
        for row in rows:
            item = dict(row)
            item["permissions"] = json.loads(item.pop("permissions_json"))
            item["config"] = json.loads(item.pop("config_json"))
            item["definition"] = json.loads(item.pop("definition_json"))
            result.append(item)
        return result

    def grant_agent_plugin(self, agent_id: str, plugin_id: str, value: dict) -> dict:
        """Grant only declared plugin permissions and validate its typed config."""
        self.get_agent(agent_id)
        row = self.db.execute(
            "SELECT definition_json FROM capability_plugins WHERE id=? AND status='available'",
            (plugin_id,),
        ).fetchone()
        if not row:
            raise KeyError(plugin_id)
        definition = json.loads(row["definition_json"])
        allowed = set(definition.get("permissions") or [])
        permissions = list(dict.fromkeys(value.get("permissions") or []))
        invalid = set(permissions) - allowed
        if invalid:
            raise ValueError("Plugin does not declare permission(s): " + ", ".join(sorted(invalid)))
        connection_id = value.get("connection_id")
        if connection_id:
            connection = self.get_integration_connection(connection_id)
            accepted_kinds = set(definition.get("connection_kinds") or [])
            if connection["adapter"] not in accepted_kinds:
                raise ValueError(
                    f"Connection adapter {connection['adapter']} is not supported by this plugin"
                )
            if connection["status"] != "ready":
                raise ValueError(f"Plugin connection is not ready: {connection['status']}")
            capability_map = definition.get("connection_capabilities") or {}
            required_capabilities = {
                capability_map[permission] for permission in permissions
                if permission in capability_map
            }
            missing_capabilities = required_capabilities - set(connection["capabilities"])
            if missing_capabilities:
                raise ValueError(
                    "Connection is missing capability/capabilities: "
                    + ", ".join(sorted(missing_capabilities))
                )
        config = validate_plugin_config(definition, value.get("config") or {})
        now = utc_now()
        self.db.execute(
            "INSERT INTO agent_plugin_grants(agent_id,plugin_id,connection_id,permissions_json,config_json,"
            "status,created_at,updated_at) VALUES(?,?,?,?,?,'enabled',?,?) ON CONFLICT(agent_id,plugin_id) "
            "DO UPDATE SET connection_id=excluded.connection_id,permissions_json=excluded.permissions_json,"
            "config_json=excluded.config_json,status='enabled',updated_at=excluded.updated_at",
            (
                agent_id, plugin_id, connection_id, json.dumps(permissions),
                json.dumps(config, ensure_ascii=False), now, now,
            ),
        )
        self.db.commit()
        self.audit("agent.plugin_granted", {
            "agent_id": agent_id, "plugin_id": plugin_id, "permissions": permissions,
        })
        return next(item for item in self.list_agent_plugins(agent_id) if item["plugin_id"] == plugin_id)

    def revoke_agent_plugin(self, agent_id: str, plugin_id: str) -> None:
        changed = self.db.execute(
            "UPDATE agent_plugin_grants SET status='disabled',updated_at=? "
            "WHERE agent_id=? AND plugin_id=?", (utc_now(), agent_id, plugin_id),
        ).rowcount
        self.db.commit()
        if changed != 1:
            raise KeyError(plugin_id)
        self.audit("agent.plugin_revoked", {"agent_id": agent_id, "plugin_id": plugin_id})

    def get_profile(self) -> dict:
        """Return user-configurable company creation parameters."""
        row = self.db.execute("SELECT profile_json FROM company_profile WHERE id=1").fetchone()
        return json.loads(row["profile_json"]) if row else {}

    def update_profile(self, profile: dict) -> dict:
        """Replace the editable brief without changing accounting history."""
        if not self.is_initialized():
            raise RuntimeError("Company is not initialized")
        now = utc_now()
        self.db.execute("BEGIN IMMEDIATE")
        try:
            self.db.execute(
                "INSERT INTO company_profile(id,profile_json,updated_at) VALUES(1,?,?) "
                "ON CONFLICT(id) DO UPDATE SET profile_json=excluded.profile_json,updated_at=excluded.updated_at",
                (json.dumps(profile, ensure_ascii=False), now),
            )
            self.db.execute(
                "UPDATE company SET goal=? WHERE id=1", (str(profile.get("goal", "")).strip(),)
            )
            self.db.commit()
        except Exception:
            self.db.rollback()
            raise
        self.audit("company.profile_updated", {
            "workflow_type": profile.get("workflow_type", "general"),
            "publishing_configured": bool(profile.get("publishing_url")),
        })
        return self.get_profile()

    def reset_attention_queue(self, reason: str) -> dict:
        """Supersede stale decisions when the owner replaces the operating brief."""
        now = utc_now()
        self.db.execute("BEGIN IMMEDIATE")
        try:
            approvals = list(self.db.execute(
                "SELECT id,task_id FROM approvals WHERE status IN ('pending','approved','waiting_human')"
            ))
            handoffs = list(self.db.execute(
                "SELECT id,task_id FROM human_handoffs WHERE status='pending'"
            ))
            for row in approvals:
                self.db.execute(
                    "UPDATE approvals SET status='superseded',resolved_at=?,decision_comment=?,decided_by='owner' "
                    "WHERE id=?", (now, reason, row["id"]),
                )
                self.db.execute(
                    "UPDATE tasks SET status='superseded',completed_at=? WHERE id=? "
                    "AND status IN ('proposed','waiting_approval','approved','waiting_human')",
                    (now, row["task_id"]),
                )
            for row in handoffs:
                self.db.execute(
                    "UPDATE human_handoffs SET status='superseded',outcome=?,resolved_at=? WHERE id=?",
                    (reason, now, row["id"]),
                )
                self.db.execute(
                    "UPDATE tasks SET status='superseded',completed_at=? WHERE id=? "
                    "AND status IN ('proposed','waiting_human')", (now, row["task_id"]),
                )
            self.db.execute(
                "UPDATE runtime_control SET state='stopped',detail=?,updated_at=? WHERE id=1",
                ("Operating brief updated; ready for a clean start", now),
            )
            self.db.commit()
        except Exception:
            self.db.rollback()
            raise
        result = {"approvals": len(approvals), "handoffs": len(handoffs)}
        self.audit("company.attention_queue_reset", {**result, "reason": reason})
        return result

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

    def upsert_integration_connection(self, payload: dict) -> dict:
        """Persist one transport profile without accepting credential values."""
        value = validate_connection(payload)
        now = utc_now()
        status = "configured" if value["enabled"] else "disabled"
        self.db.execute(
            "INSERT INTO integration_connections(id,name,adapter,provider,location,base_url,status,"
            "capabilities_json,config_json,credential_fields_json,created_at,updated_at) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET "
            "name=excluded.name,adapter=excluded.adapter,provider=excluded.provider,"
            "location=excluded.location,base_url=excluded.base_url,status=excluded.status,"
            "capabilities_json=excluded.capabilities_json,config_json=excluded.config_json,"
            "credential_fields_json=excluded.credential_fields_json,updated_at=excluded.updated_at",
            (
                value["id"], value["name"], value["adapter"], value["provider"],
                value["location"], value["base_url"], status,
                json.dumps(value["capabilities"]), json.dumps(value["config"]),
                json.dumps(value["credential_fields"]), now, now,
            ),
        )
        self.db.commit()
        self.audit("integration.connection_configured", {
            "connection_id": value["id"], "adapter": value["adapter"],
            "provider": value["provider"], "capabilities": value["capabilities"],
        })
        return self.get_integration_connection(value["id"])

    def list_integration_connections(self) -> list[dict]:
        """Return provider-neutral profiles with boolean-only credential state."""
        values = []
        for row in self.db.execute(
            "SELECT * FROM integration_connections ORDER BY name,id"
        ):
            fields = json.loads(row["credential_fields_json"])
            secret_status = {
                field: bool(get_secret(integration_secret_name(row["id"], field)))
                for field in fields
            }
            configured = row["status"] == "configured"
            config = json.loads(row["config_json"])
            no_auth_smtp = (
                row["adapter"] == "smtp"
                and str(config.get("authentication", "password")).lower() == "none"
            )
            ready = configured and (no_auth_smtp or all(secret_status.values()))
            authorization_required = (
                row["adapter"] == "oauth2_authorization_code"
                and not bool(config.get("authorized"))
            )
            effective = (
                "disabled" if not configured
                else "missing_credentials" if not ready
                else "authorization_required" if authorization_required
                else "ready"
            )
            values.append({
                "id": row["id"], "name": row["name"], "adapter": row["adapter"],
                "adapter_label": INTEGRATION_ADAPTERS[row["adapter"]]["label"],
                "provider": row["provider"], "location": row["location"],
                "base_url": row["base_url"], "status": effective,
                "capabilities": json.loads(row["capabilities_json"]),
                "config": config, "credential_fields": fields,
                "secret_status": secret_status, "updated_at": row["updated_at"],
            })
        return values

    def get_integration_connection(self, connection_id: str) -> dict:
        item = next((
            value for value in self.list_integration_connections()
            if value["id"] == connection_id
        ), None)
        if not item:
            raise KeyError(connection_id)
        return item

    def capability_snapshot(self) -> list[dict]:
        """Expose legacy platform requests and ready transport profiles to the CEO."""
        result = self.list_integrations()
        result.extend({
            "provider": item["provider"], "connection_id": item["id"],
            "adapter": item["adapter"], "status": item["status"],
            "config": {"capabilities": item["capabilities"], "base_url": item["base_url"]},
            "required_secrets": item["credential_fields"],
            "secret_status": item["secret_status"],
        } for item in self.list_integration_connections())
        return result

    def prepare_integration_operation(
        self, execution_key: str, connection_id: str, capability: str,
        method: str, path: str, request: dict,
    ) -> dict:
        """Freeze one connector call and derive its provider idempotency key."""
        connection = self.get_integration_connection(connection_id)
        if connection["status"] != "ready":
            raise RuntimeError(f"Integration connection is not ready: {connection['status']}")
        if capability not in connection["capabilities"]:
            raise ValueError("Connection does not grant the requested capability")
        method = method.upper()
        if method not in {"GET", "POST", "PUT", "PATCH", "DELETE"}:
            raise ValueError("Unsupported integration method")
        if not path.startswith("/") or "://" in path or "\x00" in path or len(path) > 1000:
            raise ValueError("Integration path must be a relative URL path")
        if not execution_key or len(execution_key) > 200:
            raise ValueError("A bounded execution key is required")
        canonical_request = json.dumps(request, sort_keys=True, separators=(",", ":"))
        if len(canonical_request) > 100_000:
            raise ValueError("Integration request is too large")
        provider_key = "telekt-" + hashlib.sha256(execution_key.encode()).hexdigest()[:40]
        existing = self.db.execute(
            "SELECT * FROM integration_operations WHERE execution_key=?", (execution_key,),
        ).fetchone()
        if existing:
            same = (
                existing["connection_id"] == connection_id
                and existing["capability"] == capability
                and existing["method"] == method
                and existing["path"] == path
                and existing["request_json"] == canonical_request
            )
            if not same:
                raise RuntimeError("Execution key was already used for a different integration operation")
            return {**dict(existing), "request": json.loads(existing["request_json"]), "cached": True}
        now = utc_now()
        self.db.execute(
            "INSERT INTO integration_operations VALUES(?,?,?,?,?,?,?,?,?,?,?)",
            (
                execution_key, connection_id, capability, method, path, canonical_request,
                provider_key, "prepared", None, now, now,
            ),
        )
        self.db.commit()
        self.audit("integration.operation_prepared", {
            "execution_key": execution_key, "connection_id": connection_id,
            "capability": capability, "method": method, "path": path,
            "provider_idempotency_key": provider_key,
        })
        return {
            "execution_key": execution_key, "connection_id": connection_id,
            "capability": capability, "method": method, "path": path,
            "request": request, "provider_idempotency_key": provider_key,
            "status": "prepared", "cached": False,
        }

    def complete_integration_operation(self, execution_key: str, response: dict) -> dict:
        """Persist a sanitized connector result so Temporal retries do not resend it."""
        encoded = json.dumps(response, ensure_ascii=False)
        if len(encoded) > 100_000:
            raise ValueError("Integration response is too large")
        changed = self.db.execute(
            "UPDATE integration_operations SET status='completed',response_json=?,updated_at=? "
            "WHERE execution_key=? AND status IN ('prepared','failed')",
            (encoded, utc_now(), execution_key),
        ).rowcount
        self.db.commit()
        row = self.db.execute(
            "SELECT status,response_json FROM integration_operations WHERE execution_key=?",
            (execution_key,),
        ).fetchone()
        if not row or (changed != 1 and row["status"] != "completed"):
            raise RuntimeError("Integration operation is not completable")
        return json.loads(row["response_json"])

    def fail_integration_operation(self, execution_key: str, reason: str) -> None:
        self.db.execute(
            "UPDATE integration_operations SET status='failed',response_json=?,updated_at=? "
            "WHERE execution_key=? AND status='prepared'",
            (json.dumps({"error": reason[:2000]}), utc_now(), execution_key),
        )
        self.db.commit()

    def begin_activity(self, execution_key: str, agent_id: str | None = None) -> dict | None:
        """Start/recover one Temporal activity or return its cached result."""
        row = self.db.execute(
            "SELECT status,task_id,result_json,agent_id FROM activity_executions WHERE execution_key=?",
            (execution_key,),
        ).fetchone()
        if row and row["agent_id"] != agent_id:
            raise RuntimeError("Activity execution key belongs to a different agent")
        now = utc_now()
        if row and row["status"] == "completed":
            return json.loads(row["result_json"])
        if row and row["task_id"]:
            task = self.db.execute("SELECT status FROM tasks WHERE id=?", (row["task_id"],)).fetchone()
            if task and task["status"] in {
                "completed", "denied", "stopped", "superseded", "waiting_approval", "waiting_human",
                "failed", "rejected",
            }:
                status_map = {
                    "waiting_approval": "waiting_for_approval",
                    "waiting_human": "waiting_for_human",
                    "completed": "recovered_task_completed",
                    "failed": "error",
                    "rejected": "failed",
                }
                result = {
                    "status": status_map.get(task["status"], task["status"]),
                    "cycles": 1, "task_id": row["task_id"], "recovered": True,
                }
                self.complete_activity(execution_key, result)
                return result
        if row:
            self.db.execute(
                "UPDATE activity_executions SET attempt_count=attempt_count+1,updated_at=? WHERE execution_key=?",
                (now, execution_key),
            )
        else:
            self.db.execute(
                "INSERT INTO activity_executions(execution_key,status,task_id,result_json,attempt_count,started_at,updated_at,agent_id) "
                "VALUES(?,?,?,?,?,?,?,?)",
                (execution_key, "started", None, None, 1, now, now, agent_id),
            )
        self.db.commit()
        return None

    def fail_activity(self, execution_key: str, reason: str) -> dict:
        """Finalize unfinished work after the final Temporal attempt."""
        now = utc_now()
        row = self.db.execute(
            "SELECT task_id FROM activity_executions WHERE execution_key=?", (execution_key,),
        ).fetchone()
        if row and row["task_id"]:
            result = SpecialistResult(
                status="failed", summary=reason, evidence=[], recommendation="Human review required",
            )
            self.db.execute(
                "UPDATE tasks SET status='failed',result_json=?,completed_at=? "
                "WHERE id=? AND status IN ('proposed','executing')",
                (result.model_dump_json(), now, row["task_id"]),
            )
            self.db.execute(
                "UPDATE approvals SET status='execution_failed' "
                "WHERE task_id=? AND status='executing'", (row["task_id"],),
            )
        outcome = {"status": "error", "cycles": 0, "error": reason}
        self.db.execute(
            "UPDATE activity_executions SET status='completed',result_json=?,updated_at=? "
            "WHERE execution_key=?", (json.dumps(outcome), now, execution_key),
        )
        self.db.commit()
        return outcome

    def complete_activity(self, execution_key: str, result: dict) -> None:
        """Cache the durable activity outcome before Temporal receives its ACK."""
        self.db.execute(
            "UPDATE activity_executions SET status='completed',result_json=?,updated_at=? WHERE execution_key=?",
            (json.dumps(result), utc_now(), execution_key),
        )
        self.db.commit()

    def recover_activity_task(self, execution_key: str) -> tuple[str, TaskProposal, str] | None:
        """Return unfinished frozen work belonging to a retried activity."""
        row = self.db.execute(
            "SELECT t.id,t.status,t.proposal_json FROM activity_executions e "
            "JOIN tasks t ON t.id=e.task_id WHERE e.execution_key=? "
            "AND t.status IN ('proposed','executing')", (execution_key,),
        ).fetchone()
        if not row or not row["proposal_json"]:
            return None
        return row["id"], TaskProposal.model_validate_json(row["proposal_json"]), row["status"]

    def link_activity_task(
        self, execution_key: str, task_id: str, proposal: TaskProposal | None = None,
    ) -> None:
        """Attach an already-created approved task to its current activity."""
        if proposal is not None:
            self.db.execute(
                "UPDATE tasks SET proposal_json=COALESCE(proposal_json,?) WHERE id=?",
                (proposal.model_dump_json(), task_id),
            )
        changed = self.db.execute(
            "UPDATE activity_executions SET task_id=?,updated_at=? "
            "WHERE execution_key=? AND (task_id IS NULL OR task_id=?)",
            (task_id, utc_now(), execution_key, task_id),
        ).rowcount
        if changed != 1:
            raise RuntimeError("Activity already owns a different task")
        self.db.commit()

    def create_task(
        self, proposal: TaskProposal, status: str, execution_key: str | None = None,
        agent_id: str | None = None,
    ) -> str:
        """Persist a CEO proposal before authorization or execution."""
        task_id = str(uuid4())
        self.db.execute(
            "INSERT INTO tasks(id,action,title,specialist,objective,rationale,estimated_cost_eur,status,"
            "result_json,created_at,completed_at,proposal_json,agent_id) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (task_id, proposal.action.value, proposal.title, proposal.specialist,
             proposal.objective, proposal.rationale, proposal.estimated_cost_eur,
             status, None, utc_now(), None, proposal.model_dump_json(), agent_id),
        )
        if execution_key:
            changed = self.db.execute(
                "UPDATE activity_executions SET task_id=?,updated_at=? "
                "WHERE execution_key=? AND task_id IS NULL",
                (task_id, utc_now(), execution_key),
            ).rowcount
            if changed != 1:
                self.db.rollback()
                raise RuntimeError("Activity already owns a different task")
        self.db.commit()
        self.audit("task.created", {"task_id": task_id, "proposal": proposal.model_dump(mode="json")})
        return task_id

    def create_handoff(self, task_id: str, proposal: TaskProposal) -> str:
        """Pause execution on a precise human-only browser/account checkpoint."""
        task = self.db.execute("SELECT agent_id FROM tasks WHERE id=?", (task_id,)).fetchone()
        agent_id = task["agent_id"] if task else None
        requested_host = urlparse(proposal.handoff_url).hostname if proposal.handoff_url else None
        for row in self.db.execute(
            "SELECT h.id,h.payload_json FROM human_handoffs h JOIN tasks t ON t.id=h.task_id "
            "WHERE h.status='pending' AND " + ("t.agent_id=?" if agent_id else "t.agent_id IS NULL") +
            " ORDER BY h.created_at", (agent_id,) if agent_id else (),
        ):
            existing = TaskProposal.model_validate_json(row["payload_json"])
            existing_host = urlparse(existing.handoff_url).hostname if existing.handoff_url else None
            if existing.action == proposal.action and existing_host == requested_host:
                self.db.execute(
                    "UPDATE tasks SET status='superseded',completed_at=? WHERE id=? AND status='proposed'",
                    (utc_now(), task_id),
                )
                self.db.commit()
                self.audit("handoff.duplicate_suppressed", {
                    "existing_handoff_id": row["id"], "task_id": task_id,
                })
                return row["id"]
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
        if agent_id:
            self.db.execute(
                "UPDATE agent_instances SET status='waiting_human',updated_at=? WHERE id=?",
                (now, agent_id),
            )
        else:
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

    def list_handoffs(
        self, status: str | None = None, agent_id: str | None = None,
    ) -> list[dict]:
        query = (
            "SELECT h.*,t.agent_id FROM human_handoffs h "
            "JOIN tasks t ON t.id=h.task_id"
        )
        clauses = []
        params: list = []
        if status:
            clauses.append("h.status=?")
            params.append(status)
        if agent_id:
            clauses.append("t.agent_id=?")
            params.append(agent_id)
        if clauses:
            query += " WHERE " + " AND ".join(clauses)
        query += " ORDER BY h.created_at DESC LIMIT 30"
        return [dict(row) for row in self.db.execute(query, tuple(params))]

    def get_handoff(self, handoff_id: str, require_pending: bool = False) -> dict:
        """Return one frozen handoff payload for an operator action."""
        row = self.db.execute(
            "SELECT * FROM human_handoffs WHERE id=?", (handoff_id,)
        ).fetchone()
        if not row:
            raise RuntimeError("Handoff not found")
        if require_pending and row["status"] != "pending":
            raise RuntimeError("Pending handoff not found")
        item = dict(row)
        item["proposal"] = TaskProposal.model_validate_json(item["payload_json"])
        return item

    def resolve_handoff(self, handoff_id: str, outcome: str, completed: bool) -> str | None:
        """Record human evidence and resume the CEO without pretending the agent did it."""
        if not outcome.strip():
            raise ValueError("A handoff outcome is required")
        row = self.db.execute(
            "SELECT h.task_id,h.status,h.payload_json,t.agent_id FROM human_handoffs h "
            "JOIN tasks t ON t.id=h.task_id WHERE h.id=?", (handoff_id,)
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
            "INSERT INTO stakeholder_messages(id,kind,content,status,response,created_at,addressed_at,agent_id) "
            "VALUES(?,?,?,?,?,?,?,?)",
            (
                message_id, "handoff_result", outcome.strip(), "pending", None, now, None,
                row["agent_id"],
            ),
        )
        if row["agent_id"]:
            self.db.execute(
                "UPDATE agent_instances SET status='running',updated_at=? WHERE id=?",
                (now, row["agent_id"]),
            )
        else:
            self.db.execute(
                "UPDATE runtime_control SET state='running',detail=?,updated_at=? WHERE id=1",
                (f"Human handoff {status}; CEO reconsideration queued", now),
            )
        self.db.commit()
        self.audit(f"handoff.{status}", {
            "handoff_id": handoff_id, "task_id": row["task_id"], "message_id": message_id,
        })
        return row["agent_id"]

    def complete_task(self, task_id: str, result: SpecialistResult, cost: float) -> None:
        """Atomically complete a task, its approval, and authorized ledger cost."""
        current = self.db.execute("SELECT status,agent_id FROM tasks WHERE id=?", (task_id,)).fetchone()
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
                "INSERT INTO ledger(id,task_id,amount_eur,description,created_at,agent_id) VALUES(?,?,?,?,?,?)",
                (str(uuid4()), task_id, cost, "Authorized task cost", utc_now(), current["agent_id"]),
            )
        self.db.commit()
        self.audit("task.completed", {"task_id": task_id, "result": result.model_dump(mode="json")})

    def fail_task(
        self, task_id: str, reason: str, result: SpecialistResult | None = None,
    ) -> None:
        """Finalize an attempted task as failed so it cannot execute twice."""
        now = utc_now()
        result = result or SpecialistResult(
            status="failed", summary=reason, evidence=[], recommendation="Human review required",
        )
        self.db.execute(
            "UPDATE tasks SET status='failed',result_json=?,completed_at=? "
            "WHERE id=? AND status IN ('proposed','executing')",
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

    def request_approval(
        self, task_id: str, proposal: TaskProposal, reason: str,
        required_approvals: int = 1, ttl_hours: int = 72,
    ) -> str:
        """Freeze the exact proposed payload as a pending human approval."""
        if not 1 <= required_approvals <= 20:
            raise ValueError("Approval quorum must be between 1 and 20")
        if not 1 <= ttl_hours <= 720:
            raise ValueError("Approval TTL must be between 1 and 720 hours")
        approval_id = str(uuid4())
        now = datetime.now(timezone.utc)
        expires_at = (now + timedelta(hours=ttl_hours)).isoformat()
        self.db.execute("BEGIN IMMEDIATE")
        try:
            self.db.execute(
                "INSERT INTO approvals(id,task_id,payload_json,status,reason,created_at,resolved_at,"
                "required_approvals,expires_at) VALUES(?,?,?,?,?,?,?,?,?)",
                (approval_id, task_id, proposal.model_dump_json(), "pending", reason,
                 now.isoformat(), None, required_approvals, expires_at),
            )
            changed = self.db.execute(
                "UPDATE tasks SET status='waiting_approval' WHERE id=? AND status='proposed'", (task_id,)
            ).rowcount
            if changed != 1:
                raise RuntimeError("Only a proposed task can request approval")
            self.db.commit()
        except Exception:
            self.db.rollback()
            raise
        self.audit("approval.requested", {
            "approval_id": approval_id, "task_id": task_id,
            "required_approvals": required_approvals, "expires_at": expires_at,
        })
        return approval_id

    def has_pending_equivalent_approval(
        self, proposal: TaskProposal, agent_id: str | None = None,
    ) -> bool:
        """Prevent repeated stakeholder requests for the same external action."""
        query = (
            "SELECT a.payload_json FROM approvals a JOIN tasks t ON t.id=a.task_id "
            "WHERE a.status='pending' AND " +
            ("t.agent_id=?" if agent_id else "t.agent_id IS NULL")
        )
        for row in self.db.execute(query, (agent_id,) if agent_id else ()):
            existing = TaskProposal.model_validate_json(row["payload_json"])
            if (
                existing.action != proposal.action
                or existing.platform_candidate != proposal.platform_candidate
            ):
                continue
            if proposal.action in {
                ActionType.BROWSER_OPERATE,
                ActionType.REQUEST_PLATFORM_ACCESS,
                ActionType.PUBLISH_CONTENT,
            }:
                existing_host = urlparse(existing.handoff_url).hostname if existing.handoff_url else None
                proposed_host = urlparse(proposal.handoff_url).hostname if proposal.handoff_url else None
                if existing_host == proposed_host:
                    return True
            if existing.objective.strip().lower() == proposal.objective.strip().lower():
                return True
        return False

    def latest_content_draft(self, agent_id: str | None = None) -> dict | None:
        """Return the latest completed content artifact for review and publishing."""
        query = (
            "SELECT id,title,result_json,completed_at FROM tasks "
            "WHERE action=? AND status='completed' AND result_json IS NOT NULL "
        )
        params: list = [ActionType.CREATE_CONTENT_DRAFT.value]
        if agent_id:
            query += "AND agent_id=? "
            params.append(agent_id)
        query += "ORDER BY completed_at DESC LIMIT 20"
        rows = self.db.execute(query, tuple(params))
        for row in rows:
            result = json.loads(row["result_json"])
            if result.get("artifact_path") and result.get("artifact_content"):
                return {
                    "task_id": row["id"], "title": row["title"],
                    "path": result["artifact_path"], "content": result["artifact_content"],
                    "summary": result.get("summary", ""),
                    "evidence": result.get("evidence", []),
                    "sources": result.get("sources", []),
                    "completed_at": row["completed_at"],
                }
        return None

    def _approval_review(self, item: dict) -> dict | None:
        """Attach the exact draft to publication approvals without changing authority."""
        try:
            proposal = TaskProposal.model_validate_json(item["payload_json"])
        except Exception:
            return None
        return (
            self.latest_content_draft(item.get("agent_id"))
            if proposal.action == ActionType.PUBLISH_CONTENT else None
        )

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
        self.expire_pending_approvals()
        result = [dict(row) for row in self.db.execute(
            "SELECT a.*,t.agent_id,(SELECT COUNT(*) FROM approval_votes v WHERE v.approval_id=a.id "
            "AND v.decision='approve') AS approval_count FROM approvals a "
            "JOIN tasks t ON t.id=a.task_id ORDER BY a.created_at DESC"
        )]
        for item in result:
            item["review"] = self._approval_review(item)
        return result

    def pending_approval_details(self) -> list[dict]:
        """Return frozen pending proposals ready for a consolidated brief."""
        self.expire_pending_approvals()
        result = []
        for row in self.db.execute(
            "SELECT a.*,t.agent_id,(SELECT COUNT(*) FROM approval_votes v WHERE v.approval_id=a.id "
            "AND v.decision='approve') AS approval_count FROM approvals a "
            "JOIN tasks t ON t.id=a.task_id WHERE a.status='pending' ORDER BY a.created_at"
        ):
            item = dict(row)
            item["proposal"] = TaskProposal.model_validate_json(item.pop("payload_json"))
            item["review"] = (
                self.latest_content_draft(item.get("agent_id"))
                if item["proposal"].action == ActionType.PUBLISH_CONTENT else None
            )
            result.append(item)
        return result

    def expire_pending_approvals(self) -> list[str]:
        """Close approvals after their policy TTL so stale authority cannot be used."""
        now = datetime.now(timezone.utc)
        expired = []
        self.db.execute("BEGIN IMMEDIATE")
        try:
            rows = self.db.execute(
                "SELECT id,task_id,expires_at FROM approvals "
                "WHERE status='pending' AND expires_at IS NOT NULL"
            ).fetchall()
            for row in rows:
                if datetime.fromisoformat(row["expires_at"]) > now:
                    continue
                changed = self.db.execute(
                    "UPDATE approvals SET status='expired',resolved_at=?,decision_comment=?,decided_by='system' "
                    "WHERE id=? AND status='pending'",
                    (now.isoformat(), "Approval window expired", row["id"]),
                ).rowcount
                if changed != 1:
                    continue
                self.db.execute(
                    "UPDATE tasks SET status='rejected',completed_at=? "
                    "WHERE id=? AND status='waiting_approval'",
                    (now.isoformat(), row["task_id"]),
                )
                expired.append(row["id"])
            self.db.commit()
        except Exception:
            self.db.rollback()
            raise
        for approval_id in expired:
            self.audit("approval.expired", {"approval_id": approval_id})
        return expired

    def approve(self, approval_id: str, comment: str = "", decided_by: str = "dashboard") -> dict:
        """Record a distinct approval vote and release only after quorum is reached."""
        self.expire_pending_approvals()
        voter = decided_by.strip().lower() or "dashboard"
        now = utc_now()
        self.db.execute("BEGIN IMMEDIATE")
        try:
            lock = " FOR UPDATE" if getattr(self.db, "is_postgres", False) else ""
            row = self.db.execute(
                "SELECT a.task_id,a.status,a.required_approvals,t.agent_id FROM approvals a "
                "JOIN tasks t ON t.id=a.task_id WHERE a.id=?" + lock,
                (approval_id,),
            ).fetchone()
            if not row:
                raise RuntimeError("Approval not found.")
            if row["status"] != "pending":
                raise RuntimeError("Approval has already been resolved.")
            existing = self.db.execute(
                "SELECT decision FROM approval_votes WHERE approval_id=? AND voter=?",
                (approval_id, voter),
            ).fetchone()
            if existing:
                raise RuntimeError("This approver has already voted.")
            self.db.execute(
                "INSERT INTO approval_votes(approval_id,voter,decision,comment,created_at) VALUES(?,?,?,?,?)",
                (approval_id, voter, "approve", comment.strip() or None, now),
            )
            vote_count = int(self.db.execute(
                "SELECT COUNT(*) AS value FROM approval_votes WHERE approval_id=? AND decision='approve'",
                (approval_id,),
            ).fetchone()["value"])
            required = int(row["required_approvals"])
            final = vote_count >= required
            if final:
                self.db.execute(
                    "UPDATE approvals SET status='approved',resolved_at=?,decision_comment=?,decided_by=? "
                    "WHERE id=? AND status='pending'",
                    (now, comment.strip() or None, voter, approval_id),
                )
                self.db.execute("UPDATE tasks SET status='approved' WHERE id=?", (row["task_id"],))
                if row["agent_id"]:
                    self.db.execute(
                        "UPDATE agent_instances SET status='running',updated_at=? WHERE id=?",
                        (now, row["agent_id"]),
                    )
                else:
                    self.db.execute(
                        "UPDATE runtime_control SET state='running',detail=?,updated_at=? WHERE id=1",
                        ("Approval quorum reached; task queued for execution", now),
                    )
            self.db.commit()
        except Exception:
            self.db.rollback()
            raise
        event = "approval.approved" if final else "approval.vote_recorded"
        self.audit(event, {
            "approval_id": approval_id, "task_id": row["task_id"],
            "voter": voter, "approval_count": vote_count, "required_approvals": required,
        })
        if comment.strip():
            self.add_approval_feedback(comment, approval_id, voter, "approved")
        result = {
            "status": "approved" if final else "pending",
            "approval_count": vote_count, "required_approvals": required,
        }
        if row["agent_id"]:
            result["agent_id"] = row["agent_id"]
        return result

    def claim_approved_task(self, agent_id: str | None = None) -> tuple[str, TaskProposal] | None:
        """Atomically claim the oldest frozen approved payload exactly once."""
        self.expire_pending_approvals()
        self.db.execute("BEGIN IMMEDIATE")
        try:
            row = self.db.execute(
                "SELECT a.task_id,a.payload_json FROM approvals a JOIN tasks t ON t.id=a.task_id "
                "WHERE a.status='approved' AND t.status='approved' AND " +
                ("t.agent_id=?" if agent_id else "t.agent_id IS NULL") +
                " ORDER BY a.created_at LIMIT 1",
                (agent_id,) if agent_id else (),
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
        self.expire_pending_approvals()
        voter = decided_by.strip().lower() or "dashboard"
        now = utc_now()
        self.db.execute("BEGIN IMMEDIATE")
        try:
            lock = " FOR UPDATE" if getattr(self.db, "is_postgres", False) else ""
            row = self.db.execute(
                "SELECT a.task_id,a.status,t.agent_id FROM approvals a "
                "JOIN tasks t ON t.id=a.task_id WHERE a.id=?" + lock, (approval_id,)
            ).fetchone()
            if not row or row["status"] != "pending":
                raise RuntimeError("Pending approval not found.")
            if self.db.execute(
                "SELECT 1 FROM approval_votes WHERE approval_id=? AND voter=?", (approval_id, voter)
            ).fetchone():
                raise RuntimeError("This approver has already voted.")
            self.db.execute(
                "INSERT INTO approval_votes(approval_id,voter,decision,comment,created_at) VALUES(?,?,?,?,?)",
                (approval_id, voter, "reject", comment.strip(), now),
            )
            self.db.execute(
                "UPDATE approvals SET status='rejected',resolved_at=?,decision_comment=?,decided_by=? "
                "WHERE id=? AND status='pending'",
                (now, comment.strip(), voter, approval_id),
            )
            self.db.execute("UPDATE tasks SET status='rejected' WHERE id=?", (row["task_id"],))
            if row["agent_id"]:
                self.db.execute(
                    "UPDATE agent_instances SET status='running',updated_at=? WHERE id=?",
                    (now, row["agent_id"]),
                )
            else:
                self.db.execute(
                    "UPDATE runtime_control SET state='running',detail=?,updated_at=? WHERE id=1",
                    ("Rejected task; queued for CEO reconsideration", now),
                )
            self.db.commit()
        except Exception:
            self.db.rollback()
            raise
        self.audit("approval.rejected", {"approval_id": approval_id, "task_id": row["task_id"]})
        self.add_approval_feedback(comment, approval_id, voter, "rejected")
        return row["agent_id"]

    def add_approval_feedback(self, comment: str, approval_id: str, author: str, decision: str) -> None:
        """Put human decision context into the CEO/specialist canonical snapshot."""
        message_id = str(uuid4())
        content = f"Approval {decision} by {author}: {comment.strip()}"
        task = self.db.execute(
            "SELECT t.agent_id FROM approvals a JOIN tasks t ON t.id=a.task_id WHERE a.id=?",
            (approval_id,),
        ).fetchone()
        self.db.execute(
            "INSERT INTO stakeholder_messages(id,kind,content,status,response,created_at,addressed_at,agent_id) "
            "VALUES(?,?,?,?,?,?,?,?)",
            (
                message_id, "approval_feedback", content, "pending", None, utc_now(), None,
                task["agent_id"] if task else None,
            ),
        )
        self.db.commit()
        self.audit("approval.feedback", {"approval_id": approval_id, "message_id": message_id})

    def get_email_settings(self) -> dict:
        """Return non-secret, per-company email notification preferences."""
        profile = self.get_profile()
        connection_id = profile.get("approval_smtp_connection_id") or None
        connection = None
        if connection_id:
            try:
                candidate = self.get_integration_connection(connection_id)
                if candidate["adapter"] == "smtp":
                    connection = candidate
            except KeyError:
                connection = None
        return {
            "enabled": bool(profile.get("approval_email_enabled", False)),
            "approvers": profile.get("approval_emails", []),
            "sender_name": profile.get("approval_sender_name", profile.get("name", "Digital Company")),
            "smtp_connection_id": connection_id,
            "smtp_connection": connection,
            "from_address": profile.get("approval_from_address", ""),
            "public_base_url": profile.get("approval_public_base_url", ""),
            "environment_fallback_ready": bool(os.getenv("SMTP_HOST") and os.getenv("SMTP_FROM")),
            "signing_secret_configured": bool(
                os.getenv("APPROVAL_SIGNING_SECRET") or get_secret("APPROVAL_SIGNING_SECRET")
            ),
        }

    def set_email_settings(
        self, enabled: bool, approvers: list[str], sender_name: str,
        smtp_connection_id: str | None = None, from_address: str = "",
        public_base_url: str = "",
    ) -> dict:
        """Persist recipients and a reference to write-only SMTP credentials."""
        profile = self.get_profile()
        profile["approval_email_enabled"] = enabled
        profile["approval_emails"] = approvers
        profile["approval_sender_name"] = sender_name.strip() or profile.get("name", "Digital Company")
        profile["approval_smtp_connection_id"] = smtp_connection_id or ""
        profile["approval_from_address"] = from_address.strip()
        profile["approval_public_base_url"] = public_base_url.strip().rstrip("/")
        self.db.execute(
            "INSERT INTO company_profile(id,profile_json,updated_at) VALUES(1,?,?) "
            "ON CONFLICT(id) DO UPDATE SET profile_json=excluded.profile_json,updated_at=excluded.updated_at",
            (json.dumps(profile), utc_now()),
        )
        self.db.commit()
        self.audit("email.settings", {
            "enabled": enabled, "approver_count": len(approvers),
            "smtp_connection_id": smtp_connection_id,
        })
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

    def add_stakeholder_message(
        self, content: str, kind: str = "directive", agent_id: str | None = None,
    ) -> str:
        """Persist an owner message and supersede stale approvals for directives.

        A directive changes the decision context, so an approval produced before
        that intervention must not remain executable with stale assumptions.
        """
        if kind not in {"directive", "question"}:
            raise ValueError("Invalid stakeholder message kind")
        if agent_id:
            self.get_agent(agent_id)
        message_id = str(uuid4())
        self.db.execute(
            "INSERT INTO stakeholder_messages(id,kind,content,status,response,created_at,addressed_at,agent_id) "
            "VALUES(?,?,?,?,?,?,?,?)",
            (message_id, kind, content, "pending", None, utc_now(), None, agent_id),
        )
        if kind == "directive":
            task_scope = " AND t.agent_id=?" if agent_id else ""
            pending = list(self.db.execute(
                "SELECT a.id,a.task_id FROM approvals a JOIN tasks t ON t.id=a.task_id "
                "WHERE a.status IN ('pending','approved')" + task_scope,
                (agent_id,) if agent_id else (),
            ))
            for row in pending:
                self.db.execute("UPDATE approvals SET status='superseded',resolved_at=? WHERE id=?", (utc_now(), row["id"]))
                self.db.execute("UPDATE tasks SET status='superseded' WHERE id=?", (row["task_id"],))
            handoffs = list(self.db.execute(
                "SELECT h.id,h.task_id FROM human_handoffs h JOIN tasks t ON t.id=h.task_id "
                "WHERE h.status='pending'" + task_scope,
                (agent_id,) if agent_id else (),
            ))
            for row in handoffs:
                self.db.execute(
                    "UPDATE human_handoffs SET status='superseded',resolved_at=? WHERE id=?",
                    (utc_now(), row["id"]),
                )
                self.db.execute("UPDATE tasks SET status='superseded' WHERE id=?", (row["task_id"],))
        self.db.commit()
        self.audit("stakeholder.message", {
            "message_id": message_id, "kind": kind, "agent_id": agent_id,
        })
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
        snapshot["policy"] = self.get_policy()
        snapshot["profile"] = self.get_profile()
        snapshot["approvals"] = self.list_approvals()[:20]
        snapshot["human_handoffs"] = self.list_handoffs()
        snapshot["integration_connections"] = self.list_integration_connections()
        snapshot["agents"] = self.list_agents()
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
        return project_operations(
            decode_audit_events(rows), task_counts, authorized_spend, usage,
            stale_after_seconds=max(
                60, int(float(os.getenv("MODEL_TIMEOUT_SECONDS", "240"))) + 60
            ),
            control=self.get_control(),
        )

    def record_model_usage(self, payload: dict) -> None:
        """Persist one idempotent usage record without prompts or secrets."""
        self.db.execute(
            "INSERT INTO model_usage(run_id,provider,model,requests,input_tokens,cached_tokens,output_tokens,"
            "reasoning_tokens,total_tokens,estimated_usd,estimated_budget_cost,pricing_status,created_at,agent_id) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(run_id) DO NOTHING",
            (payload["run_id"], payload["provider"], payload["model"], payload["requests"],
             payload["input_tokens"], payload["cached_tokens"], payload["output_tokens"],
             payload["reasoning_tokens"], payload["total_tokens"], payload.get("estimated_usd"),
             payload.get("estimated_budget_cost"), payload["pricing_status"], utc_now(),
             payload.get("agent_id")),
        )
        self.db.commit()

    def get_settings(self) -> dict:
        """Return per-company model routing settings."""
        return dict(self.db.execute(
            "SELECT model_mode,local_model,allow_cloud_fallback,cloud_provider,cloud_model,local_connection_id,cloud_connection_id,updated_at FROM runtime_settings WHERE id=1"
        ).fetchone())

    def set_model_mode(self, mode: str) -> None:
        """Select local, hybrid, or cloud routing for future cycles."""
        current = self.get_settings()
        self.set_model_settings(mode, current["local_model"], bool(current["allow_cloud_fallback"]),
                                current["cloud_provider"], current["cloud_model"])

    def set_model_settings(self, mode: str, local_model: str, allow_cloud_fallback: bool = False,
                           cloud_provider: str = "openai", cloud_model: str = "gpt-5.4-mini",
                           local_connection_id: str = "local-default",
                           cloud_connection_id: str = "cloud-default") -> None:
        """Atomically select routing and the concrete local/cloud models."""
        if mode not in {"local", "hybrid", "cloud"}:
            raise ValueError("Invalid model mode")
        local_model = local_model.strip()
        if not local_model or len(local_model) > 200:
            raise ValueError("Local model name must contain 1 to 200 characters")
        # The persisted name is retained for compatibility. Its value now
        # identifies transport technology, not a hard-coded vendor catalog.
        if not re.fullmatch(r"[a-z][a-z0-9_-]{0,99}", cloud_provider):
            raise ValueError("Invalid cloud transport identifier")
        cloud_model = cloud_model.strip()
        if not cloud_model or len(cloud_model) > 200:
            raise ValueError("Cloud model name must contain 1 to 200 characters")
        self.db.execute(
            "UPDATE runtime_settings SET model_mode=?,local_model=?,allow_cloud_fallback=?,cloud_provider=?,cloud_model=?,local_connection_id=?,cloud_connection_id=?,updated_at=? WHERE id=1",
            (mode, local_model, int(allow_cloud_fallback), cloud_provider, cloud_model,
             local_connection_id, cloud_connection_id, utc_now()),
        )
        self.db.commit()
        self.audit("runtime.model_settings", {
            "mode": mode, "local_model": local_model,
            "allow_cloud_fallback": allow_cloud_fallback,
            "cloud_provider": cloud_provider, "cloud_model": cloud_model,
            "local_connection_id": local_connection_id, "cloud_connection_id": cloud_connection_id,
        })

    def audit(self, event_type: str, payload: dict) -> None:
        """Append an immutable event describing a meaningful state transition."""
        self.db.execute(
            "INSERT INTO audit_events VALUES(?,?,?,?)",
            (str(uuid4()), event_type, json.dumps(payload), utc_now()),
        )
        self.db.commit()
