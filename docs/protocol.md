# Rizzstack protocol map (agent-agnostic interface index)

Versioned contracts any implementation can read or write. The glue between elements —
and the path for adding agents, providers, and orchestrators later.

| Contract | Status | Source of truth | Consumers |
|---|---|---|---|
| rizzstack.observability/snapshot/1 | frozen + deployed (live Observatory) | workflow observability/schemas | Observatory, collector, any dashboard |
| bee-landed/1 (+usage payload) | frozen + deployed (tool-path emissions wired) | hyvv events | bundlebees consumer, usage ledger |
| rails (hash-chained activity ledger) | frozen (workflow #40); wave-2 batch 1 landing (8/23) | workflow schemas/rails | vibeline-class renderers, agent query tool, forensics |
| action queue + fencing | merged + driving dispatch | bundlebees coordinator_actions | orchestrators, control surfaces, reference coordinator |
| owner decision queue | spec in review; approval surface shipped (orbitdesktop #41) | workflow + bundlebees | Orbit control surface, notifier, any operator UI |
| ACDD contracts + feature packets | frozen | workflow | brief compiler (planned), verification evidence |
| rizzstack.memory/1 | harness built (workflow #82, #88): fixtures, evaluator, stub arms, report | workflow scripts/trial | coordinator briefs (trial pending) |
| job authorize / sealed ceilings | frozen | bundlebees job CLI | any authorized dispatcher |

Rule: new agents/providers/orchestrators implement these contracts; the stack does not
learn their internals.
