"""Contract for merge-lane v5: slice and non-slice pull requests.

Owner-approved policy (2026-11-25): a non-slice PR merges on an independent
review at its exact head plus green checks; a slice PR additionally needs
verify evidence at the exact head (or blob equality for every changed file).
Neither class may merge on the other class's evidence, and the class must come
from the lease/tamper classifier -- an unknown class fails closed.
"""

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "merge_lane_v5.py"
sys.path.insert(0, str(ROOT / "scripts"))

import merge_lane_v5 as lane  # noqa: E402

HEAD = "a" * 40
OLDER = "b" * 40


def approve(head=HEAD, state="COMMENTED", body=None):
    return {
        "state": state,
        "commit_id": head,
        "author": "reviewer",
        "body": body
        if body is not None
        else "APPROVE (independent review at %s, hermes family)\n\nVERDICT: approve" % head,
    }


def owner_approve(head=HEAD, state="COMMENTED", body=None):
    return {
        "state": state,
        "commit_id": head,
        "author": "owner",
        "body": body
        if body is not None
        else "APPROVE (owner) at %s\n\nOne-time owner decision." % head,
    }


def gate_record(ok=True, ran=True, reason=None):
    return {
        "ran": ran,
        "ok": ok,
        "reason": reason or ("gate_ok" if ok else "gate_failed"),
        "script_ref": "origin/main",
        "script_sha": "c" * 40,
        "base": "d" * 40,
        "head": HEAD,
        "exit_code": 0 if ok else 1,
        "output_tail": "",
    }


def facts(**overrides):
    value = {
        "pr": {
            "state": "OPEN",
            "is_draft": False,
            "mergeable": True,
            "merge_state_status": "CLEAN",
            "head": HEAD,
            "files": ["bundlebees_plugin/example.py", "tests/test_example.py"],
        },
        "class": lane.NON_SLICE,
        "reviews": [approve()],
        "checks": [
            {"name": "ci-gate", "status": "completed", "conclusion": "success", "head": HEAD},
            {"name": "gitleaks", "status": "completed", "conclusion": "success", "head": HEAD},
        ],
        "required_checks": ["ci-gate"],
        "verify": {"records": [], "compare": {}},
        "lease_tamper": gate_record(),
        "owner_held": False,
    }
    for key, override in overrides.items():
        if key == "pr" and isinstance(override, dict):
            value["pr"] = {**value["pr"], **override}
        else:
            value[key] = override
    return value


def verify(sha=HEAD, ok=True, record="verify-results/landed.json"):
    return {"record": record, "sha": sha, "ok": ok}


