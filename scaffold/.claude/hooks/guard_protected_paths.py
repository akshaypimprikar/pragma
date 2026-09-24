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
    sed/perl -i, cp/install/ln (destination, or a destination directory), mv/rm
    (incl. a directory holding protected files, or the repo root and its
    ancestors), git rm/mv, truncate, dd of=, `bash -c '...'` (also -lc and
    similar), `eval`, subshells, `$( )` and backticks (also inside double
    quotes), `if`/`while`/`for` bodies, wrappers (sudo, env, xargs, nice,
    timeout ...), partly quoted words, backslash-newline continuations, and
    `cd dir && ...` (scoped to its subshell; `cd -` and bare `cd` work).
    Protected files are matched at the repo root and under any subdirectory
    (`ios/CLAUDE.md`), and a protected name that is a symlink is caught.
    Not detected: `python -c`, interpreter heredocs, variable/glob expansion
    (including `cd $VAR`), `find -exec/-delete`, `xargs rm` fed from stdin,
    git plumbing (`git checkout <ref> -- file`, `git restore`), removing a
    whole directory that merely contains a nested project (`rm -rf ios`), and
    state that spans commands (`ln -s CLAUDE.md n && echo x > n`; a link that
    already exists is followed, one created in the same command is not).
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
import subprocess
import sys
import tempfile

