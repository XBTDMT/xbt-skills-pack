---
name: project-update
description: Use when the user says "/project-update", "refresh the project", "full update", "update all the docs", "bring everything up to date", "the docs are stale" or "out of date", or wants the docs, master docs, overview artifact and memory to match where the project is today — when nothing has just closed and the thread is not ending. Works in any project folder. A unit just closed, or the thread is ending, is /closeout.
---

# /project-update — every doc, the overview and memory, true to the project as it is now

Nothing has just closed and the thread goes on. The user wants everything true again: *a full update of the project —
all the docs, the master docs, the overview artifact, and memory — up to date, accurate and factual with where the
project actually is today.* Corrected in place, not appended to: *make sure all documentation is
up to date — not just added to.*

**Read `~/.claude/skills/closeout/UPDATING.md` first.** It holds the steps this skill shares with `/closeout`, and
"§ X" below names a section of it. What is different here:

| | `/closeout` | `/project-update` |
|---|---|---|
| Tracking docs | the closed unit flips to done | nothing is newly marked done; a status that contradicts a record the project already keeps is fixed as a mirror (step 7); counts re-derived; nothing allocated, reworded or reordered |
| Scope | what the closed unit changed | every live doc and every figure in it, worked from a written worklist |
| Memory | the entries that touch this work | every entry about this project, in every memory folder |
| Pin | new state; `last_marker` moves to this commit | the same — new state, marker to this pass's commit — unless the pin freezes it |
| Handoff | a new one | none (step 9) |
| Questions | one, when it is unclear what closed | none — they go in the pin's open questions |
| Redman ledgers | open rows carried into tracking and dropped | read and reported; a big unruled row in the pin's open questions; no ledger rewritten, no item allocated |
| Greenman records | ids onto the closed item; `real fix owed` onto *Not decided*; passed S rows dropped | read and reported; `awaiting the user` in the pin's open questions |
| Commit gate, untracked records | installed where missing; committed | the same (§ The skills' records) |

## Steps, in order

1. **Note** — § Note. A fresh session with nothing to note writes that and goes on.
2. **Memory** — § Memory, for every entry `memory` lists: each claim checked against disk and fixed in place or
   deleted, the index repaired, one way in. Project state that lives only in memory goes into the project's docs
   (step 7); an entry that has to point at a doc this pass creates is finished after step 8.
3. **Take stock** — § Take stock, including the skills' records. Read every commit since the pin's `last_marker`
   (`facts` → `since_marker`): that is where the docs drifted.
4. **Name this pass in one line**: "refresh as of <date> · <HEAD>". Nothing closes in a refresh. A unit that looks
   done but is not recorded as done stays open, and goes in Open questions as "looks done — `/closeout` records it".
5. **Re-derive the facts.** Run the project's tests and the build its own docs call the build (never a target that
   installs, launches or restarts a running app), and every command whose figure the docs cite; keep the exact
   result lines. A failure is a fact the docs now state, not a reason to stop. If the only build that re-derives a
   figure has a side effect the project's own docs call a hazard (a second bundle, a registered copy), run it and
   name the side effect in the report. Anything outside the repo: read-only, or "not re-checked — last seen
   <date>" (§ What kind of doc is it).
6. **Build the worklist**: `$H worklist --docs <every live doc> > <scratchpad>/worklist.md`. Live docs:
   the pin, the overview, `README.md`, `CLAUDE.md`, the tracking docs, the descriptive docs, an undated handoff,
   and any other doc whose facts can go stale. Leave out, and name in the report: append-only logs; redman's ledgers and greenman's
   decision records (the project's own; read at Take stock and reported, not rewritten here) and the pack's side
   ledger `xbt-skills-pack/LEDGER.md`;
   dated records (an audit, report, spec or old handoff that carries a date — history: at most a superseded banner
   naming what in it is now wrong), generated files (re-run their command instead). Each doc gets one item for
   reading it whole against the code and one per line that carries a figure. Close every item with an arrow and
   what you found: `→ unchanged (<command>)`, `→ now <value> (<command>)`, `→ not a figure`, `→ history, left as
   written`, `→ not re-checked — last seen <date>`. Close each item as you check it: a bulk tick of items you did
   not read is the one way this pass can lie, and no gate can catch it.
7. **Update the sources of truth, then everything that repeats them** — § Sources, then everything that repeats
   them — working down the worklist. A baseline doc the project lacks is created first (§ Baseline), except a
   handoff, which only `/closeout` writes; append a created doc's items to the worklist
   (`$H worklist --docs <it> >> <scratchpad>/worklist.md`). A tracking doc changes only through its own
   procedure: counts and pointers re-derived; a status that contradicts a record the project already keeps (a
   ledger entry, a header, a closed issue that says it shipped — the pin is a summary, not a record, so a status
   contradicted only by the pin stays open) is a mirror — fix it and cite that record; a unit with no such record
   is not marked done (step 4). Replace a false figure where it stands; a claim that was overturned rather than
   merely stale is struck and left visible with a pointer, in the project's own convention. Never add a section
   that states the new truth while the old figure still stands elsewhere.
8. **Pin** — § Pin. Rewrite State, Re-derive on arrival, In flight and Open questions; the Next action changes
   only if the facts moved it. `## Just closed` stays as the last close-out wrote it, false facts in it fixed. A
   pin you create takes the whole § Pin shape; its `## Just closed` names the last unit the docs record as closed,
   with that record, or says nothing is recorded as closed yet.
9. **Handoff: never write one** — that is `/closeout`'s, for a thread that is ending. A dated handoff is history:
   leave it. An undated one (`HANDOFF.md`) is a living doc: it is on the worklist and fixed in place. If a dated
   handoff is still where a new thread would start and it now misleads, give it a banner on its first
   line — `> Superseded in part — <what is now wrong>; current state: <pin path>.` — and nothing else; say so in
   the report.
10. **Overview** — § Overview, every figure in it on the worklist.
11. **Audit** — § Audit, with `--worklist <scratchpad>/worklist.md`.
12. **Commit** — § Commit, with `git commit -m "refresh: docs, overview and pin true as of <date>"`; then
    `$H stamp <pin>` (a pin that freezes its marker makes it refuse: then `--keep-marker`).
13. **Report** in plain words, leading with "refreshed as of <date> · <HEAD>", how many figures were wrong and how
    many other false claims you corrected, by doc; then each doc changed and what was false in it; the docs left
    off the worklist and why; the memory entries fixed, deleted or re-pointed, and the backup's path; the two
    commits; the audit (0 fail; each warn and why it stays); the commit gate (installed, already there, or no git);
    untracked records committed; open redman rows and greenman records awaiting the user; open rows in the pack's side
    ledger; any operation that was refused; what is in flight that is not yours; the user's open questions, "looks done"
    units first.

## Never

- Mark anything done, allocate, reword or reorder a tracking item, or write a handoff.
- Advance a `last_marker` the pin freezes, or edit the body of a dated record.
- Put the new truth beside a stale figure instead of correcting it.
- Stop for approval or to ask. The worklist and the audit are the gate.
- Anything in § Never.

When `/eli5` is on, the report still ends with its receipt block — a long ceremony does not displace it.
