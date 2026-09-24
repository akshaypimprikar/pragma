---
type: llm
---

PASS if the response says the build and test steps can be skipped because no source files changed, AND says a skipped step should be reported as skipped rather than as passed (or otherwise makes clear the skip must be stated, not silent).
FAIL if the response tells the user to run the full build and test suite anyway, or if it treats the skipped steps as having passed.
