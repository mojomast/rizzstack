# Merge lane v5: slice and non-slice pull requests

**Status (2026-11-27): merged, running in dry run.** The decision core, the
risk-tier rule (rizzstack #37) and the tier-aware gate vendored in all six
product repos are on `main`. The 24-hour dry run started 2026-11-27T14:26Z,
pinned to rizzstack `316087f7`. The canary merge and go-live each need the
owner's typed yes. Until go-live, pull requests merge by hand under the same
rules.

Owner decision (2026-11-25): the merge lane's evidence rule depends on the
class of the pull request.

| Class | Green checks at head | Independent review at the head, or carried forward from an earlier head | Verify evidence |
| --- | --- | --- | --- |
| **slice, tier T1** (a frozen lease names it; every changed path is on the repo's T1 allowlist) | required | **not required**, but a newest-decisive rejection at the head still refuses | **required** at the exact head, or blob equality on every changed file |
| **slice, tier T3** | required | required | **required** at the exact head, or blob equality on every changed file |
| **non-slice** (docs fix, CI chore, gate change with no slice lease) | required | required | not required |

"Carried forward" is the owner's 2026-11-25 rule: a review at an earlier head
still counts when every commit after it is a merge of the base and the pull
request's diff against the base is byte-identical (per-file blob equality). See
[Review carry-forward](#review-carry-forward).

The two classes do not borrow each other's evidence: a review alone does not
merge a slice, and a verify record alone does not merge anything. An unknown
class fails closed (`class_unknown`) — the lane never guesses from a branch
name.

## The lane is a control, not an adviser

`scripts/merge_lane_v5.py` performs the merge itself. It does not consume a
caller-supplied facts file on the live path, and no argument names the gate ref
or the required checks. Every input is measured by the lane:

| Fact | Source |
| --- | --- |
| PR state, draft, mergeability, head, base, labels | `GET /repos/{owner}/{repo}/pulls/{N}` |
| reviews (state, `commit_id`, body, `submitted_at`, author) | `GET .../pulls/{N}/reviews` (paginated) |
| changed files | `GET .../pulls/{N}/files` (paginated) |
| check runs at the head | `GET .../commits/{head}/check-runs?filter=latest` (paginated) |
| compare entries for blob equality | `GET .../compare/{record_sha}...{head}` |
| slice/non-slice class | the **trusted gate's own stdout**: `lease-tamper: class=slice` / `class=non-slice` |
| lease/tamper verdict | the lane runs `.github/scripts/lease_tamper.py` from `origin/main` itself |
| verify records | `~/.hermes/plugin-data/bundlebees/verify-results/*.json` (trusted local state), each bound to `repo`/`slice`/`contract_sha256` |
| slice provenance (repo, slice id, contract sha256) | `.rizzstack/leases/*.json` at `origin/main`, first frozen lease naming the PR (the gate's own rule) |
| owner-held | the PR's `owner-held` label and the campaign plan's `owner_held.prs` list |
| required checks | `policy/rulesets/<repo>.json` at `origin/main`, read with `git show` (never the worktree; `ci-gate` fallback) |

`--facts FILE` exists only to **replay a recorded document** (debugging and
regression evidence). It is refused together with `--merge`
(`facts_merge_forbidden`), so a facts file can never drive a merge. On the live
path the review carry-forward is measured from git and overwrites any recorded
`review_carry`; a caller-supplied record is never consulted there.

## Where the class comes from

The lease/tamper gate classifies a pull request as a slice exactly when a
frozen lease on the **base** branch names it by `pull_request`, `branch`, or
`slice`/`id` (workflow #182, vendored in hyvv #74). A branch-name prefix is
not a classifier. On a passing run the gate prints one machine-readable line —
`lease-tamper: class=slice` or `lease-tamper: class=non-slice` — and the lane
reads that. No class line means `class_unknown`.

## Risk tier (T1/T3)

The same gate run that prints the class also prints a merge-time risk tier on
its last line: `lease-tamper: class=slice TIER=T1`. The tier comes from the
changed paths (`<merge-base>...<head>`), never from the author:

- **T1** only when **every** changed path matches the repository's positive
  allowlist (`TIER_T1_COMMON` plus `TIER_T1_ALLOWLIST[<repo>]` in
  `.github/scripts/lease_tamper.py`: product source dirs, docs, new test files)
  and no T3 reason applies.
- **T3** for anything else, with a reason: `not_t1_allowlisted`, `github`,
  `conftest`, `existing_test_edit`, `pytest_config` (decided by parsing
  `pyproject.toml` before and after with `tomllib`; a parse failure is T3),
  `lease_store`, `gate_lane_script`, `deploy_config`, scanner findings, and
  `always_t3`. The always-T3 overlay (`TIER_ALWAYS_THREE`) covers
  `hyvv/ticket_verify.py` and any auth/token/crypt/sign/verify module in
  Python or JS/TS, even inside an allowlisted directory.
- A repository with no allowlist entry (uirizz, orbit-control-plane today), an
  unreadable history, or a missing tier line is **T3**. The lane never treats
  a missing tier as T1.

The lane (`decide()` in `scripts/merge_lane_v5.py`) merges a **T1 slice**
without an approval (reason `t1_verified`) when all of these hold: the
owner gate is clear, the PR is mergeable, lease-tamper passed, required checks
are SUCCESS at the head, verify evidence matches the head, any sensitive path
has an owner review, and the newest decisive review at or carried to the head
is not a rejection. Everything else needs the review the table above names.

This merge-time tier is not the dispatch-time `risk.tier` (low/medium/high) in
a brief. That one decides whether a bee needs a frozen contract. This one
decides whether a merge needs a review.

## The gate ref is pinned

`run_lease_tamper()` fetches `origin`, resolves `origin/main`, extracts
`.github/scripts/lease_tamper.py` at that exact commit, and executes it from a
temporary file. The required-check list is read from the same pinned revision
(`git show origin/main:policy/rulesets/<repo>.json`), so a pull request that
edits the ruleset file in the lane's checkout cannot lower the checks that
judge it. It records `script_sha` and `script_blob`, and refuses unless
`script_sha` equals the resolved `origin/main` commit (`gate_ref_not_pinned`).
There is no `--gate-ref` option: pointing the lane at a ref it chose would let
a pull request or an operator pick the judge. `allow_unpinned` exists only for
the test suite. Missing ref, missing script, failed fetch, failed base
resolution, timeout or a non-zero exit are refusals
(`gate_ref_unresolved`, `gate_script_unavailable`, `gate_fetch_failed`,
`gate_base_unresolved`, `gate_timeout`, `gate_failed`), never "green by
absence".

## Review format: one shared parser

The canonical attestation line is the first non-empty line of the review body:

```
APPROVE (independent review) at <sha>
APPROVE (owner) at <sha>
CHANGES REQUESTED (<why>) at <sha>
```

The pre-canonical forms remain accepted by the same parser:
`APPROVE — independent review ... at <sha>`, `APPROVE (independent review at
<sha>)`, and `OWNER APPROVED`. Markdown bold and a leading bullet are allowed.
`APPROVED` as a word, `APPROVE?`, bare `APPROVE`, and `APPROVE (not an
independent review ...)` do not qualify. A named sha must be a prefix of the
review's `commit_id`.

GitHub's `APPROVED` state is an approval even with an empty body. A body that
names the owner is an **owner** attestation only: an owner approval never
satisfies the independent-review rule, whatever state GitHub records, and an
independent approval never satisfies the owner-review rule.

The parser, the rejection rule, the carry-forward predicate, the sensitive-path
list and the green-conclusion rule live in **one shared policy block**, vendored
byte-identically in `lease_tamper.py` and `merge_lane_v5.py`. Both test suites
assert the block's sha256 and run the same `tests/fixtures/review_lines.json`
and `tests/fixtures/carry_scenarios.py`, so the two copies cannot drift
silently.

## The newest decisive review governs

A rejection — state `CHANGES_REQUESTED`, or a `COMMENTED` review whose first
line starts `CHANGES REQUESTED` — submitted after the approval being used, at
that approval's commit or any later commit (the head counts), refuses the
approval. The three reproduced cases are tests: approve@H1 + rejection@H1 then
a base-merge update to H2; approve@H1 + rejection@H2; approve@H + rejection@H
at the same head. A later re-approval supersedes the rejection in its turn, so
the rule is "newest decisive review", not "any rejection ever". A rejection
recorded at an earlier commit that the approval supersedes does not block.

## Checks: only SUCCESS is green

Every check carries a name and the exact head it ran against; an unnamed or
unattributed entry is not evidence (`checks_invalid`, `checks_unattributed`).
`required_checks` comes from the repo's ruleset file (`ci-gate` fallback); a
required name absent at the head is `checks_missing`, never "green by absence".
Duplicate names must be ordered by distinct valid ISO-8601 timestamps; missing
or equal timestamps are `checks_ambiguous` (list order is never a
tie-breaker). A green run at an older sha does not count. A required check that
completed `SKIPPED` or `NEUTRAL` is **not** green (`checks_not_success`): a
skipped gate proves nothing about the change.

## Verify (slices only)

A verify record is evidence only for the slice and contract it was written for.
It must carry `repo` (the `owner/name` slug), `slice` (the slice id) and
`contract_sha256`, and all three must equal the frozen base-store lease
(`.rizzstack/leases/*.json` at `origin/main`) that the lease/tamper gate used to
classify the pull request. A record from another repository or another slice is
refused at a blob-equal sha (`verify_provenance_mismatch`), and a slice with no
frozen base lease to bind to fails closed (`verify_provenance_missing`). A
record without provenance — every pre-provenance record — is not evidence for
any pull request.

Within those records: an `ok` record at the exact head, or an ancestor record
with no changed file in common with the PR's file list. A record at the exact
head that is not `ok` is `verify_failed_at_head` and is not rescued by an older
blob match. The compare entry must carry the `files` key: explicit `null` means
identical trees; a missing key, a non-string member, or a 300-entry (truncated)
list is `verify_blob_check_inconclusive` unless another candidate proves
equality.

## Local state is pinned, not configurable

The verify store (`~/.hermes/plugin-data/bundlebees/verify-results`), the
campaign plan (`~/.hermes/plugin-data/bundlebees/campaigns/feature-batch-1/plan.json`)
and the base lease store are constants, like the gate script's `origin/main`
ref. There is no `--verify-dir`, `--plan` or environment override in the CLI: an
operator-supplied path could point the lane at a record or a hold list the merge
machinery does not own. Tests inject both stores through `collect_facts`'s
function parameters, never through the command line.

## Owner review for gate/lane changes

A PR that touches `.github/**`, `.rizzstack/**`, `gate/**`, `gates/**`,
`policy/**`, or a file whose name contains the word `gate` or `lane`, or one of
these **explicit** paths — the code that defines "verified" and "approved" —
additionally needs an explicit owner review:

- `bundlebees_plugin/verify_local.py`
- `bundlebees_plugin/contract.py`
- `bundlebees_plugin/approval.py`
- `bundlebees_plugin/tdd.py`
- `bundlebees_plugin/acdd_cli.py`
- `bundlebees_plugin/subversion_scan.py`
- `bundlebees_plugin/test_receipts.py`, `bundlebees_plugin/transport.py`,
  `bundlebees_plugin/return_report.py` (ticket/receipt code)
- `tests/test_lease_tamper.py`, `tests/test_merge_lane_v5.py`

The owner attestation is `APPROVE (owner) at <sha>` (or `OWNER APPROVED`), at
the exact head or at an earlier head that carries forward under the same
predicate. The independent review and the owner review do not substitute for
each other. Refusals: `owner_review_missing` or the carry condition
(`review_carry_unmeasured`, `review_carry_stale`, …). Word tokens are used for
the name heuristic, so `delegate.py` is not gate code; the explicit list above
does not depend on the heuristic.

## Owner gate

`owner_held` must be explicitly `false`. It is `true` when the PR carries the
`owner-held` label or the campaign plan lists `<owner>/<repo>#<N>`. A missing
or unreadable plan is `owner_gate_unknown` (fail closed), not `false`.

## Shape

`pr.files` must be a list of paths (`files_invalid` otherwise); `pr.is_draft`
must be explicit `false` and `pr.mergeable` explicit `true` or `MERGEABLE`;
`reviews` must be a list; non-object facts are `facts_invalid`; duplicate JSON
keys in a replayed facts file are refused.

## Review carry-forward

The predicate is the same one `lease_tamper.py` applies to a non-slice pull
request, from the same shared block; the lane measures it with git and the
facts never supply it:

1. the review's `commit_id` is an ancestor of the head (a review that was
   rewritten away judged different code and never carries);
2. every commit in `review_sha..head` that is not already on the base is a
   merge commit whose non-first parents are on the base — a branch update, not
   authored work;
3. `git diff base...head` and `git diff base...review_sha` carry the same paths
   with the same blob shas (`--raw --no-renames`, so a rename is a delete plus
   an add): the diff the reviewer read is byte-identical to the diff that would
   merge.

If any condition fails the earlier review does not count and the refusal names
the condition. A missing record is `review_carry_unmeasured`; a record measured
for another head is `review_carry_stale`; an unknown reason is
`review_carry_invalid`. The same rule covers the owner attestation required on
a gate/lane change.

The reason it exists: the workflow ruleset sets
`strict_required_status_checks_policy`, so every merge to `main` forces open
branches to update before they can merge. That update moves the head and, with
an exact-head-only rule, demanded a fresh review of unchanged code every time.

## Merge execution

Only after the verdict is `merge: true` and `--merge` was passed:

```
BUNDLEBEES_MERGE_LANE=1 gh pr merge <N> --repo <owner/repo> --squash \
    --match-head-commit <head>
```

The lane then re-reads the pull request and requires `merged: true` **and**
`head.sha == <head>` (`merge_unverified` otherwise — a merge race is never
reported as success). `merge_failed` carries the command's output verbatim.
`--record PATH` appends one JSON line per verified merge. The lane never
force-pushes, never updates a branch by hand, never posts a review and never
edits a PR.

## Replay

```
python3 scripts/merge_lane_v5.py --facts recorded.json --repo <checkout>
```

Replays a recorded facts document through `decide()` and prints the verdict.
It never merges (`facts_merge_forbidden` with `--merge`), never fetches and
never runs the gate.

## Reason codes

`owner_gate`, `owner_gate_unknown`, `facts_invalid`, `facts_unreadable`,
`facts_merge_forbidden`, `pr_missing`, `repo_slug_missing`, `token_missing`,
`facts_unavailable`, `pr_not_open`, `pr_draft`, `pr_not_mergeable`,
`merge_state_<state>`, `head_missing`, `files_invalid`, `class_unknown`,
`checks_missing`, `checks_pending`, `checks_failed`, `checks_not_success`,
`checks_invalid`, `checks_unattributed`, `checks_ambiguous`,
`required_checks_missing`, `reviews_invalid`, `review_missing`,
`review_changes_requested`, `review_not_ancestor`,
`review_carry_authored_commit`, `review_carry_unrelated_merge`,
`review_carry_content_changed`, `review_carry_unreadable`,
`review_carry_unmeasured`, `review_carry_stale`, `review_carry_invalid`,
`lease_tamper_not_run`, `lease_tamper_failed`, `gate_ref_not_pinned`,
`gate_ref_unresolved`, `gate_fetch_failed`, `gate_script_unavailable`,
`gate_base_unresolved`, `gate_timeout`, `gate_run_failed`,
`owner_review_missing`, `verify_evidence_missing`,
`verify_provenance_missing`, `verify_provenance_mismatch`,
`lease_provenance_missing`, `lease_provenance_invalid`,
`verify_failed_at_head`, `verify_at_older_head`,
`verify_blob_check_inconclusive`, `merge_failed`,
`merge_unverified`, `reviewed`.

## Rollout

1. Done: the decision core, policy and tests for both classes, self-fetched
   REST facts, the pinned self-run gate, sensitive-path owner review,
   SUCCESS-only checks and review carry-forward.
2. Done: the risk tier in the gate (rizzstack #32, #35, #38, #39), vendored
   byte-identically into workflow, hyvv, bundlebees, orbitdesktop,
   orbit-control-plane and uirizz, and the T1 rule in the lane (rizzstack #37).
3. In progress: a 24h / ≥10-PR dry run with the tier in every logged
   decision (started 2026-11-27T14:26Z). The coordinator reports decisions
   vs. reality and every decision where tier or class looked wrong.
4. Owner's typed yes: one canary merge through the lane, then go-live as the
   only merge path.

Re-vendoring: any change to rizzstack's `.github/scripts/lease_tamper.py`
must be copied byte-identically to all six product repos, and the lane's
pinned script sha updated. Batch small gate changes (rizzstack #40 is held for
this) so the copies don't go stale one PR at a time.
