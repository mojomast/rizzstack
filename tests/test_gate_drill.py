"""Contract for the CI drill corpus (.github/scripts/gate_drill.py).

Three things are pinned here:

1. the corpus covers every bypass the two PoC scripts describe -- a case label
   in ``lease_tamper_poc_v2.sh`` or an entry in ``lease_tamper_poc.CASES`` with
   no drill case fails this suite;
2. the lock manifest describes exactly the current corpus (no missing case, no
   orphan entry, no changed declaration);
3. the drift detector works: a weakened case or a dropped entry is refused.

The drill itself is exercised end to end on a representative slice of cases, so
this file also proves the suite runs against the real gate.
"""

import importlib.util
import json
import re
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DRILL = ROOT / ".github" / "scripts" / "gate_drill.py"
LOCK = ROOT / ".github" / "scripts" / "gate_drill.lock.json"
V2_SCRIPT = ROOT / ".github" / "scripts" / "lease_tamper_poc_v2.sh"


def load_drill():
    spec = importlib.util.spec_from_file_location("gate_drill_under_test", DRILL)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


DRILL_MODULE = load_drill()


def v2_labels():
    """The case labels the bash reproduction declares (``check "<label>"``)."""
    text = V2_SCRIPT.read_text(encoding="utf-8")
    labels = []
    for match in re.finditer(r'^\s*check\s+"([^"]+)"', text, re.MULTILINE):
        label = match.group(1)
        prefix = label.split(".", 1)[0].strip()
        if prefix:
            labels.append(prefix)
    return labels


class CorpusCoverageTests(unittest.TestCase):
    def test_every_v2_bash_label_has_a_drill_case(self):
        case_ids = {case.cid for case in DRILL_MODULE.CASES}
        labels = v2_labels()
        self.assertGreaterEqual(len(labels), 13, "the v2 script no longer declares 13 cases")
        missing = ["v2-%s" % label for label in labels if "v2-%s" % label not in case_ids]
        self.assertEqual(missing, [], "v2 labels without a drill case: %s" % missing)

    def test_every_v1_poc_case_has_a_drill_case(self):
        case_ids = {case.cid for case in DRILL_MODULE.CASES}
        for index in range(len(DRILL_MODULE.poc.CASES)):
            self.assertIn("v1-%d" % index, case_ids)
        self.assertIn("v1-control", case_ids)

    def test_both_inline_table_shapes_are_cases(self):
        case_ids = {case.cid for case in DRILL_MODULE.CASES}
        self.assertIn("tier-inline-table-tool", case_ids)
        self.assertIn("tier-inline-table-root", case_ids)

    def test_case_ids_and_declarations_are_unique(self):
        ids = [case.cid for case in DRILL_MODULE.CASES]
        self.assertEqual(len(ids), len(set(ids)), "duplicate case id")
        digests = [case.digest() for case in DRILL_MODULE.CASES]
        self.assertEqual(len(digests), len(set(digests)), "two cases declare the same fixture")

    def test_every_case_declares_a_fixture(self):
        for case in DRILL_MODULE.CASES:
            self.assertIn(case.kind, ("v1", "v2", "tier"), case.cid)
            self.assertIsInstance(case.inputs, dict, case.cid)
            self.assertIn(case.expect_exit, (0, 1), case.cid)


class LockTests(unittest.TestCase):
    def test_the_shipped_lock_verifies(self):
        problems, stale = DRILL_MODULE.verify_lock(LOCK)
        self.assertEqual(problems, [])
        self.assertEqual(stale, [])

    def test_the_lock_covers_every_case(self):
        payload = json.loads(LOCK.read_text(encoding="utf-8"))
        self.assertEqual(payload["version"], DRILL_MODULE.LOCK_VERSION)
        self.assertEqual(
            sorted(payload["cases"]), sorted(case.cid for case in DRILL_MODULE.CASES)
        )

    def test_a_changed_case_is_drift(self):
        payload = json.loads(LOCK.read_text(encoding="utf-8"))
        payload["cases"]["v2-0"]["digest"] = "0" * 64
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as handle:
            json.dump(payload, handle)
            path = handle.name
        problems, stale = DRILL_MODULE.verify_lock(path)
        self.assertEqual(stale, ["v2-0"])
        self.assertTrue(any("v2-0 changed since the lock" in item for item in problems), problems)

    def test_a_weakened_expectation_is_drift(self):
        payload = json.loads(LOCK.read_text(encoding="utf-8"))
        payload["cases"]["tier-github"]["expect_tier"] = "T1"
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as handle:
            json.dump(payload, handle)
            path = handle.name
        problems, _stale = DRILL_MODULE.verify_lock(path)
        self.assertTrue(any("tier-github expectation drift" in item for item in problems), problems)

    def test_a_missing_or_orphan_entry_is_drift(self):
        payload = json.loads(LOCK.read_text(encoding="utf-8"))
        del payload["cases"]["v1-0"]
        payload["cases"]["invented-case"] = {"digest": "0" * 64}
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as handle:
            json.dump(payload, handle)
            path = handle.name
        problems, _stale = DRILL_MODULE.verify_lock(path)
        self.assertTrue(any("v1-0 has no lock entry" in item for item in problems), problems)
        self.assertTrue(any("invented-case has no case" in item for item in problems), problems)

    def test_an_unreadable_lock_fails_closed(self):
        problems, _stale = DRILL_MODULE.verify_lock("/nonexistent/gate_drill.lock.json")
        self.assertTrue(problems)
        self.assertIn("cannot read the lock", problems[0])


class DrillRunTests(unittest.TestCase):
    """Run a representative slice of the corpus against the real gate."""

    def run_drill(self, *case_ids):
        proc = subprocess.run(
            [
                "python3",
                str(DRILL),
                "--script",
                str(ROOT / ".github" / "scripts" / "lease_tamper.py"),
                "--lock",
                str(LOCK),
                "--only",
                ",".join(case_ids),
                "--json",
            ],
            capture_output=True,
            text=True,
            cwd=str(ROOT),
        )
        payload = json.loads(proc.stdout[proc.stdout.index("{") :]) if "{" in proc.stdout else {}
        return proc, payload

    def test_representative_cases_match_their_expectations(self):
        wanted = [
            "v1-control",
            "v2-P",
            "v2-0",
            "v2-2",
            "tier-src",
            "tier-inline-table-tool",
            "tier-inline-table-root",
            "tier-dotted-keys",
            "tier-new-test",
            "tier-existing-test",
        ]
        proc, payload = self.run_drill(*wanted)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertEqual(payload.get("lock_problems"), [])
        results = {item["case"]: item for item in payload.get("results", [])}
        self.assertEqual(sorted(results), sorted(wanted))
        for case_id, item in results.items():
            self.assertTrue(item["ok"], "%s: %s" % (case_id, item.get("problems")))

    def test_an_unknown_case_id_is_refused(self):
        proc, _payload = self.run_drill("not-a-case")
        self.assertEqual(proc.returncode, 1)
        self.assertIn("unknown case id", proc.stdout)

    def test_update_lock_writes_the_expected_document(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "lock.json"
            proc = subprocess.run(
                ["python3", str(DRILL), "--update-lock", "--lock", str(target)],
                capture_output=True,
                text=True,
                cwd=str(ROOT),
            )
            self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
            self.assertEqual(
                json.loads(target.read_text(encoding="utf-8")),
                DRILL_MODULE.expected_lock(),
            )


if __name__ == "__main__":
    unittest.main()
