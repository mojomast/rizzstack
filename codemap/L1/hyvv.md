# hyvv (L1): the worker host

Pinned `9691837` (package 0.1.17; launcher release 0.1.15) · https://github.com/mojomast/hyvv · generated detail: `codemap/generated/hyvv.md`

**What it is.** The runtime on each machine where bees run (a Windows PC with Docker Desktop, or an SSH host). It owns the sandboxes, keeps provider keys on the host and never returns them, and exposes a narrow token-scoped HTTP API, the versioned `/v1` contract, on loopback or a private address only. "hyvv is a vocabulary, not a dependency on bundlebees": any coordinator that speaks `/v1` can drive it.

**Layer.** 2. Called by bundlebees (`bundlebees/bundlebees_plugin/client.py`), Orbit's local-workers adapter (`orbitdesktop/server/local-workers.mjs`) and workflow bee-ci (`workflow/contrib/bee-ci/dispatch.py`).

## Where things are

| Need | Path |
|---|---|
| The runtime (HTTP handler, attempt lifecycle, backends, model routing) | `hyvv/launcher.py`: `Launcher`, `make_handler`, `CuaBackend` (Cua SDK sandboxes), `FakeBackend` (tests), `model_spec`, `load_config`, `in_assigned_scope`, `export_project` |
| CLI (`hyvv` → `hyvv.cli:main`) | `hyvv/hyvv/cli.py` |
| `/v1` contract (source of truth) | `hyvv/contract/v1/openapi.yaml`, schemas in `hyvv/contract/v1/schemas`, error codes `hyvv/contract/v1/error_codes.json`, semver policy `hyvv/contract/v1/SEMVER.md` |
| What is implemented vs reference-only vs specified | `hyvv/contract/v1/STATUS.json` → table rendered by `hyvv/contract/v1/status.py` into the README |
| Reference server for not-yet-shipped routes (artifact fetch, project-state gate) | `hyvv/contract/v1/reference.py`, `hyvv/contract/v1/project_state.py` |
| Bee-slot leases (specified, unimplemented) | `hyvv/contract/v1/LEASES.md` |
| Workspace provisioning (`rizzstack.provisioning/1`) | `hyvv/provisioning.py` |
| Durable `bee-landed/1` outbox export | `hyvv/landing_export.py`, doc `hyvv/docs/bee-landed.md` |
| Rails worker recorder + read-only replication (`/v1/rails/...`) | `hyvv/rails_emitter.py`, `hyvv/rails_export.py`, `hyvv/rails_log_source.py`, doc `hyvv/docs/rails.md` |
| Vendored Rails runtime (subset, from bundlebees) | `hyvv/contrib/rizzstack_rails`, refreshed by `hyvv/scripts/vendor_rails.py` |
| Brief / result schemas the launcher accepts | `hyvv/brief.schema.json`, `hyvv/result.schema.json`, example config `hyvv/config.example.json` |
| `rizzstack.brief/1` validation (report-only; `brief_validation` on the attempt: valid / invalid / unversioned / unavailable, no values echoed) | `brief_validation` in `hyvv/launcher.py`, vendored validator `hyvv/hyvv/rizzstack_validators.py`, frozen schemas in `hyvv/schemas` |
| Attempt journal (one JSON per attempt, per-attempt save lock, stale `lseq` writes refused) | `Launcher._save` in `hyvv/launcher.py` |
| Deploy to the worker host (`apply-update`: build-sha proof, wheel-derived payload, pre-restart smoke, automatic revert) | `hyvv/scripts/launcher-admin.ps1`, `hyvv/scripts/deploy_payload.py` |
| Install, start, doctor (Windows-first) | `hyvv/scripts` (`install.ps1`, `install.sh`, `start.ps1`, `doctor.ps1`, `contract-test.sh`) |
| Deployment + pins | `hyvv/docs/DEPLOYMENT.md`, `hyvv/docs/STACK-PINS.md`, ADRs in `hyvv/docs/adr` |

## Contract summary (`/v1`)

Implemented in the launcher: `GET /v1/version`, `GET /v1/health`, `GET|POST /v1/attempts`, `GET /v1/attempts/{id}`, `…/events`, `…/result`, `POST …/review`, `POST …/cancel`, `GET /v1/projects`, `GET /v1/containers`, `POST /v1/config`. Reference-only: `GET /v1/attempts/{id}/artifact` (`hyvv.worker-artifact/1`, `bee.usage/v1`). Specified only: `/v1/leases`. Additive merged routes not yet in `STATUS.json`: provisioning receipts and landing export. Every route needs the host bearer token; Rails replication has its own read credential. Full operation list: `codemap/generated/hyvv.md`.

**Model routing:** an explicitly named model in the brief is authoritative. Unprefixed and `go:` go to OpenCode Go, `deepseek:` to DeepSeek; `priority: high` reroutes only a brief that names no model. Go limits fall back to DeepSeek automatically (`go_fallback`).

## CI gates

`ci.yml`: `test` (pytest), `scan` (gitleaks), `windows-provisioning`, `bee-ci-*` shadows, aggregate `ci`, `lease-tamper` and the required aggregate `ci-gate`; `review-rerun.yml` re-runs a failed `lease-tamper` on a submitted review. The wheel test checks that every file under `schemas/` ships. `release.yml` builds release assets (`hyvv/scripts/build_release_assets.py`). `slop-lint.yml` blocks new findings.

## Change recipes

- **Add a `/v1` route:** `hyvv/contract/v1/openapi.yaml` + a schema in `hyvv/contract/v1/schemas` + error codes, conformance tests under `hyvv/contract/v1/tests`, implement in `hyvv/contract/v1/reference.py` first, then in `make_handler` in `hyvv/launcher.py`, then update `hyvv/contract/v1/STATUS.json` and run `python -m contract.v1.status --write`. Additive only; a break is `/v2` served alongside for a release. Then update clients: `bundlebees/bundlebees_plugin/client.py`, `orbitdesktop/server/local-workers.mjs`.
- **Add a model/provider route:** `model_spec` and config handling in `hyvv/launcher.py`, `hyvv/config.example.json`. Keys are read on the host only, never echoed.
- **Deploy:** merge first, then the owner's typed yes, then one `apply-update <main sha>` through `hyvv/scripts/launcher-admin.ps1`. Success means the reported `+g<sha>` equals the target, the smoke passes (`brief_validation` of a valid probe is `valid`), and health is back. On failure it reverts by itself, so don't retry in a loop.
- **Change what a bee may touch:** scope checks (`in_assigned_scope`) in `hyvv/launcher.py`; the coordinator-side lease is in bundlebees.

## Invariants

- Provider keys never leave the host; responses and logs never echo tokens.
- Bind to loopback or a private interface only; never a public listener.
- A worker cannot widen its own `assigned_files`. Recording and export are off by default.
- `/v1` is semver-additive.
