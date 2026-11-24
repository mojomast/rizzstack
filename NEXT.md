# NEXT: current and next work waves

The repo copy of the wave plan, so the queue lives in a repository again instead of a chat page.
Checked against GitHub on 24 November 2026 (America/Toronto). Update this file in the same PR that
starts, finishes or re-scopes a wave.

**Why this file and not `workflow/plans/backlog.json`:** the backlog is the
`bundlebees.gate-backlog/1` queue that the bundlebees gate-watcher reads every beat. Each slice there
must carry a pre-written dispatch brief with an exact file lease, and a slice whose gates clear moves
`planned -> queued -> dispatched` without anyone looking. These waves are plans, not sealed briefs,
so adding them there could dispatch bees nobody approved. A wave item moves into `backlog.json` only
when its brief, lease and gates are written and approved.

Every merge, deploy, restart, tag, release, branch deletion and live canary still needs the owner's
own yes for that specific action.

## Done: the Nov 23 waves ("five quick wins")

All merged on 23 November. Approved failing tests were committed before each bee's patch.

| Item | What | PR |
|---|---|---|
| W1-SLOPCORE | slop-lint engine, baseline ratchet and CLI | [workflow #116](https://github.com/mojomast/workflow/pull/116) |
| W1-SLOPCI | report-only slop-lint workflow | [workflow #115](https://github.com/mojomast/workflow/pull/115) |
| W1-SLOPORB | slop-lint for JS/TS in orbitdesktop | [orbitdesktop #84](https://github.com/mojomast/orbitdesktop/pull/84) |
| W1-PRSUM | PR summary renderer | [workflow #117](https://github.com/mojomast/workflow/pull/117) |
| W1-RISKSCHEMA | risk tier derivation and optional schema field | [bundlebees #241](https://github.com/mojomast/bundlebees/pull/241) |
| W1-RISKPOL | risk tier to review policy table | [bundlebees #242](https://github.com/mojomast/bundlebees/pull/242) |
| W1-METCORE | rework metrics compute core | [bundlebees #243](https://github.com/mojomast/bundlebees/pull/243) |
| W1-METSCHEMA | optional metrics object in the evidence schema | [bundlebees #244](https://github.com/mojomast/bundlebees/pull/244) |
| W2-BASE* | committed slop-lint baselines | bundlebees #245, hyvv #34, orbitdesktop #85, workflow #121 |
| W2-METSYNC | metrics object synced into workflow's schema | [workflow #120](https://github.com/mojomast/workflow/pull/120) |
| W2-LESSONS | lessons-check (report-only, not in CI) | [workflow #122](https://github.com/mojomast/workflow/pull/122) |
| W2-RISKWIRE | advisory risk tier wiring for brief generation | [bundlebees #246](https://github.com/mojomast/bundlebees/pull/246) |
| W2-ORBDISPLAY | rework bucket in the control plane's stats | [orbit-control-plane #3](https://github.com/mojomast/orbit-control-plane/pull/3) |
| W2-PRCOMMENT | fork-safe PR summary comment | [workflow #127](https://github.com/mojomast/workflow/pull/127) |
| Owner-approved follow-ups | risk tiers enforced at dispatch; slop-lint blocking in four repos | bundlebees #247; workflow #124, hyvv #35, bundlebees #248, orbitdesktop #87 |

## Current: wave 0, guardrails on for every bee

The meta-problem: bees dispatched straight to Orbit's local-workers route never pass through
bundlebees `_dispatch`, so the risk floor, `contract_missing` and the research gate never run for
them. Wave 0 makes every dispatch pass the same gates first. Owner approvals for wave 0 were given on
24 November; each merge, deploy and restart is still its own act.

| ID | Goal | Where | Status |
|---|---|---|---|
| W0-1 | `preflight`: the same gates as `_dispatch`, no launcher call | bundlebees `preflight.py`, `tools.py`, `cli.py`, `docs/risk-tiers.md` | **open PR** [bundlebees #252](https://github.com/mojomast/bundlebees/pull/252) |
| W0-2 | risk docstrings say what is actually enforced (codemap gaps #4) | bundlebees `risk.py`, `risk_policy.py` | **open PR** [bundlebees #251](https://github.com/mojomast/bundlebees/pull/251) |
| W0-3 | Orbit flags ungated local-worker creates (warn-only by default) | orbitdesktop `server/local-workers-route.mjs` | **merged** [orbitdesktop #88](https://github.com/mojomast/orbitdesktop/pull/88) |
| W0-4 | `build.py --slice` and `--impact`, so briefs get a mechanical cut of the map | rizzstack `scripts/codemap/` | **merged** [rizzstack #6](https://github.com/mojomast/rizzstack/pull/6) |
| W0-P | pin workflow and hyvv `main` in bundlebees before the Hermes host update | bundlebees pins | **open PR** [bundlebees #253](https://github.com/mojomast/bundlebees/pull/253) |
| W0-C | the coordinator's dispatch script runs preflight before every dispatch and refuses on failure | coordinator tooling, not in a repo | after W0-1 merges |
| W0-H | Hermes host to `main` for bundlebees, Orbit, workflow and hyvv, with a backup and rollback first | deploy (owner-gated) | after W0-1 and W0-P merge |
| W0-L | worker-host launcher workspaces sync from `main`, not old feature branches | launcher config (owner-gated) | approved; not recorded in a repo |

Exit: every coordinator dispatch passes preflight; Orbit's ungated-create count stays 0 for
coordinator bees for 24 hours in warn mode; the Hermes host runs bundlebees `main`.

## Current: the Nov 24 review follow-ups

Five tracks, one PR per track, each checked against `main` before changing anything:

- **A — status docs:** README, protocol map and NEWS ([rizzstack #9](https://github.com/mojomast/rizzstack/pull/9)), workflow ROADMAP ([workflow #129](https://github.com/mojomast/workflow/pull/129)), and this file.
- **B — codemap generator:** private-name detection instead of rewriting, a pinned Python version, a wider scrubber, tracked files only, a strict weekly staleness job, uirizz routes, fpsrizz in `adjacent.md`, then a pin bump.
- **C — uirizz approval gate:** tighter bare-"yes" rules, honest docs for free-form approvals, log rotation that keeps protections, cross-process locking, persisted read-only clicks, threat-model docs, no "tamper-proof" claims.
- **D — CI hygiene:** pinned actions, gitleaks and slop-lint for uirizz; gitleaks for rizzstack; first CI for orbit-control-plane; a check command for vendored slop-lint copies.
- **E — decisions for the owner:** next milestone, first coordinated release, Rails qualification runs, branch cleanup, the legacy launcher, whether `codemap/invariants.md` stays public, and a balance rule for future waves.

## Next: wave 1, cheap gaps and CI hygiene

Seven bees in parallel, all under preflight. Several overlap Track D above; whichever lands first
closes the other.

| ID | Goal | Repo |
|---|---|---|
| W1-1 | first CI: tests, rizzobs pytest, gitleaks, report-only slop-lint | orbit-control-plane |
| W1-2 | gitleaks and slop-lint | uirizz |
| W1-3 | lessons-check as an advisory CI job | workflow |
| W1-4 | `contract/v1/STATUS.json` lists provisioning and landing export (codemap gaps #8) | hyvv |
| W1-5 | protocol map: Rails status and both observability ids | rizzstack (done by Track A, #9) |
| W1-6 | hook count (three) and schema-id note (codemap gaps #5) | orbit-control-plane docs |
| W1-7 | mark the legacy `pc-launcher/launcher.py` (codemap gaps #6) | orbitdesktop |
| W1-M | pin bump after merges; close fixed gaps | rizzstack codemap |

## Next: wave 2, memory on

| ID | Goal | Repo |
|---|---|---|
| W2-1 | read-only `python -m rizzstack_memory query` CLI | workflow |
| W2-2 | seed loader (invariants, lessons, gaps as typed records), dry-run by default | workflow |
| W2-3 | capture one memory record per verified attempt, a no-op unless `MEMORY_ENABLED=1` | bundlebees |
| W2-4 | recall into the brief's READ FIRST, behind the flag | bundlebees |
| W2-5 | `verify_workers` in shadow mode (report only, never blocks) | bundlebees |
| W2-G | a two-bee canary with memory on | Hermes host restart (owner-gated) |

## Next: wave 3, Rails release and Windows

Blocked on the owner's choices in the Rails [release decision](https://github.com/mojomast/workflow/blob/main/evidence/rails-rc/release/DECISION.md).

| ID | Goal | Repo |
|---|---|---|
| W3-1 | implement the chosen worker-assignment binding, then re-vendor | hyvv, bundlebees |
| W3-2 | a real Windows bee-ci runner instead of the stub | workflow `contrib/bee-ci/runners/windows/` |
| W3-3 | a Rails canary on one project (`rails_enabled` on), then the release decision | config only (owner-gated) |

## Next: wave 4, unused surfaces and housekeeping

| ID | Goal | Repo |
|---|---|---|
| W4-1 | the token router on a test Orbit with the current model plan as its only upstream | orbitdesktop |
| W4-2 | hyvv provisioning dry run and doc | hyvv |
| W4-3 | Connect GitHub (Orbit #27) read-only trial; rebase or close the Colab window (#18) | orbitdesktop |
| W4-4 | a refresh check for vendored slop-lint copies (overlaps Track D item 24) | hyvv, bundlebees |
| W4-5 | the Hive decision adapter in shadow mode | orbitdesktop |
| W4-H | orphan-branch and stale-PR lists (workflow #7, orbitdesktop #17, #18), list only | coordinator |

## The loop for every bee

1. The coordinator cuts the map for the lease (`build.py --slice`, `--impact`), commits approved
   failing tests, and freezes an `acdd/1` contract for high-tier work.
2. Preflight, then dispatch; watch in the Observatory or the `rizzobs` chat cards.
3. Verify: RED at the source revision, GREEN with the patch, full suite, scope, approved tests
   untouched, gitleaks, private-host grep, `build.py --verify-docs`.
4. A PR on a feature branch with green CI; merge only with the owner's yes.
5. Same day: bump the codemap pins, fix any map errata the bee reported, and update this file.

## Owner gates still open

| Gate | Decision | Blocks |
|---|---|---|
| G7 | each merge to `main` (or the standing heartbeat rule) | every item |
| G8 | restart with `MEMORY_ENABLED=1` for a two-bee canary | W2-G |
| G9 | Rails: the worker-assignment binding; Windows canary first | wave 3 |
| G10 | close workflow #7 and orbitdesktop #17, #18; delete orphan branches | W4-H |
| G11 | later: Orbit preflight blocking, lessons-check blocking, rename or document the observability id, retire the legacy launcher | follow-ups |
