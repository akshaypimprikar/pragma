#!/usr/bin/env python3
"""
PreToolUse guard: blocks Write/Edit/MultiEdit (and, best-effort, Bash writes)
against gate-definition / guardrail files while the current git branch is
feature/*. Same branch semantics as scripts/check_gate_integrity.py, which
only fires on feature/* — a real feature never needs to change what counts as
passing; that belongs on a chore/* or fix/* branch.

Hook contract (Claude Code hooks reference, code.claude.com/docs/en/hooks):
  stdin  JSON with tool_name, tool_input (file_path, or command for Bash), cwd
  exit 2 blocks the tool call and feeds stderr back to Claude
  exit 0 allows; any other exit code is a non-blocking error (tool proceeds)

PROTECTED_GLOBS must stay in step with GUARDED_PATH_GLOBS in
scripts/check_gate_integrity.py: this hook blocks the edit live, that script
catches the same files on a plain commit that never went through Claude Code.

Limits (deliberate):
  - Bash detection is a best-effort parse: redirects (> >> &> >| >&), tee,
    sed/perl -i, cp/install/ln (destination), mv/rm (incl. a directory holding
    protected files), truncate, dd of=, `bash -c '...'` and `cd dir && ...`.
    `python -c`, interpreter heredocs, variable/glob expansion, git plumbing
    (`git checkout <ref> -- file`) and the like are not detected.
  - This file and its settings.json entry are protected only on feature/*:
    editable on any other branch, and a chore/* edit is not blocked at all.
    `.claude/settings.local.json` is not on the protected list.
  - Fails open: bad stdin, no git repo, or a detached HEAD allow the call.

Usage: guard_protected_paths.py            (reads hook JSON on stdin)
       guard_protected_paths.py --self-test
"""
import fnmatch
import json
import os
import re
import shlex
import subprocess
import sys
import tempfile

# Repo-relative globs. fnmatch's `*` also crosses `/`, so nested paths match.
PROTECTED_GLOBS = (
    ".claude/skills/*/SKILL.md",
    "scripts/check_*.py",
    "AGENTS.md",
    "CLAUDE.md",
    ".claude/context/invariants.md",
    ".claude/settings.json",
    ".claude/hooks/*",
    "CONSTRAINTS.md",
)
GUARDED_BRANCH = re.compile(r"^feature/")
FILE_TOOLS = ("Write", "Edit", "MultiEdit")
WRAPPER_WORDS = ("sudo", "env", "command", "time", "nohup", "exec")
# Per-wrapper flags that consume a following argument token (not just the
# flag itself) — e.g. `-u` in `sudo -u foo` or `exec -a name`. Keyed by
# wrapper word, not a single flat set: `-p` takes an argument for sudo
# (custom prompt) but is a bare no-arg flag for `time -p`/`command -p` —
# a flat set would misidentify `time -p rm CLAUDE.md`'s "rm" as -p's
# argument and never reach the real command at all. A wrapper with no
# entry here (command/time/nohup) has no argument-taking flags.
WRAPPER_ARG_FLAGS = {
    "sudo": {"-u", "-g", "-p", "-h", "-r", "-t", "-C", "-a"},
    "env": {"-u", "-C", "-S"},
    "exec": {"-a"},
}


def git(cwd, *args):
    try:
        out = subprocess.run(
            ("git", "-C", cwd) + args, capture_output=True, text=True, check=True
        ).stdout
        return out.strip()
    except (subprocess.CalledProcessError, OSError):
        return None


def nearest_existing_dir(path):
    d = os.path.dirname(path)
    while d and not os.path.isdir(d):
        parent = os.path.dirname(d)
        if parent == d:
            break
        d = parent
    return d if os.path.isdir(d) else None


# Directory-component lists (glob's dirname portion, "*" kept as a wildcard
# component — not flattened to a literal string) for the destructive-removal
# check below. A plain string-membership set can't handle a glob like
# ".claude/skills/*/SKILL.md" whose wildcard sits mid-path, not at the
# filename: "rm -rf .claude/skills/gates" must still match "*" against the
# real directory name "gates", not against the literal three-character
# string "*".
PROTECTED_DIR_PARTS = [_g.split("/")[:-1] for _g in PROTECTED_GLOBS]


