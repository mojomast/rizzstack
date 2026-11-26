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
APPROVE_LINE = re.compile(r"(?i)^\W*APPROVE(?![A-Za-z?])")
CHANGES_LINE = re.compile(r"(?i)^\W*CHANGES\s+REQUESTED\b")
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
    review whose first non-empty line starts ``APPROVE`` (not ``APPROVED`` and
    not a question such as ``APPROVE?``) and names an ``independent review``.
    Both must carry ``commit_id`` equal to ``head``; an approval at an older
    head is not an approval. A ``CHANGES_REQUESTED`` review at the head -- the
    formal state or the textual ``CHANGES REQUESTED`` convention -- always
    blocks, even when another review approves.
    """
    wanted = _sha(head)
    if wanted is None:
        return None
    head_reviews = [r for r in reviews if isinstance(r, dict) and _sha(r.get("commit_id")) == wanted]
    for review in head_reviews:
        state = _text(review.get("state")).upper()
        first = next((line.strip() for line in _text(review.get("body")).splitlines() if line.strip()), "")
        if state == "CHANGES_REQUESTED" or CHANGES_LINE.search(first):
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
    for review in reviews:
        if not isinstance(review, dict) or _sha(review.get("commit_id")) != wanted:
            continue
        first = next((line.strip() for line in _text(review.get("body")).splitlines() if line.strip()), "")
        if _text(review.get("state")).upper() == "CHANGES_REQUESTED" or CHANGES_LINE.search(first):
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


def _check_stamp(check):
    for key in ("completed_at", "completedAt", "started_at", "startedAt"):
        value = check.get(key)
        if isinstance(value, str) and value:
            return value
    return None


def _newer(candidate, incumbent):
    """True when ``candidate`` is the newer run of the same check name.

    ISO-8601 timestamps compare lexicographically. When neither entry carries
    one, the later list entry wins (the lane appends oldest to newest). A
    timestamped entry always beats an undated one, so a reversed list cannot
    resurrect a stale failure or hide a newer one.
    """
    if incumbent is None:
        return True
    left, right = _check_stamp(candidate), _check_stamp(incumbent)
    if left is not None and right is not None:
        return left >= right
    if left is not None:
        return True
    if right is not None:
        return False
    return True


def checks_state(checks, head):
    """``(ok, reason)`` for the newest run per check name at ``head``.

    Every check must name a check and carry the exact head it ran against;
    an unnamed or unattributed entry is not evidence. The newest run per name
    is selected by its ISO timestamp, falling back to list order. No checks at
    all fails closed.
    """
    wanted = _sha(head)
    if wanted is None:
        return False, "checks_missing"
    if not isinstance(checks, (list, tuple)):
        return False, "checks_invalid"
    newest = {}
    for check in checks:
        if not isinstance(check, dict):
            return False, "checks_invalid"
        name = _text(check.get("name")).strip()
        if not name:
            return False, "checks_invalid"
        check_head = _sha(check.get("head"))
        if check_head is None:
            return False, "checks_unattributed"
        if check_head != wanted:
            continue
        if _newer(check, newest.get(name)):
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

    Exact equality is preferred. A record at the exact head that is not ``ok``
    fails closed: a head the verifier rejected is not rescued by an older
    record. Otherwise an ``ok`` record at an ancestor sha passes only with
    blob equality on every file the PR changes: the compare's changed-file
    list must not intersect the PR file list. ``files: null`` means the trees
    are identical; a 300-entry (truncated) compare is inconclusive, and a
    later candidate that proves equality still wins over it.
    """
    wanted = _sha(head)
    if not isinstance(records, (list, tuple)):
        return False, "verify_evidence_missing", None
    records = [r for r in records if isinstance(r, dict)]
    if wanted is not None:
        head_records = [r for r in records if _sha(r.get("sha")) == wanted]
        if any(r.get("ok") is not True for r in head_records):
            return False, "verify_failed_at_head", None
        if any(r.get("ok") is True for r in head_records):
            return True, "verify_exact", "exact"
    ok_records = [r for r in records if r.get("ok") is True]
    if not isinstance(compare, dict):
        compare = {}
    pr_files = {f for f in files if isinstance(f, str) and f} if isinstance(files, (list, tuple)) else set()
    saw_older = False
    inconclusive = False
    for record in ok_records:
        sha = _sha(record.get("sha"))
        if sha is None or sha == wanted:
            continue
        saw_older = True
        entry = compare.get(sha)
        if not isinstance(entry, dict):
            continue
        if _text(entry.get("status")).lower() not in ("ahead", "identical"):
            continue
        changed = entry.get("files")
        if changed is None:
            return True, "verify_blob_equal", "blob"
        if not isinstance(changed, list):
            inconclusive = True
            continue
        if len(changed) >= 300:
            inconclusive = True
            continue
        if pr_files.intersection(f for f in changed if isinstance(f, str)):
            continue
        return True, "verify_blob_equal", "blob"
    if inconclusive:
        return False, "verify_blob_check_inconclusive", None
    if saw_older:
        return False, "verify_at_older_head", None
    return False, "verify_evidence_missing", None


def decide(facts):
    """Apply the owner-approved merge policy to one PR's measured facts."""
    if not isinstance(facts, dict):
        return {"merge": False, "class": None, "reason": "facts_invalid", "verify": None, "head": None}
    pr = facts.get("pr")
    if not isinstance(pr, dict):
        pr = {}
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

    if facts.get("owner_held") is not False:
        return refuse("owner_gate" if facts.get("owner_held") else "owner_gate_unknown")
    if _text(pr.get("state")).upper() != "OPEN":
        return refuse("pr_not_open")
    if pr.get("is_draft") is not False:
        return refuse("pr_draft")
    mergeable = pr.get("mergeable")
    if mergeable is not True and _text(mergeable).upper() != "MERGEABLE":
        return refuse("pr_not_mergeable")
    merge_state = _text(pr.get("merge_state_status")).upper()
    if merge_state not in MERGE_STATES:
        return refuse("merge_state_" + (merge_state.lower() or "unknown"))
    if head is None:
        return refuse("head_missing")
    files = pr.get("files")
    if not isinstance(files, (list, tuple)) or any(not isinstance(f, str) or not f for f in files):
        return refuse("files_invalid")

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
            files,
        )
        if not ok:
            return refuse(reason)
        verdict["verify"] = match

    verdict["merge"] = True
    verdict["reason"] = "reviewed"
    return verdict


def main(argv=None):
    parser = argparse.ArgumentParser(description="Apply the merge-lane v5 slice/non-slice policy.")
    parser.add_argument("--facts", default="-", help="facts JSON file, or '-' for stdin")
    args = parser.parse_args(argv)
    try:
        if args.facts == "-":
            facts = json.load(sys.stdin)
        else:
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
