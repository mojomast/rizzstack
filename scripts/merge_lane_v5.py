#!/usr/bin/env python3
"""Merge-lane v5: fetch the facts, apply the policy, and merge one pull request.

Owner-approved policy (2026-11-25): a pull request that is **not a slice**
merges on an independent review at its exact head plus green checks; a
**slice** pull request additionally needs verify evidence at the exact head
(or blob equality for every file it changes).  The verify record is never a
substitute for the review, and the review is never a substitute for a slice's
verify evidence.

This module is the **control**, not an advisory decision function.  It:

1. fetches its own facts from the GitHub REST API -- the pull request's state,
   reviews (with submission timestamps), check runs, changed files, and the
   compare entries used for blob equality -- and reads verify records and the
   owner-held list from trusted local state (the verify-results directory and
   the campaign plan).  No caller-supplied facts file can drive a merge;
2. takes the slice/non-slice class from the **trusted gate's own output**
   (``lease-tamper: class=slice`` / ``class=non-slice``), never from a branch
   name and never from the caller;
3. runs the lease/tamper gate itself, extracting ``.github/scripts/
   lease_tamper.py`` from ``origin/main`` after a fetch and refusing unless the
   resolved ``script_sha`` equals that commit.  There is no ``--gate-ref``
   option: the pinned ref is the policy, not an operator choice;
4. measures the review carry-forward with git, using the same vendored
   predicate as the gate (see the shared policy block below);
5. performs the merge itself with ``gh pr merge --squash
   --match-head-commit <head>`` and then re-reads the pull request to prove the
   merge landed at that head.  Without ``--merge`` the lane decides and prints
   the verdict but changes nothing.

The decision core (``decide``) stays pure so every refusal is reproducible from
the facts the lane measured; ``--facts`` replays a recorded document and is
explicitly forbidden to merge.

A review at an earlier head **carries forward** only under the shared
predicate: the review commit is an ancestor of the head, every commit after it
that is not on the base is a merge whose non-first parents are on the base, and
the pull request's diff against the base is byte-identical to the reviewed diff.

Usage::

    python3 scripts/merge_lane_v5.py --repo <clean checkout> --pr <N> \\
        --repo-slug <owner/repo> [--merge]

Output (exit 0 merge, exit 1 refuse)::

    {"merge": true, "class": "non-slice", "reason": "reviewed", "verify": null, ...}
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

SLICE = "slice"
NON_SLICE = "non-slice"
MERGE_STATES = ("CLEAN", "BEHIND")

#: The gate the lane runs itself.  It is read from the trusted base revision,
#: never from the pull request's head: a PR that edits the gate must not be
#: judged by its own copy.  The ref is pinned to origin/main and cannot be
#: overridden from the command line.
GATE_SCRIPT_PATH = ".github/scripts/lease_tamper.py"
GATE_SCRIPT_REF = "origin/main"
GATE_TIMEOUT_S = 300

DEFAULT_API_URL = "https://api.github.com"
HTTP_TIMEOUT_SECONDS = 20
MAX_PAGES = 10
DEFAULT_REQUIRED_CHECKS = ("ci-gate",)
DEFAULT_VERIFY_DIR = "~/.hermes/plugin-data/bundlebees/verify-results"
DEFAULT_PLAN_PATH = "~/.hermes/plugin-data/bundlebees/campaigns/feature-batch-1/plan.json"
MERGE_LANE_ENV = "BUNDLEBEES_MERGE_LANE"

#: Base-store lease identity.  A slice verify record is bound to the frozen
#: lease on the base branch that classified the pull request: the record's
#: ``repo``/``slice``/``contract_sha256`` must equal that lease's, read from
#: the trusted base revision only.  Both the lease store and its revision are
#: pinned like the gate script; neither is configurable from the command line.
LEASE_STORE_DIR = ".rizzstack/leases"
LEASE_REF = GATE_SCRIPT_REF
LEASE_PR_NAME_KEYS = ("pull_request", "pull_requests", "pr", "prs")
LEASE_BRANCH_NAME_KEYS = ("branch", "branches")
LEASE_SLICE_NAME_KEYS = ("slice", "slice_name", "id")
LEASE_FROZEN_STATUSES = ("approved", "frozen")
SHA256 = re.compile(r"^[0-9a-f]{64}$")

SHA = re.compile(r"^[0-9a-f]{40}$")

#: The gate's final line.  The risk tier is optional so an older gate (without
#: the tier step) still parses, and a lane never derives either value itself.
_GATE_CLASS_LINE = re.compile(
    r"^lease-tamper: class=(slice|non-slice)(?: TIER=(T1|T3))?$"
)
STAMP_LINE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})$")


# --- BEGIN SHARED GATE POLICY (vendored; keep byte-identical) ---------------
# One implementation of the rules the lease/tamper gate and the merge lane both
# apply: how a review attestation is parsed, which rejection overrides an
# approval, the review carry-forward predicate, the sensitive-path list and the
# only green conclusion.  The two repositories cannot import each other (the
# gate must stay a single self-contained script), so this block is duplicated
# verbatim and the tests prove the copies are byte-identical with a sha256.
#
# Canonical attestation line (first non-empty line of the review body):
#     APPROVE (independent review) at <sha>
#     APPROVE (owner) at <sha>
#     CHANGES REQUESTED (<why>) at <sha>
# The pre-canonical forms stay accepted by the same parser:
#     APPROVE — independent review ... at <sha>
#     APPROVE (independent review at <sha>)
#     OWNER APPROVED

REVIEW_APPROVE_INDEPENDENT = "approve_independent"
REVIEW_APPROVE_OWNER = "approve_owner"
REVIEW_CHANGES_REQUESTED = "changes_requested"
REVIEW_APPROVAL_KINDS = (REVIEW_APPROVE_INDEPENDENT, REVIEW_APPROVE_OWNER)

#: How a review matches the head: at the head itself, or carried from an
#: earlier head by a base-merge-only update with an unchanged diff.
REVIEW_MATCH_EXACT = "exact"
REVIEW_MATCH_CARRIED = "carried"

#: Named outcomes of the base-merge review carry-forward.  The refusal reasons
#: are stable codes, so a lane decision and a gate log name the same condition.
CARRY_OK = "review_carry_ok"
CARRY_NOT_ANCESTOR = "review_not_ancestor"
CARRY_AUTHORED_COMMIT = "review_carry_authored_commit"
CARRY_UNRELATED_MERGE = "review_carry_unrelated_merge"
CARRY_CONTENT_CHANGED = "review_carry_content_changed"
CARRY_UNREADABLE = "review_carry_unreadable"

#: Paths whose change makes a pull request a gate/lane change: it needs an
#: explicit owner attestation in addition to the independent review, because a
#: coordinator or a bee could otherwise relax the machinery that judges it.
#: The explicit list is authoritative; the prefix and token rules below only
#: catch new files in the same places.
SENSITIVE_PATHS = frozenset((
    "bundlebees_plugin/acdd_cli.py",
    "bundlebees_plugin/approval.py",
    "bundlebees_plugin/contract.py",
    "bundlebees_plugin/return_report.py",
    "bundlebees_plugin/subversion_scan.py",
    "bundlebees_plugin/tdd.py",
    "bundlebees_plugin/test_receipts.py",
    "bundlebees_plugin/transport.py",
    "bundlebees_plugin/verify_local.py",
    "tests/test_lease_tamper.py",
    "tests/test_merge_lane_v5.py",
))
SENSITIVE_PREFIXES = (".github/", ".rizzstack/", "gate/", "gates/", "policy/")
SENSITIVE_TOKENS = frozenset(("gate", "gates", "lane", "lanes"))

#: Only SUCCESS is green.  A skipped or neutral required check proves nothing
#: about the change, so it is not evidence that a gate ran.
CHECK_GREEN_CONCLUSION = "success"
CHECK_RED_CONCLUSIONS = (
    "failure",
    "timed_out",
    "cancelled",
    "action_required",
    "startup_failure",
    "stale",
    "error",
)

_POLICY_APPROVE = re.compile(r"(?i)^APPROVE\b(?!\?)")
_POLICY_OWNER_WORD = re.compile(r"(?i)^OWNER\s+APPROVE[DS]?\b")
_POLICY_CHANGES = re.compile(r"(?i)^CHANGES[\s_-]*REQUESTED\b")
_POLICY_INDEPENDENT = re.compile(r"(?i)\bindependent\s+review\b")
_POLICY_NEGATED = re.compile(
    r"(?i)\b(?:not|no|never|without|isn'?t|is\s+not)\b[^.;:!?]{0,48}?\bindependent\s+review\b"
)
_POLICY_OWNER = re.compile(r"(?i)\bowner\b")
_POLICY_SHA = re.compile(r"(?<![0-9a-fA-F])([0-9a-fA-F]{7,64})(?![0-9a-fA-F])")
_POLICY_LEAD = re.compile(r"^[\s>#•\-\u2013\u2014]+")
_POLICY_EMPHASIS = re.compile(r"^(?:\*\*|__|`)+")
_POLICY_TRAILING_SHA = re.compile(r"(?i)\s+at\s+[0-9a-f]{7,64}\s*$")
_CARRY_HEX = re.compile(r"^[0-9a-f]{40}(?:[0-9a-f]{24})?$")


def _policy_text(value):
    return value if isinstance(value, str) else ""


def _policy_first_line(body):
    for line in _policy_text(body).splitlines():
        if line.strip():
            return line.strip()
    return ""


def _policy_strip_markup(line):
    line = _POLICY_LEAD.sub("", line)
    line = _POLICY_EMPHASIS.sub("", line)
    line = re.sub(
        r"(?i)^(APPROVE|CHANGES[\s_-]*REQUESTED|OWNER\s+APPROVE[DS]?)(?:\*\*|__|`)",
        lambda match: match.group(1),
        line,
    )
    return line.strip()


def _policy_descriptor(rest):
    """The words that qualify an APPROVE: its first parenthetical or clause."""
    rest = rest.strip()
    while rest[:1] in ("*", "_", "`", " "):
        rest = rest[1:]
    if rest.startswith("("):
        end = rest.find(")")
        return rest[1:end] if end != -1 else rest[1:]
    return _POLICY_TRAILING_SHA.sub("", rest).strip(" \t-–—:;,")


def parse_review_line(body):
    """``(kind, named_sha)`` for a review body's first non-empty line.

    ``kind`` is ``approve_independent``, ``approve_owner``,
    ``changes_requested`` or ``None``.  ``named_sha`` is the commit the line
    names after ``at`` (a 7..64-hex prefix), or ``None``.  Every review line is
    parsed here and nowhere else, so the gate and the lane cannot disagree
    about what an approval is.
    """
    line = _policy_strip_markup(_policy_first_line(body))
    if not line:
        return None, None
    named = None
    found = _POLICY_SHA.findall(line)
    if found:
        named = found[-1].lower()
    if _POLICY_CHANGES.search(line):
        return REVIEW_CHANGES_REQUESTED, named
    if _POLICY_OWNER_WORD.search(line):
        return REVIEW_APPROVE_OWNER, named
    match = _POLICY_APPROVE.search(line)
    if not match:
        return None, None
    descriptor = _policy_descriptor(line[match.end():])
    if _POLICY_INDEPENDENT.search(descriptor) and not _POLICY_NEGATED.search(descriptor):
        return REVIEW_APPROVE_INDEPENDENT, named
    if _POLICY_OWNER.search(descriptor):
        return REVIEW_APPROVE_OWNER, named
    return None, None


def review_form(review):
    """``(kind, named_sha)`` for one review, or ``(None, None)``.

    GitHub's ``APPROVED`` state is an approval even with an empty body.  A body
    that names the owner is an owner attestation only: an owner approval never
    satisfies the independent-review rule, whatever state GitHub records.
    """
    if not isinstance(review, dict):
        return None, None
    state = _policy_text(review.get("state")).strip().upper()
    kind, named = parse_review_line(review.get("body"))
    if state == "CHANGES_REQUESTED":
        return REVIEW_CHANGES_REQUESTED, named
    if state not in ("APPROVED", "COMMENTED"):
        return None, None
    if kind == REVIEW_CHANGES_REQUESTED:
        return REVIEW_CHANGES_REQUESTED, named
    if kind is not None:
        return kind, named
    if state == "APPROVED":
        return REVIEW_APPROVE_INDEPENDENT, named
    return None, None


def review_names_commit(named, commit):
    """True when the line names no commit, or a prefix of ``commit``."""
    if not named:
        return True
    commit = _policy_text(commit).strip().lower()
    return bool(commit) and commit.startswith(named.lower())


def review_is_approval(review, kind=None):
    form, named = review_form(review)
    if form not in REVIEW_APPROVAL_KINDS:
        return False
    if kind is not None and form != kind:
        return False
    return review_names_commit(named, (review or {}).get("commit_id"))


def review_is_rejection(review):
    return review_form(review)[0] == REVIEW_CHANGES_REQUESTED


def review_chronology(reviews):
    """Reviews in submission order.

    GitHub returns reviews chronologically.  When every review carries a
    ``submitted_at`` timestamp the list is sorted by it, so a facts file that
    reorders reviews cannot reorder history; otherwise list order is used.
    """
    items = [item for item in (reviews or []) if isinstance(item, dict)]
    stamps = [
        _policy_text(item.get("submitted_at") or item.get("submittedAt")).strip()
        for item in items
    ]
    if items and all(stamps):
        order = sorted(range(len(items)), key=lambda index: stamps[index])
        return [items[index] for index in order]
    return items


def governing_rejection(reviews, approval, head=None, ancestor=None):
    """The rejection submitted after ``approval`` that overrides it, or None.

    The newest decisive review governs: a rejection (state ``CHANGES_REQUESTED``
    or a first line starting ``CHANGES REQUESTED``) submitted after the approval
    refuses when it is recorded at the approval's commit, at the head, or at a
    commit that descends from the approval's commit.  A rejection the approval
    supersedes does not block, and a later approval supersedes the rejection in
    its turn.  With no ``ancestor`` callable an unrelated commit is treated as
    governing: failing closed beats ignoring a rejection whose history cannot be
    read.
    """
    ordered = review_chronology(reviews)
    index = next((position for position, item in enumerate(ordered) if item is approval), None)
    if index is None:
        return None
    approval_commit = _policy_text((approval or {}).get("commit_id")).strip().lower()
    head = _policy_text(head).strip().lower()
    for candidate in ordered[index + 1:]:
        if not review_is_rejection(candidate):
            continue
        commit = _policy_text(candidate.get("commit_id")).strip().lower()
        if not commit or commit == approval_commit or (head and commit == head):
            return candidate
        if ancestor is None:
            return candidate
        if approval_commit and ancestor(approval_commit, commit):
            return candidate
    return None


def approvals_at_head(reviews, head, kind):
    """Approvals of ``kind`` recorded at exactly ``head``, oldest first."""
    wanted = _policy_text(head).strip().lower()
    found = []
    if not wanted:
        return found
    for review in review_chronology(reviews):
        form, named = review_form(review)
        if form != kind:
            continue
        if _policy_text(review.get("commit_id")).strip().lower() != wanted:
            continue
        if not review_names_commit(named, wanted):
            continue
        found.append(review)
    return found


def newest_approval_at_head(reviews, head, kind, ancestor=None):
    """The newest approval of ``kind`` at ``head`` no rejection overrides."""
    for review in reversed(approvals_at_head(reviews, head, kind)):
        if governing_rejection(reviews, review, head, ancestor) is None:
            return review
    return None


def approval_chain(reviews, head, kind, ancestor=None):
    """Approvals that may decide ``head``, newest first.

    Each entry is ``(review, "exact"|"older")``.  An approval with a governing
    rejection after it is dropped, so the newest decisive review governs: a
    re-approval supersedes an earlier rejection, and a rejection supersedes the
    approval it follows.
    """
    wanted = _policy_text(head).strip().lower()
    chain = []
    for review in reversed(approvals_at_head(reviews, wanted, kind)):
        if governing_rejection(reviews, review, wanted, ancestor) is None:
            chain.append((review, REVIEW_MATCH_EXACT))
    seen = set()
    for review in reversed(review_chronology(reviews)):
        form, named = review_form(review)
        if form != kind:
            continue
        commit = _policy_text(review.get("commit_id")).strip().lower()
        if not commit or commit == wanted or commit in seen:
            continue
        if not review_names_commit(named, commit):
            continue
        if governing_rejection(reviews, review, wanted, ancestor) is not None:
            continue
        seen.add(commit)
        chain.append((review, REVIEW_MATCH_CARRIED))
    return chain


def rejection_at_head(reviews, head):
    """The newest rejection recorded at ``head`` (or naming no commit)."""
    wanted = _policy_text(head).strip().lower()
    for review in reversed(review_chronology(reviews)):
        if not review_is_rejection(review):
            continue
        commit = _policy_text(review.get("commit_id")).strip().lower()
        if not commit or commit == wanted:
            return review
    return None


def _policy_path_tokens(path):
    name = _policy_text(path).replace("\\", "/").rstrip("/").rsplit("/", 1)[-1].lower()
    return [token for token in re.split(r"[^a-z0-9]+", name) if token]


def sensitive_paths(files):
    """The changed paths that make a pull request a gate/lane change."""
    found = []
    if not isinstance(files, (list, tuple)):
        return found
    for item in files:
        if not isinstance(item, str) or not item.strip():
            continue
        path = item.replace("\\", "/")
        normal = path.lower()
        while normal.startswith("./"):
            normal = normal[2:]
        normal = normal.lstrip("/")
        sensitive = normal in SENSITIVE_PATHS
        if not sensitive:
            sensitive = any(
                normal == prefix.rstrip("/") or normal.startswith(prefix)
                for prefix in SENSITIVE_PREFIXES
            )
        if not sensitive:
            sensitive = bool(SENSITIVE_TOKENS.intersection(_policy_path_tokens(path)))
        if sensitive and item not in found:
            found.append(item)
    return found


def check_run_is_success(run):
    """A check run proves a gate ran only when it completed with SUCCESS."""
    if not isinstance(run, dict):
        return False
    status = _policy_text(run.get("status")).strip().lower()
    conclusion = _policy_text(run.get("conclusion")).strip().lower()
    return status in ("", "completed") and conclusion == CHECK_GREEN_CONCLUSION


def check_run_is_red(run):
    if not isinstance(run, dict):
        return False
    return _policy_text(run.get("conclusion")).strip().lower() in CHECK_RED_CONCLUSIONS


def _carry_git(repo, *args, timeout=60):
    return subprocess.run(
        ["git", "-C", str(repo), *args], capture_output=True, text=True, timeout=timeout
    )


def _carry_commit(repo, sha):
    return _carry_git(repo, "cat-file", "-e", "%s^{commit}" % sha).returncode == 0


def _carry_ancestor(repo, older, newer):
    return _carry_git(repo, "merge-base", "--is-ancestor", older, newer).returncode == 0


def _carry_merge_base(repo, base, head):
    proc = _carry_git(repo, "merge-base", base, head)
    value = proc.stdout.strip()
    return value if proc.returncode == 0 and value else base


def _carry_parents(repo, sha):
    return _carry_git(repo, "rev-list", "--parents", "-n", "1", sha).stdout.split()[1:]


def _carry_subject(repo, sha):
    return _carry_git(repo, "log", "-1", "--format=%s", sha).stdout.strip()


def carry_diff_blobs(repo, older, newer):
    """``{path: new blob sha or None}`` for one tree-to-tree diff."""
    proc = _carry_git(repo, "diff", "--raw", "-z", "--no-renames", "--no-abbrev", older, newer)
    if proc.returncode != 0:
        raise RuntimeError("git diff failed: " + (proc.stderr or "").strip()[-200:])
    fields = proc.stdout.split("\0")
    blobs = {}
    index = 0
    while index + 1 < len(fields):
        header, path = fields[index], fields[index + 1]
        index += 2
        if not header.startswith(":"):
            continue
        parts = header[1:].split()
        if len(parts) < 5:
            continue
        new_sha = parts[3]
        blobs[path] = None if set(new_sha) == {"0"} else new_sha
    return blobs


def reviewed_diff_blobs(repo, base, revision):
    """The pull request's diff at ``revision`` as ``{path: blob sha or None}``."""
    return carry_diff_blobs(repo, _carry_merge_base(repo, base, revision), revision)


