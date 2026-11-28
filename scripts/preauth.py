"""Signed pre-authorization envelopes: parsing, policy and one-use decisions.

This module is parsing and policy only.  It starts no process, writes no file and uses no
temporary file.  The one way it checks a signature is the frozen verifier's private entry
point, ``_ssh_verify``, called with the exact envelope bytes.  It opens exactly two files,
both through the constants below and both in the checkout this module lives in:
``policy/allowed_signers`` (the trust root) and ``policy/preauth.md`` (the policy the decision
is bound to by sha256).  Every doubt, including missing measured state, refuses with one
literal reason from the closed set ``REFUSAL_REASONS`` through the single ``refuse`` helper.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
import re

from datetime import datetime, timedelta, timezone

from preauth_verify import _ssh_verify

NAMESPACE = "rizzstack-preauth"
MAX_TTL_SECONDS = 7 * 24 * 3600
VERIFY_TIMEOUT_SECONDS = 20
SCHEMA_ID = "rizzstack.preauth/2"
NAMESPACE_OPTION = 'namespaces="rizzstack-preauth"'

TRUST_ROOT = __file__.rsplit("/", 2)[0] + "/policy/allowed_signers"
POLICY = __file__.rsplit("/", 2)[0] + "/policy/preauth.md"

OWNER_FINGERPRINTS = ()

REQUIRED_FIELDS = frozenset({
    "schema", "id", "issuer", "issued_at", "expires_at", "nonce", "action_class", "repos",
    "bases", "contracts", "review", "budget", "conditions", "max_uses", "created_from",
})

ALLOWED_ACTION_CLASSES = frozenset({
    "merge_slice", "refreeze_contract", "vendor_sync", "close_superseded_pr", "dispatch_wave",
})

REFUSAL_REASONS = frozenset({
    "envelope_schema_invalid", "envelope_clock_invalid", "envelope_policy_unproven",
    "envelope_signers_invalid", "envelope_signer_untrusted", "envelope_bad_signature",
    "envelope_verifier_unavailable", "envelope_expired", "envelope_not_yet_valid",
    "envelope_ttl_exceeded", "envelope_facts_unproven", "envelope_revoked",
    "envelope_action_forbidden", "envelope_repo_mismatch", "envelope_base_mismatch",
    "envelope_contract_not_listed", "envelope_tier_exceeded", "envelope_oracle_unproven",
    "envelope_oracle_changed", "envelope_nonce_reused", "envelope_replayed",
    "envelope_budget_exhausted",
})

BUDGET_FIELDS = frozenset({"max_merges", "max_bees", "max_attempts_per_contract"})
CONTRACT_FIELDS = frozenset({"repo", "slice", "contract_sha256", "source_rev", "max_tier"})
KEY_TYPES = frozenset({"ssh-ed25519", "sk-ssh-ed25519@openssh.com"})
TIERS = {"T1": 1, "T2": 2, "T3": 3}
CONFIG_NAMES = frozenset({
    "pytest.ini", "tox.ini", "setup.cfg", "pyproject.toml", "setup.py", "noxfile.py", ".coveragerc",
})
HOOK_NAMES = frozenset({"sitecustomize.py", "usercustomize.py"})
STAMP = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z")
TRUST_ROOT_BYTES = frozenset(range(0x20, 0x7f)) | {9, 10, 13}
PRINCIPAL = re.compile(r"[A-Za-z0-9._@-]{1,64}")


class PreauthRefused(Exception):
    """A named refusal."""


def refuse(reason):
    error = PreauthRefused(reason)
    error.reason = reason
    raise error


def read_trust_root():
    try:
        with open(TRUST_ROOT, "rb") as handle:
            return handle.read()
    except OSError:
        refuse("envelope_signer_untrusted")


def read_policy():
    try:
        with open(POLICY, "rb") as handle:
            return handle.read()
    except OSError:
        refuse("envelope_policy_unproven")


def unique_object(pairs):
    seen = set()
    for key, item in pairs:
        if key in seen:
            refuse("envelope_schema_invalid")
        seen.add(key)
    return dict(pairs)


def parse_json(data):
    try:
        value = json.loads(data, object_pairs_hook=unique_object)
    except (ValueError, TypeError):
        refuse("envelope_schema_invalid")
    if not isinstance(value, dict):
        refuse("envelope_schema_invalid")
    return value


def parse_stamp(text):
    if not isinstance(text, str) or not STAMP.fullmatch(text):
        refuse("envelope_schema_invalid")
    try:
        moment = datetime.strptime(text, "%Y-%m-%dT%H:%M:%SZ")
    except ValueError:
        refuse("envelope_schema_invalid")
    return moment.replace(tzinfo=timezone.utc)


def positive_int(value):
    return isinstance(value, int) and not isinstance(value, bool) and value >= 1


def budget_ok(budget):
    if not isinstance(budget, dict):
        refuse("envelope_schema_invalid")
    if set(budget.keys()) != BUDGET_FIELDS:
        refuse("envelope_schema_invalid")
    merges = budget.get("max_merges")
    bees = budget.get("max_bees")
    attempts = budget.get("max_attempts_per_contract")
    if not positive_int(merges):
        refuse("envelope_schema_invalid")
    if not isinstance(bees, int) or isinstance(bees, bool) or bees < 1 or bees > 4:
        refuse("envelope_schema_invalid")
    if not positive_int(attempts):
        refuse("envelope_schema_invalid")


def contract_fields(entry):
    if not isinstance(entry, dict):
        refuse("envelope_schema_invalid")
    for name in CONTRACT_FIELDS:
        if name not in entry or not isinstance(entry.get(name), str):
            refuse("envelope_schema_invalid")
    if entry.get("max_tier") not in TIERS:
        refuse("envelope_schema_invalid")


def validate_envelope(value):
    if set(value.keys()) != REQUIRED_FIELDS:
        refuse("envelope_schema_invalid")
    if value.get("schema") != SCHEMA_ID:
        refuse("envelope_schema_invalid")
    if not isinstance(value.get("id"), str) or not value.get("id"):
        refuse("envelope_schema_invalid")
    issuer = value.get("issuer")
    if not principal_ok(issuer):
        refuse("envelope_schema_invalid")
    issued = parse_stamp(value.get("issued_at"))
    expires = parse_stamp(value.get("expires_at"))
    if expires <= issued:
        refuse("envelope_schema_invalid")
    if not isinstance(value.get("nonce"), str) or not value.get("nonce"):
        refuse("envelope_schema_invalid")
    repos = value.get("repos")
    if not isinstance(repos, list):
        refuse("envelope_schema_invalid")
    for item in repos:
        if not isinstance(item, str) or not item:
            refuse("envelope_schema_invalid")
    bases = value.get("bases")
    if not isinstance(bases, dict):
        refuse("envelope_schema_invalid")
    for key in bases.keys():
        if not isinstance(key, str) or not isinstance(bases.get(key), str):
            refuse("envelope_schema_invalid")
    contracts = value.get("contracts")
    if not isinstance(contracts, list):
        refuse("envelope_schema_invalid")
    for entry in contracts:
        contract_fields(entry)
    if not isinstance(value.get("review"), dict):
        refuse("envelope_schema_invalid")
    budget_ok(value.get("budget"))
    if not isinstance(value.get("conditions"), list):
        refuse("envelope_schema_invalid")
    if not positive_int(value.get("max_uses")):
        refuse("envelope_schema_invalid")
    if not isinstance(value.get("created_from"), str):
        refuse("envelope_schema_invalid")


def principal_ok(value):
    return isinstance(value, str) and bool(PRINCIPAL.fullmatch(value)) and value[0] != "-"


def load_envelope(data):
    value = parse_json(data)
    validate_envelope(value)
    return value


def fingerprint_of(blob):
    try:
        raw = base64.b64decode(blob, validate=True)
    except binascii.Error:
        refuse("envelope_signers_invalid")
    digest = hashlib.sha256(raw).digest()
    return "SHA256:" + base64.b64encode(digest).decode("ascii").rstrip("=")


def trust_fingerprints(raw, trusted):
    if not isinstance(raw, bytes):
        refuse("envelope_signers_invalid")
    for value in raw:
        if value not in TRUST_ROOT_BYTES:
            refuse("envelope_signers_invalid")
    found = []
    for line in raw.split(b"\n"):
        if line.endswith(b"\r"):
            line = line[:-1]
        if b"\r" in line:
            refuse("envelope_signers_invalid")
        stripped = line.strip(b" \t")
        if not stripped or stripped.startswith(b"#"):
            continue
        parts = stripped.split()
        if len(parts) < 4:
            refuse("envelope_signers_invalid")
        principal = parts[0].decode("ascii")
        if not principal_ok(principal):
            refuse("envelope_signers_invalid")
        if parts[1].decode("ascii") != NAMESPACE_OPTION:
            refuse("envelope_signers_invalid")
        if parts[2].decode("ascii") not in KEY_TYPES:
            refuse("envelope_signers_invalid")
        found.append(fingerprint_of(parts[3].decode("ascii")))
    if not found:
        refuse("envelope_signer_untrusted")
    for item in found:
        if item not in trusted:
            refuse("envelope_signer_untrusted")


def validate_facts(facts):
    if not isinstance(facts, dict):
        refuse("envelope_facts_unproven")
    for name in ("revoked", "consumed", "nonces", "attempts", "bees_in_flight"):
        if name not in facts:
            refuse("envelope_facts_unproven")
    revoked = facts.get("revoked")
    consumed = facts.get("consumed")
    nonces = facts.get("nonces")
    attempts = facts.get("attempts")
    bees = facts.get("bees_in_flight")
    if not isinstance(revoked, list) or not isinstance(consumed, list):
        refuse("envelope_facts_unproven")
    if not isinstance(nonces, dict) or not isinstance(attempts, dict):
        refuse("envelope_facts_unproven")
    if isinstance(bees, bool) or not isinstance(bees, int) or bees < 0:
        refuse("envelope_facts_unproven")
    for item in revoked:
        if not isinstance(item, str):
            refuse("envelope_facts_unproven")
    for item in consumed:
        if not isinstance(item, dict):
            refuse("envelope_facts_unproven")
        if not isinstance(item.get("envelope_id"), str):
            refuse("envelope_facts_unproven")
        if not isinstance(item.get("contract_sha256"), str):
            refuse("envelope_facts_unproven")
    for key in nonces.keys():
        if not isinstance(key, str) or not isinstance(nonces.get(key), str):
            refuse("envelope_facts_unproven")
    for key in attempts.keys():
        value = attempts.get(key)
        if not isinstance(key, str) or isinstance(value, bool) or not isinstance(value, int) or value < 0:
            refuse("envelope_facts_unproven")
    return (revoked, consumed, nonces, attempts, bees)


def normalize_path(value):
    if not isinstance(value, str):
        refuse("envelope_oracle_unproven")
    text = value.replace("\\", "/")
    if text.startswith("/"):
        refuse("envelope_oracle_unproven")
    result = []
    for part in text.split("/"):
        if not part or part == ".":
            continue
        if part == "..":
            if not result:
                refuse("envelope_oracle_unproven")
            result.pop()
        else:
            result.append(part)
    if not result:
        refuse("envelope_oracle_unproven")
    return "/".join(result)


def normalized_map(measured):
    result = {}
    for key, value in measured.items():
        if not isinstance(key, str) or "\\" in key:
            refuse("envelope_oracle_unproven")
        if not (isinstance(value, str) and len(value) in (40, 64)
                and all(ch in "0123456789abcdefABCDEF" for ch in value)):
            refuse("envelope_oracle_unproven")
        path = normalize_path(key)
        if path in result:
            refuse("envelope_oracle_unproven")
        result[path] = value
    return result


def is_oracle(path, test_files):
    lower = path.lower()
    parts = lower.split("/")
    base = parts[-1]
    if lower in test_files:
        return True
    if parts[0] == "tests":
        return True
    if base == "conftest.py":
        return True
    if base in CONFIG_NAMES or base in HOOK_NAMES:
        return True
    if base.endswith(".pth"):
        return True
    if re.fullmatch(r"test_[^/]*\.py", base) or re.fullmatch(r"[^/]*_test\.py", base):
        return True
    for part in parts[:-1]:
        if part in {"fixtures", "data", "helpers"}:
            return True
    if base == "__init__.py":
        return True
    if base.startswith("requirements") or base in {"uv.lock", "poetry.lock"} or base.startswith("pipfile"):
        return True
    if lower.startswith(".github/workflows/"):
        return True
    return False


def oracle_paths(paths, test_files=None):
    wanted = set()
    if test_files is not None:
        for item in test_files:
            wanted.add(normalize_path(item).lower())
    result = set()
    for item in paths:
        normalized = normalize_path(item)
        if is_oracle(normalized, wanted):
            result.add(normalized)
    return result


def _decide(data, signature, allowed_signers, allowed_signers_bytes, policy_bytes, trusted_fingerprints,
            repo, base_rev, contract_sha256, tier, now, facts, is_ancestor):
    value = load_envelope(data)
    if not isinstance(now, datetime) or now.tzinfo is None:
        refuse("envelope_clock_invalid")
    trust_fingerprints(allowed_signers_bytes, trusted_fingerprints)
    issuer = value.get("issuer")
    result = _ssh_verify(data, signature, allowed_signers, issuer)
    if result is None:
        refuse("envelope_verifier_unavailable")
    if result is not True:
        refuse("envelope_bad_signature")
    action = value.get("action_class")
    if not isinstance(action, str) or action not in ALLOWED_ACTION_CLASSES:
        refuse("envelope_action_forbidden")
    issued = parse_stamp(value.get("issued_at"))
    expires = parse_stamp(value.get("expires_at"))
    if now >= expires:
        refuse("envelope_expired")
    if now < issued:
        refuse("envelope_not_yet_valid")
    if (expires - issued).total_seconds() > MAX_TTL_SECONDS:
        refuse("envelope_ttl_exceeded")
    revoked, consumed, nonces, attempts, bees = validate_facts(facts)
    envelope_id = value.get("id")
    nonce = value.get("nonce")
    if envelope_id in revoked:
        refuse("envelope_revoked")
    recorded = nonces.get(nonce)
    if recorded is not None and recorded != envelope_id:
        refuse("envelope_nonce_reused")
    for entry in consumed:
        if entry.get("contract_sha256") == contract_sha256:
            refuse("envelope_replayed")
    budget = value.get("budget")
    uses = 0
    for entry in consumed:
        if entry.get("envelope_id") == envelope_id:
            uses += 1
    limit = min(value.get("max_uses"), budget.get("max_merges"))
    if uses >= limit:
        refuse("envelope_budget_exhausted")
    if attempts.get(contract_sha256, 0) >= budget.get("max_attempts_per_contract"):
        refuse("envelope_budget_exhausted")
    if action == "dispatch_wave" and bees >= budget.get("max_bees"):
        refuse("envelope_budget_exhausted")
    repos = value.get("repos")
    if repo not in repos:
        refuse("envelope_repo_mismatch")
    bases = value.get("bases")
    if repo not in bases:
        refuse("envelope_base_mismatch")
    if is_ancestor is None:
        refuse("envelope_base_mismatch")
    try:
        descended = is_ancestor(bases.get(repo), base_rev)
    except Exception:
        descended = False
    if not descended:
        refuse("envelope_base_mismatch")
    entry = None
    for item in value.get("contracts"):
        if item.get("repo") == repo and item.get("contract_sha256") == contract_sha256:
            entry = item
    if entry is None:
        refuse("envelope_contract_not_listed")
    max_tier = entry.get("max_tier")
    if not isinstance(tier, str) or tier not in TIERS:
        refuse("envelope_tier_exceeded")
    rank = TIERS.get(tier)
    if rank > TIERS.get(max_tier, 0):
        refuse("envelope_tier_exceeded")
    if action == "refreeze_contract":
        oracle = facts.get("oracle")
        if not isinstance(oracle, dict):
            refuse("envelope_oracle_unproven")
        old = oracle.get("old")
        new = oracle.get("new")
        if not isinstance(old, dict) or not isinstance(new, dict):
            refuse("envelope_oracle_unproven")
        test_files = entry.get("test_files", [])
        if not isinstance(test_files, list):
            test_files = []
        old_map = normalized_map(old)
        new_map = normalized_map(new)
        old_paths = oracle_paths(old_map.keys(), test_files)
        new_paths = oracle_paths(new_map.keys(), test_files)
        if old_paths != new_paths:
            refuse("envelope_oracle_changed")
        for path in old_paths:
            if old_map.get(path) != new_map.get(path):
                refuse("envelope_oracle_changed")
    return {
        "ok": True,
        "envelope_id": envelope_id,
        "contract_sha256": contract_sha256,
        "max_tier": max_tier,
        "policy_sha256": hashlib.sha256(policy_bytes).hexdigest(),
        "allowed_signers_sha256": hashlib.sha256(allowed_signers_bytes).hexdigest(),
    }


def check(data, signature, repo, base_rev, contract_sha256, tier, now, facts, is_ancestor):
    trust_bytes = read_trust_root()
    policy_bytes = read_policy()
    return _decide(data, signature, TRUST_ROOT, trust_bytes, policy_bytes, OWNER_FINGERPRINTS,
                   repo, base_rev, contract_sha256, tier, now, facts, is_ancestor)


def _check_for_tests(data, signature, *, allowed_signers, allowed_signers_bytes, policy_bytes,
                     trusted_fingerprints, repo, base_rev, contract_sha256, tier, now, facts,
                     is_ancestor):
    """Test-only entry point: the decision tests pass a temporary trust root and pin."""
    return _decide(data, signature, allowed_signers, allowed_signers_bytes, policy_bytes,
                   trusted_fingerprints, repo, base_rev, contract_sha256, tier, now, facts, is_ancestor)
