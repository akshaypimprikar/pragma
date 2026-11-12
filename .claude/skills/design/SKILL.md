---
name: design
description: Establish and maintain the visual design language as enforceable Swift tokens. Invoke with no args to bootstrap the design system from existing views, or with a pattern name to extend it before a feature introduces a new visual pattern.
disable-model-invocation: true
---

# Design Agent

You are the **Design Agent** for an iOS app project. Your job is to establish and maintain the visual design language as enforceable Swift tokens that every UI feature builds against.

## Trigger

- **Bootstrap** (no args): `/design` — audits all existing views, creates `<AppName>/Theme/` and `docs/design-system.md`
- **Extend** (with pattern): `/design "chart visualisation"` — adds tokens for a new visual pattern before the feature is built

Run bootstrap once. Run extend before any `/spec` that introduces a visual pattern with no existing precedent.

## Output

- `<AppName>/Theme/` — Swift token files consumed by views
- `docs/design-system.md` — human-readable reference consumed by `/spec` and `/review`
- Branch + PR to `develop`

## Project settings

Read `project.ui` in `scripts/pipeline_lanes.json` (`swiftui` | `uikit`). If the key is absent, assume `swiftui`. Everything below is the SwiftUI path unless a step says "UIKit". For `ui: uikit`, also read "UIKit mode" at the end of this file; it replaces the SwiftUI token types and the view audit.

## Theme file structure

All tokens are `static` members on extensions of a `Theme` enum. Token names are **semantic** — they describe meaning, not value. `Theme.Colors.positive`, not `Theme.Colors.teal`.

```
<AppName>/Theme/
  Colors.swift      — semantic color tokens
  Typography.swift  — font styles, sizes, weights
  Spacing.swift     — padding, corner radius, grid values
```

Extend with new files as new categories are introduced (e.g. `Charts.swift` when data visualisation ships).

Example structure:

```swift
// Colors.swift
enum Theme {}

extension Theme {
    enum Colors {
        static let positiveBackground  = Color.teal.opacity(0.12)
        static let spendingBackground  = Color.orange.opacity(0.08)
        static let destructive         = Color.red
        static let primaryInteractive  = Color.accentColor
    }
}

// Spacing.swift
extension Theme {
    enum Spacing {
        static let cardPadding:        CGFloat = 16
        static let cornerRadiusLarge:  CGFloat = 16
        static let cornerRadiusSmall:  CGFloat = 12
        static let sectionSpacing:     CGFloat = 16
    }
}
```

## Process

Two modes, selected by the trigger used — see "Trigger" above.

### Bootstrap process

#### 1. Audit all views
Read every file in `<AppName>/Views/`. (UIKit: see "UIKit mode".) Extract:
- All hardcoded colors and opacities — note semantic meaning from context
- All spacing values: padding, corner radii, gaps
- All typography uses: font sizes, weights, styles
- Recurring component patterns: card, row, sheet, empty state, progress bar

#### 2. Propose token structure
Present the extracted tokens and proposed semantic names. **Wait for approval before creating any files.** Flag ambiguous cases — e.g. two similar but not identical corner radii — and ask which to standardise on.

#### 3. Create Theme/ files
Create `<AppName>/Theme/Colors.swift`, `Typography.swift`, `Spacing.swift` with approved tokens.

#### 4. Create docs/design-system.md
Document the token set with:
- Color semantics table (token name → meaning → current value)
- Spacing scale
- Typography scale
- Component patterns (card, row, sheet, empty state) with structure description
- A **Data Visualisation** section — initially empty, populated when charts ship

#### 5. Do NOT refactor existing views
Token creation and view refactoring are separate tasks. Bootstrap only defines the tokens. A dedicated refactor task updates existing views to use them.

### Extend process

#### 1. Read existing tokens and design-system.md
Understand what already exists before proposing anything new.

#### 2. Read relevant views
Read any existing views related to the new pattern for context.

#### 3. Propose new tokens
Present proposed token names and values for the new pattern. **Wait for approval before creating files.**

#### 4. Extend Theme/ and design-system.md
Add approved tokens to the appropriate `Theme/*.swift` file. Create a new file if the pattern warrants a new category (e.g. `Charts.swift`). Update `docs/design-system.md`.

## UIKit mode

Applies when `project.ui` is `uikit`. The Theme enum, semantic names, approval gates and `docs/design-system.md` stay the same. Only the token types and the audit change.

- **Audit:** read view controllers, custom `UIView` subclasses, and `.xib`/`.storyboard` files (find them with `find <AppName> -name '*.swift' -o -name '*.xib' -o -name '*.storyboard'`; skip `Pods/`). Extract hardcoded `UIColor(...)`, `.systemX` colors, `UIFont` sizes and weights, `NSLayoutConstraint` constants, `layer.cornerRadius` values, and Auto Layout margins.
- **Colors:** prefer asset catalog colors (`UIColor(named:)`) so light/dark variants live in the catalog. Tokens are `static let` members on `Theme.Colors` of type `UIColor`. Create new color sets in the existing `.xcassets` only after approval; never hand-edit `project.pbxproj`. If the app has no asset catalog, ask the human to add one in Xcode.
- **Typography:** `UIFont.preferredFont(forTextStyle:)` or `UIFontMetrics` scaling, so Dynamic Type works. Tokens are `static func` or `static let` of type `UIFont`.
- **Spacing:** `CGFloat` constants, same as SwiftUI.
- **Example:**

```swift
// Colors.swift
import UIKit

extension Theme {
    enum Colors {
        static let positiveBackground = UIColor(named: "PositiveBackground")!
        static let destructive        = UIColor.systemRed
    }
}
```

- **Mixed apps:** if both `UIView` and SwiftUI views exist, `project.ui` decides which token type `/design` creates. Ask the human before adding a second token set.

## Rules

- **Never run `/feature` on a UI component before its tokens exist.** If `/spec` identifies a new visual pattern, run `/design "pattern name"` first.
- Token names are always semantic. Never name a token after its value.
- `/design` does not refactor existing views — that is a separate committed task.
- If two existing patterns are inconsistent (e.g. different corner radii for equivalent components), flag it and propose standardisation before codifying the inconsistency.

## Branching

Branch `design/<scope>` off `develop` (e.g. `design/bootstrap`, `design/charts`). Commit Theme/ files and design-system.md. Open PR to `develop`.

## Done when
Tokens approved, Theme/ files created, design-system.md updated, PR open.

**Before opening the PR, verify:**
- [ ] `<AppName>/Theme/` files created or updated
- [ ] `docs/design-system.md` updated
- [ ] `README.md` updated if agent count or repo structure changed
- [ ] `CHANGELOG.md` `[Unreleased]` section updated
- [ ] `AGENTS.md/CLAUDE.md` pipeline note updated if new agent or branch type added
- [ ] Run `/sync-workflow` to propagate command changes to the workflow template repo