class NonSliceTests(unittest.TestCase):
    def test_review_and_green_checks_merge_without_verify_evidence(self):
        verdict = lane.decide(facts())
        self.assertTrue(verdict["merge"], verdict)
        self.assertEqual(verdict["class"], lane.NON_SLICE)
        self.assertEqual(verdict["reason"], "reviewed")
        self.assertIsNone(verdict["verify"])

    def test_leftover_verify_evidence_is_not_required_but_does_not_block(self):
        value = facts(verify={"records": [verify(OLDER)], "compare": {}})
        verdict = lane.decide(value)
        self.assertTrue(verdict["merge"], verdict)
        self.assertIsNone(verdict["verify"])

    def test_missing_review_refuses_even_with_exact_verify(self):
        value = facts(reviews=[], verify={"records": [verify()], "compare": {}})
        verdict = lane.decide(value)
        self.assertFalse(verdict["merge"])
        self.assertEqual(verdict["reason"], "review_missing")

    def test_review_at_an_older_head_is_not_an_approval(self):
        value = facts(reviews=[approve(OLDER)])
        verdict = lane.decide(value)
        self.assertFalse(verdict["merge"])
        self.assertEqual(verdict["reason"], "review_at_older_head")

    def test_changes_requested_at_head_blocks_even_with_an_approve(self):
        value = facts(reviews=[
            approve(),
            {"state": "CHANGES_REQUESTED", "commit_id": HEAD, "body": "CHANGES REQUESTED"},
        ])
        verdict = lane.decide(value)
        self.assertFalse(verdict["merge"])
        self.assertEqual(verdict["reason"], "review_changes_requested")

    def test_github_approved_state_at_head_passes(self):
        value = facts(reviews=[{"state": "APPROVED", "commit_id": HEAD, "body": ""}])
        verdict = lane.decide(value)
        self.assertTrue(verdict["merge"], verdict)

    def test_commented_review_must_name_an_independent_review(self):
        value = facts(reviews=[approve(body="APPROVE\n\nlooks fine")])
        verdict = lane.decide(value)
        self.assertFalse(verdict["merge"])
        self.assertEqual(verdict["reason"], "review_missing")

    def test_approve_question_is_not_an_approval(self):
        value = facts(reviews=[approve(body="APPROVE? NO - not an independent review")])
        self.assertEqual(lane.decide(value)["reason"], "review_missing")

    def test_a_denial_is_not_an_approval(self):
        value = facts(reviews=[approve(body="APPROVE (not an independent review at %s)" % HEAD)])
        self.assertEqual(lane.decide(value)["reason"], "review_missing")

    def test_commented_approved_word_is_not_an_approval(self):
        value = facts(reviews=[approve(body="APPROVED (independent review at %s)" % HEAD)])
        self.assertEqual(lane.decide(value)["reason"], "review_missing")

    def test_textual_changes_requested_at_head_blocks(self):
        value = facts(reviews=[
            approve(),
            {"state": "COMMENTED", "commit_id": HEAD,
             "body": "CHANGES REQUESTED (independent review at %s)" % HEAD},
        ])
        self.assertEqual(lane.decide(value)["reason"], "review_changes_requested")

    def test_markdown_bold_approve_form_passes(self):
        value = facts(reviews=[approve(body="**APPROVE** (independent review at %s)" % HEAD)])
        self.assertTrue(lane.decide(value)["merge"])


