---
name: eli5
description: Append a fixed-format ELI5 receipt block to the end of every substantial turn for the rest of the session. Use when the user says "/eli5", "eli5", "give me the eli5", "eli5 mode on", or asks for plain-language summaries after each turn. Once invoked it stays on for the whole session.
---

# /eli5 — receipt block after every substantial turn

Once this skill is invoked, **every substantial turn for the rest of the session ends with the block below**.
Not just the final turn of a task — every one. It does not expire, and no other instruction shortens it.

## Why it exists

The user reads the receipt, not the prose. The block is how the user tells a real result from a claimed one,
and how the user learns there is a decision waiting. `Proof:` is the anti-hallucination line —
it must name evidence you actually observed this session. `Result: unclear` and `Need from you:` are
the escape valves: use them instead of overstating or stalling silently.

## The block

End the turn with this, verbatim in shape. The title is a bare line reading exactly `ELI5 v5` —
no heading marks, no bold, no emoji (it stays greppable across transcripts). The number is the block's version: it
changes whenever a line is added, moved or reworded. Receipts before v3 were titled plain `ELI5`; v3 added the version,
v4 the deep-run, mixed-turn and ruling forms of the Redman and Greenman lines; v5 (2026-09-22, the user's order) groups
the lines into four parts separated by blank lines, moves Fail beside Proof and Next beside Need from you, and closes
with one sentence.

```
ELI5 v5
- Did:
- Why:
- Result: worked / failed / unclear
- Proof:
- Fail: what broke + durable fix (only when Result is failed or unclear)
- Files:

- Redman: off | NA | filed N small → <ledger path> | held N — no edits allowed | big: <finding> → <ledger path> — now or later? | deep run <path>: <state>
- Greenman: NA | decided N small → <ledger path> | G-<n> (M): <chose> over <others>, <shortcut> rejected | G-<n> (L): awaiting you — <question> | ruled by the user → <ledger path>

- Next:
- Need from you: <one question, and how it serves the goal | none>

- Goal: <macro id · micro id> — <one line> · goal changed: no | yes, <old goal → new goal>
- In one sentence:
```

The four parts read in order: what happened (Did to Files), what the watchdogs saw (Redman, Greenman), what comes
next (Next, Need from you), and the goal with the one-sentence close. The blank lines are part of the shape: they
are real empty lines, never a lone dash, which renders as an empty bullet.

## What goes on each line

One line each, ~15 words, plain English. Short but precise and useful — a line that could be
pasted into any turn's block is worthless.

- **Did** — what you actually did this turn. Concrete verb + object, not a category ("ran the 41 ingest tests", not "testing work").
- **Why** — the reason the action was needed: the defect it fixes, the goal it serves, or the mechanism it relies on.
  **"You asked" is never a Why.** the user's instruction can trail the reason in brackets when it matters ("the connector's
  coordinates were stale (and you flagged it)"), but a Why that is only "the user said so" reads as obedience, not
  understanding, and the user asked for the reason (2026-09-22). Do not restate Did.
- **Result** — exactly one of `worked` / `failed` / `unclear`. `unclear` is a real answer; prefer it over a hopeful `worked`.
- **Proof** — the specific evidence: command + the key output line, `file:line`, test counts, a URL, a screenshot path. If you did not verify it, write `none — not verified`. Never cite output you did not see.
- **Fail** — only when Result is `failed` or `unclear`: what broke, plus a *durable* fix (a mechanism, not "remember to…"). It sits directly under Proof so the failure and its evidence are read together. Omit the whole line when it worked.
- **Files** — paths created/edited/deleted this turn, comma-separated, or `none`.
- **Redman** — `off` when neither the `/redman` mode nor a deep run is active this turn; `NA` when it is on and nothing came up;
  `filed N small → <ledger path>` when only small findings were filed, N at least 1 (never list them here — they are on disk with
  enough to pick up cold); `held N — no edits allowed` when findings came up but the task forbade edits, so they are in
  the report and not in a ledger; `big: <finding> → <ledger path> — now or later?` when a big one came up: it is **filed first,
  as a row, and the line names the ledger it went to** (a big finding that reaches the user only as a question was never
  recorded; every finding, big or small, is a row), then asked (the same ask goes in Need from you), followed by
  `+N small → <ledger path>` if small ones were filed too; `deep run <path>: paused, N for
  you` · `converged` · `NOT CONVERGED, N open` for a `/redman <path>` run. A turn that filed to the project's ledger
  and the pack's side ledger names both paths. Nothing filed is `NA`, never `filed 0 small`. Always present, so a
  missing line can never be mistaken for "nothing found".
- **Greenman** — always directly under Redman, and always present (greenman is on all the time, so it has no
  `off`). `NA` when no real choice came up this turn (never `decided 0 small`); `decided N small → <ledger path>` when only small calls were
  made (never listed); `G-<n> (M): <chose> over <the others>, <shortcut> rejected` when a medium decision was made
  and acted on; `G-<n> (L): awaiting you — <the question>` when a large one stopped for the user (the same question goes
  in Need from you); `ruled by the user → <ledger path>` (with `G-<n>` at M or L) when the user overruled a pushback and
  the ruling was recorded. Several decisions in one turn: the highest-depth one, then `+N more → <ledger path>`.
- **Next** — the single next action, or `nothing — waiting on you`.
- **Need from you** — at most **one** question, or `none`. If two things need deciding, ask the one that blocks, and put the other in Next.
  **The question connects to the Goal line**: it says in a few words how answering it serves the goal ("push the chart
  fix now? — it is the last open item of R-16"). The two legitimate off-goal questions are a permission refusal and a
  big redman finding; those say plainly that they are outside the goal, so an interruption is never dressed up as
  progress.
- **Goal** — a goal lives on the roadmap, so name it: the macro id (a sprint or roadmap row, e.g. `Sprint 49` or
  `R-12`) and the micro id (the row or sub-item this turn serves), then the goal in one line, then `goal changed: no`
  unless the session's actual GOAL moved (not the code — a code change is never a goal change), in which case
  `yes, <old goal → new goal>`. `none · none` when the work serves no roadmap
  item yet, which is itself a thing to notice.
- **In one sentence** — the last line: what changed for the user, in one plain sentence a stranger could act on
  ("The public charts are fixed and pushed; nothing is waiting on you."). It is the so-what, not a second Did: it
  never repeats the Did line's verbs, and it names the state the user is now in.

## When it applies

Substantial = you ran tools, changed files, produced a finding, or answered something that changes
what the user does next.

Skip it only for: a bare acknowledgement, a clarifying question asked *before* any work, a
one-line factual answer, or a background notice (an agent, command or task finished) that you only acknowledge.
A notice you act on (you read the result, change something, report a finding) is substantial. When in doubt, include it.

**One turn can owe more than one block.** If a turn delivers several distinct summaries — one per
agent, per dimension, per phase, per document — each gets its own block, placed with the summary it
belongs to. A single block at the end of ten summaries means nine reached the user only in technical form;
that has happened and the user corrected it. A turn *about* the ELI5 rule is not exempt from the ELI5 rule.

**Subagent work counts as your work.** An agent's report is not shown to the user. When one returns, the
block covers what the agent actually found and whether you verified it — never "the agent says it
worked" as `Proof`.

**If a hook keeps the turn open** (the doc-guardrails end-of-turn check lists unlinked documents), the block goes
again at the real end of the turn, with Files and Proof brought up to date.

**If the turn ends in a question tool** (AskUserQuestion or similar), the block goes *before* the
call, not after — otherwise the turn ends with no receipt.

## Rules

- Never inflate `Result`. `worked` requires a `Proof` you actually ran this session.
- **`Proof` must carry its own limits.** State what the evidence covers *and does not*: "41 tests pass —
  on the branch, not the merge", "builds — not launched", "one stage fixed, four unchecked". A proof
  with no stated scope reads as total coverage, and that is how the expensive misses have happened.
- No hedging, no preamble, no summary of the summary. The block is the last thing in the turn — the
  one exception is a turn ending in a question tool, where it goes just before the call.
- Plain language. No jargon except the names of things the user owns.
- The block never replaces the normal answer — it follows it.