def review_carry_forward(repo, review_sha, base, head, timeout=60):
    """``(ok, reason, detail)``: may a review at ``review_sha`` judge ``head``?

    Accept only when (1) the review commit is an ancestor of the head, (2)
    every commit in ``review_sha..head`` that is not already on the base is a
    merge whose non-first parents are on the base, and (3) the pull request's
    diff against the base is byte-identical at ``head`` and at ``review_sha``.
    """
    if not isinstance(review_sha, str) or not _CARRY_HEX.fullmatch(review_sha.strip().lower()):
        return False, CARRY_NOT_ANCESTOR, "the review names no full commit sha"
    review_sha = review_sha.strip().lower()
    try:
        if not _carry_commit(repo, review_sha):
            return (
                False,
                CARRY_NOT_ANCESTOR,
                "review commit %s is not in this history" % review_sha[:12],
            )
        if review_sha != head and not _carry_ancestor(repo, review_sha, head):
            return (
                False,
                CARRY_NOT_ANCESTOR,
                "review commit %s is not an ancestor of head %s; the reviewed history was "
                "rewritten away" % (review_sha[:12], head[:12]),
            )

        listed = _carry_git(repo, "rev-list", "%s..%s" % (review_sha, head), "--not", base, timeout=timeout)
        if listed.returncode != 0:
            raise RuntimeError("git rev-list failed: " + (listed.stderr or "").strip()[-200:])
        commits = [line for line in listed.stdout.splitlines() if line]
        for sha in commits:
            parents = _carry_parents(repo, sha)
            subject = _carry_subject(repo, sha)
            if len(parents) < 2:
                return (
                    False,
                    CARRY_AUTHORED_COMMIT,
                    "commit %s (\"%s\") was authored after the review; only merges of the base "
                    "may follow a review" % (sha[:12], subject),
                )
            for parent in parents[1:]:
                if not _carry_ancestor(repo, parent, base):
                    return (
                        False,
                        CARRY_UNRELATED_MERGE,
                        "merge %s (\"%s\") brings in %s, which is not on base %s; only a merge "
                        "of the base may follow a review"
                        % (sha[:12], subject, parent[:12], base[:12]),
                    )

        reviewed = reviewed_diff_blobs(repo, base, review_sha)
        current = reviewed_diff_blobs(repo, base, head)
        if reviewed != current:
            changed = sorted(
                path for path in set(reviewed) | set(current) if reviewed.get(path) != current.get(path)
            )
            detail = ", ".join(changed[:8]) + (" ..." if len(changed) > 8 else "")
            return (
                False,
                CARRY_CONTENT_CHANGED,
                "the diff against base %s is not byte-identical to the reviewed diff: %s"
                % (base[:12], detail or "<trees differ>"),
            )
    except (OSError, subprocess.SubprocessError) as exc:
        return False, CARRY_UNREADABLE, "cannot read the review history: %s" % type(exc).__name__
    except RuntimeError as exc:
        return False, CARRY_UNREADABLE, str(exc)
    return (
        True,
        CARRY_OK,
        "every commit after review %s is a merge of base %s and the diff is byte-identical"
        % (review_sha[:12], base[:12]),
    )
