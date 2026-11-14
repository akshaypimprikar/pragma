# Review Rejection Log

<!-- Append one entry per violation per PR. Never edit past entries. -->
<!-- Format:
## YYYY-MM-DD — PR#<N> — <Violation Type>
**What was wrong:** <description>
**Rule violated:** <rule from invariants.md or CLAUDE.md>
**File:** <path:line if known>
**Caught by:** <this review | code-review pass | manual verification>
-->

## 2026-08-18 — PR#49 — New schema-drift check in check_coverage.py was unreachable in the case it was written for
**What was wrong:** The fix added `if schema_drift_files: ... sys.exit(2)` to catch xccov schema drift (missing `lineCoverage` key), but placed it after the pre-existing `if not source_files: sys.exit(0)` guard. Schema drift is uniform across a report — a schema change typically empties `source_files` entirely rather than leaving some files behind — so the empty-files guard fired first and exited 0 (success) before the new diagnostic ever ran. This reproduced, in a worse form, the exact silent-false-pass failure the fix was written to prevent. Three independent code-review passes (shallow bug scan, git-history context, code-comment compliance) converged on the same root cause.
**Rule violated:** No CLAUDE.md exists in this repo — no formal rule, caught on correctness grounds.
**File:** `scripts/check_coverage.py:39-51` (fixed by moving the `schema_drift_files` check ahead of the `not source_files` exit)
**Caught by:** code-review:code-review

