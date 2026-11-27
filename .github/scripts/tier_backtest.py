#!/usr/bin/env python3
"""Back-test the risk-tier step over already-merged pull requests.

The tier decides who reviews a change, so a change to the allowlist has to be
checked against history before it lands: this reports, for every row of a
manifest, the tier the CURRENT gate would have given that pull request, plus the
reasons it would print.  It is a report, never a gate: it exits 0 as long as it
could read every row.

The manifest is a JSON list of rows::

    [{"number": 31, "title": "ci(lease): ...", "base": "<sha>", "head": "<sha>"}]

``base`` is the revision the pull request was judged against (the first parent
of its merge commit) and ``head`` is the pull request's head; both must exist in
``--repo`` (``git fetch origin +refs/pull/<n>/head:refs/remotes/pr/<n>`` brings
the heads of merged pull requests in).  The tier itself is read through the same
``risk_tier`` the gate runs, in the same checkout, so the split cannot drift from
the gate: a path with no allowlist entry has to show up as T3 here too.

Usage::

    python3 .github/scripts/tier_backtest.py --repo . --manifest rows.json
                                                 [--repo-name rizzstack] [--json]

Exit status: 0 when every row was read, 1 when a row's history is unreadable.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import lease_tamper  # noqa: E402  (the gate under test, same revision)


def classify(repo, rows, repo_name=None):
    """``[{"number", "tier", "reasons", "title"}]`` for every manifest row."""
    results = []
    for row in rows:
        tier, reasons = lease_tamper.risk_tier(
            repo, row["base"], row["head"], repo_name=repo_name
        )
        results.append(
            {
                "number": row.get("number"),
                "title": row.get("title") or "",
                "tier": tier,
                "reasons": [
                    "%s:%s" % (reason, path) if path else reason
                    for reason, path in reasons
                ],
            }
        )
    return results


def split(results):
    """``(tier_one, tier_three)`` counts, in tier order."""
    return (
        sum(1 for item in results if item["tier"] == lease_tamper.TIER_ONE),
        sum(1 for item in results if item["tier"] == lease_tamper.TIER_THREE),
    )


def main(argv=None):
    parser = argparse.ArgumentParser(description="Back-test the risk tier over merged pull requests.")
    parser.add_argument("--repo", default=".", help="checkout the rows resolve in")
    parser.add_argument("--manifest", required=True, help="JSON list of base/head rows")
    parser.add_argument("--repo-name", default=None, help="repository name the allowlist is keyed by")
    parser.add_argument("--json", action="store_true", dest="as_json")
    args = parser.parse_args(argv)

    try:
        rows = json.loads(Path(args.manifest).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        print("tier_backtest: cannot read the manifest %r: %s" % (args.manifest, exc))
        return 1
    if not isinstance(rows, list) or not rows:
        print("tier_backtest: the manifest must be a non-empty JSON list")
        return 1

    results = classify(args.repo, rows, repo_name=args.repo_name)
    one, three = split(results)
    if args.as_json:
        print(json.dumps({"split": {"T1": one, "T3": three}, "rows": results}, indent=2))
        return 0
    for item in results:
        print(
            "PR#%-4s %s  %-60s %s"
            % (
                item["number"],
                item["tier"],
                ", ".join(item["reasons"])[:240] or "-",
                item["title"][:60],
            )
        )
    print("tier_backtest: %d pull request(s): T1=%d T3=%d" % (len(results), one, three))
    return 0


if __name__ == "__main__":
    sys.exit(main())
