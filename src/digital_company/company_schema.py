"""Additive company-database schema bootstrap for SQLite and PostgreSQL."""

from __future__ import annotations

import json

from digital_company.text_encoding import repair_text_encoding

BASE_SCHEMA = """
CREATE TABLE IF NOT EXISTS company_schema_versions (
  version INTEGER PRIMARY KEY, description TEXT NOT NULL, applied_at TEXT NOT NULL
);
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
CREATE TABLE IF NOT EXISTS approval_votes (
  approval_id TEXT NOT NULL, voter TEXT NOT NULL, decision TEXT NOT NULL,
  comment TEXT, created_at TEXT NOT NULL,
  PRIMARY KEY (approval_id, voter)
);
CREATE TABLE IF NOT EXISTS policy_versions (
  version INTEGER PRIMARY KEY, document_json TEXT NOT NULL,
  status TEXT NOT NULL, created_at TEXT NOT NULL, created_by TEXT NOT NULL
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
  addressed_at TEXT, agent_id TEXT
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
CREATE TABLE IF NOT EXISTS integration_connections (
  id TEXT PRIMARY KEY, name TEXT NOT NULL, adapter TEXT NOT NULL,
  provider TEXT NOT NULL, location TEXT NOT NULL, base_url TEXT NOT NULL,
  status TEXT NOT NULL, capabilities_json TEXT NOT NULL,
  config_json TEXT NOT NULL, credential_fields_json TEXT NOT NULL,
  created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS integration_operations (
  execution_key TEXT PRIMARY KEY, connection_id TEXT NOT NULL,
  capability TEXT NOT NULL, method TEXT NOT NULL, path TEXT NOT NULL,
  request_json TEXT NOT NULL, provider_idempotency_key TEXT NOT NULL,
  status TEXT NOT NULL, response_json TEXT, created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS human_handoffs (
  id TEXT PRIMARY KEY, task_id TEXT NOT NULL, payload_json TEXT NOT NULL,
  status TEXT NOT NULL, outcome TEXT, created_at TEXT NOT NULL,
  resolved_at TEXT
);
CREATE TABLE IF NOT EXISTS agent_skills (
  id TEXT PRIMARY KEY, name TEXT NOT NULL, version TEXT NOT NULL,
  definition_json TEXT NOT NULL, status TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS activity_executions (
  execution_key TEXT PRIMARY KEY, status TEXT NOT NULL,
  task_id TEXT, result_json TEXT, attempt_count INTEGER NOT NULL,
  started_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS agent_instances (
  id TEXT PRIMARY KEY, name TEXT NOT NULL, role TEXT NOT NULL,
  agent_type TEXT NOT NULL DEFAULT 'custom',
  purpose TEXT NOT NULL, instructions TEXT NOT NULL,
  model_connection_id TEXT NOT NULL, status TEXT NOT NULL,
  autonomy_mode TEXT NOT NULL, token_limit INTEGER,
  spend_limit_eur REAL, schedule_json TEXT NOT NULL, config_json TEXT NOT NULL,
  created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS capability_plugins (
  id TEXT PRIMARY KEY, name TEXT NOT NULL, version TEXT NOT NULL,
  description TEXT NOT NULL, definition_json TEXT NOT NULL,
  status TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS agent_plugin_grants (
  agent_id TEXT NOT NULL, plugin_id TEXT NOT NULL, connection_id TEXT,
  permissions_json TEXT NOT NULL, config_json TEXT NOT NULL,
  status TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
  PRIMARY KEY(agent_id, plugin_id)
);
CREATE TABLE IF NOT EXISTS agent_runs (
  id TEXT PRIMARY KEY, agent_id TEXT NOT NULL, status TEXT NOT NULL,
  trigger_type TEXT NOT NULL, execution_key TEXT,
  started_at TEXT NOT NULL, completed_at TEXT, error TEXT
);
CREATE TABLE IF NOT EXISTS agent_playbook_versions (
  id TEXT PRIMARY KEY, agent_id TEXT NOT NULL, version INTEGER NOT NULL,
  document_json TEXT NOT NULL, status TEXT NOT NULL, source_message_id TEXT,
  created_by TEXT NOT NULL, created_at TEXT NOT NULL,
  UNIQUE(agent_id, version)
);
CREATE TABLE IF NOT EXISTS content_work_items (
  id TEXT PRIMARY KEY, agent_id TEXT NOT NULL, topic TEXT NOT NULL,
  status TEXT NOT NULL, research_task_id TEXT, draft_task_id TEXT,
  approval_id TEXT, publication_task_id TEXT, summary TEXT NOT NULL,
  created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
  last_contact_at TEXT, next_followup_at TEXT
);
CREATE INDEX IF NOT EXISTS tasks_status_created ON tasks(status, created_at);
CREATE INDEX IF NOT EXISTS approvals_status_created ON approvals(status, created_at);
CREATE INDEX IF NOT EXISTS approvals_task ON approvals(task_id);
CREATE INDEX IF NOT EXISTS approval_votes_approval ON approval_votes(approval_id, decision);
CREATE INDEX IF NOT EXISTS audit_events_time ON audit_events(created_at);
CREATE INDEX IF NOT EXISTS stakeholder_messages_status ON stakeholder_messages(status, created_at);
CREATE INDEX IF NOT EXISTS handoffs_status_created ON human_handoffs(status, created_at);
CREATE INDEX IF NOT EXISTS activities_status_updated ON activity_executions(status, updated_at);
CREATE INDEX IF NOT EXISTS agent_instances_status ON agent_instances(status, updated_at);
CREATE INDEX IF NOT EXISTS agent_plugin_grants_agent ON agent_plugin_grants(agent_id, status);
CREATE INDEX IF NOT EXISTS agent_runs_agent_started ON agent_runs(agent_id, started_at);
CREATE INDEX IF NOT EXISTS agent_playbook_agent_status ON agent_playbook_versions(agent_id, status);
CREATE INDEX IF NOT EXISTS content_work_agent_status ON content_work_items(agent_id, status, updated_at);
"""


