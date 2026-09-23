#!/usr/bin/env bash
# Usage: ./scripts/setup.sh APP_NAME [PROJECT_DIR] [SCHEME]
#
# APP_NAME     — your Xcode project/module name (e.g. MyApp)
# PROJECT_DIR  — path to your iOS project root (default: current directory)
# SCHEME       — Xcode scheme name (default: same as APP_NAME)
#
# What it does:
#   - Copies .claude/skills/, .claude/context/, scripts/, and
#     scaffold/.github/workflows/ into your project. .claude/skills/ is the
#     SKILL.md format (an open standard also read by Cursor, Codex, GitHub
#     Copilot, Windsurf, and others), replacing the old .claude/commands/
#     directory (Claude Code-only) as pragma's pipeline layer
#   - Replaces <AppName> in the copied skill files with APP_NAME
#   - Before overwriting, backs up .claude/skills/ to
#     .claude/skills.bak-<timestamp>/ if any existing skill file differs
#     from what pragma is about to write (i.e. you customized it)
#   - Migrates an older install: any .claude/commands/<name>.md that shares
#     a name with a skill being installed is backed up alongside it and
#     removed, so the old command can't shadow or collide with the new skill
#   - Replaces YOUR_PROJECT / YOUR_SCHEME in workflow files
#   - Generates a starter AGENTS.md if one doesn't exist, plus a CLAUDE.md
#     that imports it (@AGENTS.md), so any AGENTS.md-reading agent and
#     Claude Code both pick up the same content

set -euo pipefail

# ── Colours ──────────────────────────────────────────────────────────────────
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[0;33m'
CYAN='\033[0;36m'; BOLD='\033[1m'; RESET='\033[0m'

info()    { echo -e "${CYAN}  →${RESET} $*"; }
success() { echo -e "${GREEN}  ✓${RESET} $*"; }
warn()    { echo -e "${YELLOW}  !${RESET} $*"; }
die()     { echo -e "${RED}  ✗${RESET} $*" >&2; exit 1; }

# ── Args ─────────────────────────────────────────────────────────────────────
APP_NAME="${1:-}"
PROJECT_DIR="${2:-.}"
SCHEME="${3:-$APP_NAME}"

[[ -z "$APP_NAME" ]] && die "Usage: $0 APP_NAME [PROJECT_DIR] [SCHEME]"
[[ ! -d "$PROJECT_DIR" ]] && die "Project directory not found: $PROJECT_DIR"

# pwd -P resolves symlinks, so a symlink to pragma's root can't slip past the
# string comparison; -ef (same device+inode) covers anything pwd -P doesn't.
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd -P)"
PROJECT_DIR="$(cd "$PROJECT_DIR" && pwd -P)"

if [[ "$PROJECT_DIR" == "$REPO_ROOT" || "$PROJECT_DIR" -ef "$REPO_ROOT" ]]; then
    die "PROJECT_DIR resolves to pragma's own repo root ($REPO_ROOT) — this would delete pragma's own init.md/pragma-review.md. Pass an explicit path to your iOS project as the second argument."
fi
# Same guard for a different clone/worktree of pragma: not the same path, same damage.
if grep -qs '"name": *"pragma"' "$PROJECT_DIR/.claude-plugin/plugin.json"; then
    die "PROJECT_DIR ($PROJECT_DIR) is a pragma checkout (.claude-plugin/plugin.json names it) — this would delete its init.md/pragma-review.md. Pass the path to your iOS project as the second argument."
fi

echo ""
echo -e "${BOLD}Pragma setup${RESET}"
echo -e "  App:     ${CYAN}${APP_NAME}${RESET}"
echo -e "  Scheme:  ${CYAN}${SCHEME}${RESET}"
echo -e "  Target:  ${CYAN}${PROJECT_DIR}${RESET}"
echo ""

# ── sed helper (BSD/GNU portable) ────────────────────────────────────────────
sedi() {
    if sed --version 2>/dev/null | grep -q GNU; then
        sed -i "$@"
    else
        sed -i '' "$@"
    fi
}

# ── 1. Skills ────────────────────────────────────────────────────────────────
info "Copying skill files…"
mkdir -p "$PROJECT_DIR/.claude/skills"