# --- END SHARED GATE POLICY -------------------------------------------------

#: Named outcomes of the carry records the lane measures.  A record that is
#: missing, stale or malformed fails closed.
CARRY_UNMEASURED = "review_carry_unmeasured"
CARRY_STALE = "review_carry_stale"
CARRY_INVALID = "review_carry_invalid"
CARRY_REASONS = frozenset(
    (
        CARRY_OK,
        CARRY_NOT_ANCESTOR,
        CARRY_AUTHORED_COMMIT,
        CARRY_UNRELATED_MERGE,
        CARRY_CONTENT_CHANGED,
        CARRY_UNREADABLE,
    )
)


class FetchError(RuntimeError):
    """A fact the lane measures itself could not be read (fail closed)."""


def _sha(value):
    if isinstance(value, str):
        value = value.strip().lower()
        if SHA.fullmatch(value):
            return value
    return None


def _text(value):
    return value if isinstance(value, str) else ""


def _first_line(body):
    return next((line.strip() for line in _text(body).splitlines() if line.strip()), "")


def _git(repo, *args, timeout=60):
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def _is_ancestor(repo, older, newer):
    return _git(repo, "merge-base", "--is-ancestor", older, newer).returncode == 0


def review_carry_record(carry, review_sha, head):
    """``(ok, reason)`` from the lane-measured carry records.

    The record must be measured for this exact review commit and this exact
    head.  A missing record is ``review_carry_unmeasured`` and a record for a
    different head is ``review_carry_stale``; both fail closed, so a stale or
    caller-supplied record cannot vouch for a review it did not measure.
    """
    if not isinstance(carry, dict):
        return False, CARRY_UNMEASURED
    record = carry.get(review_sha)
    if not isinstance(record, dict):
        return False, CARRY_UNMEASURED
    if _sha(record.get("head")) != _sha(head) or _sha(record.get("review_sha")) != _sha(review_sha):
        return False, CARRY_STALE
    if record.get("ok") is True:
        return True, CARRY_OK
    reason = record.get("reason")
    if isinstance(reason, str) and reason in CARRY_REASONS:
        return False, reason
    return False, CARRY_INVALID