def is_protected_dir_prefix(rel_dir):
    """True if removing/moving rel_dir (a directory) would necessarily take a
    protected file with it — rel_dir is itself, or an ancestor of, some
    PROTECTED_GLOBS entry's directory."""
    rel_parts = [p.lower() for p in rel_dir.split("/")]
    for glob_parts in PROTECTED_DIR_PARTS:
        if not glob_parts or len(rel_parts) > len(glob_parts):
            continue
        if all(
            gp == "*" or gp.lower() == rp
            for gp, rp in zip(glob_parts, rel_parts)
        ):
            return True
    return False


# Cheap substring fragments that must appear somewhere in a path for it to
# possibly match a PROTECTED_GLOBS entry — checked before any subprocess.
# Not a full match test (fnmatch below still does that precisely); this is
# purely an early-exit so ordinary Write/Edit/MultiEdit calls (the vast
# majority in any session — this hook fires on every one) skip the git
# rev-parse + directory walk entirely instead of paying that cost on every
# single edit regardless of what it touches. Only applied to the
# non-destructive path: a destructive rm/mv of a protected *directory*
# (".claude/skills/gates", not "SKILL.md") doesn't contain any of these
# fragments in its own path, so that check must still go through in full —
# destructive calls (mv/rm) are a small minority of writes, unlike
# Write/Edit/MultiEdit and tee/redirect/sed-i/cp, so this still captures
# the dominant cost.
_PROTECTED_FRAGMENTS = tuple(
    frag.lower() for frag in (
        "skill.md", "scripts/check_", "agents.md", "claude.md",
        "invariants.md", "settings.json", ".claude/hooks/", "constraints.md",
    )
)


def could_be_protected(abs_path):
    low = abs_path.lower()
    return any(frag in low for frag in _PROTECTED_FRAGMENTS)


def protected_relpath(abs_path, destructive=False):
    """(repo_root, relpath) if abs_path is a protected file inside a git repo, else None.
    With destructive=True (rm/mv), a directory holding protected files also counts."""
    if not destructive and not could_be_protected(abs_path):
        return None
    d = nearest_existing_dir(abs_path)
    if not d:
        return None
    root = git(d, "rev-parse", "--show-toplevel")
    if not root:
        return None
    rel = os.path.relpath(os.path.realpath(abs_path), os.path.realpath(root))
    # macOS volumes are case-insensitive by default: claude.md is CLAUDE.md there.
    for glob in PROTECTED_GLOBS:
        if fnmatch.fnmatchcase(rel.lower(), glob.lower()):
            return root, rel
    if destructive and is_protected_dir_prefix(rel):
        return root, rel
    return None


def resolve(path, cwd):
    return os.path.normpath(os.path.join(cwd, os.path.expanduser(path)))


def unquote(word):
    if len(word) >= 2 and word[0] == word[-1] and word[0] in "'\"":
        return word[1:-1]
    return word


def strip_heredocs_and_newlines(command):
    """Drop heredoc bodies (data, not commands); turn unquoted newlines into `;`."""
    lines, out, delim = command.split("\n"), [], None
    for line in lines:
        if delim is not None:
            if line.strip() == delim:
                delim = None
            continue
        out.append(line)
        m = re.search(r"<<-?\s*(['\"]?)([A-Za-z_][A-Za-z_0-9]*)\1", line)
        if m:
            delim = m.group(2)
    return " ; ".join(out)


def tokenize(command):
    # Non-posix keeps quotes on tokens, so a quoted '>' stays distinguishable from a redirect.
    try:
        lex = shlex.shlex(strip_heredocs_and_newlines(command), posix=False, punctuation_chars=True)
        lex.whitespace_split = False
        return list(lex)
    except ValueError:
        return None


