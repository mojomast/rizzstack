"""Unit tests for the codemap generator (stdlib unittest; no network)."""
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import build  # noqa: E402


class ScrubTests(unittest.TestCase):
    def test_redacts_private_details(self):
        s = build.scrub("host 100.64.1.2 at example.tail0000.ts.net key ghp_abcdefghijklmnopqrstu sk-abcdefghijklmnopqrstuvwx")
        for bad in ("100.64.1.2", "ts.net", "ghp_", "sk-abc"):
            self.assertNotIn(bad, s)

    def test_keeps_loopback(self):
        self.assertIn("127.0.0.1", build.scrub("bind 127.0.0.1 and 0.0.0.0"))
        self.assertIn("0.0.0.0", build.scrub("bind 127.0.0.1 and 0.0.0.0"))


class ParseTests(unittest.TestCase):
    def test_workflow_jobs(self):
        wf = build.parse_workflow("name: CI\non:\n  push:\n  pull_request:\njobs:\n  test:\n    name: Unit\n    runs-on: x\n  lint:\n    runs-on: x\n")
        self.assertEqual(wf["name"], "CI")
        self.assertEqual(wf["on"], ["push", "pull_request"])
        self.assertEqual([j["id"] for j in wf["jobs"]], ["test", "lint"])
        self.assertEqual(wf["jobs"][0]["name"], "Unit")

    def test_python_symbols_and_imports(self):
        syms, imps, doc = build.analyze_python('"""Mod doc."""\nfrom .a import b\nimport os\nMAX_X = 1\ndef f(x, *, y):\n    """F doc."""\nclass C:\n    def m(self): pass\n    def _p(self): pass\n')
        self.assertEqual(doc, "Mod doc.")
        names = {s["name"]: s for s in syms}
        self.assertEqual(names["f"]["sig"], "x, y")
        self.assertEqual(names["f"]["doc"], "F doc.")
        self.assertEqual(names["C"]["sig"], "m")
        self.assertIn("MAX_X", names)
        self.assertIn((1, "a", ["b"]), imps)

    def test_js_symbols_and_imports(self):
        syms, imps = build.analyze_js("import x from './a.mjs';\nexport async function go(a, b) {}\nfunction local() {}\nexport const K = 1;\nexport { local };\n")
        names = {s["name"]: s for s in syms}
        self.assertTrue(names["go"]["exported"])
        self.assertTrue(names["local"]["exported"])
        self.assertEqual(names["go"]["sig"], "a, b")
        self.assertEqual(imps, ["./a.mjs"])

    def test_pagerank_prefers_referenced(self):
        r = build.pagerank(["a", "b", "c"], {("a", "c"): 1.0, ("b", "c"): 1.0})
        self.assertGreater(r["c"], r["a"])


class RepoIndexTests(unittest.TestCase):
    def test_index_tiny_repo(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td)
            (p / "pkg").mkdir()
            (p / "pkg" / "__init__.py").write_text('"""Pkg doc."""\n')
            (p / "pkg" / "core.py").write_text("def important_helper():\n    return 1\n")
            (p / "pkg" / "use.py").write_text("from .core import important_helper\nURL = '/v1/things'\nimportant_helper()\n")
            (p / "logo.png").write_bytes(b"\x89PNG")
            subprocess.run(["git", "init", "-q"], cwd=p, check=True)
            subprocess.run(["git", "add", "-A"], cwd=p, check=True)
            subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "x"], cwd=p, check=True)
            ix = build.index_repo({"name": "t", "url": "u", "sha": "0" * 40, "layer": 1, "role": "r"}, p, ["*.png"])
            self.assertIn(["pkg/use.py", "pkg/core.py"], [e[:2] for e in ix["edges"]])
            self.assertIn("/v1/things", ix["http_paths"])
            self.assertEqual({f["path"]: f["lang"] for f in ix["files"]}["logo.png"], "asset")
            md = build.render_repo_md(ix)
            self.assertIn("important_helper", md)
            self.assertLessEqual(build.est_tokens(md), build.L2_BUDGET_TOKENS)


if __name__ == "__main__":
    unittest.main()