class SliceTests(unittest.TestCase):
    def test_slice_needs_verify_evidence_at_the_exact_head(self):
        value = facts(class_=None)
        value["class"] = lane.SLICE
        verdict = lane.decide(value)
        self.assertFalse(verdict["merge"])
        self.assertEqual(verdict["reason"], "verify_evidence_missing")

    def test_slice_with_exact_verify_and_review_merges(self):
        value = facts()
        value["class"] = lane.SLICE
        value["verify"] = {"records": [verify()], "compare": {}}
        verdict = lane.decide(value)
        self.assertTrue(verdict["merge"], verdict)
        self.assertEqual(verdict["verify"], "exact")

    def test_slice_with_blob_equal_verify_at_an_ancestor_merges(self):
        value = facts()
        value["class"] = lane.SLICE
        value["verify"] = {
            "records": [verify(OLDER)],
            "compare": {OLDER: {"status": "ahead", "files": ["CHANGELOG.md"]}},
        }
        verdict = lane.decide(value)
        self.assertTrue(verdict["merge"], verdict)
        self.assertEqual(verdict["verify"], "blob")

    def test_slice_blob_equality_fails_when_a_pr_file_changed(self):
        value = facts()
        value["class"] = lane.SLICE
        value["verify"] = {
            "records": [verify(OLDER)],
            "compare": {OLDER: {"status": "ahead", "files": ["tests/test_example.py"]}},
        }
        verdict = lane.decide(value)
        self.assertFalse(verdict["merge"])
        self.assertEqual(verdict["reason"], "verify_at_older_head")

    def test_slice_truncated_compare_is_inconclusive(self):
        value = facts()
        value["class"] = lane.SLICE
        value["verify"] = {
            "records": [verify(OLDER)],
            "compare": {OLDER: {"status": "ahead", "files": ["f%d" % n for n in range(300)]}},
        }
        verdict = lane.decide(value)
        self.assertFalse(verdict["merge"])
        self.assertEqual(verdict["reason"], "verify_blob_check_inconclusive")

    def test_slice_verify_record_not_ok_at_head_fails_closed(self):
        value = facts()
        value["class"] = lane.SLICE
        value["verify"] = {"records": [verify(ok=False)], "compare": {}}
        verdict = lane.decide(value)
        self.assertFalse(verdict["merge"])
        self.assertEqual(verdict["reason"], "verify_failed_at_head")

    def test_slice_failed_verify_at_head_blocks_an_older_blob_match(self):
        value = facts()
        value["class"] = lane.SLICE
        value["verify"] = {
            "records": [verify(HEAD, ok=False, record="verify-results/att_head.json"),
                        verify(OLDER)],
            "compare": {OLDER: {"status": "ahead", "files": ["CHANGELOG.md"]}},
        }
        verdict = lane.decide(value)
        self.assertFalse(verdict["merge"])
        self.assertEqual(verdict["reason"], "verify_failed_at_head")

    def test_slice_truncated_compare_does_not_shadow_a_later_proof(self):
        value = facts()
        value["class"] = lane.SLICE
        value["verify"] = {
            "records": [verify(OLDER), verify("c" * 40)],
            "compare": {
                OLDER: {"status": "ahead", "files": ["f%d" % n for n in range(300)]},
                "c" * 40: {"status": "ahead", "files": ["CHANGELOG.md"]},
            },
        }
        verdict = lane.decide(value)
        self.assertTrue(verdict["merge"], verdict)
        self.assertEqual(verdict["verify"], "blob")

    def test_slice_exact_verify_with_review_at_an_older_head_refuses(self):
        value = facts()
        value["class"] = lane.SLICE
        value["verify"] = {"records": [verify()], "compare": {}}
        value["reviews"] = [approve(OLDER)]
        verdict = lane.decide(value)
        self.assertFalse(verdict["merge"])
        self.assertEqual(verdict["reason"], "review_at_older_head")

    def test_slice_explicit_null_compare_proves_identical_trees(self):
        value = facts()
        value["class"] = lane.SLICE
        value["verify"] = {
            "records": [verify(OLDER)],
            "compare": {OLDER: {"status": "identical", "files": None}},
        }
        verdict = lane.decide(value)
        self.assertTrue(verdict["merge"], verdict)
        self.assertEqual(verdict["verify"], "blob")

    def test_slice_compare_without_a_files_key_is_inconclusive(self):
        value = facts()
        value["class"] = lane.SLICE
        value["verify"] = {
            "records": [verify(OLDER)],
            "compare": {OLDER: {"status": "ahead"}},
        }
        verdict = lane.decide(value)
        self.assertFalse(verdict["merge"])
        self.assertEqual(verdict["reason"], "verify_blob_check_inconclusive")

    def test_slice_still_needs_the_review(self):
        value = facts(reviews=[])
        value["class"] = lane.SLICE
        value["verify"] = {"records": [verify()], "compare": {}}
        verdict = lane.decide(value)
        self.assertFalse(verdict["merge"])
        self.assertEqual(verdict["reason"], "review_missing")


