"""Contract for COMPAT.md: the released tuple and the pins bundlebees ships with it.

- compat/tuple.json is the single source; COMPAT.md is generated from it and must match;
- every released repo carries a semver version, its tag and a full 40-hex commit SHA;
- the bundlebees pins block records WORKFLOW_PIN, HYVV_PIN and LAUNCHER_PIN as read from
  bundlebees_plugin/pins.py at the released bundlebees tag;
- skew between a pin and the released tuple is shown, never hidden.
"""
import importlib.util
import json
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("compat", ROOT / "scripts" / "compat.py")
compat = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(compat)

PINS_PY = '''"""doc"""
WORKFLOW_PIN = "%s"
HYVV_PIN = "%s"
LAUNCHER_PIN = "0.1.17"
''' % ("a" * 40, "b" * 40)


class CompatTests(unittest.TestCase):
    def tuple_(self):
        return json.loads((ROOT / "compat" / "tuple.json").read_text(encoding="utf-8"))

    def test_compat_md_is_generated_from_tuple_json(self):
        self.assertEqual((ROOT / "COMPAT.md").read_text(encoding="utf-8"), compat.render(self.tuple_()))

    def test_every_released_repo_has_version_tag_and_full_sha(self):
        released = self.tuple_()["released"]
        self.assertEqual([r["repo"] for r in released][:4], ["workflow", "hyvv", "bundlebees", "orbitdesktop"])
        for r in released:
            with self.subTest(repo=r["repo"]):
                self.assertRegex(r["version"], r"^\d+\.\d+\.\d+$")
                self.assertRegex(r["sha"], r"^[0-9a-f]{40}$")
                self.assertTrue(r["tag"])

    def test_pins_parse_from_bundlebees_pins_py(self):
        self.assertEqual(compat.parse_pins(PINS_PY),
                         {"WORKFLOW_PIN": "a" * 40, "HYVV_PIN": "b" * 40, "LAUNCHER_PIN": "0.1.17"})

    def test_parse_pins_fails_closed_on_a_missing_pin(self):
        with self.assertRaises(ValueError):
            compat.parse_pins('WORKFLOW_PIN = "%s"\n' % ("a" * 40))

    def test_check_pins_reports_any_difference(self):
        t = self.tuple_()
        recorded = t["bundlebees_pins"]["pins"]
        text = "".join(f'{k} = "{v}"\n' for k, v in recorded.items())
        self.assertEqual(compat.check_pins(t, text), [])
        drifted = text.replace(recorded["HYVV_PIN"], "c" * 40)
        self.assertEqual(compat.check_pins(t, drifted), ["HYVV_PIN"])

    def test_skew_is_shown_when_a_pin_is_not_the_released_sha(self):
        t = self.tuple_()
        t["bundlebees_pins"]["pins"]["WORKFLOW_PIN"] = "d" * 40
        out = compat.render(t)
        self.assertRegex(out, r"WORKFLOW_PIN.*`d{12}`.*not the released workflow")


if __name__ == "__main__":
    unittest.main()
