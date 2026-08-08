# HTTP API

The API is a local POC interface. It has no authentication and must not be exposed to an untrusted network.

## Health and dashboard

### `GET /health`

Returns basic server readiness.

### `GET /api/dashboard`

Returns the active company snapshot, runtime control, settings, approvals, recent audit events, and portfolio switcher data.

### `GET /api/local-model/health`

Checks whether Ollama's model-list endpoint is reachable. It does not prove that the configured model can complete an inference.

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

### `POST /api/approvals/{approval_id}/approve`

Approves one exact pending payload.

### `POST /api/approvals/{approval_id}/reject`

Rejects one exact pending payload.

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
