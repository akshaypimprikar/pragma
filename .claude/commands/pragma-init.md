# Pragma Init

You are running Pragma's setup inside Claude Code — the plugin equivalent of `scripts/setup.sh`, with one difference: instead of leaving `CLAUDE.md` and `invariants.md` as templates for the user to fill in later, you interview them for real content now.

## Trigger

Invoked after the user installs the `pragma` plugin (`/plugin install pragma@pragma`) and runs `/pragma:init` inside their iOS project's repo root. Optionally takes the app name as an argument (e.g. `/pragma:init MyApp`); ask for anything not supplied.

## Process

### 1. Gather the basics

Ask (skip any already given as arguments):
- App/Xcode module name (e.g. `MyApp`) — must match the `.xcodeproj`
- Project root path (default: current directory — confirm this is actually the repo root containing the `.xcodeproj`)
- Xcode scheme name (default: same as the app name)
- iOS Simulator device name to standardize on (e.g. `iPhone 17`)

### 2. Copy and substitute command files

```bash
mkdir -p "$PROJECT_DIR/.claude/commands"
cp -r "${CLAUDE_PLUGIN_ROOT}/.claude/commands/." "$PROJECT_DIR/.claude/commands/"
find "$PROJECT_DIR/.claude/commands" -name "*.md" -exec sed -i '' "s|<AppName>|$APP_NAME|g" {} +
```
(Use GNU `sed -i` without the trailing `''` on Linux — detect with `sed --version 2>/dev/null | grep -q GNU`.)

Remove `pragma-init.md` itself from the copied set — it's the plugin's install command, not part of the target project's own pipeline.

### 3. Copy context, scripts, and CI workflows

Same as `scripts/setup.sh`: copy `.claude/context/*.md` (skip any that already exist — never overwrite a project's existing decisions/rejections log), `scripts/select_simulator.py` and `check_coverage.py`, and `scaffold/.github/workflows/*.yml` into `.github/workflows/` — strip the leading setup-comment block from each workflow file, then substitute `YOUR_PROJECT` → app name and `YOUR_SCHEME` → scheme name. Skip any workflow file that already exists at the destination and warn instead of overwriting.

### 4. Generate CLAUDE.md — interactively, not as a template

This is the actual point of `/pragma-init` over `setup.sh`. Ask:
- "What's your app's one-line description?" (e.g. "a personal finance app")
- "What's your architecture?" (offer MVVM + Repository as the default pragma assumes, but accept anything — TCA, VIPER, plain MVC)
- "What are your non-negotiable rules — the things no agent should ever be allowed to violate?" Prompt with examples relevant to their stated architecture (money-as-Decimal if it's a finance-adjacent app, zero-SwiftData-imports in domain services if MVVM+Repository, etc.) but don't assume — ask.

Write `CLAUDE.md` with real content in the Architecture and Key Constraints sections instead of the HTML-comment placeholders `scripts/setup.sh` leaves behind. Keep the Build & Test and Merge Rule sections as-is (those are mechanical, not project-specific).

### 5. Seed invariants.md from the same interview

The "non-negotiable rules" answered in step 4 go into both `CLAUDE.md`'s Key Constraints section and `.claude/context/invariants.md` as numbered entries — these should already be consistent, since they're the same interview answer, not two separate questions.

### 6. Report done

```
Pragma is installed and configured for <AppName>.

Next: run your first feature —

  /spec "describe your feature idea"
```

## Done when

`.claude/commands/`, `.claude/context/`, `scripts/`, and `.github/workflows/` are populated in the target project; `CLAUDE.md` has real architecture and constraint content, not placeholders; `.claude/context/invariants.md` is seeded from the same interview.
