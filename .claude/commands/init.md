# Pragma Init

You are running Pragma's setup inside Claude Code — the plugin equivalent of `scripts/setup.sh`, with one difference: instead of leaving `CLAUDE.md` and `invariants.md` as templates for the user to fill in later, you interview them for real content now.

## Trigger

Invoked after the user installs the `pragma` plugin (`/plugin install pragma@pragma`) and runs `/pragma:init` inside their iOS project's repo root. Optionally takes the app name as an argument (e.g. `/pragma:init MyApp`); ask for anything not supplied.

## Process

### 1. Gather the basics

Ask (skip any already given as arguments):
- App/Xcode module name (e.g. `MyApp`) — must match the `.xcodeproj`
- Project root path (default: current directory — confirm this is actually the repo root containing the `.xcodeproj`)
- Xcode scheme name (default: same as the app name)

**Path guard — run this before any copy or delete below.** Confirming the root above is your judgment; this is the check. Resolve real paths (so a symlink can't disguise the target) and stop with a clear message, without copying or deleting anything, if it fails:

```bash
PROJECT_DIR="<the project root path from above>"   # set it explicitly — each Bash call is a fresh shell, so set PROJECT_DIR and APP_NAME at the top of every block below too
stop() { echo "STOP: $1"; exit 1; }
[ -n "$PROJECT_DIR" ] || stop "PROJECT_DIR is empty"
PROJECT_DIR="$(cd "$PROJECT_DIR" && pwd -P)" || stop "cannot enter PROJECT_DIR"
PLUGIN_ROOT="$(cd "${CLAUDE_PLUGIN_ROOT}" && pwd -P)" || stop "cannot resolve CLAUDE_PLUGIN_ROOT"
ls -d "$PROJECT_DIR"/*.xcodeproj >/dev/null 2>&1 || stop "no .xcodeproj in $PROJECT_DIR — not an iOS project root"
if [ "$PROJECT_DIR" = "$PLUGIN_ROOT" ] || [ "$PROJECT_DIR" -ef "$PLUGIN_ROOT" ]; then stop "$PROJECT_DIR is pragma itself"; fi
if grep -qs '"name": *"pragma"' "$PROJECT_DIR/.claude-plugin/plugin.json"; then stop "$PROJECT_DIR is a pragma checkout"; fi
if [ "$PROJECT_DIR/.claude/skills" -ef "$PLUGIN_ROOT/.claude/skills" ]; then stop "$PROJECT_DIR/.claude/skills is pragma's own skills"; fi
```

Any `STOP:` line (the block exits non-zero) means do not continue — if the target were pragma itself, step 2 would overwrite pragma's own skills. Tell the user which check failed and ask for the correct project root.

### 2. Copy and substitute skill files

Before copying, compare each existing `$PROJECT_DIR/.claude/skills/*/SKILL.md` with the pragma file of the same name (after `<AppName>` substitution). If any differ, the user customized them — copy the whole directory to `$PROJECT_DIR/.claude/skills.bak-<timestamp>/` first and tell them so; never overwrite customized skills silently. Also migrate any leftover `$PROJECT_DIR/.claude/commands/<name>.md` from a pre-skills pragma install: it's not diffable against this repo's old content (that content no longer exists here to compare against), so back up every match unconditionally to `$PROJECT_DIR/.claude/commands.bak-<timestamp>/` and remove it, so it can't shadow the new same-named skill; tell the user if they'd customized one, they should re-apply it into the matching `.claude/skills/<name>/SKILL.md`. The single block below implements both — do not skip straight to the final `cp -r`, it would silently discard any customization the comparison found, and skipping the migration loop would leave the old command file in place to collide with the new skill.

```bash
mkdir -p "$PROJECT_DIR/.claude/skills"
PRAGMA_SKILLS="$(cd "${CLAUDE_PLUGIN_ROOT}/.claude/skills" && find . -name 'SKILL.md' | sed 's|^\./||')"

# Customized-skill backup (compare before overwrite)
CUSTOMIZED=""
while IFS= read -r rel; do
  dest="$PROJECT_DIR/.claude/skills/$rel"
  [ -f "$dest" ] || continue
  sed "s|<AppName>|$APP_NAME|g" "${CLAUDE_PLUGIN_ROOT}/.claude/skills/$rel" | cmp -s - "$dest" || CUSTOMIZED="$CUSTOMIZED $rel"
done <<< "$PRAGMA_SKILLS"
if [ -n "$CUSTOMIZED" ]; then
  BACKUP_DIR="$PROJECT_DIR/.claude/skills.bak-$(date +%Y%m%d-%H%M%S)"
  while [ -e "$BACKUP_DIR" ]; do BACKUP_DIR="${BACKUP_DIR}-x"; done
  cp -RL "$PROJECT_DIR/.claude/skills" "$BACKUP_DIR"
  echo "Existing skill file(s) differ from pragma's:${CUSTOMIZED}"
  echo "Backed up .claude/skills/ to ${BACKUP_DIR#"$PROJECT_DIR"/} — re-apply your edits from there; delete it when done"
fi

# Migrate an older command-based install — one backup dir for the whole
# migration, computed once here, not recomputed per file inside the loop:
# date's 1-second resolution would otherwise scatter a multi-file migration
# across several separate backup directories instead of one.
if [ -d "$PROJECT_DIR/.claude/commands" ]; then
  MIGRATE_BACKUP_DIR="$PROJECT_DIR/.claude/commands.bak-$(date +%Y%m%d-%H%M%S)"
  while [ -e "$MIGRATE_BACKUP_DIR" ]; do MIGRATE_BACKUP_DIR="${MIGRATE_BACKUP_DIR}-x"; done
  MIGRATED=""
  while IFS= read -r rel; do
    name="$(dirname "$rel")"
    old="$PROJECT_DIR/.claude/commands/$name.md"
    [ -f "$old" ] || continue
    mkdir -p "$MIGRATE_BACKUP_DIR"
    cp -L "$old" "$MIGRATE_BACKUP_DIR/"
    rm -f "$old"
    MIGRATED="$MIGRATED $name"
  done <<< "$PRAGMA_SKILLS"
  if [ -n "$MIGRATED" ]; then
    echo "Superseded old .claude/commands/ file(s) by the new skill of the same name:${MIGRATED}"
    echo "Each was backed up before removal — check ${MIGRATE_BACKUP_DIR#"$PROJECT_DIR"/} if you had customized any of them, then re-apply into the matching .claude/skills/<name>/SKILL.md"
  fi
fi

cp -r "${CLAUDE_PLUGIN_ROOT}/.claude/skills/." "$PROJECT_DIR/.claude/skills/"
(cd "${CLAUDE_PLUGIN_ROOT}/.claude/skills" && find . -name "SKILL.md") | while read -r rel; do
  sed -i '' "s|<AppName>|$APP_NAME|g" "$PROJECT_DIR/.claude/skills/$rel"
done
```
(Use GNU `sed -i` without the trailing `''` on Linux — detect with `sed --version 2>/dev/null | grep -q GNU`.) This mirrors `scripts/setup.sh`'s equivalent logic exactly — reuse that behavior rather than reinventing it, so the two installers don't drift apart on what counts as "customized."

`.claude/skills/` ships only consumer-facing skills — `init.md` and `pragma-review.md` stay pragma-repo-only meta-commands under `.claude/commands/` and are never part of this copy.

### 3. Copy context, scripts, and CI workflows

Same as `scripts/setup.sh`, using `${CLAUDE_PLUGIN_ROOT}` as the source root: copy `${CLAUDE_PLUGIN_ROOT}/.claude/context/*.md` (skip any that already exist — never overwrite a project's existing decisions/rejections log), `${CLAUDE_PLUGIN_ROOT}/scripts/select_simulator.py`, `check_coverage.py`, `check_tdd_commit_order.py`, `check_gate_integrity.py`, `capture_pipeline_metrics.py`, and `slim_simulator.sh`, and `${CLAUDE_PLUGIN_ROOT}/scaffold/.github/workflows/*.yml` into `.github/workflows/` — strip the leading setup-comment block from each workflow file, then substitute `YOUR_PROJECT` → app name and `YOUR_SCHEME` → scheme name. Skip any workflow file that already exists at the destination and warn instead of overwriting. Also copy `${CLAUDE_PLUGIN_ROOT}/CONSTRAINTS.md` into the project root, same skip-if-exists rule as `AGENTS.md` below.

### 4. Ask for real content, write only where safe

Ask the same three questions regardless of what already exists in the target project:
- "What's your app's one-line description?" (e.g. "a personal finance app")
- "What's your architecture?" (offer MVVM + Repository as the default pragma assumes, but accept anything — TCA, VIPER, plain MVC)
- "What are your non-negotiable rules — the things no agent should ever be allowed to violate?" Prompt with examples relevant to their stated architecture (money-as-Decimal if it's a finance-adjacent app, zero-SwiftData-imports in domain services if MVVM+Repository, etc.) but don't assume — ask.

Then, independently for each destination file — the interview happens once, but whether it gets written depends on that specific file's own existing state:

- **`AGENTS.md` / `CLAUDE.md`**: if `CLAUDE.md` already exists, skip both — same guard `scripts/setup.sh` has always had — leave it untouched and tell the user to reconcile the interview answers into it themselves. Else if `AGENTS.md` already exists (but `CLAUDE.md` doesn't), just write `CLAUDE.md` as a one-line `@AGENTS.md` import. Otherwise generate `AGENTS.md` with real Architecture and Key Constraints content from the interview answers (keep the Build & Test and Merge Rule sections as-is — those are mechanical, not project-specific) and write `CLAUDE.md` as the `@AGENTS.md` import stub, so any AGENTS.md-reading agent and Claude Code both pick up the same content.
- **`.claude/context/invariants.md`**: if it already exists, skip — the same "never overwrite" rule step 3 applies to every other context file applies here too, since invariants.md is one of them. Do not append or merge into it; that risks duplicating or conflicting with existing numbered entries. If it doesn't exist, seed it with the non-negotiable-rules answer as numbered entries.

### 5. Report done

```
Pragma is installed and configured for <AppName>.

Next: run your first feature —

  /spec "describe your feature idea"
```

## Done when

`.claude/skills/`, `.claude/context/`, `scripts/`, `.github/workflows/`, and `CONSTRAINTS.md` are populated in the target project; any superseded `.claude/commands/<name>.md` from an older install has been backed up and removed; `AGENTS.md` has real architecture and constraint content if it didn't already exist, and `CLAUDE.md` imports it; `.claude/context/invariants.md` is seeded from the interview if it didn't already exist; no existing file was overwritten.
