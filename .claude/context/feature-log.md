# Feature Log

<!-- Append one entry per release. Never edit past entries. -->
<!-- Format:
## v<X.Y.Z> — YYYY-MM-DD
**Features added:** <bullet list>
**Key files changed:** <comma-separated key files or layers>
**Key architectural decisions:** <brief note or "none">
-->

## v1.2.0 — 2026-08-17
**Features added:** `/pragma-review` command — auditable self-review for pragma's own PRs (template-literal leakage, `<placeholder>` convention, gate-numbering consistency, doc/content accuracy), posts verdict via `gh pr review --comment`; guarded to refuse running outside a checkout of the pragma repo itself. `/gates` Gate 9 — RED-before-GREEN commit order, synced from FinanceTracker, generic `scripts/check_tdd_commit_order.py` with a loud exit-2 warning (not a silent pass) when `SCOPED_LAYER_DIRS` doesn't match the adopting project.
**Key files changed:** `.claude/commands/pragma-review.md` (new), `.claude/commands/gates.md`, `.claude/commands/review.md`, `.claude/commands/feature.md`, `.claude/commands/plan.md`, `scripts/check_tdd_commit_order.py` (new), `scripts/setup.sh`, `.claude-plugin/plugin.json`
**Key architectural decisions:** none

## v1.1.0 — 2026-08-15
**Features added:** Installable as a real Claude Code plugin (`.claude-plugin/plugin.json` + `marketplace.json`, `/pragma:init` interactive setup command replacing manual CLAUDE.md/invariants.md fill-in); README screenshots + "Why not a spec mode?" positioning section.
**Key files changed:** `.claude-plugin/plugin.json`, `.claude-plugin/marketplace.json`, `.claude/commands/init.md` (new), `.claude/commands/review.md`, `.claude/commands/feature.md`, `.claude/commands/test.md`, `README.md`, `CHANGELOG.md`.
**Key architectural decisions:** Plugin `commands`/`skills` fields reference the existing `.claude/commands/` and `skills/` directories directly via custom paths rather than restructuring into a root-level `commands/` dir — avoids duplicating files, matches the docs' supported custom-path mechanism. `/pragma:init` ports `scripts/setup.sh`'s file-copy logic but adds an interview step for `CLAUDE.md`/`invariants.md` content, with the same skip-if-exists guard `setup.sh` has always had on both files independently. This release also closed a real gap: `/review`/`/feature`/`/test` had drifted behind FinanceTracker's locally-improved copies (rejections.md logging, real GitHub review posting, edge-case test requirements) — caught via a code-review pass on an external plugin-directory listing, synced back in the same release.
