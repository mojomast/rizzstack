"""Contract for this repo's GitHub Actions hygiene (rizzstack/codemap/ci-gates.md, shared rules).

- a gitleaks job scans full history on every pull request and push to main;
- every action is pinned to a full commit SHA;
- every checkout sets persist-credentials: false;
- workflow token permissions are read-only, except the one allow-listed
  `issues: write` the Factory status watchdog needs to rewrite its single issue.
"""
import re
import unittest
from pathlib import Path

# The one workflow allowed a write scope, and the only scope it may have.
# Widening this map is a reviewed change: every other workflow stays read-only.
WRITE_EXEMPT = {"factory-status.yml": {"issues"}}

ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = ROOT / ".github" / "workflows"
GITLEAKS = WORKFLOWS / "gitleaks.yml"


def workflow_files():
    return sorted(list(WORKFLOWS.glob("*.yml")) + list(WORKFLOWS.glob("*.yaml")))


def checkout_blocks(text):
    """Each `uses: actions/checkout@...` step with its indented `with:` lines."""
    lines = text.splitlines()
    blocks = []
    for i, line in enumerate(lines):
        if re.match(r"^\s*-?\s*uses:\s*actions/checkout@", line):
            indent = len(line) - len(line.lstrip(" -"))
            body = [line]
            for nxt in lines[i + 1:]:
                stripped = nxt.lstrip(" ")
                if stripped.startswith("- ") or (stripped and len(nxt) - len(stripped) < indent):
                    break
                body.append(nxt)
            blocks.append("\n".join(body))
    return blocks


class GitleaksJobTests(unittest.TestCase):
    def text(self):
        self.assertTrue(GITLEAKS.is_file(), "missing .github/workflows/gitleaks.yml")
        return GITLEAKS.read_text()

    def test_runs_on_pull_requests_and_pushes_to_main(self):
        t = self.text()
        self.assertIn("pull_request:", t)
        self.assertRegex(t, r"push:\s*\n\s*branches:\s*\[main\]")
        self.assertNotIn("pull_request_target", t)

    def test_job_has_a_stable_name_and_is_bounded_and_blocking(self):
        t = self.text()
        self.assertRegex(t, r"(?m)^    name:\s*gitleaks\s*$")
        self.assertRegex(t, r"timeout-minutes:\s*\d+")
        self.assertNotIn("continue-on-error", t)

    def test_scans_full_history_with_a_checksum_verified_scanner(self):
        t = self.text()
        self.assertRegex(t, r"fetch-depth:\s*0")
        self.assertRegex(t, r"echo \"[0-9a-f]{64}  gitleaks\.tgz\" \| sha256sum -c -")
        self.assertRegex(t, r"gitleaks detect --source \. --redact")
        self.assertNotRegex(t, r"--exit-code\s+0")

    def test_needs_no_secrets(self):
        self.assertNotIn("secrets.", self.text())


class WorkflowHygieneTests(unittest.TestCase):
    def test_every_action_is_pinned_to_a_full_sha(self):
        for wf in workflow_files():
            for ref in re.findall(r"(?m)^\s*-?\s*uses:\s*(\S+)", wf.read_text()):
                if ref.startswith("./"):
                    continue
                self.assertRegex(ref, r"@[0-9a-f]{40}$", "%s: %s" % (wf.name, ref))

    def test_every_checkout_drops_the_token(self):
        for wf in workflow_files():
            for block in checkout_blocks(wf.read_text()):
                self.assertRegex(block, r"persist-credentials:\s*false", "%s:\n%s" % (wf.name, block))

    def test_token_permissions_are_read_only(self):
        for wf in workflow_files():
            t = wf.read_text()
            m = re.search(r"(?m)^permissions:\s*\n((?:[ \t]+.*\n)+)", t)
            self.assertIsNotNone(m, "%s: top-level permissions block required" % wf.name)
            self.assertIn("contents: read", m.group(1))
            writes = re.findall(r"(?m)^\s+([a-z-]+):\s*write\s*$", t)
            allowed = WRITE_EXEMPT.get(wf.name, set())
            self.assertEqual(sorted(allowed), sorted(writes), "%s: write scopes" % wf.name)
            self.assertNotIn("contents: write", t, wf.name)

    def test_only_the_watchdog_may_hold_a_write_scope(self):
        self.assertEqual({"factory-status.yml": {"issues"}}, WRITE_EXEMPT)
        self.assertTrue((WORKFLOWS / "factory-status.yml").is_file())


if __name__ == "__main__":
    unittest.main()
