# Rizzstack

> **You describe the software you want. A factory of worker agents builds it — under contracts,
> verification, and independent review — and brings you only the decisions that require a human.**

Rizzstack is an autonomous software development factory: it takes an authorized outcome and runs the
entire path — decomposition into frozen contracts, parallel implementation by sandboxed worker agents,
fail-closed verification, independent review by a *different* model, integration gates on green CI,
docs and deployment — while reserving judgment calls (accept a feature, approve a rename, set a budget)
for the human, delivered through an audited decision queue rather than chat interruptions.

It is built from four independent elements glued by versioned contracts — use one, some, or all:

| | Element | One-line job | Repo |
|---|---|---|---|
| 📜 | **workflow** | the rulebook: contracts, acceptance criteria, provenance, schemas | [mojomast/workflow](https://github.com/mojomast/workflow) |
| 🐝 | **bundlebees** | the factory floor: orchestration, leases, verification, review, merge gates | [mojomast/bundlebees](https://github.com/mojomast/bundlebees) |
| ⚙️ | **hyvv** | the workers: sandboxed execution, model routing, receipts, budgets | [mojomast/hyvv](https://github.com/mojomast/hyvv) |
| 🖥️ | **orbitdesktop** | the control room: workbench, the live Observatory dashboard, approvals | [mojomast/orbitdesktop](https://github.com/mojomast/orbitdesktop) |

**What makes it different:** governance is the product. Every agent action is authorized, leased,
verified, and independently reviewed *before* it can touch your main branch — and the evidence trail
is hash-chained, so "the agent says it passed" is never the standard. The system is agent-agnostic by
contract: swap model providers, agent harnesses, or even the orchestrator itself (a non-LLM reference
coordinator is on the roadmap) without changing the guarantees.

**Proof it works:** in one day of operation the factory merged **~40 pull requests** across the four
repos — built, verified, reviewed, and integrated by worker agents themselves, including the very
mechanisms that run the factory. The live truth table below separates what is proven from what is
honestly still in flight.

---

> **Status: pre-1.0, under heavy active development** (as of **November 16, 2026**).

## The loop

```
authorize ─► packet/contract ─► dispatch ─► bee implements (sandboxed)
    │                                              │
    │                                              ▼
    │                              verify (fail-closed, exact interpreter)
    │                                              │
    │                                              ▼
    │                              independent review (different model)
    │                                              │
    │                                              ▼
    └── owner decisions ◄── RETURN manifest ◄── integration gate ─► exact-head CI ─► merge
                                       │
                                       ▼
                          docs-sync (CHANGELOG enforced by test) ─► deploy ─► runtime-verify
```

Every arrow above is now **mechanized**: the action queue with fencing tokens drives dispatch,
parallel verify-as-bee runs in shadow mode, reconciliation sweeps catch completed-but-unpushed work,
the gate-watcher auto-dispatches queued slices when their gates clear, and graceful recovery mines
died-worker event streams into resume briefs. The human stays where judgment is required.

## The four elements (+ the umbrella)

| Element | Repo | What it does | Use it alone for |
|---|---|---|---|
| **workflow** | [mojomast/workflow](https://github.com/mojomast/workflow) | Contracts, policy, acceptance criteria, provenance, observability schemas, the protocol playbook | Versioned acceptance criteria, evidence formats, protocol schemas |
| **bundlebees** | [mojomast/bundlebees](https://github.com/mojomast/bundlebees) | Orchestration: authorized jobs, scoped leases, sandboxed verification, independent review, integration gates, recovery, the action queue | Running verified multi-agent implementation work with your own workers |
| **hyvv** | [mojomast/hyvv](https://github.com/mojomast/hyvv) | Worker launcher: sandboxes (runc/Cua/firejail), model routing (Zoo GLM-5.3-Flash primary, DeepSeek fallback), receipts with usage, budgets, durable events | Operating coding agents on your own worker hosts with budget enforcement |
| **orbitdesktop** | [mojomast/orbitdesktop](https://github.com/mojomast/orbitdesktop) | Operator desktop: workbench, the Observatory dashboard, approvals, decision surfaces | A local agent workspace with live operation views |

**This repo (rizzstack)** is the umbrella: the jumping-off point, the element map, and the versioned
protocol index — [docs/protocol.md](docs/protocol.md). Install profiles (full / observer /
orchestrator / worker / contracts) are being specified in [docs/profiles.md](docs/profiles.md) —
each element is independently deployable; the contracts are the glue.

## The protocol map (agent-agnostic by contract)

Every stable interface is versioned and documented. New agents, providers, and orchestrators
implement these contracts; the stack does not learn their internals.

| Contract | Status | What it gives you |
|---|---|---|
| `rizzstack.observability/snapshot/1` | **frozen + deployed** | Live fleet/pipeline/CI/failures/alerts/bottlenecks/usage state as one document |
| `bee-landed/1` + usage payload | **frozen + merged** | Worker completion events carrying observed token usage into the ledger |
| `rizzstack.rails/1` | **spec in final review** | The hash-chained per-agent activity ledger: forensic timelines, tamper-evident evidence, agent-queryable history |
| action queue + fencing | **merged** | Dispatchable, replayable, fenced coordinator actions any orchestrator can consume |
| owner decision queue | **in review** | Durable request/answer surface for every human decision, never self-answered |
| `rizzstack.memory/1` | **spec built** | Semantic memory: an Obsidian-compatible markdown vault, typed memories, sha-guarded writes, brief-injection |
| ACDD contracts + packets | **frozen** | Feature packets with acceptance criteria, leases, RED-first checks |
| job authorize / sealed ceilings | **frozen** | Authorized dispatch with bounded retries and typed refusals |

See [docs/protocol.md](docs/protocol.md) for the full map and
[docs/adding-an-agent.md](docs/adding-an-agent.md) for the extension paths.

## Live truth table (Nov 16, 2026)

### Proven working end-to-end
- **~40 merges in one day** across the four repos through the full gate chain (verify → independent
  review → exact-head CI → merge), ~100+ worker attempts, one model (zoo:glm-5.3-flash) throughout
- **The Observatory is live** on the owner's tailnet: real fleet/pipeline/CI data, per-check failure
  attribution, honest `unavailable`/`stale`/`UNKNOWN` degradation instead of blank lies
- **The usage pipeline is complete in mainline**: launcher receipts → outbox → ingest service (installed
  and running) → pricing snapshots → per-attempt/job/feature rollup. *Not yet flowing:* the deployed
  launcher predates the payload — numbers appear after the next deploy
- **Contract enforcement (F2) is mainline**: `require_contract` at the campaign/repo/profile boundary
  with machine-readable refusals — the M2 canary (dedicated smoke project, built, runbook ready)
  proves it under fire next
- **The coordinator's throughput mechanisms are mainline**: action queue with fencing (F6),
  parallel verify-as-bee (V1, shadow), completed-attempt reconciliation, planned→queued gate-watcher,
  graceful recovery from died workers, lease-payload serving — every transition that used to depend
  on someone noticing is now machinery

### Honest gaps (named, owned, in flight)
- **13 PRs queue behind one review verdict** — the Rails spec (#40) passed CI 14/14 and a two-reviewer
  panel (approve with 4 adopted spec-text repairs; repair landed and mutation-proven); when its
  re-review clears, the 22-package parallel build opens under a 14-bee ceiling
- **The deployed launcher is behind main** (idle-gated deploy carries: usage numbers going live,
  budget enforcement, the 14-cap)
- **The fleet section** of the Observatory waits on the M1 wiring PR (#41) — same deploy-adjacent path
- **The workbench** carries three product bugs under active repair (one died worker's diagnosis was
  recovered verbatim from the event stream and resumed with evidence — the recovery mechanism exists
  because of it)
- **Verification exceptions** are reduced to documented environmental constraints (gitleaks CI-vs-local
  divergence: solved by reproducing with the exact CI binary; scanner overmatches: attributed and
  re-briefed, never disabled)

### Owner-gated (deliberately not automated)
- The M1 receiver decision (the first full autonomous feature, awaiting its final review)
- The rebrand execution — plan exists (workflow #47), nothing renames until approved
- Spending thresholds (measure-first posture; daily summaries once the ledger flows)
- The model-provider switch (Nov 29: Zoo → opencode-go)

## The roadmap

| Milestone | What it proves / delivers | State |
|---|---|---|
| **M1 — live fleet** | The Observatory shows every live bee; a coordinator restart loses nothing (interruption test); the first full autonomous feature RETURNs to the human | wiring PR in review; install sequence ready |
| **M2 — enforced contracts** | A canary environment where tampered/unauthorized work is refused with precise reasons | machinery mainline; canary runbook ready |
| **M3 — economics truth** | Per-attempt/job/feature token+cost accounting before the free model pass ends | data path mainline; deploy pending |
| **Rails** | Forensic per-agent timelines; tamper-evident evidence; the debug layer | spec in final gate; 22-package build behind it |
| **Memory** | Semantic memory vault (Obsidian-compatible) feeding briefs — agents start smarter than their predecessors | contract built; service next |
| **Umbrella + rebrand + website** | One public jumping-off point under the final identity | repo live; plans approved-awaiting-checklist |
| **v2 Observatory (Operations View)** | Spatial control surface: see the factory, act through owner-gated fenced controls, bottlenecks named | design spike done; build staged |
| **F10 — reference coordinator** | A non-LLM coordinator drives the same loop — proof the guarantees belong to Rizzstack, not to any agent | Phase 3 |

## For agents and orchestrators

- **Add an agent harness**: implement the launcher's `kind` contract (receipts with observed usage,
  tool-call streams, stop reasons) — see hyvv
- **Add a model provider**: a routing entry + a versioned price snapshot (unknown stays UNKNOWN, never 0)
- **Add an orchestrator**: implement the action queue + decision queue contracts — no Hermes-specific
  interfaces required
- **Read the protocol map**: [docs/protocol.md](docs/protocol.md)

## Branding note

Product naming/visual identity is planned but deliberately deferred; the operational names in the
repo table are in active use, and a completed migration plan awaits the owner's checklist approval.

---

*This README is updated as milestones land. The execution record behind every claim lives in the
campaign logs; the code of truth is the repositories themselves.*
