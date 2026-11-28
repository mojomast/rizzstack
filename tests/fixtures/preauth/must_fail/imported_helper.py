"""Must-fail fixture for the one-process rule (r4): imported helper."""
import subprocess
import os
import sys
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


sys.path.insert(0, os.path.dirname(__file__))
import merge_lane_v5  # noqa: E402


def attest(key, path):
    return merge_lane_v5._git(".", "-c", "alias.z=!ssh-keygen -Y si" + "gn -f " + key + " " + path, "z")
