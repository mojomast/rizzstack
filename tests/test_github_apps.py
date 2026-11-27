"""Contract for tools/github-apps (docs/github-apps.md).

The manifests ask for exactly the documented permissions and nothing that could
change settings, rulesets or secrets; the generated page POSTs the manifest with a
random state; the state file is private; and the scripts never echo secrets.
"""
import json
import os
import re
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / "tools" / "github-apps"

REVIEWER = {"pull_requests": "write", "contents": "read", "checks": "read", "metadata": "read"}
COORDINATOR = {
    "contents": "write", "pull_requests": "write", "issues": "write", "actions": "write",
    "workflows": "write", "checks": "read", "statuses": "read", "metadata": "read",
}
FORBIDDEN = ("administration", "secrets", "members", "environments", "organization_administration",
             "repository_hooks", "organization_hooks", "variables")


def template(role):
    return json.loads((TOOLS / "manifests" / ("%s.manifest.template.json" % role)).read_text())


class ManifestTests(unittest.TestCase):
    def test_exact_permissions(self):
        self.assertEqual(template("reviewer")["default_permissions"], REVIEWER)
        self.assertEqual(template("coordinator")["default_permissions"], COORDINATOR)

    def test_nothing_that_changes_settings_or_secrets(self):
        for role in ("reviewer", "coordinator"):
            perms = template(role)["default_permissions"]
            for name in FORBIDDEN:
                self.assertNotIn(name, perms, "%s asks for %s" % (role, name))

    def test_private_no_events_inactive_hook_placeholders(self):
        for role in ("reviewer", "coordinator"):
            m = template(role)
            self.assertIs(m["public"], False)
            self.assertEqual(m["default_events"], [])
            self.assertIs(m["hook_attributes"]["active"], False)
            self.assertEqual(m["name"], "<app-name>")
            self.assertIn("<owner>", m["url"])


class PageTests(unittest.TestCase):
    def run_tool(self, out, *extra):
        subprocess.run([sys.executable, str(TOOLS / "make_submit_page.py"), "--role", "coordinator",
                        "--owner", "example-owner", "--out", out, *extra],
                       check=True, capture_output=True, text=True)

    def test_page_posts_manifest_with_random_state(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = os.path.join(tmp, "o")
            self.run_tool(out)
            page = Path(out, "rizzstack-coordinator.html").read_text()
            m = re.search(r'<form id="f" method="post" action="https://github\.com/settings/apps/new\?state=([0-9a-f]{32})">', page)
            self.assertIsNotNone(m)
            state_path = Path(out, "rizzstack-coordinator.state.json")
            st = json.loads(state_path.read_text())
            self.assertEqual(st["state"], m.group(1))
            self.assertEqual(stat.S_IMODE(state_path.stat().st_mode), 0o600)
            self.assertEqual(stat.S_IMODE(Path(out).stat().st_mode), 0o700)
            manifest = json.loads(Path(out, "rizzstack-coordinator.manifest.json").read_text())
            self.assertEqual(manifest["default_permissions"], COORDINATOR)
            self.assertEqual(manifest["url"], "https://github.com/example-owner/rizzstack")
            self.assertNotIn("<owner>", json.dumps(manifest))
            self.assertIn('name="manifest"', page)

    def test_states_differ_and_org_and_fallback_name(self):
        with tempfile.TemporaryDirectory() as tmp:
            a, b = os.path.join(tmp, "a"), os.path.join(tmp, "b")
            self.run_tool(a)
            self.run_tool(b, "--org", "--name", "rizzstack-coordinator-exowner")
            sa = json.loads(Path(a, "rizzstack-coordinator.state.json").read_text())["state"]
            page = Path(b, "rizzstack-coordinator-exowner.html").read_text()
            self.assertIn("https://github.com/organizations/example-owner/settings/apps/new?state=", page)
            self.assertNotIn(sa, page)


class ScriptTests(unittest.TestCase):
    def test_shell_syntax(self):
        for name in ("convert.sh", "verify.sh", "fingerprint.sh"):
            subprocess.run(["bash", "-n", str(TOOLS / name)], check=True)

    def test_convert_writes_key_exclusively_and_drops_secrets(self):
        text = (TOOLS / "convert.sh").read_text()
        self.assertIn("os.O_EXCL", text)
        self.assertIn("0o600", text)
        self.assertIn('data.pop("client_secret"', text)
        self.assertIn('data.pop("webhook_secret"', text)
        self.assertIn("read -rs", text)
        self.assertNotRegex(text, r"print\([^)]*pem\b(?!_)")

    def test_verify_narrows_and_revokes_the_token(self):
        text = (TOOLS / "verify.sh").read_text()
        self.assertIn('{"permissions": {"metadata": "read"}}', text)
        self.assertIn('"DELETE", "/installation/token"', text)
        self.assertNotRegex(text, r"print\([^)]*\btok\b")


if __name__ == "__main__":
    unittest.main()
