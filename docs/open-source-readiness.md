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

## Required before making the repository public

- [ ] Repository owner selects and adds a license
- [ ] Review current and historical commits for secrets and private customer data
- [ ] Enable GitHub private vulnerability reporting
- [ ] Confirm every bundled logo, icon, font, fixture, and example may be redistributed
- [ ] Replace personal addresses, real domains, and company-specific examples where inappropriate
- [ ] Run the complete quality gate from a clean clone
- [ ] Verify Docker startup in local-model and cloud-model modes from a new machine
- [ ] Mark the first public version as a pre-release / POC

## License decision

No license has been added automatically. Source code visible on GitHub is not the
same as open-source software without a license.

The practical default for this project is **Apache License 2.0** because it is
permissive and includes an explicit patent grant. **MIT** is shorter and widely
recognized, but has no comparable explicit patent language. The repository
owner must make this legal/product decision before public release.

## Production gaps

Publishing the POC must not imply that it is safe for untrusted tenants. The
production backlog still includes authentication, RBAC, encrypted secret
storage, stronger job isolation, rate limiting, backup/restore, centralized
observability, dependency and container scanning, adversarial agent evaluation,
and incident-response procedures.
