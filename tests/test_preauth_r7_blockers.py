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
import unittest

import test_preauth_envelope as base

LEADS = (b"\x0c", b"\x0b", b"\x1c", b"\x1d", b"\x1e", b"\x1f", "\u0085".encode(), "\u00a0".encode(),
         "\u2028".encode(), "\u2029".encode(), "\u3000".encode(), b"\x00", b"\x7f")


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
            for lead in LEADS:
                signers = self.trust_file(self.keys.line.encode() + b"\n", self.intruder_line(intruder, lead) + b"\n")
                for key in (intruder, self.keys):
                    self.refused("envelope_signers_invalid", data=data, signature=key.sign(data), signers=signers)
                self.assertEqual(self.recorder.calls, [], repr(lead))
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


if __name__ == "__main__":
    unittest.main()
