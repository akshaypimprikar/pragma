"""Canary for pragma legacy mode (plan C, C5): the FakeApp-Legacy fixture (UIKit,
CocoaPods, classic groups) must keep going through the C1 and C2 scripts end to end.
The build test needs xcodebuild, so it skips off macOS; the workflow
.github/workflows/legacy-canary.yml runs this file on a macOS runner.
"""
import os
import shutil
import subprocess
import tempfile
import unittest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
FIXTURE = os.path.join(ROOT, "fixtures", "FakeApp-Legacy")
APP = "FakeApp-Legacy"
BUILD_TARGET = os.path.join(ROOT, "scripts", "detect_build_target.sh")
DETECT = os.path.join(ROOT, "scripts", "detect_file_registration.sh")
REGISTER = os.path.join(ROOT, "scripts", "register_files.rb")
CHECK = os.path.join(ROOT, "scripts", "check_file_registration.py")

HAS_GEM = subprocess.run(["ruby", "-e", "require 'xcodeproj'"], capture_output=True).returncode == 0
HAS_APPLE_TOOLS = all(shutil.which(t) for t in ("plutil", "xcodebuild"))


class LegacyFixtureTests(unittest.TestCase):
    def setUp(self):
        self.d = os.path.join(tempfile.mkdtemp(), APP)
        self.addCleanup(shutil.rmtree, os.path.dirname(self.d), True)
        shutil.copytree(FIXTURE, self.d)

    def run_script(self, *cmd):
        return subprocess.run(cmd, capture_output=True, text=True)

    def new_file(self):
        path = os.path.join(self.d, APP, "Greeter.swift")
        with open(path, "w") as f:
            f.write("struct Greeter {}\n")
        return path

    def test_build_target_is_workspace(self):
        r = self.run_script("bash", BUILD_TARGET, self.d, APP)
        self.assertEqual(r.stdout.strip(), f"-workspace {APP}.xcworkspace")

    def test_registration_mode_is_classic(self):
        r = self.run_script("bash", DETECT, self.d, APP)
        self.assertEqual(r.stdout.strip(), "classic")

    @unittest.skipUnless(HAS_GEM and HAS_APPLE_TOOLS, "needs the xcodeproj gem, plutil and xcodebuild")
    def test_gate_fails_before_registration_and_passes_after(self):
        f = self.new_file()
        gate = ["python3", CHECK, "--project-dir", self.d, "--app-name", APP, f]
        before = self.run_script(*gate)
        self.assertEqual(before.returncode, 1, before.stdout + before.stderr)
        self.assertIn("Greeter.swift", before.stdout)
        reg = self.run_script("ruby", REGISTER, self.d, APP, f)
        self.assertEqual(reg.returncode, 0, reg.stderr)
        after = self.run_script(*gate)
        self.assertEqual(after.returncode, 0, after.stdout + after.stderr)

    @unittest.skipUnless(HAS_GEM and HAS_APPLE_TOOLS, "needs the xcodeproj gem, plutil and xcodebuild")
    def test_workspace_builds_with_a_registered_file(self):
        f = self.new_file()
        subprocess.run(["ruby", REGISTER, self.d, APP, f], check=True)
        r = subprocess.run(
            ["xcodebuild", "build", "-workspace", f"{APP}.xcworkspace", "-scheme", APP,
             "-destination", "generic/platform=iOS Simulator", "CODE_SIGNING_ALLOWED=NO",
             "-derivedDataPath", os.path.join(self.d, "dd")],
            cwd=self.d, capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stdout[-2000:])


if __name__ == "__main__":
    unittest.main()
