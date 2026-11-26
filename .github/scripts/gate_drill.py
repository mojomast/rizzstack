#!/usr/bin/env python3
"""One CI drill suite for every known lease/tamper and risk-tier bypass.

The gate's adversarial corpus used to live in two places -- ``lease_tamper_poc.py``
(five bypasses, the pre-fix shapes) and ``lease_tamper_poc_v2.sh`` (thirteen
cases against the real slice path) -- and CI ran both, so a new bypass could be
added to one and silently skipped by the other.  This suite is the single CI
entry point.  It:

* imports the v1 builders from ``lease_tamper_poc.py`` (same code, not a copy);
* ports the v2 bash cases one-for-one, keeping the bash case labels as ids, and
  ``tests/test_gate_drill.py`` fails if a label in the script has no case here;
* adds the risk-tier cases, including both ``pyproject.toml`` inline-table
  shapes (``[tool]`` with an inline ``pytest = {...}``, and a document that is
  one inline table with no header), the dotted-key shape, and the controls that
  must stay T1 (a new test file, ordinary metadata, a build-system bump);
* runs every case against the gate and verifies ``gate_drill.lock.json``.

**The lock is the rule "every new bypass becomes a case before the fix".**  Each
case declares its fixture inputs and its expectation (exit code, class, tier);
the lock pins a sha256 over that declaration.  A case added without a lock
entry, a case whose declaration changed without the lock being regenerated, or a
lock entry with no case fails the drill with ``lock_drift`` -- so a fix cannot
land a behaviour change the corpus does not describe, and a corpus case cannot
be weakened silently.  Regenerate deliberately with ``--update-lock``.

Usage::

    python3 .github/scripts/gate_drill.py [--script PATH] [--lock PATH]
                                         [--update-lock] [--only ID[,ID]]
                                         [--json] [--keep]

Exit status: 0 when every case matches its expectation and the lock verifies,
1 otherwise.  Standard library only; no network.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
DEFAULT_SCRIPT = HERE / "lease_tamper.py"
DEFAULT_LOCK = HERE / "gate_drill.lock.json"
LOCK_VERSION = 1

sys.path.insert(0, str(HERE))
import lease_tamper_poc as poc  # noqa: E402  (the v1 bypass builders, reused)


# ---------------------------------------------------------------------------
# fixture helpers
# ---------------------------------------------------------------------------


class Repo:
    """A throwaway git repository with committed fixtures."""

    def __init__(self, path):
        self.path = Path(path)
        self.path.mkdir(parents=True, exist_ok=True)
        self.git("init", "-b", "main")
        self.git("config", "user.email", "drill@example.invalid")
        self.git("config", "user.name", "Gate Drill")
        self.git("config", "commit.gpgsign", "false")

    def git(self, *args):
        proc = subprocess.run(
            ["git", "-C", str(self.path), *args], capture_output=True, text=True
        )
        if proc.returncode != 0:
            raise SystemExit(
                "git %s failed in %s: %s%s"
                % (" ".join(args), self.path, proc.stdout, proc.stderr)
            )
        return proc

    def write(self, relative, text):
        target = self.path / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")

    def append(self, relative, text):
        target = self.path / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("a", encoding="utf-8") as handle:
            handle.write(text)

    def commit(self, message):
        self.git("add", "-A")
        self.git("commit", "-m", message)
        return self.rev()

    def rev(self, ref="HEAD"):
        return self.git("rev-parse", ref).stdout.strip()

    def checkout(self, *args):
        self.git("checkout", "-q", *args)


def contract_document(source_rev, assigned, test_files=(), contract_id="drill-slice", prohibited=None):
    return {
        "contract_version": "acdd/1",
        "id": contract_id,
        "source_rev": source_rev,
        "scope": {
            "assigned_files": list(assigned),
            "test_files": list(test_files),
            "prohibited": [".github/**"] if prohibited is None else list(prohibited),
            "read_only": [],
        },
        "review": {"status": "approved", "approved_by": "owner (drill fixture)"},
    }


def contract_hash(document):
    body = {key: value for key, value in document.items() if key != "review"}
    text = json.dumps(body, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def land_contract_and_lease(
    repo,
    source_rev,
    branch,
    assigned,
    test_files=(),
    review=True,
    contract_id="drill-slice",
    prohibited=None,
):
    """Write an approved contract + matching lease; return the contract sha."""
    document = contract_document(source_rev, assigned, test_files, contract_id, prohibited)
    sha = contract_hash(document)
    repo.write(
        ".rizzstack/contracts/%s.json" % sha,
        json.dumps(document, indent=2, sort_keys=True) + "\n",
    )
    lease = {
        "id": contract_id,
        "lease_version": "rizzstack.lease/1",
        "contract_sha256": sha,
        "source_rev": source_rev,
        "branch": branch,
        "scope": {key: list(value) for key, value in document["scope"].items()},
    }
    if review:
        lease["review"] = {"status": "frozen", "approved_by": "coordinator (drill fixture)"}
    repo.write(".rizzstack/leases/%s.json" % contract_id, json.dumps(lease, indent=2) + "\n")
    return sha


TIER_BASE_FILES = {
    "pyproject.toml": '[project]\nname = "drill"\nversion = "1.0.0"\n',
    "src/slice.py": "base\n",
    "src/unleased.py": "base\n",
    "tests/test_frozen.py": "def test_ok():\n    assert True\n",
    "tests/test_edit.py": "def test_edit():\n    assert True\n",
    "docs/readme.md": "# docs\n",
}


def apply_change(repo, inputs):
    for path, text in (inputs.get("changes") or {}).items():
        repo.write(path, text)
    for path, text in (inputs.get("appends") or {}).items():
        repo.append(path, text)
    for path in inputs.get("deletes") or ():
        repo.git("rm", "-q", path)


def tier_fixture(root, inputs):
    """A passing slice whose change is the only thing between base and head."""
    repo = Repo(root)
    for path, text in TIER_BASE_FILES.items():
        repo.write(path, text)
    source_rev = repo.commit("base")
    land_contract_and_lease(
        repo,
        source_rev,
        "feat/tier",
        inputs["assigned"],
        test_files=inputs.get("test_files") or (),
        contract_id="tier-slice",
        prohibited=(),
    )
    base = repo.commit("land the approved contract and its lease")
    repo.checkout("-b", "feat/tier")
    apply_change(repo, inputs)
    head = repo.commit("the change")
    return {"path": repo.path, "base": base, "head": head, "args": {"branch": "feat/tier"}}


# ---------------------------------------------------------------------------
# v2 fixture: the thirteen real-slice-path cases from lease_tamper_poc_v2.sh
# ---------------------------------------------------------------------------

V2_CONTRACTS = (
    ("slice-a", ["src/app.py"], "feat/slice-a", True),
    ("slice-glob", ["src/*"], "feat/slice-glob", True),
    ("slice-noreview", ["src/app.py"], "feat/slice-noreview", False),
    ("slice-wide", ["src/app.py"], "feat/slice-wide", True),
    ("slice-mut", ["src/app.py"], "feat/slice-mut", True),
)


def v2_repo(root):
    repo = Repo(root)
    repo.write(".github/scripts/lease_tamper.py", "# placeholder gate file\n")
    repo.write("src/app.py", "x=1\n")
    repo.write("tests/test_frozen.py", "def test(): pass\n")
    source_rev = repo.commit("base")
    shas = {}
    for name, assigned, branch, review in V2_CONTRACTS:
        shas[name] = land_contract_and_lease(
            repo,
            source_rev,
            branch,
            assigned,
            test_files=["tests/test_frozen.py"],
            review=review,
            contract_id=name,
        )
    # slice-wide: the LEASE is wider than its approved contract.
    lease_path = repo.path / ".rizzstack/leases/slice-wide.json"
    lease = json.loads(lease_path.read_text())
    lease["scope"]["assigned_files"] = ["src/app.py", "tests/test_frozen.py"]
    lease_path.write_text(json.dumps(lease, indent=2) + "\n")
    # slice-mut: the CONTRACT text changes after approval (hash mismatch).
    contract_path = repo.path / (".rizzstack/contracts/%s.json" % shas["slice-mut"])
    document = json.loads(contract_path.read_text())
    document["scope"]["assigned_files"].append("README.md")
    contract_path.write_text(json.dumps(document, indent=2) + "\n")
    lease_path = repo.path / ".rizzstack/leases/slice-mut.json"
    lease = json.loads(lease_path.read_text())
    lease["scope"]["assigned_files"].append("README.md")
    lease_path.write_text(json.dumps(lease, indent=2) + "\n")
    # A base lease for the gate itself, without a contract hash: a body line or a
    # branch leaf may select it, and it must still be unusable.
    repo.write(
        ".rizzstack/leases/gate.json",
        json.dumps(
            {
                "id": "gate",
                "source_rev": source_rev,
                "review": {"status": "approved", "approved_by": "owner (drill fixture)"},
                "scope": {"assigned_files": [".github/scripts/lease_tamper.py"]},
            },
            indent=2,
        )
        + "\n",
    )
    base = repo.commit("approved contracts + leases on base")
    return repo, base, source_rev


def v2_fixture(root, inputs):
    repo, base, _source_rev = v2_repo(root)
    repo.checkout("-b", inputs["branch"])
    apply_change(repo, inputs)
    head = repo.commit("the change")
    return {
        "path": repo.path,
        "base": base,
        "head": head,
        "args": {"branch": inputs["branch"], **(inputs.get("args") or {})},
    }


# ---------------------------------------------------------------------------
# the case registry
# ---------------------------------------------------------------------------


class Case:
    """One corpus case: a declared fixture, an expectation, and a lock digest.

    ``kind`` selects the fixture builder: ``v1`` (the imported PoC builders,
    selected by ``inputs["index"]``), ``v2`` (the ported bash fixture) or
    ``tier`` (the risk-tier slice fixture).  ``inputs`` is the whole declaration
    of the fixture -- the lock hashes it, so a weaker fixture is a lock drift,
    not a silent edit.
    """

    def __init__(self, cid, what, kind, inputs, expect_exit, expect_class=None, expect_tier=None):
        self.cid = cid
        self.what = what
        self.kind = kind
        self.inputs = inputs
        self.expect_exit = expect_exit
        self.expect_class = expect_class
        self.expect_tier = expect_tier

    def declaration(self):
        return {
            "id": self.cid,
            "what": self.what,
            "kind": self.kind,
            "inputs": self.inputs,
            "expect_exit": self.expect_exit,
            "expect_class": self.expect_class,
            "expect_tier": self.expect_tier,
        }

    def digest(self):
        text = json.dumps(self.declaration(), sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(text.encode("utf-8")).hexdigest()

    def lock_entry(self):
        return {
            "what": self.what,
            "kind": self.kind,
            "expect_exit": self.expect_exit,
            "expect_class": self.expect_class,
            "expect_tier": self.expect_tier,
            "digest": self.digest(),
        }


def v1(index):
    return {"index": index}


def v2(branch, changes=None, appends=None, deletes=None, args=None):
    return {
        "branch": branch,
        "changes": changes or {},
        "appends": appends or {},
        "deletes": deletes or [],
        "args": args or {},
    }


def tier(assigned, changes=None, test_files=None, appends=None, deletes=None):
    return {
        "assigned": list(assigned),
        "changes": changes or {},
        "appends": appends or {},
        "test_files": test_files or [],
        "deletes": deletes or [],
    }


FROZEN_EDIT = {"tests/test_frozen.py": "def test(): assert True\n"}

CASES = (
    # v1: the five pre-fix bypasses (imported builders; every one must fail closed).
    Case("v1-control", "a legitimate slice changes only its leased file (control)", "v1", v1(-1), 0, "slice", "T1"),
    Case("v1-0", poc.CASES[0], "v1", v1(0), 1, "slice", "T1"),
    Case("v1-1", poc.CASES[1], "v1", v1(1), 1, "slice", "T1"),
    Case("v1-2", poc.CASES[2], "v1", v1(2), 1, "non-slice", "T3"),
    Case("v1-3", poc.CASES[3], "v1", v1(3), 1, "non-slice", "T1"),
    Case("v1-4", poc.CASES[4], "v1", v1(4), 1, "slice", "T1"),
    # v2: the thirteen real-slice-path cases (ports of lease_tamper_poc_v2.sh).
    Case(
        "v2-P",
        "positive control: clean in-lease change",
        "v2",
        v2("feat/slice-a", {"src/app.py": "x=2\n"}, args={"pull_request": 1}),
        0,
        "slice",
        "T1",
    ),
    Case(
        "v2-0",
        "frozen test edited (must fail on scope, not non-slice)",
        "v2",
        v2("feat/slice-a", FROZEN_EDIT, args={"pull_request": 1}),
        1,
        "slice",
        "T3",
    ),
    Case(
        "v2-1",
        "frozen test edited + body 'Lease-Verify: <head>'",
        "v2",
        v2("feat/slice-a", FROZEN_EDIT, args={"pull_request": 1, "body": "Lease-Verify: {head}"}),
        1,
        "slice",
        "T3",
    ),
    Case(
        "v2-1b",
        "frozen test edited + label 'lease-verify:<head>'",
        "v2",
        v2("feat/slice-a", FROZEN_EDIT, args={"pull_request": 1, "labels": "lease-verify:{head}"}),
        1,
        "slice",
        "T3",
    ),
    Case(
        "v2-2",
        "PR adds its own .rizzstack/lease.json and edits the gate",
        "v2",
        v2(
            "chore/anything",
            {
                ".rizzstack/lease.json": '{"id": "self", "scope": {"assigned_files": '
                '["src/app.py", ".github/scripts/lease_tamper.py"]}}\n'
            },
            appends={".github/scripts/lease_tamper.py": "# gutted\n"},
            args={"pull_request": 2},
        ),
        1,
        "non-slice",
        "T3",
    ),
    Case(
        "v2-3",
        "slice PR sneaks a new lease file",
        "v2",
        v2(
            "feat/slice-a",
            {
                "src/app.py": "x=3\n",
                ".rizzstack/leases/grant.json": '{"id": "grant", "branch": "evil/x", '
                '"scope": {"assigned_files": ["src/app.py"]}}\n',
            },
            args={"pull_request": 3},
        ),
        1,
        "non-slice",
        "T3",
    ),
    Case(
        "v2-4",
        "body 'Lease-Contract:' picks the gate lease",
        "v2",
        v2(
            "fix/whatever",
            appends={".github/scripts/lease_tamper.py": "# weakened\n"},
            args={"pull_request": 4, "body": "Lease-Contract: .rizzstack/leases/gate.json"},
        ),
        1,
        "non-slice",
        "T3",
    ),
    Case(
        "v2-4b",
        "label 'lease-contract:' picks the gate lease",
        "v2",
        v2(
            "fix/whatever",
            appends={".github/scripts/lease_tamper.py": "# weakened\n"},
            args={"pull_request": 4, "labels": "lease-contract:.rizzstack/leases/gate.json"},
        ),
        1,
        "non-slice",
        "T3",
    ),
    Case(
        "v2-4c",
        "branch leaf named 'gate' matches the gate lease id",
        "v2",
        v2(
            "evil/gate",
            appends={".github/scripts/lease_tamper.py": "# weakened\n"},
            args={"pull_request": 4},
        ),
        1,
        None,
        "T3",
    ),
    Case(
        "v2-5",
        "approved contract with glob scope 'src/*'",
        "v2",
        v2("feat/slice-glob", {"src/app.py": "x=4\n"}, args={"pull_request": 5}),
        1,
        None,
        "T1",
    ),
    Case(
        "v2-6",
        "lease with no review block must not count as frozen",
        "v2",
        v2("feat/slice-noreview", {"src/app.py": "x=5\n"}, args={"pull_request": 6}),
        1,
        "non-slice",
        "T1",
    ),
    Case(
        "v2-7",
        "lease wider than its approved contract",
        "v2",
        v2("feat/slice-wide", FROZEN_EDIT, args={"pull_request": 7}),
        1,
        None,
        "T3",
    ),
    Case(
        "v2-8",
        "contract text changed after approval (hash mismatch)",
        "v2",
        v2("feat/slice-mut", {"README.md": "readme\n"}, args={"pull_request": 8}),
        1,
        None,
        "T1",
    ),
    # tier: the risk-tier controls and every T3 class (both inline-table shapes).
    Case("tier-src", "risk tier: a leased source change is T1", "tier", tier(["src/slice.py"], {"src/slice.py": "new\n"}), 0, "slice", "T1"),
    Case(
        "tier-github",
        "risk tier: a workflow change is T3",
        "tier",
        tier([".github/scripts/helper.sh"], {".github/scripts/helper.sh": "#!/bin/sh\n"}),
        0,
        "slice",
        "T3",
    ),
    Case(
        "tier-new-test",
        "risk tier: a NEW test file is T1",
        "tier",
        tier(["tests/test_new.py"], {"tests/test_new.py": "def test_new():\n    assert 1\n"}),
        0,
        "slice",
        "T1",
    ),
    Case(
        "tier-existing-test",
        "risk tier: an edit to an EXISTING test file is T3",
        "tier",
        tier(["tests/test_edit.py"], {"tests/test_edit.py": "def test_edit():\n    assert 1\n"}),
        0,
        "slice",
        "T3",
    ),
    Case(
        "tier-conftest",
        "risk tier: any conftest.py is T3",
        "tier",
        tier(["conftest.py"], {"conftest.py": "import sys\n"}),
        0,
        "slice",
        "T3",
    ),
    Case(
        "tier-inline-table-tool",
        "risk tier: an inline pytest table under [tool] is T3",
        "tier",
        tier(
            ["pyproject.toml"],
            {
                "pyproject.toml": '[project]\nname = "drill"\nversion = "1.0.0"\n\n'
                '[tool]\npytest = { ini_options = { addopts = "-p no:cacheprovider" } }\n'
            },
        ),
        0,
        "slice",
        "T3",
    ),
    Case(
        "tier-inline-table-root",
        "risk tier: a fully inline root table hiding pytest config is T3",
        "tier",
        tier(
            ["pyproject.toml"],
            {
                "pyproject.toml": 'tool = { pytest = { ini_options = { addopts = "-p no:cacheprovider" } } }\n'
            },
        ),
        0,
        "slice",
        "T3",
    ),
    Case(
        "tier-dotted-keys",
        "risk tier: dotted keys hiding pytest config are T3",
        "tier",
        tier(["pyproject.toml"], {"pyproject.toml": 'tool.pytest.ini_options.addopts = "-q"\n'}),
        0,
        "slice",
        "T3",
    ),
    Case(
        "tier-entry-points",
        "risk tier: a pytest11 entry point is T3",
        "tier",
        tier(
            ["pyproject.toml"],
            {
                "pyproject.toml": '[project]\nname = "drill"\n\n'
                '[project.entry-points.pytest11]\ndrill = "drill.plugin"\n'
            },
        ),
        0,
        "slice",
        "T3",
    ),
    Case(
        "tier-coverage",
        "risk tier: a [tool.coverage] table change is T3",
        "tier",
        tier(["pyproject.toml"], {"pyproject.toml": '[project]\nname = "drill"\n\n[tool.coverage.run]\nbranch = true\n'}),
        0,
        "slice",
        "T3",
    ),
    Case(
        "tier-packaging-schemas",
        "risk tier: packaging that ships runtime schemas is T3",
        "tier",
        tier(
            ["pyproject.toml"],
            {
                "pyproject.toml": '[project]\nname = "drill"\n\n'
                '[tool.hatch.build.targets.wheel.force-include]\n"schemas" = "drill/schemas"\n'
            },
        ),
        0,
        "slice",
        "T3",
    ),
    Case(
        "tier-build-system",
        "risk tier: a build-system bump that ships no schema is T1",
        "tier",
        tier(["pyproject.toml"], {"pyproject.toml": '[build-system]\nrequires = ["hatchling>=1"]\n'}),
        0,
        "slice",
        "T1",
    ),
    Case(
        "tier-invalid-toml",
        "risk tier: an unparseable pyproject is T3 (fail closed)",
        "tier",
        tier(["pyproject.toml"], {"pyproject.toml": "this is not toml = [\n"}),
        0,
        "slice",
        "T3",
    ),
    Case(
        "tier-deploy",
        "risk tier: a deploy unit is T3",
        "tier",
        tier(["contrib/systemd/drill.service"], {"contrib/systemd/drill.service": "[Unit]\nDescription=x\n"}),
        0,
        "slice",
        "T3",
    ),
    Case(
        "tier-scanner",
        "risk tier: a scanner finding in a NEW test file is T3",
        "tier",
        tier(["tests/test_plug.py"], {"tests/test_plug.py": 'pytest_plugins = ["drill.plugin"]\n'}),
        0,
        "slice",
        "T3",
    ),
    Case(
        "tier-lease-store",
        "risk tier: a lease-store change is T3 and non-slice",
        "v2",
        v2("feat/slice-a", {".rizzstack/leases/extra.json": "{}\n"}, args={"pull_request": 9}),
        1,
        "non-slice",
        "T3",
    ),
    Case(
        "tier-non-slice-workflow",
        "risk tier: an unleased workflow change is non-slice T3",
        "v2",
        v2("chore/ci", {".github/workflows/ci.yml": "on: push\n"}),
        1,
        "non-slice",
        "T3",
    ),
)


# ---------------------------------------------------------------------------
# running
# ---------------------------------------------------------------------------


def build_fixture(case, root):
    if case.kind == "v1":
        index = case.inputs["index"]
        built = poc.case_control(root) if index < 0 else poc.BUILDERS[index](root)
        extra = built[3] if len(built) > 3 else {}
        return {
            "path": built[0].path,
            "base": built[1],
            "head": built[2],
            "args": {"branch": "feat/poc", **extra},
        }
    if case.kind == "v2":
        return v2_fixture(root, case.inputs)
    if case.kind == "tier":
        return tier_fixture(root, case.inputs)
    raise SystemExit("unknown case kind %r" % case.kind)


def build_command(script, fixture):
    args = dict(fixture.get("args") or {})
    head = fixture["head"]
    command = [
        sys.executable,
        str(script),
        "--repo",
        str(fixture["path"]),
        "--base",
        fixture["base"],
        "--head",
        head,
        "--branch",
        str(args.pop("branch", "feat/drill")).replace("{head}", head),
    ]
    if "pull_request" in args:
        command += ["--pull-request", str(args.pop("pull_request"))]
    if "labels" in args:
        command += ["--labels", str(args.pop("labels")).replace("{head}", head)]
    if "body" in args:
        command += ["--body", str(args.pop("body")).replace("{head}", head)]
    if args:
        raise SystemExit("unused case args: %s" % sorted(args))
    return command


def class_and_tier(output):
    """The last class line, or the gate's tier-only line, and its tier."""
    for line in reversed((output or "").splitlines()):
        stripped = line.strip()
        if stripped.startswith("lease-tamper: class="):
            chunks = stripped[len("lease-tamper: class="):].split()
            pull_class = chunks[0] if chunks else None
            tier = next(
                (chunk[len("TIER="):] for chunk in chunks[1:] if chunk.startswith("TIER=")), None
            )
            return pull_class, tier
        if stripped.startswith("lease-tamper: gate: TIER="):
            return None, stripped[len("lease-tamper: gate: TIER="):].strip()
    return None, None


