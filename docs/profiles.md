# Install profiles (planned)

full | observer | orchestrator | worker | contracts — each documented with its minimum
component set, what it watches/does, and its dependency edges. Spec in progress.

## GitHub identities (orchestrator profile)

An orchestrator that opens, merges or reviews PRs needs two GitHub Apps installed on your repositories, and their private keys on the orchestrator host: the **coordinator** app for building and merging, and the **reviewer** app for posting reviews. Your own account stays the owner and decides. Set them up with [github-apps.md](github-apps.md) and the scripts in [`tools/github-apps/`](../tools/github-apps/). Observer, worker and contracts-only profiles need neither.
