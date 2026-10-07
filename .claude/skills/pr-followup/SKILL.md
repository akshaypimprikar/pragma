---
name: pr-followup
description: Run the review chain for the PR's lane immediately after a PR is opened: /review alone for a pipeline PR, the built-in code-review then /review for an app PR, nothing for docs, release and sync PRs. Invoke right after a PR is created, or manually against an existing PR.
disable-model-invocation: true
---

# PR Followup Agent

Runs the review chain for the PR's lane immediately after a PR is opened, and records each
result where the `review-evidence` CI check reads it (`scripts/check_review_evidence.py`). A `pipeline` PR gets `/review` alone.
An `app` PR gets the built-in `code-review` (at `medium` effort) and then `/review`. The
`docs`, `release` and `sync` lanes get neither.

On an `app` PR, `code-review` runs first so its fixes land before the `/review`
rounds; a fix after an APPROVED verdict would otherwise need `/review --confirm`.

Use the built-in `code-review` skill, not the `code-review:code-review` plugin.
The plugin starts 10-20 subagents per run (Haiku triage, 5 Sonnet reviewers, a
Haiku scorer per issue); from 2026-09-28, when it became the default here, it
made each PR's review several times more expensive. `/review`'s isolated
subagent already covers design and rule compliance.
Note: `code-review` is a Claude Code skill and may not be invocable at all —
either because it is unavailable in this session, or because this coding
agent has no such skill mechanism. Either way, step 3
below catches the invocation error and continues to reporting rather than
halting the whole command; you'll still need to run an equivalent review
yourself before merging.

## Trigger
Invoked right after `gh pr create` succeeds, or manually against an existing
PR: `/pr-followup 71` or `/pr-followup fix/some-branch`.

## Process
1. Run `python3 scripts/check_pr_lane.py --git origin/<base> --head-branch <head> --base-branch <base>`.
   The script prints the lane: `app`, `pipeline`, `docs`, `release` or `sync`.
   For the `docs`, `release` and `sync` lanes, report the lane and stop: they
   need neither step below.
   For the `pipeline` lane, or any PR that also touches pipeline paths (an `app`-lane PR still owes the pipeline lane's `motivating_incident`, per `evidence_for_change` in `scripts/check_pr_lane.py`), make sure the PR body has a non-empty
   `Motivating incident: <what went wrong, with a link or date>` line (or
   `none (<reason>)`); add it if missing, since `review-evidence` fails without it.
   **Small pipeline PRs:** when the lane is `pipeline` and the PR changes fewer than 30 lines
   (additions plus deletions, `small_pr` in `scripts/pipeline_lanes.json`) and touches none of its
   `exclude_paths` (the files that enforce the rules: hooks, gates, review, `scripts/check_*`, lane configs,
   workflows), skip `/review` too: the gates,
   the lane check and `review-evidence` decide, and `review_verdict` is not required. Report
   "small pipeline PR, no model review" and stop. Count with `git diff --shortstat origin/<base>...HEAD`.
   **Lane routing:** a `pipeline` PR gets one reviewer, so skip steps 2–3 and go to step 4.
   Only the `app` lane (which also covers a PR touching app and pipeline paths) runs both,
   because on pipeline diffs the second reviewer doubled the token cost and added
   design suggestions rather than defects (2026-10-05: the same 4-line Gate 0 fix
   got 7 inline comments across a project PR and pragma #100).
2. `app` lane only: run the built-in `code-review` once: `code-review <PR> medium --comment`.
   Fix any issue it posts, commit and push. Then add or replace one line in the PR body with the head
   SHA it reviewed: `code-review: <comment URL> at <sha>`, or
   `code-review: no issues at <sha>` when it posted nothing.
3. `app` lane only: if the step 2 invocation errors (e.g. `Unknown skill`), don't stall. Print
   `⚠️ code-review couldn't be invoked here (unavailable, or unsupported on this agent) — run an equivalent review yourself before merging.`
   and continue.
4. Run `/review <PR>`. It posts the verdict and updates the PR body's `Review:`
   line. Stop at CHANGES REQUESTED until the blocking issues are fixed. `/review`
   runs one round by default and a second only to confirm blocking fixes.
5. Report the results (one for a `pipeline` PR, two for `app`) and the lane.

`/test` is not in this chain: it now runs between `/feature` and `/gates`.

## Done when
Each result the lane needs is reported and recorded in the PR body (or the
fallback warning printed). Do not merge — per AGENTS.md/CLAUDE.md's Merge rule, the required checks decide
mergeability and the user merges.
