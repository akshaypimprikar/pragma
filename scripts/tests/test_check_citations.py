import os
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import check_citations as cit  # noqa: E402

SCRIPT = os.path.join(os.path.dirname(__file__), "..", "check_citations.py")


class ExtractTests(unittest.TestCase):
    def test_path_with_slash(self):
        self.assertEqual(cit.extract("see `scripts/a.py:12`"), [("scripts/a.py", 12, 12)])

    def test_range(self):
        self.assertEqual(cit.extract("`docs/x.md:3-9`"), [("docs/x.md", 3, 9)])

    def test_root_file_with_known_extension(self):
        self.assertEqual(cit.extract("`AGENTS.md:4`"), [("AGENTS.md", 4, 4)])

    def test_host_port_ignored(self):
        self.assertEqual(cit.extract("`localhost:8080` and `example.com:443`"), [])

    def test_url_ignored(self):
        self.assertEqual(cit.extract("`https://github.com/o/r/blob/x/a.py:12`"), [])

    def test_unbackticked_ignored(self):
        self.assertEqual(cit.extract("scripts/a.py:12"), [])


class RepoTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.parent = self.tmp.name
        self.repo = os.path.join(self.parent, "proj")
        os.makedirs(os.path.join(self.repo, "docs", "superpowers", "specs"))
        os.makedirs(os.path.join(self.repo, "scripts"))
        self.git("init", "-q", "-b", "develop")
        self.git("config", "user.email", "t@example.com")
        self.git("config", "user.name", "t")
        self.write("scripts/a.py", "".join(f"line {i}\n" for i in range(1, 11)))
        self.write("docs/superpowers/specs/s.md", "old `scripts/gone.py:1` citation\n")
        self.git("add", ".")
        self.git("commit", "-qm", "base")
        self.git("checkout", "-qb", "chore/x")

    def tearDown(self):
        self.tmp.cleanup()

    def git(self, *a):
        subprocess.run(["git", *a], cwd=self.repo, check=True, capture_output=True)

    def write(self, rel, text):
        with open(os.path.join(self.repo, rel), "w") as f:
            f.write(text)

    def run_check(self):
        self.git("add", ".")
        self.git("commit", "-qm", "change", "--allow-empty")
        return subprocess.run([sys.executable, SCRIPT, "--base", "develop"], cwd=self.repo,
                              capture_output=True, text=True)

    def test_valid_citation_passes(self):
        self.write("docs/superpowers/specs/s.md", "old `scripts/gone.py:1` citation\nnew `scripts/a.py:3-10`\n")
        out = self.run_check()
        self.assertEqual(out.returncode, 0, out.stdout + out.stderr)

    def test_only_added_lines_are_checked(self):
        # The legacy broken citation on an unchanged line must not fail the PR.
        self.write("docs/superpowers/specs/s.md", "old `scripts/gone.py:1` citation\nplain text\n")
        self.assertEqual(self.run_check().returncode, 0)

    def test_missing_file_fails(self):
        self.write("docs/superpowers/specs/s.md", "old `scripts/gone.py:1` citation\nnew `scripts/nope.py:1`\n")
        out = self.run_check()
        self.assertEqual(out.returncode, 1)
        self.assertIn("scripts/nope.py", out.stdout)

    def test_out_of_range_fails(self):
        self.write("docs/superpowers/specs/s.md", "old `scripts/gone.py:1` citation\nnew `scripts/a.py:9-11`\n")
        self.assertEqual(self.run_check().returncode, 1)

    def test_other_repo_present_is_checked(self):
        os.makedirs(os.path.join(self.parent, "pragma", "scripts"))
        with open(os.path.join(self.parent, "pragma", "scripts", "s.sh"), "w") as f:
            f.write("a\nb\n")
        self.write("docs/superpowers/specs/s.md", "old `scripts/gone.py:1` citation\n`pragma/scripts/s.sh:2` and `pragma/scripts/s.sh:5`\n")
        out = self.run_check()
        self.assertEqual(out.returncode, 1)
        self.assertIn("pragma/scripts/s.sh:5", out.stdout)

    def test_other_repo_absent_is_skipped(self):
        self.write("docs/superpowers/specs/s.md", "old `scripts/gone.py:1` citation\n`pragma/scripts/s.sh:2`\n")
        out = self.run_check()
        self.assertEqual(out.returncode, 0, out.stdout)
        self.assertIn("skipped", out.stdout)

    def test_files_outside_scope_are_ignored(self):
        self.write("README.md", "`scripts/nope.py:1`\n")
        self.assertEqual(self.run_check().returncode, 0)

    def test_renamed_and_edited_file_is_checked(self):
        self.git("mv", "docs/superpowers/specs/s.md", "docs/superpowers/specs/t.md")
        self.write("docs/superpowers/specs/t.md", "old `scripts/gone.py:1` citation\nnew `scripts/nope.py:1`\n")
        out = self.run_check()
        self.assertEqual(out.returncode, 1)
        self.assertIn("scripts/nope.py", out.stdout)

    def test_skill_files_are_in_scope(self):
        os.makedirs(os.path.join(self.repo, ".claude", "skills", "x"))
        self.write(".claude/skills/x/SKILL.md", "`scripts/nope.py:1`\n")
        self.assertEqual(self.run_check().returncode, 1)


if __name__ == "__main__":
    unittest.main()
