#!/usr/bin/env python3
"""
Detects gate-weakening: changes on this branch that make /gates easier to
pass without fixing the underlying violation, rather than a legitimate gate
or config maintenance change. Every other gate in this pipeline checks the
code; this one checks that nobody edited the ruler.

Flags, via a git diff against the base branch:
  1. A gate-definition file (see GATE_DEFINITION_FILES/GUARDED_PATH_GLOBS
     below for the exact list — not repeated here so this docstring can't
     drift out of sync with it the way an inline copy already had)
     touched on a feature/* branch — a real feature never needs to change
     what counts as passing. Only reliably checkable when the actual branch
     name is known (see current_branch()). A guarded file renamed away is
     caught through its rename source.
  2. A previously-existing test file deleted rather than fixed. Same
     rename caveat as #1 — a test renamed away rather than deleted outright
     isn't caught.
  3. A new suppression/skip marker introduced in the diff: swiftlint:disable
     and XCTSkip are checked everywhere; a Swift Testing .disabled() trait
     is checked in test files only, since SwiftUI's .disabled(condition)
     view modifier uses the identical syntax in ordinary application code.
  4. An unfinished stub newly introduced in non-test code: a bare or
     empty-string fatalError()/preconditionFailure(), or either with a
     placeholder message ("not implemented", "todo") — a message alone
     doesn't make it legitimate, only a real, specific reason does.
  5. A numeric threshold in a gate-definition file lowered by this diff,
     matched by normalized line content within each diff hunk (not
     position) so an unrelated number elsewhere in the file, or an unrelated
     line added/removed alongside the real edit, can't produce a false
     positive or mask a real change; every percentage on a matched line is
     compared, not just the first, so a line naming more than one threshold
     doesn't hide a drop in a later one.

Usage: python3 scripts/check_gate_integrity.py [base_ref] [branch]
  base_ref defaults to 'develop', matching this pipeline's other gate
  scripts (see check_tdd_commit_order.py) and its gitflow convention.
  branch overrides the detected current branch — needed in CI, where a PR
  is usually checked out at a detached commit rather than a real branch;
  GitHub Actions' pull_request trigger sets GITHUB_HEAD_REF for this
  automatically, but any other CI provider (or a push-triggered GHA run)
  has no such env var, so pass the branch explicitly there.
"""
import difflib
import fnmatch
import os
import re
import subprocess
import sys

BASE_REF = sys.argv[1] if len(sys.argv) > 1 else "develop"
BRANCH_OVERRIDE = sys.argv[2] if len(sys.argv) > 2 else None

# core.quotepath=false: without it, git octal-escapes any non-ASCII byte in a
# path (e.g. "café.swift" -> "caf\303\251.swift") in diff/--name-status
# output, which would never match a plain-ASCII GATE_DEFINITION_FILES entry
# or a suppression-scan path check. --src-prefix/--dst-prefix: diff_by_path's
# a/ b/ header matching must not silently break under a contributor's global
# diff.noprefix/diff.mnemonicPrefix git config — applied here, to every
# git-diff call, not just the narrower text_diff_for() re-fetch below, so a
# config-dependent header format can't mis-parse (or drop) the main diff
# every other check reads. --literal-pathspecs: text_diff_for() passes changed
# paths after `--`, and a name like ":(top)x.swift" must name that file, not
# act as pathspec magic.
GIT_DIFF_BASE_ARGS = (
    "git", "-c", "core.quotepath=false", "--literal-pathspecs",
    "diff", "--src-prefix=a/", "--dst-prefix=b/",
)

