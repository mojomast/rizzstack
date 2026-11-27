# Bypass catalogue

Every entry below got past a rizzstack gate at least once before review caught it. For a
`gate_security` review, try each entry that applies to the change. Write the probe as a small
script in `review/scratch/`, run it against the PR head **and** against `origin/main`, and save
both outputs. A probe that main refuses and the head allows is a blocking regression.

## Configuration that changes the test oracle
1. **Config spellings.** The same pytest setting written another way: a TOML inline table
   (`tool = { pytest = { ini_options = { addopts = "-p evil" } } }`), dotted keys, a table header,
   `[project] entry-points = { pytest11 = {...} }`, `setup.cfg`/`tox.ini`/`pytest.ini`. A
   line-based scanner can't keep up with these. Expect the PR's config to be inert (an oracle lock
   using main's config, plugin autoload off) and the scanner to be only a tripwire.
2. **Harness files in the worktree.** If the harness writes a file into the checkout *before*
   `git apply`, a patch with that file's predictable content as its preimage can rewrite it. The
   fix is to write harness files outside the worktree, hash-check them before use, and refuse
   patches that touch the name.
3. **Runner shadowing.** `python -m pytest` puts the current directory on `sys.path`, so a root
   `pytest.py` or `_pytest/` (or `pluggy.py`, or a stdlib name) replaces the runner. A two-line
   fake that prints "1 passed" exits 0. Expect `-P`/`PYTHONSAFEPATH` and a refusal of top-level
   shadow modules.
4. **conftest hooks.** `pytest_runtest_makereport` can rewrite outcomes at runtime. This can't be
   blocked by config; expect it to be flagged and to force T3.
5. **The check runs from the PR's own copy.** A CI step that imports the lock, gate or scanner
   from the PR checkout can be edited by the PR. Expect it to be loaded from the base branch, and
   any bootstrap fallback removed once main carries the file.

## Scanner subtraction
6. **Moving a line.** Dropping a finding because "the same statement exists in the base" lets a
   patch move an existing `os._exit(0)` or `sys.modules[...] = stub` somewhere dangerous, such as
   an import-time function. Expect findings on **added** lines never to be subtracted.
7. **Activating a dormant line.** Adding a call to an existing function whose unchanged body is
   dangerous isn't caught by line-based scanning. It's an accepted residual, but expect the
   subtracted findings to be recorded in the verify evidence.

## Filesystem identity
8. **Symlink modes.** On Linux, `lstat()` of a symlink always reports 0777, so a
   "group/world-writable" check rejects every symlink. Check symlinks for owner only.
9. **Stat-keyed caches.** A directory's mtime doesn't change when a file deep inside is edited,
   and `cp -p` keeps size and mtime. Expect a tree walk keyed on inode and ctime as well.
10. **Shared directories.** A pinned tool under `/var/tmp` (or any shared path) can be planted
    by another user, or aged out by tmpfiles. Expect owner-only, non-writable checks, and user
    directories.

## Merge mechanics
11. **Review-triggered reruns.** Rerunning every failed job on any review lets reviews re-roll
    a failing test. Rerunning two dependent jobs races. Expect only the gate job (`lease-tamper`)
    to be rerun, with `ci-gate` following as its dependent.
12. **Auto-merge skipping review evaluation.** A T1 path that skips review evaluation also
    ignores a CHANGES REQUESTED at the head. A rejection must always block.
13. **Tier denylists.** Listing T3 paths misses whatever nobody listed (lock files, agent
    instructions, the oracle module, auth/crypto code in product folders). Expect a positive T1
    allowlist with an always-T3 overlay, and an unknown repo or missing tier treated as T3.
14. **Carry-forward.** A review may carry over base-only merges only when every changed file's
    blob is identical. A conflict resolution or authored commit needs a fresh review.

## Records and identity
15. **Text-attributed approvals.** If the gate trusts the words of a review, anything holding
    the token can approve. Note when an approval at the head came from an automated lane rather
    than the owner or the reviewer identity.
16. **Values in public records.** Validator or error messages that echo input values leak them
    into `/v1` records and logs. Expect paths named, values never.
17. **Content-addressed keys.** A dedupe key built from content must not include wall-clock
    fields, or every retry creates a new row.
