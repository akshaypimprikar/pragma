#!/usr/bin/env python3
"""
Detects gate-weakening: changes on this branch that make /gates easier to
pass without fixing the underlying violation, rather than a legitimate gate
or config maintenance change. Every other gate in this pipeline checks the
code; this one checks that nobody edited the ruler.

Flags, via a single git diff against the base branch:
  1. A gate-definition file (see GATE_DEFINITION_FILES/GATE_SCRIPT_PREFIX
     below for the exact list — not repeated here so this docstring can't
     drift out of sync with it the way an inline copy already had)
     touched on a feature/* branch — a real feature never needs to change
     what counts as passing. Only reliably checkable when the actual branch
     name is known (see current_branch()); does not currently track a
     gate-definition file across a rename.
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
import os
import re
import subprocess
import sys

BASE_REF = sys.argv[1] if len(sys.argv) > 1 else "develop"
BRANCH_OVERRIDE = sys.argv[2] if len(sys.argv) > 2 else None

# core.quotepath=false: without it, git octal-escapes any non-ASCII byte in a
# path (e.g. "café.swift" -> "caf\303\251.swift") in diff/--name-status
# output, which would never match a plain-ASCII GATE_DEFINITION_FILES entry
# or a suppression-scan path check. --src-prefix/--dst-prefix: parse_diff_by_file's
# a/ b/ header parsing must not silently break under a contributor's global
# diff.noprefix/diff.mnemonicPrefix git config — applied here, to every
# git-diff call, not just the narrower text_diff_for() re-fetch below, so a
# config-dependent header format can't mis-parse (or drop) the main diff
# every other check reads.
GIT_DIFF_BASE_ARGS = ("git", "-c", "core.quotepath=false", "diff", "--src-prefix=a/", "--dst-prefix=b/")

GATE_DEFINITION_FILES = (
    ".claude/skills/gates/SKILL.md",
    "CONSTRAINTS.md",
    ".claude/skills/deterministic-pr-gates/SKILL.md",
)
GATE_SCRIPT_PREFIX = "scripts/check_"
# Suppression/stub detection (checks 3 & 4) is about shipped application
# code — a doc file describing these exact patterns in prose (this script's
# own docstring, a CHANGELOG entry writing up a past bug) isn't code and a
# substring match can't tell the two apart, so doc extensions are excluded
# outright rather than relying only on the narrower GATE_DEFINITION_FILES/
# GATE_SCRIPT_PREFIX exclusion below. Only *this* file is excluded from
# checks 3 & 4 by path, not every scripts/check_*.py — check #1 already
# permits editing gate scripts on a chore/*/fix/* branch, and excluding a
# sibling script's real code (not just prose) from stub/suppression
# detection there would silently defeat that gate for those files.
DOC_EXTENSIONS = (".md", ".txt", ".rst")
SELF_PATH = "scripts/check_gate_integrity.py"

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
        # rest, rather than raising UnicodeDecodeError mid-gate-run.
        return subprocess.run(
            args, capture_output=True, text=True, errors="replace", check=True
        ).stdout
    except subprocess.CalledProcessError as e:
        print(f"ERROR: `{' '.join(args)}` failed — {e.stderr.strip() or e}", file=sys.stderr)
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


def parse_diff_by_file(full_diff):
    """Split one `git diff` invocation's output into {path: diff_text}."""
    files = {}
    current_path = None
    buf = []
    for line in full_diff.splitlines(keepends=True):
        m = re.match(r"^diff --git a/.+ b/(.+)$", line)
        if m:
            if current_path is not None:
                files[current_path] = "".join(buf)
            current_path = m.group(1)
            buf = [line]
        elif current_path is not None:
            buf.append(line)
    if current_path is not None:
        files[current_path] = "".join(buf)
    return files


def find_suppressions(path, diff):
    hits = []
    test_path = is_test_path(path)
    for line in diff.splitlines():
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
    for line in diff.splitlines():
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

    for line in diff.splitlines():
        if line.startswith("-") and not line.startswith("---"):
            removed_buf.append(line)
        elif line.startswith("+") and not line.startswith("+++"):
            added_buf.append(line)
        else:
            flush()
    flush()
    return drops


def file_status(diff_text):
    """Derive a name-status-style letter (A/D/R/M) from one file's own diff
    block instead of a second `git diff --name-status` subprocess call over
    the exact same {BASE_REF}...HEAD range — the block's own header lines
    (new file mode / deleted file mode / rename from) already say this."""
    for line in diff_text.splitlines()[:8]:
        if line.startswith("new file mode"):
            return "A"
        if line.startswith("deleted file mode"):
            return "D"
        if line.startswith("rename from "):
            return "R"
    return "M"


violations = []
branch = current_branch()

full_diff = run(*GIT_DIFF_BASE_ARGS, f"{BASE_REF}...HEAD")
diff_by_file = parse_diff_by_file(full_diff)
status_by_path = {path: file_status(diff) for path, diff in diff_by_file.items()}

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
    raw = run(*GIT_DIFF_BASE_ARGS, "--text", f"{BASE_REF}...HEAD", "--", *paths)
    return parse_diff_by_file(raw)


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
    touched_defs = [
        p for p in status_by_path
        if p in GATE_DEFINITION_FILES or p.startswith(GATE_SCRIPT_PREFIX)
    ]
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
