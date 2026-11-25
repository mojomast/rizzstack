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
| W0-1 | `preflight`: the same gates as `_dispatch`, no launcher call | bundlebees `preflight.py`, `tools.py`, `cli.py`, `docs/risk-tiers.md` | **merged** [bundlebees #252](https://github.com/mojomast/bundlebees/pull/252) |
| W0-2 | risk docstrings say what is actually enforced (codemap gaps #4) | bundlebees `risk.py`, `risk_policy.py` | **merged** [bundlebees #251](https://github.com/mojomast/bundlebees/pull/251) |
| W0-3 | Orbit flags ungated local-worker creates (warn-only by default) | orbitdesktop `server/local-workers-route.mjs` | **merged** [orbitdesktop #88](https://github.com/mojomast/orbitdesktop/pull/88) |
| W0-4 | `build.py --slice` and `--impact`, so briefs get a mechanical cut of the map | rizzstack `scripts/codemap/` | **merged** [rizzstack #6](https://github.com/mojomast/rizzstack/pull/6) |
| W0-P | pin workflow and hyvv `main` in bundlebees before the Hermes host update | bundlebees pins | **merged** [bundlebees #253](https://github.com/mojomast/bundlebees/pull/253) |
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

## Next milestone

**Decided 2026-11-24 (owner): option B.** Decision #25 splits the next milestone in two.

- **M-A — a stranger can run Orbit locally:** F11 (`workflow/plans/packets/F11-runnable-by-anyone.md`), then F12 (`workflow/plans/packets/F12-connect-github-repo-picker.md`), which is Orbit #27 ([orbitdesktop #27](https://github.com/mojomast/orbitdesktop/pull/27), now retargeted to `main`).
- **M-B — a stranger can lend a worker:** F15 (`workflow/plans/packets/F15-relay.md`) -> F18 (`workflow/plans/packets/F18-revocable-tokens.md`) -> F16/F17 (`workflow/plans/packets/F16-tailcat.md`, `workflow/plans/packets/F17-join-probing.md`) -> F19 (`workflow/plans/packets/F19-live-host-join-install.md`).
- **Separate later owner yeses:** the F19 throwaway rehearsal, the ghcr image publish and any live-host install. None of them is granted by this decision.

Option A (one big milestone) and option C (M-A only) were not chosen.

## Next: the next-level review, waves A-F

Source: the [Nov 24 next-level review](https://github.com/mojomast/rizzstack/pull/17)
(`docs/reviews/2026-11-24-next-level-review.md`). Every finding was re-checked against `main` on
24 November before it became a slice; dropped and rescoped findings are listed in
[`plans/wave-a/README.md`](plans/wave-a/README.md). Waves run in this order. Each wave closes on a
check you can run.

| Wave | Goal | Maps onto the existing plan | Plan |
|---|---|---|---|
| A | stop the leaks: Fix-now rows 1-13 and the quick token wins | new; runs before waves 1-4 | 21 slices with RED tests, leases, tiers and preflight: [`plans/wave-a/`](plans/wave-a/README.md). **Approved by the owner (Kyle t1650u, t1651u): every lane runs in parallel** on the Hermes kanban, lane successors after their predecessor merges; recorded in `backlog.json` ([workflow #147](https://github.com/mojomast/workflow/pull/147)). |
| B | one worker contract: `rizzstack.brief/1`, `rizzstack.attempt/1` | new; finishes the bee-landed id fix started by A12a and HY-3 | [workflow #141](https://github.com/mojomast/workflow/pull/141) |
| C | gates at the boundary: a signed dispatch ticket | **W3-1, generalized**: the Rails worker-assignment binding becomes one case of the ticket | [hyvv #42](https://github.com/mojomast/hyvv/pull/42) |
| D | one core, one log: `rizzstack-core`, Rails as the only log, one control loop, unwired code out | **absorbs W4-4** (vendored-copy refresh becomes the core vendoring check) **and W1-7** (the legacy `pc-launcher` is deleted, not marked) | [workflow #142](https://github.com/mojomast/workflow/pull/142) |
| E | adapters: harness (ACP), model config, OCI sandbox, forge, `bundlebees mcp` | new; W4-1 (token router) feeds its model adapter | [bundlebees #262](https://github.com/mojomast/bundlebees/pull/262) |
| F | measure and tune: an A/B harness scored by tokens per verified patch | new; **gates W2-G**: memory goes on only after an A/B shows its effect | [workflow #143](https://github.com/mojomast/workflow/pull/143) |

Wave A exit: each fix lands with RED then GREEN, and the factory reports tokens per verified patch
for the week (slice M1).

## Waves B and C (in progress)

Queue state is the run-scoped `waves-bc-queue.json` (`rizzstack.waves-bc-queue/1`, generated
2026-11-25T04:10:00Z, base `71cf42c3` on `workflow`), with draft contracts under
`contracts/wave-bc/` there. Neither is in this repo. The two rows above are expanded here into
dispatchable slices.

The overnight B2 and C1 bees succeeded, but their write-back was rejected on a glob lease, so no
commit and no PR exists for either.

| Slice | Repo | Status | Depends on | Recorded blocker |
|---|---|---|---|---|
| B1 | hyvv | ready | - | yaml-parser dependency decision: hyvv is stdlib-only and has no policy for adding one |
| B2 | workflow | ready | - | RED `9bd22b4` on `red/wave-b-b2`; the GREEN (examples, checker, CI job, `x-status` removal) is not started |
| B3 | bundlebees | blocked | B2 | numbering conflict: the owner table and `bundlebees/docs/design/wave-b-worker-contract.md` disagree |
| B4 | hyvv | blocked | B3 | waits on B3's report-only validation |
| B5 | orbitdesktop, orbit-control-plane | blocked | B3 | waits on B3's report-only drift feed |
| B6 | hyvv, bundlebees | blocked | B4, B5 | owner enforcement date not set (two weeks of zero drift reports first) |
| B7 | workflow | blocked | B4, B5 | numbering conflict: the owner table and the design doc disagree |
| C1 | workflow | ready | - | RED `a43f8de` on `red/wave-c-c1`; the GREEN (schema, example, docs-parity row) is not started |
| C2 | hyvv | blocked | B1, C1, B5 | waits on the parsable `/v1` contract, the ticket schema and Orbit's `brief/1` |
| C3 | bundlebees | blocked | C1, B4 | crypto dependency + wave D JCS + key location; the queue records it skipped |
| C4 | hyvv | blocked | C2, C3 | crypto dependency + wave D JCS + key location; the queue records it skipped |
| C5 | hyvv, orbitdesktop, orbit-control-plane, workflow | blocked | C4 | crypto dependency + wave D JCS + key location; the queue records it skipped |

Dispatch order: B1 and B2 in parallel; B3 after B2; B4 and B5 after B3; B6 and B7 after B4 and B5;
C1 alongside B1 and B2; C2 after B1, C1 and B5; C3 after C1 and B4; C4 after C2 and C3; C5 last.
The queue file records narrower edges for B7 (B2), C2 (B1), C3/C4 (C1, C2) and C5 (C3, C4); the
order above is the dispatch plan and the file is the queue's own record.

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
| W1-7 | ~~mark the legacy `pc-launcher/launcher.py` (codemap gaps #6)~~ absorbed by wave D: delete it (owner decision) | orbitdesktop |
| W1-M | pin bump after merges; close fixed gaps | rizzstack codemap |

## Next: wave 2, memory on

| ID | Goal | Repo |
|---|---|---|
| W2-1 | read-only `python -m rizzstack_memory query` CLI | workflow |
| W2-2 | seed loader (invariants, lessons, gaps as typed records), dry-run by default | workflow |
| W2-3 | capture one memory record per verified attempt, a no-op unless `MEMORY_ENABLED=1` | bundlebees |
| W2-4 | recall into the brief's READ FIRST, behind the flag | bundlebees |
| W2-5 | `verify_workers` in shadow mode (report only, never blocks) | bundlebees |
| W2-G | a two-bee canary with memory on: **moved behind wave F**, so it runs only after the A/B harness measures memory's effect | Hermes host restart (owner-gated) |

## Next: wave 3, Rails release and Windows

Blocked on the owner's choices in the Rails [release decision](https://github.com/mojomast/workflow/blob/main/evidence/rails-rc/release/DECISION.md).

| ID | Goal | Repo |
|---|---|---|
| W3-1 | implement the chosen worker-assignment binding, then re-vendor. Wave C generalizes this into the signed dispatch ticket. | hyvv, bundlebees |
| W3-2 | a real Windows bee-ci runner instead of the stub | workflow `contrib/bee-ci/runners/windows/` |
| W3-3 | a Rails canary on one project (`rails_enabled` on), then the release decision | config only (owner-gated) |

## Next: wave 4, unused surfaces and housekeeping

| ID | Goal | Repo |
|---|---|---|
| W4-1 | the token router on a test Orbit with the current model plan as its only upstream | orbitdesktop |
| W4-2 | hyvv provisioning dry run and doc | hyvv |
| W4-3 | Connect GitHub (Orbit #27) read-only trial; rebase or close the Colab window (#18) | orbitdesktop |
| W4-4 | ~~a refresh check for vendored slop-lint copies~~ absorbed by wave D's core vendoring check | hyvv, bundlebees |
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
| G8 | restart with `MEMORY_ENABLED=1` for a two-bee canary: **paused** until wave F's experiment F1 reports (Kyle t1650u) | W2-G |
| G9 | Rails: the worker-assignment binding; Windows canary first | wave 3 |
| G10 | close workflow #7 and orbitdesktop #17, #18; delete orphan branches | W4-H |
| G11 | later: Orbit preflight blocking, lessons-check blocking, rename or document the observability id, retire the legacy launcher | follow-ups |
| G12 | wave A slices approved (Kyle t1650u, t1651u). `CONTRACT_GATE_MODE`: plan agreed, not flipped: enforce at medium tier one week after A1a, A1b and A11 merge | wave A |
