"""Pre-authorization policy and envelope schema (program: automated outside review, Phase 2).

This test freezes the *content* of the policy before the checker exists, so the doc cannot
drift from the rules the checker enforces: the envelope fields, the seven-day ceiling, the
never-pre-authorizable classes, hash approval, immediate revocation, and the five initial
envelopes the owner is asked to consider.

Run: python3 -m unittest discover -s tests -p 'test_preauth_policy.py' -v
"""

from __future__ import annotations

import json
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
POLICY = ROOT / "policy" / "preauth.md"
SCHEMA = ROOT / "schemas" / "preauth.schema.json"

FIELDS = {"id", "action_class", "conditions", "max_uses", "expires_at", "created_from"}


class PreauthPolicyTest(unittest.TestCase):
    def setUp(self) -> None:
        self.text = POLICY.read_text(encoding="utf-8")
        self.schema = json.loads(SCHEMA.read_text(encoding="utf-8"))

    def test_schema_requires_the_envelope_fields(self):
        self.assertLessEqual(FIELDS, set(self.schema["required"]))
        properties = self.schema["properties"]
        self.assertEqual(properties["max_uses"]["type"], "integer")
        self.assertEqual(properties["max_uses"]["minimum"], 1)
        self.assertEqual(properties["conditions"]["type"], "array")
        self.assertEqual(properties["expires_at"]["format"], "date-time")

    def test_policy_states_the_seven_day_ceiling(self):
        self.assertIn("7 days", self.text)
        self.assertIn("expires_at", self.text)

    def test_policy_names_every_never_preauthorizable_class(self):
        for phrase in (
            "gate", "merge lane", "policy/**", "rulesets", "preauth", "secrets",
            "force-push", "deleting data", "automatic rollback",
        ):
            self.assertIn(phrase, self.text.lower())

    def test_policy_requires_hash_approval_and_immediate_revocation(self):
        self.assertIn("approved: preauth <hash>", self.text)
        self.assertIn("revoke preauth <id>", self.text)
        self.assertIn("effective immediately", self.text)
        self.assertIn("never create, widen or extend", self.text)

    def test_policy_proposes_the_five_initial_envelopes(self):
        for marker in ("P1", "P2", "P3", "P4", "P5"):
            self.assertIn(marker, self.text)
        for condition in (
            "source_rev", "RED evidence", "byte-identical", "superseding PR",
            "health check", "automatic rollback", "dependency order", "4 bees in flight",
        ):
            self.assertIn(condition, self.text)

    def test_policy_requires_mechanical_conditions_and_a_daily_digest(self):
        self.assertIn("prove every condition from data", self.text.lower())
        self.assertIn("daily digest", self.text.lower())
        self.assertIn("ledger", self.text.lower())


if __name__ == "__main__":
    unittest.main()

    def test_p1_rejects_a_refreeze_that_changes_frozen_test_blobs(self):
        """A re-freeze is not a spec change: P1 must require the frozen test blobs to be
        identical at the old and the new source_rev."""
        text = self.text.lower()
        self.assertIn("frozen test blobs", text)
        self.assertIn("identical at the old and new source_rev", text)
