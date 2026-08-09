# Known Limitations

This document is intentionally candid so a new developer does not mistake POC behavior for production guarantees.

## Workflow durability

The infrastructure overlay uses a signal-driven Temporal workflow per company,
including durable pause/start/stop, approval, handoff, stakeholder-message
signals, hourly brief timers, and startup recovery. V3 gives every company cycle
a stable execution key, frozen task checkpoint, cached result, and up to three
Temporal attempts. The lightweight stack still ships the legacy SQLite polling
worker as a development fallback. External connectors still require their own
provider-level idempotency contracts.

## Persistence

PostgreSQL is canonical for company business and portfolio state in the
infrastructure overlay and has schema versioning. `registry.db` remains only as
a lightweight-mode fallback and idempotent import source. Database connections
now have explicit request/activity lifetimes. Production connection pooling,
backup/restore automation, and encryption-at-rest configuration remain open.

## Security

- The HTTP API has no authentication or tenant authorization.
- The server is safe only when bound to localhost in a trusted environment.
- There is no CSRF protection.
- Generated HTML is stored and can be opened locally; it must be treated as untrusted code.
- Execution is not sandboxed.
- The audit log is mutable by anyone with database access.

## Model routing

- Hybrid routing is static, not complexity-aware.
- Local-to-cloud fallback is explicit and disabled by default. A configurable
  minimum budget reserve blocks new cloud calls, but this is not a prepaid or
  transactional reservation at the provider.
- Local health checks API reachability, not model inference quality.
- Local DeepSeek may repeat work or make weaker strategic decisions.
- Structured-output repair retries the complete model run and must be revisited before side-effecting tools are placed inside agent runs.
- Agents SDK tracing disablement is process-global and currently sticky.
- Provider-reported usage is recorded per model run. Prices are estimates for
  known models, exclude unpriced tools, and are not a replacement for invoices.

## Approvals

Approvals freeze the proposal payload and the worker executes that exact payload
without asking the CEO for a replacement. Database claiming prevents the same
approval from being started twice. Real external connectors must still supply
provider-level idempotency keys: no local transaction can guarantee exactly-once
delivery across an email, payment, or deployment provider failure.

## Budget

The ledger records model-estimated task cost, not actual API, advertising, infrastructure, or payment transactions. Currency conversion is not implemented. The field name remains EUR-oriented internally even when a company profile selects another currency.

## Agent execution

Development output now passes through a confined per-company file workspace with
atomic writes, a small extension allowlist, size limits, hashes, and deterministic
HTML checks. This is not yet a process/container sandbox: specialists still lack
a general shell, browser, GitHub, deployment, or payment tool runtime. Generated
HTML is downloadable rather than executed in the trusted control-plane origin.

## Platform integrations

The CEO can evaluate build/buy/integrate/manual paths, request a platform, and
reason over durable capability readiness. Shopify configuration currently stores
only metadata and secret references. OAuth/client-credentials exchange, API
health verification, catalog reads, draft creation, publication, webhooks, and
supplier marketplace connectors are not implemented yet.

## Browser and outsourcing

The system now has an isolated persistent Playwright/Chromium cockpit with
screenshots and manual input, and models browser missions, human takeover, tool
discovery, talent sourcing, negotiation, and hiring as distinct governed actions.
Approved browser tasks now run a bounded OpenAI Computer Use screenshot/action
loop and expose mission status plus refreshed screenshots in the dashboard. A
paused mission is stopped safely but its model conversation is not yet resumable;
the CEO must propose a new mission after review. The system does not stream live
video, search Upwork/Fiverr automatically, send proposals, or hire contractors. CAPTCHA, login,
2FA, identity checks, terms acceptance, and payment entry are intentionally
human-only; bypass behavior is out of scope.

## Concurrency

Temporal owns company-cycle scheduling in the infrastructure overlay. The old
lease implementation remains only for the lightweight polling-worker fallback
and must not be horizontally scaled.

## Portfolio registry

The registry SQLite connection uses `check_same_thread=False` without an explicit application lock. It is acceptable for the low-write local POC but not for concurrent production traffic.

## Frontend

The dashboard is a single static HTML file with inline CSS and JavaScript. It has no component framework, build pipeline, typed API client, automated accessibility audit, or frontend test suite.

## CLI

The CLI targets the original root company database and is not portfolio-aware. The web control plane is the primary interface.
