# Known Limitations

This document is intentionally candid so a new developer does not mistake POC behavior for production guarantees.

## Workflow durability

The infrastructure overlay uses a signal-driven Temporal workflow per company,
including durable pause/start/stop, approval, handoff, stakeholder-message
signals, hourly brief timers, and startup recovery. V4 gives every company cycle
a stable execution key, frozen task checkpoint, cached result, and up to three
Temporal attempts. The lightweight stack still ships the legacy SQLite polling
worker as a development fallback. External connectors still require their own
provider-level idempotency contracts.

Structured specialist failures are stored separately from completed work and
are included in the CEO's next canonical snapshot. The recovery control resumes
from committed state rather than blindly replaying the failed action. This still
does not prove exactly-once behavior for future external provider adapters.

## Persistence

PostgreSQL is canonical for company business and portfolio state in the
infrastructure overlay and has schema versioning. `registry.db` remains only as
a lightweight-mode fallback and idempotent import source. Database connections
now have explicit request/activity lifetimes and a bounded process-local
connection pool. A validated backup script is included; scheduled off-machine
backups, restore drills, and encryption-at-rest configuration remain open.

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
HTML checks. The internal execution service also creates idempotent per-company
Git checkpoints on a separate, network-isolated volume. General subprocess
commands remain fail-closed because the service is not yet a per-job
container/VM sandbox. GitHub push, deployment, and payment tool runtimes are not
implemented. Generated HTML is downloadable rather than executed in the trusted
control-plane origin.

## Platform integrations

The CEO can evaluate build/buy/integrate/manual paths, request a platform, and
reason over durable capability readiness. Provider-neutral HTTP, OAuth and
webhook profiles now store capability grants, write-only credential references,
and stable idempotency keys for prepared operations. Adapter dispatch,
OAuth exchanges, API health verification, catalog reads, draft creation,
publication, incoming webhooks, and supplier marketplace connectors are not
implemented yet.

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

The dashboard remains a zero-build frontend. Shared primitives, settings, and
the browser cockpit are split into allowlisted JavaScript modules, while the
page shell, inline CSS, and the main render/navigation controller remain in
`index.html`. Contract tests cover module ordering, HTML escaping, and the
mobile-layout markers, but there is not yet a typed API client, automated
accessibility audit, or full end-to-end browser suite.

## CLI

The CLI targets the original root company database and is not portfolio-aware. The web control plane is the primary interface.
