# redman — the deep run (`/redman <path>`), read when a path is given

Ledger format: `SKILL.md` § The ledger. The run writes its own dated file beside the ledger of the lane its subject belongs to
(`<ledger folder>/REDMAN-<date>-<slug>.md`, in the project's tree): its header carries the subject's `roadmap:` line in
the first 40 lines and the pre-registered stop conditions under it; its rows use the ledger's columns, with `kind` =
the dimension and `size` left `—`. Commit only the run's file and the ledger, by name.

**Pass 0.** Name the subject in one line with its `roadmap:` row (or `none — why`). **Establish its state in the
world before any sweep** — refs, dates, tree; pending or already executed; has production moved. A plan that
already ran changes the meaning of every later item. Pick the dimension list: the project's own if `CLAUDE.md`
defines one (for example Facts · Structure · Timing · Citations · Scope · Sequence · Reconciliation), else the
default seven — Premise · Mechanism · Blast radius · Evidence · Reconciliation · Failure modes · Undecided.
Pre-register the stop conditions in the run's file (not the project ledger) before the first sweep: converged = every dimension
closed-with-evidence, nothing left for the user, last pass changed no item's state · cap = 5 passes · agents = reading
only (read-only gates and tests included), at most 3 per pass, disjoint sweeps · findings only.

**Each pass.** Walk every dimension with its own sweep (a content sweep sees nothing about structure or timing).
For each item: answer from the repo first (`path#anchor`, never a line number); from a command second (keep the
output line); only what is left is the user's, with the options explained well enough to rule on. Every item ends a
pass **closed**, **open-for-user** or **dropped** (ledger states `closed — evidence`, `open-for-user`, `dropped — why`; a refuted remedy does not refute its problem).
The session consolidates overlapping sweeps into one row per distinct item. A closed item is not reopened without
new evidence; an edit to the subject re-opens every dimension it could have touched.

**Pause, resume, stop.** A run **pauses** when the only items left are the user's, delivers the For-the-user block, and
waits. **the user's rulings resume it** from the ledger (`ruled by the user <date>: <text>`), a ruling can re-open a
dimension, and the walk continues until nothing is undecided; the pass count carries across the pause. A run
**stops** at converged, at a pass that changed nothing with nothing left for the user, or at the cap — reported as NOT
CONVERGED with the open list, never smoothed over. A new `/redman <path>` on a subject that already has a `REDMAN-*-<slug>.md` beside the ledger resumes the newest one.

**Delivery.** The For-the-user block first; the subject with every branch marked; the ledger path; the pre-registered
condition that ended or paused the run; then the eli5 receipt exactly as `~/.claude/skills/eli5/SKILL.md` defines
it, with the `Redman:` line carrying the run's state (`deep run <path>: paused, N for you` · `converged` · `NOT CONVERGED, N open`). No fix, no patch, no "while I was in there".
