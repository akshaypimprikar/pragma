"""sync_skills.sh links each skill into .agents/skills/ for Codex.

Codex lists skills from .agents/skills/ only, not .claude/skills/. The links
are per skill and relative, so .claude/skills/ stays the only copy, the
project's own .agents/skills/ entries survive, and nothing points back into
pragma's own skills (the setup.sh guard against writing into them).
"""
import os
import subprocess
import tempfile
import unittest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
SYNC = os.path.join(ROOT, "scripts", "sync_skills.sh")
SKILLS = os.path.join(ROOT, ".claude", "skills")
NAMES = sorted(n for n in os.listdir(SKILLS) if os.path.isfile(os.path.join(SKILLS, n, "SKILL.md")))


class AgentsSkillsLinkTests(unittest.TestCase):
    def setUp(self):
        self.d = os.path.realpath(tempfile.mkdtemp())
        self.addCleanup(lambda: subprocess.run(["rm", "-rf", self.d]))
        self.agents = os.path.join(self.d, ".agents", "skills")

    def sync(self):
        return subprocess.run([SYNC, SKILLS, self.d, "App"], capture_output=True, text=True, check=True).stdout

    def test_every_skill_is_a_relative_link_into_the_project(self):
        self.sync()
        self.assertEqual(sorted(os.listdir(self.agents)), NAMES)
        for name in NAMES:
            link = os.path.join(self.agents, name)
            self.assertTrue(os.path.islink(link), name)
            self.assertEqual(os.readlink(link), f"../../.claude/skills/{name}")
            self.assertEqual(os.path.realpath(link), os.path.join(self.d, ".claude", "skills", name))
            self.assertTrue(os.path.isfile(os.path.join(link, "SKILL.md")), name)

    def test_claude_skills_stays_a_real_dir_outside_pragma(self):
        self.sync()
        claude_skills = os.path.join(self.d, ".claude", "skills")
        self.assertFalse(os.path.islink(claude_skills))
        self.assertFalse(os.path.samefile(claude_skills, SKILLS))

    def test_rerun_is_quiet_and_unchanged(self):
        self.sync()
        out = self.sync()
        self.assertNotIn(".agents/skills", out)
        self.assertEqual(sorted(os.listdir(self.agents)), NAMES)

    def test_projects_own_entries_survive(self):
        os.makedirs(os.path.join(self.agents, "my-skill"))
        own = os.path.join(self.agents, NAMES[0])
        os.makedirs(own)
        open(os.path.join(own, "SKILL.md"), "w").write("mine\n")
        out = self.sync()
        self.assertTrue(os.path.isdir(os.path.join(self.agents, "my-skill")))
        self.assertFalse(os.path.islink(own))
        self.assertEqual(open(os.path.join(own, "SKILL.md")).read(), "mine\n")
        self.assertIn(f"left as is: {NAMES[0]}", out)
        for name in NAMES[1:]:
            self.assertTrue(os.path.islink(os.path.join(self.agents, name)), name)

    def test_link_elsewhere_is_not_replaced(self):
        os.makedirs(self.agents)
        link = os.path.join(self.agents, NAMES[0])
        os.symlink("/nonexistent", link)
        out = self.sync()
        self.assertEqual(os.readlink(link), "/nonexistent")
        self.assertIn(f"left as is: {NAMES[0]}", out)

    def test_whole_dir_link_to_claude_skills_is_left_alone(self):
        os.makedirs(os.path.join(self.d, ".agents"))
        os.symlink("../.claude/skills", self.agents)
        out = self.sync()
        self.assertEqual(os.readlink(self.agents), "../.claude/skills")
        self.assertNotIn(".agents/skills", out)
        for name in NAMES:
            self.assertFalse(os.path.islink(os.path.join(self.d, ".claude", "skills", name)), name)


if __name__ == "__main__":
    unittest.main()
