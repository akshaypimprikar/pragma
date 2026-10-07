"""File registration for classic-group Xcode projects (plan C, C2).

detect_file_registration.sh picks the mode, register_files.rb adds new .swift files
to a target's Sources phase with the xcodeproj gem, and check_file_registration.py
fails when a new .swift file is not registered. Fixtures are built with the gem, so
the gem-backed tests skip where it is not installed (macOS system Ruby + CocoaPods
projects have it; a bare machine may not).
"""
import os
import shutil
import subprocess
import tempfile
import unittest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
DETECT = os.path.join(ROOT, "scripts", "detect_file_registration.sh")
REGISTER = os.path.join(ROOT, "scripts", "register_files.rb")
CHECK = os.path.join(ROOT, "scripts", "check_file_registration.py")

HAS_GEM = subprocess.run(["ruby", "-e", "require 'xcodeproj'"], capture_output=True).returncode == 0

MAKE_PROJECT = """
require 'xcodeproj'
dir, mode = ARGV
Dir.chdir(dir)
project = Xcodeproj::Project.new('App.xcodeproj')
target = project.new_target(:application, 'App', :ios, '17.0')
Dir.mkdir('App')
File.write('App/A.swift', "struct A {}\\n")
if mode == 'synchronized'
  group = project.new(Xcodeproj::Project::Object::PBXFileSystemSynchronizedRootGroup)
  group.path = 'App'
  group.source_tree = '<group>'
  project.main_group << group
else
  group = project.main_group.new_group('App', 'App')
  target.add_file_references([group.new_file('A.swift')])
end
project.save
"""


def make_project(d, mode="classic"):
    subprocess.run(["ruby", "-e", MAKE_PROJECT, d, mode], check=True)


def xcproj_env():
    """Environment whose xcodebuild can write the .xcproj format (Xcode 27.2+), or None."""
    candidates = [os.environ.get("DEVELOPER_DIR"), "/Applications/Xcode-beta.app/Contents/Developer"]
    for dev in filter(None, candidates):
        env = dict(os.environ, DEVELOPER_DIR=dev)
        probe = tempfile.mkdtemp()
        try:
            subprocess.run(["ruby", "-e", MAKE_PROJECT, probe, "classic"], check=True)
            r = subprocess.run(["xcodebuild", "-convert-project", "xcproj", "-project",
                                os.path.join(probe, "App.xcodeproj")], capture_output=True, env=env)
            if r.returncode == 0:
                return env
        finally:
            shutil.rmtree(probe, True)
    return None


XCPROJ_ENV = xcproj_env() if HAS_GEM else None


def make_xcproj(d, mode="classic"):
    make_project(d, mode)
    subprocess.run(["xcodebuild", "-convert-project", "xcproj", "-project", os.path.join(d, "App.xcodeproj")],
                   check=True, capture_output=True, env=XCPROJ_ENV)


def detect(d):
    r = subprocess.run([DETECT, d, "App"], capture_output=True, text=True)
    return r.returncode, r.stdout.strip(), r.stderr


def add_swift(d, name="B.swift"):
    path = os.path.join(d, "App", name)
    with open(path, "w") as f:
        f.write("struct B {}\n")
    return path


class TempDirCase(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.d, True)


