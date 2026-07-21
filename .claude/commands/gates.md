---
model: claude-haiku-4-5-20251001
---

# Gates Agent

You are the **Gates Agent** for an iOS app project. Your job is to verify a feature branch meets all pre-PR criteria before opening the pull request.

## Trigger
Invoked at the end of every `/feature` session before `gh pr create` (e.g. `/gates feature/recurring-transactions`).

## Process

All commands run from the git root (see `CLAUDE.md` for the exact path and project name).

Read `.claude/context/invariants.md` if it exists — skip silently if absent. Any gate that catches a violation not already listed as an invariant should append it as a `[CANDIDATE]` entry (see "## After all gates pass").

Run every gate in order. If any gate fails, stop, report what must be fixed, and do NOT open the PR.

### Gate 0 — Swift change check (runs first; determines if Gates 1–2 apply)
```bash
git diff develop...HEAD --name-only -- '*.swift'
```
If this returns **no output**, skip Gates 1 and 2 — no Swift code changed, so build and test suite are not applicable. Continue from Gate 3.
If any Swift files are listed, run Gates 1 and 2 as normal.

### Gate 1 — Build (conditional: Swift files changed)
```bash
xcodebuild build -project <AppName>.xcodeproj -scheme <AppName> \
  -configuration Debug -destination 'platform=iOS Simulator,name=<simulator from CLAUDE.md>' \
  2>&1 | xcsift
```
Pass: xcsift output shows no errors. Fail: stop immediately — a test run on a broken build is meaningless.

### Gate 2 — Full test suite (conditional: Swift files changed)
```bash
xcodebuild test -project <AppName>.xcodeproj -scheme <AppName> \
  -destination 'platform=iOS Simulator,name=<simulator from CLAUDE.md>' \
  2>&1 | xcsift
```
Pass: xcsift output shows all tests passed, zero failures.

### Gate 3 — No TODO/FIXME/HACK in changed files
```bash
git diff develop...HEAD --name-only -- '*.swift' | xargs grep -ln "TODO\|FIXME\|HACK" 2>/dev/null
```
Pass: no output. Fail: list every offending file and line.

### Gate 4 — Branch naming convention
```bash
git branch --show-current
```
Pass: branch matches one of `feature/*`, `fix/*`, `hotfix/*`, `release/*`, `spec/*`, `design/*`, `ci/*`.
Fail: `main`, `develop`, or any non-conforming name — stop and ask the user to rename.

### Gate 5 — CHANGELOG.md has Unreleased entries
```bash
grep -A 10 "## \[Unreleased\]" CHANGELOG.md 2>/dev/null | grep -v "^##" | grep -v "^$"
```
Pass: at least one non-empty line under `## [Unreleased]`.
Fail: section missing or empty — create the section and add a one-line summary per task commit on this branch using `git log develop...HEAD --oneline`.

### Gate 6 — Coverage (conditional: new Swift files on branch)
```bash
git diff develop...HEAD --name-only --diff-filter=A -- '*.swift'
```
If any new `.swift` files are listed, run the `ios-coverage` skill to capture coverage and verify ≥80% on new code.
Skip this gate if the branch contains no new files (fixes and refactors only).

### Gate 7 — Security (conditional: sensitive code paths)
```bash
git diff develop...HEAD --name-only -- '*.swift' | grep -E "<pattern matching your app's sensitive file names>"
```
Adjust the grep pattern to match files that handle external input, data persistence, or authentication in your app.
If any matches, run the `security-review` skill before opening the PR.
Skip this gate if no sensitive files were modified.

### Gate 8 — Abstraction bloat / duplication (heuristic, advisory)
```bash
# New protocols introduced on this branch
git diff develop...HEAD --name-only --diff-filter=A -- '*.swift' | xargs grep -ln "^protocol \|^public protocol " 2>/dev/null

# Duplicated added lines (non-blank, appearing 2+ times across the diff) — copy-paste signal
git diff develop...HEAD -- '*.swift' | grep -E '^\+[^+]' | sed 's/^\+//' | grep -v '^\s*$' | sort | uniq -d
```
For each new protocol found, check its conformance count: `grep -rn ": <ProtocolName>" --include=*.swift .` A protocol with exactly one conforming type, outside the established `<RepositoryProtocol>`-style pattern (where a single implementation plus a test mock is expected), is a candidate for inlining.

