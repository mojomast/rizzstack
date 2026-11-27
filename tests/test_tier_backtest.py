"""Tests for the risk-tier back-test harness.

The harness must run the SAME ``risk_tier`` the gate runs, so its split cannot
drift from the gate: these tests build a two-commit repository and check that the
harness reports the tier the gate gives those rows, including the allowlist's
T1/T3 boundary and the ``repo_unknown`` fail-closed case.
"""

import importlib.util
import json
import subprocess
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parents[1] / ".github" / "scripts"
SCRIPT = HERE / "tier_backtest.py"
GATE_SCRIPT = HERE / "lease_tamper.py"


def load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


BACKTEST = load(SCRIPT, "tier_backtest_under_test")
GATE = load(GATE_SCRIPT, "tier_backtest_gate_under_test")


class Repo:
    def __init__(self, path):
        self.path = Path(path)
        self.path.mkdir(parents=True, exist_ok=True)
        self.git("init", "-b", "main")
        self.git("config", "user.email", "backtest@example.invalid")
        self.git("config", "user.name", "Tier Backtest")
        self.git("config", "commit.gpgsign", "false")

    def git(self, *args):
        proc = subprocess.run(
            ["git", "-C", str(self.path), *args], capture_output=True, text=True
        )
        if proc.returncode != 0:
            raise AssertionError("git %s failed: %s%s" % (" ".join(args), proc.stdout, proc.stderr))
        return proc

    def write(self, relative, text):
        target = self.path / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")

    def commit(self, message):
        self.git("add", "-A")
        self.git("commit", "-m", message)
        return self.git("rev-parse", "HEAD").stdout.strip()


class BackTest(unittest.TestCase):
    def test_the_split_matches_the_gate_and_fails_closed(self):
        root = tempfile.mkdtemp(prefix="tier-backtest-")
        repo = Repo(root)
        repo.write("docs/readme.md", "# docs\n")
        repo.write("pyproject.toml", '[project]\nname = "slice"\n')
        base = repo.commit("base")
        repo.git("checkout", "-q", "-b", "feat/docs")
        repo.write("docs/guide.md", "# guide\n")
        docs_head = repo.commit("a documentation change")
        repo.git("checkout", "-q", "main")
        repo.git("checkout", "-q", "-b", "feat/manifest")
        repo.write("uv.lock", "version = 1\n")
        manifest_head = repo.commit("a manifest change")

        rows = [
            {"number": 1, "title": "docs", "base": base, "head": docs_head},
            {"number": 2, "title": "manifest", "base": base, "head": manifest_head},
        ]
        results = BACKTEST.classify(str(repo.path), rows, repo_name="rizzstack")
        self.assertEqual([item["tier"] for item in results], ["T1", "T3"])
        self.assertEqual(results[0]["reasons"], [])
        self.assertIn("not_t1_allowlisted:uv.lock", results[1]["reasons"])
        self.assertEqual(BACKTEST.split(results), (1, 1))

        # The harness and the gate must agree row for row, or the reported split
        # is not the gate's.
        for row in rows:
            gate_tier, _reasons = GATE.risk_tier(
                str(repo.path), row["base"], row["head"], repo_name="rizzstack"
            )
            self.assertEqual(gate_tier, next(r["tier"] for r in results if r["number"] == row["number"]))

        # No repository name: the allowlist cannot apply, so both rows are T3.
        unknown = BACKTEST.classify(str(repo.path), rows, repo_name="not-a-known-repository")
        self.assertEqual(BACKTEST.split(unknown), (0, 2))
        self.assertIn("repo_unknown:not-a-known-repository", unknown[0]["reasons"])

    def test_main_prints_the_split(self):
        root = tempfile.mkdtemp(prefix="tier-backtest-main-")
        repo = Repo(root)
        repo.write("docs/readme.md", "# docs\n")
        base = repo.commit("base")
        repo.write("docs/guide.md", "# guide\n")
        head = repo.commit("docs")
        manifest = Path(root) / "rows.json"
        manifest.write_text(
            json.dumps([{"number": 7, "title": "docs", "base": base, "head": head}]),
            encoding="utf-8",
        )
        proc = subprocess.run(
            [
                "python3",
                str(SCRIPT),
                "--repo",
                str(repo.path),
                "--manifest",
                str(manifest),
                "--repo-name",
                "rizzstack",
            ],
            capture_output=True,
            text=True,
        )
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertIn("T1=1 T3=0", proc.stdout)


if __name__ == "__main__":
    unittest.main()
