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
     Re-running is a no-op once the canonical entry exists. An older or hand-edited
     guard registration (a different matcher or command) is replaced by the
     canonical one, so a later version that guards more tools reaches existing
     installs; other hooks that shared an entry with it are kept.
  3. Does the same for Codex CLI in PROJECT_DIR/.codex/hooks.json (same
     hooks/PreToolUse shape), pointing at the same script. Codex has no
     $CLAUDE_PROJECT_DIR, so the command finds the repo root with git. Codex
     runs a project hook only after the user trusts it (`/hooks` in Codex).

Validation happens before any write: a settings.json or hooks.json that is not
valid JSON, or whose hooks/PreToolUse has an unexpected shape, is reported and
both files are left exactly as found (exit 1), so a failure can't leave a
half-installed hook.

The hook only blocks edits to gate-definition files on feature/* branches; see
the docstring in the hook itself for what it does and does not detect.
"""
import json
import os
import re
import shutil
import subprocess
import sys
import time

HOOK_NAME = "guard_protected_paths.py"
MATCHER = "Write|Edit|MultiEdit|Bash"
# [ -f ] || exit 0: a project that deletes the hook file gets no error on every tool call.
COMMAND = (
    'f="$CLAUDE_PROJECT_DIR/.claude/hooks/guard_protected_paths.py"; '
    '[ -f "$f" ] || exit 0; exec python3 "$f"'
)
# Codex reports file edits as apply_patch and shell commands as Bash; matcher is a regex.
CODEX_MATCHER = "^(apply_patch|Bash)$"


def codex_command(rel="."):
    """rel: the project's path inside its git repo. The hook lives in PROJECT_DIR/.claude/hooks/, which is
    below the git root when the project is (an ios/ folder), so the command must say where."""
    hook = ".claude/hooks/guard_protected_paths.py" if rel == "." else f"{rel}/.claude/hooks/guard_protected_paths.py"
    return f'f="$(git rev-parse --show-toplevel 2>/dev/null)/{hook}"; [ -f "$f" ] || exit 0; exec python3 "$f"'


CODEX_COMMAND = codex_command()


def repo_relpath(project):
    """PROJECT_DIR relative to its git root ("." at the root, or when it is not in a git repo)."""
    try:
        top = subprocess.run(["git", "-C", project, "rev-parse", "--show-toplevel"],
                             capture_output=True, text=True, check=True).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return "."
    return os.path.relpath(project, os.path.realpath(top)) if top else "."


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
    # null is treated as absent, so register() can add the entry instead of crashing
    if data.get("hooks") is None:
        data.pop("hooks", None)
    elif isinstance(data["hooks"], dict) and data["hooks"].get("PreToolUse", 0) is None:
        del data["hooks"]["PreToolUse"]
    hooks = data.get("hooks")
    if hooks is not None and not isinstance(hooks, dict):
        raise ValueError(f'{path}: "hooks" must be an object')
    pre = (hooks or {}).get("PreToolUse")
    if pre is not None and not isinstance(pre, list):
        raise ValueError(f'{path}: "hooks.PreToolUse" must be a list')
    return data, raw


def guard_hooks(data):
    """[(entry, hook)] for every registered hook whose command names the guard script."""
    found = []
    for entry in (data.get("hooks") or {}).get("PreToolUse") or []:
        if not isinstance(entry, dict):
            continue
        for h in entry.get("hooks") or []:
            if isinstance(h, dict) and HOOK_NAME in str(h.get("command", "")):
                found.append((entry, h))
    return found


def is_canonical(entry, hook, matcher=MATCHER, command=COMMAND):
    return entry.get("matcher") == matcher and hook.get("command") == command


def register(data, matcher=MATCHER, command=COMMAND):
    """Make the canonical guard entry the only guard registration. Returns True if `data` changed."""
    existing = guard_hooks(data)
    if len(existing) == 1 and is_canonical(*existing[0], matcher, command):
        return False
    pre = data["hooks"]["PreToolUse"] if existing else None
    for entry, hook in existing:  # drop stale registrations, keep whatever shared their entry
        entry["hooks"].remove(hook)
    if pre is not None:
        pre[:] = [e for e in pre if not (isinstance(e, dict) and e.get("hooks") == [])]
    hooks = data.setdefault("hooks", {})
    hooks.setdefault("PreToolUse", []).append(
        {"matcher": matcher, "hooks": [{"type": "command", "command": command}]}
    )
    return True


def write_settings(path, data, raw, stamp, project, label):
    if raw:
        backup = unique(f"{path}.bak-{stamp}")
        shutil.copy2(path, backup)
        print(f"  backed up {label} to {os.path.relpath(backup, project)}")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf8") as f:
        json.dump(data, f, indent=detect_indent(raw), ensure_ascii=False)
        f.write("\n")


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
    codex_path = os.path.join(project, ".codex", "hooks.json")
    try:
        data, raw = load_settings(settings_path)
        codex_data, codex_raw = load_settings(codex_path)
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

    existing = bool(guard_hooks(data))
    if not register(data):
        print("  settings.json already registers the guard hook — left unchanged")
    else:
        write_settings(settings_path, data, raw, stamp, project, "settings.json")
        print(f"  {'updated the' if existing else 'registered the'} PreToolUse guard in .claude/settings.json")

    existing = bool(guard_hooks(codex_data))
    if not register(codex_data, CODEX_MATCHER, codex_command(repo_relpath(project))):
        print("  .codex/hooks.json already registers the guard hook — left unchanged")
    else:
        write_settings(codex_path, codex_data, codex_raw, stamp, project, ".codex/hooks.json")
        print(f"  {'updated the' if existing else 'registered the'} PreToolUse guard in .codex/hooks.json "
              "(Codex runs it once you trust it with /hooks)")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
