# bundlebees (L1): the coordinator

Pinned `cb8946e` (plugin.yaml 0.2.0; last tag v0.1.1) · https://github.com/mojomast/bundlebees · generated detail: `codemap/generated/bundlebees.md`

**What it is.** A native Hermes plugin plus a user-run CLI. The model gets a few small tools to check a worker launcher, dispatch leased briefs, watch attempts and print a host setup prompt; the human-run CLI (`hermes bundlebees verify`, `hermes bundlebees deploy`, `job …`) applies patches in a sandbox, verifies, integrates and pushes a feature branch only. On `main` it also carries an untagged autonomy layer: durable jobs, feature queue, reconciler, bounded recovery, RETURN packets, wake-up and remote landing ingestion.

**Layer.** 3. Talks down to hyvv `/v1` (or Orbit's loopback `/api/local-workers` adapter) and is driven from Hermes / Orbit above. Owns the Rails ledger runtime that hyvv and orbitdesktop vendor.

## Where things are

| Need | Path |
|---|---|
| Plugin registration, tools, hook | `bundlebees/__init__.py` (registers tools; `pre_tool_call` hook asks Hermes approval before cancel/review), `bundlebees/plugin.yaml` |
| Tool schemas + handlers (`bundlebees_status`, `bundlebees_dispatch`, `bundlebees_attempt`, `bundlebees_host_prompt`, `bundlebees_route_preview`; optional `bundlebees_rails`) | `bundlebees/bundlebees_plugin/tools.py` (`_dispatch` holds the risk-tier and contract gates) |
| Brief validation | `bundlebees/bundlebees_plugin/brief.py` |
| Risk tiers: path-derived floor, effective tier | `bundlebees/bundlebees_plugin/risk.py`, floor data `bundlebees/bundlebees_plugin/policy/risk-floor.json` (the tier → process table is parked unwired in `bundlebees/contrib/unwired/risk_policy.py`) |
| Oracle lock: tests judged with main's pytest config, autoload off, `python -P`; harness-owned names refused in patches | `bundlebees/bundlebees_plugin/oracle_lock.py`, used by `bundlebees/bundlebees_plugin/verify_local.py` |
| Test-subversion scan (repo-backed; base findings subtracted only on unchanged lines and recorded in the verify evidence) | `bundlebees/bundlebees_plugin/subversion_scan.py` |
| Verify sandbox, pinned tools (uv, pwsh) staged with owner/mode checks and recorded hashes | `bundlebees/bundlebees_plugin/sandbox.py` |
| ACDD contracts (freeze by hash), TDD evidence, contract gate | `bundlebees/bundlebees_plugin/contract.py`, `bundlebees/bundlebees_plugin/tdd.py`, `bundlebees/bundlebees_plugin/contract_gate.py` |
| File leases | `bundlebees/bundlebees_plugin/leases.py` |
| HTTP clients (Orbit adapter, launcher `/v1`) | `bundlebees/bundlebees_plugin/client.py`, `bundlebees/bundlebees_plugin/launcher_adapter.py`, `bundlebees/bundlebees_plugin/adapters.py` |
| Sandboxed verify + integrate | `bundlebees/bundlebees_plugin/sandbox.py`, `bundlebees/bundlebees_plugin/verify_local.py`, `bundlebees/bundlebees_plugin/integrate_job.py` |
| CLI (`hermes bundlebees …`) | `bundlebees/bundlebees_plugin/cli.py`, jobs `bundlebees/bundlebees_plugin/job_cli.py`, campaign `bundlebees/bundlebees_plugin/campaign_cli.py`, rails `bundlebees/bundlebees_plugin/rails_cli.py` |
| Durable jobs + owner authorization, one-time approval | `bundlebees/bundlebees_plugin/job.py`, `bundlebees/bundlebees_plugin/approval.py`, `bundlebees/bundlebees_plugin/grants.py` |
| Factory loop (queue → landing bridge → event inbox → scheduler) | `bundlebees/bundlebees_plugin/factory_driver.py`, `bundlebees/bundlebees_plugin/work_scheduler.py`, `bundlebees/bundlebees_plugin/queue.py`, `bundlebees/bundlebees_plugin/landing_bridge.py`, `bundlebees/bundlebees_plugin/event_consumer.py`, scout `bundlebees/bundlebees_plugin/scout_hook.py` |
| Board sync to Hermes kanban | `bundlebees/bundlebees_plugin/board_sync.py`, `bundlebees/bundlebees_plugin/board_dispatch.py` |
| Recovery, research-before-retry, RETURN | `bundlebees/bundlebees_plugin/recovery.py`, `bundlebees/bundlebees_plugin/research_gate.py`, `bundlebees/bundlebees_plugin/return_report.py` |
| Action queue + fencing, owner decisions | `bundlebees/bundlebees_plugin/coordinator_actions.py`, `bundlebees/bundlebees_plugin/owners.py`, Hive docket (parked, unwired) `bundlebees/contrib/unwired/hive_docket.py` |
| Scrubbing, child env, URL guards, path confinement | `bundlebees/bundlebees_plugin/security.py` |
| Rails ledger runtime (source of truth; vendored elsewhere) | `bundlebees/contrib/rizzstack_rails` with `vendor-manifest.json`; Rails sources/actions `bundlebees/bundlebees_plugin/rails_actions.py`, `bundlebees/bundlebees_plugin/rails_remote_source.py` |
| Desktop pane, dashboard API, operator skill | `bundlebees/desktop/plugin.js`, `bundlebees/dashboard/plugin_api.py`, `bundlebees/skills/bundlebees-operator` |
| Docs per subsystem | `bundlebees/docs` (`ADAPTERS.md`, `ACDD.md`, `RAILS.md`, `factory-driver-service.md`, `coordinator-actions.md`, …) |

## The dispatch gate (fail closed, in `_dispatch`)

`validate_brief` → risk: a declared tier below the path-derived floor returns `risk_tier_below_floor`; a high tier without a frozen `acdd/1` contract returns `contract_missing` → research gate: a retry of a recorded failure is refused until accepted findings exist → only then a launcher call. Errors are JSON with `code`, `error`, `fix`.

## CI gates

`ci.yml`: `tests` (pytest, deterministic, no model calls), `plugin-doctor`, `orbit-roundtrip`, `secret-scan` (gitleaks); `scripts/measure_schemas.py` keeps the tool-schema token budget (under 900 o200k). `slop-lint.yml` blocks new findings. CODEOWNERS on `slop-baseline.json` (advisory).

## Change recipes

- **Add a tool:** handler + schema in `bundlebees/bundlebees_plugin/tools.py`, register in `bundlebees/__init__.py`, list it under `provides_tools` in `bundlebees/plugin.yaml`, re-measure with `bundlebees/scripts/measure_schemas.py`, tests in `bundlebees/tests`.
- **Change the risk floor or policy:** `bundlebees/bundlebees_plugin/risk.py` (path patterns), `bundlebees/bundlebees_plugin/policy/risk-floor.json` (floor data; the unwired process table is `bundlebees/contrib/unwired/risk_policy.py`). Tiers only go up; unknown fails closed to high.
- **Support a new worker backend:** implement the adapter contract in `bundlebees/docs/ADAPTERS.md` (`bundlebees/bundlebees_plugin/adapters.py`); don't teach the coordinator the backend's internals.
- **Change the Rails runtime:** edit `bundlebees/contrib/rizzstack_rails`, regenerate its manifest, then re-vendor with `hyvv/scripts/vendor_rails.py` and orbitdesktop's vendor check (`orbitdesktop/tests/test_rails_vendor.py`).

## Invariants

- The model dispatches and watches; a human runs verify and deploy. Verify pushes a feature branch only.
- Worker tests run in bwrap/firejail with no network and no coordinator credentials; `unshare` only with an explicit unsafe override; refuse if no sandbox.
- Secrets only via Hermes `get_secret`; the desktop pane never shows tokens or host addresses; the host prompt never takes a host, token or URL argument.
- A task/project `accepted` is not campaign acceptance.
