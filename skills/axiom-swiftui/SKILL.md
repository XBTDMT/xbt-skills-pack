---
name: axiom-swiftui
description: "Use when building, fixing or improving SwiftUI UI on any Apple platform: views and state (@State, @Observable, MainActor and Swift 6 isolation), navigation, layout, animation, gestures, toolbars, sheets and presentations, previews, performance, and 'view not updating' debugging; includes iOS 26 and iPhone Duo references. Not for macOS signing, sandbox, windows or menus (that is axiom-macos)."
license: MIT
---

# SwiftUI

This skill covers SwiftUI work: views, state, navigation, layout, animations, architecture, gestures, and debugging.

## Quick Reference

| Symptom / Task | Reference |
|----------------|-----------|
| View not updating | See `skills/debugging.md` |
| View update still broken after debugging | See `skills/debugging-diag.md` |
| Slow previews / building good previews / `@Previewable` / `PreviewModifier` / variant matrix | See `skills/previews.md` |
| Preview API reference (`#Preview`, traits, modes, Development Assets) | See `skills/previews-ref.md` |
| Preview crashes / won't load | See `skills/debugging.md` (Preview Crashes section) |
| Navigation issues | See `skills/nav.md` |
| Navigation still broken after debugging | See `skills/nav-diag.md` |
| Navigation API reference | See `skills/nav-ref.md` |
| Layout breaks on iPad/rotation | See `skills/layout.md` |
| State lost on resize/rotation (scroll, selection, focus, drafts) | See `skills/layout.md` (State Survives the Transition) |
| Layout API reference | See `skills/layout-ref.md` |
| Performance/lag/slow scroll | See `skills/swiftui-performance.md` |
| Architecture/testability | See `skills/architecture.md` |
| `@State` object rebuilt every view init, or an `init` assignment ignored at runtime | See `skills/architecture.md` (`@State` is a macro now) |
| Animation issues | See `skills/animation-ref.md` |
| Stacks/grids/outlines | See `skills/containers-ref.md` |
| Custom containers / List replacement (iOS 18+) | See `skills/containers-ref.md` Part 7 |
| Search implementation | See `skills/search-ref.md` |
| Toolbars, ToolbarItem, sheet button placement, customization | See `skills/toolbars.md` |
| Navigation subtitle: `navigationSubtitle`, `.title` / `.subtitle` / `.largeTitle` / `.largeSubtitle` placements (iOS 26) | See `skills/toolbars.md` (Pattern 14) |
| Sheets, detents, popovers, fullScreenCover, presentation adaptation | See `skills/presentations.md` |
| Multi-column Table, sortable/resizable columns (iPad/Mac; collapses to first column in compact) | See axiom-macos (skills/swiftui-differences.md) |
| Inspector panel (`.inspector` — trailing column in regular width, sheet in compact) | See axiom-macos (skills/swiftui-differences.md) |
| Gesture conflicts | See `skills/gestures.md` |
| Section index — the vertical A–Z index strip / alphabet scrubber on a list's trailing edge, jump-to-section | See `skills/26-ref.md` (Section Index) |
| List section margins / insets around a `Section` | See `skills/26-ref.md` (Section Margins) |
| Web content — `WebView` / `WebPage`, scroll modifiers, WebView-in-NavigationStack | See `skills/26-ref.md` (WebView & WebPage) |
| iPhone Duo / foldable iPhone: poses, vertical bars, the fold, arrangements, hinge, scene accessories (SwiftUI and UIKit) | See `skills/iphone-duo.md` |
| iOS 26 features | See `skills/26-ref.md` |
| Architecture audit of a codebase | Follow `skills/swiftui-architecture-auditor.md` inline |
| Performance scan of a codebase | Follow `skills/swiftui-performance-analyzer.md` inline |
| Navigation audit of a codebase | Follow `skills/swiftui-nav-auditor.md` inline |
| Layout audit of a codebase | Follow `skills/swiftui-layout-auditor.md` inline |
| UX dead ends, dismiss traps, flow audit | Follow `skills/ux-flow-auditor.md` inline |
| TextKit scan (fallback triggers, glyph APIs, Writing Tools wiring) | Follow `skills/textkit-auditor.md` inline |

## Decision Tree

```dot
digraph swiftui {
    start [label="SwiftUI issue" shape=ellipse];
    what [label="What's wrong?" shape=diamond];

    start -> what;
    what -> "skills/debugging.md" [label="view not updating"];
    what -> "skills/nav.md" [label="navigation"];
    what -> "skills/swiftui-performance.md" [label="slow/lag"];
    what -> "skills/layout.md" [label="adaptive layout"];
    what -> "skills/containers-ref.md" [label="stacks/grids/outlines"];
    what -> "skills/architecture.md" [label="feature architecture"];
    what -> "skills/animation-ref.md" [label="animations"];
    what -> "skills/gestures.md" [label="gestures"];
    what -> "skills/search-ref.md" [label="search"];
    what -> "skills/toolbars.md" [label="toolbars / sheet buttons"];
    what -> "skills/presentations.md" [label="sheets/detents/popovers"];
    what -> "skills/26-ref.md" [label="iOS 26 features"];
    what -> "skills/iphone-duo.md" [label="iPhone Duo / foldable"];
    what -> "skills/previews.md" [label="slow previews / building good previews"];
    what -> "skills/previews-ref.md" [label="preview API reference"];
    what -> "skills/debugging.md" [label="preview crashes / won't load"];
    what -> "axiom-macos (skills/swiftui-differences.md)" [label="Table / .inspector\n(large-window density)"];
}
```

## Anti-Rationalization

| Thought | Reality |
|---------|---------|
| "Simple SwiftUI layout, no need" | SwiftUI layout has 12 gotchas. `skills/layout.md` covers all of them. |
| "I know how NavigationStack works" | Navigation has state restoration, deep linking, and identity traps. `skills/nav.md` prevents 2-hour debugging. |
| "It's just a view not updating" | View update failures have 4 root causes. `skills/debugging.md` diagnoses in 5 min. |
| "I'll just add .animation()" | Animation issues compound. `skills/animation-ref.md` has the correct patterns. |
| "No architecture needed" | Even small features benefit from separation. `skills/architecture.md` prevents refactoring debt. |
| "I know .searchable" | Search has 6 gotchas. `skills/search-ref.md` covers all of them. |
| "I'll just add a Done button" | Sheets without Cancel break the HIG (updated 2026-03-24). `.cancellationAction` / `.confirmationAction` produce HIG-correct placement automatically — `skills/toolbars.md` Pattern 2 has the rules. |
| "A sheet is a sheet, nothing to configure" | Detents, compact adaptation, background interaction, and iOS 18 sizing decide how it behaves across window shapes. `skills/presentations.md` covers the adaptation traps (landscape sheets silently become full-screen covers). |
| "Previews are slow forever, I'll just use the simulator" | Five concrete fixes in `skills/previews.md`. Rule 4 (auto-refresh off) is 30 seconds and often halves perceived slowness. |
| "`@State` is lazy in Xcode 27, I read the release notes" | Only when the property is `private`/`fileprivate`. `skills/architecture.md` has the gate, the three TN3211 breaks, and the one that compiles and is wrong at runtime. |
| "I'll just write a wrapper view for `@State` in this preview" | `@Previewable @State` (Xcode 16+) eliminates that boilerplate. `skills/previews-ref.md` has the macro signature. |
| "Duo is just a bigger iPhone; my layout already resizes" | Bars move to the side, the fold divides the screen, and the outer display can't open windows. `skills/iphone-duo.md` covers what resizing alone misses. |
