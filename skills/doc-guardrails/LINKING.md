# LINKING — the guardrails that make every document trackable

The intent: *every project always has a roadmap and a CLAUDE.md; outside those each project designs
its own files and folders; but within that design there must be a linking mechanism that connects everything —
specs, audits, recon, designs — to the roadmap.* This document is that mechanism. It constrains **links**, never
the tree. `closeout.py link-audit` checks it and only reports; the commit gate (§ 6) refuses a commit only in a
project whose doc map says `guardrails: block`.

## 0 · The guaranteed set, and going-forward only

Every project carries **three** documents whatever else it has: the **brain** (lessons and decisions, e.g.
`knowledge/00-PROJECT-BRAIN.md` or `knowledge/00-BRAIN.md`), **`CLAUDE.md`**, and the **roadmap** — part of
§ Baseline's seven in `closeout/UPDATING.md`. Everything else is the project's own design, declared in its doc map.

**The guardrail is live and constant; it is not "go fix stuff".** Linking is enforced going
forward only. `link-audit` never edits a document. Run without `--no-inventory`, `--file` or `--staged`, it writes
**one inventory file**, `knowledge/UNLINKED-<date>.md` (or `docs/` when there is no `knowledge/`: the backlog is the
project's own work, so it sits in the project's tree), listing every unlinked document with its first heading, its
date and its folder — the worklist a thread links from, in that thread or another. Each run replaces any earlier
inventory with today's, and removes it
once nothing is unlinked. `audit` warns while documents are unlinked and fails only on a **broken** link.

## 1 · The spine is the roadmap, and its items have ids

The roadmap (`knowledge/00-PROJECT-ROADMAP.md` or `knowledge/00-ROADMAP.md`, per § Baseline in `UPDATING.md`) is the
one place work is planned and marked done. **Every roadmap item that a document can serve has a stable id.** The id
form is the project's own and is declared in its doc map (§ 3): a table roadmap uses its rows `| 731 |`; a prose
roadmap tags its bullets `R-12` (**a prose roadmap adopts `R-<n>` tags, added to an item the first time a
close-out or a linked document touches it; never retrofitted in bulk**). An id never changes and is never reused; a renumbering is an event that carries a
mapping.

## 2 · Every linked document names its roadmap item — and the roadmap names it back

**Forward link.** Every document of a *linked kind* — a spec, an audit, a recon or research document, a design, a
review, a redman ledger, a greenman decision record, a handoff for a unit of work — carries, in its first 40 lines, exactly one of:

    roadmap: 731
    roadmap: 731, 789
    roadmap: none — <why this document serves no roadmap item>

Two or more ids mean the document serves each. `none` with a reason is a first-class answer (the aim is
*0% unknown, not 100% linked*); `none` without a reason is not. An id that is not in the roadmap is a **broken**
link.

**Back link.** A roadmap item that cites a document does so as **path plus anchor**, never a line number:
`knowledge/02-SPEC-<x>.md#§4.2` or `docs/design/<y>.md#Layout`. The anchor is a literal string (a heading, a `§` label)
that must still be present in the file; the checker opens the file and searches it as a fixed string. A line number
rots silently the next time anyone edits above it; an anchor that is gone is caught (in one project, well under
half the line-number citations were still accurate).

**The asymmetry: absence is REPORTED; a broken link FAILS.**
A document with no `roadmap:` line is a gap to fill; a `roadmap:` naming a row that does not exist, or a citation
whose anchor is gone, is a false statement in the record.

## 3 · The doc map — one block that tells the checker where to look

