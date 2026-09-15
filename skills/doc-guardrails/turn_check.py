#!/usr/bin/env python3
"""doc-guardrails, the end-of-turn layer: a Stop hook that checks every markdown document of a linked kind left
uncommitted and changed since this session's previous turn ended, whatever wrote it (Write, Edit, a shell command, a
Python script, a subagent). The PostToolUse hook (`hook.sh`) only sees Write and Edit; this closes that gap.

Whose documents: only this session's. From the session's transcript (and its subagents' transcripts) it takes every
file written with Write, Edit, MultiEdit or NotebookEdit, and the time window of every other tool call (a shell
command, a script, a subagent); a changed document counts when it was written by one of those tools or modified
inside one of those windows. The repositories checked are the session's own and every one its tool calls wrote to or
named, so a session started in one worktree that writes into another is still checked, and another session's
uncommitted edits in the same checkout are not this session's to fix (two lanes blocked each other, 2026-09-15).
Without a readable transcript it falls back to every changed document in the session's repository.

How it works: the project is the git repository the session is in. "This turn" means changed since this session's
previous turn ended (a timestamp kept per session under DOC_GUARDRAILS_STATE, default ~/.claude/doc-guardrails-state);
a session's first turn looks back one hour. Each modified, added or untracked `.md` file newer than that is judged
by closeout.py's `link_check_file` (the same check the Write/Edit hook uses). When any is unlinked or broken, it prints
Claude Code's Stop decision `{"decision": "block", "reason": …}` so the session adds the lines before it ends the turn.
A document written and committed within one turn is the commit gate's to catch; a document someone else changed in
the same repository since the last turn is included. It never blocks a continuation (`stop_hook_active`), so it
cannot loop, and it is silent when there is no repository, no roadmap, `guardrails: off` in the doc map, or nothing
to say. Never edits anything. Stdlib only, Python 3.9+.
"""
import datetime
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "closeout"))
import closeout  # noqa: E402

FIRST_TURN_LOOKBACK = 3600
WRITE_TOOLS = {"Write", "Edit", "MultiEdit", "NotebookEdit"}
SLACK = 2.0  # seconds around a tool call's window: file times and transcript times come from different clocks'
MAX_ROOTS = 20
# The paths a tool call names in its own text, so a command that works in another checkout still has that checkout
# checked. Posix and Windows spellings both: a Bash or PowerShell command on Windows names `C:\repo` or `\\box\share`,
# which no leading-slash pattern can see, and that checkout's documents then go unattributed.
PATH_RE = re.compile(r"(?:~|/)[^\s'\"`;|&<>()]+"           # /abs/path, ~/under the home directory
                     r"|[A-Za-z]:[\\/][^\s'\"`;|&<>()]*"    # C:\path or C:/path
                     r"|\\\\[^\s'\"`;|&<>()]+")             # \\server\share


def same_path(path):
    """A path in the form paths are compared in: symlinks resolved, and on Windows one casing and one separator
    (normcase is the identity on posix, so nothing is loosened there)."""
    return os.path.normcase(os.path.realpath(path))


def state_dir():
    return Path(os.environ.get("DOC_GUARDRAILS_STATE") or Path.home() / ".claude" / "doc-guardrails-state")


def changed_markdown(root, since):
    """Tracked-and-modified or untracked .md files under the repository changed after `since` (epoch seconds)."""
    r = subprocess.run(["git", "-C", str(root), "status", "--porcelain", "--untracked-files=all", "-z"],
                       capture_output=True, encoding="utf-8", errors="replace")
    if r.returncode != 0:
        return []
    out, entries = [], r.stdout.split("\0")
    i = 0
    while i < len(entries):
        entry = entries[i]
        i += 1
        if len(entry) < 4:
            continue
        code, rel = entry[:2], entry[3:]
        if code[0] in "RC":
            i += 1  # a rename carries its old path as the next entry
        if code == " D" or code[0] == "D" or not rel.endswith(".md"):
            continue
        p = root / rel
        try:
            if p.is_file() and p.stat().st_mtime > since:
                out.append(rel)
        except OSError:
            continue
    return sorted(out)