SCHEMA_VERSIONS = (
    (1, "Canonical company state"),
    (2, "Model routing and stakeholder control"),
    (3, "Temporal activity recovery"),
    (4, "Skill and platform capability registry"),
    (5, "Provider-neutral integration connections"),
    (6, "Versioned policy and approval quorum"),
    (7, "Multi-agent instances and reusable capability plugins"),
    (8, "Typed agent templates and scoped runtime context"),
    (9, "Agent-scoped stakeholder messages and intervention queues"),
    (10, "Versioned agent playbooks and approval delivery tracking"),
    (11, "Repair legacy UTF-8 text decoded as Latin-1"),
    (12, "Durable content work items and scheduled agent wake-ups"),
)


def migrate_company_database(db, now: str) -> None:
    """Create the current additive POC schema and singleton defaults."""
    if getattr(db, "is_postgres", False):
        # App and worker can open the same company simultaneously during a
        # rolling deployment. Serialize DDL inside this schema transaction.
        db.execute("BEGIN")
        db.execute("SELECT pg_advisory_xact_lock(hashtext(current_schema()))")
    db.executescript(BASE_SCHEMA)
    _add_column(db, "tasks", "proposal_json", "TEXT")
    _add_column(db, "approvals", "decision_comment", "TEXT")
    _add_column(db, "approvals", "decided_by", "TEXT")
    _add_column(db, "approvals", "required_approvals", "INTEGER NOT NULL DEFAULT 1")
    _add_column(db, "approvals", "expires_at", "TEXT")
    _add_column(
        db,
        "runtime_settings",
        "allow_cloud_fallback",
        "INTEGER NOT NULL DEFAULT 0",
    )
    _add_column(
        db,
        "runtime_settings",
        "cloud_provider",
        "TEXT NOT NULL DEFAULT 'openai'",
    )
    _add_column(
        db,
        "runtime_settings",
        "cloud_model",
        "TEXT NOT NULL DEFAULT 'gpt-5.4-mini'",
    )
    _add_column(
        db,
        "runtime_settings",
        "local_connection_id",
        "TEXT NOT NULL DEFAULT 'local-default'",
    )
    _add_column(
        db,
        "runtime_settings",
        "cloud_connection_id",
        "TEXT NOT NULL DEFAULT 'cloud-default'",
    )
    _add_column(db, "tasks", "agent_id", "TEXT")
    _add_column(db, "ledger", "agent_id", "TEXT")
    _add_column(db, "model_usage", "agent_id", "TEXT")
    _add_column(db, "activity_executions", "agent_id", "TEXT")
    _add_column(db, "agent_instances", "agent_type", "TEXT NOT NULL DEFAULT 'custom'")
    _add_column(db, "stakeholder_messages", "agent_id", "TEXT")
    _add_column(db, "approvals", "notified_at", "TEXT")
    _add_column(db, "tasks", "work_item_id", "TEXT")
    _add_column(db, "agent_instances", "next_wake_at", "TEXT")
    applied_versions = {
        int(row["version"]) for row in db.execute("SELECT version FROM company_schema_versions")
    }
    if 11 not in applied_versions:
        _repair_legacy_text_encoding(db)
    db.execute(
        "INSERT OR IGNORE INTO runtime_control(id,state,detail,updated_at) "
        "VALUES(1,'stopped','Ready',?)",
        (now,),
    )
    db.execute(
        "INSERT OR IGNORE INTO runtime_settings(id,model_mode,local_model,updated_at) "
        "VALUES(1,'local','deepseek-company:8b',?)",
        (now,),
    )
    for version, description in SCHEMA_VERSIONS:
        db.execute(
            "INSERT INTO company_schema_versions(version,description,applied_at) VALUES(?,?,?) "
            "ON CONFLICT(version) DO NOTHING",
            (version, description, now),
        )
    db.commit()


