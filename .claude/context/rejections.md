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

## 2026-09-07 — PR#55 — Round 1: unconditional auto-chain regression + exposed pre-existing bug
**What was wrong:** Making `code-review:code-review` auto-chain (per the 2026-09-07 pipeline-review Medium finding) was first implemented as an unconditional call, dropping the pre-existing `disable-model-invocation` graceful-degradation path in `pr-followup.md` and `parallel-review.md` (README.md and `gates.md` also fell out of sync with the same fact). Fixing `parallel-review.md`'s Check 1 also exposed a pre-existing bug: it pointed at `review.md`'s Architecture section, which by design only checks a PR's post-`/gates` summary — unsatisfiable at the pre-PR point `/parallel-review` actually runs.
**Rule violated:** No CLAUDE.md exists in this repo — no formal rule, caught on correctness grounds.
**File:** `pr-followup.md`, `parallel-review.md`, `gates.md`, README.md
**Caught by:** code-review pass (round 1, documented in PR#55 body / `docs/pipeline-review/2026-09-07.md`)

## 2026-09-07 — PR#55 — Round 2: duplicated stale copies of the same fact + missing severity rule
**What was wrong:** Two more stale copies of the `/pr-followup` 2-stage-chain fact surfaced (`test.md`, README.md's `/parallel-review` row), and the `disable-model-invocation` fallback text was independently duplicated across three files instead of having one canonical source — the same duplication pattern that caused the Round 1 regression. `parallel-review.md`'s combined verdict also had no severity rule tying `code-review:code-review` findings into the `READY FOR /gates` decision.
**Rule violated:** No CLAUDE.md exists in this repo — no formal rule, caught on correctness grounds.
**File:** `test.md`, README.md, `pr-followup.md`, `parallel-review.md`
**Caught by:** code-review pass (round 2, documented in PR#55 body / `docs/pipeline-review/2026-09-07.md`)

## 2026-09-07 — PR#55 — Round 3: wording/logic gaps in the consolidation
**What was wrong:** Five gaps introduced while consolidating the fallback: an ambiguous qualifier that could be read as gating an unconditional check, a severity rule that didn't cover Check 1's binary pass/fail output, a `Done when` that dropped the `disable-model-invocation` caveat, an Output-format fallback string that restated canonical wording instead of referencing it, and the canonical Note itself omitting that the fallback degrades gracefully rather than halting.
**Rule violated:** No CLAUDE.md exists in this repo — no formal rule, caught on correctness grounds.
**File:** `parallel-review.md`, `pr-followup.md`
**Caught by:** code-review pass (round 3, documented in PR#55 body / `docs/pipeline-review/2026-09-07.md`)

## 2026-09-07 — PR#55 — Round 4: missing carve-out, missing verdict state, misleading fallback wording, sibling staleness
**What was wrong:** Gate 10's UI-selector-listing command wasn't carved out of the "any output = fail" rule (false-positive risk on UI-test branches); the combined-verdict template had no slot for the qualified `disable-model-invocation` state; the fallback wording read as an interactive wait, contradicting its own "don't stall" clause; `plan.md` carried a sibling stale copy of the parallel-execution claim; and a CHANGELOG entry mislabeled which round fixed which defect.
**Rule violated:** No CLAUDE.md exists in this repo — no formal rule, caught on correctness grounds.
**File:** `parallel-review.md`, `plan.md`, `CHANGELOG.md`
**Caught by:** code-review pass (round 4, documented in PR#55 body / `docs/pipeline-review/2026-09-07.md`)

## 2026-09-07 — PR#55 — Round 5: last stale wording, final restated-fallback string
**What was wrong:** README.md's `/parallel-review` row still described its checks as running "in parallel," stale after Check 2 became a sequential step; `parallel-review.md` Check 2 still restated `/pr-followup`'s fallback substance despite its own instruction not to restate it.
**Rule violated:** No CLAUDE.md exists in this repo — no formal rule, caught on correctness grounds.
**File:** README.md, `parallel-review.md`
**Caught by:** code-review pass (round 5, documented in PR#55 body / `docs/pipeline-review/2026-09-07.md`)

## 2026-09-08 — PR#56 — Round 1: overclaim about Gate 10's diff scope
**What was wrong:** `parallel-review.md`'s Check 1 said Gate 10 "already uses `git diff develop...HEAD`, the same scope this command needs," with no exception noted. Gate 10's UI-selector-listing command actually scans all of `<AppName>UITests/*.swift` unconditionally, not the branch diff like every other Gate 10 command — the framing gave no warning of that one exception.
**Rule violated:** No CLAUDE.md exists in this repo — no formal rule, caught on correctness grounds.
**File:** `.claude/commands/parallel-review.md`
**Caught by:** code-review pass (found while porting this file to FinanceTracker, financetracker-ios PR #105, against its actual Gate 9 — documented in PR#56 commit `8db91c4`)

## 2026-09-08 — PR#56 — Round 2: run-on sentence, ambiguous phrasing, misleading fallback text
**What was wrong:** Three more wording issues in the same file: Check 1 was a single ~150-word run-on sentence covering three different checks and scoping rules; `Done when`'s "combined verdict from the three above" was ambiguous (three checks, or three possible verdict strings?); and Check 2's fallback said to reuse `/pr-followup`'s wording "exactly as documented," but that wording is merge-specific ("run it yourself before merging") and misleading in this pre-PR context, where the next step is `/gates`, not a merge.
**Rule violated:** No CLAUDE.md exists in this repo — no formal rule, caught on correctness grounds.
**File:** `.claude/commands/parallel-review.md`
**Caught by:** code-review pass (found on financetracker-ios PR #105, which ported this same file — documented in PR#56 commit `678fa95`)

## 2026-09-08 — PR#56 — Round 3: reintroduced the fallback-string duplication-drift pattern
**What was wrong:** Round 2's own fix ("adapt its wording") was itself an independently-authored copy of `pr-followup.md`'s fallback warning string with one word swapped — the exact duplication pattern PR#55's Round 2/3/5 entries above already flagged as a repeat-violation risk on this same file. Replaced with an explicit, mechanical substitution rule (take `/pr-followup`'s canonical string, swap `before merging` → `before /gates`) so there is no independently-maintained copy left to drift. Verified: the substitution renders correctly against `pr-followup.md`'s actual string, and the fix references the transformation rather than restating the text.
**Rule violated:** No CLAUDE.md exists in this repo — no formal rule, caught on correctness grounds. (Repeats the pattern named in the PR#55 Round 2/3/5 entries above; caught and fixed pre-merge this time.)
**File:** `.claude/commands/parallel-review.md`
**Caught by:** code-review pass (found by `code-review:code-review` on PR#56 itself — documented in commit `989aaa4`)
