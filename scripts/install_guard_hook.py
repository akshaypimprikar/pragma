#!/usr/bin/env python3
"""
Installs pragma's PreToolUse guard hook into a consuming project.

Shared by scripts/setup.sh and .claude/commands/init.md so the two installers
can't drift (same reason scripts/sync_skills.sh exists).

Usage: install_guard_hook.py PRAGMA_ROOT PROJECT_DIR

What it does:
  1. Copies PRAGMA_ROOT/scaffold/.claude/hooks/guard_protected_paths.py to
     PROJECT_DIR/.claude/hooks/. A differing existing copy is backed up to
     .claude/hooks.bak-<timestamp>/ first.
  2. Merges one PreToolUse entry into PROJECT_DIR/.claude/settings.json without
     touching anything else in it: other hooks, permissions and env keep their
     place. The original is backed up to .claude/settings.json.bak-<timestamp>.
     Re-running is a no-op once an entry naming guard_protected_paths.py exists.

Validation happens before any write: a settings.json that is not valid JSON, or
whose hooks/PreToolUse has an unexpected shape, is reported and left exactly as
found (exit 1), so a failure can't leave a half-installed hook.

The hook only blocks edits to gate-definition files on feature/* branches; see
the docstring in the hook itself for what it does and does not detect.
"""
import json
import os
import re
import shutil
import sys
import time

HOOK_NAME = "guard_protected_paths.py"
MATCHER = "Write|Edit|MultiEdit|Bash"
# [ -f ] || exit 0: a project that deletes the hook file gets no error on every tool call.
COMMAND = (
    'f="$CLAUDE_PROJECT_DIR/.claude/hooks/guard_protected_paths.py"; '
    '[ -f "$f" ] || exit 0; exec python3 "$f"'
)


def unique(path):
    """path, or path-x, path-xx... until it doesn't exist (same-second re-runs must not nest)."""
    while os.path.exists(path):
        path += "-x"
    return path


def detect_indent(raw):
    m = re.search(r'^([ \t]+)"', raw, re.MULTILINE)
    return m.group(1) if m else 2


def load_settings(path):
    """(settings dict, raw text). ({}, "") if the file does not exist. Raises ValueError on a bad shape."""
    if not os.path.exists(path):
        return {}, ""
    raw = open(path, encoding="utf8").read()
    try:
        data = json.loads(raw) if raw.strip() else {}
    except json.JSONDecodeError as e:
        raise ValueError(f"{path} is not valid JSON ({e}); fix it and re-run") from e
    if not isinstance(data, dict):
        raise ValueError(f"{path} must contain a JSON object")
    hooks = data.get("hooks")
    if hooks is not None and not isinstance(hooks, dict):
        raise ValueError(f'{path}: "hooks" must be an object')
    pre = (hooks or {}).get("PreToolUse")
    if pre is not None and not isinstance(pre, list):
        raise ValueError(f'{path}: "hooks.PreToolUse" must be a list')
    return data, raw


def already_registered(data):
    for entry in (data.get("hooks") or {}).get("PreToolUse") or []:
        if not isinstance(entry, dict):
            continue
        for h in entry.get("hooks") or []:
            if isinstance(h, dict) and HOOK_NAME in str(h.get("command", "")):
                return True
    return False


def main(argv):
    if len(argv) != 3:
        print("Usage: install_guard_hook.py PRAGMA_ROOT PROJECT_DIR", file=sys.stderr)
        return 2
    pragma_root, project = os.path.realpath(argv[1]), os.path.realpath(argv[2])
    src = os.path.join(pragma_root, "scaffold", ".claude", "hooks", HOOK_NAME)
    if not os.path.isfile(src):
        print(f"ERROR: hook source not found: {src}", file=sys.stderr)
        return 1

    settings_path = os.path.join(project, ".claude", "settings.json")
    try:
        data, raw = load_settings(settings_path)
    except ValueError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1

    stamp = time.strftime("%Y%m%d-%H%M%S")
    hooks_dir = os.path.join(project, ".claude", "hooks")
    dest = os.path.join(hooks_dir, HOOK_NAME)
    os.makedirs(hooks_dir, exist_ok=True)
    if os.path.exists(dest) and open(dest, "rb").read() != open(src, "rb").read():
        backup = unique(os.path.join(project, ".claude", f"hooks.bak-{stamp}"))
        os.makedirs(backup)
        shutil.copy2(dest, os.path.join(backup, HOOK_NAME))
        print(f"  backed up the existing {HOOK_NAME} to {os.path.relpath(backup, project)}/")
    shutil.copy2(src, dest)
    os.chmod(dest, 0o755)
    print(f"  installed .claude/hooks/{HOOK_NAME}")

    if already_registered(data):
        print("  settings.json already registers the guard hook — left unchanged")
        return 0

    if raw:
        backup = unique(f"{settings_path}.bak-{stamp}")
        shutil.copy2(settings_path, backup)
        print(f"  backed up settings.json to {os.path.relpath(backup, project)}")
    hooks = data.setdefault("hooks", {})
    hooks.setdefault("PreToolUse", []).append(
        {"matcher": MATCHER, "hooks": [{"type": "command", "command": COMMAND}]}
    )
    os.makedirs(os.path.dirname(settings_path), exist_ok=True)
    with open(settings_path, "w", encoding="utf8") as f:
        json.dump(data, f, indent=detect_indent(raw), ensure_ascii=False)
        f.write("\n")
    print("  registered the PreToolUse guard in .claude/settings.json")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
