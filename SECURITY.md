# Security Policy

## Project status

Telekt is a proof of concept and is not yet hardened for public, multi-tenant,
or unsupervised production use. Run it only in an environment whose users and
network you trust. Review [known limitations](docs/known-limitations.md) before
granting any real service connection.

## Reporting a vulnerability

Please do not publish exploitable details in a public issue. Use GitHub's private
vulnerability reporting feature for this repository when available. If it is
not enabled, contact the repository owner privately and include:

- the affected version or commit;
- reproduction steps and required configuration;
- the expected and observed security boundary;
- potential impact;
- a suggested mitigation, if known.

Do not include live credentials, personal data, or customer content in a report.

## High-risk areas

Security review should pay particular attention to capability grants, the
Governor, connection secrets, approval links, browser profiles, execution
isolation, WordPress publishing, Temporal retry behavior, and tenant/company
boundaries.

The model is never an authorization boundary. Prompt instructions must not be
treated as a substitute for deterministic permission checks.