class GateTests(unittest.TestCase):
    def test_owner_gate_blocks_both_classes(self):
        for pull_class in (lane.SLICE, lane.NON_SLICE):
            value = facts(owner_held=True)
            value["class"] = pull_class
            self.assertEqual(lane.decide(value)["reason"], "owner_gate")

    def test_owner_gate_fact_must_be_explicit(self):
        value = facts()
        del value["owner_held"]
        self.assertEqual(lane.decide(value)["reason"], "owner_gate_unknown")
        self.assertEqual(lane.decide(facts(owner_held=None))["reason"], "owner_gate_unknown")

    def test_unknown_class_fails_closed(self):
        value = facts()
        value["class"] = None
        self.assertEqual(lane.decide(value)["reason"], "class_unknown")

    def test_checks_pending_and_failed_and_missing(self):
        pending = facts(checks=[
            {"name": "ci-gate", "status": "in_progress", "conclusion": None, "head": HEAD}])
        failed = facts(checks=[
            {"name": "ci-gate", "status": "completed", "conclusion": "failure", "head": HEAD}])
        missing = facts(checks=[])
        self.assertEqual(lane.decide(pending)["reason"], "checks_pending")
        self.assertEqual(lane.decide(failed)["reason"], "checks_failed")
        self.assertEqual(lane.decide(missing)["reason"], "checks_missing")

    def test_unattributed_or_unnamed_checks_are_not_evidence(self):
        unattributed = facts(checks=[
            {"name": "ci-gate", "status": "completed", "conclusion": "success"}])
        unnamed = facts(checks=[
            {"status": "completed", "conclusion": "success", "head": HEAD}])
        self.assertEqual(lane.decide(unattributed)["reason"], "checks_unattributed")
        self.assertEqual(lane.decide(unnamed)["reason"], "checks_invalid")

    def test_newest_run_per_check_name_wins(self):
        # An old failure is superseded by a newer success on the same name.
        green = facts(checks=[
            {"name": "ci-gate", "status": "completed", "conclusion": "failure", "head": HEAD,
             "completed_at": "2026-11-26T00:10:00Z"},
            {"name": "ci-gate", "status": "completed", "conclusion": "success", "head": HEAD,
             "completed_at": "2026-11-26T00:20:00Z"},
        ])
        # A newer pending run supersedes an old success.
        pending = facts(checks=[
            {"name": "ci-gate", "status": "completed", "conclusion": "success", "head": HEAD,
             "completed_at": "2026-11-26T00:10:00Z"},
            {"name": "ci-gate", "status": "in_progress", "conclusion": None, "head": HEAD,
             "completed_at": "2026-11-26T00:20:00Z"},
        ])
        self.assertTrue(lane.decide(green)["merge"])
        self.assertEqual(lane.decide(pending)["reason"], "checks_pending")

    def test_duplicate_check_names_without_distinct_timestamps_are_ambiguous(self):
        undated = facts(checks=[
            {"name": "ci-gate", "status": "completed", "conclusion": "failure", "head": HEAD},
            {"name": "ci-gate", "status": "completed", "conclusion": "success", "head": HEAD},
        ])
        equal = facts(checks=[
            {"name": "ci-gate", "status": "completed", "conclusion": "failure", "head": HEAD,
             "completed_at": "2026-11-26T00:10:00Z"},
            {"name": "ci-gate", "status": "completed", "conclusion": "success", "head": HEAD,
             "completed_at": "2026-11-26T00:10:00Z"},
        ])
        self.assertEqual(lane.decide(undated)["reason"], "checks_ambiguous")
        self.assertEqual(lane.decide(equal)["reason"], "checks_ambiguous")

    def test_a_required_check_missing_at_head_refuses(self):
        value = facts(checks=[
            {"name": "gitleaks", "status": "completed", "conclusion": "success", "head": HEAD}])
        self.assertEqual(lane.decide(value)["reason"], "checks_missing")

    def test_required_checks_fact_must_be_explicit(self):
        value = facts()
        del value["required_checks"]
        self.assertEqual(lane.decide(value)["reason"], "required_checks_missing")

    def test_newest_check_uses_timestamps_not_list_order(self):
        reversed_failure = facts(checks=[
            {"name": "ci-gate", "status": "completed", "conclusion": "failure", "head": HEAD,
             "completed_at": "2026-11-26T00:20:00Z"},
            {"name": "ci-gate", "status": "completed", "conclusion": "success", "head": HEAD,
             "completed_at": "2026-11-26T00:10:00Z"},
        ])
        self.assertEqual(lane.decide(reversed_failure)["reason"], "checks_failed")

    def test_a_green_check_at_an_older_head_does_not_count(self):
        value = facts(checks=[
            {"name": "ci-gate", "status": "completed", "conclusion": "success", "head": OLDER}])
        self.assertEqual(lane.decide(value)["reason"], "checks_missing")

    def test_pr_state_gates(self):
        self.assertEqual(lane.decide(facts(pr={"is_draft": True}))["reason"], "pr_draft")
        self.assertEqual(lane.decide(facts(pr={"is_draft": None}))["reason"], "pr_draft")
        self.assertEqual(lane.decide(facts(pr={"state": "CLOSED"}))["reason"], "pr_not_open")
        self.assertEqual(lane.decide(facts(pr={"mergeable": False}))["reason"], "pr_not_mergeable")
        self.assertEqual(
            lane.decide(facts(pr={"mergeable": "CONFLICTING"}))["reason"], "pr_not_mergeable")
        self.assertEqual(
            lane.decide(facts(pr={"mergeable": "UNKNOWN"}))["reason"], "pr_not_mergeable")
        self.assertEqual(lane.decide(facts(pr={"mergeable": "MERGEABLE"}))["merge"], True)
        self.assertEqual(
            lane.decide(facts(pr={"merge_state_status": "DIRTY"}))["reason"], "merge_state_dirty")
        self.assertTrue(lane.decide(facts(pr={"merge_state_status": "BEHIND"}))["merge"])

    def test_absent_mergeable_or_draft_key_refuses(self):
        value = facts()
        del value["pr"]["mergeable"]
        self.assertEqual(lane.decide(value)["reason"], "pr_not_mergeable")
        value = facts()
        del value["pr"]["is_draft"]
        self.assertEqual(lane.decide(value)["reason"], "pr_draft")

    def test_reviews_must_be_a_list(self):
        self.assertEqual(lane.decide(facts(reviews=1))["reason"], "reviews_invalid")
        self.assertEqual(lane.decide(facts(reviews=None))["reason"], "reviews_invalid")

    def test_files_must_be_a_list_of_paths(self):
        self.assertEqual(lane.decide(facts(pr={"files": "a.py"}))["reason"], "files_invalid")
        self.assertEqual(lane.decide(facts(pr={"files": None}))["reason"], "files_invalid")

    def test_non_dict_facts_are_invalid(self):
        self.assertEqual(lane.decide([])["reason"], "facts_invalid")


