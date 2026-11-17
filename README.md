# Rizzstack

> **You authorize an outcome. A factory of worker agents builds it — under frozen contracts,
> fail-closed verification, and independent review — and brings you back only the decisions that
> need a human.**

Rizzstack is an autonomous software development factory. Hand it an authorized goal and it runs the
whole path: decomposition into contracts, parallel implementation by sandboxed worker agents,
verification that refuses rather than guesses, review by a *different* model than the one that wrote
the code, integration gates on exact-head CI, docs, and deployment. The human stays where judgment
belongs — accepting a feature, approving a rename, setting a budget — and those asks arrive in an
audited decision queue, not as chat interruptions.

It is four independent elements glued by versioned contracts. Use one, some, or all:

| | Element | One-line job | Repo |
|---|---|---|---|
| 📜 | **workflow** | the rulebook: contracts, acceptance criteria, provenance, schemas | [mojomast/workflow](https://github.com/mojomast/workflow) |
| 🐝 | **bundlebees** | the factory floor: orchestration, leases, verification, review, merge gates | [mojomast/bundlebees](https://github.com/mojomast/bundlebees) |
| ⚙️ | **hyvv** | the workers: sandboxed execution, model routing, receipts, budgets | [mojomast/hyvv](https://github.com/mojomast/hyvv) |
| 🖥️ | **orbitdesktop** | the control room: workbench, the live Observatory, approvals | [mojomast/orbitdesktop](https://github.com/mojomast/orbitdesktop) |

## Why it's different

Governance is the product. Plenty of tools can make an agent write code; almost none can prove the
code was *allowed*, *checked*, and *reviewed* before it touched a shared branch. In Rizzstack:

- **Every dispatch is leased.** A brief names the exact files an agent may change. The verify gate
  rejects any patch outside the lease — there is no "just this once" path (`scope_violation`).
- **Verification is fail-closed.** Patches are re-applied to a clean worktree of the target revision
  and tested there; a secret scan (gitleaks) runs on the patch itself. A refusal carries the rule
  identity, the line, and a masked excerpt — never a silent pass.
- **Review is adversarial by construction.** An independent reviewer agent — different model
  instance — reads the product and its tests together and can block the merge.
- **Merges are gated on exact-head evidence.** CI must be green at the exact commit being merged,
  after the last update-branch. "Mergeable" per GitHub is not the standard.
- **The record is hash-chained.** Worker events carry attempt ids, observed usage, and stop reasons.
  "The agent says it passed" is never the evidence.
- **Agents are swappable.** Model providers, agent harnesses, and even the orchestrator speak
  versioned contracts (`rizzstack.observability/snapshot/1`, `bee-landed/1`, ACDD packets,
  the action queue with fencing). Swap the model and the guarantees don't move.

## The loop

```
authorize ─► packet/contract ─► dispatch ─► bee implements (sandboxed)

    │                                              ▼
    │                              verify (fail-closed, clean worktree, secret scan)
    │                                              ▼
    │                              independent review (different model)
    │                                              ▼
    └── owner decisions ◄── RETURN manifest ◄── integration gate ─► exact-head CI ─► merge
                                       │
                                       ▼
                          docs-sync (CHANGELOG enforced by test) ─► deploy ─► runtime parity check
```

Every arrow is mechanized today: an action queue with fencing tokens drives dispatch; a gate-watcher
auto-dispatches backlog slices when their dependency gates clear; reconciliation sweeps recover
completed-but-unpushed work; a deploy-parity checker compares every deployed surface against merged
main and reports `DRIFT` instead of guessing. The human is paged only where judgment is required.

## Live numbers

Measured from the repositories and the launcher on **November 17, 2026**:

| | |
|---|---|
| Pull requests merged **today** through the full gate chain | **39** (workflow 24 · bundlebees 8 · orbitdesktop 6 · hyvv 1) |
| Worker attempts succeeded (lifetime, this host) | **638** |
| Deploy parity across five surfaces (plugin, gateway, collector, Observatory app, launcher) | **all green** |
| Rails wave-2 slices landed of 23 planned | **8** (R00–R04, R08, R10, R13) |
| Hindsight trial harness | **complete** — fixtures, evaluator, three stub arms, report layer, 38 tests |

Those merges include the mechanisms that run the factory: the verify gate that now reports *why* it
refused (`bundlebees #73`), the gate-watcher that requires origin-landed delivery evidence
(`#74`), the approval surface itself (`orbitdesktop #41`), and the trial harness that will decide
whether the next memory layer is worth its cost (`workflow #82`, `#88`).

## The protocol map

Every interface is versioned and documented. New agents, providers, and orchestrators implement the
contracts; the stack never learns their internals.

| Contract | Status | What it gives you |
|---|---|---|
| `rizzstack.observability/snapshot/1` | frozen + deployed | Fleet/pipeline/CI/failures/alerts/bottlenecks/usage as one document, with honest `unavailable`/`stale` degradation |
| `bee-landed/1` + usage payload | frozen + deployed | Completion events carrying observed token usage into the ledger |
| `rizzstack.rails/1` | frozen; wave-2 build landing | Hash-chained per-agent activity ledger: forensic timelines, tamper-evident evidence |
| action queue + fencing | merged | Dispatchable, replayable, fenced coordinator actions any orchestrator can consume |
| owner decision queue | in review | Durable request/answer surface for human decisions |
| `rizzstack.memory/1` | harness built | Semantic memory vault feeding briefs; the Hindsight trial measures whether it pays |
| ACDD contracts + feature packets | frozen | Acceptance criteria, leases, RED-first evidence |
| job authorize / sealed ceilings | frozen | Authorized dispatch with bounded retries and typed refusals |

Full index: [docs/protocol.md](docs/protocol.md). Extension paths:
[docs/adding-an-agent.md](docs/adding-an-agent.md).

## See it working

- **The Observatory** is live on the owner's tailnet: a single-page operations view over the
  `snapshot/1` document — live fleet, pipeline, CI attribution, failure classes, usage. It degrades
  honestly: anything it cannot observe renders as `UNKNOWN`, never as zero.
- **The approval surface** (`orbitdesktop/apps/rizzstack-approvals`) turns a job approval into one
  click: the page shows the plain-language preview and the SHA-256 binding digest; approving writes
  a signed, one-time receipt (0600, atomic). No secret ever reaches the browser.
- **The decision queue** is how the owner is asked anything; the answer is durable and audited.

## Honest gaps (November 17)

- **The worker's primary model route changed under us.** Zoo's free GLM pass stopped accepting API
  traffic mid-day (`models are available in the Zoo Code IDE only`). Dispatch now runs on the
  configured DeepSeek fallback; a launcher fix (`hyvv #25`) makes *refused-model* errors trigger
  the fallback automatically instead of killing the bee. This is the class of thing the factory
  exists to make visible and survivable.
- **Some models cannot hold a long design brief.** One model reliably spends its whole turn
  designing and ends without writing a file. The factory now makes that failure *visible*
  (empty checks + a landing event) instead of silent; the systematic fix is smaller briefs and
  the queued `verify --include-uncommitted` recovery path.
- **Rails is 8 of 23 slices in.** The remaining slices are gated on their dependencies, not on
  capacity; the wave cap is deliberately ≤8 concurrent.
- **The rebrand is approved but held** behind an owner read of the runbook — nothing renames until
  the checklist is signed off.
- **Pre-1.0.** Interfaces are versioned, but the umbrella repo is a map, not a stable release.

## Roadmap

| Milestone | What it proves | State |
|---|---|---|
| **M1 — live fleet** | Observatory shows every live bee; a coordinator restart loses nothing; the first full autonomous feature RETURNs | fleet wiring merged and live; interruption test scheduled |
| **M2 — enforced contracts** | A canary where tampered/unauthorized work is refused with precise reasons | machinery mainline; canary half-run, resumable |
| **M3 — economics truth** | Per-attempt/job/feature token+cost accounting before the free pass ends | data path mainline; deploy parity green |
| **Rails** | Forensic per-agent timelines; the debug layer for the whole factory | 8/23 landed; batch 2 in dispatch |
| **Memory (Hindsight)** | Does semantic memory beat contracts alone? Decided by a pre-registered trial, not vibes | harness complete; trial pending |
| **Approval surface** | Owner decisions as one audited click | merged into orbitdesktop |
| **Rebrand + website** | One public jumping-off point under the final identity | runbook held for owner read |
| **F10 — reference coordinator** | A non-LLM coordinator drives the same loop | Phase 3 |

## For agents and orchestrators

- **Add an agent harness** — implement the launcher's `kind` contract: receipts with observed usage,
  tool-call streams, stop reasons. See [hyvv](https://github.com/mojomast/hyvv).
- **Add a model provider** — a routing entry plus a versioned price snapshot; unknown prices stay
  `UNKNOWN`, never 0.
- **Add an orchestrator** — implement the action queue and decision queue contracts. Nothing in the
  contracts is Hermes-specific.
- **Read the protocol first** — [docs/protocol.md](docs/protocol.md), then
  [docs/adding-an-agent.md](docs/adding-an-agent.md).

## News

See [NEWS.md](NEWS.md) for the dated progress log — what landed, what broke, and what it taught.

## Branding

Product naming and visual identity are planned and deliberately deferred; the operational names in
the table are in active use, and a completed migration plan awaits the owner's checklist.

---

*This README is updated as milestones land. The execution record behind every claim lives in the
campaign logs and the repositories themselves — those are the code of truth.*
