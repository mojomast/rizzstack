#!/usr/bin/env python3
"""Merge-lane v5 decision core: slice and non-slice pull requests.

Owner-approved policy (2026-11-25): a pull request that is **not a slice**
merges on an independent review at its exact head plus green checks; a
**slice** pull request additionally needs verify evidence at the exact head
(or blob equality for every file it changes). The verify record is never a
substitute for the review, and the review is never a substitute for a slice's
verify evidence.

A review at an earlier head **carries forward** under the same predicate the
lease/tamper gate applies: the review commit is an ancestor of the head, every
commit after it that is not on the base is a merge whose non-first parents are
on the base, and the pull request's diff against the base is byte-identical
(same paths, same blob shas) to the reviewed diff.  Updating a branch with the
base therefore does not demand a fresh review; an authored commit, an unrelated
merge or any content change still does.  The lane measures the predicate with
git (``measure_review_carry``) and folds the records into the facts; a facts
file cannot vouch for its own carry.

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
import os
import re
import subprocess
import sys
import tempfile

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
APPROVE_LINE = re.compile(r"(?i)^\W*APPROVE\W*\(\s*independent\s+review\b")
OWNER_APPROVE_LINE = re.compile(r"(?i)^\W*(?:APPROVE\W*\(\s*owner\b|OWNER\s+APPROVE[DS]?\b)")
CHANGES_LINE = re.compile(r"(?i)^\W*CHANGES\s+REQUESTED\b")
STAMP_LINE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})$")
INDEPENDENT = re.compile(r"(?i)\bindependent\s+review\b")
SHA = re.compile(r"^[0-9a-f]{40}$")

#: The gate the lane runs itself. It is read from the trusted base revision,
#: never from the pull request's head: a PR that edits the gate must not be
#: judged by its own copy.
GATE_SCRIPT_PATH = ".github/scripts/lease_tamper.py"
GATE_SCRIPT_REF = "origin/main"
GATE_TIMEOUT_S = 300

#: Paths whose change makes a pull request a gate/lane change. They require an
#: explicit owner review in addition to the independent review, because a
#: coordinator or a bee could otherwise relax the machinery that judges it.
SENSITIVE_PREFIXES = (".github/", ".rizzstack/", "gate/", "gates/", "policy/")
SENSITIVE_TOKENS = frozenset(("gate", "gates", "lane", "lanes"))

#: How a review matches the head: at the head itself, or carried from an
#: earlier head by a base-merge-only update with an unchanged diff.
REVIEW_MATCH_EXACT = "exact"
REVIEW_MATCH_CARRIED = "carried"

#: Named outcomes of the base-merge review carry-forward. The refusal reasons
#: are stable codes shared with the lease/tamper gate, so a lane decision and a
#: gate log name the same condition.
CARRY_OK = "review_carry_ok"
CARRY_NOT_ANCESTOR = "review_not_ancestor"
CARRY_AUTHORED_COMMIT = "review_carry_authored_commit"
CARRY_UNRELATED_MERGE = "review_carry_unrelated_merge"
CARRY_CONTENT_CHANGED = "review_carry_content_changed"
CARRY_UNREADABLE = "review_carry_unreadable"
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


def _head_changes_requested(reviews, head):
    """A ``CHANGES_REQUESTED`` review at the head always blocks, in any form."""
    wanted = _sha(head)
    if wanted is None:
        return False
    for review in reviews or []:
        if not isinstance(review, dict) or _sha(review.get("commit_id")) != wanted:
            continue
        if _text(review.get("state")).upper() == "CHANGES_REQUESTED":
            return True
        if CHANGES_LINE.search(_first_line(review.get("body"))):
            return True
    return False


def _independent_form(review):
    """GitHub ``APPROVED``, or the ``APPROVE (independent review ...)`` form."""
    state = _text(review.get("state")).upper()
    if state == "APPROVED":
        return True
    if state != "COMMENTED":
        return False
    first = _first_line(review.get("body"))
    return bool(APPROVE_LINE.search(first) and INDEPENDENT.search(first))


def _owner_form(review):
    """The textual owner attestation: ``APPROVE (owner ...)``/``OWNER APPROVED``."""
    if _text(review.get("state")).upper() not in ("APPROVED", "COMMENTED"):
        return False
    return bool(OWNER_APPROVE_LINE.search(_first_line(review.get("body"))))


def independent_review_at_head(reviews, head):
    """Return the review that qualifies as an independent approval at ``head``.

    A qualifying review is either GitHub state ``APPROVED`` or a ``COMMENTED``
    review whose first non-empty line starts ``APPROVE`` (not ``APPROVED`` and
    not a question such as ``APPROVE?``) and names an ``independent review``.
    Both must carry ``commit_id`` equal to ``head``; a review at an older head
    is handled by ``review_acceptance`` (base-merge carry-forward), never here.
    A ``CHANGES_REQUESTED`` review at the head -- the formal state or the
    textual ``CHANGES REQUESTED`` convention -- always blocks, even when
    another review approves.
    """
    wanted = _sha(head)
    if wanted is None or _head_changes_requested(reviews, head):
        return None
    for review in reviews or []:
        if not isinstance(review, dict) or _sha(review.get("commit_id")) != wanted:
            continue
        if _independent_form(review):
            return review
    return None


def review_carry_record(carry, review_sha, head):
    """``(ok, reason)`` from the lane-measured carry records.

    The record must be measured for this exact review commit and this exact
    head. A missing record is ``review_carry_unmeasured`` and a record for a
    different head is ``review_carry_stale``; both fail closed, so a stale or
    caller-supplied facts file cannot vouch for a review it did not measure.
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


