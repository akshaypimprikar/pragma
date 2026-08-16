---
model: claude-haiku-4-5-20251001
---

# Pragma Review Agent

Pragma is a template repo — it has no `CLAUDE.md`, no `Views/`, and no architecture
layers, so FinanceTracker's Swift-shaped `/review` agent has nothing to check here.
This command checks what actually goes wrong in *this* repo instead: a source
project's concrete value leaking into template content, a missing `<placeholder>`,
or a gate-numbering mismatch. It exists so every pragma PR gets an auditable
GitHub review object — not just the ones opened by `/sync-workflow`, which is the
only path that had a self-review step until now.

## Trigger
Invoked when a PR is opened against pragma: `/pragma-review 12` or `/pragma-review sync/2026-08-16`.

## Process

Read the PR diff:
```bash
gh pr diff <PR> --repo akshaypimprikar/pragma
```

Each check below only applies if its trigger condition is met — report the others as N/A rather than running them against an unrelated diff.

### 1. Template-literal leak (if `.claude/commands/` files changed)
```bash
gh pr diff <PR> --repo akshaypimprikar/pragma | grep -E '^\+' | grep -iE 'FinanceTracker|/Users/akshaypimprikar|iPhone 17|AccountViewModel|SwiftData[A-Z]\w*Repository'
```
Advisory — eyeball every hit. A worked example in prose is fine (pragma's own files already do this, e.g. `/gates feature/recurring-transactions`). A hardcoded value standing in for what should be a `<placeholder>` is the actual problem.

### 2. `<placeholder>` convention held (if `.claude/commands/` files changed)
For every newly added or changed section that generalizes a source project's concrete rule (an architecture check, a gate, a build command), confirm it uses `<AppName>`-style placeholder tokens for anything project-specific — a type name, a file path, a field name — matching the style already used throughout `gates.md`'s Gate 9/10 examples. Zero placeholders in a section that's supposed to be generic is the leak.

### 3. Gate numbering/count consistency (if `.claude/commands/gates.md` changed)
`Gate 0` (the Swift-change pre-check) is intentionally excluded from both the sequence and the count — that's a convention, not an oversight.
```bash
awk 'BEGIN{expected=1} {if($1!=expected) print "non-sequential: expected "expected" got "$1; expected=$1+1}' \
  <(grep -oE '^### Gate [1-9][0-9]*' .claude/commands/gates.md | grep -oE '[0-9]+')
MAX=$(grep -oE '^### Gate [1-9][0-9]*' .claude/commands/gates.md | grep -oE '[0-9]+' | sort -n | tail -1)
grep -rniE "all [0-9]+ gates" .claude/commands/*.md | grep -viE "all $MAX gates"
```
Pass: the sequential check prints nothing, and the count-reference grep returns no lines disagreeing with `$MAX`.

### 4. Doc/content accuracy spot-check (if `README.md`, `CONTRIBUTING.md`, or any `.claude/commands/*.md` changed)
Spot-check any specific counts or lists the changed file states (command counts, gate counts, file lists) against the actual files in the repo. Flag anything the PR's own diff makes newly inaccurate, or a pre-existing inaccuracy the PR touches without fixing.

## Output format

For each of the 4 checks: ✅ PASS, ❌ FAIL (with file:line), or ⚪ N/A (trigger condition not met).

Final verdict:
- **APPROVED** — no FAILs
- **CHANGES REQUESTED** — list what must be fixed

## Posting the verdict to GitHub

```bash
gh pr review <PR> --repo akshaypimprikar/pragma --comment --body "$(cat <<'EOF'
## Pragma Review Agent verdict: <APPROVED | CHANGES REQUESTED>

<the check-by-check output>
EOF
)"
```
Use `--comment`, not `--approve` — GitHub blocks self-approval on PRs authored under your own account.

## Done when
Verdict posted to GitHub via `gh pr review`, verdict reported to the user. Do not merge — the user merges pragma PRs themselves.
