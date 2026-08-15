# Multi-agent company platform

Telekt deliberately separates four things that were coupled in the original
POC:

1. **Company** — durable organization facts shared by all employees: identity,
   industry, website, jurisdiction, business description, goal, and optional
   company capital.
2. **Agent instance** — one independently runnable digital employee with a
   typed role, mandate, model connection, token limit, model-spend limit,
   lifecycle, chat, and run history.
3. **Capability plugin** — a reusable definition of tools, typed setup fields,
   and permissions. A plugin definition contains no tenant credentials.
4. **Integration connection** — a reusable transport endpoint and write-only
   credential set, such as a WordPress REST API or SMTP mailbox.

An agent plugin grant joins an agent, plugin, optional connection, selected
permissions, and non-secret per-agent configuration. The backend validates this
grant and the orchestrator enforces it; prompt text is not an authorization
boundary.

```text
Company
  ├── Agent: CEO ───────────── model A, token/€ limits, lifecycle, chat
  │     ├── Web Research grant
  │     ├── Email grant ────── CEO mailbox connection
  │     └── Banking grant ──── future banking connection
  └── Agent: Content & SEO ─── model B, token/€ limits, lifecycle, chat
        ├── Web Research grant
        ├── Content Workspace grant
        ├── WordPress grant ── WordPress REST connection
        └── Email grant ────── content mailbox connection
```

## Setup flow

1. Create the company under **New company**. This creates no agent
   automatically.
2. Open **Configuration → Agents → Add agent**.
3. Select the agent type. Type-specific fields appear from the server-provided
   schema; adding a new type does not require hardcoding the form.
4. Select any enabled model connection. The agent can be saved while that model
   is offline or missing a credential; **Start** is the readiness boundary.
5. Set the maximum tokens and maximum estimated model spend for that agent.
   These are independent from optional company capital.
6. Open **Plugins** on the agent card and grant only the required permissions.
7. Attach a ready integration connection where API execution is desired.
8. Start only the agents that should run. Each has an independent Temporal
   workflow, status, retry/recovery history, and stakeholder chat.

The owner may run zero, one, or many agents. Stopping one does not stop another.
A direct chat directive supersedes stale work only for its target agent.

## Runtime and accounting boundaries

`AgentLoopWorkflowV1` uses a stable workflow ID derived from company and agent.
Start, pause, resume, stop, approvals, human handoffs, and direct stakeholder
messages are Temporal signals. Activity execution keys and task ownership are
agent-scoped so a retry cannot execute another agent's task.

Before each model call the runtime checks the agent's remaining token allowance.
Before a cloud agent starts it checks the remaining estimated model-spend
allowance. Usage records, tasks, activity executions, run history, approvals,
handoffs, and messages carry `agent_id`.

PostgreSQL is canonical in the durable stack. `registry.db` and SQLite company
databases remain import/rollback sources and are not silently deleted.

## WordPress Content plugin

The built-in `wordpress-content` plugin declares:

- setup: `site_url`, `posts_url`, optional review email, draft-only default;
- permissions: `read_posts`, `write_drafts`, `manage_terms`, `upload_media`,
  `write_seo_metadata`, `publish_posts`;
- transports: HTTP Basic, Bearer token, or API-key header;
- connection capabilities: `wordpress.posts.read`,
  `wordpress.posts.write_drafts`, `wordpress.terms.manage`,
  `wordpress.media.upload`, `wordpress.seo.write`, and
  `wordpress.posts.publish`.

The content agent first returns a typed `ContentPackage`: clean title and slug,
keywords, SEO title and description, excerpt, categories, tags, complete HTML,
internal links, CTA, authoritative sources, and a featured-image specification.
Deterministic Telekt SEO QA must reach the agent's configured target before a
WordPress side effect is allowed. This score is intentionally distinct from any
score computed by AIOSEO.

The API runtime resolves or creates categories and tags, writes AIOSEO title and
description metadata, optionally generates and uploads a featured image through
the separately granted `featured-image-generation` plugin, then writes an
unpublished post using a deterministic slug. Temporal
retries reuse a durable integration operation, while later retries also look up
the slug before creating anything. Publication changes that same draft to
`publish`; it is still blocked by the deterministic company approval policy even
when the agent has `publish_posts`.

### Recommended WordPress setup

1. In WordPress create a dedicated least-privilege user for Telekt.
2. Generate a WordPress Application Password for that user.
3. In Telekt open **Configuration → Connections** and create:

   - adapter: `HTTP API · Basic authentication`;
   - base URL: `https://YOUR-SITE/wp-json/wp/v2`;
   - capabilities: `wordpress.posts.read, wordpress.posts.write_drafts, wordpress.terms.manage, wordpress.media.upload, wordpress.seo.write, wordpress.posts.publish`;
   - username: dedicated WordPress username;
   - password: the Application Password.

4. Open the Content agent's **Plugins → WordPress Content Publishing**, select
   that connection, choose permissions, and save.

Credentials are write-only and stored in the local runtime secret vault. They
are never returned by the API or placed in agent prompts. If no REST connection
is attached, the plugin can use the isolated browser path and stop for login,
CAPTCHA, 2FA, terms, identity, or other human-only checkpoints.

## G&K migration included in the development state

The current G&K company profile contains organization facts only. A stopped
`Content & SEO` agent owns the legal-content mandate, uses the configured cloud
connection, has a 250,000-token limit and a EUR 10 model-spend limit, and has Web
Research, Content Workspace, WordPress Content, AI Featured Images, and Email
grants. Its WordPress REST connection and write-only Application Password are
configured. Historical stale attention items were superseded, not deleted.

The agent also has a versioned owner playbook. A chat message sent as `memory`
creates a new immutable playbook version; ordinary directives and questions do
not silently become permanent rules. A publication approval email contains the
complete sanitized article, SEO facts, taxonomy, sources, quality result,
featured image, and WordPress preview link. Publishing remains approval-gated.

## Current safety boundary

- Plugin grants and connection capabilities are checked in deterministic code.
- Publishing, external communication, production deployment, and spending still
  follow the versioned company policy.
- Signing a contract remains denied.
- CAPTCHA, account recovery, 2FA, identity, payment, and acceptance of legal
  terms remain human checkpoints.
- Authentication/RBAC for the Telekt control plane is intentionally still
  pending and must be completed before public deployment.
