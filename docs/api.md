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

`token_usage` is currently `null`. The application does not estimate or invent
token counts when the provider has not supplied them.

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
  "concept": "A niche store for remote-work equipment",
  "description": "Existing supplier relationships in the EU",
  "goal": "Validate demand and reach the first profitable sales",
  "budget": 1500,
  "currency": "EUR",
  "target_market": "Remote workers in the EU",
  "customer_type": "B2C",
  "time_horizon_days": 60,
  "risk_tolerance": "medium",
  "autonomy_level": "balanced",
  "constraints": ["Approval before spending money"],
  "success_criteria": ["Validated product category", "First ten orders"]
}
```

### `POST /api/companies/{company_id}/select`

Selects the company used by endpoints that operate on the active company.

## Runtime

### `POST /api/control/start`

Sets the active company to `running` and starts its background loop. Duplicate starts are suppressed by a per-company process lock.

### `POST /api/control/pause`

Requests a cooperative pause after the current atomic operation.

### `POST /api/control/stop`

Requests a cooperative stop after the current atomic operation.

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

Approving a pending item queues its exact frozen proposal for worker execution.
Rejecting it permanently closes that task and lets the CEO reconsider. Repeating
either decision on a resolved approval returns an error; an approval is single-use.

## Human takeover

### `POST /api/handoffs/{handoff_id}/complete`

Records the stakeholder's outcome/evidence for a pending manual checkpoint and
resumes the company. The body is `{ "outcome": "..." }`.

### `POST /api/handoffs/{handoff_id}/cancel`

Records why the manual checkpoint could not be completed and resumes CEO
reconsideration. Handoffs are single-use and distinct from permission approvals.

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

Approves one exact pending payload.

### `POST /api/approvals/{approval_id}/reject`

Rejects one exact pending payload.

## Platform capabilities

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

## Error behavior

- `400`: invalid action, decision, mode, or payload.
- `404`: company or approval not found.
- `409`: model mode changed while a company is running.
- `422`: Pydantic request validation failure.
- `500`: unhandled server failure; background runner failures instead set company state to `error`.
