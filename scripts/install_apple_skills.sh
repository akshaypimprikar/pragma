#!/usr/bin/env bash
# Usage: ./scripts/install_apple_skills.sh [DEST_DIR]
#
# Installs an allowlisted subset of the Agent Skills that ship inside Xcode 27
# and later into DEST_DIR (default: ~/.claude/skills), where Claude Code loads
# them on its own when a prompt matches. Rerun after an Xcode update to refresh
# them. Apple publishes these files only inside Xcode and they carry no license
# header, so pragma exports them from your own Xcode instead of vendoring them,
# and installs them user-level so they are never committed to a project.
#
# The allowlist holds only skills reviewed against pragma's gates. Reviewed on
# Xcode 27.2 (27B52), 2026-11-01; left out on purpose:
#   - app-intents-whats-new-27: its testing recipe writes XCTestCase unit tests,
#     which /test and Gate 7 forbid
#   - app-resizability: lists adding SceneDelegate.swift to .pbxproj as an
#     auto-fix; /feature never hand-edits project.pbxproj
#   - modernize-tests: turns XCTest skip calls into Swift Testing's disabled
#     trait, which check_gate_integrity.py flags as a new suppression marker
#   - device-interaction, audit-xcode-security-settings: build, run or edit
#     build settings and entitlements outside /feature and /gates
#   - the rest: not reviewed yet
# Add a skill here only after checking it against /test, /feature, /gates and
# check_gate_integrity.py.

set -euo pipefail

ALLOWLIST=(swiftui-whats-new-27)
DEST="${1:-$HOME/.claude/skills}"

warn() { echo "  ! $*" >&2; }

# Apple's exporter writes SKILL.md frontmatter keys in a different order from
# run to run (seen on 27.2), so SKILL.md is compared as sorted lines; every
# other file must match exactly.
same_skill() {
    diff -rq -x SKILL.md "$1" "$2" >/dev/null 2>&1 &&
        [[ -f "$2/SKILL.md" ]] &&
        diff -q <(sort "$1/SKILL.md") <(sort "$2/SKILL.md") >/dev/null 2>&1
}

if ! command -v xcrun >/dev/null 2>&1 || ! xcrun agent skills >/dev/null 2>&1; then
    warn "Apple Agent Skills need Xcode 27 or later (xcrun agent skills not found); nothing installed"
    exit 1
fi

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

echo "  → Exporting Apple Agent Skills from $(xcode-select -p) (can take a few minutes)…"
xcrun agent skills export --output-dir "$TMP" >/dev/null

mkdir -p "$DEST"
BAK=""
for name in "${ALLOWLIST[@]}"; do
    if [[ ! -f "$TMP/$name/SKILL.md" ]]; then
        warn "$name is not in this Xcode's export; skipped"
        continue
    fi
    if [[ -e "$DEST/$name" ]]; then
        if same_skill "$TMP/$name" "$DEST/$name"; then
            echo "  ✓ $name already up to date"
            continue
        fi
        # Backed up beside DEST, not inside it, so the old copy can't load as a
        # second skill. mktemp keeps two runs in the same second apart.
        [[ -z "$BAK" ]] && BAK="$(mktemp -d "$DEST.bak-$(date +%Y%m%d-%H%M%S).XXXXXX")"
        mv "$DEST/$name" "$BAK/$name"
        warn "existing $name backed up to $BAK/$name"
    fi
    cp -R "$TMP/$name" "$DEST/$name"
    echo "  ✓ $name installed to $DEST/$name"
done
