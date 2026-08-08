# Platform strategy and Shopify

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

## Shopify setup boundary

The human creates or owns the Shopify organization/store, accepts terms, chooses
a paid plan when needed, and installs/authorizes the app. For a direct integration
with one owned store, create the app in Shopify's Dev Dashboard, release a version
with minimal scopes, install it on the store, then provide credentials through
environment variables.

```env
SHOPIFY_CLIENT_ID=...
SHOPIFY_CLIENT_SECRET=...
```

In the dashboard, open **Platforms**, enter only the permanent
`store-name.myshopify.com` domain and requested capabilities, then restart app
and worker after changing `.env.local`. Secret values are never accepted by the web
form, stored in SQLite, returned by the API, or written to audit events.

For new development use Shopify's GraphQL Admin API. The REST Admin API is
legacy, and product creation in new public apps belongs on GraphQL. Creating a
product requires `write_products`; creation produces an unpublished product, so
publication remains a separate approval-controlled action.

This commit tracks and exposes capability readiness but does not yet exchange
credentials for a token or call Shopify. The next connector activity should:

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
