import os
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import check_pr_lane as lane  # noqa: E402

CONFIG = {
    "release": {"paths": ["App.xcodeproj/project.pbxproj", "CHANGELOG.md"], "evidence": []},
    "sync": {"branch_prefix": "sync/", "paths": [".claude/skills/**", "scaffold/**"], "evidence": ["synced_from"]},
    "lanes": [
        {"name": "app", "paths": ["App/**", "*.xcodeproj/**", "**/*.xctestplan"], "evidence": ["gate_summary"]},
        {"name": "pipeline", "paths": [".claude/skills/**", "scripts/**", "AGENTS.md"], "evidence": ["review_verdict"]},
    ],
    "default": {"name": "docs", "evidence": []},
    "carryover_paths": [],
}


class GlobMatchTests(unittest.TestCase):
    def test_double_star_matches_nested(self):
        self.assertTrue(lane.glob_match("App/**", "App/Views/Home.swift"))

    def test_leading_double_star_matches_root_and_nested(self):
        self.assertTrue(lane.glob_match("**/*.xctestplan", "Unit.xctestplan"))
        self.assertTrue(lane.glob_match("**/*.xctestplan", "Plans/Unit.xctestplan"))

    def test_single_star_does_not_cross_slash(self):
        self.assertFalse(lane.glob_match("*.md", "docs/readme.md"))
        self.assertTrue(lane.glob_match("*.md", "README.md"))

    def test_exact_root_file(self):
        self.assertTrue(lane.glob_match("AGENTS.md", "AGENTS.md"))
        self.assertFalse(lane.glob_match("AGENTS.md", "sub/AGENTS.md"))


class LaneForTests(unittest.TestCase):
    def lane(self, changed, base="develop", head="chore/x", release_changed=None):
        return lane.lane_for(CONFIG, base, head, changed, release_changed or [])

    def test_app_outranks_pipeline(self):
        self.assertEqual(self.lane(["scripts/a.py", "App/Model.swift"]), "app")

    def test_pipeline(self):
        self.assertEqual(self.lane([".claude/skills/review/SKILL.md"]), "pipeline")

    def test_docs_default(self):
        self.assertEqual(self.lane(["docs/notes.md"]), "docs")

    def test_empty_change_is_default(self):
        self.assertEqual(self.lane([]), "docs")

    def test_release_uses_release_branch_commits_not_pr_diff(self):
        # The PR diff to main holds the whole release; only the release branch's own commits count.
        self.assertEqual(
            self.lane(["App/Model.swift", "CHANGELOG.md"], base="main", head="release/1.5.0",
                      release_changed=["CHANGELOG.md", "App.xcodeproj/project.pbxproj"]),
            "release",
        )

    def test_release_branch_with_other_commits_is_laned_by_paths(self):
        self.assertEqual(
            self.lane(["App/Model.swift"], base="main", head="release/1.5.0",
                      release_changed=["CHANGELOG.md", "App/Model.swift"]),
            "app",
        )

    def test_release_branch_with_unknown_commits_is_laned_by_paths(self):
        # No release-branch file list (compare failed or not supplied) must not mean "release".
        self.assertEqual(self.lane(["App/Model.swift"], base="main", head="release/1.5.0"), "app")

    def test_release_branch_to_develop_is_not_release(self):
        self.assertEqual(self.lane(["CHANGELOG.md"], base="develop", head="release/1.5.0"), "docs")

    def test_back_merge_uses_pr_diff(self):
        self.assertEqual(self.lane(["CHANGELOG.md"], base="develop", head="main"), "release")

    def test_back_merge_with_other_paths_is_laned_by_paths(self):
        self.assertEqual(self.lane(["CHANGELOG.md", "scripts/a.py"], base="develop", head="main"), "pipeline")

    def test_sync_needs_sync_paths(self):
        self.assertEqual(self.lane([".claude/skills/a/SKILL.md", "scaffold/x.yml"], head="sync/2026-09-28"), "sync")

    def test_sync_branch_with_other_paths_is_laned_by_paths(self):
        self.assertEqual(self.lane([".claude/skills/a/SKILL.md", "scripts/a.py"], head="sync/2026-09-28"), "pipeline")

    def test_no_sync_lane_in_config(self):
        cfg = dict(CONFIG)
        del cfg["sync"]
        self.assertEqual(lane.lane_for(cfg, "develop", "sync/x", [".claude/skills/a/SKILL.md"], []), "pipeline")

    def test_mixed_pr_needs_evidence_of_every_touched_lane(self):
        # An app + pipeline PR is laned app but must still carry pipeline evidence.
        self.assertEqual(
            lane.evidence_for_change(CONFIG, "app", ["App/Model.swift", "scripts/a.py"]),
            ["gate_summary", "review_verdict"],
        )

    def test_single_lane_evidence_unchanged(self):
        self.assertEqual(lane.evidence_for_change(CONFIG, "app", ["App/Model.swift"]), ["gate_summary"])

    def test_release_and_sync_evidence_not_unioned(self):
        self.assertEqual(lane.evidence_for_change(CONFIG, "release", ["CHANGELOG.md", "scripts/a.py"]), [])
        self.assertEqual(lane.evidence_for_change(CONFIG, "sync", [".claude/skills/a/SKILL.md"]), ["synced_from"])

    def test_evidence_for_lane(self):
        self.assertEqual(lane.evidence_for(CONFIG, "app"), ["gate_summary"])
        self.assertEqual(lane.evidence_for(CONFIG, "release"), [])
        self.assertEqual(lane.evidence_for(CONFIG, "sync"), ["synced_from"])
        self.assertEqual(lane.evidence_for(CONFIG, "docs"), [])


