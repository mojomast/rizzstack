"""Contract for the public codemap/invariants.md (rizzstack decision 12).

The page stays public and states generic rules. Operator specifics (the
owner's name, vendors and capacity, held PR numbers, unannounced names) live
in a private operator file agents load from their own store. The two sections
scripts/codemap/slice.py injects into every bee brief must stay present.
"""
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PAGE = ROOT / "codemap" / "invariants.md"

OPERATOR_SPECIFICS = [
    r"Durepos", r"@mojomast", r"Night Hive", r"OpenCode", r"DeepSeek",
    r"bee cap", r"\bcap 12\b", r"Google Colab", r"poptart", r"stitchslop",
    r"Hermagotchi", r"\bEOH\b", r"#\d{2,}", r"launcher allow-list",
]
MANDATORY = ("Security posture", "Secrets and private details")


def sections(text):
    found, current = {}, None
    for line in text.splitlines():
        if line.startswith("## "):
            current = line[3:].strip()
            found[current] = []
        elif current:
            found[current].append(line)
    return found


class InvariantsPublicTests(unittest.TestCase):
    def test_no_operator_specifics(self):
        text = PAGE.read_text(encoding="utf-8")
        for pattern in OPERATOR_SPECIFICS:
            self.assertIsNone(re.search(pattern, text), "operator specific in public page: %s" % pattern)

    def test_points_to_the_private_operator_file(self):
        self.assertIn("operator-invariants.md", PAGE.read_text(encoding="utf-8"))

    def test_slicer_sections_stay_with_bullets(self):
        found = sections(PAGE.read_text(encoding="utf-8"))
        for name in MANDATORY:
            self.assertIn(name, found)
            self.assertTrue(any(line.startswith("- ") for line in found[name]), name)


if __name__ == "__main__":
    unittest.main()
