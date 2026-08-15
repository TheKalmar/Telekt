# HTTP API

The API is a local POC interface. It has no authentication and must not be exposed to an untrusted network.

## Health and dashboard

### `GET /health`

Returns basic server readiness.

### `GET /api/dashboard`

Returns the active company snapshot, runtime control, settings, approvals, recent audit events, and portfolio switcher data.

### `GET /api/operations`

Returns operational telemetry for the active company: the currently inferred
model run, worker heartbeat, task counts by status, model successes and errors,
latency, provider usage, fallback count, ledger-estimated spend, and the latest
40 decoded audit events. It also projects the latest Browser Mission objective,
status, step limit, last action, allowed domains, and stop detail for the live
dashboard. Metrics are projected from the latest 250 audit events.

`token_usage` aggregates provider-reported input, cached, output, reasoning, and
total tokens. Known model prices produce an explicitly labelled estimate;
unknown models remain visible as unpriced calls rather than receiving an
invented price.

### `GET /api/runtime/preflight`

Runs fresh readiness checks for the selected company. It returns pass, warning,
skip, or blocking results for the worker, selected model connections, credentials
presence, and isolated browser runtime. Secret values are never returned.

`POST /api/control/start` runs the same checks without cache and returns `409`
with actionable blockers instead of queuing a company that cannot execute its
configured model route. Browser availability and a cloud key in local-only mode
are warnings because reasoning work can still proceed.

### `GET /api/local-model/health`

Checks whether Ollama's model-list endpoint is reachable. It does not prove that the configured model can complete an inference.

## Artifacts

### `GET /api/artifacts`

Lists artifact path, byte size, and SHA-256 digest for the active company's
confined workspace. File contents are not included.

### `GET /api/artifacts/{artifact_path}`

Downloads one allowed artifact as an attachment. Traversal, absolute paths, and
unsupported executable file types are rejected. Generated HTML is not rendered
inside the control-plane origin because it is untrusted agent output.

## Portfolio

### `GET /api/companies`

Returns all registered companies and the active company ID.

### `POST /api/companies`

Creates an isolated company and makes it active.

Example body:

```json
{
  "name": "Northstar Commerce",
  "company_type": "eCommerce",
  "industry": "Retail",
  "website_url": "https://northstar.example",
  "jurisdiction": "European Union",
  "concept": "A niche store for remote-work equipment",
  "description": "Existing supplier relationships in the EU",
  "goal": "Validate demand and reach the first profitable sales",
  "budget": 1500,
  "currency": "EUR"
}
```

`budget` is optional company capital, not an AI inference allowance. A new
company contains no implicit CEO or other agent.

### `POST /api/companies/{company_id}/select`

Selects the company used by endpoints that operate on the active company.

## Independent agents

### `GET /api/agent-types`

Returns built-in typed agent templates and their setup fields, defaults,
suggested plugins, and allowed action boundaries.

### `GET /api/agents`

Lists the active company's independent agents with lifecycle, model connection,
limits, metered usage, run history, attention counts, and plugin grants.

### `POST /api/agents`

Creates a stopped agent. The selected model connection may be configured before
its credential/model is ready; Start is the authoritative readiness boundary.

```json
{
  "name": "Content & SEO",
  "agent_type": "content_seo",
  "role": "content and SEO strategist",
  "purpose": "Grow qualified organic visibility",
  "instructions": "Verify legal claims against official sources.",
  "model_connection_id": "cloud-default",
  "autonomy_mode": "governed",
  "token_limit": 150000,
  "spend_limit_eur": 10,
  "schedule": {},
  "config": {
    "content_language": "sr-Latn",
    "target_audience": "Legal-service clients in BiH",
    "content_scope": "Employment, commercial and civil law",
    "brand_voice": "professional and clear",
    "require_official_sources": true
  }
}
```

`PUT /api/agents/{agent_id}` updates configuration without resetting usage or
run history. `POST /api/agents/{agent_id}/running`, `/paused`, and `/stopped`
signal only that agent's `AgentLoopWorkflowV1`.

### Agent chat

- `GET /api/agents/{agent_id}/messages` lists direct and company-wide context.
- `POST /api/agents/{agent_id}/messages` sends a `directive` or `question` to
  one agent. A directive supersedes only that agent's stale attention items.

## Capability plugins

### `GET /api/capability-plugins`

Returns trusted reusable plugin definitions with typed configuration fields,
tools, connection kinds, declared permissions, and instructions. Definitions
never contain tenant credentials.

### `PUT /api/agents/{agent_id}/plugins/{plugin_id}`

Creates or updates one least-privilege grant:

```json
{
  "connection_id": "gk-wordpress",
  "permissions": ["read_posts", "write_drafts"],
  "config": {
    "site_url": "https://gkadvokati.com",
    "posts_url": "https://gkadvokati.com/wp-admin/post-new.php",
    "review_email": "owner@example.com",
    "default_post_status": "draft"
  }
}
```

The backend rejects undeclared permissions, incompatible adapters, missing
connection capabilities, non-ready connections, and invalid config. `DELETE`
on the same route disables the grant without deleting its audit history.

## Runtime

### `POST /api/control/start`

