# Gaps, drift and open questions found while mapping

Observed at the pinned SHAs. Each is a candidate task, not a decision.

1. **Two observability schema ids.** `rizzstack.observability/snapshot/1` (workflow collector → Observatory) and `rizzstack.observability/1` (orbit-control-plane collector → rizzobs) are different documents with confusingly similar names, and the umbrella `docs/protocol.md` lists only the first. Decide whether to rename the control-plane one (e.g. a `chat-card` suffix) or document both.
2. **orbit-control-plane has no GitHub Actions.** No tests, slop-lint or gitleaks run on push, unlike the other five repos.
3. **uirizz has no slop-lint or gitleaks job** in CI.
4. **Stale docstrings in bundlebees risk modules.** `bundlebees/bundlebees_plugin/risk.py` and `bundlebees/bundlebees_plugin/risk_policy.py` say "nothing here is wired into dispatch", but `_dispatch` in `bundlebees/bundlebees_plugin/tools.py` now enforces the floor and the contract requirement. `risk_policy.py`'s process table itself still looks report-only.
5. **orbit-control-plane design doc says uirizz has "two" hooks;** `uirizz/plugin.yaml` declares three (`pre_llm_call`, `pre_tool_call`, `post_tool_call`).
6. **Legacy launcher copy.** `orbitdesktop/pc-launcher/launcher.py` (~1k lines) predates `hyvv/launcher.py` (~3.4k lines). Agents may edit the wrong one; hyvv is the maintained host.
7. **Vendored code without a single refresh command.** `rizzstack_rails` has manifest checks; slop-lint copies in bundlebees and hyvv have none, and orbitdesktop's JS port can drift in rule semantics.
8. **hyvv status table lags `main`.** Provisioning and landing-export routes are merged but not in `hyvv/contract/v1/STATUS.json`.
9. **Umbrella docs lag.** `docs/protocol.md` still calls Rails "in review" and lists bundlebees #82 as under review; refresh when the next release decision lands.
10. **Map limits.** The generator is regex/AST-based, not a compiler index: JS/TS re-exports, dynamic imports, Python `__all__` and call graphs are not resolved, and ranking is a heuristic. For precise references use the repo's own tooling or a SCIP indexer.
