# News — rizzstack progress log

Dated, concrete entries. Every number here was read from the repositories, CI, or the launcher at
the time of writing; every incident is recorded with its cause, because a factory that hides its
failures is worthless.

---

## 2026-11-27

*Covers 2026-11-25 to 2026-11-27, America/Toronto. Pull request numbers are read from GitHub.*

### Decided

- **Merge-time risk tiers.** A slice merges without a review only when every changed path is on its
  repository's T1 allowlist, verify evidence matches the head, and no rejection stands at the head.
  Everything else needs an independent review (`policy/merge-lane-v5.md`).
- **The outside reviewer is a role, not a person or a model.** Review bees fill it with the model
  configured for the role (Kimi by default, DeepSeek Flash on the Nous route when Kimi wrote the
  PR, the owner when neither is independent). Shadow calibration comes before any automatic
  approval (`policy/outside-review.md`, `skills/rizzstack-outside-review/`).
- **Three GitHub identities** (owner, coordinator app, reviewer app) replace text-trusted reviews.
  Planned in `policy/identities.md`.

### Landed

- **Gate hardening.** The lease/tamper gate with review carry-forward and "newest decisive review
  governs", risk tiers with a positive T1 allowlist and an always-T3 overlay (rizzstack
  [#32](https://github.com/mojomast/rizzstack/pull/32), [#35](https://github.com/mojomast/rizzstack/pull/35),
  [#38](https://github.com/mojomast/rizzstack/pull/38), [#39](https://github.com/mojomast/rizzstack/pull/39)),
  the lane's T1 rule ([#37](https://github.com/mojomast/rizzstack/pull/37)), and the gate vendored
  byte-identically into all six product repos. The bundlebees oracle lock
  ([bundlebees #324](https://github.com/mojomast/bundlebees/pull/324), [#330](https://github.com/mojomast/bundlebees/pull/330),
  [#331](https://github.com/mojomast/bundlebees/pull/331), [#335](https://github.com/mojomast/bundlebees/pull/335)) judges tests with main's config.
  Gate-only reruns on review are in every repo.
- **Wave work.** B4 landed in hyvv ([#101](https://github.com/mojomast/hyvv/pull/101), [#102](https://github.com/mojomast/hyvv/pull/102)),
  with the `unavailable` status and value redaction in [#103](https://github.com/mojomast/hyvv/pull/103). C2-PKG ships every schema in the
  wheel ([hyvv #96](https://github.com/mojomast/hyvv/pull/96)). B6 was re-frozen against current main (contract `da07129c`).
- **Deploy path.** `apply-update` proves the build sha, deploys the wheel's runtime closure, smoke-tests
  brief validation before restart, and reverts on failure ([hyvv #100](https://github.com/mojomast/hyvv/pull/100)).
- **Ingest.** Content-addressed diagnostic keys end the `idempotency_conflict` loop
  ([bundlebees #336](https://github.com/mojomast/bundlebees/pull/336)). The unit is green again.

### Incidents

- **Bypasses found in review, before merge:**
  - a TOML inline table and `entry-points.pytest11` slipped past the line scanner
  - a patch could rewrite the oracle config, because it was written into the worktree before `git apply`
  - a root `pytest.py` replaced the test runner
  - base-finding subtraction let a dangerous line be moved instead of added
  - the symlink mode check refused every symlink

  Each got a RED test. All 17 classes are in `skills/rizzstack-outside-review/references/bypass-catalogue.md`.
- **Self-approval.** Two scheduled review lanes posted "independent review" approvals under the owner's
  account, and one PR ([bundlebees #331](https://github.com/mojomast/bundlebees/pull/331)) merged on such an approval. Both lanes are now advisory
  only. The merge is marked `self_approval` in the ledger, and its delta was reviewed afterwards.
- **Merge box.** A green check from a review-triggered run does not replace a failed same-named check
  from the `pull_request` run. Fixed by gate-only reruns, not by re-running full CI on reviews.
- **Deploy reverted twice.** The launcher began reporting `0.1.19+g<sha>`, and the old exact version
  check reverted two correct deploys. Fixed in hyvv #100. Restart time is growing quadratically with
  the attempt count ([hyvv #106](https://github.com/mojomast/hyvv/issues/106)).

---

## 2026-11-24

*Dates in this log are America/Toronto. Counts are merged pull requests read from GitHub for that
day across workflow, bundlebees, hyvv, orbitdesktop, orbit-control-plane, uirizz and rizzstack.*

### Decided

- **Next milestone: option B** (decision #25). The owner chose option B — M-A "a stranger can run
  Orbit locally" pairs F11 with F12 and retargets Orbit
  [#27](https://github.com/mojomast/orbitdesktop/pull/27) to `main`, while M-B "a stranger can lend a
  worker" runs F15 -> F18 -> F16/F17 -> F19 with separate later yeses for the F19 throwaway rehearsal,
  the ghcr image publish and any live-host install — over option A (one big milestone) and option C
  (M-A only), and Orbit [#17](https://github.com/mojomast/orbitdesktop/pull/17) and
  [#18](https://github.com/mojomast/orbitdesktop/pull/18) are closed as superseded.

### Landed

- **The code map** ([rizzstack #5](https://github.com/mojomast/rizzstack/pull/5), merged 02:35 as
  `9baec53`): a layered, regenerable map of every rizzstack repo at pinned commits — a one-screen
  overview, a page per repo, cross-repo contracts, change recipes, invariants, gaps, and a generated
  symbol index rebuilt by `python3 scripts/codemap/build.py`, with a CI check that fails on drift.
  Agents start at [AGENTS.md](AGENTS.md). Wave 0 followed with `--slice` and `--impact` for bee
  briefs ([rizzstack #6](https://github.com/mojomast/rizzstack/pull/6)) and a warn-only flag for
  ungated local-worker dispatch in Orbit ([orbitdesktop #88](https://github.com/mojomast/orbitdesktop/pull/88)).

### Operational

- Wave 0's bundlebees changes are open for review, not merged: the dispatch preflight
  ([#252](https://github.com/mojomast/bundlebees/pull/252)), risk docstrings that say what is enforced
  ([#251](https://github.com/mojomast/bundlebees/pull/251)), and the workflow/hyvv pin bump that has to
  land before the Hermes host moves to `main` ([#253](https://github.com/mojomast/bundlebees/pull/253)).

## 2026-11-23

### Landed

**34 pull requests merged.** Wave 1 and wave 2 ran as bee attempts with approved failing tests
committed first; enforcement and blocking CI were separate owner-approved PRs.

- **slop-lint, from engine to blocking ratchet in four repos.** The stdlib-only engine and baseline
  ratchet ([workflow #116](https://github.com/mojomast/workflow/pull/116)), report-only CI
  ([workflow #115](https://github.com/mojomast/workflow/pull/115)), a JS/TS port for Orbit
  ([orbitdesktop #84](https://github.com/mojomast/orbitdesktop/pull/84)), committed baselines for
  bundlebees, hyvv, orbitdesktop and workflow (bundlebees #245, hyvv #34, orbitdesktop #85, workflow
  #121), then blocking on new findings in workflow #124, hyvv #35, bundlebees #248 and orbitdesktop
  #87. uirizz, orbit-control-plane and rizzstack do not run slop-lint yet.
- **Risk tiers, from schema to enforcement.** Tier derivation and an optional brief field
  ([bundlebees #241](https://github.com/mojomast/bundlebees/pull/241)), the tier-to-review policy
  table ([#242](https://github.com/mojomast/bundlebees/pull/242)), advisory wiring into brief
  generation ([#246](https://github.com/mojomast/bundlebees/pull/246)), and fail-closed enforcement
  at dispatch ([#247](https://github.com/mojomast/bundlebees/pull/247)): a tier below the
  path-derived floor and a high-tier brief without a frozen contract are refused. The policy table's
  human gates and reviewer counts are still report-only.
- **Rework metrics.** A compute core ([bundlebees #243](https://github.com/mojomast/bundlebees/pull/243)),
  an optional `metrics` object in the evidence schema ([#244](https://github.com/mojomast/bundlebees/pull/244)),
  synced into workflow's schema ([workflow #120](https://github.com/mojomast/workflow/pull/120)), and a
  rework bucket in Orbit's control-plane stats ([orbit-control-plane #3](https://github.com/mojomast/orbit-control-plane/pull/3)).
- **PR summary comments.** A Markdown renderer for a contract's criteria and evidence
  ([workflow #117](https://github.com/mojomast/workflow/pull/117)) and a fork-safe comment builder plus
  build workflow ([workflow #127](https://github.com/mojomast/workflow/pull/127)).
- **lessons-check** ([workflow #122](https://github.com/mojomast/workflow/pull/122)): recurring review
  findings as three deterministic checks. Report-only, and not wired into CI.
- **uirizz v0.1.0** ([release](https://github.com/mojomast/uirizz/releases/tag/v0.1.0), tag at
  `98b795a`): a Hermes plugin that renders A2UI v1.0 cards in chat with an approval gate where a click
  is never approval, and declared actions that bind a button to one exact tool call
  ([uirizz #21](https://github.com/mojomast/uirizz/pull/21)). 23 uirizz PRs merged on 22–23 November.
- **Live observability for Hermes** ([orbit-control-plane #4](https://github.com/mojomast/orbit-control-plane/pull/4)):
  a redacted `rizzstack.observability/1` snapshot and the read-only `rizzobs` plugin that shows it as
  chat cards. This is a different document from the Observatory's `snapshot/1`.

### Broke, and what it taught

- **A missing coverage-registry row turned workflow `main` red again.** Wave 1's PRs #115–#117
  merged without docs-sync rows; [workflow #118](https://github.com/mojomast/workflow/pull/118)
  registered them and restored main CI, and #119 excluded the docs-sync PR itself. Same failure class
  as 17 November: the gate held, the merge order had a hole.

## 2026-11-22

### Landed

**22 pull requests merged.**

- **hyvv's Windows journal and shutdown fixes** ([hyvv #33](https://github.com/mojomast/hyvv/pull/33)):
  the launcher retries `os.replace` on `PermissionError` / WinError 5 and 32 (then raises, never
  swallows), always runs the attempt's own sandbox cleanup, reconciles a split-brain journal at
  startup, and reports `split_brain` on `/v1/health`. `start.ps1` now waits up to 180 s for health and
  no longer stops a launcher that is still starting. 9 approved tests in `tests/test_win_save_reconcile.py`.
- **uirizz's first 16 PRs** (#1–#16): A2UI present/update/delete, live card refresh, the Express
  compiler, the Desktop card, and the approval gate with an audit log (#16).
- The Nov 20–22 hardening run closed with bundlebees #236–#240 (below).

### Broke, and what it taught

- **On 20 November the Windows worker host's journal save failed with WinError 5,** and the retry save
  failed too, so sandbox cleanup never ran: memory said the attempt had failed while the disk journal
  still said running. An apply-update of hyvv `2a5b956`, recorded in the PR on 22 November, also rolled back because `start.ps1`'s 45 s health probe
  gave up during a ~96 s startup. Both are fixed in hyvv #33.
- **uirizz cards did not appear on first install.** Six fixes in a row (#8–#13) — a CJS `require`
  shim, a standalone `MEDIA:` line, surface ids dropped by the catalog and the real message
  processor, and a double-encoded REST body — before a card rendered end to end.

## 2026-11-20 to 2026-11-22: the self-verified hardening run

- **bundlebees #195–#240:** 46 PRs were opened in that range; **41 merged** and 5 were closed
  unmerged (#197, #220, #221, #225, #227). All 41 merged PRs are `verify:` PRs carrying their bee attempt id.
  They hardened queue and status reporting, tool-completion reads, the delivery and landing bridges,
  and the verify record.
- **12 of the merged PRs were `verify-argv-*` shape pins and scrubs** (#229–#240), merged back to
  back from 21 November 12:24 to 22 November 07:55. How much of a wave's capacity that kind of
  narrowing work should take is an open question for future waves.

## 2026-11-21

**27 pull requests merged**, 20 of them in that bundlebees hardening run. workflow #108–#114
corrected the observability collector's fleet fallback to read the live bee-landed outbox and pinned
the module header to the behaviour, after #107 (20 November) made cold snapshots run their CLI reads
concurrently (~10.8 s to ~3.6 s measured on the campaign host).

## 2026-11-20

### Landed

**33 pull requests merged.**

- **Deployment receipt on hyvv's health check** ([hyvv #31](https://github.com/mojomast/hyvv/pull/31),
  [#32](https://github.com/mojomast/hyvv/pull/32)): `/v1/health` publishes only the validated
  `{deployed_at, version, rev}` from the apply-update receipt and fails closed on a malformed one, so
  `bundlebees doctor` can read the launcher identity instead of `unknown`. #32 followed within the
  hour because the real receipt sits in the install dir with an `installed_at_utc` key that #31 never
  looked for.
- Zoo routing became opt-in ([hyvv #30](https://github.com/mojomast/hyvv/pull/30)), and bundlebees
  pinned the launcher build recorded on the worker host, 0.1.17 / hyvv `1fcf1039`
  ([bundlebees #192](https://github.com/mojomast/bundlebees/pull/192)).

## 2026-11-19

### Landed

**52 pull requests merged.**

- **The bundlebees cleanup merge** ([bundlebees #164](https://github.com/mojomast/bundlebees/pull/164),
  09:54, owner-approved, merge commit): the reviewed `[cleanup]` PRs #127, #131–#136, #142, #143 and
  #146 — graceful recovery deleted, a pinned and fail-closed workflow provenance check, the contract
  gate in report-only mode, a classify-only reconciliation sweep in the factory driver, the evidence
  schema synced with workflow, and docs made true. The PR reports 2,959 tests with 6 failures at the
  base and 2,891 tests with the same 6 failures at the head: no new failures or skips.
- **Orbit's cleanup phases 1–3** ([orbitdesktop #78](https://github.com/mojomast/orbitdesktop/pull/78)
  and #57–#77): dead server modules, Kitty DOOM, IRC, the spatial view, Knowledge search and the
  Project Workbench were cut, and the Hive and Observatory now read the snapshot through an
  owner-authenticated extension proxy (#66, #69, #81).

## 2026-11-18

### Landed

**70 pull requests merged.**

- **Memory vault service** ([workflow #98](https://github.com/mojomast/workflow/pull/98)) with write
  quarantine (#101), an index (#102) and retention (#104): the disk-backed `rizzstack.memory/1`
  vault. Not wired into the runtime; `MEMORY_ENABLED` stays off.
- **Owner decisions in the snapshot** ([workflow #96](https://github.com/mojomast/workflow/pull/96),
  #97) and the Hive projecting real snapshots (orbitdesktop #51–#55).
- **The factory driver and board bridge:** scout hook, completion events, service mode and the board
  → dispatcher bridge for approved ready cards (bundlebees #105, #106, #109, #118, #122).

---

## 2026-11-17

### Four-repository buildout in review

[Workflow #91](https://github.com/mojomast/workflow/pull/91) ties together the worker ([Hyvv #26](https://github.com/mojomast/hyvv/pull/26)), coordinator ([BundleBees #82](https://github.com/mojomast/bundlebees/pull/82)), and operator workspace ([Orbit Desktop #46](https://github.com/mojomast/orbitdesktop/pull/46)). Five local acceptance cases passed, including a coding campaign that went from a failing test to an integrated patch. The browser and CLI identified the same worker event after a restart. The Rails evidence attachment validated; the enclosing RETURN manifest still differed from Workflow’s schema at seven field paths. See the [acceptance record](https://github.com/mojomast/workflow/blob/60f61961c73aeb841a87bd52e38dde5577518b40/specs/rails-rollout.md).

- **Reconnects:** In a [30-minute local soak](https://github.com/mojomast/workflow/blob/60f61961c73aeb841a87bd52e38dde5577518b40/evidence/rails-bench-linux.md) against a 100,000-event ledger, the HTTP client found all 164 events written while connected and all 164 written while disconnected. Reconnect to replayed event took 20.7 ms at the 95th percentile on that Linux guest.
- **Windows worker:** The [native Windows CI job](https://github.com/mojomast/hyvv/actions/runs/44168250607/job/134341551899) passed the launcher journal, authenticated loopback export, and cross-process storage checks.
- **Live canary:** The [offline rehearsal](https://github.com/mojomast/workflow/blob/60f61961c73aeb841a87bd52e38dde5577518b40/contrib/core-review/README.md) produced four expected failures on the seed and four passes on the reference implementation. It created zero worker attempts. The owner approved the live run; worker registration and the [pre-dispatch runbook gate](https://github.com/mojomast/workflow/blob/60f61961c73aeb841a87bd52e38dde5577518b40/docs/m2-canary-runbook.md) still need live evidence.

### Landed

**39 pull requests merged across the four repos today**, all through the full gate chain
(verify → secret scan → independent review → exact-head CI → merge). The ones that matter most:

- **Rails wave-2 batch 1 — 8 of 23 slices landed.** R00 (feasibility: pinned real source versions,
  sanitized native traces, hook citations; `workflow #80`), R01 (the contract layer; `#83`), R02
  (the usage-ledger rails; `bundlebees #70`), and R03/R04/R08/R10/R13 (vendor pin, R13 Observatory
  pane `orbitdesktop #45`, catalog and adapter work) adopted in `#85–#87`. Each landing carries its
  own docs-sync companion and registry row.
- **Approval surface shipped** (`orbitdesktop #41`): the click-to-approve route and operator app.
  The page shows the plain-language preview and the binding digest; approving writes a signed,
  one-time receipt (0600, atomic); the secret never leaves the process env.
- **Hindsight trial harness complete** (`workflow #82`, `#88`): fixtures, evaluator with the
  pre-registered thresholds, three deterministic stub arms, and the report layer with a documented
  exit-code contract — 38 tests. The trial itself runs before any memory provider is adopted.
- **Verify gate now says *why* it refused** (`bundlebees #73`): rule identity, line, and a masked
  excerpt in the refusal envelope — the failure class where a refusal used to reach the coordinator
  with no reason at all.
- **Gate-watcher requires origin-landed delivery evidence** (`bundlebees #74`), and binary payloads
  verify end-to-end (`#72`).
- **Deploy parity checker live** (`workflow #76`): five surfaces compared against merged main;
  `DRIFT` and `UNKNOWN` are never treated as parity.

### Broke, and what it taught

- **Zoo's free GLM pass stopped accepting API traffic** mid-morning: every dispatch failed with
  `Internal error: Zoo Pass Pilot models are available in the Zoo Code IDE only.` The launcher had
  a DeepSeek fallback *configured* but only engaged it on 429/402, so the refusal killed attempts
  after zero model work. Fix: `hyvv #25` — a model-refusal error class that rolls the fallback once
  and fails closed if the fallback refuses too (RED-first: 2/3 tests failed on the pre-fix tree;
  GREEN 3/3, 112/112 across the routing family).
- **One model cannot hold a long design brief.** Four consecutive worker attempts at the trial
  harness ended after 3–4 minutes of pure reasoning with *zero* write calls. The workspaces were
  genuinely empty — nothing was lost, nothing to recover. The factory now shows that failure
  honestly (empty checks + a landing event); the systematic fix is smaller briefs, and a
  `verify --include-uncommitted` recovery path is queued for the built-but-uncommitted case.
- **A missing registry row turned every PR red.** PR #75 (collector alias) merged without its
  docs-sync registry row, so every later PR failed the same protocol test — the gate was working,
  the process had a hole. The beat's companion (`#79`) closed it within the hour.

### Operational

- **All five deploy surfaces green**: plugin `07cdc17`, gateway-loaded revision, collector
  `7729b8c1`, Observatory app `9b919b23`, launcher `129191f` (0.1.17).
- Worker lifetime: **638 attempts succeeded**, 0 active at last read; the admin lock from an
  earlier aborted restart was cleared by the owner, restoring admin operations.

---

## 2026-11-16

- **The Observatory went live** on the owner's tailnet: live fleet, pipeline, CI attribution,
  failure classes, alerts, bottlenecks and usage in one `snapshot/1` document — with honest
  `unavailable`/`stale`/`UNKNOWN` degradation instead of fabricated zeros.
- **~40 PRs merged in one day** through the full gate chain, including the mechanisms that run the
  factory (action queue with fencing, parallel verify-as-bee, gate-watcher, reconciliation sweeps,
  graceful recovery from died workers).
- **The usage pipeline reached mainline**: launcher receipts → outbox → ingest → pricing snapshots
  → per-attempt/job/feature rollup.
- **A dedicated failure audit** (independent review of the day's incidents) produced an eight-class
  failure ledger with fixes adopted — the audit itself was packaged with hash-verified evidence.
- **Lessons recorded, not just learned:** leases bind absolutely (a lease that omits a needed file
  has no sanctioned bypass — re-dispatch with a corrected lease); a missing branch is not missing
  work (check merge history before claiming loss); restart the launcher only with zero active bees.

## 2026-11-15

- First contract-frozen dispatches under the sealed-ceiling job system; the fleet wiring decisions
  were made; the Rails specification (`rizzstack.rails/1`) was drafted and frozen for review.

---

*Format: date → landed / broke / operational. Entries are added when a milestone lands or an
incident closes, and are never rewritten to look better in hindsight.*
