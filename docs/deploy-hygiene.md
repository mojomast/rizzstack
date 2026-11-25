# Deploy directory hygiene

A deploy directory is a **deployed artifact**, not a workspace. Four surfaces on a
Hermes host run from checkouts: the factory driver, the Hermes plugin, the control
plane and the Rails ingest. Each one is a detached checkout of a merged commit, and
nothing else.

Three rules, all mechanically checkable:

| Rule | Why it exists | Pass condition |
|---|---|---|
| Detached HEAD at a merged SHA | a branch invites `git pull`; the artifact's identity then moves under the running process and no PR or test run covers what is actually executing | `git -C <dir> rev-parse --abbrev-ref HEAD` prints `HEAD` |
| Clean tree | a local edit in a deploy dir is code no review, no test and no PR ever saw, and the next checkout deletes it without a trace | `git -C <dir> status --porcelain` prints nothing |
| Running SHA equals checkout SHA | updating a checkout without restarting the unit deploys nothing; restarting after a hand-edit deploys unreviewed code | the unit's recorded start SHA equals `git -C <dir> rev-parse HEAD` |

A violation is a **watchdog alert** (`deploy-dir-hygiene`, `deploy-sha-drift`), not a
style note. The same two checks are part of the factory-status slice
(`scripts/factory_status.py`, snapshot schema `rizzstack.factory-status.snapshot/1`).

## The four surfaces

| Surface | Deploy directory (default) | Unit that runs it |
|---|---|---|
| Factory driver | `~/rizzstack/bundlebees` | `bundlebees-factory-driver.service` |
| Hermes plugin | `~/.hermes/plugins/bundlebees` | `hermes-gateway.service` (imports it at startup) |
| Control plane | `~/rizzstack/orbit-control-plane` | `orbit-control-plane.service` |
| Rails ingest | `~/rizzstack-ingest/bundlebees` | `bundlebees-rails-ingest.service` |

Paths are the defaults used by the shipped units (`%h` is the login user's home). A
host that puts a surface somewhere else updates this table and the watchdog snapshot,
not the rules.

## Development happens in worktrees

One worktree per slice, branched off the current `main`, never in the deploy
directory:

```bash
git -C ~/rizzstack/bundlebees fetch --prune origin
git -C ~/rizzstack/bundlebees worktree add ~/rizzstack/wt-<slice> -b <branch> origin/main
```

A worktree is disposable and its branch is meant to move; the deploy dir is neither.
`git -C <deploy-dir> worktree list` shows every worktree of that clone, and
`git -C <deploy-dir> status --short` is the fastest proof that a deploy dir is still
pristine.

## Deploying a merged commit (detached)

```bash
dir=~/rizzstack/bundlebees
sha=<full 40-hex merged commit>

git -C "$dir" fetch --prune origin
git -C "$dir" checkout --detach "$sha"
test "$(git -C "$dir" rev-parse HEAD)" = "$sha" || { echo "checkout did not land on $sha"; exit 1; }
test -z "$(git -C "$dir" status --porcelain)" || { echo "deploy dir is dirty: $(git -C "$dir" status --porcelain | wc -l) path(s)"; exit 1; }
systemctl --user restart bundlebees-factory-driver.service
```

Never `git checkout main` or `git pull` in a deploy dir: `main` is a moving ref, not an
artifact. If the merged SHA is not the commit that was reviewed and verified, it is not
deployable.

## Check commands

Copy-paste check for all four surfaces:

```bash
for spec in \
  "driver:$HOME/rizzstack/bundlebees" \
  "plugin:$HOME/.hermes/plugins/bundlebees" \
  "control-plane:$HOME/rizzstack/orbit-control-plane" \
  "ingest:$HOME/rizzstack-ingest/bundlebees"
do
  surface=${spec%%:*}; dir=${spec#*:}
  branch=$(git -C "$dir" rev-parse --abbrev-ref HEAD 2>/dev/null)
  head=$(git -C "$dir" rev-parse HEAD 2>/dev/null)
  dirty=$(git -C "$dir" status --porcelain 2>/dev/null | wc -l)
  running=$(cat "$HOME/.local/state/rizzstack/$surface.running-sha" 2>/dev/null || echo missing)
  printf '%-14s branch=%-24s dirty=%-3s head=%s running=%s\n' \
    "$surface" "$branch" "$dirty" "${head:0:12}" "${running:0:12}"
done
```

Pass conditions for every row: `branch` is `HEAD`, `dirty` is `0`, and `running` equals
`head`.

Single-surface checks, when only one surface is in question:

```bash
# 1. detached? prints HEAD when detached, a branch name when not
git -C ~/rizzstack/bundlebees rev-parse --abbrev-ref HEAD

# 2. clean? no output is the only passing output
git -C ~/rizzstack/bundlebees status --porcelain

# 3. which commit is checked out, and what unit is running it
git -C ~/rizzstack/bundlebees rev-parse HEAD
systemctl --user show -p ActiveEnterTimestamp --value bundlebees-factory-driver.service

# 4. what did the unit start on?
cat ~/.local/state/rizzstack/driver.running-sha
```

`ActiveEnterTimestamp` is useful context, not proof: it tells you when the unit
started, and a commit date newer than it means the checkout moved after the process
did.

## The running-SHA contract

A checkout's SHA says what is on disk; only the process's start SHA says what is
executing. Each unit records its start SHA before the process starts, so the watchdog
can compare the two without guessing:

```ini
[Service]
ExecStartPre=/bin/sh -c 'git -C %h/rizzstack/bundlebees rev-parse HEAD > %h/.local/state/rizzstack/driver.running-sha'
```

The plugin is imported by `hermes-gateway.service`, so its `ExecStartPre` writes the
plugin checkout's SHA to `plugin.running-sha`. State lives under
`~/.local/state/rizzstack/`, never inside the deploy dir. A missing running-SHA file is
reported as `running_sha: null` and treated as drift until the unit has been restarted
once with the drop-in in place.

Restart the unit after every checkout change. A deploy that does not restart is not a
deploy: it leaves the old code running and the new SHA on disk, which is exactly the
`deploy-sha-drift` the watchdog flags.

## When a deploy dir is dirty

Do not reach for `git clean` first: the diff may be the only copy of a fix.

```bash
dir=~/rizzstack/bundlebees
stamp=$(date -u +%Y%m%dT%H%M%SZ)
git -C "$dir" diff > "/tmp/deploy-dirty-$stamp.diff"
git -C "$dir" status --porcelain > "/tmp/deploy-dirty-$stamp.files.txt"
```

Move the change into a worktree through a normal branch and PR, then restore the
artifact:

```bash
git -C "$dir" checkout --detach "$(git -C "$dir" rev-parse HEAD)"
git -C "$dir" clean -fd
```

`clean -fd` deletes untracked files. Runtime state does not belong in a deploy dir: if
the untracked paths are state, move them out before cleaning (Hermes plugin state lives
under `~/.hermes/plugin-data`, host state under `~/.local/state`).

## What the watchdog reports

`scripts/factory_status.py` reads a `deploys[]` block in the snapshot
(`rizzstack.factory-status.snapshot/1`) and emits one finding per violation:

- `deploy-dir-hygiene` — the surface is on a branch (`branch` is not null) or its tree
  is dirty (`dirty` is true); the finding names the surface, the branch and the
  changed-path count.
- `deploy-sha-drift` — `running_sha` differs from `head`, including a null running SHA.

Both are state flags, not age-based flags: they are reported on every run until the
surface is back on a detached, clean checkout that the running process actually loaded.
Silencing them by editing the snapshot is the same class of mistake as editing the
deploy dir.
