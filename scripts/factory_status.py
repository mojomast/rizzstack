#!/usr/bin/env python3
"""Factory status watchdog: one issue body from a snapshot of the seven public repos.

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
      "next_md": {"ref": "mojomast/rizzstack@main:NEXT.md", "ready_slices": ["B1"]},
      "deploys": [
        {"surface": "driver", "path": "/home/<user>/rizzstack/bundlebees",
         "head": "<40-hex>", "branch": null, "dirty": false, "dirty_files": 0,
         "running_sha": "<40-hex>" | null}
      ]
    }

Flags, evaluated against ``captured_at`` (strictly greater than the limit):

===========================  ==============================================================
``stale-green-pr``           PR open > 2h, every required check succeeded, not in the queue
``failing-required-check``   a required check concluded failure > 1h ago
``ownerless-pr``             PR open > 24h with no ``owner`` / ``owner:<name>`` label
``no-merge-with-ready-slices``  no repo merged for > 3h while NEXT.md lists ready slices
``stale-heartbeat``          coordinator heartbeat older than 45m (missing counts as stale)
``deploy-dir-hygiene``       a deploy dir is on a branch (``branch`` not null) or dirty
``deploy-sha-drift``         ``running_sha`` differs from ``head`` (a missing record drifts)
===========================  ==============================================================

The deploy flags are state flags, not age flags: they carry no ``age_seconds`` and are
reported on every run until the surface is a detached, clean checkout that the running
process actually loaded. A snapshot with no ``deploys`` key reports no deploy findings,
so an older collector cannot manufacture drift.

The rendered body is deterministic: findings sort by ``(code, repo, pull)``, ages are
durations measured from ``captured_at``, and the body carries no absolute timestamp, so
a re-run whose facts did not change produces the same bytes.
"""
import argparse
from datetime import datetime, timezone
import json
import sys

SNAPSHOT_SCHEMA = "rizzstack.factory-status.snapshot/1"
REPORT_SCHEMA = "rizzstack.factory-status.report/1"
BODY_MARKER = "<!-- factory-status: v1 -->"

