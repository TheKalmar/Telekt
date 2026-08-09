"""Additive company-database schema bootstrap for SQLite and PostgreSQL."""

from __future__ import annotations


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
CREATE INDEX IF NOT EXISTS tasks_status_created ON tasks(status, created_at);
CREATE INDEX IF NOT EXISTS approvals_status_created ON approvals(status, created_at);
CREATE INDEX IF NOT EXISTS approvals_task ON approvals(task_id);
CREATE INDEX IF NOT EXISTS approval_votes_approval ON approval_votes(approval_id, decision);
CREATE INDEX IF NOT EXISTS audit_events_time ON audit_events(created_at);
CREATE INDEX IF NOT EXISTS stakeholder_messages_status ON stakeholder_messages(status, created_at);
CREATE INDEX IF NOT EXISTS handoffs_status_created ON human_handoffs(status, created_at);
CREATE INDEX IF NOT EXISTS activities_status_updated ON activity_executions(status, updated_at);
"""


SCHEMA_VERSIONS = (
    (1, "Canonical company state"),
    (2, "Model routing and stakeholder control"),
    (3, "Temporal activity recovery"),
    (4, "Skill and platform capability registry"),
    (5, "Provider-neutral integration connections"),
    (6, "Versioned policy and approval quorum"),
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
        db, "runtime_settings", "allow_cloud_fallback",
        "INTEGER NOT NULL DEFAULT 0",
    )
    _add_column(
        db, "runtime_settings", "cloud_provider",
        "TEXT NOT NULL DEFAULT 'openai'",
    )
    _add_column(
        db, "runtime_settings", "cloud_model",
        "TEXT NOT NULL DEFAULT 'gpt-5.4-mini'",
    )
    _add_column(
        db, "runtime_settings", "local_connection_id",
        "TEXT NOT NULL DEFAULT 'local-default'",
    )
    _add_column(
        db, "runtime_settings", "cloud_connection_id",
        "TEXT NOT NULL DEFAULT 'cloud-default'",
    )
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
