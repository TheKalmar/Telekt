# Architecture Refactor Working Notes

This file is the durable working memory for the repository-wide architecture
review. It records observed behavior, assumptions, decisions, completed work,
and remaining debt so later changes do not depend on chat history.

## System model

Telekt is a control plane for a portfolio of autonomous companies. Canonical
business state belongs to one `CompanyStore` per company. A CEO model proposes a
typed `TaskProposal`; the deterministic `Governor` authorizes it; a specialist
returns a typed `SpecialistResult`; and the orchestrator records the result.
Temporal is the production-oriented durable scheduler, while a polling worker
remains the lightweight SQLite fallback. PostgreSQL is canonical in the
infrastructure overlay; SQLite remains a development/import compatibility path.

Main runtime paths:

1. FastAPI persists stakeholder intent or control changes.
2. The web-to-Temporal gateway signals the stable per-company workflow.
3. The workflow invokes one idempotent `advance_company` activity.
4. `CompanyOrchestrator` observes state, asks the CEO for one task, applies
   policy, and either executes, requests approval/handoff, or stops.
5. `CompanyStore` commits task, approval, ledger, usage, and audit state.

Security boundary: model output is untrusted. Policy, budget checks, approval,
browser restrictions, secret handling, and workspace confinement stay in
deterministic application code.

## Current modules and ownership

| Capability | Current owner | Notes |
|---|---|---|
| Typed agent/application contracts | `models.py` | Good deterministic boundary |
| Model construction, routing, execution, retries | `agents.py` | Several responsibilities in one class |
| Autonomous control flow | `orchestrator.py` | Also constructs infrastructure dependencies |
| Authorization | `policy.py` | Small and cohesive |
| Company persistence | `store.py` | God repository: schema, writes, read models, telemetry, skills, integrations |
| Portfolio and worker leases | `registry.py` | Mixes SQLite/PostgreSQL setup, legacy import, paths, leases, store factory |
| HTTP control plane | `web.py` | God module: schemas, endpoints, preflight, HTTP clients, HTML rendering |
| Durable scheduling | `temporal_workflow.py` | Cohesive and deterministic |
| Temporal activities | `temporal_worker.py` | Duplicates fallback-worker state/brief behavior |
| Local fallback execution | `worker.py` | Duplicates Temporal activity behavior |
| Artifact confinement | `workspace.py` | Cohesive safety boundary |
| Browser isolation and policy | `browser_runtime.py`, `browser_policy.py` | Separate service; process-global sessions are intentional POC state |
| Email rendering/transmission | `email_service.py` | Cohesive transport, but application-level brief flow lives in workers |
| Model connection profiles/secrets | `model_connections.py`, `runtime_secrets.py` | Global configuration; file-backed and atomic |

## Dependency observations

- `web -> registry -> store -> models/postgres compatibility/skill catalog`.
- `temporal_worker` and `worker` both construct `registry -> store -> orchestrator`.
- `orchestrator` constructs model registry, agent engine, governor, workspace,
  mailer, and browser runner instead of receiving those boundaries explicitly.
- `agents` combines SDK adapter construction, routing policy, retry/telemetry,
  prompt assembly, and result evidence validation.
- UI operational state is derived from append-only audit records inside the
  write repository, coupling persistence to presentation logic.
- Environment variables are read throughout domain/application modules. They
  should be read at composition boundaries where practical.

## Highest-priority problems

1. **Duplicated execution policy**: Temporal and fallback workers independently
   map orchestration results to runtime state and build/send daily briefs. They
   can drift and already use slightly different detail text.
2. **`CompanyStore` has too many responsibilities**: schema migration, command
   writes, queries, skill registry, integrations, idempotency, and UI telemetry.
3. **`web.py` has too many responsibilities**: HTTP routes share a module with
   runtime readiness logic, outbound service clients, validation, and HTML.
4. **Implicit dependencies**: orchestration creates infrastructure internally,
   making focused tests and provider replacement harder.