GATE_DEFINITION_FILES = (
    ".claude/skills/gates/SKILL.md",
    "AGENTS.md",
    "CLAUDE.md",
    ".claude/context/invariants.md",
    "CONSTRAINTS.md",
    ".claude/skills/deterministic-pr-gates/SKILL.md",
)
# Wider than GATE_DEFINITION_FILES, and used by check #1 only: every file the
# PreToolUse hook (.claude/hooks/guard_protected_paths.py; scaffold/.claude/hooks/
# in the pragma repo) blocks on a feature/* branch. The hook stops the edit live but only inside a Claude Code
# or Codex CLI session; a plain commit + push bypasses it, so this list is the CI-side
# backstop for the same set. Keep it in step with the hook's PROTECTED_GLOBS.
# `*` crosses `/` (fnmatch), so nested paths match, and each glob is also
# tried under any subdirectory (`ios/CLAUDE.md`, `ios/.claude/settings.json`),
# so a Claude project inside a monorepo is covered. The match is
# case-insensitive because macOS volumes are.
GUARDED_PATH_GLOBS = (
    "scripts/check_*",
    ".claude/skills/*/SKILL.md",
    ".claude/skills/*/reference.md",
    "AGENTS.md",
    "CLAUDE.md",
    "CONSTRAINTS.md",
    ".claude/context/invariants.md",
    ".claude/settings.json",
    ".claude/hooks/*",
    "scripts/pipeline_lanes.json",
    "scripts/detect_build_target.sh",
    "scripts/select_simulator.py",
    ".github/workflows/*",
    ".codex/hooks.json",
    ".codex/config.toml",
)
# Suppression/stub detection (checks 3 & 4) is about shipped application
# code — a doc file describing these exact patterns in prose (this script's
# own docstring, a CHANGELOG entry writing up a past bug) isn't code and a
# substring match can't tell the two apart, so doc extensions are excluded
# outright rather than relying only on the narrower GATE_DEFINITION_FILES
# exclusion below. Only *this* file is excluded from
# checks 3 & 4 by path, not every scripts/check_*.py — check #1 already
# permits editing gate scripts on a chore/*/fix/* branch, and excluding a
# sibling script's real code (not just prose) from stub/suppression
# detection there would silently defeat that gate for those files.
DOC_EXTENSIONS = (".md", ".txt", ".rst")
SELF_PATH = "scripts/check_gate_integrity.py"


def is_guarded_path(path):
    """True for a gate-definition file or anything the guard hook protects."""
    if path in GATE_DEFINITION_FILES:
        return True
    low = path.lower()
    return any(
        fnmatch.fnmatchcase(low, g.lower()) or fnmatch.fnmatchcase(low, "*/" + g.lower())
        for g in GUARDED_PATH_GLOBS
    )


TEST_PATH_SEGMENT = re.compile(r"(^|/)tests?(/|$)", re.IGNORECASE)
TEST_FILENAME_UNDERSCORE = re.compile(r"(^|/)(test_[^/]+|[^/]+_test)\.py$", re.IGNORECASE)
# Swift's own convention: <Type>Tests.swift (plural) or <Type>Test.swift
# (singular, less common but real) — anchored to the end of the filename so
# e.g. "ABTestsManager.swift" (a feature-flag file, not a test) doesn't match.
TEST_FILENAME_SUFFIX = re.compile(r"[^/]*Tests?\.(swift|py)$")

# Only .disabled( is ambiguous with SwiftUI's .disabled(condition) view
# modifier — swiftlint:disable and XCTSkip have no such ambiguity in
# application code, so they're checked everywhere, not just in test files.
# XCTSkipIf/XCTSkipUnless are the same suppression mechanism as bare
# XCTSkip, just conditional — matched too, not just the unconditional form.
UNAMBIGUOUS_SUPPRESSION_PATTERNS = (
    re.compile(r"^\+.*//\s*swiftlint:disable"),
    re.compile(r"^\+.*\bXCTSkip(If|Unless)?\b"),
)
TEST_ONLY_SUPPRESSION_PATTERNS = (
    re.compile(r"^\+.*\.disabled\("),  # Swift Testing trait
)
# A message alone doesn't make a fatalError/preconditionFailure legitimate —
# "not implemented"/"todo" are placeholder text, not a documented
# exhaustiveness reason — so those are flagged even though they carry a
# message; anything else with a message is treated as a real, intentional
# reason and left alone.
PLACEHOLDER_STUB_MESSAGE = re.compile(
    r'(fatalError|preconditionFailure)\(\s*"(not implemented|unimplemented|todo|TODO)', re.IGNORECASE
)
# Bare call, or a call whose only argument is an empty string — neither
# carries an actual reason, so both count as an unfinished placeholder.
BARE_STUB_PATTERNS = (
    re.compile(r'\bfatalError\(\s*(""\s*)?\)'),
    re.compile(r'\bpreconditionFailure\(\s*(""\s*)?\)'),
)
PERCENT_PATTERN = re.compile(r"(\d+(?:\.\d+)?)\s*%")


