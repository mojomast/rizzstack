"""Signed pre-authorization envelopes (S2 r2, preauth-envelope).

The owner signs one bounded batch with ``ssh-keygen -Y sign -n rizzstack-preauth``; the key
never leaves the owner's machine.  The trust root ``policy/allowed_signers`` is read-only and
its bytes are pinned here, and the checker also requires every signer key's fingerprint to be
in a trusted set the caller reads from the frozen owner pin, so an implementer-added key is
refused.  ``scripts/preauth.py`` verifies an envelope and decides one use of it from measured
facts only; every doubt is a named refusal, including missing state (fail closed).

Production code calls ``check()``, which reads ``policy/allowed_signers`` from the checkout the
module itself lives in (the base checkout) and pins it to the module constant
``OWNER_FINGERPRINTS``; it takes no trust-root override.  The decision tests below drive the
private, test-only ``_check_for_tests()``, which accepts a temporary trust root and pin; the module
never calls it, and the gate and lane may reference only ``check`` and ``PreauthRefused`` (frozen
here for ``preauth-wiring``).

r5 structure: the only code that starts a process is the frozen ``scripts/preauth_verify.py``
(committed at RED and pinned by sha256 here and in the contract).  It runs exactly one
``/usr/bin/ssh-keygen -Y verify`` with a constant environment and a bounded timeout, and every
name it defines is private.  The implementer's ``scripts/preauth.py`` (envelope parsing,
policy, facts) imports its single entry point ``_ssh_verify`` by name and may have no process,
filesystem-write or tempfile access at all: ``preauth_violations`` is a strict AST allowlist
over it.  ``process_violations`` (the r4 one-process rule, hardened against GROKCHAT's round-4
bypasses) still pins the verify module and refuses every earlier stub.

Every checker call in this file runs with process creation recorded: the only program ever
started is ``/usr/bin/ssh-keygen -Y verify``, never ``-Y sign``.

Wiring the checker into the gate/lane is the separate named slice ``preauth-wiring``; its frozen
rules (``WiringFreezeTest``) scan every file under .github/ and scripts/ now.

Every key in these tests is generated in a temporary directory at run time.

Run: python3 -m pytest -q -p no:cacheprovider tests/test_preauth_envelope.py
"""

from __future__ import annotations

import ast
import base64
import hashlib
import importlib.util
import inspect
import json
import os
import re
import shutil
import contextlib
import io
import subprocess
import sys
import tempfile
import types
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
MODULE = ROOT / "scripts" / "preauth.py"
SIGNERS = ROOT / "policy" / "allowed_signers"
SCHEMA = ROOT / "schemas" / "preauth.schema.json"
PROCESS_FIXTURES = ROOT / "tests" / "fixtures" / "preauth"
SCRIPTS = ROOT / "scripts"
VERIFY_MODULE = SCRIPTS / "preauth_verify.py"
#: The frozen verifier.  Changing scripts/preauth_verify.py needs a new owner-approved contract.
PREAUTH_VERIFY_SHA256 = "181d5ab1ebb23d7bb27d55ab44e57f6c2e213b834c4a95ee5e226075935143a9"  # gitleaks:allow (sha256 of a committed file, not a secret)
VERIFY_ENTRY = "_ssh_verify"

#: The frozen trust root.  Changing policy/allowed_signers needs a new owner-approved contract.
ALLOWED_SIGNERS_SHA256 = "be7d243277ad0073c9d03dc09355f1b769824cde7c2ed0b662c0c190d19973ee"
#: The owner fingerprints the trust root may name.  Empty until the owner adds a key.
OWNER_FINGERPRINTS: frozenset = frozenset()

ALLOWED_ACTIONS = {"merge_slice", "refreeze_contract", "vendor_sync", "close_superseded_pr", "dispatch_wave"}
REPO = "mojomast/bundlebees"
OTHER_REPO = "mojomast/hyvv"
SIGNED_BASE = "a" * 40
CURRENT_BASE = "b" * 40
CONTRACT = "7ef0c9d0826186d5fe5e7188b3e8c56268feb5ed73928963fd442a4f49f56d08"
SOURCE_REV = "758e0b6e" + "0" * 32
NONCE = "4f1c2a9e8b7d6c5b4a39281706f5e4d3"
ENVELOPE_ID = "pa-20261128-01"
NOW = datetime(2026, 11, 28, 15, 0, tzinfo=timezone.utc)


def _scripts_on_path():
    # preauth.py imports the frozen verifier by name, as the lane does when it runs from scripts/
    if str(SCRIPTS) not in sys.path:
        sys.path.insert(0, str(SCRIPTS))


