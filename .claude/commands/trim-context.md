# Trim Context Agent

## Trigger
Invoked manually after completing any plan: `/trim-context`.

## Process

Audit and reduce always-on token costs.

## What to audit

### 1. CLAUDE.md (target: ≤50 lines)
Read `CLAUDE.md` and flag any content that violates these rules:

| Keep | Remove |
|---|---|
| Build commands + simulator name | Anything already in `.claude/commands/` |
| Layer rules (enforced in code decisions) | Model/service tables (readable from source) |
| Non-obvious domain constraints | Navigation structure (visible in source) |
| Test framework (import Testing, not XCTest) | ASCII diagrams |
| File inclusion note (PBXFileSystemSynchronizedRootGroup) | Verbose descriptions of obvious patterns |

If CLAUDE.md exceeds 50 lines or contains removable content, edit it in place and report what was cut.

### 2. Memory index (~/.claude/projects/.../memory/MEMORY.md)
Read the index and each linked file. Flag entries that are:
- Stale (reference things that no longer exist or have changed)
- Redundant with current project state
- No longer actionable

Remove stale files and their index entries. Report what was removed.

### 3. Settings.json allow list (target: broad patterns only)
Read `.claude/settings.json`. Flag any `Bash(...)` entries that are:
- Specific file paths — replace with a broad `Bash(git -C <project path> *)` pattern
- Pointing to paths that no longer exist
- Duplicated by an existing broad pattern

Edit in place, report changes.

### 4. Skill files (~/.claude/skills/)
Read each skill file. Flag content that is:
- Outdated (wrong simulator name, wrong paths, obsolete commands)
- Duplicated verbatim in CLAUDE.md or a command file

Report findings. Only edit skills if clearly outdated — skills load on-demand so verbosity matters less than CLAUDE.md.

### 5. Runtime read/tool-output waste (diagnostic only, no proxy required)
If `headroom` is installed (`command -v headroom`), run `headroom audit-reads` — it streams local Claude Code transcripts read-only and sizes addressable waste by category (identical repeats, stale reads, line-number scaffolding). This is a diagnostic, not a compression layer: it doesn't touch anything, it just tells you where the waste actually is.

Report the top category by byte share. If "stale (edit after read)" or "identical repeat" is unusually large relative to the last run, that's a *behavioral* signal, not a config fix — flag it in prose (e.g. "a file was re-read N times this session where the content hadn't changed — read once and hold it in context instead") rather than editing anything automatically; this section never changes files, unlike sections 1–4.

Skip silently if `headroom` isn't installed — this section is optional, not a requirement to adopt the tool.

## Output
For each section: what was found, what was changed (or "no changes needed").
End with the new line count for CLAUDE.md.

## Rules
- Do not remove content that is genuinely useful and not available elsewhere
- Do not add new content — this is a reduction pass only
- Commit any changes to CLAUDE.md and settings.json with message `chore: trim context after <plan-name>`

## Done when
CLAUDE.md is ≤50 lines, stale memory entries removed, settings.json allow list tightened, and changes committed.
