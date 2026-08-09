# Platform strategy and provider-neutral connectors

## Strategy gate

The CEO must compare four paths before custom development:

1. **Build** when proprietary software is the differentiator.
2. **Buy** when an existing product solves the capability economically.
3. **Integrate** when APIs provide the required control without rebuilding the platform.
4. **Manual validation** when the business hypothesis can be tested before automation.

Platform evaluation is reversible internal research. Requesting account access
or publishing a catalog requires human approval. The CEO records the candidate
platform and minimum capabilities in its typed proposal; it cannot treat a
requested integration as ready.

## Connector architecture

Configuration focuses on transport technology rather than a hardcoded vendor
catalog. A company can create any number of connections using:

- HTTP Bearer token;
- HTTP API-key header;
- OAuth 2 client credentials;
- OAuth 2 user authorization;
- HTTPS webhook.

Each connection stores a user-defined provider label, local/cloud location,
base URL, explicitly granted capabilities, and non-secret adapter config.
Credential fields are write-only and stored in `runtime-secrets.json`; API and
dashboard responses contain only boolean presence. A hash in the secret name
prevents IDs that normalize similarly from sharing a credential slot.

Before a connector call can be dispatched, Telekt freezes an integration
operation with the company execution key, connection, capability, method, path,
and request body. It derives a stable `telekt-*` provider idempotency key. Reuse
of that execution key with changed input is rejected. The current endpoint only
prepares the operation and never sends it; governed adapter dispatch is the next
boundary.

Legacy Shopify capability metadata remains readable for existing companies, but
new configuration should use the provider-neutral connection manager.

## Shopify setup boundary and example

The human creates or owns the Shopify organization/store, accepts terms, chooses
a paid plan when needed, and installs/authorizes the app. For a direct integration
with one owned store, create the app in Shopify's Dev Dashboard, release a version
with minimal scopes, install it on the store, then provide credentials through
environment variables.

```env
SHOPIFY_CLIENT_ID=...
SHOPIFY_CLIENT_SECRET=...
```

In the dashboard, open **Integrations** and create the technology profile needed
by the current Shopify app/API. Enter the provider endpoint, minimum capabilities
and write-only credentials. Secret values are never stored in PostgreSQL,
returned by the API, or written to audit events.

For new development use Shopify's GraphQL Admin API. The REST Admin API is
legacy, and product creation in new public apps belongs on GraphQL. Creating a
product requires `write_products`; creation produces an unpublished product, so
publication remains a separate approval-controlled action.

The generic framework tracks capability readiness and prepares idempotent calls,
but does not yet exchange credentials for a token or call Shopify. A Shopify
adapter should:

- obtain short-lived access tokens from the configured credentials;
- support read-only catalog inspection first;
- create draft products idempotently;
- require separate human approval before publication;
- audit request IDs and returned Shopify object IDs without secrets.

## Supplier discovery

Alibaba or another supplier marketplace is not treated as a trusted product
feed by default. Research may collect candidates, but the system must preserve
source URLs, price timestamps, MOQ, shipping assumptions, supplier identity,
and confidence. Contacting a supplier, placing an order, accepting terms, or
spending money remains approval-controlled. Scraping or automating a marketplace
must also comply with its current API terms and access rules.
