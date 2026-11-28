"""RED for preauth-wiring W1: the merge lane must require a signed preauth envelope.

Merge lane v5 decides and (with ``--merge``) squash-merges a pull request. Today no
signed pre-authorization is consulted on that path. This suite freezes the wiring:

* the lane imports ``preauth`` from its own checkout's ``scripts/`` directory and uses only
  ``preauth.check`` and ``preauth.PreauthRefused`` (the frozen wiring rule, plus the stricter
  GROKCHAT round-5 rules recorded in contracts/preauth-wiring.requirements.json);
* after ``decide()`` says merge, the live path asks ``authorize_merge`` for a decision. That
  calls ``preauth.check`` with facts the lane measured itself (envelope and signature bytes and
  the ledger from the pinned store, the lease's contract, the gate's tier, the base tip, a UTC
  clock and git ancestry) and refuses on a missing, invalid, expired, out-of-scope or
  out-of-budget envelope;
* ``--merge`` merges only after the use is recorded in the ledger; without ``--merge``
  nothing is merged and nothing is recorded.

Production trust is empty (policy/allowed_signers has no keys, OWNER_FINGERPRINTS is ()), so
after this slice the lane authorizes nothing until the owner enrols a key in a later,
owner-reviewed change. Behaviour tests therefore drive the lane with a stand-in ``preauth``
module that exposes only ``check`` and ``PreauthRefused``.
"""

from __future__ import annotations

import ast
import contextlib
import copy
import hashlib
import importlib.util
import inspect
import io
import json
import os
import subprocess
import sys
import tempfile
import types
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
LANE_PATH = SCRIPTS / "merge_lane_v5.py"
GATE_PATH = ROOT / ".github" / "scripts" / "lease_tamper.py"
PREAUTH_PATH = SCRIPTS / "preauth.py"
VERIFY_PATH = SCRIPTS / "preauth_verify.py"
VERIFY_SHA256 = "0a3ab62b134c0174d150ce25a9c18364a7685872914cf06c8eac36f9ee0d959b"
FIXTURES = ROOT / "tests" / "fixtures" / "preauth_wiring"

REPO = "mojomast/rizzstack"
HEAD = "a" * 40
BASE_TIP = "b" * 40
CONTRACT = "c" * 64
OTHER_CONTRACT = "d" * 64
NOW_FLOOR = datetime(2026, 11, 1, tzinfo=timezone.utc)

#: the closed refusal set of the frozen checker (scripts/preauth.py REFUSAL_REASONS)
CHECKER_REASONS = frozenset({
    "envelope_schema_invalid", "envelope_clock_invalid", "envelope_policy_unproven",
    "envelope_signers_invalid", "envelope_signer_untrusted", "envelope_bad_signature",
    "envelope_verifier_unavailable", "envelope_expired", "envelope_not_yet_valid",
    "envelope_ttl_exceeded", "envelope_facts_unproven", "envelope_revoked",
    "envelope_action_forbidden", "envelope_repo_mismatch", "envelope_base_mismatch",
    "envelope_contract_not_listed", "envelope_tier_exceeded", "envelope_oracle_unproven",
    "envelope_oracle_changed", "envelope_nonce_reused", "envelope_replayed",
    "envelope_budget_exhausted",
})

CHECK_PARAMS = ("data", "signature", "repo", "base_rev", "contract_sha256", "tier", "now", "facts",
                "is_ancestor")


def _reference_check(data, signature, repo, base_rev, contract_sha256, tier, now, facts, is_ancestor):
    """The frozen production signature of preauth.check (bound against, never called)."""


class StubPreauth:
    """A stand-in ``preauth`` module: ``check`` and ``PreauthRefused`` only; any other name raises."""

    def __init__(self, outcomes=None):
        self.calls = []
        self.forbidden = []
        self.outcomes = list(outcomes or [])
        module = types.ModuleType("preauth")

        class PreauthRefused(Exception):
            pass

        def check(*args, **kwargs):
            bound = inspect.signature(_reference_check).bind(*args, **kwargs)
            self.calls.append(dict(bound.arguments))
            outcome = self.outcomes.pop(0) if self.outcomes else ("refuse", "envelope_signer_untrusted")
            kind, value = outcome
            if kind == "ok":
                return dict(value)
            if kind == "return":
                return value
            if kind == "refuse":
                error = PreauthRefused(value)
                error.reason = value
                raise error
            raise value

        def trap(name):
            if name.startswith("__") and name.endswith("__"):
                raise AttributeError(name)
            self.forbidden.append(name)
            raise AttributeError("preauth-wiring: only check and PreauthRefused may be used, not " + name)

        module.check = check
        module.PreauthRefused = PreauthRefused
        module.__getattr__ = trap
        self.module = module
        self.PreauthRefused = PreauthRefused


