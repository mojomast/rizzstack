"""The outside-review role: skill, verdict schema, brief template and policy stay consistent.

Run: python3 -m unittest tests.test_outside_review_skill -v
"""

from __future__ import annotations

import copy
import importlib.util
import json
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SKILL_DIR = ROOT / "skills" / "rizzstack-outside-review"
SKILL = SKILL_DIR / "SKILL.md"
SCHEMA = SKILL_DIR / "verdict.schema.json"
TEMPLATE = SKILL_DIR / "templates" / "review.brief.template.json"
POLICY = ROOT / "policy" / "outside-review.md"
SHA = "4ccf9d128764d826b8fc08c71ab4602f4843abe6"

try:
    import jsonschema
except ImportError:  # pragma: no cover - the CI runner may not carry it
    jsonschema = None


def _frontmatter(text: str) -> dict[str, str]:
    match = re.match(r"^---\n(.*?)\n---\n", text, re.S)
    if not match:
        return {}
    fields = {}
    for line in match.group(1).splitlines():
        key, _, value = line.partition(":")
        fields[key.strip()] = value.strip()
    return fields


def _example_verdict() -> dict:
    block = re.search(r"```json\n(\{.*?\})\n```", SKILL.read_text(encoding="utf-8"), re.S)
    return json.loads(block.group(1))


def _gate():
    spec = importlib.util.spec_from_file_location("lease_tamper", ROOT / ".github/scripts/lease_tamper.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class SkillShape(unittest.TestCase):
    def test_frontmatter_names_the_directory_and_describes_when_to_use(self):
        fields = _frontmatter(SKILL.read_text(encoding="utf-8"))
        self.assertEqual(fields.get("name"), SKILL_DIR.name)
        self.assertTrue(0 < len(fields.get("description", "")) <= 1024)

    def test_skill_is_agent_and_vendor_neutral(self):
        # The role is filled by whatever model the coordinator configures; the skill
        # must not assume a vendor, a person, or a particular orchestrator.
        for path in SKILL_DIR.rglob("*"):
            if path.is_file():
                text = path.read_text(encoding="utf-8").lower()
                for word in ("claude", "anthropic", "openai", "kyle", "hermes"):
                    self.assertNotIn(word, text, f"{path.relative_to(ROOT)} names {word!r}")

    def test_referenced_files_exist(self):
        text = SKILL.read_text(encoding="utf-8")
        for rel in ("references/bypass-catalogue.md", "verdict.schema.json"):
            self.assertIn(rel, text)
            self.assertTrue((SKILL_DIR / rel).is_file(), rel)
        self.assertIn("policy/outside-review.md", text)
        self.assertTrue(POLICY.is_file())


class VerdictSchema(unittest.TestCase):
    def setUp(self):
        self.schema = json.loads(SCHEMA.read_text(encoding="utf-8"))
        self.example = _example_verdict()

    def test_example_carries_every_required_field(self):
        self.assertEqual(self.example["schema"], "rizzstack.review-verdict/1")
        for key in self.schema["required"]:
            self.assertIn(key, self.example)

    @unittest.skipIf(jsonschema is None, "jsonschema not installed")
    def test_schema_is_valid_and_the_example_validates(self):
        jsonschema.Draft202012Validator.check_schema(self.schema)
        jsonschema.validate(self.example, self.schema)

    @unittest.skipIf(jsonschema is None, "jsonschema not installed")
    def test_verdict_and_findings_must_agree(self):
        blocking = {"id": "F9", "severity": "blocking", "title": "t", "detail": "d", "failure_scenario": "x → y"}

        approve_with_blocking = copy.deepcopy(self.example)
        approve_with_blocking["findings"].append(blocking)
        with self.assertRaises(jsonschema.ValidationError):
            jsonschema.validate(approve_with_blocking, self.schema)

        reject_without_blocking = copy.deepcopy(self.example)
        reject_without_blocking["verdict"] = "changes_requested"
        with self.assertRaises(jsonschema.ValidationError):
            jsonschema.validate(reject_without_blocking, self.schema)

        reject_ok = copy.deepcopy(reject_without_blocking)
        reject_ok["findings"].append(blocking)
        jsonschema.validate(reject_ok, self.schema)

        blocking_without_scenario = copy.deepcopy(reject_ok)
        del blocking_without_scenario["findings"][-1]["failure_scenario"]
        with self.assertRaises(jsonschema.ValidationError):
            jsonschema.validate(blocking_without_scenario, self.schema)

        owner_without_decision = copy.deepcopy(self.example)
        owner_without_decision["verdict"] = "needs_owner"
        with self.assertRaises(jsonschema.ValidationError):
            jsonschema.validate(owner_without_decision, self.schema)

    @unittest.skipIf(jsonschema is None, "jsonschema not installed")
    def test_evidence_must_live_under_scratch(self):
        bad = copy.deepcopy(self.example)
        bad["findings"][0]["evidence"] = ["review/repo/launcher.py"]
        with self.assertRaises(jsonschema.ValidationError):
            jsonschema.validate(bad, self.schema)


class BriefTemplate(unittest.TestCase):
    def test_template_is_a_read_only_brief_that_writes_only_the_verdict(self):
        brief = json.loads(TEMPLATE.read_text(encoding="utf-8"))
        self.assertEqual(brief["schema"], "rizzstack.brief/1")
        for key in ("objective", "project", "source_rev", "assigned_files", "acceptance", "risk"):
            self.assertIn(key, brief)
        self.assertEqual(brief["assigned_files"], ["review/verdict.json"])
        self.assertIn("review/repo/**", brief["read_only"])
        self.assertIn(f"skills/{SKILL_DIR.name}/SKILL.md", brief["objective"])


class PolicyLines(unittest.TestCase):
    def test_posted_first_lines_parse_in_the_gate(self):
        gate = _gate()
        approve = f"APPROVE (independent review — rizzstack-reviewer, model family deepseek) at {SHA}"
        reject = (
            "CHANGES REQUESTED (independent review — rizzstack-reviewer, model family deepseek: "
            f"oracle config can be rewritten) at {SHA}"
        )
        self.assertEqual(gate.parse_review_line(approve), ("approve_independent", SHA))
        self.assertEqual(gate.parse_review_line(reject), ("changes_requested", SHA))

    def test_policy_documents_the_line_formats_it_promises(self):
        text = POLICY.read_text(encoding="utf-8")
        self.assertIn("APPROVE (independent review — <identity>, model family <family>) at <head sha>", text)
        self.assertIn("CHANGES REQUESTED (independent review — <identity>, model family <family>:", text)
        self.assertIn("post_mode", text)


if __name__ == "__main__":
    unittest.main()
