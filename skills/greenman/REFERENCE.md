# greenman — reference (read before writing a ledger row or a G record)

## The record — `<decisions folder>/G-<n>-<slug>.md` (M and L)

The decisions folder is the lane's own: the `decisions:` folder the doc map declares for the call's lane (under `## Doc
map`, or `## Doc map: lane <name>`), else `<project root>/knowledge/decisions/`
(`docs/decisions/` in a project without `knowledge/`), created there and declared as `decisions:` in the doc map
when missing. Never the pack folder.

The id is `closeout.py facts` → `lanes.<lane>.next_decision_id` (`closeout.py` is in the closeout skill's folder,
beside greenman's): `G-<n>` in the project, the lane's declared form
(`decision ids: G-A<n>`) in a lane, one past the highest number recorded in that lane's folder and ledger on any of its
branches. The ids the doc map lists under `decision ids outside sequence` (records kept from before the project's
numbering) never set it, and a citation of one still resolves in its section's folder and ledger. An id is never
reused or changed; an old record keeps its id. It is a linked document: its `roadmap:` line names the item the decision serves (or
`none — why`), and doc-guardrails checks it. Greenman never edits the roadmap; the user owns it, and `/closeout` carries
the id onto the item.

```
# G-<n> · <the choice, in a few words>
roadmap: <id> | none — <why>
status: decided | decided — delegated by the user <date> | awaiting the user | ruled by the user <date>: <the user's words> | shortcut taken — real fix owed | superseded by G-<m>
date: <YYYY-MM-DD> · depth: S | M | L

goal:      <the real goal, one line>
gates:     <what every option had to pass>
constraints: (L) | constraint (its source) | what it buys | what it costs | how it is held (a test, a hook, a check) |
options:   | option | how it works | best case | cons | shortcut test |
chose:     <option>, because <why it beats the others>
rejected:  <each shortcut and why>
assumptions: <each one the pick rests on: checked (the read or command, and what it showed) or unchecked (why not, and how the pick holds either way)>
evidence:  <tested: what was run or measured; read: what was read; argued: what neither settles, or "argued, not tested">
revisit:   <the condition that would reopen this>
```

An assumption is what would change the pick if it were false: a size, a version, a rule, what the data holds, what a
user does. Each one a read or a command can settle is checked before the pick and recorded with what it showed; one
that cannot be checked stays named, as unchecked, and the pick takes the route that holds whichever way it turns
out, or asks: naming an assumption is never a licence to bet on it (an API whose rate limit is unknown gets retries with
backoff, not the hope that the limit is generous). `none` is a real answer
only when no fact the pick rests on is in doubt.

A status can combine states (`ruled by the user …: … — shortcut taken — real fix owed`); `facts` reads it anywhere on
the line. When the user rules, rewrite `status:` and `chose:` in place, and the record's ledger row with them.

## At L: widen and refine, and the constraint table

Keep the best five; make five more from their strengths, at least two by forbidding the leader. Stop when round two
beats nothing from round one.

An L record also carries the constraints the chosen design keeps, one row each: the constraint and where it comes
from (a ruling, a spec, a law of the platform), the property it buys, what it costs, and how it is held (a test, a
hook, a check, or "not held yet"). Start from no constraints and add one at a time; a row that buys nothing is
dropped. A constraint with no source is an assumption: it moves to `assumptions:`. Two constraints that conflict
say so and how the design resolves it (by time, scope or condition). Gates say what an option must pass; the table
says what the chosen design pays for each thing it holds to.

## The ledger — `<decisions folder>/LEDGER.md` (every call)

One file per project: the index of every call, so a small "also considered" never lives only in a chat that ends.
Create it on the first call. First lines:

```
# Greenman ledger — <project>
roadmap: none — the project's greenman ledger; each row names its own roadmap item
```

Columns: date · id (`G-<n>` for M and L; `S` for a small call) · the choice · depth · status · chose · rejected, and
which were shortcuts · `roadmap:` · revisit. One row may record a run of ids together (ids quoted and never recorded,
voided as a group): its id cell is a range, `G-<a>–G-<b>`, `G-<a>-G-<b>` or `G-<a>..G-<b>`, both ends one form, and it
records every id in it, so a citation of one resolves and none is minted again. Rows dated, rewritten in place. An S row is the whole record of that
call; `/closeout` drops S rows it has passed. M and L rows stay: they index the `G-` files. `facts` reads the status
column too, so an S row marked `shortcut taken — real fix owed` still surfaces at close-out. `/closeout` reads the
ledger by its header names (`id`, `status`, `the choice`, `roadmap`): keep the header row, keep every row inside the
table (no blank or text line between rows), and a ledger without a `status` column is reported unreadable, never read
by position.

## How it mirrors redman

Both keep small things quiet and on disk and bring big things to the user now. A small *finding* (redman) is filed and
left for later; a small *call* (greenman) is made now and filed as done. A big finding is asked *now or later?*; a
big call stops for the ruling. Redman's ledger holds problems (what is wrong, where, how it was seen, a first step);
greenman's holds choices (the goal, the routes, the pick, the shortcuts turned down, when to revisit).

## Where it sits in the pack

The trackers raise the item: redman (something failed, something adjacent), doc-guardrails (something is unlinked),
the close-out audit (something is broken), or the task itself. If *what* to build is unclear, superpowers
`brainstorming` shapes it first; greenman picks *how*. A clear bug goes straight to a fix, and to greenman only if
more than one sensible fix exists or the obvious one is blocked.

## Seats

On a two-seat setup the engineer seat drafts the options at L and the reviewer seat (Opus) or the user rules; a hard call
is judged by the reviewer seat or the user. Which seat is better at what, with the measurements, is in the doctrine's
`SEATS.md`; this file restates none of them.