def load_module():
    _scripts_on_path()
    spec = importlib.util.spec_from_file_location("preauth_under_test", MODULE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def stamp(moment):
    return moment.strftime("%Y-%m-%dT%H:%M:%SZ")


def envelope(**overrides):
    value = {
        "schema": "rizzstack.preauth/2",
        "id": ENVELOPE_ID,
        "issuer": "kyle",
        "issued_at": stamp(NOW - timedelta(hours=1)),
        "expires_at": stamp(NOW + timedelta(days=2)),
        "nonce": NONCE,
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


def fingerprint(public_line):
    blob = base64.b64decode(public_line.split()[1])
    return "SHA256:" + base64.b64encode(hashlib.sha256(blob).digest()).decode().rstrip("=")


class Keys:
    """A throwaway owner key and an allowed_signers file, both in a temp dir."""

    def __init__(self, principal="kyle", namespaces="rizzstack-preauth", options=None):
        self.dir = Path(tempfile.mkdtemp(prefix="preauth-keys-"))
        self.key = self.dir / "owner"
        subprocess.run(["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-C", "test-owner",
                        "-f", str(self.key)], check=True, capture_output=True)
        self.public = (self.dir / "owner.pub").read_text().strip()
        self.fingerprint = fingerprint(self.public)
        parts = self.public.split()
        opts = options if options is not None else 'namespaces="%s"' % namespaces
        self.line = "%s %s %s %s" % (principal, opts, parts[0], parts[1])
        self.signers = self.write_signers([self.line])

    def write_signers(self, lines, name="allowed_signers"):
        path = self.dir / name
        path.write_text("".join(line + "\n" for line in lines))
        return path

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
    value = {"revoked": [], "consumed": [], "nonces": {}, "attempts": {}, "bees_in_flight": 0,
             "oracle": None}
    value.update(overrides)
    return value


def used(n, envelope_id=ENVELOPE_ID, contract="f" * 64):
    return [{"envelope_id": envelope_id, "nonce": NONCE, "contract_sha256": contract}] * n


class ProcessRecorder:
    """Record every process the checker starts; refuse anything but ssh-keygen -Y verify."""

    def __init__(self, case, fail=None):
        self.case, self.fail, self.calls, self.timeouts = case, fail, [], []
        self.envs, self.executables = [], []
        self.inside = False
        self.hook = None
        self._real_popen = subprocess.Popen

    def popen(self, args, *a, **k):
        argv = [str(x) for x in (args if isinstance(args, (list, tuple)) else [args])]
        self.calls.append(argv)
        self.envs.append(k.get("env"))
        self.executables.append(k.get("executable"))
        if self.fail == "missing":
            raise FileNotFoundError(2, "No such file or directory", argv[0])
        if self.fail == "timeout":
            return _HangingProcess(argv, self)
        if self.hook is not None:
            self.hook(argv)
        self.inside = True  # the recorded Popen may itself use os.posix_spawn/fork_exec
        try:
            return self._real_popen(args, *a, **k)
        finally:
            self.inside = False

    def forbid(self, name):
        real = getattr(os, name.split(".", 1)[-1])

        def refuse(*a, **k):
            if self.inside:
                return real(*a, **k)
            self.calls.append([name] + [str(x) for x in a])
            raise AssertionError("checker started a process via %s" % name)
        return refuse

    def __enter__(self):
        self.environ_before = dict(os.environ)
        self.patches = [mock.patch("subprocess.Popen", self.popen)]
        for name in ("system", "popen", "posix_spawn", "posix_spawnp", "execv", "execve", "execvp",
                     "spawnv", "spawnve", "fork", "forkpty"):
            if hasattr(os, name):
                self.patches.append(mock.patch("os." + name, self.forbid("os." + name)))
        for p in self.patches:
            p.start()
        return self

    def __exit__(self, *exc):
        for p in reversed(self.patches):
            p.stop()
        for argv in self.calls:
            self.case.assertTrue(argv and os.path.basename(argv[0]) == "ssh-keygen", argv)
            self.case.assertEqual(argv[1:3], ["-Y", "verify"], argv)
            self.case.assertNotIn("sign", argv[1:3], argv)
            # r4: absolute executable, resolved outside any temp dir, and a constant environment
            self.case.assertEqual(argv[0], "/usr/bin/ssh-keygen", argv)
            resolved = os.path.realpath(argv[0])
            self.case.assertFalse(resolved.startswith(os.path.realpath(tempfile.gettempdir()) + os.sep), resolved)
        for env in self.envs:
            self.case.assertEqual(env, {"PATH": "/usr/bin:/bin"}, "ssh-keygen must run with a constant env")
        for executable in self.executables:
            self.case.assertIsNone(executable)
        self.case.assertEqual(dict(os.environ), self.environ_before, "the checker changed os.environ")
        return False


class _HangingProcess:
    def __init__(self, argv, recorder):
        self.args, self.recorder, self.returncode, self.pid = argv, recorder, None, 0
        self.stdin = self.stdout = self.stderr = None

    def communicate(self, input=None, timeout=None):
        self.recorder.timeouts.append(timeout)
        raise subprocess.TimeoutExpired(self.args, timeout or 0)

    def wait(self, timeout=None):
        self.recorder.timeouts.append(timeout)
        if self.returncode is None and timeout is not None:
            raise subprocess.TimeoutExpired(self.args, timeout)
        return self.returncode

    def poll(self):
        return self.returncode

    def kill(self):
        self.returncode = -9

    terminate = kill

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.kill()
        return False


class EnvelopeCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.pre = load_module()
        cls.keys = Keys()

    @classmethod
    def tearDownClass(cls):
        cls.keys.close()

    def decide(self, value=None, *, signature=None, data=None, contract=CONTRACT, repo=REPO,
               base=CURRENT_BASE, tier="T3", now=NOW, measured=None, signers=None, trusted=None,
               fail=None, is_ancestor=lambda older, newer: older == SIGNED_BASE and newer == CURRENT_BASE):
        data = data if data is not None else json.dumps(value if value is not None else envelope(),
                                                        sort_keys=True).encode("utf-8")
        signature = signature if signature is not None else self.keys.sign(data)
        with ProcessRecorder(self, fail) as self.recorder:
            return self.pre._check_for_tests(
                data, signature, allowed_signers=str(signers or self.keys.signers),
                trusted_fingerprints=frozenset(trusted if trusted is not None else {self.keys.fingerprint}),
                repo=repo, base_rev=base, contract_sha256=contract, tier=tier, now=now,
                facts=measured if measured is not None else facts(), is_ancestor=is_ancestor)

    def refused(self, reason, *args, **kwargs):
        with self.assertRaises(self.pre.PreauthRefused) as caught:
            self.decide(*args, **kwargs)
        self.assertEqual(caught.exception.reason, reason)


class SignatureTest(EnvelopeCase):
    def test_valid_signed_envelope_authorizes_one_listed_contract(self):
        decision = self.decide()
        self.assertTrue(decision["ok"])
        self.assertEqual(decision["envelope_id"], ENVELOPE_ID)
        self.assertEqual(decision["contract_sha256"], CONTRACT)
        self.assertEqual(decision["max_tier"], "T3")
        self.assertEqual(len(self.recorder.calls), 1, self.recorder.calls)

    def test_tampered_envelope_is_a_bad_signature(self):
        data = json.dumps(envelope(), sort_keys=True).encode()
        signature = self.keys.sign(data)
        tampered = json.dumps(envelope(max_uses=1), sort_keys=True).encode()
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
        self.refused("envelope_bad_signature", data=data, signature=self.keys.sign(data, namespace="git"))

    def test_garbage_signature_is_a_bad_signature(self):
        self.refused("envelope_bad_signature", signature=b"not a signature")

    def test_issuer_must_be_the_signing_principal(self):
        self.refused("envelope_bad_signature", envelope(issuer="coordinator"))

    def test_multi_principal_trust_root_binds_issuer_to_its_own_key(self):
        other = Keys(principal="bob")
        try:
            signers = self.keys.write_signers([self.keys.line, other.line], "two_signers")
            trusted = {self.keys.fingerprint, other.fingerprint}
            data = json.dumps(envelope(issuer="bob"), sort_keys=True).encode()
            self.refused("envelope_bad_signature", data=data, signature=self.keys.sign(data),
                         signers=signers, trusted=trusted)
            self.assertTrue(self.decide(data=data, signature=other.sign(data), signers=signers,
                                        trusted=trusted)["ok"])
        finally:
            other.close()

    def test_schema_invalid_envelope_refuses_before_anything_else(self):
        value = envelope()
        del value["nonce"]
        self.refused("envelope_schema_invalid", value)
        self.refused("envelope_schema_invalid", envelope(unexpected="field"))
        self.refused("envelope_schema_invalid", data=b'{"id": "a", "id": "b"}')
        self.refused("envelope_schema_invalid", data=b"[]")
        self.refused("envelope_schema_invalid", data=b"\xff\xfe")
        self.assertEqual(self.recorder.calls, [])


class TrustRootTest(EnvelopeCase):
    def test_key_not_in_the_owner_pin_is_refused(self):
        self.refused("envelope_signer_untrusted", trusted=set())
        self.refused("envelope_signer_untrusted", trusted={"SHA256:" + "A" * 43})
        self.assertEqual(self.recorder.calls, [])

    def test_implementer_added_key_beside_the_owner_key_is_refused(self):
        intruder = Keys(principal="kyle")
        try:
            signers = self.keys.write_signers([self.keys.line, intruder.line], "with_intruder")
            data = json.dumps(envelope(), sort_keys=True).encode()
            self.refused("envelope_signer_untrusted", data=data, signature=intruder.sign(data),
                         signers=signers)
            self.refused("envelope_signer_untrusted", data=data, signature=self.keys.sign(data),
                         signers=signers)
        finally:
            intruder.close()

    def test_cert_authority_wildcard_and_malformed_trust_lines_are_refused(self):
        key = self.keys.public.split()
        bad_lines = (
            'kyle cert-authority,namespaces="rizzstack-preauth" %s %s' % (key[0], key[1]),
            '* namespaces="rizzstack-preauth" %s %s' % (key[0], key[1]),
            'ky?e namespaces="rizzstack-preauth" %s %s' % (key[0], key[1]),
            'kyle,bob namespaces="rizzstack-preauth" %s %s' % (key[0], key[1]),
            '!kyle namespaces="rizzstack-preauth" %s %s' % (key[0], key[1]),
            'kyle namespaces="rizzstack-preauth,git" %s %s' % (key[0], key[1]),
            'kyle namespaces="*" %s %s' % (key[0], key[1]),
            'kyle %s %s' % (key[0], key[1]),
            'kyle namespaces="rizzstack-preauth" ssh-rsa %s' % key[1],
            'kyle namespaces="rizzstack-preauth" %s' % key[0],
            'kyle namespaces="rizzstack-preauth" %s not-base64!' % key[0],
        )
        for line in bad_lines:
            signers = self.keys.write_signers([line], "bad_signers")
            self.refused("envelope_signers_invalid", signers=signers)
        self.refused("envelope_signers_invalid", signers=self.keys.dir / "missing_file")

    def test_empty_trust_root_refuses_every_envelope(self):
        signers = self.keys.write_signers(["# comments only"], "empty_signers")
        self.refused("envelope_signer_untrusted", signers=signers)


class ValidityTest(EnvelopeCase):
    def test_expired_envelope_refuses(self):
        self.refused("envelope_expired", now=NOW + timedelta(days=3))
        self.refused("envelope_expired", now=NOW + timedelta(days=2))  # expires_at itself is expired

    def test_not_yet_valid_envelope_refuses(self):
        self.refused("envelope_not_yet_valid", now=NOW - timedelta(days=1))

    def test_longer_than_seven_days_refuses(self):
        self.refused("envelope_ttl_exceeded",
                     envelope(expires_at=stamp(NOW - timedelta(hours=1) + timedelta(days=7, seconds=1))))

    def test_expiry_before_issue_and_malformed_timestamps_refuse(self):
        self.refused("envelope_schema_invalid", envelope(expires_at=stamp(NOW - timedelta(hours=2))))
        self.refused("envelope_schema_invalid", envelope(expires_at=stamp(NOW - timedelta(hours=1))))
        for bad in ("2026-11-29T15:00:00", "2026-11-29T15:00:00+00:00", "2026-11-29 15:00:00Z",
                    "2026-13-29T15:00:00Z", "tomorrow", 1764428400, None):
            self.refused("envelope_schema_invalid", envelope(expires_at=bad))
            self.refused("envelope_schema_invalid", envelope(issued_at=bad))

    def test_naive_or_missing_clock_refuses(self):
        self.refused("envelope_clock_invalid", now=NOW.replace(tzinfo=None))
        self.refused("envelope_clock_invalid", now=None)
        self.refused("envelope_clock_invalid", now="2026-11-28T15:00:00Z")

    def test_issuer_that_could_be_read_as_an_option_or_pattern_refuses(self):
        for issuer in ("*", "ky*", "kyle,bob", "-kyle", "kyle bob", "", " kyle", "kyle\n", "!kyle", 7):
            self.refused("envelope_schema_invalid", envelope(issuer=issuer))

    def test_revoked_envelope_refuses_immediately(self):
        self.refused("envelope_revoked", measured=facts(revoked=[ENVELOPE_ID]))


class FactsFailClosedTest(EnvelopeCase):
    def test_missing_state_refuses_with_a_named_reason(self):
        for key in ("revoked", "consumed", "nonces", "attempts", "bees_in_flight"):
            value = facts()
            del value[key]
            self.refused("envelope_facts_unproven", measured=value)

    def test_null_or_mistyped_state_refuses(self):
        for key, bad in (("revoked", None), ("revoked", "pa-1"), ("consumed", None), ("consumed", {}),
                         ("nonces", None), ("nonces", []), ("attempts", None), ("attempts", []),
                         ("bees_in_flight", None), ("bees_in_flight", "0"), ("bees_in_flight", -1),
                         ("bees_in_flight", True)):
            self.refused("envelope_facts_unproven", measured=facts(**{key: bad}))
        self.refused("envelope_facts_unproven", measured=[])


class ScopeTest(EnvelopeCase):
    def test_wrong_repository_refuses(self):
        self.refused("envelope_repo_mismatch", repo=OTHER_REPO)

    def test_contract_listed_only_under_another_repo_refuses(self):
        value = envelope(repos=[REPO, OTHER_REPO], bases={REPO: SIGNED_BASE, OTHER_REPO: SIGNED_BASE})
        self.refused("envelope_contract_not_listed", value, repo=OTHER_REPO)

    def test_bases_missing_the_repo_refuses(self):
        value = envelope(repos=[REPO, OTHER_REPO], bases={REPO: SIGNED_BASE})
        value["contracts"].append(dict(value["contracts"][0], repo=OTHER_REPO))
        self.refused("envelope_base_mismatch", value, repo=OTHER_REPO)

    def test_base_that_does_not_descend_from_the_signed_base_refuses(self):
        self.refused("envelope_base_mismatch", is_ancestor=lambda older, newer: False)
        self.refused("envelope_base_mismatch", is_ancestor=None)

    def test_contract_not_listed_refuses(self):
        self.refused("envelope_contract_not_listed", contract="c" * 64)

    def test_action_class_is_an_allowlist(self):
        self.assertEqual(set(self.pre.ALLOWED_ACTION_CLASSES), ALLOWED_ACTIONS)
        for action in ("merge_gate_change", "ruleset_change", "deploy", "component_deploy", "force_push",
                       "preauth_widen", "admin", "merge_gate_change2", "", "MERGE_SLICE", "merge_slice ",
                       None, 1):
            self.refused("envelope_action_forbidden", envelope(action_class=action))

    def test_tier_above_the_contracts_max_tier_refuses(self):
        entry = dict(envelope()["contracts"][0], max_tier="T1")
        self.refused("envelope_tier_exceeded", envelope(contracts=[entry]), tier="T3")
        self.assertTrue(self.decide(envelope(contracts=[entry]), tier="T1")["ok"])
        for tier in ("T2", "", None, "t1"):
            self.refused("envelope_tier_exceeded", envelope(contracts=[entry]), tier=tier)
        bad = dict(entry, max_tier="T9")
        self.refused("envelope_schema_invalid", envelope(contracts=[bad]))


class OracleTest(EnvelopeCase):
    OLD = {"tests/test_review_packet.py": "1" * 40, "tests/conftest.py": "2" * 40,
           "pyproject.toml": "3" * 40, "pytest.ini": "4" * 40, "tests/fixtures/case.json": "5" * 40,
           "sitecustomize.py": "6" * 40, "src/thing.py": "7" * 40}

    def refreeze(self):
        entry = dict(envelope()["contracts"][0])
        entry["refreeze_of"] = {"contract_sha256": "d" * 64, "source_rev": "e" * 40}
        entry["test_files"] = ["tests/test_review_packet.py"]
        return envelope(contracts=[entry], action_class="refreeze_contract")

    def test_refreeze_with_an_identical_oracle_is_allowed(self):
        new = dict(self.OLD, **{"src/thing.py": "8" * 40})  # product code may differ
        self.assertTrue(self.decide(self.refreeze(), measured=facts(oracle={"old": self.OLD, "new": new}))["ok"])

    def test_refreeze_that_changes_any_oracle_file_refuses(self):
        for path in ("tests/test_review_packet.py", "tests/conftest.py", "pyproject.toml",
                     "pytest.ini", "tests/fixtures/case.json", "sitecustomize.py"):
            new = dict(self.OLD, **{path: "9" * 40})
            self.refused("envelope_oracle_changed", self.refreeze(),
                         measured=facts(oracle={"old": self.OLD, "new": new}))

    def test_refreeze_that_adds_or_removes_an_oracle_file_refuses(self):
        for extra in ("tests/plugins/hook.py", "x.pth", "tests/helpers.py", "tests/test_other.py",
                      "noxfile.py", ".github/workflows/ci.yml", "requirements-dev.txt", "./tests/../conftest.py"):
            self.refused("envelope_oracle_changed", self.refreeze(),
                         measured=facts(oracle={"old": self.OLD, "new": dict(self.OLD, **{extra: "a" * 40})}))
        removed = {k: v for k, v in self.OLD.items() if k != "tests/conftest.py"}
        self.refused("envelope_oracle_changed", self.refreeze(),
                     measured=facts(oracle={"old": self.OLD, "new": removed}))

    def test_refreeze_without_measured_oracle_refuses(self):
        self.refused("envelope_oracle_unproven", self.refreeze(), measured=facts(oracle=None))
        self.refused("envelope_oracle_unproven", self.refreeze(), measured=facts(oracle={"old": self.OLD}))

    def test_oracle_paths_cover_tests_conftest_config_fixtures_dependencies_ci_and_hooks(self):
        inside = ["tests/test_a.py", "tests/conftest.py", "a/b/conftest.py", "pytest.ini", "tox.ini",
                  "setup.cfg", "pyproject.toml", "setup.py", "noxfile.py", ".coveragerc",
                  "tests/fixtures/f.json", "tests/data/d.txt", "tests/helpers.py", "tests/__init__.py",
                  "tests/unit/test_b.py", "pkg/test_c.py", "pkg/c_test.py", "sitecustomize.py",
                  "usercustomize.py", "x.pth", "lib/site.pth", "tests/plugins/p.py", "requirements.txt",
                  "requirements-dev.txt", "uv.lock", "poetry.lock", "Pipfile", "Pipfile.lock",
                  ".github/workflows/ci.yml", ".github/workflows/x.yaml"]
        outside = ["src/x.py", "docs/a.md", "README.md", "src/testing_utils.py"]
        self.assertEqual(self.pre.oracle_paths(inside + outside, test_files=["tests/test_a.py"]), set(inside))

    def test_oracle_paths_normalise_before_matching(self):
        got = self.pre.oracle_paths(["./tests/../conftest.py", "tests//conftest.py", "./pytest.ini",
                                     "Tests/Conftest.py", "tests\\conftest.py"], test_files=[])
        self.assertEqual(got, {"conftest.py", "tests/conftest.py", "pytest.ini", "Tests/Conftest.py"})
        for bad in ("../outside/conftest.py", "/abs/conftest.py"):
            with self.assertRaises(self.pre.PreauthRefused):
                self.pre.oracle_paths([bad], test_files=[])


class BudgetTest(EnvelopeCase):
    def test_exhausted_merge_budget_refuses(self):
        self.refused("envelope_budget_exhausted", measured=facts(consumed=used(2)))

    def test_max_uses_is_a_ceiling_too(self):
        self.refused("envelope_budget_exhausted", envelope(max_uses=1), measured=facts(consumed=used(1)))

    def test_uses_of_another_envelope_do_not_count(self):
        self.assertTrue(self.decide(measured=facts(consumed=used(5, envelope_id="pa-other")))["ok"])

    def test_attempts_per_contract_are_capped(self):
        self.refused("envelope_budget_exhausted", measured=facts(attempts={CONTRACT: 3}))
        self.assertTrue(self.decide(measured=facts(attempts={CONTRACT: 2, "c" * 64: 9}))["ok"])

    def test_bees_in_flight_are_capped_for_wave_dispatch(self):
        self.refused("envelope_budget_exhausted", envelope(action_class="dispatch_wave"),
                     measured=facts(bees_in_flight=3))
        self.assertTrue(self.decide(envelope(action_class="dispatch_wave"), measured=facts(bees_in_flight=2))["ok"])

    def test_budget_shape_is_bounded(self):
        for budget in ({"max_merges": 0, "max_bees": 3, "max_attempts_per_contract": 3},
                       {"max_merges": 2, "max_bees": 5, "max_attempts_per_contract": 3},
                       {"max_merges": 2, "max_bees": 3, "max_attempts_per_contract": 0},
                       {"max_merges": 2, "max_bees": 3},
                       {"max_merges": 2, "max_bees": 3, "max_attempts_per_contract": 3, "extra": 1},
                       {"max_merges": "2", "max_bees": 3, "max_attempts_per_contract": 3},
                       {"max_merges": True, "max_bees": 3, "max_attempts_per_contract": 3}):
            self.refused("envelope_schema_invalid", envelope(budget=budget))
        for max_uses in (0, -1, "2", True, 2.0):
            self.refused("envelope_schema_invalid", envelope(max_uses=max_uses))

    def test_replayed_use_of_the_same_contract_refuses(self):
        self.refused("envelope_replayed", measured=facts(consumed=used(1, contract=CONTRACT)))

    def test_nonce_reused_by_another_envelope_refuses(self):
        self.refused("envelope_nonce_reused", measured=facts(nonces={NONCE: "pa-20261101-07"}))


class VerifierTest(EnvelopeCase):
    def test_missing_ssh_keygen_refuses(self):
        self.refused("envelope_verifier_unavailable", fail="missing")

    def test_hanging_ssh_keygen_refuses_within_a_bounded_timeout(self):
        self.refused("envelope_verifier_unavailable", fail="timeout")
        self.assertTrue(self.recorder.timeouts, "ssh-keygen must be run with a timeout")
        self.assertTrue(all(t is not None and 0 < t <= 60 for t in self.recorder.timeouts[:1]),
                        self.recorder.timeouts)

    def test_a_path_hijack_cannot_forge_a_signature(self):
        fake = Path(tempfile.mkdtemp(prefix="preauth-hijack-"))
        self.addCleanup(shutil.rmtree, fake, True)
        (fake / "ssh-keygen").write_text("#!/bin/sh\necho Good signature; exit 0\n")
        os.chmod(fake / "ssh-keygen", 0o755)
        path = str(fake) + os.pathsep + os.environ.get("PATH", "")
        with mock.patch.dict(os.environ, {"PATH": path}):
            self.refused("envelope_bad_signature", signature=b"-----BEGIN SSH SIGNATURE-----\nAAAA\n-----END SSH SIGNATURE-----\n")
        self.assertEqual(self.recorder.calls[0][0], "/usr/bin/ssh-keygen")
        self.assertEqual(self.recorder.envs, [{"PATH": "/usr/bin:/bin"}])

    def test_every_process_is_ssh_keygen_verify(self):
        self.decide()
        self.assertEqual([argv[1:3] for argv in self.recorder.calls], [["-Y", "verify"]])
        argv = self.recorder.calls[0]
        self.assertIn("-n", argv)
        self.assertEqual(argv[argv.index("-n") + 1], "rizzstack-preauth")


class KeyCustodyTest(unittest.TestCase):
    def test_module_verifies_and_never_signs(self):
        pre = load_module()
        self.assertEqual(pre.NAMESPACE, "rizzstack-preauth")
        self.assertEqual(pre.MAX_TTL_SECONDS, 7 * 24 * 3600)
        for name in dir(pre):
            self.assertNotRegex(name.lower(), r"(^|_)(sign|mint|generate)(_|$)", name)
        tree = ast.parse(MODULE.read_text(encoding="utf-8"))
        forbidden = {"Ed25519PrivateKey", "SigningKey", "PrivateKey", "load_ssh_private_key",
                     "load_pem_private_key", "nacl", "cryptography", "paramiko"}
        for node in ast.walk(tree):
            if isinstance(node, ast.Name):
                self.assertNotIn(node.id, forbidden)
            elif isinstance(node, ast.Attribute):
                self.assertNotIn(node.attr, forbidden)
            elif isinstance(node, (ast.Import, ast.ImportFrom)):
                names = [a.name for a in node.names] + [getattr(node, "module", None) or ""]
                for name in names:
                    self.assertNotIn(name.split(".")[0], forbidden, name)
            elif isinstance(node, ast.Constant) and isinstance(node.value, str):
                self.assertNotIn("PRIVATE KEY", node.value)

    def test_trust_root_is_frozen_and_holds_only_pinned_owner_keys(self):
        self.assertTrue(SIGNERS.is_file(), "missing policy/allowed_signers")
        self.assertEqual(hashlib.sha256(SIGNERS.read_bytes()).hexdigest(), ALLOWED_SIGNERS_SHA256,
                         "policy/allowed_signers is read-only; a new key needs an owner-approved contract")
        for line in SIGNERS.read_text(encoding="utf-8").splitlines():
            if not line.strip() or line.lstrip().startswith("#"):
                continue
            self.assertNotIn("cert-authority", line)
            self.assertRegex(line, r'^[A-Za-z0-9._@-]+ namespaces="rizzstack-preauth" '
                                   r'(ssh-ed25519|sk-ssh-ed25519@openssh\.com) [A-Za-z0-9+/=]+(\s.*)?$')
            self.assertIn(fingerprint(" ".join(line.split()[2:4])), OWNER_FINGERPRINTS)

    def test_no_tracked_file_holds_private_key_material(self):
        tracked = subprocess.run(["git", "-C", str(ROOT), "ls-files", "-z"], capture_output=True,
                                 check=True).stdout.decode().split("\0")
        marker = re.compile(r"-----BEGIN ([A-Z0-9 ]+ )?PRIVATE KEY-----|" + "PuTTY-User-" + "Key-File")
        for path in filter(None, tracked):
            target = ROOT / path
            if target.is_file() and target.stat().st_size < 2_000_000:
                self.assertIsNone(marker.search(target.read_text(encoding="utf-8", errors="ignore")), path)

    def test_schema_matches_the_checker(self):
        pre = load_module()
        schema = json.loads(SCHEMA.read_text(encoding="utf-8"))
        self.assertEqual(set(schema["required"]), set(pre.REQUIRED_FIELDS))
        self.assertFalse(schema["additionalProperties"])
        self.assertEqual(set(schema["properties"]["action_class"]["enum"]), ALLOWED_ACTIONS)


# --------------------------------------------------------------- production entry point
PRODUCTION_PARAMETERS = ("data", "signature", "repo", "base_rev", "contract_sha256", "tier", "now", "facts",
                         "is_ancestor")
OVERRIDE_PARAMETERS = {"allowed_signers", "trusted_fingerprints", "signers", "fingerprints", "trust_root",
                       "signers_path", "root", "base_dir"}


def _dotted_root(node):
    while isinstance(node, ast.Attribute):
        node = node.value
    return node.id if isinstance(node, ast.Name) else None


def _load_copy(tree, name):
    _scripts_on_path()
    spec = importlib.util.spec_from_file_location(name, tree / "scripts" / "preauth.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class ProductionEntryPointTest(unittest.TestCase):
    """check() reads the base checkout's trust root and the module pin; nothing overrides them."""

    @classmethod
    def setUpClass(cls):
        cls.pre = load_module()
        cls.keys = Keys()

    @classmethod
    def tearDownClass(cls):
        cls.keys.close()

    def call(self, module, **overrides):
        data = json.dumps(envelope(), sort_keys=True).encode("utf-8")
        kwargs = dict(repo=REPO, base_rev=CURRENT_BASE, contract_sha256=CONTRACT, tier="T3", now=NOW,
                      facts=facts(), is_ancestor=lambda older, newer: older == SIGNED_BASE)
        kwargs.update(overrides)
        signature = self.keys.sign(data)
        with ProcessRecorder(self):
            return module.check(data, signature, **kwargs)

    def tree(self, signer_lines):
        root = Path(tempfile.mkdtemp(prefix="preauth-tree-"))
        self.addCleanup(shutil.rmtree, root, True)
        (root / "scripts").mkdir()
        (root / "policy").mkdir()
        shutil.copyfile(MODULE, root / "scripts" / "preauth.py")
        shutil.copyfile(VERIFY_MODULE, root / "scripts" / "preauth_verify.py")
        (root / "policy" / "allowed_signers").write_text("".join(l + "\n" for l in signer_lines))
        return root

    def test_owner_fingerprints_constant_is_frozen_and_equals_the_test_pin(self):
        pin = self.pre.OWNER_FINGERPRINTS
        self.assertIsInstance(pin, tuple)
        self.assertEqual(len(pin), len(set(pin)))
        self.assertEqual(frozenset(pin), OWNER_FINGERPRINTS)
        for value in pin:
            self.assertRegex(value, r"^SHA256:[A-Za-z0-9+/]{43}$")

    def test_check_takes_no_trust_root_override(self):
        params = inspect.signature(self.pre.check).parameters
        self.assertEqual(tuple(params), PRODUCTION_PARAMETERS)
        for param in params.values():
            self.assertNotIn(param.kind, (param.VAR_POSITIONAL, param.VAR_KEYWORD), param)
        with self.assertRaises(TypeError):
            self.call(self.pre, allowed_signers=str(self.keys.signers))
        with self.assertRaises(TypeError):
            self.call(self.pre, trusted_fingerprints=frozenset({self.keys.fingerprint}))

    def test_the_committed_trust_root_refuses_every_envelope(self):
        with self.assertRaises(self.pre.PreauthRefused) as caught:
            self.call(self.pre)
        self.assertEqual(caught.exception.reason, "envelope_signer_untrusted")

    def test_check_reads_the_base_checkout_trust_root_and_the_module_pin(self):
        root = self.tree([self.keys.line])
        copy = _load_copy(root, "preauth_tree_copy")
        with self.assertRaises(copy.PreauthRefused) as caught:
            self.call(copy)
        self.assertEqual(caught.exception.reason, "envelope_signer_untrusted", "empty module pin")
        with mock.patch.object(copy, "OWNER_FINGERPRINTS", (self.keys.fingerprint,)):
            self.assertTrue(self.call(copy)["ok"])

    def test_cwd_and_environment_do_not_redirect_the_trust_root(self):
        root = self.tree(["# empty"])
        decoy = self.tree([self.keys.line])
        copy = _load_copy(root, "preauth_tree_decoy")
        env = {name: str(decoy / "policy" / "allowed_signers") for name in (
            "PREAUTH_ALLOWED_SIGNERS", "RIZZSTACK_ALLOWED_SIGNERS", "ALLOWED_SIGNERS")}
        cwd = os.getcwd()
        try:
            os.chdir(decoy)
            with mock.patch.dict(os.environ, env), \
                    mock.patch.object(copy, "OWNER_FINGERPRINTS", (self.keys.fingerprint,)):
                with self.assertRaises(copy.PreauthRefused) as caught:
                    self.call(copy)
        finally:
            os.chdir(cwd)
        self.assertEqual(caught.exception.reason, "envelope_signer_untrusted")

    def test_overrides_live_only_in_the_test_only_entry_point(self):
        tree = ast.parse(MODULE.read_text(encoding="utf-8"))
        functions = {node.name: node for node in ast.walk(tree)
                     if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))}
        self.assertIn("_check_for_tests", functions)
        self.assertNotIn("check_for_tests", functions, "the test-only entry point must be private")
        self.assertIn("test", (ast.get_docstring(functions["_check_for_tests"]) or "").lower())
        for name, node in functions.items():
            args = {a.arg for a in node.args.args + node.args.kwonlyargs + node.args.posonlyargs}
            if args & {"trusted_fingerprints", "allowed_signers"} and not name.startswith("_"):
                self.fail("public %s takes a trust-root override" % name)
        # r5: the verify entry point is private (no public verify_signature or similar)
        verifying = sorted(n for n in functions if not n.startswith("_") and re.search("verif|signature", n))
        self.assertEqual(verifying, [], "the verify entry point must be private")
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                func = node.func
                called = func.id if isinstance(func, ast.Name) else getattr(func, "attr", "")
                self.assertNotIn(called, ("check_for_tests", "_check_for_tests"),
                                 "the module calls its test-only entry point")
            if isinstance(node, ast.Name) and node.id in ("check_for_tests", "_check_for_tests"):
                self.fail("the module references its test-only entry point outside its definition")
        public = sorted(n for n in functions if not n.startswith("_") and "check" in n)
        self.assertEqual(public, ["check"], public)



# --------------------------------------------------------------- wiring freeze (preauth-wiring, r5)
WIRING_ALLOWED = {"check", "PreauthRefused"}
WIRING_FIXTURES = PROCESS_FIXTURES / "wiring_must_fail"
WIRING_EXEMPT = {MODULE.resolve(), VERIFY_MODULE.resolve()}
PRIVATE_MARKERS = ("check_for_tests", VERIFY_ENTRY, "preauth_verify", "_decide")
DYNAMIC_IMPORT = {"importlib", "runpy", "imp", "pkgutil", "zipimport"}
DYNAMIC_CODE = {"__import__", "eval", "exec", "compile", "execfile", "spec_from_file_location",
                "SourceFileLoader", "exec_module", "load_module", "run_path", "run_module"}
#: as attributes (``re.compile`` is fine; a loader's ``exec_module`` is not)
DYNAMIC_ATTRS = DYNAMIC_CODE - {"compile", "eval", "exec"}
WIRING_TEXT = re.compile(r"\bimport\s+(scripts\.)?preauth\b|\bfrom\s+(scripts\.preauth|scripts|preauth)\s+import\b|"
                         r"preauth_verify|check_for_tests|_ssh_verify|\bpreauth\.(?!md\b|py\b)[A-Za-z_]")


def wiring_violations(source):
    """Violations of the frozen wiring rule for one Python file under .github/ or scripts/."""
    try:
        tree = ast.parse(source)
    except SyntaxError as exc:
        return ["syntax_error:%s" % exc.lineno]
    out, aliases = [], set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                parts = alias.name.split(".")
                if "preauth_verify" in parts:
                    out.append("verify_import")
                elif parts[-1] == "preauth":
                    aliases.add(alias.asname or alias.name)
                if parts[0] in DYNAMIC_IMPORT:
                    out.append("dynamic_import:" + parts[0])
        elif isinstance(node, ast.ImportFrom):
            mod = node.module or ""
            parts = mod.split(".")
            if parts[0] in DYNAMIC_IMPORT:
                out.append("dynamic_import:" + parts[0])
            if "preauth_verify" in parts or any(a.name == "preauth_verify" for a in node.names):
                out.append("verify_import")
            elif parts[-1] == "preauth":
                for alias in node.names:
                    if alias.name not in WIRING_ALLOWED:
                        out.append("preauth_name:" + alias.name)
            else:
                for alias in node.names:
                    if alias.name == "preauth":
                        aliases.add(alias.asname or alias.name)
    parents = _parents(tree)
    for node in ast.walk(tree):
        if isinstance(node, (ast.Name, ast.Attribute)):
            dotted = _dotted(node)
            if dotted in aliases:
                parent = parents.get(id(node))
                if not (isinstance(parent, ast.Attribute) and parent.value is node):
                    out.append("preauth_module_value:" + dotted)
                elif parent.attr not in WIRING_ALLOWED:
                    out.append("preauth_name:" + parent.attr)
        if isinstance(node, ast.Name):
            if node.id in DYNAMIC_CODE:
                out.append("dynamic_code:" + node.id)
            if any(marker in node.id for marker in PRIVATE_MARKERS):
                out.append("private_name:" + node.id)
        elif isinstance(node, ast.Attribute):
            if node.attr in DYNAMIC_ATTRS:
                out.append("dynamic_code:" + node.attr)
            if node.attr == "modules" and _dotted(node.value) == "sys":
                out.append("sys_modules")
            if any(marker in node.attr for marker in PRIVATE_MARKERS):
                out.append("private_name:" + node.attr)
        elif isinstance(node, ast.Constant) and isinstance(node.value, str):
            if any(marker in node.value for marker in PRIVATE_MARKERS):
                out.append("private_name_string")
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                and node.func.id in ("getattr", "hasattr", "setattr", "delattr", "vars")):
            target = node.args[0] if node.args else None
            if target is not None and _dotted(target) in aliases:
                out.append("preauth_getattr")
            if node.func.id != "vars" and (len(node.args) < 2 or not isinstance(node.args[1], ast.Constant)):
                out.append("getattr_computed")
    return sorted(set(out))


