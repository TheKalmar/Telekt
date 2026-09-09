# Engineering standards

This document describes the quality contract for Telekt. It exists so a local
change, a pull request, and a release candidate are judged by the same rules.

## One-command quality gate

From PowerShell, run:

```powershell
.\scripts\check.ps1
```

The command is location-independent and fails on the first broken gate:

1. Ruff static analysis and import ordering
2. Ruff formatting verification
3. Pytest with branch coverage
4. Offline validation of behavior-evaluation fixtures
5. Bandit medium/high-severity source scan
6. `pip-audit` against the installed dependency graph

Use `-SkipDependencyAudit` only when working without network access. GitHub
Actions always runs the complete gate on Python 3.11 and 3.12 and separately
validates every supported Docker Compose overlay.

## Coverage policy

Coverage is a guardrail, not a vanity metric. The repository enforces at least
65% branch-aware coverage across application code. Thin process entry points
for the browser runtime, lightweight worker, and Temporal worker are excluded;
their behavior is exercised through service boundaries and workflow tests.

New deterministic domain logic should normally arrive with focused unit tests.
Network and provider integrations should be tested at the local adapter
boundary without making paid or externally mutating calls.

## Architectural invariants

- Models propose work; deterministic code grants authority.
- PostgreSQL owns canonical state and Temporal owns durable execution history.
- Every externally visible side effect is capability-checked, policy-checked,
  idempotent where possible, and auditable.
- Company facts, agent configuration, plugin grants, and connection secrets are
  separate concerns.
- User-controlled paths stay inside a company workspace.
- Provider and runtime URLs use explicit HTTP(S) transports. Remote model
  endpoints require HTTPS, while plain HTTP is allowed only for declared local
  connections.
- Approval links are recipient-specific, expiring, signed, and state-changing
  only through an explicit POST.

## Language policy

English is the source language for code, comments, documentation, logs, API
errors, and first-run interface text. Additional UI languages live in the
localization catalog and are selected explicitly by the user. Language-specific
tokens used by content analysis or browser intent recognition are data, not
source copy, and must remain visibly scoped to that purpose.

## Review expectations

A pull request should explain the user problem, identify any changed authority
or persistence boundary, and list verification evidence. Visible changes need a
screenshot. Security-sensitive changes should include at least one negative
test that proves the operation fails closed.

Large structural improvements should be isolated from behavior changes whenever
possible. The current persistence facade remains intentionally broad during the
PostgreSQL migration; splitting it into bounded repositories is tracked as
technical debt rather than being hidden behind cosmetic abstractions.
