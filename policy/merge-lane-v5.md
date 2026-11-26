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
         "merge_state_status": "CLEAN", "head": "<40-hex>",
         "number": 83, "branch": "feat/example", "base_ref": "origin/main",
         "files": ["..."]},
  "class": "slice" | "non-slice" | null,
  "reviews": [{"state": "...", "commit_id": "<40-hex>", "body": "...", "author": "..."}],
  "checks": [{"name": "...", "status": "completed", "conclusion": "success",
              "head": "<40-hex>", "completed_at": "<ISO-8601>"}],
  "required_checks": ["ci-gate"],
  "verify": {"records": [{"record": "...", "sha": "<40-hex>", "ok": true}],
             "compare": {"<sha>": {"status": "ahead", "files": ["..."] | null}}},
  "lease_tamper": {"ran": true, "ok": true, "reason": "gate_ok",
                   "script_ref": "origin/main", "script_sha": "<40-hex>",
                   "base": "<40-hex>", "head": "<40-hex>", "exit_code": 0},
  "owner_held": false
}
```

The `lease_tamper` record is **measured by the lane**, not supplied by the
caller: `main()` extracts `.github/scripts/lease_tamper.py` from the base
revision (`origin/main`), runs it against the exact head, and folds the record
into the facts. `decide()` refuses a missing, unrun or failed record. A facts
file that carries its own record is overwritten, so a caller cannot vouch for
the gate.

The module returns `{"merge": bool, "class": ..., "reason": ..., "verify":
"exact"|"blob"|null, "head": ...}`. Exit status is 0 only when `merge` is true.

Rules enforced in code:

- **Review** — GitHub `APPROVED`, or `COMMENTED` whose first non-empty line
  is the attestation form `APPROVE (independent review ...)` (markdown bold
  allowed; `APPROVED`, `APPROVE?` and `APPROVE (not an independent review)`
  do not qualify), with `commit_id` equal to the head. A review at an older
  head is `review_at_older_head`; a `CHANGES_REQUESTED` review at the head —
  formal state or the textual `CHANGES REQUESTED` convention — always blocks.
  Reviewer independence is the reviewing agent's attestation: on this
  single-maintainer account every review is posted by the same GitHub login,
  so login equality cannot be used as the test.
- **Checks** — every check carries a name and the exact head it ran against;
  an unnamed or unattributed entry is not evidence (`checks_invalid`,
  `checks_unattributed`). `required_checks` must explicitly list the names
  that gate the repo (the aggregate `ci-gate` is the ruleset's required
  check); a required name absent at the head is `checks_missing`, never
  "green by absence". Duplicate names must be ordered by distinct valid
  ISO-8601 timestamps; missing or equal timestamps are `checks_ambiguous`
  (list order is never a tie-breaker), so a reversed list cannot resurrect a
  stale success. A green run at an older sha does not count.
- **Verify (slices only)** — an `ok` record at the exact head, or an ancestor
  record with no changed file in common with the PR's file list. A record at
  the exact head that is not `ok` is `verify_failed_at_head` and is not
  rescued by an older blob match. The compare entry must carry the `files`
  key: explicit `null` means identical trees; a missing key, a non-string
  member, or a 300-entry (truncated) list is `verify_blob_check_inconclusive`
  unless another candidate proves equality.
- **Lease/tamper gate self-run** — the lane never treats the PR's own
  `lease-tamper` CI job as proof: a PR can edit the workflow or the gate
  script. Before any merge it extracts `.github/scripts/lease_tamper.py` from
  `origin/main` (fetching first when an `origin` remote exists), runs that
  trusted copy against the exact head with `--repo/--base/--head/--pull-request/--branch`,
  and refuses unless it exits 0. Missing/unreadable/unresolved script or base
  is `lease_tamper_not_run`; a non-zero gate is `lease_tamper_failed`. The
  record names the exact `script_sha` and `base` it used. PR CI remains a
  signal only.
- **Owner review for gate/lane changes** — a PR that touches `.github/**`,
  `.rizzstack/**`, `gate/**`, `gates/**`, `policy/**`, or a file whose name
  contains the word `gate` or `lane` (`scripts/merge_lane_v5.py`,
  `scripts/check_ci_gate.py`) additionally needs an explicit **owner** review
  at the exact head: first non-empty line `APPROVE (owner …)` or
  `OWNER APPROVED`, `commit_id` equal to the head. The independent review and
  the owner review do not substitute for each other; a `CHANGES_REQUESTED`
  review at the head blocks. Refusals: `owner_review_missing`,
  `owner_review_at_older_head`. Word tokens are used, so `delegate.py` is not
  gate code.
- **Owner gate** — `owner_held` must be explicitly `false`; true is
  `owner_gate` and an absent/unknown value is `owner_gate_unknown`. It blocks
  both classes before anything else.
- **Shape** — `pr.files` must be a list of paths (`files_invalid` otherwise);
  `pr.is_draft` must be explicit `false` and `pr.mergeable` explicit `true`
  or `MERGEABLE`; `reviews` must be a list; non-object facts are
  `facts_invalid`; duplicate JSON keys in the facts file are refused.

Reason codes: `owner_gate`, `owner_gate_unknown`, `facts_invalid`,
`pr_not_open`, `pr_draft`, `pr_not_mergeable`, `merge_state_<state>`,
`head_missing`, `files_invalid`, `class_unknown`, `checks_missing`,
`checks_pending`, `checks_failed`, `checks_invalid`, `checks_unattributed`,
`checks_ambiguous`, `required_checks_missing`, `reviews_invalid`,
`review_missing`, `review_at_older_head`, `review_changes_requested`,
`lease_tamper_not_run`, `lease_tamper_failed`, `owner_review_missing`,
`owner_review_at_older_head`, `verify_evidence_missing`,
`verify_failed_at_head`, `verify_at_older_head`,
`verify_blob_check_inconclusive`, `reviewed`.

## Rollout

1. This PR lands the decision core, the policy and the tests for both classes,
   the self-run lease/tamper gate and the sensitive-path owner review.
2. An independent review at the PR head is required before merge (the PR is
   non-slice: no frozen lease names it).
3. After the merge, the coordinator updates the live
   `rizzstack-auto-merge-v5` cron prompt to gate 4 = "slice only", to read the
   class from the lease/tamper gate, and to invoke this module with
   `--repo <clean checkout> --repo-slug <owner/repo>` so the gate runs on
   `origin/main`'s script. Until that edit, the live lane is unchanged and this
   code is not deployed.
