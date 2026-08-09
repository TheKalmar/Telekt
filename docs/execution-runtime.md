# Isolated execution runtime

The default Compose stack includes `execution-runtime`, an internal-only service
with no host port and no public-internet network. It owns the separate
`execution_data` volume; app and worker never give it the PostgreSQL connection,
model credentials, SMTP credentials, browser profile, or the main
`company_data` volume.

## Current production-safe capability

Development artifacts are copied through a token-authenticated structured API
into a per-company workspace and committed to a local Git branch named from the
task ID. Every checkpoint has an idempotency key. Repeating the same request
returns the cached commit; reusing the key with different bytes is rejected.

The worker records `execution.repository_checkpointed` with the branch and
commit. Git `clone`, `fetch`, `push`, remote configuration, deployment, and
credential use are not available through this service. Publication to GitHub
will be a separate governed connector with an approval and provider-level
idempotency contract.

## Command boundary

The API contains a structured command contract for Python, pytest, Node, npm,
and a small local Git command set. It never uses `shell=True`, limits arguments,
timeouts and output, and caches results by idempotency key. However, a working
directory restriction is not sufficient tenant isolation for arbitrary
generated code: a subprocess could still inspect sibling workspaces or call an
internal service.

Therefore `EXECUTION_COMMANDS_ENABLED=false` is hardcoded in the standard
Compose service. General commands fail closed with HTTP 503. Enable them only in
the deterministic test process. Production execution requires a per-job sandbox
such as an ephemeral container/VM with:

- only one company workspace mounted;
- no service credentials;
- no network by default;
- read-only base filesystem;
- CPU, memory, PID, file-size and wall-clock limits;
- explicit egress capability granted by policy;
- destruction after the job result is checkpointed.

Do not solve this by mounting the Docker socket into `execution-runtime`; that
would effectively grant host control.

## Verification

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_execution_runtime.py -q
docker compose --env-file .env.local -f compose.yaml -f compose.infrastructure.yaml ps execution-runtime
Invoke-RestMethod http://127.0.0.1:8421/api/execution/health
```

The internal runtime URL is intentionally accessible only through the control
plane proxy/health boundary, not through a published Docker port.
