"""Contract for merge-lane v5: slice and non-slice pull requests.

Owner-approved policy (2026-11-25): a non-slice PR merges on an independent
review at its exact head plus green checks; a slice PR additionally needs
verify evidence at the exact head (or blob equality for every changed file).
Neither class may merge on the other class's evidence, and the class must come
from the lease/tamper classifier -- an unknown class fails closed.
"""

import hashlib
import importlib.util
import json
import os
import re
import subprocess
import sys
import tempfile
import types
import unittest
import urllib.parse
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "merge_lane_v5.py"
sys.path.insert(0, str(ROOT / "scripts"))

import merge_lane_v5 as lane  # noqa: E402

HEAD = "a" * 40
OLDER = "b" * 40
BASE = "c" * 40

#: The provenance a slice verify record must carry: the frozen base lease's
#: repo slug, slice id and contract sha256.
PROVENANCE = {
    "repo": "mojomast/workflow",
    "pr": 7,
    "slice": "wave-b-b3",
    "contract_sha256": "e" * 64,
    "lease": ".rizzstack/leases/wave-b-b3.json",
}

FIXTURES = Path(__file__).resolve().parent / "fixtures"

#: sha256 of the shared policy block and the shared fixtures.  The lease/tamper
#: gate (workflow, vendored into hyvv) asserts the same values, so the two
#: copies cannot drift silently.
SHARED_POLICY_SHA256 = "f749821a6170379743a7e440edcb0d90141de8d04bf22ef9a3b6d28a78b83ec8"
REVIEW_LINES_SHA256 = "ac2de684dd22fceed5aa2f18ac6d7d6f8c101c0fd67a619d2a2c6a663642311b"
CARRY_FIXTURES_SHA256 = "33435d738b84742c8e2b5cfa3e153cf20921fb7a1985f808bbde68603941165e"
SHARED_BLOCK_BEGIN = "# --- BEGIN SHARED GATE POLICY"
SHARED_BLOCK_END = "# --- END SHARED GATE POLICY -------------------------------------------------"


def shared_policy_block():
    text = SCRIPT.read_text(encoding="utf-8")
    start = text.index(SHARED_BLOCK_BEGIN)
    end = text.index(SHARED_BLOCK_END) + len(SHARED_BLOCK_END)
    return text[start:end]


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
        "verify": {"records": [], "compare": {}, "provenance": dict(PROVENANCE)},
        "lease_tamper": gate_record(),
        "owner_held": False,
    }
    for key, override in overrides.items():
        if key == "pr" and isinstance(override, dict):
            value["pr"] = {**value["pr"], **override}
        else:
            value[key] = override
    return value


def verify(sha=HEAD, ok=True, record="verify-results/landed.json", **provenance):
    value = {
        "record": record,
        "sha": sha,
        "ok": ok,
        "repo": PROVENANCE["repo"],
        "slice": PROVENANCE["slice"],
        "contract_sha256": PROVENANCE["contract_sha256"],
    }
    value.update(provenance)
    return value


def carry(sha=OLDER, head=HEAD, ok=True, reason=None):
    """A lane-measured review carry-forward record for ``sha``."""
    return {
        sha: {
            "review_sha": sha,
            "base": "c" * 40,
            "head": head,
            "ok": ok,
            "reason": reason or (lane.CARRY_OK if ok else lane.CARRY_CONTENT_CHANGED),
            "detail": "",
        }
    }


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

    def test_review_at_an_older_head_without_carry_evidence_is_not_an_approval(self):
        value = facts(reviews=[approve(OLDER)])
        verdict = lane.decide(value)
        self.assertFalse(verdict["merge"])
        self.assertEqual(verdict["reason"], "review_carry_unmeasured")

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


