#!/usr/bin/env python3
"""What skill text a session holds, read from its transcript (R-28 § 5.1): the readers the freshness hook
(eli5/skill_fresh.py, on every prompt) and the live receipt check (eli5/receipt_check.py, on every stop) use.

Part of the closeout helper: closeout.py loads this module and re-exports every name below (closeout.held_skills,
closeout.skill_changes, closeout.turn_facts, …), so it stays the pack's one home for shared code. It is a module of its
own so the per-prompt hook imports only what it needs (json, os, re, mmap, zlib), never the whole helper: that import
is a fifth of the hook's budget (under 0.1 s on the largest live transcript). The file is searched as bytes and only
the lines that can matter are decoded; the hook's index (held_skills' `index`) keeps what it has read, so each prompt
reads only what was appended since. Stdlib only, Python 3.9+.
"""
import json
import mmap
import os
import re
from pathlib import Path

SKILL_PREFIX = "Base directory for this skill: "
_FRONT_RE = re.compile(r"\A---[ \t]*\r?\n.*?\r?\n---[ \t]*(?:\r?\n|\Z)", re.S)
_WINDOWS_PATH_RE = re.compile(r"^(?:[A-Za-z]:[\\/]|\\\\)")


def skill_name(folder):
    """A skill's name: the last part of its folder, whichever separator the path was written with."""
    return [p for p in re.split(r"[\\/]", str(folder).rstrip("\\/")) if p][-1] if str(folder).strip("\\/") else ""


def _skill_key(folder, cache=None):
    """The key a skill folder is held under: its real path (a load through each login's skills link is one skill). A
    Windows path read on another platform is kept as written. `cache` (one reading's dict) spares the stats: a long
    session loads the same folders again and again, and each real path costs a stat per path part."""
    folder = str(folder)
    if cache is not None and folder in cache:
        return cache[folder]
    key = folder if os.name != "nt" and _WINDOWS_PATH_RE.match(folder) else os.path.realpath(folder)
    if cache is not None:
        cache[folder] = key
    return key


_SCAN_CHUNK = 16 << 20   # bytes per scan window: bounded memory, and bytes.find is faster than mmap.find on 3.9


def _hits(mm, needles, start=0, stop=None):
    """{needle: [offset, …]} of every occurrence in the mapped file between `start` and `stop` (default: the end),
    found window by window with bytes.find (a hit that straddles two windows is found once, by the overlap)."""
    out = {n: [] for n in needles}
    overlap = max(len(n) for n in needles) - 1
    size = len(mm) if stop is None else stop
    off = start
    while off < size:
        chunk = mm[off:min(off + _SCAN_CHUNK + overlap, size)]
        for n in needles:
            pos = chunk.find(n)
            while pos >= 0:
                if pos < _SCAN_CHUNK or off + _SCAN_CHUNK >= size:
                    out[n].append(off + pos)
                pos = chunk.find(n, pos + 1)
        off += _SCAN_CHUNK
    return out


def _line_spans(mm, offsets, within=None):
    """(start, end) of each line of the mapped file holding one of the offsets (end excludes the newline), once per
    line; with `within`, only lines that also hold that byte string."""
    spans, last_end = [], -1
    for i in sorted(offsets):
        if i <= last_end:
            continue
        start = mm.rfind(b"\n", 0, i) + 1
        end = mm.find(b"\n", i)
        end = len(mm) if end < 0 else end
        last_end = end
        if within is None or mm.find(within, start, end) >= 0:
            spans.append((start, end))
    return spans


def _texts(content):
    if isinstance(content, str):
        return [content]
    return [c.get("text") for c in content or [] if isinstance(c, dict) and isinstance(c.get("text"), str)]


def _loads_in(record):
    """[(folder, body)] of the skill loads one transcript record carries: an isMeta user record whose text starts with
    the prefix (a typed or invoked load), or each entry of a compaction's invoked_skills attachment (a re-injection;
    an entry without the prefix, a bundled skill, names no folder and is skipped)."""
    out = []
    if record.get("type") == "user" and record.get("isMeta") is True:
        texts = _texts((record.get("message") or {}).get("content"))
    elif record.get("type") == "attachment" and (record.get("attachment") or {}).get("type") == "invoked_skills":
        texts = [e.get("content") for e in record["attachment"].get("skills") or [] if isinstance(e, dict)]
    else:
        return out
    for text in texts:
        if isinstance(text, str) and text.startswith(SKILL_PREFIX):
            head, _, body = text.partition("\n\n")
            folder = head[len(SKILL_PREFIX):].strip()
            if folder:
                out.append((folder, body))
    return out


