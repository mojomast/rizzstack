# Rizzstack protocol map (agent-agnostic interface index)

Versioned contracts any implementation can read or write. The glue between elements —
and the path for adding agents, providers, and orchestrators later.

| Contract | Status | Source of truth | Consumers |
|---|---|---|---|
| rizzstack.observability/snapshot/1 | frozen (bottlenecks section in review) | workflow observability/schemas | Observatory, collector, any dashboard |
| bee-landed/1 (+usage payload) | frozen (payload extension in review) | hyvv events | bundlebees consumer, usage ledger |
| rails (hash-chained activity ledger) | spec in review (workflow #40) | workflow schemas/rails | vibeline-class renderers, agent query tool, forensics |
| action queue + fencing | merged (bundlebees #54) | bundlebees coordinator_actions | orchestrators, control surfaces, reference coordinator |
| owner decision queue | spec in review | workflow + bundlebees | Orbit control surface, notifier, any operator UI |
| ACDD contracts + feature packets | frozen | workflow | brief compiler (planned), verification evidence |
| job authorize / sealed ceilings | frozen | bundlebees job CLI | any authorized dispatcher |

Rule: new agents/providers/orchestrators implement these contracts; the stack does not
learn their internals.