def run_case(script, case, root):
    fixture = build_fixture(case, root)
    command = build_command(script, fixture)
    env = {
        key: value
        for key, value in os.environ.items()
        if key not in ("GITHUB_REPOSITORY", "GITHUB_TOKEN", "GH_TOKEN", "GITHUB_EVENT_PATH")
    }
    proc = subprocess.run(command, capture_output=True, text=True, env=env)
    output = (proc.stdout or "") + (proc.stderr or "")
    pull_class, tier = class_and_tier(proc.stdout)
    problems = []
    if proc.returncode != case.expect_exit:
        problems.append("exit %d, expected %d" % (proc.returncode, case.expect_exit))
    if case.expect_class and pull_class != case.expect_class:
        problems.append("class %r, expected %r" % (pull_class, case.expect_class))
    if case.expect_tier and tier != case.expect_tier:
        problems.append("tier %r, expected %r" % (tier, case.expect_tier))
    return {
        "case": case.cid,
        "what": case.what,
        "exit": proc.returncode,
        "expected": case.expect_exit,
        "class": pull_class,
        "tier": tier,
        "ok": not problems,
        "problems": problems,
        "tail": output.strip().splitlines()[-4:],
    }


# ---------------------------------------------------------------------------
# the lock
# ---------------------------------------------------------------------------


