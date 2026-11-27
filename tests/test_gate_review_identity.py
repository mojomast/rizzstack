"""The gate accepts an independent approval only from the reviewer app or the owner (Phase 3).

Today the gate trusts the *text* of a review: any account that can comment can mint
``APPROVE (independent review …)``. Phase 3 gives the reviewer its own GitHub App, so the
gate must also check the review's author login: ``rizzstack-reviewer[bot]`` or the owner's
login. A rejection stays valid whatever account posted it — findings are always welcome.

Run: python3 -m unittest discover -s tests -p 'test_gate_review_identity.py' -v
"""

from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / ".github" / "scripts" / "lease_tamper.py"

HEAD = "1a1d032f98691fb4121d5b9673b36562c77a5627"
BASE = "316087f7da8ea01a206b4c6417c2a1599e84b35c"
LINE = "APPROVE (independent review — rizzstack-reviewer, model family kimi) at " + HEAD


def load_gate():
    spec = importlib.util.spec_from_file_location("lease_tamper_identity_under_test", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


GATE = load_gate()


def _review(login, body=LINE, state="COMMENTED", commit=HEAD):
    review = {
        "state": state,
        "body": body,
        "commit_id": commit,
        "submitted_at": "2026-11-27T20:00:00Z",
    }
    if login is not None:
        review["user"] = {"login": login}
    return review


class ReviewIdentityTest(unittest.TestCase):
    def test_reviewer_app_login_is_accepted(self):
        review, match, _ = GATE.independent_review(None, [_review("rizzstack-reviewer[bot]")], BASE, HEAD)
        self.assertIsNotNone(review)
        self.assertEqual(match, GATE.REVIEW_MATCH_EXACT)

    def test_owner_login_is_accepted(self):
        review, match, _ = GATE.independent_review(None, [_review("mojomast")], BASE, HEAD)
        self.assertIsNotNone(review)
        self.assertEqual(match, GATE.REVIEW_MATCH_EXACT)

    def test_any_other_login_is_not_an_approval(self):
        review, match, refusal = GATE.independent_review(None, [_review("some-agent")], BASE, HEAD)
        self.assertIsNone(review)
        self.assertIsNone(match)

    def test_a_review_without_a_login_is_not_an_approval(self):
        review, _, _ = GATE.independent_review(None, [_review(None)], BASE, HEAD)
        self.assertIsNone(review)

    def test_identity_helper_names_the_two_allowed_identities(self):
        self.assertTrue(GATE.review_identity_ok(_review("rizzstack-reviewer[bot]")))
        self.assertTrue(GATE.review_identity_ok(_review("mojomast")))
        self.assertFalse(GATE.review_identity_ok(_review("someone-else")))
        self.assertFalse(GATE.review_identity_ok({}))

    def test_a_rejection_from_any_login_still_governs(self):
        body = "CHANGES REQUESTED (gate bypass) at " + HEAD
        form, _ = GATE.review_form(_review("some-agent", body=body, state="CHANGES_REQUESTED"))
        self.assertEqual(form, GATE.REVIEW_CHANGES_REQUESTED)


if __name__ == "__main__":
    unittest.main()
