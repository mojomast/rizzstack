#!/usr/bin/env python3
"""rizzstack codemap generator (stdlib only).

Rebuilds the machine-generated layer of the rizzstack code map from the repo
SHAs pinned in scripts/codemap/sources.json:

  codemap/generated/index.json      cross-repo summary for agents (L1 data)
  codemap/generated/<repo>.json     full per-repo index: files, symbols, edges (L2 data)
  codemap/generated/<repo>.md       token-budgeted ranked repo map (L2, Aider-style)
  codemap/generated/cross-repo.md   contract-id matrix + shared HTTP paths

Hand-written narrative (codemap/*.md, codemap/L1/*.md, AGENTS.md, llms.txt) is
never touched; `--verify-docs` checks that every `repo/path` it names exists at
the pinned SHA and that the L0/L1 layers stay inside their token budgets.

Usage:
  python3 scripts/codemap/build.py                 # fetch pinned SHAs, regenerate
  python3 scripts/codemap/build.py --check         # regenerate to a temp dir, fail on drift
  python3 scripts/codemap/build.py --verify-docs   # path + budget checks only (no network)
  python3 scripts/codemap/build.py --upstream      # report pinned SHA vs upstream main
  python3 scripts/codemap/build.py --src workflow=/path/to/checkout ...  # reuse checkouts
  python3 scripts/codemap/build.py --slice repo/path [--max-tokens N]  # task cut of the map
  python3 scripts/codemap/build.py --impact repo/path ...              # importers + tests (JSON)
"""
from __future__ import annotations

import argparse
import ast
import fnmatch
import json
import math
import os
import re
import shutil
import subprocess
import sys
import tempfile
import tomllib
from collections import Counter, defaultdict
from pathlib import Path

GENERATOR_VERSION = "1.0.0"
ROOT = Path(__file__).resolve().parents[2]
SOURCES = ROOT / "scripts" / "codemap" / "sources.json"
OUT = ROOT / "codemap" / "generated"
MAX_READ = 512 * 1024
L2_BUDGET_TOKENS = 6000

# Token budgets for the hand-written layers (estimated as ceil(chars / 4)).
BUDGETS = {
    "AGENTS.md": 1500,
    "CLAUDE.md": 50,
    "llms.txt": 1200,
    "codemap/README.md": 1800,
    "codemap/L1/*.md": 3000,
}

LANG = {
    ".py": "python", ".pyi": "python",
    ".js": "js", ".mjs": "js", ".cjs": "js", ".jsx": "js",
    ".ts": "ts", ".tsx": "ts", ".mts": "ts",
    ".sh": "shell", ".bash": "shell", ".ps1": "powershell",
    ".md": "markdown", ".json": "json", ".jsonl": "json",
    ".yml": "yaml", ".yaml": "yaml", ".toml": "toml",
    ".html": "html", ".css": "css", ".sql": "sql",
}
CODE_LANGS = {"python", "js", "ts"}
IDENT = re.compile(r"\b[A-Za-z_][A-Za-z0-9_]*\b")
HTTP_PATH = re.compile(r"""['"`](/(?:api|v1)/[A-Za-z0-9_\-./{}:<>*]*)['"`]""")
CONTRACT_ID = re.compile(
    r"\b(rizzstack\.[a-z_]+(?:[./][a-z_]+)*/\d+|acdd/\d+|tdd-evidence/\d+|bee-landed/\d+|"
    r"hyvv\.[a-z_]+(?:[./][a-z_]+)*/\d+|bundlebees\.[a-z_]+(?:[./][a-z_]+)*/\d+|orbit\.[a-z_]+(?:[./][a-z_]+)*/\d+|"
    r"uirizz\.[a-z_]+(?:[./][a-z_]+)*/\d+)\b"
)
ENV_PATTERNS = [
    re.compile(r"""os\.environ\.get\(\s*['"]([A-Z][A-Z0-9_]{2,})['"]"""),
    re.compile(r"""os\.environ\[\s*['"]([A-Z][A-Z0-9_]{2,})['"]"""),
    re.compile(r"""os\.getenv\(\s*['"]([A-Z][A-Z0-9_]{2,})['"]"""),
    re.compile(r"""process\.env\.([A-Z][A-Z0-9_]{2,})"""),
    re.compile(r"""process\.env\[\s*['"]([A-Z][A-Z0-9_]{2,})['"]"""),
]
JS_IMPORT = re.compile(r"""(?:import\s[^'"`;]*?from\s*|import\s*\(\s*|require\(\s*|import\s+)['"](\.{1,2}/[^'"]+)['"]""")
JS_DEFS = [
    (re.compile(r"^export\s+(?:default\s+)?(?:async\s+)?function\s*\*?\s*([A-Za-z_$][\w$]*)\s*\(([^)]*)\)", re.M), "function", True),
    (re.compile(r"^export\s+(?:default\s+)?class\s+([A-Za-z_$][\w$]*)", re.M), "class", True),
    (re.compile(r"^export\s+(?:const|let|var)\s+([A-Za-z_$][\w$]*)", re.M), "const", True),
    (re.compile(r"^(?:async\s+)?function\s*\*?\s*([A-Za-z_$][\w$]*)\s*\(([^)]*)\)", re.M), "function", False),
    (re.compile(r"^class\s+([A-Za-z_$][\w$]*)", re.M), "class", False),
]
JS_EXPORT_LIST = re.compile(r"^export\s*\{([^}]*)\}", re.M)
STOP_SYMBOLS = {"main", "run", "init", "get", "set", "default", "setup", "handler", "app", "data", "self",
                "name", "value", "result", "error", "test", "tests", "config", "path", "args", "kwargs"}

