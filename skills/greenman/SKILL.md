---
name: greenman
description: Use whenever a real choice comes up, or a fix fails or is ruled out by a constraint (never a permission refusal) and the goal still stands - before a design, architecture, data model, storage format, library or fix is picked; when starting a roadmap item whose route is not chosen ("let's start on R-2"), including mid-brainstorm when a turn would stop for the user with competing routes on the table; when the user proposes a route, asks for what looks like a shortcut (change a test to match the code, silence a failure) or reaffirms after pushback; or on "/greenman", "options for", "what's the best way", "is this a shortcut". Lays out routes that genuinely differ, runs the shortcut test, picks the long-term route at a depth matched to the stakes, acts or asks by that depth, and records the call. Redman's partner - redman keeps watch, greenman makes the call.
---

# /greenman — find the route that really reaches the goal, and say why

**Green means the goal is really reached, not that a check turned green.** Redman keeps watch; greenman makes the
call. It is **on all the time**, with or without redman. Invoke it as a skill: options laid out in
chat without its record leave the call untracked.

## When it runs

On a choice that is **costly to undo, or one a reasonable person would argue with**: before building; when a fix
failed or the obvious fix is ruled out by a constraint (a vendored, generated or do-not-edit file) and the goal still
stands ("what routes still reach the goal?", not "can this step work?"); when the user proposes a route on such a choice;
when asked. **Not** on a permission refusal or sandbox block: that is the user's red, not a problem to route around. Report
it and wait; never run the denied operation again in another form (split out of a combined command, `git -C`, another path, its pieces one by one); the denied part of a combined command stays undone. Not on taste (a name, a wording: the user's), or
on a choice with one sensible answer (do it). Brainstorming settles *what*; greenman settles *how*. They are not
turns apart: a turn that ends asking the user to pick between routes has made a call awaiting the user, so it is recorded in
that turn (an L record `awaiting the user`), even while brainstorming is still settling the what. Options that exist only
in chat are lost when the session ends.

## The run

1. **The real goal.** One line: what must be true when done. Is it the goal, or a stand-in ("tests pass", "CI is
   green", "the error is gone")? Judge everything against the real goal; against a stand-in the shortcut wins.
2. **The depth, from the stakes.**

   | depth | when | options | acts alone? | record |
   |---|---|---|---|---|
   | **S** | small, reversible, one place | the obvious one + *also considered X, not taken because Y* | yes, silently | ledger row |
   | **M** | a problem with more than one sensible fix; reversible; in scope | **3** that work differently | yes if reversible and in scope, showing the menu; else ask | row + `G-` file |
   | **L** | design, architecture, a contract or API, how data is stored (a file format, a schema: files written in it outlive the code), production, hard to undo | **5–10**, refined to 5 | **no**: stop, recommend, wait for the user | row + `G-` file |

   Unclear depth: take the deeper. Never shallower than the user asked. **Depth describes stakes, not permission**: an L
   call the user plainly delegated ("decide how, and build it") may be acted on, stays L, and is recorded
   `decided — delegated by the user <date>`. Never lower a depth to justify acting.
3. **Gates, then trade-offs.** Before writing options, name the gates (reaches the real goal; not a silent shortcut;
   within constraints, permissions and the user's rulings) and the trade-offs survivors are compared on (long-term cost,
   reversibility, blast radius, time). Gates remove; trade-offs are compared in words. No scores.
4. **Options that differ in how they work.** At M and L also list *do nothing / defer* and *change the requirement*.
   Any route the user gave is on the list. Each option gets its best case (who would pick it, and why); one
   nobody would pick is dropped. Fewer real routes than the number: say so; never pad.
5. **The shortcut test, on each.** A shortcut makes the check pass without the goal being true: weakening, skipping or
   deleting a test; mocking the hard part; special-casing the failing input; swallowing an error; widening a timeout
   or tolerance; quietly narrowing scope. The correct route fixes the problem where it starts and still holds as the
   project grows. A labelled shortcut can be right, never a silent one: its record says
   `shortcut taken — real fix owed` until the real fix lands.
6. **Widen and refine (L).** Keep the best five; make five more from their strengths, at least two by forbidding the
   leader. Stop when round two beats nothing from round one.
7. **Evidence where cheap:** a prototype, a failing test, a measurement. Name the source of any claim about a
   library, SDK or platform, or say it is unverified.
8. **Recommend in plain English** (the route, why it beats the others, what it costs), then act or ask by depth. At L
   the message ends with the menu and one question.
9. **Record, in the project's own tree.** Every call is a row in the project's greenman ledger; M and L also write a
   `G-<n>-<slug>.md` beside it. **Where:** the folder the doc map declares (`decisions: <path>`), else
   `<project root>/knowledge/decisions/` (`docs/decisions/` in a project without `knowledge/`). **None exists:** create
   it there, add `decisions: <path>` to the doc map and say so. Never in the pack folder. Commit records by name with
   the work they decided; if the turn commits nothing, say in the report that the record is uncommitted. **Read
   `~/.claude/skills/greenman/REFERENCE.md` before writing either**: formats, statuses, columns. Never edit the roadmap.

## Pushback — when the user's route loses

When the user's route is wrong (incorrect, a shortcut, a real risk, or against evidence or the user's recorded rulings; never
taste): say so **before acting, at the top, in plain English**: what you would do instead, why in a sentence or two,
and what it costs if the user goes ahead. **Once.** Then **wait for the user's answer**; never carry out your route in place
of the user's. If the user reaffirms, it is the user's call: do it, and **in the same turn** add a ledger row
`ruled by the user <date>: <the user's words>` with what you recommended and why (and the `G-` file at M or L), and say where
you recorded it. Do not raise it again without new evidence. With redman on, it is one pushback per suggestion
between the two skills, and greenman's ledger holds the ruling.

## How it surfaces

The eli5 receipt's `Greenman:` line, directly under `Redman:` (format in `eli5/SKILL.md`): `NA`;
`decided N small → <ledger path>`; `G-<n> (M): <chose> over <others>, <shortcut> rejected`; `G-<n> (L): awaiting
you — <question>`, with the question also in Need from you; `ruled by the user → <ledger path>` (with `G-<n>` at M or L)
after a pushback the user overruled. Several calls in one turn: the highest-depth one, then `+N more → <ledger path>`. No
call made is `NA`, never `decided 0 small`. Without eli5 the same line closes the turn. S calls are never listed in
chat.

## Never

Take a shortcut silently. Treat a refusal as a problem to solve, or run a denied operation again in any form. List an option only to lose. Pad to a number. Score
options. Edit the roadmap. Reopen a ruled decision without new evidence. Debate taste.
