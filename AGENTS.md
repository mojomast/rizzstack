# rizzstack umbrella: guide for agents

This repo holds docs, install profiles, the protocol map and the **code map** for the whole stack (workflow, hyvv, bundlebees, orbitdesktop, orbit-control-plane, uirizz). Code lives in those repos, not here.

## Find your way

1. Read `codemap/README.md` (L0, one screen).
2. Touching one repo: read `codemap/L1/<repo>.md`, then that repo's own `AGENTS.md` or `ARCHITECTURE.md` if it has one.
3. Crossing repos: `codemap/contracts.md` (seams), `codemap/change-recipes.md` (what to touch, in order), `codemap/architecture.md` (flows).
4. Looking for a symbol: `codemap/generated/<repo>.md` (ranked), or query `codemap/generated/<repo>.json` (files, symbols with `path:line`, edges, HTTP paths, env var names, CI jobs, schema ids). Don't read the JSON whole.
5. Before any risky action: `codemap/invariants.md`.
6. Reviewing a pull request as the outside reviewer (any agent, any model): `skills/rizzstack-outside-review/SKILL.md`; the coordinator's side (dispatch, validation, posting) is `policy/outside-review.md`.

Paths in the map are written `repo/path` and resolve at the SHAs pinned in `scripts/codemap/sources.json`. Check the pin against the live repo before trusting line numbers.

## Commands

```sh
python3 scripts/codemap/build.py                # regenerate codemap/generated from the pins
python3 scripts/codemap/build.py --check        # CI: regenerate + diff + verify docs
python3 scripts/codemap/build.py --verify-docs  # every repo/path in narrative exists; budgets met
python3 scripts/codemap/build.py --upstream     # which pins are behind main
python3 scripts/codemap/build.py --budgets      # token cost of each layer
python3 scripts/codemap/build.py --slice repo/path [--max-tokens N]  # task cut of the map
python3 scripts/codemap/build.py --impact repo/path ...              # importers + matching tests (JSON)
python3 -m unittest discover -s scripts/codemap -p 'test_*.py'
```

Use `nice`, `ionice` and `timeout` on shared machines. Clones go to a temp dir (`--workdir` to change, `--src name=path` to reuse a checkout at the pinned SHA).

## Rules for editing the map

- Never edit files under `codemap/generated`; change the generator or the pins and rerun.
- Hand-written pages state ownership, boundaries, invariants and change recipes, not file tours. Keep L0 ≤1.8k and each L1 ≤3k tokens.
- When upstream moves: bump `sha` in `scripts/codemap/sources.json`, regenerate, fix what `--verify-docs` reports, note new drift in `codemap/gaps.md`.
- No secrets, tokens, tailnet hosts, IPs or private URLs, anywhere. Use generic host names ("Hermes host", "Orbit host", "owner PC").
- Write rizzstack lowercase. Run gitleaks before every push. Feature branch + PR; no force-push.
- Tags, releases, deploys and public posts need the owner's own typed yes.
