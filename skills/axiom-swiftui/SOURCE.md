# Source

- Upstream: https://github.com/charleswiltgen/axiom
- Path: `.claude-plugin/plugins/axiom/skills/axiom-swiftui/`
- Commit: 8f27104744cb4bc587ed5171208bab2d09b216b6
- Copied: 2026-09-14
- License: MIT (see LICENSE, copied from the upstream repo root)

## What was trimmed and why

The upstream skill is one suite of a ~20-suite plugin with its own slash commands, agents and pack
binaries. Only this suite (plus its sibling, axiom-macos) was copied, so every route to something
that does not exist here was removed or reworded to plain language. Everything else is verbatim.

- SKILL.md: removed "Non-SwiftUI UI Routes", "Conflict Resolution", "Automated Scanning", the
  AXIOM_AUDITOR_INLINE auto-generated block, the "Skills:" footer, all `/axiom:` commands and
  "launch <x> agent" instructions, and the `axiom-*` edges in the decision tree. The auditors are now
  routed from the Quick Reference table straight to their .md files ("Follow ... inline"). Rewrote
  `description` and replaced the "You MUST use this skill" opener with a plain sentence.
- Deleted `skills/hot-reload.md` (needs the pack's binaries and `xclog`) and its references in
  SKILL.md and `skills/previews.md`.
- `skills/debugging.md`: the `/axiom:screenshot` and `/axiom:test-simulator` steps in the simulator
  verification workflow were replaced with the equivalent `xcrun simctl io booted screenshot` calls;
  the manual steps are unchanged.
- Auditor files (`*-auditor.md`, `swiftui-performance-analyzer.md`): removed the "Claude Code —
  launch the agent / run /axiom:audit" line and the generated-file comment; "`x` agent" references
  now point at the local `skills/x.md`; "compound with" bullets naming auditors that live in other
  suites (concurrency, memory, accessibility, storage, energy, resize, swift-performance) were dropped.
- In the remaining `skills/*.md`: removed "Related Skills" bullets and "Skills:" footer entries that
  pointed at other axiom suites (axiom-uikit, axiom-design, axiom-build, axiom-concurrency,
  axiom-accessibility, axiom-data, axiom-testing, axiom-swift, axiom-performance, axiom-media,
  axiom-games, axiom-integration); reworded in-prose mentions to plain language.
  iOS material (26-ref, iphone-duo, nav files, auditors) and cross-references to axiom-macos kept.