def real_command_index(words):
    """Index of the actual command token in one `;`/`&&`-separated segment,
    skipping past wrapper words (sudo/env/command/time/nohup/exec) AND their
    own flags and flag-arguments — not just the wrapper word itself. A
    single `w not in WRAPPER_WORDS` check stops at the first token after a
    wrapper word regardless of what it is, so `sudo -u foo rm CLAUDE.md`
    picked up "-u" as the command and never looked at `rm`; verified by
    direct reproduction. Returns None if every token is a wrapper/flag/
    assignment (nothing left to be a real command)."""
    i, n = 0, len(words)
    while i < n:
        w = words[i]
        if "=" in w:
            i += 1  # a VAR=val prefix (env-style or a literal assignment)
            continue
        if w in WRAPPER_WORDS:
            arg_flags = WRAPPER_ARG_FLAGS.get(w, set())
            i += 1
            while i < n and (words[i].startswith("-") or "=" in words[i]):
                if words[i] in arg_flags:
                    i += 1  # also skip this flag's own argument token
                i += 1
            continue
        return i
    return None


def bash_write_targets(command, cwd, _depth=0):
    """Best-effort [(absolute path, is_delete_or_move)] a shell command writes to or removes."""
    tokens = tokenize(command)
    if tokens is None:
        return []
    segments, segment = [], []
    for t in tokens + [";"]:
        if t in (";", "&&", "||", "|", "&", "|&"):
            segments.append(segment)
            segment = []
        else:
            segment.append(t)
    results, cur = [], cwd  # `cd dir && ...` moves cur for later segments
    for words in segments:
        found, destructive = [], False
        for i, w in enumerate(words):
            if w in (">", ">>", "&>", "&>>", ">|") and i + 1 < len(words):
                found.append(unquote(words[i + 1]))
            elif w == ">&" and i + 1 < len(words) and not re.match(r"^(\d+|-)$", words[i + 1]):
                found.append(unquote(words[i + 1]))  # `>& file` redirects both streams
        cmd_idx = real_command_index(words)
        if cmd_idx is not None:
            cmd = os.path.basename(unquote(words[cmd_idx]))
            rest = [unquote(w) for w in words[cmd_idx + 1 :]]
            args = [w for w in rest if not w.startswith("-") and not re.match(r"^[<>&|]", w)]
            flags = [w for w in rest if w.startswith("-")]
            if cmd == "cd":
                if args:
                    cur = resolve(args[0], cur)
                continue
            if cmd in ("bash", "sh", "zsh") and "-c" in rest and _depth < 3:
                idx = rest.index("-c")
                if idx + 1 < len(rest):
                    results += bash_write_targets(rest[idx + 1], cur, _depth + 1)
            elif cmd == "tee":
                found += args
            elif cmd in ("sed", "gsed", "perl") and any(
                f.startswith("--in-place") or re.match(r"^-[A-Za-z]*i", f) for f in flags
            ):
                found += args
            elif cmd in ("cp", "install", "ln") and args:
                found.append(args[-1])
            elif cmd in ("mv", "rm"):
                found += args
                destructive = True
            elif cmd == "truncate":
                found += args
            elif cmd == "dd":
                found += [w[3:] for w in rest if w.startswith("of=")]
        results += [(resolve(t, cur), destructive) for t in found]
    return results


def evaluate(payload):
    """Return a block message, or None to allow."""
    tool = payload.get("tool_name", "")
    tool_input = payload.get("tool_input") or {}
    cwd = payload.get("cwd") or os.getcwd()

    if tool in FILE_TOOLS:
        candidates = [(resolve(tool_input["file_path"], cwd), False)] if tool_input.get("file_path") else []
    elif tool == "Bash":
        candidates = bash_write_targets(tool_input.get("command") or "", cwd)
    else:
        return None

    for abs_path, destructive in candidates:
        hit = protected_relpath(abs_path, destructive)
        if not hit:
            continue
        root, rel = hit
        branch = git(root, "branch", "--show-current")
        if not branch or not GUARDED_BRANCH.match(branch):
            continue  # detached HEAD or a non-feature branch: allow
        return (
            f"BLOCKED: `{rel}` is a gate-definition/guardrail file and the current branch is "
            f"`{branch}` (feature/*). A feature branch must not change what counts as passing. "
            "Remedy: make this change on a chore/* or fix/* branch in its own PR, then rebase "
            "this feature branch onto it. If the edit really belongs to this feature, stop and "
            "ask the user instead of editing around the guard."
        )
    return None


def main():
    try:
        payload = json.load(sys.stdin)
        message = evaluate(payload)
    except Exception:  # fail open: a broken guard must not wedge every tool call
        return 0
    if message:
        print(message, file=sys.stderr)
        return 2
    return 0


