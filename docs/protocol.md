# rizzstack protocol map (agent-agnostic interface index)

Versioned contracts any implementation can read or write. The glue between elements —
and the path for adding agents, providers, and orchestrators later.

Status checked against each repository's `main` on 24 November 2026 (America/Toronto). The
[code map's contract table](../codemap/contracts.md) lists the producer and consumer files for each one.

| Contract | Status | Source of truth | Consumers |
|---|---|---|---|
| `rizzstack.observability/snapshot/1` (Observatory) | frozen + deployed (live Observatory) | [workflow `observability/schemas/snapshot.schema.json`](https://github.com/mojomast/workflow/blob/main/observability/schemas/snapshot.schema.json); collector `contrib/rizzstack-observability/collector.py` | Observatory and Hive apps in Orbit, any dashboard |
| `rizzstack.observability/1` (Hermes chat cards) | merged ([orbit-control-plane #4](https://github.com/mojomast/orbit-control-plane/pull/4), 23 Nov); a separate, redacted document, **not** the Observatory snapshot above despite the similar name | [orbit-control-plane `src/observability.mjs`](https://github.com/mojomast/orbit-control-plane/blob/main/src/observability.mjs) (`SCHEMA`); producer `scripts/obs-snapshot.mjs` | the read-only `rizzobs` Hermes plugin, which renders it as chat cards |
| bee-landed/1 (+usage payload) | frozen + deployed (tool-path emissions wired) | hyvv events | bundlebees consumer, usage ledger |
| `rizzstack.rails/1` (hash-chained activity ledger) | Frozen wire format; **implementation merged in all four repos** ([Workflow #91](https://github.com/mojomast/workflow/pull/91), [Hyvv #26](https://github.com/mojomast/hyvv/pull/26), [Orbit #46](https://github.com/mojomast/orbitdesktop/pull/46), [BundleBees #82](https://github.com/mojomast/bundlebees/pull/82)); all 22 Rails slices `landed` in the backlog. Release on hold per the [release decision](https://github.com/mojomast/workflow/blob/main/evidence/rails-rc/release/DECISION.md); `rails_enabled` off | [Workflow Rails schemas](https://github.com/mojomast/workflow/tree/main/schemas/rails) | Hyvv emits worker events; BundleBees imports and queries them; Orbit shows the same event IDs in its inspector. |
| `rizzstack.provisioning/1` (worker workspace provisioning) | implemented and advertised by hyvv; bundlebees reads the capability, and `bundlebees doctor` reports it | [hyvv `provisioning.py`](https://github.com/mojomast/hyvv/blob/main/provisioning.py) | bundlebees `provisioning.py` and `provision_bind.py` |
| action queue + fencing | merged + driving dispatch | bundlebees coordinator_actions | orchestrators, control surfaces, reference coordinator |
| `rizzstack.owner-decision/1` (owner decision queue) | queue and schema merged (spec marked draft, normative for the owner-decision lane); snapshot `decisions` section merged (workflow #96, #97); approval surface shipped (orbitdesktop #41) | [workflow `docs/decision-queue.md`](https://github.com/mojomast/workflow/blob/main/docs/decision-queue.md), `observability/schemas/owner-decision.schema.json`, `bundlebees_plugin/decision_queue.py` | Orbit control surface and Hive, notifier, any operator UI |
| Risk tiers (`risk.tier` on a brief) | **enforced at dispatch, fail closed** ([BundleBees #247](https://github.com/mojomast/bundlebees/pull/247)): a tier below the path-derived floor is refused (`risk_tier_below_floor`), and a high-tier brief without a frozen ACDD contract is refused (`contract_missing`). The phase, reviewer and human-gate table is still report-only | [bundlebees `docs/risk-tiers.md`](https://github.com/mojomast/bundlebees/blob/main/docs/risk-tiers.md), `risk.py`, `risk_policy.py` | bundlebees `_dispatch`; brief generators |
| ACDD contracts + feature packets | frozen | workflow | brief compiler (planned), verification evidence |
| `rizzstack.memory/1` | contract frozen; disk vault service M1 merged ([workflow #98](https://github.com/mojomast/workflow/pull/98)), plus write quarantine, index and retention (#101, #102, #104). Not wired into the runtime; `MEMORY_ENABLED` stays off | [workflow `schemas/memory-schema.v1.json`](https://github.com/mojomast/workflow/blob/main/schemas/memory-schema.v1.json); `rizzstack_memory/` | coordinator briefs (trial pending) |
| job authorize / sealed ceilings | frozen | bundlebees job CLI | any authorized dispatcher |

Rule: new agents/providers/orchestrators implement these contracts; the stack does not
learn their internals.

## Owner decisions are queue state, not prose

An owner answer takes effect only when it reaches the machine queue. The coordinator reads its plan first and never auto-dispatches a slice marked `blocked-on-owner`, so a decision recorded only in a campaign log is restated as outstanding by every later beat while the queue sits idle. Write the decision as a queue transition, in the same action:

1. **No beat holds the claim.** Back up the plan, move every gate the decision answers off `blocked-on-owner`, add `state.owner_decisions_YYYYMMDDTHHMMZ` with the verbatim answers, and point `next_action` at the newly unblocked slice.
2. **A beat owns the claim.** Write `owner-decisions-YYYYMMDDTHHMMZ.json` beside the plan and append the record section; the beat's first duty folds any sidecar newer than `last_coordinator_touch` before dispatch.
3. **A redacted or missing answer is a missing answer.** Name the exact field that needs restating. Never infer it, never mark the gate closed.
4. **No gate is closed until the plan no longer reads `blocked-on-owner`.** Recorded and unblocked are different states.

Failure this closes: a decision four later beats could read in the log and none acted on, because none was allowed to dispatch a `blocked-on-owner` slice.
