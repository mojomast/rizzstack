# workflow (L1): the method and the protocol

Pinned `c8ef4c8` · https://github.com/mojomast/workflow · generated detail: `codemap/generated/workflow.md`

**What it is.** The written method (numbered chapters in `workflow/docs`) plus the machine-checkable protocol every other layer obeys: JSON schemas, ACDD contract and TDD evidence formats, the observability snapshot schema, the Rails ledger wire format, reference tooling and lints. It is mostly documentation and validators; it runs no services itself except optional contrib collectors.

**Layer.** 1 (bottom). Nothing here imports other rizzstack repos. Other repos vendor or re-implement its formats.

## Where things are

| Need | Path |
|---|---|
| Method chapters (01–31), rules, coordinator playbook | `workflow/docs` (rules: `workflow/docs/06-rules-and-lessons.md`; playbook: `workflow/docs/19-coordinator-playbook.md`; ACDD+TDD: `workflow/docs/16-acdd-tdd.md`) |
| Three-layer framing | `workflow/docs/12-three-layer-architecture.md` |
| Protocol schemas (contracts, evidence, packets, RETURN, recovery, project state, memory, bee-landed) | `workflow/schemas` with examples in `workflow/schemas/examples` |
| Rails ledger wire format (`rizzstack.rails/1`) | `workflow/schemas/rails`, spec `workflow/specs/rizzstack-rails-spec.md` |
| Observability snapshot (`rizzstack.observability/snapshot/1`) | `workflow/observability/schemas/snapshot.schema.json`, envelope `workflow/observability/envelope.py`, validator `workflow/observability/validate.py`, alerts `workflow/observability/alerts.py` |
| Observability collector, events bridge, decision notifier | `workflow/contrib/rizzstack-observability` (`collector.py`, `events_bridge.py`, `decision_notifier.py`, systemd units) |
| Observatory web app (static) | `workflow/apps/rizzstack-observatory` |
| Templates people copy | `workflow/templates` (`CONTRACT.md`, `HANDOFF.md`, `RETURN.md`, `INFRA-DEPLOY.md`, `PARKED.md`) |
| Lints | `workflow/tools/acdd-lint` (contracts + signed evidence), `workflow/tools/slop-lint` (ratchet; source of truth, vendored into bundlebees and hyvv) |
| Protocol checkers | `workflow/tools/feature_packet.py`, `workflow/tools/return_manifest.py`, `workflow/tools/recovery.py`, `workflow/tools/bee_landed.py`, `workflow/tools/campaign_report.py`, `workflow/tools/project-state` |
| Offline reference demo (no keys, no services) | `workflow/reference/run_demo.py`, library `workflow/reference/workflow`, schema check `workflow/reference/check_schemas.py` |
| Cold-start acceptance harness (real bundlebees path) | `workflow/acceptance/harness.py`, `workflow/acceptance/cold_start.sh` |
| Memory contract (`rizzstack.memory/1`) | `workflow/rizzstack_memory/vault.py`, `workflow/schemas/memory-schema.v1.json`, `workflow/docs/memory-contract.md` |
| bee-ci (dispatches CI work to a hyvv launcher) | `workflow/contrib/bee-ci/dispatch.py` |
| Signed evidence from real runs | `workflow/evidence` |

## Entry points

- `python3 reference/run_demo.py --out demo-run`: the offline demo; also run by CI.
- `python3 -m acdd_lint <dir>` (from `tools/acdd-lint`) and `python3 -m slop_lint check . --baseline slop-baseline.json --pass-on-unpruned` (with `PYTHONPATH=tools/slop-lint`).
- `python3 workflow/observability/validate.py`-style validation is imported by the collector; see `workflow/observability/README.md`.

## CI gates (from `workflow/.github/workflows`)

`ci.yml` runs one job per protocol piece (`reference`, `project-state`, `feature-packet`, `return-manifest`, `recovery-protocol`, `bee-landed`, `campaign-report`, `observability`, `acceptance-light`, `secret-scan` with gitleaks, plus `bee-ci-*` shadows). `slop-lint.yml` is the blocking ratchet. `pr-summary-build.yml` (read-only, `pull_request`) + `pr-summary-comment.yml` (`workflow_run`, `pull-requests: write`, no checkout) are the fork-safe PR summary pair. `rails-acceptance.yml`, `rails-evidence-pr.yml` gate the Rails wire format. CODEOWNERS: raising `slop-baseline.json` needs owner review (advisory).

## Change recipes

- **Add or change a protocol schema:** edit `workflow/schemas/<name>.schema.json`, add/adjust an example in `workflow/schemas/examples`, extend the matching checker in `workflow/tools`, add or extend the CI job in `workflow/.github/workflows/ci.yml`. Bump the id (`…/2`) for breaking changes; consumers in bundlebees/hyvv/orbitdesktop pin ids (see `codemap/generated/cross-repo.md`).
- **Change the snapshot schema:** `workflow/observability/schemas` + `workflow/observability/envelope.py` + collector, then the Observatory consumers (`workflow/apps/rizzstack-observatory`, `orbitdesktop/apps/rizzstack-observatory`). Note the separate control-plane schema in `codemap/gaps.md`.
- **Change slop-lint:** `workflow/tools/slop-lint/slop_lint`, then re-vendor into `bundlebees/tools/slop-lint` and `hyvv/tools/slop-lint`. orbitdesktop has its own JS port `orbitdesktop/scripts/slop-lint.mjs`.
- **Add a method chapter:** next number in `workflow/docs`, add a row to the index in `workflow/README.md`.

## Invariants specific to this repo

- Public repo: hosts, ports, container names and private paths stay in the private host runbook, described generically.
- `not run` is never counted as passed; self-reported worker results are not evidence.
- Historical evidence under `workflow/evidence` is append-only history; CI asserts the one known stale record rather than deleting it.
