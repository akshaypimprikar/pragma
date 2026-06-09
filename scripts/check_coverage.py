#!/usr/bin/env python3
"""
Reads an xccov JSON report and enforces coverage thresholds.
  FAIL (<60%)  — exits 1, blocks CI
  WARN (<80%)  — printed as advisory, CI still passes
Usage: python3 scripts/check_coverage.py coverage.json
"""
import json
import sys

FAIL_THRESHOLD = 0.60
WARN_THRESHOLD = 0.80

with open(sys.argv[1]) as f:
    report = json.load(f)

source_files = []
for target in report.get("targets", []):
    if "Tests" in target.get("name", ""):
        continue
    for file in target.get("files", []):
        path = file.get("path", "")
        name = file.get("name", path)
        if "Tests" in path or path.endswith("main.swift"):
            continue
        # UI-only files contain no business logic — tested via UI tests, not unit tests
        if (name.endswith("View.swift") or name.endswith("Sheet.swift") or
                name.endswith("Row.swift") or name.startswith("Color+")):
            continue
        source_files.append({
            "name": name,
            "coverage": file.get("lineCoverage", 0.0),
        })

if not source_files:
    print("No source files found in coverage report.")
    sys.exit(0)

failing = [f for f in source_files if f["coverage"] < FAIL_THRESHOLD]
warning = [f for f in source_files if FAIL_THRESHOLD <= f["coverage"] < WARN_THRESHOLD]

print(f"\n=== Coverage Report ({len(source_files)} source files) ===\n")
for f in sorted(source_files, key=lambda x: x["coverage"]):
    pct = f["coverage"] * 100
    flag = "FAIL" if f["coverage"] < FAIL_THRESHOLD else ("WARN" if f["coverage"] < WARN_THRESHOLD else "  OK")
    print(f"  [{flag}] {f['name']:50s} {pct:5.1f}%")

print()

if warning:
    print(f"WARNING: {len(warning)} file(s) below 80% coverage:")
    for f in warning:
        print(f"  {f['name']} — {f['coverage']*100:.1f}%")
    print()

if failing:
    print(f"FAIL: {len(failing)} file(s) below 60% coverage threshold:")
    for f in failing:
        print(f"  {f['name']} — {f['coverage']*100:.1f}%")
    sys.exit(1)

print(f"Coverage check passed. ({len(warning)} warning(s), 0 failures)")