class LeaseTamperGateTests(unittest.TestCase):
    def test_missing_gate_record_refuses(self):
        value = facts()
        del value["lease_tamper"]
        self.assertEqual(lane.decide(value)["reason"], "lease_tamper_not_run")

    def test_gate_not_run_refuses(self):
        value = facts(lease_tamper=gate_record(ran=False))
        self.assertEqual(lane.decide(value)["reason"], "lease_tamper_not_run")

    def test_failed_gate_refuses_even_when_every_other_fact_is_green(self):
        value = facts(lease_tamper=gate_record(ok=False))
        self.assertEqual(lane.decide(value)["reason"], "lease_tamper_failed")

    def test_green_gate_merges(self):
        self.assertTrue(lane.decide(facts())["merge"])

    def test_refusal_names_the_gate_reason(self):
        verdict = lane.decide(facts(lease_tamper=gate_record(ok=False)))
        self.assertEqual(verdict["reason"], "lease_tamper_failed")
        self.assertEqual(verdict["lease_tamper"], "gate_failed")


class SensitivePathOwnerReviewTests(unittest.TestCase):
    def test_github_workflow_needs_an_owner_review(self):
        value = facts(pr={"files": [".github/workflows/ci.yml"]})
        self.assertEqual(lane.decide(value)["reason"], "owner_review_missing")

    def test_rizzstack_lease_needs_an_owner_review(self):
        value = facts(pr={"files": [".rizzstack/leases/wave-c-c2.json"]})
        self.assertEqual(lane.decide(value)["reason"], "owner_review_missing")

    def test_lane_code_needs_an_owner_review(self):
        value = facts(pr={"files": ["scripts/merge_lane_v5.py"]})
        self.assertEqual(lane.decide(value)["reason"], "owner_review_missing")

    def test_gate_code_needs_an_owner_review(self):
        value = facts(pr={"files": ["scripts/check_ci_gate.py"]})
        self.assertEqual(lane.decide(value)["reason"], "owner_review_missing")

    def test_policy_needs_an_owner_review(self):
        value = facts(pr={"files": ["policy/merge-lane-v5.md"]})
        self.assertEqual(lane.decide(value)["reason"], "owner_review_missing")

    def test_owner_review_at_head_satisfies_the_extra_gate(self):
        value = facts(pr={"files": [".github/workflows/ci.yml"]},
                      reviews=[approve(), owner_approve()])
        verdict = lane.decide(value)
        self.assertTrue(verdict["merge"], verdict)
        self.assertEqual(verdict["sensitive_files"], [".github/workflows/ci.yml"])

    def test_owner_review_at_an_older_head_does_not(self):
        value = facts(pr={"files": [".github/workflows/ci.yml"]},
                      reviews=[approve(), owner_approve(OLDER)])
        self.assertEqual(lane.decide(value)["reason"], "owner_review_at_older_head")

    def test_independent_review_does_not_satisfy_the_owner_gate(self):
        value = facts(pr={"files": [".github/scripts/lease_tamper.py"]})
        self.assertEqual(lane.decide(value)["reason"], "owner_review_missing")

    def test_owner_review_does_not_replace_the_independent_review(self):
        value = facts(pr={"files": [".github/workflows/ci.yml"]}, reviews=[owner_approve()])
        self.assertEqual(lane.decide(value)["reason"], "review_missing")

    def test_docs_only_change_needs_no_owner_review(self):
        value = facts(pr={"files": ["docs/readme.md", "README.md"]})
        self.assertTrue(lane.decide(value)["merge"])

    def test_delegate_named_file_is_not_gate_code(self):
        value = facts(pr={"files": ["bundlebees_plugin/delegate.py"]})
        self.assertTrue(lane.decide(value)["merge"])

    def test_owner_changes_requested_at_head_blocks(self):
        value = facts(pr={"files": [".github/workflows/ci.yml"]}, reviews=[
            approve(), owner_approve(),
            {"state": "COMMENTED", "commit_id": HEAD, "body": "CHANGES REQUESTED (owner)"},
        ])
        self.assertEqual(lane.decide(value)["reason"], "review_changes_requested")

    def test_sensitive_paths_are_reported_for_both_classes(self):
        for pull_class in (lane.SLICE, lane.NON_SLICE):
            value = facts(pr={"files": [".rizzstack/contracts/c.json"]},
                          reviews=[approve(), owner_approve()])
            value["class"] = pull_class
            if pull_class == lane.SLICE:
                value["verify"] = {"records": [verify()], "compare": {}}
            verdict = lane.decide(value)
            self.assertTrue(verdict["merge"], verdict)
            self.assertEqual(verdict["sensitive_files"], [".rizzstack/contracts/c.json"])


