"""Contract for the Factory status watchdog (rizzstack wave BC, FACTORY-STATUS).

The watchdog keeps exactly one issue, titled "Factory status", up to date from a
snapshot of the seven public repos. This file pins the seven flags (five stall flags
plus the two deploy-hygiene checks), the all-clear body, the deterministic body
rendering, the fixture schema and the workflow draft that runs the script with the
default token.

Fixtures in tests/fixtures/factory-status/ are snapshots of
`rizzstack.factory-status.snapshot/1`; every age is measured from the snapshot's
own `captured_at`, never from the wall clock, so a re-run whose facts did not
change produces the same body.
"""
import copy
import json
import re
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "factory_status.py"
WORKFLOW = ROOT / ".github" / "workflows" / "factory-status.yml"
FIXTURES = ROOT / "tests" / "fixtures" / "factory-status"

SNAPSHOT_SCHEMA = "rizzstack.factory-status.snapshot/1"
REPOS = [
    "mojomast/workflow",
    "mojomast/bundlebees",
    "mojomast/rizzstack",
    "mojomast/orbitdesktop",
    "mojomast/hyvv",
    "mojomast/orbit-control-plane",
    "mojomast/uirizz",
]
FLAGS = [
    "deploy-dir-hygiene",
    "deploy-sha-drift",
    "failing-required-check",
    "no-merge-with-ready-slices",
    "ownerless-pr",
    "stale-green-pr",
    "stale-heartbeat",
]

_module = None


def watchdog():
    """Import scripts/factory_status.py inside a test, so a missing stub fails per test."""
    global _module
    if _module is None:
        scripts = str(ROOT / "scripts")
        if scripts not in sys.path:
            sys.path.insert(0, scripts)
        import factory_status

        _module = factory_status
    return _module


def load(name):
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def report_for(snapshot):
    return watchdog().build_report(snapshot)


def codes(report):
    return [finding["code"] for finding in report["findings"]]


def body_for(snapshot):
    module = watchdog()
    return module.render_issue_body(module.build_report(snapshot))


def parse(text):
    return datetime.strptime(text, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)


def stamp(when):
    return when.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def shift(node, delta):
    """Move every timestamp in a snapshot by the same delta (captured_at included)."""
    if isinstance(node, dict):
        return {
            key: stamp(parse(value) + delta)
            if key.endswith("_at") and isinstance(value, str)
            else shift(value, delta)
            for key, value in node.items()
        }
    if isinstance(node, list):
        return [shift(item, delta) for item in node]
    return node


def pull(snapshot, repo, number):
    for entry in snapshot["repos"]:
        if entry["name"] == repo:
            for item in entry["pulls"]:
                if item["number"] == number:
                    return item
    raise AssertionError("%s#%s missing from fixture" % (repo, number))


def deploy(snapshot, surface):
    for entry in snapshot["deploys"]:
        if entry["surface"] == surface:
            return entry
    raise AssertionError("%s missing from fixture deploys" % surface)


class SnapshotFixtureTests(unittest.TestCase):
    def test_every_fixture_declares_the_slice_schema(self):
        files = sorted(FIXTURES.glob("*.json"))
        self.assertTrue(files, "no fixtures under tests/fixtures/factory-status/")
        for path in files:
            data = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(SNAPSHOT_SCHEMA, data.get("schema"), path.name)

    def test_the_snapshot_reads_the_seven_public_repos(self):
        data = load("all-clear.json")
        self.assertEqual(REPOS, [entry["name"] for entry in data["repos"]])
        self.assertIsInstance(data["captured_at"], str)
        self.assertIn("heartbeat_at", data["coordinator"])
        self.assertIn("ready_slices", data["next_md"])

    def test_the_loader_refuses_a_foreign_schema(self):
        module = watchdog()
        with tempfile.TemporaryDirectory() as scratch:
            path = Path(scratch) / "foreign.json"
            path.write_text(json.dumps({"schema": "someone-else/1"}), encoding="utf-8")
            with self.assertRaises(ValueError):
                module.load_snapshot(str(path))


