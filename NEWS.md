# News — Rizzstack progress log

Dated, concrete entries. Every number here was read from the repositories, CI, or the launcher at
the time of writing; every incident is recorded with its cause, because a factory that hides its
failures is worthless.

---

## 2026-11-17

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
