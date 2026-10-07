"""Gate 0's build-relevance pathspec must keep matching project-file changes.

Gate 0 in .claude/skills/gates/SKILL.md holds the only copy of the pathspec;
later gates read its result file. The
pathspec is run against a scratch git repo, so dropping a pattern, or a second
copy appearing, fails here instead of silently skipping build and test.
"""
import os
import re
import shlex
import subprocess
import tempfile
import unittest

ROOT = os.path.join(os.path.dirname(__file__), "..", "..")
SKILL = os.path.join(ROOT, ".claude", "skills", "gates", "SKILL.md")
WORKFLOWS = os.path.join(ROOT, "scaffold", ".github", "workflows")
# Any `git diff` line naming a project-bundle pattern, however its flags are written.
PATHSPEC = re.compile(r"^.*\bgit diff\b.*?\s--\s+(.*?\*\.xcodeproj.*?)\s*(?:\||>|$)", re.M)

BUILD_RELEVANT = [
    "App.xcodeproj/project.pbxproj",
    "App.xcodeproj/project.xcproj",
    "App.xcodeproj/xcshareddata/xcschemes/App.xcscheme",
    "App.xcodeproj/project.xcworkspace/xcshareddata/WorkspaceSettings.xcsettings",
    "App.xcworkspace/contents.xcworkspacedata",
    "App/Thing.swift",
    "App/Info.plist",
    "Config/Debug.xcconfig",
    "App.xctestplan",
    "App/App.entitlements",
    "Package.swift",
    "Package.resolved",
]
NOT_BUILD_RELEVANT = ["docs/notes.md", "README.md"]


def pathspecs():
    with open(SKILL) as f:
        return [m.group(1).strip() for m in PATHSPEC.finditer(f.read())]


def git(cwd, *args):
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True).stdout


def changed(spec, path):
    """Files Gate 0 lists when a branch off develop changes only `path`."""
    with tempfile.TemporaryDirectory() as d:
        git(d, "init", "-q", "-b", "develop")
        git(d, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "--allow-empty", "-m", "base")
        git(d, "checkout", "-q", "-b", "fix/x")
        full = os.path.join(d, path)
        os.makedirs(os.path.dirname(full) or d, exist_ok=True)
        with open(full, "w") as f:
            f.write("x\n")
        git(d, "add", "-A")
        git(d, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "change")
        return git(d, "diff", "develop...HEAD", "--name-only", "--", *shlex.split(spec)).split()


class Gate0PathspecTests(unittest.TestCase):
    def test_exactly_one_copy(self):
        specs = pathspecs()
        self.assertEqual(len(specs), 1, specs)

    def test_project_and_build_inputs_listed(self):
        spec = pathspecs()[0]
        for path in BUILD_RELEVANT:
            with self.subTest(path=path):
                self.assertEqual(changed(spec, path), [path])

    def test_docs_only_change_lists_nothing(self):
        spec = pathspecs()[0]
        for path in NOT_BUILD_RELEVANT:
            with self.subTest(path=path):
                self.assertEqual(changed(spec, path), [])


def path_filters(text):
    """Each `paths:` list in a workflow, as a list of its entries."""
    blocks, current, indent = [], None, None
    for line in text.splitlines():
        stripped = line.strip()
        if stripped == "paths:":
            current, indent = [], len(line) - len(line.lstrip())
            blocks.append(current)
        elif current is not None:
            if stripped.startswith("- ") and len(line) - len(line.lstrip()) > indent:
                current.append(stripped[2:].strip("'\""))
            elif stripped and not stripped.startswith("#"):
                current = None
    return blocks


class WorkflowPathFilterTests(unittest.TestCase):
    def test_every_app_paths_filter_includes_project_bundle(self):
        for name in ("pr-checks.yml", "ui-tests.yml", "concurrency-advisory.yml"):
            with open(os.path.join(WORKFLOWS, name)) as f:
                blocks = path_filters(f.read())
            self.assertTrue(blocks, name)
            for i, entries in enumerate(blocks):
                with self.subTest(workflow=name, block=i):
                    self.assertIn("YOUR_PROJECT/**", entries)
                    self.assertIn("YOUR_PROJECT.xcodeproj/**", entries)


if __name__ == "__main__":
    unittest.main()
