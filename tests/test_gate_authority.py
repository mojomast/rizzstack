"""Gate authority (S1): who may approve, which gate runs, what is T1, and stale bases.

An independent approval counts only when GitHub says the review was submitted by the
authenticated reviewer app login ``rizzstack-reviewer[bot]``. The review text never
names an identity: the coordinator app, the owner's token and any other account can
post the canonical line, and none of them is an approval. A rejection still governs
whatever account posted it.

The gate GitHub runs must be the base branch's copy: a ``pull_request_target``
workflow checks out the base commit, fetches the pull request head as git objects
only, and runs nothing from the pull request.

The T1 allowlist keeps auth, CI, subprocess, network, dependency, test-infra and gate
surfaces out of T1; a file kind the gate does not know is T3.

The merge lane refuses a pull request whose branch is behind its base.

Run: python3 -m pytest -q -p no:cacheprovider tests/test_gate_authority.py
"""

from __future__ import annotations

import importlib.util
import os
import re
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
GATE_SCRIPT = ROOT / ".github" / "scripts" / "lease_tamper.py"
LANE_SCRIPT = ROOT / "scripts" / "merge_lane_v5.py"
WORKFLOW = ROOT / ".github" / "workflows" / "gate-authority.yml"

HEAD = "1a1d032f98691fb4121d5b9673b36562c77a5627"
BASE = "316087f7da8ea01a206b4c6417c2a1599e84b35c"
LINE = "APPROVE (independent review) at " + HEAD
REVIEWER = "rizzstack-reviewer[bot]"
COORDINATOR = "rizzstack-coordinator[bot]"
OWNER = "mojomast"


def _load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


GATE = _load(GATE_SCRIPT, "lease_tamper_authority_under_test")
LANE = _load(LANE_SCRIPT, "merge_lane_authority_under_test")


def _review(login, body=LINE, state="COMMENTED", commit=HEAD):
    review = {"state": state, "body": body, "commit_id": commit,
              "submitted_at": "2026-11-27T22:00:00Z"}
    if login is not None:
        review["user"] = {"login": login}
    return review


def _accepted(reviews):
    review, _match, _refusal = GATE.independent_review(None, reviews, BASE, HEAD)
    return review


# --------------------------------------------------------------- review identity
class ReviewIdentityTest(unittest.TestCase):
    def test_reviewer_app_login_is_the_only_independent_approval(self):
        self.assertIsNotNone(_accepted([_review(REVIEWER)]))
        self.assertIsNotNone(_accepted([_review(REVIEWER, body="", state="APPROVED")]))

    def test_coordinator_app_cannot_approve(self):
        self.assertIsNone(_accepted([_review(COORDINATOR)]))
        self.assertIsNone(_accepted([_review(COORDINATOR, body="", state="APPROVED")]))

    def test_owner_token_cannot_approve(self):
        self.assertIsNone(_accepted([_review(OWNER)]))
        self.assertIsNone(_accepted([_review(OWNER, body="", state="APPROVED")]))

    def test_text_naming_the_reviewer_is_not_identity(self):
        body = "APPROVE (independent review — rizzstack-reviewer[bot], model family kimi) at " + HEAD
        self.assertIsNone(_accepted([_review("someone-else", body=body)]))
        self.assertIsNone(_accepted([_review(None, body=body)]))
        self.assertIsNone(_accepted([_review("rizzstack-reviewer", body=body)]))  # not the [bot] login

    def test_rejection_from_any_login_still_governs(self):
        rejection = _review(OWNER, body="CHANGES REQUESTED (finding) at " + HEAD)
        rejection["submitted_at"] = "2026-11-27T22:05:00Z"
        self.assertIsNone(_accepted([_review(REVIEWER), rejection]))

    def test_identity_rule_lives_in_the_shared_policy_block(self):
        for module in (GATE, LANE):
            self.assertEqual(module.REVIEWER_LOGIN, REVIEWER)
            self.assertTrue(module.review_identity_ok({"user": {"login": REVIEWER}}))
            self.assertTrue(module.review_identity_ok({"author": REVIEWER}))
            for login in (COORDINATOR, OWNER, "", None):
                self.assertFalse(module.review_identity_ok({"user": {"login": login}}), login)
            self.assertFalse(module.review_identity_ok({}))

    def test_lane_accepts_only_the_reviewer_app(self):
        def facts(author):
            return {
                "pr": {"state": "OPEN", "is_draft": False, "mergeable": True,
                       "merge_state_status": "CLEAN", "head": HEAD, "files": ["docs/readme.md"]},
                "class": LANE.NON_SLICE,
                "reviews": [{"state": "COMMENTED", "commit_id": HEAD, "author": author, "body": LINE}],
                "checks": [{"name": "ci-gate", "status": "completed", "conclusion": "success", "head": HEAD}],
                "required_checks": ["ci-gate"],
                "verify": {"records": [], "compare": {}, "provenance": {}},
                "lease_tamper": {"ran": True, "ok": True, "reason": "gate_ok",
                                 "output_tail": "lease-tamper: class=non-slice TIER=T3"},
                "owner_held": False,
            }
        self.assertTrue(LANE.decide(facts(REVIEWER))["merge"])
        for author in (COORDINATOR, OWNER, "reviewer"):
            verdict = LANE.decide(facts(author))
            self.assertFalse(verdict["merge"], author)