def run(*args):
    try:
        # errors="replace": a diff containing bytes that aren't valid UTF-8
        # (a binary-ish file, or another encoding entirely) must not crash
        # this script outright — decode what's decodable and substitute the
        # rest, rather than raising UnicodeDecodeError mid-gate-run. Decoded
        # here, not with text=True: universal newlines would turn a bare "\r"
        # in a diff line or a path into "\n", which could start a fake
        # `diff --git` block or break a path match.
        return subprocess.run(
            args, capture_output=True, check=True
        ).stdout.decode("utf-8", errors="replace")
    except subprocess.CalledProcessError as e:
        err = (e.stderr or b"").decode("utf-8", errors="replace").strip()
        print(f"ERROR: `{' '.join(args)}` failed — {err or e}", file=sys.stderr)
        # BASE_REF never appears as its own arg — every call site embeds it in a
        # formatted ref spec like f"{BASE_REF}...HEAD" — so this has to search
        # each arg for it as a substring, not check tuple membership.
        if any(BASE_REF in a for a in args):
            print(
                f"Gate integrity could not run — confirm this branch has a valid "
                f"'{BASE_REF}' base ref to compare against (pass a different one "
                "as this script's first argument if your project's trunk isn't "
                "named 'develop').",
                file=sys.stderr,
            )
        else:
            print("Gate integrity could not run — see the git error above.", file=sys.stderr)
        sys.exit(2)


def current_branch():
    # Explicit override (this script's 2nd argument) wins — works for any CI
    # provider. Otherwise, GitHub Actions specifically sets GITHUB_HEAD_REF
    # to the real source branch on a pull_request-triggered run, which
    # checks out a detached commit rather than that branch; that env var is
    # unset on a push-triggered run or any non-GHA CI, so it's a narrower
    # fallback than the override, not a full fix for detached HEAD in
    # general. Last resort is git's own detection (capture_pipeline_metrics.py's
    # existing convention), returning the literal string "HEAD" in
    # detached-HEAD state rather than an empty string, so that state is
    # explicit, not silent.
    return (
        BRANCH_OVERRIDE
        or os.environ.get("GITHUB_HEAD_REF")
        or run("git", "rev-parse", "--abbrev-ref", "HEAD").strip()
    )


def is_test_path(path):
    return (
        bool(TEST_FILENAME_SUFFIX.search(path))
        or bool(TEST_PATH_SEGMENT.search(path))
        or bool(TEST_FILENAME_UNDERSCORE.search(path))
    )


# git's C-style path quoting (quote.c) under core.quotepath=false: control
# characters and DEL become escapes, `"` and `\` get a backslash, and
# non-ASCII stays as is.
CQ_NAMED = {"\a": "a", "\b": "b", "\t": "t", "\n": "n", "\v": "v", "\f": "f", "\r": "r", '"': '"', "\\": "\\"}


def git_quoted(prefix, path):
    """prefix + path as a `diff --git` header prints it: bare, or in quotes with
    escapes when any character needs one (diff.c quote_two)."""
    name = prefix + path
    if not any(c in CQ_NAMED or ord(c) < 0x20 or ord(c) == 0x7F for c in name):
        return name
    out = []
    for c in name:
        if c in CQ_NAMED:
            out.append("\\" + CQ_NAMED[c])
        elif ord(c) < 0x20 or ord(c) == 0x7F:
            out.append("\\%03o" % ord(c))
        else:
            out.append(c)
    return '"' + "".join(out) + '"'


def stop(reason):
    print(f"Gate integrity could not run — {reason}", file=sys.stderr)
    sys.exit(2)


def changed_files(*diff_args):
    """[(status, old_path, new_path)] from `git diff -z --name-status`. Fields are
    NUL-separated, so each path is read exactly as stored, whatever it holds.
    status is A, D or R; any other letter (M, C, T) counts as M, as before."""
    fields = run(*GIT_DIFF_BASE_ARGS, "-z", "--name-status", *diff_args).split("\0")
    entries = []
    i = 0
    while i + 1 < len(fields):
        letter = fields[i][:1]
        if letter in ("R", "C"):
            old, new = fields[i + 1], fields[i + 2]
            i += 3
        else:
            old = new = fields[i + 1]
            i += 2
        entries.append((letter if letter in ("A", "D", "R") else "M", old, new))
    return entries


def diff_by_path(*diff_args):
    """({path: diff_text}, changed_files entries) for one diff. Each `diff --git`
    block is matched to a path from changed_files() by the exact header git
    prints for that path, never by splitting the header on " b/": a file name
    can hold " b/" itself (#159). A typechange prints two blocks under one
    header, and both are kept. A block that matches no path, a header two
    paths share, or a path with no block stops the run (exit 2) instead of
    checking the wrong file."""
    entries = changed_files(*diff_args)
    path_by_header = {}
    for _, old, new in entries:
        header = f"diff --git {git_quoted('a/', old)} {git_quoted('b/', new)}"
        if header in path_by_header:
            stop(f"two changed paths share the diff header {header!r}.")
        path_by_header[header] = new
    raw = run(*GIT_DIFF_BASE_ARGS, *diff_args)
    # re.M's ^ follows only "\n", and run() keeps "\r" as is, so a CR or a
    # Unicode line separator inside a path or a line can't start a block.
    starts = [m.start() for m in re.finditer(r"^diff --git ", raw, re.M)]
    files = {}
    for start, end in zip(starts, starts[1:] + [len(raw)]):
        block = raw[start:end]
        header = block.split("\n", 1)[0]
        path = path_by_header.get(header)
        if path is None:
            stop(f"diff header {header!r} matches no changed path from `git diff --name-status`.")
        files[path] = files.get(path, "") + block
    missing = [new for _, _, new in entries if new not in files]
    if missing:
        stop("no diff block found for: " + ", ".join(missing))
    return files, entries


