# Which seat, when

Three ways to start Claude Code. The `cc` commands below are the Windows launcher (`powershell/cc.ps1`); on macOS and
Linux, start the same seats by hand — the table's model and effort are what matter, not the command that sets them.
Each seat runs the same Claude Code; they differ in the model, the effort, and whether the doctrine is added at the
start of the session.

| Seat | Type | What you get | Use it for |
|---|---|---|---|
| Engineer | `cc` | Fable 5.1 (or whatever `$CcEngineerModel` says), low effort, the short working notes | most work: features, bug fixes, scripts, data wrangling, writing docs, `/closeout` |
| Reviewer | `cc opus` | Opus 5, high effort, the full doctrine | reviewing code, checking a derivation or a model, anything where one wrong sign or off-by-one costs more than the session, long builds where it checks its own work as it goes |
| Plain | `cc plain` | Claude Code exactly as installed, nothing added | seeing what Claude does without the doctrine, or when the doctrine gets in the way |

On macOS and Linux the same three seats are:

```sh
claude --model claude-fable-5-1                 # engineer — set the effort to low with /effort at the start
claude --model claude-opus-5                    # reviewer — effort high
DOCTRINE=off claude                             # plain — the hook adds nothing
```

Bare `claude` also gets the doctrine (the hook is in settings.json), but its model is whatever you picked last with
`/model`. Start a seat explicitly so you know what you are running.

## Where these rules come from

The seats come from the pack author's own side-by-side runs of both seats on real project work: the Fable engineer
seat held up on day-to-day building and doc passes for less, and the Opus reviewer seat was the more reliable at
finding every defect in a code review. That is one person's experience on their own projects, not a law: if a seat
does badly on your kind of work, switch.

## Habits that save your usage limit

- **Start a new session instead of changing model or effort mid-session.** `/model` inside a session changes it for
  the rest of that session and, for bare `claude`, the default for next time, with nothing on screen to remind you.
- **Avoid xhigh and max effort.** In the author's runs they did not beat high and cost noticeably more.
- **Agents and workflows only when the job is really parallel** (many independent files or sources). The reviewer
  doctrine allows agents without asking; if a session starts spawning lots of them on a small job, say
  "do not use agents or workflows; do it yourself".
- **Check what you got:** `/status` shows the model, effort and account. Ask "quote the first line of your doctrine"
  and it should name the model it is running as.

## Claude Desktop

The Code tab in Claude Desktop uses the same `.claude` settings, skills and hook. A plain Desktop
chat does not run hooks: to give a Desktop project the reviewer's habits, paste `desktop-architect.md` into that
project's custom instructions, then start a new chat and ask it to quote its first instruction.