# --------------------------------------------------------------- stale base
class StaleBaseTest(unittest.TestCase):
    def test_lane_refuses_a_branch_behind_its_base(self):
        facts = {
            "pr": {"state": "OPEN", "is_draft": False, "mergeable": True,
                   "merge_state_status": "BEHIND", "head": HEAD, "files": ["docs/readme.md"]},
            "class": LANE.NON_SLICE,
            "reviews": [{"state": "COMMENTED", "commit_id": HEAD, "author": REVIEWER, "body": LINE}],
            "checks": [{"name": "ci-gate", "status": "completed", "conclusion": "success", "head": HEAD}],
            "required_checks": ["ci-gate"],
            "verify": {"records": [], "compare": {}, "provenance": {}},
            "lease_tamper": {"ran": True, "ok": True, "reason": "gate_ok",
                             "output_tail": "lease-tamper: class=non-slice TIER=T3"},
            "owner_held": False,
        }
        verdict = LANE.decide(facts)
        self.assertFalse(verdict["merge"])
        self.assertEqual(verdict["reason"], "merge_state_behind")
        self.assertNotIn("BEHIND", LANE.MERGE_STATES)


# --------------------------------------------------------------- base-pinned gate
class BasePinnedGateWorkflowTest(unittest.TestCase):
    def text(self):
        self.assertTrue(WORKFLOW.is_file(), "missing .github/workflows/gate-authority.yml")
        return WORKFLOW.read_text(encoding="utf-8")

    def test_runs_on_pull_request_target_only(self):
        text = self.text()
        on_block = re.search(r"(?ms)^on:\s*\n(.*?)(?=^\S)", text)
        self.assertIsNotNone(on_block, "no top-level on: block")
        events = re.findall(r"(?m)^  ([a-z_]+):", on_block.group(1))
        self.assertEqual(events, ["pull_request_target"])
        self.assertRegex(text, r"(?m)^  gate-authority:\s*$")
        self.assertRegex(text, r"(?m)^    name: gate-authority\s*$")

    def test_every_checkout_is_the_base_commit_without_credentials(self):
        text = self.text()
        checkouts = re.findall(r"(?ms)uses:\s*actions/checkout@[0-9a-f]{40}.*?(?=\n      - |\Z)", text)
        self.assertTrue(checkouts, "no pinned actions/checkout step")
        for block in checkouts:
            self.assertRegex(block, r"ref:\s*\$\{\{\s*github\.event\.pull_request\.base\.sha\s*\}\}")
            self.assertRegex(block, r"persist-credentials:\s*false")
        self.assertNotRegex(text, r"ref:\s*\$\{\{\s*github\.event\.pull_request\.head")
        self.assertNotIn("refs/pull/${{ github.event.pull_request.number }}/merge", text)

    def test_pull_request_code_is_fetched_as_objects_and_never_run(self):
        text = self.text()
        self.assertRegex(text, r"git fetch [^\n]*refs/pull/\$\{\{\s*github\.event\.pull_request\.number\s*\}\}/head")
        runs = re.findall(r"(?m)^\s+run:\s*(.*)$", text)
        commands = " ".join(runs) + " " + " ".join(re.findall(r"(?ms)run:\s*\|\n(.*?)(?=\n\s*-\s|\Z)", text))
        for forbidden in ("pip install", "npm ", "pytest", "make ", "unittest", "bash ./", "sh ./",
                          "git checkout", "git switch", "git worktree", "source "):
            self.assertNotIn(forbidden, commands, forbidden)
        self.assertIn("python3 .github/scripts/lease_tamper.py", commands)
        self.assertNotIn("secrets.", text)
        # Attacker-controlled strings never reach a shell (script injection).
        for untrusted in ("pull_request.title", "pull_request.body", "head.ref", "head.label",
                          "head_ref", "github.event.review"):
            self.assertNotIn(untrusted, text, untrusted)

    def test_token_is_read_only(self):
        text = self.text()
        perms = re.findall(r"(?m)^\s+([a-z-]+):\s*(read|write|none)\s*$", text)
        self.assertTrue(perms)
        self.assertFalse([p for p in perms if p[1] == "write"], perms)
        self.assertNotRegex(text, r"permissions:\s*write-all")

    def test_gate_judges_pull_request_target_like_a_pull_request(self):
        self.assertIn("pull_request_target", GATE.PULL_REQUEST_EVENTS)


