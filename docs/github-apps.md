# GitHub App identities: reviewer and coordinator

rizzstack splits the GitHub work across three identities so that each repository's gate can eventually tell who did what from the GitHub login, not from the text of a comment. This guide sets the split up for your own account or organization from start to finish. The scripts it uses are in [`tools/github-apps/`](../tools/github-apps/).

| Identity | Who uses it | Job |
|---|---|---|
| **Owner** (your own GitHub account) | you, a human | Decide: owner approvals, typed yeses, settings, rulesets, creating and installing the apps |
| **Coordinator app** (`rizzstack-coordinator[bot]`) | the orchestrator (for example a Hermes agent running the BundleBees coordinator) and its scheduled lanes | Build and land: push branches, open, update and merge PRs, label and comment, rerun CI |
| **Reviewer app** (`rizzstack-reviewer[bot]`) | the outside-review poster | Review: post PR reviews (approve or request changes) and nothing else |

Examples use the default names. If you had to fall back to `rizzstack-reviewer-<owner>`, the bot login follows the app's slug: `<slug>[bot]`.

## Why split the identities

If the owner, the orchestrator and its review lanes all act through one personal token, GitHub records every action under the same login. The gate then can't tell an owner approval from an agent's review, or an independent review from one the coordinator wrote about its own work. All it can trust is the wording of a review, and anything holding the token can write those words.

With three identities:

- the coordinator can build and merge, but any review it posts counts for nothing;
- the reviewer can review, but it can't push or merge what it reviewed;
- the owner's login is never used by an agent. Once the coordinator's GitHub calls move to the app, the owner's personal token comes off the orchestrator host.

