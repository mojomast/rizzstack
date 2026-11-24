# Gaps, drift and open questions found while mapping

Observed at the pinned SHAs. Each is a candidate task, not a decision.

1. **Two observability schema ids.** `rizzstack.observability/snapshot/1` (workflow collector → Observatory) and `rizzstack.observability/1` (orbit-control-plane collector → rizzobs) are different documents with confusingly similar names, and the umbrella `docs/protocol.md` lists only the first. Decide whether to rename the control-plane one (e.g. a `chat-card` suffix) or document both.
2. **Resolved:** orbit-control-plane has GitHub Actions since [orbit-control-plane #5](https://github.com/mojomast/orbit-control-plane/pull/5) (Node 22 tests, rizzobs pytest, gitleaks; slop-lint is report-only for now). Making slop-lint blocking is still open.
3. **Resolved:** uirizz runs gitleaks and a blocking slop-lint ratchet since [uirizz #27](https://github.com/mojomast/uirizz/pull/27). The desktop JSX is not covered by slop-lint.
4. **Resolved:** the bundlebees risk-module docstrings now say what dispatch enforces ([bundlebees #251](https://github.com/mojomast/bundlebees/pull/251)). `risk_policy.py`'s process table itself still looks report-only.
5. **orbit-control-plane design doc says uirizz has "two" hooks;** `uirizz/plugin.yaml` declares three (`pre_llm_call`, `pre_tool_call`, `post_tool_call`).
6. **Legacy launcher copy.** `orbitdesktop/pc-launcher/launcher.py` (~1k lines) predates `hyvv/launcher.py` (~3.4k lines). Agents may edit the wrong one; hyvv is the maintained host.
7. **Vendored code drift (mostly resolved).** `rizzstack_rails` has manifest checks, and the slop-lint copies in hyvv, bundlebees and uirizz now fail CI on drift (`scripts/vendor_slop_lint.py --check`, [hyvv #38](https://github.com/mojomast/hyvv/pull/38), [bundlebees #254](https://github.com/mojomast/bundlebees/pull/254), [uirizz #27](https://github.com/mojomast/uirizz/pull/27)). orbitdesktop's JS port and orbit-control-plane's `scripts/slop-lint.mjs` can still drift in rule semantics.
8. **hyvv status table lags `main`.** Provisioning and landing-export routes are merged but not in `hyvv/contract/v1/STATUS.json`.
9. **Resolved:** `docs/protocol.md` now says Rails is merged in all four repos with the release on hold ([rizzstack #9](https://github.com/mojomast/rizzstack/pull/9)). Refresh again when the next release decision lands.
10. **Map limits.** The generator is regex/AST-based, not a compiler index: JS/TS re-exports, dynamic imports, Python `__all__` and call graphs are not resolved, and ranking is a heuristic. For precise references use the repo's own tooling or a SCIP indexer.
