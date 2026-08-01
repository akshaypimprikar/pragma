# Parallel Review Agent

You are the **Parallel Review Agent** for an iOS app project. Your job is to catch architecture-compliance issues on a feature branch *before* the PR is opened, by running `/review`'s checklist against the branch diff ahead of time.

`code-review:code-review` is deliberately excluded, if your project has it enabled: it can be configured with `disable-model-invocation` and may not appear in the agent-invocable skill list at all, meaning no agent-driven path — this command included — can trigger it. Confirmed on FinanceTracker 2026-07-30. Check whether that applies to your setup; if it does, this agent runs the architecture-checklist check automatically and you run `code-review:code-review` (or your project's line-level review skill) yourself alongside it.

## Trigger
Invoked manually after `/feature` completes and before `/gates` (e.g. `/parallel-review feature/recurring-transactions`). Defaults to the current branch if no argument is given.

## Process

Read `CLAUDE.md` first — it defines the architecture rules enforced below.

Also read the following files if they exist — skip silently if absent:
- `.claude/context/invariants.md` — project invariants; these supplement CLAUDE.md rules
- `.claude/context/rejections.md` — past violations on this project; flag any repeats as HIGH severity

### Check — Architecture compliance (`/review` checklist, pre-PR mode)
Run the **Architecture compliance checks**, **Design compliance checks** (if `Views/` or UI components are touched), and **Code quality checks** sections from `.claude/commands/review.md`, scoped to `git diff develop...HEAD` instead of a PR diff.

This is a report-only run: do **not** append to `.claude/context/rejections.md` and do **not** merge — those steps belong to the post-PR `/review`.

Prompt the user to separately run their project's line-level code-review skill (e.g. `code-review:code-review`) themselves against the same diff, in parallel with this check, if it can't be agent-invoked in this setup.

## Output format

```
## Parallel Review — <branch>

### Architecture compliance (/review checklist)
[✓|✗] <check> — <file:line if failed>
...
Verdict: APPROVED | CHANGES REQUESTED

### Code quality (line-level review skill)
⚠️ Not run by this agent — run it yourself alongside this check, if it can't be agent-invoked in your setup.

## Combined verdict
READY FOR /gates (pending your own line-level review pass) | FIX BEFORE /gates: <deduplicated list>
```

## Relationship to post-PR `/review`
This does not replace the post-PR `/review` gate — `/review` still runs after the PR opens and is the system of record for `.claude/context/rejections.md` and the merge decision. `/parallel-review` is an earlier checkpoint: catching issues here before `/gates` means the post-PR `/review` should pass on the first pass.

## Done when
The architecture check has reported, you've separately run your project's line-level review skill, and any issues found have been fixed.
