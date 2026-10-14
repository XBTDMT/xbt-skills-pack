#!/usr/bin/env python3
"""closeout.py — the deterministic half of the /closeout and /project-update skills.

  closeout.py facts [FOLDER]                     what is true now: git state, the pin, handoff, overview and tracking
                                                 docs, generated and never-hand-edit files, Claude Code's memory for
                                                 this folder
  closeout.py memory [FOLDER]                    every memory entry about this project, in any memory folder, with
                                                 duplicate / unindexed / dangling index lines and paths that are gone
  closeout.py memory [FOLDER] --backup DEST      copy each of those folders into DEST/memory-backup-<own|home|other>
                                                 before changing it; never over an existing backup (exit 1)
  closeout.py worklist [FOLDER] --docs F [F ...] one checklist item per doc and per line that carries a figure
  closeout.py tree  [FOLDER] [--depth N]         the git-tracked file tree; folders deeper than N show a file count
                    [--also F ...]               ... plus the files this close-out is adding
  closeout.py stamp PIN [--marker SHA]           set the pin's `last_marker:` and `updated:` lines
                        [--keep-marker]          ... or only `updated:`, for a pin whose marker is frozen
  closeout.py hunks FILE                         number the file's unstaged hunks
  closeout.py stage-hunks FILE N [N ...]         stage only those hunks (another session's edit stays unstaged)
  closeout.py audit [FOLDER] --changed F [F ...] [--pin PIN] [--worklist WL]
                                                 check what is about to be committed; exit 1 on a failure
  closeout.py link-audit [FOLDER] [--strict] [--lane NAME]
                                                 the LINKING.md contract: report-only unless --strict
  closeout.py link-audit [FOLDER] --staged [--heartbeat]
                                                 the commit gate: staged linked docs only; blocks on `guardrails: block`
  closeout.py install-gate [FOLDER]              register the commit gate as a git config hook (git 2.54+), else place it
                                                 first in a hook file the pack owns; never where hook.doc-guardrails.enabled
                                                 is false, nor where core.hooksPath is a folder the project tracks until
                                                 its doc map says gate: config-hook; prints what runs
  closeout.py gate-registrations                 every repository where the config hook is still registered; exit 1
                                                 while any is
  closeout.py doc-map [FOLDER] [--lane NAME]     the doc map as the checker reads it: lanes, patterns, problems; exit 1 on
                                                 an error
  closeout.py figures [FOLDER] [--check | --write] [--public-tree DIR]
                                                 every `<!-- fig:NAME -->` span of the docs the doc map's `figures:`
                                                 declares, against its source; --write rewrites the spans and tables

Stdlib only, Python 3.9 or newer, on Windows, macOS and Linux. It writes only through `stamp` (the pin it is given),
`figures --write` (the span contents and marked tables of the declared docs),
`stage-hunks` (the index), `link-audit` without `--no-inventory`, `--file` or `--staged` (the UNLINKED inventory in
`knowledge/`, else `docs/`, replacing older ones) and `install-gate` (the repository's git config, or a hook file in git's
own hooks folder). CLOSEOUT_CONFIG_DIRS (os.pathsep-separated)
replaces the Claude Code config folders it reads memory from — a sandbox run points it at copies.
"""
from __future__ import annotations

import datetime
import functools
import hashlib
import html
import json
import ntpath
import os
import posixpath
import re
import shlex
import subprocess
import sys
import time
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


def native_path(p):
    """A path git printed (`worktree list` gives C:/Data/... on Windows) spelled with this platform's separator, as
    every other path the pack prints is; unchanged on macOS and Linux."""
    return os.path.normpath(p) if p else p


def git_bytes(folder, *args):
    """git's stdout exactly as git wrote it. A diff that is going back into `git apply` must never be decoded and
    re-encoded: text mode translates the line endings (on Windows every LF becomes CRLF), and the patch no longer
    matches the bytes in the file."""
    r = subprocess.run(["git", "-C", str(folder), *args], capture_output=True)
    return r.stdout if r.returncode == 0 else b""


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
    names = list(dict.fromkeys([escape(project_identity(folder)), escape(Path(folder).resolve())]))
    found, seen = [], set()
    for cfg, escaped in [(c, n) for c in config_dirs() for n in names]:
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
    identity = project_identity(folder)
    own_names, home_name = {escape(identity), escape(folder)}, escape(Path.home())
    name_re = project_name_re(identity)
    files = project_files(folder)
    dirs, dead, seen = [], [], set()
    for cfg in config_dirs():
        for m in sorted((cfg / "projects").glob("*/memory")) if (cfg / "projects").is_dir() else []:
            if not m.is_dir() or m.resolve() in seen:
                continue
            seen.add(m.resolve())
            kind = "own" if m.parent.name in own_names else "home" if m.parent.name == home_name else "other"
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
                             if kind == "own" or rooted(w)]
    dirs.sort(key=lambda d: KIND_ORDER[d["kind"]])
    dead.sort(key=lambda d: KIND_ORDER[d["dir"]])
    return {"project": folder.name, "dirs": dirs, "dead_paths": dead}


def memory_backup(folder, dest):
    """Copy every memory folder `memory` reports for this project into DEST before a refresh changes it (memory has no
    history): one `memory-backup-<own|home|other>` folder each (`-2`, `-3` for a second of a kind). Checks every
    target first and copies nothing if one exists, so a backup never writes over another. Python, so the step runs
    wherever the helper does, `cp` allowed or not and on Windows."""
    import shutil   # here, not at the top: the hooks import this module on every prompt and stop
    dest, plan, seen = Path(dest).expanduser(), [], {}
    for d in memory_report(folder)["dirs"]:
        seen[d["kind"]] = seen.get(d["kind"], 0) + 1
        n = seen[d["kind"]]
        plan.append((d, dest / (f"memory-backup-{d['kind']}" + (f"-{n}" if n > 1 else ""))))
    taken = [str(t) for _, t in plan if t.exists()]
    if taken:
        raise FileExistsError(f"{', '.join(taken)} exists: a backup never writes over another; name a new folder")
    made = []
    for d, target in plan:
        shutil.copytree(d["path"], str(target), copy_function=shutil.copy2)
        files = sum(len(fs) for _, _, fs in os.walk(target))
        made.append({"kind": d["kind"], "from": d["path"], "to": str(target), "files": files})
    return made


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
        g["worktrees"] = [native_path(l[9:]) for l in git(root, "worktree", "list", "--porcelain").splitlines()
                          if l.startswith("worktree ")]
        g["stashes"] = len(git(root, "stash", "list").splitlines())
        g["recent"] = git(root, "log", "-10", "--format=%h %cs %s").splitlines()
        out["git"] = g
    files = project_files(folder)
    live = [f for f in files if not ARCHIVE_RE.search(f)]
    ctx = link_context(project_root(folder), deep=True)
    out["doc_map"] = {"source": ctx["source"], "heading_line": ctx["heading_line"], "hash": ctx["hash"],
                      "problems": ctx["problems"], "errors": sum(1 for p in ctx["problems"] if p["level"] == "ERROR"),
                      "warnings": sum(1 for p in ctx["problems"] if p["level"] == "WARN")}
    lf, lc = link_audit(folder, inventory=False)
    judged = {w for l, w, _ in lf if l in ("LINKED", "NONE", "MISSING", "BROKEN")}
    out["guardrails"] = {"unlinked_docs": lc["MISSING"], "broken_links": lc["BROKEN"] + lc["NO FILE"] + lc["NO ANCHOR"],
                         "roadmap_ids": None if lc.get("no_roadmap") else lc.get("ids", 0),
                         "notes": [f"{w}: {m}" for l, w, m in lf if l == "NOTE" and w in judged]}
    ledgers, records = skill_records(folder)
    counted = redman_ledgers(folder)
    states = {led: ledger_state(led, folder) for led in counted}
    out["redman"] = {"open_rows": sum(s["open"] for s in states.values()),
                     "ledgers": [l.relative_to(folder).as_posix() for l in counted],
                     "deep_runs": deep_runs(counted, folder),
                     "unreadable_states": [u for s in states.values() for u in s["unknown"]],
                     "unreadable_ledgers": [f"{l.relative_to(folder).as_posix()}: {s['unreadable']}"
                                            for l, s in states.items() if s["unreadable"]],
                     "rows_after_a_break": [b for s in states.values() for b in s["breaks"]]}
    lanes_ctx = ctx["lanes"]
    by_lane = {}
    for lane in lanes_ctx:
        mine = _ledger_files(folder, lane["ledgers"]) if lane["ledgers"] else (
            {folder / d / "LEDGER.md" for d in DEFAULT_REDMAN_DIRS if (folder / d / "LEDGER.md").is_file()}
            if lane["name"] == "main" else set())
        by_lane[lane["name"]] = sum(ledger_state(l, folder)["open"] for l in mine)
    out["redman"]["by_lane"] = by_lane
    out["lanes"] = {}
    for lane in lanes_ctx:
        nxt, read = next_decision_id(folder, lane)
        out["lanes"][lane["name"]] = {
            "roadmap": lane["roadmap"], "id_form": lane["form"], "ids": len(lane["ids"]), "pin": lane["pin"],
            "branch": lane["branch"], "ledgers": lane["ledgers"], "decisions": lane["decisions"],
            "decision_ids": f"G-{lane['decids']}<n>", "outside_sequence": sorted(lane["outside"], key=_id_order),
            "next_decision_id": nxt, "next_decision_id_from": read}
    side = folder / PACK_SIDE_LEDGER
    out["pack_side_ledger"] = {"path": PACK_SIDE_LEDGER, "open_rows": open_ledger_rows(side)} if side.is_file() else None
    dledgers = decision_ledgers(folder)
    out["untracked_records"] = untracked_records(root, record_paths(folder), folder)
    def with_status(pattern):
        return [r.relative_to(folder).as_posix() for r in records
                if re.search(STATUS_LINE + r"[^\n]*?" + pattern, r.read_text(encoding="utf-8", errors="replace"), re.M | re.I)
                ] + ledger_rows_with_status(folder, dledgers, records, pattern)
    # a status can combine states ("ruled by the user …: … — shortcut taken — real fix owed"), so match anywhere on the line
    out["greenman"] = {"decisions": len(records), "awaiting_ruling": with_status(r"\bawaiting\b"),
                       "real_fix_owed": with_status(r"shortcut taken"),
                       "unreadable_ledgers": [f"{l.relative_to(folder).as_posix()}: {decision_table(l)[1]}"
                                              for l in dledgers if decision_table(l)[1]],
                       "undeclared_record_folders": undeclared_record_folders(
                           folder, _all_declared(folder, "decisions"))}
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
    main_pin = ctx["lanes"][0]["pin"] or PIN_DEFAULT
    if (folder / main_pin).is_file():
        pins.append(main_pin)
    out["gate"] = gate_state(root) if root else None
    out["pins"] = [dict(pin_facts(root, folder, p), lane="main") for p in pins]
    branch = git(root, "symbolic-ref", "--short", "-q", "HEAD") if root else ""
    for lane in ctx["lanes"][1:]:
        if not lane["pin"]:
            continue
        entry = dict(pin_facts(root, folder, lane["pin"]), lane=lane["name"])
        if lane["branch"] and root:
            entry["stamp_here"] = bool(branch) and kind_match(branch, [lane["branch"]])
            entry["lane_checkouts"] = lane_checkouts(root, lane["branch"])
        out["pins"].append(entry)
    out["baseline"] = baseline(out["docs"], out["pins"])
    return out


BASELINE = ("readme", "claude_md", "pin", "handoff", "overview", "roadmap", "brain")
# A plan, not a record of what happened: `00-PROJECT-ROADMAP.md`, `OPEN-ITEMS.md`, `SPRINT-BOARD.html`, `TODO.md`.
PLAN_RE = re.compile(r"roadmap|board|milestones?|backlog|open[-_]items|todo|sprints?", re.I)
# Whole words by letters (W-46(c)): `BACKLOG.md` is a plan, not a log; `_` is not a letter, so `decision_log` is a record.
RECORD_RE = re.compile(r"(?<![a-z])(?:ledger|log|decisions?|lessons?|brain|status)(?![a-z])", re.I)
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


def lane_pin_guard(pin, root):
    """A lane's pin is stamped only in a checkout on that lane's branch: stamped elsewhere it would be the stale copy
    another branch merged. `--force` does not bypass this."""
    try:
        rel = pin.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return
    for lane in link_context(root)["lanes"][1:]:
        if lane["pin"] != rel or not lane["branch"]:
            continue
        branch = git(root, "symbolic-ref", "--short", "-q", "HEAD")
        if branch and kind_match(branch, [lane["branch"]]):
            return
        where = lane_checkouts(root, lane["branch"])
        raise SystemExit(f"refused: {rel} is lane {lane['name']}'s pin and this checkout is on "
                         f"{branch or 'a detached HEAD'}, not {lane['branch']}: "
                         + (f"stamp it in the lane's checkout ({', '.join(where)})" if where else
                            f"check out a branch matching {lane['branch']} first"))


def stamp(pin, marker, force=False, today=None, keep_marker=False):
    pin = Path(pin).resolve()
    if not pin.is_file():
        raise SystemExit(f"refused: no pin at {pin}")
    if repo_root(pin.parent):
        lane_pin_guard(pin, Path(repo_root(pin.parent)))
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
    """`diff` is the bytes git printed, and the lines stay bytes all the way back to `git apply` (see git_bytes)."""
    header, hunks = [], []
    for line in diff.split(b"\n"):
        if line.startswith(b"@@"):
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
    return root, rel, split_hunks(git_bytes(root, "diff", "-U0", "--no-color", "--", rel))


def stage_hunks(path, keep):
    """Stage only the named hunks of a file, so another session's uncommitted edit in the same file stays out of
    the commit and stays in the working tree."""
    root, rel, (header, hunks) = file_hunks(path)
    bad = [k for k in keep if not 1 <= k <= len(hunks)]
    if not hunks or bad:
        raise SystemExit(f"refused: {rel} has {len(hunks)} unstaged hunk(s); asked for {sorted(keep)}")
    patch = b"\n".join(header + [l for k in sorted(set(keep)) for l in hunks[k - 1]]) + b"\n"
    r = subprocess.run(["git", "-C", str(root), "apply", "--cached", "--unidiff-zero", "-"],
                       input=patch, capture_output=True)
    if r.returncode:
        raise SystemExit(f"refused: git apply --cached failed: {r.stderr.decode('utf-8', 'replace').strip()}")
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


# ---------------------------------------------------------------------------------------------------- figures

# A figure a document states is a span its source fills (R-29 § 5.1): `<!-- fig:NAME -->VALUE<!-- /fig -->`, the same in
# markdown and HTML, never inside a heading; a generated table sits between `<!-- fig-table:NAME -->` and
# `<!-- /fig-table -->`. The doc map's `figures:` names the JSON file that declares which documents carry spans and where
# each figure comes from. `figures --check` holds every span to its source; `--write` rewrites span contents and the
# marked tables and no other byte. Nothing here runs without the `figures:` key.
FIG_SPAN_RE = re.compile(r"<!--[ \t]*fig:([A-Za-z0-9_.-]+)[ \t]*-->(.*?)<!--[ \t]*/fig[ \t]*-->", re.S)
FIG_TABLE_RE = re.compile(r"(<!--[ \t]*fig-table:([A-Za-z0-9_.-]+)[ \t]*-->)(.*?)(<!--[ \t]*/fig-table[ \t]*-->)", re.S)
FIG_TIMEOUT = 60
# The counts a declared document may still state by hand (the migration finder's patterns, R-29 § 4.2): a finder that
# `audit` WARNs with, never the check.
UNMARKED_PATTERNS = (
    re.compile(r"(?<![\w.$])\d+\s+(?:scenarios|graded\s+sessions|distinct\s+checks|checks|turns|check-runs|quick\s+sessions"
               r"|real\s+sessions|unit\s+tests|sessions)\b", re.I),
    re.compile(r"(?<![\w.$])\d+\s+of\s+the\s+\d+\b", re.I),
    re.compile(r"\babout\s+\$\d+(?:\.\d+)?", re.I),
)
SEAT_NAMES = {"fable": "Fable", "opus": "Opus", "opus55": "Opus 5.5"}
CASES_SUFFIX = {"quick": " (quick)"}


