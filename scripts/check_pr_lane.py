#!/usr/bin/env python3
"""Sort a PR into a pipeline lane from its changed paths and branches.

Lanes and the evidence each one requires come from scripts/pipeline_lanes.json.
The first rule that applies wins:

  release   head release/* into main, or the main -> develop back-merge, and
            every path changed on the release branch itself is a release path
            (a release branch with no known changed paths is laned by paths)
  sync      head starts with the config's sync prefix and every changed path
            is a sync path (only when the config defines "sync")
  <lanes>   the first configured lane with a matching changed path
  default   everything else

Usage:
  check_pr_lane.py --git <base-ref> --head-branch <name> --base-branch <name> [--config PATH]
  check_pr_lane.py --files - --head-branch <name> --base-branch <name> [--config PATH]
      (changed paths on stdin, one per line; release-branch paths from --release-files)

Exit codes: 0 lane printed, 2 invalid config or bad arguments.
"""
import argparse
import json
import os
import re
import subprocess
import sys

EVIDENCE_ITEMS = {"gate_summary", "review_verdict", "code_review", "motivating_incident", "synced_from"}
DEFAULT_CONFIG = os.path.join(os.path.dirname(os.path.abspath(__file__)), "pipeline_lanes.json")


class ConfigError(Exception):
    pass


def glob_match(pattern, path):
    """gitignore-style match of a whole repo-relative path.

    `**/` matches zero or more directories, `**` matches anything, and `*` and
    `?` never cross `/`.
    """
    i, out = 0, []
    while i < len(pattern):
        if pattern.startswith("**/", i):
            out.append("(?:.*/)?")
            i += 3
        elif pattern.startswith("**", i):
            out.append(".*")
            i += 2
        elif pattern[i] == "*":
            out.append("[^/]*")
            i += 1
        elif pattern[i] == "?":
            out.append("[^/]")
            i += 1
        else:
            out.append(re.escape(pattern[i]))
            i += 1
    return re.fullmatch("".join(out), path) is not None


def _matches_any(patterns, path):
    return any(glob_match(p, path) for p in patterns)


def validate_config(config):
    if not isinstance(config, dict):
        raise ConfigError("config must be a JSON object")
    if not isinstance(config.get("lanes"), list) or not config["lanes"]:
        raise ConfigError("config needs a non-empty 'lanes' list")
    default = config.get("default")
    if not isinstance(default, dict) or not default.get("name"):
        raise ConfigError("config needs a 'default' lane with a name")
    entries = [("default", default)] + [(l.get("name"), l) for l in config["lanes"]]
    for key in ("release", "sync"):
        if key in config:
            entries.append((key, config[key]))
    for name, entry in entries:
        if not name:
            raise ConfigError("every lane needs a name")
        evidence = entry.get("evidence", [])
        unknown = set(evidence) - EVIDENCE_ITEMS
        if unknown:
            raise ConfigError(f"lane '{name}' lists unknown evidence: {sorted(unknown)}")
        if name != "default" and entry is not default and not isinstance(entry.get("paths"), list):
            raise ConfigError(f"lane '{name}' needs a 'paths' list")
        small = entry.get("small_pr")
        if small is not None:
            if not isinstance(small, dict):
                raise ConfigError(f"lane '{name}' small_pr must be an object")
            if not isinstance(small.get("max_changed_lines"), int) or small["max_changed_lines"] < 1:
                raise ConfigError(f"lane '{name}' small_pr needs a positive integer 'max_changed_lines'")
            unknown = set(small.get("evidence", [])) - EVIDENCE_ITEMS
            if unknown:
                raise ConfigError(f"lane '{name}' small_pr lists unknown evidence: {sorted(unknown)}")
    if "sync" in config and not config["sync"].get("branch_prefix"):
        raise ConfigError("'sync' needs a 'branch_prefix'")
    return config


def load_config(path):
    try:
        with open(path, encoding="utf8") as f:
            return validate_config(json.load(f))
    except (OSError, ValueError) as e:
        raise ConfigError(f"cannot read {path}: {e}")


