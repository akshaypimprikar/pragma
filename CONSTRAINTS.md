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

1. **gate-integrity** — tier: `task` — `python3 scripts/check_gate_integrity.py`
   Catches gate-weakening: a gate-definition file edited on a `feature/*`
   branch, a test deleted instead of fixed, a new suppression/skip marker,
   an unfinished stub in shipped code, or a lowered threshold in a
   gate-definition file. See `/gates`' new Gate 11 for how this wires in.

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