def wiring_text_violations(text):
    """Violations of the frozen wiring rule for a non-Python file (workflows, shell, docs)."""
    return ["preauth_in_text"] if WIRING_TEXT.search(text) else []


def _wiring_targets():
    for top in (ROOT / ".github", ROOT / "scripts"):
        for path in sorted(top.rglob("*")):
            if path.is_file() and "__pycache__" not in path.parts and path.resolve() not in WIRING_EXEMPT:
                yield path


def _trapped_preauth(record):
    """A stand-in preauth module: check and PreauthRefused work, every other name raises."""
    module = types.ModuleType("preauth")

    class PreauthRefused(Exception):
        pass

    def check(*args, **kwargs):
        record.append(("allowed", "check"))
        error = PreauthRefused("preauth-wiring trap")
        error.reason = "envelope_signer_untrusted"
        raise error

    def trap(name):
        if name.startswith("__") and name.endswith("__"):
            raise AttributeError(name)
        record.append(("forbidden", name))
        raise AttributeError("preauth-wiring freeze: only check and PreauthRefused may be used, not " + name)

    module.check, module.PreauthRefused = check, PreauthRefused
    module.__getattr__ = trap
    return module


class WiringFreezeTest(unittest.TestCase):
    """Frozen for the separate slice preauth-wiring; holds at RED and must keep holding."""

    def test_gate_and_lane_reference_only_check_from_preauth(self):
        """Frozen for preauth-wiring: the gate and lane may use preauth.check and PreauthRefused only."""
        for path in (ROOT / ".github" / "scripts" / "lease_tamper.py", ROOT / "scripts" / "merge_lane_v5.py"):
            self.assertEqual(wiring_violations(path.read_text(encoding="utf-8")), [], path.name)

    def test_every_file_under_github_and_scripts_obeys_the_wiring_rule(self):
        scanned = 0
        for path in _wiring_targets():
            try:
                text = path.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                continue
            scanned += 1
            got = wiring_violations(text) if path.suffix == ".py" else wiring_text_violations(text)
            self.assertEqual(got, [], str(path.relative_to(ROOT)))
        self.assertGreater(scanned, 5)

    #: each wiring fixture and the violation it must raise (GROKCHAT round 4 first)
    WIRING_MUST_FAIL = {
        "control_direct.py": "preauth_name:verify_signature",
        "from_pkg_alias.py": "preauth_name:verify_signature",
        "importlib_load.py": "dynamic_import:importlib",
        "getattr_name.py": "preauth_getattr",
        "private_via_getattr.py": "preauth_getattr",
        "dotted_import.py": "preauth_name:oracle_paths",
        "from_import_name.py": "preauth_name:oracle_paths",
        "star_import.py": "preauth_name:*",
        "module_value.py": "preauth_module_value:preauth",
        "module_rebind.py": "preauth_module_value:preauth",
        "getattr_constant_private.py": "preauth_getattr",
        "getattr_computed_elsewhere.py": "getattr_computed",
        "verify_module_import.py": "verify_import",
        "verify_module_dotted.py": "verify_import",
        "private_name_string.py": "private_name_string",
        "private_name.py": "private_name:_ssh_verify",
        "runpy_load.py": "dynamic_import:runpy",
        "dunder_import.py": "dynamic_code:__import__",
        "exec_code.py": "dynamic_code:exec",
        "sys_modules.py": "sys_modules",
        "workflow_inline.yml": "preauth_in_text",
        "shell_inline.sh": "preauth_in_text",
    }

    def test_wiring_must_fail_fixtures_are_caught(self):
        found = sorted(p.name for p in WIRING_FIXTURES.iterdir() if p.is_file())
        self.assertEqual(found, sorted(self.WIRING_MUST_FAIL))
        for name, code in self.WIRING_MUST_FAIL.items():
            text = (WIRING_FIXTURES / name).read_text(encoding="utf-8")
            got = wiring_violations(text) if name.endswith(".py") else wiring_text_violations(text)
            self.assertIn(code, got, name)

    def test_preauth_names_other_than_check_raise_on_the_lane_and_gate_paths(self):
        record = []
        trapped = _trapped_preauth(record)
        saved = {name: sys.modules.get(name) for name in ("preauth", "scripts.preauth")}
        sys.modules["preauth"] = sys.modules["scripts.preauth"] = trapped
        try:
            # the trap itself: a forbidden name raises and is recorded, check is allowed
            probe = types.ModuleType("probe")
            exec("import preauth\ntry:\n    preauth.oracle_paths\nexcept AttributeError:\n    pass\n", probe.__dict__)
            self.assertEqual(record, [("forbidden", "oracle_paths")])
            del record[:]
            lane = self._load(ROOT / "scripts" / "merge_lane_v5.py", "lane_wiring_trap")
            gate = self._load(ROOT / ".github" / "scripts" / "lease_tamper.py", "gate_wiring_trap")
            for facts in ({}, {"pr": {"number": 12, "state": "OPEN", "head": "a" * 40, "files": []},
                               "class": getattr(lane, "NON_SLICE", "non-slice"), "reviews": [], "checks": [],
                               "required_checks": [], "lease_tamper": {"ran": True, "ok": True}}):
                with contextlib.suppress(Exception):
                    lane.decide(facts)
            repo, base, head = self._git_repo()
            out = io.StringIO()
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out), \
                    contextlib.suppress(Exception, SystemExit):
                gate.main(["--repo", str(repo), "--base", base, "--head", head, "--quiet"], environ={})
        finally:
            for name, module in saved.items():
                if module is None:
                    sys.modules.pop(name, None)
                else:
                    sys.modules[name] = module
        self.assertEqual([name for kind, name in record if kind == "forbidden"], [])

    def _load(self, path, name):
        spec = importlib.util.spec_from_file_location(name, path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def _git_repo(self):
        repo = Path(tempfile.mkdtemp(prefix="preauth-wiring-"))
        self.addCleanup(shutil.rmtree, repo, True)
        git = ["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@example.invalid",
               "-c", "commit.gpgsign=false"]
        subprocess.run(git[:3] + ["init", "-q"], check=True, capture_output=True)
        (repo / "README.md").write_text("base\n")
        subprocess.run(git + ["add", "-A"], check=True, capture_output=True)
        subprocess.run(git + ["commit", "-q", "-m", "base"], check=True, capture_output=True)
        base = subprocess.run(git + ["rev-parse", "HEAD"], check=True, capture_output=True, text=True).stdout.strip()
        (repo / "docs.md").write_text("change\n")
        subprocess.run(git + ["add", "-A"], check=True, capture_output=True)
        subprocess.run(git + ["commit", "-q", "-m", "docs: change"], check=True, capture_output=True)
        head = subprocess.run(git + ["rev-parse", "HEAD"], check=True, capture_output=True, text=True).stdout.strip()
        return repo, base, head


# --------------------------------------------------------------- static process rule (r4: allowlists)
SSH_KEYGEN = "/usr/bin/ssh-keygen"
SSH_ENV = {"PATH": "/usr/bin:/bin"}
SSH_ARGV = (SSH_KEYGEN, "-Y", "verify", "-f", None, "-I", None, "-n", None, "-s", None)
CALL_KEYWORDS = {"input", "capture_output", "timeout", "check", "env"}
#: Modules preauth.py may import (plain ``import x``, top level, no alias).
ALLOWED_IMPORTS = {"base64", "binascii", "hashlib", "json", "os", "posixpath", "re", "subprocess", "tempfile"}
#: ``from x import y`` allowed only for these names.
ALLOWED_FROM = {"__future__": {"annotations"}, "datetime": {"datetime", "timezone", "timedelta"}}
#: The only attributes reachable on each imported module.
MODULE_ATTRS = {
    "os": {"path", "fspath", "fsencode", "fsdecode", "sep"},
    "os.path": {"join", "dirname", "abspath", "basename", "normpath", "realpath", "isfile"},
    "posixpath": {"normpath", "join", "dirname", "basename"},
    "subprocess": {"run", "SubprocessError", "TimeoutExpired", "CalledProcessError"},
    "tempfile": {"TemporaryDirectory", "NamedTemporaryFile"},
    "json": {"loads", "dumps", "JSONDecodeError"},
    "re": {"compile", "fullmatch", "match", "search", "escape", "ASCII", "IGNORECASE"},
    "hashlib": {"sha256"},
    "base64": {"b64decode", "b64encode"},
    "binascii": {"Error"},
}
BANNED_NAMES = {"eval", "exec", "compile", "getattr", "setattr", "delattr", "globals", "locals", "vars",
                "breakpoint", "input", "help", "license", "credits", "copyright", "exit", "quit", "memoryview"}
ALLOWED_DUNDER_NAMES = {"__file__", "__name__"}
#: Attribute names banned on ANY object (process, environment, permissions, frames).
BANNED_ATTRS = re.compile(r"^(environ|environb|putenv|unsetenv|chmod|fchmod|lchmod|chown|lchown|fchown|system|"
                          r"popen|startfile|kill|killpg|fork.*|exec.*|spawn.*|posix_spawn.*|Popen|call|"
                          r"check_call|check_output|getoutput|getstatusoutput|run|gi_frame|gi_code|cr_frame|"
                          r"ag_frame|tb_frame|f_globals|f_locals|f_builtins|f_back|f_code|co_code)$")
PROTECTED_NAMES = {"open", "print", "isinstance", "type"}


def _dotted(node):
    parts = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if isinstance(node, ast.Name):
        parts.append(node.id)
        return ".".join(reversed(parts))
    return None


def _write_mode(call):
    mode = None
    if len(call.args) >= 2:
        mode = call.args[1]
    for kw in call.keywords:
        if kw.arg == "mode":
            mode = kw.value
        if kw.arg is None or kw.arg == "opener":
            return True
    if mode is None:
        return False
    if not (isinstance(mode, ast.Constant) and isinstance(mode.value, str)):
        return True
    return any(ch in mode.value for ch in "wax+")


#: Module attributes that may be used as plain values (everything else must be called directly).
PROCESS_VALUE_ATTRS = {"os.path", "os.sep", "re.ASCII", "re.IGNORECASE"}


def _parents(tree):
    parents = {}
    for node in ast.walk(tree):
        for child in ast.iter_child_nodes(node):
            parents[id(child)] = node
    return parents


def _is_call_func(node, parents):
    parent = parents.get(id(node))
    return isinstance(parent, ast.Call) and parent.func is node


def _in_except_type(node, parents):
    child, parent = node, parents.get(id(node))
    while isinstance(parent, ast.Tuple):
        child, parent = parent, parents.get(id(parent))
    return isinstance(parent, ast.ExceptHandler) and parent.type is child


def process_violations(source):
    """Violations of the one-process rule; [] means the module can only start ssh-keygen -Y verify."""
    try:
        tree = ast.parse(source)
    except SyntaxError as exc:
        return ["syntax_error:%s" % exc.lineno]
    out = []
    modules = set()
    from_names = set()
    top = set(id(n) for n in tree.body)
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            if id(node) not in top:
                out.append("nested_import")
            for alias in node.names:
                if alias.name == "subprocess" and alias.asname:
                    out.append("subprocess_alias")
                if alias.name not in ALLOWED_IMPORTS:
                    out.append("import_not_allowed:" + alias.name)
                elif alias.asname:
                    out.append("import_alias:" + alias.name)
                else:
                    modules.add(alias.name)
        elif isinstance(node, ast.ImportFrom):
            if id(node) not in top:
                out.append("nested_import")
            mod = node.module or ""
            if mod.split(".")[0] == "subprocess":
                out.append("from_subprocess_import")
            allowed = ALLOWED_FROM.get(mod) if node.level == 0 else None
            for alias in node.names:
                if allowed is None or alias.name not in allowed or alias.asname:
                    out.append("import_not_allowed:%s.%s" % (mod, alias.name))
                else:
                    from_names.add(alias.name)
    bound = modules | from_names | PROTECTED_NAMES
    parents = {}
    for node in ast.walk(tree):
        for child in ast.iter_child_nodes(node):
            parents[id(child)] = node
    calls = []
    for node in ast.walk(tree):
        # rebinding or shadowing a module, an imported name or a protected builtin
        targets = []
        if isinstance(node, (ast.Assign, ast.AugAssign, ast.AnnAssign, ast.For, ast.AsyncFor, ast.NamedExpr)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
        elif isinstance(node, (ast.With, ast.AsyncWith)):
            targets = [item.optional_vars for item in node.items if item.optional_vars is not None]
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            if node.name in bound:
                out.append("rebind:" + node.name)
        elif isinstance(node, ast.arg) and node.arg in bound:
            out.append("rebind:" + node.arg)
        elif isinstance(node, ast.ExceptHandler) and node.name in bound:
            out.append("rebind:" + node.name)
        elif isinstance(node, (ast.Global, ast.Nonlocal)):
            out.append("global_statement")
        elif isinstance(node, ast.Delete):
            targets = node.targets
        for target in targets:
            for sub in ast.walk(target):
                if isinstance(sub, ast.Name) and sub.id in bound:
                    out.append("rebind:" + sub.id)
        if isinstance(node, ast.Name):
            if node.id in BANNED_NAMES:
                out.append("banned_name:" + node.id)
            if node.id.startswith("__") and node.id.endswith("__") and node.id not in ALLOWED_DUNDER_NAMES:
                out.append("dunder_name:" + node.id)
            if BANNED_ATTRS.match(node.id):
                out.append("banned_identifier:" + node.id)
            if node.id in modules:
                parent = parents.get(id(node))
                if not (isinstance(parent, ast.Attribute) and parent.value is node):
                    out.append("module_reference:" + node.id)
                else:
                    attr = parent.attr
                    if attr not in MODULE_ATTRS.get(node.id, set()):
                        out.append("module_attr:%s.%s" % (node.id, attr))
                    elif node.id == "os" and attr == "path":
                        grand = parents.get(id(parent))
                        if not (isinstance(grand, ast.Attribute) and grand.value is parent
                                and grand.attr in MODULE_ATTRS["os.path"]):
                            out.append("module_reference:os.path")
            if node.id == "open":
                parent = parents.get(id(node))
                if not (isinstance(parent, ast.Call) and parent.func is node):
                    out.append("open_reference")
                elif _write_mode(parent):
                    out.append("write_mode_open")
        elif isinstance(node, ast.Attribute):
            if node.attr.startswith("_"):
                out.append("private_or_dunder_attr:" + node.attr)
            dotted = _dotted(node)
            if BANNED_ATTRS.match(node.attr) and dotted != "subprocess.run":
                out.append("banned_attr:" + node.attr)
        elif isinstance(node, ast.Constant) and isinstance(node.value, str):
            text = node.value.strip().lower()
            if text == "sign" or re.search(r"-y\s*sign", text):
                out.append("sign_literal")
        elif isinstance(node, ast.Lambda):
            out.append("lambda")
        if isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name) and func.value.id == "subprocess":
                calls.append(node)
    # r5 (GROKCHAT round 4): no value use of a module attribute, no decorators, class keywords,
    # dunder defs, match or format; tempfile only with a constant suffix
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and node.decorator_list:
            out.append("decorator")
        if isinstance(node, ast.ClassDef) and node.keywords:
            out.append("class_keyword")
        if (isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
                and node.name.startswith("__") and node.name.endswith("__")):
            out.append("dunder_def:" + node.name)
        if isinstance(node, ast.Match):
            out.append("match_statement")
        if isinstance(node, ast.Attribute) and node.attr in ("format", "format_map"):
            out.append("format_attr:" + node.attr)
        if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name) and node.value.id in modules:
            dotted = node.value.id + "." + node.attr
            if not (dotted in PROCESS_VALUE_ATTRS or _is_call_func(node, parents)
                    or _in_except_type(node, parents)):
                out.append("module_attr_value:" + dotted)
        if isinstance(node, ast.Call) and _dotted(node.func) in ("tempfile.NamedTemporaryFile",
                                                                 "tempfile.TemporaryDirectory"):
            kws = node.keywords
            if node.args or len(kws) != 1 or kws[0].arg != "suffix" or not (
                    isinstance(kws[0].value, ast.Constant) and isinstance(kws[0].value.value, str)):
                out.append("tempfile_args")
    if len(calls) != 1:
        out.append("process_calls:%d" % len(calls))
    for call in calls:
        if call.func.attr != "run":
            out.append("subprocess_attr:" + call.func.attr)
        if len(call.args) != 1:
            out.append("argv_args")
            continue
        argv = call.args[0]
        if not isinstance(argv, ast.List) or len(argv.elts) != len(SSH_ARGV):
            out.append("argv_shape")
        else:
            for want, got in zip(SSH_ARGV, argv.elts):
                if want is None:
                    if not isinstance(got, ast.Name):
                        out.append("argv_computed")
                elif not (isinstance(got, ast.Constant) and got.value == want):
                    out.append("argv_shape" if want != SSH_KEYGEN else "ssh_keygen_not_absolute")
        names = [k.arg for k in call.keywords]
        if None in names or any(k not in CALL_KEYWORDS for k in names if k):
            out.append("call_keyword")
        if "timeout" not in names:
            out.append("call_timeout_missing")
        env = [k.value for k in call.keywords if k.arg == "env"]
        if not env:
            out.append("call_env_missing")
        else:
            node = env[0]
            ok = (isinstance(node, ast.Dict) and len(node.keys) == 1
                  and all(isinstance(x, ast.Constant) for x in node.keys + node.values)
                  and {k.value: v.value for k, v in zip(node.keys, node.values)} == SSH_ENV)
            if not ok:
                out.append("call_env_not_constant")
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and node.value == "-f":
            in_slot = any(isinstance(c.args[0], ast.List) and len(c.args[0].elts) > 3
                          and c.args[0].elts[3] is node for c in calls if c.args)
            if not in_slot:
                out.append("f_literal")
    return sorted(set(out))


