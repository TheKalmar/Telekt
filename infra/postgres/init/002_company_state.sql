ALTER TABLE telekt.companies ADD COLUMN IF NOT EXISTS source_id text UNIQUE;
ALTER TABLE telekt.companies ADD COLUMN IF NOT EXISTS artifacts_path text;

CREATE TABLE IF NOT EXISTS telekt.company_records (
  company_id uuid NOT NULL REFERENCES telekt.companies(id) ON DELETE CASCADE,
  record_type text NOT NULL,
  record_id text NOT NULL,
  payload jsonb NOT NULL,
  source_created_at text,
  imported_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY(company_id, record_type, record_id)
);
CREATE INDEX IF NOT EXISTS company_records_type_time
  ON telekt.company_records(company_id, record_type, imported_at DESC);

CREATE TABLE IF NOT EXISTS telekt.migration_runs (
  id uuid PRIMARY KEY,
  source_path text NOT NULL,
  company_count integer NOT NULL,
  record_count integer NOT NULL,
  verification jsonb NOT NULL,
  completed_at timestamptz NOT NULL DEFAULT now()
);

INSERT INTO telekt.schema_version(version, description)
VALUES (2, 'Lossless SQLite company-state import') ON CONFLICT (version) DO NOTHING;
