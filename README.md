# Digital AI Company

A proof of concept for a portfolio of autonomous digital companies. A stakeholder creates a company with a goal, budget, profile, constraints, and success criteria. A CEO agent selects the next job, specialist agents execute internal work, a deterministic Governor enforces authority boundaries, and SQLite stores durable company state.

This repository proves the control loop and governance model. It is not yet a production-ready autonomous business platform.

## What is implemented

- Multi-company portfolio with isolated state and artifacts
- CEO, Research, Product, Development, QA, and Growth agent roles
- Local, hybrid, and cloud model routing
- Deterministic allow / approval / deny policy
- Durable tasks, decisions, approvals, budget ledger, chat, and audit history
- Stakeholder directives that supersede stale approvals
- Cooperative start, pause, and stop controls
- Operations dashboard with active agent, model latency, failures, fallbacks, task states, and audit events
- Confined per-company artifact workspace with atomic writes, validation, hashes, inventory, and downloads
- Build/buy/integrate/manual strategy gate with durable platform capability tracking
- Human takeover missions for login, CAPTCHA, 2FA, identity, terms, and other manual checkpoints
- Isolated persistent Chromium cockpit with domain allowlists, screenshots, manual clicks, and direct typing
- Local web control plane
- Local Ollama model support through an OpenAI-compatible API
- Automatic adoption of the original single-company POC database

## Architecture at a glance

```text
Browser control plane
        |
        v
FastAPI API ---- Portfolio registry
        |              |
        |              +---- Company A SQLite + artifacts
        |              +---- Company B SQLite + artifacts
        |
        v
CompanyOrchestrator
        |
        +---- AgentEngine (local / hybrid / cloud)
        +---- Governor (deterministic policy)
        +---- CompanyStore (durable state + audit)
```

See [Architecture](docs/architecture.md) for the full system description.

## Requirements

- Windows, Linux, or macOS
- Python 3.11+
- An OpenAI API key for cloud or hybrid mode
- Ollama plus `deepseek-company:8b` for local or hybrid mode

The local model configuration is in [config/Modelfile.deepseek-company](config/Modelfile.deepseek-company).

## Quick start

### Docker (recommended)

Docker Compose runs the control plane, browser runtime, Ollama, the customized local model, and
persistent volumes for company state and model files. No OpenAI key is required
when a company uses Local mode.

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

Use **Model settings** in Mission control to select Local, Hybrid, or Cloud and
choose any model reported by the configured Ollama server. Settings are stored
per company.

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

## Model modes

| Mode | CEO | Development | Research / Product / QA / Growth | Intended use |
|---|---|---|---|---|
| Local | Ollama | Ollama | Ollama | Free workflow testing and simple work |
| Hybrid | OpenAI | OpenAI | Ollama | Better quality with controlled cloud cost |
| Cloud | OpenAI | OpenAI | OpenAI | Highest quality |

Mode changes apply to future cycles and are rejected while a company is running. Local mode disables OpenAI trace export.

## Runtime controls

- **Start / Resume** starts a background loop for the selected company.
- **Pause** stops before the next atomic agent step.
- **Stop** also stops before the next atomic agent step.
- **Stakeholder directive** becomes priority CEO context and supersedes stale pending approvals.
- **Stakeholder question** requests a response without invalidating the current plan.

Pause and stop are cooperative. An in-flight model request or database write is allowed to finish.

## Tests

```powershell
.\.venv\Scripts\python.exe -m pytest -q
```

The current suite covers policy outcomes, durable state, model settings, operational telemetry, stakeholder messages, and multi-company isolation.

## Repository map

```text
src/digital_company/
  agents.py          Agents SDK definitions and model routing
  models.py          Typed agent/application contracts
  orchestrator.py    Autonomous control loop
  workspace.py       Confined artifact writes and deterministic validation
  browser_runtime.py Isolated Playwright/Chromium session service
  browser_policy.py  URL allowlist and human-checkpoint rules
  computer_use.py    Bounded OpenAI Computer Use mission controller
  policy.py          Deterministic Governor
  store.py           Per-company SQLite repository
  registry.py        Multi-company portfolio registry
  web.py             FastAPI control plane
  worker.py          Restart-safe autonomous cycle worker
  cli.py             Legacy single-company CLI
  static/index.html  Dashboard UI
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
9. [Model reliability](docs/model-reliability.md)
10. [Platform strategy and Shopify](docs/platform-integrations.md)
11. [Browser missions and human takeover](docs/browser-handoffs.md)

## Security warning

The Governor is the authorization boundary. Do not move external side effects into agent prompts or specialist code. A model may propose an action, but deterministic application code must authorize and execute it. Production deployments also need authentication, tenant authorization, encrypted secret storage, PostgreSQL, a durable workflow engine, and an isolated execution sandbox.
