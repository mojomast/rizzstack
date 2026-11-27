# Outside review

An **outside review** is an independent check of one pull request at one head sha, done by a
reviewer that sits outside the author's pipeline. Since 2026-11-27 the reviewer is a role any agent
and any model can fill: the coordinator dispatches a **review bee** with the model configured for
the role, and the bee follows `skills/rizzstack-outside-review/SKILL.md`. This page is the coordinator's half
of the protocol: who may review, what the bee gets, how its verdict is checked, and how it reaches
GitHub.

## Who may review

- **Independence is about model family.** The reviewer's model family must differ from the
  author's. For a bee-authored slice, that's the family of the model that produced the patch; for
  a coordinator-authored PR, the coordinator's. The coordinator refuses to dispatch a review whose
  configured family equals the author's (`review_not_independent`).
- **The review sees evidence, not the author's reasoning.** The packet carries the diff, records
  and claims, never the author's transcript.
- **Advisory lanes are not reviewers.** Scheduled lanes that read PRs may comment findings. They
  never post a first line starting `APPROVE`, and nothing they write counts as a review.
- **The owner may review in person.** For the gate, a human review counts as independent
  only with the independent form: `APPROVE (independent review — owner, human) at <sha>`.
  `APPROVE (owner) at <sha>` is an **owner attestation**: the lane requires it on sensitive
  paths, on top of the independent review. It never satisfies the non-slice review rule on its
  own (`independent_review()` in the gate accepts only `approve_independent`).

## Role configuration and routing

The coordinator's config names the role. The model aliases resolve through the launcher's
`models` map like any other bee model, so changing a reviewer model is a config change:

```json
{
  "roles": {
    "reviewer": {
      "models": [
        {"model": "<kimi alias, e.g. k3.1-pro>", "model_family": "kimi"},
        {"model": "<deepseek alias, e.g. nous:deepseek-v4.1-flash-1017>", "model_family": "deepseek"}
      ],
      "kind": "opencode",
      "budget": {"timeout_s": 2700, "max_tool_calls": 120},
      "identity": "rizzstack-reviewer",
      "post_mode": "auto_reject"
    }
  }
}
```

Routing is per PR, from the **author families** recorded for every commit on it:

- bee commits → the model recorded on the attempt; coordinator commits → the coordinator's
  model; anything else (another agent, a human, unknown) → `unknown`;
- the reviewer is the first listed model whose family appears in **no** author family (with the
  list above: Kimi by default, DeepSeek when Kimi wrote any part of the PR);
- if no listed family qualifies (both families authored, or an author is `unknown`), no review
  bee is dispatched: the PR goes to the owner or an outside reviewer (`review_not_independent`).

## Dispatch

1. **Build the packet** (layout in `skills/rizzstack-outside-review/SKILL.md` §1), entirely from trusted
   sources:
   - the GitHub API for the PR, files, check runs and prior reviews
   - the trusted gate run for `class` and `tier`
   - the verify store for records
   - the base branch for leases and contracts
   - canonical repos for `related/` copies
   
   `claims.md` is the coordinator's own report of the PR, written as claims to verify.
2. **Dispatch** a bee with `skills/rizzstack-outside-review/templates/review.brief.template.json` filled in:
   `source_rev` = the PR head, the role's model and budget, a read-only security profile, no push
   or API credentials, and network only to the model provider.
3. **Collect** `review/verdict.json` and `review/scratch/**` as the attempt's artifacts.

## Validation (before anything is posted)

The coordinator refuses the verdict and re-dispatches once (then reports to the owner) if:

- it doesn't validate against `skills/rizzstack-outside-review/verdict.schema.json`;
- `head_sha`, `repo` or `pr` differ from the packet, or the PR head has moved since dispatch (a
  moved head means a new review, never a carried verdict);
- `reviewer.model_family` equals the author's model family;
- a blocking finding cites evidence that isn't in the artifacts;
- the attempt wrote outside `review/verdict.json` and `review/scratch/**`.

## Posting

The coordinator posts one `COMMENT` review pinned to `commit_id = head_sha`. The **first line** is
fixed, and the gate parses it (`.github/scripts/lease_tamper.py` `parse_review_line`,
`tests/fixtures/review_lines.json`):

- approve: `APPROVE (independent review — <identity>, model family <family>) at <head sha>`
- changes_requested: `CHANGES REQUESTED (independent review — <identity>, model family <family>: <first blocking title>) at <head sha>`
- needs_owner: nothing is posted. The owner gets `summary` and `owner_decisions`.

The body is rendered from the verdict: summary, `checked`, blocking findings (with failure
scenarios), then follow-ups and nits. Evidence files are attached or linked from the attempt record.

`post_mode` decides who presses the button:

| post_mode | changes_requested | approve |
|---|---|---|
| `confirm` (default) | the owner confirms each batch | the owner confirms each batch |
| `auto_reject` | posted automatically | the owner confirms |
| `auto` | posted automatically | posted automatically, except for the paths below |

Whatever the mode, the owner confirms approvals on PRs that touch the gate or merge lane
(`.github/**`, `scripts/merge_lane_*`, the vendored `lease_tamper.py`), the deploy path
(`apply-update`, `deploy_payload.py`, `launcher-admin.ps1`, `start.ps1`), `.rizzstack/**`
(leases and contracts), or `policy/**`.

`auto` needs two things first: the reviewer posts under its own GitHub identity
(`policy/identities.md`), with the gate trusting that login, and **shadow calibration** is
complete. In shadow calibration, review bees review the same heads as the owner or outside
reviewer without posting, until ≥10 PRs (≥3 of them `gate_security` or `deploy_path`) show
zero missed blocking findings and at most 20% false blocks. The owner decides the promotion.
While reviews are posted from the owner's account, the gate can only trust their **text**, so
`post_mode` must stay `confirm` or `auto_reject`.

## Recording

Every verdict, posted or not, gets one line in the campaign record: repo#PR, head sha, verdict,
reviewer model/family, attempt id, and post status. For slice PRs with an ACDD contract, an approve
verdict is also recorded as signed REVIEW evidence (`hermes bundlebees tdd review <contract>
--reviewer <identity> --role reviewer --reviewer-key <enrolled key>`).

## Status and follow-ups

This page and `skills/rizzstack-outside-review/` define the protocol. Until the pieces below land, a human
operator or an outside agent can follow the same skill by hand: the packet assembled manually, the
verdict posted after the owner's yes.

1. Packet builder (bundlebees): `review-packet <repo>#<pr>` → the staged `review/` tree.
2. Review dispatch (hyvv/bundlebees): the `roles.reviewer` config, the independence check, and a
   read-only security profile for review bees.
3. Verdict validator and poster (bundlebees): schema validation, rendering, `post_mode`, and the
   campaign record line.
4. Reviewer identity: the `rizzstack-reviewer` GitHub App (`policy/identities.md`). The gate then
   accepts `approve_independent` only from that app's login or the owner's, instead of trusting the text.
5. Move `verdict.schema.json` to workflow `schemas/` as the canonical `rizzstack.review-verdict/1`.
