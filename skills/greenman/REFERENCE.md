# greenman — reference (read before writing a ledger row or a G record)

## The record — `<decisions folder>/G-<n>-<slug>.md` (M and L)

The decisions folder is the project's own: the doc map's `decisions:` path, else `<project root>/knowledge/decisions/`
(`docs/decisions/` in a project without `knowledge/`), created there and declared as `decisions:` in the doc map
when missing. Never the pack folder.

`<n>` is the next free number. It is a linked document: its `roadmap:` line names the item the decision serves (or
`none — why`), and doc-guardrails checks it. Greenman never edits the roadmap; the user owns it, and `/closeout` carries
the id onto the item.

```
# G-<n> · <the choice, in a few words>
roadmap: <id> | none — <why>
status: decided | decided — delegated by the user <date> | awaiting the user | ruled by the user <date>: <the user's words> | shortcut taken — real fix owed | superseded by G-<m>
date: <YYYY-MM-DD> · depth: S | M | L

goal:      <the real goal, one line>
gates:     <what every option had to pass>
options:   | option | how it works | best case | cons | shortcut test |
chose:     <option>, because <why it beats the others>
rejected:  <each shortcut and why>
evidence:  <what was run or read, or "argued, not tested">
revisit:   <the condition that would reopen this>
```

A status can combine states (`ruled by the user …: … — shortcut taken — real fix owed`); `facts` reads it anywhere on
the line. When the user rules, rewrite `status:` and `chose:` in place, and the record's ledger row with them.

## The ledger — `<decisions folder>/LEDGER.md` (every call)

One file per project: the index of every call, so a small "also considered" never lives only in a chat that ends.
Create it on the first call. First lines:

```
# Greenman ledger — <project>
roadmap: none — the project's greenman ledger; each row names its own roadmap item
```

Columns: date · id (`G-<n>` for M and L; `S` for a small call) · the choice · depth · status · chose · rejected, and
which were shortcuts · `roadmap:` · revisit. Rows dated, rewritten in place. An S row is the whole record of that
call; `/closeout` drops S rows it has passed. M and L rows stay: they index the `G-` files. `facts` reads the status
column too, so an S row marked `shortcut taken — real fix owed` still surfaces at close-out.

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

On a two-seat setup the engineer seat drafts the options at L and the reviewer seat (Opus) or the user rules. In the
author's own review runs the reviewer seat was the more reliable at catching every planted defect, so until the
engineer seat holds on real reviews, a hard call is judged by Opus or the user.
