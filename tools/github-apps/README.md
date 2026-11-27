# tools/github-apps

Scripts for the reviewer and coordinator GitHub Apps. The full walkthrough, including why the identities are split, the permissions, key custody, rotation and the planned gate by login, is in [docs/github-apps.md](../../docs/github-apps.md).

| File | What it does |
|---|---|
| `manifests/reviewer.manifest.template.json` | Reviewer app manifest: pull requests write; contents, checks and metadata read. `<owner>` and `<app-name>` are placeholders |
| `manifests/coordinator.manifest.template.json` | Coordinator app manifest: contents, pull requests, issues, actions and workflows write; checks, statuses and metadata read. No administration, secrets, members or environments |
| `make_submit_page.py` | Fills in a manifest and writes the self-submitting HTML page (form POST to `settings/apps/new?state=<random>`) plus a mode-600 state file |
| `convert.sh` | Exchanges the one-hour code (after checking its state), writes the key with `O_EXCL` at mode 600, and discards the client and webhook secrets unprinted |
| `verify.sh` | JWT → `GET /app` → `GET /app/installations` → a metadata-only installation token (revoked after use) → exact repository-set check → key fingerprint |
| `fingerprint.sh` | A key's SHA256 fingerprint, as shown on the app's settings page |

Requirements: `bash`, `python3` (standard library only) and `openssl`. Nothing here prints a key, code or token. Never commit what these scripts write: keys, records and state files belong in private directories outside any repository.

```sh
python3 make_submit_page.py --role reviewer --owner <owner> --out ~/rizzstack-apps-setup
# open the .html in the owner's browser, confirm, copy the redirect address
./convert.sh ~/rizzstack-apps-setup/rizzstack-reviewer.state.json
# install: https://github.com/apps/<slug>/installations/new → Only select repositories
./verify.sh rizzstack-reviewer --repos workflow,hyvv,bundlebees,orbitdesktop,orbit-control-plane,uirizz,rizzstack
```