_LOADED = [0]


def load_lane(stub=None):
    """A fresh copy of the lane; with ``stub`` its ``import preauth`` resolves to the stand-in."""
    _LOADED[0] += 1
    name = "merge_lane_wiring_%d" % _LOADED[0]
    saved = sys.modules.get("preauth")
    if stub is not None:
        sys.modules["preauth"] = stub.module
    try:
        spec = importlib.util.spec_from_file_location(name, LANE_PATH)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
    finally:
        if saved is None:
            sys.modules.pop("preauth", None)
        else:
            sys.modules["preauth"] = saved
    return module


def ok_result(envelope_id="env-1", contract=CONTRACT):
    return {"ok": True, "envelope_id": envelope_id, "contract_sha256": contract, "max_tier": "T3",
            "policy_sha256": "e" * 64, "allowed_signers_sha256": "f" * 64}


def envelope_bytes(envelope_id="env-1", action="merge_slice", nonce="nonce-1", contract=CONTRACT):
    document = {
        "schema": "rizzstack.preauth/2", "id": envelope_id, "issuer": "owner@example.invalid",
        "issued_at": "2026-11-28T00:00:00Z", "expires_at": "2026-11-30T00:00:00Z",
        "nonce": nonce, "action_class": action, "repos": [REPO], "bases": {REPO: BASE_TIP},
        "contracts": [{"repo": REPO, "slice": "s", "contract_sha256": contract,
                       "source_rev": BASE_TIP, "max_tier": "T3"}],
        "review": {"packet_sha256": "0" * 64}, "budget": {"max_merges": 1, "max_bees": 1,
                                                          "max_attempts_per_contract": 1},
        "conditions": [], "max_uses": 1, "created_from": "owner",
    }
    return (json.dumps(document, sort_keys=True) + "\n").encode("ascii")


LEDGER = {"revoked": [], "consumed": [], "nonces": {}, "attempts": {}, "bees_in_flight": 0}


def write_store(root, envelopes=(), ledger=LEDGER, signatures=True):
    """The pinned store layout: envelopes/<name>.json with <name>.json.sig, and ledger.json."""
    root = Path(root)
    (root / "envelopes").mkdir(parents=True, exist_ok=True)
    for name, data in envelopes:
        (root / "envelopes" / name).write_bytes(data)
        if signatures:
            (root / "envelopes" / (name + ".sig")).write_bytes(b"SIG:" + name.encode("ascii"))
    if ledger is not None:
        (root / "ledger.json").write_text(json.dumps(ledger, sort_keys=True), encoding="utf-8")
    return root


def slice_verdict(tier="T3", merge=True):
    return {"merge": merge, "class": "slice", "reason": "reviewed" if merge else "review_missing",
            "head": HEAD, "tier": tier, "verify": "exact", "review": "exact"}


def slice_facts(contract=CONTRACT, pull_class="slice"):
    return {
        "class": pull_class,
        "reviews": [],
        "pr": {"number": 50, "head": HEAD, "base_ref": "main", "branch": "feat/x", "state": "OPEN",
               "is_draft": False, "mergeable": True, "merge_state_status": "CLEAN",
               "files": ["scripts/x.py"]},
        "verify": {"records": [], "compare": {},
                   "provenance": ({"repo": REPO, "pr": 50, "slice": "s", "contract_sha256": contract,
                                   "lease": ".rizzstack/leases/s.json"} if contract else None),
                   "provenance_reason": "lease_provenance_ok" if contract else "lease_provenance_missing"},
        "lease_tamper": {"ran": True, "ok": True, "class": pull_class, "tier": "T3"},
    }