class StaleGreenPrTests(unittest.TestCase):
    def test_a_green_pr_open_past_two_hours_is_flagged(self):
        report = report_for(load("stale-green-pr.json"))
        self.assertEqual(["stale-green-pr"], codes(report))
        finding = report["findings"][0]
        self.assertEqual("mojomast/workflow", finding["repo"])
        self.assertEqual(147, finding["pull"])

    def test_a_green_pr_in_the_merge_queue_is_not_flagged(self):
        snapshot = load("stale-green-pr.json")
        pull(snapshot, "mojomast/workflow", 147)["in_merge_queue"] = True
        self.assertNotIn("stale-green-pr", codes(report_for(snapshot)))

    def test_a_green_pr_at_exactly_two_hours_is_not_flagged(self):
        snapshot = load("stale-green-pr.json")
        item = pull(snapshot, "mojomast/workflow", 147)
        item["opened_at"] = stamp(parse(snapshot["captured_at"]) - timedelta(hours=2))
        self.assertNotIn("stale-green-pr", codes(report_for(snapshot)))

    def test_one_minute_past_two_hours_is_flagged(self):
        snapshot = load("stale-green-pr.json")
        item = pull(snapshot, "mojomast/workflow", 147)
        item["opened_at"] = stamp(parse(snapshot["captured_at"]) - timedelta(hours=2, minutes=1))
        self.assertIn("stale-green-pr", codes(report_for(snapshot)))

    def test_a_pr_whose_required_check_is_not_green_is_not_flagged_as_stale_green(self):
        snapshot = load("stale-green-pr.json")
        pull(snapshot, "mojomast/workflow", 147)["required_checks"][0]["conclusion"] = "failure"
        self.assertNotIn("stale-green-pr", codes(report_for(snapshot)))


class FailingRequiredCheckTests(unittest.TestCase):
    def test_a_required_check_failing_past_one_hour_is_flagged(self):
        report = report_for(load("failing-required-check.json"))
        self.assertEqual(["failing-required-check"], codes(report))
        finding = report["findings"][0]
        self.assertEqual("mojomast/bundlebees", finding["repo"])
        self.assertEqual(300, finding["pull"])

    def test_a_check_failing_for_exactly_one_hour_is_not_flagged(self):
        snapshot = load("failing-required-check.json")
        check = pull(snapshot, "mojomast/bundlebees", 300)["required_checks"][0]
        check["started_at"] = stamp(parse(snapshot["captured_at"]) - timedelta(hours=1))
        self.assertNotIn("failing-required-check", codes(report_for(snapshot)))

    def test_a_green_check_is_not_flagged(self):
        self.assertNotIn("failing-required-check", codes(report_for(load("all-clear.json"))))


class OwnerlessPrTests(unittest.TestCase):
    def test_a_pr_open_past_a_day_without_an_owner_label_is_flagged(self):
        report = report_for(load("ownerless-pr.json"))
        self.assertEqual(["ownerless-pr"], codes(report))
        finding = report["findings"][0]
        self.assertEqual("mojomast/orbitdesktop", finding["repo"])
        self.assertEqual(104, finding["pull"])

    def test_a_pr_with_an_owner_label_is_not_flagged(self):
        snapshot = load("ownerless-pr.json")
        pull(snapshot, "mojomast/orbitdesktop", 104)["labels"].append("owner:kyle")
        self.assertNotIn("ownerless-pr", codes(report_for(snapshot)))

    def test_a_pr_under_a_day_without_an_owner_label_is_not_flagged(self):
        snapshot = load("ownerless-pr.json")
        item = pull(snapshot, "mojomast/orbitdesktop", 104)
        item["opened_at"] = stamp(parse(snapshot["captured_at"]) - timedelta(hours=23))
        self.assertNotIn("ownerless-pr", codes(report_for(snapshot)))


