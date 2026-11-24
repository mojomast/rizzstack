# ADR 0001: Format of the rizzstack code map

Status: proposed (Nov 24 2026) · Deciders: owner

## Context

rizzstack is six repos (~3,000 files) with no shared code imports: they meet at schemas and HTTP contracts. Agents starting cold waste most of their steps finding where a thing lives and which repo owns a contract, and they miss the rules that are expressed as absences ("never approve on click"). The map must serve agents first and humans second, stay true as repos move, and leak nothing private.

## What the research says, and what we took from it

| Source | Finding | Adopted as |
|---|---|---|
| AGENTS.md convention (agents.md; nested-file guidance, Codex 32 KiB cap, ~200–500 line advice) | one short root file for commands, rules, pointers; nested files only where rules differ; don't restate the root | root `AGENTS.md` that points into the map; `CLAUDE.md` is just `@AGENTS.md` |
| matklad, "ARCHITECTURE.md" | a codemap answers "where is the thing that does X"; name files and types, don't hyperlink every one; call out invariants and boundaries; keep it short and stable | hand-written L0/L1 focus on ownership, boundaries, invariants; repo-relative paths in backticks |
| ETH Zurich context-file study (2026, as summarised by stackness.dev) and practitioner reports | prose repository overviews barely reduce agent steps; pitfalls, rationale, "current vs legacy" and "never" rules do | L1 pages lead with "where things are" tables and change recipes; `codemap/invariants.md`, `codemap/gaps.md` (legacy launcher, schema-id confusion) |
| Aider repo map (tree-sitter tags + PageRank over a def/ref graph, binary-searched to a token budget, default 1k) | rank files and symbols by how much the rest of the code refers to them; fit a budget; drop the tail honestly | generated L2 `<repo>.md`: PageRank (personalised by file size) blended with weighted in-degree over import + identifier-reference edges, IDF-damped, cut at 6k tokens with "… +N more" |
| ctags / SCIP / LSIF | precise symbol indexes need language tooling | out of scope for v1 (stdlib only, runs anywhere); JSON index has `path:line` so a SCIP index can be added later (see gaps) |
| C4 model, Mermaid | context and container views explain runtime boundaries better than a file tree | `codemap/architecture.md` with context, container table and sequence flows; generic host names only |
| ADRs | record why, separately from what | this file; `codemap/decisions` |
| llms.txt | curated index of high-value docs, not everything | root `llms.txt` |
| Generated CODEMAP practice (freshness banner, provenance, size cap, CI drift check) | volatile structure must be generated and checked or it rots | GENERATED headers with SHA + regen command; pinned SHAs in `scripts/codemap/sources.json`; CI `--check` regenerates and diffs; `--verify-docs` fails if a hand-written `repo/path` no longer exists; `--upstream` reports stale pins |

## Decision

1. **Layered, token-budgeted:** L0 `codemap/README.md` (≤1.8k tokens) → L1 `codemap/L1/<repo>.md` (≤3k each) + cross-cutting pages → L2 generated `<repo>.md` (≤6k) → JSON for programmatic queries. Budgets are enforced by `--verify-docs`.
2. **Hand-written vs generated are separate files.** Narrative never sits inside generated output; generated files are never hand-edited.
3. **Pinned, reproducible:** generation is deterministic from `sources.json` (full SHAs); no timestamps in output.
4. **Scrubbed by construction:** the generator redacts IPs, tailnet hostnames and key-shaped strings; `--verify-docs` refuses narrative that contains them. Machine-local private names are detected from a gitignored file and fail the check rather than being rewritten, so output never depends on the machine (Nov 24 review, item 6).
5. **Stdlib-only Python** so CI and any agent can run it without installs.

## Consequences

- Bumping a pin is a one-line change plus a regenerate; CI tells you which narrative paths broke.
- Ranking is heuristic and regex-based for JS/TS; precise cross-references need a compiler index.
- The map describes `main` at the pins, not open PRs.
