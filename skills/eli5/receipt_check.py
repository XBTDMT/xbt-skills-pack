#!/usr/bin/env python3
"""The live receipt check, a Stop hook: while eli5 is on in a session, a turn that ran tools must end with the ELI5
block in its shape, and the decision ids its Greenman line cites must have a record.

- A tool-using turn whose last message has no block of the current version is held once (`decision: block`); the
  reason names the absolute eli5/SKILL.md, so a session whose compaction dropped the skill's text knows what to read.
- A block of the current version that breaks the shape (closeout.receipt_facts: the order and groups, a Result outside
  worked, failed and unclear, Fail present while worked or absent while not, an empty Proof, a Why with no reason, no
  Goal or one sentence) is held once, each error named: the same function grades the receipts in the evals, so the
  live check and the measurement judge alike. A Fail line beside `worked` is answered from the facts (a step the turn
  owed that failed or was refused makes it unclear or failed), never by dropping the line.
- An id on the Greenman line has a record when its `→ <path>` names a readable `<id>-<slug>.md` by an absolute path
  (closeout.receipt_named_records: a build worktree's uncommitted record), or when the decisions folders of the Stop's
  project (the repository root of its cwd) record it, in its checkout or on any local branch (a lane's record sits on
  its own branch until it merges). An id with neither is held once in a project listed as held in the pack's
  tools/receipt_ids.json, and only reported (`systemMessage`) anywhere else: a project's records can live outside the
  folders its cwd declares. Neither text asks for a record to be written; a record written only to quiet the check
  would be a duplicate.
- Nothing at all on a continuation (`stop_hook_active`), in a session that never loaded eli5, or in a turn that ran
  no tool. A turn starts at a prompt, a task notice or another session's message (closeout.turn_facts), so a bare
  acknowledgement of a peer's message is not charged with the tools of the reply before it.

It reads the transcript's last turn and, only when the Greenman line names an id, that project's decision records and
ledgers (closeout.resolved_decision_ids: no link-audit, no git status; the branches only for an id the checkout does
not record, with read-only git). It writes nothing but the branch lookup's cache in the doc-guardrails state folder
(decision-ids/, keyed by commit); never into the project. An internal error is one stderr line and exit 0: a crash
never blocks a turn. Stdlib only, Python 3.9+.
"""
import json
import os
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "closeout"))
IDS_FILE = HERE.parent / "tools" / "receipt_ids.json"   # the pack's own data: absent in a public or kit install
VERSION_RE = re.compile(r"(?m)^ELI5 v(\d+)\s*$")


def held_roots(ids_file):
    """The repository roots whose ids all resolved when the precondition was last run; an absent or unreadable file
    holds none (report only, the safe side)."""
    try:
        data = json.loads(Path(ids_file).read_text(encoding="utf-8"))
        return {os.path.realpath(str(h["root"])) for h in data.get("held") or []}
    except (OSError, ValueError, TypeError, KeyError, AttributeError):
        return set()


def _and(items):
    return items[0] if len(items) == 1 else ", ".join(items[:-1]) + " and " + items[-1]


def check(payload, ids_file=IDS_FILE):
    """The JSON the hook prints for one Stop input, or None."""
    import closeout
    if payload.get("stop_hook_active"):
        return None
    transcript = payload.get("transcript_path") or ""
    eli5 = [k for k, v in closeout.held_skills(transcript).items() if v["name"] == "eli5"]
    if not eli5:
        return None
    turn = closeout.turn_facts(transcript)
    if not turn["ran_tools"]:
        return None
    skill_md = Path(eli5[-1]) / "SKILL.md"
    if not skill_md.is_file():
        skill_md = HERE / "SKILL.md"
    m = VERSION_RE.search(skill_md.read_text(encoding="utf-8")) if skill_md.is_file() else None
    title = f"ELI5 v{m.group(1)}" if m else "ELI5"
    last = payload.get("last_assistant_message")   # the final message itself: the hook can run before it is in the
    if not isinstance(last, str):                   # transcript file; the transcript is read only when it is absent
        last = turn["last_text"]
    blocks = [b for b in closeout.receipt_facts(last) if b["title"] == title]
    if not blocks:
        return json.dumps({"decision": "block", "reason": (
            f"eli5 is on in this session and this turn ran tools, but the last message has no {title} block; end the "
            f"turn with it ({skill_md}).")})
    shape = shape_reason(blocks, title, skill_md)
    ids = _ids_out(blocks, payload, ids_file)
    if not shape:
        return json.dumps(ids) if ids else None
    out = {"decision": "block", "reason": shape}
    if ids and ids.get("decision") == "block":
        out["reason"] += " Also: " + ids["reason"]
    elif ids:
        out["systemMessage"] = ids["systemMessage"]
    return json.dumps(out)


def shape_reason(blocks, title, skill_md):
    """The hold's reason when a block breaks the v5 shape (closeout.receipt_facts' errors), or None."""
    bad = [(n, b["errors"]) for n, b in enumerate(blocks, 1) if b["errors"]]
    if not bad:
        return None
    errors = "; ".join((f"block {n}: " if len(blocks) > 1 else "") + "; ".join(e) for n, e in bad)
    reason = f"the {title} block breaks its shape: {errors}. Rewrite it as {skill_md} gives it, keeping every fact it reported."
    if any("Fail is present while Result is worked" in e for _, es in bad for e in es):
        reason += (" A step the turn owed that failed or was refused makes the Result unclear (part done) or failed,"
                   " with the Fail line; only when nothing failed does the Fail line go.")
    return reason


def _ids_out(blocks, payload, ids_file):
    """The id check's output as a dict (a hold, a report), or None when every cited id has a record."""
    import closeout
    ids = list(dict.fromkeys(i for b in blocks for i in closeout.receipt_ids(b)))
    if not ids:
        return None
    named = {}
    for b in blocks:
        named.update(closeout.receipt_named_records(b))
    rest = [i for i in ids if i not in named]
    if not rest:
        return None
    root = closeout.cwd_root(payload.get("cwd") or os.getcwd())
    known = closeout.resolved_decision_ids(root, rest, cache=closeout.state_dir() / "decision-ids")
    missing = [i for i in rest if i not in known]
    if not missing:
        return None
    folders = closeout.decision_folders(root)
    where = (f"{_and(folders)} under {root}" if folders else f"any decisions folder under {root}") \
        + ", in this checkout or on any local branch"
    cites = f"the Greenman line cites {_and(missing)}, which {'has' if len(missing) == 1 else 'have'} no record in {where}"
    if root in held_roots(ids_file):
        return {"decision": "block", "reason": (
            f"{cites}. The record may live outside these folders: correct the id, or end its Greenman form with "
            f"`→ <the record's absolute path>`; then end the turn with the block.")}
    return {"systemMessage": (f"receipt check (reports only in this project until its ids resolve): {cites}; "
                              f"the record may live outside these folders.")}


def main():
    try:
        out = check(json.loads(sys.stdin.read() or "{}"))
    except Exception as e:           # never a block from a crash
        sys.stderr.write(f"receipt check failed ({type(e).__name__}: {str(e)[:200]}); nothing checked\n")
        return 0
    if out:
        print(out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
