"""Tests for the gate's risk tier step (T1/T3).

Every case builds a real temporary git repository and, where the gate's own
output is under test, drives the real CLI.  The tier is read from the changed
paths only, so these tests pin one rule each: the path classes, the
``pyproject.toml`` before/after parse (including both inline-table shapes),
the scanner findings, and the fail-closed default.
"""

import hashlib
import importlib.util
import json
import os
import re
import subprocess
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / ".github" / "scripts" / "lease_tamper.py"
CLASS_LINE = re.compile(r"^lease-tamper: class=(slice|non-slice) TIER=(T1|T3)$")


def load_gate_module():
    spec = importlib.util.spec_from_file_location("lease_tamper_tier_under_test", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


GATE = load_gate_module()


class GitRepo:
    def __init__(self, path):
        self.path = Path(path)
        self.path.mkdir(parents=True, exist_ok=True)
        self.git("init", "-b", "main")
        self.git("config", "user.email", "tier@example.invalid")
        self.git("config", "user.name", "Risk Tier Tests")
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

    def remove(self, relative):
        self.git("rm", "-q", relative)

    def commit(self, message):
        self.git("add", "-A")
        self.git("commit", "-m", message)
        return self.rev()

    def rev(self, ref="HEAD"):
        return self.git("rev-parse", ref).stdout.strip()

    def checkout(self, *args):
        self.git("checkout", "-q", *args)


DEFAULT_BASE = {
    "pyproject.toml": '[project]\nname = "slice"\nversion = "1.0.0"\n',
    "src/slice.py": "base\n",
    "src/unleased.py": "base\n",
    "tests/test_frozen.py": "def test_ok():\n    assert True\n",
    "docs/readme.md": "# docs\n",
}


def build_change(root, base_files=None, changes=None, deletes=(), branch="feat/tier"):
    """A repo with one base commit and one head commit carrying the change."""
    repo = GitRepo(root)
    for path, text in (base_files or DEFAULT_BASE).items():
        repo.write(path, text)
    base = repo.commit("base")
    repo.checkout("-b", branch)
    for path, text in (changes or {}).items():
        repo.write(path, text)
    for path in deletes:
        repo.remove(path)
    head = repo.commit("change")
    return repo, base, head


class TierRules(unittest.TestCase):
    def tier(self, changes=None, deletes=(), base_files=None):
        root = tempfile.mkdtemp(prefix="tier-rules-")
        repo, base, head = build_change(root, base_files, changes, deletes)
        tier, reasons = GATE.risk_tier(str(repo.path), base, head)
        return tier, [reason for reason, _path in reasons]

    def assert_t1(self, **kwargs):
        tier, reasons = self.tier(**kwargs)
        self.assertEqual((tier, reasons), ("T1", []), "expected T1, got %s %s" % (tier, reasons))

    def assert_t3(self, reason, **kwargs):
        tier, reasons = self.tier(**kwargs)
        self.assertEqual(tier, "T3", "expected T3, got %s %s" % (tier, reasons))
        self.assertIn(reason, reasons)

    # ---- T1 controls -----------------------------------------------------

    def test_source_and_docs_change_is_t1(self):
        self.assert_t1(
            changes={"src/slice.py": "new\n", "docs/readme.md": "# new\n"}
        )

    def test_new_test_file_is_t1(self):
        self.assert_t1(changes={"tests/test_new.py": "def test_new():\n    assert 1\n"})

    def test_ordinary_pyproject_metadata_is_t1(self):
        self.assert_t1(
            changes={
                "pyproject.toml": '[project]\nname = "slice"\nversion = "1.0.1"\n'
                'description = "ordinary metadata"\n'
            }
        )

    def test_build_system_requires_is_not_schema_packaging(self):
        self.assert_t1(changes={"pyproject.toml": '[build-system]\nrequires = ["hatchling>=1"]\n'})

    def test_same_pytest_config_written_differently_is_t1(self):
        # The parsed table is identical, so a reformat is not a config change.
        base = dict(DEFAULT_BASE)
        base["pyproject.toml"] = '[project]\nname = "slice"\n\n[tool.pytest.ini_options]\naddopts = "-q"\n'
        self.assert_t1(
            base_files=base,
            changes={"pyproject.toml": 'tool = { pytest = { ini_options = { addopts = "-q" } } }\n'},
        )

    # ---- path classes ----------------------------------------------------

    def test_github_workflow_is_t3(self):
        self.assert_t3("github", changes={".github/workflows/ci.yml": "on: push\n"})

    def test_lane_script_is_t3(self):
        self.assert_t3("gate_lane_script", changes={"scripts/merge_lane_v5.py": "# lane\n"})

    def test_policy_file_is_t3(self):
        self.assert_t3("gate_lane_script", changes={"policy/merge-lane-v5.md": "# policy\n"})

    def test_lease_store_is_t3(self):
        self.assert_t3("lease_store", changes={".rizzstack/leases/new.json": "{}\n"})

    def test_contract_store_is_t3(self):
        self.assert_t3("lease_store", changes={".rizzstack/contracts/x.json": "{}\n"})

    def test_root_lease_file_is_t3(self):
        self.assert_t3("lease_store", changes={".rizzstack/lease.json": "{}\n"})

    def test_conftest_at_root_is_t3(self):
        self.assert_t3("conftest", changes={"conftest.py": "import sys\n"})

    def test_nested_conftest_is_t3(self):
        self.assert_t3("conftest", changes={"pkg/tests/conftest.py": "import sys\n"})

    def test_edited_existing_test_is_t3(self):
        self.assert_t3(
            "existing_test_edit", changes={"tests/test_frozen.py": "def test_ok():\n    assert 1\n"}
        )

    def test_deleted_test_is_t3(self):
        self.assert_t3("existing_test_edit", deletes=("tests/test_frozen.py",))

    def test_pytest_ini_is_t3(self):
        self.assert_t3("pytest_config", changes={"pytest.ini": "[pytest]\naddopts = -q\n"})

    def test_tox_ini_is_t3(self):
        self.assert_t3("pytest_config", changes={"tox.ini": "[pytest]\naddopts = -q\n"})

    def test_coveragerc_is_t3(self):
        self.assert_t3("coverage_config", changes={".coveragerc": "[run]\nbranch = true\n"})

    def test_deploy_service_is_t3(self):
        self.assert_t3(
            "deploy_config", changes={"contrib/systemd/slice.service": "[Unit]\nDescription=x\n"}
        )

    def test_dockerfile_is_t3(self):
        self.assert_t3("deploy_config", changes={"Dockerfile": "FROM scratch\n"})

    def test_deploy_directory_is_t3(self):
        self.assert_t3("deploy_config", changes={"deploy/prod.yml": "replicas: 1\n"})

    def test_contracts_directory_is_t3(self):
        # The frozen ACDD contracts a lane reads may live outside .rizzstack/.
        self.assert_t3("contracts", changes={"plans/wave-a/contracts/A1a.contract.json": "{}\n"})

    def test_contract_json_elsewhere_is_t3(self):
        self.assert_t3("contracts", changes={"docs/A1a.contract.json": "{}\n"})

    def test_docs_named_after_a_gate_are_t1(self):
        # A documentation or image file is not a gate script.
        self.assert_t1(
            changes={
                "docs/ci-gates.md": "# gates\n",
                "codemap/ci-gates.md": "# gates\n",
                "docs/images/art-verify-gate.jpg": "not-a-real-jpeg\n",
            }
        )

    def test_startup_hook_is_t3(self):
        self.assert_t3("startup_hook", changes={"sitecustomize.py": "import os\n"})

    def test_pth_file_is_t3(self):
        self.assert_t3("pth_file", changes={"slice.pth": "import os\n"})

    # ---- pyproject: parsed before/after ----------------------------------

    def test_pytest_table_header_is_t3(self):
        self.assert_t3(
            "pytest_config",
            changes={
                "pyproject.toml": '[project]\nname = "slice"\nversion = "1.0.0"\n\n'
                '[tool.pytest.ini_options]\naddopts = "-q"\n'
            },
        )

    def test_pytest_inline_table_inside_tool_is_t3(self):
        # Inline table case 1: an inline `pytest = {...}` under a [tool] header.
        self.assert_t3(
            "pytest_config",
            changes={
                "pyproject.toml": '[project]\nname = "slice"\nversion = "1.0.0"\n\n'
                '[tool]\npytest = { ini_options = { addopts = "-p no:cacheprovider" } }\n'
            },
        )

    def test_pytest_fully_inline_root_table_is_t3(self):
        # Inline table case 2: the whole document is one inline table, no header.
        self.assert_t3(
            "pytest_config",
            changes={
                "pyproject.toml": 'tool = { pytest = { ini_options = { addopts = "-p no:cacheprovider" } } }\n'
            },
        )

    def test_pytest_dotted_keys_are_t3(self):
        self.assert_t3(
            "pytest_config", changes={"pyproject.toml": 'tool.pytest.ini_options.addopts = "-q"\n'}
        )

    def test_removed_pytest_table_is_t3(self):
        base = dict(DEFAULT_BASE)
        base["pyproject.toml"] = '[project]\nname = "slice"\n\n[tool.pytest.ini_options]\naddopts = "-q"\n'
        self.assert_t3(
            "pytest_config",
            base_files=base,
            changes={"pyproject.toml": '[project]\nname = "slice"\n'},
        )

    def test_coverage_table_is_t3(self):
        self.assert_t3(
            "coverage_config",
            changes={
                "pyproject.toml": '[project]\nname = "slice"\n\n[tool.coverage.run]\nbranch = true\n'
            },
        )

    def test_entry_points_table_is_t3(self):
        self.assert_t3(
            "entry_points",
            changes={
                "pyproject.toml": '[project]\nname = "slice"\n\n'
                '[project.entry-points.pytest11]\nslice = "slice.plugin"\n'
            },
        )

    def test_project_scripts_is_t3(self):
        self.assert_t3(
            "entry_points",
            changes={
                "pyproject.toml": '[project]\nname = "slice"\n\n[project.scripts]\nslice = "slice:main"\n'
            },
        )

    def test_packaging_that_ships_schemas_is_t3(self):
        self.assert_t3(
            "packaging_schemas",
            changes={
                "pyproject.toml": '[project]\nname = "slice"\n\n'
                '[tool.hatch.build.targets.wheel.force-include]\n"schemas" = "slice/schemas"\n'
            },
        )

    def test_package_data_with_json_glob_is_t3(self):
        self.assert_t3(
            "packaging_schemas",
            changes={
                "pyproject.toml": '[project]\nname = "slice"\n\n'
                '[tool.setuptools.package-data]\nslice = ["schemas/*.json"]\n'
            },
        )

    def test_unparseable_pyproject_is_t3(self):
        self.assert_t3(
            "pyproject_unparseable", changes={"pyproject.toml": "this is not toml = [\n"}
        )

    def test_unparseable_base_pyproject_is_t3(self):
        base = dict(DEFAULT_BASE)
        base["pyproject.toml"] = "broken = [\n"
        self.assert_t3(
            "pyproject_unparseable",
            base_files=base,
            changes={"pyproject.toml": '[project]\nname = "fixed"\n'},
        )

    def test_setup_cfg_pytest_section_is_t3(self):
        self.assert_t3(
            "pytest_config",
            changes={"setup.cfg": "[tool:pytest]\naddopts = -q\n"},
        )

    def test_setup_cfg_pytest11_entry_point_is_t3(self):
        self.assert_t3(
            "entry_points",
            changes={"setup.cfg": "[options.entry_points]\npytest11 =\n    slice = slice.plugin\n"},
        )

    # ---- scanner findings ------------------------------------------------

    def test_pytest_plugins_in_new_test_file_is_t3(self):
        # A NEW test file is normally T1; a scanner finding makes it T3.
        self.assert_t3(
            "scanner:pytest_plugin",
            changes={"tests/test_new.py": 'pytest_plugins = ["slice.plugin"]\n'},
        )

    def test_addopts_plugin_in_new_conftest_is_t3(self):
        self.assert_t3(
            "scanner:pytest_plugin",
            changes={"conftest.py": 'addopts = "-p no:cacheprovider"\n'},
        )

    def test_forced_exit_in_new_module_is_t3(self):
        self.assert_t3("scanner:forced_exit", changes={"src/runner.py": "os._exit(0)\n"})

    def test_pytest_hook_definition_is_t3(self):
        self.assert_t3(
            "scanner:pytest_hook",
            changes={"src/hooks.py": "def pytest_collection_modifyitems(items):\n    return items\n"},
        )

    def test_patched_assertion_is_t3(self):
        self.assert_t3(
            "scanner:patched_assertion",
            changes={"src/plugin.py": "builtins.AssertionError = RuntimeError\n"},
        )

    # ---- fail closed -----------------------------------------------------

    def test_unreadable_history_is_t3(self):
        root = tempfile.mkdtemp(prefix="tier-broken-")
        repo, _base, head = build_change(root, changes={"src/slice.py": "new\n"})
        tier, reasons = GATE.risk_tier(str(repo.path), "0" * 40, head)
        self.assertEqual(tier, "T3")
        self.assertIn("tier_unreadable", [reason for reason, _path in reasons])

    def test_not_a_repository_is_t3(self):
        directory = tempfile.mkdtemp(prefix="tier-norepo-")
        tier, reasons = GATE.risk_tier(directory, "a" * 40, "b" * 40)
        self.assertEqual(tier, "T3")
        self.assertIn("tier_unreadable", [reason for reason, _path in reasons])


def contract_document(source_rev, assigned, test_files=()):
    return {
        "contract_version": "acdd/1",
        "id": "tier-slice",
        "source_rev": source_rev,
        "scope": {
            "assigned_files": list(assigned),
            "test_files": list(test_files),
            "prohibited": [],
            "read_only": [],
        },
        "review": {"status": "approved", "approved_by": "owner (tier fixture)"},
    }


def contract_hash(document):
    body = {key: value for key, value in document.items() if key != "review"}
    text = json.dumps(body, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def slice_repo(root, assigned, changes, test_files=()):
    """A base branch with an approved contract + frozen lease, then one change."""
    repo = GitRepo(root)
    for path, text in DEFAULT_BASE.items():
        repo.write(path, text)
    source_rev = repo.commit("base")
    document = contract_document(source_rev, assigned, test_files)
    sha = contract_hash(document)
    repo.write(
        ".rizzstack/contracts/%s.json" % sha,
        json.dumps(document, indent=2, sort_keys=True) + "\n",
    )
    repo.write(
        ".rizzstack/leases/tier.json",
        json.dumps(
            {
                "id": "tier-slice",
                "lease_version": "rizzstack.lease/1",
                "contract_sha256": sha,
                "source_rev": source_rev,
                "branch": "feat/tier",
                "scope": {key: list(value) for key, value in document["scope"].items()},
                "review": {"status": "frozen", "approved_by": "coordinator (tier fixture)"},
            },
            indent=2,
        )
        + "\n",
    )
    base = repo.commit("land the contract and lease")
    repo.checkout("-b", "feat/tier")
    for path, text in changes.items():
        repo.write(path, text)
    head = repo.commit("the change")
    return repo, base, head


class TierOutput(unittest.TestCase):
    """The gate's final line is ``class=... TIER=...``."""

    def run_gate(self, repo, base, head, *extra):
        env = {
            key: value
            for key, value in os.environ.items()
            if key not in ("GITHUB_REPOSITORY", "GITHUB_TOKEN", "GH_TOKEN", "GITHUB_EVENT_PATH")
        }
        return subprocess.run(
            [
                "python3",
                str(SCRIPT),
                "--repo",
                str(repo.path),
                "--base",
                base,
                "--head",
                head,
                "--branch",
                "feat/tier",
                *extra,
            ],
            capture_output=True,
            text=True,
            env=env,
        )

    def last_line(self, proc):
        lines = [line for line in (proc.stdout or "").splitlines() if line.strip()]
        self.assertTrue(lines, "the gate printed nothing")
        return lines[-1]

    def test_clean_slice_prints_tier_one_on_the_last_line(self):
        root = tempfile.mkdtemp(prefix="tier-out-")
        repo, base, head = slice_repo(root, ["src/slice.py"], {"src/slice.py": "new\n"})
        proc = self.run_gate(repo, base, head)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertEqual(self.last_line(proc), "lease-tamper: class=slice TIER=T1")
        self.assertNotIn("tier: T3", proc.stdout)

    def test_slice_touching_the_workflow_prints_tier_three(self):
        root = tempfile.mkdtemp(prefix="tier-out3-")
        repo, base, head = slice_repo(
            root,
            [".github/scripts/helper.sh"],
            {".github/scripts/helper.sh": "#!/bin/sh\n"},
        )
        proc = self.run_gate(repo, base, head)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertEqual(self.last_line(proc), "lease-tamper: class=slice TIER=T3")
        self.assertIn("lease-tamper: tier: T3 because github:.github/scripts/helper.sh", proc.stdout)

    def test_slice_touching_pyproject_pytest_table_prints_tier_three(self):
        root = tempfile.mkdtemp(prefix="tier-out4-")
        repo, base, head = slice_repo(
            root,
            ["pyproject.toml"],
            {
                "pyproject.toml": '[project]\nname = "slice"\nversion = "1.0.0"\n\n'
                '[tool.pytest.ini_options]\naddopts = "-q"\n'
            },
        )
        proc = self.run_gate(repo, base, head)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertEqual(self.last_line(proc), "lease-tamper: class=slice TIER=T3")

    def test_tamper_still_ends_with_the_class_line(self):
        root = tempfile.mkdtemp(prefix="tier-out5-")
        repo, base, head = slice_repo(root, ["src/slice.py"], {"src/unleased.py": "tamper\n"})
        proc = self.run_gate(repo, base, head)
        self.assertEqual(proc.returncode, 1)
        self.assertEqual(self.last_line(proc), "lease-tamper: class=slice TIER=T1")
        self.assertIn("outside the slice lease", proc.stdout)

    def test_non_slice_refusal_ends_with_the_class_line(self):
        root = tempfile.mkdtemp(prefix="tier-out6-")
        repo, base, head = build_change(
            root, changes={".github/workflows/ci.yml": "on: push\n"}
        )
        proc = self.run_gate(repo, base, head)
        self.assertEqual(proc.returncode, 1)
        self.assertEqual(self.last_line(proc), "lease-tamper: class=non-slice TIER=T3")
        self.assertIn("reason=non_slice_requires_independent_review", proc.stdout)

    def test_quiet_still_prints_the_class_line(self):
        root = tempfile.mkdtemp(prefix="tier-out7-")
        repo, base, head = slice_repo(root, ["src/slice.py"], {"src/slice.py": "new\n"})
        proc = self.run_gate(repo, base, head, "--quiet")
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertEqual(self.last_line(proc), "lease-tamper: class=slice TIER=T1")

    def test_every_class_line_matches_the_contract(self):
        root = tempfile.mkdtemp(prefix="tier-out8-")
        cases = [
            (slice_repo(root + "a", ["src/slice.py"], {"src/slice.py": "new\n"}), "slice", "T1"),
            (
                slice_repo(root + "b", [".github/x.yml"], {".github/x.yml": "on: push\n"}),
                "slice",
                "T3",
            ),
        ]
        for (repo, base, head), expected_class, expected_tier in cases:
            proc = self.run_gate(repo, base, head)
            match = CLASS_LINE.match(self.last_line(proc))
            self.assertIsNotNone(match, proc.stdout)
            self.assertEqual((match.group(1), match.group(2)), (expected_class, expected_tier))


if __name__ == "__main__":
    unittest.main()
