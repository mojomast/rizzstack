# Adding an agent (planned path)

1. Agent harness: implement the launcher's `kind` contract (receipts with observed.usage,
   tool-call log stream, stop reasons) — see hyvv.
2. Model provider: add a routing entry + versioned price snapshot (unknown stays UNKNOWN).
3. Orchestrator: implement the documented state transitions (action queue + decision
   queue); no Hermes-specific interfaces required (see the reference-coordinator plan).
