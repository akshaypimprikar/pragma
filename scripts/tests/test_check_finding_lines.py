import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import check_finding_lines as fl  # noqa: E402

DIFF = """diff --git a/a.py b/a.py
--- a/a.py
+++ b/a.py
@@ -3,0 +4,2 @@
+x
+y
@@ -10 +12 @@
-old
+new
diff --git a/gone.py b/gone.py
--- a/gone.py
+++ /dev/null
@@ -1,3 +0,0 @@
-a
"""


class ClassifyTests(unittest.TestCase):
    def setUp(self):
        self.ranges = fl.changed_ranges(DIFF)

    def test_ranges(self):
        self.assertEqual(self.ranges, {"a.py": [(4, 5), (12, 12)]})

    def test_inside_single_line(self):
        self.assertEqual(fl.classify("a.py:5 something", self.ranges)[0], "INSIDE")

    def test_outside_line(self):
        self.assertEqual(fl.classify("a.py:8 something", self.ranges)[0], "OUTSIDE")

    def test_range_overlap_is_inside(self):
        self.assertEqual(fl.classify("a.py:1-4 x", self.ranges)[0], "INSIDE")

    def test_unchanged_file_is_outside(self):
        self.assertEqual(fl.classify("b.py:1 x", self.ranges)[0], "OUTSIDE")

    def test_backticked_citation(self):
        self.assertEqual(fl.classify("- `a.py:12` MEDIUM x", self.ranges), ("INSIDE", "a.py:12"))

    def test_leading_dot_path_is_kept(self):
        ranges = {".claude/skills/review/SKILL.md": [(39, 41)]}
        self.assertEqual(fl.classify("`.claude/skills/review/SKILL.md:40` x", ranges),
                         ("INSIDE", ".claude/skills/review/SKILL.md:40"))

    def test_added_line_starting_plus_plus_is_not_a_header(self):
        ranges = fl.changed_ranges("+++ b/a.py\n@@ -1 +1,2 @@\n+++ not a header\n+z\n")
        self.assertEqual(ranges, {"a.py": [(1, 2)]})

    def test_uncited_is_never_lowered(self):
        self.assertEqual(fl.classify("no citation here", self.ranges)[0], "UNCITED")


if __name__ == "__main__":
    unittest.main()
