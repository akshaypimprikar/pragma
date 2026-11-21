# Gates — reference

Background for `SKILL.md`. `scripts/check_gates.sh` (Gates 0–11) and `SKILL.md` hold every gate command, threshold and pass rule, and `/review` re-runs the deterministic ones. This file restates a few rules only to explain them (Gate 2's zero-test failure, the `/loop` stop condition), so edit both files together.

## Gate 1 — why no pipeline; Xcode 27 advisory

`xcodebuild` writes to a log file and `xcsift` reads that file afterwards — there is no pipeline, so
its own exit status is captured directly (a `| xcsift` pipeline hides it unless `pipefail`,
`PIPESTATUS` (bash) or `pipestatus` (zsh) is used, and `2>&1 | xcsift` on an empty or crashed run
prints a clean-looking summary). Pass: the script prints `[✓] Build` — non-empty log, exit 0, and the `BUILD SUCCEEDED`
marker. Fail: anything else — an empty log or a non-zero exit is a failure, never "no errors seen".
Stop immediately — a test run on a broken build is meaningless.

Advisory: a compile error in SwiftUI code that built before an Xcode major-version update may be an SDK
source-compatibility break rather than a bug in the change — see
[`docs/xcode-27-sdk-migration.md`](https://github.com/akshaypimprikar/pragma/blob/develop/docs/xcode-27-sdk-migration.md)
for the two known Xcode 27 patterns.

## Gate 2 — why zero executed tests fails

Pass: the script prints `[✓] Tests — N tests executed` with N above zero. The count is required because
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
/loop Fix failing gates and re-check. Stop when all blocking gates pass (Gates 1–11, 10 blocking; Gate 8 abstraction bloat is advisory, `[i]` only, never blocks): tree clean and SHA recorded, build succeeds, all tests pass with a non-zero executed count, no TODO/FIXME/HACK in changed files, branch name valid, CHANGELOG Unreleased section populated (or Gate 5 N/A on a feature-log-only PR or a `release/*` branch; a row the script prints as N/A on a release-lane branch counts as met), coverage ≥80% on new files, security review clean, RED commit precedes GREEN commit for every new file in a scoped layer, architecture & layer-rule compliance clean, gate integrity clean.
```
Claude iterates on fixes and re-checks until all conditions hold. Keep the condition deterministic and verifiable — exit-code or grep-checkable facts only. "implement the feature correctly" is not verifiable and risks the loop satisfying the literal wording without a real fix.

To drive the full feature-to-PR cycle autonomously (no interval = Claude self-paces):
```
/loop run /feature on the next uncovered task from the plan. Then run /gates. Stop when all blocking gates pass.
```

## Guard — what each layer covers

Gates 0–11 are agent-instruction checks, so an agent under pressure to make a stuck gate pass could edit a gate definition instead of fixing the violation, then report a clean summary. This is most likely in an unattended `/loop` run with no human turn in between. Two layers close that:

- **Live block:** `.claude/hooks/guard_protected_paths.py`, a native `PreToolUse` hook installed by `setup.sh` and `/pragma:init` (`--no-guard-hook` opts out). On a `feature/*` branch it blocks `Write`, `Edit` and `MultiEdit`, and, best effort, `Bash` writes, to every path in `GUARDED_PATH_GLOBS` (`scripts/check_gate_integrity.py`): skills (`.claude/skills/*/SKILL.md`, `.claude/skills/*/reference.md`), `AGENTS.md`, `CLAUDE.md`, `CONSTRAINTS.md`, `.claude/context/invariants.md`, `.claude/settings.json`, `.claude/hooks/*`, `scripts/check_*`, `scripts/pipeline_lanes.json`, `scripts/detect_build_target.sh`, `scripts/select_simulator.py`, `.github/workflows/*`, `.codex/hooks.json` and `.codex/config.toml`, at the repo root or under any subdirectory (a Claude project inside a monorepo). The fix is to make that change on a `chore/*` or `fix/*` branch.
- **CI backstop:** Gate 11's check 1 (`scripts/check_gate_integrity.py`) flags the same set of files on a `feature/*` PR, through `GUARDED_PATH_GLOBS`. The `gates` job (`.github/workflows/gates.yml`) runs on every PR, so a PR that only touches them still runs it. A step in that job runs the hook's self-test, which fails if the hook's glob list and the script's differ (it runs the PR's own copy, so it catches an honest slip, not a deliberate one). This catches a plain commit and push that never went through Claude Code.

What is not covered: the hook's Bash detection is a best-effort parse, so `python -c`, interpreter heredocs, variable or glob expansion (including `cd $VAR` before a write), `find -exec` or `-delete`, `xargs rm` fed from stdin, `git checkout <ref> -- file`, `git restore` and `rm -rf <dir that only contains a nested project>` are not detected. A symlink that already exists is followed; one created and written through in the same command is not. The hook only runs in sessions that load the project's own `.claude/settings.json`: verified 2026-09-23 that a session started from a parent directory did not fire it, so start Claude Code from the project root. The CI backstop applies either way. The hook fails open on bad input, no git repo or a detached HEAD, and it allows every edit off `feature/*`, except a command that may run a shell-run `apply_patch`, `git apply`, `git am` or `patch` in two cases, both blocked on every branch: after a `cd` it can't work out (a variable, `$( )`, a glob, zsh's `cd old new`, `env -C`), because the hook can't tell which branch that directory is on; and when it can't read every diff header (listed below). For those tools it follows #140's model: on `feature/*`, a command that names one is blocked unless every command in it is read-only, and the directories checked include every path named in the command text and every name in a diff header it can read (from the command or a file it names), each checked as written and with symlinks followed; a command the hook can't read in full is a possible run (2026-11-19 fail-closed redesign; it replaces letter matching of spelled names, which leaked a new spelling every review round): a command name the shell builds (a variable, `$( )`, glob, brace or zsh `=cmd`: `g${E}it`, `/usr/bin/gi?`, `=rm`); a word the shell builds, given to a program outside the hook's no-exec list (`eval`, shells, `xargs`, `env`, `trap`, `fd -x`, `make`, any unknown program; git with `-c`, `--config-env`, a runner option such as `rebase --exec` or a subcommand outside its list; `find -exec`; `sed` with an `e` command; `rg --pre`; `[[ ... -eq ... ]]`) or as a wrapper option; a word `xargs` may add to such a program (`echo apply | xargs -I{} git {} x.diff`), any `parallel`, `sem`, `env_parallel`, `parset` or `gxargs` command, or a `find -exec` command taking `{}` as its program or git subcommand; a variable that runs its value (`GIT_EDITOR`, `GIT_SEQUENCE_EDITOR`, `PAGER`, `GIT_SSH_COMMAND`, `BASH_ENV`, `LESSOPEN`, `PS4`) set to a built word, by `printf -v` (a built name too), `read`, `for NAME in` with built or quoted values (or zsh `for NAME (...)`), or `${NAME:=}`; an alias, or a `hash` with `=` or `-p` (any `alias`/`hash` word in the command part, read from the parsed words: a quoted string is one word, so `--body "alias a=git"` is prose), or a command table entry (`BASH_CMDS`, `BASH_ALIASES`, zsh `aliases[`, or `aliases=(`/`aliases+=(` as an assignment, `commands[`, `functions[`); a zsh alternation or extended glob (`(g|x)it`, `@(..)`); code evaluated at run time (arithmetic, an array subscript holding an expansion, `${!x}`, `@P`, zsh `${(..)}` outside single quotes; `let`, `(( ))`, `declare -i` outside quotes; a subscript holding `$( )` anywhere); a shell that may read commands from stdin (no `-c` and no script, `-s`, a `-` script or one under `/dev` or `/proc` or ending in `stdin` (`/dev/./stdin` too), an option that takes an argument such as `-o pipefail`, `-euo pipefail` or `--rcfile`, or `.`/`source` of such a file or of `<(...)`); an unquoted heredoc that runs `$( )`; or commands nested more than 3 levels deep. Comments are dropped first, only where the shell surely reads one (an unescaped `#` at a line start or after whitespace, `;`, `&` or `|`, outside quotes and before any `$( )`, `${ }` or backtick on the line); a quote, `<<` or trailing backslash in a comment hides nothing, and a heredoc starts only at an unquoted `<<`. A quoted string given to a program outside the list (`bash -c '...'`, `eval '...'`, `trap '...' EXIT`, `env -S '...'`) is parsed as commands, and so is a quoted glob that holds a `/` or is the program's first non-option operand (`ssh h '/usr/bin/gi?' apply`, `watch 'gi?' apply`), except for `find` and package, build or test runners (`npm`, `swift`, `cargo`, `go`, `gradle`, `pytest`, `brew`, ...), whose globs are data (`npm run 'test:*'`). A possible run is blocked when the session's directory, a `cd` target, a path named in the command or a folder in the header of a diff file it names literally is on `feature/*`; a directory built at run time (a variable, `$( )` or glob `cd` target) is not seen for it. Interpreters (`python3`, `node`, `perl`, `ruby`, `awk`) are on the no-exec list: what their own code runs (`system()`) is not seen. Tradeoff: on `feature/*`, `"$PY" x.py`, `make t$i`, `bash -c "$X"`, `bash -o pipefail x.sh` (an option argument reads like a script), `source <(cmd)`, `GIT_SSH_COMMAND="ssh -i $KEY" git push`, `PS4='+ ${LINENO}: ' bash -x x.sh`, `printf -v "$name" ...` and `read PAGER` block; `git push origin feature/git$BRANCH`, `git -C "$P" status` and `${GIT_BIN:-git} status` no longer block on every branch, and alias prose in a quoted `--body` no longer blocks. A command naming one of these tools and more than 32 files, a file over 1 MB, diffs with more than 8,192 header names (or over the header-work budget, or a directory check that takes over 20 s), a word with an unquoted glob character, brace expansion, zsh `<n-m>` range or `(a|b)` group, or bash extglob, or any `$` or backtick outside single quotes, even in a word unrelated to the diff (`npm version patch -m "v$V"`), is blocked on every branch, unread. Checking the files any command writes takes at most 20 s, each file once; past that the command is blocked on every branch, as is a shell-run `apply_patch` pairing more than 100,000 file names with directories. A diff it can't read (`curl ... | patch`) is not seen; nor is a quoted glob command with no `/` after a program's first operand (`ssh -p 22 h '[g]it' apply`, once a file named `git` exists), a script file or `/dev/stdin` link the same command writes and then runs (`printf ... > s.sh; bash s.sh`), a git alias set outside the command, or a tool name or directory built at run time or passed only through a pipe, file or variable. Neither layer catches an agent that renames its branch away from `feature/*`. `.claude/settings.local.json` is not on the list. Pattern sourced from `karanb192/claude-code-hooks`'s "config-guard" hook, surfaced in the 2026-09-08 Agentic AI Intelligence Report.