def figures_declaration(root):
    """(the declaration, its path, a problem or None): the JSON file the doc map's `figures:` names; (None, None, None)
    when the key is absent."""
    rel = doc_map(root).get("figures")
    if not rel:
        return None, None, None
    try:
        data = json.loads((Path(root) / rel).read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        return None, rel, f"{rel}: not a readable figures file ({type(e).__name__}: {e})"
    if not isinstance(data, dict) or not isinstance(data.get("figures", {}), dict):
        return None, rel, f"{rel}: not a figures file (an object with `docs` and `figures`)"
    return data, rel, None


def _line_at(text, pos):
    return text.count("\n", 0, pos) + 1


def _is_public_doc(rel):
    return rel.startswith("public/")


class FigureSources:
    """Each named figure's value, computed once per run: `json_command` runs its command once per distinct argv and
    folder, the others read their files. `tree` is the built public tree a `public/…` document's commands and
    mentions are computed in (R-29 § 5.1)."""

    def __init__(self, root, decl, tree=None):
        self.root, self.decl, self.tree = Path(root), decl, Path(tree) if tree else None
        self.figs = decl.get("figures") or {}
        self.commands, self.exports, self.cache = {}, {}, {}

    def folder_for(self, doc):
        return self.tree if self.tree is not None and _is_public_doc(doc) else self.root

    def value(self, name, doc):
        """(text, None) or (None, why)."""
        spec = self.figs.get(name)
        if spec is None:
            return None, f"not declared in {self.decl.get('_path', 'the figures file')}"
        kind = spec.get("kind")
        folder = self.folder_for(doc) if kind == "json_command" else self.root
        key = (name, str(folder))
        if key not in self.cache:
            try:
                self.cache[key] = getattr(self, "_" + str(kind))(spec, folder)
            except AttributeError:
                self.cache[key] = (None, f"unknown kind {kind!r}")
        return self.cache[key]

    def _command(self, argv, folder):
        key = (str(folder), tuple(argv))
        if key in self.commands:
            return self.commands[key]
        shown = "`" + " ".join(argv) + "`"
        try:
            r = subprocess.run([sys.executable, str(folder / argv[0]), *argv[1:]], cwd=folder, capture_output=True,
                               text=True, timeout=FIG_TIMEOUT)
        except subprocess.TimeoutExpired:
            out = (None, f"{shown} timed out after {FIG_TIMEOUT} s")
        except OSError as e:
            out = (None, f"{shown} could not run: {e}")
        else:
            if r.returncode != 0:
                out = (None, f"{shown} exited {r.returncode}")
            else:
                try:
                    out = (json.loads(r.stdout), None)
                except ValueError:
                    out = (None, f"{shown} printed no JSON")
        self.commands[key] = out
        return out

    def _json_command(self, spec, folder):
        data, why = self._command(spec["argv"], folder)
        if why:
            return None, why
        field = spec.get("field")
        if not isinstance(data, dict) or field not in data:
            return None, f"`{' '.join(spec['argv'])}` gave no field {field!r}"
        return _fig_format(data[field], spec.get("format")), None

    def _export(self, expr):
        """A value read from this project's tools/export.py, imported in a subprocess of the running interpreter."""
        if expr in self.exports:
            return self.exports[expr]
        code = ("import json, sys; sys.path.insert(0, 'tools'); import export as e; "
                f"print(json.dumps({expr}))")
        try:
            r = subprocess.run([sys.executable, "-c", code], cwd=self.root, capture_output=True, text=True,
                               timeout=FIG_TIMEOUT)
            out = (json.loads(r.stdout), None) if r.returncode == 0 else (None, "tools/export.py could not be read: "
                                                                             + (r.stderr.strip().splitlines() or ["?"])[-1])
        except (OSError, ValueError, subprocess.TimeoutExpired) as e:
            out = (None, f"tools/export.py could not be read: {type(e).__name__}")
        self.exports[expr] = out
        return out

    def _base(self, spec):
        if spec.get("repo") in (None, "", "pack"):
            return self.root, None
        if spec["repo"] == "doctrine":
            path, why = self._export("str(e.DOCTRINE)")
            return (Path(path), None) if path else (None, why)
        return None, f"unknown repo {spec['repo']!r}"

    def _json_count(self, spec, folder):
        base, why = self._base(spec)
        if why:
            return None, why
        path = base / spec["file"]
        if not path.is_file():
            return None, f"{spec['file']} is not there"
        try:
            items = json.loads(path.read_text(encoding="utf-8")).get(spec.get("list", ""), [])
        except (ValueError, AttributeError) as e:
            return None, f"{spec['file']} is not readable JSON ({type(e).__name__})"
        where = spec.get("where") or {}
        return str(sum(1 for x in items if isinstance(x, dict) and all(x.get(k) == v for k, v in where.items()))), None

    def _unittest_count(self, spec, folder):
        code = "import sys, unittest; print(unittest.defaultTestLoader.discover(sys.argv[1]).countTestCases())"
        try:
            r = subprocess.run([sys.executable, "-c", code, spec.get("dir", "tests")], cwd=self.root,
                               capture_output=True, text=True, timeout=FIG_TIMEOUT)
        except (OSError, subprocess.TimeoutExpired) as e:
            return None, f"the unittest loader could not run: {type(e).__name__}"
        out = r.stdout.strip()
        return (out, None) if r.returncode == 0 and out.isdigit() else (None, "the unittest loader failed")

    def _jsonl_last(self, spec, folder):
        path = self.root / spec["file"]
        if not path.is_file():
            return None, f"{spec['file']} is not there"
        where, last = spec.get("where") or {}, None
        for line in path.read_text(encoding="utf-8").splitlines():
            try:
                rec = json.loads(line)
            except ValueError:
                continue
            if isinstance(rec, dict) and all(rec.get(k) == v for k, v in where.items()):
                last = rec
        if last is None:
            return None, f"no record in {spec['file']} matches {where}"
        field = spec["field"]
        value = last.get(field)
        if field == "cost" and value is None:   # an estimate reaches a page only with the word (R-29 § 5.1)
            est = last.get("cost_estimated")
            if isinstance(est, dict) and est.get("cost") is not None:
                return _fig_format(est["cost"], spec.get("format")) + " (estimated)", None
            return None, f"{last.get('run', '?')} has no cost"
        if value is None:
            return None, f"{last.get('run', '?')} has no {field}"
        return _fig_format(value, spec.get("format")), None

    def _roadmap_done_on(self, spec, folder):
        dm = doc_map(self.root)
        pin = self.root / (dm.get("pin") or PIN_DEFAULT)
        m = UPDATED_RE.search(pin.read_text(encoding="utf-8", errors="replace")) if pin.is_file() else None
        if not m:
            return None, "the pin has no `updated:` line"
        rm = dm.get("roadmap") or next((r for r in LINK_DEFAULT_ROADMAPS if (self.root / r).is_file()), None)
        if not rm or not (self.root / rm).is_file():
            return None, "no roadmap"
        heading, n = None, 0
        for line in (self.root / rm).read_text(encoding="utf-8", errors="replace").splitlines():
            h = re.match(r"^##(?!#)\s*(.+?)\s*$", line)
            if h:
                heading = h.group(1)
                continue
            if heading and heading.lower().startswith("done") and re.match(r"^\s*[-*]\s+", line):
                d = re.search(r"\d{4}-\d{2}-\d{2}", line)
                n += bool(d and d.group(0) == m.group(1))
        return str(n), None

    def _shipped_skills(self, spec, folder):
        target = spec.get("target", "kit")
        lists = "e.SHARED + list(getattr(e, 'KIT_ONLY', []))" if target == "kit" else "e.SHARED"
        names, why = self._export(f"sorted({{d.split('/')[1] for _, _, d in {lists} if d.startswith('skills/') "
                                  f"and d.count('/') >= 2}})")
        return (str(len(names)), None) if names is not None else (None, why)

    def _external(self, spec, folder):
        return None, f"checked by {spec.get('checked_by', 'another command')}"


def _fig_format(value, fmt=None):
    if fmt:
        try:
            return fmt.format(value)
        except (ValueError, TypeError):
            return str(value)
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)


def _seat_list(seats):
    names = [SEAT_NAMES.get(s, s) for s in seats]
    return names[0] if len(names) == 1 else ", ".join(names[:-1]) + " and " + names[-1]


def _fail_list(failed):
    by = {}
    for scenario, seat in failed or []:
        by.setdefault(scenario, []).append(seat)
    return "; ".join(f"`{s}` on {_seat_list(seats)}" for s, seats in by.items())


def _note_names(note, scenario, seat, seats):
    """Does the note name this failure, scenario and seat together: as the record's own `scenario-seat`, as the table's
    "`scenario` on <seats>" (backticks optional), or, in a one-seat run, as the scenario alone. A name is whole, never
    part of a longer one: `large_stop-fable` names no Opus failure, and `full_flow_two_lane` is not `full_flow`."""
    s = r"(?<![\w-])`?" + re.escape(scenario)
    if re.search(s + "-" + re.escape(seat) + r"(?![\w-])", note):
        return True
    names = "|".join(re.escape(n) for n in sorted(set(SEAT_NAMES.values()), key=len, reverse=True))
    for m in re.finditer(s + rf"`? on ((?:{names})(?:(?:, | and )(?:{names}))*)(?![\w.]| \d)", note):
        if SEAT_NAMES.get(seat, seat) in re.findall(names, m.group(1)):
            return True
    return len(seats) == 1 and re.search(s + r"(?![\w-])", note) is not None


def runs_table(records, spec):
    """The run table (R-29 C28): one row per record whose selection the spec lists, then one line counting the others
    it summarises. A row's last cell is the record's note, else its FAIL list; a failure the note does not name,
    scenario and seat together (_note_names), leads the cell, so a failure is never hidden behind a note."""
    where = (spec.get("where") or {}).get("selection") or []
    rows = [r for r in records if r.get("selection") in where]
    out = ["| Date | Cases | Result | What failed, and what it was |", "|---|---|---|---|"]
    for r in rows:
        cases, passed = r.get("cases", 0), r.get("passed", 0)
        label = str(cases) + (f" ({_seat_list(r.get('seats') or [])})" if r.get("selection") == "seat"
                              else CASES_SUFFIX.get(r.get("selection"), ""))
        result = f"{passed} / {cases}"
        result = f"**{result}**" if cases and passed == cases else result
        if r.get("not_run"):
            result += f" ({r['not_run']} NOT RUN)"
        note = (r.get("note") or "").strip()
        uncovered = [(s, seat) for s, seat in r.get("failed") or []
                     if not _note_names(note, s, seat, r.get("seats") or [])]
        if uncovered:                      # a failure the note does not name leads the cell: never hidden behind it
            note = _fail_list(uncovered) + ("; " + note if note else "")
        out.append(f"| {r.get('date', '?')} | {label} | {result} | {(note or '—').replace('|', chr(92) + '|')} |")
    summary = spec.get("summary_of")
    if summary:
        key, value = next(iter(summary.items()))
        rest = [r for r in records if r.get(key) == value]
        if rest:
            out += ["", f"plus {len(rest)} targeted runs (`--only`), {sum(r.get('passed', 0) for r in rest)}/"
                        f"{sum(r.get('cases', 0) for r in rest)} cases passed; every run is in `evals/RUNS.jsonl`"]
    return "\n".join(out)


def _table_text(root, name, tables):
    spec = tables.get(name)
    if spec is None:
        return None, "not declared"
    path = Path(root) / spec["file"]
    if not path.is_file():
        return None, f"{spec['file']} is not there"
    records = []
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            records.append(json.loads(line))
        except ValueError:
            return None, f"{spec['file']} has a line that is not JSON"
    return "\n\n" + runs_table(records, spec) + "\n\n", None


def figures_findings(root, decl=None, tree=None):
    """[(level, doc, line, name, message)] for every span, table and mention of the declared docs: level OK, STALE,
    UNKNOWN, MISSING or NOTE (an `external` figure). A doc that is not on disk is MISSING."""
    root = Path(root)
    if decl is None:
        decl, rel, problem = figures_declaration(root)
        if decl is None:
            return [("UNKNOWN", rel or "figures", 0, "figures", problem)] if problem else []
    src = FigureSources(root, decl, tree)
    found = []
    for doc in decl.get("docs") or []:
        p = root / doc
        if not p.is_file():
            found.append(("NOTE", doc, 0, "doc", "a declared document that is not on disk: nothing to check"))
            continue
        text = p.read_text(encoding="utf-8", errors="replace")
        for m in FIG_SPAN_RE.finditer(text):
            name, said, line = m.group(1), m.group(2), _line_at(text, m.start())
            value, why = src.value(name, doc)
            spec = (decl.get("figures") or {}).get(name) or {}
            if value is None and spec.get("kind") == "external":
                found.append(("NOTE", doc, line, name, why))
            elif value is None:
                found.append(("UNKNOWN", doc, line, name, why))
            elif said != value:
                found.append(("STALE", doc, line, name, f"says {said}, source says {value}"))
            else:
                found.append(("OK", doc, line, name, value))
        for m in FIG_TABLE_RE.finditer(text):
            name, line = m.group(2), _line_at(text, m.start())
            want, why = _table_text(root, name, decl.get("tables") or {})
            if want is None:
                found.append(("UNKNOWN", doc, line, "fig-table:" + name, why))
            elif m.group(3) != want:
                found.append(("STALE", doc, line, "fig-table:" + name,
                              f"differs from {(decl.get('tables') or {})[name]['file']} (`figures --write` rewrites it)"))
            else:
                found.append(("OK", doc, line, "fig-table:" + name, "current"))
    for mention in decl.get("mentions") or []:
        doc = mention["doc"]
        base = src.folder_for(doc)
        each = mention["each"]
        try:
            items = json.loads((base / each["file"]).read_text(encoding="utf-8")).get(each["list"], [])
        except (OSError, ValueError) as e:
            found.append(("UNKNOWN", doc, 0, "mentions", f"{each['file']} unreadable ({type(e).__name__})"))
            continue
        text = (root / doc).read_text(encoding="utf-8", errors="replace") if (root / doc).is_file() else ""
        for item in items:
            form = mention.get("form", "{}").format(item.get(each["field"]) if isinstance(item, dict) else item)
            if form not in text:
                found.append(("MISSING", doc, 0, "mention", f"{doc}: {form}"))
    return found


def figures_lines(found):
    """The printed form of figures_findings, and the summary line."""
    lines = []
    for level, doc, line, name, msg in found:
        if level in ("STALE", "UNKNOWN", "NOTE"):
            lines.append(f"{level} {doc}:{line} {name}: {msg}")
        elif level == "MISSING":
            lines.append(f"MISSING {msg}")
    spans = sum(1 for f in found if f[0] in ("OK", "STALE", "UNKNOWN", "NOTE") and not f[3].startswith("fig-table:")
                and f[3] not in ("mentions", "doc"))
    count = lambda lv: sum(1 for f in found if f[0] == lv)
    lines.append(f"figures: {spans} span(s), {count('STALE')} stale, {count('UNKNOWN')} unknown, {count('MISSING')} missing")
    return lines


def figures_write(root, tree=None):
    """Rewrite every span's contents and every marked table from its source; no other byte changes. Returns the
    findings of a check made after the write."""
    root = Path(root)
    decl, _, problem = figures_declaration(root)
    if decl is None:
        return figures_findings(root)
    src = FigureSources(root, decl, tree)
    for doc in decl.get("docs") or []:
        p = root / doc
        if not p.is_file():
            continue
        data = p.read_bytes().decode("utf-8")

        def span(m, doc=doc, data=data):
            value, _ = src.value(m.group(1), doc)
            return m.group(0) if value is None else data[m.start():m.start(2)] + value + data[m.end(2):m.end()]

        def table(m):
            want, _ = _table_text(root, m.group(2), decl.get("tables") or {})
            return m.group(0) if want is None else m.group(1) + want + m.group(4)
        new = FIG_TABLE_RE.sub(table, FIG_SPAN_RE.sub(span, data))
        if new != data:
            p.write_bytes(new.encode("utf-8"))
    return figures_findings(root, decl, tree)


def unmarked_figures(text):
    """(line, match) of each count a declared document states outside a span: the finder, never the check."""
    blank = FIG_TABLE_RE.sub(lambda m: m.group(1) + re.sub(r"[^\n]", " ", m.group(3)) + m.group(4),
                             FIG_SPAN_RE.sub(lambda m: re.sub(r"[^\n]", " ", m.group(0)), text))
    return [(_line_at(blank, m.start()), m.group(0)) for rx in UNMARKED_PATTERNS for m in rx.finditer(blank)]


# ---------------------------------------------------------------------------------------------------- state checks

def record_paths(folder):
    """Every ledger and decision record the skills keep (W-46(b)): redman's ledgers and deep runs, greenman's records
    and its decision ledgers. `facts` and `audit` both read this one list."""
    ledgers, records = skill_records(folder)
    return ledgers + records + decision_ledgers(folder)


def declared_handoff(root):
    """The handoff whose Open items `audit` checks (R-29 § 5.4): the doc map's `handoff:`, else HANDOFF.md at the root;
    None when neither is declared (a handoff found by its name alone never is)."""
    root = Path(root)
    rel = doc_map(root).get("handoff")
    if rel:
        return rel
    return "HANDOFF.md" if (root / "HANDOFF.md").is_file() else None


def roadmap_ids_under(root, heading_prefix):
    """The roadmap's ids whose `##` heading starts with the prefix (`Now`, `Done`)."""
    dm = doc_map(root)
    rm = dm.get("roadmap") or next((r for r in LINK_DEFAULT_ROADMAPS if (Path(root) / r).is_file()), None)
    if not rm or not (Path(root) / rm).is_file():
        return []
    sections = roadmap_sections((Path(root) / rm).read_text(encoding="utf-8", errors="replace"))
    return [i for i, h in sections.items() if h.lower().startswith(heading_prefix.lower())]


def open_items(root):
    """What `facts` lists as open, each as (the label a report shows, the texts that name it in a handoff): each open
    redman ledger row (its item's first 60 characters, or its ledger and date), each greenman record awaiting a ruling
    or owing a real fix (its id or path), each decision ledger row in that state with no record of its own (its choice,
    or its id), each `## Now` roadmap id."""
    root = Path(root)
    items = []
    for led in redman_ledgers(root):
        rel = led.relative_to(root).as_posix()
        rows, _, problem = ledger_table(led)
        for r in rows if not problem else []:
            state = r.get("state")
            if state is None or (not REDMAN_OPEN_RE.match(state) and REDMAN_CLOSED_RE.match(state)
                                 and not r.get("_shift")):
                continue
            what = (r.get("item") or r.get("finding") or r.get("what") or "").strip()
            date = (r.get("date") or "").strip()
            items.append((f"{rel} {date} ({what[:60]})", [what[:60], f"{rel} {date}"]))
    _, records = skill_records(root)
    for rec in records:
        text = rec.read_text(encoding="utf-8", errors="replace")
        if re.search(STATUS_LINE + r"[^\n]*?(?:\bawaiting\b|shortcut taken)", text, re.M | re.I):
            gid = re.match(r"(G-[A-Z]*\d+)", rec.name)
            rel = rec.relative_to(root).as_posix()
            items.append((gid.group(1) if gid else rel, [gid.group(1) if gid else rel, rel]))
    have = {re.match(r"(G-[A-Z]*\d+)", r.name).group(1) for r in records if re.match(r"G-[A-Z]*\d+", r.name)}
    for led in decision_ledgers(root):              # a decision kept only as a ledger row, as `facts` lists it
        rows, _ = decision_table(led)
        for r in rows:
            if r.get("id") in have or not re.search(r"\bawaiting\b|shortcut taken", r.get("status", ""), re.I):
                continue
            choice = (r.get("the choice") or r.get("decision") or r.get("chose") or r.get("id") or "").strip()
            keys = [choice[:60]] + ([r["id"]] if re.fullmatch(r"G-[A-Z]*\d+", r.get("id") or "") else [])
            items.append((f"{led.relative_to(root).as_posix()}: {choice[:60]}", keys))
    for rid in roadmap_ids_under(root, "Now"):
        items.append((rid, [rid]))
    return items


def _names(section, key):
    key = " ".join(key.split())
    if not key:
        return False
    if re.fullmatch(r"[A-Za-z]{1,4}-\d+(?:\.\d+)*", key):   # an id, as a whole word
        return re.search(r"(?<![A-Za-z0-9])" + re.escape(key) + r"(?![0-9.])", section) is not None
    return key.lower() in section.lower()


def handoff_open_items(root):
    """(level-free finding or None): the declared handoff's `## Open items` must name every open item; None when no
    handoff is declared."""
    rel = declared_handoff(root)
    if rel is None:
        return None
    path = Path(root) / rel
    if not path.is_file():
        return ("missing", rel, f"the declared handoff {rel} is not on disk")
    m = re.search(r"^##[ \t]+Open items[^\n]*\n(.*?)(?=^##[ \t]|\Z)", path.read_text(encoding="utf-8", errors="replace"),
                  re.M | re.S | re.I)
    if not m:
        return ("no-section", rel, "handoff has no Open items section")
    section = " ".join(m.group(1).split())
    missing = [label for label, keys in open_items(root) if not any(_names(section, k) for k in keys)]
    return ("omit", rel, "handoff Open items omit: " + "; ".join(missing)) if missing else None


def record_owed_on_done(root):
    """[(record, message)] for each `G-` record whose roadmap item is Done while its status says owed, awaiting or
    still (R-29 § 5.4: a decision record left saying a thing is owed after its item closed)."""
    root = Path(root)
    done = set(roadmap_ids_under(root, "Done"))
    out = []
    _, records = skill_records(root)
    for rec in records:
        text = rec.read_text(encoding="utf-8", errors="replace")
        status = re.search(STATUS_LINE + r"[ \t]*([^\n]*)", text, re.M | re.I)
        value = roadmap_value(text) or ""
        if not status or not re.search(r"\b(?:owed|awaiting|still)\b", status.group(1), re.I):
            continue
        ids = [i for i in re.findall(r"(?<![A-Za-z0-9])R-\d+(?:\.\d+)*", value) if i in done]
        if ids:
            out.append((rec.relative_to(root).as_posix(),
                        f"its roadmap item {ids[0]} is Done, but its status still reads: {status.group(1).strip()[:100]}"))
    return out


INVENTORY_LINE = "roadmap: none — this is the linking backlog itself"


def inventory_findings(root, missing_now):
    """[(path, message)]: an inventory whose heading count differs from a fresh MISSING count (stale), and an inventory
    anywhere else in the project, found by its roadmap line (stray: reported, never deleted)."""
    root = Path(root)
    out, known = [], {p.resolve() for p in existing_inventories(root)}
    for inv in sorted(known):
        head = inv.read_text(encoding="utf-8", errors="replace").split("\n", 1)[0]
        m = re.search(r"UNLINKED\s+—\s+(\d+)\s+document", head)
        if m and int(m.group(1)) != missing_now:
            out.append((inv.relative_to(root).as_posix(), f"lists {m.group(1)} unlinked document(s); a fresh count "
                        f"gives {missing_now} now — `closeout.py link-audit` refreshes it (a snapshot, not the backlog)"))
    for rel in project_files(root):
        p = root / rel
        if not rel.endswith(".md") or p.resolve() in known or not p.is_file():
            continue
        try:
            head = "\n".join(p.read_text(encoding="utf-8", errors="replace").splitlines()[:3])
        except OSError:
            continue
        if INVENTORY_LINE in head:
            out.append((rel, "stray inventory: an UNLINKED list outside the place link-audit writes it (reported, "
                             "not deleted)"))
    return out


# ---------------------------------------------------------------------------------------------------- audit

