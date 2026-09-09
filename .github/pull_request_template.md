## Why

Describe the user or operational problem this change solves.

## What changed

- Describe the implementation and any deliberately deferred work.

## Safety and durability

- [ ] External side effects still pass deterministic capability and policy checks.
- [ ] Retried work is idempotent, or retry behavior is explicitly documented.
- [ ] No credentials, customer data, browser profiles, or company state are included.
- [ ] Database and workflow compatibility were considered.

## Verification

- [ ] `ruff check src tests scripts evals`
- [ ] `ruff format --check src tests scripts evals`
- [ ] `pytest --cov`
- [ ] `python evals/run_local.py --validate-only`
- [ ] UI changes include a screenshot or recording.
