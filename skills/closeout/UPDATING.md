# Updating a project's docs — the shared steps

Not a skill. `/closeout` and `/project-update` both follow the sections below, so the doc rules, the memory
rules, the pin and overview formats, the audit and the commit are written down once instead of twice. Each skill
says which sections it runs, in what order, and what it does differently.

The master docs are the ones **the project itself created** — its roadmap, sprint or milestone board, open items,
decision and lessons logs, architecture and file-tree docs, `README.md`, `CLAUDE.md`. Work **in order**: first what
only this session knows (it dies with the thread), then memory, then the facts, then the sources of truth, then
everything that repeats them, then the summaries — so nothing is lost and no doc is ever "fixed" to match a doc that
was itself stale. Every figure you write is one you observed in this session, with its command — or is marked "not
re-checked — last seen <date>".

Helper (stdlib Python): `closeout.py`, next to this file in `~/.claude/skills/closeout/`. `$H` below means
running it: `python3 ~/.claude/skills/closeout/closeout.py` on macOS or Linux, `python` and the full path on Windows
(PowerShell does not expand `~` in an argument to a program: write the home folder out). Commands: `facts`,
`memory`, `worklist`, `tree`, `hunks`, `stage-hunks`, `stamp`, `audit`, `link-audit`, `install-gate`; `$H -h` for usage. It writes only through
`stamp` (the pin), `stage-hunks` (the index), `link-audit` without `--no-inventory` (the UNLINKED inventory in
`knowledge/`, else `docs/`) and `install-gate` (the repository's pre-commit hook).

## What kind of doc is it — and where its truth lives

`facts` shows the project's own tracking docs, the files marked generated, the files `CLAUDE.md` says never to
hand-edit, and every `CLAUDE.md` line about which doc is canonical. The project's own rules win over this table.

| The doc is… | Its truth is… | You… |
|---|---|---|
| a tracking doc (roadmap, sprint board, milestones, open items) | the work, and the user's plan | flip status with its evidence, through the project's own procedure (its counts method, allocator, gate or tool); never rewrite, reorder or re-scope an item |
| a decision, lessons or ledger log | what happened | append; never edit a past entry — correct it the project's way (a dated `CORRECTED` note, a new entry). Redman's and greenman's ledgers are the exception: rewritten in place by their own rules (§ The skills' records) |
| owned by a tool (anything `CLAUDE.md` says never to hand-edit, or says only a command may change) | the tool | change it only through the tool |
| generated (its first lines say so, or a command builds it) | its generator | re-run the command; never hand-edit it |
| a description of the code (file tree, architecture, API list, install notes) | the code | fix the false fact where it stands, from a command you ran |
| a mirror of another doc (a status line, a counts block, an index, a summary) | the doc it repeats | fix the source first, then re-derive the mirror in the same words; never bend a source to match a mirror |
| about something outside the repo (live database, installed app, remote, issues) | that system | re-check it read-only if you can reach it (a copy of the database, a read-only query, `stat` on the installed app) — never change a live setting, database or app to take a measurement, and never a target that installs, launches or restarts one; else write "not re-checked — last seen <date>" |
| a dated record (an older handoff, a dated report, spec or audit) — unless its own header says it is a living document, which wins | the day it was written | leave it as written; at most mark it superseded, and the banner may name what in it is now known to be wrong — it is history, not a doc to bring up to date |
| Claude Code memory (`memory`) | the user, and past sessions | a lead, not authority: check it against disk before you repeat it (§ Memory) |

Two sources that disagree, where the project does not say which wins: do not pick. Put both in the user's open
questions with a recommendation. A procedure too big for this pass (a full re-count of a thousand-row board) is
listed as owed — in the project's open-items doc if it has one, else under the pin's `## In flight` — not skipped
silently. Code is not a doc here: a stale comment in the code is listed the same way, not fixed in this commit.

## The three summaries

| Doc | Who reads it | What it holds | Path when the project has none |
|---|---|---|---|
| **Pin** | the next agent, first minute | the current state at a stable path | `knowledge/00-CURRENT.md` |
| **Handoff** | a brand-new thread | everything it needs to start cold | `HANDOFF.md` |
| **Overview** (HTML) | the user | what the project is, where it stands, the file directory | `docs/project-overview.html` |