def _file_lines(text):
    """A file's lines as Read numbers them: split at each newline (a final newline makes a last, empty line), a
    carriage return before it dropped."""
    return [line[:-1] if line.endswith("\r") else line for line in text.split("\n")]


_LOAD = SKILL_PREFIX.encode("utf-8")
_READ = b'"startLine"'            # a Read's result carries toolUseResult.file.startLine; a Write or Edit's does not
_RESULT = b'"toolUseResult"'
_FILE_PATH = b'"filePath"'
_BOUNDARY = b'"compact_boundary"'  # a compaction's boundary record (type system, subtype compact_boundary)
_DECODER = json.JSONDecoder()


class _Reader:
    """The held skills, built record by record in file order: `feed` reads the records of a byte range of the
    transcript that can matter (a skill load, a Read's result, a compaction's boundary) and applies them; nothing else
    is decoded. `compactions` counts the boundaries read."""

    def __init__(self, held=None, compactions=0):
        self.held = held if held is not None else {}
        self.compactions = compactions
        self.keys = {}

    def key(self, folder):
        return _skill_key(folder, self.keys)

    def owner(self, parent):
        """The held skill folder `parent` is, or lies under (the deepest one); None when it is in none."""
        if parent in self.held:
            return parent
        best = None
        for k in self.held:
            base = k.rstrip("\\/")
            if parent.startswith(base + os.sep) or (_WINDOWS_PATH_RE.match(k) and parent.startswith(base + "\\")):
                if best is None or len(k) > len(best):
                    best = k
        return best

    def _may_matter(self, mm, start, end):
        """Whether a Read's result line names a file inside a held skill's folder, read from its `filePath` values
        alone, so a Read of any other file is never decoded. A value that cannot be read this way says yes."""
        if not self.held:
            return False
        i = mm.find(_FILE_PATH, start, end)
        while i >= 0:
            q = mm.find(b'"', i + len(_FILE_PATH), min(end, i + len(_FILE_PATH) + 8))
            if q < 0:
                return True
            try:
                value, _ = _DECODER.raw_decode(mm[q:min(end, q + 8192)].decode("utf-8", "replace"))
            except ValueError:
                return True
            if not isinstance(value, str) or self.owner(self.key(os.path.dirname(value))):
                return True
            i = mm.find(_FILE_PATH, i + 1, end)
        return False

    def feed(self, mm, start, stop):
        hits = _hits(mm, (_LOAD, _READ, _BOUNDARY), start, stop)
        loads = set(_line_spans(mm, hits[_LOAD]))
        reads = set(_line_spans(mm, hits[_READ], within=_RESULT))
        bounds = set(_line_spans(mm, hits[_BOUNDARY]))
        for s, e in sorted(loads | reads | bounds):
            if (s, e) not in loads and (s, e) not in bounds and not self._may_matter(mm, s, e):
                continue
            try:
                record = json.loads(mm[s:e].decode("utf-8"))
            except ValueError:
                continue
            if isinstance(record, dict):
                self.apply(record)

    def apply(self, record):
        if record.get("type") == "system" and record.get("subtype") == "compact_boundary":
            self.compactions += 1
            return
        for folder, body in _loads_in(record):
            key = self.key(folder)
            self.held[key] = {"name": skill_name(folder), "loaded_at": record.get("timestamp") or "", "text": body,
                              "via": "load", "files": (self.held.get(key) or {}).get("files") or {}}
        result = record.get("toolUseResult")
        f = result.get("file") if isinstance(result, dict) else None
        if not (isinstance(f, dict) and isinstance(f.get("filePath"), str) and isinstance(f.get("content"), str)):
            return
        full = f.get("startLine") == 1 and f.get("numLines") == f.get("totalLines")
        parent = self.key(os.path.dirname(f["filePath"]))
        base = skill_name(f["filePath"])
        when = record.get("timestamp") or ""
        if base == "SKILL.md" and parent in self.held:      # a skill's own text: only a full Read holds it
            if full:
                self.held[parent] = dict(self.held[parent], text=f["content"], via="read",
                                         loaded_at=when or self.held[parent]["loaded_at"])
            return
        owner = self.owner(parent)
        start, total = f.get("startLine"), f.get("totalLines")
        if owner is None or not isinstance(start, int) or not isinstance(total, int):
            return
        lines = _file_lines(f["content"])
        if isinstance(f.get("numLines"), int) and 0 <= f["numLines"] < len(lines):
            lines = lines[:f["numLines"]]
        path = parent.rstrip("\\/") + ("\\" if _WINDOWS_PATH_RE.match(parent) and os.name != "nt" else os.sep) + base
        files = self.held[owner].setdefault("files", {})
        entry = files.get(path)
        if entry is None or full:          # a full Read replaces every line held before; a chunk only its own lines
            entry = files[path] = {"read_at": "", "total": total, "lines": {}}
        for n, line in enumerate(lines, start):
            entry["lines"][str(n)] = line
        entry["total"], entry["read_at"] = total, when or entry["read_at"]


