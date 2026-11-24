# orbit-control-plane (L1): remote dashboard, intents, live observability

Pinned `ce37230` · https://github.com/mojomast/orbit-control-plane · generated detail: `codemap/generated/orbit-control-plane.md` · repo's own docs: `orbit-control-plane/ARCHITECTURE.md`, `orbit-control-plane/API.md`, `orbit-control-plane/SECURITY.md`

**What it is.** Two things in one repo:
1. **Control plane**: one Node 22 process (no build step) that serves a dashboard, keeps a SQLite read model fed by pushes from Orbit and workers, and holds an *intent queue* that a sidecar connector on the Orbit host applies. The dashboard never runs agents and never calls Orbit directly.
2. **Live observability for Hermes chat**: a collector that writes a redacted `rizzstack.observability/1` snapshot, and the read-only Hermes plugin `rizzobs` that turns it into an A2UI Express card for uirizz.

**Layer.** 4. Reads from Orbit (`/api/collab-board`, `/api/local-workers`, `/api/grokbots-app`) through the connector; reads GitHub read-only via `gh` in the collector.

## Where things are

| Need | Path |
|---|---|
| HTTP routes, static files, process entry | `orbit-control-plane/src/server.mjs` |
| SQLite schema + read model, intents lease/expire/close | `orbit-control-plane/src/db.mjs` |
| Auth (bearer, private-network identity header, session cookie) | `orbit-control-plane/src/auth.mjs` |
| Snapshot/event intake, worker scope checks | `orbit-control-plane/src/ingest.mjs` |
| Owner actions → intents | `orbit-control-plane/src/control.mjs` |
| Auto-approved sandbox events vs human decisions | `orbit-control-plane/src/approvals.mjs` |
| Board column mapping | `orbit-control-plane/src/columns.mjs` |
| Grok Bots proxy over the connector socket | `orbit-control-plane/src/grok.mjs`, `orbit-control-plane/src/hub.mjs`, `orbit-control-plane/src/ws.mjs` |
| Redaction | `orbit-control-plane/src/redact.mjs` |
| Stats (rates, cycle time, per-model tokens) | `orbit-control-plane/src/stats.mjs` |
| Dashboard UI (no build step, inline SVG charts) | `orbit-control-plane/public` (`app.js`) |
| Sidecar on the Orbit host (holds Orbit keys, scrubs, leases intents) | `orbit-control-plane/connector/orbit-connector.mjs` |
| Observability snapshot builder + collectors + CLI | `orbit-control-plane/src/observability.mjs` (pure builder, `SCHEMA`), `orbit-control-plane/src/obs-collect.mjs`, `orbit-control-plane/scripts/obs-snapshot.mjs` |
| rizzobs plugin (tool `rizz_observability(view)`) | `orbit-control-plane/hermes-plugin/rizzobs` (`rizzobs_plugin/snapshot.py` loads + validates, `rizzobs_plugin/cards.py` builds Express + fallback text, `rizzobs_plugin/tools.py`) |
| systemd units (generic) | `orbit-control-plane/deploy` (`orbit-control-plane.service`, `orbit-connector.service`, `obs-snapshot.service` + `.timer`) |
| Design + rollback | `orbit-control-plane/docs/OBSERVABILITY.md`, `orbit-control-plane/docs/ORBIT_HOOKS.md`, `orbit-control-plane/docs/ROLLBACK.md` |

## Data flows

- **Intents:** owner clicks in the dashboard → `control.mjs` writes an `intents` row (+ optimistic `pending` change) → connector `GET /api/v1/ingest/intents` leases a batch → applies against Orbit → `POST …/result` with `applied` / `rejected` / `deferred` (idempotent). Expired leases return to `queued`.
- **Live updates:** browser via SSE `GET /api/v1/stream`; connector via WebSocket (chat needs a round trip).
- **Observability:** collector (has credentials, no model) → redacted JSON written 0600 with atomic rename into the Hermes plugin-data dir → `rizz_observability(view)` (no network, creds, subprocess or hooks) → model calls uirizz `a2ui_render` `present`/`update` with the same `surface_id`. Cards older than 15 minutes say "Stale"; missing/invalid snapshot returns `no_snapshot` / `bad_snapshot` with a fix. Cost is `null` unless a rates file is passed; spend is never guessed.

## Tests and CI

`npm test` runs `orbit-control-plane/tests/*.test.mjs` (node:test). rizzobs: `python -m pytest tests` in the plugin dir (set `UIRIZZ_SRC` to also compile cards with uirizz). **No GitHub Actions workflows exist in this repo at the pinned SHA** (see `codemap/gaps.md`).

## Change recipes

- **Add a dashboard action:** validate + enqueue in `orbit-control-plane/src/control.mjs`, apply in `orbit-control-plane/connector/orbit-connector.mjs`, document in `orbit-control-plane/API.md`, test in `orbit-control-plane/tests/intents.test.mjs`. If Orbit has no API for it, the connector returns `deferred`.
- **Add an observability field/card:** allow-list it in `orbit-control-plane/src/observability.mjs` (pass every string through `sanitizeText`), collect it in `orbit-control-plane/src/obs-collect.mjs`, coerce it in `rizzobs_plugin/snapshot.py`, render in `rizzobs_plugin/cards.py` (stay under uirizz's 100-component cap; no buttons, images or links).
- **Change board states:** `orbit-control-plane/src/columns.mjs`, keeping parity with `orbitdesktop/server/local-workers.mjs`.

## Invariants

- Orbit keys, the launcher token and provider keys never leave the Orbit host; connector scrubs before push, control plane scrubs again before SQLite and logs.
- Workers may only update attempts Orbit already stored, and only for their own node; they never create attempts, move the board or submit reviews.
- Cards with an auto-approve signal get no decision buttons. rizzobs cards have no actions at all.