def rooted(word):
    """True when the word names a place rather than something relative: `/a/b`, `~/a`, and the Windows spellings
    `C:\\a`, `C:/a` and `\\\\box\\share`. A document names its paths for the machine it was written on, so both
    spellings are read here, whichever machine is doing the reading."""
    return word.startswith("~/") or posixpath.isabs(word) or ntpath.isabs(word)


def path_words(span):
    """Words inside a code span that look like a path, stripped of line numbers and punctuation.

    Bare file names (`cc.sh`, `rec.json`) are skipped: they name a file somewhere, not a place to find it, and
    checking them only produced noise. So are placeholders, globs, URLs, flags, `../` prose and slash commands
    (`/closeout`). Quoted words keep their spaces: `rm -rf "/Applications/My Tool.app"`.
    """
    try:
        # posix=False: a backslash is a separator in `C:\logs\x.md`, not an escape. In posix mode shlex eats it and
        # the word becomes `C:logsx.md`, so every Windows path in a document was mis-read or dropped.
        words = shlex.split(span, posix=False)
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
        if rooted(w) or "/" in w.strip("/"):
            yield w


def spaced_path_exists(word, span):
    """A path with spaces in a longer span (`71259 /Applications/My Tool.app/Contents/MacOS/X`) splits
    at its spaces; rejoin the words that follow the one starting it and see whether a real path begins there."""
    if not rooted(word) or word not in span:
        return False
    rest = span[span.index(word):].split(" ")
    return any(Path(os.path.expanduser(" ".join(rest[:n]).rstrip(".,;:)"))).exists() for n in range(len(rest), 1, -1))


def resolve_exists(word, root, base, files):
    """True when the path exists, or when it is not rooted in this project (a branch name, another repo's
    folder), where there is nothing here to check it against."""
    if word.startswith("~/"):
        top = Path.home() / word[2:].split("/", 1)[0]
        return not top.exists() or Path(os.path.expanduser(word)).exists()  # `~/a/b` in prose is an example
    if rooted(word):
        # Rooted the other platform's way (`C:\\proj\\x` read on a Mac, `/usr/local/x` read on Windows) names a place
        # this machine does not have, so there is nothing here to check it against either.
        return Path(word).exists() if os.path.isabs(word) else True
    w = word[2:] if word.startswith("./") else word
    first = w.split("/", 1)[0]
    if not ((root / first).is_dir() or (base / first).is_dir()):
        return True
    # A prefix of a real file counts: `apptests/FileWatcherTests` names apptests/FileWatcherTests.swift.
    return (root / w).exists() or (base / w).exists() or any(f.startswith(w) for f in files)


def missing_paths(span, root, base, files):
    """The path-like words of one code span that name nothing on disk."""
    span = span.replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">").strip()
    if rooted(span) and Path(os.path.expanduser(span)).exists():
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


def audit(folder, changed, pin=None, worklist=None, check_baseline=False, today=None):
    """Checks the lines this close-out added — old text in a file it only marked superseded is history, not its
    work. Whole-file properties (charset, the pin's fields) are checked on the whole file. A broken link FAILs only
    when it is this pass's: its file is in `changed` and its line is one this pass added (or the file is new); any
    other is PRE-EXISTING, reported and never a failure. A staged file this pass did not write is one WARN naming it,
    left as it is. `today` (YYYY-MM-DD, default the clock) is the day the pin's `updated:` must name, so a caller that
    knows the day never reads the clock twice."""
    today = today or datetime.date.today().isoformat()
    folder = Path(folder).resolve()
    root = repo_root(folder) or folder
    findings = []

    def add(level, where, msg):
        findings.append((level, where, msg))

    changed = [Path(c).as_posix() for c in changed]  # git names staged files with /, on Windows too
    for p in link_context(project_root(root), deep=True)["problems"]:
        if p["level"] == "ERROR":   # a map problem is the owner's to fix, never a failure of this close-out
            add("WARN", "doc-map", f"{p['where']} {p['message']}")
    link_found, link_counts = link_audit(root, inventory=False)
    if link_counts.get("no_roadmap"):
        link_found = []
    if link_counts["MISSING"]:
        add("WARN", "linking", f"{link_counts['MISSING']} document(s) of a linked kind carry no `roadmap:` line — "
                               "`closeout.py link-audit` writes the inventory (LINKING.md)")
    added_cache = {}

    def added(rel):
        if rel not in added_cache:
            added_cache[rel] = added_lines(root, rel)
        return added_cache[rel]

    for level, where, msg in link_found:
        if level not in ("BROKEN", "NO FILE", "NO ANCHOR"):
            continue
        path, _, line = where.rpartition(":") if re.search(r":\d+$", where) else (where, "", "")
        if not line:   # a document's own `roadmap:` line: the line it sits on decides whose it is
            try:
                line = str(roadmap_line_number((root / path).read_text(encoding="utf-8", errors="replace")) or "")
            except OSError:
                line = ""
        new = added(path) if path in changed else set()
        mine = path in changed and (new is None or (line.isdigit() and int(line) in new))
        if mine:
            add("FAIL", where, f"linking {level}: {msg}")
        else:
            add("PRE-EXISTING", where, f"linking {level}: {msg} (reported, not this pass's)")
    if repo_root(root):
        loose = untracked_records(repo_root(root), record_paths(root), root)
        if loose:
            add("WARN", "records", f"{len(loose)} ledger or decision record(s) not in git ({', '.join(loose[:4])}): "
                                   "commit them with the work that produced them, or other worktrees and a clean-up lose them")
    staged = [s for s in git(root, "diff", "--cached", "--name-only").splitlines() if s not in changed]
    if staged:
        shown = ", ".join(staged[:6]) + (f", +{len(staged) - 6}" if len(staged) > 6 else "")
        add("WARN", "staged", f"{len(staged)} staged file(s) this close-out did not write: {shown} — another writer's "
                              "work in this index: leave it as it is, and commit only your own files by name "
                              "(UPDATING.md § Commit)")
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
        spans = [(pos, s) for pos, s in spans if fresh(pos) and not is_struck(text, pos)]
        links = [(pos, s) for pos, s in links if fresh(pos) and not is_struck(text, pos)]
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
    def this_pass(rel, line=None):
        """R-24's rule: a finding is this pass's when its file is in `changed` and its line is one this pass added
        (or the file is new, or the finding has no line)."""
        if rel not in changed:
            return False
        new = added(rel)
        return new is None or line is None or line in new

    decl, figures_rel, problem = figures_declaration(root)
    if problem:
        add("FAIL" if figures_rel in changed else "WARN", figures_rel, problem)
    if decl is not None:
        for level, doc, line, name, msg in figures_findings(root, decl):
            if level not in ("STALE", "UNKNOWN", "MISSING"):
                continue
            where = f"{doc}:{line}" if line else doc
            text_msg = f"figure {level}: {name}: {msg}" if level != "MISSING" else f"figure MISSING: {msg}"
            add("FAIL" if this_pass(doc, line or None) else "WARN", where, text_msg)
        for doc in (decl.get("docs") or []):
            if doc not in changed or not (root / doc).is_file():
                continue
            for line, hit in unmarked_figures((root / doc).read_text(encoding="utf-8", errors="replace")):
                if this_pass(doc, line):
                    add("WARN", f"{doc}:{line}", f"unmarked figure? {hit!r} — a count a source gives belongs in a "
                                                 f"`<!-- fig:NAME -->` span (UPDATING.md § Figures)")
    handoff = handoff_open_items(root)
    if handoff is not None:
        kind, rel, msg = handoff
        add("WARN" if kind == "no-section" else ("FAIL" if rel in changed else "WARN"), rel, msg)
    for rel, msg in record_owed_on_done(root):
        add("WARN", rel, msg)
    for rel, msg in inventory_findings(root, link_counts["MISSING"]):
        add("WARN", rel, msg)
    if pin:
        pp = root / pin
        text = pp.read_text(encoding="utf-8", errors="replace") if pp.is_file() else ""
        if not text:
            add("FAIL", pin, "the pin is missing or empty")
        else:
            m = UPDATED_RE.search(text)
            if not m:
                add("FAIL", pin, "no `updated: YYYY-MM-DD` line at the start of a line")
            elif m.group(1) != today:
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
# The `roadmap:` line (LINKING § 2): the key first on its line (after list markers, `>` and bold), a bold label
# `**roadmap:**` anywhere, or the key starting a sentence or a field after `.`, `!`, `?`, `·` or `|`. Heading lines
# are dropped before it is searched: a title such as `# AUDIT ROADMAP: …` is not a declaration.
ROADMAP_LINE_RE = re.compile(r"(?:^[ \t]*(?:(?:[-*+]|\d+\.)[ \t]+|>[ \t]*)*\**|\*\*(?=roadmap)|(?<=[.!?·|])\**[ \t]+\**)"
                             r"roadmap\**[ \t]*:\**[ \t]*(.+?)[ \t]*$", re.I | re.M)
ID_NUM = r"\d+(?:\.\d+)*"   # an id may carry dots: `148.6` and `R-1.23` are ids in their own right
ROW_ID_RE = re.compile(r"^\|\s*(" + ID_NUM + r")\s*\|", re.M)
TAG_ID_RE = re.compile(r"(?<![A-Za-z0-9])R-(" + ID_NUM + r")\b")


def roadmap_line_number(text):
    """The 1-based line of the document's `roadmap:` line in its first 40 lines, or None (roadmap_value's rule)."""
    for n, line in enumerate(text.splitlines()[:40], 1):
        if not re.match(r"^[ \t]{0,3}#", line) and ROADMAP_LINE_RE.search(line):
            return n
    return None


def roadmap_value(text):
    """The value of the document's `roadmap:` line in its first 40 lines (LINKING § 2), or None."""
    lines = [l for l in text.splitlines()[:40] if not re.match(r"^[ \t]{0,3}#", l)]
    m = ROADMAP_LINE_RE.search("\n".join(lines))
    return m.group(1).strip() if m else None


def struck_spans(line):
    """Every `~~…~~` span of one line, as (start, end): a struck citation is withdrawn history."""
    return [(m.start(), m.end()) for m in re.finditer(r"~~.+?~~", line)]


def is_struck(text, pos):
    """True when the position lies inside a struck span of its own line."""
    start = text.rfind("\n", 0, pos) + 1
    end = text.find("\n", pos)
    line = text[start:end if end >= 0 else len(text)]
    return any(a <= pos - start < b for a, b in struck_spans(line))
PROSE_ITEM_RE = re.compile(r"^\s*(?:[-*]\s+|\d+\.\s+)\S", re.M)


def roadmap_sections(text, prefix="R-"):
    """{id: the `##` heading its bullet sits under} for a bullet-tag roadmap: each bullet's first `R-<n>` is its own
    id (a later id on the line is a mention), and the first bullet carrying an id is the item. Deeper headings belong
    to the `##` above them. R-23 extends this per lane."""
    out, heading = {}, None
    for line in text.splitlines():
        m = re.match(r"^##(?!#)\s*(.+?)\s*$", line)
        if m:
            heading = m.group(1)
            continue
        if heading is not None and re.match(r"^\s*[-*]\s+", line):
            tag = (TAG_ID_RE if prefix == "R-" else id_patterns(prefix, "tags")[1]).search(line)
            if tag:
                out.setdefault(tag.group(0), heading)
    return out
CITATION_RE = re.compile(r"`([A-Za-z0-9_./-]+\.(?:md|html?|py|sh|swift|sql|js|txt))(?:#([^`]+))?`|\]\(([^)\s#]+)(?:#([^)]+))?\)")


# ---- the doc map: a typed registry, a loader that reads what is declared and names every problem

# Each key: where it may appear, and what it falls back to when it is wrong. LINKING § 3's key table says the same (a
# test holds the two equal).
KEYS = {
    "roadmap": {"where": ("project", "lane"), "default": "the default roadmaps"},
    "id form": {"where": ("project", "lane"), "default": "detected from the roadmap"},
    "linked kinds": {"where": ("project", "lane"), "default": "the defaults (LINKING § 5)"},
    "exempt": {"where": ("project",), "default": "the defaults (LINKING § 5)"},
    "ledgers": {"where": ("project", "lane"), "default": "the default folders"},
    "decisions": {"where": ("project", "lane"), "default": "the default folders"},
    "guardrails": {"where": ("project",), "default": "report"},
    "pin": {"where": ("project", "lane"), "default": "knowledge/00-CURRENT.md"},
    "brain": {"where": ("project",), "default": None},
    "overview": {"where": ("project",), "default": None},
    "branch": {"where": ("lane",), "default": None},
    "decision ids": {"where": ("lane",), "default": "G- and the first letter of the lane's id prefix"},
    "decision ids outside sequence": {"where": ("project", "lane"), "default": "none"},
    "gate": {"where": ("project",), "default": "not read"},
    "figures": {"where": ("project",), "default": None},
    "handoff": {"where": ("project",), "default": "HANDOFF.md at the root"},
}
OUTSIDE_KEY = "decision ids outside sequence"
OUTSIDE_ONE_RE = re.compile(r"^G-([A-Z]{0,4})(\d+)$")
OUTSIDE_RANGE_RE = re.compile(r"^G-([A-Z]{0,4})(\d+)[ \t]*\.\.[ \t]*G-([A-Z]{0,4})(\d+)$")
OUTSIDE_RANGE_MAX = 100000


def parse_outside(value):
    """(ids, bad entries) of a `decision ids outside sequence` value: ids (`G-<n>`) and ranges (`G-<a>..G-<b>`, one form,
    ascending), comma-separated."""
    ids, bad = set(), []
    for entry in _entries(value or ""):
        one, rng = OUTSIDE_ONE_RE.match(entry), OUTSIDE_RANGE_RE.match(entry)
        if one:
            ids.add(f"G-{one.group(1)}{int(one.group(2))}")
        elif rng and rng.group(1) == rng.group(3) and int(rng.group(2)) <= int(rng.group(4)) \
                and int(rng.group(4)) - int(rng.group(2)) <= OUTSIDE_RANGE_MAX:
            ids.update(f"G-{rng.group(1)}{n}" for n in range(int(rng.group(2)), int(rng.group(4)) + 1))
        else:
            bad.append(entry)
    return ids, bad
MAP_FILES = ("CLAUDE.md", "knowledge/00-CURRENT.md")
PROJECT_HEADING_RE = re.compile(r"^##[ \t]+Doc map(?:[ \t]*|[ \t]*[—–\-:(·][^\n]*)$", re.I)
LANE_HEADING_RE = re.compile(r"^##[ \t]+Doc map:[ \t]*lane[ \t]+(\S+)[ \t]*$", re.I)
LOOKALIKE_RE = re.compile(r"^(#{1,6})[ \t]+(?:the[ \t]+)?doc[ -]?map\b", re.I)
KEY_LINE_RE = re.compile(r"^[ \t]*([A-Za-z][A-Za-z ]*?)[ \t]*:[ \t]*(.*?)[ \t]*$")
ID_FORM_RE = re.compile(r"^(table rows|bullet tags)[ \t]*(?:`?[ \t]*\|?[ \t]*(?:([A-Za-z]{1,4}-)?<n>)?[ \t]*\|?[ \t]*`?)?[ \t]*$",
                        re.I)
LANE_NAME_RE = re.compile(r"^[a-z][a-z0-9-]*$")
DECISION_IDS_RE = re.compile(r"^G-([A-Z]{1,4})<n>$")


def _problem(level, where, key, message, default_used=False):
    return {"level": level, "where": where, "key": key, "message": message, "default_used": default_used}


def _blocks(text):
    """([kind, name, heading line, heading text, [(line number, line)]] per doc-map section, [(line, heading, level)]
    per look-alike heading) of one file's text. A section runs to the next heading of level one or two."""
    sections, looks, current, fence = [], [], None, False
    for n, line in enumerate(text.splitlines(), 1):
        if re.match(r"^[ \t]*(```|~~~)", line):
            fence = not fence
        if fence or re.match(r"^[ \t]*(```|~~~)", line):
            if current is not None:
                current[4].append((n, line))
            continue
        lane = LANE_HEADING_RE.match(line)
        if lane or PROJECT_HEADING_RE.match(line):
            current = ["lane", lane.group(1), n, line.strip(), []] if lane else ["project", "main", n, line.strip(), []]
            sections.append(current)
            continue
        look = LOOKALIKE_RE.match(line)
        if look:
            looks.append((n, line.strip(), len(look.group(1))))
        if re.match(r"^#{1,2}[ \t]", line):
            current = None
            continue
        if current is not None and not look:
            current[4].append((n, line))
    return sections, looks


def _paragraphs(body):
    paras, cur = [], []
    for n, line in body:
        if line.strip():
            cur.append((n, line))
        elif cur:
            paras.append(cur)
            cur = []
    if cur:
        paras.append(cur)
    return paras


def _is_key_paragraph(para):
    for _, line in para:
        if line.lstrip().startswith(("`", "-", "*", "|", ">")):
            continue
        m = KEY_LINE_RE.match(line)
        if m and m.group(1).lower() in KEYS:
            return True
    return False


def _read_block(src, kind, name, body, problems):
    """The raw `key: value` pairs of one block, and the line each came from; every line it cannot read is a problem."""
    values, lines = {}, {}
    for para in _paragraphs(body):
        key_para = _is_key_paragraph(para)
        for n, line in para:
            where = f"{src}:{n}"
            if re.match(r"^[ \t]{0,3}#{3,6}[ \t]", line):
                if not LOOKALIKE_RE.match(line.strip()):
                    problems.append(_problem("ERROR", where, None,
                                             f"sub-heading '{line.strip()}' inside the doc map is not read"))
                continue
            if not key_para:
                continue
            m = KEY_LINE_RE.match(line)
            if not m or line.lstrip().startswith(("`", "-", "*", "|", ">")):
                problems.append(_problem("ERROR", where, None, f"line not read (not `key: value`): {line.strip()[:60]}"))
                continue
            key, value = m.group(1).lower(), m.group(2)
            if key not in KEYS:
                hint = ""
                if key.startswith("exclu"):
                    hint = "; to keep documents out of the check, use `exempt`"
                else:
                    import difflib
                    near = difflib.get_close_matches(key, list(KEYS), n=1, cutoff=0.5)
                    hint = f"; nearest known key: `{near[0]}`" if near else ""
                problems.append(_problem("ERROR", where, key, f"unknown key `{key}` (not read){hint}"))
                continue
            if kind not in KEYS[key]["where"]:
                problems.append(_problem("ERROR", where, key, f"`{key}` is a {KEYS[key]['where'][0]} key (not read in "
                                         + ("a lane)" if kind == "lane" else "the project block)")))
                continue
            if key in values:
                problems.append(_problem("ERROR", where, key, f"`{key}` given twice (the second is not read)"))
                continue
            values[key], lines[key] = value, n
    return values, lines


def _entries(value):
    return [v.strip().strip("`").strip() for v in value.split(",") if v.strip().strip("`").strip()]


def _is_pattern(entry):
    if not entry or re.search(r"\s", entry) or "\\" in entry or not re.search(r"[/*.]", entry):
        return False
    try:
        compile_pattern(entry)
    except PatternError:
        return False
    return True


