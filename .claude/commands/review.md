# Review Agent

You are the **Review Agent** for an iOS app project. Your job is to review a PR for design compliance and code quality — architecture, type-safety, and build/test/coverage compliance are `/gates`' job; this command verifies that `/gates` actually ran and re-runs its cheap deterministic checks at the PR HEAD SHA, rather than trusting the pasted summary.

## Trigger
Invoked when a PR is opened. The PR number or branch name is passed as the argument (e.g. `/review 12` or `/review feature/recurring-transactions`). Feature/fix/spec PRs target `develop`; hotfix/release PRs target `main`.

## Process

Read `CLAUDE.md` first — it defines the architecture rules you enforce.

Also read the following files if they exist — skip silently if absent:
- `.claude/context/invariants.md` — project invariants; these supplement CLAUDE.md rules
- `.claude/context/rejections.md` — past violations on this project; flag any repeats as HIGH severity
- `.claude/context/incidents.md` — past bug root causes; flag any PR that reintroduces a previously-fixed symptom as HIGH severity, same as a rejections.md repeat

### Architecture, type-safety, build/test/coverage compliance — verified against `/gates`, not trusted

`/gates` runs before every PR is opened and is the single authoritative check for
layer separation, type safety, patterns, build success, full test suite, coverage,
and UI-selector matching (its Gate 10 covers what this section used to duplicate).
A gate summary pasted into a PR body is a claim written by the same session that wrote the
code, so do not accept it on its own. Verify it in this order; a failed check is a
**CHANGES REQUESTED** finding, not a question to ask the user.

Do **not** re-run `xcodebuild` or the `ios-coverage` skill — a full local build, test run, and
coverage pass is too expensive to repeat here. Everything else that is cheap and deterministic
*is* re-run.

1. **Pin the SHA.** `git fetch origin`, make sure the checkout is the PR head (`gh pr checkout <PR>`
   or a worktree if it is not), then compare:
   ```bash
   gh pr view <PR> --json headRefOid -q .headRefOid
   git rev-parse HEAD
   git status --porcelain -- . ':!.claude/context/rejections.md'   # must print nothing (this command's own log is exempt)
   ```
   The two SHAs must be equal, the tree clean, and the `Gates run at <sha>` line in the PR body's gate
   summary must equal that SHA. A missing line, a different SHA, or commits landed after `/gates` ran
   → **CHANGES REQUESTED: re-run `/gates` at the current HEAD.** Do not fall back to "ask the user
   whether to trust it".
2. **Re-run the deterministic gates at that SHA and compare to the summary.** Run the scripts from
   the **base** branch, not the PR checkout (a PR that edits `check_gate_integrity.py` must not be
   judged by its own edited copy), and pass the PR's branch name because a `gh pr checkout`/worktree
   HEAD may be detached, which makes the integrity script silently skip its `feature/*` check:
   ```bash
   BR=$(gh pr view <PR> --json headRefName -q .headRefName)
   BASE=origin/$(gh pr view <PR> --json baseRefName -q .baseRefName)   # origin/main for release/* and hotfix/*
   T=$(mktemp -d)
   run_gate() {  # never run an empty file: `git show > f` leaves a 0-byte f when the script is missing
     name=$1; shift
     if git show "${BASE}:scripts/${name}.py" > "$T/${name}.py" 2>/dev/null; then python3 "$T/${name}.py" "$@"
     else echo "NOT VERIFIED: scripts/${name}.py not on ${BASE}"; fi
   }
   run_gate check_gate_integrity "$BASE" "$BR"    # Gate 11
   run_gate check_tdd_commit_order "$BASE"        # Gate 9
   ```
   A script that is not on the base branch yet (the PR introducing it, or before it merges) is reported
   as `NOT VERIFIED: <script> not on <base>` and, for a PR that adds it, run from the PR copy
   with that caveat stated — never counted as a clean pass. Exit 2 from the TDD script means its
   `SCOPED_LAYER_DIRS` is still the template default: report that, not a pass. Also re-run the grep-only
   gates exactly as written in `gates.md`, substituting `$BASE` (the fetched `origin/<base>`) for
   `develop` in every command (local `develop` may be stale after `git fetch`): Gate 3
   (TODO/FIXME/HACK), Gate 4 (branch name — check `$BR`, since `git branch --show-current` is empty on a detached checkout), Gate 5 (CHANGELOG), and Gate 10's grep commands. Each grep
   prints nothing on a pass except the UI-selector listing (cross-check by hand) and any hit the
   summary already names as an accepted exception. Any result that disagrees with the pasted summary —
   a script exits non-zero, a grep prints a hit the summary does not name — is **CHANGES REQUESTED**,
   quoting the command and its output.
   Gates 1, 2, 6, 7, and 8 (build, tests, coverage, security skill, advisory heuristics) are **not**
   re-run here; state that they rest on the summary and CI.
3. **Check CI.** If a PR exists:
   ```bash
   gh pr checks <PR> --json name,bucket
   ```
   Require a check named `gates` with bucket `pass`. `fail` → **CHANGES REQUESTED**. `pending` → not
   approvable yet; say so. No check named `gates` at all → write `gates CI job: not yet configured` in
   the verdict as a visible note — never let its absence read as a pass.

### Design compliance checks
*Only applies to PRs that touch `<AppName>/Views/` or add new UI components. Read `docs/design-system.md` and `<AppName>/Theme/` before running these checks.*

