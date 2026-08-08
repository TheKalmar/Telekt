# Known Limitations

This document is intentionally candid so a new developer does not mistake POC behavior for production guarantees.

## Workflow durability

Background execution uses a separate polling worker with durable SQLite state and per-company leases. It recovers `running` companies after a process restart, but does not provide Temporal workflow history, activity retries, timers, signals, or multi-node scheduling guarantees. Temporal is planned but not implemented.

## Persistence

SQLite is used per company. There is no migration versioning, connection pool, WAL configuration, distributed locking, encryption at rest, or production backup mechanism.

## Security

- The HTTP API has no authentication or tenant authorization.
- The server is safe only when bound to localhost in a trusted environment.
- There is no CSRF protection.
- Generated HTML is stored and can be opened locally; it must be treated as untrusted code.
- Execution is not sandboxed.
- The audit log is mutable by anyone with database access.

## Model routing

- Hybrid routing is static, not complexity-aware.
- There is no automatic fallback when Ollama or OpenAI fails.
- Local health checks API reachability, not model inference quality.
- Local DeepSeek may repeat work or make weaker strategic decisions.
- Structured-output failures have no repair/retry layer.
- Agents SDK tracing disablement is process-global and currently sticky.
- Token usage, latency, and monetary cost are not recorded per task.

## Approvals

Approvals freeze the proposal payload, but approved tasks are not yet resumed through a dedicated execution path. The CEO may generate a new proposal after approval. This must be corrected before real external actions are enabled.

## Budget

The ledger records model-estimated task cost, not actual API, advertising, infrastructure, or payment transactions. Currency conversion is not implemented. The field name remains EUR-oriented internally even when a company profile selects another currency.

## Agent execution

Specialists generate artifacts but do not yet have a general sandbox, browser, shell, GitHub, deployment, email, or payment tool runtime. Browser QA evidence in the original POC was manually bridged into state.

## Concurrency

Durable per-company leases suppress duplicate cycles across worker processes. The current fixed one-hour lease has no heartbeat extension; a worker that hangs longer than the lease could overlap with a replacement. Temporal should replace this mechanism before horizontal scaling.

## Portfolio registry

The registry SQLite connection uses `check_same_thread=False` without an explicit application lock. It is acceptable for the low-write local POC but not for concurrent production traffic.

## Frontend

The dashboard is a single static HTML file with inline CSS and JavaScript. It has no component framework, build pipeline, typed API client, automated accessibility audit, or frontend test suite.

## CLI

The CLI targets the original root company database and is not portfolio-aware. The web control plane is the primary interface.
