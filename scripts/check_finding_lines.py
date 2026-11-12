#!/usr/bin/env python3
"""Flag review findings that cite a line this branch did not add or change.

Reads findings on stdin, one per line, each starting with a `path:N` or
`path:N-M` citation (anything after it is ignored), and compares them to the
lines `git diff BASE...HEAD` adds or changes. Prints `OUTSIDE <citation>` for a
finding whose cited lines do not overlap the diff, and `INSIDE <citation>` for
the rest. /review lowers an OUTSIDE MEDIUM to LOW (it opens an issue instead of
blocking), except where the review skill's two exceptions apply. A line with no
citation prints `UNCITED <line>` and is never lowered.

Usage: check_finding_lines.py --base <ref>     (run from the repo root)
Exit codes: 0 done, 2 error.
"""
import argparse
import re
import subprocess
import sys

CITATION = re.compile(r"^(?:[^\w.`]|\d+[.)]\s|\[[^\]]*\])*`?([A-Za-z0-9_.][A-Za-z0-9_./-]*):(\d+)(?:-(\d+))?`?")
HUNK = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@")


def changed_ranges(diff):
    """{path: [(start, end)]} of new-side lines each file's hunks add or change."""
    out, path = {}, None
    for line in diff.splitlines():
        if line.startswith("+++ b/") or line == "+++ /dev/null":
            path = line[6:].split("\t")[0] if line != "+++ /dev/null" else None
        elif path and (m := HUNK.match(line)):
            start, count = int(m.group(1)), int(m.group(2) if m.group(2) is not None else 1)
            if count:
                out.setdefault(path, []).append((start, start + count - 1))
    return out


def classify(line, ranges):
    m = CITATION.match(line)
    if not m:
        return "UNCITED", line.strip()
    path, start = m.group(1), int(m.group(2))
    end = int(m.group(3) or start)
    cite = f"{path}:{start}" + (f"-{end}" if m.group(3) else "")
    inside = any(s <= end and start <= e for s, e in ranges.get(path, []))
    return ("INSIDE" if inside else "OUTSIDE"), cite


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--base", required=True)
    args = ap.parse_args(argv)
    diff = subprocess.run(["git", "diff", "-U0", f"{args.base}...HEAD"], capture_output=True, text=True)
    if diff.returncode != 0:
        print(f"check_finding_lines: git diff failed: {diff.stderr.strip()}", file=sys.stderr)
        return 2
    ranges = changed_ranges(diff.stdout)
    for line in sys.stdin:
        if line.strip():
            print(*classify(line, ranges))
    return 0


if __name__ == "__main__":
    sys.exit(main())
