#!/usr/bin/env bash
# Usage: ./scripts/setup.sh APP_NAME [PROJECT_DIR] [SCHEME]
#
# APP_NAME     — your Xcode project/module name (e.g. MyApp)
# PROJECT_DIR  — path to your iOS project root (default: current directory)
# SCHEME       — Xcode scheme name (default: same as APP_NAME)
#
# What it does:
#   - Copies .claude/commands/, .claude/context/, scripts/, and
#     scaffold/.github/workflows/ into your project
#   - Replaces <AppName> in all command files with APP_NAME
#   - Replaces YOUR_PROJECT / YOUR_SCHEME in workflow files
#   - Generates a starter CLAUDE.md if one doesn't exist

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

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
PROJECT_DIR="$(cd "$PROJECT_DIR" && pwd)"

echo ""
echo -e "${BOLD}ios-agent-workflow setup${RESET}"
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

# ── 1. Commands ───────────────────────────────────────────────────────────────
info "Copying command files…"
mkdir -p "$PROJECT_DIR/.claude/commands"
cp -r "$REPO_ROOT/.claude/commands/." "$PROJECT_DIR/.claude/commands/"

info "Substituting <AppName> in commands…"
find "$PROJECT_DIR/.claude/commands" -name "*.md" | while read -r f; do
    sedi "s|<AppName>|${APP_NAME}|g" "$f"
done
success "Commands ready ($(find "$PROJECT_DIR/.claude/commands" -name "*.md" | wc -l | tr -d ' ') files)"

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
cp "$REPO_ROOT/scripts/select_simulator.py" "$PROJECT_DIR/scripts/"
cp "$REPO_ROOT/scripts/check_coverage.py"   "$PROJECT_DIR/scripts/"
success "Scripts ready"

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

# ── 5. CLAUDE.md ──────────────────────────────────────────────────────────────
CLAUDE_MD="$PROJECT_DIR/CLAUDE.md"
if [[ -f "$CLAUDE_MD" ]]; then
    warn "CLAUDE.md already exists — skipping"
else
    info "Generating starter CLAUDE.md…"
    cat > "$CLAUDE_MD" <<CLAUDEMD
# CLAUDE.md

${APP_NAME} — iOS app (SwiftUI + SwiftData).

## Build & Test

All commands run from the repo root (contains \`${APP_NAME}.xcodeproj\`).

\`\`\`bash
# Build
xcodebuild build -project ${APP_NAME}.xcodeproj -scheme ${SCHEME} -configuration Debug -destination 'platform=iOS Simulator,name=iPhone 17'

# Full test suite
xcodebuild test -project ${APP_NAME}.xcodeproj -scheme ${SCHEME} -destination 'platform=iOS Simulator,name=iPhone 17'

# Single suite / single test
xcodebuild test -project ${APP_NAME}.xcodeproj -scheme ${SCHEME} -destination 'platform=iOS Simulator,name=iPhone 17' -only-testing:${APP_NAME}Tests/<SuiteName>
\`\`\`

> **Simulator:** \`iPhone 17\` — iOS 26.4 ships with iPhone 17 only, not iPhone 16.
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
CLAUDEMD
    success "CLAUDE.md generated"
fi

# ── Done ──────────────────────────────────────────────────────────────────────
echo ""
echo -e "${BOLD}${GREEN}Setup complete.${RESET} Next steps:"
echo ""
echo -e "  1. Fill in ${CYAN}CLAUDE.md${RESET} — architecture rules + build commands"
echo -e "  2. Seed ${CYAN}.claude/context/invariants.md${RESET} with your non-negotiable rules"
echo -e "  3. Replace \`YOUR_SIMULATOR\` in CI workflows if you use a non-default device"
echo -e "  4. Run your first feature:"
echo ""
echo -e "     ${BOLD}/spec \"describe your feature idea\"${RESET}"
echo ""
