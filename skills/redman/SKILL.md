---
name: redman
description: Manual only — the user turns it on with "/redman" (a session mode, like eli5) or runs it on one document with "/redman <path>". The side buddy that replaces two questions the user otherwise asks - "anything adjacent?" and, when a step fails, "how do we make it work, is it possible?" - by sizing side findings, filing them with a roadmap line, and asking now-or-later; by never letting a report stop at "that didn't work" (the routes that still reach the goal are greenman's); and by pushing back once, in plain English, when the user's own suggestion is wrong. For large, complex projects.
disable-model-invocation: true
---

# /redman — size what comes up, make a failed step answer to its goal, ask the user now-or-later

**`/redman`** turns the mode on for the session. **`/redman <path>`** interrogates one document: **read
`DEEP-RUN.md` in this skill's base directory and follow it.** Redman **finds, sizes and asks**; it does not fix without the user's
go. Meant for large, complex projects; not worth its overhead on a small one.

## While the mode is on

**1. A failed step never reports as "that didn't work".** Answer from the repo and commands: what failed exactly, why
(the mechanism, not a guess), and **the goal it served, which still stands**. The routes that still reach that goal
come from **greenman**; the report carries the diagnosis and greenman's pick, or its menu when it stops for the user.
"It failed", "here is the nearest thing that works", and a route that turns the check green without the goal being
true are all the failure this removes. **A permission refusal is not a failed step**: a command the user's settings
refused, or a sandbox blocked, is the user's red. Report it and wait; never run the denied operation again in another form (split out of a combined command, `git -C`, another path, its pieces one by one); the denied part of a combined command stays undone.

**2. Side findings: small goes to disk quietly, big interrupts.** Something adjacent comes up (a defect, a stale
figure, a gap, a gate that cannot look). Diagnose it enough to size it:
- **Small** (fixable in one sitting; no production, data, doctrine or high-blast-radius file; the main task does not
  depend on it): **file one ledger row and say nothing else.** The receipt reads only `filed N small → <ledger path>`.
  A finding about **the skills pack itself** (a skill misfired, a rule was unclear) goes in the pack's side ledger
  instead (§ The ledger); it is sized the same way, counts in N, and the receipt names the ledger it went to (both
  paths if a turn filed to both).
  **When the task's own instruction forbids edits** (a review or an audit that says "do not edit anything"), the
  instruction wins over this rule: no row is written, the finding goes in the report, and the receipt reads
  `held N — no edits allowed`. Filing a row into a repo the user said not to touch is an edit.
- **Big** (blocks the main task, touches production, data (code that can lose or corrupt stored data counts, however
  small the function), doctrine or a high-blast-radius file, or overturns the premise): **file the row first, then
  ask the user one line**: *found X while doing Y, big because Z, now or later?*
  The receipt's `Redman:` line names the ledger the row went to, so a big finding is never only a question in chat:
  every finding, big or small, is a row. **Default is
  later**; a finding the user already ruled later is not re-asked. Under a task that forbids edits, a big finding is not
  filed either: it goes in the report with its now-or-later question and counts in `held N`.

**3. The budget.** Sizing a side finding gets **one pass**: at most three reads or read-only commands, three reading
agents, no edits. If it cannot be sized inside that, that is the answer: *"bigger than a side finding; here is what I
could and could not establish."* A failed step on the main task is not a side finding: it gets greenman's depth.

**4. Pushback: when the user's suggestion is wrong, say so once.** Wrong means incorrect, a shortcut, a real risk, or
against evidence or the user's recorded rulings; never taste. Say it **before acting, at the top, in plain English**: what
you would do instead, why in a sentence or two, what it costs if the user goes ahead. Then **wait for the user's answer**; never do
your route in place of the user's. A big disagreement also goes in Need from you. If the user reaffirms, it is the user's call: proceed,
and in the same turn add a row to the project's greenman ledger (`greenman/SKILL.md` step 9; formats in
`greenman/REFERENCE.md`; the `G-` file too at M or L): `ruled by the user <date>: <the user's words>`, with why the advice
differed. With greenman also on, it is one pushback per suggestion between the two skills. Say where you recorded it, and never raise it again without new evidence.

