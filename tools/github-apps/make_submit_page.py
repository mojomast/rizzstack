#!/usr/bin/env python3
"""Generate a GitHub App manifest and a self-submitting HTML page for it.

Usage:
  python3 make_submit_page.py --role reviewer --owner <owner> [--name <app-name>]
      [--org] [--homepage URL] [--redirect-url URL] [--out DIR]

Writes three files into --out (default ./out-<owner>, created mode 700):
  <app-name>.manifest.json  the filled-in manifest (public)
  <app-name>.html           a page that POSTs the manifest to GitHub (public)
  <app-name>.state.json     the random state + manifest, mode 600; convert.sh
                            checks the state GitHub returns against it

The page must be opened in a browser that is signed in as <owner> (or as an
org owner with --org). GitHub's manifest flow only accepts the manifest as a
form POST, so a plain link cannot carry it. Nothing in these files is secret;
the state is a one-time CSRF check, not a credential.
"""
import argparse
import html
import json
import os
import re
import secrets
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROLES = ("reviewer", "coordinator")
NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 ._-]{0,33}$")  # GitHub app names: 34 chars max
OWNER_RE = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})$")


def fill(template, owner, name, homepage, redirect):
    manifest = json.loads(json.dumps(template).replace("<owner>", owner).replace("<app-name>", name))
    if homepage:
        manifest["url"] = homepage
        manifest["hook_attributes"]["url"] = homepage
    if redirect:
        manifest["redirect_url"] = redirect
    return manifest


def page(manifest, action, role, autosubmit=True):
    blob = json.dumps(manifest, indent=2)
    esc = html.escape(blob, quote=True)
    perms = "".join(
        "<li><code>%s: %s</code></li>" % (html.escape(k), html.escape(v))
        for k, v in manifest["default_permissions"].items()
    )
    return """<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="referrer" content="no-referrer">
<title>Create GitHub App: {name}</title>
<style>
body{{background:#0d1117;color:#e6edf3;font-family:system-ui,sans-serif;max-width:760px;margin:40px auto;padding:0 16px}}
button{{background:#238636;color:#fff;border:0;border-radius:6px;padding:10px 18px;font-size:16px;cursor:pointer}}
pre{{background:#161b22;padding:12px;border-radius:6px;overflow:auto;font-size:12px}}
code{{color:#79c0ff}}
</style></head>
<body>
<h1>Create GitHub App: {name}</h1>
<p>Role: <b>{role}</b>. This page submits the manifest to GitHub {when}. GitHub then shows a
"Create GitHub App" confirmation; nothing is created until you confirm there.</p>
<ul>{perms}</ul>
<p>No webhook (inactive), no events, private app.</p>
<form id="f" method="post" action="{action}">
<input type="hidden" name="manifest" value="{esc}">
<button type="submit">Submit manifest to GitHub</button>
</form>
<h2>Manifest</h2>
<pre>{pre}</pre>
{script}
</body></html>
""".format(name=html.escape(manifest["name"]), role=html.escape(role), perms=perms,
           action=html.escape(action, quote=True), esc=esc, pre=html.escape(blob),
           when="automatically (press the button if nothing happens)" if autosubmit else "when you press the button",
           script='<script>document.getElementById("f").submit();</script>' if autosubmit else "")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--role", required=True, choices=ROLES)
    ap.add_argument("--owner", required=True, help="GitHub user or org that will own the app")
    ap.add_argument("--name", help="app name (default rizzstack-<role>; fallback rizzstack-<role>-<owner>)")
    ap.add_argument("--org", action="store_true", help="owner is an organization")
    ap.add_argument("--homepage", help="app homepage and (inactive) hook URL; default https://github.com/<owner>/rizzstack")
    ap.add_argument("--redirect-url", help="where GitHub sends ?code=&state= after creation; default the homepage repo")
    ap.add_argument("--no-autosubmit", action="store_true", help="wait for a button press instead of submitting on load")
    ap.add_argument("--out", help="output directory (default ./out-<owner>)")
    args = ap.parse_args(argv)

    if not OWNER_RE.match(args.owner):
        ap.error("owner looks wrong: %r" % args.owner)
    name = args.name or "rizzstack-%s" % args.role
    if not NAME_RE.match(name):
        ap.error("app name must be 1-34 chars of letters, digits, space, . _ -")
    template = json.loads((HERE / "manifests" / ("%s.manifest.template.json" % args.role)).read_text())
    manifest = fill(template, args.owner, name, args.homepage, args.redirect_url)

    state = secrets.token_hex(16)
    base = ("https://github.com/organizations/%s/settings/apps/new" % args.owner) if args.org \
        else "https://github.com/settings/apps/new"
    action = "%s?state=%s" % (base, state)

    out = Path(args.out or "out-%s" % args.owner)
    out.mkdir(mode=0o700, parents=True, exist_ok=True)
    (out / ("%s.manifest.json" % name)).write_text(json.dumps(manifest, indent=2) + "\n")
    (out / ("%s.html" % name)).write_text(page(manifest, action, args.role, not args.no_autosubmit))
    state_path = out / ("%s.state.json" % name)
    if state_path.exists():
        state_path.unlink()
    fd = os.open(str(state_path), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w") as fh:
        json.dump({"role": args.role, "owner": args.owner, "org": args.org, "name": name,
                   "state": state, "created": int(time.time()), "manifest": manifest}, fh, indent=2)
    print("wrote %s/%s.{manifest.json,html,state.json}" % (out, name))
    print("open %s in a browser signed in as %s, on the machine that holds the file" % (out / ("%s.html" % name), args.owner))
    return 0


if __name__ == "__main__":
    sys.exit(main())
