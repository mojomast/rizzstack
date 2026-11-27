"""Pre-authorization policy and envelope schema (preauth-envelope re-cut of preauth-policy r3).

This test freezes the *content* of the policy before the checker exists, so the doc cannot
drift from the rules the checker enforces: the envelope fields, the seven-day ceiling, the
never-pre-authorizable classes, owner-signed approval (ssh-keygen -Y, namespace
rizzstack-preauth, key held only by the owner), immediate revocation, and the five initial
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

FIELDS = {"id", "action_class", "conditions", "max_uses", "expires_at", "created_from",
          "schema", "issuer", "issued_at", "nonce", "repos", "bases", "contracts", "budget"}


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

    def test_policy_requires_signed_approval_and_immediate_revocation(self):
        """A typed hash proves content, not who approved it: the owner signs the envelope."""
        self.assertIn("ssh-keygen -Y sign", self.text)
        self.assertIn("rizzstack-preauth", self.text)
        self.assertIn("policy/allowed_signers", self.text)
        self.assertIn("never on the hermes host", self.text.lower())
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

    def test_p1_rejects_a_refreeze_that_changes_frozen_test_blobs(self):
        """A re-freeze is not a spec change: P1 must require the frozen test blobs to be
        identical at the old and the new source_rev."""
        text = self.text.lower()
        self.assertIn("frozen test blobs", text)
        self.assertIn("identical at the old and new source_rev", text)

    def test_p1_is_a_numbered_condition_covering_conftest_and_fixtures(self):
        """P1 must be the first numbered envelope condition and must cover conftest, pytest
        configuration and fixtures, not just the phrase 'test blobs'."""
        self.assertRegex(self.text, r"(?m)^1\.\s+\*\*P1")
        section = self.text.lower().split("**p1", 1)[1].split("**p2", 1)[0]
        for token in ("frozen test blobs", "identical at the old and new source_rev",
                      "conftest", "pytest config", "fixtures"):
            self.assertIn(token, section)


if __name__ == "__main__":
    unittest.main()