def _governing_chain_refusal(reviews, head, carry, kind, prefix, ancestor=None):
    """``(chain, reason)``: the decisive approvals, or why none are left."""
    chain = approval_chain(reviews, head, kind, ancestor)
    if chain:
        return chain, None
    if kind == REVIEW_APPROVE_INDEPENDENT:
        if rejection_at_head(reviews, head) is not None:
            return [], "review_changes_requested"
        for candidate in reversed(review_chronology(reviews)):
            if not review_is_approval(candidate, kind):
                continue
            if governing_rejection(reviews, candidate, head, ancestor) is not None:
                return [], "review_changes_requested"
        return [], "review_missing"
    return [], prefix + "_missing"


def review_acceptance(reviews, head, carry, ancestor=None):
    """``(review, match, reason)`` for the independent-review rule.

    The exact-head approval wins.  Otherwise the newest approval at an earlier
    head is offered to the measured carry records; the first that carries wins.
    An approval a later rejection overrides is dropped before either test, so
    the newest decisive review governs.
    """
    chain, reason = _governing_chain_refusal(
        reviews, head, carry, REVIEW_APPROVE_INDEPENDENT, "review", ancestor
    )
    if reason is not None:
        return None, None, reason
    refusal = None
    for review, match in chain:
        if match == REVIEW_MATCH_EXACT:
            return review, REVIEW_MATCH_EXACT, "reviewed"
        ok, why = review_carry_record(carry, _sha(review.get("commit_id")), head)
        if ok:
            return review, REVIEW_MATCH_CARRIED, "reviewed"
        if refusal is None:
            refusal = why
    return None, None, refusal or "review_missing"


def owner_review_acceptance(reviews, head, carry, ancestor=None):
    """``(review, match, reason)`` for the owner-review rule.

    Applies the same base-merge carry-forward as the independent review, so an
    owner attestation does not have to be re-posted for a base-merge-only
    update either.
    """
    chain, reason = _governing_chain_refusal(
        reviews, head, carry, REVIEW_APPROVE_OWNER, "owner_review", ancestor
    )
    if reason is not None:
        return None, None, reason
    refusal = None
    for review, match in chain:
        if match == REVIEW_MATCH_EXACT:
            return review, REVIEW_MATCH_EXACT, "owner_reviewed"
        ok, why = review_carry_record(carry, _sha(review.get("commit_id")), head)
        if ok:
            return review, REVIEW_MATCH_CARRIED, "owner_reviewed"
        if refusal is None:
            refusal = why
    return None, None, refusal or "owner_review_missing"


def measure_review_carry(repo, review_sha, base, head, timeout=60):
    """Measure one review's carry-forward against this checkout."""
    record = {
        "review_sha": review_sha,
        "base": None,
        "head": head,
        "ok": False,
        "reason": CARRY_UNREADABLE,
        "detail": "",
    }
    try:
        resolved = _git(repo, "rev-parse", "--verify", "%s^{commit}" % (base or GATE_SCRIPT_REF))
        if resolved.returncode != 0:
            record["detail"] = "base %r is not resolvable in this checkout" % (base or GATE_SCRIPT_REF)
            return record
        record["base"] = resolved.stdout.strip()
        ok, reason, detail = review_carry_forward(
            repo, review_sha, record["base"], head, timeout=timeout
        )
    except (OSError, subprocess.SubprocessError) as exc:
        record["detail"] = type(exc).__name__
        return record
    record["ok"] = bool(ok)
    record["reason"] = reason
    record["detail"] = detail
    return record


def measure_review_carry_facts(repo, pr, reviews, timeout=60):
    """Measure every review that is not at the head, keyed by its commit."""
    head = _sha(pr.get("head"))
    base_ref = pr.get("base_ref") or GATE_SCRIPT_REF
    records = {}
    if head is None or not isinstance(reviews, (list, tuple)):
        return records
    for review in reviews:
        if not isinstance(review, dict):
            continue
        commit = _sha(review.get("commit_id"))
        if commit is None or commit == head or commit in records:
            continue
        records[commit] = measure_review_carry(repo, commit, base_ref, head, timeout=timeout)
    return records


def run_lease_tamper(repo, head, base=GATE_SCRIPT_REF, number=None, branch=None,
                     repo_slug=None, script_ref=GATE_SCRIPT_REF, timeout=GATE_TIMEOUT_S,
                     env=None, fetch=None, allow_unpinned=False):
    """Run the **pinned base revision's** lease/tamper gate against ``head``.

    The script is always extracted from ``origin/main`` after a fetch, executed
    from a temporary file, and refused unless the resolved ``script_sha``
    equals ``origin/main``: a pull request that edits the gate is still judged
    by the trusted copy, and an operator cannot point the lane at another ref.
    ``allow_unpinned`` exists for tests only.

    ``ok`` is true only when the gate exits 0.  Any failure to obtain or run
    the script is a refusal, never "green by absence".
    """
    record = {
        "ran": False,
        "ok": False,
        "reason": None,
        "script_ref": script_ref,
        "script_sha": None,
        "script_blob": None,
        "base": None,
        "head": head,
        "exit_code": None,
        "output_tail": "",
    }

    def failure(reason, detail=""):
        record["reason"] = reason
        if detail:
            record["output_tail"] = str(detail)[-4000:]
        return record

    if script_ref != GATE_SCRIPT_REF and not allow_unpinned:
        return failure(
            "gate_ref_not_pinned",
            "the gate ref is pinned to %s; %r is not accepted" % (GATE_SCRIPT_REF, script_ref),
        )
    if fetch is None:
        try:
            fetch = _git(repo, "remote", "get-url", "origin").returncode == 0
        except (OSError, subprocess.SubprocessError):
            fetch = False
    if fetch:
        try:
            fetched = _git(repo, "fetch", "--quiet", "origin", "main")
        except (OSError, subprocess.SubprocessError) as exc:
            return failure("gate_fetch_failed", type(exc).__name__)
        if fetched.returncode != 0:
            return failure("gate_fetch_failed", fetched.stderr)
    try:
        pinned = _git(repo, "rev-parse", "--verify", "%s^{commit}" % GATE_SCRIPT_REF)
    except (OSError, subprocess.SubprocessError) as exc:
        return failure("gate_ref_unresolved", type(exc).__name__)
    if pinned.returncode != 0:
        return failure("gate_ref_unresolved", pinned.stderr)
    pinned_sha = pinned.stdout.strip()

    try:
        resolved = _git(repo, "rev-parse", "--verify", "%s^{commit}" % script_ref)
    except (OSError, subprocess.SubprocessError) as exc:
        return failure("gate_ref_unresolved", type(exc).__name__)
    if resolved.returncode != 0:
        return failure("gate_ref_unresolved", resolved.stderr)
    script_sha = resolved.stdout.strip()
    record["script_sha"] = script_sha
    if script_sha != pinned_sha:
        return failure(
            "gate_ref_not_pinned",
            "resolved %s to %s but %s is %s" % (script_ref, script_sha[:12], GATE_SCRIPT_REF, pinned_sha[:12]),
        )
    try:
        shown = _git(repo, "show", "%s:%s" % (script_sha, GATE_SCRIPT_PATH))
    except (OSError, subprocess.SubprocessError) as exc:
        return failure("gate_script_unavailable", type(exc).__name__)
    if shown.returncode != 0:
        return failure("gate_script_unavailable", shown.stderr)
    blob = _git(repo, "rev-parse", "%s:%s" % (script_sha, GATE_SCRIPT_PATH))
    record["script_blob"] = blob.stdout.strip() if blob.returncode == 0 else None
    try:
        base_proc = _git(repo, "rev-parse", "--verify", "%s^{commit}" % (base or GATE_SCRIPT_REF))
    except (OSError, subprocess.SubprocessError) as exc:
        return failure("gate_base_unresolved", type(exc).__name__)
    if base_proc.returncode != 0:
        return failure("gate_base_unresolved", base_proc.stderr)
    base_sha = base_proc.stdout.strip()
    record["base"] = base_sha

    handle = None
    try:
        handle = tempfile.NamedTemporaryFile("w", suffix=".py", delete=False, encoding="utf-8")
        handle.write(shown.stdout)
        handle.close()
        command = [sys.executable, handle.name, "--repo", repo,
                   "--base", base_sha, "--head", head]
        if number is not None:
            command += ["--pull-request", str(number)]
        if branch:
            command += ["--branch", str(branch)]
        run_env = dict(os.environ if env is None else env)
        if repo_slug:
            run_env["GITHUB_REPOSITORY"] = repo_slug
        proc = subprocess.run(
            command, capture_output=True, text=True, timeout=timeout, env=run_env)
    except subprocess.TimeoutExpired:
        return failure("gate_timeout", "lease_tamper timed out after %ss" % timeout)
    except (OSError, subprocess.SubprocessError) as exc:
        return failure("gate_run_failed", type(exc).__name__)
    finally:
        if handle is not None:
            try:
                os.unlink(handle.name)
            except OSError:
                pass

    record["ran"] = True
    record["exit_code"] = proc.returncode
    record["ok"] = proc.returncode == 0
    record["reason"] = "gate_ok" if record["ok"] else "gate_failed"
    record["output_tail"] = ((proc.stdout or "") + (proc.stderr or ""))[-4000:]
    return record