INDEX_VERSION = 2     # 2: the index keeps the count of compaction boundaries read
_PRINT = 4096      # bytes at each end of the part an index has read, whose checksums tell a rewritten transcript
KEEP_DAYS = 14


def state_dir():
    """The doc-guardrails state folder both logins share (DOC_GUARDRAILS_STATE, else ~/.claude/doc-guardrails-state),
    the same rule as closeout.state_dir: that module is not imported here (the per-prompt budget)."""
    return Path(os.environ.get("DOC_GUARDRAILS_STATE") or Path.home() / ".claude" / "doc-guardrails-state")


def index_dir():
    """Where the freshness hook keeps one index per transcript."""
    return state_dir() / "skill-index"


def _prints(mm, size):
    import zlib
    return [zlib.crc32(mm[:min(size, _PRINT)]), zlib.crc32(mm[max(0, size - _PRINT):size])]


def _index_file(folder, real):
    import zlib
    stem = re.sub(r"[^A-Za-z0-9._-]", "_", os.path.basename(real))[:80]
    return Path(folder) / f"{stem}-{zlib.crc32(real.encode('utf-8', 'surrogateescape')):08x}.json"


def _read_index(where, real, mm):
    """(held, compactions, size) the index recorded, or ({}, 0, 0) when there is none, it is unreadable, or the
    transcript is not the one it read (another path, shorter now, or other bytes at either end of the part it read)."""
    try:
        data = json.loads(Path(where).read_text(encoding="utf-8"))
        size, compactions = data["size"], data["compactions"]
        if data.get("version") == INDEX_VERSION and data.get("path") == real and isinstance(data.get("held"), dict) \
                and isinstance(size, int) and 0 < size <= len(mm) and data.get("prints") == _prints(mm, size) \
                and isinstance(compactions, int) and compactions >= 0:
            return data["held"], compactions, size
    except (OSError, ValueError, TypeError, KeyError, AttributeError):
        pass
    return {}, 0, 0


def _write_index(where, real, mm, size, reader, fresh):
    """Written whole, through a temp file and os.replace, so a reader never sees half of one. A first index for a
    transcript also clears indexes untouched for KEEP_DAYS. A failure costs only the next run a longer read."""
    import time
    where = Path(where)
    try:
        where.parent.mkdir(parents=True, exist_ok=True)
        if fresh:
            limit = time.time() - KEEP_DAYS * 86400
            for p in where.parent.iterdir():
                try:
                    if p.is_file() and p.stat().st_mtime < limit:
                        p.unlink()
                except OSError:
                    continue
        tmp = where.parent / f".{where.name}.{os.getpid()}.tmp"
        tmp.write_text(json.dumps({"version": INDEX_VERSION, "path": real, "size": size, "prints": _prints(mm, size),
                                   "compactions": reader.compactions, "held": reader.held}), encoding="utf-8")
        os.replace(tmp, where)
    except OSError:
        pass


