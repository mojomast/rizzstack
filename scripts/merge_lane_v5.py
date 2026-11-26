#!/usr/bin/env python3
"""Merge-lane v5 decision core: slice and non-slice pull requests.

Owner-approved policy (2026-11-25): a pull request that is **not a slice**
merges on an independent review at its exact head plus green checks; a
**slice** pull request additionally needs verify evidence at the exact head
(or blob equality for every file it changes). The verify record is never a
substitute for the review, and the review is never a substitute for a slice's
verify evidence.

A pull request is a slice exactly when a frozen lease on the base branch names
it (by ``pull_request``, ``branch`` or ``slice``/``id``). The lease/tamper gate
(workflow #182, vendored in hyvv) makes that classification; this module does
not re-derive it from a branch-name prefix. An unknown class fails closed.

The decision core is pure: the lane collects the facts (PR state, checks,
reviews, verify records, compare results) with ``gh`` and passes them in. This
keeps every refusal reproducible from a JSON fact file and testable without a
network or a live repository.

Usage::

    python3 scripts/merge_lane_v5.py --facts facts.json
    python3 scripts/merge_lane_v5.py < facts.json

Output (exit 0 merge, exit 1 refuse)::

    {"merge": true, "class": "non-slice", "reason": "reviewed", "verify": null}
"""

from __future__ import annotations

import argparse
import json
import re
import sys

SLICE = "slice"
NON_SLICE = "non-slice"
MERGE_STATES = ("CLEAN", "BEHIND")
GREEN_CONCLUSIONS = ("SUCCESS", "NEUTRAL", "SKIPPED")
RED_CONCLUSIONS = (
    "FAILURE",
    "TIMED_OUT",
    "CANCELLED",
    "ACTION_REQUIRED",
    "STARTUP_FAILURE",
    "STALE",
)
APPROVE_LINE = re.compile(r"(?i)^\W*approve\b")
INDEPENDENT = re.compile(r"(?i)\bindependent\s+review\b")
SHA = re.compile(r"^[0-9a-f]{40}$")


def _sha(value):
    if isinstance(value, str):
        value = value.strip().lower()
        if SHA.fullmatch(value):
            return value
    return None


def _text(value):
    return value if isinstance(value, str) else ""


def independent_review_at_head(reviews, head):
    """Return the review that qualifies as an independent approval at ``head``.

    A qualifying review is either GitHub state ``APPROVED`` or a ``COMMENTED``
    review whose first non-empty line starts ``APPROVE`` and names an
    ``independent review``. Both must carry ``commit_id`` equal to ``head``;
    an approval at an older head is not an approval. A ``CHANGES_REQUESTED``
    review at the head always blocks, even when another review approves.
    """
    wanted = _sha(head)
    if wanted is None:
        return None
    head_reviews = [r for r in reviews if isinstance(r, dict) and _sha(r.get("commit_id")) == wanted]
    if any(_text(r.get("state")).upper() == "CHANGES_REQUESTED" for r in head_reviews):
        return None
    for review in head_reviews:
        state = _text(review.get("state")).upper()
        if state == "APPROVED":
            return review
        if state != "COMMENTED":
            continue
        lines = [line.strip() for line in _text(review.get("body")).splitlines() if line.strip()]
        if lines and APPROVE_LINE.search(lines[0]) and INDEPENDENT.search(lines[0]):
            return review
    return None


def review_refusal(reviews, head):
    """Why ``independent_review_at_head`` found nothing, as a reason code."""
    wanted = _sha(head)
    if wanted is None:
        return "review_missing"
    if any(isinstance(r, dict) and _text(r.get("state")).upper() == "CHANGES_REQUESTED"
           and _sha(r.get("commit_id")) == wanted for r in reviews):
        return "review_changes_requested"
    for review in reviews:
        if not isinstance(review, dict):
            continue
        state = _text(review.get("state")).upper()
        body = _text(review.get("body"))
        first = next((line.strip() for line in body.splitlines() if line.strip()), "")
        approve_form = (
            state == "APPROVED"
            or (state == "COMMENTED" and APPROVE_LINE.search(first) and INDEPENDENT.search(first))
        )
        if approve_form and _sha(review.get("commit_id")) not in (None, wanted):
            return "review_at_older_head"
    return "review_missing"