def class_from_gate(record):
    """The class the trusted gate printed, or ``None``.

    The lane never derives the class itself: a branch-name prefix is not a
    classifier, and a caller cannot supply one.
    """
    # The gate prints the class as its final stdout line, optionally followed by
    # the risk tier ("lease-tamper: class=slice TIER=T1").  Take the LAST
    # anchored line, never a substring: a changed path or contract id echoed
    # earlier in the output must not be able to name the class.
    for line in reversed(_text((record or {}).get("output_tail")).splitlines()):
        match = _GATE_CLASS_LINE.match(line.strip())
        if match:
            return match.group(1)
    return None


def tier_from_gate(record):
    """The risk tier the trusted gate printed (``T1``/``T3``), or ``None``."""
    for line in reversed(_text((record or {}).get("output_tail")).splitlines()):
        match = _GATE_CLASS_LINE.match(line.strip())
        if match and match.group(2):
            return match.group(2)
    return None


def _name_list(value):
    if value is None:
        return []
    if isinstance(value, (list, tuple)):
        return list(value)
    return [value]


def _leaf_branch(branch):
    value = _text(branch).strip()
    return value.rsplit("/", 1)[-1] if value else ""


def _lease_is_frozen(doc):
    """A base-store lease is usable only when it says it is frozen, with an approver."""
    review = doc.get("review") if isinstance(doc, dict) else None
    if not isinstance(review, dict):
        return False
    status = _text(review.get("status")).strip().lower()
    if status not in LEASE_FROZEN_STATUSES:
        return False
    return bool(_text(review.get("approved_by")).strip())


def _lease_names_target(doc, number, branch):
    """True when a lease names this pull request, its branch or its slice.

    This mirrors the gate's own naming rule: only an explicit field counts, and
    the shape of the branch name never does.
    """
    if not isinstance(doc, dict):
        return False
    branch = _text(branch).strip()
    leaf = _leaf_branch(branch)
    for key in LEASE_PR_NAME_KEYS:
        for item in _name_list(doc.get(key)):
            if isinstance(item, bool):
                continue
            if isinstance(item, int) and number is not None and item == number:
                return True
            if isinstance(item, str):
                text = item.strip().lstrip("#")
                if text.isdigit() and number is not None and int(text) == number:
                    return True
    for key in LEASE_BRANCH_NAME_KEYS:
        for item in _name_list(doc.get(key)):
            if isinstance(item, str) and branch and item.strip() == branch:
                return True
    for key in LEASE_SLICE_NAME_KEYS:
        value = doc.get(key)
        if not isinstance(value, str) or not value.strip():
            continue
        name = value.strip().lower()
        if branch and name == branch.lower():
            return True
        if leaf and name == leaf.lower():
            return True
    return False


def _lease_paths(repo, revision):
    proc = _git(repo, "ls-tree", "-r", "--name-only", revision, "--", LEASE_STORE_DIR)
    if proc.returncode != 0:
        return []
    return [
        line.strip()
        for line in (proc.stdout or "").splitlines()
        if line.strip().endswith(".json")
    ]


def _lease_at(repo, revision, path):
    proc = _git(repo, "show", "%s:%s" % (revision, path))
    if proc.returncode != 0:
        return None
    try:
        return json.loads(proc.stdout)
    except ValueError:
        return None


def base_lease_provenance(repo, repo_slug, number, branch, revision=LEASE_REF):
    """``(provenance, reason)`` for the frozen base lease that names this PR.

    The first frozen lease that names the pull request wins, exactly as in the
    gate's own ``_named_lease_at``, so the lane and the gate cannot disagree
    about which lease classified the pull request.  Only ``revision`` (the
    pinned base ref) is read; a lease written on the pull request's own head is
    never provenance.  Fail-closed: a matching lease without a slice id or a
    64-hex ``contract_sha256`` is invalid, not "unknown".
    """
    provenance = {
        "repo": _text(repo_slug).strip().lower(),
        "pr": int(number) if number else None,
    }
    if not provenance["repo"] or provenance["pr"] is None:
        return None, "lease_provenance_invalid"
    for path in _lease_paths(repo, revision):
        doc = _lease_at(repo, revision, path)
        if not isinstance(doc, dict) or not _lease_is_frozen(doc):
            continue
        if not _lease_names_target(doc, provenance["pr"], branch):
            continue
        slice_id = _text(doc.get("slice") or doc.get("slice_name") or doc.get("id")).strip()
        contract = _text(doc.get("contract_sha256")).strip().lower()
        if not slice_id or not SHA256.fullmatch(contract):
            return None, "lease_provenance_invalid"
        provenance["slice"] = slice_id
        provenance["contract_sha256"] = contract
        provenance["lease"] = path
        return provenance, "lease_provenance_ok"
    return None, "lease_provenance_missing"


def _check_stamp(check):
    for key in ("completed_at", "completedAt", "started_at", "startedAt"):
        value = check.get(key)
        if isinstance(value, str) and STAMP_LINE.fullmatch(value.strip()):
            return value.strip()
    return None


def _newest_check(entries):
    """The single newest entry, or ``(None, 'checks_ambiguous')``.

    Two entries for one name must be ordered by distinct, valid ISO-8601
    timestamps.  Missing or equal timestamps are ambiguous and fail closed;
    list order is never used to break a tie.
    """
    if len(entries) == 1:
        return entries[0], None
    stamps = [_check_stamp(entry) for entry in entries]
    if any(stamp is None for stamp in stamps) or len(set(stamps)) != len(stamps):
        return None, "checks_ambiguous"
    valid = [stamp for stamp in stamps if stamp is not None]
    return entries[stamps.index(max(valid))], None


def checks_state(checks, head, required_checks):
    """``(ok, reason)`` for the newest run per check name at ``head``.

    Every check must name a check and carry the exact head it ran against; an
    unnamed or unattributed entry is not evidence.  Duplicate names must be
    ordered by distinct valid timestamps (no list-order fallback).  Every name
    in ``required_checks`` must be present, and a required check is green only
    when it completed with SUCCESS: SKIPPED and NEUTRAL prove nothing.
    """
    wanted = _sha(head)
    if wanted is None:
        return False, "checks_missing"
    if not isinstance(checks, (list, tuple)):
        return False, "checks_invalid"
    if (not isinstance(required_checks, (list, tuple)) or not required_checks
            or any(not isinstance(name, str) or not name.strip() for name in required_checks)):
        return False, "required_checks_missing"
    by_name = {}
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
        by_name.setdefault(name, []).append(check)
    if not by_name:
        return False, "checks_missing"
    newest = {}
    for name, entries in by_name.items():
        chosen, reason = _newest_check(entries)
        if chosen is None:
            return False, reason
        newest[name] = chosen
    missing = [name.strip() for name in required_checks if name.strip() not in newest]
    if missing:
        return False, "checks_missing"
    failed = False
    pending = False
    not_success = False
    for check in newest.values():
        status = _text(check.get("status")).lower()
        conclusion = _text(check.get("conclusion")).upper()
        if conclusion in CHECK_RED_CONCLUSIONS or check_run_is_red(check):
            failed = True
        elif status != "completed":
            pending = True
        elif not check_run_is_success(check):
            not_success = True
    if failed:
        return False, "checks_failed"
    if pending:
        return False, "checks_pending"
    if not_success:
        return False, "checks_not_success"
    return True, "checks_green"