`/closeout` writes all three. `/project-update` rewrites the pin and the overview; it never writes a handoff (§ Handoff
in its skill says what it does instead).

**The project's existing files and conventions win** — their names, titles, banners and section style. If it
already has a pin, handoff or overview under another name, update that file — `facts` lists the candidates.
Never create a file beside one that does the same job (a `HANDOFF.md` next to `HANDOFF-2026-09-07.md` is a decoy a
fresh thread reads instead of the real one). A handoff inside a subfolder belongs to that lane, not the project
(`design/…/HANDOFF.md` is the canvas's): leave it to its own lane. **A dated handoff is not a pin** — its path changes
every close-out; if
the project has no pin at a stable path, create `knowledge/00-CURRENT.md` and say so in the report. If the project dates its handoffs, write a new dated file and mark the old one superseded
on its first line, in the project's own style if it has one, else `> Superseded by <new file>.`

## Baseline — the docs every project has

Every project carries these seven, whatever else it has. A pass that finds one missing creates it, in the project's
own conventions (its folders, numbering and style — `knowledge/00-…` if it has a `knowledge/`), and from facts only:
every line from disk, a command you ran, or the user's words in this session. What is not known goes in the pin's open
questions, never into a guess. `facts` → `baseline` says which exist (the brain included); `audit --baseline` warns on
any still missing.

| Doc | Holds | Path when the project has none | Created by |
|---|---|---|---|
| `README.md` | what it is; how to build, run and test it (commands you ran); where to look | `README.md` | either skill |
| `CLAUDE.md` | how Claude works here: verified build and test commands, the user's rulings as dated non-negotiables, which doc is the source of truth for what, a close-out map | `CLAUDE.md` | either skill |
| Pin | § Pin | `knowledge/00-CURRENT.md` — this path even when the project keeps its docs elsewhere: `facts` and `audit` look there. Its content and style follow the project | either skill |
| Handoff | § The three summaries | `HANDOFF.md` | `/closeout` only |
| Overview (HTML) | § Overview — with the file directory | `docs/project-overview.html` | either skill |
| Roadmap | the plan: done, now, next, not decided | `knowledge/00-ROADMAP.md` | either skill |
| Brain | lessons and decisions, dated, each with what it cost to learn | `knowledge/00-BRAIN.md` | either skill |

**Linking (`~/.claude/skills/doc-guardrails/LINKING.md`).** Every spec, audit, recon, design, template, ledger and handoff names its roadmap item
(`roadmap: <ids>` or `roadmap: none — <why>`) in its first 40 lines, and the roadmap cites documents by path plus
anchor. A project declares where those documents live in a `## Doc map` block in `CLAUDE.md`; a pass that finds no
map creates one from what it observed, `ledgers:`, `decisions:` and `guardrails: report` included, one line per key
(§ 3 of LINKING.md). Three layers catch an unlinked document as it is written: the Write|Edit hook, the end-of-turn
check and the per-project commit gate (§ The skills' records). `audit` warns while any linked-kind document is
unlinked and fails on a broken link; `closeout.py link-audit` writes the inventory a thread links from, in
`knowledge/` (else `docs/`) — a file you then list in `--changed` and commit, or delete.

**A created roadmap is the user's plan written down, not a plan you made.** *Done* lists what the commits and docs show
finished, each with its evidence; *Now* is the pin's state and in-flight work; *Next* holds only items already
written down somewhere (a pin's next action, open items, a TODO in a doc, the user's words in this session), each with
where it came from; everything else is *Not decided — for the user*. Its first line says
`Created <date> by /<skill> from <sources>; the user owns the plan.` A plan that already lives in a section of another
doc (the README, the handoff, the overview) counts: point to it rather than create a second — name it in `CLAUDE.md`'s
source-of-truth lines and in the README's map, say which one governs if the plan is split, and say in the report why
`audit --baseline` still warns.

**Project-specific docs are the thread's call** — what this project needs that no baseline doc holds: an
architecture doc for an app with several parts, a data or schema doc, a runbook for something that runs on a
schedule, release notes for a shipped app, findings for a study. Create one only when the facts to fill it exist
now — never an empty scaffold — add it to the README's map and to `CLAUDE.md`'s source-of-truth lines, and name it
in the report.

## Note — what only this session knows

Before reading anything, while it is all still in context: what
was built or changed and why, the user's rulings, what was tried and failed and why, what is half-done, what you
would tell the next thread in person. Save it as a scratch note outside the repo (your scratchpad or `$TMPDIR`):
it survives a context compaction partway through, and the logs, handoff and pin draw their *why* from it.

## Memory — read it, then update it

`$H memory` lists every memory entry about this project in every memory folder on this computer (not in
git): the folder's own memory, the home folder's — sessions started from `~` write there, and
most project memory lives in it — and any other project's that names this one. For each folder it gives the index
lines that are duplicated, missing or pointing at nothing, the "START HERE" entry points, and the paths an entry
names that are gone. Read `MEMORY.md` and the entries the skill says to read, and check each against disk before
you rely on it. Then update memory from your note, by the memory rules in your system
prompt: a preference of the user's about how to work, a lesson future sessions need, a fact the repo cannot tell
them. A ruling about this project goes in its `CLAUDE.md`; memory at most points to it.
Update the entry that already covers it rather than adding a second; fix or delete one that turned out wrong;
keep `MEMORY.md` pointing at every entry (`memory` lists entries missing from it and lines pointing at nothing).
Project state — status, counts, what is next — goes in the project's docs, never only in memory: memory is not
in git, so a cloud session or another machine never sees it, and nobody reviews it.

- **Back up first.** Memory has no history: copy each folder you are about to change into your scratchpad
  (`<scratchpad>/memory-backup-<own|home|other>/`, timestamps kept) and name the backup in the report.
- **Whose lines you may change.** In the project's own folder, any of them. In the home folder and other projects'
  folders, only the entries and index lines about this project (the ones `memory` lists) — never another project's,
  not even an obvious duplicate; name it in the report instead.
- **One way in per project.** More than one "START HERE" / `⇒` line for this project is a decoy: keep one, pointing
  at the current pin or handoff; delete a duplicated line's extra copies. None at all: add one pointer-only entry
  naming where the docs are, holding no state.
- **Check what is checkable.** An entry stating a fact about this project (a path, a count, a mechanism, what shipped)
  is checked against disk. An entry recording a preference of the user's, a lesson or a ruling is not a disk fact: confirm
  it is still the rule, and leave it.
- **The same entry in two folders.** Make the project's own copy the full one and reduce the other to a pointer at it,
  or make both say the same thing; never leave two versions that disagree.
- **A gone path is a lead, not a verdict.** An entry recording that something was removed stays as it is; an entry
  that sends a future session to a path that is gone gets fixed or deleted. A path an entry names as the one *not*
  to use is not a dead path: leave it.

## Take stock

`$H facts`: git state, the pin and how many commits it does not describe, the handoff,
overview and tracking candidates, generated and never-hand-edit files. Read
`CLAUDE.md`, `README.md`, the pin, the newest handoff, the tracking docs, the commits since the pin's marker
(or the last 20), and the commits since the newest handoff was written (`git log -1 --format=%h -- <handoff>`) —
a refresh may have moved the marker since, and a handoff's *what changed* runs from the last handoff. Tracking does
not always have its own file: a roadmap or board can be a section of the
handoff, the README or the overview — `CLAUDE.md`, the README and the handoff say where. If `CLAUDE.md` has a
close-out map, every row that fired is part of this job.


**The skills' records: read them now.** `facts` → `redman.ledgers`, `pack_side_ledger`, `untracked_records` and
`greenman` say what exists; read each ledger and record they list, as § The skills' records says. Nothing about them
is written at this step: the writes come after the unit is proved, in § Sources.

## The skills' records — redman's ledgers, greenman's decisions, the commit gate

**Where they live.** Each project keeps its documents and folders its own way, and the skills add to it. Redman's ledgers and greenman's decision records live in the project's tree: the paths its doc map
declares (`ledgers:`, `decisions:`), else `knowledge/redman/` and `knowledge/decisions/` (the `docs/` equivalents in a
project without `knowledge/`).
`<project root>/xbt-skills-pack/LEDGER.md` is only the pack's side ledger (findings about the skills themselves): read
it, carry nothing from it into the project's tracking, and name its open rows in the report, so whoever maintains
the pack sees them.

**Untracked records.** A ledger or record `facts` → `untracked_records` lists is the project's record, not another
session's stray change: this pass commits it (both skills) and names it in the report, unless a live session is still
writing it. Pass it to `tree --also` so it appears in the overview's file directory.

**The commit gate.** In a git repository, run `python3 $H install-gate <folder>` (both skills; safe to re-run: it
replaces its own block and keeps any other hook). It is report-only: it prints unlinked staged documents and lets the
commit through; `guardrails: block` in the doc map makes it refuse. It writes git's local hooks folder, not the tree —
unless `core.hooksPath` points inside the tree, which the command prints: then the hook file is a project file, named
in the report and not committed unasked. No git: skip it and say so. Report whether it was installed or already there.

**Redman ledgers.** Read every ledger `facts` → `redman.ledgers` lists (the doc map's, the defaults) and any deep-run
`REDMAN-<date>-*.md` beside them since the last marker. Every open row (`filed`, `asked · later`, `asked · now`,
`open-for-user`) is a known finding. **`/closeout`**, at § Sources: carries each `filed` or `closed — evidence` row into
the project's tracking the project's own way (an open item, a roadmap row, a brain lesson) or records why not, and
drops the row from the ledger once it lives elsewhere, so the ledger stays short. A row that is a documentation
correction (a stale figure, a wrong command in `CLAUDE.md`, a dead citation) is within the pass's remit: correct it in
place, record it, drop the row. A row that needs a code change, a data change or a design decision stays in the
ledger as it is, `asked · later` until the user rules. **`/project-update`** rewrites no ledger and allocates no item: it
names the open rows' count and every big unruled row in the report, and a big unruled row in the pin's open questions.

**Greenman decisions.** Read the project's greenman ledger (`<decisions folder>/LEDGER.md`) and every record `facts` →
`greenman.awaiting_ruling` and `real_fix_owed` lists, whatever its date (an S call has only its ledger row, which
`facts` reads too), plus every `G-*.md` written or changed since the last marker. An `awaiting the user` record goes in the
pin's open questions and the report, never decided by the pass. A `shortcut taken — real fix owed` record goes on the
roadmap's *Not decided — for the user* list with its path (`/closeout`; `/project-update` reports it) until the real fix
lands and the record is set to `decided`. A `decided` or `ruled by the user` record whose roadmap item closed in this pass
gets its id onto that item (`/closeout` only), and its lesson, if it taught one, becomes a dated brain line. Check every
record's `revisit:` condition against what the repo shows now: a condition that has fired is reported as a question
for the user, with the record's path. `/closeout` drops the ledger's S rows it has passed (they were the whole record of a
small call); M and L rows stay as the index of the `G-` files. The ledgers and decision folders appear in the
overview's file directory like any other project doc.

## Sources, then everything that repeats them

**Sources of truth first**, through their own procedures (table above): counts re-derived the project's way; a ruling the user
made is one dated line in `CLAUDE.md`; the writes § The skills' records gives each skill (the gate, untracked records
committed, ledger rows carried, decision ids onto closed items). A baseline doc the project lacks is created here (§ Baseline), the roadmap
before anything that repeats it. The skill says what else changes in them.

**Then everything that repeats them**: descriptive docs against the code, mirrors re-derived from what you
just updated, generated docs regenerated. Fix false facts where they stand; do not restyle or reorganise. If a
file you must fix carries another session's uncommitted edit, change only your lines and stage only your hunks:
`$H hunks <file>` numbers them, `$H stage-hunks <file> N [N…]` stages those.

## Pin

Under ~100 lines (depth goes in the handoff):
```
<!-- <path> — the pin. Read first. Re-derive every figure; nothing here is authority. -->
updated: <YYYY-MM-DD>
## State — <date> · <one line>          2–4 sentences: where it is, what works, what does not yet
## Just closed                           the unit, its evidence, where it is recorded as done
## Re-derive on arrival                  commands, each with the result it printed today
## In flight                             this session's unfinished work; then others' uncommitted changes
## Next action                           the next unit's first step, on the line right under the heading
## Open questions                       only what the user must decide; plain English, options, a recommendation
```
State is prose — where it is, what works, what does not; *Re-derive on arrival* is commands with the result each
printed today. A failing test belongs in both: in words under State, and as the line it printed. `stamp` adds
`last_marker:` in the commit step; leave an existing one where it is. Describe git state by work, not by
commits — this pass's own commits land after you write it ("no code changes since 2026-09-08").

**An existing pin keeps its own shape**: add what is missing above the project's newest block and restructure
nothing. Three things are not optional, because `facts` and `audit` read them: `updated: <date>` at the
start of its own line (never sharing a line with `last_marker:`), one heading whose text starts with `Next` — the
live one, above any superseded `NEXT ACTION` — with the action on the line right under it, and the state near the
top. A pin already past ~100 lines is not trimmed here: leave or fence the history where it is, and put the cut to
the user as an open question.

## Overview

An existing overview keeps its structure and styling; add what is missing. It must
have: `<meta charset="utf-8">` on the first line (WebKit reads a file without it as Latin-1), a `<title>`,
inline CSS, no script it depends on, readable at phone width; the name and one line of what it is, "as of
<date> · <commit>"; *What it is* (plain sentences, no jargon — 3 to 5 for a new overview); *Where it stands today*
(what just closed,
the pin's state and next action, same words; "Done — <date>" if the whole project closed); the observed
figures, each dated; *How it is built* (short); *File directory* — `$H tree --depth 2 --also <files
you are adding, and the untracked records this pass commits>` in a `<pre>` (past ~60 lines, re-run with `--depth 1`, which collapses each folder to a count;
never hand-trim the generated block), one-line purpose beside each main entry (tracked files only, so another
session's untracked work never lands in it); links to the pin and the handoff as relative paths, with the path also
written out, so a published copy that cannot resolve them still tells the reader where to look.

## Audit

Before anything is committed:
`$H audit --changed <every file you wrote> --pin <pin> --baseline` (a `WARN` for each baseline doc still
missing; plus `--worklist <file>` when the skill builds
one: every item still open is a `FAIL`, and so is an item ticked without saying what was found). It checks the lines
you added (a new file is
all new), and warns on a generated or never-hand-edit file you touched: each `FAIL` is fixed, each `WARN` fixed
or explained in the report. A `FAIL` on a line this pass did not write (a broken citation already in the roadmap) is
reported, not fixed: no pass edits a document to satisfy the guardrail. Then read your note, the sources, the pin, the handoff and the overview side by
side: nothing from the note lost, the same unit closed, the same next action, the same figures and dates.

The audit checks form, not truth — paths, links, charset, the pin's fields, whether the worklist is closed. Whether
a figure is right is the worklist's job: a wholesale rewrite of a doc passes the audit clean.

## Commit

**Commit by name, never `git add -A`.** Stage everything except the pin with `git add -- <files>` (and
`stage-hunks` for a file that carries another session's edit); check that `git diff --cached --name-only`
lists your files and nothing else; commit with the message the skill gives. Then stamp the pin the way the skill
says — `$H stamp <pin>` marks the commit you just made; `stamp <pin> --keep-marker` moves only the date —
`git add -- <pin>`, and commit the pin alone (`-m "<the same prefix>: pin stamped <YYYY-MM-DD>"`). If the pin says its marker
must not be advanced, `stamp` refuses: use `stamp <pin> --keep-marker` to move only the date. No git: skip
commits and say so. Push only if the project's `CLAUDE.md` says close-outs push; otherwise report the unpushed
count, or that there is no upstream to count against. A claim this pass's own commits falsify (a remote that
matched, an installed copy that was current) is written as of the date it was true, and the report says the
commits are local. If the overview is already published as a claude.ai artifact (a URL in the
pin), republish it to that same URL; never create a new published artifact unasked. If no doc in the project records it, add `<!-- published: <url> -->` to the overview so the next pass
need not look it up. An artifact can be republished
only from the login that published it: if this login's Artifact tool cannot find it, publish nothing — put a
one-line publish prompt for the owning login in the report.

## Never

- Bend a source of truth to match a doc that repeats it, hand-edit a generated file, or edit a file the project
  says only its tool may change.
- Leave project state only in memory, or repeat a memory as fact without checking it against disk.
- Stage, commit, revert or edit a change this session did not make — not even to "tidy" the commit — except the
  untracked ledgers and decision records `facts` lists (§ The skills' records).
- Run a denied or refused operation again in another form: a push the permissions deny, a commit the gate refuses
  (`--no-verify`, `git -C`, a split command, a doc map edited to get past it). Stop, and put it first in the report.
- Write a number you did not see printed today without saying when it was last seen.
