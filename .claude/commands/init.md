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

This is the same operation `scripts/setup.sh` performs — both invoke `scripts/sync_skills.sh` so the two installers can't drift apart on what counts as "customized" or how the old-command migration backs things up. It compares each existing `$PROJECT_DIR/.claude/skills/*/SKILL.md` with the pragma file of the same name (after `<AppName>` substitution) and backs up the whole directory to `$PROJECT_DIR/.claude/skills.bak-<timestamp>/` first if any differ, then migrates any leftover `$PROJECT_DIR/.claude/commands/<name>.md` from a pre-skills pragma install to `$PROJECT_DIR/.claude/commands.bak-<timestamp>/` before removing it, so it can't shadow the new same-named skill.

```bash
"${CLAUDE_PLUGIN_ROOT}/scripts/sync_skills.sh" "${CLAUDE_PLUGIN_ROOT}/.claude/skills" "$PROJECT_DIR" "$APP_NAME"
```

`.claude/skills/` ships only consumer-facing skills — `init.md` and `pragma-review.md` stay pragma-repo-only meta-commands under `.claude/commands/` and are never part of this copy.

### 3. Copy context, scripts, and CI workflows

Same as `scripts/setup.sh`, using `${CLAUDE_PLUGIN_ROOT}` as the source root: copy `${CLAUDE_PLUGIN_ROOT}/.claude/context/*.md` (skip any that already exist — never overwrite a project's existing decisions/rejections log), `${CLAUDE_PLUGIN_ROOT}/scripts/select_simulator.py`, `check_coverage.py`, `check_tdd_commit_order.py`, `check_gate_integrity.py`, `capture_pipeline_metrics.py`, and `slim_simulator.sh`, and `${CLAUDE_PLUGIN_ROOT}/scaffold/.github/workflows/*.yml` into `.github/workflows/` — strip the leading setup-comment block from each workflow file, then substitute `YOUR_PROJECT` → app name and `YOUR_SCHEME` → scheme name. Skip any workflow file that already exists at the destination and warn instead of overwriting. Also copy `${CLAUDE_PLUGIN_ROOT}/CONSTRAINTS.md` into the project root, same skip-if-exists rule as `AGENTS.md` below.

Then install the guard hook, the same step `scripts/setup.sh` runs (default on; skip it only if the user asks not to have it):

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/install_guard_hook.py" "${CLAUDE_PLUGIN_ROOT}" "$PROJECT_DIR"
```

This copies `scaffold/.claude/hooks/guard_protected_paths.py` to `.claude/hooks/` and merges one `PreToolUse` entry into the project's `.claude/settings.json` without touching its other settings (the original is backed up; a second run changes nothing). If the script exits non-zero, the project's `settings.json` is invalid or has an unexpected `hooks` shape: it left everything as found. Show the user the error, do not edit `settings.json` yourself, and continue with the rest of setup. Tell the user in the final report that the hook blocks edits to skills, `AGENTS.md`/`CLAUDE.md`, `CONSTRAINTS.md`, `.claude/settings.json` and `.claude/hooks/` on `feature/*` branches, so those changes belong on a `chore/*` or `fix/*` branch.

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

`.claude/skills/`, `.claude/context/`, `scripts/`, `.github/workflows/`, and `CONSTRAINTS.md` are populated in the target project; any superseded `.claude/commands/<name>.md` from an older install has been backed up and removed; `AGENTS.md` has real architecture and constraint content if it didn't already exist, and `CLAUDE.md` imports it; `.claude/context/invariants.md` is seeded from the interview if it didn't already exist; no existing file was overwritten except `.claude/hooks/guard_protected_paths.py` (backed up first if it differed) and `.claude/settings.json` (backed up first, and only the guard entry added). The guard hook is installed and registered, or the user was told why not.
