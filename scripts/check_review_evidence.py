#!/usr/bin/env python3
"""Check that a PR carries the evidence its lane requires.

Runs in the `review-evidence` workflow on `pull_request_target`, from the base
branch's checkout. It reads the PR only through the GitHub REST API and treats
PR text as data: nothing from the PR is executed.

The lane comes from check_pr_lane.py, with two extra rules here: a PR from a
fork is never laned release, sync or back-merge, and a release/* PR whose
develop...head compare fails exits 2 instead of guessing.

Evidence items (each SHA-tied item accepts an ancestor SHA when every file
changed between it and the head is on the config's carryover_paths):
  gate_summary         "Gates run at <sha>" in the PR body (last occurrence)
  review_verdict       latest "## Review Agent verdict:" review posted by an
                       OWNER, MEMBER or COLLABORATOR is APPROVED and contains
                       "Reviewed at <sha>"; other accounts' reviews are ignored
  code_review          a "code-review: <url or 'no issues'> at <sha>" body line
  motivating_incident  a non-empty "Motivating incident:" body line
  synced_from          a non-empty "Synced from:" body line

Usage: check_review_evidence.py --repo OWNER/NAME --pr N [--config PATH]
Needs GH_TOKEN with pull-requests:read and contents:read.
Exit codes: 0 all evidence present, 1 something missing, 2 error.
"""
import argparse
import json
import os
import re
import sys
import urllib.error
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import check_pr_lane  # noqa: E402

SHA = r"([0-9a-f]{40})"
VERDICT_HEADER = "## Review Agent verdict:"
# Only verdicts posted by accounts with write-level standing count; anyone else's review is ignored.
TRUSTED_ASSOCIATIONS = {"OWNER", "MEMBER", "COLLABORATOR"}
COMPARE_FILE_LIMIT = 300  # GitHub's cap on files in one compare response


def _sha_ok(sha, head_sha, carryover, changed_between):
    if sha == head_sha:
        return True, f"at head {head_sha[:7]}"
    files = changed_between(sha)
    if files is None:
        return False, f"{sha[:7]} is not an ancestor of head {head_sha[:7]}, or too many files changed since it to check"
    extra = [f for f in files if not any(check_pr_lane.glob_match(p, f) for p in carryover)]
    if extra:
        return False, f"{sha[:7]} is stale: head {head_sha[:7]} also changes {', '.join(extra[:5])}"
    return True, f"at {sha[:7]}; later commits touch only carryover paths"


def _latest_verdict(reviews):
    verdicts = [r for r in reviews if (r.get("body") or "").lstrip().startswith(VERDICT_HEADER)
                and r.get("author_association") in TRUSTED_ASSOCIATIONS]
    if not verdicts:
        return None
    return max(verdicts, key=lambda r: r.get("submitted_at") or "")


def lane_head(pr, repo):
    """The head branch name to lane by. A fork's branch never gets the release, sync or back-merge lanes."""
    head = pr["head"]["ref"]
    if (pr["head"].get("repo") or {}).get("full_name") != repo:
        return "fork:" + head
    return head


def evaluate(items, head_sha, body, reviews, carryover, changed_between):
    """Return [(item, ok, detail)] for each required evidence item."""
    body = body or ""
    results = []
    for item in items:
        if item == "gate_summary":
            found = re.findall(r"Gates run at " + SHA, body)
            if not found:
                results.append((item, False, "no 'Gates run at <sha>' line in the PR body"))
            else:
                ok, detail = _sha_ok(found[-1], head_sha, carryover, changed_between)
                results.append((item, ok, detail))
        elif item == "review_verdict":
            latest = _latest_verdict(reviews)
            if latest is None:
                results.append((item, False, "no '## Review Agent verdict:' review"))
                continue
            text = latest["body"].lstrip()
            first = text.splitlines()[0]
            if "APPROVED" not in first:
                results.append((item, False, f"latest verdict is not APPROVED: {first[len(VERDICT_HEADER):].strip()}"))
                continue
            m = re.search(r"Reviewed at " + SHA, text)
            if not m:
                results.append((item, False, "latest verdict has no 'Reviewed at <sha>' line"))
                continue
            ok, detail = _sha_ok(m.group(1), head_sha, carryover, changed_between)
            results.append((item, ok, detail))
        elif item == "code_review":
            found = re.findall(r"(?m)^code-review:\s*\S.*?\bat " + SHA + r"\s*$", body)
            if not found:
                results.append((item, False, "no 'code-review: <url or no issues> at <sha>' line in the PR body"))
            else:
                ok, detail = _sha_ok(found[-1], head_sha, carryover, changed_between)
                results.append((item, ok, detail))
        elif item in ("motivating_incident", "synced_from"):
            label = "Motivating incident:" if item == "motivating_incident" else "Synced from:"
            ok = re.search(r"(?m)^" + re.escape(label) + r"[ \t]*\S", body) is not None
            results.append((item, ok, "present" if ok else f"no non-empty '{label}' line in the PR body"))
        else:
            results.append((item, False, f"unknown evidence item '{item}'"))
    return results


