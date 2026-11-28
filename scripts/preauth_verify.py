"""Frozen ssh-keygen verifier for rizzstack pre-authorization envelopes (preauth-envelope r6).

This file is frozen: its sha256 is pinned in the preauth-envelope contract and in
tests/test_preauth_envelope.py, so it changes only under a new owner-approved contract.

It starts exactly one process, ``/usr/bin/ssh-keygen -Y verify``, by absolute path, with the
constant environment ``PATH=/usr/bin:/bin`` and a bounded timeout.  The signature goes to a
private (0600, O_EXCL) temporary file with a constant suffix that is removed on return.  It
never signs and holds no key.  Defence in depth (r6): data and signature sizes are capped, and a
good result needs exit status 0 and exactly one stdout line starting
``Good "rizzstack-preauth" signature for <issuer> with``.  Every name it defines is private:
scripts/preauth.py imports ``_ssh_verify`` by name, and nothing else may use this module.
"""
import subprocess
import tempfile

_NAMESPACE = "rizzstack-preauth"
_TIMEOUT_SECONDS = 20
_MAX_DATA_BYTES = 65536
_MAX_SIGNATURE_BYTES = 8192
_PRINCIPAL_CHARS = frozenset("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789._@-")


def _plain_principal(value):
    return (isinstance(value, str) and 0 < len(value) <= 64 and value[0] != "-"
            and all(ch in _PRINCIPAL_CHARS for ch in value))


def _plain_signers_path(value):
    return (isinstance(value, str) and value.startswith("/") and "\n" not in value and "\0" not in value)


def _good_line(stdout, issuer):
    expected = b'Good "' + _NAMESPACE.encode("ascii") + b'" signature for ' + issuer.encode("ascii") + b" with "
    lines = stdout.splitlines() if isinstance(stdout, bytes) else []
    return len(lines) == 1 and lines[0].startswith(expected)


def _ssh_verify(data, signature, signers, issuer):
    """True for a good signature by issuer under signers, False for a bad one, None if unavailable."""
    if not (isinstance(data, bytes) and isinstance(signature, bytes) and signature
            and len(data) <= _MAX_DATA_BYTES and len(signature) <= _MAX_SIGNATURE_BYTES
            and _plain_signers_path(signers) and _plain_principal(issuer)):
        return False
    with tempfile.NamedTemporaryFile(suffix=".sig") as handle:
        handle.write(signature)
        handle.flush()
        sig_path = handle.name
        try:
            proc = subprocess.run(["/usr/bin/ssh-keygen", "-Y", "verify", "-f", signers, "-I", issuer,
                                   "-n", _NAMESPACE, "-s", sig_path], input=data, capture_output=True,
                                  timeout=_TIMEOUT_SECONDS, env={"PATH": "/usr/bin:/bin"})
        except (OSError, subprocess.SubprocessError):
            return None
    return proc.returncode == 0 and _good_line(proc.stdout, issuer)
