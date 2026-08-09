# Developer Guide

## Development setup

```powershell
py -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
.\.venv\Scripts\python.exe -m pytest -q
```

Start the web application:

```powershell
.\.venv\Scripts\digital-company-web.exe
```

The server binds to `127.0.0.1` and uses port `8421` unless `PORT` is set.

## Environment variables

| Variable | Required | Purpose |
|---|---|---|
| `OPENAI_API_KEY` | Default remote connection only | Responses API authentication |
| `OPENAI_MODEL` | No | Seeds the default remote connection model |
| `COMPUTER_USE_MODEL` | No | Visual browser model, default `gpt-5.6` |
| `COMPUTER_USE_MAX_STEPS` | No | Per-mission action-batch ceiling, default `12` |
| `PORT` | No | Control-plane port, default `8421` |

Ollama is expected at `http://127.0.0.1:11434` only for the bundled default
local connection. Arbitrary local or remote connection profiles and their
write-only credentials can be configured through the Models panel.

## Adding an action type

1. Add the value to `ActionType` in `models.py`.
2. Decide its default permission in `Governor.evaluate()`.
3. Add or update a specialist capable of producing its result.
4. Add deterministic execution code; do not place real side effects inside a prompt.
5. Add policy tests for allow, approval, denial, and budget behavior.
6. Add an audit event for execution success/failure.

## Adding a specialist

1. Extend the `specialist` literal in `TaskProposal`.
2. Add instructions in `SPECIALIST_INSTRUCTIONS`.
3. Update role routing rules in `AgentEngine` only when the role has different
   local/remote or hosted-tool requirements.
4. Add any required bounded context in `CompanyOrchestrator`.
5. Add an eval or test for output schema and routing.

Keep specialist output typed. Do not parse free-form prose to determine authority or costs.

## Changing model routing

Role routing is constructed in `AgentEngine.__init__`. SDK adapter construction
belongs to `ModelAdapterFactory`; connection profile persistence belongs to
`ModelConnectionRegistry`. Add a new model ID or endpoint through configuration,
not source code. Add code only for a genuinely new transport technology.

Known caveat: `set_tracing_disabled(True)` is SDK-global. Once local mode constructs an engine in a process, OpenAI trace export remains disabled until explicitly re-enabled or the process restarts. Model inference routing still works.

Before calling a new provider production-ready, test:

- JSON-schema structured output;
- context limits with a realistic company snapshot;
- malformed output recovery;
- timeouts and provider outages;
- tool compatibility;
- quality on representative CEO and specialist decisions.

## Persistence conventions

- Every meaningful transition writes an audit event.
- Proposed tasks are persisted before authorization.
- Approval payloads are immutable snapshots of the proposal.
- Budget changes are ledger entries, not a mutable remaining-balance field.
- Artifacts are referenced from state but stored outside agent context.
- All model-provided artifact paths and writes go through `WorkspaceRuntime`.
- Registry data and company business data must remain separate.

The current workspace permits only `.html`, `.css`, `.js`, `.json`, `.md`, and
`.txt` files up to 2 MiB each. Extend the allowlist only with a matching threat
model and tests. Do not replace `WorkspaceRuntime` with direct model-controlled
`Path.write_text` or shell commands.

## Database migrations

The POC schema bootstrap lives in `company_schema.py` and uses idempotent
`CREATE TABLE IF NOT EXISTS` plus additive column checks. This is acceptable for
additive prototypes only. Before production, introduce a migration framework
such as Alembic and version every schema change.

## Application service boundaries

- Put behavior shared by Temporal and the fallback worker in
  `company_runtime.py`; workers are composition roots, not business-rule owners.
- Keep operational telemetry interpretation pure in
  `operations_projection.py`; `CompanyStore` should only obtain persisted facts.
- Use `CompanyStore` as a context manager in request/activity/loop scopes so its
  SQLite or PostgreSQL connection has an explicit owner.
- Keep FastAPI request schemas in `api_models.py` and transport-independent
  readiness rules in `preflight.py`.

## Testing strategy

Current tests are deterministic and make no model calls. They cover policy,
storage, stakeholder semantics, model settings, company isolation, operational
telemetry, and workspace confinement.

Recommended next test layers:

1. API tests using FastAPI `TestClient`.
2. Agent routing tests that inspect provider assignment.
3. Local-model integration tests behind an opt-in marker.
4. Agents SDK eval cases for task selection and stakeholder handling.
5. Browser tests for create/switch/start/pause/approval flows.
6. Restart and concurrency tests.

## Code style

- Prefer typed Pydantic boundaries for all model-controlled data.
- Keep external effects deterministic and idempotent.
- Comment why a safety or orchestration decision exists, not obvious syntax.
- Never log or commit API keys.
- Avoid placing full artifacts in snapshots or prompts.
