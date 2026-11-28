"""Must-fail fixture for the one-process rule (r4): write open."""
import subprocess
import tempfile

NAMESPACE = "rizzstack-preauth"
TIMEOUT = 30


def verify(data, signature, allowed_signers, issuer):
    with tempfile.NamedTemporaryFile(suffix=".sig") as fh:
        fh.write(signature)
        fh.flush()
        sig = fh.name
        try:
            proc = subprocess.run(["/usr/bin/ssh-keygen", "-Y", "verify", "-f", allowed_signers, "-I", issuer,
                                   "-n", NAMESPACE, "-s", sig], input=data, capture_output=True,
                                  timeout=TIMEOUT, env={"PATH": "/usr/bin:/bin"})
        except (OSError, subprocess.SubprocessError):
            return False
    return proc.returncode == 0


def prep(p):
    with open(p, "w") as fh:
        fh.write("#!/bin/sh\nexit 0\n")
