# Which seat, when

Six seats to start Claude Code from. The `cc` commands below are the Windows launcher (`powershell/cc.ps1`):
`cc [seat] [1|2]`, then anything for claude itself (`cc opus --continue`). On macOS and Linux, start the same seats
by hand — the table's model and effort are what matter, not the command that sets them. Each seat runs the same
Claude Code; they differ in the model, the effort, and whether the doctrine is added at the start of the session.
The engineer, reviewer, fable and helper seats pin their models to exact ids, not aliases, so a new release reaches
them only after it is measured; `cc skills` runs Claude Code's default model, and `$CcEngineerModel` puts the engineer
seat on any model you name.

| Seat | Type | What you get | Use it for |
|---|---|---|---|
| Engineer | `cc` | Opus 5.5 (or whatever `$CcEngineerModel` says), medium effort, its own working notes | most work: features, bug fixes, scripts, data wrangling, writing docs, `/closeout` |
| Reviewer | `cc opus` | Opus 5.5, high effort, its own working notes | reviewing code, checking a derivation or a model, anything where one wrong sign or off-by-one costs more than the session, long builds where it checks its own work as it goes |
| Fable | `cc fable` | Fable 5.1, low effort, the short working notes | the same work as the engineer, on Fable's own usage window: when the Opus window is spent, or to put a second long job on another meter |
| Helper | `cc sonnet` | Sonnet 5.5, high effort, the pack's skills and hooks, no working notes (why none: below the table) | cheap side jobs beside a long session: a quick script, a lookup, a doc tidy; keep migrations and reviews on the engineer and reviewer |
| Skills | `cc skills` | Claude Code's default model, the pack's skills and hooks, no doctrine | seeing what Claude does without the doctrine, or when the doctrine gets in the way |
| Default | `cc default` | stock Claude Code (`claude --safe-mode`): no doctrine, CLAUDE.md, skills, plugins, hooks or MCP | checking whether something odd comes from your setup or from Claude Code itself |

Why the helper runs without working notes (a doctrine page). On the nine-task set the Opus 5 page, the one the loader
would give Sonnet 5.5, was level with no page within noise at high and at max effort; the Opus 5.5 page did better,
just past the noise rule at high and clearly at max, but that page was written from misses on those same nine tasks.
On eight tasks no page was written from, the Opus 5.5 page was level with no page at high and within noise at max:
short of the bar set before those runs for keeping it (at least one effort three or more points above no page, neither
below it beyond noise). So the seat runs without one.

The account word picks the login: `1` (or nothing) is your usual one; `2` runs claude with `CLAUDE_CONFIG_DIR` set to
the folder you name as `$CcSecondaryDir` in your PowerShell profile, above the line that loads `cc.ps1`.

On macOS and Linux the same seats are:

```sh
claude --model claude-opus-5-5 --effort medium  # engineer
claude --model claude-opus-5-5 --effort high    # reviewer
claude --model claude-fable-5-1 --effort low    # fable
DOCTRINE=off claude --model claude-sonnet-5-5 --effort high   # helper
DOCTRINE=off claude                             # skills — the hook adds nothing
claude --safe-mode                              # default — stock Claude Code
CLAUDE_CONFIG_DIR=<second folder> claude …      # any of them on a second account
```

Plain `claude`, launched with no flags, also gets the doctrine once you have installed it (`install.py --with-doctrine`
puts the hook in settings.json; it is off by default), but its model is whatever you picked last with `/model`. Start a
seat explicitly so you know what you are running.

## Where these rules come from

The seats come from the author's own runs of each seat's model and effort, with a doctrine page and without, on the
same tasks: each model's own page, and for the helper's Sonnet 5.5 both the Opus 5 page the loader would give it and
the Opus 5.5 page. The measurements, with their method and
sample sizes, are on the evidence page (https://github.com/XBTDMT/xbt-skills-pack/blob/main/docs/evidence.md); this
page restates none of the measured figures. They are one person's benchmark on their own projects, not a law: if a seat does badly
on your kind of work, switch.

## Habits that save your usage limit

- **Start a new session instead of changing model or effort mid-session.** `/model` inside a session changes it for
  the rest of that session and, for plain `claude`, the default for next time, with nothing on screen to remind you.
- **Never xhigh or max effort.** In the author's runs max never beat high by more than one run's noise, and always
  cost more; xhigh was not measured beside high.
- **Agents and workflows only when the job is really parallel** (many independent files or sources). The Opus 5 page
  (`opus-layer.md`, which the gate gives Opus 5, Sonnet and Haiku) allows agents without asking; the Fable and Opus 5.5
  pages do not. If a session starts spawning lots of them on a small job, say
  "do not use agents or workflows; do it yourself".
- **Check what you got:** `/status` shows the model, effort and account. Ask "quote the first line of your doctrine"
  and it should name the model it is running as.

## Claude Desktop

In the author's setup the Code tab in Claude Desktop used the same `.claude` settings, skills and hook. A plain
Desktop chat does not run hooks: to give a Desktop project the reviewer's habits, paste `desktop-architect.md` into that
project's custom instructions, then start a new chat and ask it to quote its first instruction.