# --------------------------------------------------------------- T1 allowlist
class GitRepo:
    def __init__(self, path):
        self.path = Path(path)
        self.git("init", "-q", "-b", "main")
        self.git("config", "user.email", "tier@example.invalid")
        self.git("config", "user.name", "Gate Authority Tests")
        self.git("config", "commit.gpgsign", "false")

    def git(self, *args):
        proc = subprocess.run(["git", "-C", str(self.path), *args], capture_output=True, text=True)
        if proc.returncode != 0:
            raise AssertionError("git %s: %s%s" % (" ".join(args), proc.stdout, proc.stderr))
        return proc.stdout.strip()

    def write(self, relative, text, mode=None):
        target = self.path / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
        if mode is not None:
            os.chmod(target, mode)

    def commit(self, message):
        self.git("add", "-A")
        self.git("commit", "-q", "-m", message)
        return self.git("rev-parse", "HEAD")


BASE_FILES = {
    "src/slice.py": "VALUE = 1\n",
    "docs/readme.md": "# docs\n",
    "tests/test_frozen.py": "def test_ok():\n    assert True\n",
    "packages/widget/index.js": "export const x = 1;\n",
    "apps/board/index.html": "<p>board</p>\n",
    "hyvv/cli.py": "VALUE = 1\n",
}


class T1AllowlistTest(unittest.TestCase):
    def tier(self, changes, repo_name="orbitdesktop", symlinks=(), modes=None):
        root = tempfile.mkdtemp(prefix="gate-authority-")
        repo = GitRepo(root)
        for path, text in BASE_FILES.items():
            repo.write(path, text)
        base = repo.commit("base")
        repo.git("checkout", "-q", "-b", "feat/x")
        for path, text in changes.items():
            repo.write(path, text, (modes or {}).get(path))
        for link, target in symlinks:
            (repo.path / link).parent.mkdir(parents=True, exist_ok=True)
            os.symlink(target, repo.path / link)
        head = repo.commit("change")
        tier, reasons = GATE.risk_tier(str(repo.path), base, head, repo_name=repo_name, environ={})
        return tier, [reason for reason, _ in reasons]

    def assert_t3(self, reason, changes, **kwargs):
        tier, reasons = self.tier(changes, **kwargs)
        self.assertEqual(tier, "T3", (changes, reasons))
        self.assertIn(reason, reasons)

    def test_plain_source_docs_and_new_test_module_stay_t1(self):
        tier, reasons = self.tier({"src/slice.py": "VALUE = 2\n", "docs/readme.md": "# new\n",
                                   "tests/test_more.py": "def test_more():\n    assert 1\n"})
        self.assertEqual((tier, reasons), ("T1", []))

    def test_subprocess_capability_is_t3(self):
        self.assert_t3("capability_subprocess", {"src/slice.py": "import subprocess\nVALUE = 2\n"})
        self.assert_t3("capability_subprocess", {"src/slice.py": "import os\nos.system('true')\n"})
        self.assert_t3("capability_subprocess",
                       {"packages/widget/index.js": "const cp = require('child_process');\n"})

    def test_network_capability_is_t3(self):
        self.assert_t3("capability_network", {"src/slice.py": "import urllib.request\nVALUE = 2\n"})
        self.assert_t3("capability_network", {"src/slice.py": "import socket\nVALUE = 2\n"})
        self.assert_t3("capability_network",
                       {"packages/widget/index.js": "export const x = fetch('https://example.invalid');\n"})

    def test_dependency_manifests_are_t3(self):
        self.assert_t3("dependency_manifest", {"packages/widget/package.json": '{"name": "w"}\n'})
        self.assert_t3("dependency_manifest", {"src/requirements.txt": "requests==2\n"})
        self.assert_t3("dependency_manifest", {"packages/widget/package-lock.json": "{}\n"})

    def test_test_infrastructure_is_t3(self):
        self.assert_t3("test_infra", {"tests/fixtures/case.json": "{}\n"})
        self.assert_t3("test_infra", {"tests/helpers.py": "VALUE = 1\n"})
        self.assert_t3("test_infra", {"tests/__init__.py": ""})

    def test_nested_ci_configuration_is_t3(self):
        self.assert_t3("ci_config", {"packages/widget/.github/workflows/ci.yml": "on: push\n"})

    def test_auth_validator_and_approval_surfaces_are_t3(self):
        self.assert_t3("auth_surface", {"hyvv/rizzstack_validators.py": "VALUE = 1\n"}, repo_name="hyvv")
        self.assert_t3("auth_surface", {"apps/rizzstack-approvals/index.html": "<p>x</p>\n"})
        self.assert_t3("auth_surface", {"src/sandbox_policy.py": "VALUE = 1\n"})

    def test_unknown_file_kind_is_t3(self):
        self.assert_t3("unknown_kind", {"src/run.sh": "echo hi\n"})
        self.assert_t3("unknown_kind", {"docs/setup.ps1": "Write-Host hi\n"})
        self.assert_t3("unknown_kind", {"src/slice.py": "VALUE = 2\n"}, modes={"src/slice.py": 0o755})
        self.assert_t3("unknown_kind", {}, symlinks=[("src/link.py", "../hyvv/cli.py")])


if __name__ == "__main__":
    unittest.main()
