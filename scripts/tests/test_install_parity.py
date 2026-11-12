"""The two installers copy the same scripts.

`scripts/setup.sh` copies scripts with `cp "$REPO_ROOT/scripts/<name>" ...`, and the
plugin's `/pragma:init` (`.claude/commands/init.md`) lists the same files in prose.
A script that only one of them copies leaves plugin installs without it (#133:
`/gates` runs `scripts/check_gates.sh`, which init.md did not copy).
"""
import os
import re
import unittest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
SETUP = os.path.join(ROOT, "scripts", "setup.sh")
INIT = os.path.join(ROOT, ".claude", "commands", "init.md")


def setup_copies():
    """Paths under scripts/ that setup.sh copies with cp (continuation lines included)."""
    text = open(SETUP, encoding="utf-8").read().replace("\\\n", " ")
    paths = []
    for line in text.splitlines():
        if line.lstrip().startswith("cp "):
            paths += re.findall(r'"\$REPO_ROOT/scripts/([^"]+)"', line)
    return paths


class InstallParityTests(unittest.TestCase):
    def test_setup_copies_scripts(self):
        # Guards the parser: if setup.sh's cp form changes, this test must not pass on an empty list.
        self.assertIn("check_gates.sh", setup_copies())

    def test_plugin_init_names_every_script_setup_copies(self):
        init = open(INIT, encoding="utf-8").read()
        # init.md names a file as `name` or as a path ending in it (`${CLAUDE_PLUGIN_ROOT}/scripts/name`).
        missing = [p for p in setup_copies()
                   if not re.search(r"[`/]" + re.escape(os.path.basename(p)) + "`", init)]
        self.assertEqual(missing, [], "init.md does not copy these scripts that setup.sh copies")


if __name__ == "__main__":
    unittest.main()
