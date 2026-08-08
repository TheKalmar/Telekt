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

## Verify

```powershell
docker compose -f compose.yaml -f compose.local.yaml -f compose.email.yaml -f compose.infrastructure.yaml ps
docker exec digital-company-postgres-1 psql -U telekt -d telekt -c "select * from telekt.schema_version"
docker exec digital-company-temporal-1 temporal workflow list -n default --address temporal:7233
```

The `auto-setup` Temporal image is for local development and evaluation. A
production self-hosted deployment must use managed schema upgrades, private
networking, backups, TLS, and the supported production server topology.
