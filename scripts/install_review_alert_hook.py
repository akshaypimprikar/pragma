#!/usr/bin/env python3
"""
Installs pragma's pipeline-review alert hook into a consuming project.

Shared by scripts/setup.sh and .claude/commands/init.md so the two installers
can't drift (same reason install_guard_hook.py and scripts/sync_skills.sh exist).

Usage: install_review_alert_hook.py PROJECT_DIR

What it does:
  Merges one UserPromptSubmit entry into PROJECT_DIR/.claude/settings.json without
  touching anything else in it. On every prompt, the hook reads the frontmatter of
  each docs/pipeline-review/*.md report and, if any has `addressed: false`, shows a
  one-line alert naming the count and the latest report, both to the user
  (`systemMessage`) and to the agent (`additionalContext`; a `systemMessage` alone
  is shown only to the user, so the agent would never act on it). It reads the frontmatter
  only, so a report that mentions the phrase in its body is not counted, and it
  accepts `addressed: "false"` and a trailing comment. It is an inline shell command
  (no file to copy) that needs only awk and resolves the project through
  $CLAUDE_PROJECT_DIR, and it exits quietly when the directory does not exist.

  The original settings.json is backed up to .claude/settings.json.bak-<timestamp>.
  Re-running is a no-op once the canonical entry exists; an older registration
  (any UserPromptSubmit hook whose command names docs/pipeline-review) is replaced
  by the canonical one, and other hooks that shared its entry are kept.

Validation happens before any write: a settings.json that is not valid JSON, or
whose hooks/UserPromptSubmit has an unexpected shape, is reported and left exactly
as found (exit 1).
"""
import json
import os
import shutil
import sys
import time

# Importing the sibling installer would otherwise leave scripts/__pycache__/ in pragma's checkout.
sys.dont_write_bytecode = True
from install_guard_hook import detect_indent, unique  # noqa: E402

EVENT = "UserPromptSubmit"
MARKER = "docs/pipeline-review"
COMMAND = (
    'D="$CLAUDE_PROJECT_DIR/docs/pipeline-review"; [ -d "$D" ] || exit 0; '
    'U=$(for f in "$D"/*.md; do [ -f "$f" ] && awk \'NR==1 && !/^---/{exit} NR>1 && /^---/{exit} '
    '/^addressed:[[:space:]]*"?false"?[[:space:]]*(#.*)?$/{print FILENAME; exit}\' "$f"; done); '
    '[ -n "$U" ] || exit 0; '
    "N=$(printf '%s\\n' \"$U\" | wc -l | tr -d ' '); "
    "L=$(printf '%s\\n' \"$U\" | sort | tail -1 | xargs basename | tr -d '\"\\\\'); "
    "M=\"PIPELINE REVIEW ALERT: $N unaddressed pipeline review report(s) in docs/pipeline-review/. "
    "Latest: $L. Before starting any planned work, ask the user whether they want to address these "
    "findings first.\"; "
    "printf '{\"systemMessage\": \"%s\", \"hookSpecificOutput\": {\"hookEventName\": "
    "\"UserPromptSubmit\", \"additionalContext\": \"%s\"}}\\n' \"$M\" \"$M\""
)


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
    entries = (hooks or {}).get(EVENT)
    if entries is not None and not isinstance(entries, list):
        raise ValueError(f'{path}: "hooks.{EVENT}" must be a list')
    return data, raw


def alert_hooks(data):
    """[(entry, hook)] for every registered UserPromptSubmit hook that reads docs/pipeline-review."""
    found = []
    for entry in (data.get("hooks") or {}).get(EVENT) or []:
        if not isinstance(entry, dict):
            continue
        for h in entry.get("hooks") or []:
            if isinstance(h, dict) and MARKER in str(h.get("command", "")):
                found.append((entry, h))
    return found


def register(data):
    """Make the canonical alert entry the only alert registration. Returns True if `data` changed."""
    existing = alert_hooks(data)
    if len(existing) == 1 and existing[0][1].get("command") == COMMAND:
        return False
    entries = data["hooks"][EVENT] if existing else None
    for entry, hook in existing:  # drop stale registrations, keep whatever shared their entry
        entry["hooks"].remove(hook)
    if entries is not None:
        entries[:] = [e for e in entries if not (isinstance(e, dict) and e.get("hooks") == [])]
    data.setdefault("hooks", {}).setdefault(EVENT, []).append(
        {"hooks": [{"type": "command", "command": COMMAND}]}
    )
    return True


def main(argv):
    if len(argv) != 2:
        print("Usage: install_review_alert_hook.py PROJECT_DIR", file=sys.stderr)
        return 2
    project = os.path.realpath(argv[1])
    settings_path = os.path.join(project, ".claude", "settings.json")
    try:
        data, raw = load_settings(settings_path)
    except ValueError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1

    existing = bool(alert_hooks(data))
    if not register(data):
        print("  settings.json already registers the pipeline-review alert — left unchanged")
        return 0

    if raw:
        backup = unique(f"{settings_path}.bak-{time.strftime('%Y%m%d-%H%M%S')}")
        shutil.copy2(settings_path, backup)
        print(f"  backed up settings.json to {os.path.relpath(backup, project)}")
    os.makedirs(os.path.dirname(settings_path), exist_ok=True)
    with open(settings_path, "w", encoding="utf8") as f:
        json.dump(data, f, indent=detect_indent(raw), ensure_ascii=False)
        f.write("\n")
    print(f"  {'updated the' if existing else 'registered the'} {EVENT} pipeline-review alert in .claude/settings.json")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
