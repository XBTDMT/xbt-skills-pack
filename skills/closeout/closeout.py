#!/usr/bin/env python3
"""closeout.py — the deterministic half of the /closeout and /project-update skills.

  closeout.py facts [FOLDER]                     what is true now: git state, the pin, handoff, overview and tracking
                                                 docs, generated and never-hand-edit files, Claude Code's memory for
                                                 this folder
  closeout.py memory [FOLDER]                    every memory entry about this project, in any memory folder, with
                                                 duplicate / unindexed / dangling index lines and paths that are gone
  closeout.py worklist [FOLDER] --docs F [F ...] one checklist item per doc and per line that carries a figure
  closeout.py tree  [FOLDER] [--depth N]         the git-tracked file tree; folders deeper than N show a file count
                    [--also F ...]               ... plus the files this close-out is adding
  closeout.py stamp PIN [--marker SHA]           set the pin's `last_marker:` and `updated:` lines
                        [--keep-marker]          ... or only `updated:`, for a pin whose marker is frozen
  closeout.py hunks FILE                         number the file's unstaged hunks
  closeout.py stage-hunks FILE N [N ...]         stage only those hunks (another session's edit stays unstaged)
  closeout.py audit [FOLDER] --changed F [F ...] [--pin PIN] [--worklist WL]
                                                 check what is about to be committed; exit 1 on a failure
  closeout.py link-audit [FOLDER] [--strict]     the LINKING.md contract: report-only unless --strict
  closeout.py link-audit [FOLDER] --staged       the commit gate: staged linked docs only; blocks on `guardrails: block`
  closeout.py install-gate [FOLDER]              add that gate to the repository's pre-commit hook, keeping any hook there

Stdlib only, Python 3.9 or newer, on Windows, macOS and Linux. It writes only through `stamp` (the pin it is given),
`stage-hunks` (the index), `link-audit` without `--no-inventory`, `--file` or `--staged` (the UNLINKED inventory in
`knowledge/`, else `docs/`, replacing older ones) and `install-gate` (the repository's pre-commit hook). CLOSEOUT_CONFIG_DIRS (os.pathsep-separated)
replaces the Claude Code config folders it reads memory from — a sandbox run points it at copies.
"""
from __future__ import annotations

import argparse
import datetime
import html
import json
import os
import re
import shlex
import subprocess
import sys
from pathlib import Path
from urllib.parse import unquote, urlsplit

PIN_DEFAULT = "knowledge/00-CURRENT.md"
HANDOFF_RE = re.compile(r"(^|/)[^/]*(handoff|start-prompt|pickup)[^/]*\.md$", re.I)
OVERVIEW_RE = re.compile(r"(^|/)[^/]*overview[^/]*\.html?$", re.I)
UPDATED_RE = re.compile(r"^\s*updated:\s*([0-9]{4}-[0-9]{2}-[0-9]{2})", re.M)
MARKER_RE = re.compile(r"^\s*last_marker:\s*([0-9a-f]{7,40})", re.M)
# The pin's next action: the first line under a heading that starts with NEXT or PICK UP HERE.
NEXT_RE = re.compile(r"^#+\s*(?:NEXT|PICK UP HERE)[^\n]*\n+(.+)", re.M | re.I)
NO_ADVANCE_RE = re.compile(
    r"(?:do not|don't|never)\s+advance\s+`?last_marker|last_marker`?\s+(?:is\s+)?deliberately\s+not\s+advanced", re.I)
SECRET_RES = [re.compile(p) for p in (
    r"sk-ant-[A-Za-z0-9_-]{16,}", r"\bsk-[A-Za-z0-9]{32,}", r"\bghp_[A-Za-z0-9]{30,}", r"\bgithub_pat_[A-Za-z0-9_]{30,}",
    r"\bAKIA[0-9A-Z]{16}\b", r"\bxox[bpas]-[A-Za-z0-9-]{10,}", r"-----BEGIN [A-Z ]*PRIVATE KEY-----",
    # Shapes real projects handle, so a handoff cannot carry them: a DSN with its password
    # (`postgres://app:hunter2@127.0.0.1:5432/db`), and any `KEY=value` / `key: "value"` whose name says it is a
    # secret and whose value is long enough to be one — an empty or placeholder value (`DATA_API_KEY=`, `<your key>`)
    # is not caught.
    # A DSN password that starts with `<`, `$`, `*` or `{`, or reads `u:p`, `PW`, `CHANGE_ME`, `password`,
    # `redacted`, is a placeholder, not a secret; so is a value with a dot in it (`process.env.KEY`).
    r"\b(?:postgres(?:ql)?|mysql|redis|mongodb(?:\+srv)?|amqp)://[^:/\s@]+:"
    r"(?!(?:p|PW|CHANGE_ME|password|redacted|x+)@)[^<$*{@\s][^@\s]{2,}@",
    r"(?i)\b[A-Z0-9_]*(?:api[_-]?key|api[_-]?token|secret|password|passwd|access[_-]?token)\s*[=:]\s*['\"]?[A-Za-z0-9_\-]{16,}(?![.\w])")]
PLACEHOLDER_RE = re.compile(r"\bTBD\b|\bFIXME\b|<fill[^>]*>|\?\?\?")
CONFLICT_RE = re.compile(r"^(<{7}|>{7}|={7})(\s|$)", re.M)
FILE_EXTS = {"md", "html", "htm", "py", "swift", "js", "mjs", "ts", "tsx", "json", "jsonl", "sh", "plist", "yml",
             "yaml", "toml", "txt", "css", "csv", "pdf", "png", "jpg", "svg", "sql", "rb", "go", "rs", "c", "h", "m"}
# The project's own tracking docs, where "done" is recorded: named for that job (`00-PROJECT-ROADMAP.md`,
# `SPRINT-LEDGER.md`, `00-OPEN-ITEMS.md`), not every file that mentions a sprint (`02-ADDENDUM-sprint-15.10.md`).
TRACKING_RE = re.compile(r"(^|/)(?:\d+[-_])?(?:project[-_])?(?:roadmap|sprints?|milestones?|backlog|open[-_]items|todo"
                         r"|ledger|decisions?|lessons|brain|changelog|board|status)"
                         r"(?:[-_](?:index|board|ledger|log|items|plan))?\.(md|html?|csv|txt)$", re.I)
# Copies kept for history or as starters are not the project's live docs: `docs/backups/pre_x/TODO.md`.
ARCHIVE_RE = re.compile(r"(^|/)(backups?|archived?|archives|old|templates?|vendor|node_modules)/", re.I)
# A file that says it is built by a command — at the start of a line in its header, so `project.yml` saying
# "the .xcodeproj is generated from this file" is not read as generated itself.
GENERATED_RE = re.compile(r"^[\s#/*<!\-\"'>{]*(?:auto-?generated\b|generated\b(?:\s+(?:by|with|on|at)\b"
                          r"|:?\s+\d{4}-\d{2}-\d{2}|[\"']?\s*:\s*[\"']\d{4})|this (?:file|page|document) is "
                          r"(?:auto-?)?generated|derived\s*[—–-]+\s*do not|do not (?:hand-)?edit\b)", re.I | re.M)
GENERATED_EXTS = (".md", ".html", ".htm", ".txt", ".json", ".yml", ".yaml", ".csv", ".js")
HAND_WRITTEN = {"CLAUDE.md", "README.md"}  # their opening lines often say "do not edit X by hand" about OTHER files
# What a CLAUDE.md says about which doc is the truth, which is generated, which is append-only or tool-owned.
DOC_RULE_RE = re.compile(r"source of truth|single source|canonical|authoritative|\bgenerated\b|hand-edit|by hand"
                         r"|do not edit|append-only|derived from|re-derive", re.I)
NO_HAND_EDIT_RE = re.compile(r"(?:never|do not|don't)\s+(?:hand-edit|edit\b[^.]*\bby hand)", re.I)


def run_git(folder, *args):
    # Git prints UTF-8; Windows' default text encoding is not, so name it rather than inherit the locale.
    r = subprocess.run(["git", "-C", str(folder), *args], capture_output=True, encoding="utf-8", errors="replace")
    return r.returncode, r.stdout.rstrip("\n")


def git(folder, *args):
    code, out = run_git(folder, *args)
    return out if code == 0 else ""


def repo_root(folder):
    code, out = run_git(folder, "rev-parse", "--show-toplevel")
    return Path(out) if code == 0 and out else None


def project_files(folder, untracked=True):
    """Tracked (plus, by default, untracked-but-not-ignored) files, relative to the folder. Without git, a walk."""
    root = repo_root(folder)
    if root is not None:
        out = git(root, "ls-files", "--cached", *(("--others", "--exclude-standard") if untracked else ()))
        return sorted(set(out.splitlines())) if out else []
    found = []
    for dirpath, dirnames, filenames in os.walk(folder):
        dirnames[:] = sorted(d for d in dirnames if not d.startswith(".") and d not in ("node_modules", "build"))
        for name in filenames:
            found.append(Path(dirpath, name).relative_to(folder).as_posix())
    return sorted(found)


