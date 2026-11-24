# uirizz (L1): A2UI cards in Hermes chat

Pinned `98b795a` = v0.1.0 (tagged, released; catalog listing PR pending) · https://github.com/mojomast/uirizz · generated detail: `codemap/generated/uirizz.md`

**What it is.** A native Hermes plugin that renders official A2UI v1.0 **Basic Catalog** surfaces with one tool, `a2ui_render`. Desktop and Webapp mount `@a2ui/react` widgets (always dark); CLI, Telegram and TUI show the required `fallback_text`. Declared card actions are bound to one exact Hermes tool call and need the user's typed `yes <code>`.

**Layer.** 4 (presentation). Consumes Express produced by the model or by rizzobs (`orbit-control-plane/hermes-plugin/rizzobs`). Has no network access, credentials, `requires_env` or subprocesses at runtime.

## Where things are

| Need | Path |
|---|---|
| Tool schema + handlers (`op`: `present`, `update`, `delete`, `describe`, `audit`) | `uirizz/uirizz_plugin/tools.py` |
| Payload admission against the Basic Catalog allowlist + caps | `uirizz/uirizz_plugin/validate.py`, `uirizz/uirizz_plugin/catalog.py` |
| Express → camelCase v1.0 JSONL compiler | `uirizz/uirizz_plugin/express.py` |
| Approval gate, single-use approvals (`APPROVAL_TTL_S = 600`), hash-chained audit log | `uirizz/uirizz_plugin/gate.py` |
| Declared actions (button → one exact tool call) | `uirizz/uirizz_plugin/bindings.py`, example `uirizz/docs/actions.example.yaml`, doc `uirizz/docs/ACTIONS.md` |
| User-action log | `uirizz/uirizz_plugin/actions.py` |
| Public-https-only media admission | `uirizz/uirizz_plugin/urls.py` |
| Hooks (`pre_llm_call`, `pre_tool_call`, `post_tool_call`; never `transform_llm_output`) | `uirizz/uirizz_plugin/hooks.py` |
| Surface store (plugin data dir) + read-only REST payload | `uirizz/uirizz_plugin/store.py`, `uirizz/uirizz_plugin/api.py` |
| Desktop pane (React, `@hermes/plugin-sdk` + `ctx.rest` only) | `uirizz/desktop/src` (`plugin.jsx`, `card.jsx`, `rest.js`), built bundle `uirizz/desktop/plugin.js` |
| Skill the model reads (≤2000-token budget) | `uirizz/skills/uirizz/SKILL.md` + `uirizz/skills/uirizz/references` |
| Behavior evals (currently skipped by decision) | `uirizz/evals` |
| Token budget checks, version bump | `uirizz/scripts/measure_tokens.py`, `uirizz/scripts/bump_plugin_version.py` |
| Manifest | `uirizz/plugin.yaml` |

## CI gates

`ci.yml`: `test` (pytest) and `desktop` (`npm test`, `npm run check-bundle`). No slop-lint or gitleaks job in this repo (see `codemap/gaps.md`). Token budgets (SKILL.md ≤2000, tool schema ≤500) are enforced by tests.

## Change recipes

- **Add a component or widget:** only if it is in the official Basic Catalog (no Progress, Chart or Table exist): `uirizz/uirizz_plugin/catalog.py` allowlist + `uirizz/uirizz_plugin/validate.py` + `uirizz/desktop/src/card.jsx`; tests first.
- **Add a declared action type:** `uirizz/uirizz_plugin/bindings.py` + gate wiring in `uirizz/uirizz_plugin/gate.py`; the typed yes remains the only approval.
- **Change the skill text:** `uirizz/skills/uirizz/SKILL.md`, then run `uirizz/scripts/measure_tokens.py`.

## Maintainer invariants (from the owner)

- A click is never approval; only the user's typed yes approves. Approvals are single-use, 600 s TTL.
- Fail closed everywhere. Media is public https only. Never register `transform_llm_output`.
- Desktop uses `@hermes/plugin-sdk` + `ctx.rest` only. Handlers return JSON with `code`, `error`, `fix`.
- Surface labels are untrusted; strip control characters.
- No credentials, no `requires_env`, no subprocesses at runtime. Tests first; token budgets stay green.
- Known doc bug: Express needs `root = ...`.
