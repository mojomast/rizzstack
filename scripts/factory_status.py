#!/usr/bin/env python3
"""Factory status watchdog: one issue body from a snapshot of the seven public repos.

RED stub. Only argument parsing, snapshot loading and the report/body seams exist;
``build_report`` and ``render_issue_body`` raise ``NotImplementedError`` on purpose.
The behaviour they must provide is pinned by ``tests/test_factory_status.py`` and by
``contracts/wave-bc/FACTORY-STATUS.contract.json``.

Snapshot schema (``rizzstack.factory-status.snapshot/1``)::

    {
      "schema": "rizzstack.factory-status.snapshot/1",
      "captured_at": "<ISO-8601 Z>",          # the clock every age is measured from
      "repos": [
        {"name": "mojomast/<repo>",
         "last_merge_at": "<ISO-8601 Z>" | null,
         "pulls": [{"number": 1, "title": "...", "url": "...", "opened_at": "<ISO-8601 Z>",
                    "labels": ["owner:kyle"], "in_merge_queue": false,
                    "required_checks": [{"name": "ci-gate", "status": "completed",
                                         "conclusion": "success",
                                         "started_at": "<ISO-8601 Z>",
                                         "completed_at": "<ISO-8601 Z>" | null}]}]}
      ],
      "coordinator": {"heartbeat_at": "<ISO-8601 Z>", "branch": "status/heartbeat",
                      "path": "heartbeat.json"},
      "next_md": {"ref": "mojomast/rizzstack@main:NEXT.md", "ready_slices": ["B1"]}
    }

Flags, evaluated against ``captured_at`` (strictly greater than the limit):

===========================  ==============================================================
``stale-green-pr``           PR open > 2h, every required check succeeded, not in the queue
``failing-required-check``   a required check concluded failure > 1h ago
``ownerless-pr``             PR open > 24h with no ``owner`` / ``owner:<name>`` label
``no-merge-with-ready-slices``  no repo merged for > 3h while NEXT.md lists ready slices
``stale-heartbeat``          coordinator heartbeat older than 45m (missing counts as stale)
===========================  ==============================================================

The rendered body is deterministic: findings sort by ``(code, repo, pull)``, ages are
durations measured from ``captured_at``, and the body carries no absolute timestamp, so
a re-run whose facts did not change produces the same bytes.
"""
import argparse
import json
import sys

SNAPSHOT_SCHEMA = "rizzstack.factory-status.snapshot/1"
REPORT_SCHEMA = "rizzstack.factory-status.report/1"
BODY_MARKER = "<!-- factory-status: v1 -->"

FLAG_CODES = (
    "failing-required-check",
    "no-merge-with-ready-slices",
    "ownerless-pr",
    "stale-green-pr",
    "stale-heartbeat",
)
FAILING_CONCLUSIONS = ("action_required", "cancelled", "failure", "startup_failure", "timed_out")
OWNER_LABEL_PREFIX = "owner"

STALE_GREEN_HOURS = 2
FAILING_CHECK_HOURS = 1
OWNERLESS_PR_HOURS = 24
NO_MERGE_HOURS = 3
HEARTBEAT_STALE_MINUTES = 45

DEFAULT_REPOS = (
    "mojomast/workflow",
    "mojomast/bundlebees",
    "mojomast/rizzstack",
    "mojomast/orbitdesktop",
    "mojomast/hyvv",
    "mojomast/orbit-control-plane",
    "mojomast/uirizz",
)


def load_snapshot(path):
    """Read one snapshot file and refuse anything that is not this slice's schema."""
    with open(path, encoding="utf-8") as handle:
        snapshot = json.load(handle)
    if snapshot.get("schema") != SNAPSHOT_SCHEMA:
        raise ValueError("%s: expected schema %s" % (path, SNAPSHOT_SCHEMA))
    return snapshot


def build_report(snapshot):
    """Return the report for one snapshot: findings plus the per-repo and NEXT.md rows."""
    raise NotImplementedError("build_report: implement the five flag rules")


def render_issue_body(report):
    """Return the Markdown issue body for a report, with no absolute timestamps."""
    raise NotImplementedError("render_issue_body: implement the deterministic body")


def build_parser():
    parser = argparse.ArgumentParser(
        description="Rewrite the single 'Factory status' issue from the seven public repos."
    )
    parser.add_argument("--repos", nargs="+", default=list(DEFAULT_REPOS),
                        help="owner/name for every repo the watchdog reads")
    parser.add_argument("--issue-title", default="Factory status",
                        help="title of the one issue this workflow keeps updated")
    parser.add_argument("--snapshot", help="read a snapshot file instead of the GitHub API")
    parser.add_argument("--output", help="write the body here instead of stdout")
    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)
    if not args.snapshot:
        raise NotImplementedError(
            "live collection: read %s from the API and write issue %r"
            % (", ".join(args.repos), args.issue_title)
        )
    body = render_issue_body(build_report(load_snapshot(args.snapshot)))
    if args.output:
        with open(args.output, "w", encoding="utf-8") as handle:
            handle.write(body)
    else:
        sys.stdout.write(body)
    return 0


if __name__ == "__main__":
    sys.exit(main())
