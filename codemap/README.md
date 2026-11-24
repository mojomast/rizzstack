# rizzstack code map (L0: start here)

One screen for the whole stack. Go deeper only as far as the task needs:

| Layer | File | Read it when | Size |
|---|---|---|---|
| L0 | this file | always, first | ~1k tokens |
| L1 | `codemap/L1/<repo>.md` | you will touch that repo | ~1–1.6k tokens each |
| cross-cutting | `codemap/contracts.md`, `codemap/change-recipes.md`, `codemap/invariants.md`, `codemap/architecture.md`, `codemap/ci-gates.md` | a change crosses repos, or you need a rule | ~0.6–1.5k each |
| L2 (generated) | `codemap/generated/<repo>.md` | you need "where is symbol X" | ≤6k tokens each |
| L2 data | `codemap/generated/<repo>.json`, `codemap/generated/index.json` | programmatic lookup | large; query, don't read |

Hand-written files explain *why* and *where to change*. Generated files (header says GENERATED) list *what exists*; never edit them, rerun `python3 scripts/codemap/build.py`.

## The stack in four layers

```
1 workflow             method + protocol: ACDD contracts, evidence, schemas, lints, reference demo
        │ schemas + rules every layer obeys
2 hyvv                 worker host: sandboxed bees on a PC/SSH host, versioned /v1 HTTP API
        ▲ /v1 attempts, artifacts, Rails events
3 bundlebees           coordinator (Hermes plugin): briefs, leases, risk tiers, dispatch, verify, integrate
        ▲ local-workers adapter, approvals, Rails ledger
4 orbitdesktop         operator workspace: Hermes sessions, terminals, Observatory, Hive, collab board
  orbit-control-plane  remote dashboard + intents; rizzobs live-observability plugin + collector
  uirizz               Hermes plugin: A2UI cards in chat (a2ui_render), typed-yes approval gate
```

A coordinator can speak hyvv `/v1` without Orbit; a team can adopt workflow contracts without any workers. Contracts are the seams, not code imports: the only shared code is vendored copies (see `codemap/contracts.md`).

## Repos at the pinned SHAs

| Repo | SHA | Main language | Start in |
|---|---|---|---|
| workflow | `c8ef4c8` | Python, JSON schemas, Markdown | `codemap/L1/workflow.md` |
| hyvv | `9691837` | Python (+PowerShell installers) | `codemap/L1/hyvv.md` |
| bundlebees | `cb8946e` | Python (Hermes plugin) | `codemap/L1/bundlebees.md` |
| orbitdesktop | `d33157e` | Node .mjs server, TypeScript UI | `codemap/L1/orbitdesktop.md` |
| orbit-control-plane | `ce37230` | Node .mjs, Python plugin | `codemap/L1/orbit-control-plane.md` |
| uirizz | `98b795a` (v0.1.0) | Python plugin, React desktop | `codemap/L1/uirizz.md` |

Pins live in `scripts/codemap/sources.json`. `python3 scripts/codemap/build.py --upstream` says which pins are behind `main`.

## The one flow to know

Owner ask → ACDD contract frozen by hash (workflow) → brief with `assigned_files`, `source_rev`, risk tier → bundlebees `bundlebees_dispatch` (refuses `risk_tier_below_floor`, `contract_missing`) → hyvv `POST /v1/attempts` runs a bee in a sandbox → result + `bee-landed/1` event → bundlebees verify in bwrap (RED/GREEN, scope, test fingerprints, gitleaks) → integrate on a feature branch → PR with CI (slop-lint, gitleaks) → merge (heartbeat may auto-merge when green + independently reviewed) → **owner's own typed yes** for tag, release, deploy. Orbit, the control plane and uirizz only *show* and *queue intents*; they never approve on a click.

## Rules that bite first

- A click is never approval; only the owner's typed yes approves. Relayed approval doesn't count.
- Fail closed. Never fix a failing test by weakening a sandbox or faking evidence.
- No secrets, tokens, private hosts, IPs or private URLs in any repo or map.
- Tags, releases, deploys, catalog PRs, posts: explicit owner yes each time.
- Full list and owners: `codemap/invariants.md`.

## Not covered at code level

slop-factory (pitch site) and the hackathon Hermes plugins are indexed only in `codemap/adjacent.md`. Known drift and open questions: `codemap/gaps.md`. Why the map looks like this: `codemap/decisions/0001-codemap-format.md`.
