# CI gates by repo

Job ids are regenerated in `codemap/generated/cross-repo.md`; this page says what each gate protects.

| Repo | Blocking gates | What they protect |
|---|---|---|
| workflow | `reference` (offline demo + schema check + acdd-lint), per-protocol jobs (`project-state`, `feature-packet`, `return-manifest`, `recovery-protocol`, `bee-landed`, `campaign-report`, `observability`), `acceptance-light`, `secret-scan` (gitleaks), `slop-lint`, `rails-acceptance` | every schema validates its examples; the demo still rejects `scope_violation` and `tests_tampered`; signed evidence verifies |
| hyvv | `test`, `scan` (gitleaks), `windows-provisioning`, `bee-ci-*`, aggregate `ci`, `slop-lint`; `release` on tags | `/v1` conformance, installer dry run, release assets |
| bundlebees | `tests` (deterministic, no model), `plugin-doctor`, `orbit-roundtrip`, `secret-scan`, `slop-lint` | dispatch gates, verify sandbox, tool-schema token budget (`bundlebees/scripts/measure_schemas.py`) |
| orbitdesktop | `test` (offline regression, Rails vendor + HTTP, Hermes plugin, publisher parity), `fresh-container`, `catalog`, `slop-lint`; `browser` weekly/manual | workspace contract, plugin publication, archive parity |
| orbit-control-plane | none in GitHub Actions | `npm test` locally only |
| uirizz | `test` (pytest incl. token budgets), `desktop` (`npm test`, bundle check) | catalog admission, gate, budgets |
| rizzstack (this repo) | `codemap` (`.github/workflows/codemap.yml`) | generated map matches pinned SHAs; every `repo/path` in the narrative exists; L0/L1 token budgets |

## Shared rules

- **slop-lint ratchet:** `check --baseline slop-baseline.json --pass-on-unpruned` fails on new or higher findings and on a missing/invalid baseline; shrink with `prune`. Raising the baseline is an owner-reviewed change.
- **gitleaks** runs in CI where listed and locally before every push.
- **Actions pinned by full SHA**, `permissions: contents: read`, `persist-credentials: false`.
- **Fork-safe PR comments:** build in `pull_request` (read-only, artifact), post in `workflow_run` with `pull-requests: write`, no checkout of PR code (`workflow/.github/workflows/pr-summary-build.yml`, `workflow/.github/workflows/pr-summary-comment.yml`).
- **Risk tiers** are enforced at dispatch time in bundlebees, not in CI.
- Code-owner review is advisory.
