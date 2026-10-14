---
name: doc-guardrails
description: Use when the user says "/doc-guardrails", "guardrails", "is everything linked", "what is unlinked", "link this doc", or after a document is created and it must be trackable — checks that every spec, audit, recon, design, template, ledger and handoff names its roadmap item and that the roadmap's citations resolve, writes the UNLINKED inventory a thread links from, and reports; never edits a document. The always-on half is three layers (the Write|Edit hook, the end-of-turn check and the per-project commit gate). A document the session wrote, itself or through a command it ran, is its own to link. Asked to commit one someone else wrote or staged, commit as asked and let the gate judge; when it refuses, that document is never edited to satisfy it: say the exact line it needs (`roadmap: <id>`, the id read from the roadmap) and ask.
---

# /doc-guardrails — everything links to the roadmap, so nothing is lost

The rule: every project has a brain file, a `CLAUDE.md` and a roadmap; everything else is the
project's own design; but every document links to the roadmap, so ideas, findings and things to come back to are
never lost. **This skill is the live guardrail, not the fixer.** Read `LINKING.md` in this folder: the contract.

## What runs, and when

- **Always on — three layers.** (1) `hook.sh` after every Write or Edit: the file just written; it does not see a
  file a shell command or a script wrote. (2) `turn_check.py` at the end of every turn (a Stop hook): a changed
  document of a linked kind is this session's when the session is shown to have written, named or committed it this
  turn; nothing else is. It does not judge an unnamed script's output the session neither names nor commits, nor
  what another session wrote with its write tools. It keeps the turn open once with the list of the documents it
  wrote, named or committed, each line saying why it is the session's (its tools, a command it ran, named, committed);
  only a document committed this turn but written before it can be someone else's. (3) The commit gate
  (`closeout.py install-gate`, run as a command of its own: a strict session refuses a whole chain that holds
  `$?`, so never `install-gate …; echo "exit=$?"`): a git config hook (git 2.54+) that git runs before any hook file, in every worktree;
  with older git, a block first in the hook file the pack owns; never in a hooks folder the project tracks unless its
  doc map says `gate: config-hook`. It judges the staged copy of each staged document, whatever wrote it, report-only
  unless the doc map says `guardrails: block`, and leaves a heartbeat, so `closeout.py facts` says whether it ran on
  the last commit. It does not run on a `--no-verify` commit, and says so in its output when it cannot find
  `closeout.py`. When a document of a linked kind has no `roadmap:` line, or a broken one, the session
  adds the line as part of the same task. Silent otherwise. `guardrails: off` in the doc map switches all three off
  (the on-demand run and the close-out audit still report); a project with no roadmap never hears from them. When the
  gate refuses a commit: a document this session wrote gets its line added, then the commit is made again; a document
  someone else wrote (staged by the user, another lane's) is not edited to get past the gate — name the exact line it
  needs (`roadmap: <id>`, the id read from the roadmap) and ask. Never `--no-verify`, never edit the doc map or the hook to get past it. A doc map with problems never
  silences a layer: each checks with the map's valid parts and the defaults, and says so in its own channel (LINKING
  § 6).
- **On demand — `/doc-guardrails [folder]`.** Runs `closeout.py link-audit` on the project: names the roadmap
  and id form it found, reports every unlinked or broken document and every roadmap citation that does not resolve,
  and rewrites `<project root>/knowledge/UNLINKED-<date>.md` (or `docs/`), the inventory a thread links from (this
  thread or another). Report-only. This is the only layer that sweeps the whole project, backlog included: run it
  after a session that wrote documents in bulk.
- **At close-out.** `/closeout` and `/project-update` call the same check (a warning while anything is unlinked, a
  failure on a broken link this pass wrote; an older one is reported as pre-existing) and register the commit gate,
  report-only, where it is missing, reporting the state line it prints.

## When invoked by hand, in order

1. `python3 <closeout skill folder>/closeout.py link-audit <folder>` (the closeout folder beside this skill's base
   directory, written out in full) and read every line; its `CONFIG` lines, first, are the map's problems.
2. **The doc map.** If the run printed `CONFIG` lines, or the project has no `## Doc map`, run
   `python3 <closeout skill folder>/closeout.py doc-map <folder>` (the closeout folder beside this skill's base
   directory, written out in full) and propose the corrected map (or a new one) from
   what the two runs observed: the roadmap and its id form, the folders where its specs, audits, designs and
   ledgers actually live written as path patterns, `ledgers:`, `decisions:` (a folder) and `guardrails: report`; one
   line per key, no inline comments (LINKING.md § 3). A second roadmap is proposed as a lane — a `## Doc map: lane
   <name>` section with its own roadmap, prefixed id form, pin, ledgers and decisions folder — never as a second path
   on `roadmap:`. Propose; do not write it, unless the user says so or the run is inside `/closeout`, whose § Baseline
   step creates a missing map. A map that another lane or person owns is proposed to them, never edited to quiet a
   report.
3. Report: the map's problems first (each is a check the project believes it has and does not), then the counts, the broken links first (each is a false statement in the record), the inventory path, and
   the prose roadmap's untagged items if any. One question at most: whether to link anything now. Default is later:
   the inventory is the backlog; the guardrail does not fix.
4. If a document was just written in this session and is unlinked, link it now — it is this session's work, not
   the backlog.

## Never

Retrofit links in bulk (ruled going-forward only). Edit a roadmap row's id. Turn the hook's message into a fix
the session did not ask for. Report a project with no roadmap as broken — that is a baseline gap for `/closeout`.
