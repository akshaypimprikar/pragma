---
type: llm
---

PASS if the response puts a change-scope check first in its order of checks: working out which files changed, or whether any source files changed, before it lists the build and test checks. A change-scope check that appears only after the build or tests does not count.
FAIL if the response has no change-scope check, or lists it after the build or the tests.
