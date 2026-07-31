# Changelog

All notable changes to this project are documented in this file.

## [Unreleased]

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

### Fixed
- `pr-checks.yml` path filter that excluded files it should have covered
- README Mermaid diagram rendering (quoting) and the FinanceTracker repo link

### Documentation
- Full README polish for public launch: badges, Mermaid pipeline diagram, navigation, Getting Started, Quick Start
- `CONTRIBUTING.md` — contribution model, project-adaptation instructions, CI scaffold contribution rules