5. **Connection lifetime is unclear**: `registry.store_for()` opens a database
   connection, but `CompanyStore` has no explicit close/context contract and
   high-frequency API/worker paths do not close stores.
6. **Model settings retain legacy vendor fields** (`cloud_provider`) even though
   model connections are now transport-oriented. Preserve the DB/API field for
   compatibility, but stop using it as a vendor allowlist.
7. **Import-time configuration/global state**: `web.py` computes paths and opens
   the registry during import, which complicates tests and reload/config order.
8. **Mojibake exists in user-visible literals and older documentation output**.

## Assumptions

- Existing API routes, Pydantic payloads, task/approval state names, audit event
  names, Temporal workflow name/queue, and database schema are public contracts.
- Refactoring must preserve SQLite and PostgreSQL behavior.
- No authentication/RBAC work is included; the stakeholder explicitly deferred
  it and it requires product/security policy decisions.
- A modular monolith is the correct target. Splitting more microservices would
  add deployment and consistency cost without current scale evidence.
- Historical audit/task failures are immutable diagnostic history and are not
  deleted during refactoring.

## Architectural decisions

- Use capability modules, not a generic service/repository framework.
- Extract pure read-model projection from `CompanyStore` before splitting write
  repositories; this gives high testability with low behavior risk.
- Centralize worker exit-state and stakeholder-brief behavior in one application
  service shared by Temporal and fallback execution.
- Make store lifetime explicit with `close()` and context-manager support, then
  close stores at composition boundaries.
- Extract SDK model-adapter construction from agent execution/routing; model
  transport technology remains data-driven.
- Preserve compatibility wrappers where tests or external callers may import an
  existing function during the evolutionary migration.

## Refactoring plan

- [x] Extract operational telemetry/read-model projection into a pure module.
- [x] Centralize worker result-state projection and daily stakeholder briefs.
- [x] Add explicit `CompanyStore` lifetime and close stores in owned scopes.
- [x] Extract model SDK adapter construction from `AgentEngine`.
- [x] Remove duplicate specialist execution paths in the orchestrator and inject
      replaceable boundaries where useful.
- [x] Extract runtime preflight evaluation from FastAPI route definitions.
- [x] Move company schema creation out of the company repository.
- [x] Add characterization/unit tests for every extracted boundary.
- [x] Move HTTP request contracts and the shared browser HTTP client out of
      `web.py` and remove the duplicated browser transport implementation.
- [x] Update architecture/developer documentation and validate the final suite.

## Completed

- Repository, documentation, runtime entry points, deployment overlays, test
  suite, recent git hotspots, and direct import graph reviewed on 2026-08-09.
- Baseline before this refactor: 92 deterministic tests passing.
- Shared application behavior now lives in `company_runtime.py`; Temporal and
  fallback workers no longer duplicate brief delivery or state projection.
- `CompanyStore` now owns an explicit close/context contract. Web requests,
  activities, registry enrichment, and worker loops close their owned stores.
- Schema bootstrap, operations projection, skill selection, model adapter
  construction, preflight evaluation, API request contracts, and browser HTTP
  transport have cohesive modules with focused tests.
- `CompanyOrchestrator` has one specialist execution path and injectable policy,
  workspace, and connection-registry boundaries.
- Legacy `cloud_provider` persistence remains compatible but now accepts generic
  transport identifiers instead of enforcing a vendor allowlist.
- Final validation: 100 tests passing, five eval fixtures valid, Python compile
  clean, dependency check clean, Docker app/worker images built, and the deployed
  PostgreSQL/Temporal stack healthy with the company still stopped.

## Deferred technical debt

- Introduce versioned migrations (for example Alembic) after schema ownership is
  extracted; replacing additive POC migration in the same change is too risky.
- Authentication, tenant authorization, encrypted external secret storage,
  connection pooling, structured logging, and full browser-runtime persistence
  need dedicated production work.
- The zero-build single-file frontend should eventually become feature modules,
  but backend safety and execution boundaries currently carry higher risk.
- The compatibility `cloud_provider` column should be renamed to
  `cloud_adapter` only through a versioned migration and coordinated API change.
