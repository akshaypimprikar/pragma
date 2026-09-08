# PR Followup Agent

Auto-chains `/review`, `/test`, and `code-review:code-review` immediately
after a PR is opened — none of the three need a human trigger.

Note: `code-review:code-review` can be configured per-project with
`disable-model-invocation`, which removes it from the agent-invocable skill
list entirely — if your project has that set, this step will fail to invoke
and you'll need to run it yourself before merging. That's a project-config
issue to fix, not the expected default.

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
   (e.g. `Unknown skill`, on a project with `disable-model-invocation` set),
   don't stall — print this line instead and continue to step 5:
   `⚠️ code-review:code-review couldn't be agent-invoked on this project (disable-model-invocation?) — run it yourself before merging.`
5. Report all three verdicts (or the fallback warning in place of the third).

## Done when
`/review` and `/test` have reported, and `code-review:code-review` has either
reported or (on a `disable-model-invocation` setup) printed the fallback
warning. Do not merge — merging is the user's call once every configured
check is clean.
