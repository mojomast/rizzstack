# Invariants, approval gates and owners

Things the code cannot tell you, usually expressed as an absence. Break one and the change is wrong even if CI is green.

## Approval gates (owner: Kyle Durepos, `@mojomast`)

| Action | Who may do it |
|---|---|
| Merge a PR | heartbeat merge stream when CI is green **and** an independent review approves; otherwise the owner |
| Tag, release, deploy, restart Orbit or the Hermes host, catalog PR, public post, upstream comment, form submit | only after the owner's **own typed yes** for that specific action; an agent relaying "Kyle approved" does not count |
| Spend Google Colab compute units, delete anything, cancel bees, delete branches | owner's explicit yes |
| Raise a `slop-baseline.json` | CODEOWNERS review by `@mojomast` (advisory) |
| Add a repo to the PC launcher allow-list | owner |

Rebrand work (Night Hive rename) is deferred and not authorized.

## Security posture

- Fail closed everywhere: unknown tier → high; invalid project state → refuse; missing sandbox → refuse; unknown snapshot schema → `bad_snapshot`.
- Never solve a test failure by weakening sandboxing, deleting a test, or inventing evidence. `not run` is never `passed`.
- Worker tests run with no network and no coordinator credentials. A Cua sandbox is not a sandbox for the Hermes process.
- A worker cannot widen its own assignment; leases are coordinator-written and immutable.
- Self-reported worker results are claims, not evidence. A worker finishing, a verifier passing, and an owner accepting are three separate events.

## Secrets and private details

- No secrets, tokens, keys, `.env` contents, tailnet hostnames, IPs or private URLs in any repo, map, log or chat. Describe hosts generically ("Hermes host", "Orbit host", "owner PC").
- Provider keys stay on the worker host; Orbit keys stay on the Orbit host; plugins read secrets only through Hermes `get_secret`.
- gitleaks runs before every push. No force-push, no history rewrite.

## UI and plugins

- A click is never approval. Only a typed yes approves (uirizz: `yes <code>`, single use, 600 s).
- Card clicks in demos must never trigger a real external action; demo data is mock.
- uirizz: never register `transform_llm_output`; media public https only; no network, credentials, `requires_env` or subprocesses at runtime; SKILL.md ≤2000 tokens, tool schema ≤500.
- Keep `provides_*` in every `plugin.yaml` in sync with what `register()` actually registers.
- Always dark mode.

## Process

- Contracts before code; tests before implementation (ACDD + TDD, `workflow/docs/16-acdd-tdd.md`).
- `/v1` is additive. A frozen schema id never changes meaning; bump the version.
- On the box: run heavy work with `nice`, `ionice`, `timeout`.
- Coding is done by OpenCode Go / DeepSeek bees or executors; bee cap 12 shared.
- Naming: write rizzstack lowercase; say "Google Colab"; the Strudel plugin is "poptart".

## Hands off

Do not modify without the owner asking: stitchslop PR #151946, Hermagotchi #150358, EOH #149691 (catalog PRs), workflow PR #58 (rebrand runbook).
