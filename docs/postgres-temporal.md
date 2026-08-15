# PostgreSQL and self-hosted Temporal

This is the free, local durable-infrastructure path. It does not use Temporal
Cloud and needs no Temporal account.

## Start

Keep the overlays that match the chosen local model and email setup:

```powershell
.\scripts\up.ps1 -Mode Bundled -Mail -Build
```

The script includes `compose.infrastructure.yaml` by default. Add
`-Lightweight` only when intentionally testing the legacy SQLite/polling path.

Open Telekt at `http://127.0.0.1:8421` and Temporal UI at
`http://127.0.0.1:8080`. PostgreSQL listens on `127.0.0.1:5432` and Temporal
gRPC on `127.0.0.1:7233` by default.

## Boundaries

- PostgreSQL schema `telekt` is reserved for canonical application state.
- Temporal creates and owns separate internal databases. Application code must
  never use Temporal tables as company memory.
- `AgentLoopWorkflowV1` owns the independent lifecycle for every configured
  agent. The legacy `CompanyLoopWorkflowV4` remains registered so pre-migration
  single-loop companies can be recovered. LLM calls, PostgreSQL access, email,
  browser work, and filesystem changes run only inside Activities.
- The original polling worker is replaced by `digital-company-temporal-worker`
  only when `compose.infrastructure.yaml` is included.
- Agent start, pause, stop, approvals, handoffs, and direct stakeholder
  directives wake the target workflow through durable Temporal signals.
  Waiting agents do not poll the database.
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
company state. Schema version 4 also makes portfolio metadata, active selection,
worker liveness, and work leases canonical in PostgreSQL. Existing
`registry.db` metadata is imported idempotently on startup; stale leases are
intentionally not copied. The source file remains untouched for rollback and
for the lightweight SQLite-only stack.

## Workflow version cutover

The multi-agent implementation uses workflow type `AgentLoopWorkflowV1`, a
workflow ID derived from both company and agent, and task queue
`digital-company-v4`. Stable per-agent identities prevent one agent from
claiming another agent's task or activity execution key. The V4 company workflow
is a compatibility path for existing single-loop histories.

Each agent cycle receives a monotonic, agent-scoped execution key. PostgreSQL
checkpoints the key, agent, frozen proposal, owned task, attempt count, and final result. Temporal
may retry the activity up to three times: unfinished work resumes the same task,
while a committed result is returned from the cache if the worker died before
acknowledging it. This prevents duplicate task and ledger records. External API
adapters must additionally pass this key to providers that support idempotency;
SMTP and providers without such a contract cannot offer true exactly-once delivery.

V4 heartbeats every 20 seconds while a blocking company activity runs, bounds
queue/start/total attempt time, skips retries for deterministic validation and
permission failures, and continues as new before history becomes large. The
rollover also applies to long-idle companies whose hourly brief timers would
otherwise grow forever.

## Backup

Create and validate a custom-format application database archive:

```powershell
.\scripts\backup-postgres.ps1
```

The script writes a host copy under `.backups` and a second copy in the durable
`postgres_data` volume. Copy backups off-machine and perform regular restore
drills before calling a deployment production-ready. Temporal owns separate
databases; back up the entire PostgreSQL cluster with infrastructure tooling if
workflow history must be recoverable as well.

## Verify

```powershell
docker compose -f compose.yaml -f compose.local.yaml -f compose.email.yaml -f compose.infrastructure.yaml ps
docker exec digital-company-postgres-1 psql -U telekt -d telekt -c "select * from telekt.schema_version"
docker exec digital-company-temporal-1 temporal workflow list -n default --address temporal:7233
```

The `auto-setup` Temporal image is for local development and evaluation. A
production self-hosted deployment must use managed schema upgrades, private
networking, backups, TLS, and the supported production server topology.
