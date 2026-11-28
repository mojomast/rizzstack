"""Must-pass control for the preauth.py rule (r6): parsing and policy only; reads only its two pinned files."""
from __future__ import annotations

import json
import re

from preauth_verify import _ssh_verify

TRUST_ROOT = __file__.rsplit("/", 2)[0] + "/policy/allowed_signers"
POLICY = __file__.rsplit("/", 2)[0] + "/policy/preauth.md"
PRINCIPAL = re.compile(r"[A-Za-z0-9._@-]{1,64}")


class PreauthRefused(Exception):
    """A named refusal."""


def refuse(reason):
    error = PreauthRefused(reason)
    error.reason = reason
    raise error


def read_trust_root():
    with open(TRUST_ROOT, "rb") as handle:
        return handle.read()


def read_policy():
    with open(POLICY, "rb") as handle:
        return handle.read()


def verified(data, signature, issuer):
    if not PRINCIPAL.fullmatch(issuer):
        refuse("envelope_schema_invalid")
    result = _ssh_verify(data, signature, TRUST_ROOT, issuer)
    if result is None:
        refuse("envelope_verifier_unavailable")
    if result is not True:
        refuse("envelope_bad_signature")
    return json.loads(data)


def leak():
    print(read_policy())
