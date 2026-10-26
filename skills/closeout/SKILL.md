---
name: closeout
description: Use when the user says "/closeout", "close out", "get ready to close out", "wrap up", "update the master docs", "write the handoff", or when a gap, milestone, sprint, goal or the whole project has just been finished and the project's own docs must record it so a brand-new thread can pick up cold. Works in any project folder. When nothing has just closed and the thread goes on, a full update of the docs and memory is /project-update.
---

# /closeout — record what just closed, bring the project's own docs up to date, hand off

You are in the project's own session, usually right after something finished: a gap closed, a milestone or sprint
done, a goal reached, or the whole project complete. The user's request: *update all master docs so they
are up to date, accurate, factual and trackable, and tell the next agent everything it needs to know; a handoff a
new thread can start from; an HTML overview of the project and of where we are today, with a file directory.*

**Read `UPDATING.md` in this skill's base directory first.** It holds the steps this skill shares with `/project-update`
— which doc holds which truth, the three summaries, memory, the skills' records (redman's ledgers, greenman's
decisions, the commit gate), the pin and overview formats, the audit, the commit —
and "§ X" below names a section of it. Work **in order**: first what only this session knows (it dies with the
thread), then memory, then prove what closed, then the sources of truth, then everything that repeats them, then the
summaries — so nothing is lost and no doc is ever "fixed" to match a doc that was itself stale. Every figure you
write is one you observed in this session, with its command — or is marked "not re-checked — last seen <date>".

## Steps, in order

1. **Write down what only this session knows** — § Note.
2. **Memory: read it, then update it** — § Memory. Read the entries that touch this work.
3. **Take stock** — § Take stock, including the skills' records (`facts` → `doc_map`, `lanes`, `redman.ledgers`, `greenman`,
   `pack_side_ledger`, `untracked_records`; the map's problems are reported first). Read only; nothing is written until step 6.
4. **Name what closed**, in one line: the kind (gap, milestone, sprint, goal, whole project) and its name in the
   project's own tracking (row, sprint, milestone). Take it from your note and the tracking docs. If they do not
   make it clear — including when the tracking calls the unit done but a ruling or open item of the user's still
   applies to it, or a `G-` record `awaiting the user` names it — ask the user **one** question before writing anything else, the only question this skill asks; if
   you cannot ask, record the unit as open and put the question first in the report. If
   nothing closed (a checkpoint mid-stream), say so: the steps below then record progress, not completion.
5. **Prove it.** Run the project's own tests and build, and whatever its tracking docs define as done for that
   unit (acceptance criteria, a gate, a checklist); keep the exact result lines. If any of it does not pass, the
   unit is not closed: it stays open with what is left, and the report says so first. Every uncommitted change
   this session did not make is someone else's: leave it exactly as it is, and name it as in flight — except the
   untracked ledgers and decision records `facts` lists (§ The skills' records).
6. **Update the sources of truth**, through their own procedures (§ What kind of doc is it): the closed unit flips
   to done with its evidence (commit, test result, date); counts re-derived the project's way; the next unit becomes
   the current one; what this unit decided or taught (your note) is appended to the project's logs; a ruling the user made
   is one dated line in `CLAUDE.md`; a baseline doc the project lacks is created (§ Baseline); the writes of § The
   skills' records: the commit gate installed, untracked records committed, redman rows carried and dropped (only in
   the closing lane's ledgers: another lane's wait for its own close-out), `real fix owed` records onto *Not decided —
   for the user*, decision ids onto the closed item, passed S rows dropped. **Whole project
   closed:** its status says done, with the date and what "done" covered, and the handoff becomes the
   how-to-pick-it-up-again-later document.
7. **Update everything that repeats them** — § Sources, then everything that repeats them. Where the doc map declares
   `figures:`, `closeout.py figures . --write` runs first, so every span and generated table is refilled from its source (§ Figures).
8. **Write the pin** — § Pin: the pin of the lane whose unit closed (`facts` → `pins`). `## Just closed` names this
   unit and its proof.
9. **Write the handoff**: `# <Project> — handoff (<date>)`, a `Supersedes` line if there was one, then
   *Where things stand* (one paragraph) · *Just closed, and the proof* · *Read in this order* · *Arrival block*
   (commands + what they printed today) · *What changed since the last handoff, and why* (from your note) ·
   *Where to look* (path → what it is, including which doc is the source of truth for what) · *Do not undo*
   (things learned the hard way, each with its reason, each ending with its guard in parentheses: a test name, an eval
   id or a command) · *Open items* (copied from `facts`: every open ledger row, every record awaiting a ruling or owing
   a real fix, every *Now* item, plus any procedure owed from step 6; `audit` checks the list where a handoff is
   declared) · *Start prompt* — a paste-ready block: the launch line from `CLAUDE.md`, else the project's last handoff;
   else two lines, `cd <folder>` and `claude --model <the seat's full model id>` (never `cd … &&`, never a bare
   `claude`, whose model is whatever `/model` last wrote). The arrival block never begins with `cd … &&`: the session
   is already in the folder. Then a prompt that says what to read, to run the arrival block and report the figures it
   actually sees, and what not to do without asking.
10. **Write the overview HTML** — § Overview.
11. **Audit** before anything is committed — § Audit.
12. **Commit** — § Commit, with `git commit -m "close-out: <what closed> …"`; then `closeout.py stamp <pin>` marks
    that commit (`--keep-marker` only when the pin freezes its marker).
13. **Report** in plain words, leading with what closed and its proof (or that it did not close, and what is
    left); the doc map's problems, if any; then each master doc changed and what was false in it; the memory entries added, changed or removed;
    the two commits; the audit (0 fail; N pre-existing broken, reported; each warn, whose it is and why it stays); the commit gate
    (its `install-gate` state line; ran on the last commit: yes, no or no commit); untracked records committed; redman rows carried or dropped; greenman records awaiting the user and `revisit:`
    conditions that fired; open rows in the pack's side ledger; any operation that was refused; what is in flight
    that is not yours; the user's open questions.

## Never

- Mark a unit done that did not prove done, or invent a next action — if the facts give none, the next action is
  the open question.
- Anything in § Never.
- Stop for approval. The audit is the gate; the one question is step 4's, and only when it is unclear what closed.

When `/eli5` is on, the report still ends with its receipt block — a long ceremony does not displace it.
