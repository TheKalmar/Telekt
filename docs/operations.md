# Operations

## Starting services

Start Ollama using the platform installation, then verify it:

```powershell
ollama list
ollama ps
```

Start the control plane:

```powershell
.\.venv\Scripts\digital-company-web.exe
```

Check readiness:

```powershell
Invoke-RestMethod http://127.0.0.1:8421/health
Invoke-RestMethod http://127.0.0.1:8421/api/local-model/health
```

The app container probes the HTTP health endpoint. The worker container probes
its PID 1 process; the dashboard separately reports functional worker liveness
from its durable heartbeat.

## Backups

Stop all company loops before taking a consistent filesystem backup. Back up the complete `.company` directory, including `registry.db`, every company database, and artifact directories.

SQLite WAL mode is not explicitly configured. Do not copy a database while writes are active and assume the result is transactionally consistent.

## Logs and audit

Uvicorn writes process logs to stdout/stderr. Domain-level transitions are stored in each company's `audit_events` table and returned in the dashboard projection.

The audit log is application-level and not tamper-proof. Production requires immutable external audit storage.

## Operations dashboard

Mission control includes an **Operations & model telemetry** panel. It shows the
active agent and elapsed time, provider, worker heartbeat, successful and failed
model calls, structured-output retries, cloud fallbacks, average and p95 latency,
task-state counts, ledger-estimated spend, and recent audit events.

The projection uses the latest 250 audit events, so it is a recent operational
window rather than lifetime accounting. An active run is inferred from a
`model.started` event without a matching terminal `model.succeeded`,
`model.failed`, or `model.fallback_failed` event. After a hard process crash, the
last run may appear active until later recovery tooling marks it abandoned.

Token usage is captured from provider responses and shown with an estimated
budget-currency cost for known models; the ledger amount remains the
company's authorized estimated task spend, not an API invoice.

The **Runtime readiness** panel checks that the worker is online, the exact
per-company Ollama tag exists when local inference is required, the OpenAI key is
present for cloud/hybrid routing, and the browser runtime is reachable. Dashboard
checks are cached briefly to avoid polling dependent services every two seconds;
pressing Start always performs a fresh check and fails closed on blockers.

## Failure recovery

### Ollama offline

Pause the company, start Ollama, verify the configured model exists, then resume. There is currently no automatic local-to-cloud fallback.

### OpenAI unavailable

The legacy CLI converts common authentication/quota errors into JSON. The web background runner records other provider exceptions by setting company state to `error`. Inspect `runtime_control.detail`, correct the provider problem, and resume.

### Server restart

The separate worker automatically resumes companies whose durable state remains
`running`. Check `docker compose ps` and worker logs if a company does not
advance. Temporal is still required for production-grade histories, activity
retries, timers, and distributed worker scheduling.

### Stuck approval

Approve or reject the exact pending payload. A new stakeholder directive marks pending approvals as superseded so the CEO can reconsider under the new direction.

## Local model resource profile

The current model is tuned for an 8 GB laptop GPU:

- model: `deepseek-company:8b`;
- quantization source: Q4_K_M;
- context: 8,192 tokens;
- temperature: 0.1.

Large prompts can still exceed context. Keep snapshots compact and load artifacts only for the specialist that requires them.

## Production checklist

- Replace daemon threads with Temporal.
- Replace SQLite with PostgreSQL and explicit tenant isolation.
- Add identity, authentication, authorization, and CSRF protection.
- Encrypt secrets and remove environment-file dependency.
- Add sandboxed execution and network egress policy.
- Add idempotency keys for every external action.
- Add provider timeouts, retries, circuit breakers, and fallback policy.
- Reconcile estimated usage costs with provider billing exports.
- Add schema migrations and database backups.
- Add end-to-end tests and agent evals.
# Container operations

The bundled-local Compose stack contains five services:

- `app`: FastAPI control plane and autonomous loop.
- `worker`: autonomous loop execution and restart recovery.
- `ollama`: persistent local inference server.
- `ollama-init`: idempotent one-shot model bootstrapper.
- `browser-runtime`: isolated persistent headless Chromium controlled through the app proxy.

Company databases and artifacts live in the `company_data` named volume. Ollama
models live in `ollama_models`. Rebuilding or replacing containers therefore
does not erase operating state.

Browser cookies and profiles live in `browser_data`. Treat this volume as
sensitive authentication material: restrict host access, encrypt production
storage, and delete it when revoking all retained browser sessions.

Use `compose.gpu.yaml` only on a host with a working NVIDIA Container Toolkit.
The base `compose.yaml` remains CPU-compatible. Useful diagnostics are:

```powershell
docker compose ps -a
docker compose logs --tail 100 app
docker compose logs --tail 100 ollama
docker compose exec ollama ollama list
```

The application health endpoint is `GET /health`; Ollama connectivity is exposed
at `GET /api/local-model/health`. The Ollama port is intentionally not published
to the host because only the application needs to reach it.