def held_skills(transcript_path, index=None):
    """read_session's held skills alone."""
    return read_session(transcript_path, index)[0]


def read_session(transcript_path, index=None):
    """(held, compactions): what the transcript gave the session, and how many compaction boundaries it holds (each
    replaced the context before it with a summary). held is {real path of a skill folder: {"name", "loaded_at", "text",
    "via": "load" | "read", "files"}}: the last text of each skill the transcript gave the session, taken in file
    order (a compaction's re-injection of an older body after
    a newer load makes the older one held; the order is the context's, never the timestamps). A load is a record
    `_loads_in` finds; a Read of a skill's SKILL.md counts only when it read the whole file (startLine 1, numLines ==
    totalLines) of a skill already held, and then holds that file's text. `files` holds every other file the session
    Read inside a held skill's folder: {real path: {"read_at", "total", "lines": {"<n>": text}}}, each line as
    the last Read that covered it gave it (a full Read replaces them all). Empty or absent file: {}. A malformed line
    is skipped.

    With `index` (a folder), the reading resumes where the last one on this transcript stopped: the index keeps the
    result and the byte offset it reached (the end of its last complete line), so only what was appended since is
    read, as turn_check.py keeps its offsets. A transcript that is shorter, or whose bytes at either end of the part
    read differ, is read again whole. An unfinished last line is read but not kept. The result is the one a whole read
    gives."""
    path = Path(transcript_path)
    if not path.exists():
        return {}, 0
    with open(path, "rb") as fh:
        size = os.fstat(fh.fileno()).st_size
        if size == 0:
            return {}, 0
        with mmap.mmap(fh.fileno(), 0, access=mmap.ACCESS_READ) as mm:
            size = len(mm)
            if index is None:
                reader = _Reader()
                reader.feed(mm, 0, size)
                return reader.held, reader.compactions
            real = os.path.realpath(str(path))
            where = _index_file(index, real)
            held, compactions, done = _read_index(where, real, mm)
            reader = _Reader(held, compactions)
            newline = mm.rfind(b"\n", done, size)
            keep = newline + 1 if newline >= 0 else done
            reader.feed(mm, done, keep)
            if keep != done:
                _write_index(where, real, mm, keep, reader, fresh=done == 0)
            if keep < size:
                reader.feed(mm, keep, size)
            return reader.held, reader.compactions


def skill_body(text):
    """A SKILL.md's text as a load injects it: the front matter dropped, the rest stripped."""
    return _FRONT_RE.sub("", text, count=1).strip()


def _skill_md(key):
    """The SKILL.md path of a held skill's key (a Windows path read on another platform keeps its separator)."""
    return os.path.join(key, "SKILL.md") if not _WINDOWS_PATH_RE.match(key) or os.name == "nt" \
        else key.rstrip("\\/") + "\\SKILL.md"


def skill_changes(held):
    """[(name, SKILL.md path, loaded_at, "changed" | "gone")] for every held skill whose file is gone or no longer holds
    the text the session has. A load holds the body (a typed command may append `ARGUMENTS: …` after it); a full Read
    holds the whole file."""
    out = []
    for key, h in held.items():
        skill_md = _skill_md(key)
        try:
            now = Path(skill_md).read_text(encoding="utf-8")
        except FileNotFoundError:
            out.append((h["name"], skill_md, h["loaded_at"], "gone"))
            continue
        except (OSError, ValueError):
            if not os.path.exists(skill_md):
                out.append((h["name"], skill_md, h["loaded_at"], "gone"))
                continue
            raise
        if h["via"] == "read":
            same = h["text"] == now
        else:
            held_text, body = h["text"].strip(), skill_body(now)
            rest = held_text[len(body):] if held_text.startswith(body) else None
            same = rest is not None and (rest == "" or rest.lstrip("\n").startswith("ARGUMENTS:"))
        if not same:
            out.append((h["name"], skill_md, h["loaded_at"], "changed"))
    return out