def expected_lock():
    return {
        "version": LOCK_VERSION,
        "cases": {case.cid: case.lock_entry() for case in CASES},
    }


def read_lock(path):
    try:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return None, "cannot read the lock %r: %s" % (str(path), exc)
    if not isinstance(payload, dict) or payload.get("version") != LOCK_VERSION:
        return None, "the lock is not a version-%d document" % LOCK_VERSION
    cases = payload.get("cases")
    if not isinstance(cases, dict):
        return None, "the lock has no case map"
    return cases, None


def verify_lock(path):
    """``(problems, stale_ids)``: does the lock describe exactly this corpus?"""
    cases, error = read_lock(path)
    if error:
        return [error], []
    expected = {case.cid: case.lock_entry() for case in CASES}
    problems = []
    for cid in sorted(set(expected) - set(cases)):
        problems.append("case %s has no lock entry (add it with --update-lock)" % cid)
    for cid in sorted(set(cases) - set(expected)):
        problems.append("lock entry %s has no case (remove it with --update-lock)" % cid)
    stale = []
    for cid in sorted(set(expected) & set(cases)):
        want, have = expected[cid], cases[cid]
        if not isinstance(have, dict):
            problems.append("lock entry %s is not an object" % cid)
            continue
        if want["digest"] != have.get("digest"):
            stale.append(cid)
            problems.append(
                "case %s changed since the lock was written (declaration sha256 %s != %s); a "
                "corpus change must be reviewed and re-locked with --update-lock"
                % (cid, want["digest"][:12], str(have.get("digest"))[:12])
            )
        for key in ("expect_exit", "expect_class", "expect_tier"):
            if want[key] != have.get(key):
                problems.append(
                    "case %s expectation drift: %s is %r, the lock says %r"
                    % (cid, key, want[key], have.get(key))
                )
    return problems, stale