Each project declares its own design once, in `CLAUDE.md`, under a heading `## Doc map` (if `CLAUDE.md` has none,
`knowledge/00-CURRENT.md` is read). **One line per key, `key: value`, the value running to the end of the line; no
comments on the line and no continuation lines** — the checker reads a key's line and nothing else:

    ## Doc map
    roadmap: knowledge/00-PROJECT-ROADMAP.md
    id form: table rows `| <n> |`
    linked kinds: knowledge/02-SPEC-*.md, knowledge/*audit*.md, knowledge/recon-*/**/*.md, docs/design/*.md, knowledge/00-HANDOFF-*.md
    exempt: knowledge/00-CURRENT.md, HANDOFF.md, README.md
    ledgers: knowledge/redman/LEDGER.md, knowledge/audit/redman/LEDGER.md
    decisions: knowledge/decisions
    guardrails: report

The keys: `roadmap` is the spine; `id form` is `table rows | <n> |` or `bullet tags R-<n>`; `linked kinds` are the
globs of documents that must link; `exempt` are baseline docs that are not units of work; `ledgers` are redman's
ledgers, one per lane; `decisions` is greenman's records folder; `guardrails` is `off`, `report` (the default) or
`block` (the commit gate refuses). Every place `ledgers:` and `decisions:` name is checked as a linked kind even when
`linked kinds` does not list it: a ledger, the deep runs beside it, and every file in the decisions folder.

Without a doc map the checker uses defaults (§ 5) and reports that the map is missing. **The map declares; it does
not constrain.** A project may keep specs anywhere; it just says where.

## 4 · What the checker does — `closeout.py link-audit [folder] [--strict] [--no-inventory]`

1. Reads the doc map (or defaults); names the roadmap and the id form it found.
2. Collects the roadmap's ids; for a prose roadmap, counts items (bullets, numbered lines) that carry no id.
3. For every file matching a linked kind: finds the `roadmap:` line in the first 40 lines →
   `LINKED` (all ids exist) · `NONE` (with reason) · `MISSING` (no line — reported) · `BROKEN` (an id not in the
   roadmap, or `none` without a reason — fails under `--strict`).
4. For every `path#anchor` and every backticked or linked path in the roadmap: `OK` · `NO FILE` · `NO ANCHOR`
   (both broken).
5. Prints one line per finding and a summary; exit 0 in report mode, 1 under `--strict` when anything is broken.

Two narrower forms: `--file <path>` judges one file (the Write|Edit hook's form; exit 2 when it is unlinked or
broken) and `--staged` judges the staged copy of each staged document (the commit gate's form; exit 1 only under
`guardrails: block`). It never edits a document. The close-out `audit` already fails on a broken link and warns on a
missing one; failing on missing lines too is the user's ruling, not taken. Per project, `guardrails: block` makes the
commit gate refuse.

## 5 · Defaults when a project has no doc map

roadmap: the first of `knowledge/00-PROJECT-ROADMAP.md`, `knowledge/00-ROADMAP.md`, `ROADMAP.md`. id form:
auto — table rows `| <n> |` if any exist, else bullet tags `R-<n>`. linked kinds: `knowledge/*spec*.md`,
`knowledge/*SPEC*.md`, `knowledge/*audit*.md`, `knowledge/*AUDIT*.md`, `knowledge/recon*/**/*.md`,
`knowledge/*design*.md`, `docs/design/*.md`, `docs/superpowers/specs/*.md`, `knowledge/redman/*.md`, `docs/redman/*.md`,
`knowledge/decisions/*.md`, `docs/decisions/*.md`, `knowledge/00-HANDOFF-*.md`, `docs/handoff/*HANDOFF*.md`,
`xbt-skills-pack/**/*.md` (the pack's side ledger). ledgers: `knowledge/redman/` or `docs/redman/`. decisions:
`knowledge/decisions/` or `docs/decisions/`. guardrails: report. exempt: `README.md`, `CLAUDE.md`, `HANDOFF.md`,
`knowledge/00-CURRENT.md`, `docs/project-overview.html`, `knowledge/00-ROADMAP.md`, `knowledge/00-PROJECT-ROADMAP.md`.

## 6 · Where the pieces live

- This contract: `~/.claude/skills/doc-guardrails/LINKING.md`, read by `/doc-guardrails`, `/closeout` and `/project-update`.
- The always-on parts, three layers, all silent in a project with no roadmap and all switched
  off by `guardrails: off` in the doc map (the on-demand `link-audit` and the close-out `audit` still report):
  1. `doc-guardrails/hook.sh`, a PostToolUse hook on Write and Edit: the one file just written, at once.
  2. `doc-guardrails/turn_check.py`, a Stop hook: every linked-kind document this session changed since its previous
     turn (the first turn looks back an hour): written with Write or Edit, or changed while one of its own tool calls
     ran (a shell command, a Python script, a subagent), in every repository it wrote to; another session's edits are
     left alone. It keeps the turn open once with the list, never twice.
  3. The commit gate, `closeout.py install-gate` → `link-audit --staged` in the repository's pre-commit hook: the
     staged copy of every staged document, whatever wrote it, on any machine where the gate is installed and the pack
     is at `~/.claude/skills` (or `CLOSEOUT_PY` points at `closeout.py`); where it is not, the gate does nothing,
     silently. Report-only by default; `guardrails: block` refuses the commit. This is the lasting guarantee; the two
     hooks are the early warning.
- The checker: `closeout.py link-audit`.
- The stub for a new project: the first `/closeout` creates the doc map when it creates the baseline docs
  (§ Baseline) and installs the commit gate, report-only — day one has no ritual; the first close-out is the first
  thing that touches the docs.
- Existing projects get their doc map by hand, once, after reading the report.
