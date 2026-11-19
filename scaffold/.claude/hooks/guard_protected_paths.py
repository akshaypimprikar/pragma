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

Codex CLI runs the same script from .codex/hooks.json (Codex hooks docs,
learn.chatgpt.com/docs/hooks): same stdin fields and the same exit 2. Codex
reports shell commands as Bash, and file edits as tool_name `apply_patch` with
the patch text in tool_input.command; its `*** Add/Update/Delete File:` and
`*** Move to:` paths are the edit targets.

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
    `cd dir && ...` (scoped to its subshell; `cd -`, bare `cd`, `pushd` and `popd` work).
    Protected files are matched at the repo root and under any subdirectory
    (`ios/CLAUDE.md`), and a protected name that is a symlink is caught.
    `git apply`, `git am` and `patch` (#200) are not parsed for targets. Like a
    shell-run apply_patch (#140), a command whose text names one is blocked
    when a directory it may run in is on feature/*, unless every command in it
    is read-only (runs_diff_tool()). Those directories are the session's, each
    `cd` target, `git -C`/`--git-dir`/`--work-tree`, `patch -d`, and any path
    named in the text, quoted strings included (named_dirs()), plus the folder
    of every name in a diff header, read from the command and from files it
    names (diff_header_dirs()); each is checked as written and with symlinks
    followed. A `cd` the hook can't work out blocks such a command on every
    branch (cd_dirs()), and so does any `$` or backtick outside single quotes
    in it (diff_header_dirs()).
    Not detected: `python -c`, interpreter heredocs, variable/glob expansion
    (including `cd $VAR` before a write), a patch tool run under a name built
    at run time or from a script file, a git alias set outside the command,
    a directory that reaches a patch tool only through a pipe, file or
    variable, a diff the hook can't read (`curl ... | patch`), `find -exec/-delete`,
    `xargs rm` fed from stdin,
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
import codecs
import fnmatch
import json
import os
import re
import subprocess
import sys
import tempfile
import time

# Repo-relative globs. fnmatch's `*` also crosses `/`, so nested paths match.
PROTECTED_GLOBS = (
    ".claude/skills/*/SKILL.md",
    ".claude/skills/*/reference.md",
    "scripts/check_*",
    "AGENTS.md",
    "CLAUDE.md",
    ".claude/context/invariants.md",
    ".claude/settings.json",
    ".claude/hooks/*",
    "CONSTRAINTS.md",
    "scripts/pipeline_lanes.json",
    "scripts/detect_build_target.sh",
    "scripts/select_simulator.py",
    ".github/workflows/*",
    ".codex/hooks.json",
    ".codex/config.toml",
)
GUARDED_BRANCH = re.compile(r"^feature/")
FILE_TOOLS = ("Write", "Edit", "MultiEdit")
# Codex trims each patch line before reading a header, so indentation must not hide one.
PATCH_TARGET = re.compile(r"^\s*\*\*\*\s*(?:Add File|Update File|Delete File|Move to):\s*(.+?)\s*$", re.MULTILINE)
# Codex runs a shell-invoked patch as apply_patch or applypatch; a patch body also gives one away.
SHELL_PATCH = re.compile(r"apply_?patch|\*\*\*\s*Begin Patch")
WRAPPER_WORDS = ("sudo", "env", "command", "time", "nohup", "exec", "xargs", "nice", "ionice", "timeout", "stdbuf", "setsid",
                 "builtin", "busybox", "flock", "chroot", "arch", "caffeinate", "doas", "xcrun", "strace", "runuser",
                 "noglob", "nocorrect")
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
    "sudo": {"-u", "-g", "-p", "-h", "-r", "-t", "-C", "-D", "-a", "--user", "--group", "--prompt", "--host",
             "--role", "--type", "--chdir", "--close-from"},
    "env": {"-u", "-C", "-S", "--unset", "--chdir", "--split-string"},
    "exec": {"-a"},
    "xargs": {"-I", "-n", "-P", "-L", "-d", "-E", "-s", "-a", "--max-args", "--max-procs", "--delimiter",
              "--arg-file", "--eof", "--max-chars", "--max-lines"},
    "nice": {"-n", "--adjustment"},
    "ionice": {"-c", "-n", "-p", "-P", "-u", "--class", "--classdata", "--pid", "--pgid", "--uid"},
    "timeout": {"-s", "-k", "--signal", "--kill-after"},
    "flock": {"-w", "-E", "--timeout", "--conflict-exit-code"},
    "doas": {"-u", "-C"},
    "xcrun": {"-sdk", "--sdk", "--toolchain"},
    "strace": {"-o", "-e", "-p", "-s", "-u", "-E"},
    "runuser": {"-u", "-g", "-G", "--user", "--group"},
}
# Wrappers whose first non-flag argument is not the command (`timeout 5 rm x`: 5 is a duration).
WRAPPER_LEADING_OPERAND = ("timeout", "flock", "chroot")  # a duration, a lock file, a new root
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
    PROTECTED_GLOBS entry's directory. A `.claude/...` or `.github/...` glob
    also matches when that directory sits below the repo root
    (`ios/.claude/skills`, `ios/.github/workflows`)."""
    parts = [p.lower() for p in rel_dir.split("/")]
    for glob_parts in PROTECTED_DIR_PARTS:
        if not glob_parts:
            continue
        starts = [0] + ([i for i, p in enumerate(parts) if i and p == glob_parts[0]] if glob_parts[0] in (".claude", ".github") else [])
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
        "skill.md", "/reference.md", "scripts/check_", "agents.md", "claude.md",
        "invariants.md", "settings.json", ".claude/hooks/", "constraints.md",
        "pipeline_lanes.json", "detect_build_target.sh", "select_simulator.py", ".github/workflows/", ".codex/hooks.json", ".codex/config.toml",
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


MAX_CHECK_SECONDS = 20  # a patch-tool directory check past this blocks; the hook timeout is 60 s and a timeout fails open
CHECK_DEADLINE = [None]  # time.monotonic() deadline, set by evaluate() on each call and again before the patch-tool directory checks
REALPATHS = {}


class CheckTimeout(Exception):
    pass


def start_deadline():
    CHECK_DEADLINE[0] = time.monotonic() + MAX_CHECK_SECONDS


def check_deadline():
    """Raise CheckTimeout past CHECK_DEADLINE: the item caps don't weigh path depth, and a deep tree made the checks
    run past the hook timeout, which fails open (#196 confirm on acdbbd8)."""
    if CHECK_DEADLINE[0] is not None and time.monotonic() > CHECK_DEADLINE[0]:
        raise CheckTimeout


def timeout_message(tool_name):
    return (
        f"BLOCKED: this command runs, or may run, {tool_name}, and checking the directories it names took "
        f"over {MAX_CHECK_SECONDS} s (deep folders, many words or diff names), so it was not checked (a hook "
        "that times out fails open; this applies on every branch). Split it, or run it from a shallower folder."
    )


def resolve_both(path, cwd):
    """resolve() and the path the kernel reaches with symlinks followed: `lnk/..` is the link target's parent, not
    `cwd` (FT#234 review). A patch tool's directories are checked both ways."""
    check_deadline()
    joined = os.path.join(cwd, os.path.expanduser(path))
    if joined not in REALPATHS:
        REALPATHS[joined] = os.path.realpath(joined)
    return [resolve(path, cwd), REALPATHS[joined]]


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
# One operator per match, longest first. A run such as `;>`, `&&>`, `|>` or `(>` is a
# separator followed by a redirect; treating the whole run as one token hid the redirect.
OPERATOR = re.compile(r"&>>|&>|<<<|<<|<&|<>|>>|>&|>\||>|<|&&|\|\||\|&|;;|;|\||&|\(|\)")


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
            tokens += OPERATOR.findall(command[i : j + 1])
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
        base = os.path.basename(unquote(w))  # `/usr/bin/env` is the env wrapper
        if w in ("function", "coproc"):
            # `function f { ...`: skip the name too. `coproc NAME { ...`: NAME only when a `{` follows.
            i += 2 if w == "function" or (i + 2 < n and words[i + 2] == "{") else 1
            continue
        if base == "command":
            opts = []
            for x in words[i + 1 :]:
                if x == "--" or not x.startswith("-"):
                    break
                opts.append(x)
            if any(re.search(r"[vV]", x) for x in opts):
                return None  # `command -v x` / `command -V x` look a command up, they do not run it
        if w in REDIRECT_OPS:
            i += 2  # a leading redirection: `>/dev/null rm x`, `</dev/null sed -i ...`
            continue
        if re.fullmatch(r"\d+", w) and i + 1 < n and words[i + 1] in REDIRECT_OPS:
            i += 1  # its fd number: `2>/dev/null rm x`
            continue
        if "=" in w and not re.match(r"[\"']?\$\{", w):
            # a VAR=val prefix (env-style or a literal assignment; `a[$(echo 1)]=1` reaches here split at `(`);
            # zsh `${=A:-git}` is a command word (#196 confirm A on d5e89cd)
            i += 1
            continue
        if w in SHELL_KEYWORDS:
            i += 1
            continue
        if base in WRAPPER_WORDS:
            arg_flags = WRAPPER_ARG_FLAGS.get(base, set())
            i += 1
            while i < n and (words[i].startswith("-") or "=" in words[i]):
                if words[i] in arg_flags:
                    i += 1  # also skip this flag's own argument token
                i += 1
            if base in WRAPPER_LEADING_OPERAND and i < n:
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
    """Bodies of `$( ... )` in a word, wherever they sit: bare, inside double quotes, or
    in a later part of a partly quoted word (`'x'"$(rm f)"`). One inside single quotes
    is data, not a command. (Bare `$(` is normally split off by the tokenizer.)"""
    if "$(" not in word:
        return []
    bodies, i, quote = [], 0, None
    while i < len(word):
        c = word[i]
        if c == "\\" and quote != "'" and i + 1 < len(word):
            i += 2
            continue
        if c in "'\"" and quote in (None, c):
            quote = None if quote else c
        elif quote != "'" and word.startswith("$(", i):
            depth, j = 1, i + 2
            while j < len(word) and depth:
                depth += {"(": 1, ")": -1}.get(word[j], 0)
                j += 1
            bodies.append(word[i + 2 : j - 1] if depth == 0 else word[i + 2 :])
            i = j
            continue
        i += 1
    return bodies


# What a candidate path is subject to (the second element of a target tuple).
WRITE, MOVE_OR_REMOVE_DEST, REMOVE = 0, 1, 2  # REMOVE: rm / git rm / the source of a mv
# `git apply`, `git am` and `patch` (#200) follow pragma #140's model for a shell-run apply_patch: on feature/*, a
# command whose text names one (names_diff_tool()) is blocked unless every command in it is read-only
# (read_only_segment()). The hook does not read the diff. The read-only forms, with nothing else on the command:
GIT_APPLY_READ_ONLY = ("--check", "--stat", "--numstat", "--summary")  # at least one of these: nothing is applied
GIT_APPLY_SAFE_ARGS = GIT_APPLY_READ_ONLY + ("-v", "--verbose", "-R", "--reverse", "-z")  # also `-pN` and file names
# allowed, though not read-only: `--abort` resets HEAD and `--quit` drops .git/rebase-apply; neither applies a diff
GIT_AM_ALLOWED = ("--abort", "--quit", "--show-current-patch", "--show-current-patch=diff", "--show-current-patch=raw")
HELP_ONLY = (["-h"], ["--help"])
PATCH_SHORT_ARG = "BDdFioprVYz"  # `patch` short options that take a value (GNU and BSD): `-sd DIR` sets -d
SHELLS = ("bash", "sh", "zsh", "dash", "ksh", "mksh", "yash", "ash", "fish", "csh", "tcsh")
# The tool names, in any case (macOS paths are case-insensitive): `patch`, `gpatch`, `git-apply`, `git-am`, or a `git`
# followed later in the same command by an `apply` or `am` word (which also catches an alias defined on the way).
DIFF_TOOL_NAME = re.compile(r"(?i)(?<![\w.-])(?:g?patch|git-(?:apply|am))(?![\w.-])"
                            r"|(?<![\w.-])git(?=\s)[^;&|\n]*?(?<![\w.-])(?:apply|am)(?![\w.-])")
STASH_APPLY = re.compile(r"(?i)(?<![\w.-])stash\s+apply(?![\w.-])")  # `git stash apply` restores a stash, not a diff
# Global git options that cannot run an alias or another program. Any other one before the subcommand
# (`--config-env`, `--exec-path=`, `--attr-source`) counts as a possible run.
KNOWN_GIT_GLOBALS = ("-C", "-c", "--git-dir", "--work-tree", "--namespace", "--bare", "--no-pager", "-p", "--paginate",
                     "-P", "--no-replace-objects", "--literal-pathspecs", "--glob-pathspecs", "--noglob-pathspecs",
                     "--icase-pathspecs", "--no-optional-locks", "--no-advice", "--no-lazy-fetch")
GIT_CONFIG_ENV = re.compile(r"GIT_CONFIG_(?:COUNT|KEY_|VALUE_|PARAMETERS)")  # config, so aliases, from the environment
DIFF_TOOLS = ("git", "git-apply", "git-am", "patch", "gpatch")  # command names a glob command word is matched against


def patch_dirs(operands):
    """The directories a `patch` command's -d/--directory options name, as written (`-d D`, `-dD`, `-sd D`,
    `--directory=D`, `--dir D`)."""
    dirs, i = [], 0
    while i < len(operands):
        w = operands[i]
        i += 1
        if w == "--":
            break
        if w.startswith("--"):
            name, eq, val = w.partition("=")
            if len(name) >= 4 and "--directory".startswith(name):  # getopt_long takes any unique prefix
                if not eq:
                    val = operands[i] if i < len(operands) else ""
                    i += 1
                dirs.append(val)
        elif w.startswith("-") and len(w) > 1:
            for k, c in enumerate(w[1:], 1):
                if c in PATCH_SHORT_ARG:
                    val = w[k + 1 :]
                    if not val:
                        val = operands[i] if i < len(operands) else ""
                        i += 1
                    if c == "d":
                        dirs.append(val)
                    break
    return dirs


GIT_WORD_QUOTES = re.compile(r"[\\'\"]")  # removed before the `git$` word check: `bin/g''it${IFS}apply` is `bin/git${IFS}apply`


# one innermost expansion each; `$` before `{`, `(` or `[` waits until what is inside is gone
EXPANSION = re.compile(r"\$\([^()]*\)|`[^`]*`|\$\{[^{}]*\}|\$\[[^\[\]]*\]|\$(?:[A-Za-z_]\w*|[^{(\[])")
IFS_EXPANSION = re.compile(r"\$\{[=~^#!]*IFS(?!\w)[^{}]*\}|\$IFS(?!\w)")  # `${IFS}`, `${=IFS}`, `${IFS//x/}`, `$IFS`
# the flags, name and subscript at the start of a `${...}` body: `=`, `~`, `!`, `#`, zsh `(L)`; `A`, `1`, `@`; `[0]`
EXPANSION_HEAD = re.compile(r"(?:[=~^#!]|\([^()]*\))*(?:[A-Za-z_]\w*|\d+|[@*#?$!-])?(?:\[[^\]]*\])?")
# `${X:-text}`, `${X=text}`, `${X:+text}`, also with a flag or subscript (`${=A:-git}`, `${A[0]:-git}`, `${1:-git}`)
DEFAULT_EXPANSION = re.compile(r"\$\{(?:[=~^#!]|\([^()]*\))*(?:[A-Za-z_]\w*|\d+|[@*])(?:\[[^\]]*\])?"
                               r":?[-=+?]([^{}]*)\}")
BRACED_EXPANSION = re.compile(r"\$\{([^{}]*)\}")  # one innermost `${...}`
PLAIN_EXPANSION = re.compile(r"\$(?:[A-Za-z_]\w*|[0-9@*#?$!-])")  # `$NAME`, `$1`, `$@`
MAX_EXPANSION_PASSES = 64  # nesting levels shell_variants()/may_name_git() unwrap before they fail closed
MAX_RUN_DEPTH = 3  # `bash -c` levels diff_apply_runs() follows; past it a text with `$`, `{` or a backtick is a run
# an `alias` definition anywhere, quotes and backslashes removed first (`al''ias a=g''it`): any alias can run git
ALIAS_DEFINITION = re.compile(r"(?i)(?<![\w.-])alias\s[^\n;&|]*=|(?<![\w.-])aliases\[|BASH_ALIASES"
                              r"|(?<![\w.-])hash\s[^\n;&|]*(?<!\S)-\w*p")


def shell_variants(command):
    """The texts a command may read as once the shell expands it, or None to fail closed; [] when it has no `$`,
    backtick or `{`. `$'...'` is decoded (an escape bash reads differently fails closed) and `$"` read as `"`,
    `${IFS}` forms read as a space, then either every other expansion is dropped or a `${X:-text}`-style one is read
    as its text, and quotes and braces are removed. diff_apply_runs() parses each with the real git option handling,
    so `${E:-git}${IFS}apply`, `g${E}it${IFS}-C${IFS}dir${IFS}apply` and `gi{t,}${IFS}apply` are runs (#196 confirm
    on 45e9a30: matching the letters `gitapply` missed default text and options, and false-blocked `git-amend`)."""
    if not re.search(r"[$`{]", command):
        return []
    bodies = re.findall(r"\$'((?:[^'\\]|\\.)*)'", command)
    if any(ansi_c_decode(b) is None for b in bodies):
        return None
    text = re.sub(r"\$'((?:[^'\\]|\\.)*)'", lambda m: "'" + ansi_c_decode(m.group(1)) + "'", command).replace('$"', '"')
    text = IFS_EXPANSION.sub(" ", text)
    variants = []
    for keep_defaults in (False, True):
        t = text
        for _ in range(MAX_EXPANSION_PASSES):
            check_deadline()
            d = DEFAULT_EXPANSION.sub(r"\1", t) if keep_defaults else t
            if d == t:
                d = EXPANSION.sub("", t)
            if d == t:
                break
            t = d
        else:
            return None
        variants.append(re.sub(r"[{},\\'\"]", "", t))
    return variants


def last_path_part(word):
    """The text after the last `/` that is outside every `${...}` (`${A:-/usr/bin/g}it` has none), in one pass."""
    depth, start, i = 0, 0, 0
    while i < len(word):
        if word[i] == "\\":
            i += 1
        elif word.startswith("${", i):
            depth += 1
            i += 1
        elif word[i] == "}" and depth:
            depth -= 1
        elif word[i] == "/" and not depth:
            start = i + 1
        i += 1
    return word[start:]


def holds_letters(text, name):
    """True if the letters of `text` hold those of `name` in order, in one pass (a regex `g.*i.*t` is cubic)."""
    k = -1
    for c in name:
        k = text.find(c, k + 1)
        if k < 0:
            return False
    return True


def may_name_git(word):
    """True if a word built with an expansion may run git once the shell expands it: its last path part (#196
    confirm on a882ff1: a literal one, as in `${HOME}/go/bin/gitleaks`, is the name as written, so it is not git)
    holds g, i, t in order, with `$( )` bodies dropped (they are parsed as commands of their own), a `${...}` read
    as the text after its flags, name and subscript (`${A:-gi}${B:+x}t`, `${=A:-git}`, `${(L)A:-GIT}`,
    `g${A//\\}/}it`) and a plain `$NAME` or `${NAME}` read as empty. Tradeoff: `${GIT_BIN:-git} status` counts (and
    blocks on every branch)."""
    if not re.search(r"[$`{\\]", word):
        return False
    part = last_path_part(drop_substitutions(word))
    if not re.search(r"[$`{\\]", part):
        return False
    for _ in range(MAX_EXPANSION_PASSES):
        read = BRACED_EXPANSION.sub(lambda m: m.group(1)[EXPANSION_HEAD.match(m.group(1)).end():], part)
        if read == part:
            break
        part = read
    else:
        return True  # nested 64 or more deep: fail closed, as shell_variants() does
    return holds_letters(re.sub(r"[^a-z]", "", PLAIN_EXPANSION.sub("", part).lower()), "git")


def diff_apply_dirs(words, config_env=False):
    """None unless this one command is a `git apply`, `git am`, `git-apply`, `git-am` or `patch`, or a git command
    the hook can't follow: a `-c alias.*`, a global option not in KNOWN_GIT_GLOBALS, a subcommand built from a
    variable, glob or brace, or a GIT_CONFIG_* setting (`config_env`, or a prefix on this command), since an alias can
    run either tool. Otherwise the directories it names, as written: `git -C`, `--git-dir`, `--work-tree` (each -C
    relative to the one before), `git apply --directory`, or `patch -d`/`--directory`. Read-only forms are told apart
    later, by read_only_segment()."""
    idx = real_command_index(words)
    # `git${IFS}apply`, `git$IFS$9am`, zsh `git${=IFS}apply` (which reads as an assignment): the shell splits the word
    # into `git apply`. Any word anywhere counts, with quotes and backslashes removed and split on shell separators,
    # through any path: a closed list of launchers (`eval`, `find -exec`, `watch`, `parallel`) kept missing some (`trap`,
    # `fd -x`, `entr`, `source`) and quotes inside an eval string hid the word (#196 scans on f4791d5). The path part
    # ends at the first `$`/backtick: basename() of `git${IFS//x/}apply` is `}apply` (#196 scan on ac8a069).
    # Tradeoff: `git push origin feature/git$BRANCH` counts too, so it is blocked on every branch (its `$` makes the
    # diff unreadable) unless the command is read-only. Spellings built from expansions are caught by shell_variants()
    if any(re.match(r"(?:[^$`]*/)?git[$`]", t, re.I) for w in words for t in re.split(r"[\s;&|()]+", GIT_WORD_QUOTES.sub("", w))):
        return []
    if idx is not None and may_name_git(words[idx]):
        return []
    if idx is None:
        return None
    cmd = os.path.basename(unquote(words[idx])).lower()  # macOS paths are case-insensitive: `PATCH` runs patch
    if re.search(r"[*?\[]", cmd):  # `/usr/bin/gi? apply`, `/usr/bin/[g]it apply` (#196 confirm N6 on a882ff1)
        cmd = next((n for n in DIFF_TOOLS if fnmatch.fnmatchcase(n, posix_glob(cmd))), cmd)
    operands = command_operands(words[idx + 1 :])
    if cmd in ("patch", "gpatch"):
        return patch_dirs(operands)
    if cmd in ("git-apply", "git-am"):
        return []
    if cmd != "git":
        return None
    config_env = config_env or any(GIT_CONFIG_ENV.match(w) for w in words[:idx])
    dirs, base, unknown, i = [], "", config_env, 0
    while i < len(operands) and operands[i].startswith("-"):
        name, eq, val = operands[i].partition("=")
        if name in GIT_ARG_FLAGS and not eq:
            val = operands[i + 1] if i + 1 < len(operands) else ""
            i += 1
        if name == "-C":
            base = os.path.join(base, val)
            dirs.append(base)
        elif name in ("--git-dir", "--work-tree"):
            dirs.append(os.path.join(base, val))
        elif name == "-c" and val.lower().startswith("alias."):
            unknown = True
        elif name not in KNOWN_GIT_GLOBALS:
            unknown = True  # `--config-env alias.x=VAR`, `--exec-path=DIR`: it may consume the next word too
        i += 1
    sub = operands[i].lower() if i < len(operands) else ""
    if sub == "apply":
        dirs += [w.partition("=")[2] for w in operands[i + 1 :] if w.startswith("--directory=")]
    unknown = unknown or bool(re.search(r"[$`*?\[{}]", sub))
    return dirs if sub in ("apply", "am") or unknown else None


def glob_names_tool(word, before_subcommand):
    """True if a glob word's last path part matches git (only `before_subcommand`, an `apply`/`am` word follows)
    or, with 3 or more literal letters, patch or gpatch."""
    base = unquote(word).rsplit("/", 1)[-1].lower()
    if not re.search(r"[*?\[]", base):
        return False
    pattern = posix_glob(base)
    if before_subcommand and any(fnmatch.fnmatchcase(n, pattern) for n in ("git", "git-apply", "git-am")):
        return True
    return len(re.findall(r"[a-z]", pattern)) >= 3 and any(fnmatch.fnmatchcase(n, pattern) for n in ("patch", "gpatch"))


ZSH_ALTERNATION = re.compile(r"\(([\w./-]*\|[\w|./-]*)\)")  # zsh `(g|x)it`, which the tokenizer splits at `(`
NAME_CHARS_BEFORE = re.compile(r"[\w./-]*$")
NAME_CHARS_AFTER = re.compile(r"[\w./-]*")


def zsh_alternation_names_tool(command):
    """True if a zsh `(a|b)` alternation glob can spell a diff tool's command name (`/usr/bin/(g|x)it apply`)."""
    for m in ZSH_ALTERNATION.finditer(command):
        pre = NAME_CHARS_BEFORE.search(command[max(0, m.start() - 256) : m.start()]).group()
        post = NAME_CHARS_AFTER.match(command, m.end()).group()
        if any(os.path.basename(pre + alt + post).lower() in DIFF_TOOLS for alt in m.group(1).split("|")):
            return True
    return False


def reads_stdin_script(cmd, operands):
    """True for a shell with no `-c` and no script operand: it runs commands from a heredoc, here-string or pipe."""
    return cmd in SHELLS and not any(re.fullmatch(r"-[A-Za-z]*c[A-Za-z]*", w) or not w.startswith("-") for w in operands)


def diff_apply_runs(command, _depth=0):
    """(runs, dirs): whether a command parses as a `git apply`, `git am` or `patch` run, or a git command the hook
    can't follow (diff_apply_dirs()), and the directories those commands name. Looks inside `$( )`, backticks and
    `bash -c`. This adds to names_diff_tool(), which reads the text; neither decides read-only on its own."""
    tokens = tokenize(command)
    if tokens is None or _depth > MAX_RUN_DEPTH:
        return False, []
    # the tokenizer splits `g$(:)it${IFS}apply` at `$(`, so the whole text is checked too (#196 scan on ac8a069)
    variants = shell_variants(command)
    # any alias definition, quoted or spelled, on its own line too: a closed list of alias values kept leaking (#196
    # confirm F8 on e6aa65d, N4 on a882ff1). Tradeoff: prose such as `--body "alias a=git"` blocks on feature/*
    # backslash-newlines joined first: `alias \<NL>a=git` (#196 confirm C on d5e89cd)
    runs = bool(ALIAS_DEFINITION.search(GIT_WORD_QUOTES.sub("", command.replace("\\\n", ""))))
    runs = runs or "${(" in command or variants is None
    # past the nesting budget a `$`-built text is not reparsed, so it counts as a run (#196 scan on 7bf3e12: three
    # `bash -c` levels hid `g${E}it${IFS}apply`, and the depth cap failed open)
    runs = runs or bool(variants and _depth >= MAX_RUN_DEPTH) or zsh_alternation_names_tool(command)
    runs = runs or any(diff_apply_runs(v, _depth + 1)[0] for v in variants or ())
    dirs, config_env = [], bool(GIT_CONFIG_ENV.search(command))

    def visit(text):
        nonlocal runs
        sub_runs, sub_dirs = diff_apply_runs(text, _depth + 1)
        runs = runs or sub_runs
        dirs.extend(sub_dirs)

    for body in re.findall(r"`([^`]*)`", command):
        visit(body)
    for item in split_segments(tokens):
        if item[0] != "cmd":
            continue
        words = item[1]
        for w in words:
            for body in command_substitutions(w):
                visit(body)
        found = diff_apply_dirs(words, config_env)
        if found is not None:
            runs = True
            dirs += found
        idx = real_command_index(words)
        # a spelled git outside command position, where a launcher (`eval`, `find -exec`, `trap`, `| sh`, `env -S`)
        # runs it (#196 confirm N1 on a882ff1): a word holding an expansion that splits into words (`${IFS}`, quoted
        # spaces) is parsed again as a command, and a spelled git word followed by an `apply` or `am` word is a run
        last_sub = max((k for k, w in enumerate(words) if unquote(w).lower() in ("apply", "am")), default=-1)
        for k, w in enumerate(words):
            if k == idx or not re.search(r"[$`]", w):
                continue
            text = GIT_WORD_QUOTES.sub("", IFS_EXPANSION.sub(" ", w))
            if text != w and re.search(r"\s", text):
                sub_runs, sub_dirs = diff_apply_runs(text, _depth)  # each reparse removes quotes or `${IFS}`, so it ends
                runs = runs or sub_runs
                dirs.extend(sub_dirs)
            runs = runs or (k < last_sub and may_name_git(w))
        for k, w in enumerate(words):  # `eval /usr/bin/gi? apply`, `find -exec p?tch` (#196 confirm D on d5e89cd)
            runs = runs or (k != idx and glob_names_tool(w, k < last_sub))
        if idx is None:
            continue
        cmd = os.path.basename(unquote(words[idx])).lower()
        operands = command_operands(words[idx + 1 :])
        if cmd in SHELLS:
            for j, w in enumerate(operands):
                if re.fullmatch(r"-[A-Za-z]*c[A-Za-z]*", w):
                    if j + 1 < len(operands):
                        visit(operands[j + 1])
                    break
    return runs, dirs


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
    results, cur, prev, stack, dirstack = [], cwd, None, [], []  # `cd dir && ...` moves cur for later segments
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
            if cmd == "pushd" and args and "$" not in args[0]:
                dirstack.append(cur)
                prev, cur = cur, resolve(args[0], cur)
                continue
            if cmd == "popd":
                if dirstack:
                    prev, cur = cur, dirstack.pop()
                continue
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


def patch_targets(patch, cwd, dirs=None):
    """Every path a Codex apply_patch adds, updates, deletes or moves to; the source of a move is its Update File line.
    Each path is resolved against every directory in `dirs` (default: cwd alone), each pair once."""
    return [(resolve(p, d), WRITE) for p in dict.fromkeys(PATCH_TARGET.findall(patch)) for d in dict.fromkeys(dirs or [cwd])]


PATCH_NAME = re.compile(r"apply_?patch", re.IGNORECASE)  # macOS paths are case-insensitive
ASSIGNMENT = re.compile(r"(?:^|[\s;&|(])(?:(?:export|declare|typeset|local|readonly)\s+(?:-\w+\s+)*)?"
                        r"([A-Za-z_]\w*)=('[^']*'|\"[^\"]*\"|[^\s;&|()]*)")
# Read-only tools a command that names apply_patch may run on a feature branch (pragma #140: a denylist of ways to
# run it kept leaking, so anything not listed here is blocked). Matched by exact name: `./grep` or `$g` is not grep.
READ_ONLY_TOOLS = ("grep", "egrep", "fgrep", "rg", "echo", "printf", "cat", "type", "which", "sed", "find", "git", "command")
FIND_ACTIONS = re.compile(r"-(?:exec|execdir|ok|okdir|delete|fprint0?|fprintf|fls)")
SED_PRINT = re.compile(r"(?:(?:\d+|\$|/[^/]*/)(?:,(?:\d+|\$|/[^/]*/))?)?p")  # `5p`, `1,9p`, `/re/p`: no e or w command
# Expansions that evaluate a value as code: `$(( ))`, `(( ))`, `$[ ]`, and any `${...}` other than a plain
# `${name}` (`${a[...]}`, `${a@P}`, `${v:off:len}` offsets are arithmetic).
EVAL_EXPANSION = re.compile(r"\$\(\(|\(\(|\$\[|\$\{(?![A-Za-z_]\w*\})")
GLOB_MIN_LETTERS = 5  # a glob word needs this many literal letters to count: `*` alone matches any name
MAX_ANALYZED_COMMAND = 32768  # characters; none of ~4,800 real session commands exceeded 20k (2026-11-03)
BRACE_SEPARATORS = "{},"  # an unquoted one of these sits next to every piece of text brace expansion leaves out
SUBSTITUTION_SEPARATORS = BRACE_SEPARATORS + "$()`"  # a `$( )`/backtick span starts with `$`/`` ` `` and ends with `)`/`` ` ``
# Allowlisted tools whose options can run code or write files, so their arguments must be literal.
FLAG_SENSITIVE = ("sed", "git", "rg", "find", "command", "printf")
# rg options that run another program (rg 15 `--help`): a preprocessor, a hostname command, decompressors.
# Any short-option word containing `z` (`-nz`, `-zA3`) counts as -z/--search-zip: a cluster can end in an attached
# value, so this fails closed (`-ez` is blocked too). rg takes no abbreviated long options.
RG_RUNS_PROGRAM = ("--pre", "--hostname-bin", "--search-zip")
SHELL_WORDS = ("if", "then", "elif", "else", "fi", "while", "until", "do", "done", "!", "{", "}", "for", "case", "esac",
               "select", "in", "time")


def patch_text_match(forms):
    """names_patch()'s test on the [flat, expanded, defaulted, dropped] forms of a command."""
    if any(PATCH_NAME.search(s) for s in forms):
        return True
    if has_brace_group(forms[0]) and any(spells_through_braces(t) for t in forms):
        return True  # `apply_{{p,q}a,x}tch`, `apply_pa{s..t}ch`, `apply_{'{',pa}tch`
    if re.search(r"\$\(|`", forms[0]) and any(spells_through_braces(t, SUBSTITUTION_SEPARATORS) for t in forms[:3]):
        return True  # `apply_p$(: ")")atch`: a `$( )` or backtick span may print nothing, wherever its quotes end
    return False


BRACE_RANGE = re.compile(r"(-?\d+)\.\.(-?\d+)(?:\.\.-?\d+)?|([A-Za-z])\.\.([A-Za-z])(?:\.\.-?\d+)?")
BRACE_LIMIT = 256  # words one brace expansion may produce before it counts as naming the tool


def brace_expand(word):
    """The words bash brace expansion makes of one word (quotes already removed, so a quoted brace counts as a real
    one, an over-approximation): `pa{t,}ch` -> [patch, pach], `{a..c}` ranges too. None past BRACE_LIMIT words."""
    match, commas, stack = {}, {}, []
    for k, c in enumerate(word):  # one pass: each `{` with its `}` and its own top-level commas
        if c == "{":
            stack.append(k)
            commas[k] = []
        elif c == "}" and stack:
            match[stack.pop()] = k
        elif c == "," and stack:
            commas[stack[-1]].append(k)
    for start in sorted(match):
        end, cuts = match[start], commas[start]
        if cuts:
            bounds = [start] + cuts + [end]
            alts = [word[a + 1 : b] for a, b in zip(bounds, bounds[1:])]
        else:
            m = BRACE_RANGE.fullmatch(word[start + 1 : end])
            if not m:
                continue  # `{}`, `{x}`, `${name}`: not an expansion
            lo, hi = (int(m.group(1)), int(m.group(2))) if m.group(1) is not None else (ord(m.group(3)), ord(m.group(4)))
            if abs(hi - lo) >= BRACE_LIMIT:
                return None
            step = 1 if lo <= hi else -1
            alts = [str(x) if m.group(1) is not None else chr(x) for x in range(lo, hi + step, step)]
        out = []
        for alt in alts:
            sub = brace_expand(word[:start] + alt + word[end + 1 :])
            if sub is None or len(out) + len(sub) > BRACE_LIMIT:
                return None
            out += sub
        return out
    return [word]


def diff_text_match(forms):
    """names_diff_tool()'s test on the [flat, expanded, defaulted, dropped] forms of a command: DIFF_TOOL_NAME, also
    after brace expansion (`pa{t,}ch`, `git ap{p,}ly`); `git stash apply` is not a match."""
    for text in forms:
        if DIFF_TOOL_NAME.search(STASH_APPLY.sub("stash", text)):
            return True
        if "{" in text:
            words = [(w, brace_expand(w) if "{" in w else [w]) for w in text.split()]
            git_named = re.search(r"(?i)(?<![\w.-])git(?![\w.-])", text)
            for w, expanded in words:
                # past BRACE_LIMIT, fall back to spells_through_braces(): could the word spell a name at all
                if expanded is None and (any(spells_through_braces(w, name=n) for n in ("patch", "gitapply", "gitam"))
                                         or git_named and any(spells_through_braces(w, name=n) for n in ("apply", "am"))):
                    return True
            if DIFF_TOOL_NAME.search(STASH_APPLY.sub("stash", " ".join(x for w, e in words for x in (e or [w])))):
                return True
    return False


def names_patch(command):
    """True if apply_patch/applypatch is named anywhere in the command (names_tool())."""
    return names_tool(command, ("apply_patch", "applypatch"), patch_text_match)


def names_diff_tool(command):
    """True if `patch`, `gpatch`, `git-apply`, `git-am`, or `git` ... `apply`/`am` is named anywhere in the command
    (names_tool(), diff_text_match())."""
    return names_tool(command, ("patch", "gpatch", "git-apply", "git-am"), diff_text_match)


def names_tool(command, names, text_match):
    """True if a tool is named anywhere in the command: `text_match` on the command after removing quotes and
    backslashes (`ap''ply_patch`), after expanding variables the command assigns itself (`a=apply_; ${a}patch`), or
    with unset ones dropped (`apply${x}_patch`), with `$'\\x61'`/`$"..."` quoting decoded and with `$( )`,
    backticks, `$[ ]` and special parameters (`$@`) dropped, as they can expand to nothing (`apply_p$(:)atch`); or a
    glob word that matches one of `names` (`apply_pat?h`). A name printed at run time (`base64 -d`, `$(echo pa)`) is
    not seen; one assembled with `printf -v` or `read` and then run as `$a` is caught by builds_command_name()."""
    if "\\\n" in command:  # `apply\<NL>patch`: a line continuation joins the word; check both forms
        joined = re.sub(r"(?<!\\)((?:\\\\)*)\\\n", r"\1", command)
        if joined != command and names_tool(joined, names, text_match):
            return True
    if "$'" in command or '$"' in command:
        bodies = re.findall(r"\$'((?:[^'\\]|\\.)*)'", command)
        if any(ansi_c_decode(b) is None for b in bodies):
            return True  # an escape bash reads differently (`\\u61`, `\\x6`): fail closed
        decoded = re.sub(r"\$'((?:[^'\\]|\\.)*)'", lambda m: "'" + ansi_c_decode(m.group(1)).replace("'", "'\\''") + "'", command).replace('$"', '"')
        if decoded != command and names_tool(decoded, names, text_match):
            return True
    flat = re.sub(r"[\\'\"]", "", command)
    values = {name: unquote(value) for name, value in ASSIGNMENT.findall(command)}
    expanded, defaulted, dropped = expand_words(flat, values)
    # #145: the same text with quoted glob characters kept as placeholders, so a quoted `]` stays a bracket member
    q_dropped = expand_words(mark_quoted(command), values)[2].replace(QUOTE_BOUNDARY, "")
    commands = set()
    for item in split_segments(tokenize(command) or []):
        idx = real_command_index(item[1]) if item[0] == "cmd" else None
        if idx is not None:
            commands.add(unquote(item[1][idx]).rsplit("/", 1)[-1].lower())
    globs = [re.sub(r"\*+", "*", posix_glob(w.rsplit("/", 1)[-1].lower())) for w in dropped.split() if re.search(r"[*?\[]", w)]
    globs += [posix_glob(c) for c in commands if re.search(r"[*?\[]", c)]
    q_words = [w for w in q_dropped.split() if re.search(r"[*?\[]", w)]
    q_tokens = tokenize(q_dropped)
    q_commands = q_words if q_tokens is None else [  # unparsable: treat every glob word as a command (fail closed)
        item[1][real_command_index(item[1])] for item in split_segments(q_tokens)
        if item[0] == "cmd" and real_command_index(item[1]) is not None]

    def q_globs(words, command=False):  # `{...}` groups dropped or opened, or read as `*`
        words = [re.sub(r"\{,+\}", "", w) for w in words]  # `{,}` expands to nothing
        # any alternative (`apply_p{a,b}[x']'t]ch`); a bare `{a,b}` command word would read as `*`, which matches
        # any name, so a command needs a bracket too
        words = words + [v for w in words if "{" in w for v in [re.sub(r"\{[^{}]*\}", "", w), re.sub(r"[{},]", "", w)]
                         + [brace_star(w)] * (not command or "[" in w)]
        return [unmark(re.sub(r"\*+", "*", posix_glob(w.rsplit("/", 1)[-1].lower()))) for w in words]

    globs += q_globs(q_words) + q_globs(q_commands, command=True)
    commands.update(q_globs(q_commands, command=True))
    commands |= {posix_glob(c) for c in commands}  # compare a command glob in the same form as `globs` (`./a[p]*h`)
    globs = [w for w in globs if glob_min_length(w) <= max(map(len, names))]  # fnmatch is slow on long bracket words
    if any((w in commands or len(re.findall(r"[a-z]", re.sub(r"\[[^\]]*\]", "", w))) >= GLOB_MIN_LETTERS)
           and fnmatch.fnmatchcase(n, w) for w in globs for n in names):
        return True  # `apply_pat?h`, `a*` as a command: the glob runs the tool when it matches a file of that name
    if text_match([flat, expanded, defaulted, dropped]):
        return True
    return builds_command_name(command)


def posix_glob(word):
    """`word` with every bash bracket expression replaced by `?` (any one character, an over-approximation), read
    the way bash reads it: an optional `!`/`^`, a leading `]` kept as a member, `[:class:]`, `[.sym.]` and `[=eq=]`
    members and a literal `[` inside (`[][:alpha:]]`, `[[a[:alpha:]]`). One pass: the `:]`, `.]` and `=]`
    positions are found up front. An unterminated `[` and the rest of the word stay as text, except an
    unterminated `[!]` or `[^]`, which zsh (the macOS shell) reads as any one character (#149); bash keeps it as
    text, which can never spell the tool's name, so the zsh reading is the one to check."""
    n = len(word)
    member_close = {c: [-1] * (n + 2) for c in ":.="}
    for c, close in member_close.items():
        nxt = -1
        for k in range(n - 2, -1, -1):
            nxt = k if word[k] == c and word[k + 1] == "]" else nxt
            close[k] = nxt
    out, i = [], 0
    while i < n:
        if word[i] != "[":
            out.append(word[i])
            i += 1
            continue
        j = i + 1
        j += j < n and word[j] in "!^"
        j += j < n and word[j] == "]"
        while j < n and word[j] != "]":
            k = member_close[word[j + 1]][j + 2] if word[j] == "[" and j + 2 < n and word[j + 1] in ":.=" else -1
            j = k + 2 if k >= 0 else j + 1
        if j >= n:
            if word[i + 1 : i + 3] in ("!]", "^]"):
                out.append("?")
                i += 3
                continue
            out.append(word[i:])
            break
        out.append("?")
        i = j + 1
    return "".join(out)


# A quoted or escaped glob character is literal, so it stands in as one of these while posix_glob() runs, then
# goes back as text fnmatch reads literally.
# Quoted whitespace, quotes and operators are held too, so the marked text splits into the same words (`[x' ]t]`).
QUOTED_GLOB_CHARS = {c: chr(0xE000 + n) for n, c in enumerate("[]!^:.=*? \t\n'\"\\;&|<>")}
QUOTE_BOUNDARY = "\ue0ff"  # where a quote opened or closed: it ends a variable name (`"$u"x` is `$u` then `x`)
QUOTED_GLOB_LITERAL = {v: {"[": "[[]", "*": "[*]", "?": "[?]"}.get(c, c) for c, v in QUOTED_GLOB_CHARS.items()}


def mark_quoted(text):
    """`text` with its quotes and backslashes removed, and each quoted or escaped glob character replaced by a
    placeholder posix_glob() does not read, so a quoted `]` inside a bracket is a member, not the closer
    (`apply_pa[x\\]t]ch`, `apply_pa[x']'t]ch`, #145). A QUOTE_BOUNDARY marks each quote and escape, so a variable
    name ends there as it does in the shell; names_patch() removes it after expanding. Linear."""
    out, quote, i = [], None, 0
    while i < len(text):
        c = text[i]
        if quote and c == quote:
            quote = None
            out.append(QUOTE_BOUNDARY)
        elif not quote and c in "'\"":
            quote = c
            out.append(QUOTE_BOUNDARY)
        elif c == "\\" and quote != "'" and i + 1 < len(text) and (not quote or text[i + 1] in '\\"$`'):
            i += 1
            out += [QUOTE_BOUNDARY, QUOTED_GLOB_CHARS.get(text[i], text[i])]
        else:
            out.append(QUOTED_GLOB_CHARS.get(c, c) if quote else c)
        i += 1
    return "".join(out)


def brace_star(word):
    """`word` with every `{...}` group, innermost first, replaced by `*`: an over-approximation of all its brace
    alternatives (fail closed, PR #148 round 2), so a glob beside a brace group is still matched against the name."""
    for _ in range(word.count("{")):
        starred = re.sub(r"\{[^{}]*\}", "*", word)
        if starred == word:
            break
        word = starred
    return word


def unmark(word):
    """A posix_glob() result with mark_quoted() placeholders turned back into text fnmatch reads literally."""
    return "".join(QUOTED_GLOB_LITERAL.get(c, c) for c in word)


def expand_words(text, values):
    """(expanded, defaulted, dropped) forms of `text` for names_patch(): variables the command sets expanded,
    `${x:-...}` defaults kept, then `$( )`, backticks, unset variables and special parameters dropped."""
    expanded = text
    for _ in range(3):  # a value may itself name another variable
        expanded = re.sub(r"\$\{([A-Za-z_]\w*)\}|\$([A-Za-z_]\w*)",
                          lambda m: values.get(m.group(1) or m.group(2), m.group(0)), expanded)
    defaulted = re.sub(r"\$\{[A-Za-z_]\w*:?[-=+?]([^}]*)\}", r"\1", expanded)  # `apply_pat${x:-ch}`
    dropped = re.sub(r"\$\{[^}]*\}|\$[A-Za-z_]\w*|\$\[[^\]]*\]|\$[@*#?$!0-9-]", "", drop_substitutions(defaulted))
    return expanded, defaulted, dropped


def glob_min_length(word):
    """The fewest characters a glob word can match (`*` matches none, `?` and a `[...]` class one each), in one pass,
    so a word too long to match the name is skipped before fnmatch, which is quadratic on unmatched `[`."""
    close, last = [-1] * (len(word) + 2), -1
    for k in range(len(word) - 1, -1, -1):
        last = k if word[k] == "]" else last
        close[k] = last
    n, i = 0, 0
    while i < len(word):
        if word[i] == "[":
            k = i + 1 + (word[i + 1 : i + 2] in ("!", "^"))
            k += word[k : k + 1] == "]"  # `[]a]`: a leading `]` is part of the class
            j = close[k] if k < len(word) else -1
            i = j + 1 if j >= 0 else i + 1
        else:
            i += 1
            if word[i - 1] == "*":
                continue
        n += 1
    return n


def ansi_c_decode(body):
    """The text bash makes of a `$'...'` body: `\\x61`, `\\141`, `\\u0061` and the other C escapes decoded, or None
    when Python's decoder rejects it (bash accepts shorter `\\x`/`\\u`/`\\U` forms), so the caller fails closed."""
    try:
        return codecs.decode(body, "unicode_escape")
    except (UnicodeDecodeError, ValueError):
        return None


def drop_substitutions(text):
    """`text` without its `$( )` and backtick substitutions, outermost first, in one pass. An unterminated `$(`
    is kept as text, so a stray `(` cannot swallow the rest of the command."""
    close, stack = {}, []
    for k, c in enumerate(text):  # match every `(` to its `)` once, so the scan below stays linear
        if c == "(":
            stack.append(k)
        elif c == ")" and stack:
            close[stack.pop()] = k
    out, i = [], 0
    while i < len(text):
        if text.startswith("$(", i) and i + 1 in close:
            i = close[i + 1] + 1
            continue
        if text[i] == "`":
            j = text.find("`", i + 1)
            if j >= 0:
                i = j + 1
                continue
            out.append(text[i:])  # no closing backtick: the rest is text
            break
        out.append(text[i])
        i += 1
    return "".join(out)


def has_brace_group(text):
    """True if `text` has a `{` followed by a `,` or `..` and then a `}`: a possible brace expansion, quoted or not.
    One left-to-right pass: the first `{` is the earliest start any group could have."""
    i = text.find("{")
    if i < 0:
        return False
    seps = [j for j in (text.find(",", i), text.find("..", i)) if j >= 0]
    return bool(seps) and text.find("}", min(seps)) >= 0


# A comma-free `{...}` group; one with a `..` is a possible range (bash range grammar varies by version). Each match
# stops at the next brace or comma, so matching is linear, unlike a pattern with `..` between two open runs.
BRACE_GROUP = re.compile(r"\{[^{},]*\}")


def spells_through_braces(text, separators=BRACE_SEPARATORS, name="applypatch"):
    """True if brace expansion could turn `text` (quotes already removed) into a word containing the name.
    Brace expansion keeps text in order and only leaves out the alternatives it did not pick, and a left-out piece
    always starts and ends at an unquoted `{`, `,` or `}`. So the name's letters must appear in order, and each gap
    between two letters must be empty or start and end with one of those characters. A `{a..z}` range stands for
    any one letter. Underscores are ignored and a quoted brace counts as a real one, so this over-approximates
    bash without parsing quotes, in one pass per letter."""
    text = BRACE_GROUP.sub(lambda m: "\0" if ".." in m.group() else m.group(), text.lower().replace("_", ""))
    text = re.sub(r"\[[^\[\]{},]*\]|[?*]", "\0", re.sub(r"\[:[a-z]+:\]", "x", text))  # `ap?ly`, `pat[c]h`: a glob character stands for a letter
    sep = [c in separators for c in text]
    reach = [c == name[0] or c == "\0" for c in text]
    for letter in name[1:]:
        nxt, opened = [False] * len(text), False
        for q, c in enumerate(text):
            if q >= 2 and reach[q - 2] and sep[q - 1]:
                opened = True  # a candidate p <= q - 2 whose gap starts with a separator
            nxt[q] = (c == letter or c == "\0") and q > 0 and (reach[q - 1] or (opened and sep[q - 1]))
        reach = nxt
        if not any(reach):
            return False
    return True


def split_quoted(command):
    """(the command with every quoted string removed, the contents of each `'...'`/`"..."` string)."""
    plain, quoted, i = [], [], 0
    while i < len(command):
        c = command[i]
        if c == "\\":
            plain.append(command[i : i + 2])
            i += 2
        elif c in "'\"":
            j = i + 1
            while j < len(command) and command[j] != c:
                j += 2 if c == '"' and command[j] == "\\" else 1
            quoted.append(command[i + 1 : j])
            i = j + 1
        else:
            plain.append(c)
            i += 1
    return "".join(plain), quoted


def builds_command_name(command):
    """True if a `$var` command word uses a variable the same command sets (`a=app; a+=ly_patch; $a`,
    `printf -v a ...; $a`, `read a; $a`), or positional parameters after a `set`: the name it runs is built
    in the command, so it can't be read. `$EDITOR f` or `"$HOME/bin/x"` (set elsewhere) stay readable."""
    tokens = tokenize(command)
    for item in split_segments(tokens or []):
        if item[0] != "cmd":
            continue
        idx = real_command_index(item[1])
        word = item[1][idx] if idx is not None else ""
        for name in re.findall(r"\$\{?!?([A-Za-z_]\w*|[0-9@*])", word):
            if not name[0].isalpha() and name[0] != "_":
                if re.search(r"(?:^|[\s;&|(])set\s", command):
                    return True
            elif re.search(rf"(?<![$\w{{!]){name}\b", command):
                return True
    return False


def redirects_read_only(words):
    """True if every redirection in one command only reads or duplicates a descriptor: input (`<`, `<<`, `<<<`,
    `<&`), `>&N`/`>&-`, or output to /dev/null. Any other output target (`>`, `>>`, `>|`, `&>`, `&>>`, `<>`,
    `>& file`) writes a file, and a file written here can be run by the same command (pragma #146)."""
    for i, w in enumerate(words):
        if w not in REDIRECT_OPS or w in ("<", "<<", "<<<", "<&"):
            continue
        target = unquote(words[i + 1]) if i + 1 < len(words) else ""
        if w == ">&" and re.fullmatch(r"\d+-?|-", target):
            continue  # `2>&1`, `>&2`, `>&-`: a descriptor, not a file
        if target != "/dev/null":
            return False
    return True


def read_only_segment(words):
    """True if one `;`/`|`-separated command only reads: no command at all (assignments, keywords), or an
    allowlisted tool used read-only (`sed` only to print, `find` with no action, `git commit`/`git log` with no
    global options, `command -v`, `rg` without an option that runs a program). Leading assignments are skipped.
    An output redirection to anything but /dev/null is a write, so the command is not read-only (pragma #146:
    `echo apply_patch > tool.sh; rg --hostname-bin=./tool.sh ...` and `... >> .git/hooks/pre-commit; git commit`)."""
    if not redirects_read_only(words):
        return False
    i, assigned = 0, False
    while i < len(words):
        w = words[i]
        if w in REDIRECT_OPS:
            i += 2
        elif re.fullmatch(r"\d+", w) and i + 1 < len(words) and words[i + 1] in REDIRECT_OPS:
            i += 1
        elif w in SHELL_WORDS:
            i += 1
        elif re.fullmatch(r"[A-Za-z_]\w*=.*", w):
            assigned, i = True, i + 1
        else:
            break
    if i >= len(words) or words[0] in ("for", "case", "select"):
        return True  # nothing runs here: `a=1`, `fi`, `done`, or a for/case word list
    if assigned:
        return False  # `GIT_PAGER=... git log`, `GREP_OPTIONS=...`: an environment prefix can make a tool run commands
    cmd, args = unquote(words[i]), command_operands(words[i + 1 :])
    if cmd in ("patch", "gpatch"):
        return args in HELP_ONLY + (["--version"],)
    if cmd not in READ_ONLY_TOOLS:
        return False
    if cmd in FLAG_SENSITIVE and any(re.search(r"[$`{}]", w) or (not re.search(r"['\"]", w) and re.search(r"[*?\[]", w))
                                     for w in words[i + 1 :]):
        return False  # `git log $opt`, `sed -n {e x,1p}`: an expansion could become a flag or script that runs code
    if cmd == "sed":
        flags = [a for a in args if a.startswith("-")]
        scripts = [a for a in args if not a.startswith("-")][:1]
        # only `sed -n SCRIPT [file...]` (plus -E/-r): -e/-f/-i and long options could add an e or w command
        return "-n" in flags and all(a in ("-n", "-E", "-r") for a in flags) \
            and bool(scripts) and SED_PRINT.fullmatch(scripts[0]) is not None
    if cmd == "find":
        return not any(FIND_ACTIONS.fullmatch(a) for a in args)
    if cmd == "git" and args[:1] == ["apply"]:
        # every word must be on the list, so a `--check` in a comment (`x.diff # --check`), after `--`, or as the
        # value of another option (`--exclude --check`) does not count (#196 N1)
        rest = args[1:]
        return rest in HELP_ONLY or (any(a in GIT_APPLY_READ_ONLY for a in rest) and all(
            a in GIT_APPLY_SAFE_ARGS or re.fullmatch(r"-p\d+", a) or not a.startswith(("-", "#")) for a in rest))
    if cmd == "git" and args[:1] == ["am"]:
        return args[1:] in HELP_ONLY or (len(args) == 2 and args[1] in GIT_AM_ALLOWED)
    if cmd == "git":
        return bool(args) and args[0] in ("commit", "log") and not any(
            a.startswith(("--output", "--ext-diff", "--textconv", "--template")) for a in args)
    if cmd == "command":
        return bool(args) and re.fullmatch(r"-p?[vV]p?", args[0]) is not None
    if cmd == "printf":
        return not any(a.startswith("-v") for a in args)  # `printf -v 'a[$(...)]'` evaluates the subscript
    if cmd == "rg":
        return not any(a.startswith(RG_RUNS_PROGRAM) or re.match(r"-[^-]*z", a) for a in args)
    return True


def runs_shell_patch(command):
    """True if a Bash command names apply_patch/applypatch (see names_patch()) and does anything other than read:
    every command in it, including inside `$( )` and backticks, must be a read-only allowlisted tool
    (read_only_segment()). Anything else, from `apply_patch < f` to `bash`, `xargs`, `sudo` or an unknown command,
    counts as a possible run, so it is blocked on a feature branch. A command that does not name the tool (a name
    decoded at run time, a script file) is not seen."""
    return names_patch(command) and not_read_only(command, PATCH_NAME)


def runs_diff_tool(command, parsed_run):
    """True if a Bash command may run `git apply`, `git am` or `patch` (#200): its text names one (names_diff_tool())
    or a command parses as one (`parsed_run`, diff_apply_runs()), and it does anything other than read
    (not_read_only()). The same model as runs_shell_patch() (pragma #140)."""
    return (parsed_run or names_diff_tool(command)) and not_read_only(command, DIFF_TOOL_NAME)


def all_read_only(text, depth=0):
    """True if every command in `text`, including inside `$( )` and backticks, is read-only (read_only_segment())."""
    tokens = tokenize(text)
    if tokens is None or depth > 3:
        return False
    for item in split_segments(tokens):
        if item[0] != "cmd":
            continue
        if not read_only_segment(item[1]):
            return False
        if any(not all_read_only(b, depth + 1) for w in item[1] for b in command_substitutions(w)):
            return False
    return True


def not_read_only(command, name_re):
    """True unless every command in it is read-only (all_read_only()) and it has none of the forms the parser can't
    follow: an unquoted heredoc that expands `$( )`, nested backticks, arithmetic, a stored `$( )`, an unquoted
    brace group, or an assignment whose value matches `name_re`."""
    if any(not m.group(1) and re.search(r"\$\(|`", command[m.end():]) for m in HEREDOC.finditer(command)):
        return True  # an unquoted heredoc body expands `$( )` and backticks, and the parser drops heredoc bodies
    if "\\`" in command:
        return True  # nested, escaped backticks are not parsed
    if EVAL_EXPANSION.search(command) or any(name_re.search(v) for _, v in ASSIGNMENT.findall(command)):
        return True  # arithmetic, subscripts and @P evaluate text as code; an assigned name may be run later
    plain, quoted = split_quoted(command)
    if any(re.search(r"\$\(|`", v) for v in quoted + [v for _, v in ASSIGNMENT.findall(command)]):
        return True  # a stored `$( )` or backtick can be run later (eval, @P, an indirect expansion)
    if has_brace_group(plain):
        return True  # an unquoted `{x,y}` or `{a..b}` can add words, flags or scripts the parser never sees
    return not all_read_only(command)


def cd_dirs(command, cwd, _depth=0):
    """(dirs, unresolved): cwd plus every directory a `cd` (or zsh `chdir`), `pushd` or `popd` in the command moves
    to, in order, and whether one of them could not be worked out. Read with the hook's tokenizer, so a quoted
    directory with spaces stays one word (pragma #139). `cd -` and `popd` go back, and a bare `cd` and `~` go home.
    Looks inside `$( )`, `bash -c` and `eval` (whose `cd` carries on to later commands). Unresolved: a target
    holding a variable, `$( )`, a backtick or a glob; zsh's two-word `cd old new`; `~` or a bare `cd` when the
    command assigns HOME; a relative target when CDPATH is set or assigned; `pushd` with no directory or with
    `+N`/`-N`; a `popd` past the command's own pushes; a `cd -` with no earlier `cd`; `env -C`/`--chdir`,
    `sudo -D`/`--chdir` and `make -C`/`--directory`; and a shell reading commands from stdin whose text has a
    `cd`, `chdir`, `pushd` or `popd`. A patch path is checked
    against every directory (an over-approximation), so a `cd` before a shell-run patch can't move it off the
    guard."""
    tokens = tokenize(command)
    if tokens is None or _depth > 3:
        return [cwd], True
    home = None if re.search(r"(?<![\w$])HOME\+?=", command) else os.path.expanduser("~")
    cdpath = bool(os.environ.get("CDPATH")) or "CDPATH" in command
    dirs, unresolved, cur, prev, dirstack, scopes = [cwd], False, cwd, None, [], []

    def visit(text, carry):
        nonlocal unresolved, cur, prev
        sub_dirs, sub_unresolved = cd_dirs(text, cur, _depth + 1)
        dirs.extend(sub_dirs[1:])
        unresolved = unresolved or sub_unresolved
        if carry and sub_dirs[-1] != cur:
            prev, cur = cur, sub_dirs[-1]

    for item in split_segments(tokens):
        if item[0] == "push":
            scopes.append((cur, prev, list(dirstack)))
            continue
        if item[0] == "pop":
            if scopes:
                cur, prev, dirstack = scopes.pop()
            continue
        words = item[1]
        for w in words:
            for body in command_substitutions(w):
                visit(body, False)
        idx = real_command_index(words)
        if idx is None:
            continue
        wrappers = [os.path.basename(unquote(w)) for w in words[:idx]]
        flags = [unquote(w) for w in words[:idx] if w.startswith("-")]
        if ("env" in wrappers and any(re.match(r"-[A-Za-z]*C|--ch", f) for f in flags)) or (
            "sudo" in wrappers and any(re.match(r"-[A-Za-z]*D|--ch", f) for f in flags)):
            unresolved = True  # runs the command in another directory
        if os.path.basename(unquote(words[idx])) == "builtin":
            idx += 1
            if idx >= len(words):
                continue
        cmd = os.path.basename(unquote(words[idx]))
        rest = words[idx + 1 :]
        operands = command_operands(rest)
        if cmd in ("make", "gmake") and any(re.match(r"-[A-Za-z]*C|--dir", w) for w in operands):
            unresolved = True
        if cmd in SHELLS:
            for j, w in enumerate(operands):
                if re.fullmatch(r"-[A-Za-z]*c[A-Za-z]*", w):
                    if j + 1 < len(operands):
                        visit(operands[j + 1], False)
                    break
            if reads_stdin_script(cmd, operands) and re.search(r"(?<![\w.-])(?:cd|chdir|pushd|popd)(?![\w.-])", command):
                unresolved = True
        if cmd == "eval":
            visit(" ".join(operands), True)
            continue
        if cmd not in ("cd", "chdir", "pushd", "popd"):
            continue
        if any("$" in w or "`" in w for w in rest):
            unresolved = True
            continue
        args = [w for w in operands if w == "-" or not w.startswith("-") or re.fullmatch(r"[+-]\d+", w)]
        target = None
        if cmd in ("cd", "chdir") and len(args) > 1:
            unresolved = True  # zsh `cd old new` swaps text in $PWD
            continue
        if cmd == "popd":
            target = dirstack.pop() if dirstack else None
        elif cmd in ("cd", "chdir") and (not args or args[0] == "~"):
            target = home
        elif cmd in ("cd", "chdir") and args[0] == "-":
            target = prev
        elif args and not re.fullmatch(r"[+-]\d+", args[0]) and not re.search(r"[*?\[]", args[0]):
            word = args[0]
            if word.startswith("~"):
                target = resolve(word, cur) if home and re.fullmatch(r"~(/.*)?", word) else None
            elif cdpath and not word.startswith(("/", "./", "../")) and word not in (".", ".."):
                target = None
            else:
                target = resolve(word, cur)
        if target is None:
            unresolved = True
            continue
        if cmd == "pushd":
            dirstack.append(cur)
        prev, cur = cur, target
        dirs.append(cur)
    return dirs, unresolved


MAX_CHECKED_DIRS = 64  # distinct directories (then repos) a patch-tool command may name before it is blocked unchecked
MAX_CLUSTER_WORD = 32  # characters; an option word up to this long contributes every cluster suffix, a longer one only w[2:]
PATH_WORD = re.compile(r"[^\s'\"`=;&|()<>$]+")


def command_words(command):
    """Every word a path could hide in: the raw text split at quotes and operators (so a quoted string's words count,
    PATH_WORD), each shell word with its quotes removed (`"space dir.diff"`, `"cross".diff`), and the value of an
    attached short option (`-icross.diff`, `-o/x`) (#196 confirm F1, F2)."""
    words = PATH_WORD.findall(command) + [unquote(t) for t in tokenize(command) or [] if t not in REDIRECT_OPS]
    # the attached value (`-o/x`), and for words up to MAX_CLUSTER_WORD characters every suffix of a short-option
    # cluster (`-sicross.diff` is -s -i cross.diff), so the suffixes stay bounded (MAX_WORD_WORK caps the rest)
    words += [w[k:] for w in words if re.match(r"-\w.", w)
              for k in range(2, len(w) if len(w) <= MAX_CLUSTER_WORD else 3)]
    return list(dict.fromkeys(w for w in words if w))


def existing_dir(p, cache):
    """`p` if it is a directory, else its nearest existing parent; cached by parent, so many words in one folder
    cost one walk (#196 confirm F3, F4: the uncached walk per word and base took over 30 s, and a hook that times out
    fails open)."""
    check_deadline()
    if os.path.isdir(p):
        return p
    parent = os.path.dirname(p)
    if parent not in cache:
        cache[parent] = parent if os.path.isdir(parent) else nearest_existing_dir(p)
    return cache[parent]


def named_dirs(command, bases, cache):
    """The nearest existing folder of every word in the command (command_words()), quoted strings included, read as
    a path from each of `bases` (an absolute one once). This finds a directory that reaches a patch tool through a
    string or option the parser does not follow: `ssh h 'cd D && ...'`, `su -c`, `env -S`, `script`,
    `xargs -I{} git -C {}` fed `echo D`, a `patch` ORIGFILE or `-o`/`-r` value, `git apply --directory=D`,
    `find D -execdir` (#196 N4, N6, S1, S7). An over-approximation: a word that only looks like a path is checked too."""
    paths = dict.fromkeys(p for w in command_words(command)
                          for base in ([None] if os.path.isabs(os.path.expanduser(w)) else bases)
                          for p in resolve_both(w, base or "/"))
    return list(dict.fromkeys(d for d in (existing_dir(p, cache) for p in paths) if d))


DIFF_HEADER = re.compile(r"^(?:diff --git |--- |\+\+\+ |\*\*\* |Index: |(?:rename|copy) (?:from|to) )(.+)$", re.MULTILINE)
MAX_DIFF_FILES, MAX_DIFF_BYTES = 32, 1 << 20
MAX_DIFF_NAMES = 8192  # distinct diff header names (a 4,000-file diff has ~8,000); each is resolved against every
# checked directory
MAX_HEADER_WORK = 100000  # header names x their -pN strips x checked directories (~3 s at the cap in a shallow tree;
# MAX_CHECK_SECONDS bounds a deep one)
MAX_DIFF_NAME_LEN = 4096  # characters in one diff header name (PATH_MAX on Linux; macOS is 1024)
MAX_WORD_WORK = 100000  # command words x checked directories that named_dirs()/diff_header_dirs() may resolve
# glob characters, a brace expansion (`cr{o,}ss.diff`, `{a..b}`) and a zsh numeric range (`<1-9>`); zsh's `^ ~ #`
# act only with EXTENDED_GLOB (off by default) and stay text, so `git diff HEAD~1 | git apply` is not blocked
UNQUOTED_GLOB = re.compile(r"[*?\[]|\{[^{}]*(?:,|\.\.)[^{}]*\}|<\d*-\d*>")


def has_unquoted_glob(command):
    """True if a shell word (heredoc bodies excluded) has a glob character, brace expansion or zsh `<n-m>` outside
    quotes; `[`/`[[` (test) aside. A `<n-m>` range is looked for in the unquoted text, since the tokenizer reads its
    `<`/`>` as redirections."""
    plain = split_quoted(command)[0]
    if re.search(r"<\d*-\d*>", plain):
        return True
    # zsh `cr(o|x)ss.diff` (no option needed) and bash extglob `@(...)`/`+(...)`/`!(...)`: a `(` glued to a word
    # character before it, or a `)` glued to one after it; `$(`, `<(` and `>(` are substitutions, not globs
    if re.search(r"[^\s;&|<>()$]\(|\)[^\s;&|<>()]", plain):
        return True
    for t in tokenize(command) or []:
        bare = re.sub(r"'[^']*'|\"(?:[^\"\\]|\\.)*\"|\\.", "", t)
        if t not in ("[", "[[") and UNQUOTED_GLOB.search(bare):
            return True
    return False


def diff_header_names(text):
    """File names in diff headers (`diff --git`, `---`/`+++`, context-diff `***`, `Index:`, rename/copy), with the
    tab-separated timestamp cut and a git-quoted name ("a/\\056\\056/x") decoded in every header kind."""
    names = []
    for m in DIFF_HEADER.finditer(text):
        value = m.group(1).split("\t")[0].strip()
        quoted = re.match(r'"(?:[^"\\]|\\.)*"', value)
        parts = (re.findall(r'"(?:[^"\\]|\\.)*"|\S+', value) if m.group(0).startswith("diff --git")
                 else [quoted.group(0)] if quoted else [value])
        for part in parts:
            if part.startswith('"') and part.endswith('"') and len(part) > 1:
                try:
                    part = codecs.escape_decode(part[1:-1].encode("utf-8"))[0].decode("utf-8", "replace")
                except ValueError:
                    pass
            names.append(part)
    return names


def diff_header_dirs(command, bases, cache):
    """(dirs, unread): folders a diff may write outside the checked directories (#196 N5): every name in a diff
    header, read from the command text (a heredoc or here-string) and from each existing file the command names
    (the first MAX_DIFF_BYTES of up to MAX_DIFF_FILES). Apple `patch -p0` writes both kinds of name, and git apply
    does with --unsafe-paths. Each `-pN` strip of a name (up to its first `..`) is tried, as written and with symlinks
    followed. A diff the hook can't see (`curl ... | patch`)
    is not read. `unread` is True when a file was cut at MAX_DIFF_BYTES, files past MAX_DIFF_FILES were skipped, or
    the diffs hold more than MAX_DIFF_NAMES names: the caller blocks then, as for MAX_CHECKED_DIRS,
    since a header past the cap could name a feature repo, and checking them all could time the hook out."""
    if len(bases) > MAX_CHECKED_DIRS or has_unquoted_glob(command) or any(re.search(r"[$`]", t) and not re.fullmatch(r"'[^']*'", t)
                                            for t in tokenize(command) or []):
        return [], True  # any `$` or backtick outside single quotes, even in a word unrelated to the diff: a file named
        # through a variable, `$( )` or `$'...'` can't be opened (#196 scan)
    texts = [command, command.replace("\\n", "\n")]
    seen, unread = set(), False
    for w in command_words(command):
        for base in [None] if os.path.isabs(os.path.expanduser(w)) else bases:
            p = os.path.expanduser(w) if base is None else resolve(w, base)
            if p in seen or not os.path.isfile(p):
                continue
            if len(seen) >= MAX_DIFF_FILES:
                unread = True
                continue
            seen.add(p)
            try:
                with open(p, "rb") as f:
                    data = f.read(MAX_DIFF_BYTES + 1)
                unread = unread or len(data) > MAX_DIFF_BYTES
                texts.append(data[:MAX_DIFF_BYTES].decode("utf-8", "replace"))
            except OSError:
                pass
    # every name, not only absolute or `..` ones: `feat/CLAUDE.md` from a parent folder, or `lnk/CLAUDE.md` through
    # a symlink in a chore repo, reaches a feature repo too (FT#234 review)
    names = list(dict.fromkeys(n for t in texts for n in diff_header_names(t)))

    def strips(n):  # the -pN strips worth trying: up to the first `..`, else every one
        parts = n.split("/")
        return 1 if n.startswith("/") else (parts.index("..") if ".." in parts else len(parts) - 1) + 1

    if len(names) > MAX_DIFF_NAMES or any(len(n) > MAX_DIFF_NAME_LEN for n in names) or sum(
            strips(n) * (1 if n.startswith("/") else len(bases)) for n in names) > MAX_HEADER_WORK:
        return [], True  # a budget on the total work, not only the name count (#196 scan: `../a/../b/...` names)
    paths = {}
    for name in names:
        parts = name.split("/")
        candidates = [name] if name.startswith("/") else ["/".join(parts[k:]) for k in range(strips(name))]
        for c in candidates:
            for base in [None] if c.startswith("/") else bases:
                for p in resolve_both(c, base or "/"):
                    paths[p] = None
    return list(dict.fromkeys(d for d in (existing_dir(p, cache) for p in paths) if d)), unread


def repo_root(d):
    """The nearest folder at or above `d` (symlinks resolved, as git does) holding a `.git` entry; None outside a
    repo, where `git branch` finds no branch either."""
    d = os.path.realpath(d)
    while not os.path.exists(os.path.join(d, ".git")):
        if os.path.dirname(d) == d:
            return None
        d = os.path.dirname(d)
    return d


def evaluate(payload):
    """Return a block message, or None to allow."""
    tool = payload.get("tool_name", "")
    tool_input = payload.get("tool_input") or {}
    cwd = payload.get("cwd") or os.getcwd()
    start_deadline()  # the write-target loop at the end runs under it too

    if tool in FILE_TOOLS:
        candidates = [(resolve(tool_input["file_path"], cwd), WRITE)] if tool_input.get("file_path") else []
    elif tool == "apply_patch":
        candidates = patch_targets(tool_input.get("command") or "", cwd)
    elif tool == "Bash":
        command = tool_input.get("command") or ""
        if len(command) > MAX_ANALYZED_COMMAND:  # parsing is not linear everywhere and a timed-out hook fails open
            return (
                f"BLOCKED: this Bash command is {len(command)} characters, over the guard's {MAX_ANALYZED_COMMAND}-character "
                "limit, so it was not analyzed. Split it, or write long content to a file with the Write tool."
            )
        # an `apply_patch <<'EOF'` heredoc run through the shell (older Codex) is stripped by the parser
        candidates = bash_write_targets(command, cwd)
        dirs, unresolved = cd_dirs(command, cwd)
        if SHELL_PATCH.search(command):
            # headers joined by a literal \n (`printf '...\n*** Update File: X'`) are read too; this still
            # catches an absolute path into another repo that is on a feature branch
            patch_text = command.replace("\\n", "\n")
            # names x directories is bounded before the list is built: 800 names after 3,000 `cd`s took 99 s, and a
            # hook that times out fails open (#196 confirm on c7c66c0)
            pairs = len(set(PATCH_TARGET.findall(patch_text))) * len(set(dirs or [cwd]))
            if pairs > MAX_WORD_WORK:
                return (
                    f"BLOCKED: this command may run apply_patch through the shell and pairs {pairs} file names with "
                    f"directories, over the guard's {MAX_WORD_WORK} limit, so it was not checked (a hook that times "
                    "out fails open; this applies on every branch). Split it, or use the apply_patch tool directly."
                )
            candidates += patch_targets(patch_text, cwd, dirs)
        try:
            parsed_run, diff_dirs = diff_apply_runs(command)
        except CheckTimeout:
            return timeout_message("`git apply`, `git am` or `patch`")
        diff_runs = runs_diff_tool(command, parsed_run)
        tool_name = "apply_patch through the shell" if runs_shell_patch(command) else "`git apply`, `git am` or `patch`" if diff_runs else None
        if tool_name:
            diff_dirs += [os.environ[v] for v in ("GIT_DIR", "GIT_WORK_TREE") if os.environ.get(v)]
            if re.search(r"(?<![\w$])GIT_(?:DIR|WORK_TREE)\b", command) or any(re.search(r"[$`*?\[]", d) for d in diff_dirs):
                unresolved = True
            dirs = list(dict.fromkeys(dirs))  # #196 N7: a long chain of `cd`s repeats directories
            if len(dirs) > MAX_CHECKED_DIRS:
                return (
                    f"BLOCKED: this command runs, or may run, {tool_name} and moves through {len(dirs)} directories, "
                    f"over the guard's {MAX_CHECKED_DIRS}-directory limit, so it was not checked. Split it."
                )
            cache = {}
            start_deadline()
            if len(command_words(command)) * len(dirs) > MAX_WORD_WORK:
                return (
                    f"BLOCKED: this command runs, or may run, {tool_name} and has too many words to check against "
                    f"{len(dirs)} directories within the guard's time budget (a hook that times out fails open). Split it."
                )
            try:
                dirs = dirs + [p for x in diff_dirs for d in dirs for p in resolve_both(x, d)] + named_dirs(command, dirs, cache)
                header_dirs, unread = diff_header_dirs(command, list(dict.fromkeys(dirs)), cache)
            except CheckTimeout:
                return timeout_message(tool_name)
            dirs += header_dirs
            if unread:
                return (
                    f"BLOCKED: this command runs, or may run, {tool_name}, and the guard could not read every diff "
                    "header it may apply (this applies on every branch). Causes: a variable, `$( )` or unquoted glob "
                    f"anywhere in the command (even in a word unrelated to the diff); more than {MAX_DIFF_FILES} named "
                    f"files; a file over {MAX_DIFF_BYTES >> 20} MB; or too many diff header names. "
                    "Split the command, name the diff file literally, or apply a smaller diff."
                )
            remedy = ("use the apply_patch tool directly (its file list is checked)" if tool_name.startswith("apply_patch")
                      else "make the change with Write/Edit, or apply the diff on a chore/* or fix/* branch")
            if unresolved:
                return (
                    f"BLOCKED: this command runs, or may run, {tool_name} in a directory the guard can't work out "
                    "(a variable, `$( )`, a glob, a stack move, HOME/CDPATH/GIT_DIR, or a chdir option), so it "
                    f"can't tell whether that directory is on a feature/* branch. Remedy: {remedy}, or cd to a literal path."
                )
            # fail closed if the session or any directory the command cds into or names is on a feature branch;
            # one `git branch` per repo (#196 N7: one per directory let a long command time out, and a hook that
            # times out fails open)
            repos = {}
            try:
                for d in dict.fromkeys(dirs):  # the deadline covers this walk too (#196 confirm on 2d71985)
                    check_deadline()
                    d = d if os.path.isdir(d) else (nearest_existing_dir(d) or d)
                    root = repo_root(d)
                    if root:
                        repos.setdefault(root, d)
            except CheckTimeout:
                return timeout_message(tool_name)
            if len(repos) > MAX_CHECKED_DIRS:
                return (
                    f"BLOCKED: this command runs, or may run, {tool_name} and names {len(repos)} repositories, over the guard's {MAX_CHECKED_DIRS} limit, so it was not checked. Split it."
                )
            for d in repos.values():
                branch = git(d, "branch", "--show-current")
                if branch and GUARDED_BRANCH.match(branch):
                    return (
                        f"BLOCKED: this command runs, or may run, {tool_name} and `{d}` is on `{branch}` (feature/*). "
                        "The guard can't reliably tell which files that changes, so the route is closed on feature "
                        f"branches. Remedy: {remedy}, or make the change on a chore/* or fix/* branch."
                    )
    else:
        return None

    # one check per target, under the deadline: `>CLAUDE.md;` x2800 from a 430-deep cwd ran one `git rev-parse` each
    # past the hook timeout, which fails open (#196 confirm on b337612)
    targets = list(dict.fromkeys(candidates))
    try:
        for abs_path, kind in targets:
            check_deadline()
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
    except CheckTimeout:
        return (
            f"BLOCKED: checking the {len(targets)} files this command writes took over {MAX_CHECK_SECONDS} s (deep "
            "folders or many targets), so they were not checked (a hook that times out fails open; this applies on "
            "every branch). Split the command, or run it from a shallower folder."
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
    # Every protected glob must pass the substring pre-filter, or a Write/Edit to it exits early as
    # unprotected (fail open). `*` becomes a sample name; a glob missing from _PROTECTED_FRAGMENTS fails here.
    for g in PROTECTED_GLOBS:
        sample = g.replace("*", "x").lower()
        if not any(f in sample for f in _PROTECTED_FRAGMENTS):
            print(f"FAIL  {g} is in PROTECTED_GLOBS but not in _PROTECTED_FRAGMENTS (edits to it skip the check)")
            failures += 1
    with tempfile.TemporaryDirectory() as tmp:
        tmp = os.path.realpath(tmp)
        repos = {}
        # The `plus`, `at` and `accent` repos live in directories whose names contain
        # characters an earlier tokenizer split words on (+ @ and non-ASCII).
        for name, branch, dirname in (
            ("feat", "feature/demo", "feat"), ("chore", "chore/demo", "chore"), ("detached", None, "detached"),
            ("linked", "feature/demo", "linked"), ("plus", "feature/demo", "a+b"), ("at", "feature/demo", "x@2"), ("accent", "feature/demo", "caf\u00e9"),
            ("space", "feature/demo", "my app"),
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

        # plain names, not repos['x'] inside f'...': reusing a quote in an f-string is a SyntaxError before
        # Python 3.12, and /usr/bin/python3 on macOS is 3.9, where the hook would fail open on load (#196 M5)
        feat, chore, space = repos["feat"], repos["chore"], repos["space"]
        # #196 N5: diffs in the chore repo that name a file in the feature repo (Apple patch -p0 writes them)
        hunk = "@@ -0,0 +1 @@\n+x\n"
        for fname, name in (("cross.diff", "../feat/CLAUDE.md"), ("abs.diff", feat + "/CLAUDE.md"),
                            ("p1.diff", "a/../../feat/CLAUDE.md"), ("ok.diff", "App/A.swift"),
                            ("space dir.diff", "../feat/CLAUDE.md")):
            with open(os.path.join(chore, fname), "w") as f:
                f.write("--- " + name + "\t2026-01-01\n+++ " + name + "\n" + hunk)
        with open(os.path.join(chore, "quoted.diff"), "w") as f:
            f.write('diff --git "a/\\056\\056/feat/CLAUDE.md" "b/\\056\\056/feat/CLAUDE.md"\n')
        with open(os.path.join(chore, "quoted0.diff"), "w") as f:
            f.write('--- "\\056\\056/feat/CLAUDE.md" 2026-01-01\n+++ "\\056\\056/feat/CLAUDE.md" x\n' + hunk)
        with open(os.path.join(chore, "deep.diff"), "w") as f:  # 500 names x ~250 -pN strips: over MAX_HEADER_WORK
            f.write("".join("--- " + "a/" * 250 + "../n" + str(k) + "\n" for k in range(500)))
        with open(os.path.join(chore, "big.diff"), "w") as f:
            f.write("--- App/A.swift\n+++ App/A.swift\n" + hunk + "#" * MAX_DIFF_BYTES)
        # FT#234 review: a relative name from the repos' non-repo parent folder, and names through symlinks
        top = os.path.dirname(feat)
        with open(os.path.join(top, "child.diff"), "w") as f:
            f.write("--- feat/CLAUDE.md\n+++ feat/CLAUDE.md\n" + hunk)
        with open(os.path.join(top, "child_git.diff"), "w") as f:
            f.write("diff --git a/feat/CLAUDE.md b/feat/CLAUDE.md\n--- a/feat/CLAUDE.md\n+++ b/feat/CLAUDE.md\n" + hunk)
        os.makedirs(os.path.join(feat, "sub"), exist_ok=True)
        os.symlink(feat, os.path.join(chore, "lnk2"))
        os.symlink(os.path.join(feat, "sub"), os.path.join(chore, "lnk"))
        with open(os.path.join(chore, "sym.diff"), "w") as f:
            f.write("--- lnk2/CLAUDE.md\n+++ lnk2/CLAUDE.md\n" + hunk)

        def bash_at(cwd, cmd):
            return {"tool_name": "Bash", "cwd": cwd, "tool_input": {"command": cmd}}

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
            ("feature: Bash rm -rf ios/.github/workflows", bash("feat", "rm -rf ios/.github/workflows"), True),
            ("feature: Bash rm -rf ios/.github", bash("feat", "rm -rf ios/.github"), True),
            ("feature: Write ios/Sources/A.swift is ordinary", write("Write", "feat", "ios/Sources/A.swift"), False),
        ]
        cases += [
            # 1. Redirections before the command word.
            ("feature: Bash leading > redirect", bash("feat", ">/dev/null rm CLAUDE.md"), True),
            ("feature: Bash leading 2> redirect", bash("feat", "2>/dev/null rm CLAUDE.md"), True),
            ("feature: Bash leading < redirect", bash("feat", "</dev/null sed -i s/a/b/ CLAUDE.md"), True),
            ("feature: Bash leading redirect then VAR=val", bash("feat", ">/dev/null FOO=1 rm CLAUDE.md"), True),
            ("feature: Bash leading redirect, ordinary target", bash("feat", ">/dev/null rm App/A.swift"), False),
            # 2. A redirect glued to a separator.
            ("feature: Bash ;> glued", bash("feat", "true;>CLAUDE.md"), True),
            ("feature: Bash &&> glued", bash("feat", "echo a&&>CLAUDE.md"), True),
            ("feature: Bash |> glued", bash("feat", "ls|>CLAUDE.md"), True),
            ("feature: Bash (> glued", bash("feat", "(>CLAUDE.md)"), True),
            ("feature: Bash ;>> glued", bash("feat", "true;>>CLAUDE.md"), True),
            ("feature: Bash &>> glued", bash("feat", "true&>>CLAUDE.md"), True),
            ("feature: Bash glued redirect, ordinary target", bash("feat", "true;>App/A.swift"), False),
            ("feature: Bash 2>&1 still not a redirect target", bash("feat", "ls CLAUDE.md 2>&1;ls"), False),
            # 3. $( ) after a single-quoted part of the same word.
            ("feature: Bash 'x'\"$( )\" word", bash("feat", "echo 'x'\"$(rm CLAUDE.md)\""), True),
            ("feature: Bash \"a\"'b'$( ) word", bash("feat", "echo \"a\"'b'\"$(rm CLAUDE.md)\""), True),
            ("feature: Bash $( ) only inside single quotes is data", bash("feat", "echo 'x $(rm CLAUDE.md) y'"), False),
            # 9. Long options with a separate argument; pushd and popd.
            ("feature: Bash sudo --user foo", bash("feat", "sudo --user foo rm CLAUDE.md"), True),
            ("feature: Bash env --unset FOO", bash("feat", "env --unset FOO rm CLAUDE.md"), True),
            ("feature: Bash sudo --user=foo", bash("feat", "sudo --user=foo rm CLAUDE.md"), True),
            ("feature: Bash xargs --max-args 1", bash("feat", "xargs --max-args 1 rm CLAUDE.md"), True),
            ("feature: Bash pushd then write", bash("feat", "pushd App && echo x > ../CLAUDE.md"), True),
            ("feature: Bash pushd then popd", bash("feat", "pushd App; popd; echo x > CLAUDE.md"), True),
            ("feature: Bash pushd into a subdir, ordinary file", bash("feat", "pushd App && echo x > README.md"), False),
        ]
        cases.append(("feature: Bash rm -rf of the directory holding the repo", bash("feat", f"rm -rf {tmp}"), True))
        cases.append(("feature: Bash `a#b` word keeps the redirect", bash("feat", "echo a#b > CLAUDE.md"), True))

        def patch(repo, body):  # Codex CLI file edit
            return {"tool_name": "apply_patch", "cwd": repos[repo], "tool_input": {"command": f"*** Begin Patch\n{body}\n*** End Patch\n"}}

        cases += [
            ("feature: apply_patch Update CLAUDE.md", patch("feat", "*** Update File: CLAUDE.md\n@@\n+Rule: none"), True),
            ("feature: apply_patch Add a skill file", patch("feat", "*** Add File: .claude/skills/gates/reference.md\n+x"), True),
            ("feature: apply_patch Delete AGENTS.md", patch("feat", "*** Delete File: AGENTS.md"), True),
            ("feature: apply_patch Move an app file onto CLAUDE.md",
             patch("feat", "*** Update File: App/A.swift\n*** Move to: CLAUDE.md\n@@\n-x\n+y"), True),
            ("feature: apply_patch absolute path to nested CLAUDE.md",
             patch("feat", f"*** Update File: {os.path.join(repos['feat'], 'ios/CLAUDE.md')}\n@@\n+x"), True),
            ("feature: apply_patch Update .codex/hooks.json", patch("feat", "*** Update File: .codex/hooks.json\n@@\n+x"), True),
            ("feature: apply_patch second file in the patch is protected",
             patch("feat", "*** Update File: App/A.swift\n@@\n+x\n*** Update File: CONSTRAINTS.md\n@@\n+y"), True),
            ("feature: apply_patch Update App/A.swift is ordinary", patch("feat", "*** Update File: App/A.swift\n@@\n+x"), False),
            ("chore: apply_patch Update CLAUDE.md allowed", patch("chore", "*** Update File: CLAUDE.md\n@@\n+x"), False),
            ("detached: apply_patch Update CLAUDE.md fails open", patch("detached", "*** Update File: CLAUDE.md\n@@\n+x"), False),
            ("feature: Bash apply_patch heredoc to CLAUDE.md",
             bash("feat", "apply_patch <<'EOF'\n*** Begin Patch\n*** Update File: CLAUDE.md\n@@\n+x\n*** End Patch\nEOF"), True),
            ("feature: Bash patch header text without apply_patch is data",
             bash("feat", "cat <<'EOF' > App/notes.md\n*** Update File: CLAUDE.md\nEOF"), False),
            ("feature: Write .codex/config.toml", write("Write", "feat", ".codex/config.toml"), True),
            ("feature: apply_patch indented header", patch("feat", "  *** Update File: CLAUDE.md\n@@\n+x"), True),
            ("feature: apply_patch tab-indented Move to", patch("feat", "*** Update File: App/A.swift\n\t*** Move to: AGENTS.md\n@@\n+x"), True),
            ("feature: Bash applypatch alias heredoc",
             bash("feat", "applypatch <<'EOF'\n*** Begin Patch\n*** Update File: CLAUDE.md\n@@\n+x\n*** End Patch\nEOF"), True),
            ("feature: Bash cd then apply_patch ../CLAUDE.md",
             bash("feat", "cd App && apply_patch <<'EOF'\n*** Begin Patch\n*** Update File: ../CLAUDE.md\n@@\n+x\n*** End Patch\nEOF"), True),
            ("feature: Bash cd ios then apply_patch CLAUDE.md (nested)",
             bash("feat", "cd ios; apply_patch <<'EOF'\n*** Begin Patch\n*** Update File: CLAUDE.md\n@@\n+x\n*** End Patch\nEOF"), True),
            ("feature: Bash cd -P then apply_patch ../CLAUDE.md",
             bash("feat", "cd -P App && apply_patch <<'EOF'\n*** Begin Patch\n*** Update File: ../CLAUDE.md\n@@\n+x\n*** End Patch\nEOF"), True),
            ("feature: Bash cd then shell apply_patch of an ordinary file is blocked (shell route closed)",
             bash("feat", "cd App && apply_patch <<'EOF'\n*** Begin Patch\n*** Update File: A.swift\n@@\n+x\n*** End Patch\nEOF"), True),
            ("feature: Bash printf literal \\n patch piped to apply_patch (CLAUDE.md)",
             bash("feat", r"printf '*** Begin Patch\n*** Update File: CLAUDE.md\n@@\n+x\n*** End Patch\n' | apply_patch"), True),
            ("feature: Bash printf literal \\n patch piped to apply_patch fails closed (ordinary file)",
             bash("feat", r"printf '*** Begin Patch\n*** Update File: App/A.swift\n@@\n+x\n*** End Patch\n' | apply_patch"), True),
            ("feature: Bash apply_patch with patch from $(cat file) fails closed", bash("feat", 'apply_patch "$(cat p.patch)"'), True),
            ("feature: Bash cat file | applypatch fails closed", bash("feat", "cat p.patch | applypatch"), True),
            ("feature: Bash apply_patch < file fails closed", bash("feat", "apply_patch < p.patch"), True),
            ("feature: Bash bash -c apply_patch from file fails closed", bash("feat", "bash -c 'apply_patch < p.patch'"), True),
            ("feature: Bash grep for apply_patch is not a patch run", bash("feat", "grep -rn apply_patch scripts/"), False),
            ("feature: Bash echo naming apply_patch is not a patch run", bash("feat", "echo 'use apply_patch here'"), False),
            ("chore: Bash cat file | apply_patch allowed", bash("chore", "cat p.patch | apply_patch"), False),
            ("feature: Bash decoy header before apply_patch < file fails closed",
             bash("feat", r"printf 'x\n*** Update File: x.txt\n' >/dev/null; apply_patch < real.patch"), True),
            ("feature: Bash decoy header after apply_patch < file fails closed",
             bash("feat", r"apply_patch < real.patch; echo 'a\n*** Update File: x.txt'"), True),
            ("chore cwd: Bash cd into a feature repo then apply_patch < file fails closed",
             bash("chore", f"cd {repos['feat']} && apply_patch < p.patch"), True),
            # pragma #139 / FinanceTracker #199: cd targets read with the tokenizer; one the guard can't work out fails closed.
            ("chore cwd: Bash cd to a quoted feature dir with a space then apply_patch fails closed",
             bash("chore", f'cd "{space}" && apply_patch < p.patch'), True),
            ("chore cwd: Bash cd $var to a feature repo then apply_patch fails closed",
             bash("chore", f"d={feat}; cd $d && apply_patch < p.patch"), True),
            ("chore cwd: Bash cd $var assigned twice then apply_patch fails closed",
             bash("chore", f"d={chore}; for d in {feat}; do cd $d && apply_patch < p.patch; done"), True),
            ("chore cwd: Bash cd to an unset variable then apply_patch fails closed",
             bash("chore", "cd $SOMEWHERE && apply_patch < p.patch"), True),
            ("chore cwd: Bash cd $(...) then apply_patch fails closed", bash("chore", "cd $(pwd) && apply_patch < p.patch"), True),
            ("chore cwd: Bash HOME= then cd ~ and apply_patch fails closed", bash("chore", f"HOME={feat}; cd ~ && apply_patch < p.patch"), True),
            ("chore cwd: Bash HOME= prefix on a bare cd fails closed", bash("chore", f"HOME={feat} cd && apply_patch < p.patch"), True),
            ("chore cwd: Bash CDPATH= then a relative cd and apply_patch fails closed",
             bash("chore", f"CDPATH={os.path.dirname(feat)} cd {os.path.basename(feat)} && apply_patch < p.patch"), True),
            ("chore cwd: Bash bash -c cd into a feature repo then apply_patch fails closed",
             bash("chore", f"bash -c 'cd {feat} && apply_patch < p.patch'"), True),
            ("chore cwd: Bash eval cd into a feature repo then apply_patch fails closed",
             bash("chore", f"eval 'cd {feat}'; apply_patch < p.patch"), True),
            ("chore cwd: Bash builtin cd into a feature repo then apply_patch fails closed",
             bash("chore", f"builtin cd {feat} && apply_patch < p.patch"), True),
            ("chore cwd: Bash env -C into a feature repo then apply_patch fails closed", bash("chore", f"env -C {feat} apply_patch < p.patch"), True),
            ("chore cwd: Bash sudo -D into a feature repo then apply_patch fails closed", bash("chore", f"sudo -D {feat} apply_patch < p.patch"), True),
            ("chore cwd: Bash shell heredoc with a cd then apply_patch fails closed",
             bash("chore", f"bash <<'EOF'\ncd {feat}\napply_patch < p.patch\nEOF"), True),
            ("chore cwd: Bash cd to a literal chore dir then apply_patch allowed",
             bash("chore", f"cd {chore} && apply_patch < p.patch"), False),
            ("chore cwd: Bash cd ~ then apply_patch allowed (home is not a feature repo)", bash("chore", "cd ~ && apply_patch < p.patch"), False),
            # pragma #149: zsh reads an unterminated [!] or [^] as any one character.
            ("feature: Bash zsh-only [!] class", bash("feat", "./apply_pa[!]ch < f"), True),
            ("feature: Bash zsh-only [^] class", bash("feat", "apply_pa[^]ch < f"), True),
            # #200: any git apply / git am / patch that can write is blocked on a feature branch; the diff is not read.
            ("feature: Bash git apply fails closed", bash("feat", "git apply x.diff"), True),
            ("feature: Bash git apply of an ordinary file's diff fails closed", bash("feat", "git apply ok.diff"), True),
            ("feature: Bash git am fails closed", bash("feat", "git am < m.mbox"), True),
            ("feature: Bash git am --continue fails closed", bash("feat", "git am --continue"), True),
            ("feature: Bash patch -p1 fails closed", bash("feat", "patch -p1 < x.diff"), True),
            ("feature: Bash PATCH (case-insensitive name) fails closed", bash("feat", "PATCH -p1 < x.diff"), True),
            ("feature: Bash piped diff into git apply fails closed", bash("feat", "cat x.diff | git apply"), True),
            ("feature: Bash bash -c git apply fails closed", bash("feat", "bash -c 'git apply x.diff'"), True),
            ("feature: Bash git apply in $( ) fails closed", bash("feat", "out=$(git apply x.diff)"), True),
            ("feature: Bash patch piped to a shell fails closed", bash("feat", "echo 'patch -p1 < x.diff' | bash"), True),
            ("feature: Bash git alias for apply fails closed", bash("feat", "git -c alias.a=apply a x.diff"), True),
            ("feature: Bash git apply --check with --apply fails closed", bash("feat", "git apply --check --apply x.diff"), True),
            ("feature: Bash git apply --check with an abbreviated --appl fails closed", bash("feat", "git apply --check --appl x.diff"), True),
            ("feature: Bash git apply --stat --no-stat fails closed", bash("feat", "git apply --stat --no-stat x.diff"), True),
            ("chore cwd: Bash git -C a feature repo apply fails closed", bash("chore", f"git -C {feat} apply x.diff"), True),
            ("chore cwd: Bash git --work-tree= a feature repo apply fails closed", bash("chore", f"git --work-tree={feat} apply x.diff"), True),
            ("chore cwd: Bash GIT_WORK_TREE= git apply fails closed", bash("chore", f"GIT_WORK_TREE={feat} git apply x.diff"), True),
            ("chore cwd: Bash patch -d a feature repo fails closed", bash("chore", f"patch -d {feat} -p1 < x.diff"), True),
            ("chore cwd: Bash patch -sd (short cluster) a feature repo fails closed", bash("chore", f"patch -sd {feat} -p1 < x.diff"), True),
            ("chore cwd: Bash patch --dir= (abbreviated) a feature repo fails closed", bash("chore", f"patch --dir={feat} -p1 < x.diff"), True),
            ("chore cwd: Bash cd into a feature repo then git apply fails closed", bash("chore", f"cd {feat} && git apply x.diff"), True),
            ("chore cwd: Bash cd $var then git apply fails closed", bash("chore", f"d={feat}; cd $d && git apply x.diff"), True),
            ("feature: Bash git apply --check is read-only", bash("feat", "git apply --check x.diff"), False),
            ("feature: Bash git apply --stat is read-only", bash("feat", "git apply --stat x.diff"), False),
            ("feature: Bash git am --abort allowed", bash("feat", "git am --abort"), False),
            ("feature: Bash git apply -h allowed", bash("feat", "git apply -h"), False),
            ("feature: Bash patch --help allowed", bash("feat", "patch --help"), False),
            ("feature: Bash git stash apply is not a diff apply", bash("feat", "git stash apply"), False),
            ("feature: Bash grep for git apply is not a run", bash("feat", "grep -rn 'git apply' docs/"), False),
            ("feature: Bash git log --grep patch is not a run", bash("feat", "git log --grep patch"), False),
            ("chore: Bash git apply allowed", bash("chore", "git apply x.diff"), False),
            ("chore: Bash patch -p1 allowed", bash("chore", "patch -p1 < x.diff"), False),
            ("chore: Bash piped diff into git apply allowed", bash("chore", "cat x.diff | git apply"), False),
            # #196 round 2: pragma #140's model (text names a tool -> blocked on feature/* unless all read-only)
            ("feature: Bash N1 --check in a comment is not read-only", bash("feat", "git apply x.diff # --check"), True),
            ("feature: Bash N1 --check as an option value is not read-only", bash("feat", "git apply --exclude --check x.diff"), True),
            ("feature: Bash N1 --check after -- is not read-only", bash("feat", "git apply x.diff -- --check"), True),
            ("feature: Bash N2 alias set in the same command", bash("feat", "git config alias.p apply; git p x.diff"), True),
            ("chore cwd: Bash N2 alias set in a feature repo", bash("chore", f"git -C {feat} config alias.p apply; git -C {feat} p x.diff"), True),
            ("feature: Bash N4 noglob wrapper", bash("feat", "noglob git apply x.diff"), True),
            ("chore cwd: Bash N4 noglob git -C a feature repo", bash("chore", f"noglob git -C {feat} apply x.diff"), True),
            ("chore cwd: Bash N4 ssh string cds into a feature repo", bash("chore", f"ssh localhost 'cd {feat} && git apply x.diff'"), True),
            ("chore cwd: Bash N4 su -c into a feature repo", bash("chore", f"su -c 'git -C {feat} apply x.diff'"), True),
            ("chore cwd: Bash N4 env -S into a feature repo", bash("chore", f"env -S 'git -C {feat} apply x.diff'"), True),
            ("chore cwd: Bash N4 script runs patch -d a feature repo", bash("chore", f"script -q /dev/null patch -d {feat} -p1 < x.diff"), True),
            ("feature: Bash N4 git-apply by path", bash("feat", "/usr/libexec/git-core/git-apply x.diff"), True),
            ("feature: Bash N4 git-am", bash("feat", "git-am < m.mbox"), True),
            ("chore cwd: Bash N6 zsh chdir into a feature repo", bash("chore", f"chdir {feat} && git apply x.diff"), True),
            ("chore cwd: Bash N6 zsh two-word cd is unresolved", bash("chore", "cd chore feat && git apply x.diff"), True),
            ("chore cwd: Bash N6 xargs -I{} git -C {}", bash("chore", f"echo {feat} | xargs -I{{}} git -C {{}} apply x.diff"), True),
            ("feature: Bash N6 --config-env alias", bash("feat", "git --config-env alias.p=P p x.diff"), True),
            ("feature: Bash N6 --attr-source before the subcommand", bash("feat", "git --attr-source HEAD p x.diff"), True),
            ("chore: Bash N8 stderr to a file named --check allowed", bash("chore", "git apply x.diff 2>--check"), False),
            ("chore cwd: Bash S1 patch ORIGFILE in a feature repo", bash("chore", f"patch {feat}/CLAUDE.md x.diff"), True),
            ("chore cwd: Bash S1 patch -o into a feature repo", bash("chore", f"patch -o{feat}/CLAUDE.md -p1 < x.diff"), True),
            ("chore cwd: Bash S1 git apply --directory into a feature repo",
             bash("chore", f"git apply --unsafe-paths --directory={feat} x.diff"), True),
            ("chore cwd: Bash N5 patch -p0 of a ../ diff into a feature repo", bash("chore", "patch -p0 < cross.diff"), True),
            ("chore cwd: Bash N5 patch -p0 of an absolute diff into a feature repo", bash("chore", "patch -p0 -i abs.diff"), True),
            ("chore cwd: Bash N5 patch -p1 of a/../../ diff", bash("chore", "patch -p1 < p1.diff"), True),
            ("chore cwd: Bash N5 git apply --unsafe-paths of a quoted ../ name", bash("chore", "git apply --unsafe-paths quoted.diff"), True),
            ("chore cwd: Bash N5 cat a ../ diff into patch", bash("chore", "cat cross.diff | patch -p0"), True),
            ("chore cwd: Bash N5 heredoc diff naming a feature repo",
             bash("chore", "patch -p0 <<'EOF'\n--- ../feat/CLAUDE.md\n+++ ../feat/CLAUDE.md\n@@ -0,0 +1 @@\n+x\nEOF"), True),
            ("chore cwd: Bash N5 quoted ../ name in a --- header", bash("chore", "patch -p0 < quoted0.diff"), True),
            ("chore cwd: Bash N5 diff over the read cap fails closed", bash("chore", "patch -p0 < big.diff"), True),
            ("chore cwd: Bash confirm F1 attached -i value", bash("chore", "patch -p0 -icross.diff"), True),
            ("chore cwd: Bash confirm F1 attached -i ./ value", bash("chore", "patch -p0 -i./cross.diff"), True),
            ("chore cwd: Bash confirm F1 quote-split file name", bash("chore", 'patch -p0 -i "cross".diff'), True),
            ("chore cwd: Bash confirm F2 diff file name with a space", bash("chore", 'patch -p0 < "space dir.diff"'), True),
            ("chore cwd: Bash scan short-option cluster -si", bash("chore", "patch -p0 -sicross.diff"), True),
            ("chore cwd: Bash scan glob diff file name", bash("chore", "patch -p0 < cr*.diff"), True),
            ("chore cwd: Bash scan zsh-style glob diff name", bash("chore", "patch -p0 < cr[^x]ss.diff"), True),
            ("chore cwd: Bash scan brace-expanded diff name", bash("chore", "patch -p0 -i cr{o,}ss.diff"), True),
            ("chore cwd: Bash scan zsh numeric range diff name", bash("chore", "patch -p0 < cross<1-9>.diff"), True),
            ("chore cwd: Bash scan zsh alternation diff name", bash("chore", "patch -p0 < cr(o|x)ss.diff"), True),
            ("chore cwd: Bash scan bash extglob diff name", bash("chore", "patch -p0 -i @(cross).diff"), True),
            ("chore cwd: Bash scan subshell beside git apply allowed", bash("chore", "git apply x.diff && (cd App && ls)"), False),
            ("chore cwd: Bash scan HEAD~1 diff piped to git apply allowed", bash("chore", "git diff HEAD~1 | git apply -R"), False),
            ("chore cwd: Bash scan test [ before git apply allowed", bash("chore", "[ -f x.diff ] && git apply x.diff"), False),
            ("chore cwd: Bash scan quoted glob in a patch command allowed", bash("chore", "git apply x.diff && ls '*.orig'"), False),
            ("chore cwd: Bash scan diff file named by a variable", bash("chore", "f=cross.diff; patch -p0 < $f"), True),
            ("chore cwd: Bash scan $'...' diff file name", bash("chore", "patch -p0 < $'cross.diff'"), True),
            ("chore cwd: Bash scan deep ../ header names over the work budget", bash("chore", "patch -p0 < deep.diff"), True),
            ("chore cwd: Bash N5 diff of an ordinary chore file allowed", bash("chore", "patch -p0 < ok.diff"), False),
            ("feature: Bash FT#234 git${IFS}apply", bash("feat", "git${IFS}apply x.diff"), True),
            ("feature: Bash FT#234 zsh git${=IFS}apply", bash("feat", "git${=IFS}apply x.diff"), True),
            ("feature: Bash FT#234 git$IFS$9am", bash("feat", "git$IFS$9am x.mbox"), True),
            ("feature: Bash FT#234 bash -c git${IFS}apply", bash("feat", "bash -c 'git${IFS}apply x.diff'"), True),
            ("feature: Bash FT#234 r2 find -exec git${IFS}apply", bash("feat", "find . -exec git${IFS}apply x.diff ';'"), True),
            ("feature: Bash FT#234 r2 watch git${IFS}apply", bash("feat", "watch -n1 git${IFS}apply x.diff"), True),
            ("feature: Bash FT#234 r2 parallel git${IFS}apply", bash("feat", "parallel git${IFS}apply ::: x.diff"), True),
            ("feature: Bash r3 absolute-path git${IFS}apply", bash("feat", "/usr/bin/git${IFS}apply x.diff"), True),
            ("feature: Bash f4791d5 git$ inside a ref argument now counts (fail closed)", bash("feat", "git push origin feature/git$BRANCH"), True),
            ("chore cwd: Bash f4791d5 git$ inside a ref argument blocks on every branch (its `$`)", bash("chore", "git push origin feature/git$BRANCH"), True),
            ("feature: Bash f4791d5 scan: quotes inside eval hide nothing", bash("feat", "eval \"bin/g''it\\${IFS}apply x.diff\""), True),
            ("feature: Bash f4791d5 scan: trap launcher", bash("feat", "trap 'bin/git${IFS}apply x.diff' EXIT"), True),
            ("feature: Bash f4791d5 scan: fd -x launcher", bash("feat", "fd -e diff -x bin/git${IFS}apply"), True),
            ("feature: Bash f4791d5 scan: entr launcher", bash("feat", "entr bin/git${IFS}apply x.diff"), True),
            ("feature: Bash ac8a069 scan: ANSI-C escape in git", bash("feat", "$'g\\x69t'${IFS}apply x.diff"), True),
            ("feature: Bash ac8a069 scan: locale-quoted git", bash("feat", "$\"git\"${IFS}apply x.diff"), True),
            ("feature: Bash ac8a069 scan: empty expansion inside git", bash("feat", "g${EMPTY}it${IFS}apply x.diff"), True),
            ("feature: Bash ac8a069 scan: brace expansion inside git", bash("feat", "gi{t,}${IFS}apply x.diff"), True),
            ("feature: Bash ac8a069 scan: special parameter before git", bash("feat", "$@git${IFS}am x.mbox"), True),
            ("feature: Bash ac8a069 scan: command substitution inside git", bash("feat", "g$(:)it${IFS}apply x.diff"), True),
            ("feature: Bash 7bf3e12 scan: $-built git apply under three bash -c levels", bash("feat", "bash -c \"bash -c 'bash -c g\\${E}it\\${IFS}apply\\ x.diff'\""), True),
            ("feature: Bash e6aa65d F1 unquoted $( ) hides a spelled git", bash("feat", "echo $(g${E}it${IFS}apply x.diff)"), True),
            ("feature: Bash e6aa65d F1 assignment from $( )", bash("feat", "x=$(${E:-git}${IFS}apply x.diff)"), True),
            ("feature: Bash e6aa65d F2 mixed default and alternate", bash("feat", "${A:-gi}${B:+x}t apply x.diff"), True),
            ("feature: Bash e6aa65d F2 mixed inside bash -c", bash("feat", "bash -c \"${A:-g}${B:+j}it apply x.diff\""), True),
            ("feature: Bash e6aa65d F3 positional default", bash("feat", "${1:-git} apply x.diff"), True),
            ("feature: Bash e6aa65d F3 array default", bash("feat", "${A[0]:-git} apply x.diff"), True),
            ("feature: Bash e6aa65d F3 indirect default", bash("feat", "${!A:-git} apply x.diff"), True),
            ("feature: Bash e6aa65d F3 zsh flag default", bash("feat", "${(L)A:-GIT} apply x.diff"), True),
            ("feature: Bash e6aa65d F4 escaped brace in an expansion", bash("feat", "g${A//\\}/}it apply x.diff"), True),
            ("feature: Bash e6aa65d F5 line continuation before an expansion", bash("feat", "g\\\n${E}it${IFS}apply x.diff"), True),
            ("feature: Bash e6aa65d F7 spelled git with $@", bash("feat", "set -- apply x.diff; g${E}it \"$@\""), True),
            ("feature: Bash e6aa65d F7 default git in a function", bash("feat", "f() { \"${A:-git}\" \"$@\"; }; f apply x.diff"), True),
            ("feature: Bash e6aa65d F8 alias on its own line", bash("feat", "shopt -s expand_aliases\nalias a=git\na apply x.diff"), True),
            ("chore cwd: Bash e6aa65d $(git rev-parse)/script is not a git run", bash("chore", "\"$(git rev-parse --show-toplevel)/scripts/x.sh\" --flag"), False),
            ("chore cwd: Bash e6aa65d $PYTHON and \"$EDITOR\" are not git", bash("chore", "$PYTHON x.py; \"$EDITOR\" f"), False),
            ("feature: Bash a882ff1 N1 spelled git behind eval", bash("feat", "eval g${E}it${IFS}apply x.diff"), True),
            ("feature: Bash a882ff1 N1 spelled git in a quoted eval string", bash("feat", "eval 'g${E}it${IFS}apply x.diff'"), True),
            ("feature: Bash a882ff1 N1 spelled git piped to sh", bash("feat", "echo g${E}it${IFS}apply x.diff | sh"), True),
            ("feature: Bash a882ff1 N1 spelled git behind find -exec", bash("feat", "find . -exec g${E}it${IFS}apply x.diff \\;"), True),
            ("feature: Bash a882ff1 N1 spelled git in a trap", bash("feat", "trap 'g${E}it${IFS}apply x.diff' EXIT"), True),
            ("feature: Bash a882ff1 N1 spelled git behind env -S", bash("feat", "env -S g${E}it${IFS}apply x.diff"), True),
            ("feature: Bash a882ff1 N1 spelled patch behind eval", bash("feat", "eval pa${E}tch${IFS}-p1 -i x.diff"), True),
            ("feature: Bash a882ff1 N1 letter-spelled git behind find -exec", bash("feat", "find . -exec ${A:-gi}${B:+x}t apply x.diff \\;"), True),
            ("feature: Bash a882ff1 N3 zsh split flag default", bash("feat", "${=A:-git} apply x.diff"), True),
            ("feature: Bash a882ff1 N4 quoted alias value", bash("feat", "shopt -s expand_aliases\nalias a=g''it\na apply x.diff"), True),
            ("feature: Bash a882ff1 N4 alias to command git", bash("feat", "shopt -s expand_aliases\nalias a='command git'\na apply x.diff"), True),
            ("feature: Bash a882ff1 N4 quoted alias word", bash("feat", "shopt -s expand_aliases\nal''ias a=git\na apply x.diff"), True),
            ("feature: Bash a882ff1 N6 glob command word", bash("feat", "/usr/bin/gi? apply x.diff"), True),
            ("feature: Bash a882ff1 N6 bracket command word", bash("feat", "/usr/bin/[g]it apply x.diff"), True),
            ("chore cwd: Bash a882ff1 N5 ${HOME} path is not git", bash("chore", "\"${HOME}/go/bin/gitleaks\" detect"), False),
            ("chore cwd: Bash a882ff1 N5 ${TMPDIR}/digit is not git", bash("chore", "${TMPDIR}/digit"), False),
            ("chore cwd: Bash a882ff1 N5 $( )/git-hooks path is not git", bash("chore", "\"$(git rev-parse --show-toplevel)/scripts/git-hooks/pre-commit\""), False),
            ("feature: Bash d5e89cd A subscript with $( ) before rm", bash("feat", "a[$(echo 1)]=1 rm CLAUDE.md"), True),
            ("feature: Bash d5e89cd A nested subscript before cp", bash("feat", "A[x[1]]=2 cp x.diff CLAUDE.md"), True),
            ("chore cwd: Bash d5e89cd B 30 KB ${!!!...} finishes", bash("chore", "${" + "!" * 30000 + "} x"), False),
            ("feature: Bash d5e89cd C alias split by a backslash-newline", bash("feat", "shopt -s expand_aliases\nalias \\\na=git\na apply x.diff"), True),
            ("feature: Bash d5e89cd C alias word split by a backslash-newline", bash("feat", "shopt -s expand_aliases\nal\\\nias a=git\na apply x.diff"), True),
            ("feature: Bash d5e89cd C zsh aliases[]", bash("feat", "aliases[a]=git; eval 'a apply x.diff'"), True),
            ("feature: Bash d5e89cd C hash -p", bash("feat", "hash -p /usr/bin/git a\na apply x.diff"), True),
            ("feature: Bash d5e89cd D glob git behind eval", bash("feat", "eval /usr/bin/gi? apply x.diff"), True),
            ("feature: Bash d5e89cd D glob git behind find -exec", bash("feat", "find . -exec /usr/bin/[g]it apply x.diff \\;"), True),
            ("feature: Bash d5e89cd D glob patch behind eval", bash("feat", "eval p?tch -p1 -i x.diff"), True),
            ("feature: Bash d5e89cd D zsh alternation", bash("feat", "/usr/bin/(g|x)it apply x.diff"), True),
            ("chore cwd: Bash d5e89cd D ordinary globs are not tools", bash("chore", "ls *.swift; rm -f build/*.o"), False),
            ("feature: Bash 45e9a30 N1 default text supplies git", bash("feat", "${E:-git}${IFS}apply x.diff"), True),
            ("feature: Bash 45e9a30 N1 default text supplies a letter", bash("feat", "gi${E:-t}${IFS}apply x.diff"), True),
            ("feature: Bash 45e9a30 N1 default text supplies apply", bash("feat", "g${E}it${IFS}${X:-apply} x.diff"), True),
            ("feature: Bash 45e9a30 N2 option between spelled git and apply", bash("feat", "g${E}it${IFS}--no-pager${IFS}apply x.diff"), True),
            ("feature: Bash 45e9a30 N2 -C between spelled git and apply", bash("feat", "g${E}it${IFS}-C${IFS}.${IFS}apply x.diff"), True),
            ("chore cwd: Bash 45e9a30 N2 -C into a feature repo", bash("chore", "g${E}it${IFS}-C${IFS}" + feat + "${IFS}apply x.diff"), True),
            ("chore cwd: Bash 45e9a30 N3 ${HOME}/git-amend is not git", bash("chore", "ls ${HOME}/git-amend"), False),
            ("chore cwd: Bash 45e9a30 N3 $HOME/.gitam is not git", bash("chore", "ls $HOME/.gitam"), False),
            ("chore cwd: Bash 45e9a30 N3 brace gitamend is not git", bash("chore", "mkdir -p {gitamend,x}"), False),
            ("feature: Bash ac8a069 scan: slash inside the IFS expansion", bash("feat", "git${IFS//x/}apply x.diff"), True),
            ("feature: Bash ac8a069 scan: path + slash inside the IFS expansion", bash("feat", "/usr/bin/git${IFS%/}am x.mbox"), True),
            ("feature: Bash b337612 relative-path ./git${IFS}apply", bash("feat", "cd /usr/bin && ./git${IFS}apply x.diff"), True),
            ("feature: Bash b337612 home-path ~/git${IFS}apply through xargs", bash("feat", "xargs ~/git${IFS}apply x"), True),
            ("feature: Bash b337612 parent-path ../bin/git${IFS}am", bash("feat", "../bin/git${IFS}am x.mbox"), True),
            ("feature: Bash b337612 scan: relative command bin/git${IFS}apply", bash("feat", "cd /usr && bin/git${IFS}apply x.diff"), True),
            ("feature: Bash b337612 scan: sudo usr/bin/git${IFS}am", bash("feat", "sudo usr/bin/git${IFS}am x.mbox"), True),
            ("feature: Bash b337612 scan: $D/git${IFS}apply as the command", bash("feat", "$D/git${IFS}apply x.diff"), True),
            ("feature: Bash c7c66c0 F2 eval quoted bin/git${IFS}apply", bash("feat", "eval 'bin/git${IFS}apply /tmp/x.diff'"), True),
            ("feature: Bash c7c66c0 F2 eval bare bin/git${IFS}apply", bash("feat", "eval bin/git${IFS}apply x.diff"), True),
            ("feature: Bash c7c66c0 F2 find -exec bin/git${IFS}apply", bash("feat", "find . -maxdepth 0 -exec bin/git${IFS}apply x.diff \\;"), True),
            ("feature: Bash c7c66c0 F2 watch bin/git${IFS}apply", bash("feat", "watch bin/git${IFS}apply x"), True),
            ("feature: Bash c7c66c0 F2 parallel bin/git${IFS}apply", bash("feat", "parallel bin/git${IFS}apply ::: x"), True),
            ("feature: Bash c7c66c0 F3 $HOME/go/bin/gitleaks is not git", bash("feat", "$HOME/go/bin/gitleaks detect"), False),
            ("chore cwd: Bash c7c66c0 F3 $HOME/bin/git-lfs is not git", bash("chore", "$HOME/bin/git-lfs ls-files"), False),
            ("parent cwd: Bash FT#234 patch -p0 of a relative name into a child feature repo", bash_at(top, "patch -p0 < child.diff"), True),
            ("parent cwd: Bash FT#234 git apply of a relative name into a child feature repo", bash_at(top, "git apply child_git.diff"), True),
            ("chore cwd: Bash FT#234 diff name through a symlink to a feature repo", bash("chore", "patch -p0 < sym.diff"), True),
            ("chore cwd: Bash FT#234 git -C lnk/.. reaches the link target's parent", bash("chore", "git -C lnk/.. apply x.diff"), True),
            ("chore cwd: Bash FT#234 cd lnk/.. before git apply", bash("chore", "cd lnk/.. && git apply x.diff"), True),
            ("feature: Bash S3 xargs sh -c", bash("feat", "ls *.diff | xargs sh -c 'git apply $0'"), True),
            ("feature: Bash S3 fish -c", bash("feat", "fish -c 'git apply x.diff'"), True),
            ("feature: Bash S5 eval", bash("feat", 'eval "git apply x.diff"'), True),
            ("chore cwd: Bash S7 find -execdir in a feature repo", bash("chore", f"find {feat} -name x.diff -execdir git apply {{}} \\;"), True),
            ("feature: Bash S8 GIT_CONFIG_* alias", bash("feat", "GIT_CONFIG_COUNT=1 GIT_CONFIG_KEY_0=alias.p GIT_CONFIG_VALUE_0=apply git p x.diff"), True),
            ("feature: Bash brace-built patch name", bash("feat", "pa{t,}ch -p1 < x.diff"), True),
            ("feature: Bash brace-built git subcommand", bash("feat", "git ap{p,}ly x.diff"), True),
            ("feature: Bash glob git subcommand", bash("feat", "git ap?ly x.diff"), True),
            ("feature: Bash git apply --check -p1 is read-only", bash("feat", "git apply --check -p1 x.diff"), False),
            ("feature: Bash git am --show-current-patch=diff is read-only", bash("feat", "git am --show-current-patch=diff"), False),
            ("feature: Bash git commit message naming patch is read-only", bash("feat", "git commit -m 'fix the am patch'"), False),
            ("feature: Bash dispatch is not patch", bash("feat", "swift test --filter DispatchTests"), False),
            ("chore cwd: Bash git apply naming only chore paths allowed", bash("chore", f"git apply {chore}/x.diff"), False),
            ("chore cwd: Bash 300 cds then git apply stays fast and allowed",
             bash("chore", " && ".join(["cd App", "cd .."] * 150) + " && git apply x.diff"), False),
            ("feature: Bash $var command word with apply_patch fails closed", bash("feat", "p=apply_patch; $p < f"), True),
            ("feature: Bash alias to apply_patch fails closed", bash("feat", "alias ap=apply_patch; ap < f"), True),
            ("feature: Bash find -exec apply_patch fails closed", bash("feat", r"find . -name '*.patch' -exec apply_patch {} \;"), True),
            ("feature: Bash parallel apply_patch fails closed", bash("feat", "parallel apply_patch ::: a.patch"), True),
            ("feature: Bash apply_patch <<< herestring fails closed", bash("feat", 'apply_patch <<< "$(cat p.patch)"'), True),
            ("feature: Bash find -name apply_patch.md is not a patch run", bash("feat", "find . -name apply_patch.md"), False),
            ("feature: Bash inline apply_patch argument to an ordinary file is blocked (shell route closed)",
             bash("feat", "apply_patch '*** Begin Patch\n*** Update File: App/A.swift\n@@\n+x\n*** End Patch'"), True),
            ("chore: Bash heredoc apply_patch of an ordinary file allowed",
             bash("chore", "apply_patch <<'EOF'\n*** Begin Patch\n*** Update File: App/A.swift\n@@\n+x\n*** End Patch\nEOF"), False),
            ("feature: Bash stray Begin Patch argument with < file", bash("feat", "apply_patch '*** Begin Patch' < real.patch"), True),
            ("feature: Bash literal \\n inline argument to CLAUDE.md",
             bash("feat", r"apply_patch '*** Begin Patch\n*** Update File: CLAUDE.md\n@@\n+x\n*** End Patch'"), True),
            ("feature: Bash unquoted heredoc expanding $(cat file)", bash("feat", "apply_patch <<EOF\n$(cat real.patch)\nEOF"), True),
            ("feature: Bash ${var} command word", bash("feat", "p=apply_patch; ${p} < f"), True),
            ("feature: Bash quote-split tool name", bash("feat", "ap''ply_patch < f"), True),
            ("feature: Bash backslash-split tool name", bash("feat", "apply\\_patch < f"), True),
            # PR #140 round 3
            ("feature: Bash path-qualified env wrapper", bash("feat", "/usr/bin/env apply_patch < f"), True),
            ("feature: Bash path-qualified sudo wrapper", bash("feat", "/usr/bin/sudo -u x apply_patch < f"), True),
            ("feature: Bash function keyword body", bash("feat", "function f { apply_patch < p; }; f"), True),
            ("feature: Bash coproc", bash("feat", "coproc apply_patch < f"), True),
            ("feature: Bash named coproc", bash("feat", "coproc P { apply_patch < f; }"), True),
            ("feature: Bash builtin command wrapper", bash("feat", "builtin command apply_patch < f"), True),
            ("feature: Bash echo piped to bash", bash("feat", "echo 'apply_patch < f' | bash"), True),
            ("feature: Bash here-string to bash", bash("feat", "bash <<< 'apply_patch < f'"), True),
            ("feature: Bash heredoc piped to sh", bash("feat", "cat <<EOF | sh\napply_patch < f\nEOF"), True),
            ("feature: Bash bash -s reading stdin", bash("feat", "echo 'apply_patch < f' | bash -s"), True),
            ("feature: Bash eval $var", bash("feat", "a='apply_patch < f'; eval $a"), True),
            ("feature: Bash bash -c \"$var\"", bash("feat", "a='apply_patch < f'; bash -c \"$a\""), True),
            ("feature: Bash bash -c -- string", bash("feat", "bash -c -- 'apply_patch < f'"), True),
            ("feature: Bash bash -o pipefail -c string", bash("feat", "bash -o pipefail -c 'apply_patch < f'"), True),
            ("feature: Bash quoted parallel template", bash("feat", "parallel 'apply_patch < {}' ::: a.patch"), True),
            ("feature: Bash find -exec sh -c", bash("feat", "find . -name '*.patch' -exec sh -c 'apply_patch < \"$0\"' {} \\;"), True),
            ("feature: Bash four nested bash -c fails closed",
             bash("feat", "bash -c \"bash -c 'bash -c \\\"bash -c apply_patch\\\"'\""), True),
            ("feature: Bash watch", bash("feat", "watch -n 1 'apply_patch < f'"), True),
            ("feature: Bash ssh remote command", bash("feat", "ssh localhost 'apply_patch < f'"), True),
            ("feature: Bash script -c", bash("feat", "script -q -c 'apply_patch < f' /dev/null"), True),
            ("feature: Bash busybox sh", bash("feat", "echo 'apply_patch < f' | busybox sh"), True),
            ("feature: Bash split name across a variable", bash("feat", "a=apply_; ${a}patch < f"), True),
            ("feature: Bash split name across two variables", bash("feat", "a=app; b=ly_patch; $a$b < f"), True),
            ("feature: Bash exported split name", bash("feat", "export a=apply_; \"${a}patch\" < f"), True),
            ("feature: Bash source process substitution", bash("feat", "source <(echo 'apply_patch < f')"), True),
            ("feature: Bash dot /dev/stdin", bash("feat", "echo 'apply_patch < f' | . /dev/stdin"), True),
            ("feature: Bash command -v apply_patch is a lookup", bash("feat", "command -v apply_patch"), False),
            ("feature: Bash command -V applypatch is a lookup", bash("feat", "command -V applypatch"), False),
            ("feature: Bash bash -c naming apply_patch in echo (bash not allowlisted)", bash("feat", "bash -c 'echo apply_patch'"), True),
            ("feature: Bash bash script file plus a grep (bash not allowlisted)", bash("feat", "grep -c apply_patch log.txt; bash build.sh"), True),
            ("feature: Bash unrelated variable assignment", bash("feat", "a=1; grep -rn apply_patch $a"), False),
            ("feature: Bash command -v after the tool name is not a lookup", bash("feat", "command apply_patch -v < f"), True),
            ("feature: Bash bash -co cluster", bash("feat", "bash -co pipefail 'apply_patch < f'"), True),
            ("feature: Bash bash -oc cluster", bash("feat", "bash -oc pipefail 'apply_patch < f'"), True),
            ("feature: Bash split name built with +=", bash("feat", "a=app; a+=ly_patch; $a < f"), True),
            ("feature: Bash split name built with printf -v", bash("feat", "printf -v a '%s%s' app ly_patch; $a < f"), True),
            ("feature: Bash positional parameters after set", bash("feat", "set -- app ly_patch; \"$1$2\" < f"), True),
            ("feature: Bash env -S", bash("feat", "env -S 'apply_patch < f'"), True),
            ("feature: Bash env --split-string=", bash("feat", "/usr/bin/env '--split-string=apply_patch < f'"), True),
            ("feature: Bash sudo -s from stdin", bash("feat", "echo 'apply_patch < f' | sudo -s"), True),
            ("feature: Bash bash /dev/stdin", bash("feat", "echo 'apply_patch < f' | bash /dev/stdin"), True),
            ("feature: Bash sh -- /dev/fd/0", bash("feat", "echo 'apply_patch < f' | sh -- /dev/fd/0"), True),
            ("feature: Bash sudo -su cluster", bash("feat", "echo 'apply_patch < f' | sudo -su root"), True),
            ("feature: Bash env -iS cluster", bash("feat", "env -iS 'apply_patch < f'"), True),
            ("feature: Bash env --split prefix", bash("feat", "env --split 'apply_patch < f'"), True),
            # PR #140 allowlist redesign: round-5 forms must block, read-only tools stay allowed
            ("feature: Bash flock -c", bash("feat", "flock /tmp/l -c 'apply_patch < f'"), True),
            ("feature: Bash runuser -l -c", bash("feat", "runuser -l me -c 'apply_patch < f'"), True),
            ("feature: Bash caffeinate -t", bash("feat", "caffeinate -t 5 apply_patch < f"), True),
            ("feature: Bash arch -arch", bash("feat", "arch -arch arm64 apply_patch < f"), True),
            ("feature: Bash chroot --userspec", bash("feat", "chroot --userspec u:g / apply_patch < f"), True),
            ("feature: Bash bash /./dev/stdin", bash("feat", "echo 'apply_patch < f' | bash /./dev/stdin"), True),
            ("feature: Bash cd /dev then bash stdin", bash("feat", "cd /dev && echo 'apply_patch < f' | bash stdin"), True),
            ("feature: Bash xargs env", bash("feat", "echo apply_patch | xargs env"), True),
            ("feature: Bash xargs -J env", bash("feat", "echo apply_patch | xargs -J % env %"), True),
            ("feature: Bash doas -s from stdin", bash("feat", "echo 'apply_patch < f' | doas -s"), True),
            ("feature: Bash sed e command", bash("feat", "echo x | sed 'e apply_patch < f'"), True),
            ("feature: Bash rg --pre", bash("feat", "rg --pre apply_patch x ."), True),
            ("feature: Bash find -exec", bash("feat", "find . -name x -exec apply_patch {} +"), True),
            ("feature: Bash path-qualified grep is not allowlisted", bash("feat", "./grep apply_patch < f"), True),
            ("feature: Bash git -c then commit", bash("feat", "git -c alias.x='!apply_patch < f' x"), True),
            ("feature: Bash unquoted heredoc body with $( )", bash("feat", "cat <<EOF\n$(apply_patch < f)\nEOF"), True),
            ("feature: Bash quoted heredoc body is data", bash("feat", "cat <<'EOF'\n$(apply_patch < f)\nEOF"), False),
            ("feature: Bash arithmetic subscript", bash("feat", "a='x[$(apply_patch < f)]'; echo $((a))"), True),
            ("feature: Bash printf -v subscript", bash("feat", "printf -v 'a[$(apply_patch < f)]' x"), True),
            ("feature: Bash name in an assignment value", bash("feat", "p=apply_patch; echo ok"), True),
            ("feature: Bash nested escaped backticks", bash("feat", "echo `echo \\`apply_patch < f\\``"), True),
            ("feature: Bash @P prompt expansion", bash("feat", "a='$(apply_patch < f)'; echo ${a@P}"), True),
            ("feature: Bash upper-case name", bash("feat", "APPLY_PATCH < f"), True),
            ("feature: Bash name split by a default", bash("feat", "apply_pat${x:-ch} < f"), True),
            ("feature: Bash name split by brace expansion", bash("feat", "apply_{pa,xx}tch < f"), True),
            ("feature: Bash git log with a $var argument", bash("feat", "o=--output=CLAUDE.md; git log $o --grep apply_patch"), True),
            ("feature: Bash sed brace-expanded script", bash("feat", "sed -n {'e apply_patch < f',1p} x"), True),
            ("feature: Bash sed second -e script", bash("feat", "sed -n 1p -e 'e apply_patch < f' x"), True),
            ("feature: Bash sed -f script file", bash("feat", "sed -n -f s.sed apply_patch.md"), True),
            ("feature: Bash GIT_PAGER prefix", bash("feat", "GIT_PAGER='apply_patch < f' git log"), True),
            ("feature: Bash git log --output", bash("feat", "git log --output=CLAUDE.md --grep apply_patch"), True),
            ("feature: Bash allowlist: sed -n print", bash("feat", "sed -n '/apply_patch/p' f"), False),
            ("feature: Bash allowlist: rg", bash("feat", "rg -n 'apply_?patch' scaffold"), False),
            ("feature: Bash allowlist: git log --grep", bash("feat", "git log --oneline --grep apply_patch"), False),
            ("feature: Bash allowlist: git commit -m", bash("feat", "git commit -m 'block shell apply_patch'"), False),
            ("feature: Bash allowlist: pipe of grep and cat", bash("feat", "cat CHANGELOG.md | grep -c apply_patch"), False),
            ("feature: Bash allowlist: which and type", bash("feat", "which apply_patch; type applypatch"), False),
            ("feature: Bash allowlist: grep with $( ) of an allowed command", bash("feat", "grep -c apply_patch $(echo f)"), False),
            ("feature: Bash allowlist: $( ) running the tool", bash("feat", "echo $(apply_patch < f)"), True),
            # PR #140 round 4 + security scan of b772ccf
            ("feature: Bash xargs-fed sh -c", bash("feat", "echo 'apply_patch < f' | xargs -I{} sh -c {}"), True),
            ("feature: Bash xargs placeholder command", bash("feat", "echo apply_patch | xargs -I{} {} < f"), True),
            ("feature: Bash xargs -I custom placeholder", bash("feat", "echo apply_patch | xargs -I CMD CMD < f"), True),
            ("feature: Bash flock wrapper", bash("feat", "flock /tmp/l apply_patch < f"), True),
            ("feature: Bash arch wrapper", bash("feat", "arch -arm64 apply_patch < f"), True),
            ("feature: Bash xcrun wrapper", bash("feat", "xcrun apply_patch < f"), True),
            ("feature: Bash doas wrapper", bash("feat", "doas -u root apply_patch < f"), True),
            ("feature: Bash chroot wrapper", bash("feat", "chroot / apply_patch < f"), True),
            ("feature: Bash csh -c", bash("feat", "csh -c 'apply_patch < f'"), True),
            ("feature: Bash tmux new", bash("feat", "tmux new 'apply_patch < f'"), True),
            ("feature: Bash at reads stdin", bash("feat", "echo 'apply_patch < f' | at now"), True),
            ("feature: Bash sudo -u root -s", bash("feat", "echo 'apply_patch < f' | sudo -u root -s"), True),
            ("feature: Bash env -u X -S", bash("feat", "env -u X -S 'apply_patch < f'"), True),
            ("feature: Bash env A=1 env -S", bash("feat", "env A=1 env -S 'apply_patch < f'"), True),
            ("feature: Bash env --s prefix", bash("feat", "env --s 'apply_patch < f'"), True),
            ("feature: Bash sudo --sh prefix", bash("feat", "echo 'apply_patch < f' | sudo --sh"), True),
            ("feature: Bash bash /dev/./stdin", bash("feat", "echo 'apply_patch < f' | bash /dev/./stdin"), True),
            ("feature: Bash bash /proc/PID/fd/0", bash("feat", "echo 'apply_patch < f' | bash /proc/123/fd/0"), True),
            ("feature: Bash bash $path script", bash("feat", "p=/dev/stdin; echo 'apply_patch < f' | bash $p"), True),
            ("feature: Bash xargs grep naming the tool (ls/xargs not allowlisted)", bash("feat", "ls | xargs grep -l apply_patch"), True),
            ("feature: Bash sudo -u with a grep naming the tool (sudo not allowlisted)", bash("feat", "sudo -u me grep -c apply_patch log"), True),
            ("feature: Bash su from stdin", bash("feat", "echo 'apply_patch < f' | su me"), True),
            ("feature: Bash $HOME-based command word is readable", bash("feat", "\"$HOME/bin/lint\" App/A.swift"), False),
            # PR #140 security scan of fcbef38
            ("feature: Bash name split by nested braces", bash("feat", "apply_{{p,q}a,x}tch < f"), True),
            ("feature: Bash name split by a brace range", bash("feat", "apply_pa{s..t}ch < f"), True),
            ("feature: Bash name split by a stepped brace range", bash("feat", "apply_pa{a..z..19}ch < f"), True),
            ("feature: Bash name split by a +step brace range (bash 4+)", bash("feat", "apply_pa{s..t..+1}ch < f"), True),
            ("feature: Bash brace alternative with a quoted space", bash("feat", "apply_{'x y',pa}tch < f"), True),
            ("feature: Bash many brace groups not spelling the tool are readable", bash("feat", "x" + "{a,b}" * 13 + " < f"), False),
            ("feature: Bash quoted brace shifts the pairing", bash("feat", "apply_{'{',pa}tch < f"), True),
            ("feature: Bash quoted close brace shifts the pairing", bash("feat", "apply_{x'}'yyy,pa}tch < f"), True),
            ("feature: Bash name split across a heredoc apostrophe", bash("feat", "cat <<E\n'\nE\napply_{'x y',pa}tch < f"), True),
            ("feature: Bash 64k unmatched braces after the name", bash("feat", "apply_patch_x " + "{" * 65536), True),
            ("feature: Bash ANSI-C hex escape", bash("feat", "$'\\x61pply_patch' < f"), True),
            ("feature: Bash ANSI-C octal escape", bash("feat", "$'\\141pply_patch' < f"), True),
            ("feature: Bash empty $'' and $\"\" splits", bash("feat", "apply_pa$''tch < f; apply_pa$\"\"tch < f"), True),
            ("feature: Bash empty $( ) split", bash("feat", "apply_p$(:)atch < f"), True),
            ("feature: Bash empty backtick split", bash("feat", "apply_p`:`atch < f"), True),
            ("feature: Bash $@ split", bash("feat", "apply_pa$@tch < f"), True),
            ("feature: Bash glob command word", bash("feat", "apply_pat?h < f"), True),
            ("feature: Bash glob class command word", bash("feat", "apply_pat[c]h < f"), True),
            ("feature: Bash star glob alone is not a name", bash("feat", "ls * | xargs wc -l"), False),
            ("feature: Bash 100k unterminated $( stay fast", bash("feat", "apply_patch_x " + "$(" * 100000), True),
            ("feature: Bash empty $( ) with a quoted paren", bash("feat", "apply_p$(: \")\")atch < f"), True),
            ("feature: Bash empty $( ) with quoted paren and text", bash("feat", "apply_p$(: \")x\")atch < f"), True),
            ("feature: Bash ANSI-C short \\u escape fails closed", bash("feat", "$'\\u61'pply_patch < f"), True),
            ("feature: Bash short glob as the command word", bash("feat", "a* < f"), True),
            ("feature: Bash short glob behind sudo", bash("feat", "sudo /tmp/a* < f"), True),
            ("feature: Bash backslash-newline join", bash("feat", "apply\\\npatch < f"), True),
            ("feature: Bash backslash-newline join with underscore", bash("feat", "apply_pa\\\ntch < f"), True),
            ("feature: Bash glob inside a brace group", bash("feat", "ap?lypatc{h,} < f"), True),
            ("feature: Bash brace group around a glob", bash("feat", "{apply_pat?h,} < f"), True),
            ("feature: Bash 25k open brackets stay fast", bash("feat", "apply_patch < f; echo a" + "[" * 25000), True),
            ("feature: Bash long subscript word is not a glob name", bash("feat", "d['k']['s']=" + "x" * 400 + "; make"), False),
            ("feature: Bash command over the size cap", bash("feat", "echo " + "x" * 40000), True),
            ("feature: Bash [^x] glob class", bash("feat", "apply_pa[^x]ch < f"), True),
            ("feature: Bash POSIX glob class", bash("feat", "apply_pa[[:alpha:]]ch < f"), True),
            ("chore: Bash command over the size cap", bash("chore", "echo " + "x" * 40000), True),
            ("feature: Bash negated POSIX glob class", bash("feat", "apply_pa[^[:digit:]]ch < f"), True),
            ("feature: Bash multi-class glob bracket", bash("feat", "apply_pa[[:alpha:][:digit:]]ch < f"), True),
            ("feature: Bash mixed glob bracket", bash("feat", "apply[[:punct:]_]patch < f"), True),
            ("feature: Bash glob bracket with a leading ] member", bash("feat", "apply_pa[][:alpha:]]ch < f"), True),
            ("feature: Bash glob bracket with a literal [ member", bash("feat", "apply_pa[[a[:alpha:]]ch < f"), True),
            ("feature: Bash collating and equivalence glob members", bash("feat", "apply_pa[[.t.]]ch < f; apply_pa[[=t=]]ch < f"), True),
            ("feature: Bash allowlist: grep with an ANSI-C newline", bash("feat", "grep -c $'apply_patch\\n' f"), False),
            ("feature: Bash 128k dots in an open brace stay fast", bash("feat", "apply_pa{,}ch {" + ".." * 65536 + "; apply_{x,pa}tch < f"), True),
            ("feature: Bash brace copy not spelling the tool is readable", bash("feat", "cp Package.swift{,.bak}; make apply"), False),
            ("feature: Bash unquoted braces in a read-only command naming the tool", bash("feat", "rg {--pre=sh,apply_patch} f"), True),
            ("feature: Bash $[ ] arithmetic", bash("feat", "a='x[$(ap\"\"ply_patch < f)]'; echo $[a]"), True),
            ("feature: Bash ${v:off:len} offset", bash("feat", "a='x[$(ap\"\"ply_patch < f)]'; v=abc; echo ${v:a:1}"), True),
            ("feature: Bash quoted $( ) naming the tool", bash("feat", "grep -c apply_patch \"$(echo f)\""), True),
            ("feature: Bash integer brace range not naming the tool is readable", bash("feat", "for i in {1..99999}; do make t$i; done"), False),
            ("feature: Bash allowlist: grep with quoted braces", bash("feat", "grep -E 'apply_patch{1,2}' f"), False),
            # pragma #146: output redirects and rg options that run a program
            ("feature: Bash redirect writes a script rg --hostname-bin runs", bash("feat", "echo apply_patch > tool.sh; rg --hostname-bin=./tool.sh --hyperlink-format=default apply_patch f"), True),
            ("feature: Bash append to a git hook then commit", bash("feat", "printf 'apply_patch < p\\n' >> .git/hooks/pre-commit; git commit -m x"), True),
            ("feature: Bash output redirect forms", bash("feat", "echo apply_patch >| t; echo apply_patch &> t; echo apply_patch &>> t"), True),
            ("feature: Bash read-write and >& file redirects", bash("feat", "cat <> t apply_patch.md; echo apply_patch >& t"), True),
            ("feature: Bash fd-numbered output redirect", bash("feat", "grep apply_patch f 2> err.log"), True),
            ("feature: Bash leading output redirect", bash("feat", "> t echo apply_patch"), True),
            ("feature: Bash quoted redirect target", bash("feat", "echo apply_patch > 'tool.sh'"), True),
            ("feature: Bash output process substitution", bash("feat", "echo apply_patch > >(sh)"), True),
            ("feature: Bash rg --hostname-bin alone", bash("feat", "rg --hostname-bin ./t --hyperlink-format=default apply_patch f"), True),
            ("feature: Bash rg -z", bash("feat", "rg -z apply_patch f.gz"), True),
            ("feature: Bash rg --search-zip", bash("feat", "rg --search-zip apply_patch f.gz"), True),
            ("feature: Bash rg -z in a short-option cluster", bash("feat", "rg -nz apply_patch f.gz"), True),
            ("feature: Bash rg -z in a cluster with an attached value", bash("feat", "rg -zA3 apply_patch f.gz"), True),
            ("feature: Bash allowlist: redirects to /dev/null and fd dups", bash("feat", "rg apply_patch f 2>/dev/null >/dev/null; grep -c apply_patch f 2>&1 >&2 &>/dev/null"), False),
            ("feature: Bash allowlist: input redirect and here-string", bash("feat", "grep -c apply_patch < f; grep apply_patch <<< 'x'"), False),
            ("feature: Bash allowlist: rg --no-search-zip and -n", bash("feat", "rg -n --no-search-zip apply_patch f"), False),
            # pragma #145: a quoted or escaped `]` inside a glob bracket is a member, not the closer
            ("feature: Bash escaped ] in a glob bracket", bash("feat", "./apply_pa[x\\]t]ch"), True),
            ("feature: Bash single-quoted ] in a glob bracket", bash("feat", "./apply_pa[x']'t]ch"), True),
            ("feature: Bash double-quoted ] in a glob bracket", bash("feat", "./apply_pa[x\"]\"t]ch"), True),
            ("feature: Bash quoted member run ending in ]", bash("feat", "./apply_pa['t]']ch"), True),
            ("feature: Bash quoted ] glob as an argument", bash("feat", "bash apply_pa[x']'t]ch"), True),
            # PR #148 round 1: quoted brackets mixed with expansions, and an unquoted bracket glob as the command
            ("feature: Bash quoted ] glob with an unset variable", bash("feat", "./apply_p${u}a[x']'t]ch"), True),
            ("feature: Bash quoted ] glob with $'...' members", bash("feat", "./apply$'_'pa[x$']'t]ch"), True),
            ("feature: Bash quoted ] glob with an empty $( )", bash("feat", "./apply_p$(:)a[x']'t]ch"), True),
            ("feature: Bash quoted ] glob after a quoted variable", bash("feat", "./apply_pa\"$u\"[x']'t]ch"), True),
            ("feature: Bash quoted variable ends before the next letter", bash("feat", "./app\"$u\"\\ly_pa\\tc[\"$u\"]'h'x-]"), True),
            ("feature: Bash quoted ] glob after an empty brace expansion", bash("feat", "./apply_pa{,}[x']'t]ch"), True),
            ("feature: Bash quoted space inside a glob bracket", bash("feat", "./apply_pa[x' ]'t]ch"), True),
            ("feature: Bash brace alternatives beside a quoted ] glob", bash("feat", "./apply_p{a,b}[x']'t]ch; ./apply_pa[x']'t]c{h,i}"), True),
            ("feature: Bash brace range beside a quoted ] glob", bash("feat", "./apply_p{a..a}[x']'t]ch"), True),
            ("feature: Bash short bracket glob as the command", bash("feat", "./a[p]*h; ./?p[[p]*y_???ch"), True),
            ("feature: Bash 24k quoted brackets stay fast", bash("feat", "echo " + "['['" * 6000 + "; ./apply_pa[x']'t]ch"), True),
            ("feature: Bash allowlist: grep for a quoted bracket pattern", bash("feat", "grep -c 'apply_pa[x]t]ch' f; echo apply_pa'[t]'ch"), False),
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
        # #196 confirm on acdbbd8: a check past MAX_CHECK_SECONDS blocks instead of running into the hook timeout
        saved = MAX_CHECK_SECONDS
        globals()["MAX_CHECK_SECONDS"] = -1
        try:
            message = evaluate(bash("chore", "patch -p0 < ok.diff")) or ""
        finally:
            globals()["MAX_CHECK_SECONDS"] = saved
        ok = "took over" in message
        failures += 0 if ok else 1
        print(f"{'PASS' if ok else 'FAIL'}  in-process  chore cwd: Bash patch-tool check past MAX_CHECK_SECONDS blocks")
        # #196 confirm on b337612: the write-target loop runs under the deadline too, and checks each target once
        globals()["MAX_CHECK_SECONDS"] = -1
        try:
            message = evaluate(bash("chore", "echo x > ../feat/CLAUDE.md")) or ""
        finally:
            globals()["MAX_CHECK_SECONDS"] = saved
        ok = "files this command writes took over" in message
        failures += 0 if ok else 1
        print(f"{'PASS' if ok else 'FAIL'}  in-process  chore cwd: Bash write-target check past MAX_CHECK_SECONDS blocks")
        # #196 confirm on c7c66c0: a shell-run apply_patch's names x directories is capped before the list is built
        message = evaluate(bash("chore", "".join(f"cd d{i}; " for i in range(400)) + "apply_patch <<'E'\n"
                                + "".join(f"*** Update File: f{i}\n" for i in range(300)) + "E")) or ""
        ok = "file names with directories" in message
        failures += 0 if ok else 1
        print(f"{'PASS' if ok else 'FAIL'}  in-process  chore cwd: Bash shell apply_patch names x dirs over the cap blocks first")
        calls, real_git = [], globals()["git"]
        globals()["git"] = lambda *a: calls.append(a) or real_git(*a)
        try:
            message = evaluate(bash("chore", ">CLAUDE.md; " * 50))
        finally:
            globals()["git"] = real_git
        ok = message is None and len(calls) <= 2
        failures += 0 if ok else 1
        print(f"{'PASS' if ok else 'FAIL'}  in-process  chore cwd: Bash a repeated write target is checked once ({len(calls)} git calls)")

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