@unittest.skipUnless(HAS_GEM, "xcodeproj gem not installed")
class DetectTests(TempDirCase):
    def test_classic_groups(self):
        make_project(self.d, "classic")
        self.assertEqual(detect(self.d)[1], "classic")

    def test_synchronized_groups(self):
        make_project(self.d, "synchronized")
        self.assertEqual(detect(self.d)[1], "synchronized")

    def test_project_yml_stops(self):
        make_project(self.d)
        open(os.path.join(self.d, "project.yml"), "w").close()
        code, out, err = detect(self.d)
        self.assertEqual(out, "stop")
        self.assertIn("project.yml", err)

    def test_project_swift_stops(self):
        make_project(self.d)
        open(os.path.join(self.d, "Project.swift"), "w").close()
        self.assertEqual(detect(self.d)[1], "stop")

    def test_xcproj_without_folders_is_xcproj(self):
        make_project(self.d)
        open(os.path.join(self.d, "App.xcodeproj", "project.xcproj"), "w").close()
        self.assertEqual(detect(self.d)[1], "xcproj")

    @unittest.skipUnless(XCPROJ_ENV, "needs Xcode 27.2+ to write .xcproj")
    def test_converted_classic_project_is_xcproj(self):
        make_xcproj(self.d, "classic")
        self.assertEqual(detect(self.d)[1], "xcproj")

    @unittest.skipUnless(XCPROJ_ENV, "needs Xcode 27.2+ to write .xcproj")
    def test_converted_synchronized_project_is_synchronized(self):
        make_xcproj(self.d, "synchronized")
        self.assertEqual(detect(self.d)[1], "synchronized")


class DetectWithoutGemTests(TempDirCase):
    def test_missing_project_stops(self):
        code, out, err = detect(self.d)
        self.assertEqual(out, "stop")
        self.assertIn("App.xcodeproj", err)


@unittest.skipUnless(HAS_GEM, "xcodeproj gem not installed")
class RegisterTests(TempDirCase):
    def register(self, *files):
        return subprocess.run(["ruby", REGISTER, self.d, "App", *files], capture_output=True, text=True)

    def check(self, *files):
        return subprocess.run(["ruby", REGISTER, "--check", self.d, "App", *files], capture_output=True, text=True)

    def test_register_adds_file_to_sources(self):
        make_project(self.d)
        b = add_swift(self.d)
        self.assertNotEqual(self.check(b).returncode, 0)
        self.assertEqual(self.register(b).returncode, 0)
        self.assertEqual(self.check(b).returncode, 0)

    def test_register_is_idempotent(self):
        make_project(self.d)
        b = add_swift(self.d)
        self.register(b)
        self.assertEqual(self.register(b).returncode, 0)
        with open(os.path.join(self.d, "App.xcodeproj", "project.pbxproj")) as f:
            pbx = f.read()
        build_files = [l for l in pbx.splitlines() if "B.swift in Sources" in l and "isa = PBXBuildFile" in l]
        self.assertEqual(len(build_files), 1)

    def test_register_creates_missing_groups(self):
        make_project(self.d)
        os.makedirs(os.path.join(self.d, "App", "Feature"))
        c = add_swift(self.d, os.path.join("Feature", "C.swift"))
        self.assertEqual(self.register(c).returncode, 0)
        self.assertEqual(self.check(c).returncode, 0)

    def test_register_refuses_synchronized_project(self):
        make_project(self.d, "synchronized")
        b = add_swift(self.d)
        r = self.register(b)
        self.assertEqual(r.returncode, 3)
        self.assertIn("synchronized", r.stderr)

    def test_register_refuses_project_yml(self):
        make_project(self.d)
        open(os.path.join(self.d, "project.yml"), "w").close()
        r = self.register(add_swift(self.d))
        self.assertEqual(r.returncode, 3)
        self.assertIn("project.yml", r.stderr)