class ConfigValidationTests(unittest.TestCase):
    def test_missing_lanes_is_invalid(self):
        with self.assertRaises(lane.ConfigError):
            lane.validate_config({"default": {"name": "docs", "evidence": []}})

    def test_unknown_evidence_item_is_invalid(self):
        bad = dict(CONFIG, lanes=[{"name": "app", "paths": ["App/**"], "evidence": ["vibes"]}])
        with self.assertRaises(lane.ConfigError):
            lane.validate_config(bad)

    def test_valid_config(self):
        lane.validate_config(CONFIG)

    def test_repo_config_is_valid(self):
        path = os.path.join(os.path.dirname(__file__), "..", "pipeline_lanes.json")
        lane.validate_config(lane.load_config(path))


class CliTests(unittest.TestCase):
    def test_git_mode_prints_lane(self):
        with tempfile.TemporaryDirectory() as d:
            def git(*a):
                subprocess.run(["git", *a], cwd=d, check=True, capture_output=True)
            git("init", "-q", "-b", "develop")
            git("config", "user.email", "t@example.com")
            git("config", "user.name", "t")
            open(os.path.join(d, "README.md"), "w").write("x\n")
            git("add", ".")
            git("commit", "-qm", "base")
            git("checkout", "-qb", "chore/x")
            os.makedirs(os.path.join(d, "scripts"))
            open(os.path.join(d, "scripts", "a.py"), "w").write("x\n")
            git("add", ".")
            git("commit", "-qm", "change")
            cfg = os.path.join(d, "lanes.json")
            import json
            json.dump(CONFIG, open(cfg, "w"))
            script = os.path.join(os.path.dirname(__file__), "..", "check_pr_lane.py")
            out = subprocess.run(
                [sys.executable, script, "--git", "develop", "--head-branch", "chore/x",
                 "--base-branch", "develop", "--config", cfg],
                cwd=d, capture_output=True, text=True,
            )
            self.assertEqual(out.returncode, 0, out.stderr)
            self.assertEqual(out.stdout.strip(), "pipeline")

    def test_invalid_config_exits_2(self):
        with tempfile.TemporaryDirectory() as d:
            cfg = os.path.join(d, "lanes.json")
            open(cfg, "w").write("{not json")
            script = os.path.join(os.path.dirname(__file__), "..", "check_pr_lane.py")
            out = subprocess.run([sys.executable, script, "--config", cfg, "--head-branch", "x",
                                  "--base-branch", "develop", "--files", "-"],
                                 input="", capture_output=True, text=True)
            self.assertEqual(out.returncode, 2)


if __name__ == "__main__":
    unittest.main()