def doc_rules(folder, files):
    """The lines of CLAUDE.md that say which docs are the truth, generated, append-only or tool-owned, and the
    files it says must never be edited by hand."""
    p = Path(folder) / "CLAUDE.md"
    if not p.is_file():
        return [], []
    rules, protected = [], set()
    by_name = {f.rsplit("/", 1)[-1]: f for f in files}
    for i, line in enumerate(p.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
        if DOC_RULE_RE.search(line):
            rules.append(f"CLAUDE.md:{i}: {line.strip()[:200]}")
        if NO_HAND_EDIT_RE.search(line):
            for tok in re.findall(r"`([^`\s]+)`", line):
                if tok in files or tok in by_name:
                    protected.add(tok if tok in files else by_name[tok])
    return rules[:40], sorted(protected)


def is_generated(path):
    try:
        with open(path, "rb") as fh:
            return bool(GENERATED_RE.search(fh.read(600).decode("utf-8", "replace")))
    except OSError:
        return False


def generated_docs(folder, files):
    """Files whose first lines say a command builds them: re-run the command, never hand-edit."""
    return [f for f in files if f.lower().endswith(GENERATED_EXTS) and f.rsplit("/", 1)[-1] not in HAND_WRITTEN
            and is_generated(Path(folder) / f)]


def config_dirs():
    """Claude Code config folders on this computer: this session's first, then the default one.
    CLOSEOUT_CONFIG_DIRS replaces the whole list, so a sandbox run reads and edits copies, never the real memory."""
    if os.environ.get("CLOSEOUT_CONFIG_DIRS"):
        return [Path(p).expanduser() for p in os.environ["CLOSEOUT_CONFIG_DIRS"].split(os.pathsep) if p]
    dirs = [Path(os.environ["CLAUDE_CONFIG_DIR"]).expanduser()] if os.environ.get("CLAUDE_CONFIG_DIR") else []
    return dirs + [Path.home() / ".claude"]


INDEX_LINK_RE = re.compile(r"\]\(([^)\s]+\.md)\)")
# An index line that tells the next session where to start: a second one for the same project is a decoy.
ENTRY_POINT_RE = re.compile(r"START HERE|PICK UP HERE|⇒", re.I)
KIND_ORDER = {"own": 0, "home": 1, "other": 2}


def escape(path):
    """Claude Code's name for a folder under `projects/`."""
    return re.sub(r"[^A-Za-z0-9]", "-", str(path))


def read_index(m):
    p = m / "MEMORY.md"
    return p.read_text(encoding="utf-8", errors="replace") if p.is_file() else ""


def duplicates(lines):
    seen, dup = set(), []
    for line in lines:
        if line in seen and line not in dup:
            dup.append(line)
        seen.add(line)
    return dup


def memory_dirs(folder):
    """Claude Code's per-project memory for this folder — each real folder once (two config folders can link to
    the same `projects`) — with the index problems worth fixing at a close-out."""
    escaped = escape(Path(folder).resolve())
    found, seen = [], set()
    for cfg in config_dirs():
        m = cfg / "projects" / escaped / "memory"
        if not m.is_dir() or m.resolve() in seen:
            continue
        seen.add(m.resolve())
        entries = sorted(p.name for p in m.glob("*.md") if p.name != "MEMORY.md")
        index = read_index(m)
        linked = set(INDEX_LINK_RE.findall(index))
        found.append({"path": str(m), "index": (m / "MEMORY.md").is_file(), "entries": len(entries),
                      "not_in_index": [e for e in entries if e not in linked],
                      "index_points_at_missing": sorted(l for l in linked if not (m / l).is_file()),
                      "duplicate_index_lines": duplicates([l.rstrip() for l in index.splitlines() if l.strip()])})
    return found


def project_name_re(folder):
    """The project's name as prose writes it (`garden_app`, `garden-app`, `Garden App`) or its path."""
    folder = Path(folder).resolve()
    name = r"[-_ ]".join(re.escape(p) for p in re.split(r"[-_ ]+", folder.name) if p)
    paths = {re.escape(str(folder))}
    home = str(Path.home())
    if str(folder).startswith(home + os.sep):
        paths.add(re.escape("~/" + Path(str(folder)[len(home) + 1:]).as_posix()))
    return re.compile(rf"(?<![A-Za-z0-9]){name}(?![A-Za-z0-9])|" + "|".join(sorted(paths)), re.I)


def memory_report(folder):
    """Every memory entry about this project, wherever it is: the folder's own memory (all of it), the home folder's
    (sessions started from ~ write there — most project memory lives in it) and other projects'. For the shared
    folders only this project's entries and index lines are reported, so a refresh never tidies another's."""
    folder = Path(folder).resolve()
    own_name, home_name = escape(folder), escape(Path.home())
    name_re = project_name_re(folder)
    files = project_files(folder)
    dirs, dead, seen = [], [], set()
    for cfg in config_dirs():
        for m in sorted((cfg / "projects").glob("*/memory")) if (cfg / "projects").is_dir() else []:
            if not m.is_dir() or m.resolve() in seen:
                continue
            seen.add(m.resolve())
            kind = "own" if m.parent.name == own_name else "home" if m.parent.name == home_name else "other"
            entries = sorted(p.name for p in m.glob("*.md") if p.name != "MEMORY.md")
            texts = {e: (m / e).read_text(encoding="utf-8", errors="replace") for e in entries}
            about = entries if kind == "own" else [e for e in entries if name_re.search(texts[e])]
            index = [l.rstrip() for l in read_index(m).splitlines() if l.strip()]
            linked = {x for l in index for x in INDEX_LINK_RE.findall(l)}
            lines = index if kind == "own" else [
                l for l in index if name_re.search(l) or any(x in about for x in INDEX_LINK_RE.findall(l))]
            if kind != "own" and not about and not lines:
                continue
            dirs.append({"path": str(m), "kind": kind, "entries": len(entries), "about_project": about,
                         "not_in_index": [e for e in about if e not in linked],
                         "index_points_at_missing": sorted({x for l in lines for x in INDEX_LINK_RE.findall(l)
                                                            if not (m / x).is_file()}),
                         "duplicate_index_lines": duplicates(lines),
                         "entry_points": duplicates_removed([l for l in lines if ENTRY_POINT_RE.search(l)
                                                             and (kind == "own" or name_re.search(l))])})
            for e in about:
                for span in re.findall(r"`([^`\n]+)`", texts[e]):
                    # A relative path in a shared folder's entry has no known folder to be relative to.
                    dead += [{"dir": kind, "entry": e, "path": w} for w in missing_paths(span, folder, folder, files)
                             if kind == "own" or w.startswith(("/", "~/"))]
    dirs.sort(key=lambda d: KIND_ORDER[d["kind"]])
    dead.sort(key=lambda d: KIND_ORDER[d["dir"]])
    return {"project": folder.name, "dirs": dirs, "dead_paths": dead}


def duplicates_removed(lines):
    return [l for i, l in enumerate(lines) if l not in lines[:i]]


# ---------------------------------------------------------------------------------------------------- facts

def pin_facts(root, folder, rel):
    p = (folder / rel) if not Path(rel).is_absolute() else Path(rel)
    if not p.is_file():
        return {"path": str(rel), "exists": False}
    text = p.read_text(encoding="utf-8", errors="replace")
    info = {"path": str(rel), "exists": True, "lines": text.count("\n") + 1}
    m = UPDATED_RE.search(text)
    info["updated"] = m.group(1) if m else None
    m = NEXT_RE.search(text)
    info["next"] = m.group(1).strip()[:160] if m else None
    info["marker_frozen_by_pin"] = bool(NO_ADVANCE_RE.search(text))
    m = MARKER_RE.search(text)
    info["last_marker"] = m.group(1) if m else None
    if root is not None and info["last_marker"]:
        info["since_marker"] = since_marker(root, info["last_marker"], p)
    return info


def since_marker(root, marker, pin_path):
    """Commits after the marker that touched something other than the pin: work the pin does not describe."""
    if run_git(root, "cat-file", "-e", marker + "^{commit}")[0] != 0:
        return {"state": "unknown-marker"}
    if run_git(root, "merge-base", "--is-ancestor", marker, "HEAD")[0] != 0:
        return {"state": "diverged"}
    try:
        pin_rel = pin_path.resolve().relative_to(root.resolve()).as_posix()  # git names paths with /
    except ValueError:
        pin_rel = None
    log = git(root, "log", "--format=@%h %s", "--name-only", marker + "..HEAD")
    unseen, commit, files = [], None, []
    for line in log.splitlines() + ["@"]:
        if line.startswith("@"):
            if commit and any(f != pin_rel for f in files):
                unseen.append(commit)
            commit, files = line[1:].strip() or None, []
        elif line.strip():
            files.append(line.strip())
    return {"state": "current" if not unseen else "behind", "commits_not_in_pin": len(unseen), "commits": unseen[:20]}


def facts(folder):
    folder = Path(folder).resolve()
    root = repo_root(folder)
    out: dict = {"folder": str(folder), "today": datetime.date.today().isoformat()}
    if root is None:
        out["git"] = None
    else:
        g = {"root": str(root), "branch": git(root, "symbolic-ref", "--short", "-q", "HEAD") or None,
             "head": git(root, "rev-parse", "--short", "HEAD") or None}
        staged, modified, untracked = [], [], []
        for line in git(root, "status", "--porcelain=v1").splitlines():
            xy, path = line[:2], line[3:]
            if xy == "??":
                untracked.append(path)
                continue
            if xy[0] not in " ?":
                staged.append(path)
            if xy[1] not in " ?":
                modified.append(path)
        g.update(staged=staged, modified_unstaged=modified, untracked=untracked)
        code, up = run_git(root, "rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}")
        if code == 0 and up:
            g["upstream"] = up
            g["unpushed"] = int(git(root, "rev-list", "--count", "@{u}..HEAD") or 0)
        else:
            g["upstream"] = None
            g["remotes"] = git(root, "remote").split()
        g["worktrees"] = [l[9:] for l in git(root, "worktree", "list", "--porcelain").splitlines()
                          if l.startswith("worktree ")]
        g["stashes"] = len(git(root, "stash", "list").splitlines())
        g["recent"] = git(root, "log", "-10", "--format=%h %cs %s").splitlines()
        out["git"] = g
    files = project_files(folder)
    live = [f for f in files if not ARCHIVE_RE.search(f)]
    _, lc = link_audit(folder, inventory=False)
    out["guardrails"] = {"unlinked_docs": lc["MISSING"], "broken_links": lc["BROKEN"] + lc["NO FILE"] + lc["NO ANCHOR"],
                         "roadmap_ids": None if lc.get("no_roadmap") else lc.get("ids", 0)}
    ledgers, records = skill_records(folder)
    open_rows = sum(open_ledger_rows(led) for led in ledgers)
    out["redman"] = {"open_rows": open_rows, "ledgers": [l.relative_to(folder).as_posix() for l in ledgers]}
    side = folder / PACK_SIDE_LEDGER
    out["pack_side_ledger"] = {"path": PACK_SIDE_LEDGER, "open_rows": open_ledger_rows(side)} if side.is_file() else None
    dledgers = decision_ledgers(folder)
    out["untracked_records"] = untracked_records(root, ledgers + records + dledgers, folder)
    def with_status(pattern):
        return [r.relative_to(folder).as_posix() for r in records
                if re.search(STATUS_LINE + r"[^\n]*?" + pattern, r.read_text(encoding="utf-8", errors="replace"), re.M | re.I)
                ] + ledger_rows_with_status(folder, dledgers, records, pattern)
    # a status can combine states ("ruled by the user …: … — shortcut taken — real fix owed"), so match anywhere on the line
    out["greenman"] = {"decisions": len(records), "awaiting_ruling": with_status(r"\bawaiting\b"),
                       "real_fix_owed": with_status(r"shortcut taken")}
    out["docs"] = {
        "claude_md": (folder / "CLAUDE.md").is_file(),
        "closeout_map_in_claude_md": bool((folder / "CLAUDE.md").is_file() and re.search(
            r"close-?out map", (folder / "CLAUDE.md").read_text(encoding="utf-8", errors="replace"), re.I)),
        "readme": next((f for f in ("README.md", "README") if (folder / f).is_file()), None),
        "handoff_candidates": [f for f in live if HANDOFF_RE.search(f)],
        "overview_candidates": [f for f in live if OVERVIEW_RE.search(f)],
        "tracking_candidates": [f for f in live if TRACKING_RE.search(f) and not HANDOFF_RE.search(f)],
        "generated": generated_docs(folder, live),
    }
    out["docs"]["rules_in_claude_md"], out["docs"]["never_hand_edit"] = doc_rules(folder, files)
    out["memory"] = memory_dirs(folder)
    out["memory_elsewhere"] = [{"path": d["path"], "kind": d["kind"], "about_project": d["about_project"]}
                               for d in memory_report(folder)["dirs"] if d["kind"] != "own"]
    pins = []
    if (folder / PIN_DEFAULT).is_file():
        pins.append(PIN_DEFAULT)
    out["pins"] = [pin_facts(root, folder, p) for p in pins]
    out["baseline"] = baseline(out["docs"], out["pins"])
    return out


BASELINE = ("readme", "claude_md", "pin", "handoff", "overview", "roadmap", "brain")
# A plan, not a record of what happened: `00-PROJECT-ROADMAP.md`, `OPEN-ITEMS.md`, `SPRINT-BOARD.html`, `TODO.md`.
PLAN_RE = re.compile(r"roadmap|board|milestones?|backlog|open[-_]items|todo|sprints?", re.I)
RECORD_RE = re.compile(r"ledger|log|decision|lesson|brain|status", re.I)
# The lessons log: `00-BRAIN.md`, `00-PROJECT-BRAIN.md`, `LESSONS.md`.
BRAIN_RE = re.compile(r"brain|lessons", re.I)


def baseline(docs, pins):
    """The docs every project has (UPDATING.md § Baseline): the files holding each, and which are missing. A dated
    handoff is not a pin even when something points at it as one — its path changes every close-out."""
    names = [f.rsplit("/", 1)[-1] for f in docs["tracking_candidates"]]
    present = {
        "readme": [docs["readme"]] if docs["readme"] else [],
        "claude_md": ["CLAUDE.md"] if docs["claude_md"] else [],
        "pin": [p["path"] for p in pins if p.get("exists") and not re.search(r"\d{4}-\d{2}-\d{2}", p["path"])],
        "handoff": list(docs["handoff_candidates"]),
        "overview": list(docs["overview_candidates"]),
        "roadmap": [f for f, n in zip(docs["tracking_candidates"], names) if PLAN_RE.search(n) and not RECORD_RE.search(n)],
        "brain": [f for f, n in zip(docs["tracking_candidates"], names) if BRAIN_RE.search(n)],
    }
    return {"present": {k: v for k, v in present.items() if v}, "missing": [k for k in BASELINE if not present[k]]}


# ---------------------------------------------------------------------------------------------------- tree

def tree(folder, depth, also=()):
    """Tracked files only, plus the ones named in `also` (what this close-out is adding). Another session's
    untracked work never appears in a map that gets committed."""
    folder = Path(folder).resolve()
    extra = {Path(a).resolve().relative_to(folder).as_posix() if Path(a).is_absolute() else Path(a).as_posix()
             for a in also}
    files = sorted(set(project_files(folder, untracked=False)) | extra)
    node = {}
    for f in files:
        cur = node
        parts = f.split("/")
        for d in parts[:-1]:
            cur = cur.setdefault(d + "/", {})
        cur[parts[-1]] = None

    def count(n):
        return sum(1 if v is None else count(v) for v in n.values())

    def label(k):
        return f"{k} file" if k == 1 else f"{k} files"

    lines = [f"{folder.name}/  ({label(len(files))})"]

    def walk(n, prefix, level):
        keys = sorted(n, key=lambda k: (n[k] is None, k.lower()))
        for i, k in enumerate(keys):
            last = i == len(keys) - 1
            branch = "└── " if last else "├── "
            if n[k] is None:
                lines.append(prefix + branch + k)
            elif level >= depth:
                lines.append(f"{prefix}{branch}{k}  ({label(count(n[k]))})")
            else:
                lines.append(prefix + branch + k)
                walk(n[k], prefix + ("    " if last else "│   "), level + 1)

    walk(node, "", 1)
    return "\n".join(lines)


# ---------------------------------------------------------------------------------------------------- stamp

def read_keeping_newlines(path):
    with open(path, encoding="utf-8", newline="") as fh:
        return fh.read().replace("\r\n", "\n")


def write_keeping_newlines(path, text):
    """Write with the line ending the file already used: text mode on Windows would turn every LF into CRLF,
    and the pin's whole diff would be line endings."""
    with open(path, "rb") as fh:
        crlf = b"\r\n" in fh.read()
    with open(path, "w", encoding="utf-8", newline="\r\n" if crlf else "\n") as fh:
        fh.write(text)


def stamp(pin, marker, force=False, today=None, keep_marker=False):
    pin = Path(pin).resolve()
    if not pin.is_file():
        raise SystemExit(f"refused: no pin at {pin}")
    text = read_keeping_newlines(pin)
    today = today or datetime.date.today().isoformat()
    if keep_marker:
        return stamp_date_only(pin, text, today)
    if NO_ADVANCE_RE.search(text) and not force:
        raise SystemExit("refused: this pin says its last_marker must not be advanced. Use --keep-marker to move "
                         "only the date, or --force only if the owner said so.")
    root = repo_root(pin.parent)
    if root is None:
        raise SystemExit("refused: the pin is not inside a git repository, so there is no commit to mark")
    code, short = run_git(root, "rev-parse", "--short", (marker or "HEAD") + "^{commit}")
    if code != 0:
        raise SystemExit(f"refused: {marker or 'HEAD'} is not a commit in {root}")
    lines = text.split("\n")
    drop = [i for i, l in enumerate(lines) if re.match(r"\s*(last_marker|updated):", l)]
    at = drop[0] if drop else (1 if lines and re.match(r"\s*(<!--|#)", lines[0]) else 0)
    kept = [l for i, l in enumerate(lines) if i not in drop]
    at = min(at, len(kept))
    kept[at:at] = [f"last_marker: {short}", f"updated: {today}"]
    write_keeping_newlines(pin, "\n".join(kept))
    return short


def stamp_date_only(pin, text, today):
    """Move `updated:` and leave every last_marker line exactly as it is, for a pin whose marker is frozen."""
    lines = text.split("\n")
    for i, l in enumerate(lines):
        if re.match(r"\s*updated:", l):
            lines[i] = f"updated: {today}"
            break
        m = re.match(r"(\s*last_marker:\s*\S+)\s+updated:.*$", l)
        if m:  # the combined form: split it so the date is readable at the start of a line
            lines[i:i + 1] = [m.group(1), f"updated: {today}"]
            break
    else:
        at = 1 if lines and re.match(r"\s*(<!--|#)", lines[0]) else 0
        lines.insert(at, f"updated: {today}")
    write_keeping_newlines(pin, "\n".join(lines))
    m = MARKER_RE.search("\n".join(lines))
    return m.group(1) if m else "(none)"


# ---------------------------------------------------------------------------------------------------- hunks

def split_hunks(diff):
    header, hunks = [], []
    for line in diff.split("\n"):
        if line.startswith("@@"):
            hunks.append([line])
        elif hunks:
            hunks[-1].append(line)
        else:
            header.append(line)
    return header, hunks


def file_hunks(path):
    """The unstaged hunks of one file (working tree vs index, no context), numbered from 1."""
    path = Path(path).resolve()
    root = repo_root(path.parent)
    if root is None:
        raise SystemExit(f"refused: {path} is not inside a git repository")
    rel = path.relative_to(root.resolve()).as_posix()
    return root, rel, split_hunks(git(root, "diff", "-U0", "--no-color", "--", rel))


def stage_hunks(path, keep):
    """Stage only the named hunks of a file, so another session's uncommitted edit in the same file stays out of
    the commit and stays in the working tree."""
    root, rel, (header, hunks) = file_hunks(path)
    bad = [k for k in keep if not 1 <= k <= len(hunks)]
    if not hunks or bad:
        raise SystemExit(f"refused: {rel} has {len(hunks)} unstaged hunk(s); asked for {sorted(keep)}")
    patch = "\n".join(header + [l for k in sorted(set(keep)) for l in hunks[k - 1]]) + "\n"
    r = subprocess.run(["git", "-C", str(root), "apply", "--cached", "--unidiff-zero", "-"],
                       input=patch, capture_output=True, encoding="utf-8", errors="replace")
    if r.returncode:
        raise SystemExit(f"refused: git apply --cached failed: {r.stderr.strip()}")
    return rel


# ---------------------------------------------------------------------------------------------------- worklist

FIGURE_RE = re.compile(
    r"\b\d{4}-\d{2}-\d{2}\b"                                        # a date
    r"|\b(?=[0-9a-f]*\d)(?=[0-9a-f]*[a-f])[0-9a-f]{7,40}\b"         # a commit sha
    r"|\d+(?:\.\d+)?\s*%"                                           # a percentage
    r"|\b\d+\.\d+\b"                                                # a version or a decimal
    r"|(?<![\w.])\d{2,}(?:,\d{3})*\b"                               # a count or size of two digits or more
    r"|\b\d\s+(?!(?:is|was|has|this|its|does)\b)[A-Za-z]{2,}s\b")   # a small count: 3 commits, 4 files
BLOCK_RE = re.compile(r"<(style|script)\b.*?</\1\s*>", re.S | re.I)


def doc_lines(path):
    """A doc's lines as a reader sees them: an HTML file without its styles, scripts and tags."""
    text = path.read_text(encoding="utf-8", errors="replace")
    if path.suffix.lower() in (".html", ".htm"):
        text = BLOCK_RE.sub(lambda m: "\n" * m.group(0).count("\n"), text)
        return [html.unescape(re.sub(r"<[^>]+>", " ", l)) for l in text.split("\n")]
    return text.split("\n")


def code_lines(path, lines):
    """Which line numbers sit inside a fenced code block (markdown) or a <pre> (html) — usually examples."""
    inside, fenced, opened = set(), False, 0
    for i, line in enumerate(lines, 1):
        if path.suffix.lower() in (".html", ".htm"):
            opened += len(re.findall(r"<pre\b", line, re.I))
            if opened:
                inside.add(i)
            opened -= len(re.findall(r"</pre\s*>", line, re.I))
            opened = max(opened, 0)
        else:
            if re.match(r"\s*(```|~~~)", line):
                fenced = not fenced
                inside.add(i)
            elif fenced:
                inside.add(i)
    return inside


def worklist(folder, docs):
    """One open item per doc (read it whole against the code) and per line that carries a figure: the list a refresh
    closes item by item, so "every figure re-derived" is checked by `audit --worklist`, not remembered."""
    folder = Path(folder).resolve()
    out = [f"# Worklist — {folder.name} — {datetime.date.today().isoformat()}",
           "Tick each item `- [x]` as you check it, with an arrow saying what you found: `→ unchanged (<command>)`, "
           "`→ now <value> (<command>)`, `→ not a figure`, `→ history, left as written`, "
           "`→ not re-checked — last seen <date>`. Ticking items you did not read is the one way this pass can lie.",
           "Line numbers are from before your edits: they locate an item, not the line it sits on now. `[code]` marks "
           "a line inside a code block or a `<pre>` — often an example rather than a figure, but read it.",
           "`closeout.py audit … --worklist <this file>` fails while any item is still open.", ""]
    figures = 0
    for d in docs:
        p = Path(d) if Path(d).is_absolute() else folder / d
        out.append(f"## {d}")
        if not p.is_file():
            out += [f"- [ ] {d} — not on disk: say why in the report", ""]
            continue
        out.append(f"- [ ] {d} — whole doc read against the code: every path, name, feature and claim")
        lines = doc_lines(p)
        in_code = code_lines(p, p.read_text(encoding="utf-8", errors="replace").split("\n"))
        for i, line in enumerate(lines, 1):
            text = " ".join(line.split())
            if text and FIGURE_RE.search(text):
                out.append(f"- [ ] {d}:{i}{' [code]' if i in in_code else ''} — {text[:160]}")
                figures += 1
        out.append("")
    out.append(f"{figures} figure line(s) in {len(docs)} doc(s)")
    return "\n".join(out)


# ---------------------------------------------------------------------------------------------------- audit

def path_words(span):
    """Words inside a code span that look like a path, stripped of line numbers and punctuation.

    Bare file names (`cc.sh`, `rec.json`) are skipped: they name a file somewhere, not a place to find it, and
    checking them only produced noise. So are placeholders, globs, URLs, flags, `../` prose and slash commands
    (`/closeout`). Quoted words keep their spaces: `rm -rf "/Applications/My Tool.app"`.
    """
    try:
        words = shlex.split(span)
    except ValueError:
        words = span.split()
    for w in words:
        w = w.strip("()[],;'\"").rstrip(".:")
        w = re.sub(r"(:\d+(:\d+)?|::\S*)$", "", w)
        if "#" in w and "://" not in w:
            w = w.split("#", 1)[0]  # a citation, `path#anchor`: the path is checked here, the anchor by link-audit
        if (not w or w.startswith(("-", "../")) or "://" in w or "…" in w or re.search(r"[*?<>{}$|=@]", w)
                or re.search(r"YYYY|MM-DD", w) or re.fullmatch(r"/[^/]+", w)):
            continue
        if w.startswith(("~/", "/")) or "/" in w.strip("/"):
            yield w


def spaced_path_exists(word, span):
    """A path with spaces in a longer span (`71259 /Applications/My Tool.app/Contents/MacOS/X`) splits
    at its spaces; rejoin the words that follow the one starting it and see whether a real path begins there."""
    if not word.startswith(("/", "~/")) or word not in span:
        return False
    rest = span[span.index(word):].split(" ")
    return any(Path(os.path.expanduser(" ".join(rest[:n]).rstrip(".,;:)"))).exists() for n in range(len(rest), 1, -1))


def resolve_exists(word, root, base, files):
    """True when the path exists, or when it is not rooted in this project (a branch name, another repo's
    folder), where there is nothing here to check it against."""
    if word.startswith("/"):
        return Path(word).exists()
    if word.startswith("~/"):
        top = Path.home() / word[2:].split("/", 1)[0]
        return not top.exists() or Path(os.path.expanduser(word)).exists()  # `~/a/b` in prose is an example
    w = word[2:] if word.startswith("./") else word
    first = w.split("/", 1)[0]
    if not ((root / first).is_dir() or (base / first).is_dir()):
        return True
    # A prefix of a real file counts: `apptests/FileWatcherTests` names apptests/FileWatcherTests.swift.
    return (root / w).exists() or (base / w).exists() or any(f.startswith(w) for f in files)


def missing_paths(span, root, base, files):
    """The path-like words of one code span that name nothing on disk."""
    span = span.replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">").strip()
    if span.startswith(("~/", "/")) and Path(os.path.expanduser(span)).exists():
        return []  # a whole-span path with a space in it: `/Applications/My Tool.app`
    return [w for w in path_words(span) if not resolve_exists(w, root, base, files) and not spaced_path_exists(w, span)]


