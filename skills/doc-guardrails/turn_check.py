#!/usr/bin/env python3
"""doc-guardrails, the end-of-turn layer: a Stop hook that judges, at the end of each turn, the documents of a linked kind this session is
shown to have written, named or committed.

The contract: a changed document of a linked kind is this session's when the session is shown to have written, named
or committed it this turn; nothing else is. The evidence comes from the session's transcript and its subagents'
transcripts, read from where the previous Stop left off: a file written with Write, Edit, MultiEdit or NotebookEdit; a
file named in the input of one of its writing-capable calls (a command, a heredoc, the content of a script it wrote);
a file named in the output of one of its own writing-capable calls and changed inside that call's window; a document
committed by its own `git commit` this turn, judged on the committed blob. Naming (input or output) counts only inside
the session's own roots: the git repository of its cwd and that repository's worktrees, read from the common git dir
(outside git, the cwd's root alone). A document in any other project is the session's only when its write tools wrote
it or its own `git commit` committed it, so a read-only look at another project that names a file its own
session is writing blames nobody. A file another Claude session wrote with its write tools is theirs, even if this
session named or committed it. A touch that leaves the content as it was (an autostash) is not a change. Read-only
tools (Read, Grep, Agent, …) name nothing. What it no longer catches: a script's output the session neither names nor
commits, and a shell write into another project; the commit gate judges them when anyone commits them, and the sweep
lists them.

How it works: each root is the git work tree above a document (outside git, the nearest folder holding CLAUDE.md or
a roadmap), found in-process and judged with closeout.py's one verdict (`link_context` once per root, `judge` per
document). One `git status` per root; the reflog only when the session ran a `git commit`. State lives per session in
DOC_GUARDRAILS_STATE (default ~/.claude/doc-guardrails-state)/sessions/<session>.json, saved once, at the end, so a
check killed midway is redone at the next Stop; state files untouched for 14 days are deleted. A 20 s budget: roots it
could not reach are named and carried to the next Stop. Repositories under the system temp folders are skipped unless
one is the session's own. With no transcript it says so in a systemMessage and blocks nothing. A doc map with problems
is a systemMessage once per map, never a block. It never blocks a continuation (`stop_hook_active`), and is silent
when there is no roadmap, `guardrails: off` in the doc map, or nothing to say. Never edits anything. Stdlib only,
Python 3.9+.
"""
import datetime
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "closeout"))
import closeout  # noqa: E402

FIRST_TURN_LOOKBACK = 3600
KEEP_DAYS = 14
BUDGET = 20.0
SLACK = 2.0  # seconds around a tool call's window: file times and transcript times come from different clocks
MAX_ROOTS = 20
WRITE_TOOLS = {"Write", "Edit", "MultiEdit", "NotebookEdit"}
READ_ONLY_TOOLS = {"Read", "Grep", "Glob", "LS", "WebFetch", "WebSearch", "ToolSearch", "TodoWrite", "Skill", "Agent",
                   "Task", "SendMessage", "SubagentHandback", "Monitor", "TaskStop"}
# The paths a tool call names in its own text. Posix and Windows spellings both: a command on Windows names `C:\repo`
# or `\\box\share`, which no leading-slash pattern can see.
PATH_RE = re.compile(r"(?:~|/)[^\s'\"`;|&<>()]+"           # /abs/path, ~/under the home directory
                     r"|[A-Za-z]:[\\/][^\s'\"`;|&<>()]*"    # C:\path or C:/path
                     r"|\\\\[^\s'\"`;|&<>()]+")             # \\server\share
# a relative `.md` name, matched only from the start of a token, so a long word costs one pass, never a pass per letter
MD_TOKEN_RE = re.compile(r"(?<![^\s'\"`;|&<>(){}=,])[^\s'\"`;|&<>(){}=,]*\.md\b")
COMMIT_RE = re.compile(r"\bgit\b[^\n]*\bcommit\b")
REASON = ("doc-guardrails (end of turn): documents this session wrote (with its own tools or through a command it ran), "
          "named or committed do not name a valid roadmap item. Add `roadmap: <ids>` or `roadmap: none — <why>` in the "
          "first 40 lines of each (LINKING.md § 2). Each line says why the document is this session's: one a command it "
          "ran wrote (a generator, a script) is its own to link, like one its tools wrote. A document someone else wrote "
          "is not edited to get past this check: only a document committed this turn but written before it can be "
          "someone else's, and if it is, leave it and say so.")
