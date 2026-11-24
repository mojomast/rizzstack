"""Contract for the committed main-branch rulesets (rizzstack decision 1).

policy/rulesets/<repo>.json is the ruleset applied to each repo's default
branch: no deletion, no force-push, PRs required (0 approvals: one maintainer,
whose chat approval is the human review), required checks including the
aggregate `ci-gate`, and no bypass actors. "Up to date before merge" stays off
except where a repo already had it (workflow, hyvv), which is kept.
"""
import json
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
POLICY = ROOT / "policy" / "rulesets"
REPOS = ("workflow", "hyvv", "bundlebees", "orbitdesktop", "orbit-control-plane", "uirizz", "rizzstack")
STRICT_KEPT = {"workflow", "hyvv"}
GITHUB_ACTIONS_APP_ID = 15368


def load(repo):
    return json.loads((POLICY / ("%s.json" % repo)).read_text(encoding="utf-8"))


def rule(ruleset, kind):
    found = [r for r in ruleset["rules"] if r["type"] == kind]
    return found[0] if found else None


class RulesetPolicyTests(unittest.TestCase):
    def test_every_repo_has_a_ruleset(self):
        for repo in REPOS:
            self.assertTrue((POLICY / ("%s.json" % repo)).is_file(), repo)

    def test_baseline_rules_and_no_bypass(self):
        for repo in REPOS:
            rs = load(repo)
            self.assertEqual(rs["enforcement"], "active", repo)
            self.assertEqual(rs["bypass_actors"], [], repo)
            self.assertIsNotNone(rule(rs, "deletion"), repo)
            self.assertIsNotNone(rule(rs, "non_fast_forward"), repo)
            include = rs["conditions"]["ref_name"]["include"]
            self.assertTrue(set(include) & {"~DEFAULT_BRANCH", "refs/heads/main"}, repo)

    def test_pull_request_with_zero_approvals(self):
        for repo in REPOS:
            pr = rule(load(repo), "pull_request")
            self.assertIsNotNone(pr, repo)
            self.assertEqual(pr["parameters"]["required_approving_review_count"], 0, repo)

    def test_required_checks_include_ci_gate_from_github_actions(self):
        for repo in REPOS:
            checks = rule(load(repo), "required_status_checks")["parameters"]
            names = [c["context"] for c in checks["required_status_checks"]]
            self.assertIn("ci-gate", names, repo)
            for c in checks["required_status_checks"]:
                self.assertEqual(c.get("integration_id"), GITHUB_ACTIONS_APP_ID, (repo, c))
            self.assertEqual(checks["strict_required_status_checks_policy"], repo in STRICT_KEPT, repo)


if __name__ == "__main__":
    unittest.main()