def checks_state(checks, head):
    """``(ok, reason)`` for the newest run per check name at ``head``.

    Facts are ordered oldest to newest; a later entry with the same name
    supersedes an earlier one. Checks attributed to another head are ignored
    (a green run at an older sha does not count). No checks at all fails
    closed.
    """
    wanted = _sha(head)
    if wanted is None:
        return False, "checks_missing"
    newest = {}
    for check in checks:
        if not isinstance(check, dict):
            continue
        check_head = _sha(check.get("head"))
        if check_head is not None and check_head != wanted:
            continue
        name = _text(check.get("name")) or "unnamed"
        newest[name] = check
    if not newest:
        return False, "checks_missing"
    failed = False
    pending = False
    for check in newest.values():
        status = _text(check.get("status")).lower()
        conclusion = _text(check.get("conclusion")).upper()
        if conclusion in RED_CONCLUSIONS:
            failed = True
        elif status != "completed" or conclusion not in GREEN_CONCLUSIONS:
            pending = True
    if failed:
        return False, "checks_failed"
    if pending:
        return False, "checks_pending"
    return True, "checks_green"


def verify_at_head(records, compare, head, files):
    """``(ok, reason, match)`` for verify evidence at ``head``.

    Exact equality is preferred. Otherwise an ``ok`` record at an ancestor sha
    passes only with blob equality on every file the PR changes: the compare's
    changed-file list must not intersect the PR file list. ``files: null``
    means the trees are identical; a 300-entry (truncated) compare is
    inconclusive and fails closed.
    """
    wanted = _sha(head)
    records = [r for r in records if isinstance(r, dict) and r.get("ok") is True]
    if wanted is not None:
        for record in records:
            if _sha(record.get("sha")) == wanted:
                return True, "verify_exact", "exact"
    pr_files = {f for f in files if isinstance(f, str) and f}
    saw_older = False
    for record in records:
        sha = _sha(record.get("sha"))
        if sha is None or sha == wanted:
            continue
        saw_older = True
        entry = compare.get(sha) if isinstance(compare, dict) else None
        if not isinstance(entry, dict):
            continue
        if _text(entry.get("status")).lower() not in ("ahead", "identical"):
            continue
        changed = entry.get("files")
        if changed is None:
            return True, "verify_blob_equal", "blob"
        if not isinstance(changed, list):
            continue
        if len(changed) >= 300:
            return False, "verify_blob_check_inconclusive", None
        if pr_files.intersection(f for f in changed if isinstance(f, str)):
            continue
        return True, "verify_blob_equal", "blob"
    if saw_older:
        return False, "verify_at_older_head", None
    return False, "verify_evidence_missing", None


def decide(facts):
    """Apply the owner-approved merge policy to one PR's measured facts."""
    facts = facts if isinstance(facts, dict) else {}
    pr = facts.get("pr") if isinstance(facts.get("pr"), dict) else {}
    head = _sha(pr.get("head"))
    verdict = {
        "merge": False,
        "class": facts.get("class"),
        "reason": None,
        "verify": None,
        "head": head,
    }

    def refuse(reason):
        verdict["reason"] = reason
        return verdict

    if facts.get("owner_held"):
        return refuse("owner_gate")
    if _text(pr.get("state")).upper() != "OPEN":
        return refuse("pr_not_open")
    if pr.get("is_draft") is True:
        return refuse("pr_draft")
    if pr.get("mergeable") is False:
        return refuse("pr_not_mergeable")
    merge_state = _text(pr.get("merge_state_status")).upper()
    if merge_state not in MERGE_STATES:
        return refuse("merge_state_" + (merge_state.lower() or "unknown"))
    if head is None:
        return refuse("head_missing")

    pull_class = facts.get("class")
    if pull_class not in (SLICE, NON_SLICE):
        return refuse("class_unknown")

    ok, reason = checks_state(facts.get("checks") or [], head)
    if not ok:
        return refuse(reason)

    if independent_review_at_head(facts.get("reviews") or [], head) is None:
        return refuse(review_refusal(facts.get("reviews") or [], head))

    if pull_class == SLICE:
        verify_facts = facts.get("verify")
        if not isinstance(verify_facts, dict):
            verify_facts = {}
        ok, reason, match = verify_at_head(
            verify_facts.get("records") or [],
            verify_facts.get("compare") or {},
            head,
            pr.get("files") or [],
        )
        if not ok:
            return refuse(reason)
        verdict["verify"] = match

    verdict["merge"] = True
    verdict["reason"] = "reviewed"
    return verdict


def main(argv=None):
    parser = argparse.ArgumentParser(description="Apply the merge-lane v5 slice/non-slice policy.")
    parser.add_argument("--facts", required=True)
    args = parser.parse_args(argv)
    try:
        with open(args.facts, encoding="utf-8") as handle:
            facts = json.load(handle)
    except (OSError, ValueError) as exc:
        print(json.dumps({"merge": False, "reason": "facts_unreadable", "error": type(exc).__name__}))
        return 2
    verdict = decide(facts)
    print(json.dumps(verdict, sort_keys=True))
    return 0 if verdict["merge"] else 1


if __name__ == "__main__":
    sys.exit(main())
