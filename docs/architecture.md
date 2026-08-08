# Architecture

## Purpose

Digital AI Company explores a system in which a human supplies capital, goals, resources, and authority boundaries while an autonomous organization chooses the next useful job. The POC focuses on a reliable decision loop rather than a fixed workflow.

## Core design rule

The LLM does not own the workflow, authorization, budget, or canonical state.

```text
Observe canonical state
        |
        v
CEO proposes one typed task
        |
        v
Governor evaluates policy and budget
        |
        +---- deny ----------> audit and reconsider
        +---- approval ------> persist exact payload and pause
        +---- allow ---------> specialist executes
                                      |
                                      v
                              validate and persist result
                                      |
                                      v
                                 next cycle
```

This separation makes the probabilistic reasoning layer replaceable and keeps authority in deterministic code.

## Components

### Control plane

`web.py` exposes a FastAPI server and serves the single-page dashboard. It owns portfolio selection, runtime controls, stakeholder chat, approvals, and model-mode settings. It persists operator intent but never executes an agent cycle inside an HTTP process.

`worker.py` runs as a separate service. It polls canonical state, claims a durable per-company lease, and advances each running company by one bounded cycle. A container restart therefore resumes companies whose persisted state is still `running`. This is not yet a replacement for Temporal history, retries, timers, or signals, but it establishes the process boundary and recovery contract needed for that migration.

### Portfolio registry

`registry.py` stores portfolio metadata in `.company/registry.db`. It maps a company ID to its own SQLite database and artifact directory.

```text
.company/
  registry.db
  company.db                 legacy company database
  artifacts/                legacy company artifacts
  companies/
    <company-id>/
      company.db
      artifacts/
```

Business data never lives in the registry. This prevents normal queries from mixing state between companies.

### Company store

`store.py` is the canonical persistence boundary for one company. It stores:

- company goal and profile;
- tasks and specialist results;
- approvals with frozen proposal payloads;
- append-only budget ledger entries;
- runtime control state;
- model routing settings;
- stakeholder messages and CEO responses;
- audit events.

`snapshot()` creates the compact typed context sent to agents. Large artifact bodies are omitted so generated code is not resent on every model call.

### Agent engine

`agents.py` defines one CEO and five specialist roles using OpenAI Agents SDK structured outputs.

The CEO returns `TaskProposal`. Specialists return `SpecialistResult`. Pydantic validation is the boundary between model text and application logic.

Model routing is currently static:

- local: all roles use Ollama;
- hybrid: CEO and Development use OpenAI; other specialists use Ollama;
- cloud: all roles use OpenAI.

### Governor

`policy.py` is deliberately small and deterministic. It currently applies these rules:

| Action | Result |
|---|---|
| Internal research, product, development, QA | Allow |
| External outreach | Require approval |
| Spend money | Require approval |
| Production deployment | Require approval |
| Sign a contract | Deny |
| Estimated cost above remaining budget | Deny |

Production policy should become data-driven and company-specific, but it must remain outside the LLM.

### Orchestrator

`orchestrator.py` performs the bounded control loop. It checks cooperative stop state, blocks on existing approvals, obtains one CEO proposal, records stakeholder handling, evaluates policy, executes allowed specialist work, confines artifact paths, and records the result.

The artifact path check resolves the model-provided path and rejects any destination outside the current company's artifact root.

## Stakeholder intervention

Stakeholder messages have two modes:

- A directive changes the plan. Pending approvals become `superseded` because their assumptions may now be stale.
- A question remains in context without superseding approvals.

The CEO must include considered message IDs and a direct response in its typed proposal. The Governor still overrides stakeholder requests that exceed authority.

## State transitions

Runtime states are:

```text
stopped -> running -> waiting_approval
   ^          |
   |          +---- paused
   |          +---- error
   +--------------- stopped
```

Pause and stop are cooperative. They are evaluated between complete model calls and database operations.

## Migration path

The intended production evolution is:

| POC | Production target |
|---|---|
| SQLite company databases | PostgreSQL with tenant isolation |
| SQLite polling worker with durable leases | Temporal workflows and activities |
| Local artifact directories | GitHub plus object storage |
| Static Governor rules | Versioned policy engine |
| Direct local execution | Isolated local/cloud runtime |
| No authentication | Identity, RBAC, audit export |