def find_suppressions(path, diff):
    hits = []
    test_path = is_test_path(path)
    # split("\n"), not splitlines(): git ends diff lines with "\n" only, and
    # splitlines() also breaks on "\r", "\x0b", U+2028 and others, which cut
    # the "+" off the text after them so a marker there was never checked (#170).
    for line in diff.split("\n"):
        if not line.startswith("+") or line.startswith("+++"):
            continue
        for pat in UNAMBIGUOUS_SUPPRESSION_PATTERNS:
            if pat.match(line):
                hits.append(f"{path}: new suppression marker — {line.strip()}")
        if test_path:
            for pat in TEST_ONLY_SUPPRESSION_PATTERNS:
                if pat.match(line):
                    hits.append(f"{path}: new suppression marker — {line.strip()}")
    return hits


def find_stubs(path, diff):
    if is_test_path(path):
        return []
    hits = []
    for line in diff.split("\n"):  # not splitlines(): see find_suppressions
        if not line.startswith("+") or line.startswith("+++"):
            continue
        content = line[1:]
        if PLACEHOLDER_STUB_MESSAGE.search(content):
            hits.append(f"{path}: unfinished stub introduced in shipped code — {line.strip()}")
            continue
        for pat in BARE_STUB_PATTERNS:
            if pat.search(content):
                hits.append(f"{path}: unfinished stub introduced in shipped code — {line.strip()}")
    return hits


def paired_threshold_drops(diff):
    """Within each contiguous removed/added block in a hunk, pair a removed
    line to an added line once percentages are normalized out. Plain
    positional pairing (zip) breaks when a hunk's removed/added line counts
    differ (e.g. an unrelated comment added alongside the real edit), so
    pairing is content-based instead: an exact normalized match first (the
    same line, only the number changed), falling back to the closest
    remaining line by text similarity when nothing matches exactly (the
    line was reworded alongside the percentage change, not just the
    number). A similarity floor on the fallback keeps it from mismatching
    two genuinely unrelated lines; anything left over — no exact or
    close-enough match — is skipped rather than force-paired."""
    removed_buf, added_buf, drops = [], [], []

    def normalize(line):
        # line[1:] drops the diff marker (-/+) itself, so a removed and an
        # added line compare equal on content alone, not on which side they
        # came from.
        return PERCENT_PATTERN.sub("N%", line[1:])

    def flush():
        added_remaining = list(added_buf)
        for r in removed_buf:
            rn = normalize(r)
            # Fast path: same line except the number itself changed.
            a = next((cand for cand in added_remaining if normalize(cand) == rn), None)
            if a is None:
                # No exact match — the line may have been reworded alongside
                # the percentage change ("must be" → "should be"), not just
                # the number, so normalize() alone won't find its pair.
                # Fall back to the closest remaining added line by text
                # similarity, so a drop isn't missed just because the
                # sentence around it also changed. The similarity floor
                # keeps this from pairing genuinely unrelated lines to
                # each other — an unrelated line elsewhere in the hunk
                # scores far below it.
                best, best_ratio = None, 0.0
                for cand in added_remaining:
                    ratio = difflib.SequenceMatcher(None, rn, normalize(cand)).ratio()
                    if ratio > best_ratio:
                        best, best_ratio = cand, ratio
                if best is not None and best_ratio >= 0.5:
                    a = best
            if a is None:
                continue
            added_remaining.remove(a)
            rn_vals, an_vals = PERCENT_PATTERN.findall(r), PERCENT_PATTERN.findall(a)
            # A line can carry more than one percentage ("≥90% test coverage
            # and ≥80% doc coverage") — compare every position, not just the
            # first, so a drop in a later number on the same line isn't missed.
            for rv, av in zip(rn_vals, an_vals):
                if float(av) < float(rv):
                    drops.append((float(rv), float(av)))
        removed_buf.clear()
        added_buf.clear()

    for line in diff.split("\n"):  # not splitlines(): see find_suppressions
        if line.startswith("-") and not line.startswith("---"):
            removed_buf.append(line)
        elif line.startswith("+") and not line.startswith("+++"):
            added_buf.append(line)
        else:
            flush()
    flush()
    return drops