class ReviewCarryForwardTests(unittest.TestCase):
    """Owner rule: a review at an earlier head carries over a base-merge-only update.

    The decision core consumes the lane-measured carry records; the git
    predicate itself is covered by ReviewCarryMeasurementTests below.
    """

    def test_a_carried_independent_review_merges(self):
        verdict = lane.decide(facts(reviews=[approve(OLDER)], review_carry=carry(OLDER)))
        self.assertTrue(verdict["merge"], verdict)
        self.assertEqual(verdict["review"], lane.REVIEW_MATCH_CARRIED)
        self.assertEqual(verdict["reason"], "reviewed")

    def test_an_exact_head_review_is_preferred_and_needs_no_carry(self):
        verdict = lane.decide(facts(reviews=[approve(OLDER), approve()], review_carry={}))
        self.assertTrue(verdict["merge"], verdict)
        self.assertEqual(verdict["review"], lane.REVIEW_MATCH_EXACT)

    def test_an_authored_commit_after_the_review_refuses_named(self):
        value = facts(reviews=[approve(OLDER)],
                      review_carry=carry(OLDER, ok=False, reason=lane.CARRY_AUTHORED_COMMIT))
        self.assertEqual(lane.decide(value)["reason"], "review_carry_authored_commit")

    def test_a_content_change_after_the_review_refuses_named(self):
        value = facts(reviews=[approve(OLDER)],
                      review_carry=carry(OLDER, ok=False, reason=lane.CARRY_CONTENT_CHANGED))
        self.assertEqual(lane.decide(value)["reason"], "review_carry_content_changed")

    def test_a_merge_of_an_unrelated_branch_refuses_named(self):
        value = facts(reviews=[approve(OLDER)],
                      review_carry=carry(OLDER, ok=False, reason=lane.CARRY_UNRELATED_MERGE))
        self.assertEqual(lane.decide(value)["reason"], "review_carry_unrelated_merge")

    def test_a_review_rewritten_away_refuses_named(self):
        value = facts(reviews=[approve(OLDER)],
                      review_carry=carry(OLDER, ok=False, reason=lane.CARRY_NOT_ANCESTOR))
        self.assertEqual(lane.decide(value)["reason"], "review_not_ancestor")

    def test_missing_carry_evidence_fails_closed(self):
        self.assertEqual(lane.decide(facts(reviews=[approve(OLDER)]))["reason"],
                         "review_carry_unmeasured")

    def test_a_record_measured_for_another_head_is_stale(self):
        value = facts(reviews=[approve(OLDER)], review_carry=carry(OLDER, head="e" * 40))
        self.assertEqual(lane.decide(value)["reason"], "review_carry_stale")

    def test_an_unknown_record_reason_is_invalid(self):
        value = facts(reviews=[approve(OLDER)], review_carry=carry(OLDER, ok=False, reason="because"))
        self.assertEqual(lane.decide(value)["reason"], "review_carry_invalid")

    def test_a_carried_review_still_needs_the_slice_verify_record(self):
        value = facts(reviews=[approve(OLDER)], review_carry=carry(OLDER))
        value["class"] = lane.SLICE
        value["verify"] = {"records": [], "compare": {}, "provenance": dict(PROVENANCE)}
        self.assertEqual(lane.decide(value)["reason"], "verify_evidence_missing")

    def test_a_carried_owner_review_satisfies_the_extra_gate(self):
        value = facts(pr={"files": [".github/workflows/ci.yml"]},
                      reviews=[approve(OLDER), owner_approve(OLDER)],
                      review_carry=carry(OLDER))
        verdict = lane.decide(value)
        self.assertTrue(verdict["merge"], verdict)
        self.assertEqual(verdict["review"], lane.REVIEW_MATCH_CARRIED)
        self.assertEqual(verdict["owner_review"], lane.REVIEW_MATCH_CARRIED)

    def test_a_carried_owner_review_needs_its_own_carry_record(self):
        value = facts(pr={"files": [".github/workflows/ci.yml"]},
                      reviews=[approve(), owner_approve(OLDER)],
                      review_carry={})
        self.assertEqual(lane.decide(value)["reason"], "review_carry_unmeasured")

    def test_a_gate_change_still_needs_the_owner_review_when_the_independent_review_carries(self):
        value = facts(pr={"files": [".github/scripts/lease_tamper.py"]},
                      reviews=[approve(OLDER)], review_carry=carry(OLDER))
        self.assertEqual(lane.decide(value)["reason"], "owner_review_missing")


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
        value["verify"] = {"provenance": dict(PROVENANCE), "records": [verify()], "compare": {}}
        verdict = lane.decide(value)
        self.assertTrue(verdict["merge"], verdict)
        self.assertEqual(verdict["verify"], "exact")

    def test_slice_with_blob_equal_verify_at_an_ancestor_merges(self):
        value = facts()
        value["class"] = lane.SLICE
        value["verify"] = {
            "provenance": dict(PROVENANCE),
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
            "provenance": dict(PROVENANCE),
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
            "provenance": dict(PROVENANCE),
            "records": [verify(OLDER)],
            "compare": {OLDER: {"status": "ahead", "files": ["f%d" % n for n in range(300)]}},
        }
        verdict = lane.decide(value)
        self.assertFalse(verdict["merge"])
        self.assertEqual(verdict["reason"], "verify_blob_check_inconclusive")

    def test_slice_verify_record_not_ok_at_head_fails_closed(self):
        value = facts()
        value["class"] = lane.SLICE
        value["verify"] = {"provenance": dict(PROVENANCE), "records": [verify(ok=False)], "compare": {}}
        verdict = lane.decide(value)
        self.assertFalse(verdict["merge"])
        self.assertEqual(verdict["reason"], "verify_failed_at_head")

    def test_slice_failed_verify_at_head_blocks_an_older_blob_match(self):
        value = facts()
        value["class"] = lane.SLICE
        value["verify"] = {
            "provenance": dict(PROVENANCE),
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
            "provenance": dict(PROVENANCE),
            "records": [verify(OLDER), verify("c" * 40)],
            "compare": {
                OLDER: {"status": "ahead", "files": ["f%d" % n for n in range(300)]},
                "c" * 40: {"status": "ahead", "files": ["CHANGELOG.md"]},
            },
        }
        verdict = lane.decide(value)
        self.assertTrue(verdict["merge"], verdict)
        self.assertEqual(verdict["verify"], "blob")

    def test_slice_exact_verify_with_review_at_an_older_head_without_carry_refuses(self):
        value = facts()
        value["class"] = lane.SLICE
        value["verify"] = {"provenance": dict(PROVENANCE), "records": [verify()], "compare": {}}
        value["reviews"] = [approve(OLDER)]
        verdict = lane.decide(value)
        self.assertFalse(verdict["merge"])
        self.assertEqual(verdict["reason"], "review_carry_unmeasured")

    def test_slice_explicit_null_compare_proves_identical_trees(self):
        value = facts()
        value["class"] = lane.SLICE
        value["verify"] = {
            "provenance": dict(PROVENANCE),
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
            "provenance": dict(PROVENANCE),
            "records": [verify(OLDER)],
            "compare": {OLDER: {"status": "ahead"}},
        }
        verdict = lane.decide(value)
        self.assertFalse(verdict["merge"])
        self.assertEqual(verdict["reason"], "verify_blob_check_inconclusive")

    def test_slice_still_needs_the_review(self):
        value = facts(reviews=[])
        value["class"] = lane.SLICE
        value["verify"] = {"provenance": dict(PROVENANCE), "records": [verify()], "compare": {}}
        verdict = lane.decide(value)
        self.assertFalse(verdict["merge"])
        self.assertEqual(verdict["reason"], "review_missing")

    def test_a_valid_record_from_another_repo_at_a_blob_equal_sha_is_refused(self):
        value = facts()
        value["class"] = lane.SLICE
        value["verify"] = {
            "records": [verify(OLDER, repo="mojomast/bundlebees")],
            "compare": {OLDER: {"status": "ahead", "files": ["CHANGELOG.md"]}},
            "provenance": dict(PROVENANCE),
        }
        verdict = lane.decide(value)
        self.assertFalse(verdict["merge"])
        self.assertEqual(verdict["reason"], "verify_provenance_mismatch")

    def test_a_valid_record_from_another_slice_at_a_blob_equal_sha_is_refused(self):
        value = facts()
        value["class"] = lane.SLICE
        value["verify"] = {
            "records": [verify(OLDER, slice="wave-c-c3")],
            "compare": {OLDER: {"status": "ahead", "files": ["CHANGELOG.md"]}},
            "provenance": dict(PROVENANCE),
        }
        verdict = lane.decide(value)
        self.assertFalse(verdict["merge"])
        self.assertEqual(verdict["reason"], "verify_provenance_mismatch")

    def test_a_record_for_another_contract_of_the_same_slice_is_refused(self):
        value = facts()
        value["class"] = lane.SLICE
        value["verify"] = {
            "records": [verify(HEAD, contract_sha256="f" * 64)],
            "compare": {},
            "provenance": dict(PROVENANCE),
        }
        verdict = lane.decide(value)
        self.assertFalse(verdict["merge"])
        self.assertEqual(verdict["reason"], "verify_provenance_mismatch")

    def test_a_record_without_provenance_is_not_evidence(self):
        value = facts()
        value["class"] = lane.SLICE
        value["verify"] = {
            "records": [{"record": "verify-results/att_legacy.json", "sha": HEAD, "ok": True}],
            "compare": {},
            "provenance": dict(PROVENANCE),
        }
        verdict = lane.decide(value)
        self.assertFalse(verdict["merge"])
        self.assertEqual(verdict["reason"], "verify_provenance_mismatch")

    def test_a_slice_without_a_base_lease_provenance_fails_closed(self):
        value = facts()
        value["class"] = lane.SLICE
        value["verify"] = {"records": [verify()], "compare": {}, "provenance": None}
        verdict = lane.decide(value)
        self.assertFalse(verdict["merge"])
        self.assertEqual(verdict["reason"], "verify_provenance_missing")

    def test_a_foreign_failed_record_at_the_head_does_not_fail_this_slice(self):
        value = facts()
        value["class"] = lane.SLICE
        value["verify"] = {
            "records": [verify(HEAD, ok=False, repo="mojomast/uirizz"), verify()],
            "compare": {},
            "provenance": dict(PROVENANCE),
        }
        verdict = lane.decide(value)
        self.assertTrue(verdict["merge"], verdict)
        self.assertEqual(verdict["verify"], "exact")

    def test_a_record_naming_another_pr_is_refused(self):
        value = facts()
        value["class"] = lane.SLICE
        value["verify"] = {
            "records": [verify(HEAD, pr=99)],
            "compare": {},
            "provenance": dict(PROVENANCE),
        }
        verdict = lane.decide(value)
        self.assertFalse(verdict["merge"])
        self.assertEqual(verdict["reason"], "verify_provenance_mismatch")


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

    def test_a_failed_gate_is_not_masked_by_an_unknown_class(self):
        value = facts(lease_tamper=gate_record(ok=False))
        value["class"] = None
        self.assertEqual(lane.decide(value)["reason"], "lease_tamper_failed")

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

    def test_owner_review_at_an_older_head_without_carry_evidence_does_not(self):
        value = facts(pr={"files": [".github/workflows/ci.yml"]},
                      reviews=[approve(), owner_approve(OLDER)])
        self.assertEqual(lane.decide(value)["reason"], "review_carry_unmeasured")

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
                value["verify"] = {"records": [verify()], "compare": {},
                                   "provenance": dict(PROVENANCE)}
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

    def test_a_gate_ref_other_than_origin_main_is_refused(self):
        record = lane.run_lease_tamper(
            str(self.repo), self.head_sha, base=self.main_sha,
            script_ref="origin/dead", fetch=False)
        self.assertFalse(record["ran"], record)
        self.assertFalse(record["ok"], record)
        self.assertEqual(record["reason"], "gate_ref_not_pinned")

    def test_an_unresolvable_pinned_ref_is_not_green_by_absence(self):
        record = lane.run_lease_tamper(
            str(self.repo), self.head_sha, base=self.main_sha,
            script_ref="origin/dead", fetch=False, allow_unpinned=True)
        self.assertFalse(record["ran"], record)
        self.assertFalse(record["ok"], record)
        self.assertEqual(record["reason"], "gate_ref_unresolved")

    def test_the_pinned_gate_sha_is_recorded(self):
        record = lane.run_lease_tamper(
            str(self.repo), self.head_sha, base=self.main_sha, fetch=False)
        self.assertTrue(record["ran"], record)
        self.assertEqual(record["script_sha"], self.main_sha)
        self.assertTrue(record["script_blob"], record)


