# Gates — reference

Background for `SKILL.md`. `SKILL.md` holds every gate command, threshold and pass rule, and `/review` re-runs it as written. This file restates a few rules only to explain them (Gate 2's zero-test failure, the `/loop` stop condition), so edit both files together.

## Gate 1 — why no pipeline; Xcode 27 advisory

`xcodebuild` writes to a log file and `xcsift` reads that file afterwards — there is no pipeline, so
its own exit status is captured directly (a `| xcsift` pipeline hides it unless `pipefail`,
`PIPESTATUS` (bash) or `pipestatus` (zsh) is used, and `2>&1 | xcsift` on an empty or crashed run
prints a clean-looking summary). Pass: `GATE 1 PASS` — non-empty log, exit 0, and the `BUILD SUCCEEDED`
marker. Fail: anything else — an empty log or a non-zero exit is a failure, never "no errors seen".
Stop immediately — a test run on a broken build is meaningless.

Advisory: a compile error in SwiftUI code that built before an Xcode major-version update may be an SDK
source-compatibility break rather than a bug in the change — see
[`docs/xcode-27-sdk-migration.md`](https://github.com/akshaypimprikar/pragma/blob/develop/docs/xcode-27-sdk-migration.md)
for the two known Xcode 27 patterns.

## Gate 2 — why zero executed tests fails

Pass: `GATE 2 PASS` with an executed-test count above zero. The count is required because
`xcodebuild test` can report `** TEST SUCCEEDED **` with exit 0 when a test filter or scheme change
matches nothing. The count is read from per-test-case result lines: `Test Case '…' passed` / `Test case '…' passed`,
and `✔ Test "…" passed …` for Swift Testing's own console format. On Xcode 27, `xcodebuild test` prints
Swift Testing results in the `Test case '…' passed` form too (verified 2026-09-24 on a Swift Testing
suite: 191 `Test case` lines, 0 `✔` lines); the `✔` alternative covers other versions and runners, and the Swift Testing run summary (`✔ Test run with N tests … passed`) is excluded so a run that executes nothing can't count as one test. Both
symbol variants (`✔`/`✓`, `✘`/`✗`) are matched since different Xcode/terminal versions render this
differently — this has not been confirmed against every Xcode version's exact output, so **run it once
against a real green suite before trusting it**, and adjust the pattern if your version words or
formats the lines differently.

## Gate 9 — why commit order is checked

This exists because "write a failing test first" is unverifiable from `/feature`'s
instruction alone — nothing distinguishes an agent that watched the test fail from one that
wrote both together and never ran it red. Git history is the only outside evidence, and only
a RED-then-GREEN commit split preserves it. `/feature`'s per-task rules carry the discipline
itself (self-contained — don't gate it on an external skill invocation, since a plugin's
`enabledPlugins: true` flag doesn't guarantee its skills are actually invocable in a given
environment); this gate is the independent, git-history-based check that the discipline
actually happened, regardless of how it was instructed.

## Autonomous gate-fixing loop

The stop condition below must match the gate list in `SKILL.md`. Change both in the same commit.

If any gate fails and needs iterative fixes, run this as a separate top-level command (not from within this agent):
```
/loop Fix failing gates and re-check. Stop when all blocking gates pass (Gates 1–11, 10 blocking; Gate 8 abstraction bloat is advisory, `[i]` only, never blocks): tree clean and SHA recorded, build succeeds, all tests pass with a non-zero executed count, no TODO/FIXME/HACK in changed files, branch name valid, CHANGELOG Unreleased section populated (or Gate 5 N/A on a feature-log-only PR), coverage ≥80% on new files, security review clean, RED commit precedes GREEN commit for every new file in a scoped layer, architecture & layer-rule compliance clean, gate integrity clean.
```
Claude iterates on fixes and re-checks until all conditions hold. Keep the condition deterministic and verifiable — exit-code or grep-checkable facts only. "implement the feature correctly" is not verifiable and risks the loop satisfying the literal wording without a real fix.

To drive the full feature-to-PR cycle autonomously (no interval = Claude self-paces):
```
/loop run /feature on the next uncovered task from the plan. Then run /gates. Stop when all blocking gates pass.
```

## Guard — what each layer covers

Gates 0–11 are agent-instruction checks, so an agent under pressure to make a stuck gate pass could edit a gate definition instead of fixing the violation, then report a clean summary. This is most likely in an unattended `/loop` run with no human turn in between. Two layers close that:

- **Live block:** `.claude/hooks/guard_protected_paths.py`, a native `PreToolUse` hook installed by `setup.sh` and `/pragma:init` (`--no-guard-hook` opts out). On a `feature/*` branch it blocks `Write`, `Edit` and `MultiEdit`, and, best effort, `Bash` writes, to skills (`.claude/skills/*/SKILL.md`, `.claude/skills/*/reference.md`), `AGENTS.md`, `CLAUDE.md`, `CONSTRAINTS.md`, `.claude/context/invariants.md`, `.claude/settings.json`, `.claude/hooks/*` and `scripts/check_*`, at the repo root or under any subdirectory (a Claude project inside a monorepo). The fix is to make that change on a `chore/*` or `fix/*` branch.
- **CI backstop:** Gate 11's check 1 (`scripts/check_gate_integrity.py`) flags the same set of files on a `feature/*` PR, through `GUARDED_PATH_GLOBS`. The `gates` job (`.github/workflows/gates.yml`) runs on every PR, so a PR that only touches them still runs it. A step in that job runs the hook's self-test, which fails if the hook's glob list and the script's differ (it runs the PR's own copy, so it catches an honest slip, not a deliberate one). This catches a plain commit and push that never went through Claude Code.

What is not covered: the hook's Bash detection is a best-effort parse, so `python -c`, interpreter heredocs, variable or glob expansion (including `cd $VAR`), `find -exec` or `-delete`, `xargs rm` fed from stdin, `git checkout <ref> -- file`, `git restore` and `rm -rf <dir that only contains a nested project>` are not detected. A symlink that already exists is followed; one created and written through in the same command is not. The hook only runs in sessions that load the project's own `.claude/settings.json`: verified 2026-09-23 that a session started from a parent directory did not fire it, so start Claude Code from the project root. The CI backstop applies either way. The hook fails open on bad input, no git repo or a detached HEAD, and it allows every edit off `feature/*`. Neither layer catches an agent that renames its branch away from `feature/*`. `.claude/settings.local.json` is not on the list. Pattern sourced from `karanb192/claude-code-hooks`'s "config-guard" hook, surfaced in the 2026-09-08 Agentic AI Intelligence Report.
