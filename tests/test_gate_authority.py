"""Gate authority (S1): who may approve, which gate runs, what is T1, and stale bases.

An independent approval counts only when GitHub says the review was submitted by the
authenticated reviewer app login ``rizzstack-reviewer[bot]``. The review text never
names an identity: the coordinator app, the owner's token and any other account can
post the canonical line, and none of them is an approval. A rejection still governs
whatever account posted it.

The gate GitHub runs must be the base branch's copy: a ``pull_request_target``
workflow checks out the base commit, fetches the pull request head as git objects
only, and runs nothing from the pull request.  The workflow is parsed by a strict
YAML-subset reader and compared with the one allowed structure; must-fail fixtures
(including the demonstrated pwn request) are refused.  A review re-runs that base
gate, and the lane counts a ``gate-authority`` check only from that workflow's
``pull_request_target`` run, never from a same-named job or a commit status.

An owner attestation (``APPROVE (owner)``) counts only from the owner login.

The T1 allowlist keeps auth, CI, subprocess, network, dependency, test-infra and gate
surfaces out of T1; a file kind the gate does not know is T3.

The merge lane refuses a pull request whose branch is behind its base.

Run: python3 -m pytest -q -p no:cacheprovider tests/test_gate_authority.py
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import re
import shutil
import subprocess
import tempfile
import unittest
import urllib.parse
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
GATE_SCRIPT = ROOT / ".github" / "scripts" / "lease_tamper.py"
LANE_SCRIPT = ROOT / "scripts" / "merge_lane_v5.py"
WORKFLOW = ROOT / ".github" / "workflows" / "gate-authority.yml"
RERUN_WORKFLOW = ROOT / ".github" / "workflows" / "review-rerun.yml"
WORKFLOW_FIXTURES = ROOT / "tests" / "fixtures" / "workflows"
DRILL_SCRIPT = ROOT / ".github" / "scripts" / "gate_drill.py"
NEW_T3_REASONS = ("capability_subprocess", "capability_network", "dependency_manifest", "test_infra",
                  "ci_config", "auth_surface", "unknown_kind")

HEAD = "1a1d032f98691fb4121d5b9673b36562c77a5627"
BASE = "316087f7da8ea01a206b4c6417c2a1599e84b35c"
LINE = "APPROVE (independent review) at " + HEAD
REVIEWER = "rizzstack-reviewer[bot]"
COORDINATOR = "rizzstack-coordinator[bot]"
OWNER = "mojomast"


def _load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


GATE = _load(GATE_SCRIPT, "lease_tamper_authority_under_test")
LANE = _load(LANE_SCRIPT, "merge_lane_authority_under_test")


def _review(login, body=LINE, state="COMMENTED", commit=HEAD):
    review = {"state": state, "body": body, "commit_id": commit,
              "submitted_at": "2026-11-27T22:00:00Z"}
    if login is not None:
        review["user"] = {"login": login}
    return review


def _accepted(reviews):
    review, _match, _refusal = GATE.independent_review(None, reviews, BASE, HEAD)
    return review


# --------------------------------------------------------------- review identity
class ReviewIdentityTest(unittest.TestCase):
    def test_reviewer_app_login_is_the_only_independent_approval(self):
        self.assertIsNotNone(_accepted([_review(REVIEWER)]))
        self.assertIsNotNone(_accepted([_review(REVIEWER, body="", state="APPROVED")]))

    def test_coordinator_app_cannot_approve(self):
        self.assertIsNone(_accepted([_review(COORDINATOR)]))
        self.assertIsNone(_accepted([_review(COORDINATOR, body="", state="APPROVED")]))

    def test_owner_token_cannot_approve(self):
        self.assertIsNone(_accepted([_review(OWNER)]))
        self.assertIsNone(_accepted([_review(OWNER, body="", state="APPROVED")]))

    def test_text_naming_the_reviewer_is_not_identity(self):
        body = "APPROVE (independent review — rizzstack-reviewer[bot], model family kimi) at " + HEAD
        self.assertIsNone(_accepted([_review("someone-else", body=body)]))
        self.assertIsNone(_accepted([_review(None, body=body)]))
        self.assertIsNone(_accepted([_review("rizzstack-reviewer", body=body)]))  # not the [bot] login

    def test_rejection_from_any_login_still_governs(self):
        rejection = _review(OWNER, body="CHANGES REQUESTED (finding) at " + HEAD)
        rejection["submitted_at"] = "2026-11-27T22:05:00Z"
        self.assertIsNone(_accepted([_review(REVIEWER), rejection]))

    def test_identity_rule_lives_in_the_shared_policy_block(self):
        for module in (GATE, LANE):
            self.assertEqual(module.REVIEWER_LOGIN, REVIEWER)
            self.assertTrue(module.review_identity_ok({"user": {"login": REVIEWER}}))
            self.assertTrue(module.review_identity_ok({"author": REVIEWER}))
            for login in (COORDINATOR, OWNER, "", None):
                self.assertFalse(module.review_identity_ok({"user": {"login": login}}), login)
            self.assertFalse(module.review_identity_ok({}))

    def test_lane_accepts_only_the_reviewer_app(self):
        def facts(author):
            return {
                "pr": {"state": "OPEN", "is_draft": False, "mergeable": True,
                       "merge_state_status": "CLEAN", "head": HEAD, "files": ["docs/readme.md"]},
                "class": LANE.NON_SLICE,
                "reviews": [{"state": "COMMENTED", "commit_id": HEAD, "author": author, "body": LINE}],
                "checks": [{"name": "ci-gate", "status": "completed", "conclusion": "success", "head": HEAD}],
                "required_checks": ["ci-gate"],
                "verify": {"records": [], "compare": {}, "provenance": {}},
                "lease_tamper": {"ran": True, "ok": True, "reason": "gate_ok",
                                 "output_tail": "lease-tamper: class=non-slice TIER=T3"},
                "owner_held": False,
            }
        self.assertTrue(LANE.decide(facts(REVIEWER))["merge"])
        for author in (COORDINATOR, OWNER, "reviewer"):
            verdict = LANE.decide(facts(author))
            self.assertFalse(verdict["merge"], author)


# --------------------------------------------------------------- stale base
class StaleBaseTest(unittest.TestCase):
    def test_lane_refuses_a_branch_behind_its_base(self):
        facts = {
            "pr": {"state": "OPEN", "is_draft": False, "mergeable": True,
                   "merge_state_status": "BEHIND", "head": HEAD, "files": ["docs/readme.md"]},
            "class": LANE.NON_SLICE,
            "reviews": [{"state": "COMMENTED", "commit_id": HEAD, "author": REVIEWER, "body": LINE}],
            "checks": [{"name": "ci-gate", "status": "completed", "conclusion": "success", "head": HEAD}],
            "required_checks": ["ci-gate"],
            "verify": {"records": [], "compare": {}, "provenance": {}},
            "lease_tamper": {"ran": True, "ok": True, "reason": "gate_ok",
                             "output_tail": "lease-tamper: class=non-slice TIER=T3"},
            "owner_held": False,
        }
        verdict = LANE.decide(facts)
        self.assertFalse(verdict["merge"])
        self.assertEqual(verdict["reason"], "merge_state_behind")
        self.assertNotIn("BEHIND", LANE.MERGE_STATES)


# --------------------------------------------------------------- strict YAML subset
class StrictYamlError(ValueError):
    pass


_YAML_FORBIDDEN_START = ("&", "*", "!", "{", "?", "%", "@", "`", "|+", ">", "<<")


def _yaml_scalar(text, where):
    text = text.strip()
    if not text:
        return None
    if text.startswith(_YAML_FORBIDDEN_START) or text in ("---", "..."):
        raise StrictYamlError("%s: unsupported YAML construct %r" % (where, text))
    if text.startswith("["):
        if not text.endswith("]"):
            raise StrictYamlError("%s: unterminated flow sequence" % where)
        inner = text[1:-1].strip()
        if not inner:
            return []
        if any(c in inner for c in "[]{}&*!"):
            raise StrictYamlError("%s: nested flow collection" % where)
        return [_yaml_scalar(part, where) for part in inner.split(",")]
    if text[0] in "\"'":
        quote = text[0]
        if len(text) < 2 or text[-1] != quote:
            raise StrictYamlError("%s: unterminated quoted scalar" % where)
        body = text[1:-1]
        if quote == "'":
            if "'" in body.replace("''", ""):
                raise StrictYamlError("%s: bad single-quoted scalar" % where)
            return body.replace("''", "'")
        if "\\" in body or '"' in body:
            raise StrictYamlError("%s: escapes are not supported" % where)
        return body
    if text in ("true", "false"):
        return text == "true"
    if text in ("null", "~", "yes", "no", "on", "off", "True", "False", "Yes", "No", "On", "Off"):
        raise StrictYamlError("%s: ambiguous YAML scalar %r" % (where, text))
    if text.isdigit():
        return int(text)
    if ": " in text or text.endswith(":") or " #" in text:
        raise StrictYamlError("%s: ambiguous plain scalar %r" % (where, text))
    return text


def _yaml_strip_comment(line):
    if line.lstrip().startswith("#"):
        return ""
    quote = None
    for i, ch in enumerate(line):
        if quote:
            if ch == quote:
                quote = None
        elif ch in "\"'":
            quote = ch
        elif ch == "#" and (i == 0 or line[i - 1] == " "):
            return line[:i].rstrip()
    return line.rstrip()


def strict_yaml(text):
    """Parse the block-style YAML subset GitHub workflows here are written in.

    Anything outside the subset (flow mappings, anchors, aliases, tags, merge keys,
    folded or keep-chomp scalars, tabs, duplicate keys, multiple documents, YAML 1.1
    booleans) is refused, so a workflow cannot hide structure from this parser.
    """
    if "\t" in text:
        raise StrictYamlError("tabs are not allowed")
    raw = text.splitlines()

    def next_content(index):
        while index < len(raw):
            stripped = _yaml_strip_comment(raw[index])
            if stripped.strip():
                return index, len(stripped) - len(stripped.lstrip(" ")), stripped.strip()
            index += 1
        return index, -1, None

    def block_scalar(index, parent_indent, chomp):
        out = []
        body_indent = None
        while index < len(raw):
            line = raw[index]
            if line.strip():
                ind = len(line) - len(line.lstrip(" "))
                if ind <= parent_indent:
                    break
                if body_indent is None:
                    body_indent = ind
                if ind < body_indent:
                    raise StrictYamlError("line %d: block scalar dedent" % (index + 1))
                out.append(line[body_indent:])
            else:
                out.append("")
            index += 1
        while out and out[-1] == "":
            out.pop()
        value = "\n".join(out)
        if chomp == "|":
            value += "\n"
        return value, index

    def parse_value_after_colon(rest, index, indent, where):
        rest = rest.strip()
        if rest in ("|", "|-"):
            return block_scalar(index + 1, indent, rest)
        if rest == "":
            nxt, ind, content = next_content(index + 1)
            if content is None or ind <= indent and not (ind == indent and content.startswith("- ")):
                raise StrictYamlError("%s: empty value" % where)
            return parse_block(nxt, ind)
        return _yaml_scalar(rest, where), index + 1

    def split_key(content, where):
        if content.startswith(("\"", "'")):
            raise StrictYamlError("%s: quoted keys are not supported" % where)
        if ":" not in content:
            raise StrictYamlError("%s: expected 'key: value'" % where)
        key, rest = content.split(":", 1)
        if rest and not rest.startswith(" "):
            raise StrictYamlError("%s: expected 'key: value'" % where)
        key = key.strip()
        if not key or key.startswith(_YAML_FORBIDDEN_START) or " " in key:
            raise StrictYamlError("%s: bad key %r" % (where, key))
        return key, rest

    def parse_mapping(index, indent, first=None):
        out = {}
        while True:
            index, ind, content = next_content(index)
            if content is None or ind < indent:
                return out, index
            if ind > indent:
                raise StrictYamlError("line %d: unexpected indent" % (index + 1))
            if content.startswith("- "):
                return out, index
            where = "line %d" % (index + 1)
            key, rest = split_key(content, where)
            if key in out:
                raise StrictYamlError("%s: duplicate key %r" % (where, key))
            out[key], index = parse_value_after_colon(rest, index, indent, where)

    def parse_sequence(index, indent):
        out = []
        while True:
            index, ind, content = next_content(index)
            if content is None or ind < indent:
                return out, index
            if ind > indent or not (content.startswith("- ") or content == "-"):
                if ind == indent:
                    return out, index
                raise StrictYamlError("line %d: unexpected indent in sequence" % (index + 1))
            where = "line %d" % (index + 1)
            item = content[2:] if content.startswith("- ") else ""
            item_indent = ind + 2
            if not item.strip():
                raise StrictYamlError("%s: empty sequence item" % where)
            head = _yaml_strip_comment(item)
            if ":" in head and not head.strip().startswith(("\"", "'", "[")) and (
                    head.rstrip().endswith(":") or ": " in head):
                # A mapping item: re-read this line as the first key at item_indent.
                raw[index] = " " * item_indent + item
                value, index = parse_mapping(index, item_indent)
                out.append(value)
            else:
                out.append(_yaml_scalar(head, where))
                index += 1

    def parse_block(index, indent):
        index, ind, content = next_content(index)
        if content is None:
            raise StrictYamlError("empty block")
        if content.startswith("- ") or content == "-":
            return parse_sequence(index, ind)
        return parse_mapping(index, ind)

    start, ind, content = next_content(0)
    if content is None:
        raise StrictYamlError("empty document")
    if ind != 0:
        raise StrictYamlError("document must start at column 0")
    doc, end = parse_mapping(start, 0)
    end, _, rest = next_content(end)
    if rest is not None:
        raise StrictYamlError("line %d: trailing content" % (end + 1))
    return doc


def yaml_cross_check(text, doc):
    """When PyYAML is installed, it must read the same document (YAML 1.1 'on' -> True)."""
    try:
        import yaml  # noqa: PLC0415
    except ImportError:
        return None

    def norm(value):
        if isinstance(value, dict):
            return {("on" if k is True else k): norm(v) for k, v in value.items()}
        if isinstance(value, list):
            return [norm(v) for v in value]
        return value

    return norm(yaml.safe_load(text)) == doc


# --------------------------------------------------------------- base-pinned gate workflow
GATE_EVENT_TYPES = {"opened", "synchronize", "reopened", "edited", "ready_for_review"}
#: A retarget (edited) and a push (synchronize) must each re-run the gate from the new base.
GATE_REQUIRED_TYPES = {"opened", "synchronize", "reopened", "edited"}
CHECKOUT_RE = re.compile(r"actions/checkout@[0-9a-f]{40}")
BASE_REF = "${{ github.event.pull_request.base.sha }}"
PR_NUMBER = "${{ github.event.pull_request.number }}"
TOKEN = "${{ github.token }}"
FETCH_RUN = re.compile(r'git fetch --no-tags( --depth=[1-9][0-9]{0,3})? origin "refs/pull/\$\{PR_NUMBER\}/head"\n?')
JUDGE_RUN = re.compile(r"python3 \.github/scripts/lease_tamper\.py\n?")
FORBIDDEN_KEYS = {"working-directory", "shell", "defaults", "container", "services",
                  "continue-on-error", "strategy", "outputs", "if", "needs", "secrets"}
FORBIDDEN_TEXT = ("GITHUB_ENV", "GITHUB_PATH", "GITHUB_OUTPUT", "secrets.", "ACTIONS_")


def _walk(value, path=""):
    if isinstance(value, dict):
        for key, item in value.items():
            yield path + "/" + str(key), key, item
            yield from _walk(item, path + "/" + str(key))
    elif isinstance(value, list):
        for index, item in enumerate(value):
            yield from _walk(item, "%s[%d]" % (path, index))


def gate_workflow_violations(text):
    """Every way ``text`` differs from the one allowed base-pinned gate workflow ([] = allowed)."""
    try:
        doc = strict_yaml(text)
    except StrictYamlError as exc:
        return ["unparseable: %s" % exc]
    if yaml_cross_check(text, doc) is False:
        return ["yaml_divergence"]
    bad = []
    for where, key, item in _walk(doc):
        if key in FORBIDDEN_KEYS:
            bad.append("forbidden_key %s" % where)
        if isinstance(item, str) and any(word in item for word in FORBIDDEN_TEXT):
            bad.append("forbidden_text %s" % where)
        if key == "run" and isinstance(item, str) and "${{" in item:
            bad.append("expression_in_run %s" % where)
        if key == "permissions" and where != "/permissions":
            bad.append("job_permissions %s" % where)
    if set(doc) != {"name", "on", "permissions", "jobs"}:
        bad.append("top_level_keys %s" % sorted(doc))
    if doc.get("name") != "gate-authority":
        bad.append("workflow_name")
    on = doc.get("on")
    if not (isinstance(on, dict) and set(on) == {"pull_request_target"}
            and isinstance(on["pull_request_target"], dict)
            and set(on["pull_request_target"]) == {"types"}
            and isinstance(on["pull_request_target"]["types"], list)
            and on["pull_request_target"]["types"]
            and set(on["pull_request_target"]["types"]) <= GATE_EVENT_TYPES):
        bad.append("trigger_not_pull_request_target_only")
    elif not GATE_REQUIRED_TYPES <= set(on["pull_request_target"]["types"]):
        bad.append("trigger_types_incomplete")
    if doc.get("permissions") != {"contents": "read"}:
        bad.append("permissions_not_contents_read")
    jobs = doc.get("jobs")
    if not (isinstance(jobs, dict) and set(jobs) == {"gate-authority"}):
        return bad + ["jobs_not_exactly_gate_authority"]
    job = jobs["gate-authority"]
    if not isinstance(job, dict):
        return bad + ["job_not_a_mapping"]
    if set(job) != {"name", "runs-on", "timeout-minutes", "steps"}:
        bad.append("job_keys %s" % sorted(job))
    if job.get("name") != "gate-authority":
        bad.append("job_name")
    if not (isinstance(job.get("runs-on"), str) and re.fullmatch(r"ubuntu-[0-9.]+", job["runs-on"])):
        bad.append("runs_on")
    if not (type(job.get("timeout-minutes")) is int and 1 <= job["timeout-minutes"] <= 30):
        bad.append("timeout")
    steps = job.get("steps")
    if not (isinstance(steps, list) and len(steps) == 3 and all(isinstance(s, dict) for s in steps)):
        return bad + ["steps_not_exactly_checkout_fetch_judge"]
    checkout, fetch, judge = steps
    if (set(checkout) != {"name", "uses", "with"}
            or not (isinstance(checkout["uses"], str) and CHECKOUT_RE.fullmatch(checkout["uses"]))
            or checkout["with"] != {"ref": BASE_REF, "persist-credentials": False}):
        bad.append("checkout_not_pinned_base_without_credentials")
    if (set(fetch) != {"name", "env", "run"} or fetch["env"] != {"PR_NUMBER": PR_NUMBER}
            or not (isinstance(fetch["run"], str) and FETCH_RUN.fullmatch(fetch["run"]))):
        bad.append("fetch_step_not_allowlisted")
    if (set(judge) != {"name", "env", "run"} or judge["env"] not in ({"GH_TOKEN": TOKEN}, {"GITHUB_TOKEN": TOKEN})
            or not (isinstance(judge["run"], str) and JUDGE_RUN.fullmatch(judge["run"]))):
        bad.append("judge_step_not_allowlisted")
    return bad


class BasePinnedGateWorkflowTest(unittest.TestCase):
    def text(self):
        self.assertTrue(WORKFLOW.is_file(), "missing .github/workflows/gate-authority.yml")
        return WORKFLOW.read_text(encoding="utf-8")

    def test_workflow_is_exactly_the_base_pinned_gate(self):
        self.assertEqual(gate_workflow_violations(self.text()), [])

    def test_workflow_parses_strictly_and_agrees_with_pyyaml(self):
        text = self.text()
        doc = strict_yaml(text)
        self.assertIn(yaml_cross_check(text, doc), (True, None))

    def test_must_fail_workflows_are_refused(self):
        fixtures = sorted(WORKFLOW_FIXTURES.glob("*.yml"))
        self.assertGreaterEqual(len(fixtures), 14)
        for fixture in fixtures:
            self.assertNotEqual(gate_workflow_violations(fixture.read_text(encoding="utf-8")), [],
                                fixture.name)

    def test_grokchat_pwn_request_is_refused(self):
        bad = gate_workflow_violations((WORKFLOW_FIXTURES / "pwn_request.yml").read_text(encoding="utf-8"))
        self.assertTrue(bad and bad[0].startswith("unparseable"), bad)  # flow-style permissions
        bad = gate_workflow_violations(
            (WORKFLOW_FIXTURES / "pwn_request_block_permissions.yml").read_text(encoding="utf-8"))
        self.assertTrue(any(item.startswith("job_permissions") for item in bad), bad)
        self.assertTrue(any(item.startswith("expression_in_run") for item in bad), bad)
        self.assertIn("steps_not_exactly_checkout_fetch_judge", bad)

    def test_gate_trigger_types_include_edited(self):
        # without `edited`, retargeting the PR's base fires no new run (the retarget attack)
        text = (WORKFLOW_FIXTURES / "types_without_edited.yml").read_text(encoding="utf-8")
        self.assertEqual(gate_workflow_violations(text), ["trigger_types_incomplete"])
        self.assertTrue(WORKFLOW.is_file(), "missing .github/workflows/gate-authority.yml")
        doc = strict_yaml(WORKFLOW.read_text(encoding="utf-8"))
        self.assertLessEqual(GATE_REQUIRED_TYPES, set(doc["on"]["pull_request_target"]["types"]))
        self.assertIn("edited", doc["on"]["pull_request_target"]["types"])

    def test_strict_parser_refuses_constructs_that_hide_structure(self):
        for text in ("a: {b: c}\n", "a: &x 1\nb: *x\n", "a: !tag 1\n", "a: 1\na: 2\n",
                     "a:\n\tb: 1\n", "on: yes\n", "a: >\n  folded\n", "---\na: 1\n",
                     "a: \"esc\\u0041\"\n", "<<: {}\n"):
            with self.assertRaises(StrictYamlError, msg=repr(text)):
                strict_yaml(text)

    def test_gate_judges_pull_request_target_like_a_pull_request(self):
        self.assertIn("pull_request_target", GATE.PULL_REQUEST_EVENTS)


# --------------------------------------------------------------- re-judge after a review
FAKE_GH = r'''#!/usr/bin/env python3
"""A fake `gh api` serving canned workflow runs, filtered the way GitHub filters them."""
import json, os, sys, urllib.parse
state = json.load(open(os.environ["FAKE_GH_STATE"]))
args = sys.argv[1:]
assert args and args[0] == "api", args
args = args[1:]
method, slurp, path = "GET", False, None
i = 0
while i < len(args):
    a = args[i]
    if a in ("-X", "--method"):
        method = args[i + 1]; i += 2; continue
    if a == "--slurp":
        slurp = True
    elif a == "--paginate":
        pass
    elif a.startswith("-"):
        raise SystemExit("fake gh: unsupported flag " + a)
    else:
        path = a
    i += 1
with open(os.environ["FAKE_GH_LOG"], "a") as log:
    log.write(json.dumps([method, path]) + "\n")
url = urllib.parse.urlparse(path)
query = dict(urllib.parse.parse_qsl(url.query))
parts = url.path.strip("/").split("/")
if method != "GET":
    print("{}"); raise SystemExit(0)
def runs(items):
    for key in ("event", "head_sha", "status", "branch"):
        if key in query:
            field = "head_branch" if key == "branch" else key
            items = [r for r in items if r.get(field) == query[key] or (key == "status" and r.get("conclusion") == query[key])]
    return {"total_count": len(items), "workflow_runs": items}
if parts[-2:] == ["actions", "runs"] or parts[-1] == "runs" and "workflows" in parts:
    items = state["runs"]
    if "workflows" in parts:
        wf = parts[parts.index("workflows") + 1]
        items = [r for r in items if r["path"].endswith("/" + wf) or str(r["workflow_id"]) == wf]
    body = runs(items)
elif parts[-1] == "jobs":
    body = {"total_count": 0, "jobs": state["jobs"].get(parts[-2], [])}
else:
    raise SystemExit("fake gh: unexpected path " + path)
print(json.dumps([body] if slurp else body))
'''

HEAD_BRANCH = "feat/x"
GATE_RUN = {"id": 9001, "workflow_id": 77, "name": "gate-authority", "event": "pull_request_target",
            "path": ".github/workflows/gate-authority.yml", "head_sha": BASE, "head_branch": HEAD_BRANCH,
            "status": "completed", "conclusion": "success",
            "pull_requests": [{"number": 12, "head": {"sha": HEAD}}]}
SPOOF_RUN = {"id": 9002, "workflow_id": 78, "name": "gate-authority", "event": "pull_request",
             "path": ".github/workflows/spoof.yml", "head_sha": HEAD, "head_branch": HEAD_BRANCH,
             "status": "completed", "conclusion": "success",
             "pull_requests": [{"number": 12, "head": {"sha": HEAD}}]}
OTHER_PR_GATE_RUN = dict(GATE_RUN, id=9003, head_branch="feat/other",
                         pull_requests=[{"number": 13, "head": {"sha": "b" * 40}}])
LEASE_RUN = {"id": 9004, "workflow_id": 79, "name": "lease-tamper", "event": "pull_request",
             "path": ".github/workflows/lease-tamper.yml", "head_sha": HEAD, "head_branch": HEAD_BRANCH,
             "status": "completed", "conclusion": "failure",
             "pull_requests": [{"number": 12, "head": {"sha": HEAD}}]}


class ReviewRerunTest(unittest.TestCase):
    def doc(self):
        self.assertTrue(RERUN_WORKFLOW.is_file())
        text = RERUN_WORKFLOW.read_text(encoding="utf-8")
        doc = strict_yaml(text)
        self.assertIn(yaml_cross_check(text, doc), (True, None))
        return doc

    def step(self):
        doc = self.doc()
        (job,) = doc["jobs"].values()
        runs = [step for step in job["steps"] if "run" in step]
        self.assertEqual(len(runs), 1, "one rerun step")
        return runs[0]

    def test_rerun_workflow_shape_is_minimal(self):
        doc = self.doc()
        types = set(doc["on"]["pull_request_review"]["types"])
        self.assertEqual(set(doc["on"]), {"pull_request_review"})
        self.assertLessEqual({"submitted", "dismissed"}, types)
        self.assertEqual(doc["permissions"], {"contents": "read", "actions": "write"})
        (job,) = doc["jobs"].values()
        self.assertNotIn("permissions", job)
        for where, key, item in _walk(doc):
            self.assertNotIn(key, FORBIDDEN_KEYS - {"if"}, where)
            if key == "run":
                self.assertNotIn("${{", item, where)
            if isinstance(item, str):
                for word in ("GITHUB_ENV", "GITHUB_PATH", "secrets."):
                    self.assertNotIn(word, item, where)
        env = set(self.step()["env"])
        self.assertLessEqual({"GH_TOKEN", "REPO", "PR_NUMBER"}, env)
        self.assertLessEqual(env, {"GH_TOKEN", "REPO", "PR_NUMBER", "HEAD_SHA", "HEAD_REF"})

    def run_step(self, runs, jobs=None):
        step = self.step()
        tmp = Path(tempfile.mkdtemp(prefix="review-rerun-"))
        self.addCleanup(shutil.rmtree, tmp, True)
        (tmp / "gh").write_text(FAKE_GH, encoding="utf-8")
        os.chmod(tmp / "gh", 0o755)
        (tmp / "state.json").write_text(json.dumps({"runs": runs, "jobs": jobs or {}}), encoding="utf-8")
        values = {"${{ github.token }}": "fake-token", "${{ github.repository }}": "mojomast/rizzstack",
                  "${{ github.event.pull_request.head.sha }}": HEAD,
                  "${{ github.event.pull_request.number }}": "12",
                  "${{ github.event.pull_request.head.ref }}": HEAD_BRANCH}
        env = {"PATH": "%s:%s" % (tmp, os.environ.get("PATH", "")), "FAKE_GH_STATE": str(tmp / "state.json"),
               "FAKE_GH_LOG": str(tmp / "log.jsonl"), "HOME": str(tmp)}
        for key, value in step["env"].items():
            self.assertIn(value, values, "unexpected env expression %s=%s" % (key, value))
            env[key] = values[value]
        proc = subprocess.run(["bash", "-e", "-o", "pipefail", "-c", step["run"]], env=env,
                              capture_output=True, text=True, timeout=60)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        log = tmp / "log.jsonl"
        calls = [json.loads(line) for line in log.read_text().splitlines()] if log.exists() else []
        return [path for method, path in calls if method == "POST"]

    def test_a_review_reruns_the_base_pinned_gate_for_this_pull_request(self):
        posts = self.run_step([GATE_RUN, SPOOF_RUN, OTHER_PR_GATE_RUN])
        self.assertIn("repos/mojomast/rizzstack/actions/runs/9001/rerun", [p.lstrip("/") for p in posts])

    def test_a_green_gate_is_rerun_too_so_a_later_rejection_turns_it_red(self):
        posts = [p.lstrip("/") for p in self.run_step([dict(GATE_RUN, conclusion="success")])]
        self.assertEqual(posts, ["repos/mojomast/rizzstack/actions/runs/9001/rerun"])

    def test_same_named_runs_from_other_workflows_or_pull_requests_are_not_rerun(self):
        posts = " ".join(self.run_step([GATE_RUN, SPOOF_RUN, OTHER_PR_GATE_RUN]))
        self.assertNotIn("9002", posts)
        self.assertNotIn("9003", posts)

    def test_failed_lease_tamper_jobs_are_still_rerun(self):
        posts = [p.lstrip("/") for p in self.run_step(
            [LEASE_RUN], jobs={"9004": [{"id": 55, "name": "lease-tamper", "conclusion": "failure"}]})]
        self.assertIn("repos/mojomast/rizzstack/actions/jobs/55/rerun", posts)


# --------------------------------------------------------------- the lane's gate-authority run
# A pull_request_target run reports its check on the BASE tip (GITHUB_SHA is the base branch's
# last commit), so the real gate never appears among the PR head's check runs.  The lane finds
# the gate through its workflow run instead.  GitHub's own required check is not the authority
# (it is pinned only to the Actions app, so a same-named job satisfies it).  On these personal-
# account repos no required-workflow rule exists, so native and API merges stay unguarded by the
# gate and the lane is the only merge authority.
REPO = "mojomast/rizzstack"
GATE_WORKFLOW_PATH = ".github/workflows/gate-authority.yml"
REVIEW_AT = "2026-11-27T22:00:00Z"
BASE_TIP = "4" * 40          # main has moved on since the gate run started
FOREIGN = "6" * 40           # a commit that is not on main (another base branch)
RELEASE_TIP = "7" * 40       # tip of a non-main base branch
MISSING = object()           # compare payload without a files list
GATE_CODE_FILES = (".github/workflows/gate-authority.yml", ".github/workflows/review-rerun.yml",
                   ".github/scripts/lease_tamper.py", ".github/scripts/gate_drill.py",
                   ".github/scripts/gate_drill.lock.json", ".github/scripts/helpers/new_module.py")


def _gate_run(**overrides):
    run = {"id": 9001, "run_number": 41, "run_attempt": 1, "workflow_id": 77, "name": "gate-authority",
           "path": GATE_WORKFLOW_PATH, "event": "pull_request_target", "head_sha": BASE,
           "head_branch": HEAD_BRANCH, "status": "completed", "conclusion": "success",
           "created_at": "2026-11-27T22:05:00Z", "run_started_at": "2026-11-27T22:05:00Z",
           "check_suite_id": 502,
           "pull_requests": [{"number": 12, "head": {"sha": HEAD}, "base": {"ref": "main", "sha": BASE}}],
           "repository": {"full_name": REPO}, "head_repository": {"full_name": REPO},
           "actor": {"login": "someone"}, "triggering_actor": {"login": "someone"}}
    run.update(overrides)
    return run


def _lane_facts(checks, required=("ci-gate",), files=("docs/readme.md",), reviews=None):
    return {
        "pr": {"number": 12, "state": "OPEN", "is_draft": False, "mergeable": True,
               "merge_state_status": "CLEAN", "head": HEAD, "files": list(files)},
        "class": LANE.NON_SLICE,
        "reviews": reviews if reviews is not None else [
            {"state": "COMMENTED", "commit_id": HEAD, "author": REVIEWER, "body": LINE}],
        "checks": [{"name": "ci-gate", "status": "completed", "conclusion": "success", "head": HEAD,
                    "completed_at": "2026-11-27T22:09:00Z"}] + list(checks),
        "required_checks": list(required),
        "verify": {"records": [], "compare": {}, "provenance": {}},
        "lease_tamper": {"ran": True, "ok": True, "reason": "gate_ok",
                         "output_tail": "lease-tamper: class=non-slice TIER=T3"},
        "owner_held": False,
    }


class _FakeLaneApi:
    """GitHub-shaped REST for one open PR (#12 at HEAD on BASE)."""

    def __init__(self, case):
        self.check_runs = [
            {"name": "ci-gate", "status": "completed", "conclusion": "success", "head_sha": HEAD,
             "completed_at": "2026-11-27T22:09:00Z", "app": {"slug": "github-actions"},
             "check_suite": {"id": 501}}]
        self.workflow_runs = []
        self.suite_apps = {}
        self.reviews = [{"state": "COMMENTED", "commit_id": HEAD, "body": LINE, "user": {"login": REVIEWER},
                         "submitted_at": REVIEW_AT}]
        tmp = Path(tempfile.mkdtemp(prefix="lane-api-"))
        case.addCleanup(shutil.rmtree, tmp, True)
        self.tmp = tmp
        self.plan = tmp / "plan.json"
        self.plan.write_text(json.dumps({"owner_held": {"prs": []}}), encoding="utf-8")
        self.paths = []
        self.base_ref = "main"
        self.base_tip = BASE_TIP
        # compare/<run head_sha>...<base tip> file lists (default: nothing changed); MISSING drops the key
        self.compare_files = {}
        # compare/<run head_sha>...<base tip> statuses; any other pair at the tip is "diverged"
        self.gate_compares = {(BASE, BASE_TIP): "ahead", (BASE_TIP, BASE_TIP): "identical"}

    def add_run(self, run, app="github-actions"):
        self.workflow_runs.append(run)
        self.suite_apps[str(run.get("check_suite_id"))] = app

    def __call__(self, url, token):
        parsed = urllib.parse.urlparse(url)
        path, query = parsed.path, dict(urllib.parse.parse_qsl(parsed.query))
        self.paths.append(path)
        if re.fullmatch(r"/repos/[^/]+/[^/]+/pulls/\d+", path):
            return {"number": 12, "state": "open", "draft": False, "mergeable": True,
                    "mergeable_state": "clean", "head": {"sha": HEAD, "ref": HEAD_BRANCH,
                                                         "repo": {"full_name": REPO}},
                    "base": {"ref": self.base_ref, "sha": BASE, "repo": {"full_name": REPO}}, "labels": []}, ""
        if path.endswith("/reviews"):
            return list(self.reviews), ""
        if path.endswith("/files"):
            return [{"filename": "docs/readme.md"}], ""
        if path.endswith("/check-runs"):
            self.assertion_head(path)
            return {"total_count": len(self.check_runs), "check_runs": self.check_runs}, ""
        if (re.fullmatch(r"/repos/[^/]+/[^/]+/actions/workflows/gate-authority\.yml/runs", path)
                or re.fullmatch(r"/repos/[^/]+/[^/]+/actions/runs", path)):
            runs = list(self.workflow_runs)
            if "check_suite_id" in query:
                runs = [r for r in runs if str(r.get("check_suite_id")) == query["check_suite_id"]]
            return {"total_count": len(runs), "workflow_runs": runs}, ""
        match = re.fullmatch(r"/repos/[^/]+/[^/]+/check-suites/(\d+)", path)
        if match:
            app = self.suite_apps.get(match.group(1))
            if app is None:
                raise AssertionError("unknown check suite " + match.group(1))
            return {"id": int(match.group(1)), "app": {"slug": app}, "head_sha": BASE}, ""
        match = re.fullmatch(r"/repos/[^/]+/[^/]+/git/ref/heads/(.+)", path)
        if match:
            if urllib.parse.unquote(match.group(1)) != self.base_ref:
                raise AssertionError("base tip read for a branch other than the PR's base: " + path)
            return {"ref": "refs/heads/" + self.base_ref, "object": {"type": "commit", "sha": self.base_tip}}, ""
        match = re.fullmatch(r"/repos/[^/]+/[^/]+/compare/([0-9a-f]{40})\.\.\.([0-9a-f]{40})", path)
        if match and match.group(2) == self.base_tip:
            pair = (match.group(1), match.group(2))
            status = self.gate_compares.get(pair, "diverged")
            files = self.compare_files.get(pair, [])
            return ({"status": status} if files is MISSING else {"status": status, "files": files}), ""
        if "/compare/" in path:
            return {"status": "ahead", "files": []}, ""
        raise AssertionError("unexpected REST path: " + path)

    @staticmethod
    def assertion_head(path):
        # check runs are only ever read for the PR head
        if not path.endswith("/commits/%s/check-runs" % HEAD):
            raise AssertionError("check runs read for something other than the PR head: " + path)


class GateAuthorityRunTest(unittest.TestCase):
    """The lane locates the gate through its pull_request_target workflow run, never a head check."""

    def facts(self, api, required=("ci-gate", "gate-authority"), with_workflow=True):
        repo = Path(tempfile.mkdtemp(prefix="lane-base-"))
        self.addCleanup(shutil.rmtree, repo, True)
        if with_workflow:
            (repo / ".github" / "workflows").mkdir(parents=True)
            (repo / GATE_WORKFLOW_PATH).write_text("name: gate-authority\n", encoding="utf-8")
        verify = Path(tempfile.mkdtemp(prefix="lane-verify-"))
        self.addCleanup(shutil.rmtree, verify, True)
        return LANE.collect_facts(REPO, 12, str(repo), "test-token", api_url="https://api.test",
                                  http=api, gate_record={"ran": True, "ok": True, "reason": "gate_ok",
                                                         "head": HEAD,
                                                         "output_tail": "lease-tamper: class=non-slice\n"},
                                  verify_dir=verify, plan_path=api.plan, required_checks=required)

    def verdict(self, *runs, app="github-actions", head_checks=(), reviews=None, compares=None,
                compare_files=None, base_ref="main", base_tip=BASE_TIP, **kwargs):
        api = _FakeLaneApi(self)
        api.base_ref, api.base_tip = base_ref, base_tip
        api.compare_files.update(compare_files or {})
        for run in runs:
            api.add_run(run, app)
        api.check_runs.extend(head_checks)
        if reviews is not None:
            api.reviews = reviews
        api.gate_compares.update(compares or {})
        return LANE.decide(self.facts(api, **kwargs))

    def assert_refused(self, reason, *runs, **kwargs):
        verdict = self.verdict(*runs, **kwargs)
        self.assertFalse(verdict["merge"], (runs, verdict))
        self.assertEqual(verdict["reason"], reason, runs)

    def test_proven_gate_run_for_the_exact_head_merges_without_a_head_check(self):
        verdict = self.verdict(_gate_run())
        self.assertTrue(verdict["merge"], verdict)

    def test_lane_reads_the_gate_from_workflow_runs_not_head_check_runs(self):
        api = _FakeLaneApi(self)
        api.add_run(_gate_run())
        self.facts(api)
        self.assertTrue(any(p.endswith("/actions/workflows/gate-authority.yml/runs")
                            or p.endswith("/actions/runs") for p in api.paths), api.paths)
        self.assertIn("/repos/%s/check-suites/502" % REPO, api.paths)

    def test_a_same_named_check_on_the_head_commit_refuses(self):
        spoof = {"name": "gate-authority", "status": "completed", "conclusion": "success", "head_sha": HEAD,
                 "completed_at": "2026-11-27T22:10:00Z", "app": {"slug": "github-actions"},
                 "check_suite": {"id": 777}}
        self.assert_refused("gate_authority_unproven", _gate_run(), head_checks=[spoof])
        self.assert_refused("gate_authority_unproven", head_checks=[spoof])

    def test_runs_from_another_workflow_event_app_or_repository_do_not_count(self):
        fork = {"full_name": "someone/rizzstack"}
        for spoof in (_gate_run(path=".github/workflows/spoof.yml"),
                      _gate_run(path=".github/workflows/gate-authority.yml.bak"),
                      _gate_run(event="pull_request"),
                      _gate_run(event="workflow_dispatch"),
                      _gate_run(head_repository=fork),
                      _gate_run(repository=fork),
                      _gate_run(head_repository=None)):
            self.assert_refused("gate_authority_unproven", spoof)
        self.assert_refused("gate_authority_unproven", _gate_run(), app="some-app")

    def test_run_for_another_pull_request_or_another_head_does_not_count(self):
        for spoof in (_gate_run(pull_requests=[{"number": 13, "head": {"sha": HEAD}}]),
                      _gate_run(pull_requests=[{"number": 12, "head": {"sha": "2" * 40}}]),
                      _gate_run(pull_requests=[]),
                      _gate_run(pull_requests=None),
                      _gate_run(head_sha=HEAD, pull_requests=[])):
            self.assert_refused("gate_authority_unproven", spoof)

    def test_the_newest_run_for_the_pull_request_decides(self):
        older = _gate_run()
        pushed = _gate_run(id=9010, run_number=42, check_suite_id=503, created_at="2026-11-27T22:20:00Z",
                           run_started_at="2026-11-27T22:20:00Z",
                           pull_requests=[{"number": 12, "head": {"sha": "3" * 40}}])
        # a newer run judged another head (a push after this one): the older green run is stale
        self.assert_refused("gate_authority_unproven", older, pushed)
        for state in ({"status": "in_progress", "conclusion": None}, {"conclusion": "failure"},
                      {"conclusion": "neutral"}, {"conclusion": "skipped"}, {"conclusion": "cancelled"}):
            newer = _gate_run(id=9011, run_number=43, check_suite_id=504, created_at="2026-11-27T22:30:00Z",
                              run_started_at="2026-11-27T22:30:00Z", **state)
            self.assert_refused("gate_authority_not_green", older, newer)
            self.assert_refused("gate_authority_not_green", _gate_run(**state))

    def test_run_must_start_after_the_newest_decisive_review(self):
        late = [{"state": "COMMENTED", "commit_id": HEAD, "body": LINE, "user": {"login": REVIEWER},
                 "submitted_at": "2026-11-27T21:00:00Z"},
                {"state": "COMMENTED", "commit_id": HEAD, "body": "one question",
                 "user": {"login": "someone-else"}, "submitted_at": "2026-11-27T22:06:00Z"},
                {"state": "COMMENTED", "commit_id": HEAD, "body": LINE, "user": {"login": REVIEWER},
                 "submitted_at": "2026-11-27T22:07:00Z"}]
        self.assert_refused("gate_authority_stale", _gate_run(), reviews=late)
        rerun = _gate_run(run_attempt=2, run_started_at="2026-11-27T22:08:00Z")
        self.assertTrue(self.verdict(rerun, reviews=late)["merge"])
        for bad in (None, "", "yesterday", "2026-11-27 22:08:00"):
            self.assert_refused("gate_authority_stale", _gate_run(run_started_at=bad))

    # ---- r4: the run must have judged this PR against its CURRENT base (retarget attack)
    def test_run_from_another_base_ref_refuses(self):
        retarget = _gate_run(head_sha=FOREIGN,
                             pull_requests=[{"number": 12, "head": {"sha": HEAD}, "base": {"ref": "x", "sha": FOREIGN}}])
        self.assert_refused("gate_authority_wrong_base", retarget)
        # even if the foreign base commit were somehow on main, the base ref must match
        self.assert_refused("gate_authority_wrong_base", retarget, compares={(FOREIGN, BASE_TIP): "ahead"})
        for base in ({"ref": "x", "sha": BASE}, {"ref": "main-old", "sha": BASE}, {"ref": "", "sha": BASE},
                     {"sha": BASE}, None):
            pr = {"number": 12, "head": {"sha": HEAD}}
            if base is not None:
                pr["base"] = base
            self.assert_refused("gate_authority_wrong_base", _gate_run(pull_requests=[pr]))

    def test_run_whose_workflow_commit_is_not_on_the_current_base_refuses(self):
        for status in ("diverged", "behind", None):
            compares = {(FOREIGN, BASE_TIP): status} if status else {}
            self.assert_refused("gate_authority_wrong_base", _gate_run(head_sha=FOREIGN), compares=compares)
        for bad in (None, "", "not-a-sha"):
            self.assert_refused("gate_authority_wrong_base", _gate_run(head_sha=bad))

    def test_run_at_the_current_base_tip_or_an_ancestor_counts(self):
        self.assertTrue(self.verdict(_gate_run())["merge"])                        # BASE is behind the tip
        self.assertTrue(self.verdict(_gate_run(head_sha=BASE_TIP))["merge"])       # identical to the tip

    def test_lane_measures_the_current_base_tip_and_ancestry(self):
        api = _FakeLaneApi(self)
        api.add_run(_gate_run())
        self.facts(api)
        self.assertIn("/repos/%s/git/ref/heads/main" % REPO, api.paths)
        self.assertIn("/repos/%s/compare/%s...%s" % (REPO, BASE, BASE_TIP), api.paths)

    def test_a_run_starting_in_the_same_second_as_a_review_is_stale(self):
        same = [{"state": "COMMENTED", "commit_id": HEAD, "body": LINE, "user": {"login": REVIEWER},
                 "submitted_at": "2026-11-27T22:05:00Z"}]
        self.assert_refused("gate_authority_stale", _gate_run(run_started_at="2026-11-27T22:05:00Z"), reviews=same)
        self.assertTrue(self.verdict(_gate_run(run_started_at="2026-11-27T22:05:01Z"), reviews=same)["merge"])

    def test_a_queued_or_waiting_run_is_not_green(self):
        for state in ({"status": "queued", "conclusion": None}, {"status": "waiting", "conclusion": None},
                      {"status": "pending", "conclusion": None}, {"status": "requested", "conclusion": None}):
            self.assert_refused("gate_authority_not_green", _gate_run(**state))

    # ---- r5: the deciding run's gate code must be byte-identical to the current base tip's
    def _changed(self, *entries):
        return {(BASE, BASE_TIP): list(entries)}

    def test_gate_code_changed_on_the_base_since_the_run_refuses(self):
        for name in GATE_CODE_FILES:
            for status in ("modified", "added", "removed"):
                self.assert_refused("gate_authority_stale_gate", _gate_run(),
                                    compare_files=self._changed({"filename": name, "status": status}))
        # a rename away from (or onto) gate code counts as a change to it
        self.assert_refused("gate_authority_stale_gate", _gate_run(), compare_files=self._changed(
            {"filename": "docs/old_gate.py", "previous_filename": ".github/scripts/lease_tamper.py",
             "status": "renamed"}))
        self.assert_refused("gate_authority_stale_gate", _gate_run(), compare_files=self._changed(
            {"filename": ".github/workflows/review-rerun.yml", "previous_filename": "docs/x.yml",
             "status": "renamed"}))
        # still refused when unrelated files changed alongside
        self.assert_refused("gate_authority_stale_gate", _gate_run(), compare_files=self._changed(
            {"filename": "docs/readme.md", "status": "modified"},
            {"filename": ".github/scripts/lease_tamper.py", "status": "modified"}))

    def test_other_base_changes_since_the_run_still_count(self):
        unrelated = self._changed(*({"filename": n, "status": "modified"} for n in (
            "docs/readme.md", "scripts/merge_lane_v5.py", ".github/workflows/ci.yml",
            ".github/scripts-notes.md", ".github/workflows/gate-authority.yml.bak", "tests/test_x.py")))
        self.assertTrue(self.verdict(_gate_run(), compare_files=unrelated)["merge"])
        self.assertTrue(self.verdict(_gate_run(head_sha=BASE_TIP))["merge"])

    def test_missing_malformed_or_truncated_compare_file_list_refuses(self):
        for files in (MISSING, None, "x", {"filename": "docs/readme.md"},
                      [{"status": "modified"}], [{"filename": None}], ["docs/readme.md"],
                      [{"filename": "docs/f%d.md" % i, "status": "modified"} for i in range(300)]):
            self.assert_refused("gate_authority_stale_gate", _gate_run(),
                                compare_files={(BASE, BASE_TIP): files})

    def test_a_pull_request_whose_base_is_not_main(self):
        release = "release/2026.11"
        run = _gate_run(pull_requests=[{"number": 12, "head": {"sha": HEAD}, "base": {"ref": release,
                                                                                     "sha": BASE}}])
        on_release = {(BASE, RELEASE_TIP): "ahead"}
        api = _FakeLaneApi(self)
        api.base_ref, api.base_tip = release, RELEASE_TIP
        api.gate_compares.update(on_release)
        api.add_run(run)
        verdict = LANE.decide(self.facts(api))
        self.assertTrue(verdict["merge"], verdict)
        self.assertIn("/repos/%s/git/ref/heads/%s" % (REPO, release), api.paths)
        self.assertNotIn("/repos/%s/git/ref/heads/main" % REPO, api.paths)
        self.assertIn("/repos/%s/compare/%s...%s" % (REPO, BASE, RELEASE_TIP), api.paths)
        # a run that judged the PR against main does not count for a PR now based on release
        self.assert_refused("gate_authority_wrong_base", _gate_run(), base_ref=release, base_tip=RELEASE_TIP,
                            compares=on_release)
        # a release-based run from a commit not on release's history does not count
        self.assert_refused("gate_authority_wrong_base", run, base_ref=release, base_tip=RELEASE_TIP)
        # and gate code changed on release since the run is still stale
        self.assert_refused("gate_authority_stale_gate", run, base_ref=release, base_tip=RELEASE_TIP,
                            compares=on_release, compare_files={(BASE, RELEASE_TIP): [
                                {"filename": ".github/scripts/lease_tamper.py", "status": "modified"}]})

    def test_gate_is_demanded_whenever_the_base_carries_the_gate_workflow(self):
        self.assert_refused("gate_authority_unproven", required=("ci-gate",))
        self.assert_refused("gate_authority_unproven", required=("ci-gate", "gate-authority"),
                            with_workflow=False)
        self.assertTrue(self.verdict(required=("ci-gate",), with_workflow=False)["merge"])


# --------------------------------------------------------------- owner attestation identity
def _owner_line(login, commit=HEAD):
    review = {"state": "COMMENTED", "commit_id": commit, "body": "APPROVE (owner) at %s" % commit}
    if login is not None:
        review["author"] = login
    return review


class OwnerAttestationIdentityTest(unittest.TestCase):
    SENSITIVE = (".github/workflows/ci.yml",)

    def verdict(self, owner_review):
        reviews = [{"state": "COMMENTED", "commit_id": HEAD, "author": REVIEWER, "body": LINE}]
        if owner_review is not None:
            reviews.append(owner_review)
        return LANE.decide(_lane_facts((), required=("ci-gate",), files=self.SENSITIVE, reviews=reviews))

    def test_owner_line_from_the_owner_login_is_an_attestation(self):
        self.assertTrue(self.verdict(_owner_line(OWNER))["merge"])

    def test_owner_line_from_any_other_login_is_not(self):
        for login in (COORDINATOR, REVIEWER, "someone-else", "mojomast-bot", "", None):
            self.assertEqual(self.verdict(_owner_line(login))["reason"], "owner_review_missing", login)

    def test_owner_identity_rule_lives_in_the_shared_policy_block(self):
        for module in (GATE, LANE):
            self.assertEqual(module.OWNER_LOGIN, OWNER)
            self.assertTrue(module.owner_identity_ok({"user": {"login": OWNER}}))
            self.assertTrue(module.owner_identity_ok({"author": OWNER}))
            for login in (COORDINATOR, REVIEWER, "", None, "MOJOMAST "):
                self.assertFalse(module.owner_identity_ok({"user": {"login": login}}), login)
            self.assertFalse(module.owner_identity_ok({}))
            self.assertFalse(module.review_is_approval(
                {"state": "COMMENTED", "commit_id": HEAD, "body": "APPROVE (owner) at " + HEAD,
                 "user": {"login": REVIEWER}},
                module.REVIEW_APPROVE_OWNER))
            self.assertTrue(module.review_is_approval(
                {"state": "COMMENTED", "commit_id": HEAD, "body": "APPROVE (owner) at " + HEAD,
                 "user": {"login": OWNER}},
                module.REVIEW_APPROVE_OWNER))


# --------------------------------------------------------------- identity edge cases (review follow-ups)
class IdentityEdgeTest(unittest.TestCase):
    def test_strict_head_path_also_requires_the_reviewer_login(self):
        for login, ok in ((REVIEWER, True), (COORDINATOR, False), (OWNER, False), (None, False)):
            review = GATE.independent_review_at_head([_review(login)], HEAD)
            self.assertEqual(review is not None, ok, login)

    def test_a_carried_approval_from_the_coordinator_or_owner_is_refused(self):
        older = "2" * 40
        carry = {older: {"review_sha": older, "base": BASE, "head": HEAD, "ok": True,
                         "reason": LANE.CARRY_OK, "detail": ""}}
        for login, ok in ((REVIEWER, True), (COORDINATOR, False), (OWNER, False)):
            facts = _lane_facts((), required=("ci-gate",), reviews=[
                {"state": "COMMENTED", "commit_id": older, "author": login,
                 "body": "APPROVE (independent review) at " + older}])
            facts["review_carry"] = carry
            self.assertEqual(LANE.decide(facts)["merge"], ok, login)

    def test_lane_refuses_near_miss_and_missing_authors(self):
        for author in ("rizzstack-reviewer", "RIZZSTACK-REVIEWER[BOT]", " rizzstack-reviewer[bot]", "", None):
            review = {"state": "COMMENTED", "commit_id": HEAD, "body": LINE}
            if author is not None:
                review["author"] = author
            self.assertFalse(LANE.decide(_lane_facts((), required=("ci-gate",), reviews=[review]))["merge"],
                             repr(author))

    def test_merge_states_are_exactly_clean(self):
        self.assertEqual(tuple(LANE.MERGE_STATES), ("CLEAN",))


# --------------------------------------------------------------- T1 allowlist
class GitRepo:
    def __init__(self, path):
        self.path = Path(path)
        self.git("init", "-q", "-b", "main")
        self.git("config", "user.email", "tier@example.invalid")
        self.git("config", "user.name", "Gate Authority Tests")
        self.git("config", "commit.gpgsign", "false")

    def git(self, *args):
        proc = subprocess.run(["git", "-C", str(self.path), *args], capture_output=True, text=True)
        if proc.returncode != 0:
            raise AssertionError("git %s: %s%s" % (" ".join(args), proc.stdout, proc.stderr))
        return proc.stdout.strip()

    def write(self, relative, text, mode=None):
        target = self.path / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
        if mode is not None:
            os.chmod(target, mode)

    def commit(self, message):
        self.git("add", "-A")
        self.git("commit", "-q", "-m", message)
        return self.git("rev-parse", "HEAD")


BASE_FILES = {
    "src/slice.py": "VALUE = 1\n",
    "docs/readme.md": "# docs\n",
    "tests/test_frozen.py": "def test_ok():\n    assert True\n",
    "packages/widget/index.js": "export const x = 1;\n",
    "apps/board/index.html": "<p>board</p>\n",
    "hyvv/cli.py": "VALUE = 1\n",
}


class T1AllowlistTest(unittest.TestCase):
    def tier(self, changes, repo_name="orbitdesktop", symlinks=(), modes=None, modes_only=None,
             gitlinks=()):
        root = tempfile.mkdtemp(prefix="gate-authority-")
        self.addCleanup(shutil.rmtree, root, True)
        repo = GitRepo(root)
        for path, text in BASE_FILES.items():
            repo.write(path, text)
        base = repo.commit("base")
        repo.git("checkout", "-q", "-b", "feat/x")
        for path, text in changes.items():
            repo.write(path, text, (modes or {}).get(path))
        for link, target in symlinks:
            (repo.path / link).parent.mkdir(parents=True, exist_ok=True)
            os.symlink(target, repo.path / link)
        for path, mode in (modes_only or {}).items():
            os.chmod(repo.path / path, mode)
        for path in gitlinks:
            repo.git("update-index", "--add", "--cacheinfo", "160000,%s,%s" % (base, path))
        if gitlinks:
            repo.git("commit", "-q", "-m", "change")
            head = repo.git("rev-parse", "HEAD")
        else:
            head = repo.commit("change")
        tier, reasons = GATE.risk_tier(str(repo.path), base, head, repo_name=repo_name, environ={})
        return tier, [reason for reason, _ in reasons]

    def assert_t3(self, reason, changes, **kwargs):
        tier, reasons = self.tier(changes, **kwargs)
        self.assertEqual(tier, "T3", (changes, reasons))
        self.assertIn(reason, reasons)

    def test_plain_source_docs_and_new_test_module_stay_t1(self):
        tier, reasons = self.tier({"src/slice.py": "VALUE = 2\n", "docs/readme.md": "# new\n",
                                   "tests/test_more.py": "def test_more():\n    assert 1\n"})
        self.assertEqual((tier, reasons), ("T1", []))

    def test_subprocess_capability_is_t3(self):
        self.assert_t3("capability_subprocess", {"src/slice.py": "import subprocess\nVALUE = 2\n"})
        self.assert_t3("capability_subprocess", {"src/slice.py": "import os\nos.system('true')\n"})
        self.assert_t3("capability_subprocess",
                       {"packages/widget/index.js": "const cp = require('child_process');\n"})

    def test_network_capability_is_t3(self):
        self.assert_t3("capability_network", {"src/slice.py": "import urllib.request\nVALUE = 2\n"})
        self.assert_t3("capability_network", {"src/slice.py": "import socket\nVALUE = 2\n"})
        self.assert_t3("capability_network",
                       {"packages/widget/index.js": "export const x = fetch('https://example.invalid');\n"})

    def test_dependency_manifests_are_t3(self):
        self.assert_t3("dependency_manifest", {"packages/widget/package.json": '{"name": "w"}\n'})
        self.assert_t3("dependency_manifest", {"src/requirements.txt": "requests==2\n"})
        self.assert_t3("dependency_manifest", {"packages/widget/package-lock.json": "{}\n"})

    def test_test_infrastructure_is_t3(self):
        self.assert_t3("test_infra", {"tests/fixtures/case.json": "{}\n"})
        self.assert_t3("test_infra", {"tests/helpers.py": "VALUE = 1\n"})
        self.assert_t3("test_infra", {"tests/__init__.py": ""})

    def test_nested_ci_configuration_is_t3(self):
        self.assert_t3("ci_config", {"packages/widget/.github/workflows/ci.yml": "on: push\n"})

    def test_auth_validator_and_approval_surfaces_are_t3(self):
        self.assert_t3("auth_surface", {"hyvv/rizzstack_validators.py": "VALUE = 1\n"}, repo_name="hyvv")
        self.assert_t3("auth_surface", {"apps/rizzstack-approvals/index.html": "<p>x</p>\n"})
        self.assert_t3("auth_surface", {"src/sandbox_policy.py": "VALUE = 1\n"})

    def test_unknown_file_kind_is_t3(self):
        self.assert_t3("unknown_kind", {"src/run.sh": "echo hi\n"})
        self.assert_t3("unknown_kind", {"docs/setup.ps1": "Write-Host hi\n"})
        self.assert_t3("unknown_kind", {"src/slice.py": "VALUE = 2\n"}, modes={"src/slice.py": 0o755})
        self.assert_t3("unknown_kind", {}, symlinks=[("src/link.py", "../hyvv/cli.py")])

    def test_dynamic_subprocess_and_code_execution_are_t3(self):
        for text in ("from subprocess import run\n", "m = __import__('subprocess')\n",
                     "import importlib\nm = importlib.import_module('os')\n", "import os\nos.execv('/bin/true', [])\n",
                     "import os\nos.posix_spawn('/bin/true', [], {})\n", "eval('1')\n", "exec('x = 1')\n",
                     "import ctypes\n", "import multiprocessing\n", "import pty\n"):
            self.assert_t3("capability_subprocess", {"src/slice.py": text})
        for text in ("const cp = require('node:child_process');\n", "const cp = await import('child_process');\n",
                     "import { spawn } from 'child_process';\n", "const f = new Function('return 1');\n"):
            self.assert_t3("capability_subprocess", {"packages/widget/index.js": text})

    def test_more_network_capabilities_are_t3(self):
        for text in ("from http import client\n", "import ssl\n", "import asyncio\nasyncio.open_connection('x', 1)\n",
                     "import ftplib\n", "import smtplib\n", "import http.server\n"):
            self.assert_t3("capability_network", {"src/slice.py": text})
        for text in ("const ws = new WebSocket('wss://x.invalid');\n", "navigator.sendBeacon('/x');\n",
                     "const x = new XMLHttpRequest();\n", "import net from 'node:net';\n"):
            self.assert_t3("capability_network", {"packages/widget/index.js": text})

    def test_a_new_test_module_with_a_capability_is_t3(self):
        self.assert_t3("capability_subprocess", {"tests/test_new.py": "import subprocess\n\ndef test_x():\n    pass\n"})

    def test_unseen_auth_validator_sandbox_and_approval_variants_are_t3(self):
        self.assert_t3("auth_surface", {"hyvv/validators/x.py": "VALUE = 1\n"}, repo_name="hyvv")
        self.assert_t3("auth_surface", {"src/sandbox/x.py": "VALUE = 1\n"})
        self.assert_t3("auth_surface", {"apps/rizzstack-approvals-v2/x.html": "<p>x</p>\n"})
        self.assert_t3("auth_surface", {"src/permissions.py": "VALUE = 1\n"})

    def test_more_unknown_kinds_are_t3(self):
        for path, text in (("docs/run.bat", "echo hi\n"), ("docs/run.cmd", "echo hi\n"),
                           ("src/blob.bin", "\x00\x01\x02binary\n"), ("src/native.so", "ELF\n"),
                           ("src/pkg.whl", "PK\n"), ("src/mod.pyc", "x\n"), ("docs/nb.ipynb", "{}\n"),
                           ("src/Makefile", "all:\n\ttrue\n"), ("src/noext", "x\n")):
            self.assert_t3("unknown_kind", {path: text})

    def test_pure_mode_change_and_outside_symlink_are_t3(self):
        self.assert_t3("unknown_kind", {}, modes_only={"src/slice.py": 0o755})
        self.assert_t3("unknown_kind", {}, symlinks=[("src/escape.py", "../../../../etc/passwd")])

    def test_submodule_is_t3(self):
        self.assert_t3("unknown_kind", {}, gitlinks=["src/vendored"])

    def test_every_new_t3_reason_has_a_gate_drill_case(self):
        drill = _load(DRILL_SCRIPT, "gate_drill_authority_under_test")
        reasons = [(case.expect_reason or "") for case in drill.CASES
                   if case.kind == "tier" and case.expect_tier == "T3"]
        for reason in NEW_T3_REASONS:
            self.assertTrue(any(r.startswith(reason + ":") for r in reasons), reason)


# --------------------------------------------------------------- gate drill lock is append-only
DRILL_LOCK = ROOT / ".github" / "scripts" / "gate_drill.lock.json"
DRILL_LOCK_AT_MAIN = ROOT / "tests" / "fixtures" / "gate_drill.lock.main.json"
DRILL_LOCK_AT_MAIN_SHA256 = "560c4509dc49163ca9bf6b7a9bd205590bc3d099302b6df3c1d35873a4fc98fe"


def _entry_bytes(entry):
    return json.dumps(entry, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")


class GateDrillLockAppendOnlyTest(unittest.TestCase):
    """Every drill case pinned at main stays byte-identical: new cases may be added, none weakened."""

    def main_lock(self):
        data = DRILL_LOCK_AT_MAIN.read_bytes()
        self.assertEqual(hashlib.sha256(data).hexdigest(), DRILL_LOCK_AT_MAIN_SHA256)
        return json.loads(data)

    def test_lock_entries_present_at_main_stay_byte_identical(self):
        main = self.main_lock()
        lock = json.loads(DRILL_LOCK.read_text(encoding="utf-8"))
        self.assertEqual(lock.get("version"), main["version"])
        self.assertGreaterEqual(len(main["cases"]), 1)
        for cid, entry in main["cases"].items():
            self.assertIn(cid, lock["cases"], "drill case %s was removed" % cid)
            self.assertEqual(_entry_bytes(lock["cases"][cid]), _entry_bytes(entry), cid)

    def test_drill_declarations_present_at_main_are_unchanged(self):
        main = self.main_lock()
        drill = _load(DRILL_SCRIPT, "gate_drill_append_only_under_test")
        declared = drill.expected_lock()["cases"]
        for cid, entry in main["cases"].items():
            self.assertIn(cid, declared, "drill case %s is no longer declared" % cid)
            self.assertEqual(_entry_bytes(declared[cid]), _entry_bytes(entry), cid)


if __name__ == "__main__":
    unittest.main()