def added_lines(root, rel):
    """Line numbers this close-out added to a tracked file (working tree vs HEAD). None for a file HEAD does not
    have: every line of it is new."""
    if run_git(root, "cat-file", "-e", f"HEAD:{rel}")[0] != 0:
        return None
    lines = set()
    for m in re.finditer(r"^@@ -\S+ \+(\d+)(?:,(\d+))? @@", git(root, "diff", "-U0", "HEAD", "--", rel), re.M):
        start, n = int(m.group(1)), int(m.group(2) or 1)
        lines.update(range(start, start + n))
    return lines


def audit(folder, changed, pin=None, worklist=None, check_baseline=False):
    """Checks the lines this close-out added — old text in a file it only marked superseded is history, not its
    work. Whole-file properties (charset, the pin's fields) are checked on the whole file."""
    folder = Path(folder).resolve()
    root = repo_root(folder) or folder
    findings = []

    def add(level, where, msg):
        findings.append((level, where, msg))

    changed = [Path(c).as_posix() for c in changed]  # git names staged files with /, on Windows too
    link_found, link_counts = link_audit(root, inventory=False)
    if link_counts.get("no_roadmap"):
        link_found = []
    if link_counts["MISSING"]:
        add("WARN", "linking", f"{link_counts['MISSING']} document(s) of a linked kind carry no `roadmap:` line — "
                               "`closeout.py link-audit` writes the inventory (LINKING.md)")
    for level, where, msg in link_found:
        if level in ("BROKEN", "NO FILE", "NO ANCHOR"):
            add("FAIL", where, f"linking {level}: {msg}")
    ledgers, records = skill_records(root)
    if repo_root(root):
        loose = untracked_records(repo_root(root), ledgers + records, root)
        if loose:
            add("WARN", "records", f"{len(loose)} ledger or decision record(s) not in git ({', '.join(loose[:4])}): "
                                   "commit them with the work that produced them, or other worktrees and a clean-up lose them")
    staged = git(root, "diff", "--cached", "--name-only").splitlines()
    for s in staged:
        if s not in changed:
            add("FAIL", s, "staged but not written by this close-out — someone else's work; "
                           f"unstage it (git restore --staged -- '{s}') and leave the file as it is")
    files = project_files(root)
    _, protected = doc_rules(root, files)
    for rel in changed:
        p = root / rel
        if not p.is_file():
            add("WARN", rel, "listed as changed but not on disk (deleted?) — say so in the report")
            continue
        if rel in protected:
            add("WARN", rel, "CLAUDE.md says never hand-edit this file — name the command that changed it")
        if p.name not in HAND_WRITTEN and is_generated(p):
            add("WARN", rel, "marked as generated — name the command that regenerated it; never edit it by hand")
        if p.suffix.lower() not in (".md", ".html", ".htm", ".txt"):
            continue
        text = p.read_text(encoding="utf-8", errors="replace")
        new = added_lines(root, rel)

        def line_of(pos):
            return text.count("\n", 0, pos) + 1

        def fresh(pos):
            return new is None or line_of(pos) in new

        for m in CONFLICT_RE.finditer(text):
            if fresh(m.start()):
                add("FAIL", f"{rel}:{line_of(m.start())}", "merge-conflict marker")
        for rx in SECRET_RES:
            for m in rx.finditer(text):
                if fresh(m.start()):
                    add("FAIL", f"{rel}:{line_of(m.start())}", "looks like a secret; remove it")
        for m in PLACEHOLDER_RE.finditer(text):
            if fresh(m.start()):
                add("WARN", f"{rel}:{line_of(m.start())}", f"placeholder {m.group(0)!r} left in")
        spans, links = [], []
        if p.suffix.lower() == ".md":
            spans = [(m.start(), m.group(1)) for m in re.finditer(r"`([^`\n]+)`", text)]
            links = [(m.start(), m.group(1)) for m in re.finditer(r"\]\(([^)\s]+)\)", text)]
        elif p.suffix.lower() in (".html", ".htm"):
            if not re.search(r"<meta[^>]+charset", text[:2048], re.I):
                add("FAIL", rel, "no <meta charset> in the first 2 KB — WebKit reads such a file as Latin-1")
            spans = [(m.start(), re.sub(r"<[^>]+>", "", m.group(1))) for m in
                     re.finditer(r"<code[^>]*>(.*?)</code>", text, re.S | re.I)]
            links = [(m.start(), m.group(1)) for m in re.finditer(r"""(?:href|src)\s*=\s*["']([^"']+)["']""", text, re.I)]
        spans = [(pos, s) for pos, s in spans if fresh(pos)]
        links = [(pos, s) for pos, s in links if fresh(pos)]
        for pos, span in spans:
            for w in missing_paths(span, root, p.parent, files):
                add("WARN", f"{rel}:{line_of(pos)}", f"path `{w}` not found")
        for pos, link in links:
            u = urlsplit(link)
            if u.scheme in ("http", "https", "mailto", "data", "javascript") or link.startswith("#"):
                continue
            target = Path(unquote(u.path)) if u.scheme == "file" else (p.parent / unquote(u.path))
            if u.path and not target.exists():
                add("WARN", f"{rel}:{line_of(pos)}", f"link target {link!r} not found")
    if pin:
        pp = root / pin
        text = pp.read_text(encoding="utf-8", errors="replace") if pp.is_file() else ""
        if not text:
            add("FAIL", pin, "the pin is missing or empty")
        else:
            m = UPDATED_RE.search(text)
            if not m:
                add("FAIL", pin, "no `updated: YYYY-MM-DD` line at the start of a line")
            elif m.group(1) != datetime.date.today().isoformat():
                add("WARN", pin, f"updated: {m.group(1)} is not today")
            if not NEXT_RE.search(text):
                add("FAIL", pin, "no heading starting with 'Next' followed by the next action")
            m = MARKER_RE.search(text)
            if m and run_git(root, "cat-file", "-e", m.group(1) + "^{commit}")[0] != 0:
                add("FAIL", pin, f"last_marker {m.group(1)} is not a commit in this repository")
    if check_baseline:
        for kind in facts(root)["baseline"]["missing"]:
            add("WARN", f"baseline:{kind}", "the project has no " + kind.replace("_", ".").replace("claude.md", "CLAUDE.md")
                + " — create it (UPDATING.md § Baseline), or say in the report why not")
    if worklist is not None:
        wl = Path(worklist)
        if not wl.is_file():
            add("FAIL", str(worklist), "no worklist at this path — build it with `worklist`, then close every item")
        else:
            for i, line in enumerate(wl.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
                if re.match(r"\s*[-*] \[ \]", line):
                    add("FAIL", f"{wl.name}:{i}", "open worklist item: " + line.split("]", 1)[1].strip()[:160])
                elif re.match(r"\s*[-*] \[[xX]\]", line) and not re.search(r"→\s*\S", line):
                    add("FAIL", f"{wl.name}:{i}", "ticked with no finding — add `→ <what you found> (<command>)`")
    return findings


# ---------------------------------------------------------------------------------------------------- link-audit

LINK_DEFAULT_ROADMAPS = ("knowledge/00-PROJECT-ROADMAP.md", "knowledge/00-ROADMAP.md", "ROADMAP.md")
# Each project keeps its documents in its own structure: redman's ledgers and greenman's decision
# records live in the project's tree, declared in its doc map (`ledgers:`, `decisions:`) or at these default places.
# `<project>/xbt-skills-pack/` holds only the pack's own side ledger; the subfolders an earlier layout used there are
# still read so a project that has not moved them keeps working.
# A record's status line as people write it: `status: …`, `- Status: …`, `**Status:** …`.
STATUS_LINE = r"^[ \t]*(?:[-*][ \t]+)?\**status\**[ \t]*:\**"
PACK_DIR = "xbt-skills-pack"
PACK_SIDE_LEDGER = PACK_DIR + "/LEDGER.md"
DEFAULT_REDMAN_DIRS = ("knowledge/redman", "docs/redman", PACK_DIR + "/redman")
DEFAULT_DECISION_DIRS = ("knowledge/decisions", "docs/decisions", PACK_DIR + "/greenman")
INVENTORY_DIRS = ("knowledge", "docs")
LINK_DEFAULT_KINDS = ("knowledge/*spec*.md", "knowledge/*SPEC*.md", "knowledge/*audit*.md", "knowledge/*AUDIT*.md",
                      "knowledge/recon*/**/*.md", "knowledge/*design*.md", "docs/design/*.md",
                      "docs/superpowers/specs/*.md", "knowledge/redman/*.md", "knowledge/00-HANDOFF-*.md",
                      "docs/handoff/*HANDOFF*.md", "docs/redman/*.md", "knowledge/decisions/*.md",
                      "docs/decisions/*.md", "xbt-skills-pack/**/*.md")
LINK_DEFAULT_EXEMPT = ("README.md", "CLAUDE.md", "HANDOFF.md", "knowledge/00-CURRENT.md",
                       "docs/project-overview.html", "knowledge/00-ROADMAP.md", "knowledge/00-PROJECT-ROADMAP.md")
ROADMAP_LINE_RE = re.compile(r"(?:^|\s)\**roadmap:\**\s*(.+?)\s*$", re.I | re.M)
ROW_ID_RE = re.compile(r"^\|\s*(\d+(?:\.\d+)?)\s*\|", re.M)
TAG_ID_RE = re.compile(r"\bR-(\d+)\b")
PROSE_ITEM_RE = re.compile(r"^\s*(?:[-*]\s+|\d+\.\s+)\S", re.M)
CITATION_RE = re.compile(r"`([A-Za-z0-9_./-]+\.(?:md|html?|py|sh|swift|sql|js|txt))(?:#([^`]+))?`|\]\(([^)\s#]+)(?:#([^)]+))?\)")


def doc_map(root):
    """The `## Doc map` block of CLAUDE.md (or the pin, when CLAUDE.md says so) as a dict; {} when absent."""
    found = {}
    for rel in ("CLAUDE.md", "knowledge/00-CURRENT.md"):
        p = Path(root) / rel
        if not p.is_file():
            continue
        text = p.read_text(encoding="utf-8", errors="replace")
        m = re.search(r"^##+\s*Doc map\s*$(.*?)(?=^##\s|\Z)", text, re.M | re.S | re.I)
        if not m:
            continue
        for line in m.group(1).splitlines():
            km = re.match(r"^\s*(roadmap|id form|linked kinds|exempt|guardrails|ledgers|decisions)\s*:\s*(.+?)\s*$", line, re.I)
            if km:
                found[km.group(1).lower()] = km.group(2)
        if found:
            found["_source"] = rel
            break
    return found


def _globs(root, patterns):
    out = set()
    for pat in patterns:
        pat = pat.strip().strip("`")
        if pat:
            out.update(p.relative_to(root).as_posix() for p in Path(root).glob(pat) if p.is_file())  # globs use /
    return sorted(out)


def write_unlinked_inventory(root, findings, today=None):
    """One worklist file for a thread to link from: every MISSING document with its first heading, its date and its
    folder. Rewritten in place each run; never edits the documents themselves (the guardrail is
    live and constant, not "go fix stuff")."""
    today = today or datetime.date.today().isoformat()
    missing = [w for l, w, _ in findings if l == "MISSING"]
    out_dir = root / ("knowledge" if (root / "knowledge").is_dir() else "docs")  # the backlog is the project's own
    path = out_dir / f"UNLINKED-{today}.md"
    for old in existing_inventories(root):
        if old != path:
            old.unlink()
    lines = [f"# UNLINKED — {len(missing)} document(s) with no `roadmap:` line — {today}",
             "roadmap: none — this is the linking backlog itself, written by `closeout.py link-audit`; delete it when empty",
             "", "Each row is a document a thread should link: add `roadmap: <ids>` or `roadmap: none — <why>` in its",
             "first 40 lines (LINKING.md § 2). Rewritten on every run; do not edit by hand.", "",
             "| document | first heading | last changed | folder |", "|---|---|---|---|"]
    for rel in missing:
        p = root / rel
        head = next((ln.lstrip("# ").strip() for ln in p.read_text(encoding="utf-8", errors="replace").splitlines()
                     if ln.startswith("#")), "")[:80]
        code, when = run_git(root, "log", "-1", "--format=%ad", "--date=short", "--", rel)
        lines.append(f"| `{rel}` | {head} | {when if code == 0 and when else 'untracked'} | `{rel.rsplit('/', 1)[0] if '/' in rel else '.'}` |")
    if not missing:
        lines.append("| — | nothing unlinked | | |")
    out_dir.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path.relative_to(root).as_posix()


def existing_inventories(root):
    """Every UNLINKED inventory in the project, including one an earlier layout left in the pack folder."""
    dirs = INVENTORY_DIRS + (PACK_DIR + "/doc-guardrails",)
    return [p for d in dirs if (root / d).is_dir() for p in (root / d).glob("UNLINKED-*.md")]


def _declared(dm, key):
    return [v.strip().strip("`") for v in (dm.get(key) or "").split(",") if v.strip()]


def link_kinds(dm):
    """The linked kinds: the doc map's own list (else the defaults), plus every place its `ledgers:` and `decisions:`
    declare, so a lane's ledger, the deep runs beside it and the decision records are checked even when the map lists
    kinds without them."""
    kinds = [k.strip().strip("`") for k in (dm.get("linked kinds") or ", ".join(LINK_DEFAULT_KINDS)).split(",")]
    for led in _declared(dm, "ledgers"):
        if not led.endswith(".md"):
            kinds.append(led.rstrip("/") + "/*.md")  # a folder of ledgers
        else:
            kinds.append(led.rsplit("/", 1)[0] + "/*.md" if "/" in led else led)  # the ledger and the deep runs beside it
    kinds += [d.rstrip("/") + "/*.md" for d in _declared(dm, "decisions")]
    return [k for k in dict.fromkeys(kinds) if k]


def decision_ledgers(folder):
    """Greenman's ledgers: `LEDGER.md` in every decisions folder the doc map declares, and in the default places."""
    folder = Path(folder)
    dm = doc_map(repo_root(folder) or folder)
    return sorted({folder / d / "LEDGER.md" for d in _declared(dm, "decisions") + list(DEFAULT_DECISION_DIRS)
                   if (folder / d / "LEDGER.md").is_file()})


def ledger_rows_with_status(folder, ledgers, records, pattern):
    """Decision-ledger rows whose status matches but that have no `G-` file of their own (an S call leaves only its
    row): `<ledger>: <the choice>`. A row whose id has a record is already counted from the record."""
    have = {re.match(r"(G-\d+)", r.name).group(1) for r in records if re.match(r"G-\d+", r.name)}
    out = []
    for led in ledgers:
        for line in led.read_text(encoding="utf-8", errors="replace").splitlines():
            cells = [c.strip() for c in line.strip().strip("|").split("|")]
            if not line.startswith("|") or len(cells) < 5 or not re.search(pattern, cells[4], re.I):
                continue
            if cells[1] in have:
                continue
            out.append(f"{led.relative_to(folder).as_posix()}: {cells[2]}")
    return out


def skill_records(folder):
    """(redman ledgers, greenman decision records) for the project: every ledger and decision folder its doc map
    declares, plus the default places. The pack's side ledger is not the project's and is not in either list."""
    folder = Path(folder)
    dm = doc_map(repo_root(folder) or folder)
    ledgers = set()
    for decl in _declared(dm, "ledgers"):
        target = folder / decl
        ledgers.update(target.glob("*.md") if target.is_dir() else [target] if target.is_file() else [])
    for d in DEFAULT_REDMAN_DIRS:
        ledgers.update(p for p in (folder / d).glob("*.md") if p.is_file())
    records = set()
    for d in _declared(dm, "decisions") + list(DEFAULT_DECISION_DIRS):
        records.update(p for p in (folder / d).glob("G-*.md") if p.is_file())
    return sorted(ledgers), sorted(records)


def open_ledger_rows(path):
    if not Path(path).is_file():
        return 0
    n = 0
    for line in Path(path).read_text(encoding="utf-8", errors="replace").splitlines():
        if line.startswith("|") and re.search(r"\|\s*(filed|asked · later|asked · now|open-for-user)\b", line):
            n += 1
    return n


def untracked_records(root, paths, base=None):
    """The ledgers and decision records git does not track: nothing else keeps them (other worktrees cannot see them,
    a clean-up deletes them). [] outside a git repository."""
    if not root:
        return []
    root = Path(root).resolve()
    tracked = set(git(root, "ls-files").splitlines())
    base = Path(base or root).resolve()
    return [p.resolve().relative_to(base).as_posix() for p in paths
            if p.resolve().relative_to(root).as_posix() not in tracked]


def guardrails_mode(dm):
    """`guardrails:` in the doc map: off, report (the default) or block (the commit gate refuses)."""
    v = (dm.get("guardrails") or "report").lower()
    return "off" if v.startswith("off") else "block" if v.startswith("block") else "report"


def link_check_staged(folder):
    """The commit gate's question: which staged markdown files of a linked kind are unlinked or broken? Returns
    (findings, mode). Only what is being committed is judged, so the gate stays going-forward only."""
    folder = Path(folder).resolve()
    root = repo_root(folder) or folder
    mode = guardrails_mode(doc_map(root))
    if mode == "off":
        return [], mode
    staged = git(root, "diff", "--cached", "--name-only", "--diff-filter=ACMR").splitlines()
    found = []
    for rel in staged:
        if rel.endswith(".md"):
            code, staged_text = run_git(root, "show", f":{rel}")  # what is being committed, not the working tree
            hit = link_check_file(root, root / rel, text=staged_text if code == 0 else None)
            if hit:
                found.append((hit[0], rel, hit[1]))
    return found, mode


GATE_BEGIN = "# >>> doc-guardrails commit gate >>>"
GATE_END = "# <<< doc-guardrails commit gate <<<"


def install_gate(folder):
    """Add the doc-guardrails block to the repository's pre-commit hook, once; any hook already there is kept. The
    block runs `link-audit --staged`: it reports, and refuses the commit only where the doc map says
    `guardrails: block`. Returns the hook's path."""
    folder = Path(folder).resolve()
    root = repo_root(folder)
    if not root:
        raise ValueError(f"{folder} is not in a git repository: no commit gate to install")
    root = Path(root)
    hooks = Path(git(root, "rev-parse", "--git-path", "hooks"))
    if not hooks.is_absolute():
        hooks = root / hooks
    hooks.mkdir(parents=True, exist_ok=True)
    hook = hooks / "pre-commit"
    helper = Path(__file__).absolute().as_posix()      # the copy that installed the gate, wherever the skills live
    python = Path(sys.executable).as_posix()            # the interpreter that ran it: no python3 on PATH needed
    block = (f"{GATE_BEGIN}\n"
             "# Added by closeout.py install-gate: every staged document of a linked kind names its roadmap item.\n"
             "# It runs the helper with the interpreter recorded here (CLOSEOUT_PY / CLOSEOUT_PYTHON override them). If it\n"
             "# cannot run, a report-only project commits with a note and a `guardrails: block` project is refused.\n"
             f'CLOSEOUT_PY="${{CLOSEOUT_PY:-{helper}}}"\n'
             f'CLOSEOUT_PYTHON="${{CLOSEOUT_PYTHON:-{python}}}"\n'
             '[ -x "$CLOSEOUT_PYTHON" ] || CLOSEOUT_PYTHON="$(command -v python3 || command -v python || true)"\n'
             'if [ -f "$CLOSEOUT_PY" ] && [ -n "$CLOSEOUT_PYTHON" ]; then\n'
             '  "$CLOSEOUT_PYTHON" "$CLOSEOUT_PY" link-audit . --staged || exit 1\n'
             "elif grep -qiE '^guardrails:[[:space:]]*block' CLAUDE.md knowledge/00-CURRENT.md 2>/dev/null; then\n"
             '  echo "doc-guardrails: commit refused: the gate cannot run (no python or closeout.py found) and this project says guardrails: block" >&2\n'
             "  exit 1\n"
             "else\n"
             '  echo "doc-guardrails: gate skipped: no python or closeout.py found (report-only project, commit continues)" >&2\n'
             "fi\n"
             f"{GATE_END}\n")
    text = hook.read_text(encoding="utf-8") if hook.is_file() else "#!/bin/sh\n"
    if GATE_BEGIN in text and GATE_END in text:
        start = text.index(GATE_BEGIN)
        end = text.index(GATE_END) + len(GATE_END) + 1
        text = text[:start] + block + text[end:]
    else:
        text = text.rstrip("\n") + "\n" + block
    hook.write_text(text, encoding="utf-8")
    hook.chmod(hook.stat().st_mode | 0o111)
    return hook


def hook_in_tree(folder, hook):
    """True when the hook file is part of the project's tree (core.hooksPath inside it, e.g. `.husky/`), not git's own
    folder: then installing the gate changed a project file, which the report must name."""
    root = Path(repo_root(Path(folder).resolve()) or folder)
    try:
        rel = Path(hook).resolve().relative_to(root.resolve())
    except ValueError:
        return False
    return rel.parts[:1] != (".git",)


def cited_ids(val, form):
    """The ids a `roadmap:` value names. `R-<n>` tags count wherever they appear (the prefix makes them ids). Row numbers
    count only as a list at the start of the value or right after the word row or rows ("954, 968", "refines row 129",
    "rows 361–366"), so a date, a commit, a section number or a figure in the explanation is never taken for a row id."""
    if "row" not in form:
        return re.findall(r"R-(\d+)", val)
    one = r"\d+(?:\.\d+)?"
    sep = r"\s*(?:,|;|/|&|\band\b|\bto\b|–|—(?=\s*\d)|-)\s*#?"
    ids = []
    for chain in re.finditer(r"(?:^\s*#?|\brows?\s+#?)(" + one + r"(?:" + sep + one + r")*)", val, re.I):
        ids += re.findall(one, chain.group(1))
    return ids


def as_written(cid, form):
    """An id echoed back in the project's own form, so it is the string the author can grep for."""
    return cid if "row" in form else f"R-{cid}"


def link_check_file(folder, rel, text=None):
    """The hook's question: is this one markdown file, just written, of a linked kind and unlinked or broken?
    Returns (level, message) or None when there is nothing to say — no roadmap, guardrails off, not a linked kind.
    Fast path: reads the doc map, the roadmap's ids and this one file; never walks the project (a hook runs on
    every write, and the full walk costs seconds on a large project)."""
    folder = Path(folder).resolve()
    root = repo_root(folder) or folder
    dm = doc_map(root)
    if dm.get("guardrails", "").lower().startswith("off"):
        return None
    try:
        rel = Path(rel).resolve().relative_to(root).as_posix()  # the kinds and exempt globs use /
    except ValueError:
        return None
    rm_rel = dm.get("roadmap") or next((r for r in LINK_DEFAULT_ROADMAPS if (root / r).is_file()), None)
    if not rm_rel or not (root / rm_rel).is_file() or rel == rm_rel:
        return None
    kinds = link_kinds(dm)
    exempt = [e.strip().strip("`") for e in (dm.get("exempt") or ", ".join(LINK_DEFAULT_EXEMPT)).split(",")]
    from fnmatch import fnmatch
    def matches(pats):
        return any(fnmatch(rel, pat) or fnmatch(rel, pat.replace("**/", "")) for pat in pats if pat)
    if not matches(kinds) or matches(exempt) or re.search(r"(^|/)UNLINKED-\d{4}-\d\d-\d\d\.md$", rel):
        return None
    rm_text = (root / rm_rel).read_text(encoding="utf-8", errors="replace")
    row_ids, tag_ids = set(ROW_ID_RE.findall(rm_text)), set(TAG_ID_RE.findall(rm_text))
    form = dm.get("id form") or ("table rows" if row_ids else "bullet tags")
    ids = row_ids if "row" in form else tag_ids
    if text is None:
        text = (root / rel).read_text(encoding="utf-8", errors="replace")
    head = "\n".join(text.splitlines()[:40])
    m = ROADMAP_LINE_RE.search(head)
    if not m:
        return "MISSING", "no `roadmap:` line in the first 40 lines"
    val = m.group(1).strip()
    if val.lower().startswith("none"):
        return None if re.match(r"none\s*[—–-]\s*\S", val, re.I) else ("BROKEN", "`roadmap: none` without a reason")
    cited = cited_ids(val, form)
    bad = [c for c in cited if c not in ids]
    if not cited:
        return "BROKEN", f"`roadmap:` names no id: {val[:80]}"
    if bad:
        return "BROKEN", f"roadmap id(s) not in {rm_rel}: {', '.join(as_written(b, form) for b in bad)}"
    return None


def link_audit(folder, strict=False, inventory=True):
    """Report-only (exit 0) unless --strict: which linked documents name a roadmap item, which roadmap citations
    resolve. Absence is REPORTED; a broken link is BROKEN (fails only under --strict). Never edits."""
    folder = Path(folder).resolve()
    root = repo_root(folder) or folder
    dm = doc_map(root)
    out, counts = [], {"LINKED": 0, "NONE": 0, "MISSING": 0, "BROKEN": 0, "OK": 0, "NO FILE": 0, "NO ANCHOR": 0}

    def say(level, where, msg):
        out.append((level, where, msg))
        counts[level] = counts.get(level, 0) + 1

    if not dm:
        # "no Doc map" reads as "your CLAUDE.md is missing a section"; when there is no CLAUDE.md at all, say that.
        have = [f for f in ("CLAUDE.md", "knowledge/00-CURRENT.md") if (root / f).is_file()]
        say("NOTE", have[0] if have else "CLAUDE.md",
            "no `## Doc map` — using defaults (LINKING.md § 5)" if have else
            "no CLAUDE.md with a `## Doc map` — using defaults (LINKING.md § 5)")
    rm_rel = dm.get("roadmap") or next((r for r in LINK_DEFAULT_ROADMAPS if (root / r).is_file()), None)
    if not rm_rel or not (root / rm_rel).is_file():
        say("NOTE", rm_rel or "roadmap", "no roadmap found — nothing to link to (a baseline gap; `audit --baseline` names it)")
        counts["no_roadmap"] = 1
        return out, counts
    rm_text = (root / rm_rel).read_text(encoding="utf-8", errors="replace")
    row_ids = set(ROW_ID_RE.findall(rm_text))
    tag_ids = set(TAG_ID_RE.findall(rm_text))
    form = dm.get("id form") or ("table rows" if row_ids else "bullet tags")
    ids = row_ids if "row" in form else tag_ids
    say("NOTE", rm_rel, f"roadmap; id form: {form}; {len(ids)} ids")
    counts["ids"] = len(ids)
    if "row" not in form:
        items = PROSE_ITEM_RE.findall(rm_text)
        untagged = sum(1 for line in rm_text.splitlines() if PROSE_ITEM_RE.match(line) and not TAG_ID_RE.search(line))
        say("NOTE", rm_rel, f"prose roadmap: {len(items)} items, {untagged} without an `R-<n>` id")
    kinds = link_kinds(dm)
    exempt = set(_globs(root, [e.strip() for e in (dm.get("exempt") or ", ".join(LINK_DEFAULT_EXEMPT)).split(",")]))
    for rel in _globs(root, kinds):
        if rel in exempt or rel == rm_rel or re.search(r"(^|/)UNLINKED-\d{4}-\d\d-\d\d\.md$", rel):
            continue
        head = "\n".join((root / rel).read_text(encoding="utf-8", errors="replace").splitlines()[:40])
        m = ROADMAP_LINE_RE.search(head)
        if not m:
            say("MISSING", rel, "no `roadmap:` line in the first 40 lines")
            continue
        val = m.group(1).strip()
        if val.lower().startswith("none"):
            if re.match(r"none\s*[—–-]\s*\S", val, re.I):
                say("NONE", rel, val[:120])
            else:
                say("BROKEN", rel, "`roadmap: none` without a reason")
            continue
        cited = cited_ids(val, form)
        bad = [c for c in cited if c not in ids]
        if not cited:
            say("BROKEN", rel, f"`roadmap:` names no id: {val[:80]}")
        elif bad:
            say("BROKEN", rel, f"roadmap id(s) not in {rm_rel}: {', '.join(as_written(b, form) for b in bad)}")
        else:
            say("LINKED", rel, ", ".join(as_written(c, form) for c in cited))
    for m in CITATION_RE.finditer(rm_text):
        path, anchor = (m.group(1), m.group(2)) if m.group(1) else (m.group(3), m.group(4))
        if not path or path.startswith(("http", "~", "/", "_live/", ".")):
            continue
        target = root / path
        line = rm_text.count("\n", 0, m.start()) + 1
        if not target.is_file():  # a path cited relative to a source root (`Models/X.swift` under app/) counts
            hits = [d for depth in ("*", "*/*") for d in root.glob(depth)
                    if d.is_dir() and not any(x.startswith(".") for x in d.relative_to(root).parts) and (d / path).is_file()]
            if len(hits) == 1:
                target = hits[0] / path
        if not target.is_file():
            if "/" in path:  # a bare name is a mention, not a citation
                say("NO FILE", f"{rm_rel}:{line}", path)
            continue
        if anchor:
            needle = anchor.replace("§", "").strip()
            body = target.read_text(encoding="utf-8", errors="replace")
            if needle and needle not in body:
                say("NO ANCHOR", f"{rm_rel}:{line}", f"{path}#{anchor}")
                continue
        counts["OK"] += 1
    existing = existing_inventories(root)
    if inventory and counts["MISSING"]:
        say("NOTE", write_unlinked_inventory(root, out), f"inventory of the {counts['MISSING']} unlinked document(s) — the worklist for a thread to link from")
    elif inventory and existing:  # nothing unlinked any more: the backlog file goes, it does not sit there empty
        for p in existing:
            p.unlink()
        say("NOTE", existing[0].relative_to(root).as_posix(), "removed — nothing is unlinked; the inventory exists only while there is a backlog")
    return out, counts


# ---------------------------------------------------------------------------------------------------- cli

def main(argv=None):
    # A Windows console defaults to a code page that cannot print the tree's box characters or non-ASCII names.
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser(prog="closeout.py", description=(__doc__ or "").split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("facts", help="what is true now, as JSON: git, pin, docs, baseline, linking counts, redman ledgers, the pack side ledger, greenman records, untracked records"); a.add_argument("folder", nargs="?", default=".")
    a = sub.add_parser("memory", help="every memory entry about this project, with index problems and gone paths"); a.add_argument("folder", nargs="?", default=".")
    a = sub.add_parser("worklist", help="one checklist item per doc and per figure-carrying line"); a.add_argument("folder", nargs="?", default=".")
    a.add_argument("--docs", nargs="+", required=True)
    a = sub.add_parser("tree", help="the git-tracked file tree for the overview"); a.add_argument("folder", nargs="?", default="."); a.add_argument("--depth", type=int, default=2)
    a.add_argument("--also", nargs="+", default=[])
    a = sub.add_parser("stamp", help="set the pin's last_marker: and updated: lines (writes the pin)"); a.add_argument("pin"); a.add_argument("--marker")
    a.add_argument("--force", action="store_true"); a.add_argument("--keep-marker", action="store_true")
    a = sub.add_parser("hunks", help="number a file's unstaged hunks"); a.add_argument("file")
    a = sub.add_parser("stage-hunks", help="stage only those hunks (writes the index)"); a.add_argument("file"); a.add_argument("n", type=int, nargs="+")
    a = sub.add_parser("audit", help="check what is about to be committed: paths, links, charset, pin fields, linking (warn unlinked, fail broken), untracked records, staged files not in --changed; exit 1 on a FAIL"); a.add_argument("folder", nargs="?", default=".")
    a.add_argument("--changed", nargs="+", required=True); a.add_argument("--pin")
    a.add_argument("--worklist"); a.add_argument("--baseline", action="store_true")
    a = sub.add_parser("link-audit", help="the LINKING.md contract; writes the UNLINKED inventory unless --no-inventory, --file or --staged"); a.add_argument("folder", nargs="?", default=".")
    a.add_argument("--strict", action="store_true", help="exit 1 on a broken link")
    a.add_argument("--no-inventory", action="store_true", help="report only; write no UNLINKED file")
    a.add_argument("--file", help="the hook's form: judge this one file, print nothing when there is nothing to say")
    a.add_argument("--staged", action="store_true", help="the commit gate's form: judge the staged files only")
    a = sub.add_parser("install-gate", help="add the commit gate to the repository's pre-commit hook (writes the hook; exit 2 outside git)"); a.add_argument("folder", nargs="?", default=".")
    args = ap.parse_args(argv)
    if args.cmd == "facts":
        print(json.dumps(facts(args.folder), indent=2))
    elif args.cmd == "memory":
        print(json.dumps(memory_report(args.folder), indent=2, ensure_ascii=False))
    elif args.cmd == "worklist":
        print(worklist(args.folder, args.docs))
    elif args.cmd == "tree":
        print(tree(args.folder, args.depth, args.also))
    elif args.cmd == "stamp":
        print(f"last_marker: {stamp(args.pin, args.marker, args.force, keep_marker=args.keep_marker)}")
    elif args.cmd == "hunks":
        _, rel, (_, hunks) = file_hunks(args.file)
        for i, h in enumerate(hunks, 1):
            print(f"--- hunk {i} of {rel}\n" + "\n".join(h))
        print(f"{len(hunks)} unstaged hunk(s) in {rel}")
    elif args.cmd == "stage-hunks":
        print(f"staged hunk(s) {sorted(set(args.n))} of {stage_hunks(args.file, args.n)}")
    elif args.cmd == "install-gate":
        try:
            hook = install_gate(args.folder)
        except ValueError as e:
            print(f"install-gate: {e}", file=sys.stderr)
            return 2
        print(f"commit gate installed in {hook} (report-only unless the doc map says `guardrails: block`)")
        if hook_in_tree(args.folder, hook):
            print("note: the hook file is inside the project's tree (core.hooksPath): name it in the report; do not commit it unasked")
    elif args.cmd == "link-audit" and getattr(args, "staged", False):
        found, mode = link_check_staged(args.folder)
        for level, where, msg in found:
            print(f"doc-guardrails: {level} — {where}: {msg}")
        if found:
            print(f"doc-guardrails: {len(found)} staged document(s) need a `roadmap:` line"
                  + (" — commit refused (`guardrails: block` in the doc map)" if mode == "block" else " — report only, commit continues"))
            if mode == "block":
                print("doc-guardrails: add the line to a document you wrote, then commit again. A document someone else wrote "
                      "(staged by the user, another session's) is not edited to get past this gate: say which line it needs "
                      "and ask. Never --no-verify, never edit the doc map or this hook.")
        return 1 if (found and mode == "block") else 0
    elif args.cmd == "link-audit" and args.file:
        hit = link_check_file(args.folder, args.file)
        if hit:
            print(f"doc-guardrails: {hit[0]} — {args.file}: {hit[1]}. Add `roadmap: <ids>` or `roadmap: none — <why>` in its first 40 lines (LINKING.md § 2).")
            return 2
        return 0
    elif args.cmd == "link-audit":
        found, counts = link_audit(args.folder, args.strict, not args.no_inventory)
        for level, where, msg in found:
            print(f"{level:9} {where}  {msg}")
        broken = counts["BROKEN"] + counts["NO FILE"] + counts["NO ANCHOR"]
        print("link-audit: " + " · ".join(f"{k} {v}" for k, v in counts.items() if v and k not in ("ids", "no_roadmap")) + (" · report only" if not args.strict else ""))
        return 1 if (args.strict and broken) else 0
    else:
        found = audit(args.folder, args.changed, args.pin, args.worklist, args.baseline)
        for level, where, msg in found:
            print(f"{level}  {where}  {msg}")
        fails = sum(1 for f in found if f[0] == "FAIL")
        print(f"audit: {fails} fail, {len(found) - fails} warn")
        return 1 if fails else 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
