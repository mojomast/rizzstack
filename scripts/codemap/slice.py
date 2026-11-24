#!/usr/bin/env python3
"""Task-specific cuts of the rizzstack code map (stdlib only, read-only).

Two pure functions used by ``build.py --slice`` and ``build.py --impact``:

  slice_map(paths, max_tokens=1500) -> str
      Rows and bullets from the hand-written map pages that mention one of
      ``paths`` or its parent directory, plus the mandatory security and
      secrets invariants.  Trimmed to the token budget by dropping the
      lowest-priority pages first.

  impact(paths) -> dict
      Reverse import edges (depth 2) and naming-convention test files for the
      given repo-prefixed paths, read from ``codemap/generated/<repo>.json``.
      The result is a lower bound: the full suite still has to run.

Nothing here writes files, calls git, or opens a socket.
"""
from __future__ import annotations

import json
import math
import os
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

PAGES = [
    "codemap/README.md",
    "codemap/L1/*.md",
    "codemap/contracts.md",
    "codemap/change-recipes.md",
    "codemap/ci-gates.md",
    "codemap/gaps.md",
    "codemap/invariants.md",
]

# Lower number is dropped first when the slice is over budget.
PRIORITY = {
    "codemap/gaps.md": 0,
    "codemap/ci-gates.md": 1,
    "codemap/change-recipes.md": 2,
    "codemap/contracts.md": 3,
    "codemap/README.md": 4,
}
L1_PRIORITY = 5
INVARIANTS_PRIORITY = 6

MANDATORY_SECTIONS = ("Security posture", "Secrets and private details")
BULLET = re.compile(r"^\s*(?:[-*+]|\d+[.)])\s+")
NOTE = "Impact edges are a lower bound; always run the full suite."


def est_tokens(s: str) -> int:
    """Same estimate as build.est_tokens: ceil(chars / 4)."""
    return math.ceil(len(s) / 4)


def _pages() -> list[str]:
    out = []
    for pattern in PAGES:
        if "*" in pattern:
            out += [str(p.relative_to(ROOT)) for p in sorted(ROOT.glob(pattern))]
        elif (ROOT / pattern).is_file():
            out.append(pattern)
    return out


def _path_candidates(paths) -> tuple[list[str], list[str]]:
    full, parents = [], []
    for raw in paths or []:
        p = str(raw).strip()
        if not p:
            continue
        if p.startswith("./"):
            p = p[2:]
        if p not in full:
            full.append(p)
        parent = p.rsplit("/", 1)[0] if "/" in p else ""
        # A parent equal to the bare repo name would match every line; only
        # use a parent that is itself repo-prefixed with a directory.
        if parent and "/" in parent and parent not in parents and parent not in full:
            parents.append(parent)
    return full, parents


def _patterns(candidates: list[str]):
    return [re.compile(r"(?<![\w./-])" + re.escape(c) + r"(?![\w-]|\.\w)") for c in candidates]


def _mentions(line: str, patterns) -> bool:
    return any(p.search(line) for p in patterns)


def _is_row_or_bullet(stripped: str) -> bool:
    if stripped.startswith("|"):
        return bool(stripped.strip("|").strip())
    return bool(BULLET.match(stripped))


def _collect(paths) -> list[dict]:
    full, parents = _path_candidates(paths)
    patterns = _patterns(full + parents)
    exact_patterns = _patterns(full)
    records = []
    for page in _pages():
        text = (ROOT / page).read_text(encoding="utf-8")
        section = ""
        if page.startswith("codemap/L1/"):
            priority = L1_PRIORITY
        else:
            priority = PRIORITY.get(page, INVARIANTS_PRIORITY)
        for line in text.splitlines():
            stripped = line.strip()
            if not stripped:
                continue
            if stripped.startswith("#"):
                section = stripped.lstrip("#").strip()
                continue
            if not _is_row_or_bullet(stripped):
                continue
            mandatory = page == "codemap/invariants.md" and section in MANDATORY_SECTIONS and bool(BULLET.match(stripped))
            if not mandatory and not _mentions(stripped, patterns):
                continue
            exact = _mentions(stripped, exact_patterns)
            records.append({
                "page": page,
                "priority": 7 if exact else priority,
                "mandatory": mandatory,
                "line": f"[{page}] {stripped}",
            })
    return records


def slice_map(paths, max_tokens: int = 1500) -> str:
    records = _collect(paths)
    selected = list(records)

    def render(items) -> str:
        return "\n".join(r["line"] for r in items)

    if est_tokens(render(selected)) > max_tokens:
        for priority in sorted({r["priority"] for r in selected if not r["mandatory"]}):
            while est_tokens(render(selected)) > max_tokens:
                for i in range(len(selected) - 1, -1, -1):
                    r = selected[i]
                    if not r["mandatory"] and r["priority"] == priority:
                        del selected[i]
                        break
                else:
                    break
            if est_tokens(render(selected)) <= max_tokens:
                break
    return render(selected)


def _split(path: str):
    p = str(path).strip()
    if p.startswith("./"):
        p = p[2:]
    if "/" not in p:
        return None, None
    repo, rest = p.split("/", 1)
    return repo, rest


def _load_repo(repo: str):
    fp = ROOT / "codemap" / "generated" / f"{repo}.json"
    if not fp.is_file():
        return None
    try:
        return json.loads(fp.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _stems(paths) -> set[str]:
    return {os.path.splitext(p.rsplit("/", 1)[-1])[0] for p in paths if p}


def impact(paths) -> dict:
    changed = [str(p) for p in (paths or [])]
    dependents: set[str] = set()
    tests: set[str] = set()

    by_repo: dict[str, list[str]] = {}
    for p in changed:
        repo, rest = _split(p)
        if repo and rest:
            by_repo.setdefault(repo, []).append(rest)

    for repo, rel_paths in by_repo.items():
        data = _load_repo(repo)
        if not data:
            continue
        known = {f["path"] for f in data.get("files", []) if isinstance(f, dict) and "path" in f}
        rel_changed = [p for p in rel_paths if p in known]
        if not rel_changed:
            continue

        reverse: dict[str, set[str]] = {}
        for edge in data.get("edges", []):
            if len(edge) < 2:
                continue
            importer, imported = edge[0], edge[1]
            reverse.setdefault(imported, set()).add(importer)

        seen = set(rel_changed)
        frontier = set(rel_changed)
        repo_deps: set[str] = set()
        for _ in range(2):
            nxt: set[str] = set()
            for path in frontier:
                for importer in reverse.get(path, ()):
                    if importer not in seen:
                        nxt.add(importer)
            seen |= nxt
            repo_deps |= nxt
            frontier = nxt

        dependents |= {f"{repo}/{p}" for p in repo_deps}
        stems = _stems(rel_changed) | _stems(repo_deps)
        for f in data.get("files", []):
            path = f.get("path", "") if isinstance(f, dict) else ""
            if not path:
                continue
            base = path.rsplit("/", 1)[-1]
            for stem in stems:
                if base.startswith(f"test_{stem}") or base.startswith(f"{stem}.test."):
                    tests.add(f"{repo}/{path}")
                    break

    return {
        "changed": changed,
        "dependents": sorted(dependents),
        "tests": sorted(tests),
        "complete": False,
        "note": NOTE,
    }
