# Pre-authorization policy

**Status: proposal. P1–P5 are initial envelope proposals, not active permissions.**
An envelope is a narrow, bounded-use, expiring permission for one mechanical action
class. It never relaxes the gate, evidence, lease, sandbox, review, or secret-handling
rules. Concrete `id`, `action_class`, `conditions`, `max_uses`, `expires_at`, and
`created_from` values must validate against `schemas/preauth.schema.json` and receive
separate owner hash approval before creation.

## Envelope and approval rules

- `id` is unique and immutable. `action_class` selects exactly one permitted action;
  a class not defined here fails closed.
- Each entry in `conditions` has a stable `name`, an authoritative `fact` to measure,
  and the expected JSON `value`. All conditions must hold on every use.
- `max_uses` is an integer of at least 1. Every started action consumes one use,
  including an action that later fails or rolls back; a use refused before starting
  does not. Expiry, revocation, or reaching `max_uses` disables the envelope.
- `expires_at` is an absolute RFC 3339 date-time. It must be no more than **7 days**
  after the creation time recorded in the ledger; an earlier expiry is allowed. A
  missing, invalid, or later `expires_at` is refused. Expiry is enforced before every
  use and cannot be extended in place.
- `created_from` is the immutable provenance reference for the exact proposal. The
  separate approval ledger entry binds that reference to the envelope hash;
  `created_from` is not itself an approval.

The owner alone approves a concrete envelope by typing exactly
`approved: preauth <hash>`, where `<hash>` is the lowercase SHA-256 of the RFC 8785
canonical JSON bytes of the complete validated envelope. A click, a relayed claim of
approval, or approval of different bytes does not count. Changing any field produces a
different hash and requires a new envelope and new approval. **Hermes may propose but never create, widen or extend an
envelope without the owner's hash approval.** A proposal without that command is inert.

## Evidence, ledger, digest, and revocation

The executor must **prove every condition from data**, immediately before use, from
authoritative git objects, APIs, machine-readable receipts, and trusted ledger state.
A condition is **PROVEN** only when the measured fact equals its expected `value`.
Prose assurances, screenshots, self-reported success, missing data, stale data,
ambiguous data, and unavailable checks are not proof. Every check fails closed, and
mutable facts are re-read before an irreversible step.

An **append-only ledger** records the proposal, full envelope and hash, owner approval,
creation, every condition observation and its evidence, every allowed or refused use,
use consumption, action outcome, expiry, and revocation. Entries are hash-linked and
never edited or deleted; corrections are new entries. A **daily digest** to the owner
summarizes active and expired envelopes, uses and refusals, remaining uses, revocations,
and ledger hashes, including a no-activity digest when applicable. The digest is not
approval.

The owner may type `revoke preauth <id>` at any time. Revocation is effective immediately:
it blocks every new use and every subsequent revocable step without
waiting for expiry, a digest, or another approval. The executor checks revocation
immediately before acting and appends the event to the ledger. An already irreversible
effect cannot be undone by the command; any required automatic rollback still runs.
A revoked or expired id cannot be reactivated.

## Never pre-authorizable

There are no exceptions, including through P1–P5 or another envelope:

