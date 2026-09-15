---
name: doc-guardrails
description: Use when the user says "/doc-guardrails", "guardrails", "is everything linked", "what is unlinked", "link this doc", or after a document is created and it must be trackable — checks that every spec, audit, recon, design, template, ledger and handoff names its roadmap item and that the roadmap's citations resolve, writes the UNLINKED inventory a thread links from, and reports; never edits a document. The always-on half is three layers (the Write|Edit hook, the end-of-turn check and the per-project commit gate).
---

# /doc-guardrails — everything links to the roadmap, so nothing is lost

The rule: every project has a brain file, a `CLAUDE.md` and a roadmap; everything else is the
project's own design; but every document links to the roadmap, so ideas, findings and things to come back to are
never lost. **This skill is the live guardrail, not the fixer.** Read `LINKING.md` in this folder: the contract.

## What runs, and when

- **Always on — three layers.** (1) `hook.sh` after every Write or Edit: the file just written. (2) `turn_check.py`
  at the end of every turn (a Stop hook): every linked-kind markdown document this session changed since its previous
  turn (written with Write or Edit, or changed while one of its own tool calls ran: a shell command, a script, a
  subagent), in every repository it wrote to; another session's edits are not its to fix. It keeps the turn open once
  with the list. (3) The commit gate in the project's pre-commit hook
  (`closeout.py install-gate`): the staged copy of each staged document, report-only unless the doc map says
  `guardrails: block`. When a document of a linked kind has no `roadmap:` line, or a broken one, the session
  adds the line as part of the same task. Silent otherwise. `guardrails: off` in the doc map switches all three off
  (the on-demand run and the close-out audit still report); a project with no roadmap never hears from them. When the
  gate refuses a commit: a document this session wrote gets its line added, then the commit is made again; a document
  someone else wrote (staged by the user, another lane's) is not edited to get past the gate — name the line it needs
  and ask. Never `--no-verify`, never edit the doc map or the hook to get past it.
- **On demand — `/doc-guardrails [folder]`.** Runs `closeout.py link-audit` on the project: names the roadmap
  and id form it found, reports every unlinked or broken document and every roadmap citation that does not resolve,
  and rewrites `<project root>/knowledge/UNLINKED-<date>.md` (or `docs/`), the inventory a thread links from (this
  thread or another). Report-only. This is the only layer that sweeps the whole project, backlog included: run it
  after a session that wrote documents in bulk.
- **At close-out.** `/closeout` and `/project-update` call the same check (a warning while anything is unlinked, a
  failure on a broken link) and install the commit gate, report-only, where it is missing.

## When invoked by hand, in order

1. `python3 ~/.claude/skills/closeout/closeout.py link-audit <folder>` and read every line.
2. If the project has no `## Doc map` in `CLAUDE.md`, propose one from what the run observed (roadmap path, id
   form, the folders where its specs, audits, designs and ledgers actually live; `ledgers:`, `decisions:` and
   `guardrails: report`; one line per key, no inline comments, LINKING.md § 3) — propose, do not write it, unless
   the user says so or the run is inside `/closeout`, whose § Baseline step creates it.
3. Report: the counts, the broken links first (each is a false statement in the record), the inventory path, and
   the prose roadmap's untagged items if any. One question at most: whether to link anything now. Default is later:
   the inventory is the backlog; the guardrail does not fix.
4. If a document was just written in this session and is unlinked, link it now — it is this session's work, not
   the backlog.

## Never

Retrofit links in bulk (ruled going-forward only). Edit a roadmap row's id. Turn the hook's message into a fix
the session did not ask for. Report a project with no roadmap as broken — that is a baseline gap for `/closeout`.
