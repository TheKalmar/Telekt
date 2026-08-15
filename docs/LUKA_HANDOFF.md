# Developer handoff for Luka

This is the shortest reliable path from a clean machine to a working Telekt
development environment. Read this file first, then use the linked documents for
the subsystem being changed.

## 1. What this branch contains

Use branch `codex/multi-agent-platform`. It contains the complete current
POC history, including:

- company profiles separated from digital employee configuration;
- independently runnable typed agent instances with per-agent model, token/€
  limits, plugins, direct chat, usage, tasks, attention queue, and run history;
- reusable capability plugins plus separately configured API/mail connections;
- durable concurrent `AgentLoopWorkflowV1` Temporal workflows;
- versioned per-agent owner playbooks promoted explicitly from stakeholder chat;
- typed content packages, deterministic SEO QA, WordPress taxonomy/AIOSEO/media
  drafts, optional AI featured images, and full-content approval emails;
- autonomous legacy CEO/specialist loop with versioned company policy checks;
- provider-neutral local and remote model connections;
- durable polling worker, approvals, email approvals, and stakeholder chat;
- build/buy/integrate/manual strategy and platform capability records;
- optional agent-scoped Browser Automation plugin backed by isolated persistent
  Chromium and a manual cockpit;
- guided operator handoffs only when the selected agent has explicitly granted
  `request_human_takeover`; disabling the plugin hides its browser/intervention UI;
- governed OpenAI Computer Use missions with live dashboard visibility when granted;
- internal network-isolated execution runtime with idempotent per-company Git checkpoints;
- deterministic autonomy reliability scenarios and operator recovery from committed state.

This is a POC, not production software. The durable overlay now uses PostgreSQL
for company and portfolio state plus recovery-safe per-agent Temporal workflows. The
lightweight polling worker and SQLite backend remain development fallbacks.

## 2. Clone and select the correct branch

```powershell
git clone https://github.com/TheKalmar/Telekt.git
Set-Location Telekt
git fetch origin
git switch --track origin/codex/multi-agent-platform
Copy-Item .env.example .env.local
```

Never commit `.env.local`, API keys, SMTP passwords, company databases, browser
profiles, or Ollama model volumes.

## 3. Choose exactly one model setup

### A. Use Luka's existing Ollama models (recommended for his machine)

List the exact installed tags:

```powershell
ollama list
```

Make Ollama reachable from Docker. On Windows PowerShell, start it for development
with:

```powershell
$env:OLLAMA_HOST="0.0.0.0:11434"
ollama serve
```

In `.env.local` keep:

```env
OLLAMA_BASE_URL=http://host.docker.internal:11434/v1
INSTALL_DEFAULT_MODEL=false
```

Then start Telekt without downloading another model:

```powershell
.\scripts\up.ps1 -Mode Existing -Build
```

Open `http://127.0.0.1:8421`, use **Configuration → Models** to create/select the
local connection, then assign it to an agent in **Configuration → Agents**.
Different agents in the same company can use different models.

Good starting classes are 7B-14B instruct/reasoning models that reliably return
JSON. Available RAM/VRAM matters more than the brand name. Start with an 8B Q4
model on an 8 GB GPU; use larger models only after measuring latency and schema
reliability. Do not silently change the default for every developer.

### B. Download the bundled project model

The default is `deepseek-company:8b`, built from
`deepseek-r1:8b-0528-qwen3-q4_K_M`. It has 8 billion parameters, Q4_K_M
quantization, an approximately 5.2 GB download, an 8192-token context, and low
temperature for repeatable structured output.

CPU:

```powershell
.\scripts\up.ps1 -Mode Bundled -Build
```

NVIDIA GPU:

```powershell
.\scripts\up.ps1 -Mode Bundled -Gpu -Build
```

Watch first-run preparation:

```powershell
docker compose -f compose.yaml -f compose.local.yaml logs -f ollama-init
```

### C. OpenAI cloud only

Put the key only in `.env.local`:

```env
OPENAI_API_KEY=replace-locally
OPENAI_MODEL=gpt-5.4-mini
COMPUTER_USE_MODEL=gpt-5.6
COMPUTER_USE_MAX_STEPS=12
```

Start without Ollama:

```powershell
.\scripts\up.ps1 -Mode Cloud -Build
```

Create or edit a remote model connection and assign it to the desired agent. The
key is injected at container runtime or stored write-only in the local secret
vault; it is not copied into an image or stored in company state.

### D. Hybrid

Configure both a local and a remote connection, then assign the appropriate one
to each agent. A CEO can use the stronger remote connection while routine
content or operations agents use local/cheaper models. Computer Use remains a
governed cloud capability and requires its own approved `browser_operate` task.

## 4. Verify the installation

```powershell
docker compose ps
Invoke-RestMethod http://127.0.0.1:8421/health
Invoke-RestMethod http://127.0.0.1:8421/api/local-model/models
.\.venv\Scripts\python.exe -m pytest -q
```

Expected core services are `app`, `worker`, `browser-runtime`,
`execution-runtime`, `postgres`, `temporal`, and `temporal-ui`; bundled mode also
runs `ollama` and the one-shot `ollama-init`. `scripts/up.ps1` includes the free
self-hosted infrastructure overlay by default. The current suite has 142 tests.