violations = []
branch = current_branch()

diff_by_file, entries = diff_by_path(f"{BASE_REF}...HEAD")
status_by_path = {new: status for status, _, new in entries}
rename_sources = {new: old for status, old, new in entries if status == "R"}

# Extensions git's own binary-content heuristic correctly calls binary —
# real assets, never worth a forced-text re-fetch.
KNOWN_BINARY_EXTENSIONS = (
    ".png", ".jpg", ".jpeg", ".gif", ".pdf", ".zip", ".icns", ".ico",
    ".mov", ".mp4", ".woff", ".woff2", ".ttf", ".otf",
)


def is_binary_diff(diff_text):
    return diff_text.startswith("Binary files ") or "\nBinary files " in diff_text


def text_diff_for(*paths):
    # --text: a PR's own .gitattributes (`-diff`) must not blank a file's
    # diff from the checks that read it. --src-prefix/--dst-prefix already
    # come from GIT_DIFF_BASE_ARGS.
    if not paths:
        return {}
    return diff_by_path("--text", f"{BASE_REF}...HEAD", "--", *paths)[0]


# Re-fetch, forced to text, any changed file that (a) isn't a genuine binary
# asset by extension and (b) git nonetheless rendered as "Binary files ...
# differ" — either git's own content-sniffing heuristic mis-fired, or the
# PR's own .gitattributes marks it -diff. One consolidated call for however
# many files that turns out to be (typically zero), not one call per file —
# left as diff_by_file's normal binary placeholder otherwise, checks #3-5
# below would silently see no added-line content for such a file, exactly
# the evasion this closes.
masked_paths = [
    p for p, d in diff_by_file.items()
    if is_binary_diff(d) and not p.lower().endswith(KNOWN_BINARY_EXTENSIONS)
]
if masked_paths:
    diff_by_file.update(text_diff_for(*masked_paths))

# 1. Gate-definition files touched on a feature/* branch (any status — an
# outright deletion is at least as suspicious as an edit)
if branch == "HEAD":
    print(
        "NOTE: detached HEAD — cannot determine if this is a feature/* branch, "
        "so check #1 (gate-definition files edited on a feature branch) is "
        "inconclusive and was skipped rather than silently passed.",
        file=sys.stderr,
    )
elif branch.startswith("feature/"):
    touched_defs = [p for p in status_by_path if is_guarded_path(p)]
    # A guarded file moved off its protected path shows up only under its new name,
    # so check the rename source too (unless the new name is itself guarded and
    # already listed).
    for path, source in rename_sources.items():
        if is_guarded_path(source) and not is_guarded_path(path):
            touched_defs.append(f"{source} (renamed to {path})")
    if touched_defs:
        violations.append(
            "Gate-definition file(s) modified on a feature/* branch: "
            + ", ".join(touched_defs)
            + " — a feature branch should never need to change what counts as "
            "passing; if this is legitimate gate maintenance, do it on its own "
            "chore/* or fix/* branch."
        )

# 2. Previously-existing test files deleted rather than fixed
deleted_tests = [p for p, s in status_by_path.items() if s == "D" and is_test_path(p)]
if deleted_tests:
    violations.append("Test file(s) deleted rather than fixed: " + ", ".join(deleted_tests))

# 3 & 4. New suppression markers and unfinished stubs — application source
# only. Gate-definition files and this script's own file describe these
# exact patterns in their own prose/docstrings, which a substring match
# can't tell apart from real code; a sibling scripts/check_*.py's real code
# is scanned like any other source file (see SELF_PATH above).
for path, diff in diff_by_file.items():
    if path.endswith(DOC_EXTENSIONS) or path in GATE_DEFINITION_FILES or path == SELF_PATH:
        continue
    violations.extend(find_suppressions(path, diff))
    violations.extend(find_stubs(path, diff))

# 5. Lowered numeric thresholds in gate-definition files
for path in [p for p, s in status_by_path.items() if s == "M" and p in GATE_DEFINITION_FILES]:
    for before, after in paired_threshold_drops(diff_by_file.get(path, "")):
        violations.append(
            f"{path}: a percentage/threshold value dropped from {before}% to "
            f"{after}% — confirm this is an intentional target change, not a "
            "gate weakened to pass."
        )

if violations:
    print(f"\n=== Gate integrity: {len(violations)} issue(s) found ===\n")
    for v in violations:
        print(f"  [FAIL] {v}")
    print()
    sys.exit(1)

print("Gate integrity OK — no gate-weakening patterns found.")
sys.exit(0)
