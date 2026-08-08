-- Canonical portfolio/control-plane state. Safe to apply repeatedly.
ALTER TABLE telekt.companies ADD COLUMN IF NOT EXISTS db_path text;

CREATE TABLE IF NOT EXISTS telekt.registry_settings (
  id integer PRIMARY KEY CHECK(id=1),
  active_company_id text
);
INSERT INTO telekt.registry_settings(id,active_company_id)
VALUES(1,NULL) ON CONFLICT(id) DO NOTHING;

CREATE TABLE IF NOT EXISTS telekt.work_leases (
  company_id text PRIMARY KEY,
  owner text NOT NULL,
  expires_at timestamptz NOT NULL
);

CREATE TABLE IF NOT EXISTS telekt.worker_heartbeats (
  owner text PRIMARY KEY,
  heartbeat_at timestamptz NOT NULL
);

INSERT INTO telekt.schema_version(version,description)
VALUES(3,'PostgreSQL portfolio registry') ON CONFLICT(version) DO NOTHING;