def ancestor(older, newer):
    return True


class LaneTestCase(unittest.TestCase):
    def need(self, module, name):
        value = getattr(module, name, None)
        if value is None:
            self.fail("merge_lane_v5.%s is missing: the lane has no preauth wiring yet" % name)
        return value

    def store(self, envelopes=(), **kwargs):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        return write_store(tmp.name, envelopes, **kwargs)

    def authorize(self, stub, envelopes=(("e1.json", envelope_bytes()),), verdict=None, facts=None,
                  base_rev=BASE_TIP, now=None, **store_kwargs):
        lane = load_lane(stub)
        load_store = self.need(lane, "load_preauth_store")
        authorize = self.need(lane, "authorize_merge")
        root = self.store(envelopes, **store_kwargs)
        decision = authorize(
            verdict=verdict if verdict is not None else slice_verdict(),
            facts=facts if facts is not None else slice_facts(),
            store=load_store(str(root)),
            repo_slug=REPO,
            base_rev=base_rev,
            now=now or datetime.now(timezone.utc),
            ancestor=ancestor,
        )
        self.assertIsInstance(decision, dict)
        self.assertEqual(stub.forbidden, [], "the lane reached a preauth name other than check")
        return decision


# --------------------------------------------------------------- AC-001 static wiring
def _dotted(node):
    parts = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if isinstance(node, ast.Name):
        parts.append(node.id)
        return ".".join(reversed(parts))
    return None


def strict_wiring_violations(source):
    """The round-5 additions to the frozen wiring rule (requirements r6_additions)."""
    try:
        tree = ast.parse(source)
    except SyntaxError as exc:
        return ["syntax_error:%s" % exc.lineno]
    out = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and node.attr == "modules":
            out.append("modules_attr")
        if isinstance(node, ast.Attribute):
            dotted = _dotted(node)
            if dotted and dotted.startswith("scripts.preauth."):
                out.append("package_chain:" + dotted.split(".", 2)[2].split(".")[0])
        if isinstance(node, ast.ImportFrom) and any(alias.name == "*" for alias in node.names):
            out.append("star_import:" + (node.module or ""))
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            if node.func.id == "vars":
                out.append("vars_call")
            if node.func.id in ("getattr", "hasattr"):
                target = node.args[0] if node.args else None
                if not isinstance(target, ast.Name):
                    out.append("getattr_target")
    return sorted(set(out))


STRICT_MUST_FAIL = {
    "sys_alias_modules.py": "modules_attr",
    "getattr_constant_public.py": "getattr_target",
    "package_attr_chain.py": "package_chain:oracle_paths",
    "vars_package.py": "vars_call",
    "from_scripts_star_pkg.py": "star_import:scripts",
}


def _python_targets():
    exempt = {PREAUTH_PATH.resolve(), VERIFY_PATH.resolve()}
    for top in (ROOT / ".github", SCRIPTS):
        for path in sorted(top.rglob("*.py")):
            if "__pycache__" not in path.parts and path.resolve() not in exempt:
                yield path


