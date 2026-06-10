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

### 4. Apply updates
Edit only the lines that differ. Do not copy project-specific paths or app names into the template — use `<AppName>` placeholders.

### 5. Open a PR — never push directly to main
```bash
git -C <pragma path> checkout develop && git -C <pragma path> pull
git -C <pragma path> checkout -b sync/<YYYY-MM-DD>
git -C <pragma path> add .claude/commands/
git -C <pragma path> commit -m "chore: sync commands from <AppName> — <brief summary>"
git -C <pragma path> push -u origin sync/<YYYY-MM-DD>
gh pr create --repo akshaypimprikar/pragma \
  --title "chore: sync commands from <AppName> — <brief summary>" \
  --body "## Changes\n<bullet list of what changed and why>\n\n🤖 Generated with [Claude Code](https://claude.com/claude-code)" \
  --base develop
```

If nothing changed, do not create a branch or PR — report "no changes needed" instead.

### 6. Report
List every file changed and what was updated, plus the PR URL. If nothing needed changing, say so explicitly.

## Done when
PR is open on pragma (or "no changes needed" confirmed), report delivered.
