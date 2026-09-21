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
if [ "$PROJECT_DIR/.claude/commands" -ef "$PLUGIN_ROOT/.claude/commands" ]; then stop "$PROJECT_DIR/.claude/commands is pragma's own commands"; fi
```

Any `STOP:` line (the block exits non-zero) means do not continue — if the target were pragma itself, step 2 would overwrite and then delete pragma's own `init.md` / `pragma-review.md`. Tell the user which check failed and ask for the correct project root.

### 2. Copy and substitute command files

Before copying, compare each existing `$PROJECT_DIR/.claude/commands/*.md` with the pragma file of the same name (after `<AppName>` substitution). If any differ, the user customized them — copy the whole directory to `$PROJECT_DIR/.claude/commands.bak-<timestamp>/` first and tell them so; never overwrite customized commands silently.

```bash
mkdir -p "$PROJECT_DIR/.claude/commands"
cp -r "${CLAUDE_PLUGIN_ROOT}/.claude/commands/." "$PROJECT_DIR/.claude/commands/"
(cd "${CLAUDE_PLUGIN_ROOT}/.claude/commands" && find . -name "*.md") | while read -r rel; do
  sed -i '' "s|<AppName>|$APP_NAME|g" "$PROJECT_DIR/.claude/commands/$rel"
done
```
(Use GNU `sed -i` without the trailing `''` on Linux — detect with `sed --version 2>/dev/null | grep -q GNU`.)

Remove `init.md` and `pragma-review.md` from the copied set — both are pragma-repo-only meta-commands (the plugin's install command, and pragma's own PR review agent), not part of the target project's own pipeline.

### 3. Copy context, scripts, and CI workflows

Same as `scripts/setup.sh`, using `${CLAUDE_PLUGIN_ROOT}` as the source root: copy `${CLAUDE_PLUGIN_ROOT}/.claude/context/*.md` (skip any that already exist — never overwrite a project's existing decisions/rejections log), `${CLAUDE_PLUGIN_ROOT}/scripts/select_simulator.py`, `check_coverage.py`, `check_tdd_commit_order.py`, `check_gate_integrity.py`, `capture_pipeline_metrics.py`, and `slim_simulator.sh`, and `${CLAUDE_PLUGIN_ROOT}/scaffold/.github/workflows/*.yml` into `.github/workflows/` — strip the leading setup-comment block from each workflow file, then substitute `YOUR_PROJECT` → app name and `YOUR_SCHEME` → scheme name. Skip any workflow file that already exists at the destination and warn instead of overwriting. Also copy `${CLAUDE_PLUGIN_ROOT}/CONSTRAINTS.md` into the project root, same skip-if-exists rule as `CLAUDE.md` below.

### 4. Ask for real content, write only where safe

Ask the same three questions regardless of what already exists in the target project:
- "What's your app's one-line description?" (e.g. "a personal finance app")
- "What's your architecture?" (offer MVVM + Repository as the default pragma assumes, but accept anything — TCA, VIPER, plain MVC)
- "What are your non-negotiable rules — the things no agent should ever be allowed to violate?" Prompt with examples relevant to their stated architecture (money-as-Decimal if it's a finance-adjacent app, zero-SwiftData-imports in domain services if MVVM+Repository, etc.) but don't assume — ask.

Then, independently for each destination file — the interview happens once, but whether it gets written depends on that specific file's own existing state:

- **`CLAUDE.md`**: if it already exists, skip — same guard `scripts/setup.sh` has always had — leave it untouched and tell the user to reconcile the interview answers into it themselves. If it doesn't exist, generate it with real Architecture and Key Constraints content from the interview answers (keep the Build & Test and Merge Rule sections as-is — those are mechanical, not project-specific).
- **`.claude/context/invariants.md`**: if it already exists, skip — the same "never overwrite" rule step 3 applies to every other context file applies here too, since invariants.md is one of them. Do not append or merge into it; that risks duplicating or conflicting with existing numbered entries. If it doesn't exist, seed it with the non-negotiable-rules answer as numbered entries.

### 5. Report done

```
Pragma is installed and configured for <AppName>.

Next: run your first feature —

  /spec "describe your feature idea"
```

## Done when

`.claude/commands/`, `.claude/context/`, `scripts/`, `.github/workflows/`, and `CONSTRAINTS.md` are populated in the target project; `CLAUDE.md` has real architecture and constraint content if it didn't already exist; `.claude/context/invariants.md` is seeded from the interview if it didn't already exist; neither file was overwritten if it was already there.
