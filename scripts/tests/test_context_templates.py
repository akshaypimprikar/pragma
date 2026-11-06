"""A fresh install gets header-only context files, not pragma's own logs.

pragma logs its own releases, spec decisions and review rejections in
.claude/context/. The installers used to copy those files as they are, so a new
project's /review treated pragma's rejections as its own earlier PRs (a repeat
is HIGH), /plan read pragma's decisions and releases as the app's, and /release
checked the app's version against pragma's feature-log. The installers now copy
scaffold/.claude/context/, which holds each file's header and format comment only.
"""
import glob
import os
import subprocess
import sys
import tempfile
import unittest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
SETUP = os.path.join(ROOT, "scripts", "setup.sh")
LIVE = os.path.join(ROOT, ".claude", "context")
TEMPLATES = os.path.join(ROOT, "scaffold", ".claude", "context")
INIT = os.path.join(ROOT, ".claude", "commands", "init.md")


def read(path):
    with open(path, encoding="utf8") as f:
        return f.read()


def header(text):
    """Everything before the first `## ` entry that sits outside an HTML comment."""
    out, in_comment = [], False
    for line in text.splitlines(keepends=True):
        if not in_comment and line.startswith("## "):
            break
        if "<!--" in line and "-->" not in line.split("<!--", 1)[1]:
            in_comment = True
        elif in_comment and "-->" in line:
            in_comment = False
        out.append(line)
    return "".join(out).rstrip("\n") + "\n"


def names(directory):
    return sorted(os.path.basename(p) for p in glob.glob(os.path.join(directory, "*.md")))


class ContextTemplateTests(unittest.TestCase):
    def setUp(self):
        self.d = os.path.realpath(tempfile.mkdtemp())
        self.addCleanup(lambda: subprocess.run(["rm", "-rf", self.d]))

    def install(self):
        subprocess.run(["bash", SETUP, "--no-guard-hook", "--no-review-alert", "Demo", self.d],
                       capture_output=True, text=True, check=True, timeout=120)

    def test_templates_match_live_headers(self):
        self.assertEqual(names(TEMPLATES), names(LIVE))
        for name in names(LIVE):
            with self.subTest(name=name):
                self.assertEqual(read(os.path.join(TEMPLATES, name)), header(read(os.path.join(LIVE, name))))

    def test_fresh_install_gets_no_pragma_entries(self):
        self.install()
        context = os.path.join(self.d, ".claude", "context")
        self.assertEqual(names(context), names(LIVE))
        for name in names(LIVE):
            with self.subTest(name=name):
                text = read(os.path.join(context, name))
                self.assertEqual(text, header(text), f"{name} has log entries")
                self.assertEqual(text, header(read(os.path.join(LIVE, name))))

    def test_existing_context_file_is_kept(self):
        context = os.path.join(self.d, ".claude", "context")
        os.makedirs(context)
        mine = "# Review Rejection Log\n\n## 2026-01-01 — PR#1 — mine\n"
        with open(os.path.join(context, "rejections.md"), "w", encoding="utf8") as f:
            f.write(mine)
        self.install()
        self.assertEqual(read(os.path.join(context, "rejections.md")), mine)

    def test_plugin_init_copies_the_templates(self):
        text = read(INIT)
        self.assertTrue("${CLAUDE_PLUGIN_ROOT}/scaffold/.claude/context/*.md" in text, "init.md copies the templates")
        self.assertFalse("${CLAUDE_PLUGIN_ROOT}/.claude/context/*.md" in text, "init.md copies pragma's own logs")


if __name__ == "__main__":
    sys.exit(unittest.main())
