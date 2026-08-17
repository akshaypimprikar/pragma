# Changelog

All notable changes to this project are documented in this file.

## [Unreleased]

## [1.2.0] — 2026-08-17

### Added
- **`/pragma-review` command** — pragma has no `CLAUDE.md`, `Views/`, or architecture layers for FinanceTracker's Swift-shaped `/review` agent to check, but every non-`/sync-workflow` pragma PR was merging with zero automated check and no auditable GitHub review trail. Checks template-literal leakage, `<placeholder>` convention, gate-numbering consistency, and doc/content accuracy — generalized from `/sync-workflow`'s existing self-review checklist so it runs on any pragma PR, posting its verdict via `gh pr review --comment` the same way FinanceTracker's `/review` does. Guarded to refuse running outside a checkout of the pragma repo itself, since the plugin manifest has no per-file exclusion mechanism and would otherwise install this command for every plugin consumer.
- **`/gates` Gate 9 — RED-before-GREEN commit order** — synced from FinanceTracker: verifies via git history that a test file was committed strictly before the implementation it exercises, never bundled in the same commit. Generic `scripts/check_tdd_commit_order.py` (path-segment matching, no hardcoded app-name prefixes); warns loudly and exits 2 rather than silently passing if `SCOPED_LAYER_DIRS` still holds the template's default layer names and matches nothing in the adopting project. `/feature` and `/plan` updated for the two-commit (RED, GREEN) task structure this gate depends on. `init.md`/`setup.sh` scaffold the new script into new projects. Old Gate 9 (Architecture & layer-rule compliance) renumbered to Gate 10; `review.md`'s cross-reference updated to match.

### Fixed
- **`scripts/setup.sh` copied `init.md` into every consumer project** — `init.md` itself documents that it should be excluded from the copied set ("it's the plugin's install command, not part of the target project's own pipeline"), but `setup.sh`'s copy step had no matching exclusion, so the shell-script install path leaked it in regardless. Fixed alongside adding the new `pragma-review.md` meta-command, which needed the same exclusion to avoid the identical leak. Found while scoping `/pragma-review`.
- **`scripts/setup.sh` could delete pragma's own tracked files** — the new `init.md`/`pragma-review.md` exclusion added a `rm -f` that would fire against pragma's own repo if `PROJECT_DIR` ever resolved to `REPO_ROOT` (e.g. running the script from inside the pragma repo without an explicit target directory). Added a guard that dies with a clear message instead. Found by `code-review:code-review`.
- **`/feature`'s TDD instruction referenced a non-invokable skill** — a plugin's `enabledPlugins: true` flag in `.claude/settings.json` does not guarantee its skills are actually invocable (`Skill(test-driven-development)` errors with `Unknown skill` in the parent project despite the flag being set, confirmed empirically). Replaced with a self-contained instruction carrying the same substance directly in `feature.md`, no external invocation dependency. `gates.md`'s Gate 9 rationale updated to match. Found by FinanceTracker's 2026-08-16 pipeline review.
- **README Commands table omitted `/parallel-review`** despite the command existing and being used by both pragma and FinanceTracker; merged-PR count callout refreshed from "70+" to "80+" to match FinanceTracker's current count. Found during a README/About audit.

## [1.1.0] — 2026-08-15

### Added
- **Installable as a real Claude Code plugin** — `.claude-plugin/plugin.json` + `marketplace.json`, so `/plugin marketplace add akshaypimprikar/pragma` + `/plugin install pragma@pragma` works with no clone or shell script. Verified locally: `claude plugin validate` passes, and `claude plugin details pragma@pragma` confirms all 16 commands (including the new `/pragma:init`) plus the existing `deterministic-pr-gates` skill — 17 components total — load correctly.
- **`/pragma:init` command** — the plugin-native equivalent of `scripts/setup.sh`, but interviews the user for `CLAUDE.md`'s architecture and key-constraints content and seeds `.claude/context/invariants.md` from the same answers, instead of leaving both as templates to fill in manually — and, unlike the first draft of this command, never overwrites either file if it already exists, matching `setup.sh`'s own guard. Closes the "3 manual edits before your first `/spec`" friction gap that plain `setup.sh` left.
- README: real FinanceTracker screenshots (Dashboard, Accounts) instead of no visuals at all, and a "Why not just Cursor/Windsurf/Copilot's spec mode?" section addressing the obvious comparison directly — CI-enforced gates, cross-session memory, and a real app with 70+ merged PRs are the three things a built-in spec mode doesn't give you

