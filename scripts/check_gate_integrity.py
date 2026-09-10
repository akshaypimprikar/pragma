#!/usr/bin/env python3
"""
Detects gate-weakening: changes on this branch that make /gates easier to
pass without fixing the underlying violation, rather than a legitimate gate
or config maintenance change. Every other gate in this pipeline checks the
code; this one checks that nobody edited the ruler.

Flags, via a single git diff against the base branch:
  1. A gate-definition file (gates.md, CONSTRAINTS.md, a scripts/check_*.py)
     touched on a feature/* branch — a real feature never needs to change
     what counts as passing.
  2. A previously-existing test file deleted rather than fixed.
  3. A new suppression/skip marker introduced in a *test* file's diff
     (swiftlint:disable, a Swift Testing .disabled() trait, XCTSkip) —
     scoped to test files only, since SwiftUI's .disabled(condition) view
     modifier uses the identical syntax in ordinary application code.
  4. An unfinished stub (a bare, message-less fatalError()/
     preconditionFailure()) newly introduced in non-test code.
  5. A numeric threshold in a gate-definition file lowered by this diff,
     matched line-for-line within each diff hunk so an unrelated number
     elsewhere in the file can't produce a false positive or mask a real
     change.

Usage: python3 scripts/check_gate_integrity.py [base_ref]
  base_ref defaults to 'develop', matching this pipeline's other gate
  scripts (see check_tdd_commit_order.py) and its gitflow convention.
"""
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

TEST_PATH_SEGMENT = re.compile(r"(^|/)tests?(/|$)", re.IGNORECASE)
TEST_FILENAME = re.compile(r"(^|/)(test_[^/]+|[^/]+_test)\.(py|swift)$", re.IGNORECASE)

SUPPRESSION_PATTERNS = (
    re.compile(r"^\+.*//\s*swiftlint:disable"),
    re.compile(r"^\+.*\.disabled\("),  # Swift Testing trait — only scanned in test files, see is_test_path()
    re.compile(r"^\+.*\bXCTSkip\b"),
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
    # Matches capture_pipeline_metrics.py's existing convention. Returns the
    # literal string "HEAD" in detached-HEAD state (most CI PR checkouts)
    # rather than an empty string, so that state is explicit, not silent.
    return run("git", "rev-parse", "--abbrev-ref", "HEAD").strip()


def is_test_path(path):
    return "Tests" in path or bool(TEST_PATH_SEGMENT.search(path)) or bool(TEST_FILENAME.search(path))


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
    if not is_test_path(path):
        return []
    hits = []
    for line in diff.splitlines():
        if not line.startswith("+") or line.startswith("+++"):
            continue
        for pat in SUPPRESSION_PATTERNS:
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
        if re.search(r'fatalError\(\s*"', content):
            continue  # has a message — a documented exhaustiveness stub, not a silent placeholder
        for pat in BARE_STUB_PATTERNS:
            if pat.search(content):
                hits.append(f"{path}: unfinished stub introduced in shipped code — {line.strip()}")
    return hits


def paired_threshold_drops(diff):
    """Pair each contiguous removed/added block within a hunk positionally,
    so a threshold drop is only flagged when the same line's number changed —
    not when two unrelated percentages happen to appear anywhere in the file."""
    removed_buf, added_buf, drops = [], [], []

    def flush():
        for r, a in zip(removed_buf, added_buf):
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
    if path in GATE_DEFINITION_FILES or path.startswith(GATE_SCRIPT_PREFIX):
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