def _epoch(stamp):
    try:
        return datetime.datetime.fromisoformat(str(stamp).replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def session_activity(transcript, since, now):
    """(paths written by the write tools, [(start, end)] windows of every other tool call, paths the calls named) since
    `since`, from the session's transcript and its subagents' transcripts; None when the transcript cannot be read."""
    transcript = Path(transcript) if transcript else None
    if not transcript or not transcript.is_file():
        return None
    files = [transcript]
    sub = transcript.parent / transcript.stem / "subagents"
    if sub.is_dir():
        files += [f for f in sub.glob("*.jsonl") if f.stat().st_mtime >= since - SLACK]
    written, named, starts, ends = set(), set(), {}, {}
    for f in files:
        try:
            lines = f.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            if f == transcript:
                return None
            continue
        for line in lines:
            try:
                entry = json.loads(line)
            except ValueError:
                continue
            when = _epoch(entry.get("timestamp"))
            content = (entry.get("message") or {}).get("content") if isinstance(entry.get("message"), dict) else None
            if when is None or not isinstance(content, list):
                continue
            for item in content:
                if not isinstance(item, dict):
                    continue
                if item.get("type") == "tool_use" and when >= since - SLACK:
                    inp = item.get("input") or {}
                    target = inp.get("file_path") or inp.get("notebook_path")
                    if item.get("name") in WRITE_TOOLS and target:
                        written.add(same_path(os.path.expanduser(target)))
                    else:
                        starts[item.get("id")] = when
                    for text in (v for v in inp.values() if isinstance(v, str)):
                        named.update(m.group(0) for m in PATH_RE.finditer(text))
                elif item.get("type") == "tool_result" and item.get("tool_use_id") in starts:
                    ends[item.get("tool_use_id")] = when
    windows = [(start - SLACK, ends.get(uid, now) + SLACK) for uid, start in starts.items()]
    return written, windows, named


def candidate_roots(cwd, written, named):
    roots = []
    for path in [cwd] + sorted(written) + sorted(named):
        p = Path(os.path.expanduser(path))
        while not p.exists() and p.parent != p:
            p = p.parent
        if p == Path("."):
            continue  # what was named was not a path on this machine: walking up left nothing to look at
        if p.is_file():
            p = p.parent
        root = closeout.repo_root(p) if p.is_dir() else None
        if root and Path(root) not in roots:
            roots.append(Path(root))
        if len(roots) >= MAX_ROOTS:
            break
    return roots


def check(payload, states=None, now=None):
    """The Stop decision as a JSON string, or None when there is nothing to say."""
    now = time.time() if now is None else now
    states = Path(states) if states else state_dir()
    session = str(payload.get("session_id") or "unknown").replace("/", "_")
    marker = states / session
    try:
        since = float(marker.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        since = now - FIRST_TURN_LOOKBACK
    if payload.get("stop_hook_active"):
        return None  # the session is already continuing because of a stop hook: never block twice
    try:
        states.mkdir(parents=True, exist_ok=True)
        marker.write_text(str(now), encoding="utf-8")
    except OSError:
        pass
    cwd = Path(payload.get("cwd") or os.getcwd())
    activity = session_activity(payload.get("transcript_path"), since, now)
    if activity is None:  # no transcript to attribute from: every changed document in the session's repository
        root = closeout.repo_root(cwd) if cwd.is_dir() else None
        roots, attribute = ([Path(root)] if root else []), None
    else:
        written, windows, named = activity
        roots = candidate_roots(str(cwd), written, named)

        def attribute(path):
            if same_path(str(path)) in written:
                return True
            try:
                mtime = path.stat().st_mtime
            except OSError:
                return False
            return any(a <= mtime <= b for a, b in windows)
    if not roots:
        return None
    found = []
    for root in roots:
        for rel in changed_markdown(root, since):
            if attribute is not None and not attribute(root / rel):
                continue
            hit = closeout.link_check_file(root, root / rel)
            if hit:
                label = rel if len(roots) == 1 or root == roots[0] else f"{root}/{rel}"
                found.append(f"- {label}: {hit[0]} — {hit[1]}")
    if not found:
        return None
    reason = ("doc-guardrails (end of turn): these documents this session changed do not name a valid roadmap item (missing or broken). "
              "Add `roadmap: <ids>` or `roadmap: none — <why>` in the first 40 lines of each (LINKING.md § 2), "
              "then finish the turn:\n" + "\n".join(found))
    return json.dumps({"decision": "block", "reason": reason})


def main():
    for stream in (sys.stdin, sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    try:
        payload = json.loads(sys.stdin.read() or "{}")
    except ValueError:
        return 0
    try:
        out = check(payload if isinstance(payload, dict) else {})
    except Exception as e:  # the hook must never break a session; say so on stderr and let the turn end
        print(f"doc-guardrails turn check skipped: {type(e).__name__}: {e}", file=sys.stderr)
        return 0
    if out:
        print(out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