def _validate(root, src, kind, name, values, lines, problems, deep=False):
    """The block's values that can be read, typed; every problem named with where it is and the default used."""
    root = Path(root)
    out = {}

    def where(key):
        return f"{src}:{lines[key]}"

    def fall(key, message):
        problems.append(_problem("ERROR", where(key), key, message, default_used=True))

    for key in sorted(values, key=lambda k: k != "roadmap"):
        value = values[key]
        if key == "roadmap":
            paths = _entries(value)
            if len(paths) > 1:
                problems.append(_problem("ERROR", where(key), key,
                                         f"`roadmap` names {len(paths)} paths; one roadmap per lane — declare the second "
                                         "as a lane (`## Doc map: lane <name>`, LINKING § 3); checked against the first"))
            if not paths or not (root / paths[0]).is_file():
                fall(key, f"`roadmap: {value}` is not a file in this checkout; "
                          + ("the defaults are looked for" if kind == "project" else
                             "the lane's ids are not checked in this checkout"))
                continue
            out[key] = paths[0]
        elif key == "id form":
            m = ID_FORM_RE.match(value.strip())
            if not m:
                shown = value if len(value) <= 60 else value[:60] + "…"
                if kind == "project":
                    rm = out.get("roadmap") or next((r for r in LINK_DEFAULT_ROADMAPS if (root / r).is_file()), None)
                    rows = bool(rm and (root / rm).is_file() and ROW_ID_RE.search(
                        (root / rm).read_text(encoding="utf-8", errors="replace")))
                    tail = f"auto-detected ({'table rows | <n> |' if rows else 'bullet tags R-<n>'})"
                else:
                    tail = "the lane is not read"
                fall(key, f"`id form: {shown}` is not a form the checker knows (`table rows | <n> |`, "
                          f"`table rows | A-<n> |`, `bullet tags R-<n>`); {tail}")
                continue
            out[key] = value
        elif key in ("linked kinds", "exempt"):
            entries = _entries(value)
            bad = [e for e in entries if not _is_pattern(e)]
            if bad or not entries:
                n = len(bad)
                fall(key, f"`{key}`: {n} {'entry is' if n == 1 else 'entries are'} not a path pattern ("
                          + ", ".join(repr(b) for b in bad) + ")"
                          + ("; the defaults (LINKING § 5) are used" if kind == "project" else "; the lane adds no kinds"))
                continue
            out[key] = ", ".join(entries)
            if deep:
                for e in entries:
                    if not expand(root, [e]):
                        problems.append(_problem("WARN", where(key), key, f"`{key}` entry `{e}` matches no file"))
        elif key == "ledgers":
            good = []
            for e in _entries(value):
                if re.search(r"\s", e) or not re.search(r"[/.]", e):
                    problems.append(_problem("ERROR", where(key), key, f"`ledgers` entry `{e}` is not a path (not read)"))
                    continue
                good.append(e)
                if not (root / e).exists():
                    problems.append(_problem("NOTE", where(key), key,
                                             f"`ledgers` entry `{e}` does not exist yet (redman creates it)"))
            if good:
                out[key] = ", ".join(good)
        elif key == "decisions":
            entries = _entries(value)
            if len(entries) > 1:
                problems.append(_problem("ERROR", where(key), key, f"`decisions` names {len(entries)} folders; one "
                                         f"folder per lane (the first, {entries[0]}, is used)"))
            first = entries[0] if entries else ""
            if not first or (root / first).is_file() or first.lower().endswith(".md"):
                fall(key, f"`decisions: {value}` names a file; it takes the folder greenman writes `G-` records and "
                          "LEDGER.md into (not read; the default folders are used)")
                continue
            if not (root / first).is_dir():
                problems.append(_problem("NOTE", where(key), key, f"`decisions: {first}` does not exist yet "
                                                                  "(greenman creates it)"))
            out[key] = first
        elif key == "guardrails":
            v = value.strip().lower()
            if v not in ("off", "report", "block"):
                fall(key, f"`guardrails: {value}` is not off, report or block; report is used")
                continue
            out[key] = v
        elif key in ("pin", "brain", "overview", "figures", "handoff"):
            entries = _entries(value)
            if len(entries) != 1:
                problems.append(_problem("ERROR", where(key), key, f"`{key}` names {len(entries)} files; it takes one "
                                                                   "(not read)"))
                continue
            if not (root / entries[0]).is_file():
                problems.append(_problem("WARN", where(key), key, f"`{key}: {entries[0]}` is not in this checkout"))
            out[key] = entries[0]
        elif key == "branch":
            v = value.strip().strip("`").strip()
            try:
                compile_pattern(v)
                out[key] = v
            except PatternError:
                problems.append(_problem("ERROR", where(key), key, f"`branch: {value}` is not a pattern (not read)"))
        elif key == "gate":
            v = value.strip().lower()
            if v != "config-hook":
                fall(key, f"`gate: {value}` is not config-hook (not read; where the project's tracked hooks folder is "
                          "core.hooksPath, the gate is not registered)")
                continue
            out[key] = v
        elif key == OUTSIDE_KEY:
            ids, bad = parse_outside(value)
            if bad or not ids:
                n = len(bad)
                fall(key, f"`{key}`: {n} {'entry is' if n == 1 else 'entries are'} not a decision id or a range "
                          f"(`G-<n>`, `G-<a>..G-<b>`) (" + ", ".join(repr(b) for b in bad) + "); none is used")
                continue
            out[key] = value.strip()
        elif key == "decision ids":
            v = value.strip().strip("`").strip()
            if not DECISION_IDS_RE.match(v):
                fall(key, f"`decision ids: {value}` is not `G-<letters><n>` (not read; the default is used)")
                continue
            out[key] = v
    return out


def _form_prefix(form):
    """The id prefix an id form declares: `A-` for `table rows | A-<n> |`; `R-` for bullet tags without one; none for
    rows without one."""
    m = ID_FORM_RE.match((form or "").strip())
    if not m:
        return ""
    return (m.group(2) or ("" if m.group(1).lower() == "table rows" else "R-"))


def _detected_form(root, values):
    rm = values.get("roadmap") or next((r for r in LINK_DEFAULT_ROADMAPS if (Path(root) / r).is_file()), None)
    rows = bool(rm and (Path(root) / rm).is_file() and ROW_ID_RE.search(
        (Path(root) / rm).read_text(encoding="utf-8", errors="replace")))
    return "table rows" if rows else "bullet tags"


def load_doc_map(root, deep=False):
    """The doc map as the checker reads it: where it was found, the project block's typed values and lines, the raw
    lane sections, every problem (file:line, level, key, message, whether a default was used) and a hash of the map's
    text. `deep` adds the checks that walk the tree (a pattern matching no file)."""
    root = Path(root)
    problems, found = [], None
    texts = {}
    for rel in MAP_FILES:
        p = root / rel
        if not p.is_file():
            continue
        text = p.read_text(encoding="utf-8", errors="replace")
        sections, looks = _blocks(text)
        texts[rel] = (text, sections, looks)
        if found is None and any(s[0] == "project" for s in sections):
            found = rel
    out = {"source": found, "heading": None, "heading_line": None, "project": {}, "lines": {}, "lanes": [],
           "lane_lines": {}, "problems": problems, "hash": hashlib.sha1(b"").hexdigest()[:12]}
    looked = [found] if found else [r for r in texts if texts[r][2]][:1]
    for rel in looked:
        text, sections, looks = texts[rel]
        for n, line, level in looks:
            problems.append(_problem("ERROR", f"{rel}:{n}", None,
                                     f"heading '{line}' looks like a doc map but is not `## Doc map` (not read)"
                                     + (": make it a level-two heading" if level > 2 else "")))
        if not found:
            out["source"] = rel   # the only map-like heading in the file is a look-alike: its problems are the map's
    if found:
        text, sections, looks = texts[found]
        project_seen, lane_names, map_text = False, set(), []
        for kind, name, n, heading, body in sections:
            map_text.append(heading + "\n" + "\n".join(l for _, l in body))
            where = f"{found}:{n}"
            if kind == "project":
                if project_seen:
                    problems.append(_problem("ERROR", where, None, "a second `## Doc map` heading (not read; the first "
                                                                   "is read)"))
                    continue
                project_seen = True
                out["heading"], out["heading_line"] = heading, n
                values, lines = _read_block(found, "project", "main", body, problems)
                out["project"] = _validate(root, found, "project", "main", values, lines, problems, deep)
                out["lines"] = lines
                continue
            if not project_seen:
                problems.append(_problem("ERROR", where, None, f"lane `{name}` comes before `## Doc map` (not read)"))
                continue
            if name == "main":
                problems.append(_problem("ERROR", where, None, "a lane cannot be named `main`: it is the project block's "
                                                               "name (not read)"))
                continue
            if not LANE_NAME_RE.match(name):
                problems.append(_problem("ERROR", where, None, f"lane name `{name}` is not lower-case letters, digits "
                                                               "and hyphens (not read)"))
                continue
            if name in lane_names:
                problems.append(_problem("ERROR", where, None, f"lane `{name}` is declared twice (the second is not read)"))
                continue
            lane_names.add(name)
            values, lines = _read_block(found, "lane", name, body, problems)
            typed = _validate(root, found, "lane", name, values, lines, problems, deep)
            missing = [k for k in ("roadmap", "id form") if k not in values]
            if missing:
                problems.append(_problem("ERROR", where, None, f"lane `{name}` has no `{missing[0]}` (not read)"))
                continue
            if "id form" not in typed:
                continue
            out["lanes"].append({"name": name, "heading_line": n, "values": typed, "lines": lines})
        out["hash"] = hashlib.sha1("\n".join(map_text).encode("utf-8")).hexdigest()[:12]
        main_prefix = _form_prefix(out["project"].get("id form") or _detected_form(root, out["project"]))
        seen = {main_prefix: "main"}
        decids = {}
        kept = []
        for lane in out["lanes"]:
            where = f"{found}:{lane['heading_line']}"
            prefix = _form_prefix(lane["values"]["id form"])
            if prefix in seen:
                problems.append(_problem("ERROR", where, "id form", f"lane `{lane['name']}`: id prefix `{prefix or '(none)'}` "
                                         f"is lane `{seen[prefix]}`'s (not read)"))
                continue
            seen[prefix] = lane["name"]
            letters = DECISION_IDS_RE.match(lane["values"]["decision ids"]).group(1) if lane["values"].get(
                "decision ids") else (prefix[:1].upper() or lane["name"][:1].upper())
            if letters in decids:
                problems.append(_problem("ERROR", where, "decision ids", f"lane `{lane['name']}`: decision ids `G-{letters}"
                                         f"<n>` are lane `{decids[letters]}`'s (not read)"))
                continue
            decids[letters] = lane["name"]
            lane.update(prefix=prefix, decids=letters)
            kept.append(lane)
        out["lanes"] = kept
    problems.sort(key=lambda p: (p["where"].split(":")[0], int(p["where"].rsplit(":", 1)[1])))
    return out


def doc_map(root):
    """The project block of the doc map, the values that passed validation, with `_source`; {} when there is none."""
    m = load_doc_map(root)
    return dict(m["project"], _source=m["source"]) if m["heading"] else {}


# ---- one matcher: LINKING § 3's path patterns, the same on every Python and platform

class PatternError(ValueError):
    """A path pattern the matcher cannot read; the doc map's validator names it, the matcher skips it."""


_CASE = re.IGNORECASE if os.path.normcase("A") != "A" else 0   # letter case follows the platform (Windows ignores it)


def _component(comp, raw):
    out, i = [], 0
    while i < len(comp):
        c = comp[i]
        if c == "*":
            out.append("[^/]*")
        elif c == "?":
            out.append("[^/]")
        elif c == "[":
            j = i + 1
            if j < len(comp) and comp[j] == "!":
                j += 1
            if j < len(comp) and comp[j] == "]":
                j += 1
            end = comp.find("]", j)
            if end < 0:
                out.append(re.escape(c))
            else:
                body = comp[i + 1:end]
                neg = body.startswith("!")
                body = body[1:] if neg else body
                body = body.replace("\\", "\\\\")
                if not neg and body.startswith("^"):
                    body = "\\" + body
                out.append("(?!/)[" + ("^" if neg else "") + body + "]")
                i = end
        else:
            out.append(re.escape(c))
        i += 1
    return "".join(out)


@functools.lru_cache(maxsize=1024)
def compile_pattern(pat):
    """A path pattern as a regex over posix relative paths: `*`, `?` and `[…]` stay within one folder; `**` as a whole
    component is any number of folders (none included), and a trailing `**` every file below."""
    raw = pat
    pat = pat.strip().strip("`").strip()
    if pat.startswith("./"):
        pat = pat[2:]
    if "\\" in pat:
        raise PatternError(f"use / in patterns: '{raw}'")
    parts = pat.split("/")
    out = []
    for i, comp in enumerate(parts):
        last = i == len(parts) - 1
        if comp == "**":
            out.append(".*" if last else "(?:[^/]+/)*")
            continue
        if "**" in comp:
            raise PatternError(f"`**` must be a whole path component: '{raw}'")
        out.append(_component(comp, raw) + ("" if last else "/"))
    try:
        return re.compile("".join(out) + r"\Z", _CASE)
    except re.error as e:
        raise PatternError(f"{e}: '{raw}'") from None


def kind_match(rel, patterns):
    """True when the posix relative path matches one of the patterns; a pattern that cannot be read is skipped (the
    validator reports it: the matcher never crashes a hook)."""
    for pat in patterns:
        if not pat or not pat.strip():
            continue
        try:
            rx = compile_pattern(pat)
        except PatternError:
            continue
        if rx.match(rel):
            return True
    return False


def static_prefix(pat):
    """The pattern's leading folders that carry no `*`, `?` or `[`: where a walk for it starts."""
    pat = pat.strip().strip("`").strip()
    if pat.startswith("./"):
        pat = pat[2:]
    keep = []
    for comp in pat.split("/")[:-1]:
        if re.search(r"[*?\[]", comp):
            break
        keep.append(comp)
    return "/".join(keep)


def expand(root, patterns):
    """Every file under root that kind_match takes: one walk per distinct static prefix, `.git` and symlinked folders
    never entered; sorted posix relative paths."""
    root = Path(root)
    pats = []
    for pat in patterns:
        try:
            if pat and pat.strip():
                compile_pattern(pat)
                pats.append(pat)
        except PatternError:
            continue
    prefixes = sorted({static_prefix(p) for p in pats})
    prefixes = [p for p in prefixes if not any(q != p and (q == "" or p.startswith(q + "/")) for q in prefixes)]
    found = set()
    for pre in prefixes:
        base = root / pre if pre else root
        if not base.is_dir():
            continue
        for dirpath, dirnames, filenames in os.walk(str(base), followlinks=False):
            dirnames[:] = sorted(d for d in dirnames if d != ".git")
            for name in filenames:
                full = os.path.join(dirpath, name)
                rel = Path(full).relative_to(root).as_posix()
                if os.path.isfile(full) and kind_match(rel, pats):
                    found.add(rel)
    return sorted(found)


# ---- one verdict: every layer asks judge()

def project_root(folder):
    """The project a folder belongs to: its git work tree; outside git, the nearest folder upward (the folder itself
    included) holding CLAUDE.md or a default roadmap; else the folder."""
    folder = Path(folder).resolve()
    root = repo_root(folder)
    if root:
        return Path(root)
    for d in [folder] + list(folder.parents):
        if (d / "CLAUDE.md").is_file() or any((d / r).is_file() for r in LINK_DEFAULT_ROADMAPS):
            return d
    return folder


def id_patterns(prefix, layout):
    """(roadmap id regex, value id regex) for one id form: rows `| <P><n> |`, tags `<P><n>`; dotted ids included."""
    p = re.escape(prefix)
    tag = re.compile(r"(?<![A-Za-z0-9])" + p + r"(" + ID_NUM + r")\b")
    row = re.compile(r"^\|\s*" + p + r"(" + ID_NUM + r")\s*\|", re.M)
    return (row if layout == "rows" else tag), tag


def _lane_from(root, values, name="main", prefix=None, decids=""):
    """A lane's roadmap, id form, ids, kinds and records from its typed values (main: the project block, with the
    default roadmaps and kinds)."""
    root = Path(root)
    rm_rel = values.get("roadmap") or (next((r for r in LINK_DEFAULT_ROADMAPS if (root / r).is_file()), None)
                                       if name == "main" else None)
    kinds = link_kinds(values) if name == "main" else link_kinds(values, defaults=False)
    lane = {"name": name, "roadmap": None, "form": values.get("id form"), "layout": None, "prefix": prefix or "",
            "ids": set(), "text": "", "kinds": kinds, "ledgers": _declared(values, "ledgers"),
            "decisions": values.get("decisions"), "pin": values.get("pin"), "branch": values.get("branch"),
            "decids": decids, "outside": parse_outside(values.get(OUTSIDE_KEY, ""))[0]}
    if not rm_rel or not (root / rm_rel).is_file():
        return lane
    text = (root / rm_rel).read_text(encoding="utf-8", errors="replace")
    form = values.get("id form") or _detected_form(root, values)
    layout = "rows" if form.strip().lower().startswith("table rows") else "tags"
    prefix = _form_prefix(form) if prefix is None else prefix
    rm_re, _ = id_patterns(prefix, layout)
    lane.update(roadmap=rm_rel, form=form, layout=layout, prefix=prefix, text=text, ids=set(rm_re.findall(text)))
    return lane


def link_context(root, deep=False):
    """What every layer needs to judge a document in this project, built once per root: the doc map's lanes (their
    roadmaps and ids), the linked kinds, the exempt patterns, what is never judged, the mode and the map's problems.
    `deep` adds the checks that walk the tree; the hook and the gate never ask for them."""
    root = Path(root)
    m = load_doc_map(root, deep)
    dm = dict(m["project"])
    lanes = [_lane_from(root, dm)] + [_lane_from(root, l["values"], l["name"], l["prefix"], l["decids"])
                                      for l in m["lanes"]]
    never, kinds = [], []
    for lane in lanes:
        never += [x for x in (lane["roadmap"], lane["pin"]) if x]
        kinds += lane["kinds"]
    return {"root": root, "source": m["source"], "heading_line": m["heading_line"], "hash": m["hash"],
            "lanes": lanes, "kinds": list(dict.fromkeys(kinds)),
            "exempt": [e.strip().strip("`") for e in (dm.get("exempt") or ", ".join(LINK_DEFAULT_EXEMPT)).split(",")],
            "never": list(dict.fromkeys(never)), "mode": guardrails_mode(dm), "problems": m["problems"],
            "map": dict(dm, _source=m["source"]) if m["heading"] else {}, "loaded": m}


def cited_by_lane(value, lanes):
    """{lane name: [ids]} a `roadmap:` value names: an id with a lane's prefix wherever it appears; bare numbers (a
    leading list, or after `row`/`rows`) for the lane whose form has no prefix. A prefix no lane declares is no id."""
    out = {}
    for lane in lanes:
        if lane["layout"] is None and lane["form"] is None:
            continue
        if lane["prefix"]:
            _, val_re = id_patterns(lane["prefix"], lane["layout"] or "tags")
            ids = val_re.findall(value)
        else:
            ids = cited_ids(value, "table rows")
        if ids:
            out[lane["name"]] = ids
    return out


def document_lane(ctx, rel):
    """The lane whose linked kind matches the path most specifically (the longest static prefix; ties: the order of
    declaration), else main."""
    best, name = -1, "main"
    for lane in ctx["lanes"]:
        for pat in lane["kinds"]:
            if kind_match(rel, [pat]):
                n = len(static_prefix(pat))
                if n > best:
                    best, name = n, lane["name"]
    return name


def judge(ctx, rel, text=None):
    """The one verdict: (LINKED|NONE|MISSING|BROKEN, message), or None when the document is not judged (not a linked
    kind, exempt, a roadmap or pin, the inventory) or the project has no roadmap."""
    main = ctx["lanes"][0]
    if not main["roadmap"] or rel in ctx["never"] or re.search(r"(^|/)UNLINKED-\d{4}-\d\d-\d\d\.md$", rel):
        return None
    if not kind_match(rel, ctx["kinds"]) or kind_match(rel, ctx["exempt"]):
        return None
    if text is None:
        text = (Path(ctx["root"]) / rel).read_text(encoding="utf-8", errors="replace")
    val = roadmap_value(text)
    if val is None:
        return "MISSING", "no `roadmap:` line in the first 40 lines"
    if val.lower().startswith("none"):
        return ("NONE", val[:120]) if re.match(r"none\s*[—–-]\s*\S", val, re.I) else \
            ("BROKEN", "`roadmap: none` without a reason")
    lanes = [l for l in ctx["lanes"] if l["form"] or l["roadmap"]]
    by = cited_by_lane(val, lanes)
    if not by:
        return "BROKEN", f"`roadmap:` names no id: {val[:80]}"
    bad, unchecked, named = [], [], []
    for lane in lanes:
        ids = by.get(lane["name"], [])
        written = [lane["prefix"] + i for i in ids]
        named += written
        if not ids:
            continue
        if not lane["roadmap"]:
            unchecked += written
            continue
        missing = [lane["prefix"] + i for i in ids if i not in lane["ids"]]
        if missing:
            bad.append(f"roadmap id(s) not in {lane['roadmap']}: {', '.join(missing)}")
    if bad:
        return "BROKEN", "; ".join(bad)
    return "LINKED", ", ".join(x for x in named if x not in unchecked) + (
        f"; not checked here: {', '.join(unchecked)}" if unchecked else "")


