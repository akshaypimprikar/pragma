#!/usr/bin/env python3
"""
Detects gate-weakening: changes on this branch that make /gates easier to
pass without fixing the underlying violation, rather than a legitimate gate
or config maintenance change. Every other gate in this pipeline checks the
code; this one checks that nobody edited the ruler.

Flags, via git diff against the base branch:
  1. A gate-definition file (gates.md, CONSTRAINTS.md, a scripts/check_*.py)
     touched on a feature/* branch — a real feature never needs to change
     what counts as passing.
  2. A previously-existing test file deleted rather than fixed.
  3. A new suppression/skip marker introduced in the diff (swiftlint:disable,
     a Swift Testing .disabled() trait, XCTSkip).
  4. An unfinished stub (fatalError("not implemented"), a bare fatalError()/
     preconditionFailure()) newly introduced in non-test code.
  5. A numeric threshold in a gate-definition file lowered by this diff.

Usage: python3 scripts/check_gate_integrity.py [base_ref]
  base_ref defaults to 'develop'
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

SUPPRESSION_PATTERNS = (
    re.compile(r"^\+.*//\s*swiftlint:disable"),
    re.compile(r"^\+.*\.disabled\("),  # Swift Testing trait
    re.compile(r"^\+.*\bXCTSkip\b"),
)
STUB_PATTERNS = (
    re.compile(r'^\+.*fatalError\(\s*"(not implemented|todo|TODO)'),
    re.compile(r"^\+\s*fatalError\(\)\s*$"),
    re.compile(r"^\+\s*preconditionFailure\(\)\s*$"),
)
PERCENT_PATTERN = re.compile(r"(\d+(?:\.\d+)?)\s*%")


def run(*args):
    return subprocess.run(args, capture_output=True, text=True, check=True).stdout


def current_branch():
    return run("git", "branch", "--show-current").strip()


def changed_files(diff_filter=None):
    args = ["git", "diff", f"{BASE_REF}...HEAD", "--name-only"]
    if diff_filter:
        args += [f"--diff-filter={diff_filter}"]
    return [line for line in run(*args).splitlines() if line]


def file_diff(path):
    return run("git", "diff", f"{BASE_REF}...HEAD", "--", path)


violations = []
branch = current_branch()

# 1. Gate-definition files touched on a feature/* branch
if branch.startswith("feature/"):
    touched_defs = [
        f
        for f in changed_files()
        if f in GATE_DEFINITION_FILES or f.startswith(GATE_SCRIPT_PREFIX)
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
deleted_tests = [
    f for f in changed_files("D") if "Tests" in f and f.endswith((".swift", ".py"))
]
if deleted_tests:
    violations.append("Test file(s) deleted rather than fixed: " + ", ".join(deleted_tests))

# 3 & 4. New suppression markers and unfinished stubs, scanned from added lines
for path in changed_files("AM"):
    diff = file_diff(path)
    for line in diff.splitlines():
        if not line.startswith("+") or line.startswith("+++"):
            continue
        for pat in SUPPRESSION_PATTERNS:
            if pat.match(line):
                violations.append(f"{path}: new suppression marker — {line.strip()}")
        if "Tests" not in path:
            for pat in STUB_PATTERNS:
                if pat.match(line):
                    violations.append(
                        f"{path}: unfinished stub introduced in shipped code — {line.strip()}"
                    )

# 5. Lowered numeric thresholds in gate-definition files
for path in [f for f in changed_files("M") if f in GATE_DEFINITION_FILES]:
    diff = file_diff(path)
    removed = [
        float(m)
        for line in diff.splitlines()
        if line.startswith("-") and not line.startswith("---")
        for m in PERCENT_PATTERN.findall(line)
    ]
    added = [
        float(m)
        for line in diff.splitlines()
        if line.startswith("+") and not line.startswith("+++")
        for m in PERCENT_PATTERN.findall(line)
    ]
    if removed and added and min(added) < min(removed):
        violations.append(
            f"{path}: a percentage/threshold value dropped from {min(removed)}% to "
            f"{min(added)}% — confirm this is an intentional target change, not a "
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