class StaticWiringTest(unittest.TestCase):
    def tree(self):
        return ast.parse(LANE_PATH.read_text(encoding="utf-8"))

    def test_the_lane_imports_preauth_directly_at_module_level(self):
        tree = self.tree()
        found = [node for node in tree.body if isinstance(node, ast.Import)
                 and any(alias.name == "preauth" and alias.asname is None for alias in node.names)]
        self.assertTrue(found, "merge_lane_v5.py has no top-level `import preauth`")

    def test_the_lane_calls_preauth_check_and_catches_preauth_refused(self):
        tree = self.tree()
        calls = [node for node in ast.walk(tree) if isinstance(node, ast.Call)
                 and _dotted(node.func) == "preauth.check"]
        handlers = [node for node in ast.walk(tree) if isinstance(node, ast.ExceptHandler)
                    and node.type is not None and _dotted(node.type) == "preauth.PreauthRefused"]
        self.assertEqual(len(calls), 1, "exactly one preauth.check call site on the merge path")
        self.assertTrue(handlers, "the lane must catch preauth.PreauthRefused by name")

    def test_the_lane_puts_its_own_scripts_directory_first_on_sys_path(self):
        source = LANE_PATH.read_text(encoding="utf-8")
        tree = ast.parse(source)
        inserts = [node for node in ast.walk(tree) if isinstance(node, ast.Call)
                   and _dotted(node.func) == "sys.path.insert"
                   and node.args and isinstance(node.args[0], ast.Constant) and node.args[0].value == 0]
        self.assertTrue(inserts, "the lane must sys.path.insert(0, <its own scripts dir>) before import")
        import_line = min(node.lineno for node in tree.body if isinstance(node, ast.Import)
                          and any(alias.name == "preauth" for alias in node.names)) \
            if any(isinstance(node, ast.Import) and any(a.name == "preauth" for a in node.names)
                   for node in tree.body) else None
        self.assertIsNotNone(import_line, "no top-level `import preauth`")
        self.assertLess(min(node.lineno for node in inserts), import_line)

    def test_strict_must_fail_fixtures_are_caught(self):
        found = sorted(path.name for path in FIXTURES.iterdir() if path.is_file())
        self.assertEqual(found, sorted(STRICT_MUST_FAIL))
        requirements = json.loads((ROOT / "contracts" / "preauth-wiring.requirements.json")
                                  .read_text(encoding="utf-8"))
        cases = requirements["r6_additions_from_grokchat_round5_advisory_high_2"]["cases"]
        for name, code in STRICT_MUST_FAIL.items():
            text = (FIXTURES / name).read_text(encoding="utf-8")
            self.assertEqual(text, cases[name], name)
            self.assertIn(code, strict_wiring_violations(text), name)

    def test_every_python_file_under_github_and_scripts_obeys_the_strict_rule(self):
        scanned = 0
        for path in _python_targets():
            scanned += 1
            self.assertEqual(strict_wiring_violations(path.read_text(encoding="utf-8")), [],
                             str(path.relative_to(ROOT)))
        self.assertGreater(scanned, 5)


# --------------------------------------------------------------- AC-002 verifier pin at the call site
class VerifierPinTest(unittest.TestCase):
    def test_the_lane_loads_the_base_checkout_preauth_and_pinned_verifier(self):
        hostile = Path(tempfile.mkdtemp(prefix="preauth-wiring-hostile-"))
        self.addCleanup(lambda: subprocess.run(["rm", "-rf", str(hostile)], check=False))
        (hostile / "preauth.py").write_text("raise SystemExit('hostile preauth on PYTHONPATH')\n")
        (hostile / "preauth_verify.py").write_text("raise SystemExit('hostile verifier on PYTHONPATH')\n")
        probe = (
            "import hashlib, importlib.util, json, sys\n"
            "spec = importlib.util.spec_from_file_location('lane_pin_probe', %r)\n"
            "module = importlib.util.module_from_spec(spec)\n"
            "spec.loader.exec_module(module)\n"
            "pre = sys.modules.get('preauth')\n"
            "ver = sys.modules.get('preauth' + '_verify')\n"
            "out = {'preauth': getattr(pre, '__file__', None), 'verify': getattr(ver, '__file__', None)}\n"
            "if out['verify']:\n"
            "    out['sha256'] = hashlib.sha256(open(out['verify'], 'rb').read()).hexdigest()\n"
            "print(json.dumps(out))\n" % str(LANE_PATH)
        )
        env = dict(os.environ)
        env["PYTHONPATH"] = str(hostile)
        proc = subprocess.run([sys.executable, "-c", probe], cwd=str(hostile), env=env,
                              capture_output=True, text=True, timeout=120)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        got = json.loads(proc.stdout.strip().splitlines()[-1])
        self.assertIsNotNone(got["preauth"], "loading the lane did not import preauth")
        self.assertEqual(Path(got["preauth"]).resolve(), PREAUTH_PATH.resolve())
        self.assertEqual(Path(got["verify"]).resolve(), VERIFY_PATH.resolve())
        self.assertEqual(got["sha256"], VERIFY_SHA256)

    def test_the_lane_keeps_the_checker_refusal_set_verbatim(self):
        lane = load_lane()
        reasons = getattr(lane, "PREAUTH_REASONS", None)
        self.assertIsNotNone(reasons, "merge_lane_v5.PREAUTH_REASONS is missing")
        self.assertEqual(frozenset(reasons), CHECKER_REASONS)
        source = PREAUTH_PATH.read_text(encoding="utf-8")
        for reason in CHECKER_REASONS:
            self.assertIn('"%s"' % reason, source)


