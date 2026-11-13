"""check_gate_integrity.py's GATE_DEFINITION_FILES stays one list, inside the guarded set.

Check 5 (a lowered coverage threshold) reads only GATE_DEFINITION_FILES. pragma
and FinanceTracker once kept different lists, so a lowered `%` in a consumer's
AGENTS.md, CLAUDE.md or invariants.md went unflagged. Every entry must also be
in GUARDED_PATH_GLOBS, so check 1 and the guard hook cover it too.
"""
import ast
import fnmatch
import os
import unittest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
SCRIPT = os.path.join(ROOT, "scripts", "check_gate_integrity.py")


def tuple_constant(name):
    # The script runs its checks at import time, so read the constants from its source.
    with open(SCRIPT, encoding="utf8") as f:
        tree = ast.parse(f.read())
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(getattr(t, "id", None) == name for t in node.targets):
            return ast.literal_eval(node.value)
    raise AssertionError(f"{name} not found in check_gate_integrity.py")


class GateDefinitionFilesTests(unittest.TestCase):
    def setUp(self):
        self.defs = tuple_constant("GATE_DEFINITION_FILES")
        self.guarded = tuple_constant("GUARDED_PATH_GLOBS")

    def test_covers_every_file_that_states_a_threshold(self):
        for path in (".claude/skills/gates/SKILL.md", "AGENTS.md", "CLAUDE.md",
                     ".claude/context/invariants.md", "CONSTRAINTS.md"):
            self.assertIn(path, self.defs)

    def test_every_entry_is_guarded(self):
        for path in self.defs:
            self.assertTrue(any(fnmatch.fnmatch(path, g) for g in self.guarded), path)


if __name__ == "__main__":
    unittest.main()