def review_acceptance(reviews, head, carry):
    """``(review, match, reason)`` for the independent-review rule.

    The exact-head approval wins. Otherwise the newest approval at an earlier
    head is offered to the measured carry records; the first that carries
    wins. The refusal names the newest rejected candidate's condition.
    """
    exact = independent_review_at_head(reviews, head)
    if exact is not None:
        return exact, REVIEW_MATCH_EXACT, "reviewed"
    if _head_changes_requested(reviews, head):
        return None, None, "review_changes_requested"
    refusal = None
    for review in reversed([r for r in reviews or [] if isinstance(r, dict)]):
        commit = _sha(review.get("commit_id"))
        if commit is None or commit == _sha(head) or not _independent_form(review):
            continue
        ok, reason = review_carry_record(carry, commit, head)
        if ok:
            return review, REVIEW_MATCH_CARRIED, "reviewed"
        if refusal is None:
            refusal = reason
    return None, None, refusal or "review_missing"


def owner_review_at_head(reviews, head):
    """Return the owner-attested approval at ``head``, or ``None``.

    A gate/lane change needs an owner decision that is separate from the
    independent code review. On a single-maintainer account the GitHub login
    cannot prove who typed the review, so the owner attestation is textual:
    the first non-empty line starts ``APPROVE (owner`` or ``OWNER APPROVED``.
    The review must carry ``commit_id`` equal to the head; a review at an
    earlier head is handled by ``owner_review_acceptance``. A
    ``CHANGES_REQUESTED`` review at the head always blocks.
    """
    wanted = _sha(head)
    if wanted is None or _head_changes_requested(reviews, head):
        return None
    for review in reviews or []:
        if not isinstance(review, dict) or _sha(review.get("commit_id")) != wanted:
            continue
        if _owner_form(review):
            return review
    return None