### Fixed
- **`/review`, `/feature`, `/test` had drifted behind FinanceTracker's local, improved copies** — `/review` now logs pre-review fixes to `rejections.md` (not just its own CHANGES REQUESTED verdicts) and posts its verdict as a real GitHub review via `gh pr review --comment`; `/feature` requires both a repeat-call/duplicate test and a missing-required-field test for mutations on shared/persisted entities; `/test`'s Trigger description no longer contradicts `/pr-followup` about running in parallel with `/review`. Caught by a code-review pass on an external plugin-directory listing that described capabilities the shipped template didn't actually have.
- Pre-existing "7 gates" → "9 gates" drift in `feature.md` (`gates.md` itself already documents 9 gates; the prose just never caught up)

## [1.0.0] — 2026-07-31

First versioned release. Pragma has been developed and battle-tested against [FinanceTracker](https://github.com/akshaypimprikar/financetracker-ios) since May 2026; this release captures that history as a baseline.

### Added
- Agent commands layer: 13 Claude Code slash commands covering the full SDLC (`/spec`, `/plan`, `/feature`, `/gates`, `/review`, `/test`, `/bugfix`, `/release`, `/design`, `/pr-followup`, `/sync-workflow`, `/parallel-review`, `/pipeline-review`)
- CI pipeline layer: three GitHub Actions workflow templates (`pr-checks.yml`, `ui-tests.yml`, `release.yml`) for automated PR validation, UI test execution, and release tagging
- Support scripts layer: `select_simulator.py` (deterministic iOS Simulator selection for CI) and `check_coverage.py` (test coverage enforcement)
- `scripts/setup.sh` — one-command installer that copies all three layers into a target iOS project and substitutes placeholders
- Persistent memory layer: `.claude/context/` (invariants, decisions, rejections, feature-log) wired as read/write preambles and postambles across all agent commands
- `/gates` Gate 9 — layer-rule / architecture-compliance checks, including the Patterns checklist (`@Model` shape, relationship `deleteRule`, service statelessness), ported from `/review`'s architecture checklist
- `/gates` abstraction-bloat gate, review-fix loop tips, and a Fowler harness mapping reference
- `/pr-followup` command — auto-chains `/review` then `/test` immediately after a PR opens
- `deterministic-pr-gates` — a standalone, project-agnostic skill extracted from `/gates`
- CI: skip build/test gates when a PR changes no Swift files

### Changed
- Renamed the project from `ios-agent-workflow` to **Pragma**
- Removed auto-merge from `/review` — merging now always requires explicit human action, since GitHub blocks PR authors from approving their own PRs
- `scripts/setup.sh`'s generated CLAUDE.md Merge rule now exempts `release/*`/`hotfix/*` PRs from `/review` and `code-review:code-review` — every commit in those PRs already passed both when it merged into `develop`, so `/release`'s pre-flight test run is the only gate needed there

### Fixed
- `pr-checks.yml` path filter that excluded files it should have covered
- README Mermaid diagram rendering (quoting) and the FinanceTracker repo link

### Documentation
- Full README polish for public launch: badges, Mermaid pipeline diagram, navigation, Getting Started, Quick Start
- `CONTRIBUTING.md` — contribution model, project-adaptation instructions, CI scaffold contribution rules
