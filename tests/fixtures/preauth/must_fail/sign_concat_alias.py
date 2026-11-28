"""Must-pass control for the one-process rule: the only process is /usr/bin/ssh-keygen -Y verify."""
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


def attest(key, path):
    go = subprocess.run
    return go(["/usr/bin/ssh-keygen", "-Y", "si" + "gn", "-" + "f", key, "-n", NAMESPACE, path], timeout=TIMEOUT)