def owner_review_acceptance(reviews, head, carry):
    """``(review, match, reason)`` for the owner-review rule.

    Applies the same base-merge carry-forward as the independent review: an
    owner attestation at an earlier head counts only under the measured
    predicate, so an owner decision does not have to be re-posted for a
    base-merge-only update either.
    """
    exact = owner_review_at_head(reviews, head)
    if exact is not None:
        return exact, REVIEW_MATCH_EXACT, "owner_reviewed"
    if _head_changes_requested(reviews, head):
        return None, None, "owner_review_missing"
    refusal = None
    for review in reversed([r for r in reviews or [] if isinstance(r, dict)]):
        commit = _sha(review.get("commit_id"))
        if commit is None or commit == _sha(head) or not _owner_form(review):
            continue
        ok, reason = review_carry_record(carry, commit, head)
        if ok:
            return review, REVIEW_MATCH_CARRIED, "owner_reviewed"
        if refusal is None:
            refusal = reason
    return None, None, refusal or "owner_review_missing"


def _path_tokens(path):
    name = str(path or "").replace("\\", "/").rstrip("/").rsplit("/", 1)[-1].lower()
    return [token for token in re.split(r"[^a-z0-9]+", name) if token]


def sensitive_paths(files):
    """The changed paths that make this a gate/lane change.

    A path is sensitive when it lives under ``.github/`` (workflows and gate
    scripts), ``.rizzstack/`` (contracts and leases), ``gate/``/``gates/`` or
    ``policy/``, or when its file name contains the word ``gate`` or ``lane``
    (``merge_lane_v5.py``, ``check_ci_gate.py``). Word tokens are used so a
    name such as ``delegate.py`` is not mistaken for gate code.
    """
    found = []
    if not isinstance(files, (list, tuple)):
        return found
    for item in files:
        if not isinstance(item, str) or not item.strip():
            continue
        path = item.replace("\\", "/")
        lowered = path.lower()
        while lowered.startswith("./"):
            lowered = lowered[2:]
        sensitive = any(
            lowered == prefix.rstrip("/") or lowered.startswith(prefix)
            for prefix in SENSITIVE_PREFIXES
        )
        if not sensitive:
            sensitive = bool(SENSITIVE_TOKENS.intersection(_path_tokens(path)))
        if sensitive and item not in found:
            found.append(item)
    return found