def record_provenance_matches(record, provenance):
    """True when a verify record was written for exactly this slice/contract.

    Every field is required: a record without provenance is not evidence for
    any pull request, and one written for another repository, another slice or
    another contract is refused even when its blob-equal sha would otherwise
    qualify.
    """
    if not isinstance(record, dict) or not isinstance(provenance, dict):
        return False
    repo = _text(record.get("repo")).strip().lower()
    slice_id = _text(record.get("slice")).strip().lower()
    contract = _text(record.get("contract_sha256")).strip().lower()
    if not repo or not slice_id or not contract:
        return False
    if repo != _text(provenance.get("repo")).strip().lower():
        return False
    if slice_id != _text(provenance.get("slice")).strip().lower():
        return False
    if contract != _text(provenance.get("contract_sha256")).strip().lower():
        return False
    number = record.get("pr")
    if number is not None:
        wanted = provenance.get("pr")
        try:
            if wanted is None or int(number) != int(wanted):
                return False
        except (TypeError, ValueError):
            return False
    return True


def verify_at_head(records, compare, head, files, provenance=None):
    """``(ok, reason, match)`` for verify evidence at ``head``.

    Only records whose ``repo``/``slice``/``contract_sha256`` equal the frozen
    base lease that classified the pull request are evidence at all: a valid
    record from another repository or another slice is refused even at a
    blob-equal sha.  Exact equality is preferred.  A record at the exact head
    that is not ``ok`` fails closed: a head the verifier rejected is not
    rescued by an older record.  Otherwise an ``ok`` record at an ancestor sha
    passes only with blob equality on every file the PR changes: the compare's
    changed-file list must not intersect the PR file list.  ``files: null``
    means the trees are identical; a 300-entry (truncated) compare is
    inconclusive, and a later candidate that proves equality still wins over it.
    """
    if not isinstance(provenance, dict) or not _text(provenance.get("slice")).strip():
        return False, "verify_provenance_missing", None
    wanted = _sha(head)
    if not isinstance(records, (list, tuple)):
        return False, "verify_evidence_missing", None
    seen = [r for r in records if isinstance(r, dict)]
    records = [r for r in seen if record_provenance_matches(r, provenance)]
    if seen and not records:
        return False, "verify_provenance_mismatch", None
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
        if "files" not in entry:
            inconclusive = True
            continue
        changed = entry.get("files")
        if changed is None:
            return True, "verify_blob_equal", "blob"
        if (not isinstance(changed, list)
                or any(not isinstance(name, str) or not name for name in changed)):
            inconclusive = True
            continue
        if len(changed) >= 300:
            inconclusive = True
            continue
        if pr_files.intersection(changed):
            continue
        return True, "verify_blob_equal", "blob"
    if inconclusive:
        return False, "verify_blob_check_inconclusive", None
    if saw_older:
        return False, "verify_at_older_head", None
    return False, "verify_evidence_missing", None


# ---------------------------------------------------------------------------
# GitHub REST facts (the lane measures its own inputs)
# ---------------------------------------------------------------------------


def _http_json(url, token):
    request = urllib.request.Request(
        url,
        headers={
            "Accept": "application/vnd.github+json",
            "Authorization": "Bearer " + token,
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "merge-lane-v5",
        },
    )
    with urllib.request.urlopen(request, timeout=HTTP_TIMEOUT_SECONDS) as response:
        return json.loads(response.read().decode("utf-8")), response.headers.get("Link") or ""


def _api_url(environ=None):
    source = environ if environ is not None else os.environ
    return (source.get("GITHUB_API_URL") or DEFAULT_API_URL).rstrip("/")


def _next_page(link_header):
    for part in (link_header or "").split(","):
        if 'rel="next"' in part:
            return part.split(";")[0].strip().strip("<>")
    return ""


def _get(url, token, http=None):
    try:
        return (http or _http_json)(url, token)
    except FetchError:
        raise
    except (urllib.error.URLError, OSError, ValueError) as exc:
        raise FetchError("%s: %s" % (url, exc))


def _paged(url, token, http=None):
    items = []
    for _ in range(MAX_PAGES):
        payload, link = _get(url, token, http)
        if not isinstance(payload, list):
            raise FetchError("%s did not return a list" % url)
        items.extend(item for item in payload if isinstance(item, dict))
        url = _next_page(link)
        if not url:
            return items
    raise FetchError("the paginated lookup did not terminate: %s" % url)


def fetch_pull_request(repo_slug, number, token, api_url=DEFAULT_API_URL, http=None, attempts=3):
    """The pull request object, retried while GitHub computes ``mergeable``."""
    url = "%s/repos/%s/pulls/%d" % (api_url, repo_slug, int(number))
    for attempt in range(max(1, attempts)):
        payload, _link = _get(url, token, http)
        if not isinstance(payload, dict):
            raise FetchError("%s did not return a pull request" % url)
        if payload.get("mergeable") is not None or attempt + 1 >= max(1, attempts):
            return payload
        time.sleep(1)
    raise FetchError("%s never reported mergeability" % url)


def fetch_reviews(repo_slug, number, token, api_url=DEFAULT_API_URL, http=None):
    url = "%s/repos/%s/pulls/%d/reviews?per_page=100" % (api_url, repo_slug, int(number))
    return _paged(url, token, http)


def fetch_pull_request_files(repo_slug, number, token, api_url=DEFAULT_API_URL, http=None):
    url = "%s/repos/%s/pulls/%d/files?per_page=100" % (api_url, repo_slug, int(number))
    return _paged(url, token, http)


def fetch_check_runs(repo_slug, sha, token, api_url=DEFAULT_API_URL, http=None):
    url = "%s/repos/%s/commits/%s/check-runs?filter=latest&per_page=100" % (
        api_url,
        repo_slug,
        urllib.parse.quote(sha),
    )
    runs = []
    for _ in range(MAX_PAGES):
        payload, link = _get(url, token, http)
        if not isinstance(payload, dict):
            raise FetchError("%s did not return check runs" % url)
        page = payload.get("check_runs")
        if not isinstance(page, list):
            raise FetchError("%s did not return check runs" % url)
        runs.extend(item for item in page if isinstance(item, dict))
        url = _next_page(link)
        if not url:
            return runs
    raise FetchError("the check-run lookup did not terminate")


def fetch_compare(repo_slug, older, newer, token, api_url=DEFAULT_API_URL, http=None):
    url = "%s/repos/%s/compare/%s...%s" % (
        api_url,
        repo_slug,
        urllib.parse.quote(older),
        urllib.parse.quote(newer),
    )
    payload, _link = _get(url, token, http)
    if not isinstance(payload, dict):
        raise FetchError("%s did not return a compare" % url)
    return payload


def pr_facts(pr, files):
    """The pull request facts ``decide()`` consumes, from the REST object."""
    head = _sha(((pr.get("head") or {}).get("sha")))
    base_ref = _text((pr.get("base") or {}).get("ref")).strip()
    state = _text(pr.get("state")).strip().upper()
    mergeable = pr.get("mergeable")
    merge_state = _text(pr.get("mergeable_state") or pr.get("mergeStateStatus")).strip().upper()
    return {
        "state": state,
        "is_draft": pr.get("draft") if isinstance(pr.get("draft"), bool) else None,
        "mergeable": mergeable,
        "merge_state_status": merge_state,
        "head": head or "",
        "number": int(pr.get("number") or 0),
        "branch": _text((pr.get("head") or {}).get("ref")).strip(),
        "base_ref": ("origin/" + base_ref) if base_ref else GATE_SCRIPT_REF,
        "files": [
            item.get("filename")
            for item in files or []
            if isinstance(item, dict) and item.get("filename")
        ],
    }


def review_facts(reviews):
    """The review facts ``decide()`` consumes, from the REST objects."""
    out = []
    for review in reviews or []:
        if not isinstance(review, dict):
            continue
        out.append(
            {
                "state": _text(review.get("state")).strip().upper(),
                "commit_id": _text(review.get("commit_id")).strip().lower(),
                "body": review.get("body") or "",
                "author": _text((review.get("user") or {}).get("login")).strip(),
                "submitted_at": _text(review.get("submitted_at")).strip(),
            }
        )
    return out