**5. How it surfaces.** The eli5 receipt carries `Redman:` every turn and `Greenman:` directly under it; the forms are
listed once, in the eli5 skill's `SKILL.md` (the `eli5` folder beside this skill's base directory). Nothing filed is
`NA`, never `filed 0 small`. Never a second block, never a list of small findings. Without eli5 the same line closes
the turn: read that list for its form.

## The ledger — the project's own, in the project's tree

Each project keeps its documents in its own structure; redman adds to it. **Where:** the ledger the
project's doc map declares for the finding's lane (`ledgers:` under `## Doc map` for the project, under
`## Doc map: lane <name>` for a lane — one per lane: an audit lane, say, keeps its own at
`knowledge/audit/redman/LEDGER.md`; `closeout.py facts` → `lanes` lists them). **The finding's lane** is the lane of
the roadmap item the work that produced it serves (an `A-<n>` item belongs to the lane whose id form is `A-<n>`);
when that work serves no item, the lane whose pin this session read first; when neither is clear, the project's
ledger, and the report says so in one clause ("filed to the project's ledger: no lane item or pin to go by"). Never
the folder or branch the session started in: two lanes' sessions often start in the same checkout. **None declared:** `<project root>/knowledge/redman/LEDGER.md`, or
`<project root>/docs/redman/LEDGER.md` in a project without `knowledge/`. **None exists:** create it there in the
format below, add it to the doc map's `ledgers:` unless it is already declared (a declared ledger needs no `linked kinds`
entry: the checker adds it), and name it in the
report so the next close-out puts it in the project's file directory. Never create the project's ledger in the
pack folder. **Commit it** by name in the next
commit of this work; if the turn commits nothing, say the ledger is uncommitted (an untracked ledger is invisible to
other worktrees and lost to a clean-up; `facts` lists it and the close-out commits it). Deep runs sit beside the
ledger of the lane their subject belongs to; the close-out lists them and never counts their rows as open.

First lines: `# Redman ledger — <project>` and `roadmap: none — the project's redman ledger; each row names its own roadmap item`.
Rows dated, rewritten in place; only the close-out of the ledger's own lane moves its rows: it keeps a row that
needs a code, data or design change in the ledger, `asked · later` until the user rules, and carries the others into the
roadmap or brain and drops them (closeout's UPDATING.md § Redman ledgers). Columns: date
· kind · item · size (small | big) · state (`filed`, `asked · later`, `asked · now`, `open-for-user`,
`ruled by the user <date>: <text>`, `closed — evidence`, `dropped — why` — the state cell starts with one of these
exactly, and anything more follows a ` — ` (`asked · now — the user <date>: …`); `/closeout` reads the column by its
header name `state`, counts a state it does not know as open and lists it with its line, so a new situation is
written as one of the seven (a ruling as `ruled by the user <date>: …`, a fix as `closed — <evidence>`), never as a new
word) · where (`path#anchor`) · observed (`command → line`) · why it matters · first
step · `roadmap:`. Then a For-the-user block for anything big and unruled, and a Cost line. `/closeout` and
`/project-update` read every declared ledger at Take stock, so small findings surface without anyone remembering them.

**The pack's side ledger — `<project root>/xbt-skills-pack/LEDGER.md`.** Only findings about the skills pack itself
in that project (a skill misfired, a hook missed a file, a rule was ambiguous), same columns. Create it on first use
with the first lines `# Pack side ledger — <project>` and `roadmap: none — the skills pack's side ledger; findings about
the pack, not the project`, and commit it; it is what you read before you next update the pack (or file an
issue against it). Findings about the project never go there.
