# xbt-skills-pack

[![tests](https://github.com/XBTDMT/xbt-skills-pack/actions/workflows/tests.yml/badge.svg)](https://github.com/XBTDMT/xbt-skills-pack/actions/workflows/tests.yml)

Skills for [Claude Code](https://claude.com/claude-code) that attack a specific failure of long AI coding sessions:
the model forgets, the plan drifts from the code, decisions vanish, and "done" gets said without evidence. The pack
makes a project keep a few plain documents, makes the model record real choices and prove claims, and checks that
every document still points at the plan — and it measures whether the model actually complies, in graded sessions
you can re-run yourself.

Free, MIT, no account, no telemetry, nothing to buy.

```sh
git clone https://github.com/XBTDMT/xbt-skills-pack.git
cd xbt-skills-pack
python3 install.py --dry-run     # shows what would change
python3 install.py               # the skills, plus the end-of-turn doc check
```

## What it is for, and what it is not for

It is for **long work in a repository**: back-end services, data and analysis code, scripts, tooling, libraries,
and the documents around them. It came out of that kind of work and that is where it has been used.

It is **not a front-end tool yet.** Nothing here knows about components, styling, accessibility or design review,
so on UI work the skills will keep your documents straight and record your decisions, but they will not help with
the interface itself. **UI/UX design skills are on the roadmap, not in this repo** — see *What is coming next*.

## The problem, stated as behaviours

Each line below is a failure someone can watch happen in a long session. Each is also a graded test case in this
repo, named in brackets.

| What goes wrong | What the pack does about it |
|---|---|
| A turn ends with "done" and no evidence for it | The doctrine and the receipt make a claim carry the command and the output it printed [`redman_eli5`, `shell_doc_eli5`] |
| The model takes the shortcut that makes a check pass — weakens an assertion, special-cases the test | greenman runs the shortcut test at a real choice and picks the long-term route; the call is written down [`pushback_ruling`, `vendored_fork`, `failed_fork`] |
| A design choice is made and the reason is gone three days later | Every real choice lands in the project's decisions folder, at a depth matched to the stakes; big ones stop and wait for you [`large_stop`, `greenman_declared_decisions`] |
| A document appears that nothing can trace back to the plan | Three layers check that every document names its roadmap item — including one written from a shell script, which no tool hook can see [`shell_doc`, `gate_block`, `gate_block_own`] |
| A permission you refused gets worked around another way | Refusals are terminal: report and stop, never retry the denied thing in another form [`refusal`] |
| The session ends and everything it knew goes with it | A pin, a handoff and an overview are written at the finish, so the next session starts from them [`closeout_records`, `project_update_records`] |
| Ceremony gets applied to a one-line rename | A small job stays small: no records, no ritual [`small_no_ceremony`] |

## What is measured

Two things, measured separately, and neither is a claim that your finished work is better.

| | |
|---|---|
| **The skills** | 16 scenarios · 29 graded sessions · 101 checks · last full run **29/29** (Fable 5.1 and Opus 5) · the 13 Opus scenarios **13/13** on Claude Opus 5.5 (22 September 2026) · four real defects caught before release |
| **The doctrine layer** *(optional)* | 9 held-out tasks · 8 configurations, Claude Opus 5.5 included · measured **with it and without it** |

Graded sessions are real Claude Code sessions in real (small) repositories, checked in code — files that must exist
and match, files that must be unchanged, what reached git, commands that must not appear. **No model judges another
model.**

![Correctness against cost for nine tasks, each configuration with the layer and without it](docs/measurements.svg)

Every configuration measured so far, Claude Opus 5.5 included, is on one chart in [`docs/all-configurations.svg`](docs/all-configurations.svg). The skills control run, absent against installed against invoked against typed, is one table in [`docs/control-run.svg`](docs/control-run.svg).

The Sonnet row is not a typo, and it is why the doctrine ships **off by default**: there the layer improved how the
session *reported* its work and made its *results* slightly worse. The Opus max row is the other warning — the
layer's standing opt-in to agents let it spawn eighteen of them and doubled the bill for one point.

![Correctness against cost for four tasks, first version of the rules against the second](docs/doctrine-v2.svg)

**The limits, plainly:** the suite shows the model *complying*, not that the work is better; the with/without
comparison covers the doctrine, not the skills; sample sizes are small; nothing here changes the model or stops
hallucination at the token level. The full method, every run, and what is still unmeasured:
**[`docs/evidence.md`](docs/evidence.md)**.

## What is coming next

- **The skills control run** — done on 22 September 2026, published whichever way it came out: on four held-out
  tasks, three repeats each, the installed skills changed no outcome and cost about a third more to carry, because
  nothing in those tasks invoked them. The same evening the other half ran, with the prompt calling the skills: the
  outcome counts still did not move, and the invoked sessions were the only ones that left a handoff, an overview and
  a decision record behind, at 12% more cost than carrying the skills unused; typed, the side-buddy skill filed a
  findings row beside every migration and once collided with a "do not edit" prompt. The numbers and what they mean are in
  [`docs/evidence.md`](docs/evidence.md#the-skills-with-and-without-the-control-run-22-september-2026).
- **UI/UX design skills** — nothing in this repo helps with interface work today. A design-side set (component and
  layout review, accessibility, design decisions recorded the way code decisions are) is planned, not started.

## How it works

![How xbt-skills-pack works: start, work, finish, and the project documents they share](docs/how-it-works.svg)

1. **Every project keeps a few plain documents, in its own folders**: a roadmap, a pin (where things stand), a
   handoff (what the next session should do), a brain file (lessons), a one-page HTML overview, plus a ledger of
   small findings and a decisions folder. Every other document names the roadmap item it serves.
2. **While you work:** greenman (always on) lays out routes that genuinely differ at a real choice, rejects the
   shortcut, picks the long-term route and records the call; `/redman` (manual, for big projects) makes a failed
   step say what failed, why, and the goal it served, and sizes side findings instead of chasing them; `/eli5`
   ends each substantial answer with a plain receipt; doc-guardrails checks new documents at the end of every
   turn, and at every commit in projects that ask for it.
3. **When something is finished, `/closeout`** proves it, updates every document, carries findings and decisions
   into the plan, checks for leaked secrets and broken links, then writes the handoff and overview.
   `/project-update` does the same tidy-up mid-project.
4. **The next session starts from the pin and the handoff.**

<details>
<summary><b>What is in the box — every skill, and what each is for</b></summary>

### What's in the box

| Piece | What it is for |
|---|---|
| `skills/greenman/` | Always on. At a real choice or a failed fix: routes that work differently, the shortcut test on each, the correct long-term route at a depth matched to the stakes, and a record in the project's decisions folder. `REFERENCE.md` holds the record formats. |
| `skills/redman/` | `/redman` (manual only): a failed step answers what failed, why and the goal it served; side findings are sized, filed to the project's ledger and asked now-or-later. `/redman <path>` interrogates one document until nothing is undecided (`DEEP-RUN.md`). |
| `skills/eli5/` | `/eli5`: every substantial answer ends with a fixed receipt (did, why, result, proof, files; redman, greenman; next, what it needs from you; the goal, then one sentence). |
| `skills/doc-guardrails/` | Everything links to the roadmap. `LINKING.md` is the contract. Three layers: `turn_check.py` (end of every turn, for the documents that session changed; installed by default), `hook.sh` (right after each Write or Edit, optional, macOS and Linux) and a per-project commit gate `/closeout` adds. Reports; never edits. |
| `skills/closeout/` | `/closeout`: prove what closed, update every doc, carry findings and decisions into the plan, write the handoff and overview, audit, commit. Holds `UPDATING.md` (the steps both doc skills share) and `closeout.py` (the deterministic helper: `facts`, `memory`, `worklist`, `tree`, `stamp`, `audit`, `link-audit`, `install-gate`). |
| `skills/project-update/` | `/project-update`: nothing has closed and work goes on; make every doc, figure, overview and memory entry match the project as it is now. |
| `skills/axiom-macos/`, `skills/axiom-swiftui/` | Apple-platform references that switch on by themselves. Trimmed copies of [charleswiltgen/axiom](https://github.com/charleswiltgen/axiom) (MIT); each folder's `SOURCE.md` lists what was trimmed. |
| `doctrine/` *(optional, off by default)* | `gate.py`, a SessionStart hook that injects the layer for the session's model: `opus-5-5-layer.md` for Claude Opus 5.5 (measured for it on 22 September 2026), `fable-layer.md` for Fable, `opus-layer.md` for anything else; `SEATS.md` on which seat to use when; `desktop-architect.md` for a Claude Desktop project. Measured above — read `docs/evidence.md` first. |
| `powershell/cc.ps1` *(optional)* | The `cc` launcher on Windows: `cc`, `cc opus`, `cc plain`. The everyday seat runs Fable 5.1 (set `$CcEngineerModel` if your plan has no Fable); the reviewer seat runs Claude Opus 5.5 at high effort. |
| `plugins.json` *(optional)* | A starting set of plugins that work well with the pack. |
| `templates/CLAUDE.md` | A starting point for your personal `~/.claude/CLAUDE.md`, written only if you don't have one. |

</details>

<details>
<summary><b>Where the skills write in your project (and the doc map)</b></summary>

### Where the skills write in your project

Into the project's own structure. Redman's ledger and greenman's decision records live where your `CLAUDE.md` says,
or in `knowledge/redman/` and `knowledge/decisions/` (or `docs/`) if it says nothing, and they are committed with
the work like any other document. The one file the pack keeps for itself is
`<project>/xbt-skills-pack/LEDGER.md`, for findings about the skills rather than about your project; the skills
create it only when there is something to put in it. A project tells the skills where things are with a short
`## Doc map` block in its `CLAUDE.md`:

```
### Doc map
roadmap: knowledge/00-ROADMAP.md
id form: bullet tags `R-<n>`
linked kinds: knowledge/*spec*.md, docs/design/*.md
ledgers: knowledge/redman/LEDGER.md
decisions: knowledge/decisions
guardrails: report
```

`guardrails: block` turns the commit gate from a warning into a refusal; `guardrails: off` switches off the three
always-on layers (`/doc-guardrails` and the close-out audit still report when you ask for them).
`skills/doc-guardrails/LINKING.md` explains every key.

</details>

<details>
<summary><b>Install: the optional pieces, Windows, the per-write hook, the commit gate</b></summary>

### Install

You need [Claude Code](https://claude.com/claude-code), Python 3.9 or newer, and git.

```sh
git clone https://github.com/XBTDMT/xbt-skills-pack.git
cd xbt-skills-pack
python3 install.py --dry-run         # shows what would change
python3 install.py                   # the skills, plus the end-of-turn doc check in settings.json
```

`python3 install.py --no-doc-check` installs the skills without touching `settings.json`. Add the optional pieces
when you want them — the doctrine only after reading its measurements above:

```sh
python3 install.py --with-doctrine   # + the doctrine hook in settings.json, and a template ~/.claude/CLAUDE.md
python3 install.py --with-plugins    # + the plugins in plugins.json
```

On **Windows**, from PowerShell (it also finds the right Python and sets up the `cc` command):

```powershell
powershell -ExecutionPolicy Bypass -File .\setup.ps1                              # the skills and the doc check
powershell -ExecutionPolicy Bypass -File .\setup.ps1 -WithDoctrine -WithPlugins   # everything
```

The installer is safe to run again. It prints every file it writes and backs up anything it replaces
(`<name>.bak-<timestamp>`). It never overwrites an existing `CLAUDE.md`, and it keeps every other setting and hook
you already have. It honours `CLAUDE_CONFIG_DIR` if you've set it: everywhere this README and the skills say
`~/.claude/…`, read that folder instead.

### The per-write hook (optional; macOS, or Linux with zsh)

The end-of-turn check already catches every unlinked document, however it was written. If you also want the warning
the moment Claude writes or edits a file, add this to `~/.claude/settings.json` (it is a zsh script, so not on
Windows):

```json
{
  "hooks": {
    "PostToolUse": [
      { "matcher": "Write|Edit",
        "hooks": [{ "type": "command", "command": "$HOME/.claude/skills/doc-guardrails/hook.sh", "timeout": 15 }] }
    ]
  }
}
```

That is the whole file if you have no `settings.json` yet (`--no-doc-check` leaves none); otherwise add the
`"PostToolUse"` entry inside the `"hooks"` block you already have.

### The commit gate (per project)

`/closeout` adds a small block to the project's git pre-commit hook that checks the staged documents. It only warns
unless the project's doc map says `guardrails: block`. To add it yourself:
`python3 ~/.claude/skills/closeout/closeout.py install-gate <project folder>`.

</details>

<details>
<summary><b>Check it worked</b></summary>

### Check it worked

- In Claude Code, type `/` and look for `closeout`, `project-update`, `doc-guardrails`, `redman` and `eli5`.
  greenman is meant to come on by itself: ask a question with a real choice in it ("which storage format should we
  use for this?") and it should lay out the routes and write a record. `/greenman` calls it by hand.
- Write an unlinked document from the shell (`echo "# spec" > knowledge/02-spec-x.md` in a project with a roadmap)
  and end the turn: the end-of-turn check should list it before the turn finishes.
- With the doctrine installed, start a new session and ask **"Quote the first line of your doctrine."** It should
  answer with *"Working doctrine (injected: this session runs …)"* or *"Working notes (…)"*, naming the model you're
  running. `DOCTRINE=off claude` (or `cc plain` on Windows) starts one session without it.

</details>

<details>
<summary><b>Re-run the measurements yourself</b></summary>

### Re-run the measurements yourself

Everything above ships in this repository, including the behaviour suite, so the numbers are yours to check
rather than to take on trust:

```sh
python3 -m unittest discover -s tests   # 97 unit tests; no API cost; CI runs them on Linux, macOS and Windows
python3 evals/run.py --dry-run          # what would run: 16 scenarios, 29 graded sessions, 101 checks
python3 evals/run.py --quick            # 11 real sessions, about $9
python3 evals/run.py                    # the full set, about $33
```

The suite needs the skills installed (it uses your real `~/.claude` setup) and a plan with both Fable 5.1 and
Opus 5.5 (`--seat opus` runs the Opus scenarios on Opus 5 instead); `--seat fable` or `--seat opus55` runs one of them. `docs/evidence.md` has the method, what each case checks,
and what the numbers do and do not show.

</details>

<details>
<summary><b>Using it day to day, and updating</b></summary>

### Using it

- Keep each project in its own folder with git. Start with a plan, not code.
- Run `/closeout` when a piece of work is finished; a fresh session then reads the pin and handoff and picks up.
- Type `/eli5` at the start of a session if you want the receipt after every answer, and `/redman` on big projects.
- When greenman stops on a big choice, answer it in plain words; it records your ruling and gets on with it.
- Run `/doc-guardrails` to see which documents aren't linked to the roadmap yet.

### Updating

```sh
cd xbt-skills-pack && git pull && python3 install.py   # add the same --with-… flags you installed with
```

</details>


## Issues welcome, pull requests not

Every file in this repository is generated from a private source repository by a build script, and the build
overwrites the whole tree. So a pull request here would be erased by the next release through no fault of yours —
you would have done the work twice and lost it. That is a bad deal, so the repo does not take them.

**Open an issue instead.** A bug, a wrong instruction, a step that does not work on your machine, a case the
behaviour suite should cover: an issue gets fixed at the source, and the fix arrives here on the next build, where
it stays fixed. If you run the graded suite and get a different result from the one on this page, that is
especially worth an issue — attach the run folder's `grades.json` and the models you used.

## License

MIT, see `LICENSE`. The two `axiom-*` skills are © Charles Wiltgen under their own MIT licence, kept in each folder.