# Belt-and-braces for the rm below: a symlinked .claude/ or .claude/skills/
# can point at pragma's own skills even when PROJECT_DIR itself does not.
[[ "$PROJECT_DIR/.claude/skills" -ef "$REPO_ROOT/.claude/skills" ]] && die "$PROJECT_DIR/.claude/skills resolves to pragma's own .claude/skills — refusing to copy onto or delete from it."

# Back up before overwriting: an existing skill that differs from what we're
# about to write (after <AppName> substitution) is a customization, and cp -r
# below would silently replace it.
PRAGMA_SKILLS="$(cd "$REPO_ROOT/.claude/skills" && find . -name 'SKILL.md' | sed 's|^\./||')"
CUSTOMIZED=""
while IFS= read -r rel; do
    dest="$PROJECT_DIR/.claude/skills/$rel"
    [[ -f "$dest" ]] || continue
    sed "s|<AppName>|${APP_NAME}|g" "$REPO_ROOT/.claude/skills/$rel" | cmp -s - "$dest" || CUSTOMIZED="$CUSTOMIZED $rel"
done <<< "$PRAGMA_SKILLS"
if [[ -n "$CUSTOMIZED" ]]; then
    BACKUP_DIR="$PROJECT_DIR/.claude/skills.bak-$(date +%Y%m%d-%H%M%S)"
    # Same-second re-run must not nest inside the earlier backup.
    while [[ -e "$BACKUP_DIR" ]]; do BACKUP_DIR="${BACKUP_DIR}-x"; done
    # -L: a symlinked .claude/skills must be backed up as files, not as another symlink.
    cp -RL "$PROJECT_DIR/.claude/skills" "$BACKUP_DIR"
    warn "Existing skill file(s) differ from pragma's:${CUSTOMIZED}"
    warn "Backed up .claude/skills/ to ${BACKUP_DIR#"$PROJECT_DIR"/} — re-apply your edits from there; delete it when done"
fi

cp -r "$REPO_ROOT/.claude/skills/." "$PROJECT_DIR/.claude/skills/"

info "Substituting <AppName> in skills…"
while IFS= read -r rel; do
    dest="$PROJECT_DIR/.claude/skills/$rel"
    if [[ -f "$dest" ]]; then sedi "s|<AppName>|${APP_NAME}|g" "$dest"; fi
done <<< "$PRAGMA_SKILLS"
success "Skills ready ($(find "$PROJECT_DIR/.claude/skills" -name "SKILL.md" | wc -l | tr -d ' ') files)"

# ── 1b. Migrate an older command-based install ────────────────────────────────
# A project set up before this version has .claude/commands/<name>.md for the
# same names now shipped as .claude/skills/<name>/SKILL.md. Left in place, the
# old command would shadow or collide with the new skill. We can't diff it
# against pragma's old content (that content no longer exists in this repo to
# compare against), so every match is backed up unconditionally rather than
# guessed at — safe by construction, never a silent delete.
if [[ -d "$PROJECT_DIR/.claude/commands" ]]; then
    # One backup dir for the whole migration, computed before the loop — not
    # per file inside it. date's 1-second resolution means recomputing this
    # per iteration collides across most/all of a multi-file migration, so
    # each file would land in its own separate -x-suffixed directory instead
    # of one consolidated backup.
    MIGRATE_BACKUP_DIR="$PROJECT_DIR/.claude/commands.bak-$(date +%Y%m%d-%H%M%S)"
    while [[ -e "$MIGRATE_BACKUP_DIR" ]]; do MIGRATE_BACKUP_DIR="${MIGRATE_BACKUP_DIR}-x"; done
    MIGRATED=""
    while IFS= read -r rel; do
        name="$(dirname "$rel")"
        old="$PROJECT_DIR/.claude/commands/$name.md"
        [[ -f "$old" ]] || continue
        mkdir -p "$MIGRATE_BACKUP_DIR"
        cp -L "$old" "$MIGRATE_BACKUP_DIR/"
        rm -f "$old"
        MIGRATED="$MIGRATED $name"
    done <<< "$PRAGMA_SKILLS"
    if [[ -n "$MIGRATED" ]]; then
        warn "Superseded old .claude/commands/ file(s) by the new skill of the same name:${MIGRATED}"
        warn "Each was backed up before removal — check ${MIGRATE_BACKUP_DIR#"$PROJECT_DIR"/} if you had customized any of them, then re-apply into the matching .claude/skills/<name>/SKILL.md"
    fi
