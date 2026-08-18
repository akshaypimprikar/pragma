# Review Rejection Log

<!-- Append one entry per violation per PR. Never edit past entries. -->
<!-- Format:
## YYYY-MM-DD — PR#<N> — <Violation Type>
**What was wrong:** <description>
**Rule violated:** <rule from invariants.md or CLAUDE.md>
**File:** <path:line if known>
**Caught by:** <this review | code-review pass | manual verification>
-->

## 2026-08-18 — PR#49 — New schema-drift check in check_coverage.py was unreachable in the case it was written for
**What was wrong:** The fix added `if schema_drift_files: ... sys.exit(2)` to catch xccov schema drift (missing `lineCoverage` key), but placed it after the pre-existing `if not source_files: sys.exit(0)` guard. Schema drift is uniform across a report — a schema change typically empties `source_files` entirely rather than leaving some files behind — so the empty-files guard fired first and exited 0 (success) before the new diagnostic ever ran. This reproduced, in a worse form, the exact silent-false-pass failure the fix was written to prevent. Three independent code-review passes (shallow bug scan, git-history context, code-comment compliance) converged on the same root cause.
**Rule violated:** No CLAUDE.md exists in this repo — no formal rule, caught on correctness grounds.
**File:** `scripts/check_coverage.py:39-51` (fixed by moving the `schema_drift_files` check ahead of the `not source_files` exit)
**Caught by:** code-review:code-review