def _git(repo, *args, timeout=60):
    return subprocess.run(
        ["git", "-C", repo, *args],
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def _commit_exists(repo, sha):
    return _git(repo, "cat-file", "-e", "%s^{commit}" % sha).returncode == 0


def _is_ancestor(repo, older, newer):
    return _git(repo, "merge-base", "--is-ancestor", older, newer).returncode == 0


def _merge_base(repo, base, head):
    """The common ancestor of ``base`` and ``head`` (``base`` if git cannot say)."""
    proc = _git(repo, "merge-base", base, head)
    value = proc.stdout.strip()
    return value if proc.returncode == 0 and value else base


def _parents(repo, sha):
    return _git(repo, "rev-list", "--parents", "-n", "1", sha).stdout.split()[1:]


def _subject(repo, sha):
    return _git(repo, "log", "-1", "--format=%s", sha).stdout.strip()


def _diff_blobs(repo, older, newer):
    """``{path: new blob sha or None}`` for one tree-to-tree diff."""
    proc = _git(repo, "diff", "--raw", "-z", "--no-renames", "--no-abbrev", older, newer)
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
    return _diff_blobs(repo, _merge_base(repo, base, revision), revision)


def review_carry_forward(repo, review_sha, base, head, timeout=60):
    """``(ok, reason, detail)``: may a review at ``review_sha`` judge ``head``?

    The same predicate as the lease/tamper gate (workflow):

    1. ``review_sha`` is an ancestor of ``head`` (a rewritten-away review
       judged different code and never carries);
    2. every commit in ``review_sha..head`` that is not already on the base is
       a merge commit whose non-first parents are on the base;
    3. the pull request's diff against the base is byte-identical at ``head``
       and at ``review_sha`` (same paths, same blob shas).

    Anything else fails closed with a named reason.
    """
    if not isinstance(review_sha, str) or not SHA.fullmatch(review_sha.strip().lower()):
        return False, CARRY_NOT_ANCESTOR, "the review names no full commit sha"
    review_sha = review_sha.strip().lower()
    try:
        if not _commit_exists(repo, review_sha):
            return (
                False,
                CARRY_NOT_ANCESTOR,
                "review commit %s is not in this history" % review_sha[:12],
            )
        if review_sha != head and not _is_ancestor(repo, review_sha, head):
            return (
                False,
                CARRY_NOT_ANCESTOR,
                "review commit %s is not an ancestor of head %s; the reviewed history was "
                "rewritten away" % (review_sha[:12], head[:12]),
            )

        listed = _git(
            repo, "rev-list", "%s..%s" % (review_sha, head), "--not", base, timeout=timeout
        )
        if listed.returncode != 0:
            raise RuntimeError("git rev-list failed: " + (listed.stderr or "").strip()[-200:])
        commits = [line for line in listed.stdout.splitlines() if line]
        for sha in commits:
            parents = _parents(repo, sha)
            subject = _subject(repo, sha)
            if len(parents) < 2:
                return (
                    False,
                    CARRY_AUTHORED_COMMIT,
                    "commit %s (\"%s\") was authored after the review; only merges of the base "
                    "may follow a review" % (sha[:12], subject),
                )
            for parent in parents[1:]:
                if not _is_ancestor(repo, parent, base):
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


def measure_review_carry(repo, review_sha, base, head, timeout=60):
    """Measure one review's carry-forward against this checkout.

    ``base`` is a ref or sha (the lane uses the pull request's ``base_ref``).
    The record names the review commit, the resolved base and the head it was
    measured for, so ``decide()`` can refuse a record measured for another
    head.
    """
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
    """Measure every review that is not at the head, keyed by its commit.

    ``main()`` folds this into the facts. Only reviews whose ``commit_id`` is a
    full sha and differs from the head are measured; the head review needs no
    carry. Records are keyed by the review commit so ``review_carry_record``
    can demand the exact one it evaluates.
    """
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
                     env=None, fetch=None):
    """Run the **base revision's** lease/tamper gate against ``head``.

    The script is always extracted from ``script_ref`` (``origin/main`` in the
    lane) and executed from a temporary file: a pull request that edits
    ``.github/scripts/lease_tamper.py`` is still judged by the trusted copy.
    ``ok`` is true only when the gate exits 0. Any failure to obtain or run the
    script is a refusal, never "green by absence". Returns a JSON-able record
    that is folded into the decision facts.

    ``fetch`` defaults to true when the repository has an ``origin`` remote; a
    failed refresh is ``gate_fetch_failed`` (a stale trusted copy is not proof).
    """
    record = {
        "ran": False,
        "ok": False,
        "reason": None,
        "script_ref": script_ref,
        "script_sha": None,
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
        resolved = _git(repo, "rev-parse", "--verify", "%s^{commit}" % script_ref)
    except (OSError, subprocess.SubprocessError) as exc:
        return failure("gate_ref_unresolved", type(exc).__name__)
    if resolved.returncode != 0:
        return failure("gate_ref_unresolved", resolved.stderr)
    script_sha = resolved.stdout.strip()
    record["script_sha"] = script_sha
    try:
        shown = _git(repo, "show", "%s:%s" % (script_sha, GATE_SCRIPT_PATH))
    except (OSError, subprocess.SubprocessError) as exc:
        return failure("gate_script_unavailable", type(exc).__name__)
    if shown.returncode != 0:
        return failure("gate_script_unavailable", shown.stderr)
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
    record["output_tail"] = (proc.stdout or "") + (proc.stderr or "")
    record["output_tail"] = record["output_tail"][-4000:]
    return record


def _check_stamp(check):
    for key in ("completed_at", "completedAt", "started_at", "startedAt"):
        value = check.get(key)
        if isinstance(value, str) and STAMP_LINE.fullmatch(value.strip()):
            return value.strip()
    return None


def _newest_check(entries):
    """The single newest entry, or ``(None, 'checks_ambiguous')``.

    Two entries for one name must be ordered by distinct, valid ISO-8601
    timestamps. Missing or equal timestamps are ambiguous and fail closed;
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

    Every check must name a check and carry the exact head it ran against;
    an unnamed or unattributed entry is not evidence. Duplicate names must be
    ordered by distinct valid timestamps (no list-order fallback). Every name
    in ``required_checks`` must be present and green at the head -- a missing
    required check is a refusal, never "green by absence".
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
        "sensitive_files": [],
        "lease_tamper": None,
        "review": None,
        "owner_review": None,
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

    ok, reason = checks_state(facts.get("checks") or [], head, facts.get("required_checks"))
    if not ok:
        return refuse(reason)

    carry = facts.get("review_carry")
    review, review_match, review_reason = review_acceptance(reviews, head, carry)
    if review is None:
        return refuse(review_reason)
    verdict["review"] = review_match

    sensitive = sensitive_paths(files)
    verdict["sensitive_files"] = sensitive
    if sensitive:
        owner_review, owner_match, owner_reason = owner_review_acceptance(reviews, head, carry)
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
        )
        if not ok:
            return refuse(reason)
        verdict["verify"] = match

    verdict["merge"] = True
    verdict["reason"] = "reviewed"
    return verdict


def _no_duplicate_keys(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate key: %s" % key)
        result[key] = value
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description="Apply the merge-lane v5 slice/non-slice policy.")
    parser.add_argument("--facts", default="-", help="facts JSON file, or '-' for stdin")
    parser.add_argument("--repo", default=".", help="git checkout the gate runs against")
    parser.add_argument("--repo-slug", default=os.environ.get("GITHUB_REPOSITORY") or None,
                        help="owner/name for the gate's review lookup (default: GITHUB_REPOSITORY)")
    parser.add_argument("--gate-ref", default=GATE_SCRIPT_REF,
                        help="base revision the lease/tamper gate is read from (default: origin/main)")
    args = parser.parse_args(argv)
    try:
        if args.facts == "-":
            facts = json.load(sys.stdin, object_pairs_hook=_no_duplicate_keys)
        else:
            with open(args.facts, encoding="utf-8") as handle:
                facts = json.load(handle, object_pairs_hook=_no_duplicate_keys)
    except (OSError, ValueError) as exc:
        print(json.dumps({"merge": False, "reason": "facts_unreadable", "error": type(exc).__name__}))
        return 2

    if not isinstance(facts, dict):
        print(json.dumps({"merge": False, "reason": "facts_invalid"}))
        return 1

    # The lane never trusts PR CI for the lease/tamper verdict: it runs the
    # trusted base revision's gate itself, against this exact head, and folds
    # the measured record into the facts. A missing record refuses.
    pr = facts.get("pr") if isinstance(facts, dict) else None
    pr = pr if isinstance(pr, dict) else {}
    head = _sha(pr.get("head"))
    if head is None:
        facts["lease_tamper"] = {
            "ran": False, "ok": False, "reason": "head_missing", "head": None,
        }
    else:
        facts["lease_tamper"] = run_lease_tamper(
            args.repo,
            head,
            base=pr.get("base_ref") or GATE_SCRIPT_REF,
            number=pr.get("number"),
            branch=pr.get("branch"),
            repo_slug=args.repo_slug,
            script_ref=args.gate_ref,
        )

    # The review carry-forward is measured too, never supplied: overwrite any
    # caller copy so a facts file cannot vouch for a review it did not measure.
    facts["review_carry"] = measure_review_carry_facts(args.repo, pr, facts.get("reviews"))

    verdict = decide(facts)
    print(json.dumps(verdict, sort_keys=True))
    return 0 if verdict["merge"] else 1


if __name__ == "__main__":
    sys.exit(main())