The execution runtime has no published host port. Verify it through:

```powershell
Invoke-RestMethod http://127.0.0.1:8421/api/execution/health
```

General generated-code commands intentionally fail closed; read
`docs/execution-runtime.md` before changing that boundary.

If the virtual environment does not exist:

```powershell
py -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
```

## 5. How the execution loop works

```text
company facts + one agent's mandate/plugins/directive
  -> that agent returns one typed TaskProposal
  -> Governor allows, denies, or requests approval
  -> plugin authorization and connection capabilities are enforced
  -> specialist, API plugin, browser, or governed runtime executes
  -> result, evidence, agent usage, and audit events persist
  -> only that agent's durable workflow chooses the next task
```

The model never grants itself permission. `Governor`, the orchestrator, storage
transitions, browser allowlists, and approval records are deterministic boundaries.
Policy edits create immutable versions; approval quorum and expiry are durable,
while contract signing remains an invariant denial. See `docs/company-policy.md`.
Do not move external side effects into prompts.

For a Browser Mission authorized by that agent's `browser-automation` plugin,
the worker opens an isolated single-domain
Chromium session, requests structured actions from the Responses API `computer`
tool, executes a bounded batch, audits it, and returns a fresh screenshot. The
live dashboard shows objective, step, last action, status, and screenshot.
Pause/Stop are cooperative and take effect between model calls/browser actions.
WordPress uses its REST connection and never implies browser authority.

## 6. Source map

| Path | Responsibility |
|---|---|
| `src/digital_company/agents.py` | CEO and specialist prompts, Agents SDK, model routing |
| `src/digital_company/agent_templates.py` | Typed agent forms, defaults, mandates, and allowed actions |
| `src/digital_company/capability_plugins.py` | Reusable plugin definitions and deterministic grants |
| `src/digital_company/models.py` | Typed contracts at the probabilistic/deterministic boundary |
| `src/digital_company/policy.py` | Deterministic authorization Governor |
| `src/digital_company/execution_runtime.py` | Internal Git checkpoint and structured-command boundary |
| `src/digital_company/execution_client.py` | Worker-to-runtime fixed-host client |
| `src/digital_company/orchestrator.py` | Observe-decide-authorize-execute-record loop |
| `src/digital_company/wordpress_plugin.py` | Idempotent WordPress REST drafts and governed publication |
| `src/digital_company/store.py` | PostgreSQL/SQLite-compatible canonical state and audit projections |
| `src/digital_company/worker.py` | Background polling and company leases |
| `src/digital_company/web.py` | FastAPI control plane and browser proxy |
| `src/digital_company/browser_runtime.py` | Isolated Playwright/Chromium process |
| `src/digital_company/computer_use.py` | Bounded screenshot/action loop and interruption checks |
| `src/digital_company/static/index.html` | Zero-build dashboard shell and core render/navigation code |
| `src/digital_company/static/ui-core.js` | Shared HTML escaping, formatting, links, and API client |
| `src/digital_company/static/settings-ui.js` | Models, email, integrations, and company-policy screens |
| `src/digital_company/static/browser-ui.js` | Human-intervention browser cockpit |
| `tests/test_frontend_contract.py` | Module-load, escaping, and responsive-layout contract tests |
| `compose*.yaml` | Cloud, existing-local, bundled-local, GPU, and email variants |

Public code uses module/class/function docstrings for intent and security
boundaries. Comments explain non-obvious invariants; they should not narrate
obvious syntax. Update the relevant document whenever an architectural limitation
or setup command changes.

## 7. Safe development workflow

```powershell
git switch codex/multi-agent-platform
git pull --ff-only
git switch -c luka/<short-feature-name>
```

Before committing:

```powershell
.\.venv\Scripts\python.exe -m pytest -q
docker compose --env-file .env.local config --quiet
git diff --check
git status --short
```

Do not develop directly on `main`. Keep unrelated changes out of the same commit.
Do not run `docker compose down --volumes` unless intentionally deleting company
state, browser profiles, and downloaded models.

## 8. Current boundaries and recommended next work

Highest-value next engineering steps:

1. Add authentication and tenant authorization before any remote deployment.
2. Add production PostgreSQL/Temporal backup and restore drills.
3. Add WordPress/AIOSEO integration tests against a disposable staging site;
   the unit suite currently uses transport fakes.
4. Add real provider plugins with idempotency: Shopify OAuth/catalog drafts first.
5. Reconcile recorded token and image-generation estimates with provider invoices
   and add tool-call pricing.

Do not build autonomous CAPTCHA solving, silent account creation, contract signing,
payment submission, or unrestricted browser/shell access.

## 9. Read next

1. [Multi-agent platform](multi-agent-platform.md)
2. [Architecture](architecture.md)
3. [Developer guide](developer-guide.md)
4. [Model and Docker setup](model-setup.md)
5. [PostgreSQL and Temporal](postgres-temporal.md)
6. [Browser missions and human takeover](browser-handoffs.md)
7. [Known limitations](known-limitations.md)
