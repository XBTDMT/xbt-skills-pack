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
once nothing is unlinked. The inventory is a snapshot from the commit it names; it is not rewritten by
`--no-inventory` runs. `audit` warns while documents are unlinked and fails only on a **broken** link.

## 1 · The spine is the roadmap, and its items have ids

The roadmap (`knowledge/00-PROJECT-ROADMAP.md` or `knowledge/00-ROADMAP.md`, per § Baseline in `UPDATING.md`) is the
one place work is planned and marked done. **Every roadmap item that a document can serve has a stable id.** The id
form is the project's own and is declared in its doc map (§ 3): a table roadmap uses its rows `| 731 |`; a prose
roadmap tags its bullets `R-12` (**a prose roadmap adopts `R-<n>` tags, added to an item the first time a
close-out or a linked document touches it; never retrofitted in bulk**). An id never changes and is never reused; a renumbering is an event that carries a
mapping.

**Lanes.** A project may run more than one plan at once — a second team, an audit lane, a long side project — each
with its own roadmap. Each is a *lane*, declared in the doc map (§ 3); the project's own block is the lane called
`main`. Each lane has one roadmap, and its ids carry a prefix no other lane uses (`A-14` in an audit lane beside the
project's rows `| 731 |`), so an id says which roadmap it is in. Everything this document says about the roadmap
holds for each lane's roadmap. A lane's pin, ledgers and decisions folder are its own, and its decision records take
its own id form (`G-A<n>`), so two lanes never file under one id. A project with no lane section has one lane, and
nothing in it changes.

**Ids with dots.** An id may carry dots (`148.6`, `R-1.23`): it is an id in its own right, never its parent's.

## 2 · Every linked document names its roadmap item — and the roadmap names it back

**Forward link.** Every document of a *linked kind* — a spec, an audit, a recon or research document, a design, a
review, a redman ledger, a greenman decision record, a handoff for a unit of work — carries, in its first 40 lines, exactly one of:

    roadmap: 731
    roadmap: 731, 789
    roadmap: A-14 (the audit lane's item); project row 986
    roadmap: none — <why this document serves no roadmap item>

Two or more ids mean the document serves each. `none` with a reason is a first-class answer (the aim is
*0% unknown, not 100% linked*); `none` without a reason is not. An id that is not in the roadmap is a **broken**
link.

**Where the line may sit.** It counts when `roadmap:` is the first thing on its line (after a list marker, a `>` or
bold markers: `**roadmap:** 731`), or when it starts a sentence or a field inside a line (after `.`, `!`, `?`, `·` or
`|` and a space: `**Sprint:** 36 · **ROADMAP:** refines row 129`); a bold label `**ROADMAP:**` counts anywhere on its
line. A heading line never counts, so a title such as `# AUDIT ROADMAP: the side lane` is not a declaration, and a
sentence that only contains the word (`… not on this roadmap: …`) is not one either. The first line that counts, in
the first 40 lines, is the document's.

**Which ids a value names.** An id with a lane's prefix (`A-14`, `R-3`) counts wherever it appears in the value. A
bare number counts only as a list at the start of the value (`731, 789 — …`) or right after the word *row* or *rows*
(`refines row 129`), and belongs to the lane whose id form has no prefix; so a date, a commit or a figure in the
explanation is never taken for an id. A bare number that follows a prefixed id in the leading id list (`A-53, 99999`)
is therefore not read, and the document gets a `NOTE` naming it (write it first or as `row 99999`) in the per-file
check, the sweep and `facts`. A prefix no lane declares is not an id. A range (`A-1…A-50`, `rows 247–249`)
names its two ends, and both must exist; the rows between are not checked. A value that names ids in two lanes is
linked when every id is in its own lane's roadmap.

**Back link.** A roadmap item that cites a document does so as **path plus anchor**, never a line number:
`knowledge/02-SPEC-<x>.md#§4.2` or `docs/design/<y>.md#Layout`. The anchor is a literal string (a heading, a `§` label)
that must still be present in the file; the checker opens the file and searches it as a fixed string. A line number
rots silently the next time anyone edits above it; an anchor that is gone is caught (in one project, well under
half the line-number citations were still accurate).

**The asymmetry: absence is REPORTED; a broken link FAILS.**
A document with no `roadmap:` line is a gap to fill; a `roadmap:` naming a row that does not exist, or a citation
whose anchor is gone, is a false statement in the record. A citation struck through (`~~…~~`) is withdrawn history
and is not checked.

## 3 · The doc map — one block that tells the checker where to look

Each project declares its own design once, in `CLAUDE.md`, under a level-two heading `## Doc map`; if `CLAUDE.md` has
none, `knowledge/00-CURRENT.md` is read. The heading may carry a suffix after a separator (`—`, `–`, `-`, `:`, `(` or
`·`): `## Doc map — where the documents live` is read. A lane is a later section headed exactly
`## Doc map: lane <name>` (lower-case letters, digits and hyphens; `main` is the project block's name). Any other
heading that names a doc map (`## The doc map`, `### Doc map`) is reported as not read.

Inside a block, paragraphs of prose are the project's own words and are not read. A paragraph that holds a known key
is a key paragraph: every line in it is `key: value` — one key per line, the value running to the end of the line, no
comments and no continuation lines — and a line that is not is reported.

    ## Doc map
    roadmap: knowledge/00-PROJECT-ROADMAP.md
    id form: table rows | <n> |
    pin: knowledge/00-CURRENT.md
    linked kinds: knowledge/02-SPEC-*.md, knowledge/*audit*.md, knowledge/recon-*/**/*.md, docs/design/*.md, knowledge/00-HANDOFF-*.md
    exempt: HANDOFF.md, README.md
    ledgers: knowledge/redman/LEDGER.md
    decisions: knowledge/decisions
    guardrails: report

    ## Doc map: lane audit
    roadmap: knowledge/audit/AUDIT-ROADMAP.md
    id form: table rows | A-<n> |
    pin: knowledge/audit/00-AUDIT-CURRENT.md
    branch: audit-*
    linked kinds: knowledge/audit/**/*.md
    ledgers: knowledge/audit/redman/LEDGER.md
    decisions: knowledge/audit/greenman
    decision ids: G-A<n>

The keys (the checker's own table; a test holds the two equal):

| key | where | takes | when it is wrong |
|---|---|---|---|
| `roadmap` | project; lane (required) | one file | two paths: reported, checked against the first; a missing file: reported, the defaults looked for (project), or the lane's ids not checked in this checkout (lane) |
| `id form` | project; lane (required) | `table rows \| <P><n> \|` or `bullet tags <P><n>`, P an optional prefix of one to four letters and a hyphen (`A-`); bullet tags without one mean `R-` | reported; the project's form is detected from its roadmap (rows if it has any); a lane without one is not read |
| `linked kinds` | project; lane | path patterns | an entry that is not a pattern: reported, and the key's defaults are used (§ 5; a lane adds none); an entry that matches no file: a warning |
| `exempt` | project | path patterns | as `linked kinds` |
| `ledgers` | project; lane | files or folders | an entry that is not a path: reported; one not created yet: a note |
| `decisions` | project; lane | one folder | a file: reported, the defaults used; two: reported, the first used; not created yet: a note |
| `guardrails` | project | `off`, `report` or `block` | reported; `report` used |
| `gate` | project | `config-hook` | reported; not read |
| `pin` | project; lane | one file | not in this checkout: a warning |
| `brain`, `overview` | project | one file | not in this checkout: a warning |
| `figures` | project | one file: a JSON file of figure sources, checked by `closeout.py figures` | not in this checkout: a warning |
| `handoff` | project | one file: the handoff whose *Open items* `audit` checks, when it is not `HANDOFF.md` at the root | not in this checkout: a warning |
| `branch` | lane | a pattern for the lane's branch names (`audit-*`) | reported |
| `decision ids` | lane | `G-<letters><n>`; default `G-` and the first letter of the lane's id prefix | reported |
| `decision ids outside sequence` | project; lane | decision ids and ranges (`G-<n>, G-<m>`, `G-<a>..G-<b>`): ids the project or lane recorded outside its numbering | an entry that is not an id or a range: reported, and none is used |

`gate: config-hook` matters only in a repository whose `core.hooksPath` is a folder the project tracks: there the
commit gate is registered only when the map says so (§ 6).

**Decision ids outside the sequence.** The next decision id of a section is one past the highest id recorded in its
own form (`G-<n>`, or the lane's `decision ids`) in its decisions folder and ledger; the ids `decision ids outside
sequence` lists never set it, but they stay records: a citation of one resolves in that section's folder and ledger
like any other, and fails when it has no record there. In a lane, the bare `G-<n>` ids it recorded before it took its
own form still set its floor, so a new id never repeats a number the lane has used. A citation resolves in the lane of
the document that cites it, so the project's `G-<n>` and a lane's `G-<n>` of the same number are different decisions.

A key given twice, or a key the table does not have, is reported, with the nearest known key (`excluded` points to
`exempt`). A problem never makes the checker guess: the key takes its default, and the report says which.

**Path patterns** mean the same on every Python and every platform: `*`, `?` and `[…]` stay within one folder; `**`
as a whole path component is any number of folders, none included, and a trailing `**` is every file below; `**`
inside a component (`knowledge/audit**`) is reported. Patterns use `/`. Letter case follows the platform (Windows
ignores it).

Every place a lane's `ledgers:` and `decisions:` name is checked as a linked kind even when `linked kinds` does not
list it: a ledger, the deep runs beside it, and every file in the decisions folder. Every lane's roadmap and pin are
never judged as documents.

**A document's lane** is the lane whose linked kind matches its path most specifically, else the project block; it
matters only for a record that serves no item and for an old bare decision id a lane document cites — linking never
needs it, because ids carry their lane. **A session's lane** is the lane of the roadmap item its work serves; when
the work serves no item, the lane whose pin the session read first; when that is not clear, the project block, and
the session says so. No tool infers a lane from the folder a session started in or from its branch: two lanes'
sessions often start in the same checkout. Commands take a path, or `--lane <name>`.

Without a doc map the checker uses the defaults (§ 5) and reports that the map is missing. **The map declares; it
does not constrain.** A project may keep specs anywhere; it just says where.

## 4 · What the checker does — `closeout.py link-audit [folder] [--strict] [--no-inventory]`

1. Reads the doc map (or the defaults) and validates it (§ 3): each problem is printed first, as a `CONFIG` line
   (`ERROR`, `WARN`) or a `NOTE`; then each lane's roadmap, id form and number of ids.
2. Collects each lane's roadmap ids; for a prose roadmap, counts items (bullets, numbered lines) that carry no id.
3. For every file matching a linked kind of any lane: finds the `roadmap:` line (§ 2) →
   `LINKED` (every id is in its lane's roadmap) · `NONE` (with reason) · `MISSING` (no line — reported) · `BROKEN` (an
   id not in its lane's roadmap, a value that names no id, or `none` without a reason — fails under `--strict`).
4. For every `path#anchor` and every backticked or linked path in each lane's roadmap that is not struck through:
   `OK` · `NO FILE` · `NO ANCHOR` (both broken); a `NO FILE` that exists on a lane's branch says so.
5. Prints one line per finding and a summary with a `CONFIG` count; exit 0 in report mode, 1 under `--strict` when
   anything is broken or the map has an `ERROR`.

`--lane <name>` judges only that lane's documents and records (ids are still resolved in every lane).
`closeout.py doc-map [folder] [--lane <name>]` prints the map as the checker resolved it — where it was read, each
lane's roadmap, id form, ids, pin, branch, ledgers, decisions folder and next decision id, every pattern with the
number of files it matches — and every problem; exit 1 on an `ERROR`.

Two narrower forms: `--file <path>` judges one file (the Write|Edit hook's form; exit 2 when it is unlinked or
broken) and `--staged` judges the staged copy of each staged document (the commit gate's form; exit 1 only under
`guardrails: block`). Neither says more about the map than this: `--file` on the file that holds the doc map, when
the map has errors, lists them and exits 2 (the session just wrote them); `--staged` prints one line when the map has
errors and never refuses a commit over the map, `guardrails: block` included. It never edits a document. The close-out `audit` fails on a broken link this pass wrote, reports one already there as
`PRE-EXISTING` (never edited to pass), warns on a missing one, and names a staged file this pass did not write,
leaving it staged; failing on missing lines too is the user's ruling, not taken. Per project, `guardrails: block` makes the
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
Patterns follow § 3. pin: `knowledge/00-CURRENT.md` when it exists. A project with no lane section is one lane,
`main`; its decision ids are `G-<n>`. **Only ledger files count open rows**: a declared
ledger file, `LEDGER.md` in a declared ledger folder, or `LEDGER.md` in a default folder; a deep run beside a ledger
(`REDMAN-<date>-*.md`) is listed with its own last state and never counted. A ledger is read by its header row —
redman's `state` column, greenman's `id` and `status` columns (names compared without case, `*`, backticks or a
trailing colon); a ledger without the column is reported as unreadable, never read by position, and rows after a break
continue the table above them, with a note. A state outside the vocabulary counts as open and is listed with its file
and line (redman: `filed`, `asked · later`, `asked · now`, `open-for-user`, `ruled by the user <date>: …`, `closed — …`,
`dropped — …`; greenman: `decided`, `awaiting the user`, `ruled by the user <date>: …`, `shortcut taken — real fix owed`,
`superseded by …`).

## 6 · Where the pieces live

- This contract: `LINKING.md` in the doc-guardrails skill's folder, read by `/doc-guardrails`, `/closeout` and `/project-update`.
- The always-on parts, three layers, all switched off by `guardrails: off` in the doc map (the
  on-demand `link-audit` and the close-out `audit` still report):
  1. `doc-guardrails/hook.sh`, a PostToolUse hook on Write and Edit: the one file just written, at once.
  2. `doc-guardrails/turn_check.py`, a Stop hook: a changed document of a linked kind is this session's when the
     session is shown to have written, named or committed it this turn; nothing else is. Shown means written with its
     write tools, named in the input or output of one of its own tool calls (a shell command, a script it wrote, a
     subagent), or in a commit it made this turn. Naming counts only in the session's own roots, the repository of
     its cwd and that repository's worktrees; in any other project a document is the session's only when its write
     tools wrote it or its own `git commit` committed it. A document another session wrote with its write
     tools stays theirs. It no longer catches an unnamed script's output that the session neither names nor commits,
     nor a shell write into another project: the commit gate judges those when anyone commits them, and the audit's
     sweep lists them. It keeps
     the turn open once with the list of the documents it wrote, named or committed, never twice.
  3. The commit gate, `closeout.py install-gate` → `link-audit --staged`: a git config hook (`hook.doc-guardrails.*`,
     git 2.54+) registered by `/closeout`, run by git before any hook file in every worktree; where git is older,
     first in a hook file the pack owns; nowhere else. It finds `closeout.py` at run time (`CLOSEOUT_PY`, else
     `$CLAUDE_CONFIG_DIR` or `~/.claude` `/skills/closeout/closeout.py`) and says so when it cannot run; it never
     refuses a report-only commit, whatever version of the pack it finds. A repository declines it with git's own
     `hook.doc-guardrails.enabled=false`, which `/closeout` honours and never changes. A repository whose
     `core.hooksPath` is a folder it tracks manages its own hooks: `/closeout` registers nothing there, and says so,
     until its doc map carries `gate: config-hook`, which only the project writes. Every run leaves a heartbeat:
     `facts` says whether it ran on the last commit. It judges the staged copy of every staged document, whatever
     wrote it. Report-only by default; `guardrails: block` refuses the commit. This is the lasting guarantee; the two
     hooks are the early warning.

  **What each layer does with a doc map that has problems.** No layer checks less than it says: each judges with the
  map's valid parts and the defaults for the rest, and tells the person who can fix the map in its own channel.
  `link-audit` and `doc-map` print every problem first; `facts` carries them (`doc_map.problems`), and the close-out and
  project-update report them first at Take stock; `audit` counts each error as a warning. The Write|Edit hook reports
  them only when the file just written is the one that holds the map. The commit gate prints one line and never
  refuses a commit over the map, `guardrails: block` included. A map problem is never a reason to edit a document
  someone else owns, `CLAUDE.md` included.

  **The only silent conditions** are three: no roadmap anywhere (a baseline gap `/closeout` names), `guardrails: off`
  (the always-on layers only), and a document that is not of a linked kind. Outside a git repository the project root
  is the nearest folder above the document that holds `CLAUDE.md` or a roadmap, so the layers are not silent there
  either. Nothing is keyed on the folder a session started in or on its branch.
- The checker: `closeout.py link-audit`.
- The stub for a new project: the first `/closeout` creates the doc map when it creates the baseline docs
  (§ Baseline) and registers the commit gate, report-only — day one has no ritual; the first close-out is the first
  thing that touches the docs.
- Existing projects get their doc map by hand, once, after reading the report.