fi

# ── 2. Context ────────────────────────────────────────────────────────────────
info "Copying context files…"
mkdir -p "$PROJECT_DIR/.claude/context"
for f in "$REPO_ROOT/.claude/context/"*.md; do
    dest="$PROJECT_DIR/.claude/context/$(basename "$f")"
    if [[ -f "$dest" ]]; then
        warn "Skipping $(basename "$f") — already exists"
    else
        cp "$f" "$dest"
    fi
done
success "Context files ready"

# ── 3. Scripts ────────────────────────────────────────────────────────────────
info "Copying support scripts…"
mkdir -p "$PROJECT_DIR/scripts"
cp "$REPO_ROOT/scripts/select_simulator.py"      "$PROJECT_DIR/scripts/"
cp "$REPO_ROOT/scripts/check_coverage.py"        "$PROJECT_DIR/scripts/"
cp "$REPO_ROOT/scripts/check_tdd_commit_order.py" "$PROJECT_DIR/scripts/"
cp "$REPO_ROOT/scripts/check_gate_integrity.py"  "$PROJECT_DIR/scripts/"
cp "$REPO_ROOT/scripts/capture_pipeline_metrics.py" "$PROJECT_DIR/scripts/"
cp "$REPO_ROOT/scripts/slim_simulator.sh"        "$PROJECT_DIR/scripts/"
success "Scripts ready"

# ── 3b. CONSTRAINTS.md ────────────────────────────────────────────────────────
CONSTRAINTS_MD="$PROJECT_DIR/CONSTRAINTS.md"
if [[ -f "$CONSTRAINTS_MD" ]]; then
    warn "CONSTRAINTS.md already exists — skipping"
else
    info "Copying starter CONSTRAINTS.md…"
    cp "$REPO_ROOT/CONSTRAINTS.md" "$CONSTRAINTS_MD"
    success "CONSTRAINTS.md ready"
fi

# ── 4. CI workflows ───────────────────────────────────────────────────────────
info "Copying CI workflows…"
mkdir -p "$PROJECT_DIR/.github/workflows"
for f in "$REPO_ROOT/scaffold/.github/workflows/"*.yml; do
    dest="$PROJECT_DIR/.github/workflows/$(basename "$f")"
    if [[ -f "$dest" ]]; then
        warn "Skipping $(basename "$f") — already exists (run with --force to overwrite)"
    else
        cp "$f" "$dest"
        # Strip the setup comment block (up to and including the closing ===== line)
        sedi '1,/^# ====/d' "$dest"
        # Substitute placeholders
        sedi "s|YOUR_PROJECT|${APP_NAME}|g" "$dest"
        sedi "s|YOUR_SCHEME|${SCHEME}|g"   "$dest"
    fi
done
success "CI workflows ready"

# ── 5. AGENTS.md / CLAUDE.md ──────────────────────────────────────────────────
# AGENTS.md carries the real content so any AGENTS.md-reading agent picks it
# up; CLAUDE.md becomes a one-line import so Claude Code's own behavior is
# unchanged. If CLAUDE.md already exists with real content (pre-dating this
# split), leave both alone rather than silently overwriting it with a stub —
# that would delete whatever the user already wrote.
AGENTS_MD="$PROJECT_DIR/AGENTS.md"
CLAUDE_MD="$PROJECT_DIR/CLAUDE.md"
if [[ -f "$CLAUDE_MD" ]]; then
    warn "CLAUDE.md already exists — skipping AGENTS.md/CLAUDE.md generation (reconcile manually if you want AGENTS.md too)"
elif [[ -f "$AGENTS_MD" ]]; then
    info "AGENTS.md already exists — writing CLAUDE.md as an import stub…"
    echo '@AGENTS.md' > "$CLAUDE_MD"
    success "CLAUDE.md generated (imports AGENTS.md)"
else
    info "Generating starter AGENTS.md…"
    cat > "$AGENTS_MD" <<CLAUDEMD