## 2026-09-07 — PR#55 — Round 1: unconditional auto-chain regression + exposed pre-existing bug
**What was wrong:** Making `code-review:code-review` auto-chain (per the 2026-09-07 pipeline-review Medium finding) was first implemented as an unconditional call, dropping the pre-existing `disable-model-invocation` graceful-degradation path in `pr-followup.md` and `parallel-review.md` (README.md and `gates.md` also fell out of sync with the same fact). Fixing `parallel-review.md`'s Check 1 also exposed a pre-existing bug: it pointed at `review.md`'s Architecture section, which by design only checks a PR's post-`/gates` summary — unsatisfiable at the pre-PR point `/parallel-review` actually runs.
**Rule violated:** No CLAUDE.md exists in this repo — no formal rule, caught on correctness grounds.
**File:** `pr-followup.md`, `parallel-review.md`, `gates.md`, README.md
**Caught by:** code-review pass (round 1, documented in PR#55 body / `docs/pipeline-review/2026-09-07.md`)

## 2026-09-07 — PR#55 — Round 2: duplicated stale copies of the same fact + missing severity rule
**What was wrong:** Two more stale copies of the `/pr-followup` 2-stage-chain fact surfaced (`test.md`, README.md's `/parallel-review` row), and the `disable-model-invocation` fallback text was independently duplicated across three files instead of having one canonical source — the same duplication pattern that caused the Round 1 regression. `parallel-review.md`'s combined verdict also had no severity rule tying `code-review:code-review` findings into the `READY FOR /gates` decision.
**Rule violated:** No CLAUDE.md exists in this repo — no formal rule, caught on correctness grounds.
**File:** `test.md`, README.md, `pr-followup.md`, `parallel-review.md`
**Caught by:** code-review pass (round 2, documented in PR#55 body / `docs/pipeline-review/2026-09-07.md`)

## 2026-09-07 — PR#55 — Round 3: wording/logic gaps in the consolidation
**What was wrong:** Five gaps introduced while consolidating the fallback: an ambiguous qualifier that could be read as gating an unconditional check, a severity rule that didn't cover Check 1's binary pass/fail output, a `Done when` that dropped the `disable-model-invocation` caveat, an Output-format fallback string that restated canonical wording instead of referencing it, and the canonical Note itself omitting that the fallback degrades gracefully rather than halting.
**Rule violated:** No CLAUDE.md exists in this repo — no formal rule, caught on correctness grounds.
**File:** `parallel-review.md`, `pr-followup.md`
**Caught by:** code-review pass (round 3, documented in PR#55 body / `docs/pipeline-review/2026-09-07.md`)

## 2026-09-07 — PR#55 — Round 4: missing carve-out, missing verdict state, misleading fallback wording, sibling staleness
**What was wrong:** Gate 10's UI-selector-listing command wasn't carved out of the "any output = fail" rule (false-positive risk on UI-test branches); the combined-verdict template had no slot for the qualified `disable-model-invocation` state; the fallback wording read as an interactive wait, contradicting its own "don't stall" clause; `plan.md` carried a sibling stale copy of the parallel-execution claim; and a CHANGELOG entry mislabeled which round fixed which defect.
**Rule violated:** No CLAUDE.md exists in this repo — no formal rule, caught on correctness grounds.
**File:** `parallel-review.md`, `plan.md`, `CHANGELOG.md`
**Caught by:** code-review pass (round 4, documented in PR#55 body / `docs/pipeline-review/2026-09-07.md`)

## 2026-09-07 — PR#55 — Round 5: last stale wording, final restated-fallback string
**What was wrong:** README.md's `/parallel-review` row still described its checks as running "in parallel," stale after Check 2 became a sequential step; `parallel-review.md` Check 2 still restated `/pr-followup`'s fallback substance despite its own instruction not to restate it.
**Rule violated:** No CLAUDE.md exists in this repo — no formal rule, caught on correctness grounds.
**File:** README.md, `parallel-review.md`
**Caught by:** code-review pass (round 5, documented in PR#55 body / `docs/pipeline-review/2026-09-07.md`)

## 2026-09-08 — PR#56 — Round 1: overclaim about Gate 10's diff scope
**What was wrong:** `parallel-review.md`'s Check 1 said Gate 10 "already uses `git diff develop...HEAD`, the same scope this command needs," with no exception noted. Gate 10's UI-selector-listing command actually scans all of `<AppName>UITests/*.swift` unconditionally, not the branch diff like every other Gate 10 command — the framing gave no warning of that one exception.
**Rule violated:** No CLAUDE.md exists in this repo — no formal rule, caught on correctness grounds.
**File:** `.claude/commands/parallel-review.md`
**Caught by:** code-review pass (found while porting this file to FinanceTracker, financetracker-ios PR #105, against its actual Gate 9 — documented in PR#56 commit `8db91c4`)

## 2026-09-08 — PR#56 — Round 2: run-on sentence, ambiguous phrasing, misleading fallback text
**What was wrong:** Three more wording issues in the same file: Check 1 was a single ~150-word run-on sentence covering three different checks and scoping rules; `Done when`'s "combined verdict from the three above" was ambiguous (three checks, or three possible verdict strings?); and Check 2's fallback said to reuse `/pr-followup`'s wording "exactly as documented," but that wording is merge-specific ("run it yourself before merging") and misleading in this pre-PR context, where the next step is `/gates`, not a merge.
**Rule violated:** No CLAUDE.md exists in this repo — no formal rule, caught on correctness grounds.
**File:** `.claude/commands/parallel-review.md`
**Caught by:** code-review pass (found on financetracker-ios PR #105, which ported this same file — documented in PR#56 commit `678fa95`)

## 2026-09-08 — PR#56 — Round 3: reintroduced the fallback-string duplication-drift pattern
**What was wrong:** Round 2's own fix ("adapt its wording") was itself an independently-authored copy of `pr-followup.md`'s fallback warning string with one word swapped — the exact duplication pattern PR#55's Round 2/3/5 entries above already flagged as a repeat-violation risk on this same file. Replaced with an explicit, mechanical substitution rule (take `/pr-followup`'s canonical string, swap `before merging` → `before /gates`) so there is no independently-maintained copy left to drift. Verified: the substitution renders correctly against `pr-followup.md`'s actual string, and the fix references the transformation rather than restating the text.
**Rule violated:** No CLAUDE.md exists in this repo — no formal rule, caught on correctness grounds. (Repeats the pattern named in the PR#55 Round 2/3/5 entries above; caught and fixed pre-merge this time.)
**File:** `.claude/commands/parallel-review.md`
**Caught by:** code-review pass (found by `code-review:code-review` on PR#56 itself — documented in commit `989aaa4`)

## 2026-09-27 — PR#84 — Harness article credited to the wrong author, with a paraphrase shown as a quote
**What was wrong:** From 2026-06-14 (commit `8f02526`) the README credited "Harness engineering for coding agent users" to Martin Fowler. The article is by Birgitta Böckeler, published on martinfowler.com. The README also put "the elephant in the room — still unsolved" in quotation marks, but "still unsolved" was a paraphrase, not her wording. The FinanceTracker merged-PR count was also inconsistent (80+ in one place, 70+ in the other; 121 in fact).
**Rule violated:** No formal rule. Caught on correctness grounds: a quote or citation was never checked against its source.
**File:** `README.md` (Harness Design section)
**Caught by:** manual verification (fact-checking the /brag launch video script against the article, 2026-09-27). Documented in the PR#84 body.

## 2026-09-27 — PR#84 — Harness section misread the article's model
**What was wrong:** The first fix still presented maintainability, architecture fitness and behavior as kinds of feedback sensor, under a "Harness category" header that sat over a "Feedforward guides" row. In Böckeler's model, guides (feedforward) and sensors (feedback) are the two kinds of control, and those three are regulation categories that both kinds apply to.
**Rule violated:** No formal rule, caught pre-merge on correctness grounds.
**File:** `README.md` (Harness Design section)
**Caught by:** code-review pass (`code-review:code-review` on PR#84, fixed in `fb020aa`)

## 2026-10-07 — PR#110 — CHANGELOG claimed behavior the plugin init path did not implement
**What was wrong:** The CHANGELOG entry said the plugin `init` writes the detected build flag into the generated `AGENTS.md`, but `init.md` never told the agent to use `detect_build_target.sh` for the Build & Test section.
**Rule violated:** No formal rule, caught in review (an entry that would mislead a reader).
**File:** `CHANGELOG.md:8`
**Caught by:** this review (round 1)

## 2026-10-07 — PR#110 — pod-install test passed with a step missing
**What was wrong:** `test_pod_install_step_gated_on_podfile` only checked the gated condition appeared once per workflow file, so dropping one of release.yml's two pod install steps, or placing it after `xcodebuild`, still passed.
**Rule violated:** No formal rule, caught in review (a test that cannot fail on the defect it names).
**File:** `scripts/tests/test_build_target.py:86`
**Caught by:** this review (round 1)

## 2026-10-07 — PR#112 — Invalid .xcproj after insert
**What was wrong:** Inserting into a children array whose last item had no trailing comma wrote two adjacent objects with no comma between them.
**Rule violated:** no formal rule, caught in review
**File:** scripts/register_files.rb (append)
**Caught by:** this review

## 2026-10-07 — PR#112 — Duplicate group for multi-component path
**What was wrong:** A group whose path has several components (Sources/App) was not matched, so a second Sources > App tree was created.
**Rule violated:** no formal rule, caught in review
**File:** scripts/register_files.rb (classic loop, find_group)
**Caught by:** this review

## 2026-10-07 — PR#112 — File outside project / non-Swift registered
**What was wrong:** A file outside PROJECT_DIR created a group named `..`, and any argument (md, png) was added to Sources.
**Rule violated:** no formal rule, caught in review
**File:** scripts/register_files.rb (add_file)
**Caught by:** this review

## 2026-10-07 — PR#112 — Silent fallback to first target
**What was wrong:** An APP_NAME with no matching target registered files in the first target (classic) or wrote a membership for a nonexistent target (.xcproj).
**Rule violated:** no formal rule, caught in review
**File:** scripts/register_files.rb (target selection)
**Caught by:** this review

## 2026-10-07 — PR#112 — Mixed projects treated as synchronized
**What was wrong:** A project with synchronized and classic groups was classified synchronized, so the gate skipped and new files in the classic part were never registered.
**Rule violated:** no formal rule, caught in review
**File:** scripts/detect_file_registration.sh
**Caught by:** this review

## 2026-10-07 — PR#112 — PR check step unsafe for odd paths and empty lists
**What was wrong:** Unquoted word-split of git diff output, step failed every PR in a stop-mode project when no Swift file was new, and renamed files were not checked.
**Rule violated:** no formal rule, caught in review
**File:** scaffold/.github/workflows/pr-checks.yml, scripts/check_file_registration.py
**Caught by:** this review

## 2026-10-07 — PR#116 — Project-settings detection scanned vendored dependency folders
**What was wrong:** detect_project_settings.sh grepped the whole project dir, so Pods/ imports of SwiftUI/CoreData overrode the app's own sources and wrote wrong persistence/ui into pipeline_lanes.json without any error.
**Rule violated:** no formal rule, caught in review
**File:** scripts/detect_project_settings.sh:19
**Caught by:** this review

## 2026-11-01 — PR#125 — .agents/skills linking wrote dangling links through a redirected directory
**What was wrong:** When .agents/skills was a symlink to any directory other than .claude/skills, the -ef guard did not fire, and sync_skills.sh wrote the relative ../../.claude/skills/<name> links inside the redirected directory, where all 17 resolved to nothing. It printed no warning.
**Rule violated:** no formal rule, caught in review
**File:** scripts/sync_skills.sh:88-95
**Caught by:** this review

## 2026-11-01 — PR#125 — .agents/skills linking aborted install on an unusable .agents path
**What was wrong:** When .agents was a regular file or .agents/skills was a dangling symlink, mkdir -p failed under set -e. setup.sh and the plugin init stopped mid-install, after the skills were copied, for a layout that installed cleanly before the PR.
**Rule violated:** no formal rule, caught in review
**File:** scripts/sync_skills.sh:89
**Caught by:** this review

## 2026-11-01 — PR#131 — Round 1: empty posted URL counted as posted
**What was wrong:** `check_review_contract.py` treated only `url: null` as "not posted", so `url: ""` with an empty reason met the contract, though its docstring and the CHANGELOG say every unposted review needs a reason. Strict structured-output modes tend to emit `""` rather than null.
**Rule violated:** no formal rule, caught in review
**File:** scripts/check_review_contract.py:106
**Caught by:** this review

## 2026-11-02 — PR#136 — Round 1: Codex apply_patch parsing missed indented headers, applypatch, and cd
**What was wrong:** `PATCH_TARGET` anchored `*** Update File:` at column 0, but Codex 0.166.0 trims header lines, so an indented header edited CLAUDE.md unblocked. Bash detection checked only the string `apply_patch`, missing Codex's `applypatch` alias. Patch paths resolved against the payload cwd, ignoring a preceding `cd`, so `cd App && apply_patch ... ../CLAUDE.md` passed.
**Rule violated:** no formal rule, caught in review
**File:** scaffold/.claude/hooks/guard_protected_paths.py (PATCH_TARGET, evaluate)
**Caught by:** this review

## 2026-11-02 — PR#136 — Round 1: Codex hook entry fails open when the project is below the git root
**What was wrong:** `CODEX_COMMAND` finds the hook at `$(git rev-parse --show-toplevel)/.claude/hooks/`, but the installer puts it in `PROJECT_DIR/.claude/hooks/`. With PROJECT_DIR a subdirectory (an `ios/` layout), the file is not found and `[ -f "$f" ] || exit 0` disables the guard silently. CHANGELOG and README claimed Codex edits are blocked.
**Rule violated:** no formal rule, caught in review
**File:** scripts/install_guard_hook.py (CODEX_COMMAND); CHANGELOG.md; README.md
**Caught by:** this review

## 2026-11-02 — PR#140 — Round 1: decoy inline header skips the shell apply_patch fail-closed
**What was wrong:** `evaluate` fails closed only when no inline patch header is found anywhere in the command, so `echo '*** Update File: x.txt' >/dev/null; apply_patch < real.patch` (or a trailing decoy) is allowed on `feature/*` and `real.patch` can change CLAUDE.md.
**Rule violated:** no formal rule, caught in review
**File:** scaffold/.claude/hooks/guard_protected_paths.py:669
**Caught by:** this review

## 2026-11-02 — PR#140 — Round 1: shell apply_patch fail-closed reads the branch from cwd, not the cd target
**What was wrong:** The new block reads the branch with `git(cwd, ...)`, so from a `develop` checkout `cd <feature worktree> && apply_patch < p.patch` is allowed; the path-based check reads the target repo's branch.
**Rule violated:** no formal rule, caught in review
**File:** scaffold/.claude/hooks/guard_protected_paths.py:672
**Caught by:** this review

## 2026-11-02 — PR#140 — Round 1: runs_shell_patch misses non-literal apply_patch invocations
**What was wrong:** Only a literal command word counts, so `p=apply_patch; $p < f`, `alias ap=apply_patch; ap < f`, `find . -exec apply_patch {} \;`, `parallel apply_patch ::: f` and `bash p.sh` run a patch without a block, while the CHANGELOG says shell-run patches are covered.
**Rule violated:** no formal rule, caught in review
**File:** scaffold/.claude/hooks/guard_protected_paths.py:606
**Caught by:** this review

## 2026-11-02 — PR#140 — Round 2: inline-argument trust and dropped literal-\n parsing reopen the bypass
**What was wrong:** `opaque_shell_patch` trusts any operand containing `Begin Patch` as an inline patch, and the round-1 fix dropped `replace("\\n", "\n")`, so on `feature/*` `apply_patch '*** Begin Patch\n*** Update File: CLAUDE.md\n...'` (literal `\n`), `apply_patch '*** Begin Patch' < real.patch` and `apply_patch "$(cat real.patch)" '*** Begin Patch'` are allowed. Regression of PR#140 round-1 F1 and of FT PR#198 F1.
**Rule violated:** no formal rule, caught in review (regression of the PR#140 Round 1 decoy-header entry)
**File:** scaffold/.claude/hooks/guard_protected_paths.py:630
**Caught by:** this review

## 2026-11-02 — PR#140 — Round 3: block-all shell apply_patch still misses wrapped, nested and stdin-fed runs
**What was wrong:** At dfc69e4, `runs_shell_patch` allowed these forms on `feature/*`: path-qualified wrappers (`/usr/bin/env apply_patch`, since wrappers matched by literal word); `function f { apply_patch; }; f`, `coproc` and `builtin command`; a shell fed the script on stdin (`echo 'apply_patch < f' | bash`, `bash <<<`, `cat <<EOF | sh`); `eval $a`; `bash -c -- '...'` and a quoted `parallel 'apply_patch {}'`; and `watch`, `ssh`, `script`, `busybox`, a name split across variables (`a=apply_; ${a}patch`) and `source <(...)`. `command -v apply_patch` was falsely blocked, and the CHANGELOG overclaimed wrapper/`eval`/`bash -c` coverage and said one flipped case where there were two.
**Rule violated:** no formal rule, caught in review
**File:** scaffold/.claude/hooks/guard_protected_paths.py:352, :606; CHANGELOG.md
**Caught by:** this review

## 2026-11-02 — PR#140 — Round 4: shell reading /dev/stdin, xargs-fed scripts and unlisted wrappers bypass the shell apply_patch block
**What was wrong:** At 7eca76a, on `feature/*`, `echo 'apply_patch < f' | bash /dev/stdin` (`shell_input` returns `file` and only `source` checked `STDIN_FILES`), `echo 'apply_patch < f' | xargs -I{} sh -c {}`, and wrappers/shells missing from the lists (`flock`, `xcrun`, `doas`, `arch`, `csh -c`, `tmux new`) ran apply_patch without a block.
**Rule violated:** no formal rule, caught in review
**File:** scaffold/.claude/hooks/guard_protected_paths.py:743, :706, :80, :619
**Caught by:** this review

## 2026-11-03 — PR#140 — Round 5: allowlist misses heredoc and nested-backtick substitutions, @P and a case-changed name
**What was wrong:** At 32f1a6b, on `feature/*`, these were allowed: `$(apply_patch < f)` inside an unquoted `cat <<EOF` heredoc (the parser drops heredoc bodies), nested escaped backticks, `a='$(apply_patch < f)'; echo ${a@P}`, and `APPLY_PATCH < f` (`names_patch` was case-sensitive). The CHANGELOG claimed `$( )` and backtick bodies are all checked.
**Rule violated:** no formal rule, caught in review
**File:** scaffold/.claude/hooks/guard_protected_paths.py (runs_shell_patch, names_patch); CHANGELOG.md
**Caught by:** this review

## 2026-11-03 — PR#140 — Round 6: CHANGELOG overclaims name detection (repeat of PR#110 CHANGELOG-claims entry)
**What was wrong:** At 0041092 the CHANGELOG said a name split by backslashes is found and that detection is linear; `apply\<NL>patch` is not found, and the glob branch, `builds_command_name`, `${x:-` runs and heredoc markers are super-linear.
**Rule violated:** repeats "2026-10-07 PR#110 CHANGELOG claimed behavior the plugin init path did not implement"
**File:** CHANGELOG.md; scaffold/.claude/hooks/guard_protected_paths.py (names_patch)
**Caught by:** this review

## 2026-11-03 — PR#140 — Round 6: backslash-newline and glob-plus-brace names bypass names_patch
**What was wrong:** At 0041092, on `feature/*`, `apply\<NL>patch < f` (line continuation) and `ap?lypatc{h,} < f` (a glob inside a brace expansion) were allowed.
**Rule violated:** no formal rule, caught in review
**File:** scaffold/.claude/hooks/guard_protected_paths.py (names_patch)
**Caught by:** this review

## 2026-11-03 — PR#140 — Round 6: super-linear name detection lets a hook timeout fail open
**What was wrong:** At 0041092 `names_patch('[' * 25000)` took 5.5 s (fnmatch on unmatched `[`), and `builds_command_name`, repeated `${x:-` and repeated heredoc markers take more than 8 s at 40k-200k characters; a timed-out hook fails open.
**Rule violated:** no formal rule, caught in review
**File:** scaffold/.claude/hooks/guard_protected_paths.py (names_patch, builds_command_name)
**Caught by:** this review

## 2026-11-03 — PR#140 — Round 7: size cap ran after the quadratic write-target parse
**What was wrong:** At fd0133b the 32 KB cap was inside `runs_shell_patch`, but `evaluate()` ran `bash_write_targets` first, which is quadratic on repeated `<<E\n` (52 s at 200k characters), so a timed-out hook still failed open and the CHANGELOG's "blocked without being analyzed" was untrue.
**Rule violated:** no formal rule, caught in review
**File:** scaffold/.claude/hooks/guard_protected_paths.py (evaluate, runs_shell_patch); CHANGELOG.md
**Caught by:** this review

## 2026-11-03 — PR#140 — Round 7: bash-only glob classes bypass the glob name check
**What was wrong:** At fd0133b `apply_pa[^x]ch < f` and `apply_pa[[:alpha:]]ch < f` were allowed on `feature/*`; Python's fnmatch reads `[^` and POSIX classes differently from bash.
**Rule violated:** no formal rule, caught in review
**File:** scaffold/.claude/hooks/guard_protected_paths.py (names_patch glob branch)
**Caught by:** this review

## 2026-11-03 — PR#140 — Round 8: negated and multi-class POSIX glob brackets still bypass the glob check
**What was wrong:** At 39d9280 `apply_pa[^[:digit:]]ch`, `apply_pa[[:alpha:][:digit:]]ch` and `apply[[:punct:]_]patch` were allowed on `feature/*`: `posix_glob` rewrote `[:cls:]` to `!-~`, which fnmatch then read as a negation (28 of 102 class patterns bash matches were missed).
**Rule violated:** no formal rule, caught in review
**File:** scaffold/.claude/hooks/guard_protected_paths.py (posix_glob)
**Caught by:** this review

## 2026-11-05 — PR#147 — rg short-option check missed a cluster with an attached value
**What was wrong:** At 35c2878 the rg check matched `-z` only in a letters-only short-option cluster (`-[A-Za-z]*z[A-Za-z]*`), so on `feature/*` `rg -zA3 apply_patch f.gz` (a cluster ending in an attached value) was allowed and still turned on `--search-zip`, which runs decompressors. Fixed in 8bcac88: any short-option word containing `z` counts.
**Rule violated:** no formal rule, caught before this review
**File:** scaffold/.claude/hooks/guard_protected_paths.py (read_only_segment, rg branch)
**Caught by:** commit-time security review of 35c2878 (from the PR body)

## 2026-11-05 — PR#148 — Round 1: quoted-bracket glob check skips the expansions names_patch handles elsewhere
**What was wrong:** At 76e4481 `quoted_posix_glob` ran only on the raw token, so a quoted `]` combined with `${u}`, `$(:)`, `$'..'` or `"$u"` (`./apply_p${u}a[x']'t]ch`, `./apply$'_'pa[x$']'t]ch`) still ran `apply_patch` in bash 3.2 and zsh 5.9 while `names_patch` returned False. Repeat of the PR#140 Round 7 and Round 8 glob-bracket entries.
**Rule violated:** no formal rule, caught in review (repeat of PR#140 Rounds 7 and 8)
**File:** scaffold/.claude/hooks/guard_protected_paths.py:681
**Caught by:** this review

## 2026-11-05 — PR#148 — Round 1: CHANGELOG claims quoted-bracket and command-word globs are caught
**What was wrong:** The edited `CHANGELOG.md:26` sentence said a bracket with a quoted or escaped member is caught and removed the #145 known limit, though F1 bypasses remain. It also kept "any glob as the command word", though `./a[p]*h` is missed: `w in commands` compares the posix_glob form with the raw word.
**Rule violated:** no formal rule, caught in review
**File:** CHANGELOG.md:26
**Caught by:** this review

## 2026-11-05 — PR#148 — Round 2: brace alternatives beside a quoted-bracket glob are not tried
**What was wrong:** At 31f2232 `q_globs` only dropped or opened a brace group, so `./apply_p{a,b}[x']'t]ch` and `./apply_pa[x']'t]c{h,i}` ran `apply_patch` in bash 3.2 and zsh 5.9 while `names_patch` returned False. Fixed in d425768 by reading each group as `*` (Akshay chose fail closed).
**Rule violated:** no formal rule, caught in review
**File:** scaffold/.claude/hooks/guard_protected_paths.py (names_patch q_globs)
**Caught by:** this review

## 2026-11-05 — PR#148 — quoted space inside a glob bracket split the marked word
**What was wrong:** At 31f2232 a quoted space in a bracket (`./apply_pa[x' ]'t]ch`) split the quote-marked text into two words, so the glob was missed. Fixed in aadf990 by holding quoted whitespace, quotes and operators as placeholders.
**Rule violated:** no formal rule, caught before this review
**File:** scaffold/.claude/hooks/guard_protected_paths.py (mark_quoted)
**Caught by:** commit-time security review of 31f2232 (from the PR body)

## 2026-11-10 — PR#155 — Coverage gate silently false-passes (stdin collision)
**What was wrong:** Gate 6 piped the new-file list into `python3 - <<'PY'`; the heredoc replaced stdin, so the list was empty and the gate printed `[✓] Coverage` at 10% coverage. Repeats PR#49 (unreachable coverage check) and PR#110 (test that cannot fail on its defect): the only Gate 6 test covered the no-bundle path.
**Rule violated:** no formal rule, caught in review
**File:** scripts/check_gates.sh:197
**Caught by:** this review

## 2026-11-10 — PR#155 — /review's own log trips the gate script's clean-tree pre-step
**What was wrong:** `/review` step 2 runs `check_gates.sh --only 3,4,5,10`, whose pre-step fails (exit 3) on the uncommitted `rejections.md` that step 1 explicitly exempts; round 2 would post a false CHANGES REQUESTED.
**Rule violated:** no formal rule, caught in review
**File:** scripts/check_gates.sh:49
**Caught by:** this review

## 2026-11-10 — PR#155 — Invalid sensitive_paths regex reported as a clean skip
**What was wrong:** `grep -E` with a malformed pattern errors to stderr and Gate 7 prints `skipped (no sensitive files)`; the grep exit status is not checked.
**Rule violated:** no formal rule, caught in review
**File:** scripts/check_gates.sh:229
**Caught by:** this review

## 2026-11-10 — PR#155 — Flag with no value hangs the gate script
**What was wrong:** `--base` (or any flag) as the last argument makes `shift 2` fail without shifting, so the argument loop never ends instead of exiting 2.
**Rule violated:** no formal rule, caught in review
**File:** scripts/check_gates.sh:23
**Caught by:** this review

## 2026-11-10 — PR#155 — Coverage exclusions differ from check_coverage.py despite the SKILL saying "same"
**What was wrong:** Gate 6 omits `HARDWARE_DEPENDENT_EXCEPTIONS` (CI passes what the gate fails) and `"Tests" in rel` skips production files under paths like `Contests/`; coverage threshold 80 is hardcoded instead of a named constant.
**Rule violated:** no formal rule, caught in review (code quality: no magic numbers for thresholds)
**File:** scripts/check_gates.sh:204-216
**Caught by:** this review

## 2026-11-11 — PR#169 — Bare CR in diff content starts a fake block in gate integrity
**What was wrong:** `run()` decodes git output with `text=True` (universal newlines), so a `\r` inside an added line becomes `\n` and `^diff --git` matches mid-line; the rest of a `.swift` block is filed under a doc path and its suppression marker is skipped (exit 0). The new comment claimed only `\n` could start a block.
**Rule violated:** no formal rule, caught in review
**File:** scripts/check_gate_integrity.py:273
**Caught by:** this review

## 2026-11-11 — PR#169 — File name with a CR makes gate integrity exit 2
**What was wrong:** universal newlines turn the CR in `git diff -z --name-status` output into `\n`, so the expected header never matches git's quoted `\r` header and a legitimate change stops the gate.
**Rule violated:** no formal rule, caught in review
**File:** scripts/check_gate_integrity.py:238
**Caught by:** this review

## 2026-11-11 — PR#169 — Pathspec-magic file name skips the forced-text re-fetch
**What was wrong:** `text_diff_for` passes changed paths to `git diff --` as pathspecs, so a masked file named `:(top)Q.swift` is never re-fetched as text and its suppression marker is missed (exit 0).
**Rule violated:** no formal rule, caught in review
**File:** scripts/check_gate_integrity.py:415
**Caught by:** this review

## 2026-11-12 — PR#175 — Gate script still reads quoted newline paths (same root cause as #160)
**What was wrong:** `check_gates.sh`'s `changed_swift` (`while read f; [ -f "$f" ]`) and Gate 10's `swift()` read `git diff --name-only` without `-z`; git quotes a name with a newline, tab, `"` or `\` even with `core.quotepath=false` (`"App/a\nb.swift"`), so that file is silently skipped by Gates 3, 6, 7, 8 and 10. The PR fixed only the lane classifier.
**Rule violated:** no formal rule, caught in review
**File:** scripts/check_gates.sh:82
**Caught by:** this review

## 2026-11-12 — PR#183 — Gate 10 diff-scope overclaim reintroduced in parallel-review Check 1 (repeats PR#56 Round 1)
**What was wrong:** `parallel-review/SKILL.md` Check 1 now says `scripts/check_gates.sh --only 10` "reads the committed `git diff develop...HEAD`, the same scope this command needs", and the PR deleted the old text noting that the UI-selector check scans the whole tree. `check_gates.sh` (UI-selector block, ~lines 378-389) reads `git ls-files` for all of the UI-test and app dirs and now emits `[FAIL] UI selector: ...`, so a selector mismatch outside the branch diff fails Gate 10 and, via the Check 1 rule, forces `FIX BEFORE /gates`. Same overclaim as the PR#56 Round 1 entry.
**Rule violated:** repeats the 2026-09-08 PR#56 Round 1 entry (Gate 10 diff-scope overclaim); no formal rule beyond that entry
**File:** .claude/skills/parallel-review/SKILL.md:30
**Caught by:** this review

## 2026-11-12 — PR#183 — Memory path rule in pipeline-review is incomplete and contradicts trim-context
**What was wrong:** `pipeline-review/SKILL.md:40` tells the reviewer to build the memory path as `<launch-dir-with-slashes-as-dashes>`. `trim-context/SKILL.md` (memory path rule, `sed 's/[^A-Za-z0-9]/-/g'`) says every non-alphanumeric character becomes `-`, so a launch directory with a `.`, space or parenthesis gives a wrong path and the reviewer would wrongly report "no memory".
**Rule violated:** no formal rule, caught in review
**File:** .claude/skills/pipeline-review/SKILL.md:40
**Caught by:** this review

## 2026-11-12 — PR#183 — False "all clear" leak-sweep claim in the committed pipeline-review report
**What was wrong:** `docs/pipeline-review/2026-11-12.md` "All clear" bullet says apart from the High, no FinanceTracker names or `akshay*` strings appear in `.claude/skills/`, `scaffold/` or `scripts/`. `scripts/slim_simulator.sh:7` names FinanceTracker and `.claude/skills/trim-context/SKILL.md:59` names `akshaypimprikar.dev`, both shipped files.
**Rule violated:** no formal rule, caught in review
**File:** docs/pipeline-review/2026-11-12.md:44
**Caught by:** this review

## 2026-11-14 — PR#195 — Release-mode tests could not fail on the base switch
**What was wrong:** `test_release_branch_checks_only_the_release_commit` and `..._still_checks_the_release_commit_itself` passed with release mode keeping `BASE=main` for every gate: the develop commit tripped no gate, and the N/A rows come from `RELEASE=1`, not the base switch.
**Rule violated:** no formal rule, caught in review
**File:** scripts/tests/test_check_gates.py:302
**Caught by:** this review

## 2026-11-14 — PR#195 — Release mode preferred a stale local develop
**What was wrong:** release mode set the gates' base and the lane's `--develop-ref` to local `develop` whenever it existed; when it lags `origin/develop`, the lane reads `app` instead of `release` and Gate 3 flags develop commits already gated.
**Rule violated:** no formal rule, caught in review
**File:** scripts/check_gates.sh:48
**Caught by:** this review
