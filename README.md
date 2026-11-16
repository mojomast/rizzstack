# Rizzstack

**The autonomous software development factory.** You authorize an outcome; Rizzstack
decomposes it into frozen contracts, worker agents implement it in sandboxes, independent
reviewers verify it, integration gates merge it on green CI, and you make the human
decisions through explicit, audited gates.

> Status: pre-1.0, under active development. Element interfaces are being versioned
> (see [docs/protocol.md](docs/protocol.md)). This umbrella repo is the jumping-off
> point — use one element or the full stack.

## The four elements

| Element | Repo | What it does | Use it alone for |
|---|---|---|---|
| **workflow** | mojomast/workflow | Contracts, policy, acceptance, provenance, observability schemas | Versioned acceptance criteria, evidence formats, protocol schemas |
| **bundlebees** | mojomast/bundlebees | Orchestration: authorized jobs, scoped leases, sandboxed verification, independent review, integration gates, recovery | Running verified multi-agent implementation work with your own workers |
| **hyvv** | mojomast/hyvv | Worker launcher: sandboxes (runc/Cua), model routing, receipts, budgets, durable events | Operating coding agents on your own worker hosts with budget enforcement |
| **orbitdesktop** | mojomast/orbitdesktop | Operator desktop: workbench, the Observatory dashboard, approvals, decision queue | A local agent workspace with live operation views |

## Install profiles (planned — see docs/profiles.md)

- **full** — the complete factory loop
- **observer** — snapshot collector + Observatory only (watch any agent stack)
- **orchestrator** — bundlebees + your choice of worker host
- **worker** — hyvv on a build box, joining an existing coordinator
- **contracts** — workflow schemas + validation only

## Agent-agnostic by design

Rizzstack's governance does not depend on one AI vendor or one agent harness:

- **Model providers** are routed (currently Zoo / DeepSeek / OpenCode-Go) — adding a
  provider is a routing entry plus a price snapshot.
- **Agent harnesses** are a `kind` (currently `opencode`, `agy-cua`, `check`) — new
  agent runtimes implement the same receipt/event contract.
- **The orchestrator is replaceable**: the coordinator's decisions are documented as
  machine-readable state transitions (action queue, decision queue, snapshot/rails
  contracts) so any orchestrator — including a minimal non-LLM reference one — can drive
  the same infrastructure.

See [docs/protocol.md](docs/protocol.md) for the versioned interface map and
[docs/adding-an-agent.md](docs/adding-an-agent.md) for the extension paths.

## Repos are modular; the contract is the glue

Each element is independently deployable. The stable glue is the set of versioned
contracts (snapshot/1, rails, bee-landed, action/decision queues) — read the schema,
implement the side you need, and the rest of the stack will interoperate.

## Branding note

Product naming/visual identity is planned but deliberately deferred; current
operational names are in active use.
