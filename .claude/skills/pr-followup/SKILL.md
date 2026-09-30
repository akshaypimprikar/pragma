---
name: pr-followup
description: Auto-chain code-review and review immediately after a PR is opened, with no human trigger needed for either. Invoke right after a PR is created, or manually against an existing PR.
disable-model-invocation: true
---

# PR Followup Agent

Auto-chains the built-in `code-review` (at `medium` effort) and then `/review` immediately after a PR
is opened, and records each result where the `review-evidence` CI check reads
it (`scripts/check_review_evidence.py`).

`code-review` runs first so its fixes land before the `/review`
rounds; a fix after an APPROVED verdict would otherwise need `/review --confirm`.

Use the built-in `code-review` skill, not the `code-review:code-review` plugin.
The plugin starts 10-20 subagents per run (Haiku triage, 5 Sonnet reviewers, a
Haiku scorer per issue), which makes every PR's review several times more
expensive; `/review`'s isolated subagent already covers design and rule
compliance.

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
   For the `docs`, `release` and `sync` lanes, report the lane and stop: they
   need neither step below. For the `pipeline` lane (or a PR that also touches
   pipeline paths), make sure the PR body has a non-empty
   `Motivating incident: <what went wrong, with a link or date>` line (or
   `none (<reason>)`); add it if missing, since `review-evidence` fails without it.
2. Run the built-in `code-review` once: `code-review <PR> medium --comment`.
   Fix any issue it posts, commit and push. Then add or replace one line in the PR body with the head
   SHA it reviewed: `code-review: <comment URL> at <sha>`, or
   `code-review: no issues at <sha>` when it posted nothing.
3. If the invocation errors (e.g. `Unknown skill`), don't stall. Print
   `⚠️ code-review couldn't be invoked here (unavailable, or unsupported on this agent) — run an equivalent review yourself before merging.`
   and continue.
4. Run `/review <PR>`. It posts the verdict and updates the PR body's `Review:`
   line. Stop at CHANGES REQUESTED until the issues are fixed; `/review` allows
   at most two full rounds.
5. Report both results and the lane.

`/test` is not in this chain: it runs between `/feature` and `/gates`.

## Done when
Both results are reported and recorded in the PR body (or the fallback warning
printed). Do not merge — per AGENTS.md/CLAUDE.md's Merge rule, the required
checks decide mergeability and the user merges.