def check_facts(runs):
    """The check facts ``decide()`` consumes, from the REST check runs."""
    out = []
    for run in runs or []:
        if not isinstance(run, dict):
            continue
        out.append(
            {
                "name": _text(run.get("name")).strip(),
                "status": _text(run.get("status")).strip(),
                "conclusion": _text(run.get("conclusion")).strip(),
                "head": _text(run.get("head_sha")).strip().lower(),
                "completed_at": _text(run.get("completed_at") or run.get("started_at")).strip(),
            }
        )
    return out


def load_verify_records(directory):
    """Verify records from the lane's trusted local store, or ``[]``.

    Each record's provenance fields are carried through verbatim; a record
    without them stays in the list but can never satisfy a slice, because
    ``record_provenance_matches`` requires all three.
    """
    records = []
    root = Path(os.path.expanduser(str(directory)))
    if not root.is_dir():
        return records
    for path in sorted(root.glob("*.json")):
        try:
            document = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if not isinstance(document, dict):
            continue
        sha = _sha(document.get("sha"))
        ok = document.get("ok")
        if sha is None or not isinstance(ok, bool):
            continue
        entry = {"record": str(path), "sha": sha, "ok": ok}
        repo = _text(document.get("repo")).strip().lower()
        slice_id = _text(document.get("slice")).strip()
        contract = _text(document.get("contract_sha256")).strip().lower()
        if repo:
            entry["repo"] = repo
        if slice_id:
            entry["slice"] = slice_id
        if contract:
            entry["contract_sha256"] = contract
        if document.get("pr") is not None:
            entry["pr"] = document.get("pr")
        records.append(entry)
    return records


def collect_compare(repo_slug, head, records, token, api_url=DEFAULT_API_URL, http=None):
    """Compare entries for every distinct ok record sha that is not the head."""
    compare = {}
    for record in records:
        sha = record.get("sha")
        if record.get("ok") is not True or sha in (None, head) or sha in compare:
            continue
        document = fetch_compare(repo_slug, sha, head, token, api_url, http)
        entry = {"status": _text(document.get("status")).strip().lower()}
        # A compare with no ``files`` key is inconclusive, not identical: only
        # GitHub's explicit ``files: null`` proves the trees are identical.
        if "files" in document:
            files = document.get("files")
            if isinstance(files, list):
                entry["files"] = [
                    item.get("filename")
                    for item in files
                    if isinstance(item, dict) and item.get("filename")
                ]
            elif files is None:
                entry["files"] = None
        compare[sha] = entry
    return compare


def owner_held_from_labels(labels):
    for label in labels or []:
        name = label.get("name") if isinstance(label, dict) else label
        if _text(name).strip().lower() == "owner-held":
            return True
    return False


def owner_held_from_plan(plan_path, repo_slug, number):
    """``True``/``False`` from the campaign plan, or ``None`` when unreadable."""
    if not plan_path:
        return None
    path = Path(os.path.expanduser(str(plan_path)))
    if not path.is_file():
        return None
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(document, dict):
        return None
    held = document.get("owner_held")
    if held is None:
        return None
    if not isinstance(held, dict):
        return None
    prs = held.get("prs")
    if prs is None:
        return False
    if not isinstance(prs, list):
        return None
    key = "%s#%s" % (_text(repo_slug).strip().lower(), int(number or 0))
    return key in [_text(item).strip().lower() for item in prs]


def owner_held_facts(labels, plan_path, repo_slug, number):
    if owner_held_from_labels(labels):
        return True
    return owner_held_from_plan(plan_path, repo_slug, number)


def required_checks_from_rulesets(repo_slug, root=None, repo=None):
    """The ruleset's required check names, read from the trusted base revision.

    ``policy/rulesets/<repo>.json`` is read with ``git show origin/main:...``
    from the lane's checkout, never from the working tree: otherwise a pull
    request that edits the ruleset file in the lane's own checkout could lower
    the required checks that judge it.  A missing file on ``origin/main`` falls
    back to the standard ``ci-gate`` and is never read from the head.
    """
    name = _text(repo_slug).split("/")[-1].strip()
    if not name:
        return tuple(DEFAULT_REQUIRED_CHECKS)
    relative = "policy/rulesets/%s.json" % name
    raw = None
    if repo is not None:
        shown = _git(repo, "show", "%s:%s" % (GATE_SCRIPT_REF, relative))
        raw = shown.stdout if shown.returncode == 0 else None
    else:
        base = Path(root) if root is not None else Path(__file__).resolve().parents[1]
        path = base / relative
        if path.is_file():
            try:
                raw = path.read_text(encoding="utf-8")
            except OSError:
                raw = None
    if raw is None:
        return tuple(DEFAULT_REQUIRED_CHECKS)
    try:
        document = json.loads(raw)
    except ValueError:
        return tuple(DEFAULT_REQUIRED_CHECKS)
    contexts = []
    for rule in (document.get("rules") if isinstance(document, dict) else None) or []:
        if not isinstance(rule, dict) or rule.get("type") != "required_status_checks":
            continue
        parameters = rule.get("parameters") if isinstance(rule.get("parameters"), dict) else {}
        for check in parameters.get("required_status_checks") or []:
            if isinstance(check, dict) and _text(check.get("context")).strip():
                contexts.append(_text(check.get("context")).strip())
    return tuple(contexts) if contexts else tuple(DEFAULT_REQUIRED_CHECKS)


def collect_facts(repo_slug, number, repo, token, api_url=DEFAULT_API_URL, http=None,
                  pr=None, gate_record=None, verify_dir=DEFAULT_VERIFY_DIR,
                  plan_path=DEFAULT_PLAN_PATH, required_checks=None):
    """Every fact the decision core uses, measured by the lane itself."""
    if not token:
        raise FetchError("GITHUB_TOKEN/GH_TOKEN is required to read the pull request")
    pr = pr if isinstance(pr, dict) else fetch_pull_request(repo_slug, number, token, api_url, http)
    head = _sha(((pr.get("head") or {}).get("sha")))
    if head is None:
        raise FetchError("pull request #%s has no head sha" % number)
    reviews = fetch_reviews(repo_slug, number, token, api_url, http)
    files = fetch_pull_request_files(repo_slug, number, token, api_url, http)
    runs = fetch_check_runs(repo_slug, head, token, api_url, http)
    records = load_verify_records(verify_dir)
    compare = collect_compare(repo_slug, head, records, token, api_url, http)
    pull_class = class_from_gate(gate_record or {})
    pr_state = pr_facts(pr, files)
    provenance = None
    provenance_reason = None
    if pull_class == SLICE:
        provenance, provenance_reason = base_lease_provenance(
            repo,
            repo_slug,
            pr_state["number"],
            pr_state["branch"],
            pr_state["base_ref"] or LEASE_REF,
        )
    facts = {
        "pr": pr_state,
        "reviews": review_facts(reviews),
        "checks": check_facts(runs),
        "required_checks": list(
            required_checks if required_checks is not None
            else required_checks_from_rulesets(repo_slug, repo=repo)
        ),
        "verify": {
            "records": records,
            "compare": compare,
            "provenance": provenance,
            "provenance_reason": provenance_reason,
        },
        "owner_held": owner_held_facts(pr.get("labels"), plan_path, repo_slug, number),
        "lease_tamper": gate_record,
        "class": pull_class,
    }
    return facts


# ---------------------------------------------------------------------------
# the merge
# ---------------------------------------------------------------------------


def merge_command(repo_slug, number, head):
    """The only merge invocation: squash at the exact reviewed head."""
    return [
        "gh",
        "pr",
        "merge",
        str(int(number)),
        "--repo",
        repo_slug,
        "--squash",
        "--match-head-commit",
        head,
    ]


def merge_pull_request(repo_slug, number, head, run=None, timeout=GATE_TIMEOUT_S):
    """Merge with ``--match-head-commit`` and return a JSON-able record."""
    record = {"merged": False, "exit_code": None, "output": "", "command": merge_command(repo_slug, number, head)}
    runner = run or subprocess.run
    env = dict(os.environ)
    env[MERGE_LANE_ENV] = "1"
    try:
        proc = runner(record["command"], capture_output=True, text=True, env=env, timeout=timeout)
    except (OSError, subprocess.SubprocessError) as exc:
        record["output"] = type(exc).__name__
        return record
    record["exit_code"] = proc.returncode
    record["output"] = ((proc.stdout or "") + (proc.stderr or ""))[-2000:]
    record["merged"] = proc.returncode == 0
    return record