class ProcessRuleTest(unittest.TestCase):
    MUST_FAIL = ("split_string_sign", "popen_alias", "import_alias", "os_system", "os_exec", "posixsubprocess",
                 "ctypes_libc", "importlib_subprocess", "dunder_import", "eval_exec", "joined_argv",
                 "fstring_argv", "computed_argv", "extra_args", "executable_kw", "shell_true",
                 "getattr_popen", "sys_modules", "asyncio_subprocess", "sign_literal", "two_calls",
                 "no_timeout")
    #: r4 stubs (GROKCHAT round 3 bypasses and the new bans), each with the violation it must raise.
    MUST_FAIL_R4 = {
        "os_name_alias": "module_reference:os",
        "rebind_subprocess": "rebind:subprocess",
        "os_getattribute": "private_or_dunder_attr:__getattribute__",
        "object_getattribute": "private_or_dunder_attr:__getattribute__",
        "operator_attrgetter": "import_not_allowed:operator",
        "timeit_exec": "import_not_allowed:timeit",
        "cprofile_exec": "import_not_allowed:cProfile",
        "type_lambda_os": "module_reference:os",
        "return_os": "module_reference:os",
        "webbrowser_browser": "import_not_allowed:webbrowser",
        "imported_helper": "import_not_allowed:merge_lane_v5",
        "path_hijack": "banned_attr:environ",
        "partial_run": "import_not_allowed:functools",
        "putenv": "module_attr:os.putenv",
        "chmod": "module_attr:os.chmod",
        "write_open": "write_mode_open",
        "relative_ssh_keygen": "ssh_keygen_not_absolute",
        "env_missing": "call_env_missing",
        "env_computed": "call_env_not_constant",
        "frame_globals": "banned_attr:f_globals",
        "help_pager": "banned_name:help",
        "private_module_attr": "module_attr:tempfile._os",
        "nested_import": "nested_import",
    }

    #: GROKCHAT round-4 stubs, committed verbatim; each must be refused for its own bypass.
    MUST_FAIL_R5 = {
        "run_alias": ("module_attr_value:subprocess.run",),
        "walrus_run": ("module_attr_value:subprocess.run",),
        "dict_run": ("module_attr_value:subprocess.run",),
        "default_arg_run": ("module_attr_value:subprocess.run",),
        "match_capture": ("match_statement", "module_attr_value:subprocess.run"),
        "decorator_metaclass": ("decorator", "class_keyword", "dunder_def:__iter__"),
        "sign_concat_alias": ("module_attr_value:subprocess.run",),
        "ntf_pth": ("tempfile_args",),
        "format_read": ("format_attr:format",),
    }

    def stub(self, name, folder="must_fail"):
        return (PROCESS_FIXTURES / folder / (name + ".py")).read_text(encoding="utf-8")

    def test_safe_verify_stub_passes_the_rule(self):
        source = (PROCESS_FIXTURES / "safe_verify.py").read_text(encoding="utf-8")
        self.assertEqual(process_violations(source), [])

    def test_must_fail_stubs_are_refused(self):
        found = sorted(p.stem for p in (PROCESS_FIXTURES / "must_fail").glob("*.py"))
        self.assertEqual(found, sorted(set(self.MUST_FAIL) | set(self.MUST_FAIL_R4) | set(self.MUST_FAIL_R5)))
        for name in self.MUST_FAIL:
            self.assertNotEqual(process_violations(self.stub(name)), [], name)

    def test_grokchat_split_string_and_popen_alias_stubs_are_refused(self):
        split = self.stub("split_string_sign")
        self.assertIn('"si" + "gn"', split)
        self.assertIn("process_calls:2", process_violations(split))
        alias = self.stub("popen_alias")
        self.assertIn("from_subprocess_import", process_violations(alias))

    def test_round_three_bypass_stubs_are_refused_for_their_own_reason(self):
        for name, code in self.MUST_FAIL_R4.items():
            source = self.stub(name)
            if name != "relative_ssh_keygen":
                self.assertIn("/usr/bin/ssh-keygen", source, name)
            self.assertIn(code, process_violations(source), name)

    def test_every_r4_stub_differs_from_the_safe_stub_only_by_its_bypass(self):
        safe = set((PROCESS_FIXTURES / "safe_verify.py").read_text(encoding="utf-8").splitlines())
        for name in self.MUST_FAIL_R4:
            extra = [line for line in self.stub(name).splitlines() if line not in safe]
            self.assertTrue(extra, name)
            self.assertLessEqual(len(extra), 12, (name, extra))

    def test_round_four_bypass_stubs_are_refused_for_their_own_reason(self):
        safe = set((PROCESS_FIXTURES / "safe_verify.py").read_text(encoding="utf-8").splitlines())
        for name, codes in self.MUST_FAIL_R5.items():
            source = self.stub(name)
            self.assertIn("/usr/bin/ssh-keygen", source, name)
            extra = [line for line in source.splitlines() if line not in safe]
            self.assertTrue(extra, name)
            self.assertLessEqual(len(extra), 12, (name, extra))
            got = process_violations(source)
            for code in codes:
                self.assertIn(code, got, name)

    def test_frozen_verify_module_obeys_the_rule(self):
        # r5: the one-process rule applies to the frozen verifier; preauth.py starts no process at all
        self.assertTrue(VERIFY_MODULE.is_file(), "missing scripts/preauth_verify.py")
        self.assertEqual(process_violations(VERIFY_MODULE.read_text(encoding="utf-8")), [])


