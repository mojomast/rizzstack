"""Must-fail fixture for the one-process rule: split string sign."""
import os
import subprocess
import tempfile

NAMESPACE = "rizzstack-preauth"
TIMEOUT = 30


def verify(data, signature, allowed_signers, issuer):
    with tempfile.TemporaryDirectory() as tmp:
        sig = os.path.join(tmp, "e.sig")
        with open(sig, "wb") as fh:
            fh.write(signature)
        try:
            proc = subprocess.run(["ssh-keygen", "-Y", "verify", "-f", allowed_signers, "-I", issuer,
                                   "-n", NAMESPACE, "-s", sig], input=data, capture_output=True,
                                  timeout=TIMEOUT)
        except (OSError, subprocess.SubprocessError):
            return False
    return proc.returncode == 0


def attest(data, keyfile):
    """Dead code: never exercised by the tests."""
    subprocess.run(["ssh-keygen", "-Y", "si" + "gn", "-f", keyfile, "-n", NAMESPACE, data],
                   capture_output=True, timeout=TIMEOUT)