class ReviewCarryMeasurementTests(unittest.TestCase):
    """The lane's git predicate must match the gate's, case by case."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.repo = Path(self._tmp.name)
        self._git("init", "-q", "-b", "main")
        self._git("config", "user.email", "t@example.invalid")
        self._git("config", "user.name", "test")
        self._write(".github/scripts/lease_tamper.py", "import sys\nsys.exit(0)\n")
        self._write("src/slice.py", "base\n")
        self.base = self._commit("base")
        self._git("update-ref", "refs/remotes/origin/main", self.base)
        self._git("checkout", "-q", "-b", "feat/x")
        self._write("src/slice.py", "reviewed\n")
        self.reviewed = self._commit("implement the slice")

    def tearDown(self):
        self._tmp.cleanup()

    def _git(self, *args):
        return subprocess.run(
            ["git", "-C", str(self.repo), *args],
            capture_output=True, text=True, timeout=30, check=True)

    def _write(self, name, text):
        path = self.repo / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")

    def _commit(self, message):
        self._git("add", "-A")
        self._git("commit", "-qm", message)
        return self._git("rev-parse", "HEAD").stdout.strip()

    def _advance_base(self, name="src/base.py", text="base advance\n"):
        self._git("checkout", "-q", "main")
        self._write(name, text)
        tip = self._commit("main advances")
        self._git("checkout", "-q", "feat/x")
        return tip

    def _merge(self, ref="main", message="merge the base"):
        self._git("merge", "--no-ff", "-m", message, ref)
        return self._git("rev-parse", "HEAD").stdout.strip()

    def test_base_merge_only_update_measures_ok(self):
        new_base = self._advance_base()
        head = self._merge()

        record = lane.measure_review_carry(str(self.repo), self.reviewed, new_base, head)

        self.assertTrue(record["ok"], record)
        self.assertEqual(record["reason"], lane.CARRY_OK)
        self.assertEqual(record["base"], new_base)
        self.assertEqual(record["head"], head)

    def test_an_authored_commit_measures_refused(self):
        self._write("src/slice.py", "changed\n")
        head = self._commit("change it after the review")

        record = lane.measure_review_carry(str(self.repo), self.reviewed, self.base, head)

        self.assertFalse(record["ok"], record)
        self.assertEqual(record["reason"], lane.CARRY_AUTHORED_COMMIT)

    def test_a_merge_of_an_unrelated_branch_measures_refused(self):
        self._git("checkout", "-q", "-b", "side", "main")
        self._write("src/side.py", "side\n")
        self._commit("side work")
        self._git("checkout", "-q", "feat/x")
        head = self._merge("side", "merge side")

        record = lane.measure_review_carry(str(self.repo), self.reviewed, self.base, head)

        self.assertEqual(record["reason"], lane.CARRY_UNRELATED_MERGE)

    def test_a_conflict_resolution_that_changes_content_measures_refused(self):
        self._git("checkout", "-q", "main")
        self._write("src/slice.py", "main side\n")
        new_base = self._commit("main edits the same line")
        self._git("checkout", "-q", "feat/x")
        conflicted = subprocess.run(
            ["git", "-C", str(self.repo), "merge", "main"], capture_output=True, text=True)
        self.assertNotEqual(conflicted.returncode, 0, "the fixture must produce a conflict")
        self._write("src/slice.py", "resolution\n")
        head = self._commit("resolve differently")

        record = lane.measure_review_carry(str(self.repo), self.reviewed, new_base, head)

        self.assertEqual(record["reason"], lane.CARRY_CONTENT_CHANGED)

    def test_a_rewritten_away_review_measures_refused(self):
        self._git("checkout", "-q", "-b", "stale", "main")
        self._write("src/slice.py", "stale\n")
        stale = self._commit("a reviewed variant")
        self._git("checkout", "-q", "feat/x")
        self._write("src/slice.py", "rewritten\n")
        head = self._commit("rewrite the branch")

        record = lane.measure_review_carry(str(self.repo), stale, self.base, head)

        self.assertEqual(record["reason"], lane.CARRY_NOT_ANCESTOR)

    def test_measure_review_carry_facts_keys_every_non_head_review(self):
        new_base = self._advance_base()
        head = self._merge()
        reviews = [
            {"commit_id": self.reviewed},
            {"commit_id": head},
            {"commit_id": "not-a-sha"},
        ]

        records = lane.measure_review_carry_facts(
            str(self.repo), {"head": head, "base_ref": new_base}, reviews)

        self.assertEqual(list(records), [self.reviewed])
        self.assertTrue(records[self.reviewed]["ok"], records)

    # -- end-to-end through main() ----------------------------------------

    def _facts_payload(self, head, base_ref):
        return {
            "pr": {
                "state": "OPEN", "is_draft": False, "mergeable": True,
                "merge_state_status": "CLEAN", "head": head,
                "number": 12, "branch": "feat/x", "base_ref": base_ref,
                "files": ["src/slice.py"],
            },
            "class": lane.NON_SLICE,
            "reviews": [
                {"state": "COMMENTED", "commit_id": self.reviewed,
                 "body": "APPROVE (independent review at %s)" % self.reviewed}
            ],
            "checks": [{"name": "ci-gate", "status": "completed",
                        "conclusion": "success", "head": head}],
            "required_checks": ["ci-gate"],
            "lease_tamper": gate_record(),
            "owner_held": False,
        }

    def _measured_decide(self, payload):
        """What main() does after collecting facts: measure the carry, decide."""
        payload["review_carry"] = lane.measure_review_carry_facts(
            str(self.repo), payload["pr"], payload["reviews"])
        return lane.decide(
            payload, lambda older, newer: lane._is_ancestor(str(self.repo), older, newer))

    def test_main_measures_the_carry_and_merges(self):
        new_base = self._advance_base()
        head = self._merge()

        verdict = self._measured_decide(self._facts_payload(head, new_base))

        self.assertEqual(verdict["review"], "carried")
        self.assertTrue(verdict["merge"], verdict)

    def test_a_caller_supplied_carry_record_cannot_vouch_for_a_review(self):
        # main() re-measures and the authored commit wins over the claimed "ok".
        self._write("src/slice.py", "changed after the review\n")
        head = self._commit("authored after the review")
        payload = self._facts_payload(head, self.base)
        payload["review_carry"] = carry(self.reviewed)

        verdict = self._measured_decide(payload)

        self.assertEqual(verdict["reason"], "review_carry_authored_commit")
        self.assertIsNone(verdict["review"])


class CommandLineTests(unittest.TestCase):
    """The CLI is a replay tool for recorded facts; the live path fetches."""

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

    def _run(self, payload, extra=()):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "facts.json"
            path.write_text(json.dumps(payload), encoding="utf-8")
            return subprocess.run(
                [sys.executable, str(SCRIPT), "--facts", str(path),
                 "--repo", str(self.repo), *extra],
                capture_output=True, text=True, timeout=60)

    def test_cli_exit_zero_on_merge_and_one_on_refusal(self):
        merged = self._run(facts())
        refused = self._run(facts(reviews=[]))
        self.assertEqual(merged.returncode, 0, merged.stderr)
        self.assertTrue(json.loads(merged.stdout)["merge"])
        self.assertEqual(refused.returncode, 1, refused.stderr)
        self.assertEqual(json.loads(refused.stdout)["reason"], "review_missing")

    def test_cli_reads_facts_from_stdin_with_dash(self):
        proc = subprocess.run(
            [sys.executable, str(SCRIPT), "--facts", "-", "--repo", str(self.repo)],
            input=json.dumps(facts()),
            capture_output=True, text=True, timeout=60)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertTrue(json.loads(proc.stdout)["merge"])

    def test_replay_refuses_when_the_facts_omit_the_gate(self):
        payload = facts()
        del payload["lease_tamper"]
        proc = self._run(payload)
        self.assertEqual(proc.returncode, 1, proc.stderr)
        self.assertEqual(json.loads(proc.stdout)["reason"], "lease_tamper_not_run")

    def test_cli_has_no_gate_ref_option(self):
        proc = subprocess.run(
            [sys.executable, str(SCRIPT), "--repo", str(self.repo),
             "--gate-ref", "origin/main"],
            input=json.dumps(facts()),
            capture_output=True, text=True, timeout=60)
        self.assertEqual(proc.returncode, 2, proc.stderr)
        self.assertIn("unrecognized arguments", proc.stderr)

    def test_cli_has_no_verify_dir_option(self):
        proc = subprocess.run(
            [sys.executable, str(SCRIPT), "--repo", str(self.repo),
             "--verify-dir", "/tmp/records"],
            input=json.dumps(facts()),
            capture_output=True, text=True, timeout=60)
        self.assertEqual(proc.returncode, 2, proc.stderr)
        self.assertIn("unrecognized arguments", proc.stderr)

    def test_cli_has_no_plan_option(self):
        proc = subprocess.run(
            [sys.executable, str(SCRIPT), "--repo", str(self.repo),
             "--plan", "/tmp/plan.json"],
            input=json.dumps(facts()),
            capture_output=True, text=True, timeout=60)
        self.assertEqual(proc.returncode, 2, proc.stderr)
        self.assertIn("unrecognized arguments", proc.stderr)

    def test_no_environment_can_point_the_lane_at_another_store(self):
        """Both local stores are pinned: only function parameters inject them."""
        source = SCRIPT.read_text(encoding="utf-8")
        self.assertNotIn("BUNDLEBEES_VERIFY_DIR", source)
        self.assertNotIn("BUNDLEBEES_PLAN", source)

    def test_a_facts_file_may_never_merge(self):
        proc = self._run(facts(), extra=("--merge",))
        self.assertEqual(proc.returncode, 1, proc.stderr)
        self.assertEqual(json.loads(proc.stdout)["reason"], "facts_merge_forbidden")

    def test_cli_refuses_duplicate_json_keys(self):
        payload = json.dumps(facts())[:-1] + ', "owner_held": false}'
        proc = subprocess.run(
            [sys.executable, str(SCRIPT), "--facts", "-", "--repo", str(self.repo)],
            input=payload,
            capture_output=True, text=True, timeout=60)
        self.assertEqual(proc.returncode, 2, proc.stderr)
        self.assertEqual(json.loads(proc.stdout)["reason"], "facts_unreadable")

    def test_cli_refuses_a_non_object_facts_document(self):
        proc = subprocess.run(
            [sys.executable, str(SCRIPT), "--facts", "-", "--repo", str(self.repo)],
            input="[]",
            capture_output=True, text=True, timeout=60)
        self.assertEqual(proc.returncode, 1, proc.stderr)
        self.assertEqual(json.loads(proc.stdout)["reason"], "facts_invalid")

    def test_live_mode_requires_a_pull_request(self):
        proc = subprocess.run(
            [sys.executable, str(SCRIPT), "--repo", str(self.repo), "--repo-slug", "o/r"],
            input="",
            capture_output=True, text=True, timeout=60)
        self.assertEqual(proc.returncode, 2, proc.stderr)
        self.assertEqual(json.loads(proc.stdout)["reason"], "pr_missing")


# -- shared policy, fixtures and the phase-2 rules --------------------------


class SharedPolicyContractTests(unittest.TestCase):
    """The lane and the gate must run the same parser, carry and path rules."""

    def test_shared_policy_block_is_byte_identical(self):
        self.assertEqual(
            hashlib.sha256(shared_policy_block().encode("utf-8")).hexdigest(),
            SHARED_POLICY_SHA256,
            "the shared policy block changed; vendor the identical block into "
            "workflow .github/scripts/lease_tamper.py and update the hash in both suites",
        )

    def test_shared_review_line_fixtures(self):
        fixture = FIXTURES / "review_lines.json"
        self.assertEqual(hashlib.sha256(fixture.read_bytes()).hexdigest(), REVIEW_LINES_SHA256)
        document = json.loads(fixture.read_text(encoding="utf-8"))
        for case in document["cases"]:
            kind, sha = lane.parse_review_line(case["body"])
            self.assertEqual(kind, case["kind"], case["name"])
            self.assertEqual(sha, case["sha"], case["name"])

    def test_shared_carry_scenarios(self):
        fixture = FIXTURES / "carry_scenarios.py"
        self.assertEqual(hashlib.sha256(fixture.read_bytes()).hexdigest(), CARRY_FIXTURES_SHA256)
        spec = importlib.util.spec_from_file_location("carry_scenarios", fixture)
        scenarios = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(scenarios)
        with tempfile.TemporaryDirectory() as tmp:
            for name, review_sha, base, head, reason, ok in scenarios.build_all(Path(tmp)):
                got_ok, got_reason, detail = lane.review_carry_forward(
                    str(Path(tmp) / name), review_sha, base, head
                )
                self.assertEqual((got_ok, got_reason), (ok, reason), "%s: %s" % (name, detail))

    def test_canonical_review_forms_are_accepted(self):
        for body, kind in (
            ("APPROVE (independent review) at %s" % HEAD, lane.REVIEW_APPROVE_INDEPENDENT),
            ("APPROVE (owner) at %s" % HEAD, lane.REVIEW_APPROVE_OWNER),
            ("CHANGES REQUESTED (why) at %s" % HEAD, lane.REVIEW_CHANGES_REQUESTED),
            ("APPROVE — independent review (outside reviewer) at %s" % HEAD,
             lane.REVIEW_APPROVE_INDEPENDENT),
        ):
            self.assertEqual(lane.parse_review_line(body)[0], kind, body)

    def test_owner_body_never_satisfies_the_independent_rule(self):
        review = {"state": "APPROVED", "commit_id": HEAD,
                  "body": "APPROVE (owner) at %s" % HEAD}
        self.assertEqual(lane.review_form(review)[0], lane.REVIEW_APPROVE_OWNER)
        self.assertFalse(lane.review_is_approval(review, lane.REVIEW_APPROVE_INDEPENDENT))

    def test_only_a_completed_success_is_green(self):
        self.assertTrue(lane.check_run_is_success({"status": "completed", "conclusion": "success"}))
        for conclusion in ("neutral", "skipped", "failure", "cancelled", "stale", ""):
            self.assertFalse(
                lane.check_run_is_success({"status": "completed", "conclusion": conclusion}),
                conclusion,
            )
        self.assertFalse(lane.check_run_is_success({"status": "in_progress", "conclusion": None}))

    def test_sensitive_paths_include_the_explicit_list(self):
        required = {
            "bundlebees_plugin/verify_local.py",
            "bundlebees_plugin/contract.py",
            "bundlebees_plugin/approval.py",
            "bundlebees_plugin/tdd.py",
            "bundlebees_plugin/acdd_cli.py",
            "bundlebees_plugin/subversion_scan.py",
            "bundlebees_plugin/test_receipts.py",
            "bundlebees_plugin/transport.py",
            "bundlebees_plugin/return_report.py",
            "tests/test_lease_tamper.py",
            "tests/test_merge_lane_v5.py",
        }
        self.assertLessEqual(required, set(lane.SENSITIVE_PATHS))
        for path in required:
            self.assertEqual(lane.sensitive_paths([path]), [path], path)
        self.assertEqual(lane.sensitive_paths(["bundlebees_plugin/delegate.py"]), [])


class RejectionGovernsTests(unittest.TestCase):
    """A rejection submitted after the approval being used refuses it."""

    def review(self, sha, state="COMMENTED", body=None, submitted_at="2026-11-26T00:00:00Z"):
        return {
            "state": state,
            "commit_id": sha,
            "body": body if body is not None else "APPROVE (independent review) at %s" % sha,
            "submitted_at": submitted_at,
        }

    def test_rejection_at_the_approved_commit_refuses_the_carried_review(self):
        value = facts(
            reviews=[self.review(OLDER),
                     self.review(OLDER, state="CHANGES_REQUESTED", body="no",
                                 submitted_at="2026-11-26T01:00:00Z")],
            review_carry=carry(OLDER),
        )
        verdict = lane.decide(value)
        self.assertFalse(verdict["merge"])
        self.assertEqual(verdict["reason"], "review_changes_requested")

    def test_rejection_at_the_new_head_refuses_the_carried_review(self):
        value = facts(
            reviews=[self.review(OLDER),
                     self.review(HEAD, body="CHANGES REQUESTED (unsound) at %s" % HEAD,
                                 submitted_at="2026-11-26T01:00:00Z")],
            review_carry=carry(OLDER),
        )
        verdict = lane.decide(value)
        self.assertFalse(verdict["merge"])
        self.assertEqual(verdict["reason"], "review_changes_requested")

    def test_rejection_at_the_same_head_refuses_the_exact_review(self):
        value = facts(
            reviews=[self.review(HEAD),
                     self.review(HEAD, state="CHANGES_REQUESTED", body="no",
                                 submitted_at="2026-11-26T01:00:00Z")],
        )
        verdict = lane.decide(value)
        self.assertFalse(verdict["merge"])
        self.assertEqual(verdict["reason"], "review_changes_requested")

    def test_a_later_reapproval_supersedes_the_rejection(self):
        value = facts(
            reviews=[self.review(HEAD),
                     self.review(HEAD, state="CHANGES_REQUESTED", body="no",
                                 submitted_at="2026-11-26T01:00:00Z"),
                     self.review(HEAD, submitted_at="2026-11-26T02:00:00Z")],
        )
        verdict = lane.decide(value)
        self.assertTrue(verdict["merge"], verdict)
        self.assertEqual(verdict["review"], lane.REVIEW_MATCH_EXACT)


class ChecksSuccessOnlyTests(unittest.TestCase):
    def test_a_skipped_or_neutral_required_check_is_not_green(self):
        for conclusion in ("skipped", "neutral"):
            value = facts(checks=[{"name": "ci-gate", "status": "completed",
                                   "conclusion": conclusion, "head": HEAD}])
            verdict = lane.decide(value)
            self.assertFalse(verdict["merge"], conclusion)
            self.assertEqual(verdict["reason"], "checks_not_success", conclusion)

    def test_success_is_green(self):
        self.assertTrue(lane.decide(facts())["merge"])


class SensitivePathPolicyTests(unittest.TestCase):
    def test_verification_code_needs_the_owner_review(self):
        for path in (
            "bundlebees_plugin/verify_local.py",
            "bundlebees_plugin/contract.py",
            "bundlebees_plugin/approval.py",
            "bundlebees_plugin/tdd.py",
            "bundlebees_plugin/acdd_cli.py",
            "bundlebees_plugin/subversion_scan.py",
            "bundlebees_plugin/test_receipts.py",
            "tests/test_lease_tamper.py",
        ):
            value = facts(pr={"files": [path]})
            verdict = lane.decide(value)
            self.assertFalse(verdict["merge"], path)
            self.assertEqual(verdict["reason"], "owner_review_missing", path)
            self.assertEqual(verdict["sensitive_files"], [path], path)


# -- self-fetched REST facts ------------------------------------------------


def pull_request(head=HEAD, mergeable=True, draft=False, state="open", labels=None):
    return {
        "number": 7,
        "state": state,
        "draft": draft,
        "mergeable": mergeable,
        "mergeable_state": "clean",
        "head": {"sha": head, "ref": "feat/x"},
        "base": {"ref": "main", "sha": BASE},
        "labels": labels or [],
    }


class FakeGitHub:
    """A callable ``http`` for collect_facts: routes by REST path."""

    def __init__(self):
        self.pull = pull_request()
        self.reviews = []
        self.files = []
        self.check_runs = []
        self.compares = {}
        self.calls = []

    def __call__(self, url, token):
        path = urllib.parse.urlparse(url).path
        self.calls.append(path)
        if re.fullmatch(r"/repos/[^/]+/[^/]+/pulls/\d+", path):
            return self.pull, ""
        if re.fullmatch(r"/repos/[^/]+/[^/]+/pulls/\d+/reviews", path):
            return self.reviews, ""
        if re.fullmatch(r"/repos/[^/]+/[^/]+/pulls/\d+/files", path):
            return self.files, ""
        match = re.fullmatch(r"/repos/[^/]+/[^/]+/commits/([0-9a-f]+)/check-runs", path)
        if match:
            return {"check_runs": self.check_runs}, ""
        match = re.fullmatch(r"/repos/[^/]+/[^/]+/compare/([0-9a-f]+)\.\.\.([0-9a-f]+)", path)
        if match:
            return self.compares.get(match.group(1), {"status": "ahead", "files": []}), ""
        raise AssertionError("unexpected REST path: " + path)


class FetchFactsTests(unittest.TestCase):
    def setUp(self):
        self.api = FakeGitHub()
        self.api.files = [{"filename": "bundlebees_plugin/example.py"},
                          {"filename": "tests/test_example.py"}]
        self.api.reviews = [
            {"state": "COMMENTED", "commit_id": OLDER, "body": "APPROVE (independent review) at " + OLDER,
             "submitted_at": "2026-11-26T00:00:00Z", "user": {"login": "reviewer"}},
        ]
        self.api.check_runs = [
            {"name": "ci-gate", "status": "completed", "conclusion": "success",
             "head_sha": HEAD, "completed_at": "2026-11-26T00:10:00Z"},
        ]
        self.api.compares = {OLDER: {"status": "ahead", "files": [{"filename": "CHANGELOG.md"}]}}
        self.tmp = tempfile.TemporaryDirectory()
        self.verify_dir = Path(self.tmp.name) / "verify-results"
        self.verify_dir.mkdir()
        (self.verify_dir / ("landed-%s.json" % OLDER)).write_text(
            json.dumps({
                "sha": OLDER,
                "ok": True,
                "repo": "mojomast/workflow",
                "slice": "wave-b-b3",
                "contract_sha256": "e" * 64,
                "pr": 7,
            }), encoding="utf-8")
        self.plan = Path(self.tmp.name) / "plan.json"
        self.plan.write_text(json.dumps({"owner_held": {"prs": []}}), encoding="utf-8")

    def tearDown(self):
        self.tmp.cleanup()

    def collect(self, **overrides):
        gate = overrides.pop("gate", {
            "ran": True, "ok": True, "reason": "gate_ok", "head": HEAD,
            "output_tail": "lease-tamper: class=non-slice\n",
        })
        repo = overrides.pop("repo_path", ".")
        return lane.collect_facts(
            "mojomast/workflow", 7, repo, "test-token",
            api_url="https://api.test", http=self.api, gate_record=gate,
            verify_dir=self.verify_dir, plan_path=self.plan,
            required_checks=("ci-gate",), **overrides)

    def _lease_repo(self, docs):
        """A checkout whose ``origin/main`` carries the given base leases."""
        repo = Path(self.tmp.name) / "lease-repo"
        repo.mkdir()
        subprocess.run(["git", "-C", str(repo), "init", "-q", "-b", "main"], check=True)
        subprocess.run(["git", "-C", str(repo), "config", "user.email", "t@example.invalid"],
                       check=True)
        subprocess.run(["git", "-C", str(repo), "config", "user.name", "test"], check=True)
        for name, doc in docs.items():
            path = repo / ".rizzstack" / "leases" / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(doc), encoding="utf-8")
        subprocess.run(["git", "-C", str(repo), "add", "-A"], check=True)
        subprocess.run(["git", "-C", str(repo), "commit", "-qm", "leases"], check=True)
        sha = subprocess.run(["git", "-C", str(repo), "rev-parse", "HEAD"],
                             capture_output=True, text=True, check=True).stdout.strip()
        subprocess.run(["git", "-C", str(repo), "update-ref", "refs/remotes/origin/main", sha],
                       check=True)
        return repo

    @staticmethod
    def _lease(**overrides):
        doc = {
            "id": "wave-b-b3",
            "slice": "wave-b-b3",
            "pull_request": 7,
            "branch": "feat/x",
            "contract_sha256": "e" * 64,
            "review": {"status": "frozen", "approved_by": "owner"},
        }
        doc.update(overrides)
        return doc

    def test_a_slice_reads_its_provenance_from_the_frozen_base_lease(self):
        repo = self._lease_repo({"wave-b-b3.json": self._lease()})
        facts = self.collect(repo_path=str(repo), gate={
            "ran": True, "ok": True, "reason": "gate_ok",
            "output_tail": "lease-tamper: class=slice\n",
        })
        provenance = facts["verify"]["provenance"]
        self.assertEqual(provenance["repo"], "mojomast/workflow")
        self.assertEqual(provenance["slice"], "wave-b-b3")
        self.assertEqual(provenance["contract_sha256"], "e" * 64)
        self.assertEqual(provenance["lease"], ".rizzstack/leases/wave-b-b3.json")
        facts["review_carry"] = carry(OLDER)
        verdict = lane.decide(facts)
        self.assertTrue(verdict["merge"], verdict)
        self.assertEqual(verdict["verify"], "blob")

    def test_a_lease_from_another_pr_does_not_provide_provenance(self):
        repo = self._lease_repo({"wave-b-b3.json": self._lease(pull_request=8, branch="feat/y")})
        facts = self.collect(repo_path=str(repo), gate={
            "ran": True, "ok": True, "reason": "gate_ok",
            "output_tail": "lease-tamper: class=slice\n",
        })
        self.assertIsNone(facts["verify"]["provenance"])
        self.assertEqual(facts["verify"]["provenance_reason"], "lease_provenance_missing")
        facts["review_carry"] = carry(OLDER)
        self.assertEqual(lane.decide(facts)["reason"], "verify_provenance_missing")

    def test_an_unfrozen_lease_is_not_provenance(self):
        repo = self._lease_repo({"wave-b-b3.json": self._lease(review={"status": "draft"})})
        self.assertIsNone(self.collect(repo_path=str(repo), gate={
            "ran": True, "ok": True, "reason": "gate_ok",
            "output_tail": "lease-tamper: class=slice\n",
        })["verify"]["provenance"])

    def test_a_record_from_another_repo_is_refused_even_at_a_blob_equal_sha(self):
        repo = self._lease_repo({"wave-b-b3.json": self._lease()})
        (self.verify_dir / ("landed-%s.json" % OLDER)).write_text(
            json.dumps({
                "sha": OLDER, "ok": True, "repo": "mojomast/bundlebees",
                "slice": "wave-b-b3", "contract_sha256": "e" * 64, "pr": 7,
            }), encoding="utf-8")
        facts = self.collect(repo_path=str(repo), gate={
            "ran": True, "ok": True, "reason": "gate_ok",
            "output_tail": "lease-tamper: class=slice\n",
        })
        facts["review_carry"] = carry(OLDER)
        verdict = lane.decide(facts)
        self.assertFalse(verdict["merge"])
        self.assertEqual(verdict["reason"], "verify_provenance_mismatch")

    def test_collects_the_pull_request_state_reviews_and_checks(self):
        facts = self.collect()
        self.assertEqual(facts["pr"]["state"], "OPEN")
        self.assertFalse(facts["pr"]["is_draft"])
        self.assertTrue(facts["pr"]["mergeable"])
        self.assertEqual(facts["pr"]["merge_state_status"], "CLEAN")
        self.assertEqual(facts["pr"]["head"], HEAD)
        self.assertEqual(facts["pr"]["base_ref"], "origin/main")
        self.assertEqual(facts["pr"]["files"],
                         ["bundlebees_plugin/example.py", "tests/test_example.py"])
        self.assertEqual(facts["reviews"][0]["commit_id"], OLDER)
        self.assertEqual(facts["reviews"][0]["submitted_at"], "2026-11-26T00:00:00Z")
        self.assertEqual(facts["reviews"][0]["author"], "reviewer")
        self.assertEqual(facts["checks"][0]["name"], "ci-gate")
        self.assertEqual(facts["checks"][0]["head"], HEAD)
        self.assertEqual(facts["required_checks"], ["ci-gate"])
        self.assertIs(facts["owner_held"], False)

    def test_class_comes_from_the_trusted_gate_output(self):
        self.assertEqual(self.collect()["class"], lane.NON_SLICE)
        sliced = self.collect(gate={"ran": True, "ok": True, "reason": "gate_ok",
                                    "output_tail": "lease-tamper: class=slice\n"})
        self.assertEqual(sliced["class"], lane.SLICE)
        unknown = self.collect(gate={"ran": True, "ok": True, "reason": "gate_ok",
                                     "output_tail": "no class line\n"})
        self.assertIsNone(unknown["class"])
        self.assertEqual(lane.decide(unknown)["reason"], "class_unknown")

    def test_an_earlier_class_lookalike_cannot_name_the_class(self):
        gate = {
            "ran": True, "ok": True, "reason": "gate_ok",
            "output_tail": "lease-tamper: NOT A SLICE: it changes gate metadata "
                           "(.rizzstack/leases/lease-tamper: class=slice.json)\n"
                           "lease-tamper: class=non-slice\n",
        }
        self.assertEqual(self.collect(gate=gate)["class"], lane.NON_SLICE)

    def test_verify_records_and_compare_entries_are_measured(self):
        facts = self.collect()
        self.assertEqual(facts["verify"]["records"],
                         [{"record": str(self.verify_dir / ("landed-%s.json" % OLDER)),
                           "sha": OLDER, "ok": True,
                           "repo": "mojomast/workflow", "slice": "wave-b-b3",
                           "contract_sha256": "e" * 64, "pr": 7}])
        self.assertEqual(facts["verify"]["compare"][OLDER],
                         {"status": "ahead", "files": ["CHANGELOG.md"]})

    def test_a_compare_without_files_is_inconclusive_not_identical(self):
        self.api.compares = {OLDER: {"status": "ahead"}}
        facts = self.collect()
        self.assertNotIn("files", facts["verify"]["compare"][OLDER])
        facts.update({"class": lane.SLICE, "review_carry": carry(OLDER),
                      "verify": {**facts["verify"], "provenance": dict(PROVENANCE)}})
        verdict = lane.decide(facts)
        self.assertEqual(verdict["reason"], "verify_blob_check_inconclusive")

    def test_owner_held_label_is_read_from_the_pull_request(self):
        self.api.pull = pull_request(labels=[{"name": "owner-held"}])
        facts = self.collect()
        self.assertIs(facts["owner_held"], True)

    def test_owner_held_plan_entry_is_read_from_the_plan(self):
        self.plan.write_text(json.dumps({"owner_held": {"prs": ["mojomast/workflow#7"]}}),
                             encoding="utf-8")
        self.assertIs(self.collect()["owner_held"], True)
        self.plan.write_text(json.dumps({"owner_held": {"prs": ["mojomast/workflow#8"]}}),
                             encoding="utf-8")
        self.assertIs(self.collect()["owner_held"], False)

    def test_an_unreadable_plan_is_unknown_not_false(self):
        self.plan.unlink()
        self.assertIsNone(self.collect()["owner_held"])
        self.assertEqual(lane.decide(self.collect())["reason"], "owner_gate_unknown")

    def test_a_plan_without_the_owner_held_key_is_unknown(self):
        self.plan.write_text(json.dumps({}), encoding="utf-8")
        self.assertIsNone(self.collect()["owner_held"])

    def test_a_missing_token_fails_closed(self):
        with self.assertRaises(lane.FetchError):
            lane.collect_facts("mojomast/workflow", 7, ".", "", api_url="https://api.test",
                               http=self.api, required_checks=("ci-gate",))

    def test_ruleset_is_read_from_origin_main_not_the_worktree(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            subprocess.run(["git", "-C", str(repo), "init", "-q", "-b", "main"], check=True)
            subprocess.run(["git", "-C", str(repo), "config", "user.email", "t@example.invalid"],
                           check=True)
            subprocess.run(["git", "-C", str(repo), "config", "user.name", "test"], check=True)
            path = repo / "policy" / "rulesets" / "workflow.json"
            path.parent.mkdir(parents=True)
            path.write_text(json.dumps({"rules": [{"type": "required_status_checks", "parameters": {
                "required_status_checks": [{"context": "ci-gate"}, {"context": "gitleaks"}]}}]}),
                encoding="utf-8")
            subprocess.run(["git", "-C", str(repo), "add", "-A"], check=True)
            subprocess.run(["git", "-C", str(repo), "commit", "-qm", "policy"], check=True)
            sha = subprocess.run(["git", "-C", str(repo), "rev-parse", "HEAD"],
                                 capture_output=True, text=True, check=True).stdout.strip()
            subprocess.run(["git", "-C", str(repo), "update-ref", "refs/remotes/origin/main", sha],
                           check=True)
            # A pull request in the lane's checkout could edit the worktree copy;
            # the lane must still read origin/main's file.
            path.write_text(json.dumps({"rules": [{"type": "required_status_checks", "parameters": {
                "required_status_checks": [{"context": "ci-gate"}]}}]}), encoding="utf-8")
            self.assertEqual(
                lane.required_checks_from_rulesets("mojomast/workflow", repo=str(repo)),
                ("ci-gate", "gitleaks"))

    def test_ruleset_required_checks_are_read_from_the_policy_store(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "policy" / "rulesets").mkdir(parents=True)
            (root / "policy" / "rulesets" / "workflow.json").write_text(json.dumps({
                "rules": [{"type": "required_status_checks", "parameters": {
                    "required_status_checks": [{"context": "ci-gate"}, {"context": "gitleaks"}]}}],
            }), encoding="utf-8")
            self.assertEqual(
                lane.required_checks_from_rulesets("mojomast/workflow", root=root),
                ("ci-gate", "gitleaks"))
            self.assertEqual(
                lane.required_checks_from_rulesets("mojomast/unknown", root=root),
                ("ci-gate",))


class MergeExecutionTests(unittest.TestCase):
    def test_the_merge_matches_the_measured_head(self):
        self.assertEqual(
            lane.merge_command("mojomast/workflow", 7, HEAD),
            ["gh", "pr", "merge", "7", "--repo", "mojomast/workflow",
             "--squash", "--match-head-commit", HEAD],
        )

    def test_merge_runs_under_the_lane_guard(self):
        seen = {}

        def run(argv, **kwargs):
            seen["argv"] = argv
            seen["env"] = kwargs["env"]
            return types.SimpleNamespace(returncode=0, stdout="merged", stderr="")

        record = lane.merge_pull_request("mojomast/workflow", 7, HEAD, run=run)
        self.assertTrue(record["merged"], record)
        self.assertEqual(seen["argv"], lane.merge_command("mojomast/workflow", 7, HEAD))
        self.assertEqual(seen["env"][lane.MERGE_LANE_ENV], "1")

    def test_a_failed_merge_is_not_reported_as_merged(self):
        def run(argv, **kwargs):
            return types.SimpleNamespace(returncode=1, stdout="", stderr="head moved")

        record = lane.merge_pull_request("mojomast/workflow", 7, HEAD, run=run)
        self.assertFalse(record["merged"], record)
        self.assertIn("head moved", record["output"])

    def test_the_merge_is_verified_at_the_exact_head(self):
        api = FakeGitHub()
        api.pull = pull_request()
        api.pull["merged"] = True
        api.pull["merge_commit_sha"] = "e" * 40
        record = lane.verify_merge("mojomast/workflow", 7, HEAD, "token",
                                   api_url="https://api.test", http=api)
        self.assertTrue(record["verified"], record)
        self.assertEqual(record["merge_sha"], "e" * 40)

    def test_a_merge_at_another_head_is_not_verified(self):
        api = FakeGitHub()
        api.pull = pull_request(head=OLDER)
        api.pull["merged"] = True
        record = lane.verify_merge("mojomast/workflow", 7, HEAD, "token",
                                   api_url="https://api.test", http=api)
        self.assertFalse(record["verified"], record)
        self.assertEqual(record["reason"], "merge_unverified")


if __name__ == "__main__":
    unittest.main()