# --------------------------------------------------------------- r5: strict allowlist over preauth.py
#: Plain ``import x`` allowed in preauth.py (top level, no alias).  No os, subprocess or tempfile.
PREAUTH_IMPORTS = {"json", "hashlib", "base64", "binascii", "re"}
#: ``from x import y`` allowed only for these names; the frozen verifier's single entry point by name.
PREAUTH_FROM = {"__future__": {"annotations"}, "datetime": {"datetime", "timezone", "timedelta"},
                "preauth_verify": {VERIFY_ENTRY}}
PREAUTH_MODULE_ATTRS = {
    "json": {"loads", "dumps", "JSONDecodeError"},
    "hashlib": {"sha256"},
    "base64": {"b64decode", "b64encode"},
    "binascii": {"Error"},
    "re": {"compile", "fullmatch", "match", "search", "escape", "ASCII", "IGNORECASE"},
}
#: Module attributes usable as plain values (flags, and exception classes outside an except clause).
PREAUTH_VALUE_ATTRS = {"re.ASCII", "re.IGNORECASE", "json.JSONDecodeError", "binascii.Error"}
#: the test-only entry point and the shared private decision core check() and it both call
PREAUTH_PRIVATE_DEFS = {"_check_for_tests", "_decide"}
PREAUTH_DUNDER_NAMES = {"__file__"}
PREAUTH_BANNED_NAMES = BANNED_NAMES | {"type", "object", "super", "property", "classmethod", "staticmethod",
                                       "format", "dir", "hasattr", "open_code", "__build_class__"}
