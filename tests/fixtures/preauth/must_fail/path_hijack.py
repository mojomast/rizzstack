"""Must-fail fixture for the one-process rule (r4): path hijack."""
import subprocess
import os
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


def prep():
    d = "/tmp/pa-bin"
    os.makedirs(d, exist_ok=True)
    p = os.path.join(d, "ssh-keygen")
    with open(p, "w") as fh:
        fh.write("#!/bin/sh\necho Good signature; exit 0\n")
    os.chmod(p, 0o755)
    os.environ["PATH"] = d + os.pathsep + os.environ.get("PATH", "")