class NoMergeTests(unittest.TestCase):
    def test_three_hours_without_a_merge_and_ready_slices_is_flagged(self):
        report = report_for(load("no-merge-with-ready-slices.json"))
        self.assertEqual(["no-merge-with-ready-slices"], codes(report))
        self.assertEqual(["B1", "B2", "C1"], report["ready_slices"])

    def test_the_same_silence_without_ready_slices_is_not_flagged(self):
        snapshot = load("no-merge-with-ready-slices.json")
        snapshot["next_md"]["ready_slices"] = []
        self.assertNotIn("no-merge-with-ready-slices", codes(report_for(snapshot)))

    def test_a_recent_merge_in_any_repo_suppresses_the_flag(self):
        snapshot = load("no-merge-with-ready-slices.json")
        snapshot["repos"][0]["last_merge_at"] = stamp(parse(snapshot["captured_at"]) - timedelta(minutes=20))
        self.assertNotIn("no-merge-with-ready-slices", codes(report_for(snapshot)))


class HeartbeatTests(unittest.TestCase):
    def test_a_heartbeat_older_than_45_minutes_is_flagged(self):
        report = report_for(load("stale-heartbeat.json"))
        self.assertEqual(["stale-heartbeat"], codes(report))

    def test_a_heartbeat_at_exactly_45_minutes_is_not_flagged(self):
        snapshot = load("stale-heartbeat.json")
        snapshot["coordinator"]["heartbeat_at"] = stamp(
            parse(snapshot["captured_at"]) - timedelta(minutes=45)
        )
        self.assertNotIn("stale-heartbeat", codes(report_for(snapshot)))

    def test_a_missing_heartbeat_is_flagged(self):
        snapshot = load("stale-heartbeat.json")
        snapshot["coordinator"]["heartbeat_at"] = None
        self.assertIn("stale-heartbeat", codes(report_for(snapshot)))


class DeployDirHygieneTests(unittest.TestCase):
    def test_a_deploy_dir_on_a_branch_with_local_edits_is_flagged(self):
        report = report_for(load("deploy-dir-hygiene.json"))
        self.assertEqual(["deploy-dir-hygiene"], codes(report))
        finding = report["findings"][0]
        self.assertEqual("driver", finding["surface"])
        self.assertEqual("feat/attach-findings-flag", finding["branch"])
        self.assertTrue(finding["dirty"])
        self.assertEqual(8, finding["dirty_files"])
        self.assertIsNone(finding["age_seconds"])

    def test_a_dirty_detached_deploy_dir_is_flagged_for_the_dirt_alone(self):
        snapshot = load("deploy-dir-hygiene.json")
        deploy(snapshot, "driver")["branch"] = None
        report = report_for(snapshot)
        self.assertEqual(["deploy-dir-hygiene"], codes(report))
        finding = report["findings"][0]
        self.assertIsNone(finding["branch"])
        self.assertTrue(finding["dirty"])

    def test_a_clean_deploy_dir_on_a_branch_is_flagged_for_the_branch_alone(self):
        snapshot = load("deploy-dir-hygiene.json")
        item = deploy(snapshot, "driver")
        item["dirty"] = False
        item["dirty_files"] = 0
        report = report_for(snapshot)
        self.assertEqual(["deploy-dir-hygiene"], codes(report))
        finding = report["findings"][0]
        self.assertFalse(finding["dirty"])
        self.assertEqual("feat/attach-findings-flag", finding["branch"])

    def test_a_detached_clean_deploy_dir_is_not_flagged(self):
        self.assertEqual([], report_for(load("all-clear.json"))["findings"])

    def test_the_body_names_the_surface_the_branch_and_the_changed_paths(self):
        body = body_for(load("deploy-dir-hygiene.json"))
        self.assertIn("deploy-dir-hygiene", body)
        self.assertIn("feat/attach-findings-flag", body)
        self.assertIn("8 changed paths", body)
        self.assertNotIn("All clear", body)


