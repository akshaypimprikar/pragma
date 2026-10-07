"""`project` settings in scripts/pipeline_lanes.json: persistence, ui, architecture.

check_tdd_commit_order.py reads the layer directories from project.architecture;
detect_project_settings.sh guesses persistence and ui; check_pr_lane validates
the block. Default (no key) must stay mvvm so existing installs behave the same.
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, os.path.join(ROOT, "scripts"))
import check_pr_lane  # noqa: E402

TDD = os.path.join(ROOT, "scripts", "check_tdd_commit_order.py")
DETECT = os.path.join(ROOT, "scripts", "detect_project_settings.sh")
LEGACY = os.path.join(ROOT, "fixtures", "FakeApp-Legacy")


def git(cwd, *args):
    subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", *args], cwd=cwd, check=True, capture_output=True)


def write(root, path, text="// x\n"):
    full = os.path.join(root, path)
    os.makedirs(os.path.dirname(full), exist_ok=True)
    with open(full, "w") as f:
        f.write(text)


def make_repo(files_by_commit, project=None):
    """A repo with develop plus a feature branch holding one commit per list entry."""
    d = tempfile.mkdtemp()
    git(d, "init", "-q", "-b", "develop")
    os.makedirs(os.path.join(d, "scripts"))
    shutil.copy(TDD, os.path.join(d, "scripts"))
    if project is not None:
        write(d, "scripts/pipeline_lanes.json", json.dumps({"project": project}))
    write(d, "README.md")
    git(d, "add", "-A")
    git(d, "commit", "-q", "-m", "base")
    git(d, "checkout", "-q", "-b", "feature/x")
    for i, files in enumerate(files_by_commit):
        for f in files:
            write(d, f)
        git(d, "add", "-A")
        git(d, "commit", "-q", "-m", f"c{i}")
    return d


def run_tdd(d):
    return subprocess.run([sys.executable, "scripts/check_tdd_commit_order.py", "develop"],
                          cwd=d, capture_output=True, text=True)


VIPER_TEST = "AppTests/Presenters/ListPresenterTests.swift"
VIPER_IMPL = "App/Presenters/ListPresenter.swift"


class LayerPresetTests(unittest.TestCase):
    def tearDown(self):
        for d in getattr(self, "dirs", []):
            shutil.rmtree(d, ignore_errors=True)

    def repo(self, *a, **k):
        d = make_repo(*a, **k)
        self.dirs = getattr(self, "dirs", []) + [d]
        return d

    def test_viper_test_after_impl_fails(self):
        d = self.repo([[VIPER_IMPL], [VIPER_TEST]], {"architecture": "viper"})
        r = run_tdd(d)
        self.assertEqual(r.returncode, 1, r.stdout)
        self.assertIn("tests-after", r.stdout)

    def test_viper_test_first_passes(self):
        d = self.repo([[VIPER_TEST], [VIPER_IMPL]], {"architecture": "viper"})
        r = run_tdd(d)
        self.assertEqual(r.returncode, 0, r.stdout)
        self.assertIn("1 file(s) checked", r.stdout)

    def test_mvc_scopes_controllers(self):
        d = self.repo([["App/Controllers/ListController.swift"],
                       ["AppTests/Controllers/ListControllerTests.swift"]], {"architecture": "mvc"})
        self.assertEqual(run_tdd(d).returncode, 1)

    def test_missing_key_defaults_to_mvvm(self):
        d = self.repo([["App/ViewModels/ListViewModel.swift"],
                       ["AppTests/ViewModels/ListViewModelTests.swift"]])
        self.assertEqual(run_tdd(d).returncode, 1)

    def test_viper_layers_unscoped_under_mvvm(self):
        d = self.repo([[VIPER_IMPL], [VIPER_TEST]], {"architecture": "mvvm"})
        r = run_tdd(d)
        self.assertEqual(r.returncode, 2, r.stdout)  # nothing matches: unconfigured, not a pass

    def test_custom_layer_dirs_override_preset(self):
        d = self.repo([["App/UseCases/Load.swift"], ["AppTests/UseCases/LoadTests.swift"]],
                      {"architecture": "mvvm", "scoped_layer_dirs": ["/UseCases/"]})
        self.assertEqual(run_tdd(d).returncode, 1)

    def test_string_layer_dirs_is_an_error(self):
        d = self.repo([[VIPER_TEST]], {"scoped_layer_dirs": "Services"})
        self.assertEqual(run_tdd(d).returncode, 2)

    def test_malformed_config_is_an_error(self):
        d = self.repo([[VIPER_TEST]], {"architecture": "viper"})
        write(d, "scripts/pipeline_lanes.json", "{not json")
        self.assertEqual(run_tdd(d).returncode, 2)

    def test_unknown_architecture_is_an_error(self):
        d = self.repo([[VIPER_TEST]], {"architecture": "clean"})
        self.assertEqual(run_tdd(d).returncode, 2)


class ValidateTests(unittest.TestCase):
    BASE = {"lanes": [{"name": "app", "paths": ["x/**"]}], "default": {"name": "docs"}}

    def check(self, project):
        return check_pr_lane.validate_config({**self.BASE, "project": project})

    def test_valid(self):
        self.check({"persistence": "coredata", "ui": "uikit", "architecture": "viper"})

    def test_bad_values_rejected(self):
        for bad in ({"persistence": "sqlite"}, {"ui": "react"}, {"architecture": "clean"},
                    {"scoped_layer_dirs": "Services"}):
            with self.assertRaises(check_pr_lane.ConfigError, msg=bad):
                self.check(bad)

    def test_scaffold_config_validates(self):
        with open(os.path.join(ROOT, "scaffold", "pipeline_lanes.json")) as f:
            text = f.read().replace("YOUR_PERSISTENCE", "swiftdata").replace("YOUR_UI", "swiftui")
        check_pr_lane.validate_config(json.loads(text))


class DetectTests(unittest.TestCase):
    def detect(self, d):
        return subprocess.run([DETECT, d], capture_output=True, text=True, check=True).stdout.strip()

    def test_legacy_fixture(self):
        self.assertEqual(self.detect(LEGACY), "none uikit")

    def test_swiftdata_swiftui(self):
        with tempfile.TemporaryDirectory() as d:
            write(d, "App/A.swift", "import SwiftUI\nimport SwiftData\n")
            self.assertEqual(self.detect(d), "swiftdata swiftui")

    def test_coredata_uikit_by_model_file(self):
        with tempfile.TemporaryDirectory() as d:
            write(d, "App/A.swift", "import UIKit\n")
            write(d, "App/Model.xcdatamodeld/contents", "")
            self.assertEqual(self.detect(d), "coredata uikit")

    def test_pods_folder_is_ignored(self):
        with tempfile.TemporaryDirectory() as d:
            write(d, "App/A.swift", "import UIKit\n")
            write(d, "Pods/X/B.swift", "import SwiftUI\nimport CoreData\n")
            write(d, "Pods/Y/M.xcdatamodeld/contents", "")
            self.assertEqual(self.detect(d), "none uikit")

    def test_realm_from_podfile(self):
        with tempfile.TemporaryDirectory() as d:
            write(d, "App/A.swift", "import UIKit\n")
            write(d, "Podfile", "pod 'RealmSwift'\n")
            self.assertEqual(self.detect(d), "realm uikit")


if __name__ == "__main__":
    unittest.main()
