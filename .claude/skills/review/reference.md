# Review — reference

Background for `SKILL.md`. Nothing here changes a check, severity or blocking rule; those all stay in `SKILL.md`.

## Context isolation

By default `/review` runs in the same session as `/feature` and `/gates` — `gates/SKILL.md` invokes gates "at the end of every `/feature` session," and `/pr-followup` chains `/review` immediately after. This command splits its work so that session context matters as little as possible:

- **Gate verification** stays in this session, but it is evidence-based: the deterministic gates are re-run at the PR HEAD SHA instead of trusting the pasted summary, so a wrong or stale summary is caught by output, not by the reviewer's impression.
- **Judgment checks** (design compliance, code quality) run in a fresh-context subagent that gets only the inputs listed under "Judgment checks" in `SKILL.md`, and never the implementer's transcript, the PR body, commit messages, the gate summary, or this PR's own log entries. This is the orchestrator / implementer / isolated-reviewer split other pipelines use.

What stays shared: this session still decides which subagent findings reach the verdict. That is why the subagent's raw report is posted with the verdict and a dismissal must quote the disproving code. Anyone auditing the PR can compare the raw report with the accepted and dismissed list, and see every finding the isolated reviewer raised and why any were rejected.

The project also gets **external auditability**: posting the verdict as a real, separate GitHub review object (see "Posting the verdict to GitHub" in `SKILL.md`) means anyone auditing the repo from outside the session can see review happened and compare its content against the diff.

Running `/review` in a fresh Claude Code session against the PR number also isolates the gate-verification half; nothing about this command requires session continuity.

## Tip — automate the review-fix loop
While a PR sits in CHANGES REQUESTED (or waiting on CI), the user can avoid manually re-checking by running, as a separate top-level command:
```
/loop 5m "Check PR <N> for new review comments or failing CI. If found, fix them, push, and rebase on develop if behind. Stop once the PR is approved and CI is green."
```
This is the generic `/loop` skill with a literal prompt — there is no dedicated `/babysit` command. `/loop` re-runs the prompt on the given interval until the stop condition in the prompt is met or the user cancels it.

