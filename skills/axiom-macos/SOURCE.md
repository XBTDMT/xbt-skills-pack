# Source

- Upstream: https://github.com/charleswiltgen/axiom
- Path: `.claude-plugin/plugins/axiom/skills/axiom-macos/`
- Commit: 8f27104744cb4bc587ed5171208bab2d09b216b6
- Copied: 2026-09-14
- License: MIT (see LICENSE, copied from the upstream repo root)

## What was trimmed and why

The upstream skill is one suite of a ~20-suite plugin with its own slash commands, agents and pack
binaries. Only this suite (plus its sibling, axiom-swiftui) was copied, so every route to something
that does not exist here was removed or reworded to plain language. Everything else is verbatim.

- SKILL.md: removed the "Cross-Suite Routes" and "Conflict Resolution" sections, the `axiom-*` edges
  in the decision tree, the "Skills:" footer, the Apple Pay row (points at axiom-payments), and the
  ReplayKit note (points at axiom-media). Rewrote `description` and replaced the "You MUST use this
  skill" opener with a plain sentence.
- Deleted `skills/screencapturekit.md`, `skills/screencapturekit-ref.md`, `skills/ios-apps-on-mac.md`
  (out of scope) and their rows/links in SKILL.md.
- In the remaining `skills/*.md`: removed "Related Skills" bullets and "Skills:" footer entries that
  pointed at other axiom suites (axiom-uikit, axiom-security, axiom-shipping, axiom-design,
  axiom-integration); reworded in-prose mentions of those suites to plain language.
  Cross-references to axiom-swiftui were kept because that skill is present.