# why a listed document is this session's: the evidence the check already holds, said to the session
BY_TOOLS = "written by this session's tools"
BY_COMMAND = "written by a command this session ran"           # changed in a call whose output names it
NAMED = "changed this turn and named in this session's commands"  # named, but no call shown to have written it
NO_TRANSCRIPT = ("doc-guardrails (end of turn): no transcript to attribute from, so this turn's documents were not "
                 "checked; the commit gate judges what is committed.")


def git(root, *args):
    """Every git call of the check: read-only, never taking index.lock in a checkout another session is using."""
    return subprocess.run(["git", "-C", str(root), *args], capture_output=True, encoding="utf-8", errors="replace",
                          env=dict(os.environ, GIT_OPTIONAL_LOCKS="0"))


def same_path(path):
    """A path in the form paths are compared in: symlinks resolved, and on Windows one casing and one separator."""
    return os.path.normcase(os.path.realpath(path))


def state_dir():
    return closeout.state_dir()


def _epoch(stamp):
    try:
        return datetime.datetime.fromisoformat(str(stamp).replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


# ---------------------------------------------------------------------------------------------- the transcript

def session_files(transcript):
    """The session's transcript and its subagents' transcripts."""
    files = [transcript]
    sub = transcript.parent / transcript.stem / "subagents"
    if sub.is_dir():
        files += sorted(sub.glob("*.jsonl"))
    return files


def read_new(path, offset, since):
    """(entries, size): the entries appended since the saved offset. A file without an offset, or shorter than it (it
    was rewritten), is read whole and filtered by timestamp ≥ since − SLACK. A line is parsed only when it names a tool."""
    try:
        size = path.stat().st_size
        whole = offset is None or size < offset
        with open(path, "rb") as fh:
            if not whole:
                fh.seek(offset)
            data = fh.read()
    except OSError:
        return None, offset
    out = []
    for raw in data.split(b"\n"):
        if b'"tool_use"' not in raw and b'"tool_result"' not in raw:
            continue
        try:
            entry = json.loads(raw.decode("utf-8", errors="replace"))
        except ValueError:
            continue
        if not isinstance(entry, dict):
            continue
        if whole:
            when = _epoch(entry.get("timestamp"))
            if when is None or when < since - SLACK:
                continue
        out.append(entry)
    return out, size


def _strings(value):
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for v in value.values():
            yield from _strings(v)
    elif isinstance(value, list):
        for v in value:
            yield from _strings(v)


# Neither pattern can match across its own delimiters, and each matches at most once per delimited token (a path,
# which always holds a `/`, `~` or `\`, runs to the token's end; a `.md` name must start the token), so the text is
# split first and the pattern runs only on the tokens that can hold a match: the same set as a scan of the whole
# text, without walking every letter of a long word.
PATH_SPLIT = re.compile(r"[\s'\"`;|&<>()]+")
MD_SPLIT = re.compile(r"[\s'\"`;|&<>(){}=,]+")


def tokens(text):
    out = set()
    for tok in PATH_SPLIT.split(text):
        if "/" in tok or "~" in tok or "\\" in tok:
            m = PATH_RE.search(tok)
            if m:
                out.add(m.group(0))
    if ".md" in text:
        for tok in MD_SPLIT.split(text):
            if ".md" in tok:
                m = MD_TOKEN_RE.match(tok)
                if m and m.group(0) != ".md":
                    out.add(m.group(0))
    return out


class Evidence:
    """This turn's evidence: files written with a write tool (E1), tokens named in a writing-capable call's input
    (E2), tokens in its output with its window (E3), and the windows of calls that ran `git commit` (E4)."""

    def __init__(self):
        self.written, self.named, self.outputs, self.commit_windows = set(), set(), [], []

    def to_json(self):
        return {"written": sorted(self.written), "named": sorted(self.named), "outputs": self.outputs,
                "commit_windows": self.commit_windows}

    def merge(self, data):
        self.written.update(data.get("written") or [])
        self.named.update(data.get("named") or [])
        self.outputs += [list(o) for o in data.get("outputs") or []]
        self.commit_windows += [list(w) for w in data.get("commit_windows") or []]


def scan(entries, ev, now):
    """Add a batch of transcript entries to the evidence."""
    calls = {}
    for entry in entries:
        when = _epoch(entry.get("timestamp"))
        msg = entry.get("message") if isinstance(entry.get("message"), dict) else {}
        content = msg.get("content")
        if when is None or not isinstance(content, list):
            continue
        for item in content:
            if not isinstance(item, dict):
                continue
            if item.get("type") == "tool_use":
                name, inp = item.get("name"), item.get("input") or {}
                if name in READ_ONLY_TOOLS:
                    continue
                target = (inp.get("file_path") or inp.get("notebook_path")) if isinstance(inp, dict) else None
                if name in WRITE_TOOLS and target:
                    ev.written.add(same_path(os.path.expanduser(target)))
                texts = list(_strings(inp))
                for text in texts:
                    ev.named.update(tokens(text))
                calls[item.get("id")] = {"start": when, "end": None, "out": set(),
                                         "commit": any(COMMIT_RE.search(t) for t in texts)}
            elif item.get("type") == "tool_result" and item.get("tool_use_id") in calls:
                call = calls[item["tool_use_id"]]
                call["end"] = when
                for text in _strings(item.get("content")):
                    call["out"].update(tokens(text))
    for call in calls.values():
        window = [call["start"] - SLACK, (call["end"] if call["end"] is not None else now) + SLACK]
        if call["out"]:
            ev.outputs.append([window[0], window[1], sorted(call["out"])])
        if call["commit"]:
            ev.commit_windows.append(window)
    return ev


# ---------------------------------------------------------------------------------------------- roots and documents

class Roots:
    """root_of(path) in-process: the first folder upward holding a `.git` entry (file or folder); outside git, the
    nearest one holding CLAUDE.md or a default roadmap. Memoised per folder; no subprocess."""

    def __init__(self):
        self.memo = {}

    def of(self, path):
        p = Path(os.path.expanduser(str(path)))
        while not p.exists() and p.parent != p:
            p = p.parent
        if p.is_file():
            p = p.parent
        key = str(p)
        if key not in self.memo:
            self.memo[key] = self._find(p)
        return self.memo[key]

    @staticmethod
    def _find(p):
        cur = p
        while True:
            if (cur / ".git").exists():
                return cur, True
            if cur.parent == cur:
                break
            cur = cur.parent
        cur = p
        while True:
            if (cur / "CLAUDE.md").is_file() or any((cur / r).is_file() for r in closeout.LINK_DEFAULT_ROADMAPS):
                return cur, False
            if cur.parent == cur:
                return None
            cur = cur.parent


def _git_dir(root):
    """The git dir of a work tree: its `.git` folder, or the folder its `.git` file points to (a worktree)."""
    dot = Path(root) / ".git"
    if dot.is_dir():
        return dot
    try:
        line = dot.read_text(encoding="utf-8", errors="replace").strip()
    except OSError:
        return None
    if not line.startswith("gitdir:"):
        return None
    target = Path(line[len("gitdir:"):].strip())
    return target if target.is_absolute() else Path(root) / target


def own_roots(cwd, finder=None):
    """The session's own roots, as real paths: the root of its cwd and, in git, every work tree of that
    repository — the main one and each linked worktree, read from the common git dir (`commondir`, then
    `worktrees/*/gitdir`, absolute or relative), never from a fixed path. In-process; no subprocess."""
    found = (finder or Roots()).of(cwd)
    if not found:
        return set()
    root, in_git = found
    out = {same_path(root)}
    gitdir = _git_dir(root) if in_git else None
    if gitdir is None:
        return out
    common = gitdir
    try:
        rel = (gitdir / "commondir").read_text(encoding="utf-8", errors="replace").strip()
        if rel:
            common = Path(os.path.realpath(gitdir / rel))  # `../..` from a worktree's git dir
    except OSError:
        pass
    if common.name == ".git":
        out.add(same_path(common.parent))  # the main work tree
    try:
        entries = list((common / "worktrees").iterdir())
    except OSError:
        entries = []
    for entry in entries:
        try:
            link = (entry / "gitdir").read_text(encoding="utf-8", errors="replace").strip()
        except OSError:
            continue
        if link:
            path = Path(link)
            out.add(same_path((path if path.is_absolute() else entry / path).parent))  # it names <worktree>/.git
    return out


def default_temp_dirs():
    out = {os.path.realpath(tempfile.gettempdir())}
    out.update(os.path.realpath(p) for p in ("/tmp", "/private/tmp", "/var/tmp", "/private/var/tmp") if os.path.isdir(p))
    return sorted(out)


def _under(path, dirs):
    p = os.path.realpath(str(path))
    return any(p == d or p.startswith(d.rstrip(os.sep) + os.sep) for d in dirs)


def dirty_md(root):
    """Modified, added or untracked `.md` files of a git root, from one `git status`."""
    r = git(root, "status", "--porcelain", "--untracked-files=all", "-z")
    if r.returncode != 0:
        return []
    out, entries, i = [], r.stdout.split("\0"), 0
    while i < len(entries):
        entry = entries[i]
        i += 1
        if len(entry) < 4:
            continue
        code, rel = entry[:2], entry[3:]
        if code[0] in "RC":
            i += 1  # a rename carries its old path as the next entry
        if "D" in code or not rel.endswith(".md"):
            continue
        out.append(rel)
    return sorted(out)


def sha(path):
    try:
        return hashlib.sha1(Path(path).read_bytes()).hexdigest()
    except OSError:
        return None


def named_match(token, fpath, root, names):
    """Does a transcript token name this file? An absolute token by its real path; one with `/` by the path's ending;
    a bare name only when it is unique among the root's dirty documents; one with `*`, `?` or `[` by the one matcher,
    where any error means it names nothing (a transcript token is hostile input)."""
    token = os.path.expanduser(token.strip().rstrip(".,:;"))
    if not token:
        return False
    try:
        if os.path.isabs(token):
            return same_path(token) == same_path(fpath)
        rel = Path(fpath).resolve().relative_to(Path(root).resolve()).as_posix()
        if any(c in token for c in "*?["):
            return closeout.kind_match(rel, [token[2:] if token.startswith("./") else token])
        if "/" in token:
            t = token[2:] if token.startswith("./") else token
            return Path(fpath).resolve().as_posix().endswith("/" + t)
        return Path(fpath).name == token and names.get(token, 0) == 1
    except (ValueError, OSError, re.error):
        return False


def other_sessions_written(config_dirs, own_files, since):
    """Real paths other Claude sessions wrote with their write tools (E5), read only when a candidate rests on naming
    or committing alone: their transcripts changed since `since`, prefiltered by bytes before any parse."""
    out = set()
    own = {same_path(f) for f in own_files}
    seen = set()
    for cfg in config_dirs:
        projects = Path(cfg) / "projects"
        if not projects.is_dir():
            continue
        for f in list(projects.glob("*/*.jsonl")) + list(projects.glob("*/*/subagents/*.jsonl")):
            real = same_path(f)
            if real in own or real in seen:
                continue
            seen.add(real)
            try:
                if f.stat().st_mtime < since:
                    continue
                data = f.read_bytes()
            except OSError:
                continue
            if b'"tool_use"' not in data or not any(n.encode() in data for n in WRITE_TOOLS):
                continue
            for raw in data.split(b"\n"):
                if b'"tool_use"' not in raw:
                    continue
                try:
                    entry = json.loads(raw.decode("utf-8", errors="replace"))
                except ValueError:
                    continue
                msg = entry.get("message") if isinstance(entry, dict) and isinstance(entry.get("message"), dict) else {}
                for item in msg.get("content") if isinstance(msg.get("content"), list) else []:
                    if isinstance(item, dict) and item.get("type") == "tool_use" and item.get("name") in WRITE_TOOLS:
                        inp = item.get("input") or {}
                        target = inp.get("file_path") or inp.get("notebook_path")
                        if target:
                            out.add(same_path(os.path.expanduser(target)))
    return out


def commits_this_turn(root, since, windows):
    """[(sha, [.md paths added or modified])] of this turn's commits made inside the windows of this session's `git
    commit` calls (the HEAD reflog)."""
    r = git(root, "reflog", "show", "--date=unix", "--format=%H %gd %gs", "HEAD", "-n", "50")
    out = []
    for line in r.stdout.splitlines():
        parts = line.split(" ", 2)
        if len(parts) < 3:
            continue
        sha_, sel, action = parts
        m = re.search(r"@\{(\d+)\}", sel)
        when = float(m.group(1)) if m else None
        if when is None or when < since - SLACK or not action.startswith("commit"):
            continue
        if not any(a <= when <= b for a, b in windows):
            continue
        files = git(root, "show", "--format=", "--name-only", "--diff-filter=AM", sha_).stdout.split("\n")
        out.append((sha_, [f for f in files if f.endswith(".md")]))
    return out


# ---------------------------------------------------------------------------------------------- state

def state_key(payload):
    sid = payload.get("session_id")
    if sid:
        return re.sub(r"[^A-Za-z0-9._-]", "_", str(sid))
    tp = payload.get("transcript_path")
    return re.sub(r"[^A-Za-z0-9._-]", "_", Path(tp).stem) if tp else None


def prune(states, now):
    """Delete session state and legacy markers untouched for 14 days (the gates' folder has its own bound)."""
    limit = now - KEEP_DAYS * 86400
    for folder in (states, states / "sessions"):
        if not folder.is_dir():
            continue
        for p in folder.iterdir():
            try:
                if p.is_file() and p.stat().st_mtime < limit:
                    p.unlink()
            except OSError:
                continue


def load_state(states, key, now):
    """(state, legacy marker to delete once saved): the session's saved state, else a legacy one-number marker read
    once (its number is `since`), else a first turn that looks back an hour."""
    empty = {"version": 2, "since": now - FIRST_TURN_LOOKBACK, "offsets": {}, "hashes": {}, "carry": None,
             "map_notes": {}}
    if not key:
        return empty, None
    try:
        data = json.loads((states / "sessions" / f"{key}.json").read_text(encoding="utf-8"))
        if isinstance(data, dict) and data.get("version") == 2:
            return dict(empty, **data), None
    except (OSError, ValueError):
        pass
    legacy = states / key
    try:
        return dict(empty, since=float(legacy.read_text(encoding="utf-8"))), legacy
    except (OSError, ValueError):
        return empty, None


def save_state(states, key, data, legacy=None):
    """Written once, at the end, atomically: a check killed before this leaves the previous state."""
    if not key:
        return
    folder = states / "sessions"
    folder.mkdir(parents=True, exist_ok=True)
    tmp = folder / f"{key}.json.tmp"
    tmp.write_text(json.dumps(data), encoding="utf-8")
    os.replace(tmp, folder / f"{key}.json")
    if legacy is not None:
        try:
            legacy.unlink()
        except OSError:
            pass


# ---------------------------------------------------------------------------------------------- the check

def check(payload, states=None, now=None, temp_dirs=None, config_dirs=None, budget=BUDGET):
    """The Stop hook's output as a JSON string (a block, a systemMessage, or both), or None when there is nothing to
    say."""
    started = time.monotonic()
    now = time.time() if now is None else now
    if payload.get("stop_hook_active"):
        return None  # the session is already continuing because of a stop hook: never block twice
    states = Path(states) if states else state_dir()
    temps = default_temp_dirs() if temp_dirs is None else [os.path.realpath(str(d)) for d in temp_dirs]
    try:
        prune(states, now)
    except OSError:
        pass
    transcript = Path(payload["transcript_path"]) if payload.get("transcript_path") else None
    if transcript is None or not transcript.is_file():
        return json.dumps({"systemMessage": NO_TRANSCRIPT})
    key = state_key(payload)
    state, legacy = load_state(states, key, now)
    since = float(state["since"])
    ev, offsets = Evidence(), dict(state.get("offsets") or {})
    files = session_files(transcript)
    for f in files:
        entries, size = read_new(f, offsets.get(str(f)), since)
        if entries is not None:
            scan(entries, ev, now)
            offsets[str(f)] = size
    carry = state.get("carry") or {}
    if carry:
        ev.merge(carry)
        since = min(since, float(carry.get("since") or since))
    finder = Roots()
    cwd = Path(payload.get("cwd") or os.getcwd())
    cwd_root = finder.of(cwd) if cwd.exists() else None
    own = own_roots(cwd, finder) if cwd.exists() else set()
    candidates = [Path(r) for r in carry.get("roots") or []]
    absolute = sorted(os.path.expanduser(t) for t in ev.named if os.path.isabs(os.path.expanduser(t)))
    for p in [str(cwd)] + sorted(ev.written) + absolute:
        if len(candidates) >= MAX_ROOTS:
            break
        found = finder.of(p)
        if found and found[0] not in candidates:
            candidates.append(found[0])
    roots = [r for r in candidates if not _under(r, temps) or (cwd_root and r == cwd_root[0])]
    found, messages, hashes = [], [], {}
    map_notes, unchecked, others = dict(state.get("map_notes") or {}), [], None

    def theirs(real):
        nonlocal others
        if others is None:
            others = other_sessions_written(closeout.config_dirs() if config_dirs is None else config_dirs, files,
                                            since - SLACK)
        return real in others

    def label(root, rel):
        return rel if len(roots) == 1 or root == roots[0] else f"{root}/{rel}"

    for root in roots:
        if time.monotonic() - started >= budget:   # >=: a budget spent to the tick (Windows' clock moves in ~16 ms
            # steps, so two reads in one tick differ by 0) is spent; with >, a budget of 0 still checked a root
            unchecked.append(str(root))
            continue
        ctx = closeout.link_context(root)
        if ctx["mode"] == "off" or not ctx["lanes"][0]["roadmap"]:
            continue
        note = closeout.stop_config_message(ctx)
        if note and map_notes.get(str(root)) != ctx["hash"]:
            messages.append(note)
            map_notes[str(root)] = ctx["hash"]
        in_git = finder.of(root)[1]
        if in_git:
            dirty = [root / rel for rel in dirty_md(root)]
        else:
            dirty = sorted({Path(p) for p in list(ev.written) + absolute
                            if p.endswith(".md") and Path(p).is_file() and finder.of(p) and finder.of(p)[0] == root})
        mine = same_path(root) in own  # naming counts only here
        names = {}
        for f in dirty:
            names[f.name] = names.get(f.name, 0) + 1
        for f in dirty:
            real, h = same_path(f), sha(f)
            hashes[real] = h
            try:
                mtime = f.stat().st_mtime
            except OSError:
                continue
            old = (state.get("hashes") or {}).get(real)
            changed = (h != old) if old is not None else mtime > since - SLACK
            why = BY_TOOLS
            if real not in ev.written:
                if not changed or not mine:
                    continue
                named = any(named_match(t, f, root, names) for t in ev.named)
                shown = any(a <= mtime <= b and any(named_match(t, f, root, names) for t in toks)
                            for a, b, toks in ev.outputs)
                if not (named or shown) or theirs(real):
                    continue
                why = BY_COMMAND if shown else NAMED
            rel = f.resolve().relative_to(Path(root).resolve()).as_posix()
            hit = closeout.judge(ctx, rel)
            if hit and hit[0] in ("MISSING", "BROKEN"):
                found.append(f"- {label(root, rel)} ({why}): {hit[0]} — {hit[1]}")
        if ev.commit_windows and in_git:
            for sha_, paths in commits_this_turn(root, since, ev.commit_windows):
                for rel in paths:
                    real = same_path(Path(root) / rel)
                    if real not in ev.written and theirs(real):
                        continue
                    hit = closeout.judge(ctx, rel, git(root, "show", f"{sha_}:{rel}").stdout)
                    if hit and hit[0] in ("MISSING", "BROKEN"):
                        found.append(f"- {label(root, rel)} (committed in {sha_[:8]} this turn; "
                                     f"{written_when(real, Path(root) / rel, ev, since)}): {hit[0]} — {hit[1]}")
    new_carry = None
    if unchecked:
        new_carry = dict(ev.to_json(), roots=unchecked, since=since)
        line = f"not checked this turn (time budget): {', '.join(unchecked)}; the next turn checks them first"
        if found:
            found.append(line)
        else:
            messages.append(f"doc-guardrails (end of turn): {line}")
    save_state(states, key, {"version": 2, "since": now, "offsets": offsets, "hashes": hashes, "carry": new_carry,
                             "map_notes": map_notes}, legacy)
    out = {}
    if found:
        out.update(decision="block", reason=REASON + "\n" + "\n".join(found))
    if messages:
        out["systemMessage"] = "\n".join(messages)
    return json.dumps(out) if out else None


def written_when(real, path, ev, since):
    """Why a document committed this turn is the session's, or that it may not be: written by its tools, changed during
    the turn (a command it ran), or last written before the turn began, the one case someone else may have written."""
    if real in ev.written:
        return BY_TOOLS
    try:
        if path.stat().st_mtime > since - SLACK:
            return "written this turn"
    except OSError:
        pass
    return "written before this turn: if someone else wrote it, leave it and say so"


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
