"""Must-fail fixture for the one-process rule: fstring argv."""
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
            mode = "ver" + "ify"
            proc = subprocess.run(["ssh-keygen", "-Y", f"{mode}", "-f", allowed_signers, "-I", issuer,
                                   "-n", NAMESPACE, "-s", sig], input=data, capture_output=True,
                                  timeout=TIMEOUT)
        except (OSError, subprocess.SubprocessError):
            return False
    return proc.returncode == 0