@unittest.skipUnless(XCPROJ_ENV, "needs Xcode 27.2+ to write .xcproj")
class XcprojRegisterTests(TempDirCase):
    def run_register(self, *args):
        return subprocess.run(["ruby", REGISTER, *args], capture_output=True, text=True, env=XCPROJ_ENV)

    def setUp(self):
        super().setUp()
        make_xcproj(self.d)

    def test_register_adds_file_to_compile_sources(self):
        b = add_swift(self.d)
        self.assertEqual(self.run_register("--check", self.d, "App", b).returncode, 1)
        self.assertEqual(self.run_register(self.d, "App", b).returncode, 0)
        self.assertEqual(self.run_register("--check", self.d, "App", b).returncode, 0)
        with open(os.path.join(self.d, "App.xcodeproj", "project.xcproj")) as f:
            text = f.read()
        self.assertEqual(text.count('"B.swift"'), 1)
        self.assertIn('"B.swift", "index": true, "target-membership": [ "App/compile-sources" ]', text)

    def test_register_is_idempotent(self):
        b = add_swift(self.d)
        self.run_register(self.d, "App", b)
        self.assertEqual(self.run_register(self.d, "App", b).returncode, 0)
        with open(os.path.join(self.d, "App.xcodeproj", "project.xcproj")) as f:
            self.assertEqual(f.read().count('"B.swift"'), 1)

    def test_register_creates_missing_group(self):
        os.makedirs(os.path.join(self.d, "App", "Feature"))
        c = add_swift(self.d, os.path.join("Feature", "C.swift"))
        self.assertEqual(self.run_register(self.d, "App", c).returncode, 0)
        self.assertEqual(self.run_register("--check", self.d, "App", c).returncode, 0)

    def test_register_keeps_project_listable(self):
        os.makedirs(os.path.join(self.d, "App", "Feature"))
        self.run_register(self.d, "App", add_swift(self.d), add_swift(self.d, os.path.join("Feature", "C.swift")))
        r = subprocess.run(["xcodebuild", "-list", "-project", os.path.join(self.d, "App.xcodeproj")],
                           capture_output=True, text=True, env=XCPROJ_ENV)
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_gate_fails_then_passes(self):
        b = add_swift(self.d)
        gate = lambda: subprocess.run(["python3", CHECK, "--project-dir", self.d, "--app-name", "App", b],
                                      capture_output=True, text=True, env=XCPROJ_ENV)
        self.assertEqual(gate().returncode, 1)
        self.run_register(self.d, "App", b)
        self.assertEqual(gate().returncode, 0, gate().stdout)

    def test_unparseable_xcproj_fails_gate(self):
        with open(os.path.join(self.d, "App.xcodeproj", "project.xcproj"), "a") as f:
            f.write("}}}}")
        r = subprocess.run(["python3", CHECK, "--project-dir", self.d, "--app-name", "App", add_swift(self.d)],
                           capture_output=True, text=True, env=XCPROJ_ENV)
        self.assertEqual(r.returncode, 1)


@unittest.skipUnless(HAS_GEM, "xcodeproj gem not installed")
class GateTests(TempDirCase):
    def gate(self, *files):
        return subprocess.run(
            ["python3", CHECK, "--project-dir", self.d, "--app-name", "App", *files],
            capture_output=True, text=True,
        )

    def test_unregistered_file_fails(self):
        make_project(self.d)
        r = self.gate(add_swift(self.d))
        self.assertEqual(r.returncode, 1)
        self.assertIn("B.swift", r.stdout)

    def test_registered_file_passes(self):
        make_project(self.d)
        b = add_swift(self.d)
        subprocess.run(["ruby", REGISTER, self.d, "App", b], check=True)
        self.assertEqual(self.gate(b).returncode, 0)

    def test_synchronized_project_skips(self):
        make_project(self.d, "synchronized")
        r = self.gate(add_swift(self.d))
        self.assertEqual(r.returncode, 0)
        self.assertIn("synchronized", r.stdout)

    def test_stop_mode_fails(self):
        make_project(self.d)
        open(os.path.join(self.d, "project.yml"), "w").close()
        self.assertEqual(self.gate(add_swift(self.d)).returncode, 2)

    def test_corrupt_pbxproj_fails_lint(self):
        make_project(self.d)
        with open(os.path.join(self.d, "App.xcodeproj", "project.pbxproj"), "a") as f:
            f.write("}}}}")
        self.assertEqual(self.gate(add_swift(self.d)).returncode, 1)


if __name__ == "__main__":
    unittest.main()
