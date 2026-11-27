# CI gates by repo

Job ids are regenerated in `codemap/generated/cross-repo.md`; this page says what each gate protects.

| Repo | Blocking gates | What they protect |
|---|---|---|
| workflow | `reference` (offline demo + schema check + acdd-lint), per-protocol jobs (`project-state`, `feature-packet`, `return-manifest`, `recovery-protocol`, `bee-landed`, `campaign-report`, `observability`), `acceptance-light`, `secret-scan` (gitleaks), `slop-lint`, `rails-acceptance` | every schema validates its examples; the demo still rejects `scope_violation` and `tests_tampered`; signed evidence verifies |
| hyvv | `test`, `scan` (gitleaks), `windows-provisioning`, `bee-ci-*`, aggregate `ci`, `slop-lint` (first step: `scripts/vendor_slop_lint.py --check`); `release` on tags | `/v1` conformance, installer dry run, release assets; vendored slop-lint is byte-identical to the workflow commit in `tools/slop-lint/vendor-manifest.json` |
| bundlebees | `tests` (deterministic, no model), `plugin-doctor`, `orbit-roundtrip`, `secret-scan`, `slop-lint` (first step: `scripts/vendor_slop_lint.py --check`) | dispatch gates, verify sandbox, tool-schema token budget (`bundlebees/scripts/measure_schemas.py`); vendored slop-lint matches its pinned workflow manifest |
| orbitdesktop | `test` (offline regression, Rails vendor + HTTP, Hermes plugin, publisher parity), `fresh-container`, `catalog`, `slop-lint`; `browser` weekly/manual | workspace contract, plugin publication, archive parity |
| orbit-control-plane | `test` (Node 22, `npm test`), `rizzobs` (pytest `hermes-plugin/rizzobs`), `secret-scan` (gitleaks); `slop-lint` runs report-only (`--report-only`, never fails) | dashboard and connector tests, rizzobs plugin, no committed secrets |
| uirizz | `test` (pytest incl. token budgets), `desktop` (`npm test`, bundle check), `secret-scan` (gitleaks), `slop-lint` (blocking ratchet; first step: `scripts/vendor_slop_lint.py --check`) | catalog admission, gate, budgets, no committed secrets, no new slop findings |
| rizzstack (this repo) | `codemap` (`.github/workflows/codemap.yml`), `secret-scan` (gitleaks) and `workflow-hygiene` (`.github/workflows/gitleaks.yml`) | generated map matches pinned SHAs; every `repo/path` in the narrative exists; L0/L1 token budgets; no committed secrets; workflows pin actions by SHA |

## Merge gates (every repo)

- **`lease-tamper`** is a vendored copy of this repo's `.github/scripts/lease_tamper.py`, byte-identical in every product repo. It refuses a slice whose lease, frozen tests or contract were tampered with. It passes a non-slice PR only with an independent review at the exact head (or one carried over base-only merges with identical blobs). Its last line, `lease-tamper: class=<slice|non-slice> TIER=<T1|T3>`, is what the merge lane reads (`policy/merge-lane-v5.md`).
- **`ci-gate`** is the required aggregate. It `needs` every job in its workflow file, `lease-tamper` included, so one failed job fails the merge box.
- **`review-rerun.yml`**: when a review is submitted, it re-runs only a failed `lease-tamper` job on the PR's `pull_request` run (`ci-gate` follows as its dependent). A green check from a review-triggered run would not replace a failed same-named check from the `pull_request` run, and test jobs are never re-rolled.
- **Oracle lock (bundlebees):** the test job runs pytest through main's copy of `bundlebees/bundlebees_plugin/oracle_lock.py`, with main's pytest config, plugin autoload off and `python -P`, so a PR's own config can't change the tests that judge it.
- **Docs parity (workflow):** a `pull_request` run requires the PR's own coverage row or exclusion in `workflow/tests/test_docs_parity.py`, so merges don't turn main red.

## Shared rules

- **slop-lint ratchet:** `check --baseline slop-baseline.json --pass-on-unpruned` fails on new or higher findings and on a missing/invalid baseline; shrink with `prune`. Raising the baseline is an owner-reviewed change.
- **gitleaks** runs in CI in every repo listed and locally before every push.
- **Vendored slop-lint check:** hyvv, bundlebees and uirizz carry a copy of workflow's slop-lint; `scripts/vendor_slop_lint.py --check` fails if it drifts from the pinned manifest.
- **Actions pinned by full SHA**, `permissions: contents: read`, `persist-credentials: false`.
- **Fork-safe PR comments:** build in `pull_request` (read-only, artifact), post in `workflow_run` with `pull-requests: write`, no checkout of PR code (`workflow/.github/workflows/pr-summary-build.yml`, `workflow/.github/workflows/pr-summary-comment.yml`).
- **Two different risk tiers:** the brief's `risk.tier` (low/medium/high) is enforced at dispatch time in bundlebees and decides whether a bee needs a frozen contract. The merge-time tier (T1/T3) is computed in CI by `lease-tamper` from the changed paths and decides whether a merge needs a review.
- Code-owner review is advisory.
