#!/usr/bin/env bash
# Exchange a GitHub App manifest code for the new app's credentials.
#
# Usage: convert.sh <app-name>.state.json [--keydir DIR] [--skip-state-check]
#
# Run it on the host that will USE the key (the always-on orchestrator host), as
# the OS user that will read it. It prompts (hidden input, nothing in argv or shell
# history) for the full address GitHub redirected to after "Create GitHub App",
# e.g. https://github.com/<owner>/rizzstack?code=...&state=...
#
# - checks the returned state against the state file written by make_submit_page.py
# - POSTs /app-manifests/{code}/conversions (no auth needed; the code is single-use
#   and expires one hour after creation)
# - writes the private key to $KEYDIR/<slug>.pem with O_EXCL at mode 600, in a mode
#   700 directory; refuses to overwrite an existing file
# - discards client_secret and webhook_secret without printing them
# - writes and prints only safe fields (id, slug, client_id, owner, permissions) to
#   $KEYDIR/<slug>.json
# - fails if the created app's permissions or events differ from the manifest
#
# The key is never printed, logged or echoed. Default KEYDIR: ~/.config/rizzstack-apps
set -euo pipefail
umask 077

state_file=""; keydir="${RIZZSTACK_APPS_KEYDIR:-$HOME/.config/rizzstack-apps}"; skip_state=0
while [ $# -gt 0 ]; do
  case "$1" in
    --keydir) keydir="${2:?--keydir needs a value}"; shift 2;;
    --skip-state-check) skip_state=1; shift;;
    -h|--help) sed -n '2,23p' "$0"; exit 0;;
    *) [ -z "$state_file" ] || { echo "unexpected argument: $1" >&2; exit 2; }; state_file="$1"; shift;;
  esac
done
[ -n "$state_file" ] || { echo "usage: convert.sh <app-name>.state.json [--keydir DIR] [--skip-state-check]" >&2; exit 2; }
[ -r "$state_file" ] || { echo "cannot read $state_file" >&2; exit 2; }
command -v python3 >/dev/null || { echo "python3 is required" >&2; exit 2; }

read -rs -p "Paste the full redirect address (or the bare code with --skip-state-check): " reply; echo
REPLY_URL="$reply" STATE_FILE="$state_file" KEYDIR="$keydir" SKIP_STATE="$skip_state" \
  nice -n 10 timeout 60 python3 - <<'PY'
import json, os, sys, time, urllib.error, urllib.parse, urllib.request

reply = os.environ.pop("REPLY_URL").strip()
st = json.load(open(os.environ["STATE_FILE"]))
keydir = os.path.expanduser(os.environ["KEYDIR"])
skip_state = os.environ["SKIP_STATE"] == "1"

if "code=" in reply:
    q = urllib.parse.parse_qs(urllib.parse.urlsplit(reply).query)
    code = (q.get("code") or [""])[0]
    state = (q.get("state") or [""])[0]
else:
    code, state = reply, ""
del reply
if not code or not all(c.isalnum() or c in "-_" for c in code):
    sys.exit("no usable code in what was pasted")
if not skip_state:
    if not state:
        sys.exit("no state in what was pasted; paste the full address, or pass --skip-state-check")
    if state != st["state"]:
        sys.exit("state mismatch: this code was not issued for %s; not exchanging it" % st["name"])
age = time.time() - st.get("created", 0)
if age > 3600:
    print("warning: the page was generated %d minutes ago; codes expire one hour after creation"
          % (age // 60), file=sys.stderr)

os.makedirs(keydir, mode=0o700, exist_ok=True)
os.chmod(keydir, 0o700)

req = urllib.request.Request(
    "https://api.github.com/app-manifests/%s/conversions" % urllib.parse.quote(code),
    method="POST", data=b"",
    headers={"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28",
             "User-Agent": "rizzstack-github-apps-convert"})
del code
try:
    with urllib.request.urlopen(req, timeout=30) as r:
        data = json.load(r)
except urllib.error.HTTPError as e:
    try:
        msg = json.loads(e.read()).get("message", "")
    except Exception:
        msg = ""
    sys.exit("conversion failed: HTTP %s %s (codes are single-use and expire after an hour)" % (e.code, msg))

pem = data.pop("pem")
data.pop("client_secret", None)
data.pop("webhook_secret", None)
slug = data["slug"]
path = os.path.join(keydir, "%s.pem" % slug)
try:
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
except FileExistsError:
    sys.exit("refusing to overwrite %s; the app exists but its key was NOT saved. "
             "Generate a new key on the app's settings page instead." % path)
with os.fdopen(fd, "w") as fh:
    fh.write(pem)
os.chmod(path, 0o600)
del pem

record = {
    "role": st["role"], "name": data.get("name"), "slug": slug, "bot_login": "%s[bot]" % slug,
    "app_id": data["id"], "client_id": data.get("client_id"),
    "owner": (data.get("owner") or {}).get("login"),
    "permissions": data.get("permissions"), "events": data.get("events"),
    "manifest_permissions": st["manifest"]["default_permissions"],
    "pem_path": path,
}
rec_path = os.path.join(keydir, "%s.json" % slug)
with open(rec_path, "w") as fh:
    json.dump(record, fh, indent=2)
print(json.dumps(record, indent=2))

problems = []
if record["permissions"] != record["manifest_permissions"]:
    problems.append("permissions differ from the manifest")
if record["events"]:
    problems.append("app subscribes to events")
if record["name"] != st["name"]:
    problems.append("app was created under another name (%s); was the name taken?" % record["name"])
for p in problems:
    print("PROBLEM: " + p, file=sys.stderr)
sys.exit(3 if problems else 0)
PY
