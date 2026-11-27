# Main-branch rulesets

`rulesets/<repo>.json` is the GitHub ruleset on each rizzstack repo's default branch, as applied on 2026-11-24 (decision 1; orbitdesktop's `gitleaks` check was added later that day, orbitdesktop #90). It's a snapshot of the API response without timestamps. `tests/test_policy_rulesets.py` checks the baseline:

- no deletion and no force-push of `main`;
- changes land through a pull request (0 required approvals: one maintainer, whose chat approval is the human review);
- required checks come from GitHub Actions and include the aggregate `ci-gate` job, which needs every job in its workflow file (`.github/scripts/check_ci_gate.py` in each repo keeps that true);
- no bypass actors, admins included.

"Require branches to be up to date" stays off (parallel bees and no merge queue on a personal account), except in workflow and hyvv, which already had it.

To change a ruleset, update it in GitHub (a settings change that needs the owner's yes), then refresh the snapshot here in the same PR.

Known gaps: no required reviewer (one maintainer); rizzstack's path-filtered `codemap` check isn't required (a path-filtered check never reports on unrelated PRs and would block them).

See also `outside-review.md`: who may review a pull request, what a review bee receives, and how its verdict is validated and posted.