# --- scrubbing: the map is public; never let private network details or secrets through ---
SCRUB = [
    (re.compile(r"\b(?!127\.0\.0\.1\b)(?!0\.0\.0\.0\b)(?:\d{1,3}\.){3}\d{1,3}\b"), "<ip>"),
    (re.compile(r"\b[\w-]+(?:\.[\w-]+)*\.ts\.net\b"), "<tailnet-host>"),
    (re.compile(r"\b(?:ghp|gho|ghu|ghs|github_pat)_[A-Za-z0-9_]{10,}"), "<redacted>"),
    (re.compile(r"\bsk-[A-Za-z0-9_\-]{16,}"), "<redacted>"),
    (re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{10,}"), "<redacted>"),
    (re.compile(r"\bAKIA[0-9A-Z]{16}\b"), "<redacted>"),
    (re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"), "<redacted>"),
]


# Optional extra private names (regex, e.g. internal host names) from the environment, so the
# names themselves are never committed: CODEMAP_PRIVATE_NAMES='name-a|name-b'
if os.environ.get("CODEMAP_PRIVATE_NAMES"):
    SCRUB.append((re.compile(r"\b(?:" + os.environ["CODEMAP_PRIVATE_NAMES"] + r")\b"), "private-host"))


def scrub(s: str) -> str:
    for pat, rep in SCRUB:
        s = pat.sub(rep, s)
    return s


def scrub_obj(o):
    if isinstance(o, str):
        return scrub(o)
    if isinstance(o, list):
        return [scrub_obj(x) for x in o]
    if isinstance(o, dict):
        return {scrub(k): scrub_obj(v) for k, v in o.items()}
    return o


def sh(*cmd, cwd=None, check=True) -> str:
    r = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True)
    if check and r.returncode != 0:
        raise SystemExit(f"command failed: {' '.join(cmd)}\n{r.stderr.strip()}")
    return r.stdout


def load_sources() -> dict:
    return json.loads(SOURCES.read_text())


# ------------------------------------------------------------------ fetch
def fetch(repo: dict, workdir: Path, overrides: dict) -> Path:
    name, sha = repo["name"], repo["sha"]
    if name in overrides:
        p = Path(overrides[name])
        head = sh("git", "rev-parse", "HEAD", cwd=p).strip()
        if head != sha:
            raise SystemExit(f"{name}: checkout {p} is at {head[:7]}, sources.json pins {sha[:7]}")
        return p
    p = workdir / name
    if (p / ".git").exists() and sh("git", "rev-parse", "HEAD", cwd=p, check=False).strip() == sha:
        return p
    if p.exists():
        shutil.rmtree(p)
    p.mkdir(parents=True)
    sh("git", "init", "-q", cwd=p)
    sh("git", "remote", "add", "origin", repo["url"] + ".git", cwd=p)
    sh("git", "fetch", "-q", "--depth", "1", "--filter=blob:limit=1m", "origin", sha, cwd=p)
    sh("git", "-c", "advice.detachedHead=false", "checkout", "-q", "FETCH_HEAD", cwd=p)
    return p


# ------------------------------------------------------------------ analysis helpers
def first_line(doc: str | None, n: int = 110) -> str:
    if not doc:
        return ""
    line = doc.strip().splitlines()[0].strip() if doc.strip() else ""
    return line[:n]


def py_sig(node) -> str:
    a = node.args
    names = [x.arg for x in a.posonlyargs + a.args]
    if a.vararg:
        names.append("*" + a.vararg.arg)
    names += [x.arg for x in a.kwonlyargs]
    if a.kwarg:
        names.append("**" + a.kwarg.arg)
    if names and names[0] in ("self", "cls"):
        names = names[1:]
    s = ", ".join(names)
    return s if len(s) <= 80 else s[:77] + "..."


def analyze_python(text: str):
    syms, imports = [], []
    try:
        tree = ast.parse(text)
    except (SyntaxError, ValueError):
        return syms, imports, ""
    mdoc = first_line(ast.get_docstring(tree))
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            syms.append({"name": node.name, "kind": "function", "line": node.lineno,
                         "sig": py_sig(node), "doc": first_line(ast.get_docstring(node)),
                         "exported": not node.name.startswith("_")})
        elif isinstance(node, ast.ClassDef):
            methods = [n.name for n in node.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
                       and (not n.name.startswith("_") or n.name == "__init__")]
            syms.append({"name": node.name, "kind": "class", "line": node.lineno,
                         "sig": ", ".join(methods[:10]) + (" ..." if len(methods) > 10 else ""),
                         "doc": first_line(ast.get_docstring(node)),
                         "exported": not node.name.startswith("_")})
        elif isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
            n = node.targets[0].id
            if n.isupper() and len(n) > 3:
                syms.append({"name": n, "kind": "const", "line": node.lineno, "sig": "", "doc": "", "exported": True})
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                imports.append((0, a.name, []))
        elif isinstance(node, ast.ImportFrom):
            imports.append((node.level, node.module or "", [a.name for a in node.names]))
    return syms, imports, mdoc


def analyze_js(text: str):
    syms, seen = [], set()
    for pat, kind, exported in JS_DEFS:
        for m in pat.finditer(text):
            name = m.group(1)
            if name in seen:
                continue
            seen.add(name)
            line = text.count("\n", 0, m.start()) + 1
            sig = ""
            if kind == "function" and m.lastindex and m.lastindex >= 2:
                sig = re.sub(r"\s+", " ", m.group(2)).strip()
                sig = sig if len(sig) <= 80 else sig[:77] + "..."
            syms.append({"name": name, "kind": kind, "line": line, "sig": sig, "doc": "", "exported": exported})
    exported_names = set()
    for m in JS_EXPORT_LIST.finditer(text):
        for part in m.group(1).split(","):
            part = part.strip().split(" as ")[0].strip()
            if part:
                exported_names.add(part)
    for s in syms:
        if s["name"] in exported_names:
            s["exported"] = True
    imports = [m.group(1) for m in JS_IMPORT.finditer(text)]
    return syms, imports


def is_test(path: str) -> bool:
    p = path.lower()
    base = p.rsplit("/", 1)[-1]
    return ("/tests/" in "/" + p or "/test/" in "/" + p or base.startswith("test_") or ".test." in base
            or ".spec." in base or base.endswith("_test.py"))


def yaml_lite_top(text: str) -> dict:
    """Top-level scalars and simple string lists from a YAML file (no PyYAML dependency)."""
    out, key = {}, None
    for line in text.splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        m = re.match(r"^([A-Za-z_][\w-]*):\s*(.*)$", line)
        if m:
            key, val = m.group(1), m.group(2).strip()
            if val and not val.startswith(("|", ">")):
                out[key] = val.strip("'\"")[:160]
            else:
                out[key] = []
            continue
        m = re.match(r"^\s+-\s+(.+)$", line)
        if m and key and isinstance(out.get(key), list) and len(out[key]) < 40:
            v = m.group(1).strip().strip("'\"")
            if ":" not in v or v.startswith(("http", "/")):
                out[key].append(v[:120])
    return {k: v for k, v in out.items() if v != []}


def parse_workflow(text: str) -> dict:
    wf = {"name": "", "on": [], "jobs": []}
    m = re.search(r"^name:[ \t]*(.+)$", text, re.M)
    if m:
        wf["name"] = m.group(1).strip().strip("'\"")
    m = re.search(r"^(?:on|\"on\"|'on'):[ \t]*(.*)$", text, re.M)
    if m:
        inline = m.group(1).strip()
        if inline:
            wf["on"] = [x.strip() for x in inline.strip("[]").split(",") if x.strip()]
        else:
            block = text[m.end():]
            for line in block.splitlines():
                if re.match(r"^\S", line):
                    break
                mm = re.match(r"^  ([A-Za-z_][\w-]*):", line)
                if mm:
                    wf["on"].append(mm.group(1))
    m = re.search(r"^jobs:\s*$", text, re.M)
    if m:
        block = text[m.end():]
        cur = None
        for line in block.splitlines():
            if re.match(r"^\S", line):
                break
            mm = re.match(r"^  ([A-Za-z0-9_-]+):\s*$", line)
            if mm:
                cur = {"id": mm.group(1), "name": ""}
                wf["jobs"].append(cur)
                continue
            mm = re.match(r"^    name:\s*(.+)$", line)
            if mm and cur is not None and not cur["name"]:
                cur["name"] = mm.group(1).strip().strip("'\"")[:120]
    return wf


def pagerank(nodes, edges, iters=60, d=0.85, personalization=None):
    n = len(nodes)
    if n == 0:
        return {}
    idx = {k: i for i, k in enumerate(nodes)}
    pers = [1.0 / n] * n
    if personalization:
        tot = sum(personalization.get(k, 0.0) for k in nodes) or 1.0
        pers = [personalization.get(k, 0.0) / tot for k in nodes]
    out_w = defaultdict(float)
    for (a, b), w in edges.items():
        out_w[a] += w
    rank = [1.0 / n] * n
    inc = defaultdict(list)
    for (a, b), w in edges.items():
        inc[idx[b]].append((idx[a], w / out_w[a]))
    dangling = [i for i, k in enumerate(nodes) if out_w.get(k, 0) == 0]
    for _ in range(iters):
        dsum = sum(rank[i] for i in dangling)
        new = [(1 - d) * pers[i] + d * dsum * pers[i] for i in range(n)]
        for b, lst in inc.items():
            new[b] += d * sum(rank[a] * w for a, w in lst)
        rank = new
    return {k: rank[i] for k, i in idx.items()}


# ------------------------------------------------------------------ per-repo index
def index_repo(repo: dict, path: Path, excludes: list[str]) -> dict:
    all_paths = sorted(sh("git", "ls-tree", "-r", "--name-only", "HEAD", cwd=path).splitlines())
    files, texts = [], {}
    for p in all_paths:
        base = p.rsplit("/", 1)[-1]
        ext = os.path.splitext(base)[1].lower()
        excluded = any(fnmatch.fnmatch(base, g) or fnmatch.fnmatch(p, g) for g in excludes)
        lang = LANG.get(ext, "other")
        entry = {"path": p, "lang": "asset" if excluded else lang, "lines": 0}
        fp = path / p
        if not excluded and fp.is_file() and not fp.is_symlink() and fp.stat().st_size <= MAX_READ:
            try:
                t = fp.read_text(encoding="utf-8")
                texts[p] = t
                entry["lines"] = t.count("\n") + (0 if t.endswith("\n") or not t else 1)
            except (UnicodeDecodeError, OSError):
                entry["lang"] = "binary"
        files.append(entry)

    file_set = set(all_paths)
    # python module suffix index for import resolution
    mod_index = defaultdict(list)
    for p in all_paths:
        if p.endswith(".py"):
            parts = p[:-3].split("/")
            if parts[-1] == "__init__":
                parts = parts[:-1]
            for i in range(len(parts)):
                mod_index[".".join(parts[i:])].append(p)

    def resolve_py(importer: str, level: int, module: str, names: list[str]) -> list[str]:
        out = []
        if level:
            base = importer.split("/")[:-1]
            if level > 1:
                base = base[: len(base) - (level - 1)]
            cands = []
            mod_parts = module.split(".") if module else []
            for nm in names or [""]:
                parts = base + mod_parts + ([nm] if nm else [])
                cands.append("/".join(parts))
            cands.append("/".join(base + mod_parts))
            for c in cands:
                for f in (c + ".py", c + "/__init__.py"):
                    if f in file_set and f != importer:
                        out.append(f)
                        break
                else:
                    continue
                if out:
                    break
            return out[:1]
        for cand in ([module + "." + n for n in names] if names else []) + [module]:
            hits = mod_index.get(cand)
            if hits:
                best = max(hits, key=lambda h: (len(os.path.commonprefix([h, importer])), -len(h)))
                if best != importer:
                    return [best]
        return []

    def resolve_js(importer: str, spec: str) -> list[str]:
        base = os.path.normpath(os.path.join(os.path.dirname(importer), spec))
        for suf in ("", ".mjs", ".js", ".ts", ".tsx", ".jsx", ".cjs", "/index.mjs", "/index.js", "/index.ts", "/index.tsx"):
            if base + suf in file_set:
                return [base + suf]
        return []

    symbols, imp_edges = [], Counter()
    mdocs, env_names, http_paths, contract_ids = {}, defaultdict(set), defaultdict(set), defaultdict(set)
    tokens = {}
    for p, t in texts.items():
        lang = LANG.get(os.path.splitext(p)[1].lower(), "other")
        if lang in CODE_LANGS or lang in ("shell", "powershell", "yaml", "markdown", "json", "html", "toml"):
            for m in CONTRACT_ID.finditer(t):
                contract_ids[m.group(1)].add(p)
        if lang not in CODE_LANGS:
            continue
        if lang == "python":
            syms, imps, mdoc = analyze_python(t)
            if mdoc:
                mdocs[p] = mdoc
            for level, module, names in imps:
                for tgt in resolve_py(p, level, module, names):
                    imp_edges[(p, tgt)] += 1
        else:
            syms, imps = analyze_js(t)
            for spec in imps:
                for tgt in resolve_js(p, spec):
                    if tgt != p:
                        imp_edges[(p, tgt)] += 1
        for s in syms:
            s["path"] = p
            symbols.append(s)
        for pat in ENV_PATTERNS:
            for m in pat.finditer(t):
                env_names[m.group(1)].add(p)
        if not is_test(p):
            for m in HTTP_PATH.finditer(t):
                hp = m.group(1)
                if len(hp) > 4:
                    http_paths[hp].add(p)
        tokens[p] = Counter(IDENT.findall(t))

    # reference graph (Aider-style): referencer file -> defining file, weighted by identifier
    defines = defaultdict(set)
    for s in symbols:
        if not is_test(s["path"]) and len(s["name"]) >= 3 and s["name"].lower() not in STOP_SYMBOLS:
            defines[s["name"]].add(s["path"])
    ref_edges = defaultdict(float)
    sym_in = defaultdict(float)
    n_code = max(1, len(tokens))
    for name in sorted(defines):
        definers = defines[name]
        mul = 1.0
        if name.startswith("_"):
            mul *= 0.1
        if len(definers) > 5:
            mul *= 0.1
        if len(name) >= 8 and ("_" in name.strip("_") or (any(c.isupper() for c in name) and any(c.islower() for c in name))):
            mul *= 3
        # inverse document frequency: names used everywhere (ROOT, build, sha256) carry little signal
        df = sum(1 for f in tokens if name in tokens[f])
        mul *= max(0.02, math.log((n_code + 1) / (df + 1)) / math.log(n_code + 1)) ** 2
        for ref in sorted(tokens):
            if ref in definers:
                continue
            c = tokens[ref].get(name, 0)
            if not c:
                continue
            w = mul * math.sqrt(c) * (0.5 if is_test(ref) else 1.0)
            for d in definers:
                ref_edges[(ref, d)] += w
                sym_in[(d, name)] += w
    edges = defaultdict(float)
    for k, v in ref_edges.items():
        edges[k] += v
    for k, v in imp_edges.items():
        if k[0] in tokens and k[1] in tokens:
            edges[k] += 1.0 + math.sqrt(v)
    nodes = sorted(tokens)
    lines_of = {f["path"]: f["lines"] for f in files}
    pers = {k: (0.2 if is_test(k) else 1.0) * math.sqrt(max(1, lines_of.get(k, 1))) for k in nodes}
    pr = pagerank(nodes, edges, personalization=pers)
    indeg = defaultdict(float)
    for (a, b), w in edges.items():
        indeg[b] += w
    pr_top = max(pr.values()) if pr else 1.0
    in_top = max(indeg.values()) if indeg else 1.0
    # blend: PageRank (centrality) with weighted in-degree (direct use) so a tiny file hanging
    # off one hub cannot outrank the hub's real dependencies
    ranks = {k: 0.5 * pr[k] / pr_top + 0.5 * indeg.get(k, 0.0) / in_top for k in nodes}
    # normalise so the top file is 1.0 (stable, readable)
    top = max(ranks.values()) if ranks else 1.0
    for f in files:
        r = ranks.get(f["path"])
        if r is not None:
            f["rank"] = round(r / top, 4)
    file_rank = {f["path"]: f.get("rank", 0.0) for f in files}
    per_file_in = defaultdict(float)
    for (d, name), w in sym_in.items():
        per_file_in[d] += w
    for s in symbols:
        share = sym_in.get((s["path"], s["name"]), 0.0)
        tot = per_file_in.get(s["path"], 0.0)
        s["rank"] = round(file_rank.get(s["path"], 0.0) * (share / tot if tot else 0.0), 5)
        s["refs"] = round(share, 2)
    symbols.sort(key=lambda s: (s["path"], s["line"]))

    # entry points & metadata
    meta = {"pyproject": None, "package_json": [], "plugin_yaml": [], "docker": [], "workflows": [],
            "codeowners": [], "agent_docs": [], "schemas": [], "docs": []}
    for p, t in texts.items():
        base = p.rsplit("/", 1)[-1]
        depth = p.count("/")
        if base == "pyproject.toml" and depth <= 1:
            try:
                d = tomllib.loads(t)
                proj = d.get("project", {})
                entry = {"path": p, "name": proj.get("name"), "version": proj.get("version"),
                         "requires_python": proj.get("requires-python"),
                         "dependencies": sorted(proj.get("dependencies", []))[:30],
                         "scripts": proj.get("scripts", {}), "entry_points": proj.get("entry-points", {})}
                if depth == 0:
                    meta["pyproject"] = entry
                else:
                    meta.setdefault("sub_pyprojects", []).append(entry)
            except tomllib.TOMLDecodeError:
                pass
        elif base == "package.json" and depth <= 2 and "node_modules" not in p:
            try:
                d = json.loads(t)
                meta["package_json"].append({"path": p, "name": d.get("name"), "version": d.get("version"),
                                             "type": d.get("type"), "main": d.get("main"), "bin": d.get("bin"),
                                             "scripts": {k: v[:140] for k, v in sorted(d.get("scripts", {}).items())}})
            except (json.JSONDecodeError, AttributeError):
                pass
        elif base in ("plugin.yaml", "plugin.yml") and depth <= 2:
            meta["plugin_yaml"].append({"path": p, **yaml_lite_top(t)})
        elif base.startswith("Dockerfile") or re.match(r"(docker-)?compose.*\.ya?ml$", base):
            meta["docker"].append(p)
        elif p.startswith(".github/workflows/") and base.endswith((".yml", ".yaml")):
            meta["workflows"].append({"path": p, **parse_workflow(t)})
        elif base == "CODEOWNERS":
            meta["codeowners"] += [f"{p}: {l.strip()}" for l in t.splitlines() if l.strip() and not l.startswith("#")]
        elif base in ("AGENTS.md", "CLAUDE.md", "ARCHITECTURE.md", "CONTRIBUTING.md", "SECURITY.md"):
            meta["agent_docs"].append(p)
        if re.match(r"openapi.*\.ya?ml$", base):
            ops = []
            mp = re.search(r"^paths:\s*$", t, re.M)
            if mp:
                cur = None
                for line in t[mp.end():].splitlines():
                    if re.match(r"^\S", line):
                        break
                    mm = re.match(r"^  (/[^:]*):\s*$", line)
                    if mm:
                        cur = mm.group(1)
                        continue
                    mm = re.match(r"^    (get|post|put|patch|delete):", line)
                    if mm and cur:
                        ops.append(f"{mm.group(1).upper()} {cur}")
            mv = re.search(r"^\s+version:\s*['\"]?([^'\"\n]+)", t, re.M)
            meta.setdefault("openapi", []).append({"path": p, "version": mv.group(1).strip() if mv else "", "operations": ops})
        if base.endswith(".schema.json") or (p.startswith("schemas/") and base.endswith(".json") and "example" not in p and "fixture" not in p):
            try:
                d = json.loads(t)
                if isinstance(d, dict):
                    meta["schemas"].append({"path": p, "id": d.get("$id", ""), "title": d.get("title", "")[:100] if isinstance(d.get("title"), str) else ""})
            except json.JSONDecodeError:
                pass
        if base.endswith(".md") and depth <= 2 and (p.startswith("docs/") or depth == 0) and not is_test(p):
            m = re.search(r"^#\s+(.+)$", t, re.M)
            meta["docs"].append({"path": p, "title": (m.group(1).strip() if m else "")[:100]})
    for k in ("docker", "agent_docs", "codeowners"):
        meta[k] = sorted(meta[k])
    for k in ("package_json", "plugin_yaml", "workflows", "schemas", "docs"):
        meta[k] = sorted(meta[k], key=lambda x: x["path"])

    # directory tree (depth 2)
    dirs = defaultdict(lambda: {"files": 0, "lines": 0, "langs": Counter()})
    for f in files:
        parts = f["path"].split("/")
        keys = ["."] if len(parts) == 1 else [parts[0]] + (["/".join(parts[:2])] if len(parts) > 2 else [])
        for k in keys:
            dirs[k]["files"] += 1
            dirs[k]["lines"] += f["lines"]
            if f["lang"] not in ("asset", "binary", "other"):
                dirs[k]["langs"][f["lang"]] += f["lines"]
    tree = [{"dir": k, "files": v["files"], "lines": v["lines"],
             "main_lang": (v["langs"].most_common(1)[0][0] if v["langs"] else ""),
             "doc": ""} for k, v in sorted(dirs.items())]
    for t_ in tree:  # use package docstring when present
        init = t_["dir"] + "/__init__.py"
        if init in mdocs:
            t_["doc"] = mdocs[init]

    lang_lines = Counter()
    for f in files:
        if f["lang"] not in ("asset", "binary", "other"):
            lang_lines[f["lang"]] += f["lines"]
    by_src = defaultdict(list)
    for (a, b), w in edges.items():
        if w >= 0.5 and not is_test(a):
            by_src[a].append((round(w, 2), b))
    edge_list = sorted([a, b, w] for a, lst in by_src.items() for w, b in sorted(lst, key=lambda x: (-x[0], x[1]))[:10])
    test_symbol_count = sum(1 for s in symbols if is_test(s["path"]))
    symbols = [{k: v for k, v in s.items() if v not in ("", None)} for s in symbols if not is_test(s["path"])]
    return {
        "schema": "rizzstack.codemap.repo/1",
        "generator_version": GENERATOR_VERSION,
        "name": repo["name"], "url": repo["url"], "sha": repo["sha"], "layer": repo["layer"], "role": repo["role"],
        "stats": {"files": len(files), "code_files": len(tokens), "symbols": len(symbols), "test_symbols": test_symbol_count,
                  "import_edges": len(imp_edges), "lines_by_lang": dict(sorted(lang_lines.items(), key=lambda x: -x[1]))},
        "tree": tree,
        "meta": meta,
        "module_docs": dict(sorted(mdocs.items())),
        "http_paths": {k: sorted(v) for k, v in sorted(http_paths.items())},
        "env_names": {k: sorted(v) for k, v in sorted(env_names.items())},
        "contract_ids": {k: sorted(v) for k, v in sorted(contract_ids.items())},
        "files": files,
        "symbols": symbols,
        "edges": edge_list,
    }


# ------------------------------------------------------------------ renderers
def est_tokens(s: str) -> int:
    return math.ceil(len(s) / 4)


def render_repo_md(ix: dict, budget: int = L2_BUDGET_TOKENS) -> str:
    sha7 = ix["sha"][:7]
    L = [f"<!-- GENERATED by scripts/codemap/build.py v{GENERATOR_VERSION} from {ix['name']}@{sha7}. Do not edit; rerun the script. -->",
         f"# {ix['name']} — generated repo map (L2)", "",
         f"> Pinned at `{sha7}` ({ix['url']}/tree/{ix['sha']}). Regenerate: `python3 scripts/codemap/build.py`. "
         f"Hand-written context: `codemap/L1/{ix['name']}.md`. Full machine index: `codemap/generated/{ix['name']}.json`.", "",
         f"**Role:** {ix['role']}", ""]
    st = ix["stats"]
    langs = ", ".join(f"{k} {v:,}" for k, v in list(st["lines_by_lang"].items())[:6])
    L += [f"**Size:** {st['files']} files, {st['code_files']} code files, {st['symbols']} top-level symbols, "
          f"{st['import_edges']} internal import edges. Lines: {langs}.", ""]
    L += ["## Layout (depth 2)", "", "| dir | files | lines | main | note |", "|---|---:|---:|---|---|"]
    for t in ix["tree"]:
        if t["dir"] == "." or "/" not in t["dir"] or t["files"] >= 8 or t["doc"]:
            L.append(f"| `{t['dir']}` | {t['files']} | {t['lines']:,} | {t['main_lang']} | {t['doc'][:70]} |")
    m = ix["meta"]
    L += ["", "## Entry points and packaging", ""]
    if m.get("pyproject"):
        pp = m["pyproject"]
        L.append(f"- `pyproject.toml`: {pp.get('name')} {pp.get('version') or ''}; scripts: "
                 + (", ".join(f"`{k}` → `{v}`" for k, v in pp.get("scripts", {}).items()) or "none"))
    for pp in m.get("sub_pyprojects", []):
        L.append(f"- `{pp['path']}`: {pp.get('name')}; scripts: " + (", ".join(f"`{k}`" for k in pp.get("scripts", {})) or "none"))
    for pj in m["package_json"]:
        sc = ", ".join(f"`{k}`" for k in list(pj["scripts"])[:14])
        L.append(f"- `{pj['path']}`: {pj.get('name')} {pj.get('version') or ''}; npm scripts: {sc or 'none'}")
    for py in m["plugin_yaml"]:
        bits = ", ".join(f"{k}={v if isinstance(v, str) else '[' + ', '.join(v[:8]) + ']'}" for k, v in py.items()
                         if k in ("name", "version", "kind", "provides_tools", "tools", "hooks", "provides_hooks"))
        L.append(f"- `{py['path']}`: {bits}")
    for oa in m.get("openapi", []):
        L.append(f"- `{oa['path']}` (OpenAPI {oa['version']}): " + ", ".join(f"`{o}`" for o in oa["operations"]))
    if m["docker"]:
        L.append("- containers: " + ", ".join(f"`{d}`" for d in m["docker"][:8]))
    if m["agent_docs"]:
        L.append("- agent/arch docs: " + ", ".join(f"`{d}`" for d in m["agent_docs"]))
    L += ["", "## CI workflows", ""]
    for wf in m["workflows"]:
        jobs = ", ".join(f"`{j['id']}`" for j in wf["jobs"])
        L.append(f"- `{wf['path']}` ({wf['name']}; on: {', '.join(wf['on'])}): {jobs}")
    if not m["workflows"]:
        L.append("- none in `.github/workflows/`")
    if m["codeowners"]:
        L += ["", "CODEOWNERS: " + "; ".join(m["codeowners"][:6])]
    if ix["http_paths"]:
        L += ["", "## HTTP paths referenced in non-test code", ""]
        items = sorted(ix["http_paths"].items(), key=lambda kv: (-len(kv[1]), kv[0]))
        shown = items[:45]
        L.append(", ".join(f"`{k}`" for k, _ in sorted(shown)))
        if len(items) > len(shown):
            L.append(f"… +{len(items) - len(shown)} more in the JSON index (`http_paths`).")
    if ix["contract_ids"]:
        L += ["", "## Contract / schema ids mentioned", ""]
        L.append(", ".join(f"`{k}` ({len(v)})" for k, v in sorted(ix["contract_ids"].items(), key=lambda kv: (-len(kv[1]), kv[0]))[:30]))
    if ix["env_names"]:
        L += ["", "## Environment variable names read (names only)", ""]
        L.append(", ".join(f"`{k}`" for k in list(ix["env_names"])[:60]) + (" …" if len(ix["env_names"]) > 60 else ""))
    header = "\n".join(L)
    # ranked symbol map, Aider-style, filling the remaining budget
    body = ["", "## Ranked map (most-referenced first)", "",
            "Files ranked by PageRank over import + identifier-reference edges; symbols under each file are its "
            "top-level definitions ordered by incoming references. Tests are excluded.", ""]
    by_file = defaultdict(list)
    for s in ix["symbols"]:
        by_file[s["path"]].append(s)
    ranked = sorted((f for f in ix["files"] if "rank" in f and not is_test(f["path"])), key=lambda f: (-f["rank"], f["path"]))
    used = est_tokens(header) + est_tokens("\n".join(body))
    shown = 0
    for f in ranked:
        syms = sorted(by_file.get(f["path"], []), key=lambda s: (-s.get("rank", 0), s["line"]))[:8]
        doc = ix["module_docs"].get(f["path"], "")
        block = [f"`{f['path']}` ({f['lines']} lines, rank {f['rank']:.3f})" + (f" — {doc}" if doc else "")]
        for s in syms:
            sg, dc = s.get("sig", ""), s.get("doc", "")
            sig = f"({sg})" if s["kind"] == "function" else (f" [{sg}]" if sg else "")
            block.append(f"  - {s['kind']} `{s['name']}`{sig} :{s['line']}" + (f" — {dc}" if dc else ""))
        cost = est_tokens("\n".join(block)) + 1
        if used + cost > budget - 40:
            break
        body += block
        used += cost
        shown += 1
    if shown < len(ranked):
        body.append(f"\n… +{len(ranked) - shown} more files not shown (budget {budget} tokens). See `codemap/generated/{ix['name']}.json`.")
    return scrub(header + "\n".join(body) + "\n")


def build_index(repos: list[dict], sources: dict) -> dict:
    out = {"schema": "rizzstack.codemap.index/1", "generator_version": GENERATOR_VERSION,
           "how_to_read": "L0 codemap/README.md -> L1 codemap/L1/<repo>.md -> L2 codemap/generated/<repo>.md|json",
           "repos": {}}
    for ix in repos:
        top_files = sorted((f for f in ix["files"] if "rank" in f and not is_test(f["path"])), key=lambda f: -f["rank"])[:20]
        top_syms = sorted((s for s in ix["symbols"] if s.get("exported")), key=lambda s: (-s.get("rank", 0), s["path"], s["line"]))[:25]
        m = ix["meta"]
        out["repos"][ix["name"]] = {
            "url": ix["url"], "sha": ix["sha"], "layer": ix["layer"], "role": ix["role"], "stats": ix["stats"],
            "top_files": [{"path": f["path"], "rank": f["rank"], "lines": f["lines"], "doc": ix["module_docs"].get(f["path"], "")} for f in top_files],
            "top_symbols": [{"name": s["name"], "kind": s["kind"], "path": s["path"], "line": s["line"], "doc": s.get("doc", "")} for s in top_syms],
            "entry_points": {"pyproject": m.get("pyproject"), "package_json": [{k: p[k] for k in ("path", "name", "bin")} for p in m["package_json"]],
                             "plugin_yaml": [{k: v for k, v in p.items() if k in ("path", "name", "version", "provides_tools", "tools")} for p in m["plugin_yaml"]],
                             "docker": m["docker"], "openapi": m.get("openapi", [])},
            "ci": [{"path": w["path"], "name": w["name"], "on": w["on"], "jobs": [j["id"] for j in w["jobs"]]} for w in m["workflows"]],
            "codeowners": m["codeowners"],
            "schemas": m["schemas"],
            "contract_ids": {k: len(v) for k, v in ix["contract_ids"].items()},
            "http_path_count": len(ix["http_paths"]),
        }
    # cross-repo: contract ids and http paths that appear in more than one repo
    cid = defaultdict(dict)
    hp = defaultdict(dict)
    for ix in repos:
        for k, v in ix["contract_ids"].items():
            cid[k][ix["name"]] = len(v)
        for k, v in ix["http_paths"].items():
            hp[k][ix["name"]] = v[:3]
    out["cross_repo"] = {
        "contract_ids": {k: v for k, v in sorted(cid.items())},
        "shared_http_paths": {k: v for k, v in sorted(hp.items()) if len(v) > 1},
    }
    return out


def render_cross_md(index: dict) -> str:
    names = list(index["repos"])
    L = [f"<!-- GENERATED by scripts/codemap/build.py v{GENERATOR_VERSION}. Do not edit; rerun the script. -->",
         "# Cross-repo matrix (generated)", "",
         "Pinned: " + ", ".join(f"{n}@`{index['repos'][n]['sha'][:7]}`" for n in names), "",
         "## Contract and schema ids: files mentioning each id, per repo", "",
         "Read this as \"who speaks which contract\". An id seen in only one repo is internal or not yet adopted.", "",
         "| id | " + " | ".join(names) + " |", "|---|" + "---:|" * len(names)]
    for k, v in index["cross_repo"]["contract_ids"].items():
        if sum(v.values()) < 2 and len(v) < 2:
            continue
        L.append(f"| `{k}` | " + " | ".join(str(v.get(n, "")) for n in names) + " |")
    L += ["", "## HTTP paths referenced in more than one repo", "",
          "Usually a client in one repo calling a server in another. Paths are string literals found in non-test code.", ""]
    shared = index["cross_repo"]["shared_http_paths"]
    if not shared:
        L.append("none found")
    for k, v in shared.items():
        L.append(f"- `{k}`: " + "; ".join(f"{r} (`{', '.join(fs)}`)" for r, fs in v.items()))
    L += ["", "## CI jobs by repo", ""]
    for n in names:
        jobs = sorted({j for w in index["repos"][n]["ci"] for j in w["jobs"]})
        L.append(f"- **{n}**: " + (", ".join(f"`{j}`" for j in jobs) or "no workflows"))
    return scrub("\n".join(L) + "\n")


def dump_json(path: Path, obj) -> None:
    """Diff-friendly JSON: one top-level key per line, one list item per line."""
    obj = scrub_obj(obj)
    c = lambda v: json.dumps(v, ensure_ascii=False, separators=(",", ":"))
    out = ["{"]
    keys = list(obj)
    for i, k in enumerate(keys):
        v = obj[k]
        tail = "," if i < len(keys) - 1 else ""
        if isinstance(v, list) and v:
            out.append(f" {c(k)}:[")
            out += [f"  {c(x)}" + ("," if j < len(v) - 1 else "") for j, x in enumerate(v)]
            out.append(" ]" + tail)
        elif isinstance(v, dict) and v and k in ("repos", "http_paths", "env_names", "contract_ids", "module_docs", "cross_repo"):
            out.append(f" {c(k)}:{{")
            ks = list(v)
            out += [f"  {c(kk)}:{c(v[kk])}" + ("," if j < len(ks) - 1 else "") for j, kk in enumerate(ks)]
            out.append(" }" + tail)
        else:
            out.append(f" {c(k)}:{c(v)}{tail}")
    out.append("}")
    path.write_text("\n".join(out) + "\n")


def generate(out_dir: Path, workdir: Path, overrides: dict) -> None:
    src = load_sources()
    out_dir.mkdir(parents=True, exist_ok=True)
    repos = []
    for r in src["repos"]:
        p = fetch(r, workdir, overrides)
        print(f"indexing {r['name']}@{r['sha'][:7]} ...", file=sys.stderr)
        ix = index_repo(r, p, src.get("exclude_globs", []))
        dump_json(out_dir / f"{r['name']}.json", ix)
        (out_dir / f"{r['name']}.md").write_text(render_repo_md(ix))
        repos.append(ix)
    index = build_index(repos, src)
    dump_json(out_dir / "index.json", index)
    (out_dir / "cross-repo.md").write_text(render_cross_md(index))


# ------------------------------------------------------------------ checks
DOC_GLOBS = ["AGENTS.md", "CLAUDE.md", "llms.txt", "codemap/*.md", "codemap/L1/*.md", "codemap/decisions/*.md"]
BACKTICK = re.compile(r"`([^`\s]+)`")


def verify_docs() -> list[str]:
    problems = []
    src = load_sources()
    names = [r["name"] for r in src["repos"]]
    known = {}
    for n in names:
        jp = OUT / f"{n}.json"
        if not jp.exists():
            problems.append(f"missing generated index {jp.relative_to(ROOT)}; run build.py")
            continue
        paths = {f["path"] for f in json.loads(jp.read_text())["files"]}
        dirs = set()
        for p in paths:
            parts = p.split("/")
            for i in range(1, len(parts)):
                dirs.add("/".join(parts[:i]))
        known[n] = (paths, dirs)
    docs = []
    for g in DOC_GLOBS:
        docs += sorted(ROOT.glob(g))
    local_files = set(sh("git", "ls-files", cwd=ROOT).splitlines()) | {str(p.relative_to(ROOT)) for p in ROOT.rglob("*") if p.is_file() and ".git/" not in str(p)}
    for d in docs:
        rel = str(d.relative_to(ROOT))
        text = d.read_text()
        if "GENERATED by scripts/codemap" in text[:200]:
            continue
        for lineno, line in enumerate(text.splitlines(), 1):
            for m in BACKTICK.finditer(line):
                tok = m.group(1).rstrip(".,:;)").split("#")[0].split(":")[0]
                head = tok.split("/", 1)[0]
                if head in known and "/" in tok:
                    rest = tok.split("/", 1)[1].rstrip("/")
                    if any(ch in rest for ch in "*{}<>"):
                        continue
                    paths, dirs = known[head]
                    if rest not in paths and rest not in dirs:
                        problems.append(f"{rel}:{lineno}: `{tok}` not found in {head}@pinned")
                elif tok.startswith(("codemap/", "scripts/codemap/", "docs/")) and not any(ch in tok for ch in "*{}<>"):
                    t = tok.rstrip("/")
                    if t not in local_files and not (ROOT / t).exists():
                        problems.append(f"{rel}:{lineno}: `{tok}` not found in this repo")
        for pat, rep in SCRUB:
            if pat.search(text):
                problems.append(f"{rel}: contains a pattern the scrubber would redact ({rep})")
    for g, limit in BUDGETS.items():
        for f in sorted(ROOT.glob(g)):
            t = est_tokens(f.read_text())
            if t > limit:
                problems.append(f"{f.relative_to(ROOT)}: ~{t} tokens exceeds budget {limit}")
    return problems


def budget_report() -> str:
    rows = []
    for g in ["AGENTS.md", "llms.txt", "codemap/README.md", "codemap/L1/*.md", "codemap/*.md", "codemap/generated/*.md"]:
        for f in sorted(ROOT.glob(g)):
            rel = str(f.relative_to(ROOT))
            if any(r[0] == rel for r in rows):
                continue
            t = f.read_text()
            rows.append((rel, len(t), est_tokens(t)))
    return "\n".join(f"{t:>7} tok {c:>8} chars  {r}" for r, c, t in rows)


def upstream() -> int:
    src = load_sources()
    stale = 0
    for r in src["repos"]:
        out = sh("git", "ls-remote", "--symref", r["url"] + ".git", "HEAD", check=False)
        head = ""
        for line in out.splitlines():
            if line.endswith("\tHEAD") and not line.startswith("ref:"):
                head = line.split("\t")[0]
        state = "current" if head == r["sha"] else ("UNKNOWN" if not head else "STALE")
        stale += state == "STALE"
        print(f"{r['name']:<22} pinned {r['sha'][:7]}  upstream {head[:7] or '?':<7}  {state}")
    return stale


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--check", action="store_true", help="regenerate into a temp dir and fail if committed output differs")
    ap.add_argument("--verify-docs", action="store_true", help="check repo/path references and token budgets only")
    ap.add_argument("--upstream", action="store_true", help="compare pinned SHAs with upstream default branches")
    ap.add_argument("--strict", action="store_true", help="with --upstream: exit 3 when any pin is stale")
    ap.add_argument("--budgets", action="store_true", help="print token estimates for every map layer")
    ap.add_argument("--slice", nargs="+", metavar="PATH", help="print the map rows/bullets that mention these repo-prefixed paths")
    ap.add_argument("--impact", nargs="+", metavar="PATH", help="print importers (depth 2) and matching test files as JSON")
    ap.add_argument("--max-tokens", type=int, default=1500, help="with --slice: token budget for the output (default 1500)")
    ap.add_argument("--workdir", default=os.environ.get("CODEMAP_WORKDIR", str(Path(tempfile.gettempdir()) / "rizzstack-codemap-src")))
    ap.add_argument("--src", action="append", default=[], metavar="NAME=PATH", help="reuse an existing checkout at the pinned SHA")
    a = ap.parse_args()
    overrides = dict(s.split("=", 1) for s in a.src)
    if a.slice:
        import slice as slice_mod
        print(slice_mod.slice_map(a.slice, a.max_tokens))
        return 0
    if a.impact:
        import slice as slice_mod
        print(json.dumps(slice_mod.impact(a.impact)))
        return 0
    if a.upstream:
        n = upstream()
        print(f"{n} stale pin(s)" if n else "all pins current")
        return 3 if (n and a.strict) else 0
    if a.budgets:
        print(budget_report())
        return 0
    if a.verify_docs:
        probs = verify_docs()
        print("\n".join(probs) if probs else "docs OK: all repo/path references resolve at the pinned SHAs; budgets met")
        return 1 if probs else 0
    workdir = Path(a.workdir)
    workdir.mkdir(parents=True, exist_ok=True)
    if a.check:
        with tempfile.TemporaryDirectory() as td:
            generate(Path(td), workdir, overrides)
            diff = subprocess.run(["diff", "-rq", str(OUT), td], capture_output=True, text=True)
            if diff.returncode != 0:
                print("generated map is out of date with sources.json / build.py:\n" + diff.stdout.replace(td, "<regenerated>"))
                print("fix: python3 scripts/codemap/build.py && commit codemap/generated/")
                return 1
        probs = verify_docs()
        if probs:
            print("\n".join(probs))
            return 1
        print("codemap OK: generated layer matches pinned SHAs; docs references and budgets pass")
        return 0
    generate(OUT, workdir, overrides)
    print(f"wrote {OUT.relative_to(ROOT)}/", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
