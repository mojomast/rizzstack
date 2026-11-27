# Identities: who acts on GitHub

**Status (2026-11-27): planned.** Today the owner, the coordinator, its scheduled lanes and outside
reviewers all act through the owner's account, so the gate can trust only the **text** of a review
(`APPROVE (independent review…)`), and anything holding that token can write it. This page defines
the target: three identities with three jobs, none able to act as another.

| Identity | Who | May | May not |
|---|---|---|---|
| owner account (`mojomast`) | the owner, a human | decide: owner attestations, typed yeses, settings, rulesets, app creation | be used by any agent or service once the migration below completes |
| `rizzstack-coordinator[bot]` (GitHub App) | the coordinator (Hermes) and its lanes | open, update and merge PRs; push branches; open issues; re-run CI jobs | review, change rulesets, settings or secrets, administer repos |
| `rizzstack-reviewer[bot]` (GitHub App) | the review-bee poster (`policy/outside-review.md`) | post PR reviews (APPROVED or CHANGES REQUESTED) | push, merge, comment outside reviews, or anything else |

## App permissions

Both apps are owned by the owner's account, installed only on workflow, hyvv, bundlebees,
orbitdesktop, orbit-control-plane, uirizz and rizzstack. Neither has a webhook, OAuth or client secret.

- **rizzstack-reviewer:** pull requests read/write, contents read, checks read, metadata read.
- **rizzstack-coordinator:** contents write, pull requests write, issues write, actions write
  (rerun jobs), workflows write (CI files are edited through PRs), checks read, statuses read,
  metadata read. No administration, secrets, members or environments.

Neither app is a ruleset bypass actor.

## Key custody

The identity split is only as strong as who can read each private key.

- The reviewer key belongs to a separate OS user on the coordinator host that runs only the
  verdict poster, at mode 600. The coordinator's own process cannot read it. The poster posts only
  verdicts tied to a real review-bee attempt in the launcher's records (attempt id, routed model
  family, packet hash).
- The coordinator key sits in the coordinator's secret store.
- Keys never appear in repos, logs, chat, PRs or issues. Rotating a key means generating a new one on
  the app page and deleting the old one.

## What the gate checks (after migration)

- `approve_independent` counts only when the review's `user.login` is `rizzstack-reviewer[bot]` or
  the owner's login.
- `approve_owner`, and the owner's typed yes relayed by an agent, count only from the owner's login.
- Reviews by the coordinator's bot count for nothing.

Because PRs will then be authored by the coordinator's bot, the owner can submit ordinary GitHub
**Approve** reviews, and the reviewer bot can submit real APPROVED / CHANGES REQUESTED states
instead of comment-style attestations.

## Migration

1. Create and install both apps (manifest flow; the owner confirms creation and installation).
2. Move the coordinator's GitHub calls to the coordinator app's installation tokens, and confirm
   that PRs, pushes, merges and job reruns work.
3. Route review posting through the poster as `rizzstack-reviewer[bot]` (`post_mode: auto_reject`).
4. Gate PR (owner + outside review): trust logins as above. Re-vendor to all six product repos.
5. **Remove the owner's personal token from the coordinator host.** Until this step, the
   coordinator can still act as the owner, and the split protects nothing.
