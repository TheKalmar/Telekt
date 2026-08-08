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
- `CompanyLoopWorkflowV2` schedules durable cycles on the isolated
  `digital-company-v2` task queue. LLM calls, PostgreSQL access, email, browser
  work, and filesystem changes run only inside Activities.
- The original polling worker is replaced by `digital-company-temporal-worker`
  only when `compose.infrastructure.yaml` is included.
- Start, pause, stop, approvals, handoffs, and stakeholder directives wake the
  workflow through durable Temporal signals. Waiting companies do not poll the
  database. An hourly durable timer only checks whether a daily brief is due.
- PostgreSQL is canonical for company business records when the infrastructure
  overlay is enabled. SQLite company files remain rollback/import sources.

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

The infrastructure overlay sets `DATABASE_BACKEND=postgres` after import. Each
company uses an isolated native schema named from a stable company UUID. The
same `CompanyStore` business API is exercised against both backends, keeping
SQLite available as a rollback source while PostgreSQL becomes canonical for
company state. Portfolio discovery metadata (`registry.db`) remains SQLite in
this stage and is the final database component still awaiting PostgreSQL
cutover.

## Workflow version cutover

The signal-driven implementation uses workflow type `CompanyLoopWorkflowV2`,
workflow ID `company-loop-v2-<company-id>`, and task queue
`digital-company-v2`. These new identities are intentional: existing polling
workflow histories cannot be replayed by structurally different workflow code.
Legacy histories may remain visible in Temporal UI for audit, but no current
worker polls their task queue.

Each side-effecting company cycle has `maximum_attempts=1` at the Temporal
level because a whole cycle is not universally idempotent yet. Provider-level
transient retries remain inside the activity. An activity failure records an
error in canonical state and waits for an explicit signal instead of risking a
duplicate side effect.

## Verify

```powershell
docker compose -f compose.yaml -f compose.local.yaml -f compose.email.yaml -f compose.infrastructure.yaml ps
docker exec digital-company-postgres-1 psql -U telekt -d telekt -c "select * from telekt.schema_version"
docker exec digital-company-temporal-1 temporal workflow list -n default --address temporal:7233
```

The `auto-setup` Temporal image is for local development and evaluation. A
production self-hosted deployment must use managed schema upgrades, private
networking, backups, TLS, and the supported production server topology.
