"""Contract for docs/RELEASING.md (BEST-PRACTICE items 6 and 8).

- tags go out in dependency order: workflow, hyvv, bundlebees, orbitdesktop;
- only after the cross-stack gate passes on the bumped SHAs;
- each tag is its own typed yes from the owner;
- releases are immutable, so a release is created as a draft, assets are attached, then it is published.
"""
import re
import unittest
from pathlib import Path

DOC = Path(__file__).resolve().parents[1] / "docs" / "RELEASING.md"


class ReleasingDocTests(unittest.TestCase):
    def text(self):
        self.assertTrue(DOC.is_file(), "missing docs/RELEASING.md")
        return DOC.read_text(encoding="utf-8")

    def test_tag_order_is_dependency_order(self):
        self.assertIn("workflow → hyvv → bundlebees → orbitdesktop", self.text())

    def test_gate_and_typed_yes_per_tag(self):
        t = self.text().lower()
        self.assertIn("cross-stack gate", t)
        self.assertRegex(t, r"each tag is its own typed yes")

    def test_immutable_release_order(self):
        t = self.text().lower()
        self.assertIn("immutable", t)
        draft, attach, publish = (t.index(w) for w in ("draft", "attach", "publish"))
        self.assertLess(draft, attach)
        self.assertLess(attach, publish)

    def test_links_compat(self):
        self.assertRegex(self.text(), r"\]\(\.\./COMPAT\.md\)")


if __name__ == "__main__":
    unittest.main()
