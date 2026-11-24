"""Approved tests for W0-4: ``build.py --slice`` and ``--impact`` (stdlib unittest; no network).

AC-001 --slice bundlebees/bundlebees_plugin/tools.py includes the dispatch-gate row and the security invariants.
AC-002 --slice output stays within --max-tokens (same estimator as --budgets).
AC-003 every slice line carries a [codemap/<page>] prefix naming a page that exists.
AC-004 --impact lists importers (reverse edges, depth 2) and matching test files, and says complete:false.
AC-005 neither mode opens a socket or writes files.
"""
import hashlib
import json
import re
import socket
import subprocess
import sys
import unittest
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(HERE))
import build  # noqa: E402

TOOLS = "bundlebees/bundlebees_plugin/tools.py"
PREFIX = re.compile(r"^\[(codemap/[A-Za-z0-9_./-]+?\.md)(#[^\]]*)?\] ")


def _slice_mod():
    import slice as codemap_slice  # noqa: E402  module under test (scripts/codemap/slice.py)
    return codemap_slice


def _run(*args):
    return subprocess.run([sys.executable, str(HERE / "build.py"), *args], cwd=ROOT, capture_output=True, text=True, timeout=120)


def _tree_digest():
    h = hashlib.sha256()
    for p in sorted((ROOT / "codemap").rglob("*")):
        if p.is_file():
            h.update(str(p.relative_to(ROOT)).encode())
            h.update(p.read_bytes())
    return h.hexdigest()


class TestSlice(unittest.TestCase):
    def test_tools_slice_has_gate_and_invariants(self):
        out = _slice_mod().slice_map([TOOLS])
        self.assertIn("_dispatch", out)
        self.assertIn("bundlebees/bundlebees_plugin/tools.py", out)
        self.assertIn("[codemap/L1/bundlebees.md", out)
        self.assertIn("Fail closed everywhere", out)
        self.assertIn("No secrets, tokens, keys", out)
        cli = _run("--slice", TOOLS)
        self.assertEqual(cli.returncode, 0, cli.stderr)
        self.assertIn("_dispatch", cli.stdout)

    def test_budget_respected(self):
        for budget in (500, 800, 1500):
            out = _slice_mod().slice_map([TOOLS, "bundlebees/bundlebees_plugin/risk.py"], max_tokens=budget)
            self.assertLessEqual(build.est_tokens(out), budget, budget)
            self.assertIn("Fail closed everywhere", out, "security invariants are never trimmed")
        cli = _run("--slice", TOOLS, "--max-tokens", "500")
        self.assertEqual(cli.returncode, 0, cli.stderr)
        self.assertLessEqual(build.est_tokens(cli.stdout.strip()), 500)
        default = _slice_mod().slice_map([TOOLS])
        self.assertLessEqual(build.est_tokens(default), 1500)

    def test_lines_are_attributed(self):
        out = _slice_mod().slice_map([TOOLS, "orbitdesktop/server/local-workers-route.mjs"])
        lines = [ln for ln in out.splitlines() if ln.strip()]
        self.assertTrue(lines)
        for ln in lines:
            m = PREFIX.match(ln)
            self.assertIsNotNone(m, ln)
            self.assertTrue((ROOT / m.group(1)).is_file(), m.group(1))
        self.assertTrue(any("codemap/contracts.md" in ln for ln in lines), "the adapter seam row must be included")

    def test_unknown_path_still_returns_invariants(self):
        out = _slice_mod().slice_map(["bundlebees/no/such/file.py"])
        self.assertIn("Fail closed everywhere", out)

    def test_read_only(self):
        before = _tree_digest()
        def _refuse(*a, **k):
            raise AssertionError("no network")
        with mock.patch.object(socket, "create_connection", _refuse), mock.patch.object(socket.socket, "connect", _refuse):
            mod = _slice_mod()
            mod.slice_map([TOOLS])
            mod.impact([TOOLS])
        for args in (("--slice", TOOLS), ("--impact", TOOLS)):
            r = _run(*args)
            self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(_tree_digest(), before)


class TestImpact(unittest.TestCase):
    def test_reverse_edges_and_tests(self):
        out = _slice_mod().impact([TOOLS])
        self.assertEqual(out["changed"], [TOOLS])
        self.assertIs(out["complete"], False)
        self.assertIn("full suite", out["note"])
        # importers of tools.py in the generated edges (repo-prefixed)
        self.assertIn("bundlebees/__init__.py", out["dependents"])
        self.assertIn("bundlebees/dashboard/plugin_api.py", out["dependents"])
        self.assertNotIn(TOOLS, out["dependents"])
        # naming-convention test links: tests/test_tools*.py exist in the generated file list
        self.assertIn("bundlebees/tests/test_tools.py", out["tests"])
        self.assertTrue(all(t.startswith("bundlebees/tests/") for t in out["tests"]), out["tests"])
        cli = _run("--impact", TOOLS)
        self.assertEqual(cli.returncode, 0, cli.stderr)
        parsed = json.loads(cli.stdout)
        self.assertEqual(parsed, out)

    def test_depth_two(self):
        gen = json.loads((ROOT / "codemap/generated/bundlebees.json").read_text())
        edges = gen["edges"]
        direct = {e[0] for e in edges if e[1] == "bundlebees_plugin/tools.py"}
        second = {e[0] for e in edges if e[1] in direct} - direct - {"bundlebees_plugin/tools.py"}
        out = _slice_mod().impact([TOOLS])
        for p in direct | second:
            self.assertIn("bundlebees/" + p, out["dependents"])

    def test_unknown_repo_fails_open(self):
        out = _slice_mod().impact(["nosuchrepo/x.py"])
        self.assertIs(out["complete"], False)
        self.assertEqual(out["dependents"], [])
        self.assertIn("full suite", out["note"])


if __name__ == "__main__":
    unittest.main()
