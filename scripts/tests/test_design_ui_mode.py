"""The /design skill must branch on project.ui and keep SwiftUI as the default."""
import os
import unittest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
SKILL = os.path.join(ROOT, ".claude", "skills", "design", "SKILL.md")


class DesignUiModeTests(unittest.TestCase):
    def setUp(self):
        with open(SKILL) as f:
            self.text = f.read()

    def test_reads_project_ui_with_swiftui_default(self):
        self.assertIn("`project.ui`", self.text)
        self.assertIn("If the key is absent, assume `swiftui`", self.text)

    def test_uikit_section_covers_tokens_and_audit(self):
        section = self.text.split("## UIKit mode", 1)[1].split("## Rules", 1)[0]
        for needle in ("UIColor(named:)", "UIFont", "preferredFont", ".xib", ".storyboard", "Pods/"):
            self.assertIn(needle, section)

    def test_audit_step_points_uikit_at_its_section(self):
        self.assertIn('(UIKit: see "UIKit mode".)', self.text)

    def test_swiftui_examples_unchanged(self):
        self.assertIn("Color.teal.opacity(0.12)", self.text)


if __name__ == "__main__":
    unittest.main()
