-- Additive indexes and version marker. Existing installations receive the same
-- statements from CompanyRegistry._initialize_postgres during rolling startup.
CREATE INDEX IF NOT EXISTS companies_source_id ON telekt.companies(source_id);
CREATE INDEX IF NOT EXISTS companies_updated_at ON telekt.companies(updated_at DESC);
CREATE INDEX IF NOT EXISTS work_leases_expiry ON telekt.work_leases(expires_at);
CREATE INDEX IF NOT EXISTS worker_heartbeats_time ON telekt.worker_heartbeats(heartbeat_at DESC);

INSERT INTO telekt.schema_version(version,description)
VALUES(4,'Connection pooling and runtime hardening') ON CONFLICT(version) DO NOTHING;
