# Open-source readiness

This checklist separates publishing the source from claiming production
readiness. The repository can be shared for learning and contribution while the
product remains explicitly experimental.

## Completed in this pass

- [x] Public-facing project explanation and honest scope in `README.md`
- [x] Narrative case study based on the actual development history
- [x] Contributor guide with architectural invariants
- [x] Security reporting policy and POC warning
- [x] Local, cloud-model, hybrid, and Docker setup documentation
- [x] `.env.local`, company state, backups, model data, and browser data excluded
- [x] Tracked-file credential pattern check
- [x] Automated lint, formatting, branch-coverage, behavior-eval, dependency,
      medium/high-severity security, and Docker Compose checks in CI
- [x] English-first source and first-run UI, with Serbian isolated in the locale
      catalog and language-processing rules
- [x] Browser security headers and strict model/runtime URL validation
- [x] Apache License 2.0 selected and added to repository and package metadata
- [x] Current and historical commits scanned for common credential patterns
- [x] Complete quality gate run from a clean GitHub Actions checkout
- [x] Public version clearly marked as an alpha / proof of concept

## Required before a production release

- [x] Enable GitHub private vulnerability reporting
- [ ] Confirm every bundled logo, icon, font, fixture, and example may be redistributed
- [ ] Replace personal addresses, real domains, and company-specific examples where inappropriate
- [ ] Verify Docker startup in local-model and cloud-model modes from a new machine

## License

Telekt is distributed under the [Apache License 2.0](../LICENSE), a permissive
open-source license with an explicit patent grant. Contributors submit their
work under the same terms unless they explicitly state otherwise.

## Production gaps

Publishing the POC must not imply that it is safe for untrusted tenants. The
production backlog still includes authentication, RBAC, encrypted secret
storage, stronger job isolation, rate limiting, backup/restore, centralized
observability, dependency and container scanning, adversarial agent evaluation,
and incident-response procedures.