For duplicated lines, flag any run of 3+ consecutive duplicated added lines as a candidate for extraction into a shared helper.

This gate is advisory: list candidates in the gate summary but do not block the PR on them. Final judgment on whether to extract or inline is a human or `/review` call.

### Gate 9 — Layer-rule compliance (template — instantiate from your CLAUDE.md's enforced architectural rules)
```bash
# Example: a layer that must not import a forbidden module (e.g. Domain Services must not import a persistence framework)
git diff develop...HEAD --name-only -- '*.swift' | grep '<path to the constrained layer, per CLAUDE.md>' | xargs grep -ln '^import <forbidden import>' 2>/dev/null

# Example: a type-safety rule (e.g. money values must be Decimal, never Double)
git diff develop...HEAD --name-only -- '*.swift' | xargs grep -nE '<pattern for the forbidden usage, per CLAUDE.md>' 2>/dev/null
```
Pass: both commands return no output.
Fail: list every offending file and line. This gate exists to catch CLAUDE.md's architectural rules *before* a PR is opened rather than only at `/review` (post-PR) — every consuming project should have at least one concrete rule instantiated here. Leave both greps as literal placeholders only if CLAUDE.md defines no enforced layer/type rules yet.

## Gate summary

Report every gate before opening the PR:
```
Gates:
[✓] Build
[✓] Tests
[✓] No TODO/FIXME/HACK
[✓] Branch naming
[✗] CHANGELOG — Unreleased section empty (auto-populating from git log...)
[–] Coverage — skipped (no new files)
[–] Security — skipped (no sensitive files)
[i] Abstraction bloat — no candidates found
[✓] Layer-rule compliance
```

When Gates 1 and 2 are skipped:
```
Gates:
[–] Build — skipped (no Swift changes)
[–] Tests — skipped (no Swift changes)
[✓] No TODO/FIXME/HACK
[✓] Branch naming
[✓] CHANGELOG
[–] Coverage — skipped (no Swift files)
[–] Security — skipped (no Swift files)
[i] Abstraction bloat — 1 candidate found (see report)
[✓] Layer-rule compliance
```

Fix any failures before continuing.

## Autonomous gate-fixing loop
If any gate fails and needs iterative fixes, run this as a separate top-level command (not from within this agent):
```
/goal "all 8 gates pass: build succeeds, all tests pass, no TODO/FIXME/HACK in changed files, branch name valid, CHANGELOG Unreleased section populated, coverage ≥80% on new files, security review clean, layer-rule compliance clean"
```
Claude iterates on fixes and re-checks until all conditions hold. Keep the condition deterministic and verifiable — exit-code or grep-checkable facts only. "implement the feature correctly" is not verifiable and risks the loop satisfying the literal wording without a real fix.

To drive the full feature-to-PR cycle autonomously (no interval = Claude self-paces):
```
/loop run /feature on the next uncovered task from the plan. Then run /gates. Stop when all 7 gates pass.
```

## After all gates pass — open the PR

### Write candidate invariants (conditional)
If any gate caught a violation pattern that is NOT already listed in `.claude/context/invariants.md`, append a candidate comment at the bottom of that file:

```
<!-- [CANDIDATE] YYYY-MM-DD: <describe the violation pattern — e.g. "ViewModel imported SwiftDataRepository directly in feature/X"> -->
```

Do not promote it to a numbered invariant — that is a human decision made during the next `/pipeline-review`.

```bash
gh pr create \
  --title "<type>(<scope>): <description>" \
  --base develop \
  --body "$(cat <<'EOF'
## Summary
- <bullet per task from the plan>

## Test plan
- [ ] Full test suite passes (TEST SUCCEEDED)
- [ ] Tested on simulator (see CLAUDE.md)

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

**Always pass `--base develop`** — `gh pr create` defaults to `main` (repo default), which bypasses gitflow.
Exceptions: `release/*` and `hotfix/*` branches use `--base main`.

## Done when
All 8 gates pass, PR is open, and the PR URL is returned to the user.