# --------------------------------------------------------------- AC-003 the decision call
class AuthorizeCallTest(LaneTestCase):
    def test_check_receives_only_facts_the_lane_measured(self):
        stub = StubPreauth([("ok", ok_result())])
        data = envelope_bytes()
        now = datetime.now(timezone.utc)
        decision = self.authorize(stub, envelopes=(("e1.json", data),), now=now)
        self.assertEqual(len(stub.calls), 1, decision)
        call = stub.calls[0]
        self.assertEqual(set(call), set(CHECK_PARAMS))
        self.assertEqual(call["data"], data)
        self.assertEqual(call["signature"], b"SIG:e1.json")
        self.assertEqual(call["repo"], REPO)
        self.assertEqual(call["base_rev"], BASE_TIP)
        self.assertEqual(call["contract_sha256"], CONTRACT)
        self.assertEqual(call["tier"], "T3")
        self.assertIs(call["now"], now)
        self.assertIsNotNone(call["now"].tzinfo)
        self.assertEqual(call["facts"], LEDGER)
        self.assertIs(call["is_ancestor"], ancestor)
        self.assertTrue(decision.get("ok"), decision)
        self.assertEqual(decision.get("envelope_id"), "env-1")
        self.assertEqual(decision.get("contract_sha256"), CONTRACT)
        self.assertEqual(decision.get("nonce"), "nonce-1")

    def test_the_tier_comes_from_the_gate_verdict(self):
        stub = StubPreauth([("ok", ok_result())])
        self.authorize(stub, verdict=slice_verdict(tier="T1"))
        self.assertEqual(len(stub.calls), 1)
        self.assertEqual(stub.calls[0]["tier"], "T1")

    def test_a_missing_ledger_is_passed_as_none_never_fabricated(self):
        stub = StubPreauth([("refuse", "envelope_facts_unproven")])
        decision = self.authorize(stub, ledger=None)
        self.assertEqual(len(stub.calls), 1, decision)
        self.assertIsNone(stub.calls[0]["facts"])
        self.assertFalse(decision.get("ok"))

    def test_the_signed_bytes_are_passed_exactly(self):
        data = envelope_bytes() + b"   \n"
        stub = StubPreauth([("refuse", "envelope_bad_signature")])
        self.authorize(stub, envelopes=(("e1.json", data),))
        self.assertEqual(len(stub.calls), 1)
        self.assertEqual(stub.calls[0]["data"], data)

    def test_an_ok_result_for_another_contract_is_not_an_authorization(self):
        stub = StubPreauth([("ok", ok_result(contract=OTHER_CONTRACT))])
        decision = self.authorize(stub)
        self.assertEqual(len(stub.calls), 1)
        self.assertFalse(decision.get("ok"), decision)

    def test_a_non_true_ok_value_is_not_an_authorization(self):
        for result in ({"ok": "yes", "envelope_id": "env-1", "contract_sha256": CONTRACT},
                       {"envelope_id": "env-1", "contract_sha256": CONTRACT},
                       {"ok": 1, "envelope_id": "env-1", "contract_sha256": CONTRACT},
                       None, [], True):
            stub = StubPreauth([("return", result)])
            decision = self.authorize(stub)
            self.assertEqual(len(stub.calls), 1, result)
            self.assertFalse(decision.get("ok"), (result, decision))