FLAG_CODES = (
    "deploy-dir-hygiene",
    "deploy-sha-drift",
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
    if not isinstance(snapshot, dict) or snapshot.get("schema") != SNAPSHOT_SCHEMA:
        raise ValueError("%s: expected schema %s" % (path, SNAPSHOT_SCHEMA))
    return snapshot


def _parse_timestamp(value):
    if value is None:
        return None
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _age_seconds(captured_at, value):
    timestamp = _parse_timestamp(value)
    if timestamp is None:
        return None
    return int((captured_at - timestamp).total_seconds())


def _duration(seconds):
    if seconds is None:
        return "unknown"
    minutes = max(0, seconds) // 60
    hours, minutes = divmod(minutes, 60)
    if hours:
        return "%dh%02dm" % (hours, minutes)
    return "%dm" % minutes


def _short(sha):
    """Twelve characters of a commit id, or ``missing`` when no id was recorded."""
    if not sha:
        return "missing"
    return str(sha)[:12]


def build_report(snapshot):
    """Return the report for one snapshot: findings plus the per-repo and NEXT.md rows."""
    captured_at = _parse_timestamp(snapshot["captured_at"])
    ready_slices = list(snapshot["next_md"]["ready_slices"])
    findings = []
    repos = []
    newest_merge_age = None

    for repo in sorted(snapshot["repos"], key=lambda entry: entry["name"]):
        repo_name = repo["name"]
        merge_age = _age_seconds(captured_at, repo.get("last_merge_at"))
        if merge_age is not None and (newest_merge_age is None or merge_age < newest_merge_age):
            newest_merge_age = merge_age
        pulls = sorted(repo.get("pulls", []), key=lambda item: item["number"])
        repos.append({
            "name": repo_name,
            "last_merge_age_seconds": merge_age,
            "open_pulls": len(pulls),
        })

        for pull in pulls:
            number = pull["number"]
            opened_age = _age_seconds(captured_at, pull["opened_at"])
            labels = pull.get("labels", [])
            has_owner = any(
                label == OWNER_LABEL_PREFIX or label.startswith(OWNER_LABEL_PREFIX + ":")
                for label in labels
            )
            ownerless = opened_age > OWNERLESS_PR_HOURS * 3600 and not has_owner
            checks = pull.get("required_checks", [])

            failing_checks = []
            for check in sorted(checks, key=lambda item: item["name"]):
                check_age = _age_seconds(captured_at, check.get("started_at"))
                if (
                    check.get("conclusion") in FAILING_CONCLUSIONS
                    and check_age is not None
                    and check_age > FAILING_CHECK_HOURS * 3600
                ):
                    failing_checks.append({"name": check["name"], "age_seconds": check_age})
            if failing_checks:
                findings.append({
                    "code": "failing-required-check",
                    "repo": repo_name,
                    "pull": number,
                    "age_seconds": max(check["age_seconds"] for check in failing_checks),
                    "checks": failing_checks,
                })

            if ownerless:
                findings.append({
                    "code": "ownerless-pr",
                    "repo": repo_name,
                    "pull": number,
                    "age_seconds": opened_age,
                })
            elif (
                opened_age > STALE_GREEN_HOURS * 3600
                and not pull.get("in_merge_queue", False)
                and all(check.get("conclusion") == "success" for check in checks)
            ):
                findings.append({
                    "code": "stale-green-pr",
                    "repo": repo_name,
                    "pull": number,
                    "age_seconds": opened_age,
                })

    if ready_slices and (newest_merge_age is None or newest_merge_age > NO_MERGE_HOURS * 3600):
        findings.append({
            "code": "no-merge-with-ready-slices",
            "repo": None,
            "pull": None,
            "age_seconds": newest_merge_age,
            "ready_slices": list(ready_slices),
        })

    heartbeat_age = _age_seconds(
        captured_at, snapshot.get("coordinator", {}).get("heartbeat_at")
    )
    if heartbeat_age is None or heartbeat_age > HEARTBEAT_STALE_MINUTES * 60:
        findings.append({
            "code": "stale-heartbeat",
            "repo": None,
            "pull": None,
            "age_seconds": heartbeat_age,
        })

    deploys = []
    for deploy in sorted(snapshot.get("deploys", []), key=lambda entry: entry["surface"]):
        surface = deploy["surface"]
        head = deploy.get("head")
        branch = deploy.get("branch")
        dirty = bool(deploy.get("dirty"))
        dirty_files = int(deploy.get("dirty_files") or 0)
        running_sha = deploy.get("running_sha")
        deploys.append({
            "surface": surface,
            "path": deploy.get("path"),
            "head": head,
            "branch": branch,
            "dirty": dirty,
            "dirty_files": dirty_files,
            "running_sha": running_sha,
        })
        # Deploy dirs are artifacts: a branch or a local edit means the running code
        # is covered by no PR, no review and no test run.
        if branch is not None or dirty:
            findings.append({
                "code": "deploy-dir-hygiene",
                "repo": None,
                "pull": None,
                "age_seconds": None,
                "surface": surface,
                "path": deploy.get("path"),
                "branch": branch,
                "dirty": dirty,
                "dirty_files": dirty_files,
            })
        # A checkout that the running process never loaded is not a deploy; a missing
        # running-SHA record cannot prove the process is current, so it drifts too.
        if running_sha != head:
            findings.append({
                "code": "deploy-sha-drift",
                "repo": None,
                "pull": None,
                "age_seconds": None,
                "surface": surface,
                "path": deploy.get("path"),
                "head": head,
                "running_sha": running_sha,
            })

    findings.sort(key=lambda item: (
        item["code"], item["repo"] or "", item["pull"] if item["pull"] is not None else 0,
        item.get("surface") or "",
    ))
    return {
        "schema": REPORT_SCHEMA,
        "findings": findings,
        "ready_slices": ready_slices,
        "repos": repos,
        "deploys": deploys,
    }


def render_issue_body(report):
    """Return the Markdown issue body for a report, with no absolute timestamps."""
    lines = [BODY_MARKER, "# Factory status", ""]
    findings = report["findings"]
    if findings:
        lines.extend(["## Findings", ""])
        for finding in findings:
            code = finding["code"]
            if code.startswith("deploy-"):
                subject = finding["surface"]
            elif finding["repo"] is None:
                subject = "factory"
            else:
                subject = finding["repo"]
                if finding["pull"] is not None:
                    subject += "#%s" % finding["pull"]
            age = _duration(finding.get("age_seconds"))
            if code == "failing-required-check":
                checks = ", ".join(
                    "%s (%s)" % (check["name"], _duration(check["age_seconds"]))
                    for check in finding["checks"]
                )
                detail = "required checks failing: " + checks
            elif code == "no-merge-with-ready-slices":
                detail = "latest merge age %s; ready slices: %s" % (
                    age, ", ".join(finding["ready_slices"])
                )
            elif code == "stale-heartbeat":
                detail = "coordinator heartbeat age " + age
            elif code == "ownerless-pr":
                detail = "open for %s without an owner label" % age
            elif code == "deploy-dir-hygiene":
                reasons = []
                if finding["branch"] is not None:
                    reasons.append("on branch %s" % finding["branch"])
                if finding["dirty"]:
                    count = finding["dirty_files"]
                    reasons.append("dirty (%d changed path%s)" % (count, "" if count == 1 else "s"))
                detail = "%s: %s" % (finding["path"], " and ".join(reasons))
            elif code == "deploy-sha-drift":
                if finding["running_sha"] is None:
                    detail = "%s: checkout %s but no running SHA recorded" % (
                        finding["path"], _short(finding["head"])
                    )
                else:
                    detail = "%s: running %s but checkout %s" % (
                        finding["path"], _short(finding["running_sha"]), _short(finding["head"])
                    )
            else:
                detail = "open for %s with required checks green" % age
            lines.append("- **%s** — %s — %s" % (code, subject, detail))
        lines.append("")
    else:
        lines.extend(["All clear.", ""])

    lines.extend([
        "## Repositories",
        "",
        "| Repo | Last merge age | Open PRs |",
        "| --- | ---: | ---: |",
    ])
    for repo in sorted(report["repos"], key=lambda entry: entry["name"]):
        merge_age = repo["last_merge_age_seconds"]
        rendered_age = "never" if merge_age is None else _duration(merge_age)
        lines.append("| %s | %s | %d |" % (
            repo["name"], rendered_age, repo["open_pulls"]
        ))

    lines.extend([
        "",
        "## Deploy directories",
        "",
        "| Surface | Checkout | Ref | Dirty | Running |",
        "| --- | --- | --- | ---: | --- |",
    ])
    deploys = report.get("deploys", [])
    if deploys:
        for deploy in deploys:
            ref = "detached" if deploy["branch"] is None else deploy["branch"]
            running = _short(deploy["running_sha"])
            lines.append("| %s | %s | %s | %d | %s |" % (
                deploy["surface"], _short(deploy["head"]), ref,
                deploy["dirty_files"], running,
            ))
    else:
        lines.append("| none | — | — | 0 | — |")

    lines.extend(["", "## Ready slices", ""])
    lines.append(", ".join(report["ready_slices"]) if report["ready_slices"] else "None")
    return "\n".join(lines) + "\n"


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
