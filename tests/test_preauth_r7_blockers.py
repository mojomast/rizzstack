"""Regression tests for the two blocking findings on the preauth-envelope slice (r7).

BLOCKER 1 (AC-005, trust-root pin bypass): ``trust_fingerprints`` must read the trust root the way
ssh-keygen does: lines end at b"\\n" only, fields split on space or tab only, and any byte that is
not printable ASCII (or a tab) makes the file invalid.  Otherwise a line led by a Unicode or
control "whitespace" character is a comment to preauth.py but a real entry to ssh-keygen.

BLOCKER 2 (AC-015/016, re-freeze oracle fail-open): measured oracle maps are normalised first, a
collision between two raw keys refuses, and the normalised maps are compared.

Run: python3 -m pytest -q -p no:cacheprovider tests/test_preauth_r7_blockers.py
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import test_preauth_envelope as base

LEADS = (b"\x0c", b"\x0b", b"\x1c", b"\x1d", b"\x1e", b"\x1f", "\u0085".encode(), "\u00a0".encode(),
         "\u2028".encode(), "\u2029".encode(), "\u3000".encode(), b"\x00", b"\x7f")
# Leads that real ssh-keygen treats as a live allowed-signers entry (R9 fuzz). NUL and DEL are
# still refused by the byte rule, but ssh-keygen does not accept them, so they are not in this set.
BYPASS_LEADS = (b"\x0c", b"\x0b", b"\x1c", b"\x1d", b"\x1e", b"\x1f", "\u0085".encode(), "\u00a0".encode(),
                "\u2028".encode(), "\u2029".encode(), "\u3000".encode())


def ssh_keygen_accepts(data, signature, signers, issuer="kyle"):
    """True if /usr/bin/ssh-keygen -Y verify accepts signature as issuer under signers."""
    signers = os.path.abspath(os.fsdecode(signers))
    with tempfile.NamedTemporaryFile(suffix=".sig") as handle:
        handle.write(signature)
        handle.flush()
        proc = subprocess.run(
            ["/usr/bin/ssh-keygen", "-Y", "verify", "-f", signers, "-I", issuer,
             "-n", "rizzstack-preauth", "-s", handle.name],
            input=data, capture_output=True, timeout=20, env={"PATH": "/usr/bin:/bin"},
        )
    expected = b'Good "rizzstack-preauth" signature for ' + issuer.encode("ascii") + b" with "
    lines = proc.stdout.splitlines()
    return proc.returncode == 0 and len(lines) == 1 and lines[0].startswith(expected)


class TrustRootParsingTest(base.EnvelopeCase):
    def trust_file(self, *chunks, name="r7_signers"):
        path = self.keys.dir / name
        path.write_bytes(b"".join(chunks))
        return path

    def intruder_line(self, intruder, lead):
        parts = intruder.public.split()
        return lead + b'#,* namespaces="rizzstack-preauth" ' + parts[0].encode() + b" " + parts[1].encode()

    def test_a_comment_hidden_from_preauth_but_not_from_ssh_keygen_is_refused(self):
        intruder = base.Keys(principal="kyle")
        try:
            data = json.dumps(base.envelope(), sort_keys=True).encode()
            accepted_by_ssh = []
            for lead in LEADS:
                signers = self.trust_file(self.keys.line.encode() + b"\n", self.intruder_line(intruder, lead) + b"\n")
                intruder_sig = intruder.sign(data)
                if ssh_keygen_accepts(data, intruder_sig, signers):
                    accepted_by_ssh.append(lead)
                for key, sig in ((intruder, intruder_sig), (self.keys, self.keys.sign(data))):
                    self.refused("envelope_signers_invalid", data=data, signature=sig, signers=signers)
                self.assertEqual(self.recorder.calls, [], repr(lead))
            # Probe every lead against this OpenSSH; only leads it really accepts count for
            # vacuity, and at least one must be accepted, so a different OpenSSH cannot turn
            # the test red while the checker is still correct.
            self.assertGreaterEqual(len(accepted_by_ssh), 1,
                                    "vacuity: local ssh-keygen accepted none of the hidden-entry leads")
        finally:
            intruder.close()

    def test_any_control_or_non_ascii_byte_anywhere_in_the_trust_root_is_refused(self):
        key = self.keys.line.encode()
        for byte in (b"\x0c", b"\x0b", b"\x1c", b"\x00", b"\x7f", b"\x1b", "\u0085".encode(), "\u00e9".encode(),
                     "\u2028".encode(), b"\xff"):
            for blob in (key + b"\n# note " + byte + b"\n",
                         key + b" " + byte + b"\n",
                         byte + key + b"\n",
                         key + b"\n" + byte + b"\n"):
                self.refused("envelope_signers_invalid", signers=self.trust_file(blob))
        # a bare carriage return inside a line is not a line ending for ssh-keygen: refuse it too
        self.refused("envelope_signers_invalid", signers=self.trust_file(key + b"\r# x\n"))

    def test_ordinary_ascii_trust_roots_still_work(self):
        key = self.keys.line.encode()
        parts = key.split()
        tabbed = b"\t".join(parts)
        for blob in (b"# comment\n" + key + b"\n",
                     key + b"\n",
                     b"   # indented comment\n\t# tab comment\n\n" + key + b"\n",
                     b"  " + key + b"  \n",
                     tabbed + b"\n",
                     key + b"\r\n"):
            data = json.dumps(base.envelope(), sort_keys=True).encode()
            result = self.decide(data=data, signature=self.keys.sign(data), signers=self.trust_file(blob))
            self.assertTrue(result["ok"], repr(blob))

    def test_comment_detection_uses_space_and_tab_only(self):
        key = self.keys.line.encode()
        # first non-space/tab byte is not '#': this is an entry, and a malformed one
        self.refused("envelope_signers_invalid", signers=self.trust_file(key + b"\n", b"x#,* junk\n"))


class OracleNormalisationTest(base.EnvelopeCase):
    def refreeze(self):
        entry = dict(base.envelope()["contracts"][0])
        entry["refreeze_of"] = {"contract_sha256": "d" * 64, "source_rev": "e" * 40}
        entry["test_files"] = ["tests/test_review_packet.py"]
        return base.envelope(contracts=[entry], action_class="refreeze_contract")

    def run_old_new(self, old, new):
        return self.decide(self.refreeze(), measured=base.facts(oracle={"old": old, "new": new}))

    def test_changed_oracle_file_with_non_canonical_keys_refuses(self):
        for old_key, new_key in (("./tests/test_review_packet.py", "./tests/test_review_packet.py"),
                                 ("tests//test_review_packet.py", "tests//test_review_packet.py"),
                                 ("./tests/test_review_packet.py", "tests/test_review_packet.py"),
                                 ("tests/test_review_packet.py", "./tests/test_review_packet.py"),
                                 ("tests/./conftest.py", "tests//conftest.py"),
                                 ("tests/x/../conftest.py", "tests/x/../conftest.py")):
            old = {old_key: "1" * 40}
            new = {new_key: "9" * 40}
            self.refused("envelope_oracle_changed", self.refreeze(),
                         measured=base.facts(oracle={"old": old, "new": new}))

    def test_unchanged_oracle_with_equivalent_spellings_is_allowed(self):
        old = {"./tests/test_review_packet.py": "1" * 40, "tests//conftest.py": "2" * 40, "src/thing.py": "7" * 40}
        new = {"tests/test_review_packet.py": "1" * 40, "./tests/conftest.py": "2" * 40, "src/thing.py": "8" * 40}
        self.assertTrue(self.run_old_new(old, new)["ok"])

    def test_two_raw_keys_that_normalise_to_one_path_refuse(self):
        for pair in (("tests/test_review_packet.py", "./tests/test_review_packet.py"),
                     ("tests/conftest.py", "tests//conftest.py"),
                     ("tests/a/../conftest.py", "tests/conftest.py")):
            colliding = {pair[0]: "1" * 40, pair[1]: "1" * 40}
            clean = {"tests/test_review_packet.py": "1" * 40, "tests/conftest.py": "1" * 40}
            self.refused("envelope_oracle_unproven", self.refreeze(),
                         measured=base.facts(oracle={"old": colliding, "new": clean}))
            self.refused("envelope_oracle_unproven", self.refreeze(),
                         measured=base.facts(oracle={"old": clean, "new": colliding}))

    def test_a_path_present_in_only_one_map_still_refuses_after_normalising(self):
        old = {"./tests/test_review_packet.py": "1" * 40}
        new = {}
        self.refused("envelope_oracle_changed", self.refreeze(), measured=base.facts(oracle={"old": old, "new": new}))

    def test_trailing_slash_and_dot_slash_spellings_are_normalised(self):
        # Trailing slash, "//" and "./" cannot appear in a git tree path, so normalising them
        # cannot alias two real files. A changed file under those spellings is
        # envelope_oracle_changed; two raw keys that fold to the same path are
        # envelope_oracle_unproven; equivalent spellings of an unchanged file are allowed.
        # Backslash is not normalised: it is a legal git path character (see the backslash tests).
        digest, changed = "1" * 40, "9" * 40
        canonical = "tests/test_review_packet.py"
        for spelling in ("./tests/test_review_packet.py", "tests//test_review_packet.py",
                         "tests/test_review_packet.py/", "tests/test_review_packet.py//",
                         "tests/./test_review_packet.py"):
            self.refused("envelope_oracle_changed", self.refreeze(),
                         measured=base.facts(oracle={"old": {spelling: digest}, "new": {spelling: changed}}))
            self.refused("envelope_oracle_changed", self.refreeze(),
                         measured=base.facts(oracle={"old": {canonical: digest}, "new": {spelling: changed}}))
            self.refused("envelope_oracle_changed", self.refreeze(),
                         measured=base.facts(oracle={"old": {spelling: digest}, "new": {canonical: changed}}))
        for left, right in ((canonical, "tests/test_review_packet.py/"),
                            (canonical, "tests/test_review_packet.py//"),
                            ("./tests/test_review_packet.py", canonical),
                            ("tests/conftest.py", "tests/conftest.py/")):
            colliding = {left: digest, right: digest}
            clean = {canonical: digest, "tests/conftest.py": digest}
            self.refused("envelope_oracle_unproven", self.refreeze(),
                         measured=base.facts(oracle={"old": colliding, "new": clean}))
            self.refused("envelope_oracle_unproven", self.refreeze(),
                         measured=base.facts(oracle={"old": clean, "new": colliding}))
        old = {"./tests/test_review_packet.py": digest, "tests/conftest.py/": "2" * 40, "src/thing.py": "7" * 40}
        new = {canonical: digest, "tests/conftest.py": "2" * 40, "src/thing.py": "8" * 40}
        self.assertTrue(self.run_old_new(old, new)["ok"])

    def test_a_backslash_in_an_oracle_key_is_unproven(self):
        # A backslash is a legal filename character on Linux and in git, so folding it to slash
        # would merge two distinct tree paths. Any raw key containing "\" refuses
        # envelope_oracle_unproven, whether the hash changed, stayed the same, or collides
        # with a slash spelling in the same map.
        digest, changed = "1" * 40, "9" * 40
        canonical = "tests/test_review_packet.py"
        for spelling in (r"tests\test_review_packet.py", r"tests\test_review_packet.py/",
                         r"foo\bar.py", r"tests/foo\bar.py"):
            self.refused("envelope_oracle_unproven", self.refreeze(),
                         measured=base.facts(oracle={"old": {spelling: digest}, "new": {spelling: changed}}))
            self.refused("envelope_oracle_unproven", self.refreeze(),
                         measured=base.facts(oracle={"old": {canonical: digest}, "new": {spelling: changed}}))
            self.refused("envelope_oracle_unproven", self.refreeze(),
                         measured=base.facts(oracle={"old": {spelling: digest}, "new": {canonical: changed}}))
            self.refused("envelope_oracle_unproven", self.refreeze(),
                         measured=base.facts(oracle={"old": {spelling: digest}, "new": {spelling: digest}}))
        colliding = {canonical: digest, r"tests\test_review_packet.py": digest}
        clean = {canonical: digest, "tests/conftest.py": digest}
        self.refused("envelope_oracle_unproven", self.refreeze(),
                     measured=base.facts(oracle={"old": colliding, "new": clean}))
        self.refused("envelope_oracle_unproven", self.refreeze(),
                     measured=base.facts(oracle={"old": clean, "new": colliding}))

    def test_a_slash_to_backslash_swap_is_unproven(self):
        # Real-git case: git mv tests/test_review_packet.py to a literal backslash filename
        # keeps the blob and pytest stops collecting the file. Both directions must refuse
        # (envelope_oracle_unproven), not compare as unchanged after normalisation.
        digest = "03348f5f0f815389d5261ab8ed4aff3e237e68bc"
        slash = "tests/test_review_packet.py"
        backslash = r"tests\test_review_packet.py"
        self.refused("envelope_oracle_unproven", self.refreeze(),
                     measured=base.facts(oracle={"old": {slash: digest}, "new": {backslash: digest}}))
        self.refused("envelope_oracle_unproven", self.refreeze(),
                     measured=base.facts(oracle={"old": {backslash: digest}, "new": {slash: digest}}))

    def test_a_non_hex_oracle_value_refuses(self):
        good = {"tests/test_review_packet.py": "1" * 40, "tests/conftest.py": "2" * 40}
        bad_values = (
            None, "?", "", "1" * 39, "1" * 41, "1" * 63, "1" * 65, "g" * 40, "1" * 39 + "g",
            40, True, [],
            "1" * 40 + "\n", " " + "1" * 40, "1" * 40 + " ", "\t" + "1" * 40, "1" * 40 + "\t",
        )
        for value in bad_values:
            for side in ("old", "new"):
                old, new = dict(good), dict(good)
                (old if side == "old" else new)["tests/test_review_packet.py"] = value
                self.refused("envelope_oracle_unproven", self.refreeze(),
                             measured=base.facts(oracle={"old": old, "new": new}))
            # same bad value on both sides is still unproven, not a successful "unchanged"
            both = dict(good, **{"tests/test_review_packet.py": value})
            self.refused("envelope_oracle_unproven", self.refreeze(),
                         measured=base.facts(oracle={"old": both, "new": dict(both)}))
        # Lowercase 40- and 64-character hex values remain accepted. The tests do not require
        # uppercase or mixed-case hex to be accepted (an implementer may treat lowercase as
        # canonical). Comparison is exact, so a case mismatch of the same digest is not "unchanged".


class ProductionCheckTest(unittest.TestCase):
    """The hidden-entry pin bypass is also refused at check(), via an AC-027-style module copy."""

    def test_check_refuses_a_hidden_trust_root_entry(self):
        keys = base.Keys()
        intruder = base.Keys(principal="kyle")
        root = Path(tempfile.mkdtemp(prefix="preauth-r7-check-"))
        try:
            (root / "scripts").mkdir()
            (root / "policy").mkdir()
            shutil.copyfile(base.MODULE, root / "scripts" / "preauth.py")
            shutil.copyfile(base.VERIFY_MODULE, root / "scripts" / "preauth_verify.py")
            (root / "policy" / "preauth.md").write_bytes(base.TEST_POLICY)
            signers = root / "policy" / "allowed_signers"
            copy = base._load_copy(root, "preauth_r7_check_copy")
            data = json.dumps(base.envelope(), sort_keys=True).encode()
            signature = intruder.sign(data)
            chosen = None
            for lead in BYPASS_LEADS:
                parts = intruder.public.split()
                hidden = lead + b'#,* namespaces="rizzstack-preauth" ' + parts[0].encode() + b" " + parts[1].encode()
                signers.write_bytes(keys.line.encode() + b"\n" + hidden + b"\n")
                if ssh_keygen_accepts(data, signature, signers):
                    chosen = lead
                    break
            self.assertIsNotNone(chosen, "vacuity: local ssh-keygen accepted none of the hidden-entry leads")
            with mock.patch.object(copy, "OWNER_FINGERPRINTS", (keys.fingerprint,)):
                with self.assertRaises(copy.PreauthRefused) as caught:
                    copy.check(
                        data, signature, repo=base.REPO, base_rev=base.CURRENT_BASE,
                        contract_sha256=base.CONTRACT, tier="T3", now=base.NOW,
                        facts=base.facts(),
                        is_ancestor=lambda older, newer: older == base.SIGNED_BASE and newer == base.CURRENT_BASE,
                    )
            self.assertEqual(caught.exception.reason, "envelope_signers_invalid")
            self.assertEqual(caught.exception.args, (caught.exception.reason,))
        finally:
            keys.close()
            intruder.close()
            shutil.rmtree(root, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
