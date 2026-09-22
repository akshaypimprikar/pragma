---
name: pr-followup
description: Auto-chain review, test, and code-review immediately after a PR is opened, with no human trigger needed for any of the three. Invoke right after a PR is created, or manually against an existing PR.
disable-model-invocation: true
---

# PR Followup Agent

Auto-chains `/review`, `/test`, and `code-review:code-review` immediately
after a PR is opened — none of the three need a human trigger.

Note: `code-review:code-review` is a Claude Code skill and may not be
invocable at all — either because your project sets
`disable-model-invocation`, which removes it from the agent-invocable skill
list, or because this coding agent has no such skill mechanism. Either way,
step 4 below catches the invocation error and continues to reporting rather
than halting the whole command; you'll still need to run an equivalent
review yourself before merging.

## Trigger
Invoked right after `gh pr create` succeeds, or manually against an existing
PR: `/pr-followup 71` or `/pr-followup fix/some-branch`.

## Process
1. Run `/review <PR>`.
2. If the verdict is **CHANGES REQUESTED**, stop — do not run `/test` or
   `code-review:code-review` until the issues are addressed and the branch is
   re-reviewed.
3. If the verdict is **APPROVED**, run `/test <PR>`.
4. Run `code-review:code-review` against the PR. If the invocation errors
   (e.g. `Unknown skill`, whether from `disable-model-invocation` or from
   running on an agent without this skill), don't stall — print this line
   instead and continue to step 5:
   `⚠️ code-review:code-review couldn't be invoked here (disable-model-invocation, or unsupported on this agent) — run an equivalent review yourself before merging.`
5. Report all three verdicts (or the fallback warning in place of the third).

## Done when
`/review` and `/test` have reported, and `code-review:code-review` has either
reported or printed the fallback warning. Do not merge — merging is the
user's call once every configured check is clean.
