# Telekt — Digital AI Company

Telekt is a control plane for a portfolio of digital companies. Organization
facts are independent from digital employees: after creating a company, the
owner adds any number of typed agent instances. Every agent has its own mandate,
model connection, token and model-spend limits, reusable capability-plugin
grants, lifecycle, durable Temporal workflow, run history, and direct
stakeholder chat.

An opt-in free self-hosted infrastructure overlay now runs PostgreSQL, Temporal,
Temporal UI, and the durable Temporal company worker. See
[`docs/postgres-temporal.md`](docs/postgres-temporal.md). Temporal owns durable,
signal-driven company scheduling; SQLite remains available as a lightweight
development and import fallback.

Read [Multi-agent company platform](docs/multi-agent-platform.md) before changing
company, agent, plugin, connection, or WordPress behavior. The older versioned
Skill Registry remains available for prompt/evidence procedures; capability
plugins are the enforceable runtime authority layer.

The control-plane UI supports English and Serbian through an extensible
translation catalog. See [`docs/LOCALIZATION.md`](docs/LOCALIZATION.md).

This repository proves the control loop and governance model. It is not yet a production-ready autonomous business platform.

## What is implemented

- Multi-company portfolio with isolated state and artifacts
- Company creation independent from agent creation
- Independently runnable typed agents with per-agent model, token and € limits
- Reusable typed capability plugins and per-agent least-privilege grants
- Provider-neutral model and integration connections with write-only credentials
- Concurrent durable per-agent Temporal workflows and recovery keys
- Per-agent stakeholder chat, tasks, approvals, handoffs, runs, and usage
- Content & SEO agent template, versioned owner playbooks, structured SEO QA,
  idempotent WordPress taxonomy/AIOSEO/media drafts, and full-draft approval email
- Local and cloud model connections assignable independently to each agent
- Deterministic allow / approval / deny policy
- Durable tasks, decisions, approvals, budget ledger, chat, and audit history
- Stakeholder directives that supersede stale approvals
- Cooperative start, pause, and stop controls
- Operations dashboard with active agent, model latency, failures, fallbacks, task states, and audit events
- Runtime readiness gate for worker, selected model connections and credentials;
  browser readiness appears only when an agent has the Browser Automation plugin
- Confined per-company artifact workspace with atomic writes, validation, hashes, inventory, and downloads
- Internal network-isolated execution service with idempotent per-company Git checkpoints
- Build/buy/integrate/manual strategy gate with durable platform capability tracking
- Provider-neutral HTTP/OAuth/webhook connections with write-only credentials and idempotent operation preparation
- Optional per-agent Browser Automation and human takeover permissions for login,
  CAPTCHA, 2FA, identity, terms, and other manual checkpoints
- Isolated persistent Chromium cockpit with domain allowlists, screenshots,
  manual clicks, and direct typing, hidden when no agent has the plugin
- Local web control plane
- Local Ollama model support through an OpenAI-compatible API
- Automatic adoption of the original single-company POC database

## Architecture at a glance

```text
Browser control plane
        |
        v
FastAPI API ---- Company ---- Agent instances ---- Plugin grants ---- Connections
        |              |             |                                      |
        |              +-------------+---- PostgreSQL canonical state -------+
        |                            |
        +---- Temporal signal -------+---- AgentLoopWorkflowV1 per agent
                                             |
                                             v
                                      CompanyOrchestrator activity
                                             |
                         AgentEngine + Governor + capability enforcement
```

See [Architecture](docs/architecture.md) for the full system description.

## Requirements

- Windows, Linux, or macOS
- Python 3.11+
- A credential for the selected remote connection when that transport requires one
- Ollama plus a configured model for agents assigned to a local connection

The local model configuration is in [config/Modelfile.deepseek-company](config/Modelfile.deepseek-company).

## Quick start

### Docker (recommended)

Docker Compose runs the control plane, browser runtime, internal Git execution
runtime, PostgreSQL, free self-hosted Temporal, and optional Ollama/email
services. No OpenAI key is required when an agent uses a local connection.
`scripts/up.ps1` includes the durable infrastructure by default; use
`-Lightweight` only for legacy SQLite/polling development.

Bundled Ollama with the default DeepSeek-R1 8B model on CPU:

```powershell
.\scripts\up.ps1 -Build
```

Bundled Ollama with the default model on NVIDIA GPU:

```powershell
.\scripts\up.ps1 -Gpu -Build
```

Use an Ollama installation and models already present on the host:

```powershell
.\scripts\up.ps1 -Mode Existing -Build
```

Run only the application for cloud mode, without starting or downloading Ollama:

```powershell
.\scripts\up.ps1 -Mode Cloud -Build
```

The bundled mode's first startup downloads DeepSeek-R1 8B Q4_K_M (about 5.2
GB). Set `INSTALL_DEFAULT_MODEL=false` in `.env.local` to start an empty bundled
Ollama instead. See [Model and Docker setup](docs/model-setup.md) for all modes,
custom models, Linux host networking, and troubleshooting.

