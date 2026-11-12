# Xcode 27 SDK source-compatibility notes

`/gates` Gate 1 links here. If a SwiftUI build error appears on code that compiled before an Xcode major-version update, check these two patterns before assuming the change under review is at fault. This is a short pointer, not a migration guide.

**Primary source:** Apple's `swiftui-whats-new-27` Agent Skill, which ships inside Xcode 27. Export it with `xcrun agent skills export --output-dir <dir>`, or install it for Claude Code by running `scripts/install_apple_skills.sh` from a pragma checkout (`setup.sh --apple-skills` runs it too). Files cited here: `references/state-macro.md` and `references/content-builder.md`, read from Xcode 27.2 (27B52) on 2026-11-01. Apple publishes these files only inside Xcode, so there is no web link. Apple marks both sections "SDK Version: 27.0 and later".

**Secondary source:** [AvdLee/SwiftUI-Agent-Skill](https://github.com/AvdLee/SwiftUI-Agent-Skill), `skills/swiftui-expert-skill/references/` (commit `b24e68a965dc4b5bd2cc41dc60c094a26a9379ce`, read 2026-09-20). It describes the same two patterns (`view-structure.md:247-257`, `state-management.md:80-99`) and was this note's only source before Apple's skill was available.

## 1. `@ContentBuilder` overload ambiguity

Apple's skill says many SwiftUI result builders, most notably `@ViewBuilder`, are unified under `@ContentBuilder` and no longer constrain block contents to `View` (`content-builder.md:4`). A `ShapeStyle` expression with `.opacity()` or `.blendMode()` passed directly to the non-builder `overlay` or `background` can then fail with `error: ambiguous use of 'opacity'` or `'blendMode'` (`content-builder.md:6-13`). The fix is the trailing-closure form (`content-builder.md:27-40`):

```swift
// Fails: .overlay(Color.blue.opacity(0.70).blendMode(.overlay))
Rectangle().overlay {
    Color.blue.opacity(0.3).blendMode(.overlay)
}
```

The same applies to `background` (`content-builder.md:6`). The same file also covers `error: ambiguous use of 'red'` when another module shadows a SwiftUI type (`content-builder.md:46`); fully qualify it, as in `SwiftUI.Color.clear`, or rename the other module's type (`content-builder.md:74`).

## 2. `@State` becomes a macro

Apple's skill says `@State` has been migrated from a property wrapper to a macro (`state-macro.md:4`).

- **Seeding state from an initializer:** a declaration default plus an assignment in `init` that runs before all stored properties are set fails with `error: variable 'self.name' used before being initialized`. Drop the declaration's initial value and assign once in `init` (`state-macro.md:6-35`). Reordering the assignments compiles but is wrong: assigning to a `@State` that has an initial value doesn't take effect, so `body` sees the default (`state-macro.md:37-49`).
- **`invalid redeclaration of synthesized property '_counter'`:** another property wrapper composed with `@State` collides with the macro's backing storage. Remove the redundant wrapper or restructure (`state-macro.md:51-63`).
- **Missing private memberwise initializer:** not synthesized for a view with `@State` when all members are private. Define the initializer explicitly (`state-macro.md:65-87`).

## What pragma reproduced

On Xcode 27.2 (27B52, Swift 6.4.2, iOS 27.2 simulator SDK), 2026-11-01, with `swiftc -emit-sil` (definite-initialization errors are not reported by `-typecheck`):

- `.overlay(Color.blue.opacity(0.70).blendMode(.overlay))` fails with `ambiguous use of 'opacity'`. The trailing-closure form compiles.
- The `@State` init example fails with `variable 'self.name' used before being initialized`. Apple's fix compiles, and so do the reordered version and `_counter = State(initialValue: 42)`.
- Rendered on an iOS 27.2 simulator, `body` reads `counter` as `0` for the reordered version and `42` for Apple's fix and for `_counter = State(initialValue: 42)` with the default removed.

Not reproduced: the redeclaration and memberwise-init cases. If a build failure matches one of these patterns, apply the narrow fix, rebuild, and treat your own compiler's diagnostic as the authority over this note.
