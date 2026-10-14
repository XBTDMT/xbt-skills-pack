#!/usr/bin/env python3
"""The skill freshness hook: a running or resumed session keeps the skill text it loaded, so when a skill's file has
changed on disk since, the session is told at its next prompt (UserPromptSubmit) or when it resumes (SessionStart,
matcher `resume`) which skill changed and which file to read with the Read tool. It speaks only when something changed,
and names each change once.

The transcript is the state: closeout's held_skills gives each skill's last text in file order (a load, a compaction's
re-injection, or a full Read of its SKILL.md) and every other file the session Read inside a held skill's folder;
skill_changes and file_changes compare them with the files now. The reading is kept in a per-transcript index
in the doc-guardrails state folder (session_skills.index_dir), so each prompt reads only what the transcript gained
since the last one and the hook's time does not grow with the transcript.

Once per change: the hook keeps, per session, what it has named in the same state folder (skill-fresh/<session>.json):
each as (file path, content hash, basis), the basis being the text the session held when it was compared (its load,
re-injection or Reads) and the number of compactions so far. A pair is named once; it is named again only when the
file changes anew (a new hash, a return to an earlier content included), when the session holds another text of it (a
new basis), or after a compaction, whose summary replaced the notice. A resume alone does not repeat it: the resumed
transcript carries the notice. A Read tool read of the current file clears it, as before; a shell read (`cat`) does
not, and is not nagged. The record keeps only the changes still open, so it stays small.

The hook never blocks and writes nothing but the index and that record; it exits 0 always. An internal error is one
context line saying the check failed, never a broken prompt. Stdlib only, Python 3.9+.
"""
import json
import os
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "closeout"))

CHANGED = ("skill freshness: the {name} skill changed on disk after this session loaded it ({loaded_at}). Read {path} "
           "again with the Read tool, in full, before acting on its rules; do not re-run the skill.")
GONE = ("skill freshness: the {name} skill this session loaded ({loaded_at}) is no longer installed at {path}; do not "
        "rely on its text.")
FILE_CHANGED = ("skill freshness: {path}, a file of the {name} skill, changed on disk after this session read it "
                "({loaded_at}). Read it again with the Read tool, in full, before acting on it; do not re-run the skill.")
FILE_GONE = ("skill freshness: {path}, a file of the {name} skill this session read ({loaded_at}), is no longer there; "
             "do not rely on its text.")
FAILED = ("skill freshness check failed ({kind}: {message}); skills loaded earlier in this session may be stale.")

NAMED_VERSION = 1
KEEP_DAYS = 14


def named_file(folder, session):
    """The record of one session: its id when it is a plain name (Claude Code's ids are), else a safe form of it with
    a checksum, so any id stays one file inside the folder."""
    safe = re.sub(r"[^A-Za-z0-9_-]", "_", session)[:120]
    if safe != session:
        import zlib
        safe = f"{safe}-{zlib.crc32(session.encode('utf-8', 'surrogateescape')):08x}"
    return Path(folder) / f"{safe}.json"


def read_named(where):
    """The pairs a session's record holds, as a set of tuples; an absent or unreadable record holds none."""
    try:
        data = json.loads(Path(where).read_text(encoding="utf-8"))
        if data.get("version") == NAMED_VERSION:
            return {tuple(p) for p in data["named"] if isinstance(p, list) and len(p) == 3
                    and all(isinstance(x, str) for x in p)}
    except (OSError, ValueError, TypeError, KeyError, AttributeError):
        pass
    return set()


def write_named(where, pairs):
    """Written whole, through a temp file and os.replace, so a reader never sees half of one. A session's first record
    also clears the records untouched for KEEP_DAYS. A failure costs only a repeated notice."""
    import time
    where = Path(where)
    try:
        where.parent.mkdir(parents=True, exist_ok=True)
        if not where.exists():
            limit = time.time() - KEEP_DAYS * 86400
            for p in where.parent.iterdir():
                try:
                    if p.is_file() and p.stat().st_mtime < limit:
                        p.unlink()
                except OSError:
                    continue
        tmp = where.parent / f".{where.name}.{os.getpid()}.tmp"
        tmp.write_text(json.dumps({"version": NAMED_VERSION, "named": sorted(list(p) for p in pairs)}),
                       encoding="utf-8")
        os.replace(tmp, where)
    except OSError:
        pass


def content_hash(path, state):
    """The file's content as a short hash; "gone" for a file that is not there."""
    import hashlib
    if state == "gone":
        return "gone"
    try:
        return hashlib.sha256(Path(path).read_bytes()).hexdigest()[:32]
    except FileNotFoundError:
        return "gone"


def notice(payload):
    """The context lines for one hook input: the changes to what the session holds that this session has not been
    told of; "" when every skill text and file it holds is current, or every change was named already."""
    import session_skills   # closeout's readers alone: importing the whole helper costs a fifth of the budget
    transcript = payload.get("transcript_path") or ""
    held, compactions = session_skills.read_session(transcript, index=session_skills.index_dir())
    changes = [((CHANGED if state == "changed" else GONE).format(name=name, loaded_at=loaded_at or "unknown time",
                                                                  path=path), path, state)
               for name, path, loaded_at, state in session_skills.skill_changes(held)]
    changes += [((FILE_CHANGED if state == "changed" else FILE_GONE).format(name=name, path=path,
                                                                             loaded_at=loaded_at or "unknown time"),
                 path, state)
                for name, path, loaded_at, state in session_skills.file_changes(held)]
    session = str(payload.get("session_id") or Path(transcript).stem or "")
    if not session:                 # nothing to key a record by: every change is named, as before
        return "\n".join(line for line, _, _ in changes)
    where = named_file(session_skills.state_dir() / "skill-fresh", session)
    named = read_named(where)
    if not changes:
        if named:
            write_named(where, set())
        return ""
    bases = session_skills.change_bases(held)
    keyed = [(line, (path, content_hash(path, state), f"c{compactions}|{bases.get(path, '')}"))
             for line, path, state in changes]
    now = {key for _, key in keyed}
    if now != named:
        write_named(where, now)     # only the changes still open: a cleared or superseded one is forgotten
    return "\n".join(line for line, key in keyed if key not in named)


def main():
    event = "UserPromptSubmit"
    try:
        payload = json.loads(sys.stdin.read() or "{}")
        event = payload.get("hook_event_name") or event
        text = notice(payload)
    except Exception as e:          # loud, never a broken prompt
        text = FAILED.format(kind=type(e).__name__, message=str(e)[:200])
    if text:
        print(json.dumps({"hookSpecificOutput": {"hookEventName": event, "additionalContext": text}}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
