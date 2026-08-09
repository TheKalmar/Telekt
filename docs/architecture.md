# Architecture

## Purpose

Digital AI Company explores a system in which a human supplies capital, goals, resources, and authority boundaries while an autonomous organization chooses the next useful job. The POC focuses on a reliable decision loop rather than a fixed workflow.

## Core design rule

The LLM does not own the workflow, authorization, budget, or canonical state.
In the opt-in durable stack, Temporal owns workflow history and retry timing;
PostgreSQL is canonical company and portfolio state in the infrastructure stack.

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

With `compose.infrastructure.yaml`, `temporal_worker.py` runs as the execution
service. One `CompanyLoopWorkflowV3` exists per company. API handlers persist
operator intent and signal the workflow; approvals, handoffs, and stakeholder
directives also signal it. A waiting workflow consumes no model calls and does
not poll company state. `worker.py` remains only as the lightweight SQLite
development fallback when Temporal is not configured.

Every V3 company cycle has a stable execution key stored with its frozen task
and result. Temporal can retry a crashed activity without creating another task,
charging the ledger twice, or advancing past a result whose database commit
succeeded before the Temporal acknowledgement.

### Portfolio registry

`registry.py` stores portfolio metadata, active selection, worker heartbeats, and
leases in PostgreSQL when the infrastructure stack is enabled. The lightweight
development stack retains `.company/registry.db` as a SQLite fallback. Artifact
paths are resolved for the current runtime so host-specific paths are not reused
inside Docker or another machine.

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

`store.py` is the canonical persistence facade for one company. Schema bootstrap
lives in `company_schema.py`, while `operations_projection.py` turns queried
facts and audit events into the operations read model. The store persists:

- company goal and profile;
- tasks and specialist results;
- approvals with frozen proposal payloads;
- append-only budget ledger entries;
- runtime control state;
- model routing settings;
- stakeholder messages and CEO responses;
- audit events.
- integration capability records containing non-secret configuration, requested permissions, and secret-presence flags.
- resumable human handoffs with a URL, bounded instructions, required return evidence, and outcome.

`snapshot()` creates the compact typed context sent to agents. Large artifact bodies are omitted so generated code is not resent on every model call.

### Agent engine

`agents.py` defines one CEO and role-specific specialists using OpenAI Agents SDK
structured outputs. `model_adapters.py` owns construction of SDK model adapters
from transport-oriented connection profiles.

The CEO returns `TaskProposal`. Specialists return `SpecialistResult`. Pydantic validation is the boundary between model text and application logic.

Role routing is currently static, while concrete model IDs and transports are
data-driven:

- local: all roles use the selected local connection;
- hybrid: CEO, Development, and Research use the selected remote connection;
  other specialists use the selected local connection;
- cloud: all roles use the selected remote connection.

Connection profiles select Responses API, OpenAI-compatible Chat Completions, or
LiteLLM technology. Hosted web search is enabled only when the chosen adapter
supports hosted tools.

### Governor

`policy.py` is deliberately small and deterministic. Its rules are loaded from
the active immutable company policy version:

| Action | Result |
|---|---|
| Internal research, product, development, QA | Allow |
| Platform evaluation and product sourcing research | Allow |
| Request platform access or publish a catalog | Require approval |
| Discover tools or research contractor candidates | Allow |
| Authenticated browser operation, vendor negotiation, or hiring | Require approval |
| CAPTCHA, login, 2FA, identity verification, terms, or payment entry | Human takeover |
| External outreach | Require approval |
| Spend money | Allow under the configured autonomous limit; otherwise require approval |
| Production deployment | Require approval |
| Sign a contract | Deny |
| Estimated cost above remaining budget | Deny |

Approval quorum and expiry are also policy-controlled. Distinct votes are
durable rows rather than LLM memory. Contract signing and spending beyond the
remaining budget are non-configurable denials. See [Company policy](company-policy.md).

### Orchestrator

`orchestrator.py` performs the bounded control loop. It checks cooperative stop
state, blocks on existing approvals, obtains one CEO proposal, records
stakeholder handling, evaluates policy, executes allowed specialist work,
confines artifact paths, and records the result. Its governor, workspace, model
connection registry, and agent engine are replaceable constructor boundaries.

`company_runtime.py` owns behavior shared by Temporal and the lightweight
worker: orchestration-result state projection and consolidated daily stakeholder
briefs. This prevents the two execution modes from implementing different
business transitions.

`workspace.py` is the first narrow execution-runtime boundary. It rejects
absolute, traversal, backslash, unsupported, and oversized artifact paths;
writes accepted UTF-8 files atomically; computes SHA-256 and byte-size evidence;
and runs deterministic HTML preflight checks. Every successful write becomes an
`artifact.written` audit event. It deliberately provides no general shell or
host-filesystem access.

`execution_runtime.py` is a separate internal-only container with its own volume.
It receives no company database, browser profile, model, SMTP, or platform
credentials. Development artifacts are idempotently copied into a per-company
Git repository and committed on a task branch. General subprocess execution is
fail-closed until a true per-job container/VM backend can mount only one company
workspace and remove all network access. See `docs/execution-runtime.md`.

## Stakeholder intervention

Stakeholder messages have two modes:

- A directive changes the plan. Pending approvals become `superseded` because their assumptions may now be stale.
- A question remains in context without superseding approvals.

The CEO must include considered message IDs and a direct response in its typed proposal. The Governor still overrides stakeholder requests that exceed authority.

## Approval execution

Approval freezes the complete validated `TaskProposal`. Approve changes the task
to `approved` and durably wakes the worker. The worker atomically claims it as
`executing`, runs the frozen specialist assignment without another CEO decision,
then records `completed`/`executed` or `failed`/`execution_failed`. Reject marks
the task terminal and wakes the CEO to choose another path. A new stakeholder
directive supersedes approvals that are pending or approved but not yet claimed.

Approval requests can also be delivered through SMTP. Recipient-specific,
expiring HMAC links open a read-only review form; only an explicit POST records a
decision. Human comments are persisted as approval metadata and stakeholder
context. The dashboard remains available when email delivery fails.

## State transitions

Runtime states are:

```text
stopped -> running -> waiting_approval
   ^          |
   |          +---- waiting_human
   |          +---- paused
   |          +---- error
   +--------------- stopped
```

Pause and stop are cooperative. They are evaluated between complete model calls
and database operations, and between individual actions inside a Computer Use
browser mission. An in-flight provider request is allowed to finish safely.

`waiting_human` is a resumable checkpoint rather than a generic approval. The
CEO supplies the target URL, numbered human steps, and required return evidence.
Completing or cancelling the card creates canonical stakeholder evidence and
wakes the worker for CEO reconsideration.

## Migration path

The intended production evolution is:

| POC | Production target |
|---|---|
| SQLite `registry.db` portfolio metadata | Completed in the infrastructure stack; retained only as fallback/import source |
| Lightweight polling-worker fallback | Temporal-only production execution |
| Local artifacts plus isolated Git checkpoints | Governed GitHub connector plus object storage |
| Static Governor rules | Versioned policy engine |
| Direct local execution | Isolated local/cloud runtime |
| No authentication | Identity, RBAC, audit export |