Open [http://127.0.0.1:8421](http://127.0.0.1:8421). Stop the stack without
deleting company data or models with:

```powershell
.\scripts\down.ps1
```

The named volumes `digital-company_company_data` and
`digital-company_ollama_models` intentionally survive normal shutdown. Browser
profiles and cookies live in `digital-company_browser_data`. Running
`docker compose down --volumes` permanently deletes all three and should only be used
for a full reset.

For cloud or hybrid mode, copy `.env.example` to `.env.local` and set
`OPENAI_API_KEY`. `scripts/up.ps1` loads `.env.local` when present; the secret is
neither copied into the image nor committed to Git.

Use **Configuration → Models** to create provider-neutral model connections.
Then choose the connection on each agent under **Configuration → Agents**. An
agent can be configured while a credential/model is unavailable; Start performs
the readiness check.

### Native Python

Create a virtual environment and install the project:

```powershell
py -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
```

Create `.env.local` in the repository root:

```env
OPENAI_API_KEY=your-key-if-cloud-or-hybrid-is-used
OPENAI_MODEL=gpt-5.4-mini
PORT=8421
```

Never commit `.env.local`. It is ignored by Git.

Build the local Ollama model:

```powershell
ollama pull deepseek-r1:8b-0528-qwen3-q4_K_M
ollama create deepseek-company:8b -f config/Modelfile.deepseek-company
```

Start the control plane:

```powershell
.\.venv\Scripts\digital-company-web.exe
```

Open [http://127.0.0.1:8421](http://127.0.0.1:8421). The readiness endpoint is `GET /health`.

## Per-agent model routing

Model vendors and model IDs are data, not hardcoded UI choices. Create local or
remote connection profiles through supported adapter technologies, then assign
one to each agent. For example, a CEO may use a stronger remote model while a
Content agent uses a cheaper remote or local model. Token and estimated API-cost
ceilings are enforced per agent.

## Runtime controls

- **Start / Pause / Stop** on an agent card signals only that agent's workflow.
- Multiple agents may run concurrently inside one company.
- **Agent chat directive** becomes priority context for that agent and
  supersedes only its stale pending approvals/handoffs.
- **Stakeholder question** requests a response without invalidating the current plan.

Pause and stop are cooperative. An in-flight model request or database write is allowed to finish.

## Tests

```powershell
.\.venv\Scripts\python.exe -m pytest -q
```

The current suite covers policy outcomes, durable state, model settings, operational telemetry, stakeholder messages, and multi-company isolation.

CEO behavior has a separate real-agent eval harness. Fixture validation is free:

```powershell
.\.venv\Scripts\python evals\run_local.py --validate-only
```

An intentional live run uses one model call per eval case and writes the detailed result to
`evals/results/latest.json`; see `evals/README.md`.

## Repository map

```text
src/digital_company/
  agent_templates.py Typed agent setup schemas and allowed-action boundaries
  capability_plugins.py Reusable plugin catalog, config, and permission mapping
  agents.py          Per-agent model execution, limits, retries, and evidence gates
  model_adapters.py  Connection-profile to Agents SDK adapter factory
  api_models.py      Validated HTTP request contracts
  company_runtime.py Shared worker state and stakeholder-brief services
  company_schema.py  Additive company schema bootstrap
  operations_projection.py Pure operational dashboard read model
  preflight.py       Runtime readiness rules and connection checks
  models.py          Typed agent/application contracts
  orchestrator.py    Autonomous control loop
  wordpress_plugin.py Idempotent governed WordPress REST execution
  workspace.py       Confined artifact writes and deterministic validation
  browser_runtime.py Isolated Playwright/Chromium session service
  browser_client.py  Shared internal browser-runtime HTTP boundary
  browser_policy.py  URL allowlist and human-checkpoint rules
  computer_use.py    Bounded OpenAI Computer Use mission controller
  policy.py          Deterministic Governor
  store.py           PostgreSQL/SQLite-compatible persistence facade
  registry.py        Multi-company portfolio registry
  web.py             FastAPI control plane
  worker.py          Restart-safe autonomous cycle worker
  cli.py             Legacy single-company CLI
  static/index.html  Dashboard shell, layout, and core render/navigation code
  static/ui-core.js  Shared escaping, formatting, links, and API client
  static/settings-ui.js Models, email, integrations, and policy configuration UI
  static/browser-ui.js Human-intervention browser cockpit UI
config/
  Modelfile.deepseek-company
docs/
  architecture.md
  developer-guide.md
  prompt.md
  api.md
  operations.md
  known-limitations.md
tests/
  test_frontend_contract.py Zero-build module, escaping, and mobile contract checks
```

## Developer handoff

Read these in order:

1. [Luka / new developer quick handoff](docs/LUKA_HANDOFF.md)
2. [Architecture](docs/architecture.md)
3. [Developer guide](docs/developer-guide.md)
4. [Agent prompt contracts](docs/prompt.md)
5. [API reference](docs/api.md)
6. [Operations](docs/operations.md)
7. [Known limitations](docs/known-limitations.md)
8. [Email approvals](docs/email-approvals.md)
9. [Agent operating cadence and content pipeline](docs/agent-operating-cadence.md)
9. [Model reliability](docs/model-reliability.md)
10. [Platform strategy and Shopify](docs/platform-integrations.md)
11. [Browser missions and human takeover](docs/browser-handoffs.md)
12. [Isolated execution runtime](docs/execution-runtime.md)
13. [Company policy and approval quorum](docs/company-policy.md)

## Security warning

The Governor is the authorization boundary. Do not move external side effects into agent prompts or specialist code. A model may propose an action, but deterministic application code must authorize and execute it. Production deployments also need authentication, tenant authorization, encrypted secret storage, backup/restore operations, and a per-job execution sandbox stronger than the current isolated runtime.
