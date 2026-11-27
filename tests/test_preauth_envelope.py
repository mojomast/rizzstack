"""Signed pre-authorization envelopes (S2, preauth-envelope).

The owner signs one bounded batch with ``ssh-keygen -Y sign -n rizzstack-preauth``; the key
never leaves the owner's machine and the repository holds only ``policy/allowed_signers``
(public keys). ``scripts/preauth.py`` verifies an envelope and decides one use of it from
measured facts only, and every doubt is a named refusal:

expired, not yet valid, longer than seven days, revoked, bad or foreign signature, wrong
namespace, unknown issuer, wrong repository, a base that does not descend from the signed
base, a contract the envelope does not list, a re-freeze that changes frozen tests, conftest,
pytest configuration, fixtures or import hooks, exhausted budget or uses, a replayed use and
a reused nonce.

Every key in these tests is generated in a temporary directory at run time.

Run: python3 -m pytest -q -p no:cacheprovider tests/test_preauth_envelope.py
"""

from __future__ import annotations

import importlib.util
import json
import shutil
import subprocess
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MODULE = ROOT / "scripts" / "preauth.py"
SIGNERS = ROOT / "policy" / "allowed_signers"
SCHEMA = ROOT / "schemas" / "preauth.schema.json"

REPO = "mojomast/bundlebees"
SIGNED_BASE = "a" * 40
CURRENT_BASE = "b" * 40
CONTRACT = "7ef0c9d0826186d5fe5e7188b3e8c56268feb5ed73928963fd442a4f49f56d08"
SOURCE_REV = "758e0b6e" + "0" * 32
NOW = datetime(2026, 11, 28, 15, 0, tzinfo=timezone.utc)


