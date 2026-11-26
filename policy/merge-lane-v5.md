# Merge lane v5: slice and non-slice pull requests

**Status: implemented, awaiting independent review. The live cron prompt is
updated only after this PR is reviewed; nothing in this change merges, pushes a
branch, or edits a running lane.**

Owner decision (2026-11-25): the merge lane's evidence rule depends on the
class of the pull request.

| Class | Green checks at head | Independent review at the exact head | Verify evidence |
| --- | --- | --- | --- |
| **slice** (a frozen lease on the base branch names it) | required | required | **required** at the exact head, or blob equality on every changed file |
| **non-slice** (docs fix, CI chore, gate change with no slice lease) | required | required | not required |

The two classes do not borrow each other's evidence: a review alone does not
merge a slice, and a verify record alone does not merge anything. An unknown
class fails closed (`class_unknown`) — the lane never guesses from a branch
name.

## Where the class comes from

The lease/tamper gate classifies a pull request as a slice exactly when a
frozen lease on the **base** branch names it by `pull_request`, `branch`, or
`slice`/`id` (workflow #182, vendored in hyvv #74). A branch-name prefix is
not a classifier. The lane reads that gate's conclusion; it does not re-derive
the class itself.

## Decision core

`scripts/merge_lane_v5.py` is a pure function over facts the lane measures
with `gh`. The same facts reproduce every refusal offline, and the tests cover
both classes without a network or a live repository.

Input (`--facts facts.json`):

```json
{
  "pr": {"state": "OPEN", "is_draft": false, "mergeable": true,
         "merge_state_status": "CLEAN", "head": "<40-hex>", "files": ["..."]},
  "class": "slice" | "non-slice" | null,
  "reviews": [{"state": "...", "commit_id": "<40-hex>", "body": "...", "author": "..."}],
  "checks": [{"name": "...", "status": "completed", "conclusion": "success", "head": "<40-hex>"}],
  "verify": {"records": [{"record": "...", "sha": "<40-hex>", "ok": true}],
             "compare": {"<sha>": {"status": "ahead", "files": ["..."] | null}}},
  "owner_held": false
}
```

The module returns `{"merge": bool, "class": ..., "reason": ..., "verify":
"exact"|"blob"|null, "head": ...}`. Exit status is 0 only when `merge` is true.

Rules enforced in code:

- **Review** — GitHub `APPROVED`, or `COMMENTED` whose first non-empty line
  starts `APPROVE` and names an `independent review`, with `commit_id` equal to
  the head. A review at an older head is `review_at_older_head`; a
  `CHANGES_REQUESTED` review at the head always blocks.
- **Checks** — every newest run per name is `SUCCESS`/`NEUTRAL`/`SKIPPED` at
  the head. A green run at an older sha does not count. No checks is
  `checks_missing` (fail closed), never "green by absence".
- **Verify (slices only)** — an `ok` record at the exact head, or an ancestor
  record with no changed file in common with the PR's file list. A `files:
  null` compare means identical trees; a 300-entry (truncated) compare is
  `verify_blob_check_inconclusive`.
- **Owner gate** — `owner_held` blocks both classes before anything else.

Reason codes: `owner_gate`, `pr_not_open`, `pr_draft`, `pr_not_mergeable`,
`merge_state_<state>`, `head_missing`, `class_unknown`, `checks_missing`,
`checks_pending`, `checks_failed`, `review_missing`, `review_at_older_head`,
`review_changes_requested`, `verify_evidence_missing`, `verify_at_older_head`,
`verify_blob_check_inconclusive`, `reviewed`.

## Rollout

1. This PR lands the decision core, the policy and 21 tests for both classes.
2. An independent review at the PR head is required before merge (the PR is
   non-slice: no frozen lease names it).
3. After the merge, the coordinator updates the live
   `rizzstack-auto-merge-v5` cron prompt to gate 4 = "slice only" and to read
   the class from the lease/tamper gate. Until that edit, the live lane is
   unchanged.