def _level_count(ctx, level):
    return sum(1 for p in ctx["problems"] if p["level"] == level)


def _fallback_keys(ctx):
    return list(dict.fromkeys(p["key"] for p in ctx["problems"] if p["level"] == "ERROR" and p["default_used"]))


def file_config_message(ctx, rel):
    """The Write|Edit hook's word when the file just written holds a doc map with errors; None otherwise."""
    errors = [p for p in ctx["problems"] if p["level"] == "ERROR"]
    if not errors or not any(p["where"].rsplit(":", 1)[0] == rel for p in errors):
        return None
    shown = "; ".join(f"{p['where']}: {p['message']}" for p in errors[:3])
    more = f" (+{len(errors) - 3} more)" if len(errors) > 3 else ""
    return (f"doc-guardrails: {rel} — the doc map you just wrote has {len(errors)} problem(s): {shown}{more}. Every layer "
            "checks with the defaults for these keys until they are fixed; `closeout.py doc-map` lists them (LINKING.md "
            "§ 3). A map another lane or person owns is not edited to quiet this: say what it needs and leave it.")


def staged_config_line(ctx):
    """The commit gate's one line about a doc map with errors (stderr); None when it has none."""
    n = _level_count(ctx, "ERROR")
    if not n:
        return None
    keys = _fallback_keys(ctx)
    return (f"doc-guardrails: the doc map has {n} problem{'s' if n != 1 else ''}"
            + (f"; this commit was checked with the defaults for {', '.join(keys)}" if keys else "")
            + " — `closeout.py doc-map .` lists them (report only: a map problem never refuses a commit)")


def stop_config_message(ctx):
    """The end-of-turn check's once-per-map note (shown by the Stop hook as a systemMessage, never a block)."""
    errors = [p for p in ctx["problems"] if p["level"] == "ERROR"]
    if not errors:
        return None
    keys = _fallback_keys(ctx)
    return (f"doc-guardrails: the doc map in {ctx['root']}/{ctx['source']} has {len(errors)} problem(s) ("
            + "; ".join(f"{p['where']}: {p['message']}" for p in errors[:2]) + ")"
            + (f"; every layer is checking with the defaults for {', '.join(keys)}" if keys else "")
            + f". `closeout.py doc-map {ctx['root']}` lists them. This is for the user; nothing in this turn needs to "
            "change because of it.")


def write_unlinked_inventory(root, findings, today=None):
    """One worklist file for a thread to link from: every MISSING document with its first heading, its date and its
    folder. A snapshot of the commit it names, replaced by the next run that writes one (never by a `--no-inventory`
    run); never edits the documents themselves (the guardrail is live and constant, not "go fix stuff")."""
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
             f"first 40 lines (LINKING.md § 2). A snapshot from {today} at {_short_head(root)}: run `closeout.py "
             "link-audit` to refresh it; do not edit by hand.", "",
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


def _short_head(root):
    return git(root, "rev-parse", "--short", "HEAD") or "no commit"


def existing_inventories(root):
    """Every UNLINKED inventory in the project, including one an earlier layout left in the pack folder."""
    dirs = INVENTORY_DIRS + (PACK_DIR + "/doc-guardrails",)
    return [p for d in dirs if (root / d).is_dir() for p in (root / d).glob("UNLINKED-*.md")]


def _declared(dm, key):
    return [v.strip().strip("`") for v in (dm.get(key) or "").split(",") if v.strip()]


def link_kinds(dm, defaults=True):
    """The linked kinds: the doc map's own list (else the defaults; a lane has none), plus every place its `ledgers:`
    and `decisions:` declare, so a lane's ledger, the deep runs beside it and the decision records are checked even
    when the map lists kinds without them."""
    kinds = [k.strip().strip("`") for k in (dm.get("linked kinds") or (", ".join(LINK_DEFAULT_KINDS) if defaults else
                                                                      "")).split(",")]
    for led in _declared(dm, "ledgers"):
        if not led.endswith(".md"):
            kinds.append(led.rstrip("/") + "/*.md")  # a folder of ledgers
        else:
            kinds.append(led.rsplit("/", 1)[0] + "/*.md" if "/" in led else led)  # the ledger and the deep runs beside it
    kinds += [d.rstrip("/") + "/*.md" for d in _declared(dm, "decisions")]
    return [k for k in dict.fromkeys(kinds) if k]


def decision_ledgers(folder):
    """Greenman's ledgers: `LEDGER.md` in every decisions folder the doc map declares (project block and lanes), and in
    the default places."""
    folder = Path(folder)
    return sorted({folder / d / "LEDGER.md" for d in _all_declared(folder, "decisions") + list(DEFAULT_DECISION_DIRS)
                   if (folder / d / "LEDGER.md").is_file()})


def _id_order(gid):
    m = re.match(r"G-([A-Z]*)(\d+)", gid)
    return (m.group(1), int(m.group(2))) if m else (gid, 0)


def _lane_named(folder, lane):
    ctx = link_context(project_root(folder))
    return next((l for l in ctx["lanes"] if l["name"] == lane), None), ctx


def _lane_records(folder, lane):
    """(records, ledgers) in one lane's decisions folders: its `G-*.md` files and its LEDGER.md."""
    folder = Path(folder)
    records, ledgers = [], []
    for d in lane_decision_folders(folder, lane):
        records += sorted(p for p in (folder / d).glob("G-*.md") if p.is_file())
        if (folder / d / "LEDGER.md").is_file():
            ledgers.append(folder / d / "LEDGER.md")
    return records, ledgers


def resolve_decision_id(folder, gid, citing_rel):
    """Where a decision id a document cites is recorded, in the lane of the citing document (main's `G-<n>` and a
    lane's `G-<n>` of the same number are different decisions): the record file, else `<ledger>:<line>` of its row; None when that lane has none.
    An id the doc map lists as outside the sequence resolves the same way, and fails the same way without a record."""
    folder = Path(folder)
    ctx = link_context(project_root(folder))
    name = document_lane(ctx, citing_rel)
    lane = next(l for l in ctx["lanes"] if l["name"] == name)
    records, ledgers = _lane_records(folder, lane)
    for r in records:
        if re.match(re.escape(gid) + r"(?:\D|$)", r.name):
            return r.relative_to(folder).as_posix()
    for led in ledgers:
        for n, found in ledger_row_ids(led.read_text(encoding="utf-8", errors="replace")):
            if found == gid:
                return f"{led.relative_to(folder).as_posix()}:{n}"
    return None


def decision_ids(folder, lane=None):
    """The G-<n> ids the project has a record of: every `G-<n>-*.md` record skill_records finds, and every row of the
    decision ledgers (decision_ledgers) whose id cell is one; with `lane`, only that lane's folder and ledger (main's
    `G-<n>` and a lane's `G-<n>` of the same number are different decisions). Only those files are read: no link-audit, no git status, so it is
    fast enough for a Stop hook (R-28 §5.3; the evals' receipt check is its first user)."""
    folder = Path(folder)
    if not folder.is_dir():
        return set()
    if lane is not None:
        found, _ = _lane_named(folder, lane)
        records, ledgers = _lane_records(folder, found) if found else ([], [])
        ids = {m.group(1) for r in records for m in [re.match(r"(G-[A-Z]{0,4}\d+)(?:\D|$)", r.name)] if m}
        for led in ledgers:
            ids.update(i for _, i in ledger_row_ids(led.read_text(encoding="utf-8", errors="replace")))
        return ids
    _, records = skill_records(folder)
    ids = {m.group(1) for r in records for m in [re.match(r"(G-[A-Z]{0,4}\d+)(?:\D|$)", r.name)] if m}
    for led in decision_ledgers(folder):
        ids.update(i for _, i in ledger_row_ids(led.read_text(encoding="utf-8", errors="replace")))
    return ids


def _ro_git(root, *args):
    """(exit code, stdout) of one read-only git call: GIT_OPTIONAL_LOCKS=0, so a lookup never refreshes an index or
    takes index.lock in a checkout another session is using, and it writes nothing; gc.auto=0 and maintenance.auto=false,
    so no call can start git's own background upkeep in the project."""
    r = subprocess.run(["git", "-C", str(root), *args], capture_output=True, encoding="utf-8", errors="replace",
                       env=dict(os.environ, GIT_OPTIONAL_LOCKS="0", GIT_CONFIG_COUNT="2",
                                GIT_CONFIG_KEY_0="gc.auto", GIT_CONFIG_VALUE_0="0",
                                GIT_CONFIG_KEY_1="maintenance.auto", GIT_CONFIG_VALUE_1="false"))
    return r.returncode, r.stdout.rstrip("\n")


_BRANCH_IDS = {}   # (folders, commit) -> ids: this process's memo of commit_decision_ids


def commit_decision_ids(top, commit, folders):
    """The G-ids one commit records in the given decisions folders (paths from the work tree's top), by the working
    tree's rules: a `G-<n>-*.md` record directly in a folder, named by `git ls-tree -r --name-only` (names only), and
    each folder's LEDGER.md rows by `git show <commit>:<folder>/LEDGER.md`, read by its id column."""
    code, out = _ro_git(top, "ls-tree", "-r", "--name-only", commit, "--", *folders)
    names = set(out.splitlines()) if code == 0 else set()
    ids = set()
    for name in names:
        parent, _, base = name.rpartition("/")
        m = re.match(r"(G-[A-Z]{0,4}\d+)(?:\D|$)", base) if base.endswith(".md") else None
        if m and parent in folders:
            ids.add(m.group(1))
    for d in folders:
        if f"{d}/LEDGER.md" in names:
            code, text = _ro_git(top, "show", f"{commit}:{d}/LEDGER.md")
            if code == 0:
                ids.update(i for _, i in ledger_row_ids(text))
    return ids


def branch_decision_ids(folder, cache=None):
    """The G-ids recorded on any local branch of the folder's repository: for each branch head, the records and
    ledger rows commit_decision_ids reads in the decisions folders the folder's doc map declares (project block and
    lanes) and the default places, so a lane's record resolves from any checkout before it merges. A commit's tree
    never changes, so each head is read once: memoised in this process and, with `cache` (a folder, the state folder's
    decision-ids/), in one file per repository keeping the current heads only. Every git call is read-only
    (GIT_OPTIONAL_LOCKS=0: rev-parse, for-each-ref, ls-tree, show); nothing is written into the project. Outside a git
    work tree: set()."""
    folder = Path(folder)
    code, out = _ro_git(folder, "rev-parse", "--show-toplevel", "--git-common-dir")
    lines = out.splitlines()
    if code or len(lines) != 2:
        return set()
    top = Path(os.path.realpath(lines[0]))
    common = os.path.realpath(os.path.join(str(folder), lines[1]))
    try:
        prefix = Path(os.path.realpath(folder)).relative_to(top).as_posix()
    except ValueError:
        return set()
    prefix = "" if prefix == "." else prefix + "/"
    folders = tuple(dict.fromkeys(prefix + d.strip("/") for d in _all_declared(folder, "decisions")
                                  + list(DEFAULT_DECISION_DIRS) if d.strip("/")))
    code, refs = _ro_git(top, "for-each-ref", "--format=%(objectname)", "refs/heads/")
    heads = sorted(set(refs.split())) if code == 0 else []
    spec = "\n".join(folders)
    where = None
    saved = {}
    if cache is not None:
        import zlib
        where = Path(cache) / f"{zlib.crc32(common.encode('utf-8', 'surrogateescape')):08x}.json"
        try:
            data = json.loads(where.read_text(encoding="utf-8"))
            if data.get("version") == 1 and data.get("common") == common and data.get("folders") == spec:
                saved = {k: set(v) for k, v in data["heads"].items()}
        except (OSError, ValueError, TypeError, KeyError, AttributeError):
            saved = {}
    ids, now = set(), {}
    for sha in heads:
        got = _BRANCH_IDS.get((spec, sha))
        if got is None:
            got = saved.get(sha)
        if got is None:
            got = commit_decision_ids(top, sha, folders)
        _BRANCH_IDS[(spec, sha)] = got
        now[sha] = got
        ids |= got
    if where is not None and now != saved:
        try:
            where.parent.mkdir(parents=True, exist_ok=True)
            tmp = where.parent / f".{where.name}.{os.getpid()}.tmp"
            tmp.write_text(json.dumps({"version": 1, "common": common, "folders": spec,
                                       "heads": {k: sorted(v) for k, v in now.items()}}), encoding="utf-8")
            os.replace(tmp, where)
        except OSError:
            pass    # the cache only saves the next lookup its git calls
    return ids


def resolved_decision_ids(folder, ids, cache=None):
    """The ids among `ids` that have a record: the working tree's first (decision_ids); only when one is not
    there, every local branch's (branch_decision_ids, with its `cache`), so a record that sits on another worktree's
    branch until it merges is found, and an id with no record on any branch is still missing. The receipt check,
    tools/receipt_ids.py and the evals' receipt check make this one lookup."""
    want = list(dict.fromkeys(ids))
    known = decision_ids(folder)
    found = {i for i in want if i in known}
    if len(found) < len(want):
        on_branches = branch_decision_ids(folder, cache)
        found |= {i for i in want if i in on_branches}
    return found


def _cells(line):
    """A table line's cells: split on each `|` not escaped as `\\|` (GitHub's table rule, code spans included), an
    escaped one kept in its cell as `|`."""
    s = line.strip()
    s = s[1:] if s.startswith("|") else s
    s = s[:-1] if s.endswith("|") and not s.endswith("\\|") else s
    return [c.strip().replace("\\|", "|") for c in re.split(r"(?<!\\)\|", s)]


_TABLE_SEP_RE = re.compile(r"^\s*\|?\s*:?-{3,}:?\s*(\|\s*:?-{3,}:?\s*)*\|?\s*$")
_DECISION_ID_RE = re.compile(r"G-[A-Z]{0,4}\d+")


def ledger_row_ids(text):
    """[(line number, id)] of a decision ledger's rows, read by the header's `id` column wherever it stands (a ledger
    is read by column name, and one may put `id` first, another second). A table whose header has no
    `id` column, and rows before any header, fall back to the first two cells. A header is a `|` line followed by a
    separator row, as ledger_table reads it."""
    lines = text.splitlines()
    out, col = [], None
    for n, line in enumerate(lines, 1):
        if not line.lstrip().startswith("|") or _TABLE_SEP_RE.match(line):
            continue
        if n < len(lines) and _TABLE_SEP_RE.match(lines[n]):
            names = [_header_name(c) for c in _cells(line)]
            col = names.index("id") if "id" in names else None
            continue
        cells = _cells(line)
        cands = cells[:2] if col is None else cells[col:col + 1]
        out += [(n, c) for c in cands if _DECISION_ID_RE.fullmatch(c)]
    return out


def _header_name(cell):
    return re.sub(r"[*`:]", "", cell).strip().lower()


def ledger_columns(path):
    """Every column name a markdown ledger's headers declare (a header as ledger_table reads it), so a ledger with its
    header and no rows yet is read as well-formed and empty, never as lacking the column."""
    lines = Path(path).read_text(encoding="utf-8", errors="replace").splitlines()
    return {_header_name(c) for i, line in enumerate(lines[:-1])
            if line.lstrip().startswith("|") and not _TABLE_SEP_RE.match(line) and _TABLE_SEP_RE.match(lines[i + 1])
            for c in _cells(line)}


def ledger_table(path):
    """(rows, notes, problem) of a markdown ledger read by its header: the first `|` line followed by a separator row
    is the header (names lower-cased without `*`, backticks or `:`); a later header and separator start a new table; a
    row after a non-table line continues the table above and is noted. Each row is {header name: cell, "_line": n}.
    problem is `unreadable: no header row` when the file holds table rows but no header."""
    return ledger_table_text(Path(path).read_text(encoding="utf-8", errors="replace"))


def ledger_table_text(text):
    """ledger_table on a ledger's text (a copy read from a branch with `git show`, which has no file)."""
    lines = text.splitlines()
    rows, notes, header, prev, headerless = [], [], None, False, False
    sep = _TABLE_SEP_RE
    i = 0
    while i < len(lines):
        line = lines[i]
        if line.lstrip().startswith("|"):
            if i + 1 < len(lines) and sep.match(lines[i + 1]):
                header, prev = [_header_name(c) for c in _cells(line)], True
                i += 2
                continue
            if header is None:
                headerless = True
            else:
                if not prev:
                    notes.append(i + 1)
                cells = _cells(line)
                row = dict(zip(header, cells))
                row["_line"] = i + 1
                if len(cells) > len(header):   # a stray `|` shifted every later cell: never read as if it fit
                    row["_shift"] = f"{len(cells)} cells under a {len(header)}-column header"
                rows.append(row)
            prev = True
        else:
            prev = False
        i += 1
    return rows, notes, ("unreadable: no header row" if headerless and not rows else None)


# The redman states (the redman skill's ledger columns): open, closed, and the rest, which count open and are listed.
REDMAN_OPEN_RE = re.compile(r"^(filed|asked · (?:later|now)|open-for-user)\b")
REDMAN_CLOSED_RE = re.compile(r"^(ruled by the user\b|closed — |dropped — )")
DEEP_STATE_RE = re.compile(r"converged|paused|NOT CONVERGED")


def ledger_state(path, base=None):
    """What a redman ledger holds, read by its `state` column: {"open", "unknown" [path:line: 'cell'], "unreadable"
    (a reason or None), "breaks" [path:line]}."""
    path = Path(path)
    name = path.relative_to(base).as_posix() if base else path.name
    rows, notes, problem = ledger_table(path)
    out = {"open": 0, "unknown": [], "unreadable": None, "breaks": [f"{name}:{n}" for n in notes]}
    if problem:
        out["unreadable"] = problem
        return out
    stated = [r for r in rows if "state" in r]
    if not stated and "state" not in ledger_columns(path):
        out["unreadable"] = "no `state` column"
        return out
    for r in stated:
        cell = r["state"]
        if r.get("_shift"):
            out["open"] += 1
            out["unknown"].append(f"{name}:{r['_line']}: {r['_shift']} (a `|` inside a cell?)")
        elif REDMAN_OPEN_RE.match(cell):
            out["open"] += 1
        elif not REDMAN_CLOSED_RE.match(cell):
            out["open"] += 1
            out["unknown"].append(f"{name}:{r['_line']}: '{cell[:60]}'")
    return out


def _all_declared(folder, key):
    """A key's declared paths in the project block and in every lane."""
    m = load_doc_map(project_root(folder))
    return _declared(m["project"], key) + [d for l in m["lanes"] for d in _declared(l["values"], key)]


def _ledger_files(folder, decls):
    folder = Path(folder)
    found = set()
    for decl in decls:
        target = folder / decl
        if target.is_dir():
            target = target / "LEDGER.md"
        if target.is_file():
            found.add(target)
    return found


def redman_ledgers(folder):
    """The ledger files that count open rows: every ledger file the doc map declares (project block and lanes),
    `LEDGER.md` in a declared ledger folder, and `LEDGER.md` in a default folder. A deep run beside a ledger is a
    record, not a ledger."""
    folder = Path(folder)
    found = set()
    for decl in _all_declared(folder, "ledgers"):
        target = folder / decl
        if target.is_dir():
            target = target / "LEDGER.md"
        if target.is_file():
            found.add(target)
    found.update(folder / d / "LEDGER.md" for d in DEFAULT_REDMAN_DIRS if (folder / d / "LEDGER.md").is_file())
    return sorted(found)


