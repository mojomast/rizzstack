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
        "verify": {"records": [], "compare": {}},
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
        value = facts(reviews=[approve(state="APPROVED")])
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
            {"name": "ci-gate", "status": "completed", "conclusion": "failure", "head": HEAD},
            {"name": "ci-gate", "status": "completed", "conclusion": "success", "head": HEAD},
        ])
        # A newer pending run supersedes an old success.
        pending = facts(checks=[
            {"name": "ci-gate", "status": "completed", "conclusion": "success", "head": HEAD},
            {"name": "ci-gate", "status": "in_progress", "conclusion": None, "head": HEAD},
        ])
        self.assertTrue(lane.decide(green)["merge"])
        self.assertEqual(lane.decide(pending)["reason"], "checks_pending")

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

    def test_files_must_be_a_list_of_paths(self):
        self.assertEqual(lane.decide(facts(pr={"files": "a.py"}))["reason"], "files_invalid")
        self.assertEqual(lane.decide(facts(pr={"files": None}))["reason"], "files_invalid")

    def test_non_dict_facts_are_invalid(self):
        self.assertEqual(lane.decide([])["reason"], "facts_invalid")


class CommandLineTests(unittest.TestCase):
    def _run(self, payload):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "facts.json"
            path.write_text(json.dumps(payload), encoding="utf-8")
            return subprocess.run(
                [sys.executable, str(SCRIPT), "--facts", str(path)],
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
            [sys.executable, str(SCRIPT)], input=json.dumps(facts()),
            capture_output=True, text=True, timeout=60)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertTrue(json.loads(proc.stdout)["merge"])


if __name__ == "__main__":
    unittest.main()
