# Wave A slices: stop the leaks

Source: the [Nov 24 next-level review](https://github.com/mojomast/rizzstack/pull/17), Fix-now rows
1-13 and the quick token wins. Every finding and file:line was re-checked against `main`
(rizzstack `d057e7d`, workflow `ae9c080`, hyvv `fdd2354`, bundlebees `82b9118`, orbitdesktop
`0765a93`, orbit-control-plane `bc56669`, uirizz `302f1ac`) on 24 November 2026.

**Status: proposed. None of these slices is in `workflow/plans/backlog.json` and nothing has been
dispatched.** The owner approves slices into the backlog.

Each slice has:

- a brief in [`briefs/`](briefs/) with the exact lease (`assigned_files`), `risk.tier`, `source_rev`
  (the RED commit), budget, the approved tests, a bounded `failing_output` and `affected_tests`
  (test-context briefs);
- approved failing tests committed on a `red/wave-a-*` branch in the owning repo (the RED commit);
- for high tier, an `acdd/1` contract in [`contracts/`](contracts/), frozen with
  `bundlebees contract freeze` and a signed test-collection manifest at the RED commit. They are
  marked `approved_by: rizzstack plan coordinator (draft; pending Kyle)`. That is not the owner's
  approval: re-freeze with the owner as approver before dispatch. The contract hash does not cover
  the review block, so it stays the same. The collection manifest is signed with the coordinator
  box's key, so it has to be collected again on the dispatch host.
- a `bundlebees preflight` verdict (`python -m bundlebees_plugin.preflight <brief> --state-dir ...`).

Leases leave out `CHANGELOG.md`. The coordinator adds the changelog line when it opens the PR (see
the changelog-fragments decision in `NEXT.md`).

## Slices

| ID | Repo | Lease | Tier | RED commit | Contract sha256 | Preflight | After |
|---|---|---|---|---|---|---|---|
| A1a | bundlebees | `bundlebees_plugin/subversion_scan.py`, `bundlebees_plugin/verify_local.py` | high (floor high) | [`46e03db`](https://github.com/mojomast/bundlebees/commit/46e03db6b5c7f0aa430ba31334a00b187a4df90a) | `5495710cbf41` | ok, exit 0 (contract) | - |
| A1b | bundlebees | `bundlebees_plugin/tdd.py`, `bundlebees_plugin/verify_local.py` | high (floor high) | [`f0e77f2`](https://github.com/mojomast/bundlebees/commit/f0e77f27c8c195c36d7259420d90b65a78d45f69) | `7c82c54d1cdb` | ok, exit 0 (contract) | A1a |
| A6 | bundlebees | `bundlebees_plugin/tools.py` | high (floor medium) | [`224a954`](https://github.com/mojomast/bundlebees/commit/224a954f6a5de4a5418ac0156052a937c2eb9227) | `acad09f3d3dc` | ok, exit 0 (contract) | - |
| T1 | bundlebees | `bundlebees_plugin/tools.py` | medium (floor medium) | [`58551be`](https://github.com/mojomast/bundlebees/commit/58551bea1b99fe5f39b18125da70d9208043e51a) | - | ok, exit 0 (legacy) | A6 |
| A7 | bundlebees | `bundlebees_plugin/risk.py`, `bundlebees_plugin/policy/risk-floor.json`, `docs/risk-tiers.md` | high (floor medium) | [`a2bc902`](https://github.com/mojomast/bundlebees/commit/a2bc902e6b3b2529a420136090857492309a0edb) | `f95f6f734cc5` | ok, exit 0 (contract) | - |
| A8 | bundlebees | `bundlebees_plugin/integrate_job.py` | medium (floor medium) | [`0c00733`](https://github.com/mojomast/bundlebees/commit/0c007335a49478f5cb0b089550c2b9cb5f9083f6) | - | ok, exit 0 (legacy) | - |
| A10 | bundlebees | `bundlebees_plugin/agent_consumer.py` | medium (floor medium) | [`356d031`](https://github.com/mojomast/bundlebees/commit/356d031b2cb33bbcad29047f367b9c9804db45aa) | - | ok, exit 0 (legacy) | - |
| A11 | bundlebees | `bundlebees_plugin/preflight.py`, `bundlebees_plugin/cli.py` | medium (floor medium) | [`67d0807`](https://github.com/mojomast/bundlebees/commit/67d0807e7b82dfa07d48577456fe74640e10cc57) | - | ok, exit 0 (legacy) | - |
| T4 | bundlebees | `__init__.py`, `skills/bundlebees-operator/SKILL.md` | medium (floor medium) | [`72437ab`](https://github.com/mojomast/bundlebees/commit/72437abeb93e20c87f6fe7f18e6b5a6caf708ec2) | - | ok, exit 0 (legacy) | A6 |
| M1 | bundlebees | `bundlebees_plugin/metrics.py` | medium (floor medium) | [`7a5f5ea`](https://github.com/mojomast/bundlebees/commit/7a5f5ea0e74df45e148b445d65c6b14a01161273) | - | ok, exit 0 (legacy) | - |
| HY-1 | hyvv | `launcher.py` | high (floor medium) | [`7054d4a`](https://github.com/mojomast/hyvv/commit/7054d4a61fb7bc78c6f8f2d9b045d29d7ba139f8) | `e3362e8e1cc9` | ok, exit 0 (contract) | - |
| HY-2 | hyvv | `launcher.py` | medium (floor medium) | [`1c6001d`](https://github.com/mojomast/hyvv/commit/1c6001d335faa9f440413fab6c3edbb95494cc99) | - | ok, exit 0 (legacy) | HY-1 |
| HY-3 | hyvv | `launcher.py`, `docs/bee-landed.md` | medium (floor medium) | [`0cf4d8b`](https://github.com/mojomast/hyvv/commit/0cf4d8b94fe51f7bef070ebfd2e4533fcf245f99) | - | ok, exit 0 (legacy) | HY-2, A12a |
| A12a | workflow | `tools/bee_landed.py`, `schemas/bee-landed.schema.json`, `docs/29-bee-landed.md` | high (floor high) | [`c87c87e`](https://github.com/mojomast/workflow/commit/c87c87ed45a4239baad937079977beb8429f5ad3) | `906bbfe2b95f` | ok, exit 0 (contract) | - |
| A9 | orbit-control-plane | `src/auth.mjs`, `src/server.mjs`, `src/config.mjs`, `README.md` | high (floor high) | [`98d6c0a`](https://github.com/mojomast/orbit-control-plane/commit/98d6c0a2848596c2063bc4607baff596f1dd7b8b) | `707f55e3a0b6` | ok, exit 0 (contract) | - |
| T2a | orbitdesktop | `server/workspace.mjs`, `hermes-plugin/orbit-source.tar.gz` | medium (floor medium) | [`ab6bd5d`](https://github.com/mojomast/orbitdesktop/commit/ab6bd5dd3c710af312ccb5a806449cd5c79e8b2e) | - | ok, exit 0 (legacy) | - |
| T2b | orbitdesktop | `server/workspace-description.mjs`, `server/workspace.mjs`, `hermes-plugin/__init__.py`, `hermes-plugin/orbit-source.tar.gz` | medium (floor medium) | [`09fded1`](https://github.com/mojomast/orbitdesktop/commit/09fded1b0d37101055a1b8dcf3f55ecc2c7b2a99) | - | ok, exit 0 (legacy) | T2a |
| T3 | orbitdesktop | `AGENTS.md`, `hermes-plugin/orbit-source.tar.gz` | medium (floor medium) | [`ede1de2`](https://github.com/mojomast/orbitdesktop/commit/ede1de2c88879011e16fd29eb5fdb9484b434584) | - | ok, exit 0 (legacy) | T2b |
| T5a | uirizz | `skills/uirizz/SKILL.md`, `skills/uirizz/references/express.md` | low (floor low) | [`aa0e734`](https://github.com/mojomast/uirizz/commit/aa0e7348b2ebcc75f15e5cc0d8b0cdba90e8f34b) | - | ok, exit 0 (legacy) | - |
| T5b | uirizz | `uirizz_plugin/gate.py`, `uirizz_plugin/hints.py`, `uirizz_plugin/hooks.py` | high (floor medium) | [`70dd2fa`](https://github.com/mojomast/uirizz/commit/70dd2fa301bd123f8d1b2ea6f8c8eb39065c78c2) | `b14159073a0f` | ok, exit 0 (contract) | - |
| A5b | bundlebees | `bundlebees_plugin/tools.py` | medium | not written yet | - | - | A6, T1, HY-3 |

A5b attaches the accepted research findings (from the state dir) to `brief.findings` at create
time. Until then, HY-3 renders findings only when the brief carries them. Its RED is written after
HY-3 fixes the prompt interface, so this table has 21 slices but only 20 RED commits.

Preflight notes: every brief passes (exit 0). T3 was first declared `low` and refused with
`risk_tier_below_floor`, because the regenerated tarball is a source path. It is now `medium`. T1,
A11, T4 and M1 were planned as low and are `medium` by the path floor. T5b is raised above its
`medium` floor to `high` because it is approval-gate code. A9's tests are `node:test`, which
bundlebees' pytest-only collection can't run, so its contract uses `integration` criteria (CI runs
them) and `tdd.applicable: false`, with the RED recorded by hand.

## Re-checked, rescoped or dropped

| Finding | Result | Why |
|---|---|---|
| 1 verify trusts junit and collected files | **rescoped, split A1a + A1b** | Editing an approved test or conftest is already caught by `_test_sources_digest` and the signed collection manifest. What's missing is (a) refusing new conftest, `.pth`, startup-hook and forced-exit files in the patch, and (b) GREEN comparing the collected ids exactly. Both touch `verify_local.py`, so they run one after the other. |
| 2 no wall clock without `budget.timeout_s` | **rescoped (HY-1)** | Refusing briefs that have no budget would break the additive `/v1`. Instead, a host default always applies, and refusal happens only under an opt-in `require_budget`. |
| 3 + 4 `_backoff` drops run and sandbox; teardown keyed by attempt | **combined (HY-2)** | Same code path. Confirmed: after a retry only `['sb-1']` is torn down. Resume only on a same-model retry when the backend supports `resume`; a model switch restarts fresh. |
| 5 findings never rendered | **kept (HY-3) + follow-up A5b** | Findings live in the bundlebees state dir, not the brief, so bundlebees must attach them. HY-3 also renders `brief.tests`. |
| 6 lease check fails open | kept (A6) | Confirmed at `tools.py:684-697`. |
| 7 `CONTRACT_GATE_MODE = "report_only"` | **owner decision, not a slice** | Enforcement is a date choice. See `NEXT.md` G12. |
| 8 thin risk floor | kept (A7) | CI workflows are already high. The real gaps are conftest and test-harness config. This raises how many briefs reach high tier, which affects model routing (MI-31 should key off `risk.tier`). |
| 9 `integrate_job._git` no timeout | **rescoped (A8)** | Only `integrate_job.py`. The ~54 other git call sites move to the wave D core git runner. |
| 10 sessions never expire; no role check | kept (A9) | No operator role exists today, and the dashboard uses the viewer token for control. Needs a new operator credential and `operators` allowlist before deploy (owner). `/api/v1/grok` POST is out of scope. |
| 11 test adapters reachable in prod | kept (A10) | Two existing tests gain `BUNDLEBEES_TEST_ADAPTERS=1` in the RED commit. |
| 12 preflight ignores `require_contract` | kept (A11) | Lease includes `cli.py` (the registered command reads the setting). |
| 13 bee-landed ids disagree | **rescoped (A12a + HY-3)** | Keep type `rizzstack.bee.landed.v1` and data schema `rizzstack.bee-landed/1`. Fix only the dataschema URL, and keep accepting the legacy typo, because bundlebees `tests/test_bee_events.py` uses it. |
| T2 Orbit guide and catalog | **split T2a + T2b** | Different files. Both force regenerating `orbit-source.tar.gz`, so all orbitdesktop slices run in series. |
| T3 AGENTS.md | kept, small | It is already 1.9k bytes; the cost is the docs it sends bees to. |
| T4 operator system prompt section | kept, with a risk | The section helps tool discovery; T4 keeps one pointer. |
| T5 uirizz skill and notices | **split T5a (docs, low) + T5b (gate, high)** | T5b is approval-gate code. T5a keeps the layout section pinned by `tests/test_skill_layout.py`. |
| M1 tokens per verified patch | rescoped | `tokens_per_accepted` already exists per run. M1 adds the weekly cross-run rollup. |
| collab `sh -c` path ungated | **dropped** | It goes through an approval gate already. |

## Dispatch order and parallelism

Lanes run in parallel across repos. Inside a lane, slices run one after another because their leases
overlap.

| Lane | Order | Waits for |
|---|---|---|
| bundlebees verify | A1a -> A1b | - |
| bundlebees tools.py | A6 -> T1 -> A5b; T4 after A6 | A5b also waits for HY-3 |
| bundlebees independent | A7, A8, A10, A11, M1 | A7 after MI-31 agrees on routing by `risk.tier` |
| hyvv launcher.py | HY-1 -> HY-2 -> HY-3 | hyvv #41 (release) merges first; HY-3 also after A12a |
| workflow | A12a | - |
| orbit-control-plane | A9 | deploy needs the operator credential (owner) |
| orbitdesktop tarball | T2a -> T2b -> T3 | orbitdesktop #91 (also regenerates the tarball) resolves first |
| uirizz | T5b; T5a after uirizz #32 | - |

Suggested batches of at most four bees, to keep token spend visible:

1. A6, A1a, A10, A12a (fail-closed gates first)
2. HY-1, A1b, A8, A11
3. A7, T1, HY-2, A9
4. T4, M1, HY-3, T5b
5. T2a, T2b, T3, T5a, A5b
