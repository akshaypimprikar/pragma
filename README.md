# ios-agent-workflow

> A Claude Code command pipeline that takes an iOS feature from idea to merged PR with two decisions from you — approve the spec, approve the plan. Eight agents handle the rest.

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](./LICENSE)
[![Claude Code](https://img.shields.io/badge/Claude_Code-compatible-5A67D8?logo=anthropic&logoColor=white)](https://claude.ai/code)
[![Platform](https://img.shields.io/badge/platform-iOS-black?logo=apple&logoColor=white)](https://developer.apple.com/ios/)
[![Swift](https://img.shields.io/badge/Swift-5.9%2B-FA7343?logo=swift&logoColor=white)](https://swift.org)

Proven on [FinanceTracker](https://github.com/akshaypimprikar/personal-finance-tracker) — a production SwiftUI + SwiftData app built entirely on this pipeline from day one, with specs, plans, and PRs going back to the first commit.

---

**[Quick Start](#quick-start) · [Pipeline](#pipeline) · [Commands](#commands) · [Memory Layer](#memory-layer) · [Customising](#customising-for-your-project) · [Contributing](CONTRIBUTING.md)**

---

## Quick Start

```bash
cp -r .claude/commands/ /path/to/your-ios-app/.claude/commands/
cp -r .claude/context/  /path/to/your-ios-app/.claude/context/
```

1. Replace `<AppName>` with your module name in each command file
2. Add your build commands, simulator target, and architecture rules to `CLAUDE.md`
3. Seed `.claude/context/invariants.md` with your non-negotiable rules

Then kick off your first feature:

```
/spec "describe your feature idea"
```

Each command is plain markdown — no dependencies, no build step.

---

## Pipeline

```mermaid
flowchart TD
    A([💡 Idea]):::dim --> B
    B["/spec\n✓ you approve"]:::human --> C
    C["/plan\n✓ you approve"]:::human --> D
    D[/feature]:::auto --> E
    E[/gates]:::auto --> F
    F([PR opened]):::dim --> G & H
    G[/review]:::auto --> I
    H[/test]:::auto --> I
    I([merge to develop]):::dim -.->|next feature| B
    I --> J[/release]:::auto --> K([main · tagged]):::dim

    BUG([Bug report]):::dim --> BF[/bugfix]:::auto --> BG[/gates]:::auto --> BP([PR]):::dim --> BR[/review]:::auto --> BM([merge]):::dim

    classDef human fill:#3d2800,stroke:#fbbf24,color:#fbbf24
    classDef auto  fill:#0a1f14,stroke:#34d399,color:#34d399
    classDef dim   fill:#161b22,stroke:#30363d,color:#8b949e
```

You approve twice — after `/spec` and after `/plan`. Everything else runs autonomously until merge.

---

## Commands

### Core pipeline

| Command | What it does |
|---|---|
| `/spec "feature idea"` | Proposes 2–3 approaches, you choose, spec doc saved |
| `/plan docs/specs/my-spec.md` | Turns an approved spec into a task-by-task implementation plan |
| `/feature docs/plans/my-plan.md` | Executes an approved plan — TDD, one commit per task |
| `/gates` | Verifies build, full test suite, and architecture compliance before PR |
| `/review` | Reviews a PR for architecture compliance |
| `/test` | Writes tests for a feature branch — run in parallel with `/review` |
| `/bugfix "description"` | Regression test first, then fix — test-first always |
| `/release 1.0.0` | Version bump, changelog, PR to main, git tag |

### Utility

| Command | What it does |
|---|---|
| `/design` | Establishes visual design tokens — run before `/spec` on UI features |
| `/pipeline-review` | Audits the pipeline for drift, gaps, and inefficiencies |
| `/status` | Reconstructs where work stands — use to resume any session |
| `/trim-context` | Trims accumulated context after completing a plan |
| `/sync-workflow` | Syncs this template repo with your project's latest conventions |

---

## Memory Layer

The pipeline accumulates institutional knowledge across sessions in `.claude/context/`:

```
.claude/context/
├── invariants.md    — rules no agent may override (architecture, money types, etc.)
├── decisions.md     — log of every spec decision: approach chosen + reason
├── feature-log.md   — record of every feature shipped
└── rejections.md    — approaches ruled out, with reasons
```

Every agent reads these files before acting. Over time the pipeline carries the same context a senior engineer would — constraints, past decisions, and dead ends — surviving every session boundary.

Populate `invariants.md` before running `/feature` for the first time.

---

## How the Feature Agent works

Each task in the plan follows strict TDD:

1. Write the failing test — confirm it fails for the right reason
2. Implement the minimal code to make it pass
3. Run the full test suite — no regressions allowed
4. Commit — one commit per task, no batching

The agent never proceeds to the next task if tests are red.

---

## Customising for your project

**Architecture assumptions (defaults — override in `CLAUDE.md`):**

- **MVVM + Repository** — views contain no business logic, ViewModels depend on protocols never concrete implementations
- **SwiftData** for persistence — Domain Services have zero SwiftData imports
- **Swift Testing** — `import Testing`, `@Suite`, `@Test`, `#expect()` for unit/integration tests; XCUITest for UI tests
- **`PBXFileSystemSynchronizedRootGroup`** (Xcode 16+) — files auto-compile when placed in the correct directory; never edit `project.pbxproj`

**To adapt for your project:**

1. Copy `.claude/commands/` and `.claude/context/` into your project
2. Replace `<AppName>` with your module name in each command file
3. Update `CLAUDE.md` with your build commands, simulator target, and architecture rules
4. Populate `.claude/context/invariants.md` with your non-negotiable rules
5. Update the Architecture Rules checklist in `/review` to match your stack

---

## Branch Strategy (Gitflow)

```
main        — production, tagged on release only
develop     — integration branch, all features merge here
feature/*   — off develop
fix/*        — off develop  (hotfix/* off main)
release/*   — off develop, PR to main, back-merged to develop
spec/*      — off develop, for spec + plan docs
```

---

## Author

Built by [Akshay Pimprikar](https://www.linkedin.com/in/akshaypimprikar) — iOS lead engineer building agentic AI pipelines.
