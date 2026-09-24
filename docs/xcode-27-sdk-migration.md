# Xcode 27 SDK source-compatibility notes

`/gates` Gate 1 links here. If a SwiftUI build error appears on code that compiled before an Xcode major-version update, check these two patterns before assuming the change under review is at fault. This is a short pointer, not a migration guide: every technical claim below is taken from one third-party skill and cited to file and line, and none of it has been reproduced by pragma.

**Source:** [AvdLee/SwiftUI-Agent-Skill](https://github.com/AvdLee/SwiftUI-Agent-Skill), `skills/swiftui-expert-skill/references/`, read at commit `b24e68a965dc4b5bd2cc41dc60c094a26a9379ce` on 2026-09-20. Line numbers are for that commit and will drift.

## 1. `@ContentBuilder` overload ambiguity

The skill says SDK 27 unifies many SwiftUI result builders under `@ContentBuilder`, that block contents are no longer constrained to `View`, and that "previously compiling source can become ambiguous" (`view-structure.md:247`). It advises choosing "the narrow fix matching the diagnostic" and not broadly rewriting working builders (`view-structure.md:247`).

The case relevant to this note: an ambiguous `ShapeStyle.opacity` or `blendMode` passed directly to `overlay` or `background`. The documented fix is to select the builder overload (`view-structure.md:249-254`); its example uses `overlay`:

```swift
Rectangle().overlay {
    Color.blue.opacity(0.3).blendMode(.overlay)
}
```

Applying the same shape to `background` (`.background { Color.blue.opacity(0.3) }` in place of `.background(Color.blue.opacity(0.3))`) follows from the skill's statement that the problem covers `overlay` or `background` (`view-structure.md:249`), but the skill's only code example is for `overlay`.

Other fixes the skill lists for the same change (`view-structure.md:255-257`): fully qualify a shadowed SwiftUI type such as `SwiftUI.Color.clear`; avoid spelling concrete `TupleView`/`TupleContent` types and prefer `some View`; give an ambiguous empty nested builder an explicit `EmptyContent()` or `EmptyView()`.

## 2. `@State` becomes a macro

The skill says SDK 27 migrates `@State` from a property wrapper to a macro (`state-management.md:80`).

- **Seeding state from an initializer:** drop the declaration's initial value and assign once in `init` (example at `state-management.md:82-92`). The skill warns not to fix a "used before being initialized" error by reordering assignments, and that assigning in `init` to state that still has a declaration default remains incorrect because "later parent arguments do not replace child-owned state" (`state-management.md:94`).
- **"Invalid redeclaration of synthesized property":** attributed to another property wrapper composed with `@State` colliding with macro-generated storage; remove the redundant wrapper or restructure (`state-management.md:98`).
- **Missing private memberwise initializer:** the skill says SDK 27 may not synthesize it for a view containing `@State`; define the initializer explicitly (`state-management.md:99`).

## What is and is not established

- Both patterns are described by the third-party skill as SDK 27 source-compatibility hazards. Neither file states which Xcode 27 point release introduced the behavior, and neither quotes exact compiler diagnostics for the `@ContentBuilder` case. This note does not claim a release.
- pragma has not reproduced either break. A FinanceTracker checkout (the app pragma's templates were derived from) built cleanly and passed a targeted unit-test run on Xcode 27.0 (27A266a), including its two `.background(` call sites (`DashboardView.swift`, `ImportSheet.swift`). Those two call sites are `.background(shape.fill(...).overlay(...))` and `.background(Theme.Chips.suggestionBackground)`, neither the `Color.x.opacity(...)` shape above, so that result is weak evidence either way.
- A community post reports a related failure but does not establish this note's mechanism or fix. [r/iOSProgramming, 2026-09-19](https://www.reddit.com/r/iOSProgramming/comments/1wk6t16/if_you_use_swiftuicoloropacity_xcode_27_will/) (35 upvotes, 7 comments when read on 2026-09-21) is titled "If you use SwiftUI.Color.opacity, Xcode 27 will break your app." Its body is a one-line complaint with no code sample or Xcode build number, and of the 7 comments only 3 were readable; one of those asks the author for a minimal failing `.background(...)` example next to a working `.background { ... }` version. Treat it as a sign that others hit a `Color.opacity`-related failure, not as confirmation of the `@ContentBuilder` explanation above.
- If a build failure matches one of these patterns, apply the narrow fix, rebuild, and treat the diagnostic text from your own compiler as the authority over this note.