The split only protects you if each private key is readable only by the process that should use it. See [Key custody](#key-custody).

## Permissions

Both apps are private, have no webhook (the hook is inactive, with no events subscribed), and are installed with **Only select repositories** on the rizzstack repositories. Ask for nothing beyond these.

### Reviewer app

| Permission | Access | Why |
|---|---|---|
| Pull requests | write | Submit PR reviews (`POST /repos/{owner}/{repo}/pulls/{n}/reviews`) |
| Contents | read | Read the diff, files and commits under review |
| Checks | read | See whether CI passed on the reviewed head |
| Metadata | read | Mandatory for every app |

GitHub has no "reviews only" permission. Pull requests write also allows PR comments, labels and edits to titles and bodies, so "reviews only" is enforced by the poster that holds the key, which only submits verdicts. The reviewer can't merge (merging needs contents write) and can't push.

### Coordinator app

| Permission | Access | Why |
|---|---|---|
| Contents | write | Push feature branches; merge PRs (`PUT /repos/{owner}/{repo}/pulls/{n}/merge`) |
| Pull requests | write | Open, update and close PRs; request reviewers |
| Issues | write | Labels, and comments on PRs (PR comments go through the issues API); open tracking issues |
| Actions | write | Rerun failed jobs (`POST /repos/{owner}/{repo}/actions/runs/{id}/rerun-failed-jobs`) and dispatch workflows |
| Workflows | write | Push commits that change files under `.github/workflows/` (CI changes still land through a PR) |
| Checks | read | Read check runs before merging |
| Statuses | read | Read commit statuses before merging |
| Metadata | read | Mandatory for every app |

### Deliberately excluded

- **Administration:** without it the coordinator can't change rulesets, branch protection, collaborators or repository settings, so it can't loosen the gate that checks it.
- **Secrets, Variables, Environments:** it can't read or change CI secrets or deployment environments.
- **Members and any organization permission.**
- **Webhooks and events:** neither app receives deliveries, so there's no endpoint or webhook secret to protect.
- **All repositories:** install only on the repositories you list.

Never add either app as a ruleset **bypass actor**. Merges still have to satisfy the required checks (`ci-gate`) like any other PR.

Workflows write is the most powerful of these: a commit can change the CI that gates it. That's why changes under `.github/` and to gate scripts are high-risk in every rizzstack gate and need the owner or an independent reviewer, whoever authors them.

## What you need

- A GitHub account (or an organization you own) holding the repositories.
- A browser signed in as that owner. Two steps can only be done there: confirming each app's creation and confirming each installation.
- An always-on orchestrator host with `python3`, `openssl` and `bash`, where the keys will live.
- A copy of this repository (or just `tools/github-apps/`) on that host.

## 1. Generate the manifests and submit pages

On the orchestrator host:

```sh
cd tools/github-apps
python3 make_submit_page.py --role reviewer    --owner <owner> --out ~/rizzstack-apps-setup
python3 make_submit_page.py --role coordinator --owner <owner> --out ~/rizzstack-apps-setup
```

Each run writes three files into the output directory (mode 700):

- `<app-name>.manifest.json`: the filled-in manifest from `manifests/<role>.manifest.template.json`;
- `<app-name>.html`: a page with a form that POSTs the manifest to `https://github.com/settings/apps/new?state=<random>`, or to `https://github.com/organizations/<org>/settings/apps/new?state=<random>` with `--org`;
- `<app-name>.state.json` (mode 600): the random state and the manifest. `convert.sh` checks the state GitHub returns against it.

Options:

- `--name`: change the app name. App names are unique across all of GitHub, including private apps you can't see, and can be at most 34 characters. If GitHub says the name is taken, rerun with `--name rizzstack-reviewer-<owner>` (or `rizzstack-coordinator-<owner>`). If that's over 34 characters, shorten the prefix (for example `rs-coordinator-<owner>`). The bot login and every later step use the new slug.
- `--homepage`: point the app's homepage somewhere other than `https://github.com/<owner>/rizzstack`.
- `--redirect-url`: choose where GitHub sends you after creation (the default is the homepage repository).
- `--no-autosubmit`: wait for a button press instead of submitting as soon as the page opens.

## 2. Create each app (browser)

GitHub's manifest flow only accepts the manifest as a **form POST** from a page in the owner's browser. A plain link can't carry it. So:

1. Open `<app-name>.html` **in a browser on the machine that holds the file** (a `file://` page). If the files are on a headless host, copy the two HTML files to your own computer. They contain only the public manifest and the state.
2. The page submits itself. GitHub shows "Create GitHub App for &lt;owner&gt;" with the name and permissions. **Check them against the tables above**, then click **Create GitHub App**.
3. GitHub redirects to the redirect URL with `?code=…&state=…` appended. Copy the **whole address** from the address bar.

Notes:

- **Redirect target.** A listener on `http://127.0.0.1:<port>/callback` only works when the browser runs on the same machine as the listener. A `127.0.0.1` redirect always lands on the computer running the browser. With the default redirect (a repository page) you copy the address instead, and nothing needs to be running.
- **Hosting the page elsewhere is optional.** If you can't open a local file (on a phone, for example), you can serve the page from your own GitHub Pages site. Third-party raw-HTML proxies (githack, htmlpreview and similar) also work, but they are **untrusted**. They never see a secret, but they could serve a modified page with other permissions or another redirect. If you use one, check GitHub's confirmation screen especially carefully.
- **The code is a secret until it's exchanged.** Whoever exchanges it first receives the app's private key. It's single-use and **expires one hour after the app is created**. Paste it only into `convert.sh`, or over a private channel to whoever runs it, and do the next step promptly.
- **State.** The `state` in the returned address must match the one in `<app-name>.state.json`. A mismatch means the code belongs to a different page or app, and `convert.sh` refuses to exchange it.

## 3. Exchange the code and store the key

Run this on the orchestrator host, as the OS user that will use the key (see [Key custody](#key-custody)):

```sh
./convert.sh ~/rizzstack-apps-setup/rizzstack-reviewer.state.json
# prompts (hidden input) for the full redirect address
./convert.sh ~/rizzstack-apps-setup/rizzstack-coordinator.state.json
```

`convert.sh` checks the state, then calls `POST https://api.github.com/app-manifests/{code}/conversions`. The response holds the private key (`pem`), a client secret and a webhook secret. The script then:

- writes the key to `~/.config/rizzstack-apps/<slug>.pem`. The directory is mode 700, and the file is opened with `O_EXCL` at mode 600, so an existing key is never overwritten. Use `--keydir DIR` or `RIZZSTACK_APPS_KEYDIR` to choose another private directory;
- **discards** the client secret and webhook secret without printing them. rizzstack uses neither;
- writes and prints only safe fields to `<keydir>/<slug>.json`: App ID, slug, bot login, Client ID, owner and permissions;
- fails if the app's permissions or events differ from the manifest.

The key is never printed, logged or passed on the command line. Never commit it, paste it into chat, an issue or a PR, or copy it to another machine you don't need it on.

### Key custody

- Keep both keys on the **always-on orchestrator host**, in private directories at mode 600, never in a repository or a synced folder.
- Ideally the **reviewer key belongs to a separate OS user** that runs only the verdict poster, so the coordinator's process can't read it. If one process can read both keys, the coordinator can act as the reviewer, and the split protects nothing.
- The coordinator key belongs to the orchestrator's service user (or its secret store).

## 4. Install each app (browser)

There's no API to create an installation, so this is a manual browser step. (Once an installation exists, `PUT /user/installations/{installation_id}/repositories/{repository_id}` can add a repository to it, using a token whose user has admin on that repository. A classic PAT needs the `repo` scope. Whether a given token is accepted varies, so treat the browser as the dependable path.)

1. Open `https://github.com/apps/<slug>/installations/new`.
2. Pick the owner account, then **Only select repositories**.
3. Select exactly: `workflow`, `hyvv`, `bundlebees`, `orbitdesktop`, `orbit-control-plane`, `uirizz` and `rizzstack` (or your forks of them).
4. Click **Install**. Never choose "All repositories".

To add or remove a repository later, go to Settings → Applications → Installed GitHub Apps → Configure, then run `verify.sh` again.

## 5. Verify

```sh
REPOS=workflow,hyvv,bundlebees,orbitdesktop,orbit-control-plane,uirizz,rizzstack
./verify.sh rizzstack-reviewer    --repos "$REPOS"
./verify.sh rizzstack-coordinator --repos "$REPOS"
```

For each app, `verify.sh`:

1. builds an RS256 JWT (valid for 10 minutes; `iss` = App ID) from the key;
2. calls `GET /app` and checks the name, that the permissions equal the manifest, and that no events are subscribed;
3. calls `GET /app/installations` and checks for exactly one installation, on the owner, with `repository_selection: selected`, whose permissions match;
4. mints an installation token **narrowed to `metadata: read`** (`POST /app/installations/{id}/access_tokens`);
5. calls `GET /installation/repositories` and checks that the list is exactly the repositories you passed;
6. revokes the token (`DELETE /installation/token`) and drops it from memory;
7. prints the key's SHA256 fingerprint.

It prints only safe fields and exits non-zero if anything differs.

**Fingerprint check.** On each app's settings page (`https://github.com/settings/apps/<slug>`, under **Private keys**), compare the fingerprint GitHub shows with the one `verify.sh` printed, or run:

```sh
./fingerprint.sh ~/.config/rizzstack-apps/<slug>.pem 'SHA256:<value from the settings page>'
# same as: openssl rsa -in key.pem -pubout -outform DER | openssl dgst -sha256 -binary | openssl base64
```

The App ID, Client ID, installation ID and fingerprint aren't secrets, but they are specific to your setup. Keep them in the orchestrator's private config, not in public docs.

## How the orchestrator mints installation tokens

The coordinator never stores a long-lived token. For each batch of GitHub work it:

1. signs a JWT with its key (`iat` = now − 60 s, `exp` ≤ now + 10 min, `iss` = App ID);
2. calls `POST /app/installations/{installation_id}/access_tokens`, optionally narrowed with `"repositories": ["hyvv"]` and a smaller `"permissions"` map for the task at hand;
3. keeps the returned token **in memory only**. It's valid for one hour, so refresh it a few minutes before `expires_at`;
4. passes it to tools without writing it to disk: `Authorization: Bearer <token>` for the REST API, `GH_TOKEN` in a child process's environment for `gh`, and a credential helper supplying username `x-access-token` for `git` over HTTPS. Never embed it in a remote URL, which ends up in `.git/config` and process lists;
5. revokes it with `DELETE /installation/token` when the batch ends.

Commits and PRs made this way show `<slug>[bot]` as the author or actor. To make commit authorship match, set the author to `<slug>[bot]` with email `<bot-user-id>+<slug>[bot]@users.noreply.github.com`, where the bot's user ID comes from `GET /users/<slug>[bot]`. Unlike the Actions `GITHUB_TOKEN`, pushes and PRs made with an installation token **do** trigger workflows, so CI runs as usual.

The reviewer poster does the same with the reviewer key, narrowed to the one repository it's reviewing.

## Rotating and revoking keys

- **Rotate:** on the app's settings page, choose **Generate a private key**. GitHub downloads a new `.pem` to that browser. Move it to the orchestrator host over a private channel, write it to the key directory at mode 600 (for example `install -m 600 new.pem ~/.config/rizzstack-apps/<slug>.pem.new`, then `mv` it into place), and run `verify.sh`. Compare the new fingerprint, restart whatever caches the key, then **delete the old key** on the settings page and delete the downloaded copy.
- **Revoke one key:** delete it on the settings page. New JWTs signed with it fail at once. Installation tokens minted earlier may stay valid until they expire (up to one hour).
- **Cut off an app immediately:** suspend or uninstall its installation (Settings → Applications → Installed GitHub Apps → Configure). That also invalidates its outstanding installation tokens.
- **Retire an app:** uninstall it, then delete it from Settings → Developer settings → GitHub Apps → *app* → Advanced.
- If a key may have leaked, revoke first, then rotate, and check the repositories' audit log and recent PR activity for the bot login.

## Gate by login (planned)

> **Status: planned, not enforced yet.** Today the gates in each repository still trust review **text**. This section describes the target.

Once the coordinator's GitHub calls run through its app and review posting runs through the reviewer app:

- an **independent approval** counts only when the review's `user.login` is the reviewer bot (`rizzstack-reviewer[bot]`) or the owner's login;
- **owner approvals** and relayed owner yeses count only from the owner's login;
- reviews by the coordinator bot count for nothing;
- because PRs will be authored by the coordinator bot, the owner can submit ordinary GitHub **Approve** reviews, and the reviewer can submit real APPROVED / CHANGES REQUESTED states instead of comment-style attestations.

Migration order:

1. Create, install and verify both apps (this guide).
2. Move the coordinator's GitHub calls to installation tokens, and confirm that PRs, pushes, merges and job reruns work.
3. Route review posting through the reviewer app.
4. Change each repository's gate to trust logins as above. This is a gate change, so it needs the owner's approval and an independent review.
5. **Remove the owner's personal token from the orchestrator host.** Until this step, the coordinator can still act as the owner.

## Which identity each repository's automation uses

| Repository | Coordinator app | Reviewer app | Owner |
|---|---|---|---|
| workflow | coordinator playbook: open, update and merge PRs, rerun CI | outside-review verdicts | owner approvals and decisions |
| bundlebees | coordinator plugin: integrate accepted slices, open and merge PRs | review-bee verdict poster | owner approvals |
| hyvv | PRs and CI reruns opened by the coordinator | review verdicts | owner approvals, deploy yeses |
| orbitdesktop | PRs and CI reruns opened by the coordinator | review verdicts | owner approvals, release yeses |
| orbit-control-plane | PRs and CI reruns opened by the coordinator | review verdicts | owner approvals |
| uirizz | PRs and CI reruns opened by the coordinator | review verdicts | owner approvals, release yeses |
| rizzstack | docs and codemap PRs | review verdicts | owner approvals, ruleset and settings changes |

Each repository's own docs point back here.
