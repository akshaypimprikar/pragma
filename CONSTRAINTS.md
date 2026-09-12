# Constraints

Per-project quality dimensions that `/gates` enforces or offers, kept separate
from `gates.md` itself so platform-specific or project-tunable pieces don't
need to be hardcoded into the gate definitions. Each dimension: a name, a
cost tier (`fast` = every commit, `task` = per-`/gates` run, `full` =
pre-release only), and a concrete check.

This file is additive to `gates.md`, not a replacement for it — build, test,
and security-pattern checks stay defined directly in `gates.md` Gate 1/2/7
(and in your project's `CLAUDE.md`). Nothing here duplicates those; adding a
second source of truth for the same commands is worse than not having this
file at all.

## Floor (always enforced, every `/gates` run)

1. **gate-integrity** — tier: `task` — `python3 scripts/check_gate_integrity.py [base-branch]`
   (defaults to `develop`; pass `main` explicitly on a `release/*`/`hotfix/*`
   branch). Canonical description — `gates.md` Gate 11 and the standalone
   `deterministic-pr-gates` skill's Gate 10 (same script, one number lower
   there since that file has no Gate 9 TDD-commit-order equivalent) both
   point here rather than restating this, to avoid a third copy drifting out
   of sync.

   Every other gate is agent-instruction-driven — read the prompt, run the
   described commands, evaluate — with nothing stopping an agent under
   pressure to make a stuck gate pass from editing the gate definition
   instead of fixing the underlying violation. This dimension catches the
   diff-detectable half of that problem: a gate-definition file (`gates.md`,
   this file, a `scripts/check_*.py`) edited on a `feature/*` branch, a
   previously-existing test deleted instead of fixed, a new suppression/skip
   marker (`swiftlint:disable`, a Swift Testing `.disabled()` trait,
   `XCTSkip` — scanned in test files only, since SwiftUI's `.disabled(_:)`
   view modifier shares the same syntax in ordinary application code), a
   bare/message-less `fatalError()`/`preconditionFailure()` newly added to
   non-test code, or a percentage threshold in a gate-definition file lowered
   by the diff (matched per-hunk position, not globally, so an unrelated
   number elsewhere in the file can't false-positive or mask a real change).
   It cannot block the edit from happening mid-session — that needs a native
   `PreToolUse` hook, a separate architecture piece not built here — only
   catch it once `/gates` runs. It also can't catch an agent that renames its
   branch away from `feature/*` specifically to dodge check #1, or a
   non-percentage numeric threshold (Gate 6's coverage floor is always
   written as `≥80%`, so this hasn't mattered yet, but a future bare-integer
   threshold would slip through) — both are known, documented gaps, not
   silent ones.

## Opt-in (per-project — uncomment and adapt to enable)

<!--
2. **coverage-ratchet** — tier: `full` — no fixed target; measure the
   current coverage %, store it, and refuse any PR that regresses below the
   stored baseline. Use instead of (or alongside) `/gates` Gate 6's fixed
   ≥80% threshold if your project doesn't have — or doesn't want — a fixed
   number. Not yet wired into `check_coverage.py`; adopting this dimension
   currently means enforcing it manually until that wiring exists.

3. **accessibility** — tier: `task` — platform-specific, not yet built:
   - iOS: VoiceOver label presence + Dynamic Type support manual checklist
   - web (future): axe-core / WCAG automated scan
   - android (future): TalkBack manual checklist
   Deferred as of 2026-09-10 — tracked here so the platform-specific check
   has a config seam to land in once built, rather than getting hardcoded
   into `gates.md` directly.
-->
