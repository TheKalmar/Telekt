# PostgreSQL and self-hosted Temporal

This is the free, local durable-infrastructure path. It does not use Temporal
Cloud and needs no Temporal account.

## Start

Keep the overlays that match the chosen local model and email setup:

```powershell
docker compose -f compose.yaml -f compose.local.yaml -f compose.email.yaml -f compose.infrastructure.yaml up -d --build
```

Open Telekt at `http://127.0.0.1:8421` and Temporal UI at
`http://127.0.0.1:8080`. PostgreSQL listens on `127.0.0.1:5432` and Temporal
gRPC on `127.0.0.1:7233` by default.

## Boundaries

- PostgreSQL schema `telekt` is reserved for canonical application state.
- Temporal creates and owns separate internal databases. Application code must
  never use Temporal tables as company memory.
- `CompanyLoopWorkflow` schedules durable cycles. LLM calls, SQLite access,
  email, browser work, and filesystem changes run only inside Activities.
- The original polling worker is replaced by `digital-company-temporal-worker`
  only when `compose.infrastructure.yaml` is included.
- During this first migration stage, company business records remain in SQLite.
PostgreSQL schema version 1 is intentionally a foundation, not a claim that
data migration has already completed.

## Import existing SQLite companies

The importer opens SQLite in read-only mode, upserts every record into
`telekt.company_records`, and aborts the PostgreSQL transaction if any per-table
row count differs:

```powershell
.\.venv\Scripts\python scripts\migrate_sqlite_to_postgres.py `
  --state-dir .company `
  --database-url "postgresql://telekt:telekt-local-only@127.0.0.1:5432/telekt"
```

It is safe to rerun: company records are keyed by company, record type, and
source record ID. Each successful attempt creates an immutable summary in
`telekt.migration_runs`. The importer does not delete or edit SQLite files.

Import completion is not the canonical-store cutover. Keep SQLite enabled until
the PostgreSQL adapter passes read/write parity and the operator explicitly
switches `DATABASE_BACKEND`.

## Verify

```powershell
docker compose -f compose.yaml -f compose.local.yaml -f compose.email.yaml -f compose.infrastructure.yaml ps
docker exec digital-company-postgres-1 psql -U telekt -d telekt -c "select * from telekt.schema_version"
docker exec digital-company-temporal-1 temporal workflow list -n default --address temporal:7233
```

The `auto-setup` Temporal image is for local development and evaluation. A
production self-hosted deployment must use managed schema upgrades, private
networking, backups, TLS, and the supported production server topology.
