---
type: llm
---

PASS if the response lists concrete pre-PR checks that each have a yes/no outcome (for example a build, the full test suite, a coverage threshold, branch naming, a changelog entry) and gives an order for running them.
FAIL if the response mostly tells the user to "review the code quality" or to use their judgment, or lists no specific checks.
