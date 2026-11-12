#!/usr/bin/env python3
"""Check that PipelineLanes.tla matches scripts/check_pr_lane.py.

Runs every PR shape in PipelineLanes.PRs (5 branch kinds x 255 path-kind sets x
small or not = 2550) through the real lane_for() and evidence_for_change(), and
compares the result with a transliteration of the TLA+ LaneFor and Need. No Java
needed. Uses the scaffold lane config plus the sync block of scripts/pipeline_lanes.json
(the model's lane config, see formal/README.md).

Usage: python3 formal/conformance_lanes.py      (from the repo root)
Exit codes: 0 model and script agree on every shape, 1 a mismatch, 2 setup error.
"""
import itertools
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))
import check_pr_lane as real  # noqa: E402

PATH_OF = {  # one representative path per kind (see the Kinds comments in PipelineLanes.tla)
    "app_src": "YOUR_PROJECT/A.swift",
    "pbxproj": "YOUR_PROJECT.xcodeproj/project.pbxproj",
    "changelog": "CHANGELOG.md",
    "skill_plain": ".claude/skills/foo/SKILL.md",
    "skill_guarded": ".claude/skills/gates/SKILL.md",
    "pipe_plain": "AGENTS.md",
    "pipe_guarded": "scripts/check_x.py",
    "docs": "README.md",
}
BRANCH = {  # (head, base)
    "work": ("feature/x", "develop"),
    "release": ("release/1.0", "main"),
    "backmerge": ("main", "develop"),
    "sync": ("sync/2026-10-22", "develop"),
    "fork": ("fork:feature/x", "develop"),
}
APP, REL, SYNC = {"app_src", "pbxproj"}, {"pbxproj", "changelog"}, {"skill_plain", "skill_guarded"}
PIPE = SYNC | {"pipe_plain", "pipe_guarded"}
EXCL = {"skill_guarded", "pipe_guarded"}
EV = {"release": set(), "sync": {"synced"}, "app": {"gate", "verdict", "codereview"},
      "pipeline": {"gate", "verdict", "incident"}, "docs": {"gate"}}
SMALL = {"gate", "incident"}
ITEM = {"gate_summary": "gate", "review_verdict": "verdict", "code_review": "codereview",
        "motivating_incident": "incident", "synced_from": "synced"}


def model_lane(branch, ch):  # LaneFor in PipelineLanes.tla
    if branch in ("release", "backmerge") and ch <= REL:
        return "release"
    if branch == "sync" and ch <= SYNC:
        return "sync"
    if ch & APP:
        return "app"
    if ch & PIPE:
        return "pipeline"
    return "docs"


def model_need(branch, ch, small):  # Need in PipelineLanes.tla
    lane = model_lane(branch, ch)
    if lane in ("release", "sync"):
        return set(EV[lane])
    active = lane == "pipeline" and small and not (ch & EXCL)
    need = (EV[lane] & SMALL) if active else set(EV[lane])
    if ch & APP:
        need |= EV["app"]
    if ch & PIPE:
        need |= SMALL if active else EV["pipeline"]
    return need


def main():
    try:
        with open(os.path.join(ROOT, "scaffold", "pipeline_lanes.json"), encoding="utf8") as f:
            config = json.load(f)
        with open(os.path.join(ROOT, "scripts", "pipeline_lanes.json"), encoding="utf8") as f:
            config["sync"] = json.load(f)["sync"]
        config["project"] = {}  # scaffold placeholders (YOUR_PERSISTENCE) do not validate; lanes do not read them
        real.validate_config(config)
    except (OSError, ValueError, KeyError, real.ConfigError) as e:
        print(f"conformance_lanes: {e}", file=sys.stderr)
        return 2
    kinds = sorted(PATH_OF)
    bad = checked = 0
    for branch, (head, base) in BRANCH.items():
        for r in range(1, len(kinds) + 1):
            for combo in itertools.combinations(kinds, r):
                ch = set(combo)
                files = [PATH_OF[k] for k in combo]
                rel = files if head.startswith("release/") else []
                lane = real.lane_for(config, base, head, files, rel)
                for small in (False, True):
                    items = {ITEM[i] for i in real.evidence_for_change(config, lane, files, 10 if small else 100)}
                    checked += 1
                    if lane != model_lane(branch, ch) or items != model_need(branch, ch, small):
                        bad += 1
                        print(f"MISMATCH {branch} {sorted(ch)} small={small}: script {lane} {sorted(items)}, "
                              f"model {model_lane(branch, ch)} {sorted(model_need(branch, ch, small))}")
    print(f"{checked} shapes checked, {bad} mismatches")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
