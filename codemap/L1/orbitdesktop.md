# orbitdesktop (L1): Orbit, the operator workspace

Pinned `d33157e` (package 0.3.1, workspace schema 12) · https://github.com/mojomast/orbitdesktop · generated detail: `codemap/generated/orbitdesktop.md` · repo's own agent guide: `orbitdesktop/AGENTS.md`

**What it is.** A single-owner, agent-customizable browser desktop with Hermes as the runtime: panes, terminals (tmux-backed), agent conversations, a collab board, local workers, and rizzstack consoles (Observatory, Hive, Rails, approvals). Trusted core/integration code is separate from sandboxed generated widget plugins.

**Layer.** 4 (control surface). It runs the loopback adapter that bundlebees can call (`/api/local-workers`), proxies the Rails ledger, and hosts the Observatory. It is not required to run hyvv or bundlebees.

## Where things are

| Need | Path |
|---|---|
| Server entry (all HTTP routes) | `orbitdesktop/server/index.mjs` |
| Workspace service, auth, SQLite store | `orbitdesktop/server/workspace.mjs`, contract `orbitdesktop/contracts/workspace-v1.json` |
| Frontend entry + layout model | `orbitdesktop/src/main.ts`, `orbitdesktop/src/model.ts`, ops `orbitdesktop/src/workspace-ops.ts`, plugins `orbitdesktop/src/plugins.ts`, sync `orbitdesktop/src/workspace-sync.ts` |
| Local workers adapter (to hyvv / PC launcher) | `orbitdesktop/server/local-workers.mjs`, `orbitdesktop/server/local-workers-route.mjs` |
| Legacy PC launcher copy + usage ingest | `orbitdesktop/pc-launcher` (hyvv is the maintained host) |
| Collab board, Grok Bots app bridge | `orbitdesktop/server/collab-board.mjs`, `orbitdesktop/server/grokbots-app.mjs` |
| Rails ledger proxy + vendored runtime + HTTP server | `orbitdesktop/server/rails-proxy.mjs`, `orbitdesktop/contrib/rizzstack_rails`, `orbitdesktop/contrib/rails_server` |
| rizzstack consoles | `orbitdesktop/apps/rizzstack-hive`, `orbitdesktop/apps/rizzstack-observatory`, `orbitdesktop/apps/rizzstack-rails`, `orbitdesktop/apps/rizzstack-approvals`; trusted extensions in `orbitdesktop/extensions` |
| Managed terminals | `orbitdesktop/server/managed-terminal-provider.mjs`, `orbitdesktop/server/managed-terminal-routes.mjs` |
| Model router (Bifrost / LiteLLM, opt-in) | `orbitdesktop/server/router-supervisor.mjs`, `orbitdesktop/server/router-litellm-policy.mjs`, `orbitdesktop/compose.router.yml` |
| Onboarding wizard | `orbitdesktop/server/onboarding.mjs`, `orbitdesktop/server/onboarding-github.mjs` |
| Hermes plugin (Orbit tools for Hermes) + bundled source archive | `orbitdesktop/hermes-plugin` (`orbit.py`, `plugin.yaml`, `orbit-source.tar.gz` rebuilt by `orbitdesktop/scripts/bundle_hermes.py`) |
| Agent / plugin / architecture docs | `orbitdesktop/docs/AGENT_GUIDE.md` (injected into workspace-aware Hermes requests), `orbitdesktop/docs/PLUGINS.md`, `orbitdesktop/docs/ARCHITECTURE.md`, `orbitdesktop/docs/DEPLOYMENT.md`, `orbitdesktop/docs/HIVE_OPERATIONS.md`, `orbitdesktop/docs/QUICKSTART.md` |
| Plugin catalog | `orbitdesktop/plugin-catalog` |

## Entry points

`npm run check` and `npm test` (both via `orbitdesktop/scripts/check-local.mjs`), `node server/index.mjs` behind `orbitdesktop/Dockerfile` / `orbitdesktop/docker-compose.yml`. Smoke checks `orbitdesktop/verify-deploy.mjs`, `orbitdesktop/verify-agent-live.mjs`.

## CI gates

`test.yml` (offline regression gate `orbitdesktop/scripts/ci-offline-check.sh`, Rails vendor + HTTP contracts, Hermes plugin tests, widget publisher parity, `browser` job), `fresh-container-smoke.yml`, `orbit-catalog.yml`, `technology.yml`, `asteria.yml`, and blocking `slop-lint.yml` (JS port `orbitdesktop/scripts/slop-lint.mjs` over src, server, scripts, packages). Expensive browser CI is weekly/manual.

## Change recipes

- **Add a server API route:** route in `orbitdesktop/server/index.mjs` (or a `*-route.mjs` module it mounts), owner vs capability auth via `orbitdesktop/server/workspace.mjs`, tests in `orbitdesktop/tests`. Owner-only feature APIs are never workspace-controller or generated-plugin capabilities.
- **Add a workspace operation:** `orbitdesktop/contracts/workspace-v1.json` + `orbitdesktop/src/workspace-ops.ts` + server validation; keep `orbitdesktop/docs/AGENT_GUIDE.md` aligned.
- **Add a widget:** content-addressed plugin publication (see `orbitdesktop/docs/PLUGINS.md`); don't overwrite bundles that checkpoints reference.
- **Change local-worker states/columns:** `orbitdesktop/server/local-workers.mjs` (`TERMINAL`, column map); the control plane mirrors these states (`orbit-control-plane/src/columns.mjs`).
- **Change Hermes-side source:** rerun `orbitdesktop/scripts/bundle_hermes.py` and verify archive parity.

## Invariants

- Preserve uncommitted owner changes; never stage everything blindly; keep `.runtime`, transcripts, credentials and personal screenshots out of Git.
- Explicit draft insertion never sends a message. Opening an owner question never approves it.
- Don't restart a server with live terminals without session preservation. Live deploy/restart needs the owner's yes.
