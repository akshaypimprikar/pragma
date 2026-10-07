"""The build target (-workspace vs -project) is chosen in one place.

detect_build_target.sh picks the flag; sync_skills.sh and setup.sh substitute it
into the skills, workflows and AGENTS.md. A hard-coded `-project` creeping back
in would break CocoaPods projects, so it fails here.
"""
import glob
import os
import re
import subprocess
import tempfile
import unittest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
DETECT = os.path.join(ROOT, "scripts", "detect_build_target.sh")
SYNC = os.path.join(ROOT, "scripts", "sync_skills.sh")
SKILLS = os.path.join(ROOT, ".claude", "skills")
WORKFLOWS = os.path.join(ROOT, "scaffold", ".github", "workflows")
HARD_CODED = re.compile(r"-project\s+(<AppName>|YOUR_PROJECT|\$\{APP_NAME\})")


def detect(project_dir):
    return subprocess.run([DETECT, project_dir, "App"], capture_output=True, text=True, check=True).stdout.strip()


class DetectTests(unittest.TestCase):
    def test_plain_project(self):
        with tempfile.TemporaryDirectory() as d:
            os.mkdir(os.path.join(d, "App.xcodeproj"))
            self.assertEqual(detect(d), "-project App.xcodeproj")

    def test_podfile_selects_workspace(self):
        with tempfile.TemporaryDirectory() as d:
            open(os.path.join(d, "Podfile"), "w").close()
            self.assertEqual(detect(d), "-workspace App.xcworkspace")

    def test_root_workspace_selects_workspace(self):
        with tempfile.TemporaryDirectory() as d:
            os.mkdir(os.path.join(d, "App.xcworkspace"))
            self.assertEqual(detect(d), "-workspace App.xcworkspace")

    def test_nested_xcodeproj_workspace_is_ignored(self):
        with tempfile.TemporaryDirectory() as d:
            os.makedirs(os.path.join(d, "App.xcodeproj", "project.xcworkspace"))
            self.assertEqual(detect(d), "-project App.xcodeproj")


class SubstitutionTests(unittest.TestCase):
    def sync(self, with_podfile):
        d = tempfile.mkdtemp()
        self.addCleanup(lambda: subprocess.run(["rm", "-rf", d]))
        if with_podfile:
            open(os.path.join(d, "Podfile"), "w").close()
        subprocess.run([SYNC, SKILLS, d, "App"], capture_output=True, check=True)
        return "\n".join(
            open(p).read() for p in glob.glob(os.path.join(d, ".claude", "skills", "*", "SKILL.md"))
        )

    def test_skills_get_project_flag(self):
        text = self.sync(False)
        self.assertIn("-project App.xcodeproj -scheme", text)
        self.assertNotIn("<BuildTarget>", text)

    def test_skills_get_workspace_flag_with_podfile(self):
        text = self.sync(True)
        self.assertIn("-workspace App.xcworkspace -scheme", text)
        self.assertNotIn("-project App.xcodeproj -scheme", text)
        self.assertNotIn("<BuildTarget>", text)


class NoHardCodedProjectTests(unittest.TestCase):
    def files(self):
        yield from glob.glob(os.path.join(SKILLS, "*", "SKILL.md"))
        yield from glob.glob(os.path.join(WORKFLOWS, "*.yml"))
        yield os.path.join(ROOT, "scripts", "setup.sh")

    def test_no_hard_coded_project_flag(self):
        for path in self.files():
            with open(path) as f:
                for n, line in enumerate(f, 1):
                    self.assertIsNone(HARD_CODED.search(line), f"{path}:{n}: {line.strip()}")

    def test_pod_install_step_gated_on_podfile(self):
        for path in glob.glob(os.path.join(WORKFLOWS, "*.yml")):
            text = open(path).read()
            if re.search(r"^\s+xcodebuild ", text, re.M):
                self.assertIn("if: hashFiles('Podfile') != ''", text, path)


if __name__ == "__main__":
    unittest.main()