# --------------------------------------------------------------- AC-004 refusals
class RefusalTest(LaneTestCase):
    def refused(self, decision, reason):
        self.assertFalse(decision.get("ok"), decision)
        self.assertEqual(decision.get("reason"), reason, decision)

    def test_no_envelope_refuses_preauth_missing_without_calling_check(self):
        stub = StubPreauth([("ok", ok_result())])
        decision = self.authorize(stub, envelopes=())
        self.assertEqual(stub.calls, [])
        self.refused(decision, "preauth_missing")

    def test_no_store_at_all_refuses_preauth_missing(self):
        stub = StubPreauth([("ok", ok_result())])
        lane = load_lane(stub)
        store = self.need(lane, "load_preauth_store")(str(Path(tempfile.gettempdir()) / "no-such-preauth-store-x"))
        decision = self.need(lane, "authorize_merge")(
            verdict=slice_verdict(), facts=slice_facts(), store=store, repo_slug=REPO,
            base_rev=BASE_TIP, now=datetime.now(timezone.utc), ancestor=ancestor)
        self.assertEqual(stub.calls, [])
        self.refused(decision, "preauth_missing")

    def test_each_checker_refusal_refuses_the_merge_with_that_reason(self):
        for reason in ("envelope_schema_invalid", "envelope_bad_signature", "envelope_signer_untrusted",
                       "envelope_expired", "envelope_not_yet_valid", "envelope_revoked",
                       "envelope_repo_mismatch", "envelope_base_mismatch", "envelope_contract_not_listed",
                       "envelope_tier_exceeded", "envelope_budget_exhausted", "envelope_replayed",
                       "envelope_nonce_reused", "envelope_facts_unproven"):
            stub = StubPreauth([("refuse", reason)])
            decision = self.authorize(stub)
            self.refused(decision, "preauth_refused")
            self.assertEqual(decision.get("refusals"), [{"envelope": "e1.json", "reason": reason}], reason)

    def test_an_unknown_refusal_reason_is_reported_as_unknown(self):
        stub = StubPreauth([("refuse", "made_up_reason")])
        decision = self.authorize(stub)
        self.refused(decision, "preauth_refused")
        self.assertEqual(decision.get("refusals"), [{"envelope": "e1.json", "reason": "preauth_refused_unknown"}])

    def test_any_other_exception_refuses_and_its_text_never_leaves_the_lane(self):
        stub = StubPreauth([("raise", RuntimeError("SECRET-TEXT-1234 /home/owner/key"))])
        decision = self.authorize(stub)
        self.refused(decision, "preauth_refused")
        self.assertEqual(decision.get("refusals"), [{"envelope": "e1.json", "reason": "preauth_error"}])
        self.assertNotIn("SECRET-TEXT-1234", json.dumps(decision, default=str))

    def test_a_dispatch_envelope_never_authorizes_a_merge(self):
        stub = StubPreauth([("ok", ok_result())])
        decision = self.authorize(stub, envelopes=(("e1.json", envelope_bytes(action="dispatch_wave")),))
        self.assertEqual(stub.calls, [], "check must not run for an envelope of another action class")
        self.refused(decision, "preauth_refused")
        self.assertEqual(decision.get("refusals"), [{"envelope": "e1.json", "reason": "preauth_action_mismatch"}])

    def test_an_unparseable_or_duplicate_key_envelope_is_refused_before_check(self):
        for data in (b"not json", b'{"action_class": "merge_slice", "action_class": "merge_slice"}', b"[]"):
            stub = StubPreauth([("ok", ok_result())])
            decision = self.authorize(stub, envelopes=(("e1.json", data),))
            self.assertEqual(stub.calls, [], data)
            self.refused(decision, "preauth_refused")
            self.assertEqual(decision.get("refusals"), [{"envelope": "e1.json", "reason": "preauth_envelope_unreadable"}])

    def test_a_missing_signature_is_refused_before_check(self):
        stub = StubPreauth([("ok", ok_result())])
        decision = self.authorize(stub, signatures=False)
        self.assertEqual(stub.calls, [])
        self.refused(decision, "preauth_refused")
        self.assertEqual(decision.get("refusals"), [{"envelope": "e1.json", "reason": "preauth_signature_missing"}])

    def test_a_non_slice_pull_request_is_never_preauthorized(self):
        stub = StubPreauth([("ok", ok_result())])
        decision = self.authorize(stub, facts=slice_facts(contract=None, pull_class="non-slice"),
                                  verdict=dict(slice_verdict(), **{"class": "non-slice"}))
        self.assertEqual(stub.calls, [])
        self.refused(decision, "preauth_contract_unknown")

    def test_a_slice_without_lease_provenance_is_never_preauthorized(self):
        stub = StubPreauth([("ok", ok_result())])
        decision = self.authorize(stub, facts=slice_facts(contract=None))
        self.assertEqual(stub.calls, [])
        self.refused(decision, "preauth_contract_unknown")

    def test_an_unmeasured_base_refuses_before_check(self):
        stub = StubPreauth([("ok", ok_result())])
        decision = self.authorize(stub, base_rev=None)
        self.assertEqual(stub.calls, [])
        self.refused(decision, "preauth_base_unknown")

    def test_a_refused_decide_verdict_is_never_sent_to_check(self):
        stub = StubPreauth([("ok", ok_result())])
        decision = self.authorize(stub, verdict=slice_verdict(merge=False))
        self.assertEqual(stub.calls, [])
        self.refused(decision, "preauth_not_reached")

    def test_one_valid_envelope_among_refused_ones_authorizes(self):
        stub = StubPreauth([("refuse", "envelope_expired"), ("ok", ok_result(envelope_id="env-2"))])
        decision = self.authorize(stub, envelopes=(("a.json", envelope_bytes("env-1")),
                                                   ("b.json", envelope_bytes("env-2", nonce="nonce-2"))))
        self.assertEqual(len(stub.calls), 2)
        self.assertTrue(decision.get("ok"), decision)
        self.assertEqual(decision.get("envelope_id"), "env-2")
        self.assertEqual(decision.get("nonce"), "nonce-2")

    def test_a_result_id_that_differs_from_the_envelope_id_is_refused(self):
        stub = StubPreauth([("ok", ok_result(envelope_id="someone-else"))])
        decision = self.authorize(stub)
        self.assertFalse(decision.get("ok"), decision)


