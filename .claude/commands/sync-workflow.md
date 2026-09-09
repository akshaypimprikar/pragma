# Sync Workflow Agent

Sync the pragma template repo so it stays consistent with your project's current conventions.

## Trigger
Run manually after any change to CLAUDE.md, branch strategy, build commands, or agent conventions: `/sync-workflow`

## Process

### 1. Read the source of truth
- Read `CLAUDE.md` from your project — branch strategy, build commands, simulator name, architecture rules
- Read all files in `.claude/commands/` — the project-specific versions

### 2. Read the template
- Read all files in the pragma template repo's `.claude/commands/`

### 3. Compare and update
Check for drift in these areas (keep `<AppName>` placeholders — pragma is a template):

| What to check | Source of truth |
|---|---|
| Branch strategy (`main` vs `develop`) | Your project's CLAUDE.md |
| Simulator name | Your project's CLAUDE.md |
| Build command structure | Your project's CLAUDE.md |
| Test framework (`import Testing` vs XCTest) | Your project's CLAUDE.md |
| Pre-flight check commands in `/release` | Your project's `/release` command |
| Architecture rules checklist in `/review` | Your project's `/review` command |
| New gates in `/gates` — **only if generalizable** | Your project's `/gates` command |

### 4. Apply updates
Edit only the lines that differ. Do not copy project-specific paths or app names into the template — use `<AppName>` placeholders.

For new gates in your project's `/gates`, judge each one individually — do not copy-paste:
- **Generalizable** (checks a pattern any iOS MVVM+Repository project would want — e.g. a layer-rule compliance gate): port it as a *templated* gate with `<placeholder>` values, matching the style of Gates 1/2/7. Do not hardcode your project's literal grep patterns (field names, concrete paths, etc.) into the template.
- **App-specific** (checks something only your project's domain has — e.g. a concurrency-shape gate tied to one specific actor/service): leave it out of pragma entirely. It has no equivalent in a template repo.

### 5. Self-review the diff before committing
Pragma has no `CLAUDE.md` and no `/review` of its own — this is the
only check that runs before a sync PR opens. Keep it lightweight: it exists to catch the
specific ways a *template* repo can drift, not to re-litigate content already reviewed once
in your project. Run against the staged diff, before `git commit` — stage first, since the
checks below read `git diff --cached`:
```bash
git -C <pragma path> add .claude/commands/
```

**a. No project-specific literals leaked into template content (advisory — eyeball each hit):**
```bash
git -C <pragma path> diff --cached | grep -E '^\+' | grep -iE '<AppName>|/Users/|iPhone [0-9]+|<ConcreteViewModel>|<ConcreteRepository>'
```
A worked example in prose is fine (pragma's own files already do this, e.g. `/gates feature/recurring-transactions`). A hardcoded value standing in for what should be a `<placeholder>` is not — generalize it before committing.

**b. `<placeholder>` convention held where your project's source used a concrete name:**
For every newly templated section (an architecture rule, a gate), confirm it uses
`<placeholder>` tokens for anything project-specific — a type name, a file path, a field
name — matching the style already used throughout pragma's `gates.md` Gate 9/10 examples.
Zero placeholders in a section that generalizes a project-specific check is the leak.

**c. Gate numbering and counts stay internally consistent (deterministic):**
`Gate 0` (the Swift-change pre-check) is intentionally excluded from both the sequence and
the count — the pattern below starts from Gate 1 on purpose, not an oversight.
```bash
awk 'BEGIN{expected=1} {if($1!=expected) print "non-sequential: expected "expected" got "$1; expected=$1+1}' \
  <(grep -oE '^### Gate [1-9][0-9]*' <pragma path>/.claude/commands/gates.md | grep -oE '[0-9]+')
MAX=$(grep -oE '^### Gate [1-9][0-9]*' <pragma path>/.claude/commands/gates.md | grep -oE '[0-9]+' | sort -n | tail -1)
grep -rniE "all [0-9]+ gates" <pragma path>/.claude/commands/*.md | grep -viE "all $MAX gates"
```
Pass: the sequential check prints nothing, and the count-reference grep returns no lines
disagreeing with `$MAX`. Fail: fix the stale number before committing — a stale gate-count
reference is a real recurring bug class, not a hypothetical one.

### 6. Open a PR — never push directly to main
```bash
git -C <pragma path> checkout develop && git -C <pragma path> pull
git -C <pragma path> checkout -b sync/<YYYY-MM-DD>
git -C <pragma path> add .claude/commands/
git -C <pragma path> commit -m "chore: sync commands from <AppName> — <brief summary>"
git -C <pragma path> push -u origin sync/<YYYY-MM-DD>
gh pr create --repo akshaypimprikar/pragma \
  --title "chore: sync commands from <AppName> — <brief summary>" \
  --body "## Changes\n<bullet list of what changed and why>\n\n🤖 Generated with [Claude Code](https://claude.com/claude-code)" \
  --base develop \
  --head sync/<YYYY-MM-DD>
```

If nothing changed, do not create a branch or PR — report "no changes needed" instead.

**Verify before trusting the PR:** `--repo` alone does not fix the head branch — the shell's current working directory can silently resolve to the wrong repo's branch. Always confirm with:
```bash
gh pr view <N> --repo akshaypimprikar/pragma --json headRefName,baseRefName,files
```

### 7. Report
List every file changed and what was updated, plus the PR URL. If nothing needed changing, say so explicitly.

## Done when
PR is open on pragma (or "no changes needed" confirmed), report delivered.