def deep_runs(ledgers, base):
    """The `REDMAN-*.md` deep runs beside the ledgers, each with its last converged / paused / NOT CONVERGED line."""
    out = []
    for folder in sorted({l.parent for l in ledgers}):
        for p in sorted(folder.glob("REDMAN-*.md")):
            last = [l.strip() for l in p.read_text(encoding="utf-8", errors="replace").splitlines()
                    if DEEP_STATE_RE.search(l)]
            out.append({"path": p.relative_to(base).as_posix(), "last_state": last[-1][:120] if last else None})
    return out


def decision_table(led):
    """(rows, problem) of a greenman ledger: rows with an `id` and a `status` column; problem when it has none."""
    rows, _, problem = ledger_table(led)
    if problem:
        return [], problem
    if not any("status" in r for r in rows) and "status" not in ledger_columns(led):
        return [], "no `status` column"
    return [r for r in rows if "status" in r], None


def ledger_rows_with_status(folder, ledgers, records, pattern):
    """Decision-ledger rows whose status matches but that have no `G-` file of their own (an S call leaves only its
    row): `<ledger>: <the choice>`. Read by the header's `id`, `status` and choice column; a ledger without `status`
    is unreadable and gives nothing. A row whose id has a record is already counted from the record."""
    have = {re.match(r"(G-[A-Z]*\d+)", r.name).group(1) for r in records if re.match(r"G-[A-Z]*\d+", r.name)}
    out = []
    for led in ledgers:
        rows, problem = decision_table(led)
        for r in rows:
            if not re.search(pattern, r.get("status", ""), re.I) or r.get("id") in have:
                continue
            choice = r.get("the choice") or r.get("decision") or r.get("chose") or r.get("id", "")
            out.append(f"{led.relative_to(folder).as_posix()}: {choice}")
    return out


def _branches(root, glob):
    """Local branches whose names match a lane's `branch:` pattern (read-only)."""
    if not glob:
        return []
    names = git(root, "for-each-ref", "--format=%(refname:short)", "refs/heads/").splitlines()
    return [b for b in names if kind_match(b, [glob])]


def _decision_numbers(text_names, letters, outside=()):
    """Numbers of `G-<letters><n>` ids among names and id cells, less the ids of that form the doc map lists as outside
    the sequence; in a lane, the legacy bare `G-<n>` ids it recorded before its own form still set the floor, so a new
    `G-<letters><n>` never repeats a number the lane has used."""
    own = re.compile(r"^G-" + re.escape(letters) + r"(\d+)(?:\D|$)")
    legacy = re.compile(r"^G-(\d+)(?:\D|$)") if letters else None
    out = []
    for name in text_names:
        m = own.match(name)
        if m:
            if f"G-{letters}{int(m.group(1))}" not in outside:
                out.append(int(m.group(1)))
            continue
        m = legacy.match(name) if legacy else None
        if m:
            out.append(int(m.group(1)))
    return out


def _ledger_ids(text):
    ids = []
    for line in text.splitlines():
        if line.lstrip().startswith("|"):
            ids += [c for c in _cells(line) if re.fullmatch(r"G-[A-Z]{0,4}\d+", c)]
    return ids


def lane_decision_folders(folder, lane):
    """A lane's decisions folders: its declared one, else (main only) the default places."""
    if lane["decisions"]:
        return [lane["decisions"]]
    return list(DEFAULT_DECISION_DIRS) if lane["name"] == "main" else []


def next_decision_id(folder, lane):
    """(next id, what was read): one past the highest `G-<letters><n>` (and, in a lane, legacy `G-<n>`) recorded in
    the lane's decisions folder and ledger, in this checkout and on every local branch its `branch:` matches."""
    folder = Path(folder)
    root = repo_root(folder)
    letters = lane["decids"]
    names, read = [], []
    for d in lane_decision_folders(folder, lane):
        if (folder / d).is_dir():
            names += [p.name for p in (folder / d).glob("G-*.md")]
            if (folder / d / "LEDGER.md").is_file():
                names += _ledger_ids((folder / d / "LEDGER.md").read_text(encoding="utf-8", errors="replace"))
            read.append(f"checkout: {d}")
        if root:
            rel = (folder / d).resolve().relative_to(Path(root).resolve()).as_posix() if (folder / d).exists() else d
            for b in _branches(root, lane["branch"]):
                listed = git(root, "ls-tree", "-r", "--name-only", b, "--", rel).splitlines()
                names += [x.rsplit("/", 1)[-1] for x in listed]
                code, text = run_git(root, "show", f"{b}:{rel}/LEDGER.md")
                if code == 0:
                    names += _ledger_ids(text)
                read.append(f"branch {b}: {rel}")
    nums = _decision_numbers(names, letters, lane.get("outside", set()))
    return f"G-{letters}{max(nums) + 1 if nums else 1}", read


def lane_checkouts(root, glob):
    """["<path> (<branch>)"] of every worktree of the repository on a branch the lane's pattern matches."""
    out, path = [], None
    for line in git(root, "worktree", "list", "--porcelain").splitlines():
        if line.startswith("worktree "):
            path = native_path(line[9:])
        elif line.startswith("branch ") and path:
            branch = line[7:].replace("refs/heads/", "", 1)
            if kind_match(branch, [glob]):
                out.append(f"{path} ({branch})")
    return out


def project_identity(folder):
    """The project a checkout belongs to: the main worktree (the parent of git's common dir); outside git, the project
    root. A linked worktree is the same project as its main checkout."""
    folder = Path(folder).resolve()
    code, common = run_git(folder, "rev-parse", "--git-common-dir")
    if code == 0 and common:
        p = Path(common)
        p = (folder / p) if not p.is_absolute() else p
        return p.resolve().parent
    return project_root(folder)


def undeclared_record_folders(folder, declared):
    """Folders under knowledge/ or docs/ that hold `G-<n>-…md` records but that no map declares and no default is."""
    folder = Path(folder)
    known = set(declared) | set(DEFAULT_DECISION_DIRS)
    out = set()
    for rel in expand(folder, ["knowledge/**/G-*.md", "docs/**/G-*.md"]):
        name = rel.rsplit("/", 1)[-1]
        parent = rel.rsplit("/", 1)[0]
        if re.match(r"^G-[A-Z]*\d+-.*\.md$", name) and parent not in known:
            out.add(parent)
    return sorted(out)


def skill_records(folder):
    """(redman ledgers, greenman decision records) for the project: every ledger and decision folder its doc map
    declares, plus the default places. The pack's side ledger is not the project's and is not in either list."""
    folder = Path(folder)
    ledgers = set()
    for decl in _all_declared(folder, "ledgers"):
        target = folder / decl
        ledgers.update(target.glob("*.md") if target.is_dir() else [target] if target.is_file() else [])
    for d in DEFAULT_REDMAN_DIRS:
        ledgers.update(p for p in (folder / d).glob("*.md") if p.is_file())
    records = set()
    for d in _all_declared(folder, "decisions") + list(DEFAULT_DECISION_DIRS):
        records.update(p for p in (folder / d).glob("G-*.md") if p.is_file())
    return sorted(ledgers), sorted(records)


def open_ledger_rows(path):
    """Open rows of one redman ledger, by its `state` column (a state outside the vocabulary counts open)."""
    return ledger_state(path)["open"] if Path(path).is_file() else 0


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
    """`guardrails:` in the doc map, as validated: off, report (the default, and what a wrong value falls back to) or
    block (the commit gate refuses)."""
    v = (dm.get("guardrails") or "report").strip().lower()
    return v if v in ("off", "report", "block") else "report"


def doc_map_report(folder, lane=None):
    """(text, exit code) of `closeout.py doc-map`: where the map was read, each lane's roadmap and records, every
    pattern with the files it matches, then every problem; exit 1 on an ERROR."""
    root = project_root(folder)
    ctx = link_context(root, deep=True)
    m = ctx["loaded"]
    lines = [f'doc-map: {m["source"]}:{m["heading_line"]} "{m["heading"]}"' if m["heading"] else
             "doc-map: no `## Doc map` — the defaults (LINKING.md § 5) are used"]
    for n, ln in enumerate(ctx["lanes"]):
        if lane and ln["name"] != lane:
            continue
        # the project's pin is the default when it declares none (facts, the stamp); a lane's is only what it declares,
        # which is what lane_pin_guard compares, so a lane with none shows `-`, as a lane with no branch does
        pin = ln["pin"] or (PIN_DEFAULT if n == 0 else "-")
        lines.append(f"lane {ln['name']}: roadmap {ln['roadmap'] or '(none)'} ({ln['form'] or '-'}, {len(ln['ids'])} ids) · "
                     f"pin {pin} · branch {ln['branch'] or '-'} · ledgers "
                     f"{', '.join(ln['ledgers']) or '(defaults)'} · decisions {ln['decisions'] or '(defaults)'}")
    dm = ctx["map"]
    for key, pats in (("linked kinds", ctx["kinds"]), ("exempt", ctx["exempt"])):
        declared = "declared" if dm.get(key) else "defaults"
        for pat in pats:
            lines.append(f"  {key}: {pat} → {len(expand(root, [pat]))} files ({declared})")
    for p in ctx["problems"]:
        lines.append(f"{p['level']:6} {p['where']}  {p['message']}")
    e, w, n = (sum(1 for p in ctx["problems"] if p["level"] == lv) for lv in ("ERROR", "WARN", "NOTE"))
    lines.append(f"doc-map: {e} ERROR · {w} WARN · {n} NOTE")
    return "\n".join(lines), 1 if e else 0


def staged_gate(folder, heartbeat=False):
    """`link-audit --staged`: the commit gate. Prints each staged document's verdict; exits 1 only when the doc map
    says `guardrails: block` and a document needs a line. Any error prints `gate error` and never refuses a
    report-only commit. With `--heartbeat` (the launcher's form) each run is recorded."""
    root = project_root(folder)
    mode, result = "report", "error"
    try:
        ctx = link_context(root)
        mode = ctx["mode"]
        found, mode = link_check_staged(folder, ctx)
        line = staged_config_line(ctx) if mode != "off" else None
        if line:
            print(line, file=sys.stderr)
        for level, where, msg in found:
            print(f"doc-guardrails: {level} — {where}: {msg}")   # the gate's verdict line: GATE_VERDICT_RE reads it
        if found:
            print(f"doc-guardrails: {len(found)} staged document(s) need a `roadmap:` line"
                  + (" — commit refused (`guardrails: block` in the doc map)" if mode == "block" else " — report only, commit continues"))
            if mode == "block":
                print("doc-guardrails: add the line to a document this session wrote, with its own tools or through a "
                      "command it ran (a generator's or a script's output is the session's), then commit again. A document "
                      "someone else wrote (staged by the user, another session's) is not edited to get past this gate: say "
                      "which line it needs and ask. Never --no-verify, never edit the doc map or this hook.")
        result = "refused" if (found and mode == "block") else "report" if found else "clean"
        code = 1 if result == "refused" else 0
    except Exception as e:  # the gate never refuses a report-only commit over its own failure
        print(f"doc-guardrails: gate error: {type(e).__name__}: {e}", file=sys.stderr)
        result, code = "error", (1 if mode == "block" else 0)
    if heartbeat:
        try:
            write_gate_heartbeat(Path(repo_root(root) or root), result)
        except Exception:
            pass
    return code


def link_check_staged(folder, ctx=None):
    """The commit gate's question: which staged markdown files of a linked kind are unlinked or broken? Returns
    (findings, mode). Only what is being committed is judged, so the gate stays going-forward only."""
    root = project_root(folder)
    ctx = ctx or link_context(root)
    mode = ctx["mode"]
    if mode == "off":
        return [], mode
    staged = git(root, "diff", "--cached", "--name-only", "--diff-filter=ACMR").splitlines()
    found = []
    for rel in staged:
        if rel.endswith(".md"):
            code, staged_text = run_git(root, "show", f":{rel}")  # what is being committed, not the working tree
            hit = _check_one(ctx, rel, text=staged_text if code == 0 else None)
            if hit:
                found.append((hit[0], rel, hit[1]))
    return found, mode


GATE_BEGIN = "# >>> doc-guardrails commit gate >>>"
GATE_END = "# <<< doc-guardrails commit gate <<<"
# The line `link-audit --staged` prints for each staged document it judges (main(), below): the gate's verdict. The
# evals' gate_fires probe reads it, so a change to that print changes this regex in the same commit. "gate skipped",
# "gate error" and "commit refused: the gate cannot run" are not verdicts, and do not match.
GATE_VERDICT_RE = re.compile(r"^doc-guardrails: (?P<level>[A-Z][A-Z ]*[A-Z]) — (?P<where>.+?): ", re.M)


class GitProbeError(RuntimeError):
    """A git call the managed-hooks test needed failed: the answer is "could not tell", never "not managed"."""


class GateState:
    """What install-gate did: its state line, the exit code, the hook file it wrote (or None) and any notes."""

    def __init__(self, line, code=0, hook=None, notes=()):
        self.line, self.code, self.hook, self.notes = line, code, hook, list(notes)


def _probe(root, *args, ok=(0,)):
    r = subprocess.run(["git", "-C", str(root), *args], capture_output=True, encoding="utf-8", errors="replace")
    if r.returncode not in ok:
        first = (r.stderr.strip().splitlines() or [""])[0]
        raise GitProbeError(f"git {' '.join(args[:2])}: exit {r.returncode}: {first}")
    return r


def worktrees(root):
    """Every worktree of the repository (`git worktree list --porcelain`), as paths."""
    r = _probe(root, "worktree", "list", "--porcelain")
    return [native_path(line[9:]) for line in r.stdout.splitlines() if line.startswith("worktree ")]


def path_within(p, roots, pathmod=os.path):
    """(root, ancestor) for the first ancestor of p, walking upward from p itself, that is the same file as one of the
    roots; None when there is none. By file identity (samefile), never by a string: a sibling folder that shares a
    root's path as a prefix is not inside it, a path spelled with another case on a disk that folds case is, and a
    root on another drive is never compared, so nothing raises."""
    drive = pathmod.splitdrive(p)[0].lower()
    candidates = [r for r in roots if pathmod.splitdrive(r)[0].lower() == drive]
    cur = p
    while True:
        if pathmod is os.path and os.path.exists(cur):
            for r in candidates:
                if os.path.exists(r) and os.path.samefile(cur, r):
                    return r, cur
        parent = pathmod.dirname(cur)
        if parent == cur:
            return None
        cur = parent


def _on_disk(base, rel_parts):
    """The relative path spelled as it is on disk: each existing component as its directory entry names it."""
    out, cur = [], Path(base)
    for part in rel_parts:
        match = part
        if cur.is_dir():
            for entry in os.listdir(cur):
                if entry == part or (os.path.exists(cur / entry) and (cur / part).exists()
                                     and os.path.samefile(cur / entry, cur / part)):
                    match = entry
                    break
        out.append(match)
        cur = cur / match
    return "/".join(out)


def hooks_managed_by_project(root):
    """The configured core.hooksPath when it is a folder the project tracks (inside one of its worktrees, and `git
    ls-files` lists files under it); None when the key is unset, empty, or names an untracked folder, a folder outside
    the repository or git's own hooks. Raises GitProbeError when a git call it needs fails."""
    root = Path(root)
    r = _probe(root, "config", "--type=path", "--get", "core.hooksPath", ok=(0, 1))
    value = r.stdout.strip()
    if r.returncode == 1 or not value:
        return None
    top = Path(_probe(root, "rev-parse", "--show-toplevel").stdout.strip())
    p = Path(value) if os.path.isabs(value) else top / value
    p = os.path.realpath(str(p))
    trees = [os.path.realpath(w) for w in worktrees(root)]
    hit = path_within(p, trees)
    if hit is None:
        return None
    tree, ancestor = hit
    rel_parts = Path(p).relative_to(ancestor).parts if p != ancestor else ()
    rel = _on_disk(tree, rel_parts) if rel_parts else "."
    listed = _probe(tree, "ls-files", "--", rel).stdout.strip()
    return value if listed else None


def hook_owner(root):
    """(the pre-commit file the pack owns, None) or (None, why it owns none): the pack owns only git's own hooks
    folder's pre-commit, used when core.hooksPath is unset or names that folder. A file in any worktree's tree, or in
    a shared folder outside the repository, is the project's."""
    root = Path(root)
    common = Path(git(root, "rev-parse", "--path-format=absolute", "--git-common-dir") or (root / ".git"))
    own = common / "hooks"
    value = git(root, "config", "--type=path", "--get", "core.hooksPath")
    if value:
        p = Path(value) if os.path.isabs(value) else root / value
        if not (p.exists() and own.exists() and os.path.samefile(p, own)):
            return None, f"core.hooksPath is {value}, a folder the pack does not own"
    return own / "pre-commit", None


# The launcher git runs as the config hook (one shell line: `man git-config`, "an executable path or a shell oneliner").
# It finds the helper at run time (CLOSEOUT_PY, else the skills folder of CLAUDE_CONFIG_DIR or ~/.claude) and reads its
# exit code: 0 passes; 1 refuses only where the doc map says `guardrails: block`; anything else — a pack older or newer
# than this launcher, a crash, no helper — is "could not run": a report-only project commits with a note. The block
# test reads the `guardrails:` key where the loader reads it (the first project `## Doc map` block of CLAUDE.md, else
# of knowledge/00-CURRENT.md), in plain sh and awk, so it knows the mode when no interpreter runs. When the PATH git
# passes finds no awk (an empty PATH: git still prepends its exec-path), /usr/bin:/bin are added first, so a `block`
# project is refused rather than passed as "gate skipped".
GATE_LAUNCHER = r"""command -v awk >/dev/null 2>&1 || PATH="${PATH:+$PATH:}/usr/bin:/bin"; f="${CLOSEOUT_PY:-${CLAUDE_CONFIG_DIR:-$HOME/.claude}/skills/closeout/closeout.py}"; p="${CLOSEOUT_PYTHON:-}"; [ -n "$p" ] || p="$(command -v python3 || command -v python || true)"; b=0; for m in CLAUDE.md knowledge/00-CURRENT.md; do [ -f "$m" ] || continue; awk '{sub(/\r$/,""); l=tolower($0)} /^##?[ \t]/{if(d)exit; if(!h && l ~ /^##[ \t]+doc map([ \t]*$|[ \t]*([-:(]|[^ -~]))/ && l !~ /^##[ \t]+doc map:[ \t]*lane[ \t]/)h=d=1; next} d && l ~ /^[ \t]*guardrails:[ \t]*block[ \t]*$/{f=1} END{exit h?(f?0:1):2}' "$m"; r=$?; [ "$r" -eq 0 ] && b=1; [ "$r" -ne 2 ] && break; done; if [ -f "$f" ] && [ -n "$p" ] && "$p" -c "" 2>/dev/null; then "$p" "$f" link-audit . --staged --heartbeat; r=$?; [ "$r" -eq 0 ] && exit 0; [ "$r" -eq 1 ] && [ "$b" -eq 1 ] && exit 1; w="the gate could not run (closeout.py exit $r: a pack older or newer than this launcher, or an error)"; else w="the gate cannot run (no python or closeout.py found)"; fi; if [ "$b" -eq 1 ]; then echo "doc-guardrails: commit refused: $w and this project says guardrails: block" >&2; exit 1; fi; echo "doc-guardrails: gate skipped: $w; report-only project, commit continues" >&2; exit 0"""
HEARTBEATS_KEPT = 50
HARNESS_ROOTS = ()


def state_dir():
    """The doc-guardrails state folder both logins share (DOC_GUARDRAILS_STATE, else ~/.claude/doc-guardrails-state):
    the end-of-turn check's sessions, the gate's heartbeats and registration records, and a rollback's hold."""
    return Path(os.environ.get("DOC_GUARDRAILS_STATE") or Path.home() / ".claude" / "doc-guardrails-state")


def common_dir(root):
    out = git(root, "rev-parse", "--path-format=absolute", "--git-common-dir")
    return Path(os.path.realpath(out)) if out else None


