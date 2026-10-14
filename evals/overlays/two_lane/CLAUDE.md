# shelfkit — Claude Code working memory

A tiny inventory library. Tests: `python3 -m unittest discover -s tests`.

## Non-negotiables
- Out-of-stock items (qty 0) are always reported; items not counted yet (qty None) never are.

## Doc map
roadmap: knowledge/00-ROADMAP.md
id form: bullet tags `R-<n>`
linked kinds: knowledge/*spec*.md, knowledge/redman/*.md, knowledge/decisions/*.md, xbt-skills-pack/**/*.md
ledgers: knowledge/redman/LEDGER.md
decisions: knowledge/decisions
exempt: README.md, CLAUDE.md

## Doc map: lane audit
roadmap: knowledge/audit/AUDIT-ROADMAP.md
id form: table rows | A-<n> |
pin: knowledge/audit/00-AUDIT-CURRENT.md
linked kinds: knowledge/audit/**/*.md
ledgers: knowledge/audit/redman/LEDGER.md
decisions: knowledge/audit/greenman
decision ids: G-A<n>
