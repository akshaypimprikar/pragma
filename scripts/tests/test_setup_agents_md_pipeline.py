"""The AGENTS.md that setup.sh generates describes the pipeline /pr-followup runs.

/pr-followup runs the built-in `code-review` at medium on `app`-lane PRs only,
then `/review`, one round by default, and only the `app` lane needs a
`code-review:` line. The generated AGENTS.md (which /pragma:init also builds
from) once named the `code-review:code-review` plugin, "at most 2 rounds" and a
`code-review:` line for every lane, so every new install got a stale pipeline.
"""
import os
import re
import unittest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
SETUP = os.path.join(ROOT, "scripts", "setup.sh")


def generated_agents_md():
    with open(SETUP, encoding="utf8") as f:
        text = f.read()
    m = re.search(r'cat > "\$AGENTS_MD" <<CLAUDEMD\n(.*?)\nCLAUDEMD\n', text, re.S)
    if not m:
        raise AssertionError("AGENTS.md heredoc not found in setup.sh")
    return m.group(1)


class GeneratedAgentsMdPipelineTests(unittest.TestCase):
    def setUp(self):
        self.body = generated_agents_md()

    def test_does_not_name_the_code_review_plugin(self):
        self.assertNotIn("code-review:code-review", self.body)

    def test_does_not_promise_two_rounds(self):
        self.assertNotIn("at most 2 rounds", self.body)

    def test_pipeline_line_matches_pr_followup(self):
        line = next(l for l in self.body.splitlines() if l.startswith("Standard pipeline:"))
        self.assertIn("lane: \\`code-review\\` at medium", line)
        self.assertIn("\\`pipeline\\` lane: \\`/review\\` only", line)
        self.assertIn("one round by default", line)

    def test_code_review_line_is_app_lane_only(self):
        line = next(l for l in self.body.splitlines() if "Each PR has a lane" in l)
        self.assertIn("\\`code-review:\\` line at the head SHA (\\`app\\` only)", line)


if __name__ == "__main__":
    unittest.main()
