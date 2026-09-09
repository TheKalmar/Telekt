# Contributing to Telekt

Telekt is an experimental control plane for governed, durable digital-agent
organizations. Contributions that make autonomy more observable, recoverable,
portable, and safe are especially welcome.

## Before opening a change

1. Read the [case study](CASE_STUDY.md) for product context.
2. Read [the architecture](docs/architecture.md) and
   [multi-agent boundaries](docs/multi-agent-platform.md).
3. Check [known limitations](docs/known-limitations.md).
4. Open an issue for a large behavioral or architectural change before doing
   extensive implementation work.

## Local development

Use the Docker quick start in the [README](README.md), or install the Python
development dependencies:

```powershell
py -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip setuptools
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
.\.venv\Scripts\python.exe -m pytest -q
```

Never commit `.env.local`, `.company/`, model files, browser profiles, database
volumes, credentials, customer data, or generated private company artifacts.

## Design rules

- PostgreSQL owns canonical company state; Temporal owns durable execution.
- Models may propose actions but cannot grant themselves authority.
- External side effects must pass typed capability and deterministic policy checks.
- Retried work must be idempotent and auditable.
- Keep company, agent, plugin, and connection concerns separate.
- Preserve the lightweight SQLite path unless a change explicitly removes it.
- Add tests for permission, recovery, migration, and multi-company isolation behavior.

## Pull requests

Explain the user problem, the chosen boundary, important tradeoffs, and how the
change was verified. Include screenshots for visible UI changes. Keep unrelated
refactors separate, and update documentation when behavior or configuration
changes.

By submitting a contribution, you agree that it may be distributed under the
license selected for this repository. Do not submit code or data you do not have
the right to contribute.
