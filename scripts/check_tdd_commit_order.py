#!/usr/bin/env python3
"""
Verifies the RED step is reconstructable from git history: for every new
file under a layer directory that expects a matching test (ViewModels,
Services, Repositories — adjust SCOPED_LAYER_DIRS below to your project's
layer names), its test file must have been added in a strictly earlier
commit — never the same commit, never a later one. A test bundled into the
same commit as its implementation is unverifiable as "written and watched
failing before the code existed" — nothing distinguishes that from writing
both together and never running the test red.

Usage: python3 scripts/check_tdd_commit_order.py [base_ref]
  base_ref defaults to 'develop'
"""
import json
import os
import subprocess
import sys

BASE_REF = sys.argv[1] if len(sys.argv) > 1 else "develop"

# Path segments (not full prefixes) so this works regardless of your app's
# root folder name. Only layers that have a 1:1 "<Type>.swift" ->
# "<Type>Tests.swift" convention belong here; a Views/ or Models/ layer
# usually doesn't. The layers come from `project.architecture` in
# scripts/pipeline_lanes.json (mvvm if absent); `project.scoped_layer_dirs`
# overrides the preset with your own list.
ARCHITECTURE_PRESETS = {
    "mvvm": ("/ViewModels/", "/Services/", "/Repositories/"),
    "mvc": ("/Controllers/", "/Services/"),
    "viper": ("/Presenters/", "/Interactors/", "/Entities/"),
}


def load_scoped_layer_dirs(config_path):
    try:
        with open(config_path) as f:
            config = json.load(f)
    except FileNotFoundError:
        return ARCHITECTURE_PRESETS["mvvm"]
    except (OSError, ValueError) as e:
        print(f"ERROR: cannot read {config_path}: {e}")
        sys.exit(2)
    project = config.get("project", {}) if isinstance(config, dict) else {}
    if not isinstance(project, dict):
        print("ERROR: 'project' in scripts/pipeline_lanes.json must be an object.")
        sys.exit(2)
    custom = project.get("scoped_layer_dirs")
    if custom:
        if not isinstance(custom, list) or not all(isinstance(x, str) for x in custom):
            print("ERROR: project.scoped_layer_dirs must be a list of strings.")
            sys.exit(2)
        return tuple(custom)
    arch = project.get("architecture", "mvvm")
    if arch not in ARCHITECTURE_PRESETS:
        print(f"ERROR: unknown project.architecture {arch!r}; use one of {sorted(ARCHITECTURE_PRESETS)} or set scoped_layer_dirs.")
        sys.exit(2)
    return ARCHITECTURE_PRESETS[arch]


SCOPED_LAYER_DIRS = load_scoped_layer_dirs(
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "pipeline_lanes.json")
)

# Adjust to your test target's root directory name(s).
TEST_ROOT = "Tests/"


def run(*args):
    # errors="replace": non-UTF-8 bytes in a commit message or path must not
    # crash this script outright — decode what's decodable, substitute the
    # rest. core.quotepath=false: without it, git wraps any path containing a
    # non-ASCII byte in literal quotes and octal-escapes it (e.g.
    # "café.swift" -> "\"caf\\303\\251.swift\""), corrupting every basename
    # comparison below for that file — verified empirically, not assumed.
    return subprocess.run(
        ("git", "-c", "core.quotepath=false") + args[1:],
        capture_output=True, text=True, errors="replace", check=True,
    ).stdout


def commit_list():
    # Double-dot, not triple-dot: git log's triple-dot is symmetric difference
    # (commits on either side, not just HEAD's), unlike git diff's triple-dot
    # (merge-base diff). Triple-dot here would pull in develop-only commits
    # whenever develop advances after this branch was cut, corrupting the
    # commit ordering this script's violation detection depends on.
    out = run("git", "log", f"{BASE_REF}..HEAD", "--reverse", "--pretty=format:%H")
    return [line for line in out.splitlines() if line]


def added_files(sha):
    out = run("git", "show", "--diff-filter=A", "--name-only", "--pretty=format:", sha)
    return [line for line in out.splitlines() if line]


def all_test_files():
    out = run("git", "ls-tree", "-r", "--name-only", "HEAD")
    return [line for line in out.splitlines() if TEST_ROOT in line and line.endswith(".swift")]


def repo_has_any_scoped_file():
    # Repo-wide, not just this branch's diff: distinguishes "this branch
    # legitimately touches no scoped layer today" from "SCOPED_LAYER_DIRS
    # still holds someone else's project's layer names and will never match
    # anything here" — the latter must not look like a clean pass.
    out = run("git", "ls-tree", "-r", "--name-only", "HEAD")
    return any(any(seg in line for seg in SCOPED_LAYER_DIRS) for line in out.splitlines())


commits = commit_list()
if not commits:
    print(f"No commits ahead of {BASE_REF} — nothing to check.")
    sys.exit(0)

test_files_by_basename = {}
for path in all_test_files():
    test_files_by_basename.setdefault(path.rsplit("/", 1)[-1], path)

first_added_index = {}
added_per_commit = []
for i, sha in enumerate(commits):
    files = added_files(sha)
    added_per_commit.append(files)
    for f in files:
        first_added_index.setdefault(f, i)

violations = []
checked = 0
for i, files in enumerate(added_per_commit):
    for f in files:
        if not any(seg in f for seg in SCOPED_LAYER_DIRS) or not f.endswith(".swift") or TEST_ROOT in f:
            continue
        base = f.rsplit("/", 1)[-1]
        test_basename = base[: -len(".swift")] + "Tests.swift"
        test_path = test_files_by_basename.get(test_basename)
        if test_path is None:
            continue  # no matching test file at all — your coverage gate catches this, not this one
        test_index = first_added_index.get(test_path)
        if test_index is None:
            continue  # test file predates this branch — not a new-file case
        checked += 1
        if test_index == i:
            violations.append(
                f"{f} — test file {test_path} committed in the SAME commit "
                f"({commits[i][:8]}) — red step not separately verifiable"
            )
        elif test_index > i:
            violations.append(
                f"{f} — test file {test_path} committed AFTER implementation "
                f"({commits[test_index][:8]} follows {commits[i][:8]}) — tests-after, not TDD"
            )

if violations:
    print(f"\n=== RED-before-GREEN commit order: {len(violations)} violation(s) ===\n")
    for v in violations:
        print(f"  [FAIL] {v}")
    print()
    sys.exit(1)

if checked == 0:
    if not repo_has_any_scoped_file():
        print(
            f"WARNING: no file anywhere in this repo matches SCOPED_LAYER_DIRS {SCOPED_LAYER_DIRS} — "
            "these layer names match nothing here. Set project.architecture or project.scoped_layer_dirs in "
            "scripts/pipeline_lanes.json to match your project's actual layer folders before trusting this gate; "
            "until then, every run will silently no-op instead of checking anything."
        )
        sys.exit(2)
    print("No new files in scope with matching tests on this branch — skipping.")
else:
    print(f"RED-before-GREEN commit order OK — {checked} file(s) checked.")
sys.exit(0)
