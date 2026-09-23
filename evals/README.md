# Plugin evals

These cases test whether pragma changes how Claude answers. They use `claude plugin eval` (Claude Code 2.1.269 or later).

Each case sends one prompt to a fresh, read-only session. The session has only pragma loaded. Then simple checks (graders) score the reply. By default each case runs three times with pragma and three times without it. The difference between the two scores (`Δ`) shows what the plugin added.

## Run them

```bash
claude plugin eval . --trust-plugin --no-publish --max-cost-usd 5
```

A full run makes about 24 agent runs and costs about $2 at list price (measured at $2.08 on 2026-09-23, judge calls included). Every run and every judge check is a real model call on your account. Use `--runs 1 --ablation none` for a cheap smoke test. Results go to `evals/results/`, which git ignores.

## The cases

| Case | What it checks |
|---|---|
| `ready-for-pr-lists-gates` | For "is my branch ready for a PR", the `deterministic-pr-gates` skill fires and the answer lists checkable gates. Also checks that the change-scope gate (Gate 0) comes first. |
| `docs-only-branch-skips-build` | For a docs-only branch, the answer skips the build and test gates and says a skipped gate is reported as skipped, not passed. |
| `weakening-a-gate-to-get-green` | For a plan to disable a test and lower a coverage threshold, the answer pushes back, names gate integrity, and points to a separate `chore/*` or `fix/*` branch. |
| `unrelated-request-stays-out-of-the-way` | A plain Swift question does not fire the skill. |

## Latest results

Claude Code 2.1.278, 2026-09-23, three runs per arm. Each case ran on the suite files as they stand now. The broad and unrelated cases were re-run alone after their last edits. The other two cases had no edits after their run.

| Case | With pragma | Without | Skill fired |
|---|---|---|---|
| `ready-for-pr-lists-gates` | 1.00 | 0.22 | 3 of 3 |
| `docs-only-branch-skips-build` | 1.00 | 1.00 | 3 of 3 |
| `weakening-a-gate-to-get-green` | 0.44 | 0.33 | 0 of 3 |
| `unrelated-request-stays-out-of-the-way` | 1.00 | 1.00 | 0 of 3, as wanted |

At the default threshold a case passes only when every scored grader passes on every run. The command exits 1 when any case fails, so it exits 1 today: 3 of 4 cases pass. `weakening-a-gate-to-get-green` fails because the skill does not fire on that prompt. Its `separate-branch` grader passed on 0 of 6 runs, with and without the plugin, because that advice comes from the skill's Gate 10.

The docs-only case scores the same with and without the plugin. The base model already gives the right answer, so that case does not show what pragma adds.

## What the results mean

A `skill-fired` grader is an indicator only in a two-arm run. It never counts toward the score, because it cannot pass without the plugin.

Small suites are noisy. Three runs per arm is not enough to call a small `Δ` real. Read the per-grader lines, not just the score.

These cases cover the `deterministic-pr-gates` skill only. The slash commands (`/feature`, `/gates`, `/review` and the rest) need Bash and a real project, so they are not covered here.
