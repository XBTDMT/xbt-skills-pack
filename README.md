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

It is **not a web or front-end design tool yet.** Nothing here knows about web components, styling, accessibility
or design review, so on that work the skills will keep your documents straight and record your decisions, but they
will not help with the interface itself. The one exception is Apple platforms: the two `axiom-*` references switch
on for SwiftUI and macOS work (state, layout, animation, windows, menus) — implementation guidance, not design
review. **UI/UX design skills are on the roadmap, not in this repo** — see *What is coming next*.

## The problem, stated as behaviours

Each line below is a failure someone can watch happen in a long session. Each is also a graded test case in this
repo, named in brackets.

| What goes wrong | What the pack does about it |
|---|---|
| A turn ends with "done" and no evidence for it | The doctrine and the receipt make a claim carry its proof, and the receipt check holds a turn whose Proof line is empty [`redman_eli5`, `shell_doc_eli5`] |
| The model takes the shortcut that makes a check pass — weakens an assertion, special-cases the test | greenman runs the shortcut test at a real choice and picks the long-term route; the call is written down [`pushback_ruling`, `vendored_fork`, `failed_fork`] |
| A design choice is made and the reason is gone three days later | Every real choice lands in the project's decisions folder, at a depth matched to the stakes; big ones stop and wait for you [`large_stop`, `greenman_declared_decisions`] |
| A document appears that nothing can trace back to the plan | Three layers check that every document names its roadmap item — including one written from a shell script, which no tool hook can see [`shell_doc`, `gate_block`, `gate_block_own`] |
| A permission you refused gets worked around another way | Refusals are terminal: report and stop, never retry the denied thing in another form [`refusal`] |
| The session ends and everything it knew goes with it | A pin and a handoff are written at the finish (the close-out also writes an overview), so the next session starts from them [`closeout_records`, `project_update_records`] |
| Ceremony gets applied to a one-line rename | A small job stays small: no records, no ritual [`small_no_ceremony`] |

## What is measured

Two things, measured separately, and neither is a claim that your finished work is better.

| | |
|---|---|
| **The skills** | <!-- fig:eval.scenarios -->27<!-- /fig --> scenarios · <!-- fig:eval.sessions -->49<!-- /fig --> graded sessions · <!-- fig:eval.checks -->202<!-- /fig --> checks · last full run **<!-- fig:run.full.result -->49/49<!-- /fig -->** (<!-- fig:run.full.date -->2026-10-11<!-- /fig -->; Fable 5.1 and Claude Opus 5.5) · behaviour failures in <!-- fig:eval.early.pairs -->4<!-- /fig --> scenario-and-seat pairs caught by the full and quick runs before the first public release in mid-September, sessions given another model's doctrine page left out (the targeted reruns of those days failed <!-- fig:eval.early.targeted_pairs -->2<!-- /fig --> more), named on the evidence page, and every failure of a full, quick or seat run since in the evidence page's run table |
| **The doctrine layer** *(optional)* | the nine-task set: <!-- fig:doctrine.configurations -->10<!-- /fig --> configurations (a model at one effort), each run **with a doctrine page and without** (its own page; for Sonnet 5 the Opus 5 page the loader gives it; for Sonnet 5.5 the Opus 5.5 page of 29 September, which the loader does not give it), <!-- fig:doctrine.overview_runs -->27<!-- /fig --> runs on the correctness-and-cost chart and <!-- fig:doctrine.nine.runs_total -->53<!-- /fig --> nine-task runs in all · the held-out set: eight tasks no page was written from, <!-- fig:doctrine.heldout_runs -->15<!-- /fig --> runs: the 29 September and 7 October page revisions each against the page it replaced, Opus 5.5 medium on the 29 September page with no control run, and Sonnet 5.5 with the Opus 5.5 page and without one |

