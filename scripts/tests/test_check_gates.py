"""scripts/check_gates.sh end to end, in a scratch repo with a fake xcodebuild.

Each test builds a develop branch holding the gate scripts and a filled-in
pipeline_lanes.json, branches off it, makes one change, runs the script, and
checks the exit code and the summary block it prints.
"""
import json
import os
import shutil
import stat
import subprocess
import tempfile
import unittest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
SCRIPTS = ["check_gates.sh", "detect_build_target.sh", "check_coverage.py", "check_pr_lane.py", "check_tdd_commit_order.py", "check_gate_integrity.py"]

FAKE_XCODEBUILD = """#!/bin/sh
prev=""; for a in "$@"; do [ "$prev" = "-resultBundlePath" ] && mkdir -p "$a"; prev=$a; done
case "$FAKE_XCODEBUILD:$1" in
  pass:build) echo "** BUILD SUCCEEDED **" ;;
  pass:test)  echo "Test Case '-[AppTests testThing]' passed (0.001 seconds)."; echo "** TEST SUCCEEDED **" ;;
  *) echo "error: fake failure"; exit 65 ;;
esac
"""

# xcrun xccov view --report --json <bundle> prints the report in $FAKE_COV.
FAKE_XCRUN = """#!/bin/sh
cat "$FAKE_COV"
"""


def git(cwd, *args):
    return subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", *args],
                          cwd=cwd, check=True, capture_output=True, text=True).stdout


class CheckGatesTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.dir)
        self.repo = os.path.join(self.dir, "repo")
        os.makedirs(os.path.join(self.repo, "scripts"))
        for name in SCRIPTS:
            shutil.copy(os.path.join(ROOT, "scripts", name), os.path.join(self.repo, "scripts", name))
        with open(os.path.join(ROOT, "scaffold", "pipeline_lanes.json")) as f:
            self.config = json.loads(f.read().replace("YOUR_PROJECT", "App"))
        self.config["project"].update(persistence="swiftdata", ui="swiftui", scoped_layer_dirs=["App/Services"])
        self.config["gates"]["destination"] = "platform=iOS Simulator,name=Fake"
        self.write_config()
        self.write("CHANGELOG.md", "# Changelog\n\n## [Unreleased]\n- Something\n")
        self.write("App/Services/Existing.swift", "struct Existing {}\n")
        self.write("App/Views/HomeView.swift", 'Button("Save").accessibilityIdentifier("saveButton")\n')
        git(self.repo, "init", "-q", "-b", "develop")
        git(self.repo, "add", "-A")
        git(self.repo, "commit", "-q", "-m", "base")
        git(self.repo, "checkout", "-q", "-b", "chore/x")
        bindir = os.path.join(self.dir, "bin")
        os.makedirs(bindir)
        fake = os.path.join(bindir, "xcodebuild")
        with open(fake, "w") as f:
            f.write(FAKE_XCODEBUILD)
        os.chmod(fake, os.stat(fake).st_mode | stat.S_IEXEC)
        xcrun = os.path.join(bindir, "xcrun")
        with open(xcrun, "w") as f:
            f.write(FAKE_XCRUN)
        os.chmod(xcrun, os.stat(xcrun).st_mode | stat.S_IEXEC)
        self.env = dict(os.environ, PATH=bindir + os.pathsep + os.environ["PATH"], FAKE_XCODEBUILD="pass")

    def write(self, rel, text):
        path = os.path.join(self.repo, rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as f:
            f.write(text)

    def write_config(self):
        self.write("scripts/pipeline_lanes.json", json.dumps(self.config, indent=2))

    def commit(self, files, msg="change"):
        for rel, text in files.items():
            self.write(rel, text)
        git(self.repo, "add", "-A")
        git(self.repo, "commit", "-q", "-m", msg)

    def run_gates(self, *args, **env):
        result = subprocess.run(["bash", "scripts/check_gates.sh", *args], cwd=self.repo,
                                env=dict(self.env, **env), capture_output=True, text=True)
        summary = result.stdout.split("=== Gate summary (paste into the PR body) ===\n")[-1]
        return result.returncode, summary, result.stdout

    def row(self, summary, name):
        rows = [line for line in summary.splitlines() if line[4:].startswith(name)]
        self.assertEqual(len(rows), 1, summary)
        return rows[0]

    def test_docs_only_change_skips_build_and_passes(self):
        self.commit({"docs/notes.md": "x\n"})
        rc, summary, out = self.run_gates()
        self.assertEqual(rc, 0, out)
        lines = summary.splitlines()
        self.assertEqual(lines[0], "Gates run at " + git(self.repo, "rev-parse", "HEAD").strip())
        self.assertEqual(lines[1], "Lane: docs")
        self.assertEqual(lines[2], "Gates:")
        self.assertEqual(len(lines), 3 + 11, summary)
        self.assertEqual(self.row(summary, "Build"), "[–] Build — skipped (no build-relevant changes)")
        self.assertEqual(self.row(summary, "Gate integrity"), "[✓] Gate integrity")

    def test_unconfigured_layers_fail_only_when_swift_is_added(self):
        self.config["project"]["scoped_layer_dirs"] = ["Nowhere/"]
        self.write_config()
        self.commit({"docs/notes.md": "x\n"})
        rc, summary, out = self.run_gates("--only", "9")
        self.assertEqual(rc, 0, out)
        self.assertEqual(self.row(summary, "RED-before-GREEN"), "[–] RED-before-GREEN commit order — skipped (no new Swift files)")
        self.commit({"App/Services/NewThing.swift": "struct NewThing {}\n"})
        rc, summary, out = self.run_gates("--only", "9")
        self.assertEqual(rc, 1, out)
        self.assertIn("unconfigured", self.row(summary, "RED-before-GREEN"))

    def test_dirty_tree_stops_before_any_gate(self):
        self.write("App/Dirty.swift", "x\n")
        rc, _, out = self.run_gates()
        self.assertEqual(rc, 3, out)
        self.assertNotIn("Gates run at", out)

    def test_swift_change_runs_build_and_tests(self):
        self.commit({"App/Services/Existing.swift": "struct Existing { let a = 1 }\n"})
        rc, summary, out = self.run_gates()
        self.assertEqual(rc, 0, out)
        self.assertEqual(self.row(summary, "Build"), "[✓] Build")
        self.assertEqual(self.row(summary, "Tests"), "[✓] Tests — 1 tests executed")

    def test_failed_build_fails_and_skips_tests(self):
        self.commit({"App/Services/Existing.swift": "struct Existing { let a = 1 }\n"})
        rc, summary, out = self.run_gates(FAKE_XCODEBUILD="fail")
        self.assertEqual(rc, 1, out)
        self.assertTrue(self.row(summary, "Build").startswith("[✗] Build — xcodebuild exit 65"))
        self.assertEqual(self.row(summary, "Tests"), "[–] Tests — skipped (build failed)")

    def test_unset_scheme_fails_closed(self):
        self.config["gates"]["scheme"] = ""
        self.write_config()
        self.commit({"App/Services/Existing.swift": "struct Existing { let a = 1 }\n"})
        rc, summary, out = self.run_gates()
        self.assertEqual(rc, 1, out)
        self.assertIn("[✗] Build — not configured", summary)

    def test_new_file_without_coverage_data_fails(self):
        self.commit({"App/Services/NewThing.swift": "struct NewThing {}\n"})
        rc, summary, out = self.run_gates("--only", "6")
        self.assertEqual(rc, 1, out)
        self.assertEqual(self.row(summary, "Coverage"), "[✗] Coverage — needs Gate 2 in the same run")

    def test_unreadable_coverage_report_fails(self):
        self.commit({"App/Services/NewThing.swift": "struct NewThing {}\n"})
        rc, summary, out = self.run_gates(FAKE_COV=os.path.join(self.dir, "missing.json"))
        self.assertEqual(rc, 1, out)
        self.assertIn("could not read the xccov report", out)
        self.assertTrue(self.row(summary, "Coverage").startswith("[✗] Coverage"))

    def coverage(self, **pct_by_path):
        path = os.path.join(self.dir, "cov.json")
        files = [{"path": "/build/" + p, "name": p.rsplit("/", 1)[-1], "lineCoverage": c} for p, c in pct_by_path.items()]
        with open(path, "w") as f:
            json.dump({"targets": [{"name": "App.app", "files": files}]}, f)
        return path

    def test_new_file_under_threshold_fails_coverage(self):
        self.commit({"App/Services/Brand.swift": "struct Brand {}\n"})
        cov = self.coverage(**{"App/Services/Brand.swift": 0.10})
        rc, summary, out = self.run_gates("--only", "1,2,6", FAKE_COV=cov)
        self.assertEqual(rc, 1, out)
        self.assertIn("[FAIL] App/Services/Brand.swift 10.0%", out)
        self.assertTrue(self.row(summary, "Coverage").startswith("[✗] Coverage"))

    def test_new_file_over_threshold_passes_coverage(self):
        self.commit({"App/Services/Brand.swift": "struct Brand {}\n", "AppTests/BrandTests.swift": "x\n"})
        cov = self.coverage(**{"App/Services/Brand.swift": 0.92})
        rc, summary, out = self.run_gates("--only", "1,2,6", FAKE_COV=cov)
        self.assertEqual(rc, 0, out)
        self.assertIn("[  OK] App/Services/Brand.swift 92.0%", out)
        self.assertIn("[skip] AppTests/BrandTests.swift", out)

    def test_production_path_containing_tests_is_not_skipped(self):
        self.commit({"App/Contests/Score.swift": "struct Score {}\n"})
        cov = self.coverage(**{"App/Contests/Score.swift": 0.20})
        rc, _, out = self.run_gates("--only", "1,2,6", FAKE_COV=cov)
        self.assertEqual(rc, 1, out)
        self.assertIn("[FAIL] App/Contests/Score.swift 20.0%", out)

    def test_invalid_sensitive_pattern_fails(self):
        self.config["gates"]["sensitive_paths"] = "(unclosed"
        self.write_config()
        self.commit({"App/Services/Existing.swift": "struct Existing { let a = 1 }\n"})
        rc, summary, out = self.run_gates("--only", "7")
        self.assertEqual(rc, 1, out)
        self.assertEqual(self.row(summary, "Security"), "[✗] Security — gates.sensitive_paths is not a valid regex")

    def test_flag_without_value_exits_2(self):
        result = subprocess.run(["bash", "scripts/check_gates.sh", "--base"], cwd=self.repo, env=self.env,
                                capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 2)
        self.assertIn("--base needs a value", result.stderr)

    def test_partial_run_ignores_review_log_but_full_run_does_not(self):
        self.commit({"docs/notes.md": "x\n"})
        self.write(".claude/context/rejections.md", "entry\n")
        rc, _, out = self.run_gates("--only", "3,4,5,10")
        self.assertEqual(rc, 0, out)
        rc, _, out = self.run_gates()
        self.assertEqual(rc, 3, out)

    def test_todo_branch_and_changelog_failures(self):
        self.commit({"App/Services/Existing.swift": "struct Existing {} // TODO later\n",
                     "CHANGELOG.md": "# Changelog\n\n## [Unreleased]\n"})
        git(self.repo, "branch", "-m", "chore/x", "wip")
        rc, summary, out = self.run_gates("--only", "3,4,5")
        self.assertEqual(rc, 1, out)
        self.assertIn("Gates (only 3,4,5):", summary)
        self.assertTrue(self.row(summary, "No TODO").startswith("[✗] No TODO/FIXME/HACK — 1 hit(s)"))
        self.assertTrue(self.row(summary, "Branch naming").startswith("[✗]"))
        self.assertTrue(self.row(summary, "CHANGELOG").startswith("[✗]"))
        self.assertIn("Existing.swift:1:", out)

    def test_empty_unreleased_above_a_release_fails_changelog(self):
        # FinanceTracker PR#201 F1: a fixed-size window read the previous release's bullets as Unreleased entries.
        self.commit({"CHANGELOG.md": "# Changelog\n\n## [Unreleased]\n\n### Added\n\n## [1.0.0]\n- Old entry\n"})
        rc, summary, out = self.run_gates("--only", "5")
        self.assertEqual(rc, 1, out)
        self.assertEqual(self.row(summary, "CHANGELOG"), "[✗] CHANGELOG — Unreleased section missing or empty")

    def test_empty_unreleased_with_crlf_blank_lines_fails_changelog(self):
        # pragma #164: a CRLF blank line is "\r", which awk counted as a non-empty line.
        self.commit({"CHANGELOG.md": "# Changelog\r\n\r\n## [Unreleased]\r\n\r\n## [1.0.0]\r\n- Old entry\r\n"})
        rc, summary, out = self.run_gates("--only", "5")
        self.assertEqual(rc, 1, out)
        self.assertEqual(self.row(summary, "CHANGELOG"), "[✗] CHANGELOG — Unreleased section missing or empty")

    def test_empty_last_unreleased_with_link_definitions_fails_changelog(self):
        # pragma #165: Keep-a-Changelog link definitions at the end of the file are not entries.
        self.commit({"CHANGELOG.md": "# Changelog\n\n## [Unreleased]\n\n"
                                     "[Unreleased]: https://example.com/compare/v1.0.0...HEAD\n"
                                     "[1.0.0]: https://example.com/releases/v1.0.0\n"})
        rc, summary, out = self.run_gates("--only", "5")
        self.assertEqual(rc, 1, out)
        self.assertEqual(self.row(summary, "CHANGELOG"), "[✗] CHANGELOG — Unreleased section missing or empty")

    def test_unreleased_entry_under_subheading_passes_changelog(self):
        # pragma #166: a real entry under "### Added" above a release still counts.
        self.commit({"CHANGELOG.md": "# Changelog\r\n\r\n## [Unreleased]\r\n\r\n### Added\r\n- New thing\r\n\r\n"
                                     "## [1.0.0]\r\n- Old entry\r\n"})
        rc, summary, out = self.run_gates("--only", "5")
        self.assertEqual(rc, 0, out)
        self.assertEqual(self.row(summary, "CHANGELOG"), "[✓] CHANGELOG")

    def test_last_unreleased_with_entry_and_link_definitions_passes_changelog(self):
        # pragma #166: an [Unreleased] section that is last in the file still counts its entries.
        self.commit({"CHANGELOG.md": "# Changelog\n\n## [Unreleased]\n\n### Fixed\n- A fix\n\n"
                                     "[Unreleased]: https://example.com/compare/v1.0.0...HEAD\n"})
        rc, summary, out = self.run_gates("--only", "5")
        self.assertEqual(rc, 0, out)
        self.assertEqual(self.row(summary, "CHANGELOG"), "[✓] CHANGELOG")

    def test_project_rules_hits_allow_and_missing_mode(self):
        self.config["gates"]["rules"] = [
            {"name": "View imports SwiftData", "paths": "/Views/", "pattern": "^import SwiftData"},
            {"name": "Money as Double", "pattern": r"amount\s*:\s*Double", "allow": ["Ratio.swift"]},
            {"name": "Model not final", "paths": "/Models/", "added_only": True, "mode": "missing", "pattern": "final class"},
        ]
        self.write_config()
        git(self.repo, "commit", "-qam", "config")
        git(self.repo, "checkout", "-q", "develop")
        git(self.repo, "merge", "-q", "chore/x")
        git(self.repo, "checkout", "-q", "chore/x")
        self.commit({"App/Views/HomeView.swift": 'import SwiftData\nButton("Save").accessibilityIdentifier("saveButton")\n',
                     "App/Services/Ratio.swift": "let amount: Double = 0.5\n",
                     "App/Models/Thing.swift": "struct Thing {}\n"})
        rc, summary, out = self.run_gates("--only", "10")
        self.assertEqual(rc, 1, out)
        self.assertIn("[FAIL] View imports SwiftData: App/Views/HomeView.swift:1:", out)
        self.assertIn("[accepted] Money as Double: App/Services/Ratio.swift:1:", out)
        self.assertIn("[FAIL] Model not final: App/Models/Thing.swift", out)
        self.assertEqual(self.row(summary, "Architecture"), "[✗] Architecture & layer-rule compliance — 2 violation(s) (see report)")

    def test_force_unwrap_outside_tests_fails(self):
        self.commit({"App/Services/Existing.swift": "let x = try! load()\n",
                     "AppTests/ExistingTests.swift": "let y = try! load()\n"})
        rc, _, out = self.run_gates("--only", "10")
        self.assertEqual(rc, 1, out)
        self.assertIn("App/Services/Existing.swift:1:", out)
        self.assertNotIn("AppTests/ExistingTests.swift", out)

    def test_ui_selectors_need_an_identifier_or_typed_text(self):
        self.commit({"AppUITests/FlowTests.swift":
                     'app.buttons["saveButton"].tap()\nfield.typeText("Coffee Shop")\n'
                     'app.staticTexts["Coffee Shop"].tap()\napp.buttons["missingButton"].tap()\n'})
        rc, _, out = self.run_gates("--only", "10")
        self.assertEqual(rc, 1, out)
        self.assertIn('UI selector: "missingButton"', out)
        self.assertNotIn('"saveButton" is not', out)
        self.assertNotIn('"Coffee Shop" is not', out)

    def test_bad_rule_says_the_check_could_not_run(self):
        self.config["gates"]["rules"] = [{"name": "No pattern", "paths": "App/"}]
        self.write_config()
        git(self.repo, "commit", "-qam", "config")
        git(self.repo, "checkout", "-q", "develop")
        git(self.repo, "merge", "-q", "chore/x")
        git(self.repo, "checkout", "-q", "chore/x")
        self.commit({"App/Services/Existing.swift": "struct Existing { let a = 2 }\n"})
        rc, summary, out = self.run_gates("--only", "10")
        self.assertEqual(rc, 1, out)
        self.assertIn("KeyError", out)
        self.assertEqual(self.row(summary, "Architecture"), "[✗] Architecture & layer-rule compliance — check did not run (see report)")

    def sensitive_services(self):
        self.config["gates"]["sensitive_paths"] = "Services/"
        self.write_config()
        git(self.repo, "commit", "-qam", "config")
        git(self.repo, "checkout", "-q", "develop")
        git(self.repo, "merge", "-q", "chore/x")
        git(self.repo, "checkout", "-q", "chore/x")

    def test_sensitive_file_asks_for_security_review_with_exit_4(self):
        self.sensitive_services()
        self.commit({"App/Services/Existing.swift": "struct Existing { let a = 1 }\n"})
        rc, summary, out = self.run_gates("--only", "7")
        self.assertEqual(rc, 4, out)
        self.assertEqual(self.row(summary, "Security"), "[!] Security — security-review required: App/Services/Existing.swift")

    def test_blocking_failure_outranks_pending_security_review(self):
        self.sensitive_services()
        self.commit({"App/Services/Existing.swift": "let x = try! load()\n"})
        rc, summary, out = self.run_gates("--only", "7,10")
        self.assertEqual(rc, 1, out)
        self.assertTrue(self.row(summary, "Security").startswith("[!] Security"), summary)

    def test_duplicated_block_is_an_advisory_candidate(self):
        block = "let a = 1\nlet b = 2\nlet c = 3\n"
        self.commit({"App/Services/One.swift": block, "App/Services/Two.swift": block})
        rc, summary, out = self.run_gates("--only", "8")
        self.assertEqual(rc, 0, out)
        self.assertIn("duplicated block App/Services/One.swift:1-3 (3 lines)", out)
        self.assertEqual(self.row(summary, "Abstraction bloat"), "[i] Abstraction bloat — 2 candidate(s) found (see report)")

    def test_single_conformer_protocol_is_a_candidate(self):
        self.commit({"App/Services/Store.swift": "protocol Store {}\nstruct DiskStore: Store {}\n"})
        _, _, out = self.run_gates("--only", "8")
        self.assertIn("protocol Store (App/Services/Store.swift) — 1 conforming type(s)", out)


if __name__ == "__main__":
    unittest.main()