Starts the legacy company loop retained for migrated POC companies. New work
uses the per-agent lifecycle endpoints above. Duplicate starts are suppressed
by durable workflow identity.

### `POST /api/control/pause`

Requests a cooperative pause after the current atomic operation.

### `POST /api/control/stop`

Requests a cooperative stop after the current atomic operation.

### `POST /api/recovery/retry`

Available only while the selected company is in `error`. It reruns preflight,
closes orphaned model telemetry, and signals Temporal to start a new execution
from the last committed PostgreSQL checkpoint. It does not directly replay an
ambiguous external action. Returns `409` when there is no recoverable error or
when runtime readiness still has blockers.

## Stakeholder messages

### `POST /api/messages`

```json
{
  "kind": "directive",
  "content": "Prioritize agencies in Germany before building more features."
}
```

`kind` is `directive` or `question`. Directives supersede pending approvals and can wake a paused company.

## Approvals

Approving a pending item records one distinct vote. Reaching the policy quorum
queues its exact frozen proposal for worker execution. Rejecting it permanently
closes that task and lets the CEO reconsider. Duplicate votes and decisions on
resolved or expired approvals return an error.

## Company policy

### `GET /api/policy`

Returns the active immutable version, validated document, and supported action IDs.

### `POST /api/policy`

Creates a new version containing action rules, autonomous spend limit, approval
quorum, and approval TTL. The contract-signing prohibition cannot be removed.

## Human takeover

### `POST /api/handoffs/{handoff_id}/complete`

Records the stakeholder's outcome/evidence for a pending manual checkpoint and
resumes the company. The body is `{ "outcome": "..." }`.

### `POST /api/handoffs/{handoff_id}/cancel`

Records why the manual checkpoint could not be completed and resumes CEO
reconsideration. Handoffs are single-use and distinct from permission approvals.

### `POST /api/handoffs/{handoff_id}/browser`

Opens the exact HTTPS URL and explicit hostname allowlist stored in a still-pending
handoff inside the active company's isolated browser. Resolved, superseded, vague,
or non-HTTPS handoffs are rejected. The operator cannot use this endpoint to
replace the CEO's frozen handoff target.

## Browser cockpit

- `GET /api/browser/health` checks the internal Chromium runtime.
- `PUT /api/browser/session` opens/replaces the active company's persistent session.
- `GET /api/browser/session` returns URL, title, viewport, and checkpoint status.
- `GET /api/browser/screenshot` returns the current PNG viewport.
- `POST /api/browser/actions` accepts a bounded manual click, type, key, or navigation action.
- `DELETE /api/browser/session` closes Chromium while retaining its profile volume.

Session creation requires an HTTPS URL and explicit public-domain allowlist.
Loopback, private-network, link-local, embedded-credential, and off-list targets
are rejected. The browser container is not published to the host; these control
plane routes are the only supported access path.

### `POST /api/approvals/{approval_id}/approve`

Records one vote for the exact pending payload and returns `approval_count`,
`required_approvals`, and either `pending` or `approved` status.

### `POST /api/approvals/{approval_id}/reject`

Rejects one exact pending payload.

## Platform capabilities

### `GET /api/integration-connections`

Lists provider-neutral adapter technologies and the active company's connection
profiles. Credential values are never returned; every expected field is exposed
only as present/missing.

### `POST /api/integration-connections`

Creates or updates an arbitrary HTTP, SMTP, OAuth, or webhook connection with a
provider label, endpoint, explicit capability set, non-secret adapter config and
write-only `credentials` object. Omitting credentials while editing preserves
their existing vault values.

### `POST /api/integration-operations/prepare`

Freezes a future connector call and derives a stable provider idempotency key.
The connection must be ready and grant the requested capability. Reusing an
execution key with changed connection, capability, method, path, or body returns
`409`. This endpoint does not transmit the operation.

The older `/api/settings/integrations` routes remain for legacy platform
capability metadata.

### `GET /api/settings/integrations`

Returns configured and requested integrations, capabilities, non-secret config,
and a boolean presence flag for each required environment secret. Secret values
are never returned.

### `POST /api/settings/integrations`

Configures non-secret platform metadata. Shopify accepts a permanent
`*.myshopify.com` store domain and capability list; it always references
`SHOPIFY_CLIENT_ID` and `SHOPIFY_CLIENT_SECRET` from the process environment.

## Model routing

### `POST /api/settings/model-mode`

```json
{ "mode": "hybrid" }
```

Allowed values: `local`, `hybrid`, and `cloud`. Mode changes are rejected while the active company is running.

### `GET /api/model-connections`

Lists transport adapters and configured model connections. Credentials are never
returned; each connection exposes only `credential_configured` and `ready`.

### `POST /api/model-connections`

Creates or updates a connection using an adapter, location, arbitrary model ID,
optional base URL, and write-only `api_key`. The key is stored separately from
the connection metadata and PostgreSQL.

### `DELETE /api/model-connections/{connection_id}`

Deletes an unused connection. Connections assigned to the active company cannot
be deleted until routing is changed.

## Error behavior

- `400`: invalid action, decision, mode, or payload.
- `404`: company or approval not found.
- `409`: model mode changed while a company is running.
- `422`: Pydantic request validation failure.
- `500`: unhandled server failure; background runner failures instead set company state to `error`.