def lane_for(config, base, head, changed, release_changed):
    release = config.get("release")
    if release:
        if head.startswith("release/") and base == "main":
            # An empty list means the release branch's changes are unknown or there are none: never assume release.
            if release_changed and all(_matches_any(release["paths"], p) for p in release_changed):
                return "release"
        elif head == "main" and base == "develop":
            if all(_matches_any(release["paths"], p) for p in changed):
                return "release"
    sync = config.get("sync")
    if sync and head.startswith(sync["branch_prefix"]) and changed:
        if all(_matches_any(sync["paths"], p) for p in changed):
            return "sync"
    for entry in config["lanes"]:
        if any(_matches_any(entry["paths"], p) for p in changed):
            return entry["name"]
    return config["default"]["name"]


def evidence_for(config, lane_name):
    if lane_name in ("release", "sync") and lane_name in config:
        return list(config[lane_name].get("evidence", []))
    for entry in config["lanes"]:
        if entry["name"] == lane_name:
            return list(entry.get("evidence", []))
    if config["default"]["name"] == lane_name:
        return list(config["default"].get("evidence", []))
    raise ConfigError(f"unknown lane '{lane_name}'")


def evidence_for_change(config, lane_name, changed, changed_lines=None):
    """Evidence a PR needs: its lane's, plus that of every other configured lane it touches.

    A lane with `small_pr` swaps in the `small_pr` evidence (no model review) when
    `changed_lines` (additions plus deletions) is below `max_changed_lines`. Only a
    PR laned by that lane can qualify, and a PR with an app path is laned `app`, so
    a small PR never skips review on app code.

    The lane rank decides the lane name only. A PR touching app and pipeline
    paths is laned `app` but still owes the pipeline lane's evidence (such as a
    motivating incident). `release` and `sync` are decided by their own path
    rules, so their evidence is not combined.
    """
    items = evidence_for(config, lane_name)
    if lane_name in ("release", "sync"):
        return items
    for entry in config["lanes"]:
        if any(_matches_any(entry["paths"], p) for p in changed):
            evidence = entry.get("evidence", [])
            small = entry.get("small_pr")
            if small and entry["name"] == lane_name and changed_lines is not None \
                    and changed_lines < small["max_changed_lines"]:
                evidence = small.get("evidence", [])
                items = [i for i in items if i in evidence]
            items += [i for i in evidence if i not in items]
    return items


def _git_names(spec):
    out = subprocess.run(["git", "diff", "--name-only", spec], capture_output=True, text=True)
    if out.returncode != 0:
        raise ConfigError(f"git diff {spec} failed: {out.stderr.strip()}")
    return [l for l in out.stdout.splitlines() if l]


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--config", default=DEFAULT_CONFIG)
    ap.add_argument("--head-branch", required=True)
    ap.add_argument("--base-branch", required=True)
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--git", metavar="BASE_REF", help="diff BASE_REF...HEAD in the current repo")
    src.add_argument("--files", metavar="-", help="read changed paths from stdin")
    ap.add_argument("--release-files", metavar="PATH", help="file listing paths changed on the release branch")
    ap.add_argument("--develop-ref", default="origin/develop", help="with --git: ref for the release-branch diff")
    args = ap.parse_args(argv)
    try:
        config = load_config(args.config)
        if args.git:
            changed = _git_names(f"{args.git}...HEAD")
            release_changed = []
            if args.head_branch.startswith("release/") and args.base_branch == "main":
                release_changed = _git_names(f"{args.develop_ref}...HEAD")
        else:
            changed = [l.strip() for l in sys.stdin if l.strip()]
            release_changed = []
            if args.release_files:
                with open(args.release_files, encoding="utf8") as f:
                    release_changed = [l.strip() for l in f if l.strip()]
        print(lane_for(config, args.base_branch, args.head_branch, changed, release_changed))
        return 0
    except ConfigError as e:
        print(f"check_pr_lane: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