class ProductionRefusesTest(LaneTestCase):
    def test_with_the_real_checker_nothing_is_authorized_today(self):
        lane = load_lane()
        load_store = self.need(lane, "load_preauth_store")
        authorize = self.need(lane, "authorize_merge")
        root = self.store((("e1.json", envelope_bytes()),))
        decision = authorize(verdict=slice_verdict(), facts=slice_facts(), store=load_store(str(root)),
                             repo_slug=REPO, base_rev=BASE_TIP, now=datetime.now(timezone.utc),
                             ancestor=ancestor)
        self.assertFalse(decision.get("ok"), decision)
        self.assertEqual(decision.get("reason"), "preauth_refused")
        self.assertEqual(len(decision.get("refusals") or []), 1)
        self.assertIn(decision["refusals"][0]["reason"], CHECKER_REASONS)


# --------------------------------------------------------------- AC-005 main(): dry run, ledger, merge
class MainPathTest(LaneTestCase):
    def run_main(self, stub, merge, envelopes=(("e1.json", envelope_bytes()),), ledger=LEDGER,
                 ledger_is_dir=False, verdict=None):
        lane = load_lane(stub)
        root = self.store(envelopes, ledger=None if ledger_is_dir else ledger)
        if ledger_is_dir:
            (root / "ledger.json").mkdir()
        merges = []
        events = []
        facts = slice_facts()

        def fake_merge(repo_slug, number, head, *args, **kwargs):
            ledger_now = None
            path = root / "ledger.json"
            if path.is_file():
                ledger_now = json.loads(path.read_text(encoding="utf-8"))
            merges.append({"repo": repo_slug, "number": number, "head": head, "ledger": ledger_now})
            return {"merged": True, "exit_code": 0, "output": "", "command": []}

        patches = {
            "fetch_pull_request": lambda *a, **k: {"head": {"sha": HEAD, "ref": "feat/x"},
                                                   "base": {"ref": "main"}},
            "run_lease_tamper": lambda *a, **k: {"ran": True, "ok": True, "class": "slice", "tier": "T3",
                                                 "script_sha": "9" * 40},
            "collect_facts": lambda *a, **k: copy.deepcopy(facts),
            "measure_review_carry_facts": lambda *a, **k: {},
            "decide": lambda f, a=None: dict(verdict or slice_verdict()),
            "measure_base_rev": lambda *a, **k: BASE_TIP,
            "merge_pull_request": fake_merge,
            "verify_merge": lambda *a, **k: {"verified": True, "merged": True, "head": HEAD,
                                             "merge_sha": "1" * 40},
            "DEFAULT_PREAUTH_DIR": str(root),
        }
        for name, value in patches.items():
            setattr(lane, name, value)
        argv = ["--repo", str(root), "--pr", "50", "--repo-slug", REPO] + (["--merge"] if merge else [])
        out = io.StringIO()
        with mock.patch.dict(os.environ, {"GITHUB_TOKEN": "test-token-not-real"}), \
                contextlib.redirect_stdout(out):
            code = lane.main(argv)
        lines = [line for line in out.getvalue().splitlines() if line.strip()]
        verdict_out = json.loads(lines[-1])
        ledger_after = None
        if (root / "ledger.json").is_file():
            ledger_after = json.loads((root / "ledger.json").read_text(encoding="utf-8"))
        self.assertEqual(stub.forbidden, [])
        return code, verdict_out, merges, ledger_after, out.getvalue()

    def test_a_refused_envelope_blocks_the_merge(self):
        stub = StubPreauth([("refuse", "envelope_expired")])
        code, verdict, merges, ledger, _ = self.run_main(stub, merge=True)
        self.assertEqual(merges, [])
        self.assertEqual(code, 1)
        self.assertFalse(verdict["merge"])
        self.assertEqual(verdict["reason"], "preauth_refused")
        self.assertEqual(ledger, LEDGER)

    def test_no_envelope_blocks_the_merge(self):
        stub = StubPreauth([("ok", ok_result())])
        code, verdict, merges, _, _ = self.run_main(stub, merge=True, envelopes=())
        self.assertEqual(merges, [])
        self.assertEqual(code, 1)
        self.assertEqual(verdict["reason"], "preauth_missing")

    def test_dry_run_never_merges_and_never_records_a_use(self):
        stub = StubPreauth([("ok", ok_result())])
        code, verdict, merges, ledger, _ = self.run_main(stub, merge=False)
        self.assertEqual(merges, [])
        self.assertEqual(ledger, LEDGER)
        self.assertEqual(len(stub.calls), 1, "the dry run must still consult check()")
        self.assertTrue((verdict.get("preauth") or {}).get("ok"), verdict)

    def test_merge_records_the_use_before_merging(self):
        stub = StubPreauth([("ok", ok_result())])
        code, verdict, merges, ledger, _ = self.run_main(stub, merge=True)
        self.assertEqual(len(merges), 1, verdict)
        before = merges[0]["ledger"]
        self.assertIsNotNone(before)
        self.assertEqual([(entry.get("envelope_id"), entry.get("contract_sha256")) for entry in before["consumed"]],
                         [("env-1", CONTRACT)])
        self.assertEqual(before["nonces"], {"nonce-1": "env-1"})
        self.assertEqual(before["attempts"], {CONTRACT: 1})
        self.assertEqual(before["revoked"], [])
        self.assertEqual(before["bees_in_flight"], 0)
        self.assertEqual(ledger, before)
        self.assertEqual(code, 0)
        self.assertTrue(verdict["merge"])

    def test_an_unwritable_ledger_blocks_the_merge(self):
        stub = StubPreauth([("ok", ok_result())])
        code, verdict, merges, _, _ = self.run_main(stub, merge=True, ledger_is_dir=True)
        self.assertEqual(merges, [])
        self.assertEqual(code, 1)
        self.assertEqual(verdict["reason"], "preauth_ledger_unwritable")

    def test_a_refused_decide_verdict_never_reaches_check(self):
        stub = StubPreauth([("ok", ok_result())])
        code, verdict, merges, _, _ = self.run_main(stub, merge=True, verdict=slice_verdict(merge=False))
        self.assertEqual(merges, [])
        self.assertEqual(stub.calls, [])
        self.assertEqual(verdict["reason"], "review_missing")

    def test_the_output_never_carries_envelope_or_signature_bytes(self):
        stub = StubPreauth([("ok", ok_result())])
        _, _, _, _, text = self.run_main(stub, merge=False)
        self.assertNotIn("SIG:e1.json", text)
        self.assertNotIn("owner@example.invalid", text)
        self.assertNotIn("test-token-not-real", text)


if __name__ == "__main__":
    unittest.main()
