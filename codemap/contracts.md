# Cross-repo contracts

The seams of the stack. Repos do not import each other; they agree on these formats and APIs. When you change one, update every consumer in the same campaign. The generated "who mentions which id" matrix is `codemap/generated/cross-repo.md`.

## Wire APIs

| Contract | Source of truth | Server | Clients | Versioning |
|---|---|---|---|---|
| **hyvv `/v1` worker API** (attempts, events, result, review, cancel, projects, containers, config; artifact reference-only; leases specified) | `hyvv/contract/v1/openapi.yaml`, `hyvv/contract/v1/schemas`, status `hyvv/contract/v1/STATUS.json` | `hyvv/launcher.py` (`make_handler`); reference `hyvv/contract/v1/reference.py` | `bundlebees/bundlebees_plugin/client.py`, `orbitdesktop/server/local-workers.mjs`, `workflow/contrib/bee-ci/dispatch.py` | additive only; breaking = `/v2` alongside (`hyvv/contract/v1/SEMVER.md`) |
| **Orbit local-workers adapter** `/api/local-workers` (loopback) | `orbitdesktop/server/local-workers-route.mjs` | orbitdesktop | `bundlebees/bundlebees_plugin/client.py`, `orbit-control-plane/connector/orbit-connector.mjs`, `orbit-control-plane/scripts/obs-snapshot.mjs` | none declared; states in `orbitdesktop/server/local-workers.mjs` |
| **Rails replication** `/v1/rails/events`, `/v1/rails/head` | `workflow/schemas/rails` (`http.schema.json`) | `hyvv/rails_export.py` | `bundlebees/bundlebees_plugin/rails_remote_source.py` | `rizzstack.rails/1` frozen |
| **Orbit Rails proxy** `/api/rizzstack/rails/` | `orbitdesktop/server/rails-proxy.mjs` | orbitdesktop (`orbitdesktop/contrib/rails_server`) | Hive / Rails consoles, `orbitdesktop/src/rails-shell.ts` | follows `rizzstack.rails/1` |
| **Control-plane ingest/intents** `/api/v1/ingest/*`, `/api/v1/stream`, `/api/v1/grok` | `orbit-control-plane/API.md` | `orbit-control-plane/src/server.mjs` | `orbit-control-plane/connector/orbit-connector.mjs`, workers (scoped tokens), dashboard | none declared |
| **Collab board / Grok Bots app** `/api/collab-board`, `/api/grokbots-app` | `orbitdesktop/server/collab-board.mjs`, `orbitdesktop/server/grokbots-app.mjs` | orbitdesktop | `orbit-control-plane/connector/orbit-connector.mjs` | none declared |

## Document formats

| Id | Schema / source of truth | Producers | Consumers / checkers |
|---|---|---|---|
| `acdd/1` contract (AC ids + named tests, frozen by hash) | `workflow/schemas/acdd-contract.schema.json`, template `workflow/templates/CONTRACT.md` | coordinator / owner | `bundlebees/bundlebees_plugin/contract.py`, `workflow/tools/acdd-lint`, `orbitdesktop/server/acdd-evidence.mjs` |
| `tdd-evidence/1` (RED → GREEN → REFACTOR → integration) | `workflow/schemas/tdd-evidence.schema.json` | `bundlebees/bundlebees_plugin/tdd.py`, `workflow/reference/workflow/tdd.py` | `workflow/tools/acdd-lint` |
| Feature packet | `workflow/schemas/feature-packet.schema.json` | coordinator | `bundlebees/bundlebees_plugin/packets.py`, `workflow/tools/feature_packet.py` |
| `rizzstack.return/1` RETURN manifest | `workflow/schemas/return-manifest.schema.json` | `bundlebees/bundlebees_plugin/return_report.py` | `workflow/tools/return_manifest.py`, owner |
| `rizzstack.recovery/1` | `workflow/schemas/recovery.schema.json` | `bundlebees/bundlebees_plugin/recovery.py` | `workflow/tools/recovery.py` |
| `bee-landed/1` (a.k.a. `rizzstack.bee.landed/1`) + usage | `workflow/schemas/bee-landed.schema.json` | `hyvv/landing_export.py` | `bundlebees/bundlebees_plugin/landing_bridge.py`, `orbitdesktop/pc-launcher/scripts/usage_ingest_service.py` |
| Project state (parked/archived dispatch gate) | `workflow/schemas/project-state.schema.json` | owner (`.workflow/project-state.json` in a project) | `hyvv/contract/v1/project_state.py` (reference), `workflow/tools/project-state` |
| `rizzstack.rails/1` hash-chained activity ledger | `workflow/schemas/rails`, spec `workflow/specs/rizzstack-rails-spec.md` | hyvv recorder, bundlebees importer | bundlebees query/CLI, orbitdesktop consoles |
| `rizzstack.provisioning/1` | implemented in `hyvv/provisioning.py` | hyvv | `bundlebees/bundlebees_plugin/provisioning.py`, `bundlebees/bundlebees_plugin/provision_bind.py` |
| `rizzstack.observability/snapshot/1` (Observatory) | `workflow/observability/schemas/snapshot.schema.json` | `workflow/contrib/rizzstack-observability/collector.py` | `workflow/apps/rizzstack-observatory`, `orbitdesktop/apps/rizzstack-observatory` |
| `rizzstack.observability/1` (Hermes chat cards) | `orbit-control-plane/src/observability.mjs` (`SCHEMA`) | `orbit-control-plane/scripts/obs-snapshot.mjs` | `orbit-control-plane/hermes-plugin/rizzobs/rizzobs_plugin/snapshot.py` |
| `rizzstack.memory/1` | `workflow/schemas/memory-schema.v1.json` | `workflow/rizzstack_memory/vault.py` | coordinator briefs (trial) |
| A2UI v1.0 Basic Catalog via `a2ui_render` | `uirizz/uirizz_plugin/tools.py`, `uirizz/uirizz_plugin/catalog.py` | the model; rizzobs cards | uirizz desktop pane; fallback text elsewhere |
| Hermes plugin manifest v2 | each `plugin.yaml` (`bundlebees/plugin.yaml`, `uirizz/plugin.yaml`, `orbit-control-plane/hermes-plugin/rizzobs/plugin.yaml`, `orbitdesktop/hermes-plugin/plugin.yaml`) | — | Hermes loader; keep `provides_*` in sync with `register()` |

## Vendored code (the only shared code)

| Package | Source of truth | Copies | Refresh / check |
|---|---|---|---|
| `rizzstack_rails` (Rails runtime) | `bundlebees/contrib/rizzstack_rails` + `vendor-manifest.json` | `hyvv/contrib/rizzstack_rails` (worker subset), `orbitdesktop/contrib/rizzstack_rails` | `hyvv/scripts/vendor_rails.py --check`, `bundlebees/scripts/vendor_rails.py`, `orbitdesktop/tests/test_rails_vendor.py` |
| slop-lint (Python) | `workflow/tools/slop-lint` | `bundlebees/tools/slop-lint`, `hyvv/tools/slop-lint` | copy + rerun; JS port `orbitdesktop/scripts/slop-lint.mjs` is separate code |

At the pinned SHAs the three `contracts.py` copies of `rizzstack_rails` are byte-identical.