class LeaseTamperRunnerTests(unittest.TestCase):
    """The runner must execute the trusted base copy, never the PR's copy."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.repo = Path(self._tmp.name)
        self._git("init", "-q")
        self._git("config", "user.email", "t@example.invalid")
        self._git("config", "user.name", "test")
        self.script = self.repo / ".github" / "scripts" / "lease_tamper.py"
        self.script.parent.mkdir(parents=True)
        self.script.write_text("import sys\nprint('trusted gate ok')\nsys.exit(0)\n", encoding="utf-8")
        self._git("add", "-A")
        self._git("commit", "-qm", "trusted gate")
        self.main_sha = self._git("rev-parse", "HEAD").stdout.strip()
        self._git("update-ref", "refs/remotes/origin/main", self.main_sha)
        self.script.write_text("import sys\nprint('head gate must not run')\nsys.exit(1)\n",
                               encoding="utf-8")
        self._git("commit", "-qam", "head edits the gate")
        self.head_sha = self._git("rev-parse", "HEAD").stdout.strip()

    def tearDown(self):
        self._tmp.cleanup()

    def _git(self, *args):
        return subprocess.run(
            ["git", "-C", str(self.repo), *args],
            capture_output=True, text=True, timeout=30, check=True)

    def test_runs_the_trusted_main_copy_not_the_head_copy(self):
        record = lane.run_lease_tamper(
            str(self.repo), self.head_sha, base=self.main_sha,
            number=7, branch="feat/x", fetch=False)
        self.assertTrue(record["ran"], record)
        self.assertTrue(record["ok"], record)
        self.assertEqual(record["script_sha"], self.main_sha)
        self.assertIn("trusted gate ok", record["output_tail"])
        self.assertNotIn("head gate must not run", record["output_tail"])

    def test_a_failing_trusted_gate_refuses(self):
        self.script.write_text("import sys\nsys.exit(1)\n", encoding="utf-8")
        self._git("commit", "-qam", "trusted gate now fails")
        failing_sha = self._git("rev-parse", "HEAD").stdout.strip()
        self._git("update-ref", "refs/remotes/origin/main", failing_sha)
        record = lane.run_lease_tamper(
            str(self.repo), self.head_sha, base=self.main_sha, fetch=False)
        self.assertTrue(record["ran"], record)
        self.assertFalse(record["ok"], record)
        self.assertEqual(record["reason"], "gate_failed")
        self.assertEqual(record["script_sha"], failing_sha)

    def test_missing_gate_ref_is_not_green_by_absence(self):
        record = lane.run_lease_tamper(
            str(self.repo), self.head_sha, base=self.main_sha,
            script_ref="origin/dead", fetch=False)
        self.assertFalse(record["ran"], record)
        self.assertFalse(record["ok"], record)
        self.assertEqual(record["reason"], "gate_ref_unresolved")


class CommandLineTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        cls.repo = Path(cls._tmp.name)
        script = cls.repo / ".github" / "scripts" / "lease_tamper.py"
        script.parent.mkdir(parents=True)
        script.write_text("import sys\nsys.exit(0)\n", encoding="utf-8")
        subprocess.run(["git", "-C", str(cls.repo), "init", "-q"], check=True)
        subprocess.run(["git", "-C", str(cls.repo), "config", "user.email", "t@example.invalid"],
                       check=True)
        subprocess.run(["git", "-C", str(cls.repo), "config", "user.name", "test"], check=True)
        subprocess.run(["git", "-C", str(cls.repo), "add", "-A"], check=True)
        subprocess.run(["git", "-C", str(cls.repo), "commit", "-qm", "gate"], check=True)
        sha = subprocess.run(["git", "-C", str(cls.repo), "rev-parse", "HEAD"],
                             capture_output=True, text=True, check=True).stdout.strip()
        subprocess.run(["git", "-C", str(cls.repo), "update-ref", "refs/remotes/origin/main", sha],
                       check=True)

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def _run(self, payload):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "facts.json"
            path.write_text(json.dumps(payload), encoding="utf-8")
            return subprocess.run(
                [sys.executable, str(SCRIPT), "--facts", str(path), "--repo", str(self.repo)],
                capture_output=True, text=True, timeout=60)

    def test_cli_exit_zero_on_merge_and_one_on_refusal(self):
        merged = self._run(facts())
        refused = self._run(facts(reviews=[]))
        self.assertEqual(merged.returncode, 0, merged.stderr)
        self.assertTrue(json.loads(merged.stdout)["merge"])
        self.assertEqual(refused.returncode, 1, refused.stderr)
        self.assertEqual(json.loads(refused.stdout)["reason"], "review_missing")

    def test_cli_reads_stdin_by_default(self):
        proc = subprocess.run(
            [sys.executable, str(SCRIPT), "--repo", str(self.repo)],
            input=json.dumps(facts()),
            capture_output=True, text=True, timeout=60)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertTrue(json.loads(proc.stdout)["merge"])

    def test_cli_runs_the_gate_even_when_the_facts_omit_it(self):
        payload = facts()
        del payload["lease_tamper"]
        proc = self._run(payload)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertTrue(json.loads(proc.stdout)["merge"])

    def test_cli_refuses_when_the_gate_ref_is_missing(self):
        proc = subprocess.run(
            [sys.executable, str(SCRIPT), "--repo", str(self.repo), "--gate-ref", "origin/dead"],
            input=json.dumps(facts()),
            capture_output=True, text=True, timeout=60)
        self.assertEqual(proc.returncode, 1, proc.stderr)
        self.assertEqual(json.loads(proc.stdout)["reason"], "lease_tamper_not_run")

    def test_cli_refuses_duplicate_json_keys(self):
        payload = json.dumps(facts())[:-1] + ', "owner_held": false}'
        proc = subprocess.run(
            [sys.executable, str(SCRIPT), "--repo", str(self.repo)],
            input=payload,
            capture_output=True, text=True, timeout=60)
        self.assertEqual(proc.returncode, 2, proc.stderr)
        self.assertEqual(json.loads(proc.stdout)["reason"], "facts_unreadable")

    def test_cli_refuses_a_non_object_facts_document(self):
        proc = subprocess.run(
            [sys.executable, str(SCRIPT), "--repo", str(self.repo)],
            input="[]",
            capture_output=True, text=True, timeout=60)
        self.assertEqual(proc.returncode, 1, proc.stderr)
        self.assertEqual(json.loads(proc.stdout)["reason"], "facts_invalid")


if __name__ == "__main__":
    unittest.main()
