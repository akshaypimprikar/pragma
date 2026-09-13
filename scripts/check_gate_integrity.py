#!/usr/bin/env python3
"""
Detects gate-weakening: changes on this branch that make /gates easier to
pass without fixing the underlying violation, rather than a legitimate gate
or config maintenance change. Every other gate in this pipeline checks the
code; this one checks that nobody edited the ruler.

Flags, via a single git diff against the base branch:
  1. A gate-definition file (gates.md, CONSTRAINTS.md, a scripts/check_*.py)
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
  4. An unfinished stub newly introduced in non-test code: a bare
     fatalError()/preconditionFailure(), or either with a placeholder
     message ("not implemented", "todo") — a message alone doesn't make it
     legitimate, only a real, specific reason does.
  5. A numeric threshold in a gate-definition file lowered by this diff,
     matched by normalized line content within each diff hunk (not
     position) so an unrelated number elsewhere in the file, or an unrelated
     line added/removed alongside the real edit, can't produce a false
     positive or mask a real change.

Usage: python3 scripts/check_gate_integrity.py [base_ref]
  base_ref defaults to 'develop', matching this pipeline's other gate
  scripts (see check_tdd_commit_order.py) and its gitflow convention.
"""
import os
import re
import subprocess
import sys

BASE_REF = sys.argv[1] if len(sys.argv) > 1 else "develop"

GATE_DEFINITION_FILES = (
    ".claude/commands/gates.md",
    "CONSTRAINTS.md",
    "skills/deterministic-pr-gates/SKILL.md",
)
GATE_SCRIPT_PREFIX = "scripts/check_"
# Suppression/stub detection (checks 3 & 4) is about shipped application
# code — a doc file describing these exact patterns in prose (this script's
# own docstring, a CHANGELOG entry writing up a past bug) isn't code and a
# substring match can't tell the two apart, so doc extensions are excluded
# outright rather than relying only on the narrower GATE_DEFINITION_FILES/
# GATE_SCRIPT_PREFIX exclusion below.
DOC_EXTENSIONS = (".md", ".txt", ".rst")

TEST_PATH_SEGMENT = re.compile(r"(^|/)tests?(/|$)", re.IGNORECASE)
TEST_FILENAME_UNDERSCORE = re.compile(r"(^|/)(test_[^/]+|[^/]+_test)\.py$", re.IGNORECASE)
# Swift's own convention: <Type>Tests.swift (plural) or <Type>Test.swift
# (singular, less common but real) — anchored to the end of the filename so
# e.g. "ABTestsManager.swift" (a feature-flag file, not a test) doesn't match.
TEST_FILENAME_SUFFIX = re.compile(r"[^/]*Tests?\.(swift|py)$")

# Only .disabled( is ambiguous with SwiftUI's .disabled(condition) view
# modifier — swiftlint:disable and XCTSkip have no such ambiguity in
# application code, so they're checked everywhere, not just in test files.
UNAMBIGUOUS_SUPPRESSION_PATTERNS = (
    re.compile(r"^\+.*//\s*swiftlint:disable"),
    re.compile(r"^\+.*\bXCTSkip\b"),
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
BARE_STUB_PATTERNS = (
    re.compile(r"\bfatalError\(\)"),
    re.compile(r"\bpreconditionFailure\(\)"),
)
PERCENT_PATTERN = re.compile(r"(\d+(?:\.\d+)?)\s*%")


def run(*args):
    try:
        return subprocess.run(args, capture_output=True, text=True, check=True).stdout
    except subprocess.CalledProcessError as e:
        print(f"ERROR: `{' '.join(args)}` failed — {e.stderr.strip() or e}", file=sys.stderr)
        print(
            f"Gate integrity could not run — confirm this branch has a valid "
            f"'{BASE_REF}' base ref to compare against (pass a different one "
            "as this script's first argument if your project's trunk isn't "
            "named 'develop').",
            file=sys.stderr,
        )
        sys.exit(2)


def current_branch():
    # GitHub Actions checks out a `pull_request` event at a detached commit,
    # not the real branch — GITHUB_HEAD_REF carries the actual source branch
    # name in that case, which is exactly the context check #1 below needs
    # most. Falls back to git's own detection (capture_pipeline_metrics.py's
    # existing convention) for local use or non-GHA CI, returning the
    # literal string "HEAD" in detached-HEAD state rather than an empty
    # string, so that state is explicit, not silent.
    return os.environ.get("GITHUB_HEAD_REF") or run("git", "rev-parse", "--abbrev-ref", "HEAD").strip()


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
    line to an added line only when they're identical once percentages are
    normalized out — i.e. the same line's number changed. Plain positional
    pairing (zip) breaks when a hunk's removed/added line counts differ
    (e.g. an unrelated comment added alongside the real edit); matching by
    normalized content instead finds the real pair regardless of position,
    and simply skips lines with no textual counterpart rather than
    mismatching them."""
    removed_buf, added_buf, drops = [], [], []

    def normalize(line):
        return PERCENT_PATTERN.sub("N%", line)

    def flush():
        added_by_norm = {}
        for a in added_buf:
            added_by_norm.setdefault(normalize(a), []).append(a)
        for r in removed_buf:
            candidates = added_by_norm.get(normalize(r))
            if not candidates:
                continue
            a = candidates.pop(0)
            rn, an = PERCENT_PATTERN.findall(r), PERCENT_PATTERN.findall(a)
            if rn and an and float(an[0]) < float(rn[0]):
                drops.append((float(rn[0]), float(an[0])))
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


violations = []
branch = current_branch()

name_status = run("git", "diff", f"{BASE_REF}...HEAD", "--name-status")
status_by_path = {}
for line in name_status.splitlines():
    if not line.strip():
        continue
    parts = line.split("\t")
    status_by_path[parts[-1]] = parts[0][0]  # first letter: A/M/D/R...

full_diff = run("git", "diff", f"{BASE_REF}...HEAD")
diff_by_file = parse_diff_by_file(full_diff)

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
# only. Gate-definition files and check_*.py scripts describe these exact
# patterns in their own prose/docstrings, which a substring match can't tell
# apart from real code.
for path, diff in diff_by_file.items():
    if path.endswith(DOC_EXTENSIONS) or path in GATE_DEFINITION_FILES or path.startswith(GATE_SCRIPT_PREFIX):
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
