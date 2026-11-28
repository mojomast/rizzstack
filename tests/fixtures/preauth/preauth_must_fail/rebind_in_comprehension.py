"""Must-pass control for the preauth.py rule: parsing and policy only, no process or write access."""
from __future__ import annotations

import json
import re

from preauth_verify import _ssh_verify

PRINCIPAL = re.compile(r"[A-Za-z0-9._@-]{1,64}")


class PreauthRefused(Exception):
    """A named refusal."""


def refuse(reason):
    error = PreauthRefused(reason)
    error.reason = reason
    raise error


def read_trust_root(path):
    with open(path, "rb") as handle:
        return handle.read()


def verified(data, signature, signers, issuer):
    if not PRINCIPAL.fullmatch(issuer):
        refuse("envelope_schema_invalid")
    result = _ssh_verify(data, signature, signers, issuer)
    if result is None:
        refuse("envelope_verifier_unavailable")
    if result is not True:
        refuse("envelope_bad_signature")
    return json.loads(data)


NAMES = [re for re in (1, 2)]