def self_test():
    failures = 0
    with tempfile.TemporaryDirectory() as tmp:
        tmp = os.path.realpath(tmp)
        repos = {}
        for name, branch in (("feat", "feature/demo"), ("chore", "chore/demo"), ("detached", None)):
            d = os.path.join(tmp, name)
            os.makedirs(os.path.join(d, ".claude", "skills", "gates"))
            os.makedirs(os.path.join(d, "scripts"))
            os.makedirs(os.path.join(d, "App"))
            subprocess.run(["git", "-C", d, "init", "-q"], check=True)
            for f in (".claude/skills/gates/SKILL.md", "scripts/check_x.py", "AGENTS.md", "CLAUDE.md", "CONSTRAINTS.md", "App/A.swift"):
                open(os.path.join(d, f), "w").close()
            subprocess.run(["git", "-C", d, "add", "-A"], check=True)
            subprocess.run(
                ["git", "-C", d, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "i"],
                check=True,
            )
            if branch:
                subprocess.run(["git", "-C", d, "checkout", "-q", "-B", branch], check=True)
            else:
                subprocess.run(["git", "-C", d, "checkout", "-q", "--detach"], check=True)
            repos[name] = d

        def write(tool, repo, rel):
            return {"tool_name": tool, "cwd": repos[repo], "tool_input": {"file_path": os.path.join(repos[repo], rel)}}

        def bash(repo, cmd):
            return {"tool_name": "Bash", "cwd": repos[repo], "tool_input": {"command": cmd}}

        cases = [
            # (label, payload, expect_block)
            ("feature: Write gates SKILL.md", write("Write", "feat", ".claude/skills/gates/SKILL.md"), True),
            ("feature: Edit gates SKILL.md", write("Edit", "feat", ".claude/skills/gates/SKILL.md"), True),
            ("feature: MultiEdit CLAUDE.md", write("MultiEdit", "feat", "CLAUDE.md"), True),
            ("feature: Write AGENTS.md", write("Write", "feat", "AGENTS.md"), True),
            ("feature: Write CLAUDE.md, wrong case", write("Write", "feat", "claude.md"), True),
            ("feature: Write scripts/check_x.py", write("Write", "feat", "scripts/check_x.py"), True),
            ("feature: Write .claude/settings.json (new)", write("Write", "feat", ".claude/settings.json"), True),
            ("feature: Write .claude/hooks/new.py (new dir)", write("Write", "feat", ".claude/hooks/new.py"), True),
            ("feature: Write invariants.md (new dirs)", write("Write", "feat", ".claude/context/invariants.md"), True),
            ("feature: Write CONSTRAINTS.md", write("Write", "feat", "CONSTRAINTS.md"), True),
            ("feature: Write ordinary .swift", write("Write", "feat", "App/A.swift"), False),
            ("feature: Read tool is ignored", write("Read", "feat", "CLAUDE.md"), False),
            ("chore: Write gates SKILL.md", write("Write", "chore", ".claude/skills/gates/SKILL.md"), False),
            ("chore: Edit CLAUDE.md", write("Edit", "chore", "CLAUDE.md"), False),
            ("detached HEAD: Write gates SKILL.md (fail open)", write("Write", "detached", ".claude/skills/gates/SKILL.md"), False),
            ("feature: Bash redirect >", bash("feat", "echo x > .claude/skills/gates/SKILL.md"), True),
            ("feature: Bash append >>", bash("feat", "echo x >> CLAUDE.md"), True),
            ("feature: Bash cat > file <<EOF", bash("feat", "cat > CLAUDE.md <<'EOF'\nhello\nEOF"), True),
            ("feature: Bash tee", bash("feat", "echo x | tee -a scripts/check_x.py"), True),
            ("feature: Bash sed -i", bash("feat", "sed -i '' 's/a/b/' .claude/skills/gates/SKILL.md"), True),
            ("feature: Bash chained after &&", bash("feat", "ls && echo x>CLAUDE.md"), True),
            ("feature: Bash mv onto protected", bash("feat", "mv /tmp/x scripts/check_x.py"), True),
            ("feature: Bash rm protected", bash("feat", "rm CLAUDE.md"), True),
            ("feature: Bash rm AGENTS.md", bash("feat", "rm AGENTS.md"), True),
            ("feature: Bash read-only cat", bash("feat", "cat CLAUDE.md"), False),
            ("feature: Bash protected path as SOURCE of redirect", bash("feat", "cat CLAUDE.md > /tmp/out.txt"), False),
            ("feature: Bash 2>&1 is not a redirect target", bash("feat", "ls CLAUDE.md 2>&1"), False),
            ("feature: Bash sed without -i", bash("feat", "sed 's/a/b/' CLAUDE.md"), False),
            ("feature: Bash redirect to ordinary file", bash("feat", "echo x > App/A.swift"), False),
            ("chore: Bash redirect", bash("chore", "echo x > CLAUDE.md"), False),
            ("feature: Bash cd into dir then redirect", bash("feat", "cd .claude/skills/gates && echo x > SKILL.md"), True),
            ("feature: Bash bash -c redirect", bash("feat", "bash -c 'echo x > CLAUDE.md'"), True),
            ("feature: Bash sudo -u foo rm (wrapper flag+arg)", bash("feat", "sudo -u foo rm CLAUDE.md"), True),
            ("feature: Bash env -i rm (wrapper flag)", bash("feat", "env -i rm CLAUDE.md"), True),
            ("feature: Bash env VAR=val rm (wrapper assignment)", bash("feat", "env VAR=val rm CLAUDE.md"), True),
            ("feature: Bash sudo rm, no flags (already worked)", bash("feat", "sudo rm CLAUDE.md"), True),
            ("feature: Bash time -p rm (no-arg -p for time)", bash("feat", "time -p rm CLAUDE.md"), True),
            ("feature: Bash command -p rm (no-arg -p for command)", bash("feat", "command -p rm CLAUDE.md"), True),
            ("feature: Bash sudo -p prompt rm (arg-taking -p for sudo)", bash("feat", "sudo -p prompt rm CLAUDE.md"), True),
            ("feature: Bash sed -i with quoted |", bash("feat", "sed -i 's/a|b/c/' CLAUDE.md"), True),
            ("feature: Bash rm -rf protected dir", bash("feat", "rm -rf .claude/skills/gates"), True),
            ("feature: Bash mv protected dir away", bash("feat", "mv scripts /tmp/s"), True),
            ("feature: Bash >| clobber", bash("feat", "echo x >| CLAUDE.md"), True),
            ("feature: Bash >& file", bash("feat", "echo x >& CLAUDE.md"), True),
            ("feature: Bash truncate", bash("feat", "truncate -s 0 CLAUDE.md"), True),
            ("feature: Bash dd of=", bash("feat", "dd if=/dev/null of=CLAUDE.md"), True),
            ("feature: Bash grep '>' file is read-only", bash("feat", "grep '>' CLAUDE.md"), False),
            ("feature: Bash heredoc body mentioning rm CLAUDE.md", bash("feat", "cat > /tmp/n.md <<'EOF'\nrm CLAUDE.md\nEOF"), False),
            ("feature: Bash cd elsewhere then redirect to same-named ordinary file", bash("feat", "cd App && echo x > CLAUDE.md"), False),
            ("feature: Bash rm of an ordinary dir", bash("feat", "rm -rf App/build"), False),
        ]
        for label, payload, expect_block in cases:
            proc = subprocess.run(
                [sys.executable, os.path.abspath(__file__)],
                input=json.dumps(payload), capture_output=True, text=True,
            )
            blocked = proc.returncode == 2
            ok = blocked == expect_block and (not blocked or "BLOCKED" in proc.stderr)
            failures += 0 if ok else 1
            print(f"{'PASS' if ok else 'FAIL'}  exit={proc.returncode}  {label}")

        for label, stdin in (("malformed JSON fails open", "not json"), ("empty stdin fails open", "")):
            proc = subprocess.run(
                [sys.executable, os.path.abspath(__file__)], input=stdin, capture_output=True, text=True
            )
            ok = proc.returncode == 0
            failures += 0 if ok else 1
            print(f"{'PASS' if ok else 'FAIL'}  exit={proc.returncode}  {label}")

    print(f"\n{failures} failure(s)")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(self_test() if "--self-test" in sys.argv[1:] else main())