PREAUTH_BANNED_ATTRS = re.compile(BANNED_ATTRS.pattern[:-2] + r"|format|format_map|mro|modules|tb_next|"
                                  r"ag_code|cr_code|f_lineno|f_trace|func_globals)$")
PREAUTH_PROTECTED = {"open", "isinstance", "len", "str", "int", "bool", "dict", "list", "set", "tuple",
                     "frozenset", "sorted", "any", "all", "min", "max", "Exception", "print"}
PREAUTH_OPEN_MODES = {"r", "rb"}
PREAUTH_MAX_OPENS = 2


def preauth_violations(source):
    """Violations of the r5 preauth.py allowlist; [] means no process, write, tempfile or module escape."""
    try:
        tree = ast.parse(source)
    except SyntaxError as exc:
        return ["syntax_error:%s" % exc.lineno]
    out, modules, imported = [], set(), set()
    top = set(id(n) for n in tree.body)
    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)) and id(node) not in top:
            out.append("nested_import")
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name not in PREAUTH_IMPORTS:
                    out.append("import_not_allowed:" + alias.name)
                elif alias.asname:
                    out.append("import_alias:" + alias.name)
                else:
                    modules.add(alias.name)
        elif isinstance(node, ast.ImportFrom):
            mod = node.module or ""
            if mod == "preauth_verify" and node.level == 0 and [(a.name, a.asname) for a in node.names] != [
                    (VERIFY_ENTRY, None)]:
                out.append("verify_import")
            allowed = PREAUTH_FROM.get(mod) if node.level == 0 else None
            for alias in node.names:
                if allowed is None or alias.name not in allowed or alias.asname:
                    out.append("import_not_allowed:%s%s.%s" % ("." * node.level, mod, alias.name))
                else:
                    imported.add(alias.name)
    bound = modules | imported | PREAUTH_PROTECTED
    parents = _parents(tree)
    opens = 0
    for node in ast.walk(tree):
        targets = []
        if isinstance(node, (ast.Assign, ast.AugAssign, ast.AnnAssign, ast.For, ast.AsyncFor, ast.NamedExpr,
                             ast.comprehension)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
        elif isinstance(node, (ast.With, ast.AsyncWith)):
            targets = [item.optional_vars for item in node.items if item.optional_vars is not None]
        elif isinstance(node, ast.Delete):
            targets = node.targets
        elif isinstance(node, ast.arg) and node.arg in bound:
            out.append("rebind:" + node.arg)
        elif isinstance(node, ast.ExceptHandler) and node.name in bound:
            out.append("rebind:" + node.name)
        elif isinstance(node, (ast.Global, ast.Nonlocal)):
            out.append("global_statement")
        elif isinstance(node, ast.Match):
            out.append("match_statement")
        elif isinstance(node, ast.AsyncFunctionDef):
            out.append("async_def")
        for target in targets:
            for sub in ast.walk(target):
                if isinstance(sub, ast.Name) and sub.id in bound:
                    out.append("rebind:" + sub.id)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            if node.decorator_list:
                out.append("decorator")
            if node.name in bound:
                out.append("rebind:" + node.name)
            if node.name.startswith("_") and node.name not in PREAUTH_PRIVATE_DEFS:
                out.append("private_def:" + node.name)
        if isinstance(node, ast.ClassDef):
            if node.keywords:
                out.append("class_keyword")
            if not (len(node.bases) == 1 and isinstance(node.bases[0], ast.Name) and node.bases[0].id == "Exception"):
                out.append("class_base")
            for item in node.body:
                if not (isinstance(item, ast.Pass) or (isinstance(item, ast.Expr) and isinstance(item.value, ast.Constant)
                                                       and isinstance(item.value.value, str))):
                    out.append("class_body")
        if isinstance(node, ast.Name):
            if node.id in PREAUTH_BANNED_NAMES:
                out.append("banned_name:" + node.id)
            if node.id.startswith("__") and node.id not in PREAUTH_DUNDER_NAMES:
                out.append("dunder_name:" + node.id)
            if node.id in modules:
                parent = parents.get(id(node))
                if not (isinstance(parent, ast.Attribute) and parent.value is node):
                    out.append("module_reference:" + node.id)
                elif parent.attr not in PREAUTH_MODULE_ATTRS.get(node.id, set()):
                    out.append("module_attr:%s.%s" % (node.id, parent.attr))
                elif not ("%s.%s" % (node.id, parent.attr) in PREAUTH_VALUE_ATTRS
                          or _is_call_func(parent, parents) or _in_except_type(parent, parents)):
                    out.append("module_attr_value:%s.%s" % (node.id, parent.attr))
            if node.id == "open":
                parent = parents.get(id(node))
                if not (isinstance(parent, ast.Call) and parent.func is node):
                    out.append("open_reference")
                else:
                    opens += 1
                    out.extend(_preauth_open(parent))
        elif isinstance(node, ast.Attribute):
            if node.attr.startswith("_"):
                out.append("private_or_dunder_attr:" + node.attr)
            if PREAUTH_BANNED_ATTRS.match(node.attr):
                out.append("banned_attr:" + node.attr)
    if opens > PREAUTH_MAX_OPENS:
        out.append("open_calls:%d" % opens)
    return sorted(set(out))


def _preauth_open(call):
    mode = call.args[1] if len(call.args) >= 2 else None
    out = []
    if len(call.args) > 2:
        out.append("open_args")
    for kw in call.keywords:
        if kw.arg == "mode":
            mode = kw.value
        elif kw.arg != "encoding":
            out.append("write_mode_open" if kw.arg in (None, "opener") else "open_args")
    if mode is None or not (isinstance(mode, ast.Constant) and isinstance(mode.value, str)):
        out.append("open_mode_not_literal")
    elif mode.value not in PREAUTH_OPEN_MODES:
        out.append("write_mode_open")
    return out


class PreauthRuleTest(unittest.TestCase):
    """r5: preauth.py is parsing and policy only; the rule is a strict allowlist, not a denylist."""

    #: preauth.py-shaped stubs (safe_preauth.py plus a bypass), each with the violation it must raise
    MUST_FAIL = {
        "module_alias": "module_reference:re",
        "walrus_alias": "module_reference:re",
        "dict_alias": "module_reference:re",
        "default_arg_alias": "module_reference:re",
        "match_capture": "match_statement",
        "decorator_def": "decorator",
        "class_metaclass": "class_keyword",
        "dunder_def": "private_def:__iter__",
        "split_string_sign": "module_attr:re.enum",
        "ntf_pth": "import_not_allowed:tempfile",
        "format_leak": "banned_attr:format",
        "format_map_leak": "banned_attr:format_map",
        "import_subprocess": "import_not_allowed:subprocess",
        "import_os": "import_not_allowed:os",
        "import_datetime_module": "import_not_allowed:datetime",
        "verify_module_import": "import_not_allowed:preauth_verify",
        "verify_import_extra": "verify_import",
        "verify_import_alias": "verify_import",
        "relative_import": "import_not_allowed:..preauth_verify",
        "star_import": "import_not_allowed:json.*",
        "json_decoder_chain": "module_attr:json.decoder",
        "re_enum_chain": "module_attr:re.enum",
        "module_attr_value": "module_attr_value:json.loads",
        "nested_import": "nested_import",
        "getattr_computed": "banned_name:getattr",
        "eval_code": "banned_name:eval",
        "exec_code": "banned_name:exec",
        "compile_code": "banned_name:compile",
        "dunder_import": "dunder_name:__import__",
        "dunder_attr": "private_or_dunder_attr:__globals__",
        "private_attr": "private_or_dunder_attr:_private",
        "frame_walk": "banned_attr:gi_frame",
        "breakpoint_hook": "banned_name:breakpoint",
        "help_pager": "banned_name:help",
        "type_three_arg": "banned_name:type",
        "builtins_name": "dunder_name:__builtins__",
        "rebind_module": "rebind:json",
        "rebind_entry": "rebind:_ssh_verify",
        "rebind_in_comprehension": "rebind:re",
        "global_statement": "global_statement",
        "private_def": "private_def:_helper",
        "class_base": "class_base",
        "class_body": "class_body",
        "class_decorator": "decorator",
        "write_open": "write_mode_open",
        "append_open": "write_mode_open",
        "open_computed_mode": "open_mode_not_literal",
        "open_without_mode": "open_mode_not_literal",
        "open_opener": "write_mode_open",
        "open_reference": "open_reference",
        "three_opens": "open_calls:3",
    }

    def stub(self, name):
        return (PROCESS_FIXTURES / "preauth_must_fail" / (name + ".py")).read_text(encoding="utf-8")

    def test_safe_preauth_stub_passes_the_rule(self):
        self.assertEqual(preauth_violations((PROCESS_FIXTURES / "safe_preauth.py").read_text(encoding="utf-8")), [])

    def test_every_preauth_stub_is_refused_for_its_own_reason(self):
        found = sorted(p.stem for p in (PROCESS_FIXTURES / "preauth_must_fail").glob("*.py"))
        self.assertEqual(found, sorted(self.MUST_FAIL))
        safe = set((PROCESS_FIXTURES / "safe_preauth.py").read_text(encoding="utf-8").splitlines())
        for name, code in self.MUST_FAIL.items():
            source = self.stub(name)
            extra = [line for line in source.splitlines() if line not in safe]
            self.assertTrue(extra, name)
            self.assertLessEqual(len(extra), 12, (name, extra))
            self.assertIn(code, preauth_violations(source), name)

    def test_every_earlier_process_stub_is_refused_by_the_preauth_rule(self):
        stubs = sorted((PROCESS_FIXTURES / "must_fail").glob("*.py"))
        self.assertGreaterEqual(len(stubs), 54)
        for path in stubs + [PROCESS_FIXTURES / "safe_verify.py"]:
            self.assertNotEqual(preauth_violations(path.read_text(encoding="utf-8")), [], path.name)

    def test_the_frozen_verifier_could_not_live_in_preauth_py(self):
        # the process code is only allowed in the frozen file; the same code in preauth.py is refused
        got = preauth_violations(VERIFY_MODULE.read_text(encoding="utf-8"))
        self.assertIn("import_not_allowed:subprocess", got)
        self.assertIn("import_not_allowed:tempfile", got)

    def test_preauth_module_obeys_the_preauth_rule(self):
        self.assertTrue(MODULE.is_file(), "missing scripts/preauth.py")
        self.assertEqual(preauth_violations(MODULE.read_text(encoding="utf-8")), [])

    def test_preauth_imports_only_the_frozen_entry_point_by_name(self):
        self.assertTrue(MODULE.is_file(), "missing scripts/preauth.py")
        tree = ast.parse(MODULE.read_text(encoding="utf-8"))
        froms = [n for n in tree.body if isinstance(n, ast.ImportFrom) and n.module == "preauth_verify"]
        self.assertEqual(len(froms), 1)
        self.assertEqual([(a.name, a.asname) for a in froms[0].names], [(VERIFY_ENTRY, None)])
        pre = load_module()
        entry = getattr(pre, VERIFY_ENTRY)
        self.assertEqual(Path(inspect.getsourcefile(entry)).resolve(), VERIFY_MODULE.resolve())
        self.assertEqual(hashlib.sha256(VERIFY_MODULE.read_bytes()).hexdigest(), PREAUTH_VERIFY_SHA256)

    def test_a_decision_runs_the_frozen_verifier_exactly_once(self):
        keys = Keys()
        self.addCleanup(keys.close)
        pre = load_module()
        data = json.dumps(envelope(), sort_keys=True).encode("utf-8")
        seen = []
        real = getattr(pre, VERIFY_ENTRY)

        def spy(*args):
            seen.append(args)
            return real(*args)

        signature = keys.sign(data)
        with mock.patch.object(pre, VERIFY_ENTRY, spy), ProcessRecorder(self) as recorder:
            decision = pre._check_for_tests(
                data, signature, allowed_signers=str(keys.signers),
                trusted_fingerprints=frozenset({keys.fingerprint}), repo=REPO, base_rev=CURRENT_BASE,
                contract_sha256=CONTRACT, tier="T3", now=NOW, facts=facts(),
                is_ancestor=lambda older, newer: older == SIGNED_BASE and newer == CURRENT_BASE)
        self.assertTrue(decision["ok"])
        self.assertEqual(len(seen), 1)
        self.assertEqual(seen[0][0], data)
        self.assertEqual(seen[0][3], "kyle")
        self.assertEqual(len(recorder.calls), 1)


# --------------------------------------------------------------- r5: the frozen verifier itself
def load_verify(name="preauth_verify_under_test"):
    spec = importlib.util.spec_from_file_location(name, VERIFY_MODULE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class FrozenVerifyModuleTest(unittest.TestCase):
    """scripts/preauth_verify.py is committed at RED, frozen by sha256, and tested with real keys."""

    @classmethod
    def setUpClass(cls):
        cls.keys = Keys()
        cls.mod = load_verify()
        cls.data = json.dumps(envelope(), sort_keys=True).encode("utf-8")
        cls.sig = cls.keys.sign(cls.data)

    @classmethod
    def tearDownClass(cls):
        cls.keys.close()

    def verify(self, data=None, signature=None, signers=None, issuer="kyle", fail=None, hook=None):
        with ProcessRecorder(self, fail) as recorder:
            recorder.hook = hook
            result = getattr(self.mod, VERIFY_ENTRY)(
                self.data if data is None else data, self.sig if signature is None else signature,
                str(self.keys.signers) if signers is None else signers, issuer)
        return result, recorder

    def test_module_is_frozen_by_sha256(self):
        self.assertEqual(hashlib.sha256(VERIFY_MODULE.read_bytes()).hexdigest(), PREAUTH_VERIFY_SHA256,
                         "scripts/preauth_verify.py is frozen; a change needs an owner-approved contract")

    def test_every_name_it_defines_is_private(self):
        tree = ast.parse(VERIFY_MODULE.read_text(encoding="utf-8"))
        defined = []
        for node in tree.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                defined.append(node.name)
            elif isinstance(node, ast.Assign):
                defined.extend(t.id for t in node.targets if isinstance(t, ast.Name))
        self.assertIn(VERIFY_ENTRY, defined)
        self.assertEqual([n for n in defined if not n.startswith("_")], [])
        public = {n for n in dir(self.mod) if not n.startswith("_")}
        self.assertEqual(public, {"subprocess", "tempfile"})

    def test_exactly_one_literal_ssh_keygen_verify_call(self):
        tree = ast.parse(VERIFY_MODULE.read_text(encoding="utf-8"))
        calls = [n for n in ast.walk(tree) if isinstance(n, ast.Call) and _dotted(n.func) == "subprocess.run"]
        self.assertEqual(len(calls), 1)
        argv = calls[0].args[0]
        self.assertEqual([e.value for e in argv.elts[:4] if isinstance(e, ast.Constant)],
                         ["/usr/bin/ssh-keygen", "-Y", "verify", "-f"])
        self.assertEqual(process_violations(VERIFY_MODULE.read_text(encoding="utf-8")), [])
        self.assertEqual(self.mod._NAMESPACE, "rizzstack-preauth")
        self.assertTrue(0 < self.mod._TIMEOUT_SECONDS <= 60)

    def test_a_real_ed25519_signature_verifies(self):
        result, recorder = self.verify()
        self.assertIs(result, True)
        self.assertEqual(len(recorder.calls), 1)
        argv = recorder.calls[0]
        self.assertEqual(argv[:5], ["/usr/bin/ssh-keygen", "-Y", "verify", "-f", str(self.keys.signers)])
        self.assertEqual(argv[5:9], ["-I", "kyle", "-n", "rizzstack-preauth"])
        self.assertEqual(argv[9], "-s")
        self.assertEqual(recorder.envs, [{"PATH": "/usr/bin:/bin"}])

    def test_tampered_foreign_wrong_namespace_or_wrong_principal_is_false(self):
        other = Keys()
        self.addCleanup(other.close)
        tampered = json.dumps(envelope(max_uses=1), sort_keys=True).encode("utf-8")
        for kwargs in ({"data": tampered}, {"signature": other.sign(self.data)},
                       {"signature": self.keys.sign(self.data, namespace="git")}, {"issuer": "bob"},
                       {"signature": b"not a signature"}):
            result, _ = self.verify(**kwargs)
            self.assertIs(result, False, kwargs)

    def test_signature_goes_to_a_private_temp_file_that_is_removed(self):
        seen = {}

        def hook(argv):
            path = Path(argv[argv.index("-s") + 1])
            seen["path"], seen["mode"], seen["bytes"] = path, path.stat().st_mode & 0o777, path.read_bytes()

        result, _ = self.verify(hook=hook)
        self.assertIs(result, True)
        self.assertEqual(seen["mode"], 0o600)
        self.assertEqual(seen["path"].suffix, ".sig")
        self.assertEqual(seen["path"].parent.resolve(), Path(tempfile.gettempdir()).resolve())
        self.assertEqual(seen["bytes"], self.sig)
        self.assertFalse(seen["path"].exists())

    def test_missing_or_hanging_ssh_keygen_is_unavailable(self):
        result, _ = self.verify(fail="missing")
        self.assertIsNone(result)
        result, recorder = self.verify(fail="timeout")
        self.assertIsNone(result)
        self.assertTrue(recorder.timeouts and all(t is not None and 0 < t <= 60 for t in recorder.timeouts[:1]))

    def test_unsafe_inputs_are_false_without_starting_a_process(self):
        for kwargs in ({"issuer": "-kyle"}, {"issuer": "ky*"}, {"issuer": "kyle,bob"}, {"issuer": ""},
                       {"issuer": "k" * 65}, {"issuer": 7}, {"issuer": "kyle\n"}, {"issuer": "kyle bob"},
                       {"signers": "policy/allowed_signers"}, {"signers": "-f"}, {"signers": "/x\n"},
                       {"signers": 7}, {"data": "text"}, {"signature": b""}, {"signature": "sig"}):
            result, recorder = self.verify(**kwargs)
            self.assertIs(result, False, kwargs)
            self.assertEqual(recorder.calls, [], kwargs)

    def test_a_path_hijack_does_not_reach_the_frozen_verifier(self):
        fake = Path(tempfile.mkdtemp(prefix="preauth-hijack-"))
        self.addCleanup(shutil.rmtree, fake, True)
        (fake / "ssh-keygen").write_text("#!/bin/sh\necho Good signature; exit 0\n")
        os.chmod(fake / "ssh-keygen", 0o755)
        with mock.patch.dict(os.environ, {"PATH": str(fake) + os.pathsep + os.environ.get("PATH", "")}):
            result, recorder = self.verify(signature=b"-----BEGIN SSH SIGNATURE-----\nAAAA\n-----END SSH SIGNATURE-----\n")
        self.assertIs(result, False)
        self.assertEqual(recorder.calls[0][0], "/usr/bin/ssh-keygen")


if __name__ == "__main__":
    unittest.main()