class DeployShaDriftTests(unittest.TestCase):
    def test_a_running_sha_behind_the_checkout_is_flagged(self):
        report = report_for(load("deploy-sha-drift.json"))
        self.assertEqual(["deploy-sha-drift"], codes(report))
        finding = report["findings"][0]
        self.assertEqual("control-plane", finding["surface"])
        self.assertNotEqual(finding["running_sha"], finding["head"])

    def test_a_missing_running_sha_is_drift_not_clear(self):
        snapshot = load("all-clear.json")
        deploy(snapshot, "plugin")["running_sha"] = None
        report = report_for(snapshot)
        self.assertEqual(["deploy-sha-drift"], codes(report))
        self.assertIsNone(report["findings"][0]["running_sha"])

    def test_a_running_sha_equal_to_the_checkout_is_not_flagged(self):
        self.assertEqual([], report_for(load("all-clear.json"))["findings"])

    def test_the_body_shows_the_running_and_checked_out_shas(self):
        body = body_for(load("deploy-sha-drift.json"))
        self.assertIn("running ", body)
        self.assertIn("but checkout ", body)

    def test_the_deploy_table_lists_every_surface(self):
        body = body_for(load("all-clear.json"))
        self.assertIn("## Deploy directories", body)
        for surface in ("driver", "plugin", "control-plane", "ingest"):
            self.assertIn("| %s |" % surface, body)


class SnapshotWithoutDeploysTests(unittest.TestCase):
    def test_an_older_snapshot_without_deploys_reports_no_deploy_findings(self):
        snapshot = load("all-clear.json")
        del snapshot["deploys"]
        report = report_for(snapshot)
        self.assertEqual([], report["findings"])
        self.assertEqual([], report["deploys"])
        self.assertIn("| none |", body_for(snapshot))


class AllClearTests(unittest.TestCase):
    def test_the_all_clear_fixture_reports_no_findings(self):
        report = report_for(load("all-clear.json"))
        self.assertEqual([], report["findings"])
        self.assertEqual(["B1", "B2", "C1"], report["ready_slices"])
        self.assertEqual(7, len(report["repos"]))

    def test_the_all_clear_body_says_so(self):
        body = body_for(load("all-clear.json"))
        self.assertIn("All clear", body)
        for code in FLAGS:
            self.assertNotIn(code, body)

    def test_every_flag_has_a_fixture_that_trips_it(self):
        seen = set()
        for path in sorted(FIXTURES.glob("*.json")):
            seen.update(codes(report_for(json.loads(path.read_text(encoding="utf-8")))))
        self.assertEqual(set(FLAGS), seen, "a flag has no fixture: %s" % (set(FLAGS) - seen))


class BodyContractTests(unittest.TestCase):
    def test_the_body_opens_with_the_marker_and_the_title(self):
        body = body_for(load("multi-flag.json"))
        self.assertTrue(body.startswith("<!-- factory-status: v1 -->"), body[:80])
        self.assertIn("# Factory status", body)

    def test_every_finding_is_named_once_with_its_repo_and_pull(self):
        report = report_for(load("multi-flag.json"))
        body = body_for(load("multi-flag.json"))
        self.assertEqual(3, len(report["findings"]))
        for finding in report["findings"]:
            self.assertEqual(1, body.count(finding["code"]), finding["code"])
            if finding["pull"] is not None:
                self.assertIn(str(finding["pull"]), body)
                self.assertIn(finding["repo"], body)
        self.assertNotIn("All clear", body)

    def test_the_body_lists_every_repo(self):
        body = body_for(load("all-clear.json"))
        for name in REPOS:
            self.assertIn(name, body)


