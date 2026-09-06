#!/usr/bin/env python3
"""
Captures objective metrics for one /benchmark run and appends a structured
entry to .claude/context/benchmark-log.md: commit count, commit subjects
(to visually confirm RED/GREEN order), wall-clock duration, and a
human/agent-supplied /gates summary.

This does not run the pipeline itself — /benchmark's own instructions do
that. This script only measures what already happened on the given branch.

Usage: python3 scripts/capture_pipeline_metrics.py <branch> --label <label> [--base develop] [--started ISO8601] [--ended ISO8601]
"""
import argparse
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

LOG_PATH = Path(".claude/context/benchmark-log.md")


def run(*args):
    return subprocess.run(args, capture_output=True, text=True, check=True).stdout


def commit_log(base, branch):
    out = run("git", "log", f"{base}...{branch}", "--reverse", "--pretty=format:%H%x09%aI%x09%s")
    commits = []
    for line in out.splitlines():
        if not line:
            continue
        sha, iso_date, subject = line.split("\t", 2)
        commits.append((sha, iso_date, subject))
    return commits


def parse_iso(s):
    dt = datetime.fromisoformat(s)
    # A naive timestamp (no offset) can't be subtracted from git's always-aware
    # %aI timestamps — assume it means local system time, per datetime's own
    # documented behavior for .astimezone() on a naive value.
    return dt.astimezone() if dt.tzinfo is None else dt


def current_branch():
    return run("git", "rev-parse", "--abbrev-ref", "HEAD").strip()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("branch", help="Benchmark branch name, e.g. benchmark/baseline-2026-09-06")
    parser.add_argument("--label", required=True, help="Short label for this run, e.g. 'baseline' or 'after-haiku-review'")
    parser.add_argument("--base", default="develop", help="Base branch the benchmark branched from (default: develop)")
    parser.add_argument("--started", help="ISO8601 timestamp when /spec was invoked (optional — falls back to first commit time)")
    parser.add_argument("--ended", help="ISO8601 timestamp when /gates reported its summary (optional — falls back to last commit time)")
    args = parser.parse_args()

    commits = commit_log(args.base, args.branch)
    if not commits:
        sys.exit(f"No commits found between {args.base} and {args.branch} — check the branch name and that it has commits ahead of {args.base}.")

    first_commit_time = parse_iso(commits[0][1])
    last_commit_time = parse_iso(commits[-1][1])
    started = parse_iso(args.started) if args.started else first_commit_time
    ended = parse_iso(args.ended) if args.ended else last_commit_time
    wall_clock = ended - started

    gate_check_output = ""
    on_branch = current_branch()
    if on_branch != args.branch:
        gate_check_status = f"skipped — HEAD is `{on_branch}`, not `{args.branch}`"
        gate_check_output = "check_tdd_commit_order.py always diffs against HEAD, not an arbitrary ref — check out the benchmark branch before running this script, or the result would silently reflect the wrong branch"
    else:
        try:
            gate_check_output = run("python3", "scripts/check_tdd_commit_order.py", args.base).strip()
            gate_check_status = "pass"
        except subprocess.CalledProcessError as e:
            gate_check_output = ((e.stdout or "") + (e.stderr or "")).strip()
            # exit 1 = a real RED-before-GREEN violation was found; exit 2 = the script
            # is still unconfigured (template layer names) and checked nothing at all —
            # these are not the same outcome and must not be reported as the same status.
            gate_check_status = "unconfigured (nothing checked)" if e.returncode == 2 else "fail"
        except FileNotFoundError:
            gate_check_output = "scripts/check_tdd_commit_order.py not found or not configured for this project"
            gate_check_status = "n/a"

    entry_lines = [
        f"## {datetime.now(timezone.utc).strftime('%Y-%m-%d')} — {args.label}",
        f"**Branch:** `{args.branch}` (base: `{args.base}`)",
        f"**Wall-clock:** {wall_clock} ({started.isoformat()} → {ended.isoformat()})",
        f"**Commits:** {len(commits)}",
    ]
    for sha, iso_date, subject in commits:
        entry_lines.append(f"  - `{sha[:8]}` {subject}")
    entry_lines.append(f"**RED-before-GREEN check:** {gate_check_status}" + (f" — {gate_check_output}" if gate_check_output else ""))
    entry_lines.append("**Gates summary:** <paste /gates' final output here>")
    entry_lines.append("**Notes:** <token cost if read from the session's usage display, anything unusual about this run>")
    entry_lines.append("")

    entry = "\n".join(entry_lines)

    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    is_new = not LOG_PATH.exists()
    with LOG_PATH.open("a") as f:
        if is_new:
            f.write("# Benchmark Log\n\nAppend-only. One entry per /benchmark run. See .claude/commands/benchmark.md.\n\n")
        f.write(entry + "\n")

    print(f"Appended entry to {LOG_PATH}")
    print(entry)


if __name__ == "__main__":
    main()