def _add_column(db, table: str, column: str, definition: str) -> None:
    columns = {row["name"] for row in db.execute(f"PRAGMA table_info({table})")}
    if column not in columns:
        db.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")


def _repair_legacy_text_encoding(db) -> None:
    """Repair canonical/display text affected by an old Windows encoding path.

    Audit events remain untouched: they are immutable historical evidence.  The
    repair is versioned, transactional, idempotent, and limited to values with
    a high-confidence mojibake fingerprint.
    """
    tables = (
        ("company", "id", ("goal",), ()),
        ("company_profile", "id", (), ("profile_json",)),
        (
            "agent_instances",
            "id",
            ("name", "role", "purpose", "instructions"),
            ("schedule_json", "config_json"),
        ),
        ("agent_playbook_versions", "id", (), ("document_json",)),
        ("stakeholder_messages", "id", ("content", "response"), ()),
        (
            "tasks",
            "id",
            ("title", "objective", "rationale"),
            ("proposal_json", "result_json"),
        ),
        (
            "approvals",
            "id",
            ("reason", "decision_comment"),
            ("payload_json",),
        ),
        ("human_handoffs", "id", ("outcome",), ("payload_json",)),
    )
    for table, key, text_columns, json_columns in tables:
        columns = (key, *text_columns, *json_columns)
        # Table and column identifiers come exclusively from the closed tuple above.
        rows = db.execute(
            f"SELECT {','.join(columns)} FROM {table}"  # nosec
        ).fetchall()
        for row in rows:
            updates: dict[str, str] = {}
            for column in text_columns:
                current = row[column]
                repaired = repair_text_encoding(current)
                if repaired != current:
                    updates[column] = repaired
            for column in json_columns:
                current = row[column]
                if not current:
                    continue
                try:
                    decoded = json.loads(current)
                except (TypeError, json.JSONDecodeError):
                    continue
                repaired = repair_text_encoding(decoded)
                if repaired != decoded:
                    updates[column] = json.dumps(repaired, ensure_ascii=False)
            if updates:
                assignments = ",".join(f"{column}=?" for column in updates)
                db.execute(
                    # Identifiers come from the closed schema tuple; values are parameterized.
                    f"UPDATE {table} SET {assignments} WHERE {key}=?",  # nosec
                    (*updates.values(), row[key]),
                )
