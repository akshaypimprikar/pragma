#!/usr/bin/env bash
# Shared by scripts/setup.sh and .claude/commands/init.md — copies
# .claude/skills/ into a target project, backing up any customization and
# migrating an older .claude/commands/<name>.md install out of the way.
# The two callers must not drift on what counts as "customized": this is
# the single implementation both invoke.
#
# Usage: sync_skills.sh SKILLS_SRC PROJECT_DIR APP_NAME
#   SKILLS_SRC   — path to the pragma .claude/skills directory to copy from
#   PROJECT_DIR  — target project root (already resolved/validated by the caller)
#   APP_NAME     — substituted for <AppName> in copied skill files; <BuildTarget>
#                  becomes the detected -workspace/-project flag (detect_build_target.sh)
set -euo pipefail

SKILLS_SRC="$1"
PROJECT_DIR="$2"
APP_NAME="$3"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/lib_sedi.sh"
# <BuildTarget> becomes "-workspace <App>.xcworkspace" or "-project <App>.xcodeproj".
BUILD_TARGET="$("$SCRIPT_DIR/detect_build_target.sh" "$PROJECT_DIR" "$APP_NAME")"

# Appends -x until $1 no longer exists — a same-second re-run must not nest
# inside the earlier backup. Used for both the skills.bak and commands.bak
# directories below.
unique_backup_dir() {
    local dir="$1"
    while [[ -e "$dir" ]]; do dir="${dir}-x"; done
    echo "$dir"
}

mkdir -p "$PROJECT_DIR/.claude/skills"

PRAGMA_SKILLS="$(cd "$SKILLS_SRC" && find . -name 'SKILL.md' | sed 's|^\./||')"

# Back up before overwriting: an existing skill that differs from what we're
# about to write (after <AppName> substitution) is a customization, and cp -r
# below would silently replace it.
CUSTOMIZED=""
while IFS= read -r rel; do
    dest="$PROJECT_DIR/.claude/skills/$rel"
    [[ -f "$dest" ]] || continue
    sed -e "s|<AppName>|${APP_NAME}|g" -e "s|<BuildTarget>|${BUILD_TARGET}|g" "$SKILLS_SRC/$rel" | cmp -s - "$dest" || CUSTOMIZED="$CUSTOMIZED $rel"
done <<< "$PRAGMA_SKILLS"
if [[ -n "$CUSTOMIZED" ]]; then
    BACKUP_DIR="$(unique_backup_dir "$PROJECT_DIR/.claude/skills.bak-$(date +%Y%m%d-%H%M%S)")"
    # -L: a symlinked .claude/skills must be backed up as files, not as another symlink.
    cp -RL "$PROJECT_DIR/.claude/skills" "$BACKUP_DIR"
    echo "WARN: existing skill file(s) differ from pragma's:${CUSTOMIZED}"
    echo "WARN: backed up .claude/skills/ to ${BACKUP_DIR#"$PROJECT_DIR"/} — re-apply your edits from there; delete it when done"
fi

cp -r "$SKILLS_SRC/." "$PROJECT_DIR/.claude/skills/"

while IFS= read -r rel; do
    dest="$PROJECT_DIR/.claude/skills/$rel"
    if [[ -f "$dest" ]]; then sedi -e "s|<AppName>|${APP_NAME}|g" -e "s|<BuildTarget>|${BUILD_TARGET}|g" "$dest"; fi
done <<< "$PRAGMA_SKILLS"

# Migrate an older command-based install — one backup dir for the whole
# migration, computed once here, not recomputed per file inside the loop:
# date's 1-second resolution would otherwise scatter a multi-file migration
# across several separate backup directories instead of one.
if [[ -d "$PROJECT_DIR/.claude/commands" ]]; then
    MIGRATE_BACKUP_DIR="$(unique_backup_dir "$PROJECT_DIR/.claude/commands.bak-$(date +%Y%m%d-%H%M%S)")"
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
        echo "WARN: superseded old .claude/commands/ file(s) by the new skill of the same name:${MIGRATED}"
        echo "WARN: each was backed up before removal — check ${MIGRATE_BACKUP_DIR#"$PROJECT_DIR"/} if you had customized any of them, then re-apply into the matching .claude/skills/<name>/SKILL.md"
    fi
fi

# Codex reads skills from .agents/skills/, not .claude/skills/. Link each
# pragma skill there so .claude/skills/ stays the only copy. Per-skill links,
# not one directory link, so the project's own .agents/skills/ entries stay.
# A project that already links the whole directory to .claude/skills is done.
AGENTS_SKILLS="$PROJECT_DIR/.agents/skills"
if [[ ! "$AGENTS_SKILLS" -ef "$PROJECT_DIR/.claude/skills" ]]; then
    mkdir -p "$AGENTS_SKILLS"
    NOT_LINKED=""
    while IFS= read -r rel; do
        name="$(dirname "$rel")"
        link="$AGENTS_SKILLS/$name"
        target="../../.claude/skills/$name"
        if [[ -L "$link" && "$(readlink "$link")" == "$target" ]]; then continue; fi
        # Anything else under that name is the project's own: leave it.
        if [[ -e "$link" || -L "$link" ]]; then NOT_LINKED="$NOT_LINKED $name"; continue; fi
        ln -s "$target" "$link"
    done <<< "$PRAGMA_SKILLS"
    if [[ -n "$NOT_LINKED" ]]; then
        echo "WARN: .agents/skills/ already has an entry that is not pragma's link, left as is:${NOT_LINKED}"
        echo "WARN: Codex uses that entry instead of pragma's skill — remove it and re-run to link pragma's"
    fi
fi

echo "Skills ready ($(find "$PROJECT_DIR/.claude/skills" -name "SKILL.md" | wc -l | tr -d ' ') files)"