def verify_merge(repo_slug, number, head, token, api_url=DEFAULT_API_URL, http=None):
    """Re-read the pull request and prove it merged at exactly ``head``."""
    pr = fetch_pull_request(repo_slug, number, token, api_url, http)
    merged = pr.get("merged") is True
    current = _sha(((pr.get("head") or {}).get("sha")))
    record = {
        "verified": bool(merged and current == head),
        "merged": merged,
        "head": current or "",
        "merge_sha": _text(pr.get("merge_commit_sha")).strip(),
    }
    if not record["verified"]:
        record["reason"] = "merge_unverified"
    return record


# ---------------------------------------------------------------------------
# the decision core
# ---------------------------------------------------------------------------


def decide(facts, ancestor=None):
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
        "sensitive_files": [],
        "lease_tamper": None,
        "review": None,
        "owner_review": None,
        "tier": None,
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

    reviews = facts.get("reviews")
    if not isinstance(reviews, (list, tuple)):
        return refuse("reviews_invalid")

    gate = facts.get("lease_tamper")
    if isinstance(gate, dict):
        verdict["lease_tamper"] = gate.get("reason")
    if not isinstance(gate, dict) or gate.get("ran") is not True:
        return refuse("lease_tamper_not_run")
    if gate.get("ok") is not True:
        return refuse("lease_tamper_failed")

    pull_class = facts.get("class")
    if pull_class not in (SLICE, NON_SLICE):
        return refuse("class_unknown")
    tier = tier_from_gate(gate)
    if tier not in ("T1", "T3"):
        tier = "T3"  # a missing or unreadable tier is never trusted for auto-merge
    verdict["tier"] = tier
    auto_t1 = pull_class == SLICE and tier == "T1"

    ok, reason = checks_state(facts.get("checks") or [], head, facts.get("required_checks"))
    if not ok:
        return refuse(reason)

    carry = facts.get("review_carry")
    review, review_match, review_reason = review_acceptance(reviews, head, carry, ancestor)
    if auto_t1:
        # A T1 slice needs no approval, but a rejection is still decisive.
        if review is None and review_reason == "review_changes_requested":
            return refuse(review_reason)
        if review is not None:
            verdict["review"] = review_match
    else:
        if review is None:
            return refuse(review_reason)
        verdict["review"] = review_match

    sensitive = sensitive_paths(files)
    verdict["sensitive_files"] = sensitive
    if sensitive:
        owner_review, owner_match, owner_reason = owner_review_acceptance(
            reviews, head, carry, ancestor
        )
        if owner_review is None:
            return refuse(owner_reason)
        verdict["owner_review"] = owner_match

    if pull_class == SLICE:
        verify_facts = facts.get("verify")
        if not isinstance(verify_facts, dict):
            verify_facts = {}
        ok, reason, match = verify_at_head(
            verify_facts.get("records") or [],
            verify_facts.get("compare") or {},
            head,
            files,
            verify_facts.get("provenance"),
        )
        if not ok:
            return refuse(reason)
        verdict["verify"] = match

    verdict["merge"] = True
    verdict["reason"] = "t1_verified" if auto_t1 else "reviewed"
    return verdict


def _no_duplicate_keys(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate key: %s" % key)
        result[key] = value
    return result


def _emit(verdict):
    print(json.dumps(verdict, sort_keys=True))


def _record(path, verdict):
    if not path:
        return
    line = {
        "at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "lane": "auto-merge-v5",
        "repo": verdict.get("repo"),
        "number": verdict.get("number"),
        "head": verdict.get("head"),
        "reason": verdict.get("reason"),
        "merge_sha": (verdict.get("merge_result") or {}).get("merge_sha"),
    }
    try:
        with open(path, "a", encoding="utf-8") as handle:
            handle.write(json.dumps(line, sort_keys=True) + "\n")
    except OSError:
        pass


def main(argv=None):
    parser = argparse.ArgumentParser(description="Run the merge-lane v5 policy for one pull request.")
    parser.add_argument("--repo", default=".", help="clean git checkout the lane measures")
    parser.add_argument("--pr", type=int, default=None, help="pull-request number to evaluate")
    parser.add_argument("--repo-slug", default=os.environ.get("GITHUB_REPOSITORY") or None,
                        help="owner/name (default: GITHUB_REPOSITORY)")
    # The verify store and the campaign plan are pinned like the gate script:
    # an operator-supplied path would let the lane read a record or a hold list
    # that the merge machinery does not own.  Tests inject through the function
    # parameters of ``collect_facts`` instead.
    parser.add_argument("--merge", action="store_true",
                        help="perform the squash merge at the measured head")
    parser.add_argument("--facts", default=None,
                        help="replay a recorded facts document (never merges)")
    parser.add_argument("--record", default=None,
                        help="append one JSON line per merged pull request")
    args = parser.parse_args(argv)

    if args.facts:
        if args.merge:
            _emit({"merge": False, "reason": "facts_merge_forbidden"})
            return 1
        try:
            if args.facts == "-":
                facts = json.load(sys.stdin, object_pairs_hook=_no_duplicate_keys)
            else:
                with open(args.facts, encoding="utf-8") as handle:
                    facts = json.load(handle, object_pairs_hook=_no_duplicate_keys)
        except (OSError, ValueError) as exc:
            _emit({"merge": False, "reason": "facts_unreadable", "error": type(exc).__name__})
            return 2
        if not isinstance(facts, dict):
            _emit({"merge": False, "reason": "facts_invalid"})
            return 1
        verdict = decide(facts)
        _emit(verdict)
        return 0 if verdict["merge"] else 1

    if not args.pr:
        _emit({"merge": False, "reason": "pr_missing"})
        return 2
    if not args.repo_slug:
        _emit({"merge": False, "reason": "repo_slug_missing"})
        return 2
    token = (os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN") or "").strip()
    if not token:
        _emit({"merge": False, "reason": "token_missing"})
        return 2
    api_url = _api_url()

    try:
        pr = fetch_pull_request(args.repo_slug, args.pr, token, api_url)
    except FetchError as exc:
        _emit({"merge": False, "reason": "facts_unavailable", "error": str(exc)[:500]})
        return 1

    head = _sha(((pr.get("head") or {}).get("sha")))
    if head is None:
        gate = {"ran": False, "ok": False, "reason": "head_missing", "head": None}
    else:
        gate = run_lease_tamper(
            args.repo,
            head,
            base=("origin/" + _text((pr.get("base") or {}).get("ref")).strip())
            if _text((pr.get("base") or {}).get("ref")).strip()
            else GATE_SCRIPT_REF,
            number=args.pr,
            branch=_text((pr.get("head") or {}).get("ref")).strip() or None,
            repo_slug=args.repo_slug,
        )

    try:
        facts = collect_facts(
            args.repo_slug,
            args.pr,
            args.repo,
            token,
            api_url=api_url,
            pr=pr,
            gate_record=gate,
        )
    except FetchError as exc:
        _emit({"merge": False, "reason": "facts_unavailable", "error": str(exc)[:500]})
        return 1

    # The carry-forward is always measured here, never supplied: a facts file
    # cannot vouch for a review the lane did not measure.
    facts["review_carry"] = measure_review_carry_facts(args.repo, facts["pr"], facts["reviews"])

    ancestor = lambda older, newer: _is_ancestor(args.repo, older, newer)
    verdict = decide(facts, ancestor)
    verdict["repo"] = args.repo_slug
    verdict["number"] = args.pr
    verdict["script_sha"] = (gate or {}).get("script_sha")
    if verdict["merge"] and args.merge:
        result = merge_pull_request(args.repo_slug, args.pr, head)
        if not result["merged"]:
            verdict["merge"] = False
            verdict["reason"] = "merge_failed"
            verdict["merge_result"] = result
        else:
            verified = verify_merge(args.repo_slug, args.pr, head, token, api_url)
            verdict["merge_result"] = {**result, **verified}
            if not verified["verified"]:
                verdict["merge"] = False
                verdict["reason"] = "merge_unverified"
            else:
                _record(args.record, verdict)
    _emit(verdict)
    return 0 if verdict["merge"] else 1


if __name__ == "__main__":
    sys.exit(main())