def gate_key(root):
    cd = common_dir(root)
    return hashlib.sha1(str(cd).encode("utf-8")).hexdigest()[:16] if cd else None


def probe_config_hooks(root):
    """True when this git runs config hooks (`git hook list` exists; git 2.54+). Never trusts `git --version`."""
    r = subprocess.run(["git", "-C", str(root), "hook", "list", "pre-commit"], capture_output=True, encoding="utf-8",
                       errors="replace")
    return r.returncode != 129 and "unknown subcommand" not in (r.stderr or "")


def git_version():
    return (git(".", "--version").split() or ["", "", "?"])[-1]


def gate_reach(text):
    """Line numbers before the gate block that can end the hook (`exit`, `exec` or `return` as a word, outside a
    comment): [] means the block runs."""
    out = []
    for n, line in enumerate(text.splitlines(), 1):
        if GATE_BEGIN in line:
            break
        code = line.split("#", 1)[0] if not line.lstrip().startswith("#!") else ""
        if re.search(r"\b(exit|exec|return)\b", code):
            out.append(n)
    return out


def gate_block():
    """The fallback's block: the launcher in a subshell, so its own exit never ends the rest of the hook."""
    return (f"{GATE_BEGIN}\n# Added by closeout.py install-gate: every staged document of a linked kind names its roadmap "
            f"item.\n( {GATE_LAUNCHER} ) || exit 1\n{GATE_END}\n")


def handover(hook):
    return ("hand-over: place this block in the project's pre-commit hook"
            + (f" ({hook})" if hook else "") + ", after the shebang and any `set` lines and before the first line that "
            "can end the hook:\n" + gate_block())


def _read_hook(path):
    with open(path, encoding="utf-8", errors="replace", newline="") as fh:
        return fh.read()


def _write_hook(path, text):
    with open(path, "w", encoding="utf-8", newline="") as fh:
        fh.write(text)


def _drop_block(text):
    """The text without the pack's block (line endings kept); unchanged when there is none."""
    if GATE_BEGIN not in text or GATE_END not in text:
        return text
    start = text.rfind("\n", 0, text.index(GATE_BEGIN)) + 1
    end = text.index(GATE_END) + len(GATE_END)
    rest = text[end:]
    rest = rest[2:] if rest.startswith("\r\n") else rest[1:] if rest.startswith("\n") else rest
    return text[:start] + rest


def _insert_first(text):
    """The block after the shebang and any leading blank, comment or `set` lines; CRLF files keep CRLF."""
    nl = "\r\n" if "\r\n" in text else "\n"
    lines = text.split(nl)
    i = 1 if lines and lines[0].startswith("#!") else 0
    while i < len(lines) and (not lines[i].strip() or lines[i].lstrip().startswith("#") or
                              re.match(r"^\s*set\b", lines[i])):
        i += 1
    block = gate_block().replace("\n", nl)
    return nl.join(lines[:i]) + nl + block + nl.join(lines[i:])


SHELLS = {"sh", "bash", "dash", "zsh", "ksh"}


def _shell_hook(text):
    first = text.splitlines()[0] if text else ""
    if not first.startswith("#!"):
        return False
    parts = first[2:].split()
    if parts and os.path.basename(parts[0]) == "env":
        parts = parts[1:]
    return bool(parts) and os.path.basename(parts[0]) in SHELLS


def _hook_path(root):
    """The pre-commit file git runs as the hook file (core.hooksPath's, else git's own)."""
    value = git(root, "config", "--type=path", "--get", "core.hooksPath")
    if value:
        return (Path(value) if os.path.isabs(value) else Path(root) / value) / "pre-commit"
    cd = common_dir(root)
    return cd / "hooks" / "pre-commit" if cd else None


def _placed_block(root):
    """The project's own hook file when it holds a reachable gate block that passes --heartbeat (placed by the
    project), else None."""
    hook = _hook_path(root)
    owned, _ = hook_owner(root)
    if hook is None or not hook.is_file() or (owned and os.path.realpath(hook) == os.path.realpath(owned)):
        return None
    text = _read_hook(hook)
    if GATE_BEGIN in text and "--heartbeat" in text and not gate_reach(text):
        return hook
    return None


def _registered_command(root):
    return git(root, "config", "--get", "hook.doc-guardrails.command") or None


def _last_ran(root):
    runs = heartbeats(root)
    if not runs:
        return "last ran: never"
    h = runs[-1]
    when = datetime.datetime.fromtimestamp(h["time"]).isoformat(timespec="seconds")
    return f"last ran: {when} on {(h.get('head_before') or 'no commit yet')[:8]}"


def _clean_owned(root, notes):
    """Remove the pack's old block from the hook file the pack owns; name a block found in a file it does not own."""
    owned, _ = hook_owner(root)
    if owned and owned.is_file():
        text = _read_hook(owned)
        cleaned = _drop_block(text)
        if cleaned != text:
            _write_hook(owned, cleaned)
    hook = _hook_path(root)
    if hook and hook.is_file() and not (owned and os.path.realpath(hook) == os.path.realpath(owned)):
        text = _read_hook(hook)
        if GATE_BEGIN in text:
            line = text[:text.index(GATE_BEGIN)].count("\n") + 1
            notes.append(f"{hook}:{line} holds a pack gate block; the file is the project's: it is not edited (see "
                         "the hand-over)")


def install_gate(folder):
    """Register the doc-guardrails commit gate as a git config hook (git 2.54+), which git runs before any hook file in
    every worktree; on an older git, place it first in the hook file the pack owns (git's own hooks folder). Never
    written: a hook file the pack does not own (exit 3, the hand-over), a repository that declines with git's own
    `hook.doc-guardrails.enabled=false`, one whose core.hooksPath is a folder it tracks until its doc map says
    `gate: config-hook`, or anywhere while a pack rollback holds registrations. Returns a GateState."""
    folder = Path(folder).resolve()
    root = repo_root(folder)
    if not root:
        raise ValueError(f"{folder} is not in a git repository: no commit gate to install")
    root = Path(root)
    hold = state_dir() / "gates" / "HOLD"
    if hold.exists():
        return GateState(f"commit gate: registration held for a pack rollback ({hold}); nothing written")
    notes = []
    r = subprocess.run(["git", "-C", str(root), "config", "--show-scope", "--type=bool", "--get",
                        "hook.doc-guardrails.enabled"], capture_output=True, encoding="utf-8", errors="replace")
    scope, _, value = (r.stdout.strip().partition("\t") if r.returncode == 0 else ("", "", ""))
    if value == "false":
        _clean_owned(root, notes)
        return GateState(f"commit gate: declined by the repository (hook.doc-guardrails.enabled=false in {scope} config)",
                         0, None, notes)
    try:
        managed = hooks_managed_by_project(root)
    except GitProbeError as e:
        return GateState(f"commit gate: not registered: could not tell whether the project manages its hooks ({e}); "
                         "nothing written")
    supported = probe_config_hooks(root)
    placed = _placed_block(root)
    if managed and doc_map(root).get("gate") != "config-hook":
        if placed:
            return GateState(f"commit gate: runs first in {placed} (placed by the project)", 0, None, notes)
        _clean_owned(root, notes)
        if _registered_command(root):
            return GateState(f"commit gate: runs as a git config hook (git {git_version()}; every worktree: "
                             f"{len(worktrees(root))}; registered by the project's opt-in, left as it is: this worktree's "
                             "doc map does not carry gate: config-hook)", 0, None, notes)
        return GateState(f"commit gate: hooks managed by the project: see its doc map (core.hooksPath {managed} is a "
                         "folder the project tracks; its doc map's gate: config-hook registers the gate as a git config "
                         "hook)", 0, None, notes)
    if placed:
        return GateState(f"commit gate: runs first in {placed} (placed by the project)", 0, None, notes)
    if supported:
        already = _registered_command(root) == GATE_LAUNCHER and \
            git(root, "config", "--get", "hook.doc-guardrails.event") == "pre-commit"
        subprocess.run(["git", "-C", str(root), "config", "--local", "--replace-all", "hook.doc-guardrails.event",
                        "pre-commit"], check=True, capture_output=True)
        subprocess.run(["git", "-C", str(root), "config", "--local", "--replace-all", "hook.doc-guardrails.command",
                        GATE_LAUNCHER], check=True, capture_output=True)
        record = state_dir() / "gates" / gate_key(root)
        record.mkdir(parents=True, exist_ok=True)
        (record / "registered.json").write_text(json.dumps({
            "common_dir": str(common_dir(root)), "worktree": str(root), "time": time.time(),
            "launcher_sha1": hashlib.sha1(GATE_LAUNCHER.encode("utf-8")).hexdigest()}), encoding="utf-8")
        _clean_owned(root, notes)
        return GateState(f"commit gate: runs as a git config hook (git {git_version()}; every worktree: "
                         f"{len(worktrees(root))}{'; already registered' if already else ''})", 0, None, notes)
    hook, why = hook_owner(root)
    if hook is None:
        return GateState(f"commit gate: NOT installed: {why}; hand-over below", 3, None, notes + [handover(_hook_path(root))])
    hook.parent.mkdir(parents=True, exist_ok=True)
    text = _drop_block(_read_hook(hook)) if hook.is_file() else "#!/bin/sh\n"
    if not _shell_hook(text):
        return GateState(f"commit gate: NOT installed: {hook} is not a shell script; hand-over below", 3, None,
                         notes + [handover(hook)])
    new = _insert_first(text)
    if gate_reach(new):
        return GateState(f"commit gate: NOT installed: {hook} can end before the block (lines "
                         f"{', '.join(map(str, gate_reach(new)))}); hand-over below", 3, None, notes + [handover(hook)])
    _write_hook(hook, new)
    hook.chmod(hook.stat().st_mode | 0o111)
    return GateState(f"commit gate: runs first in {hook}", 0, hook, notes)


def heartbeats(root):
    """This repository's gate runs, oldest first."""
    key = gate_key(root)
    folder = state_dir() / "gates" / key if key else None
    out = []
    for p in sorted(folder.glob("*-*.json")) if folder and folder.is_dir() else []:
        try:
            out.append(json.loads(p.read_text(encoding="utf-8")))
        except (OSError, ValueError):
            continue
    return out


def staged_md(root):
    rows = []
    for line in git(root, "diff", "--cached", "--raw", "--no-abbrev", "--diff-filter=ACMR").splitlines():
        meta, _, path = line.partition("\t")
        parts = meta.split()
        path = path.split("\t")[-1]
        if len(parts) >= 4 and path.endswith(".md"):
            rows.append([path, parts[3]])
    return sorted(rows)


def write_gate_heartbeat(root, result):
    key = gate_key(root)
    if not key:
        return
    folder = state_dir() / "gates" / key
    folder.mkdir(parents=True, exist_ok=True)
    head = git(root, "rev-parse", "HEAD") or None
    data = {"time": time.time(), "worktree": str(root), "head_before": head, "staged_md": staged_md(root),
            "result": result, "git": git_version(), "helper": os.path.realpath(__file__)}
    (folder / f"{time.time_ns()}-{os.getpid()}.json").write_text(json.dumps(data), encoding="utf-8")
    runs = sorted(folder.glob("*-*.json"))
    for old in runs[:-HEARTBEATS_KEPT]:
        try:
            old.unlink()
        except OSError:
            pass


def _last_local_commit(root):
    """(sha, base, action) of this worktree's newest commit made here (a HEAD reflog entry whose action starts with
    commit); base is what its pre-commit hook ran against: the first parent, the replaced commit for an amend, None for
    the first commit."""
    lines = git(root, "reflog", "show", "--format=%H %gs", "HEAD", "-n", "200").splitlines()
    for i, line in enumerate(lines):
        sha, _, action = line.partition(" ")
        if not action.startswith("commit"):
            continue
        if action.startswith("commit (initial)"):
            return sha, None, action
        if action.startswith("commit (amend)"):
            base = lines[i + 1].split(" ", 1)[0] if i + 1 < len(lines) else None
            return sha, base, action
        return sha, git(root, "rev-parse", f"{sha}^") or None, action
    return None, None, None


def _committed_md(root, base, sha):
    args = ["diff-tree", "-r", "--no-abbrev", "--diff-filter=AM"] + ([base, sha] if base else ["--root", sha])
    rows = []
    for line in git(root, *args).splitlines():
        meta, _, path = line.partition("\t")
        parts = meta.split()
        if len(parts) >= 4 and path.endswith(".md"):
            rows.append([path, parts[3]])
    return sorted(rows)


def gate_state(root):
    """facts -> gate: which form the gate takes here, and whether it ran on the last commit made in this worktree."""
    root = Path(root)
    command = _registered_command(root)
    out = {"form": "none", "registered": bool(command), "command_current": command == GATE_LAUNCHER,
           "config_hooks_supported": probe_config_hooks(root), "git": git_version(), "last_run": None,
           "last_local_commit": None, "ran_on_last_commit": None, "note": ""}
    runs = heartbeats(root)
    out["last_run"] = runs[-1] if runs else None
    r = subprocess.run(["git", "-C", str(root), "config", "--show-scope", "--type=bool", "--get",
                        "hook.doc-guardrails.enabled"], capture_output=True, encoding="utf-8", errors="replace")
    scope, _, value = r.stdout.strip().partition("\t")
    if value == "false":
        out.update(form="declined", registered=False, note=f"declined by the repository ({scope} config)")
        return out
    placed = _placed_block(root)
    owned, _ = hook_owner(root)
    owned_text = _read_hook(owned) if owned and owned.is_file() else ""
    try:
        managed = hooks_managed_by_project(root)
        error = None
    except GitProbeError as e:
        managed, error = None, str(e)
    if command:
        out["form"] = "config"
    elif placed or (GATE_BEGIN in owned_text and not gate_reach(owned_text)):
        out["form"] = "hook-file"
    elif GATE_BEGIN in owned_text:
        out["form"] = "unreachable-block"
    elif error:
        out.update(form="undetermined", note=f"could not tell whether the project manages its hooks ({error})")
        return out
    elif managed:
        out.update(form="project-managed", note="hooks managed by the project: see its doc map (gate: config-hook "
                                                "registers the gate)")
        return out
    sha, base, action = _last_local_commit(root)
    if not sha:
        out["note"] = "no commit made in this worktree"
        return out
    out["last_local_commit"] = sha
    want = _committed_md(root, base, sha)
    ran = any(h.get("head_before") == base and h.get("result") != "refused" and h.get("staged_md") == want for h in runs)
    out["ran_on_last_commit"] = ran
    if not ran:
        out["note"] = f"did not run on {sha[:7]} (git <2.54, --no-verify, or no gate)"
    return out


