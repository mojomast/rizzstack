# scripts/codemap

Stdlib-only generator for the machine-made layer of the rizzstack code map. Python 3.11+ and git; no installs.

| File | Purpose |
|---|---|
| `sources.json` | repos, full pinned SHAs, layer, role, excluded globs |
| `build.py` | fetch pins (shallow, blob-filtered), index, render, check |
| `slice.py` | read-only task cut (`slice_map`) and reverse-impact (`impact`) helpers |
| `test_build.py` | unit tests (`python3 -m unittest discover -s scripts/codemap -p 'test_*.py'`) |

## What it extracts per repo

- file list with language and line counts (assets marked, not read)
- Python: module docstrings, top-level functions/classes/constants with signatures and first doc line (via `ast`); imports resolved to repo files
- JS/TS: exported and top-level functions, classes, consts (regex); relative imports resolved
- an identifier-reference graph (Aider-style): file A references a name defined in file B → edge A→B, IDF-weighted; ranks = PageRank (personalised by file size, tests down-weighted) blended with weighted in-degree
- entry points: `pyproject.toml` scripts, `package.json` scripts/bin, `plugin.yaml` tools/hooks, Dockerfiles, OpenAPI operations
- CI workflows (name, triggers, job ids), CODEOWNERS, schema files with `$id`
- HTTP path literals (`/api/...`, `/v1/...`) in non-test code, contract/schema ids (`rizzstack.*/N`, `acdd/N`, …), environment variable **names** (never values)

Every string is scrubbed (IPs other than loopback, tailnet hostnames, private host names, key-shaped strings) before it is written.

## Outputs (`codemap/generated`)

`index.json` (cross-repo summary), `<repo>.json` (full index), `<repo>.md` (ranked map, ≤6k tokens), `cross-repo.md` (contract-id matrix, shared HTTP paths, CI jobs). Output is deterministic for a given `sources.json` + `build.py`.

## Modes

```sh
python3 scripts/codemap/build.py [--workdir DIR] [--src name=PATH ...]   # regenerate
python3 scripts/codemap/build.py --check        # regenerate to temp, diff, then --verify-docs
python3 scripts/codemap/build.py --verify-docs  # narrative repo/path refs + scrub patterns + token budgets
python3 scripts/codemap/build.py --upstream [--strict]   # pinned vs upstream HEAD
python3 scripts/codemap/build.py --budgets      # token estimate (chars/4) per layer
python3 scripts/codemap/build.py --slice repo/path ... [--max-tokens N]  # attributed rows/bullets that mention the paths
python3 scripts/codemap/build.py --impact repo/path ...  # JSON: importers (depth 2), matching tests, complete:false
```

`--slice` always keeps the security and secrets invariants and trims pages by priority (gaps → ci-gates → change-recipes → contracts → README → L1). `--impact` reads `codemap/generated/<repo>.json` only; edges are a lower bound, so still run the full suite. Neither mode clones, fetches or writes.

Bumping a pin: edit `sha` (full 40 chars) in `sources.json`, regenerate, run `--verify-docs`, fix moved paths in the hand-written pages, commit both.