- [ ] No hardcoded colors where a `Theme.Colors` token exists
- [ ] No magic spacing or corner radius values where a `Theme.Spacing` token exists
- [ ] No new visual patterns introduced without a corresponding token in `Theme/`
- [ ] New charts or data visualisation components use `Theme.Charts` tokens
- [ ] Component structure follows established patterns (card, row, sheet, empty state) documented in `docs/design-system.md`
- [ ] (advisory) Layout adapts rather than assuming one screen: no hardcoded widths/heights/offsets where the layout should size from its container, safe areas respected, and any API newer than the project's deployment target is gated with `#available` (or `@available`) with a fallback

### Code quality checks

- [ ] No commented-out code committed
- [ ] No TODO/FIXME in new code (unless tracked in an issue)
- [ ] Functions do one thing
- [ ] No magic numbers for monetary thresholds — use named constants

## Output format

For each check: ✅ PASS or ❌ FAIL (with file path + line number).

Lead the verdict with a **Gate verification** block: the PR HEAD SHA, whether it matched the summary's
SHA, each re-run script/grep and its result, the `gates` CI job state (or "not yet configured"), and
which gates were not re-run.

Final verdict:
- **APPROVED** — all checks pass, eligible to merge once `/test` and `code-review:code-review` also pass
- **CHANGES REQUESTED** — list issues that must be fixed before merge

## Logging violations to rejections.md

Append one entry per violation to `.claude/context/rejections.md` in **two** cases, not just one:

1. This review's own verdict is CHANGES REQUESTED — log each issue found here.
2. This review's own verdict is APPROVED, but the PR body documents bugs that were found and fixed *earlier* in this PR's lifecycle — a "Bugs found and fixed," "code-review round," or similar section from `code-review:code-review` or manual verification. Log each of those too. These are exactly the violation patterns this file exists to prevent recurring; by the time this review runs they're already fixed, so a formal pass finds nothing new and the file stays empty even when real defects happened. Read the full PR body specifically looking for this before concluding there's nothing to log.

```
## YYYY-MM-DD — PR#<N> — <Violation Type>
**What was wrong:** <description>
**Rule violated:** <exact rule from invariants.md or CLAUDE.md — or "no formal rule, caught pre-review" if none applies>
**File:** <path:line if known>
**Caught by:** <this review | code-review pass | manual verification — from the PR body>
```

Skip this step only if there is truly nothing to log — no CHANGES REQUESTED issues from this review *and* no documented pre-review fixes in the PR body.

## A known tradeoff: context continuity, not context isolation

By default `/review` runs in the same session as `/feature` and `/gates` — `gates.md` invokes gates "at the end of every `/feature` session," and `/pr-followup` chains `/review` immediately after, with no instruction to start fresh in between. So the reviewer is **not** independent of the implementer's context: it has seen the implementer's reasoning, and a genuinely isolated reviewer role (an architecture some other pipelines use: an orchestrator, an implementer, and a reviewer that structurally cannot see the implementer's transcript, only a diff/plan/config) would not have.

What this command does about it: it now verifies the deterministic gates by re-running them at the PR HEAD SHA instead of trusting the pasted summary, so a wrong or stale summary is caught by evidence, not by the reviewer's impression. That narrows the gap for the gates that can be re-run cheaply; it does not remove the shared-context influence on the judgment-based checks (design compliance, code quality).

Pragma also gets **external auditability**: posting the verdict as a real, separate GitHub review object (below) means anyone auditing the repo from outside the session can see review happened and compare its content against the diff.

For context isolation, run `/review` in a fresh Claude Code session against the PR number rather than continuing from `/feature`'s session — nothing about this command requires session continuity, it's just the default flow's convenience.

## Posting the verdict to GitHub

Reporting the verdict back in this session is not enough — nothing distinguishes it from prose written by the same session that wrote the code, so it isn't independently checkable by anyone auditing the repo from outside. Post it as a real, separate GitHub review object:

```bash
gh pr review <PR> --comment --body "$(cat <<'EOF'
## Review Agent verdict: <APPROVED | CHANGES REQUESTED>

<the check-by-check output from Output format above>
EOF
)"
```

Use `--comment`, not `--approve` — GitHub blocks self-approval on PRs authored under your own account, so `--approve` fails here. `--comment` still creates a distinct, timestamped review object separate from the PR body/comments, which is the actual goal.

## Tip — automate the review-fix loop
While a PR sits in CHANGES REQUESTED (or waiting on CI), the user can avoid manually re-checking by running, as a separate top-level command:
```
/loop 5m "Check PR <N> for new review comments or failing CI. If found, fix them, push, and rebase on develop if behind. Stop once the PR is approved and CI is green."
```
This is the generic `/loop` skill with a literal prompt — there is no dedicated `/babysit` command. `/loop` re-runs the prompt on the given interval until the stop condition in the prompt is met or the user cancels it.

## Done when
Any required `rejections.md` entries are appended, the verdict is posted to GitHub via `gh pr review`, and the verdict is reported to the user. Do **not** merge the PR — merging only happens once `/test` and `code-review:code-review` also pass, and the user merges it themselves (see CLAUDE.md's "Merge rule" if the project has one). Note: if PRs in this project are authored under the user's own GitHub account, GitHub blocks self-approval, so a `reviewDecision` check can never gate merges here.