def load_module():
    spec = importlib.util.spec_from_file_location("preauth_under_test", MODULE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def stamp(moment):
    return moment.strftime("%Y-%m-%dT%H:%M:%SZ")


def envelope(**overrides):
    value = {
        "schema": "rizzstack.preauth/2",
        "id": "pa-20261128-01",
        "issuer": "kyle",
        "issued_at": stamp(NOW - timedelta(hours=1)),
        "expires_at": stamp(NOW + timedelta(days=2)),
        "nonce": "4f1c2a9e8b7d6c5b4a39281706f5e4d3",
        "action_class": "merge_slice",
        "repos": [REPO],
        "bases": {REPO: SIGNED_BASE},
        "contracts": [{"repo": REPO, "slice": "review-packet", "contract_sha256": CONTRACT,
                       "source_rev": SOURCE_REV, "max_tier": "T3"}],
        "review": {"t3_reviewer_login": "rizzstack-reviewer[bot]"},
        "budget": {"max_merges": 2, "max_bees": 3, "max_attempts_per_contract": 3},
        "conditions": [{"name": "P1", "fact": "frozen_oracle_identical", "value": True}],
        "max_uses": 2,
        "created_from": "owner-chat:t1760",
    }
    value.update(overrides)
    return value


class Keys:
    """A throwaway owner key and an allowed_signers file, both in a temp dir."""

    def __init__(self, principal="kyle", namespaces="rizzstack-preauth"):
        self.dir = Path(tempfile.mkdtemp(prefix="preauth-keys-"))
        self.key = self.dir / "owner"
        subprocess.run(["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-C", "test-owner",
                        "-f", str(self.key)], check=True, capture_output=True)
        public = (self.dir / "owner.pub").read_text().split()
        self.signers = self.dir / "allowed_signers"
        self.signers.write_text('%s namespaces="%s" %s %s\n' % (principal, namespaces, public[0], public[1]))

    def sign(self, data: bytes, namespace="rizzstack-preauth") -> bytes:
        message = self.dir / "envelope.json"
        message.write_bytes(data)
        sig = self.dir / "envelope.json.sig"
        if sig.exists():
            sig.unlink()
        subprocess.run(["ssh-keygen", "-q", "-Y", "sign", "-f", str(self.key), "-n", namespace,
                        str(message)], check=True, capture_output=True)
        return sig.read_bytes()

    def close(self):
        shutil.rmtree(self.dir, ignore_errors=True)


def facts(**overrides):
    value = {"revoked": [], "consumed": [], "nonces": {}, "oracle": None}
    value.update(overrides)
    return value


class EnvelopeCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.pre = load_module()
        cls.keys = Keys()

    @classmethod
    def tearDownClass(cls):
        cls.keys.close()

    def decide(self, value=None, *, signature=None, data=None, contract=CONTRACT, repo=REPO,
               base=CURRENT_BASE, now=NOW, measured=None, signers=None,
               is_ancestor=lambda older, newer: older == SIGNED_BASE and newer == CURRENT_BASE):
        data = data if data is not None else json.dumps(value if value is not None else envelope(),
                                                        sort_keys=True).encode("utf-8")
        signature = signature if signature is not None else self.keys.sign(data)
        return self.pre.check(
            data, signature, allowed_signers=str(signers or self.keys.signers), repo=repo,
            base_rev=base, contract_sha256=contract, now=now,
            facts=measured if measured is not None else facts(), is_ancestor=is_ancestor)

    def refused(self, reason, *args, **kwargs):
        with self.assertRaises(self.pre.PreauthRefused) as caught:
            self.decide(*args, **kwargs)
        self.assertEqual(caught.exception.reason, reason)


class SignatureTest(EnvelopeCase):
    def test_valid_signed_envelope_authorizes_one_listed_contract(self):
        decision = self.decide()
        self.assertTrue(decision["ok"])
        self.assertEqual(decision["envelope_id"], "pa-20261128-01")
        self.assertEqual(decision["contract_sha256"], CONTRACT)
        self.assertEqual(decision["max_tier"], "T3")

    def test_tampered_envelope_is_a_bad_signature(self):
        data = json.dumps(envelope(), sort_keys=True).encode()
        signature = self.keys.sign(data)
        tampered = json.dumps(envelope(max_uses=99), sort_keys=True).encode()
        self.refused("envelope_bad_signature", data=tampered, signature=signature)

    def test_foreign_key_is_a_bad_signature(self):
        other = Keys()
        try:
            data = json.dumps(envelope(), sort_keys=True).encode()
            self.refused("envelope_bad_signature", data=data, signature=other.sign(data))
        finally:
            other.close()

    def test_wrong_namespace_is_a_bad_signature(self):
        data = json.dumps(envelope(), sort_keys=True).encode()
        self.refused("envelope_bad_signature", data=data,
                     signature=self.keys.sign(data, namespace="git"))

    def test_garbage_signature_is_a_bad_signature(self):
        self.refused("envelope_bad_signature", signature=b"not a signature")

    def test_issuer_must_be_the_signing_principal(self):
        self.refused("envelope_bad_signature", envelope(issuer="coordinator"))

    def test_schema_invalid_envelope_refuses_before_anything_else(self):
        value = envelope()
        del value["nonce"]
        self.refused("envelope_schema_invalid", value)
        self.refused("envelope_schema_invalid", envelope(unexpected="field"))
        self.refused("envelope_schema_invalid", data=b'{"id": "a", "id": "b"}')
        self.refused("envelope_schema_invalid", data=b"[]")


class ValidityTest(EnvelopeCase):
    def test_expired_envelope_refuses(self):
        self.refused("envelope_expired", now=NOW + timedelta(days=3))

    def test_not_yet_valid_envelope_refuses(self):
        self.refused("envelope_not_yet_valid", now=NOW - timedelta(days=1))

    def test_longer_than_seven_days_refuses(self):
        self.refused("envelope_ttl_exceeded",
                     envelope(expires_at=stamp(NOW - timedelta(hours=1) + timedelta(days=7, seconds=1))))

    def test_revoked_envelope_refuses_immediately(self):
        self.refused("envelope_revoked", measured=facts(revoked=["pa-20261128-01"]))


class ScopeTest(EnvelopeCase):
    def test_wrong_repository_refuses(self):
        self.refused("envelope_repo_mismatch", repo="mojomast/hyvv")

    def test_base_that_does_not_descend_from_the_signed_base_refuses(self):
        self.refused("envelope_base_mismatch", is_ancestor=lambda older, newer: False)
        self.refused("envelope_base_mismatch", is_ancestor=None)

    def test_contract_not_listed_refuses(self):
        self.refused("envelope_contract_not_listed", contract="c" * 64)

    def test_forbidden_action_class_refuses(self):
        for action in ("merge_gate_change", "ruleset_change", "deploy", "force_push", "preauth_widen"):
            self.refused("envelope_action_forbidden", envelope(action_class=action))


class OracleTest(EnvelopeCase):
    OLD = {"tests/test_review_packet.py": "1" * 40, "tests/conftest.py": "2" * 40,
           "pyproject.toml": "3" * 40, "pytest.ini": "4" * 40, "tests/fixtures/case.json": "5" * 40,
           "sitecustomize.py": "6" * 40, "src/thing.py": "7" * 40}

    def refreeze(self):
        entry = dict(envelope()["contracts"][0])
        entry["refreeze_of"] = {"contract_sha256": "d" * 64, "source_rev": "e" * 40}
        entry["test_files"] = ["tests/test_review_packet.py"]
        return envelope(contracts=[entry])

    def test_refreeze_with_an_identical_oracle_is_allowed(self):
        new = dict(self.OLD, **{"src/thing.py": "8" * 40})  # product code may differ
        self.assertTrue(self.decide(self.refreeze(),
                                    measured=facts(oracle={"old": self.OLD, "new": new}))["ok"])

    def test_refreeze_that_changes_any_oracle_file_refuses(self):
        for path in ("tests/test_review_packet.py", "tests/conftest.py", "pyproject.toml",
                     "pytest.ini", "tests/fixtures/case.json", "sitecustomize.py"):
            new = dict(self.OLD, **{path: "9" * 40})
            self.refused("envelope_oracle_changed", self.refreeze(),
                         measured=facts(oracle={"old": self.OLD, "new": new}))

    def test_refreeze_that_adds_or_removes_an_oracle_file_refuses(self):
        added = dict(self.OLD, **{"tests/plugins/hook.py": "a" * 40, "x.pth": "b" * 40})
        self.refused("envelope_oracle_changed", self.refreeze(),
                     measured=facts(oracle={"old": self.OLD, "new": added}))
        removed = {k: v for k, v in self.OLD.items() if k != "tests/conftest.py"}
        self.refused("envelope_oracle_changed", self.refreeze(),
                     measured=facts(oracle={"old": self.OLD, "new": removed}))

    def test_refreeze_without_measured_oracle_refuses(self):
        self.refused("envelope_oracle_unproven", self.refreeze(), measured=facts(oracle=None))

    def test_oracle_paths_cover_tests_conftest_config_fixtures_and_hooks(self):
        paths = self.pre.oracle_paths(
            ["tests/test_a.py", "src/x.py", "tests/conftest.py", "a/b/conftest.py", "pytest.ini",
             "tox.ini", "setup.cfg", "pyproject.toml", "tests/fixtures/f.json", "tests/data/d.txt",
             "sitecustomize.py", "usercustomize.py", "x.pth", "tests/plugins/p.py", "docs/a.md"],
            test_files=["tests/test_a.py"])
        self.assertEqual(paths, {"tests/test_a.py", "tests/conftest.py", "a/b/conftest.py",
                                 "pytest.ini", "tox.ini", "setup.cfg", "pyproject.toml",
                                 "tests/fixtures/f.json", "tests/data/d.txt", "sitecustomize.py",
                                 "usercustomize.py", "x.pth", "tests/plugins/p.py"})


class BudgetTest(EnvelopeCase):
    def test_exhausted_merge_budget_refuses(self):
        used = [{"envelope_id": "pa-20261128-01", "nonce": "4f1c2a9e8b7d6c5b4a39281706f5e4d3",
                 "contract_sha256": "f" * 64}] * 2
        self.refused("envelope_budget_exhausted", measured=facts(consumed=used))

    def test_max_uses_is_a_ceiling_too(self):
        used = [{"envelope_id": "pa-20261128-01", "nonce": "4f1c2a9e8b7d6c5b4a39281706f5e4d3",
                 "contract_sha256": "f" * 64}]
        self.refused("envelope_budget_exhausted", envelope(max_uses=1), measured=facts(consumed=used))

    def test_replayed_use_of_the_same_contract_refuses(self):
        used = [{"envelope_id": "pa-20261128-01", "nonce": "4f1c2a9e8b7d6c5b4a39281706f5e4d3",
                 "contract_sha256": CONTRACT}]
        self.refused("envelope_replayed", measured=facts(consumed=used))

    def test_nonce_reused_by_another_envelope_refuses(self):
        self.refused("envelope_nonce_reused",
                     measured=facts(nonces={"4f1c2a9e8b7d6c5b4a39281706f5e4d3": "pa-20261101-07"}))


class KeyCustodyTest(unittest.TestCase):
    def test_module_verifies_and_never_signs(self):
        pre = load_module()
        self.assertEqual(pre.NAMESPACE, "rizzstack-preauth")
        self.assertEqual(pre.MAX_TTL_SECONDS, 7 * 24 * 3600)
        for name in ("sign", "sign_envelope", "mint", "generate_key"):
            self.assertFalse(hasattr(pre, name), name)
        source = MODULE.read_text(encoding="utf-8")
        self.assertNotIn('"-Y", "sign"', source)
        self.assertNotIn("PRIVATE KEY", source)

    def test_allowed_signers_holds_public_keys_only_and_is_namespace_bound(self):
        self.assertTrue(SIGNERS.is_file(), "missing policy/allowed_signers")
        for line in SIGNERS.read_text(encoding="utf-8").splitlines():
            if not line.strip() or line.lstrip().startswith("#"):
                continue
            self.assertIn('namespaces="rizzstack-preauth"', line)
            self.assertRegex(line, r"\s(ssh-ed25519|sk-ssh-ed25519@openssh\.com)\s")
        tracked = subprocess.run(["git", "-C", str(ROOT), "ls-files"], capture_output=True,
                                 text=True, check=True).stdout.split()
        marker = "-----BEGIN " + "OPENSSH PRIVATE KEY-----"
        for path in tracked:
            target = ROOT / path
            if target.is_file() and target.stat().st_size < 1_000_000:
                self.assertNotIn(marker, target.read_text(encoding="utf-8", errors="ignore"), path)

    def test_schema_matches_the_checker(self):
        pre = load_module()
        schema = json.loads(SCHEMA.read_text(encoding="utf-8"))
        self.assertEqual(set(schema["required"]), set(pre.REQUIRED_FIELDS))
        self.assertFalse(schema["additionalProperties"])


if __name__ == "__main__":
    unittest.main()