Graded sessions are real Claude Code sessions in real (small) repositories, checked in code — files that must exist
and match, files that must be unchanged, what reached git, commands that must not appear. **In the graded sessions no
model judges another model.** The doctrine benchmark below is scored by script too, with two exceptions. The prose
answers of three held-out tasks were scored by a blind judge: an agent that read each task's answer key and the answers
shuffled under labels, with the key from label to run held apart (one of those tasks' three checks, on the repository,
stayed the script's). Once an answer's first line named its model and doctrine state; the judge set it aside, and
later packets strip that line. And a smaller Sonnet 5 sample of 3 September, quoted below, had a model judge score the
shape of its prose; no judge enters the nine-task correctness figures.

<a href="docs/measurements.svg"><picture><source media="(max-width: 1279px)" srcset="docs/measurements-narrow.svg"><img src="docs/measurements.svg" alt="Does a doctrine page help? Ten configurations, nine tasks each, run with a page and without one"></picture></a>

The pages were written from the misses on the nine-task set, so it is not held out. Its runs, Claude Opus 5.5 and Sonnet 5.5 included, are on the evidence page in three charts
([correctness and cost](docs/all-configurations-overview.svg), [the cheap end](docs/all-configurations-zoom.svg),
[each page revision against the page it would replace](docs/all-configurations-revisions.svg)); the held-out set is
[`docs/heldout.svg`](docs/heldout.svg). The skills control run, absent against present against invoked against
typed, is one table in [`docs/control-run.svg`](docs/control-run.svg).

Three setups on the measurements chart ran twice, and each gap there uses the run shown: Opus 5.5 high with a page
<!-- fig:doctrine.QT.exact -->97.6<!-- /fig --> (<!-- fig:doctrine.QT2.exact -->96.4<!-- /fig --> later the same day), and Sonnet 5.5 high and max without a page <!-- fig:doctrine.SNQB.exact -->92.4<!-- /fig --> and <!-- fig:doctrine.SNZB.exact -->88.7<!-- /fig -->, from their
second pass, of <!-- fig:doctrine.when.SNZB -->5 October<!-- /fig -->, run side by side with the Opus 5.5 page of 29 September (their first passes without a
page, over <!-- fig:doctrine.when.SNZ -->30 September and 5 October<!-- /fig -->, scored <!-- fig:doctrine.SNQ.exact -->93.4<!-- /fig --> and <!-- fig:doctrine.SNZ.exact -->92.9<!-- /fig -->). So Sonnet 5.5 max's <!-- fig:doctrine.gap.SNZT_SNZB -->+8.0<!-- /fig --> is
<!-- fig:doctrine.SNZB.score -->89<!-- /fig --> against <!-- fig:doctrine.SNZT.score -->97<!-- /fig --> in that second pass (<!-- fig:doctrine.verdict.SNZT_SNZB -->better<!-- /fig -->).

*Noted 28 September 2026: every Claude Opus 5.5 session of 22 September, without a page and with each page, had the plugins and skills
synced from the author's claude.ai account present, 254 skills listed in all, 7 of them this pack's; the configurations
measured on <!-- fig:doctrine.when.first -->2–3 September<!-- /fig --> listed 41, none of them this pack's or the account's synced ones. Each with-and-without pair ran in one environment;
[`docs/evidence.md`](docs/evidence.md) has the details.*

The Sonnet 5 row is not a typo, and it is why the doctrine ships **off by default**: in a smaller sample the page
improved how Sonnet 5 *reported* its work (a model judge scored the prose <!-- fig:doctrine.sonnet_sample.prose_bare -->3.5<!-- /fig --> of 10 without it and <!-- fig:doctrine.sonnet_sample.prose_page -->6.7<!-- /fig --> with
it), while on the nine tasks its *results* went the wrong way, <!-- fig:doctrine.S.score -->90<!-- /fig --> without a page against <!-- fig:doctrine.T.score -->87<!-- /fig --> with it
(<!-- fig:doctrine.gap.T_S -->−2.8<!-- /fig -->: <!-- fig:doctrine.verdict.T_S -->level within noise<!-- /fig -->). The Opus 5 max row is the other warning — the page's standing opt-in to agents let
Opus 5 at max effort spawn <!-- fig:doctrine.K.agents -->18<!-- /fig --> of them, and the bill went from <!-- fig:doctrine.L.cost -->$37<!-- /fig --> to <!-- fig:doctrine.K.cost -->$84<!-- /fig --> (<!-- fig:doctrine.ratio.K_L.cost -->2.3<!-- /fig --> times) for <!-- fig:doctrine.L.score -->91<!-- /fig --> → <!-- fig:doctrine.K.score -->94<!-- /fig -->
(<!-- fig:doctrine.gap.K_L -->+3.1<!-- /fig -->: <!-- fig:doctrine.verdict.K_L -->better, just past the 3-point rule<!-- /fig -->). On the nine-task set the same setup run twice side by side moved by up
to <!-- fig:doctrine.nine.repeat_spread -->2.7<!-- /fig --> points, so a gap of <!-- fig:doctrine.nine.rule -->3<!-- /fig --> or less there is level within noise; on the held-out set the rule, set before the
run, is <!-- fig:doctrine.held.rule -->5<!-- /fig --> points.

Since 7 October the pages that ship are the 7 October pages for Fable 5.1 and Claude Opus 5.5 and the 15 September page for Opus 5,
Sonnet and Haiku; the copies in `doctrine/` add one clause to each, making superpowers' brainstorming rule apply only
where that plugin is installed. The Opus 5 page's copy also changes five more sentences: its standing opt-in to agents
reads "Installing this file is the opt-in to multi-agent work"; the sentence that named the author says "the user";
superpowers' "invoke a skill before any response" rule is said to hold "where it applies"; the sentence on the enabled
plugins adds "if any" and leaves out the plugins the author switched off; and the next one looks for the plugin catalog
under `CLAUDE_CONFIG_DIR` when that is set. On the held-out set Fable's 7 October page scored <!-- fig:doctrine.HX5.score -->94<!-- /fig --> against <!-- fig:doctrine.HX5C.score -->98<!-- /fig --> for the 29 September page it
replaced (<!-- fig:doctrine.held.gap.fable_oct7 -->−4.2<!-- /fig -->: <!-- fig:doctrine.held.verdict.fable_oct7 -->level within noise<!-- /fig -->), and Opus 5.5's <!-- fig:doctrine.HQT4.score -->98<!-- /fig --> against <!-- fig:doctrine.HQT4C.score -->98<!-- /fig --> (<!-- fig:doctrine.held.gap.opus55_oct7 -->±0.0<!-- /fig -->: <!-- fig:doctrine.held.verdict.opus55_oct7 -->level within noise<!-- /fig -->), by the <!-- fig:doctrine.held.rule -->5<!-- /fig -->-point rule.

<a href="docs/doctrine-v2.svg"><picture><source media="(max-width: 1279px)" srcset="docs/doctrine-v2-narrow.svg"><img src="docs/doctrine-v2.svg" alt="What the 14 September pages changed against the 13 September pages: four tasks, one session each"></picture></a>

**The limits, plainly:** the suite shows the model *complying*, not that the work is better; the with/without
comparison covers the doctrine, not the skills; sample sizes are small; nothing here changes the model or stops
hallucination at the token level. The full method, every run, and what is still unmeasured:
**[`docs/evidence.md`](docs/evidence.md)**.

## What is coming next

- **The skills control run** — done on 22 September 2026, published whichever way it came out: on four tasks of
  the nine-task set, three repeats each, the skills, present but not invoked (nothing in those tasks invoked them),
  moved one outcome count, by one session and the wrong way: 2 of 3 present sessions found all five of Review's
  planted defects, against 3 of 3 absent; the other pass counts were level, Wide audit found 36 · 34 · 39 files
  against 37 · 32 · 38, and carrying them cost <!-- fig:doctrine.ratio.control.present_absent -->1.32<!-- /fig --> times as much.
  The same evening the other half ran, with the prompt calling the skills: the outcome counts matched the present
  arm's; the invoked sessions were the only ones that left a handoff and an
  overview behind, and they and the sessions with `/redman` typed the only ones that left a decision record after
  every Oracle migrate, the invoked ones at <!-- fig:doctrine.ratio.control.invoked_present -->1.12<!-- /fig --> times the cost of carrying the skills unused; typed, the side-buddy skill
  filed a findings row beside every Oracle migrate, and on Wide audit met a "do not edit" prompt in all three sessions:
  two held their findings in the report, one filed a row and failed. The numbers and what they mean are in
  [`docs/evidence.md`](docs/evidence.md#the-skills-with-and-without-the-control-run-22-september-2026).
  *Corrected 28 September 2026: the present and invoked sessions of that run also listed the skills synced from the
  author's claude.ai account, 254 skills in all, 7 of them this pack's, so that extra cost measured carrying all of
  them, not this pack's skills alone.*
- **UI/UX design skills** — nothing in this repo helps with web or front-end design yet (the Apple references help
  with SwiftUI and macOS implementation, not design). A design-side set (component and
  layout review, accessibility, design decisions recorded the way code decisions are) is planned, not started.

## How it works

<a href="docs/how-it-works.svg"><picture><source media="(max-width: 1279px)" srcset="docs/how-it-works-narrow.svg"><img src="docs/how-it-works.svg" alt="How xbt-skills-pack works: start, work, finish, and the project documents they share"></picture></a>

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
| `skills/doc-guardrails/` | Everything links to the roadmap. `LINKING.md` is the contract. Three layers: `turn_check.py` (end of every turn, for the documents that session wrote, named or committed; installed by default), `hook.sh` (right after each Write or Edit, optional, macOS and Linux) and a per-project commit gate `/closeout` registers, which catches the rest. Reports; never edits. |
| `skills/closeout/` | `/closeout`: prove what closed, update every doc, carry findings and decisions into the plan, write the handoff and overview, audit, commit. Holds `UPDATING.md` (the steps both doc skills share) and `closeout.py` (the deterministic helper: `facts`, `memory`, `worklist`, `tree`, `stamp`, `audit`, `link-audit`, `install-gate`). |
| `skills/project-update/` | `/project-update`: nothing has closed and work goes on; make every doc, figure, overview and memory entry match the project as it is now. |
| `skills/axiom-macos/`, `skills/axiom-swiftui/` | Apple-platform references that switch on by themselves. Trimmed copies of [charleswiltgen/axiom](https://github.com/charleswiltgen/axiom) (MIT); each folder's `SOURCE.md` lists what was trimmed. `axiom-graphics`, which one `axiom-macos` page refers to, is not included. |
| `doctrine/` *(optional, off by default)* | `gate.py`, a SessionStart hook that injects the layer its model table names for the session's model (`python gate.py --table` prints the table): `opus-5-5-layer.md` for Claude Opus 5.5 (written for it on 22 September 2026; the copy here is its 7 October page, with one clause added: superpowers' brainstorming rule applies only where that plugin is installed), `fable-layer.md` for Fable, `opus-layer.md` for Opus 5, Sonnet and Haiku; a newer Opus gets the 5.5 layer; a model it cannot detect gets the 5.5 layer, and the layer's header says so. `gate.py --status` shows which layer each recent session really got; `gate.py --doctor` checks the table; `brief.py`, a SubagentStart hook that gives every agent the key rules in `key-rules.md` (evidence for every claim, a permission denial is a stop, no edits where the task forbids them) as it starts, whether the Agent tool, a workflow or a skill started it; `SEATS.md` on which seat to use when; `desktop-architect.md` for a Claude Desktop project. Measured above — read `docs/evidence.md` first. |
| `powershell/cc.ps1` *(optional)* | The `cc` launcher on Windows: `cc [opus\|fable\|sonnet\|skills\|default] [1\|2]`, then anything for claude itself. `cc` is the everyday seat, Claude Opus 5.5 at medium effort (set `$CcEngineerModel` to run it on another model); `cc opus` is the reviewer, Claude Opus 5.5 at high effort; `cc fable` is Fable 5.1 at low effort, on Fable's own usage window; `cc sonnet` is the helper, Sonnet 5.5 at high effort with the skills and no working notes; `cc skills` is Claude Code's default model with the skills and no doctrine; `cc default` is stock Claude Code (`--safe-mode`). The seats that name a model pin it to an exact id; `cc skills` runs Claude Code's default, and `$CcEngineerModel` puts the engineer seat on any model you name. `2` runs a second account: it sets `CLAUDE_CONFIG_DIR` to the folder you name as `$CcSecondaryDir` in your profile. `doctrine/SEATS.md` says which seat to use when. |
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
## Doc map
roadmap: knowledge/00-ROADMAP.md
id form: bullet tags `R-<n>`
linked kinds: knowledge/*spec*.md, docs/design/*.md
ledgers: knowledge/redman/LEDGER.md
decisions: knowledge/decisions
guardrails: report
```

`guardrails: block` turns the commit gate from a warning into a refusal; `guardrails: off` switches off the three
always-on layers (`/doc-guardrails` and the close-out audit still report when you ask for them). `closeout.py doc-map`
shows how the checker reads it, and a project with a second plan declares it as a lane (LINKING.md § 3).
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

What the installer registers in `settings.json`, each hook run through a short launcher that exits quietly if its
script is gone:

- `Stop`: `skills/doc-guardrails/turn_check.py`, the end-of-turn doc check (`--no-doc-check` leaves it out);
- `UserPromptSubmit`: `skills/eli5/skill_fresh.py`, which tells a running session, once per change, that a skill it
  loaded has changed on disk since, and which file to read again with the Read tool (`--no-skill-checks` leaves out
  this hook and the next two);
- `SessionStart` (on resume): `skills/eli5/skill_fresh.py` again, for a session that resumes after a change;
- `Stop`: `skills/eli5/receipt_check.py`, which, while `/eli5` is on, holds a turn that ran tools once when it ends
  without the receipt block or with one that breaks its shape (each error named), and reports a decision id on its Greenman line that has no record.

`python3 install.py --no-doc-check --no-skill-checks` installs the skills without touching `settings.json`. Add the
optional pieces when you want them — the doctrine only after reading its measurements above:

```sh
python3 install.py --with-doctrine   # + the doctrine's two hooks in settings.json (SessionStart, SubagentStart), and a template ~/.claude/CLAUDE.md
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

The end-of-turn check catches every unlinked document the session wrote, named or committed; the commit gate catches
the rest. If you also want the warning
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

`/closeout` registers a git config hook (git 2.54 or newer; older git: a block first in `.git/hooks/pre-commit`) that
checks the staged documents, and `closeout.py facts` says whether it ran on the last commit. It only warns unless the
project's doc map says `guardrails: block`. A project that keeps its hooks in a folder it tracks (`core.hooksPath`)
gets it only once its doc map says `gate: config-hook`; a project declines it with
`git config hook.doc-guardrails.enabled false`. To add it yourself:
`python3 ~/.claude/skills/closeout/closeout.py install-gate <project folder>`.

</details>

<details>
<summary><b>Check it worked</b></summary>

### Check it worked

- In Claude Code, type `/` and look for `closeout`, `project-update`, `doc-guardrails`, `redman` and `eli5`.
  greenman is meant to come on by itself: ask a question with a real choice in it ("which storage format should we
  use for this?") and it should lay out the routes and write a record. `/greenman` calls it by hand.
- Ask Claude to write an unlinked document from the shell (`echo "# spec" > knowledge/02-spec-x.md` in a project with
  a roadmap) and end the turn: the end-of-turn check should list it before the turn finishes.
- With the doctrine installed, start a new session and ask **"Quote the first line of your doctrine."** It should
  answer with *"Working doctrine (injected: this session runs …)"* or *"Working notes (…)"*, naming the model you're
  running. `DOCTRINE=off claude` (or `cc skills` on Windows) starts one session without it.

</details>

<details>
<summary><b>Re-run the measurements yourself</b></summary>

### Re-run the measurements yourself

Everything above ships in this repository, including the behaviour suite, so the numbers are yours to check
rather than to take on trust:

```sh
python3 -m unittest discover -s tests   # the unit tests; no API cost; CI runs them on Linux, macOS and Windows
python3 evals/run.py --dry-run          # prints the totals — runs nothing
python3 install.py --with-doctrine      # once: the graded sessions take the doctrine from ~/.claude/doctrine
python3 evals/run.py --build-sealed     # once: builds the sealed config folder ~/.claude-evals (each run rebuilds it)
CLAUDE_CONFIG_DIR=~/.claude-evals claude auth login   # once: that folder's own login
python3 evals/run.py --quick            # the quick set of real sessions
python3 evals/run.py                    # the full set
```

As this tree ships: <!-- fig:tests.public -->523<!-- /fig --> unit tests; the quick set is <!-- fig:eval.quick -->19<!-- /fig --> real sessions, and its last run cost
<!-- fig:run.quick.cost -->$10.17<!-- /fig -->; the full set's last run cost <!-- fig:run.full.cost -->$33.03<!-- /fig -->.

The graded sessions do not run in your own `~/.claude`. Each runs in a sealed config folder, `~/.claude-evals`
(`--config` names another), which `run.py` rebuilds from `evals/sealed.json` before every batch: this clone's skills,
the hooks the installer registers (the doctrine's read from `~/.claude/doctrine`, which `--with-doctrine` puts there;
it also adds the doctrine's hooks to your own settings), account sync and claude.ai connectors off, and a login of its
own. Before any session starts, `run.py` refuses, with exit 2, no session launched and nothing spent, when that folder
is not logged in, when one of its hooks does not resolve, or when your `claude --version` is not Claude Code
<!-- fig:eval.sealed.cli_version -->2.1.295<!-- /fig -->, the version whose bundled skills and plugins the sealed environment was reviewed on
(`cli_version` in `evals/sealed.json`); it names both versions. To reproduce the published runs, run that version;
`--probe` launches on another one, but grades every case's `sealed` check as unreviewed, never a pass. You also need
a plan with both Fable 5.1 and Opus 5.5 (`--seat opus` runs the Opus scenarios on Opus 5 instead); `--seat fable` or
`--seat opus55` runs one of them. `docs/evidence.md` has the method, what each case checks, and what the numbers do
and do not show.

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