# AGENTS.md

${APP_NAME} — iOS app (SwiftUI + SwiftData).

## Build & Test

All commands run from the repo root (contains \`${APP_NAME}.xcodeproj\`).

\`\`\`bash
# Build
xcodebuild build -project ${APP_NAME}.xcodeproj -scheme ${SCHEME} -configuration Debug -destination 'platform=iOS Simulator,name=iPhone 17,OS=<pin to your installed runtime, e.g. 26.4.1>'

# Full test suite
xcodebuild test -project ${APP_NAME}.xcodeproj -scheme ${SCHEME} -destination 'platform=iOS Simulator,name=iPhone 17,OS=<pin to your installed runtime, e.g. 26.4.1>'

# Single suite / single test
xcodebuild test -project ${APP_NAME}.xcodeproj -scheme ${SCHEME} -destination 'platform=iOS Simulator,name=iPhone 17,OS=<pin to your installed runtime, e.g. 26.4.1>' -only-testing:${APP_NAME}Tests/<SuiteName>
\`\`\`

> **Simulator:** pin \`OS=\` explicitly to your installed runtime version (check with \`xcrun simctl list runtimes\`; \`xcodebuild\` requires an exact match) — a bare \`name=iPhone 17\` destination becomes ambiguous the moment a second iOS runtime is installed, since each gets its own "iPhone 17" device. If a UI test fails with \`RequestDenied ... SBMainWorkspace\`, the simulator's SpringBoard state is corrupt — \`xcrun simctl erase <device-id>\` and reboot it; killing \`Simulator.app\`/\`CoreSimulatorService\` alone won't fix it.
> **File inclusion:** \`PBXFileSystemSynchronizedRootGroup\` (Xcode 16) — drop a \`.swift\` file in the right folder and it compiles automatically. Never edit \`project.pbxproj\`.

## Architecture

<!-- Describe your architecture here. Example:
MVVM + Repository. Layers top → bottom:
Views → ViewModels (@Observable) → Domain Services → Repository Protocols → SwiftData Repositories → @Model entities.
-->

## Key constraints

<!-- List your non-negotiable rules here. Examples:
- All money values: Decimal, never Double
- Domain Services: zero SwiftData imports — 100% unit-testable without a simulator
- Tests use \`import Testing\` with \`@Suite\` / \`@Test\` / \`#expect()\`
-->

## Merge rule

No command merges a PR automatically. A PR targeting \`develop\` is mergeable only once \`/review\` returns APPROVED, \`/test\` passes, and \`code-review:code-review\` is clean — then the user merges it themselves. (If PRs here are authored under your own GitHub account, GitHub blocks self-approval, so a GitHub review-approval check can't gate this either.) \`release/*\`/\`hotfix/*\` PRs targeting \`main\` are exempt from \`/review\` and \`code-review:code-review\` — every commit already passed both when it merged into \`develop\`; \`/release\`'s pre-flight test run is the only gate needed there. Agents report their verdict and stop.
CLAUDEMD
    success "AGENTS.md generated"
    echo '@AGENTS.md' > "$CLAUDE_MD"
    success "CLAUDE.md generated (imports AGENTS.md)"
fi

# ── Done ──────────────────────────────────────────────────────────────────────
echo ""
echo -e "${BOLD}${GREEN}Setup complete.${RESET} Next steps:"
echo ""
echo -e "  1. Fill in ${CYAN}AGENTS.md${RESET} — architecture rules + build commands (CLAUDE.md imports it)"
echo -e "  2. Seed ${CYAN}.claude/context/invariants.md${RESET} with your non-negotiable rules"
echo -e "  3. Review ${CYAN}CONSTRAINTS.md${RESET} — Gate 11 (gate integrity) is on by default and will flag any in-flight feature/* branch already editing gates.md/CONSTRAINTS.md/a check_*.py script; uncomment opt-in dimensions as you adopt them"
echo -e "  4. Replace \`YOUR_SIMULATOR\` in CI workflows if you use a non-default device"
echo -e "  5. Run your first feature:"
echo ""
echo -e "     ${BOLD}/spec \"describe your feature idea\"${RESET}"
echo ""
