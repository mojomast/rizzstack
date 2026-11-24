# Releasing the rizzstack tuple

How a coordinated release goes out across the repos. Decided 2026-11-24
(BEST-PRACTICE items 5, 6 and 8). The released tuple and the pins that ship
with it are in [COMPAT.md](../COMPAT.md).

## Order

Tags go out in dependency order, dependencies before dependents:

**workflow → hyvv → bundlebees → orbitdesktop**

1. Version-bump PRs land first (one per repo; 0.x bumps the minor for removals).
2. bundlebees re-pins to the workflow and hyvv SHAs it ships against, or
   [COMPAT.md](../COMPAT.md) records the skew and why.
3. The cross-stack gate passes on the bumped SHAs: workflow
   `rails-acceptance.yml` (manual dispatch) with `scripts/rails_tuple.py`, plus
   orbitdesktop `technology.yml` and `asteria.yml` on the orbitdesktop SHA.
   These are CI runs, not live runs.
4. Then the tags, in the order above. **Each tag is its own typed yes** from the
   owner, given for that tag; agents prepare the tag command and release notes
   and stop there.
5. After the tags, regenerate COMPAT.md (`python3 scripts/compat.py render > COMPAT.md`)
   in a PR.

A hyvv tag also runs its release workflow, which publishes to PyPI behind the
`pypi` environment's required reviewer. That approval is a separate yes.

## Immutable releases

workflow, hyvv, bundlebees and orbitdesktop have GitHub immutable releases
turned on (since 2026-11-24). Once a release is published its assets and tag
are locked: assets can't be added, replaced or deleted, and the tag can't be
moved or reused. Only the title, notes and pre-release/latest flags stay
editable. So a release always goes in this order:

1. Create the release as a **draft** (notes = that version's CHANGELOG section).
2. **Attach** every asset to the draft.
3. **Publish** the draft last.

hyvv's release workflow does this itself (create or reuse a draft, upload,
publish) and fails closed if the release is already published. A mistake in a
published release is fixed by a new version, never by editing the old one.
Releases published before 2026-11-24 stay mutable.
