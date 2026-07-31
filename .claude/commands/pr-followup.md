# PR Followup Agent

Auto-chains `/review` then `/test` immediately after a PR is opened — the two
pipeline stages that can run without a human trigger.

`code-review:code-review` is deliberately excluded, if your project has it
enabled: it can be configured with `disable-model-invocation` and may not
appear in the agent-invocable skill list at all, meaning no agent-driven path
— this command included — can trigger it. Confirmed on FinanceTracker
2026-07-30. Check whether that applies to your setup; if it does, this
command runs the two automatable stages and reminds you to run the rest
yourself.

## Trigger
Invoked right after `gh pr create` succeeds, or manually against an existing
PR: `/pr-followup 71` or `/pr-followup fix/some-branch`.

## Process
1. Run `/review <PR>`.
2. If the verdict is **CHANGES REQUESTED**, stop — do not run `/test` until the
   issues are addressed and the branch is re-reviewed.
3. If the verdict is **APPROVED**, run `/test <PR>`.
4. Report both verdicts, then print exactly this line (adjust the skill name
   if your project's line-level review skill differs):
   `⚠️ code-review:code-review still needs to run manually — it can't be triggered by an agent. Run it yourself before merging.`

## Done when
`/review` and `/test` have both reported and the code-review reminder has been
printed. Do not merge — merging is the user's call once every configured
check (review, test, and any manual code-review pass) is clean.