class IdempotencyTests(unittest.TestCase):
    def test_the_same_snapshot_renders_the_same_bytes(self):
        snapshot = load("multi-flag.json")
        self.assertEqual(body_for(copy.deepcopy(snapshot)), body_for(copy.deepcopy(snapshot)))

    def test_reordered_repos_and_pulls_render_the_same_body(self):
        snapshot = load("multi-flag.json")
        reordered = copy.deepcopy(snapshot)
        reordered["repos"] = list(reversed(reordered["repos"]))
        for entry in reordered["repos"]:
            entry["pulls"] = list(reversed(entry["pulls"]))
        self.assertEqual(body_for(snapshot), body_for(reordered))

    def test_a_rerun_seven_minutes_later_with_no_new_facts_renders_the_same_body(self):
        snapshot = load("multi-flag.json")
        later = shift(copy.deepcopy(snapshot), timedelta(minutes=7))
        self.assertEqual(body_for(snapshot), body_for(later))

    def test_the_body_carries_no_absolute_timestamps(self):
        for name in ("all-clear.json", "multi-flag.json"):
            body = body_for(load(name))
            self.assertIsNone(
                re.search(r"\b\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}", body), "%s: %s" % (name, body)
            )


class CliTests(unittest.TestCase):
    def test_the_cli_accepts_the_workflow_flags(self):
        args = watchdog().build_parser().parse_args(
            ["--repos", "mojomast/workflow", "mojomast/hyvv", "--issue-title", "Factory status"]
        )
        self.assertEqual(["mojomast/workflow", "mojomast/hyvv"], args.repos)
        self.assertEqual("Factory status", args.issue_title)

    def test_the_cli_renders_a_snapshot_to_stdout(self):
        result = subprocess.run(
            [sys.executable, str(SCRIPT), "--snapshot", str(FIXTURES / "all-clear.json")],
            cwd=str(ROOT),
            capture_output=True,
            text=True,
            timeout=60,
        )
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertTrue(result.stdout.startswith("<!-- factory-status: v1 -->"), result.stdout)
        self.assertIn("All clear", result.stdout)


class WorkflowTests(unittest.TestCase):
    def text(self):
        self.assertTrue(WORKFLOW.is_file(), "missing .github/workflows/factory-status.yml")
        return WORKFLOW.read_text(encoding="utf-8")

    def test_it_runs_every_thirty_minutes_and_on_demand(self):
        text = self.text()
        self.assertIn('cron: "*/30 * * * *"', text)
        self.assertRegex(text, r"(?m)^on:\s*$")
        self.assertRegex(text, r"(?m)^  workflow_dispatch:\s*$")

    def test_permissions_are_contents_read_plus_one_issues_write(self):
        text = self.text()
        blocks = re.findall(r"(?m)^(\s*)permissions:\s*\n((?:\1\s+[a-z-]+:\s*\w+\s*\n)+)", text)
        self.assertEqual(2, len(blocks), "expected a top-level and a job-level permissions block")
        scopes = re.findall(r"(?m)^\s+([a-z-]+):\s*(read|write)\s*$", text)
        self.assertIn(("contents", "read"), scopes)
        self.assertEqual([("issues", "write")], [pair for pair in scopes if pair[1] == "write"])

    def test_it_runs_the_script_with_the_seven_repos_and_the_issue_title(self):
        text = self.text()
        self.assertIn("python3 scripts/factory_status.py", text)
        for name in REPOS:
            self.assertIn(name, text)
        self.assertIn("--issue-title 'Factory status'", text)

    def test_it_uses_the_default_token_and_no_secrets(self):
        text = self.text()
        self.assertIn("${{ github.token }}", text)
        self.assertNotIn("secrets.", text)

    def test_every_action_is_pinned_and_credentials_are_dropped(self):
        text = self.text()
        refs = re.findall(r"(?m)^\s*-?\s*uses:\s*(\S+)", text)
        self.assertTrue(refs)
        for ref in refs:
            self.assertRegex(ref, r"@[0-9a-f]{40}$")
        self.assertIn("persist-credentials: false", text)

    def test_the_job_is_bounded_and_serialised(self):
        text = self.text()
        self.assertRegex(text, r"(?m)^    timeout-minutes:\s*\d+$")
        self.assertRegex(text, r"(?m)^concurrency:\s*$")
        self.assertIn("cancel-in-progress: false", text)


if __name__ == "__main__":
    unittest.main()
