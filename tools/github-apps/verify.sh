#!/usr/bin/env bash
# Verify a rizzstack GitHub App and its installation without printing any token.
#
# Usage: verify.sh <slug> --repos repo1,repo2,... [--owner OWNER] [--keydir DIR]
#
# Reads $KEYDIR/<slug>.json (written by convert.sh) and $KEYDIR/<slug>.pem, then:
#   1. builds an RS256 JWT (10 min) from the key and the App ID (openssl signs it)
#   2. GET /app                 name, owner, permissions == manifest, no events
#   3. GET /app/installations   exactly one, on OWNER, repository_selection "selected"
#   4. POST /app/installations/{id}/access_tokens   narrowed to metadata:read
#   5. GET /installation/repositories   must be exactly OWNER/<each --repos entry>
#   6. DELETE /installation/token        revokes the token straight away
#   7. prints the key's SHA256 fingerprint (compare it with the app's settings page)
#
# Prints safe fields only. Exit 0 when everything matches, 3 when something does not.
# Default KEYDIR: ~/.config/rizzstack-apps. OWNER defaults to the app's owner.
set -euo pipefail
umask 077

slug=""; repos=""; owner=""; keydir="${RIZZSTACK_APPS_KEYDIR:-$HOME/.config/rizzstack-apps}"
while [ $# -gt 0 ]; do
  case "$1" in
    --repos) repos="${2:?--repos needs a value}"; shift 2;;
    --owner) owner="${2:?--owner needs a value}"; shift 2;;
    --keydir) keydir="${2:?--keydir needs a value}"; shift 2;;
    -h|--help) sed -n '2,17p' "$0"; exit 0;;
    *) [ -z "$slug" ] || { echo "unexpected argument: $1" >&2; exit 2; }; slug="$1"; shift;;
  esac
done
[ -n "$slug" ] && [ -n "$repos" ] || { echo "usage: verify.sh <slug> --repos repo1,repo2,... [--owner OWNER] [--keydir DIR]" >&2; exit 2; }
command -v openssl >/dev/null || { echo "openssl is required" >&2; exit 2; }

SLUG="$slug" REPOS="$repos" OWNER="$owner" KEYDIR="$keydir" nice -n 10 timeout 120 python3 - <<'PY'
import base64, json, os, stat, subprocess, sys, time, urllib.error, urllib.request

slug = os.environ["SLUG"]
keydir = os.path.expanduser(os.environ["KEYDIR"])
info = json.load(open(os.path.join(keydir, "%s.json" % slug)))
pem = os.path.join(keydir, "%s.pem" % slug)
owner = os.environ["OWNER"] or info["owner"]
expected = sorted("%s/%s" % (owner, r.strip()) for r in os.environ["REPOS"].split(",") if r.strip())
problems = []

mode = stat.S_IMODE(os.stat(pem).st_mode)
if mode & 0o077:
    problems.append("key file mode is %o; it must be 600" % mode)

def b64(raw):
    return base64.urlsafe_b64encode(raw).rstrip(b"=")

now = int(time.time())
signing = (b64(json.dumps({"alg": "RS256", "typ": "JWT"}).encode()) + b"." +
           b64(json.dumps({"iat": now - 60, "exp": now + 540, "iss": str(info["app_id"])}).encode()))
sig = subprocess.run(["openssl", "dgst", "-sha256", "-sign", pem], input=signing,
                     capture_output=True, check=True).stdout
jwt = (signing + b"." + b64(sig)).decode()
del sig

def call(method, path, auth, body=None, ok_empty=False):
    req = urllib.request.Request(
        "https://api.github.com" + path, method=method,
        data=None if body is None else json.dumps(body).encode(),
        headers={"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28",
                 "User-Agent": "rizzstack-github-apps-verify", "Authorization": auth})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            raw = r.read()
            return json.loads(raw) if raw else None
    except urllib.error.HTTPError as e:
        try:
            msg = json.loads(e.read()).get("message", "")
        except Exception:
            msg = ""
        sys.exit("%s %s failed: HTTP %s %s" % (method, path.split("?")[0], e.code, msg))

out = {"pem_path": pem, "pem_mode": oct(mode)}
app = call("GET", "/app", "Bearer " + jwt)
out.update(name=app["name"], slug=app["slug"], bot_login=app["slug"] + "[bot]", app_id=app["id"],
           client_id=app.get("client_id"), owner=app["owner"]["login"],
           permissions=app["permissions"], events=app["events"])
if app["slug"] != slug:
    problems.append("key belongs to %s, not %s" % (app["slug"], slug))
if app["permissions"] != info["manifest_permissions"]:
    problems.append("app permissions differ from the manifest")
if app["events"]:
    problems.append("app subscribes to events")

insts = call("GET", "/app/installations", "Bearer " + jwt)
out["installations"] = [{"id": i["id"], "account": i["account"]["login"],
                         "repository_selection": i["repository_selection"],
                         "permissions": i["permissions"], "events": i["events"]} for i in insts]
if len(insts) != 1:
    problems.append("expected exactly 1 installation, found %d" % len(insts))
for i in insts:
    if i["repository_selection"] != "selected":
        problems.append("installation %s can see ALL repositories; choose 'Only select repositories'" % i["id"])
    if i["account"]["login"].lower() != owner.lower():
        problems.append("installation %s is on %s, not %s" % (i["id"], i["account"]["login"], owner))
    if i["permissions"] != info["manifest_permissions"]:
        problems.append("installation %s has not accepted the current permissions" % i["id"])

if insts:
    iid = insts[0]["id"]
    out["installation_id"] = iid
    tok = call("POST", "/app/installations/%s/access_tokens" % iid, "Bearer " + jwt,
               {"permissions": {"metadata": "read"}})["token"]
    repos, page = [], 1
    try:
        while True:
            r = call("GET", "/installation/repositories?per_page=100&page=%d" % page, "token " + tok)
            repos += [x["full_name"] for x in r["repositories"]]
            if len(repos) >= r["total_count"] or not r["repositories"]:
                break
            page += 1
    finally:
        try:
            call("DELETE", "/installation/token", "token " + tok)
            out["token_revoked"] = True
        except SystemExit:
            out["token_revoked"] = False
        del tok
    out["repositories"] = sorted(repos)
    if sorted(r.lower() for r in repos) != [e.lower() for e in expected]:
        missing = sorted(set(e.lower() for e in expected) - set(r.lower() for r in repos))
        extra = sorted(set(r.lower() for r in repos) - set(e.lower() for e in expected))
        problems.append("repository set differs (missing: %s; extra: %s)" % (missing, extra))
del jwt

der = subprocess.run(["openssl", "rsa", "-in", pem, "-pubout", "-outform", "DER"],
                     capture_output=True, check=True).stdout
dgst = subprocess.run(["openssl", "dgst", "-sha256", "-binary"], input=der,
                      capture_output=True, check=True).stdout
out["key_sha256_fingerprint"] = "SHA256:" + base64.b64encode(dgst).decode()
out["problems"] = problems
out["ok"] = not problems
print(json.dumps(out, indent=2))
sys.exit(0 if not problems else 3)
PY