# Repo-relative globs. fnmatch's `*` also crosses `/`, so nested paths match.
PROTECTED_GLOBS = (
    ".claude/skills/*/SKILL.md",
    "scripts/check_*",
    "AGENTS.md",
    "CLAUDE.md",
    ".claude/context/invariants.md",
    ".claude/settings.json",
    ".claude/hooks/*",
    "CONSTRAINTS.md",
)
GUARDED_BRANCH = re.compile(r"^feature/")
FILE_TOOLS = ("Write", "Edit", "MultiEdit")
WRAPPER_WORDS = ("sudo", "env", "command", "time", "nohup", "exec", "xargs", "nice", "ionice", "timeout", "stdbuf", "setsid")
# Shell keywords that precede a command in the same segment (`then rm x`,
# `do sed -i ...`, `! rm x`, `{ rm x; }`), so the command word is the next token.
SHELL_KEYWORDS = ("if", "then", "elif", "else", "while", "until", "do", "!", "{")
REDIRECT_OPS = ("<", "<<", "<<<", "<&", "<>", ">", ">>", ">&", ">|", "&>", "&>>")
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
    "xargs": {"-I", "-n", "-P", "-L", "-d", "-E", "-s", "-a"},
    "nice": {"-n"},
    "ionice": {"-c", "-n", "-p", "-P", "-u"},
    "timeout": {"-s", "-k", "--signal", "--kill-after"},
}
# Wrappers whose first non-flag argument is not the command (`timeout 5 rm x`: 5 is a duration).
WRAPPER_LEADING_OPERAND = ("timeout",)
# Global `git` options that take a separate argument token.
GIT_ARG_FLAGS = {"-C", "-c", "--git-dir", "--work-tree", "--namespace"}


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
    PROTECTED_GLOBS entry's directory. A `.claude/...` glob also matches when
    `.claude` sits below the repo root (`ios/.claude/skills`)."""
    parts = [p.lower() for p in rel_dir.split("/")]
    for glob_parts in PROTECTED_DIR_PARTS:
        if not glob_parts:
            continue
        starts = [0] + ([i for i, p in enumerate(parts) if i and p == ".claude"] if glob_parts[0] == ".claude" else [])
        for s in starts:
            sub = parts[s:]
            if len(sub) <= len(glob_parts) and all(gp == "*" or gp.lower() == rp for gp, rp in zip(glob_parts, sub)):
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


_PROTECTED_LOWER = tuple(g.lower() for g in PROTECTED_GLOBS)


def matches_protected(rel):
    """True if a repo-relative path matches a protected glob, at the repo root or
    under any subdirectory (a Claude project living in `<repo>/ios/`)."""
    low = rel.lower()
    return any(fnmatch.fnmatchcase(low, g) or fnmatch.fnmatchcase(low, "*/" + g) for g in _PROTECTED_LOWER)


def could_be_protected(abs_path):
    # Also test the symlink-resolved path: a link named `notes.txt` that points
    # at CLAUDE.md contains no fragment itself but writes through to it.
    lows = (abs_path.lower(), os.path.realpath(abs_path).lower())
    return any(frag in low for low in lows for frag in _PROTECTED_FRAGMENTS)


def protected_relpath(abs_path, destructive=False):
    """(repo_root, relpath) if abs_path is a protected file inside a git repo, else None.
    With destructive=True (rm/mv), a directory holding protected files also counts.
    Both spellings are tested: the fully resolved path, and the path with only its
    directory resolved, so a protected name that is itself a symlink to an
    unprotected file (CLAUDE.md -> docs/rules.md) is still caught."""
    if not destructive and not could_be_protected(abs_path):
        return None
    d = nearest_existing_dir(abs_path)
    if not d:
        return None
    root = git(d, "rev-parse", "--show-toplevel")
    if not root:
        return None
    real_root = os.path.realpath(root)
    lexical = os.path.join(os.path.realpath(os.path.dirname(abs_path)), os.path.basename(abs_path))
    for rel in dict.fromkeys(
        os.path.relpath(p, real_root) for p in (os.path.realpath(abs_path), lexical)
    ):
        if destructive and rel == ".":
            return root, rel  # removing the repo root removes every protected file with it
        if matches_protected(rel):  # case-insensitive: macOS volumes are, so claude.md is CLAUDE.md there
            return root, rel
        if destructive and is_protected_dir_prefix(rel):
            return root, rel
    return None


def covers_repo_root(abs_path, cwd):
    """(root, ".") if abs_path is the git root of cwd or one of its ancestors
    (`rm -rf ..`, `rm -rf ~`, `rm -rf /`), else None."""
    root = git(cwd, "rev-parse", "--show-toplevel")
    if not root:
        return None
    real_root, real_path = os.path.realpath(root), os.path.realpath(abs_path)
    try:
        return (root, ".") if os.path.commonpath([real_root, real_path]) == real_path else None
    except ValueError:  # different drives
        return None


def resolve(path, cwd):
    return os.path.normpath(os.path.join(cwd, os.path.expanduser(path)))


def unquote(word):
    """Remove shell quoting from one token, wherever the quotes sit:
    `"CLAUDE".md`, `CLAUDE."md"` and `'a b'/CLAUDE.md` all become plain paths."""
    out, i, quote = [], 0, None
    while i < len(word):
        c = word[i]
        if quote is None:
            if c in "'\"":
                quote = c
            elif c == "\\" and i + 1 < len(word):
                i += 1
                out.append(word[i])
            else:
                out.append(c)
        elif c == quote:
            quote = None
        elif quote == '"' and c == "\\" and i + 1 < len(word) and word[i + 1] in '"\\$`':
            i += 1
            out.append(word[i])
        else:
            out.append(c)
        i += 1
    return "".join(out)


# `<<WORD` / `<<-WORD` / `<<'WORD'`, but not the here-string `<<<`.
HEREDOC = re.compile(r"(?<!<)<<(?!<)-?\s*(['\"]?)([A-Za-z_][A-Za-z_0-9]*)\1")
CONTINUATION = re.compile(r"(?<!\\)(?:\\\\)*\\$")  # an odd number of trailing backslashes


def strip_heredocs_and_newlines(command):
    """Drop heredoc bodies (data, not commands), join backslash-newline
    continuations the way the shell does (`cp x \\\nCLAUDE.md` is one command),
    and turn the remaining newlines into `;`."""
    lines, out, i = command.split("\n"), [], 0
    while i < len(lines):
        line = lines[i]
        i += 1
        while CONTINUATION.search(line) and i < len(lines):
            line = line[:-1] + lines[i]
            i += 1
        out.append(line)
        m = HEREDOC.search(line)
        if not m:
            continue
        prefix = line[: m.start()]
        if prefix.count("((") > prefix.count("))"):
            continue  # `$((1<<n))` is arithmetic, not a heredoc
        # Only treat it as a heredoc if its terminator exists; otherwise nothing
        # is dropped, so a stray `<<word` can't hide the commands after it.
        for j in range(i, len(lines)):
            if lines[j].strip() == m.group(2):
                i = j + 1
                break
    return " ; ".join(out)


OPERATOR_CHARS = "();<>|&"


def tokenize(command):
    """Split a shell command into words and operator runs, keeping quotes on the words.
    (shlex's non-posix mode ends a word at a closing quote, cutting `"CLAUDE".md` in two.)
    Whitespace and ( ) ; < > | & end a word outside quotes; a run of those operator
    characters is one token (`&&`, `>>`, `>&`, `2>&1` is `2`, `>&`, `1`). Backticks
    are rewritten to `$( )`. None if a quote is left open."""
    command = re.sub(r"`([^`]*)`", r"$(\1)", strip_heredocs_and_newlines(command))
    tokens, word, quote, i = [], [], None, 0

    def flush():
        if word:
            tokens.append("".join(word))
            word.clear()

    while i < len(command):
        c = command[i]
        if quote:
            word.append(c)
            if c == "\\" and quote == '"' and i + 1 < len(command):
                i += 1
                word.append(command[i])
            elif c == quote:
                quote = None
        elif c in "'\"":
            quote = c
            word.append(c)
        elif c == "\\" and i + 1 < len(command):
            word.append(c)
            i += 1
            word.append(command[i])
        elif c.isspace():
            flush()
        elif c in OPERATOR_CHARS:
            flush()
            j = i
            while j + 1 < len(command) and command[j + 1] in OPERATOR_CHARS:
                j += 1
            tokens.append(command[i : j + 1])
            i = j
        else:
            word.append(c)
        i += 1
    if quote:
        return None
    flush()
    return tokens


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
        if w in SHELL_KEYWORDS:
            i += 1
            continue
        if w in WRAPPER_WORDS:
            arg_flags = WRAPPER_ARG_FLAGS.get(w, set())
            i += 1
            while i < n and (words[i].startswith("-") or "=" in words[i]):
                if words[i] in arg_flags:
                    i += 1  # also skip this flag's own argument token
                i += 1
            if w in WRAPPER_LEADING_OPERAND and i < n:
                i += 1  # `timeout 5 rm x`: 5 is the duration, not the command
            continue
        return i
    return None


def command_operands(words):
    """The words of one command with redirections removed: each operator, its
    target word and a leading fd number (`2>/dev/null`, `2>&1`, `> /dev/null`).
    Without this a trailing redirect target looks like the last operand and
    hides the real destination of `cp x CLAUDE.md 2>/dev/null`."""
    out, i = [], 0
    while i < len(words):
        w = words[i]
        if w in REDIRECT_OPS:
            i += 2
        elif re.fullmatch(r"\d+", w) and i + 1 < len(words) and words[i + 1] in REDIRECT_OPS:
            i += 1
        else:
            out.append(unquote(w))
            i += 1
    return out


def split_target_operands(operands):
    """(-t DIR or None, positional operands) of a cp/install/ln/mv operand list."""
    target_dir, positional, i = None, [], 0
    while i < len(operands):
        w = operands[i]
        if (w == "--target-directory" or re.fullmatch(r"-[A-Za-z]*t", w)) and i + 1 < len(operands):
            target_dir = operands[i + 1]
            i += 2
            continue
        if w.startswith("--target-directory="):
            target_dir = w.split("=", 1)[1]
        elif not w.startswith("-"):
            positional.append(w)
        i += 1
    return target_dir, positional


def destinations(operands, cur):
    """([destination paths], [source operands]) for cp/install/ln/mv. When the
    destination is a directory (a trailing slash, an existing directory, or -t DIR)
    the files it receives are <dir>/<source name>: a bare directory path matches no
    protected glob, but the file created inside it might."""
    target_dir, positional = split_target_operands(operands)
    if target_dir is not None:
        return [target_dir] + [os.path.join(target_dir, os.path.basename(s)) for s in positional], positional
    if not positional:
        return [], []
    dest, sources = positional[-1], positional[:-1]
    found = [dest]
    if dest.endswith("/") or os.path.isdir(resolve(dest, cur)):
        found += [os.path.join(dest, os.path.basename(s)) for s in sources]
    return found, sources


def git_subcommand(operands):
    """(subcommand, [operands after it], -C dir or None) for a `git ...` command."""
    i, git_dir = 0, None
    while i < len(operands):
        w = operands[i]
        if w.startswith("-"):
            if w in GIT_ARG_FLAGS and i + 1 < len(operands):
                if w == "-C":
                    git_dir = operands[i + 1]
                i += 1
            i += 1
            continue
        return w, operands[i + 1 :], git_dir
    return None, [], git_dir


def command_substitutions(word):
    """Bodies of `$( ... )` inside a word that is not single-quoted (a double-quoted
    string keeps its `$(...)` inside one token; bare ones are split by the tokenizer)."""
    if word.startswith("'") or "$(" not in word:
        return []
    bodies, i = [], 0
    while True:
        i = word.find("$(", i)
        if i < 0:
            return bodies
        depth, j = 1, i + 2
        while j < len(word) and depth:
            depth += {"(": 1, ")": -1}.get(word[j], 0)
            j += 1
        bodies.append(word[i + 2 : j - 1] if depth == 0 else word[i + 2 :])
        i = j


# What a candidate path is subject to (the second element of a target tuple).
WRITE, MOVE_OR_REMOVE_DEST, REMOVE = 0, 1, 2  # REMOVE: rm / git rm / the source of a mv


def split_segments(tokens):
    """Tokens -> [("cmd", words) | ("push",) | ("pop",)]. ; && || | & |& ( ) end a
    segment; ( and ) also open and close a scope, so a `cd` inside `( ... )` or
    `$( ... )` does not leak. Redirect operators (which contain < or >) are not separators."""
    items, segment = [], []
    for t in tokens + [";"]:
        if re.fullmatch(r"[();|&]+", t):
            for ch in t:
                if segment:
                    items.append(("cmd", segment))
                    segment = []
                if ch == "(":
                    items.append(("push",))
                elif ch == ")":
                    items.append(("pop",))
        else:
            segment.append(t)
    return items


def bash_write_targets(command, cwd, _depth=0):
    """Best-effort [(absolute path, WRITE | MOVE_OR_REMOVE_DEST | REMOVE)] a shell command writes to or removes."""
    tokens = tokenize(command)
    if tokens is None:
        return []
    results, cur, prev, stack = [], cwd, None, []  # `cd dir && ...` moves cur for later segments
    for item in split_segments(tokens):
        if item[0] == "push":
            stack.append((cur, prev))
            continue
        if item[0] == "pop":
            if stack:
                cur, prev = stack.pop()
            continue
        words = item[1]
        found = []  # [(path, kind)], resolved against `cur` below
        if _depth < 3:
            for w in words:
                for body in command_substitutions(w):
                    results += bash_write_targets(body, cur, _depth + 1)
        for i, w in enumerate(words):
            if w in (">", ">>", "&>", "&>>", ">|") and i + 1 < len(words):
                found.append((unquote(words[i + 1]), WRITE))
            elif w == ">&" and i + 1 < len(words) and not re.match(r"^(\d+|-)$", words[i + 1]):
                found.append((unquote(words[i + 1]), WRITE))  # `>& file` redirects both streams
        cmd_idx = real_command_index(words)
        if cmd_idx is not None:
            cmd = os.path.basename(unquote(words[cmd_idx]))
            operands = command_operands(words[cmd_idx + 1 :])
            args = [w for w in operands if not w.startswith("-")]
            flags = [w for w in operands if w.startswith("-")]
            if cmd == "cd":
                target = None
                if "-" in operands:  # `cd -` (a lone "-" is an operand, not a flag)
                    target = prev
                elif not args or args[0] == "~":
                    target = resolve("~", cur)
                elif "$" not in args[0]:
                    target = resolve(args[0], cur)
                if target:
                    prev, cur = cur, target
                continue
            if cmd in ("bash", "sh", "zsh", "dash", "ksh") and _depth < 3:
                # -c, or a combined short-flag cluster containing c (-lc, -ic, -xc)
                for j, w in enumerate(operands):
                    if re.fullmatch(r"-[A-Za-z]*c[A-Za-z]*", w):
                        if j + 1 < len(operands):
                            results += bash_write_targets(operands[j + 1], cur, _depth + 1)
                        break
            elif cmd == "eval" and _depth < 3:
                results += bash_write_targets(" ".join(operands), cur, _depth + 1)
            elif cmd == "tee":
                found += [(w, WRITE) for w in args]
            elif cmd in ("sed", "gsed", "perl") and any(
                f.startswith("--in-place") or re.match(r"^-[A-Za-z]*i", f) for f in flags
            ):
                found += [(w, WRITE) for w in args]
            elif cmd in ("cp", "install", "ln"):
                found += [(w, WRITE) for w in destinations(operands, cur)[0]]
            elif cmd == "mv":
                dests, sources = destinations(operands, cur)
                found += [(w, MOVE_OR_REMOVE_DEST) for w in dests] + [(w, REMOVE) for w in sources]
            elif cmd == "rm":
                found += [(w, REMOVE) for w in args]
            elif cmd == "git":
                sub, sub_operands, git_dir = git_subcommand(operands)
                if sub in ("rm", "mv"):
                    under = (lambda w: os.path.join(git_dir, w)) if git_dir else (lambda w: w)
                    if sub == "rm":
                        found += [(under(w), REMOVE) for w in sub_operands if not w.startswith("-")]
                    else:
                        dests, sources = destinations(sub_operands, cur)
                        found += [(under(w), MOVE_OR_REMOVE_DEST) for w in dests] + [(under(w), REMOVE) for w in sources]
            elif cmd == "truncate":
                found += [(w, WRITE) for w in args]
            elif cmd == "dd":
                found += [(w[3:], WRITE) for w in operands if w.startswith("of=")]
        results += [(resolve(path, cur), kind) for path, kind in found]
    return results


def evaluate(payload):
    """Return a block message, or None to allow."""
    tool = payload.get("tool_name", "")
    tool_input = payload.get("tool_input") or {}
    cwd = payload.get("cwd") or os.getcwd()

    if tool in FILE_TOOLS:
        candidates = [(resolve(tool_input["file_path"], cwd), WRITE)] if tool_input.get("file_path") else []
    elif tool == "Bash":
        candidates = bash_write_targets(tool_input.get("command") or "", cwd)
    else:
        return None

    for abs_path, kind in candidates:
        hit = protected_relpath(abs_path, kind != WRITE)
        if not hit and kind == REMOVE:
            hit = covers_repo_root(abs_path, cwd)
        if not hit:
            continue
        root, rel = hit
        branch = git(root, "branch", "--show-current")
        if not branch or not GUARDED_BRANCH.match(branch):
            continue  # detached HEAD or a non-feature branch: allow
        return (
            f"BLOCKED: `{rel}` is, or holds, a gate-definition/guardrail file and the current branch is "
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


def check_ci_list_in_step():
    """PROTECTED_GLOBS must equal the CI-side list in scripts/check_gate_integrity.py
    (GUARDED_PATH_GLOBS plus its scripts/check_*.py entry). Skipped, not failed, where
    that script isn't reachable from this file's location."""
    here = os.path.dirname(os.path.abspath(__file__))
    for up in ("..", "../..", "../../.."):
        script = os.path.normpath(os.path.join(here, up, "scripts", "check_gate_integrity.py"))
        if os.path.isfile(script):
            break
    else:
        print("SKIP  scripts/check_gate_integrity.py not found: cannot compare glob lists")
        return 0
    m = re.search(r"GUARDED_PATH_GLOBS = \((.*?)\n\)", open(script, encoding="utf8").read(), re.S)
    ci = set(re.findall(r'"([^"]+)"', m.group(1))) if m else set()
    hook = set(PROTECTED_GLOBS)
    ok = bool(ci) and ci == hook
    print(f"{'PASS' if ok else 'FAIL'}  hook PROTECTED_GLOBS == check_gate_integrity.py GUARDED_PATH_GLOBS")
    if not ok:
        print(f"      only in hook: {sorted(hook - ci)}   only in CI script: {sorted(ci - hook)}")
    return 0 if ok else 1


def self_test():
    failures = 0
    with tempfile.TemporaryDirectory() as tmp:
        tmp = os.path.realpath(tmp)
        repos = {}
        # The `plus`, `at` and `accent` repos live in directories whose names contain
        # characters an earlier tokenizer split words on (+ @ and non-ASCII).
        for name, branch, dirname in (
            ("feat", "feature/demo", "feat"), ("chore", "chore/demo", "chore"), ("detached", None, "detached"),
            ("linked", "feature/demo", "linked"), ("plus", "feature/demo", "a+b"), ("at", "feature/demo", "x@2"), ("accent", "feature/demo", "caf\u00e9"),
        ):
            d = os.path.join(tmp, dirname)
            os.makedirs(os.path.join(d, ".claude", "skills", "gates"))
            os.makedirs(os.path.join(d, ".claude", "hooks"))
            os.makedirs(os.path.join(d, "scripts"))
            os.makedirs(os.path.join(d, "App"))
            subprocess.run(["git", "-C", d, "init", "-q"], check=True)
            for f in (".claude/skills/gates/SKILL.md", ".claude/hooks/h.py", "scripts/check_x.py", "AGENTS.md", "CLAUDE.md", "CONSTRAINTS.md", "App/A.swift"):
                open(os.path.join(d, f), "w").close()
            os.symlink("CLAUDE.md", os.path.join(d, "notes.txt"))  # an innocent-looking name for a protected file
            os.makedirs(os.path.join(d, "ios", ".claude", "skills", "x"))
            os.makedirs(os.path.join(d, "ios", "Sources"))
            for f in ("ios/CLAUDE.md", "ios/.claude/settings.json", "ios/.claude/skills/x/SKILL.md", "ios/Sources/A.swift"):
                open(os.path.join(d, f), "w").close()
            if name == "linked":  # a protected name that is itself a symlink to an unprotected file
                os.makedirs(os.path.join(d, "docs"))
                open(os.path.join(d, "docs", "rules.md"), "w").close()
                os.remove(os.path.join(d, "CLAUDE.md"))
                os.symlink("docs/rules.md", os.path.join(d, "CLAUDE.md"))
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
            ("feature: Bash cd into a subdir, nested CLAUDE.md is protected too", bash("feat", "cd App && echo x > CLAUDE.md"), True),
            ("feature: Bash cd into a subdir, ordinary file", bash("feat", "cd App && echo x > README.md"), False),
            ("feature: Bash rm of an ordinary dir", bash("feat", "rm -rf App/build"), False),
            # A trailing redirect must not hide the destination operand.
            ("feature: Bash cp then 2>/dev/null", bash("feat", "cp evil.md CLAUDE.md 2>/dev/null"), True),
            ("feature: Bash cp then 2>&1", bash("feat", "cp x CLAUDE.md 2>&1"), True),
            ("feature: Bash cp then > /dev/null", bash("feat", "cp x CLAUDE.md > /dev/null"), True),
            ("feature: Bash cp then &>/dev/null", bash("feat", "cp x CLAUDE.md &>/dev/null"), True),
            ("feature: Bash install -m644 then 2>/dev/null", bash("feat", "install -m644 x scripts/check_x.py 2>/dev/null"), True),
            ("feature: Bash cp to an ordinary file, 2>/dev/null", bash("feat", "cp x App/B.swift 2>/dev/null"), False),
            # git rm / git mv, subshells, shell keywords, xargs, -lc.
            ("feature: Bash git rm", bash("feat", "git rm CLAUDE.md"), True),
            ("feature: Bash git rm -f --cached", bash("feat", "git rm -f --cached CLAUDE.md"), True),
            ("feature: Bash git mv", bash("feat", "git mv CLAUDE.md old.md"), True),
            ("feature: Bash git -C . rm", bash("feat", "git -C . rm CLAUDE.md"), True),
            ("feature: Bash git -c opt=v rm", bash("feat", "git -c core.x=1 rm CLAUDE.md"), True),
            ("feature: Bash git rm of an ordinary file", bash("feat", "git rm App/A.swift"), False),
            ("feature: Bash git status is read-only", bash("feat", "git status CLAUDE.md"), False),
            ("feature: Bash subshell", bash("feat", "(rm CLAUDE.md)"), True),
            ("feature: Bash $( ) body", bash("feat", "echo $(rm CLAUDE.md)"), True),
            ("feature: Bash { } group", bash("feat", "{ rm CLAUDE.md; }"), True),
            ("feature: Bash if/then", bash("feat", "if true; then rm CLAUDE.md; fi"), True),
            ("feature: Bash for/do", bash("feat", "for f in a; do rm CLAUDE.md; done"), True),
            ("feature: Bash while/do sed -i", bash("feat", "while true; do sed -i '' s/a/b/ CLAUDE.md; done"), True),
            ("feature: Bash ! negation", bash("feat", "! rm CLAUDE.md"), True),
            ("feature: Bash bash -lc", bash("feat", "bash -lc 'rm CLAUDE.md'"), True),
            ("feature: Bash sh -ec", bash("feat", "sh -ec 'echo x > CLAUDE.md'"), True),
            ("feature: Bash xargs rm with operand", bash("feat", "xargs rm CLAUDE.md"), True),
            ("feature: Bash xargs -I{} rm on ordinary path", bash("feat", "xargs -I{} rm App/A.swift"), False),
            ("feature: Bash subshell on ordinary path", bash("feat", "(rm App/A.swift)"), False),
            # Symlinks: the write goes through the link to a protected file.
            ("feature: Write through a symlink", write("Write", "feat", "notes.txt"), True),
            ("feature: Bash redirect through a symlink", bash("feat", "echo x > notes.txt"), True),
            # A destination directory: the file lands inside it.
            ("feature: Bash cp into .claude/hooks/", bash("feat", "cp evil.py .claude/hooks/"), True),
            ("feature: Bash cp into existing dir, no slash", bash("feat", "cp evil.py .claude/hooks"), True),
            ("feature: Bash cp into scripts/ (check_ file)", bash("feat", "cp check_x.py scripts/"), True),
            ("feature: Bash cp -t DIR", bash("feat", "cp -t .claude/hooks evil.py"), True),
            ("feature: Bash install -t DIR", bash("feat", "install -t .claude/hooks evil.py"), True),
            ("feature: Bash ln -s into .claude/hooks/", bash("feat", "ln -s /tmp/x .claude/hooks/"), True),
            ("feature: Bash cp into scripts/ (non-check name)", bash("feat", "cp notes.py scripts/"), False),
            ("feature: Bash cp into an ordinary dir", bash("feat", "cp x App/"), False),
            # Removing the repo root or an ancestor removes every protected file with it.
            ("feature: Bash rm -rf .", bash("feat", "rm -rf ."), True),
            ("feature: Bash rm -rf ./", bash("feat", "rm -rf ./"), True),
            ("feature: Bash rm -rf ..", bash("feat", "rm -rf .."), True),
            ("feature: Bash rm -rf /", bash("feat", "rm -rf /"), True),
            ("chore: Bash rm -rf .", bash("chore", "rm -rf ."), False),
            ("feature: Bash rm -rf outside the repo", bash("feat", "rm -rf /tmp/pragma-guard-test-nothing"), False),
        ]
        # Paths whose directory names the tokenizer used to cut apart.
        for repo in ("plus", "at", "accent"):
            cases.append((f"feature[{repo}]: Bash redirect to absolute path", bash(repo, f"echo x > {repos[repo]}/CLAUDE.md"), True))
            cases.append((f"feature[{repo}]: Bash cp to absolute path", bash(repo, f"cp x {repos[repo]}/CLAUDE.md 2>/dev/null"), True))
            cases.append((f"feature[{repo}]: Write to absolute path", write("Write", repo, "CLAUDE.md"), True))
            cases.append((f"feature[{repo}]: Bash ordinary file", bash(repo, f"echo x > {repos[repo]}/App/A.swift"), False))
        cases += [
            # 1. A protected name that is itself a symlink to an unprotected file.
            ("feature: Write CLAUDE.md that is a symlink to docs/rules.md", write("Write", "linked", "CLAUDE.md"), True),
            ("feature: Bash redirect to a symlinked CLAUDE.md", bash("linked", "echo x > CLAUDE.md"), True),
            ("feature: Bash rm a symlinked CLAUDE.md", bash("linked", "rm CLAUDE.md"), True),
            # 2. `<<<` and `$((a<<n))` are not heredocs; a heredoc without a terminator hides nothing.
            ("feature: Bash here-string then a write", bash("feat", "cat <<< foo\necho x > CLAUDE.md"), True),
            ("feature: Bash quoted here-string then a write", bash("feat", "cat <<< \"foo\"\nrm CLAUDE.md"), True),
            ("feature: Bash arithmetic shift then a write", bash("feat", "echo $((1<<n))\nrm CLAUDE.md"), True),
            ("feature: Bash heredoc without terminator hides nothing", bash("feat", "cat <<EOF\nrm CLAUDE.md"), True),
            ("feature: Bash heredoc body is still data", bash("feat", "cat <<EOF\nrm CLAUDE.md\nEOF"), False),
            # 3. cd scope.
            ("feature: Bash cd in a subshell does not leak", bash("feat", "(cd /tmp && ls); echo x > CLAUDE.md"), True),
            ("feature: Bash cd in $( ) does not leak", bash("feat", "echo $(cd App); echo x > CLAUDE.md"), True),
            ("feature: Bash cd - returns", bash("feat", "cd App; cd -; echo x > CLAUDE.md"), True),
            ("feature: Bash bare cd goes home, not the repo", bash("feat", "cd; echo x > CLAUDE.md"), False),
            # 4. Backslash-newline continuation.
            ("feature: Bash cp with a continuation line", bash("feat", "cp evil \\\nCLAUDE.md"), True),
            ("feature: Bash continuation inside the word", bash("feat", "echo x > CLAU\\\nDE.md"), True),
            # 5. eval, backticks, quoted $( ), more wrappers.
            ("feature: Bash eval", bash("feat", "eval \"rm CLAUDE.md\""), True),
            ("feature: Bash backticks", bash("feat", "echo `rm CLAUDE.md`"), True),
            ("feature: Bash $( ) inside double quotes", bash("feat", "echo \"$(rm CLAUDE.md)\""), True),
            ("feature: Bash nice", bash("feat", "nice rm CLAUDE.md"), True),
            ("feature: Bash nice -n 5", bash("feat", "nice -n 5 rm CLAUDE.md"), True),
            ("feature: Bash timeout 5", bash("feat", "timeout 5 rm CLAUDE.md"), True),
            ("feature: Bash timeout -s KILL 5", bash("feat", "timeout -s KILL 5 rm CLAUDE.md"), True),
            ("feature: Bash timeout on an ordinary path", bash("feat", "timeout 5 rm App/A.swift"), False),
            ("feature: Bash single-quoted backticks are data", bash("feat", "echo 'a `rm CLAUDE.md` b'"), False),
            # 6. Partly quoted words.
            ("feature: Bash quoted stem", bash("feat", "echo x > \"CLAUDE\".md"), True),
            ("feature: Bash quoted extension", bash("feat", "echo x > CLAUDE.\"md\""), True),
            ("feature: Bash quoted command word", bash("feat", "\"rm\" CLAUDE.md"), True),
            ("feature: Bash escaped char in the name", bash("feat", "rm CLAUD\\E.md"), True),
            # 7. mv into an ancestor is not a removal; mv of a source that is one is.
            ("feature: Bash mv into the parent dir", bash("feat", "mv build.zip .."), False),
            ("feature: Bash mv -t parent", bash("feat", "mv -t .. build.zip"), False),
            ("feature: Bash mv the repo root away", bash("feat", "mv . /tmp/gone"), True),
            ("feature: Bash git mv into the parent dir", bash("feat", "git mv build.zip .."), False),
            # 9. Check scripts of any extension.
            ("feature: Write scripts/check_lint.sh", write("Write", "feat", "scripts/check_lint.sh"), True),
            ("feature: Bash redirect to scripts/check_foo.rb", bash("feat", "echo x > scripts/check_foo.rb"), True),
            ("feature: Write scripts/build.sh is ordinary", write("Write", "feat", "scripts/build.sh"), False),
            # 10. A Claude project inside a monorepo subdirectory.
            ("feature: Write ios/CLAUDE.md", write("Write", "feat", "ios/CLAUDE.md"), True),
            ("feature: Write ios/.claude/settings.json", write("Write", "feat", "ios/.claude/settings.json"), True),
            ("feature: Write ios/.claude/skills/x/SKILL.md", write("Write", "feat", "ios/.claude/skills/x/SKILL.md"), True),
            ("feature: Bash rm -rf ios/.claude/skills", bash("feat", "rm -rf ios/.claude/skills"), True),
            ("feature: Bash rm -rf ios/.claude", bash("feat", "rm -rf ios/.claude"), True),
            ("feature: Write ios/Sources/A.swift is ordinary", write("Write", "feat", "ios/Sources/A.swift"), False),
        ]
        cases.append(("feature: Bash rm -rf of the directory holding the repo", bash("feat", f"rm -rf {tmp}"), True))
        cases.append(("feature: Bash `a#b` word keeps the redirect", bash("feat", "echo a#b > CLAUDE.md"), True))
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

    failures += check_ci_list_in_step()
    print(f"\n{failures} failure(s)")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(self_test() if "--self-test" in sys.argv[1:] else main())