def write_lock(path):
    path = Path(path)
    path.write_text(json.dumps(expected_lock(), indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
# entry point
# ---------------------------------------------------------------------------


def main(argv=None):
    parser = argparse.ArgumentParser(description="Run the lease/tamper and tier drill corpus.")
    parser.add_argument("--script", default=str(DEFAULT_SCRIPT), help="lease_tamper.py under test")
    parser.add_argument("--lock", default=str(DEFAULT_LOCK), help="case lock manifest")
    parser.add_argument("--update-lock", action="store_true", help="rewrite the lock, then exit")
    parser.add_argument("--only", default=None, help="comma-separated case ids")
    parser.add_argument("--json", action="store_true", help="machine-readable output")
    parser.add_argument("--keep", action="store_true", help="keep the temporary repositories")
    args = parser.parse_args(argv)

    script = Path(args.script).resolve()
    if not script.is_file():
        print("gate drill: no gate script at %s" % script)
        return 1

    if args.update_lock:
        path = write_lock(args.lock)
        print("gate drill: wrote %d case(s) to %s" % (len(CASES), path))
        return 0

    lock_problems, _stale = verify_lock(args.lock)

    selected = [case.cid for case in CASES]
    if args.only:
        wanted = [item.strip() for item in args.only.split(",") if item.strip()]
        unknown = [item for item in wanted if item not in selected]
        if unknown:
            print("gate drill: unknown case id(s): %s" % ", ".join(unknown))
            return 1
        selected = wanted

    tmp = Path(tempfile.mkdtemp(prefix="gate-drill-"))
    results = []
    try:
        for case in CASES:
            if case.cid not in selected:
                continue
            results.append(run_case(script, case, tmp / case.cid))
    finally:
        if not args.keep:
            shutil.rmtree(tmp, ignore_errors=True)

    failures = [item for item in results if not item["ok"]]
    summary = {
        "cases": len(results),
        "unexpected": len(failures),
        "lock_problems": len(lock_problems),
        "ok": not failures and not lock_problems,
    }
    if args.json:
        print(
            json.dumps(
                {
                    "script": str(script),
                    "lock": str(Path(args.lock).resolve()),
                    "results": results,
                    "lock_problems": lock_problems,
                    "summary": summary,
                },
                indent=2,
            )
        )
        return 0 if summary["ok"] else 1

    print("script: %s" % script)
    print("lock:   %s" % Path(args.lock).resolve())
    for item in results:
        verdict = "OK" if item["ok"] else "MISMATCH"
        print(
            "%-26s exit=%d expected=%d class=%-9s tier=%-2s %-8s %s"
            % (
                item["case"],
                item["exit"],
                item["expected"],
                item["class"] or "-",
                item["tier"] or "-",
                verdict,
                item["what"],
            )
        )
        if item["problems"]:
            print("    %s" % "; ".join(item["problems"]))
        elif item["tail"]:
            print("    %s" % item["tail"][-1])
    for problem in lock_problems:
        print("lock_drift: %s" % problem)
    print(
        "%d case(s), %d unexpected, %d lock problem(s)"
        % (len(results), len(failures), len(lock_problems))
    )
    return 0 if summary["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
