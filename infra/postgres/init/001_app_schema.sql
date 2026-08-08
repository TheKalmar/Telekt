-- Telekt canonical application database. Temporal creates and owns its own
-- databases in the same local PostgreSQL instance; application code must never
-- read or write Temporal's internal tables.
CREATE SCHEMA IF NOT EXISTS telekt;
CREATE TABLE IF NOT EXISTS telekt.schema_version (
  version integer PRIMARY KEY,
  applied_at timestamptz NOT NULL DEFAULT now(),
  description text NOT NULL
);
INSERT INTO telekt.schema_version(version, description)
VALUES (1, 'PostgreSQL foundation') ON CONFLICT (version) DO NOTHING;

CREATE TABLE IF NOT EXISTS telekt.companies (
  id uuid PRIMARY KEY,
  name text NOT NULL,
  company_type text NOT NULL,
  concept text NOT NULL,
  goal text NOT NULL,
  initial_budget numeric(14,2) NOT NULL CHECK (initial_budget >= 0),
  profile jsonb NOT NULL DEFAULT '{}'::jsonb,
  runtime_state text NOT NULL DEFAULT 'stopped',
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS telekt.audit_events (
  id uuid PRIMARY KEY,
  company_id uuid NOT NULL REFERENCES telekt.companies(id) ON DELETE CASCADE,
  event_type text NOT NULL,
  payload jsonb NOT NULL DEFAULT '{}'::jsonb,
  created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS audit_events_company_time
  ON telekt.audit_events(company_id, created_at DESC);