def gate_registrations():
    """(lines, exit code): every repository whose registration the live state folder records: `registered  <worktree>
    (launcher current|stale)`, `unset  <worktree>` or `gone  <worktree>`; exit 1 while any is registered. A harness
    task repository is labelled, to be unset pack-side."""
    lines, code = [], 0
    roots = [os.path.realpath(str(r)) for r in HARNESS_ROOTS if os.path.exists(str(r))]
    for rec in sorted((state_dir() / "gates").glob("*/registered.json")):
        try:
            data = json.loads(rec.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        wt = data.get("worktree", "?")
        if not Path(data.get("common_dir", "")).exists():
            lines.append(f"gone  {wt}")
            continue
        r = subprocess.run(["git", f"--git-dir={data['common_dir']}", "config", "--get", "hook.doc-guardrails.command"],
                           capture_output=True, encoding="utf-8", errors="replace")
        value = r.stdout.rstrip("\n")
        if r.returncode == 0 and value:
            code = 1
            line = f"registered  {wt}  (launcher {'current' if value == GATE_LAUNCHER else 'stale'})"
            if roots and os.path.exists(wt) and path_within(os.path.realpath(wt), roots):
                line += "  harness task: unset pack-side"
            lines.append(line)
        else:
            lines.append(f"unset  {wt}")
    return lines, code


def hook_in_tree(folder, hook):
    """True when the hook file lies inside the tree of any of the repository's worktrees (core.hooksPath inside one,
    e.g. `.husky/`), not git's own folder: a file the pack does not own."""
    root = Path(repo_root(Path(folder).resolve()) or folder)
    try:
        trees = [os.path.realpath(w) for w in worktrees(root)]
    except GitProbeError:
        trees = [os.path.realpath(str(root))]
    p = os.path.realpath(str(hook))
    hit = path_within(p, trees) or path_within(os.path.dirname(p), trees)
    if hit is None:
        return False
    return ".git" not in Path(p).relative_to(hit[1]).parts[:1] if p != hit[1] else True


def cited_ids(val, form):
    """The ids a `roadmap:` value names. `R-<n>` tags count wherever they appear (the prefix makes them ids). Row numbers
    count only as a list at the start of the value or right after the word row or rows ("954, 968", "refines row 129",
    "rows 361–366"), so a date, a commit, a section number or a figure in the explanation is never taken for a row id."""
    if "row" not in form:
        return TAG_ID_RE.findall(val)
    one = r"\d+(?:\.\d+)?"
    sep = r"\s*(?:,|;|/|&|\band\b|\bto\b|–|—(?=\s*\d)|-)\s*#?"
    ids = []
    for chain in re.finditer(r"(?:^\s*#?|\brows?\s+#?)(" + one + r"(?:" + sep + one + r")*)", val, re.I):
        ids += re.findall(one, chain.group(1))
    return ids


LEAD_ID_RE = re.compile(r"#?(?:(?P<prefixed>[A-Z]+-" + ID_NUM + r")|(?P<bare>" + ID_NUM + r"))(?![\w-]|\.\d)")
LEAD_SEP_RE = re.compile(r"\s*(?:,|;|/|&|\band\b|\bto\b|…|\.\.|–|—)\s*")
LEAD_END_RE = re.compile(r"\s*(?:$|[,;/&()\[\]:.…–—]|and\b|to\b)")


def bare_after_prefixed(val):
    """The bare numbers in a `roadmap:` value's leading id list (the ids before any reason text) that come after a
    prefixed id: `A-53, 99999` gives ["99999"]. cited_ids() does not read them, and rightly (the rule keeps dates and
    figures out); this names them so the author can hear it. A number followed by a word (`3 files`) or joined by a
    hyphen (`2026-09-20`) ends the list: it is reason text, not an id."""
    out, after, pos = [], False, len(val) - len(val.lstrip())
    while True:
        m = LEAD_ID_RE.match(val, pos)
        if not m:
            break
        if m.group("prefixed"):
            after = True
        elif not LEAD_END_RE.match(val, m.end()):
            break
        elif after:
            out.append(m.group("bare"))
        s = LEAD_SEP_RE.match(val, m.end())
        if not s:
            break
        pos = s.end()
    return out


def row_list_note(ctx, text):
    """The NOTE a judged document earns when its `roadmap:` value puts a bare row id after a prefixed id, or None. Only
    where some lane reads bare numbers (a lane whose id form has no prefix): elsewhere a bare number is never an id."""
    val = roadmap_value(text)
    if val is None or not any(not l["prefix"] and (l["form"] or l["roadmap"]) for l in ctx["lanes"]):
        return None
    nums = bare_after_prefixed(val)
    if not nums:
        return None
    one = len(nums) == 1
    return (f"bare {', '.join(nums)} after a prefixed id {'is' if one else 'are'} not read as {'a row id' if one else 'row ids'}: "
            f"write {'it' if one else 'them'} first or as {', '.join(f'`row {n}`' for n in nums)} (LINKING.md § 2)")


def file_row_list_note(ctx, rel):
    """The per-file check's NOTE for one document it judges (row_list_note), or None: guardrails off, a path outside the
    project, a file that is not there or not judged."""
    if not rel or ctx["mode"] == "off" or not (Path(ctx["root"]) / rel).is_file():
        return None
    text = (Path(ctx["root"]) / rel).read_text(encoding="utf-8", errors="replace")
    return row_list_note(ctx, text) if judge(ctx, rel, text) else None


def as_written(cid, form):
    """An id echoed back in the project's own form, so it is the string the author can grep for."""
    return cid if "row" in form else f"R-{cid}"


def link_check_file(folder, rel, text=None):
    """The hook's question: is this one markdown file, just written, of a linked kind and unlinked or broken?
    Returns (level, message) or None when there is nothing to say — no roadmap, guardrails off, not a linked kind.
    Fast path: reads the doc map, the roadmap's ids and this one file; never walks the project (a hook runs on
    every write, and the full walk costs seconds on a large project)."""
    root = project_root(folder)
    ctx = link_context(root)
    if ctx["mode"] == "off":
        return None
    try:
        rel = Path(rel).resolve().relative_to(root.resolve()).as_posix()  # the kinds and exempt patterns use /
    except ValueError:
        return None
    return _check_one(ctx, rel, text)


def _check_one(ctx, rel, text=None):
    """One document through judge(); only MISSING and BROKEN are worth a word to the hook and the gate."""
    hit = judge(ctx, rel, text)
    return hit if hit and hit[0] in ("MISSING", "BROKEN") else None


def cite_roadmap(root, rm_rel, rm_text, say, counts, lane_branches=()):
    """Every live `path#anchor` and backticked or linked path of one roadmap: OK, NO FILE or NO ANCHOR. A NO FILE
    that exists on a local branch of a lane says so."""
    for m in CITATION_RE.finditer(rm_text):
        if is_struck(rm_text, m.start()):   # a struck citation is withdrawn history, not a claim
            continue
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
                on = next(((n, b) for n, b in lane_branches if run_git(root, "cat-file", "-e", f"{b}:{path}")[0] == 0),
                          None)
                say("NO FILE", f"{rm_rel}:{line}", path + (f" (exists on lane {on[0]}'s branch {on[1]}, not in this "
                                                           "checkout)" if on else ""))
            continue
        if anchor:
            needle = anchor.replace("§", "").strip()
            body = target.read_text(encoding="utf-8", errors="replace")
            if needle and needle not in body:
                say("NO ANCHOR", f"{rm_rel}:{line}", f"{path}#{anchor}")
                continue
        counts["OK"] += 1


def link_audit(folder, strict=False, inventory=True, today=None, lane=None):
    """Report-only (exit 0) unless --strict: which linked documents name a roadmap item, which roadmap citations
    resolve. Absence is REPORTED; a broken link is BROKEN (fails only under --strict). Never edits. `today` dates the
    inventory (default the clock)."""
    root = project_root(folder)
    ctx = link_context(root, deep=True)
    dm = ctx["map"]
    out, counts = [], {"LINKED": 0, "NONE": 0, "MISSING": 0, "BROKEN": 0, "OK": 0, "NO FILE": 0, "NO ANCHOR": 0}

    def say(level, where, msg):
        out.append((level, where, msg))
        counts[level] = counts.get(level, 0) + 1

    for p in ctx["problems"]:   # the map's problems first: each is a check the project believes it has
        if p["level"] == "NOTE":
            say("NOTE", p["where"], p["message"])
        else:
            out.append(("CONFIG", p["where"], f"{p['level']} {p['message']}"))
    config = sum(1 for p in ctx["problems"] if p["level"] in ("ERROR", "WARN"))
    if not dm and not ctx["problems"]:
        # "no Doc map" reads as "your CLAUDE.md is missing a section"; when there is no CLAUDE.md at all, say that.
        have = [f for f in ("CLAUDE.md", "knowledge/00-CURRENT.md") if (root / f).is_file()]
        say("NOTE", have[0] if have else "CLAUDE.md",
            "no `## Doc map` — using defaults (LINKING.md § 5)" if have else
            "no CLAUDE.md with a `## Doc map` — using defaults (LINKING.md § 5)")
    rm_rel = dm.get("roadmap") or next((r for r in LINK_DEFAULT_ROADMAPS if (root / r).is_file()), None)
    if not rm_rel or not (root / rm_rel).is_file():
        say("NOTE", rm_rel or "roadmap", "no roadmap found — nothing to link to (a baseline gap; `audit --baseline` names it)")
        counts["no_roadmap"] = 1
        if config:
            counts["CONFIG"] = config
        counts["ERROR"] = sum(1 for p in ctx["problems"] if p["level"] == "ERROR")
        return out, counts
    main = ctx["lanes"][0]
    rm_text, form, ids = main["text"], main["form"], main["ids"]
    say("NOTE", rm_rel, f"roadmap; id form: {form}; {len(ids)} ids")
    counts["ids"] = len(ids)
    if main["layout"] == "tags":
        items = PROSE_ITEM_RE.findall(rm_text)
        untagged = sum(1 for line in rm_text.splitlines() if PROSE_ITEM_RE.match(line) and not TAG_ID_RE.search(line))
        say("NOTE", rm_rel, f"prose roadmap: {len(items)} items, {untagged} without an `R-<n>` id")
    for other in ctx["lanes"][1:]:
        if other["roadmap"]:
            say("NOTE", other["roadmap"], f"lane {other['name']}: roadmap; id form: {other['form']}; {len(other['ids'])} ids")
    for rel in expand(root, ctx["kinds"]):
        if lane and document_lane(ctx, rel) != lane:   # --lane: that lane's documents; ids still resolve in every lane
            continue
        text = (root / rel).read_text(encoding="utf-8", errors="replace")
        hit = judge(ctx, rel, text)
        if hit:
            say(hit[0], rel, hit[1])
            note = row_list_note(ctx, text)
            if note:
                say("NOTE", rel, note)
    lane_branches = [(l["name"], b) for l in ctx["lanes"] if l["branch"] for b in _branches(root, l["branch"])] \
        if repo_root(root) else []
    for rm in [l for l in ctx["lanes"] if l["roadmap"] and (not lane or l["name"] == lane or l["name"] == "main")]:
        cite_roadmap(root, rm["roadmap"], rm["text"], say, counts, lane_branches)
    existing = existing_inventories(root)
    if inventory and counts["MISSING"]:
        say("NOTE", write_unlinked_inventory(root, out, today), f"inventory of the {counts['MISSING']} unlinked document(s) — the worklist for a thread to link from")
    elif inventory and existing:  # nothing unlinked any more: the backlog file goes, it does not sit there empty
        for p in existing:
            p.unlink()
        say("NOTE", existing[0].relative_to(root).as_posix(), "removed — nothing is unlinked; the inventory exists only while there is a backlog")
    if config:
        counts["CONFIG"] = config
    counts["ERROR"] = sum(1 for p in ctx["problems"] if p["level"] == "ERROR")
    return out, counts


# ---------------------------------------------------------------------------------------------------- receipts

RECEIPT_TITLE_RE = re.compile(r"^ELI5(?: v\d+)?\s*$")
RECEIPT_FIELD_RE = re.compile(r"^- (Did|Why|Result|Proof|Fail|Files|Redman|Greenman|Next|Need from you|Goal|"
                              r"In one sentence):[ \t]?(.*)$")
RECEIPT_GROUPS = [["Did", "Why", "Result", "Proof", "Fail", "Files"], ["Redman", "Greenman"], ["Next", "Need from you"],
                  ["Goal", "In one sentence"]]
# the reason rule (eli5 SKILL.md, 2026-09-22): Why names the defect, goal or mechanism, never "the user said so"
REASON_RULE_RE = re.compile(r"(?i)^(because\s+)?(you|the user)\s+(asked|said|requested|wanted|told)\b")


def receipt_facts(text):
    """Every ELI5 receipt block in the text: {"title", "fields" [[name, value]…] in order, "groups" (field names
    split on blank lines), "errors"}. A block is its bare title line and the field lines after it, up to the first
    line that is neither a field, a blank line nor a lone `-`. The errors are the v5 shape's (eli5 SKILL.md)."""
    lines, blocks, i = (text or "").splitlines(), [], 0
    while i < len(lines):
        if not RECEIPT_TITLE_RE.match(lines[i].strip()):
            i += 1
            continue
        title, fields, groups, errors, i = lines[i].strip(), [], [[]], [], i + 1
        while i < len(lines):
            line = lines[i].rstrip()
            m = RECEIPT_FIELD_RE.match(line)
            if m:
                fields.append([m.group(1), m.group(2).strip()])
                groups[-1].append(m.group(1))
            elif line.strip() in ("", "-"):
                if line.strip() == "-":
                    errors.append(f"a lone `-` used as a blank line (line {i + 1})")
                if groups[-1]:
                    groups.append([])
            else:
                break
            i += 1
        groups = [g for g in groups if g]
        values = dict(fields)
        if title != "ELI5 v5":
            errors.append(f"title is {title!r}, not 'ELI5 v5'")
        result = values.get("Result", "")
        if groups != [[f for f in g if f != "Fail" or "Fail" in values] for g in RECEIPT_GROUPS]:
            errors.append(f"order is {' | '.join(', '.join(g) for g in groups)}")   # Fail's presence: below
        if result not in ("worked", "failed", "unclear"):
            errors.append(f"Result is {result!r}, not worked, failed or unclear")
        if result == "worked" and "Fail" in values:
            errors.append("Fail is present while Result is worked")
        if result in ("failed", "unclear") and "Fail" not in values:
            errors.append(f"Fail is absent while Result is {result}")
        if not values.get("Proof"):
            errors.append("Proof is empty (write `none — not verified` when nothing was)")
        why = re.sub(r"\s*\([^()]*\)\s*$", "", values.get("Why", ""))
        if REASON_RULE_RE.search(why):
            errors.append(f"Why gives no reason: {values.get('Why')!r} (the reason rule)")
        for name in ("Goal", "In one sentence"):
            if not values.get(name):
                errors.append(f"{name} is missing")
        blocks.append({"title": title, "fields": fields, "groups": groups, "errors": errors})
    return blocks


def receipt_ids(block):
    """The decision ids on a receipt's Greenman line: `G-<n>`, and a lane's `G-<letters><n>` (R-23's lane-qualified
    form), so the live receipt check and the evals' look up the same ids."""
    return re.findall(r"\bG-[A-Z]{0,4}\d+\b", dict(block["fields"]).get("Greenman", ""))


_RECORD_PATH_RE = re.compile(r"→\s*(?:`([^`]+)`|(\S+))")
_RECORD_NAME_RE = re.compile(r"(G-[A-Z]{0,4}\d+)-[^/\\]+\.md")


def receipt_named_records(block):
    """{id: path} for the ids on a receipt's Greenman line whose `→ <path>` (eli5 SKILL.md: every form naming a `G-<n>`
    ends with its record's path) is an absolute path to a readable `<id>-<slug>.md` file: that file is the id's record,
    wherever it sits (a build worktree's uncommitted record is in no folder or branch of the Stop's checkout). A
    relative path is left to the folder lookup; a path to another id's file, or one that cannot be read, resolves
    nothing. The live receipt check, tools/receipt_ids.py and the evals' receipt check read it alike."""
    ids = set(receipt_ids(block))
    out = {}
    for m in _RECORD_PATH_RE.finditer(dict(block["fields"]).get("Greenman", "")):
        raw = (m.group(1) or m.group(2).rstrip(".,;:)")).strip()
        path = os.path.expanduser(raw)
        name = _RECORD_NAME_RE.fullmatch(os.path.basename(path))
        if not name or name.group(1) not in ids or not os.path.isabs(path):
            continue
        try:
            with open(path, "rb") as fh:
                fh.read(1)
        except OSError:
            continue
        out.setdefault(name.group(1), path)
    return out


# ---------------------------------------------------------------------------------------------------- session skills
# The transcript readers (R-28 § 5.1) live beside this file in session_skills.py, which the per-prompt freshness hook
# imports alone (its budget is under 0.1 s). They are re-exported here, so closeout.py stays the one home, and loaded
# only when one is called: closeout.py copied or run on its own (the commit gate's launcher) never needs the file.

SKILL_PREFIX = "Base directory for this skill: "
_SESSION_SKILLS = []


def _session_skills():
    if not _SESSION_SKILLS:
        import importlib.util
        path = Path(__file__).resolve().parent / "session_skills.py"
        spec = importlib.util.spec_from_file_location("closeout_session_skills", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        _SESSION_SKILLS.append(module)
    return _SESSION_SKILLS[0]


def held_skills(transcript_path, index=None):
    """session_skills.held_skills: each skill's last text the transcript gave the session, in file order, and the other
    files it Read in each skill's folder; with `index` (a folder), read from where the last reading stopped."""
    return _session_skills().held_skills(transcript_path, index)


def read_session(transcript_path, index=None):
    """session_skills.read_session: held_skills' reading and the number of compaction boundaries the transcript holds."""
    return _session_skills().read_session(transcript_path, index)


def change_bases(held):
    """session_skills.change_bases: a fingerprint of the text the session holds of each file, which the freshness hook
    keys what it has named by."""
    return _session_skills().change_bases(held)


def file_changes(held):
    """session_skills.file_changes: the files read inside held skills' folders that are gone or hold other text now."""
    return _session_skills().file_changes(held)


def skill_changes(held):
    """session_skills.skill_changes: the held skills whose file is gone or holds other text now."""
    return _session_skills().skill_changes(held)


def skill_body(text):
    """session_skills.skill_body: a SKILL.md's text as a load injects it."""
    return _session_skills().skill_body(text)


def skill_name(folder):
    """session_skills.skill_name: the last part of a skill's folder, whatever the separator."""
    return _session_skills().skill_name(folder)


def turn_facts(transcript_path):
    """session_skills.turn_facts: whether the turn in progress ran a tool, and its last text."""
    return _session_skills().turn_facts(transcript_path)


def cwd_root(cwd):
    """The one root rule (R-28 § 5.3): the real path of `cwd`, walked up to the first folder holding `.git` (a folder,
    or a worktree's file), else the real path itself. No subprocess, so a Stop hook pays nothing for it; the receipt
    check and tools/receipt_ids.py key on it alike."""
    real = os.path.realpath(str(cwd))
    cur = real
    while True:
        if os.path.exists(os.path.join(cur, ".git")):
            return cur
        parent = os.path.dirname(cur)
        if parent == cur:
            return real
        cur = parent


def decision_folders(folder):
    """The decisions folders decision_ids reads that exist, relative to the folder: the declared ones, then the
    defaults."""
    folder = Path(folder)
    seen = []
    for d in _all_declared(folder, "decisions") + list(DEFAULT_DECISION_DIRS):
        if d not in seen and (folder / d).exists():
            seen.append(d)
    return seen


# ---------------------------------------------------------------------------------------------------- cli

def main(argv=None):
    import argparse   # here, not at the top: the hooks that import this module on every prompt and stop never parse
    # A Windows console defaults to a code page that cannot print the tree's box characters or non-ASCII names.
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser(prog="closeout.py", description=(__doc__ or "").split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("facts", help="what is true now, as JSON: git, pin, docs, baseline, linking counts, redman ledgers, the pack side ledger, greenman records, untracked records"); a.add_argument("folder", nargs="?", default=".")
    a = sub.add_parser("memory", help="every memory entry about this project, with index problems and gone paths"); a.add_argument("folder", nargs="?", default=".")
    a.add_argument("--backup", metavar="DEST", help="copy each memory folder it lists into DEST/memory-backup-<kind> first")
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
    a.add_argument("--heartbeat", action="store_true", help="with --staged: record this run for facts -> gate")
    a.add_argument("--lane", help="judge only this lane's documents and records (ids still resolve in every lane)")
    a = sub.add_parser("figures", help="every span of the declared docs against its source (the doc map's `figures:`); "
                                         "--write rewrites the spans and tables; exit 1 on a stale, unknown or missing one")
    a.add_argument("folder", nargs="?", default=".")
    g = a.add_mutually_exclusive_group()
    g.add_argument("--check", action="store_true", help="report only (the default)")
    g.add_argument("--write", action="store_true", help="rewrite span contents and marked tables; no other byte")
    a.add_argument("--public-tree", help="compute the public/ documents' commands and mentions in this built tree")
    a = sub.add_parser("gate-registrations", help="every repository where the config hook is still registered; exit 1 while any is")
    a = sub.add_parser("doc-map", help="the doc map as the checker reads it: lanes, patterns, problems; exit 1 on an error"); a.add_argument("folder", nargs="?", default=".")
    a.add_argument("--lane", help="only this lane")
    a = sub.add_parser("install-gate", help="register the commit gate as a git config hook, else first in a hook file the pack owns (exit 2 outside git)"); a.add_argument("folder", nargs="?", default=".")
    args = ap.parse_args(argv)
    if args.cmd == "facts":
        print(json.dumps(facts(args.folder), indent=2))
    elif args.cmd == "memory" and args.backup:
        try:
            made = memory_backup(args.folder, args.backup)
        except FileExistsError as e:
            print(f"memory --backup: {e}", file=sys.stderr)
            return 1
        print(json.dumps({"backups": made}, indent=2, ensure_ascii=False))
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
            print(f"--- hunk {i} of {rel}\n" + "\n".join(l.decode("utf-8", "replace") for l in h))
        print(f"{len(hunks)} unstaged hunk(s) in {rel}")
    elif args.cmd == "stage-hunks":
        print(f"staged hunk(s) {sorted(set(args.n))} of {stage_hunks(args.file, args.n)}")
    elif args.cmd == "install-gate":
        try:
            state = install_gate(args.folder)
        except ValueError as e:
            print(f"commit gate: no git ({e})", file=sys.stderr)
            return 2
        print(state.line)
        print(_last_ran(repo_root(Path(args.folder).resolve())))
        for note in state.notes:
            print(note if note.startswith("hand-over:") else f"note: {note}")
        return state.code
    elif args.cmd == "figures":
        root = project_root(args.folder)
        decl, rel, problem = figures_declaration(root)
        if decl is None and not problem:
            return 0                                   # no `figures:` key: nothing to say
        found = figures_write(root, args.public_tree) if args.write else figures_findings(root, None, args.public_tree)
        lines = figures_lines(found)
        print("\n".join(lines))
        return 1 if any(f[0] in ("STALE", "UNKNOWN", "MISSING") for f in found) else 0
    elif args.cmd == "gate-registrations":
        lines, code = gate_registrations()
        print("\n".join(lines) if lines else "no registration recorded")
        return code
    elif args.cmd == "doc-map":
        text, code = doc_map_report(args.folder, args.lane)
        print(text)
        return code
    elif args.cmd == "link-audit" and getattr(args, "staged", False):
        return staged_gate(args.folder, args.heartbeat)
    elif args.cmd == "link-audit" and args.file:
        root = project_root(args.folder)
        ctx = link_context(root)
        try:
            rel = Path(args.file).resolve().relative_to(root.resolve()).as_posix()
        except ValueError:
            rel = None
        message = file_config_message(ctx, rel) if rel and ctx["mode"] != "off" else None
        if message:
            print(message)
            return 2
        hit = link_check_file(args.folder, args.file)
        if hit:
            print(f"doc-guardrails: {hit[0]} — {args.file}: {hit[1]}. Add `roadmap: <ids>` or `roadmap: none — <why>` in its first 40 lines (LINKING.md § 2).")
        note = file_row_list_note(ctx, rel)
        if note:
            print(f"doc-guardrails: NOTE — {args.file}: {note}.")
        return 2 if hit or note else 0
    elif args.cmd == "link-audit":
        found, counts = link_audit(args.folder, args.strict, not args.no_inventory, lane=args.lane)
        for level, where, msg in found:
            print(f"{level:9} {where}  {msg}")
        broken = counts["BROKEN"] + counts["NO FILE"] + counts["NO ANCHOR"]
        print("link-audit: " + " · ".join(f"{k} {v}" for k, v in counts.items() if v and k not in ("ids", "no_roadmap", "ERROR")) + (" · report only" if not args.strict else ""))
        return 1 if (args.strict and (broken or counts.get("ERROR"))) else 0
    else:
        found = audit(args.folder, args.changed, args.pin, args.worklist, args.baseline)
        for level, where, msg in found:
            print(f"{level}  {where}  {msg}")
        fails = sum(1 for f in found if f[0] == "FAIL")
        pre = sum(1 for f in found if f[0] == "PRE-EXISTING")
        print(f"audit: {fails} fail, {pre} pre-existing broken, {len(found) - fails - pre} warn")
        return 1 if fails else 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
