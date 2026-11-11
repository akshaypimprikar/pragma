"""scripts/check_gate_integrity.py in a scratch repo: which path each diff block is
attributed to (#159).

Each test makes a develop branch, branches off it, commits one change and runs
the script against develop. A path is read from `git diff -z --name-status`, so a
name that holds " b/", a quote, a tab or a backslash is checked under its own name.
"""
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
SCRIPT = os.path.join(ROOT, "scripts", "check_gate_integrity.py")
# Split so this file does not trip the suppression check itself.
MARKER = "// swiftlint:" "disable all\n"


def git(cwd, *args):
    return subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", *args],
                          cwd=cwd, check=True, capture_output=True, text=True).stdout


class GateIntegrityPathTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.dir)
        git(self.dir, "init", "-q", "-b", "develop")
        self.write("README.txt", "base\n")
        self.write("CLAUDE.md", "rules\n")
        self.commit("base")

    def write(self, rel, text):
        path = os.path.join(self.dir, rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as f:
            f.write(text)

    def commit(self, msg):
        git(self.dir, "add", "-A")
        git(self.dir, "commit", "-q", "-m", msg)

    def branch(self, name):
        git(self.dir, "checkout", "-q", "-b", name)

    def run_script(self, branch):
        return subprocess.run([sys.executable, SCRIPT, "develop", branch], cwd=self.dir,
                              capture_output=True, text=True)

    def test_guarded_path_holding_b_slash_is_caught_on_feature_branch(self):
        # Header: diff --git a/scripts/check_x b/y.py b/scripts/check_x b/y.py.
        # The old regex read the path as "y.py", which no guard glob matches.
        self.branch("feature/x")
        self.write("scripts/check_x b/y.py", "print('weakened')\n")
        self.commit("add")
        r = self.run_script("feature/x")
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertIn("Gate-definition file(s) modified on a feature/* branch: scripts/check_x b/y.py", r.stdout)

    def test_suppression_is_reported_under_the_exact_path(self):
        self.branch("fix/x")
        self.write("Sources/a b/b.swift", MARKER)
        self.commit("add")
        r = self.run_script("fix/x")
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertIn("Sources/a b/b.swift", r.stdout)

    def test_quoted_paths_are_matched(self):
        # git quotes a header path that holds a quote, tab or backslash.
        self.branch("fix/x")
        names = ['Sources/we"ird.swift', "Sources/tab\there.swift", "Sources/back\\slash.swift"]
        for name in names:
            self.write(name, MARKER)
        self.commit("add")
        r = self.run_script("fix/x")
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        for name in names:
            self.assertIn(f"{name}:", r.stdout)

    def test_guarded_file_renamed_off_its_path_is_caught(self):
        self.branch("feature/x")
        os.makedirs(os.path.join(self.dir, "notes b"))
        git(self.dir, "mv", "CLAUDE.md", "notes b/CLAUDE.txt")
        self.commit("move")
        r = self.run_script("feature/x")
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertIn("CLAUDE.md (renamed to notes b/CLAUDE.txt)", r.stdout)

    def test_typechange_passes(self):
        # A file turned into a symlink prints two diff blocks under one header.
        self.branch("fix/x")
        os.remove(os.path.join(self.dir, "README.txt"))
        os.symlink("CLAUDE.md", os.path.join(self.dir, "README.txt"))
        self.commit("link")
        r = self.run_script("fix/x")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)

    def test_clean_change_passes(self):
        self.branch("feature/x")
        self.write("Sources/App.swift", "let x = 1\n")
        self.commit("add")
        r = self.run_script("feature/x")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)


if __name__ == "__main__":
    unittest.main()
