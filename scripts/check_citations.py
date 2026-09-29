#!/usr/bin/env python3
"""Fail on `path:line` citations that point to a missing file or line.

Checks only lines a branch adds (git diff BASE...HEAD) to Markdown files under
docs/superpowers/ and .claude/skills/, so older citations in an edited file
cannot block it. A citation is a backticked `path:N` or `path:N-M` whose path
contains `/` or ends in a known text extension; that leaves out host:port and
URLs. Paths starting `pragma/` or `../` resolve against the repo's parent
directory and are skipped, with a note, when that repo is not present.

It only proves the cited file and lines exist. Whether they say what the text
claims is a review question.

Usage: check_citations.py --base <ref>     (run from the repo root)
Exit codes: 0 all citations resolve, 1 a citation is broken, 2 error.
"""
import argparse
import os
import re
import subprocess
import sys

SCOPES = ("docs/superpowers/", ".claude/skills/")
TEXT_EXTENSIONS = (".md", ".py", ".swift", ".yml", ".yaml", ".json", ".sh", ".txt")
CITATION = re.compile(r"`([A-Za-z0-9_.][A-Za-z0-9_./-]*):(\d+)(?:-(\d+))?`")


def extract(text):
    out = []
    for m in CITATION.finditer(text):
        path = m.group(1)
        if "://" in text[max(0, m.start() - 8):m.start() + 1 + len(path)] or path.startswith(("http", "www.")):
            continue
        if "/" not in path and not path.endswith(TEXT_EXTENSIONS):
            continue
        start = int(m.group(2))
        out.append((path, start, int(m.group(3) or start)))
    return out


def added_lines(base, path):
    diff = subprocess.run(["git", "diff", "-U0", f"{base}...HEAD", "--", path],
                          capture_output=True, text=True, check=True).stdout
    return [l[1:] for l in diff.splitlines() if l.startswith("+") and not l.startswith("+++")]


def changed_files(base):
    names = subprocess.run(["git", "diff", "--name-only", "--diff-filter=AMR", f"{base}...HEAD"],
                           capture_output=True, text=True, check=True).stdout.splitlines()
    return [n for n in names if n.endswith(".md") and n.startswith(SCOPES)]


def resolve(root, path):
    """Return (absolute path, external repo directory or None)."""
    parent = os.path.dirname(root)
    if path.startswith("../"):
        rel = path[3:]
    elif path.startswith("pragma/"):
        rel = path
    else:
        return os.path.join(root, path), None
    repo = rel.split("/", 1)[0]
    return os.path.join(parent, rel), os.path.join(parent, repo)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--base", required=True)
    args = ap.parse_args(argv)
    try:
        root = subprocess.run(["git", "rev-parse", "--show-toplevel"], capture_output=True,
                              text=True, check=True).stdout.strip()
        files = changed_files(args.base)
    except subprocess.CalledProcessError as e:
        print(f"check_citations: git failed: {e.stderr.strip()}", file=sys.stderr)
        return 2
    broken, checked = [], 0
    for f in files:
        for line in added_lines(args.base, f):
            for path, start, end in extract(line):
                target, external_repo = resolve(root, path)
                if external_repo and not os.path.isdir(external_repo):
                    print(f"skipped {f}: `{path}:{start}` (repo not present)")
                    continue
                checked += 1
                if not os.path.isfile(target):
                    broken.append(f"{f}: `{path}:{start}` - file not found")
                    continue
                with open(target, encoding="utf8", errors="replace") as t:
                    n = sum(1 for _ in t)
                if start < 1 or end < start or end > n:
                    broken.append(f"{f}: `{path}:{start}{'-' + str(end) if end != start else ''}` - file has {n} lines")
    for b in broken:
        print(f"BROKEN {b}")
    print(f"{checked} citation(s) checked in {len(files)} file(s), {len(broken)} broken")
    return 1 if broken else 0


if __name__ == "__main__":
    sys.exit(main())
