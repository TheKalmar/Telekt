# Developer handoff for Luka

This is the shortest reliable path from a clean machine to a working Telekt
development environment. Read this file first, then use the linked documents for
the subsystem being changed.

## 1. What this branch contains

Use branch `codex/browser-mission-dashboard`. It contains the complete current
POC history, including:

- multi-company portfolio and per-company goals, budgets, settings, and state;
- autonomous CEO/specialist decision loop with deterministic policy checks;
- local, hybrid, and OpenAI cloud model routing;
- durable polling worker, approvals, email approvals, and stakeholder chat;
- build/buy/integrate/manual strategy and platform capability records;
- isolated persistent Chromium runtime and manual browser cockpit;
- governed OpenAI Computer Use missions with live dashboard visibility;
- automatic human takeover for login, CAPTCHA, 2FA, safety checks, and form submission.

This is a POC, not production software. SQLite and the polling worker deliberately
stand in for the planned PostgreSQL and Temporal layers.

## 2. Clone and select the correct branch

```powershell
git clone https://github.com/TheKalmar/Telekt.git
Set-Location Telekt
git fetch origin
git switch --track origin/codex/browser-mission-dashboard
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

Open `http://127.0.0.1:8421`, pause the selected company, open **Model settings**,
press **Refresh local models**, and select the exact tag reported by `ollama list`.
The setting is per company, so different companies can use different local models.

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

Choose **Cloud only** in Model settings. The key is injected at container runtime;
it is not copied into an image or stored in company state.

### D. Hybrid

Configure both a reachable Ollama server and `OPENAI_API_KEY`, start with
`-Mode Existing` or `-Mode Bundled`, then choose **Hybrid**. CEO, Development,
and evidence-backed Research use OpenAI; Platform, Operations, Product, QA, and
Growth use the chosen local model. Research receives hosted web search and must
return direct sources. Computer Use is always a cloud capability and requires its own
approved `browser_operate` task.

## 4. Verify the installation

```powershell
docker compose ps
Invoke-RestMethod http://127.0.0.1:8421/health
Invoke-RestMethod http://127.0.0.1:8421/api/local-model/models
.\.venv\Scripts\python.exe -m pytest -q
```

Expected POC services are `app`, `worker`, and `browser-runtime`; bundled mode
also runs `ollama` and the one-shot `ollama-init`. The current suite has 50 tests.

If the virtual environment does not exist:

```powershell
py -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
```

## 5. How the execution loop works

```text
stakeholder goal/directive
  -> CEO returns one typed TaskProposal
  -> Governor allows, denies, or requests approval
  -> specialist or governed runtime executes
  -> result, evidence, cost estimate, and audit events persist
  -> CEO observes the new canonical snapshot and chooses the next task
```

The model never grants itself permission. `Governor`, the orchestrator, storage
transitions, browser allowlists, and approval records are deterministic boundaries.
Do not move external side effects into prompts.

For an approved Browser Mission, the worker opens an isolated single-domain
Chromium session, requests structured actions from the Responses API `computer`
tool, executes a bounded batch, audits it, and returns a fresh screenshot. The
live dashboard shows objective, step, last action, status, and screenshot.
Pause/Stop are cooperative and take effect between model calls/browser actions.

## 6. Source map

| Path | Responsibility |
|---|---|
| `src/digital_company/agents.py` | CEO and specialist prompts, Agents SDK, model routing |
| `src/digital_company/models.py` | Typed contracts at the probabilistic/deterministic boundary |
| `src/digital_company/policy.py` | Deterministic authorization Governor |
| `src/digital_company/orchestrator.py` | Observe-decide-authorize-execute-record loop |
| `src/digital_company/store.py` | Canonical per-company SQLite state and audit projections |
| `src/digital_company/worker.py` | Background polling and company leases |
| `src/digital_company/web.py` | FastAPI control plane and browser proxy |
| `src/digital_company/browser_runtime.py` | Isolated Playwright/Chromium process |
| `src/digital_company/computer_use.py` | Bounded screenshot/action loop and interruption checks |
| `src/digital_company/static/index.html` | Zero-build mission-control dashboard |
| `compose*.yaml` | Cloud, existing-local, bundled-local, GPU, and email variants |

Public code uses module/class/function docstrings for intent and security
boundaries. Comments explain non-obvious invariants; they should not narrate
obvious syntax. Update the relevant document whenever an architectural limitation
or setup command changes.

## 7. Safe development workflow

```powershell
git switch codex/browser-mission-dashboard
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

1. Add an end-to-end Browser Mission fixture/site and replayable Computer Use evals.
2. Persist a resumable mission conversation instead of ending a paused mission.
3. Add authentication and tenant authorization before any remote deployment.
4. Replace SQLite/leases with PostgreSQL and Temporal.
5. Add real provider connectors with idempotency: Shopify OAuth/catalog drafts first.
6. Reconcile recorded token estimates with provider invoices and add tool-call pricing.

Do not build autonomous CAPTCHA solving, silent account creation, contract signing,
payment submission, or unrestricted browser/shell access.

## 9. Read next

1. [Architecture](architecture.md)
2. [Developer guide](developer-guide.md)
3. [Model and Docker setup](model-setup.md)
4. [Browser missions and human takeover](browser-handoffs.md)
5. [Operations](operations.md)
6. [Known limitations](known-limitations.md)