- the gate
- the merge lane
- policy/**
- rulesets
- preauth itself
- secrets
- force-push
- deleting data
- deploys without a health check and automatic rollback

## Initial envelope proposals

These are templates only. Instantiation requires concrete values, including concrete
limits and schedules, and its own hash approval. The conditions below are cumulative
with the common rules above, not alternatives to them.

### P1 — contract re-freeze for the same leased files

Permit only a metadata-only contract re-freeze under an existing lease.

1. `leased_files_unchanged`: the old and new parsed contracts and the current lease
   name exactly the same leased-file set. No path, lease, requirement, risk tier, or
   acceptance semantics may be added, removed, renamed, or changed.
2. `diff_allowlist`: a structural diff between old and new canonical contracts contains
   only replacement of `source_rev`, replacement or addition of **RED evidence**
   fields, and addition of test ids. Existing test ids remain; new ids are appended.
3. `source_rev_exact`: the new `source_rev` is the full commit SHA of the actual base
   checkout used for the new evidence, read from git at execution time.
4. `red_evidence_exact`: every added test id has a corresponding machine-readable RED
   result from an actual pre-fix run at that `source_rev`, with run id, log hash, test
   fingerprint, and explicit per-test failure. Not-run, passing, or unattributed output
   is not RED evidence.
5. `test_id_set_exact`: the set of added ids in the contract equals the set covered by
   the new RED evidence, and every id belongs to the same leased files.

Record old and new contract hashes and all evidence. Any other contract difference
requires the ordinary owner approval path.

### P2 — synchronize vendored gate copies

Permit only creation or replacement of the declared vendored gate-copy paths.

1. `copy_paths_exact`: the source-to-destination mapping is explicitly listed in the
   envelope; no other destination may change and no file may be deleted.
2. `source_main_pinned`: resolve rizzstack `main` immediately before copying and record
   its full commit SHA. Every source blob is read at that SHA.
3. `copies_byte_identical`: after writing, read back every destination and require its
   bytes and SHA-256 to equal its mapped source at the recorded SHA. Every copied file
   must be **byte-identical** to rizzstack `main`; no newline, formatting, generated,
   or manual edit is allowed.
4. `diff_scope_exact`: the resulting worktree diff contains only the declared copies.
   Record source commit, source and destination hashes, and the verification result.

P2 synchronizes copies only; it does not authorize changing the gate, its canonical
source, its semantics, or its rules.

### P3 — close a superseded PR

Permit only closing the explicitly superseded, still-open pull request.

1. `superseded_pr_open`: a fresh repository API read identifies the target PR as open
   and returns its exact repository, number, and head SHA.
2. `successor_named`: the target PR has exactly one machine-parsed
   `Superseded by: #N` declaration. That N is the named successor, in the same
   repository; zero or multiple declarations refuse the action.
3. `superseding_pr_merged`: a fresh API read of that exact **superseding PR** returns
   `merged: true`, a non-null merge timestamp, and a full merge commit SHA. A PR that
   is merely closed, approved, or green does not qualify.
4. `close_verified`: close only the target PR, then re-read it and require closed and
   unmerged. Do not merge it, alter any other PR, delete its branch, or delete data.

Record both PR numbers, the target head SHA, the successor merge SHA, and before/after
states in the ledger.

### P4 — guarded component deploy

Permit only deployment of the named component and target at a green main SHA.

1. `green_main_sha`: the candidate `source_sha` is a commit on `main`, and every
   required check, including the aggregate gate, has a current SUCCESS result at that
   exact SHA. Missing, pending, skipped, neutral, failed, or older-SHA checks refuse.
2. `artifact_provenance`: the staged immutable artifact digest is bound by a
   machine-readable build receipt to that `source_sha`, component, and target.
3. `pre_restart_smoke`: after staging and before any restart, the candidate artifact
   passes the configured pre-restart smoke. Its signed or otherwise trusted receipt
   names the artifact digest, target, checks, and pass result; an absent or stale
   receipt refuses.
4. `daily_limit`: the envelope fixes a positive integer N. At most **N/day** deploy
   attempts may start for that component per UTC day; every started attempt, including
   a failed or rolled-back attempt, counts. The ledger count must be below N before
   starting, independently of `max_uses`.
5. `outside_quiet_hours`: the envelope fixes explicit quiet-hour intervals and their
   time zone. Immediately before restart, the current time must be outside quiet
   hours. A missing or ambiguous schedule refuses; a safety rollback is never delayed
   for quiet hours.
6. `health_and_rollback`: before restart, identify the previous known-good artifact,
   configure the post-restart **health check** and timeout, and arm **automatic
   rollback**. A failed or timed-out health check automatically restores that artifact
   and verifies its health without waiting for approval. No health check or no armed
   automatic rollback means no deploy.

Record the SHA, artifact digest, smoke and health receipts, attempt count, timestamps,
and any rollback outcome.

### P5 — dependency-ordered wave dispatch

Permit only dispatch from the approved campaign plan; all ordinary lease, risk, and
verification checks still apply.

1. `plan_pinned`: the machine-readable campaign plan and its dependency DAG are pinned
   by SHA-256 in the envelope. Every dispatched bee and dependency edge must come from
   that exact plan.
2. `dependency_order`: waves are topological in **dependency order**. Every declared
   dependency of a bee must have a terminal success and verified required outputs at
   the expected revisions before that bee dispatches. An unresolved, running, failed,
   or unverified dependency blocks it; a bee cannot share a wave with its dependency.
3. `in_flight_cap`: there may never be more than **4 bees in flight** across the
   campaign. In flight means dispatch accepted until a terminal success, failure, or
   cancellation is recorded. Dispatch and ledger accounting are atomic; the measured
   count must be at most 3 immediately before another dispatch.
4. `circuit_breaker`: the first attempt failure, verification failure, lease or scope
   violation, missing evidence, or dispatch/accounting error trips the circuit breaker.
   Once tripped, no new bee or retry may dispatch and no later wave may start. Already
   in-flight bees are not cancelled by this policy; their outcomes are recorded. Reset
   requires a new concrete envelope and new owner hash approval, never an extension of
   the old envelope.

Record plan hash, topological wave, dependency evidence, in-flight count, each dispatch
and terminal event, and the circuit-breaker state and reason.