def file_changes(held):
    """[(name, file path, read_at, "changed" | "gone")] for every file other than SKILL.md the session Read inside a
    held skill's folder that is gone, or whose content is not what those Reads gave: a line read that differs
    now, or another line count than the last Read reported (a line added or removed anywhere)."""
    out = []
    for key, h in held.items():
        for path, entry in sorted((h.get("files") or {}).items()):
            try:
                now = _file_lines(Path(path).read_text(encoding="utf-8", errors="replace"))
            except FileNotFoundError:
                out.append((h["name"], path, entry["read_at"], "gone"))
                continue
            except OSError:
                if not os.path.exists(path):
                    out.append((h["name"], path, entry["read_at"], "gone"))
                    continue
                raise
            if entry["total"] != len(now) or any(int(n) > len(now) or now[int(n) - 1] != line
                                                 for n, line in entry["lines"].items()):
                out.append((h["name"], path, entry["read_at"], "changed"))
    return out


def change_bases(held):
    """{path: basis} for every SKILL.md and other file the session holds text of, as skill_changes and file_changes name
    them: the basis is a short fingerprint of the text the session holds (how and when it was taken, and a checksum of
    it), so a new load, re-injection or Read gives a new basis. The freshness hook keys what it has named by it."""
    import zlib

    def crc(value):
        return f"{zlib.crc32(json.dumps(value, sort_keys=True).encode('utf-8', 'surrogateescape')):08x}"

    out = {}
    for key, h in held.items():
        out[_skill_md(key)] = f"{h.get('via')}|{h.get('loaded_at')}|{crc(h.get('text'))}"
        for path, entry in (h.get("files") or {}).items():
            out[path] = f"read|{entry.get('read_at')}|{entry.get('total')}|{crc(entry.get('lines'))}"
    return out


def _is_prompt(record):
    """A record that starts a turn: a user record that is not a tool result, not a compaction's summary (which reads
    like a prompt, R-28 § 4.4) and not a meta record inside a turn (a skill load, a hook's feedback). A meta record
    that carries its prompt's origin or source starts a turn all the same: Claude Code writes a message from another
    session (`origin.kind` "peer") and the cross-session idle notice as meta records with a `promptSource` (seen live
    2026-09-30); reading through them gave a bare acknowledgement the previous reply's tool calls."""
    if record.get("type") != "user" or record.get("isCompactSummary") or "toolUseResult" in record:
        return False
    if record.get("isMeta") and not (record.get("origin") or record.get("promptSource")):
        return False
    content = (record.get("message") or {}).get("content")
    if isinstance(content, list):
        return not any(isinstance(c, dict) and c.get("type") == "tool_result" for c in content)
    return isinstance(content, str)


def turn_facts(transcript_path):
    """{"ran_tools", "last_text"} of the turn in progress: from the record that started it (_is_prompt: a prompt, a
    task notice or another session's message) to the end of the transcript,
    read backwards with mmap. A compaction inside the turn changes nothing: its summary record is not a prompt, so tool
    calls made before it still count."""
    path = Path(transcript_path)
    out = {"ran_tools": False, "last_text": ""}
    if not path.exists() or path.stat().st_size == 0:
        return out
    with open(path, "rb") as fh, mmap.mmap(fh.fileno(), 0, access=mmap.ACCESS_READ) as mm:
        end = len(mm)
        text_found = False
        while end > 0:
            start = mm.rfind(b"\n", 0, end - 1) + 1 if end > 1 else 0
            if mm.find(b'"type":"user"', start, end) < 0 and mm.find(b'"type": "user"', start, end) < 0 \
                    and mm.find(b'"tool_use"', start, end) < 0 and (text_found or mm.find(b'"text"', start, end) < 0):
                end = start - 1 if start > 0 else 0
                continue
            try:
                record = json.loads(mm[start:end].decode("utf-8"))
            except ValueError:
                record = None
            if isinstance(record, dict):
                if _is_prompt(record):
                    break
                if record.get("type") == "assistant":
                    content = (record.get("message") or {}).get("content")
                    items = content if isinstance(content, list) else []
                    if any(isinstance(c, dict) and c.get("type") == "tool_use" for c in items):
                        out["ran_tools"] = True
                    texts = [c["text"] for c in items if isinstance(c, dict) and c.get("type") == "text"
                             and isinstance(c.get("text"), str)]
                    if texts and not text_found:
                        out["last_text"], text_found = "\n".join(texts), True
            end = start - 1 if start > 0 else 0
    return out
