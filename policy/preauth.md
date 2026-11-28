# Signed pre-authorization envelopes

A pre-authorization envelope is one bounded, owner-signed batch: the owner signs the exact
envelope bytes with `ssh-keygen -Y sign` using namespace `rizzstack-preauth`, and the key is
held only by the owner and never on the Hermes host. The signature is checked against
`policy/allowed_signers`, the read-only trust root, and every key line there must also match
the frozen fingerprint pin in the checker. An envelope the owner did not sign is refused.
Typed hashes prove content, not who approved it: only the owner's signature is approval.

An envelope is capped at **7 days**: `expires_at` minus `issued_at` must not exceed seven
days, and `expires_at` must be after `issued_at`. The checker decides one use from measured
facts only and must prove every condition from data; a missing, null, malformed or unprovable
fact is a named refusal, not a pass. The checker is bound to this policy by its sha256.

Agents never create, widen or extend envelopes. Only the owner signs one. Agents may ask for
a specific envelope and may execute exactly the action a valid envelope names, nothing more.

## Revocation

The owner revokes with `revoke preauth <id>`, effective immediately: the envelope id goes on
the measured revocation list before the next decision, and every later use refuses. A nonce
and a consumed-use record make replay visible, and budget counters are read from measured
state, never from the envelope's own claims.

## Never pre-authorizable

Some classes are never pre-authorizable, at any tier, for any budget: changes to the gate,
the merge lane, `policy/**` (including this file and `policy/allowed_signers`), rulesets,
the preauth checker or verifier, secrets handling, force-push, deleting data, and automatic
rollback. An envelope that names one of these, or a case variant of it, is refused. A
pre-authorization can never widen preauth itself.

## The five initial envelopes the owner is asked to consider

Each candidate is proposed with mechanical conditions, and the ledger records every use.

1. **P1 — Re-freeze behind an identical oracle.** A re-freeze of a frozen contract is allowed
   only when the frozen test blobs are identical at the old and new source_rev: the same
   conftest files, the same pytest config, and the same fixtures, byte-identical. Any added,
   removed or changed oracle file refuses; product code may differ. This is not a spec
   change.
2. **P2 — Merge one reviewed slice.** Merge a named slice whose RED evidence is present, whose
   review packet is byte-identical to the reviewed head, and whose contract, source_rev and
   tier match the envelope; at most the signed merge budget.
3. **P3 — Vendor a pinned dependency sync.** Land a dependency order that only updates a
   vendored, pinned copy, with the upstream revision named and no other path touched.
4. **P4 — Close a superseding PR.** Close a superseded pull request whose superseding PR is
   identified in the envelope; no merges, no branch deletion.
5. **P5 — Dispatch a bounded wave.** Dispatch a wave in dependency order with at most
   4 bees in flight, each on a listed contract; a failing health check stops the wave, and an
   automatic rollback is a separate action that is never pre-authorizable.

## Ledger and digest

Every decision appends to a ledger: envelope id, nonce, contract_sha256, source_rev, tier,
action, the measured facts it used, and the policy and trust-root sha256 it decided under.
The owner receives a daily digest of the ledger: uses, refusals and remaining budgets.