class Api:
    def __init__(self, repo, token):
        self.base = f"https://api.github.com/repos/{repo}"
        self.token = token

    def get(self, path):
        req = urllib.request.Request(self.base + path, headers={
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {self.token}",
            "X-GitHub-Api-Version": "2022-11-28",
        })
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.load(resp)

    def paged(self, path):
        out, page = [], 1
        while True:
            sep = "&" if "?" in path else "?"
            batch = self.get(f"{path}{sep}per_page=100&page={page}")
            out.extend(batch)
            if len(batch) < 100:
                return out
            page += 1

    def compare_files(self, base, head, require_ancestor=True):
        """Files changed base...head, or None when base is not an ancestor of head.

        With require_ancestor=False a diverged base is fine (base...head is a
        merge-base diff), and None means only that the compare was not found.
        GitHub lists at most 300 files per compare, so a full list is treated as
        unknown (None) rather than trusted as complete.
        """
        try:
            data = self.get(f"/compare/{base}...{head}")
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return None
            raise
        if require_ancestor and data.get("status") not in ("ahead", "identical"):
            return None
        files = [f["filename"] for f in data.get("files", [])]
        return None if len(files) >= COMPARE_FILE_LIMIT else files


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--repo", required=True)
    ap.add_argument("--pr", required=True, type=int)
    ap.add_argument("--config", default=check_pr_lane.DEFAULT_CONFIG)
    args = ap.parse_args(argv)
    token = os.environ.get("GH_TOKEN")
    if not token:
        print("check_review_evidence: GH_TOKEN is not set", file=sys.stderr)
        return 2
    try:
        config = check_pr_lane.load_config(args.config)
        api = Api(args.repo, token)
        pr = api.get(f"/pulls/{args.pr}")
        head_sha, head, base = pr["head"]["sha"], lane_head(pr, args.repo), pr["base"]["ref"]
        files = []
        for f in api.paged(f"/pulls/{args.pr}/files"):
            files.append(f["filename"])
            if f.get("previous_filename"):
                files.append(f["previous_filename"])
        release_changed = []
        if head.startswith("release/") and base == "main":
            release_changed = api.compare_files("develop", head_sha, require_ancestor=False)
            if release_changed is None:
                raise ValueError(f"cannot compare develop...{head_sha[:7]} to lane this release PR")
        lane = check_pr_lane.lane_for(config, base, head, files, release_changed)
        items = check_pr_lane.evidence_for_change(config, lane, files)
        reviews = api.paged(f"/pulls/{args.pr}/reviews")
        results = evaluate(items, head_sha, pr.get("body"), reviews, config.get("carryover_paths", []),
                           lambda sha: api.compare_files(sha, head_sha))
    except (check_pr_lane.ConfigError, urllib.error.URLError, KeyError, ValueError) as e:
        print(f"check_review_evidence: {e}", file=sys.stderr)
        return 2
    print(f"lane: {lane} (head {head}, base {base}, {len(files)} changed paths)")
    if not items:
        print("no evidence required for this lane")
    for item, ok, detail in results:
        print(f"{'found  ' if ok else 'MISSING'} {item}: {detail}")
    return 0 if all(ok for _, ok, _ in results) else 1


if __name__ == "__main__":
    sys.exit(main())
