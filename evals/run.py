#!/usr/bin/env python3
"""Behaviour tests for the skills pack: real headless Claude Code sessions in a contained project, graded in code.

  python3 evals/run.py --quick              the subset to run after any skill or doctrine change (its size: --counts)
  python3 evals/run.py                      every scenario on every seat it names
  python3 evals/run.py --only failed_fork   one scenario (on its seats; --seat narrows further)
  python3 evals/run.py --dry-run            print what would run and the totals line, run nothing; with a selection,
                                            also the last run of it from evals/RUNS.jsonl
  python3 evals/run.py --backfill <root> --regrade <dir> [--page <evidence page>]
                                            write evals/RUNS.jsonl from every graded run under <root>: the run log every
                                            graded run appends one record to
  python3 evals/run.py --recheck <run dir>  grade a finished run again without new sessions
  python3 evals/run.py --build-sealed       build the sealed config folder (--config, default ~/.claude-evals) and stop

Every session runs in a sealed config folder, never your own ~/.claude: CLAUDE_CONFIG_DIR is set to --config
(default ~/.claude-evals), which every batch rebuilds before it starts from evals/sealed.json — links to this copy's
skill folders, a `doctrine` link to ~/.claude/doctrine (where `install.py --with-doctrine` puts it), the hooks the
installer registers plus the per-write hook, each logging its runs, and claude.ai account sync and connectors off.
The folder keeps its own login: once, `CLAUDE_CONFIG_DIR=~/.claude-evals claude auth login`. Before any session starts
the preflight refuses (exit 2, no session launched, nothing spent) when the folder is not logged in, a hook does not
resolve, account entries or MCP servers reach it, or `claude --version` is not sealed.json's `cli_version`, the version
its bundled skills and plugins were reviewed on; `--probe` launches on another version and grades the case's `sealed`
check unreviewed, never a pass.
Each session gets its own copy of evals/fixture (plus an overlay if the scenario names one) under
~/xbt-evals/<stamp>/<scenario>-<seat>/project, with git and a bare `origin`, a pinned model and effort, acceptEdits, a
narrow tool allow-list, one deny list (`git push`, and Write and Edit inside the skill folders) and the skill folders
added as readable directories. Every turn's stream is kept, and the project's files are snapshotted after each turn so
checks can ask about the state mid-conversation. Exit 1 when any check fails. Stdlib only, Python 3.9 or newer.
"""
import argparse
import collections
import concurrent.futures
import datetime
import fnmatch
import hashlib
import importlib.util
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
PACK = HERE.parent
SEATS = {"fable": ["--model", "claude-fable-5-1", "--effort", "low"],
         "opus55": ["--model", "claude-opus-5-5", "--effort", "high"],   # the reviewer seat since 2026-09-22; the scenarios name it
         "opus": ["--model", "claude-opus-5", "--effort", "high"]}       # the reviewer seat until 2026-09-22; --seat opus runs the Opus scenarios on it
SEAT_ALIAS = {"opus": "opus55"}   # a seat that stands in for another: the scenarios name "opus55"; --seat opus runs those on Opus 5
GIT_ALLOWED = ("status", "diff", "log", "add", "commit")
# Each git command allowed bare is allowed in its `git -C <dir>` form too: the doctrine pages tell a session to run git
# that way, never `cd … &&` in front of it, and the full run of 2026-10-07 had a session refused `git -C <project> status`.
# A `*` in a rule matches spaces, so `git -C * commit *` also matches, say, `git -C <dir> push origin commit`; a case
# project's one remote is the bare `origin.git` inside its own case folder, so such a command still reaches nothing else.
ALLOWED = (["Read", "Write", "Edit", "Glob", "Grep", "Skill", "Bash(python3:*)"]
           + [f"Bash(git {c}:*)" for c in GIT_ALLOWED]
           + [r for c in GIT_ALLOWED for r in (f"Bash(git -C * {c})", f"Bash(git -C * {c} *)")]
           + ["Bash(ls:*)", "Bash(cat:*)", "Bash(sed:*)"])
# `sed` (the quick set of 2026-10-09 had a session refused `sed -i` for a mechanical rename): Claude Code's own guidance
# suggests it for small edits, and a session here may already change any file in its case with Write and Edit.


def skill_dirs(config_dir):
    """The sealed folder's skills/ and the real folders its links point into (this pack), so a session can read a
    skill's reference files (greenman's REFERENCE.md) as a real session can (large_stop on opus, 2026-09-14). Only the
    sealed folder is read: never the live config."""
    root, dirs = Path(config_dir) / "skills", set()
    if root.is_dir():
        dirs.update({root, root.resolve()})
        dirs.update(child.resolve().parent for child in root.iterdir() if child.is_symlink())
    return sorted(str(d) for d in dirs)


def session_dirs(proj, config_dir):
    """Claude Code's own folder for a session in `proj` under the sealed folder's projects/, created so it can be added
    read-only: a session reads its own saved tool output there (large_stop on opus, 2026-09-15)."""
    d = Path(config_dir) / "projects" / re.sub(r"[^A-Za-z0-9]", "-", str(Path(proj).resolve()))
    d.mkdir(parents=True, exist_ok=True)
    return [str(d)]


def permission_flags(tools, dirs):
    """The allow-list, one deny list (git push, and Write and Edit inside the skill folders), and the skill folders
    as readable directories. One --disallowedTools flag, so no deny can be dropped by a second."""
    deny = ["Bash(git push:*)", *(f"{tool}(/{d}/**)" for d in dirs for tool in ("Write", "Edit"))]
    return ["--allowedTools", *tools, "--disallowedTools", *deny, *(["--add-dir", *dirs] if dirs else [])]


def _public_rel(rel):
    """Where a pack source (its path in this repo) sits in the public tree: tools/export.py's copy_path(), mirrored
    here because the public tree ships no tools/ (a private test pins the two): `public/<x>` at the root, evals/ and
    tests/ in place, every skill folder's file under skills/."""
    first, _, rest = rel.partition("/")
    if first == "public" and rest:
        return rest
    if first in ("evals", "tests"):
        return rel
    return f"skills/{rel}"


def pack_path(rel):
    """The pack file `rel` (its path in this repo) in whichever layout this copy of run.py sits: this repo's, else
    the public tree's; neither is an error naming both paths, never a silent skip."""
    here, there = PACK / rel, PACK / _public_rel(rel)
    if here.exists():
        return here
    if there.exists():
        return there
    raise FileNotFoundError(f"{rel} is in neither layout: not {here} (this repo's) nor {there} (the public tree's)")


SESSION_TIMEOUT = 1200
IGNORED_PARTS = {".git", "__pycache__"}


def _live_checkout(pack):
    """The live checkout of the pack's repository: the one this machine's doctrine chain runs gate.py from (the
    checkout holding the real <home>/.claude/doctrine/gate.py at public/doctrine/gate.py), so a clone, which shares no
    git directory with it, still finds it; with no such chain (a fresh machine), the main worktree, the parent of the
    running tree's common git dir."""
    chain = Path(os.path.realpath(str(Path.home() / ".claude" / "doctrine" / "gate.py")))
    if chain.parts[-3:] == ("public", "doctrine", "gate.py") and chain.is_file() and (chain.parents[2] / ".git").exists():
        return chain.parents[2]
    r = subprocess.run(["git", "-C", str(pack), "rev-parse", "--path-format=absolute", "--git-common-dir"],
                       capture_output=True, encoding="utf-8", errors="replace")
    return Path(r.stdout.strip()).parent if r.returncode == 0 and r.stdout.strip() else Path(pack)


# ---- preflight (§5.8): before any launch; each refusal exits 2 naming what it read, and no session starts

def account_entries(init):
    """(plugins, skills, connectors) an init lists that came from the claude.ai account."""
    init = init or {}
    return ([p.get("name") for p in init.get("plugins") or [] if str(p.get("source", "")).endswith("@synced")],
            [x for x in init.get("skills") or [] if str(x).startswith("anthropic-skills:")],
            [x.get("name") for x in init.get("mcp_servers") or [] if str(x.get("name", "")).startswith("claude.ai ")])


def _manifest_settings_at(repo, rev):
    r = subprocess.run(["git", "-C", str(repo), "show", f"{rev}:evals/sealed.json"], capture_output=True,
                       encoding="utf-8", errors="replace")
    try:
        return json.loads(r.stdout).get("settings") if r.returncode == 0 else None
    except ValueError:
        return None


def _settings_change_time(repo):
    """Leg (a): the committer time of the newest commit of evals/sealed.json whose `settings` differ from its
    parent's (the first commit that has them counts); 0 when none is committed."""
    r = subprocess.run(["git", "-C", str(repo), "log", "--format=%H %ct", "--", "evals/sealed.json"],
                       capture_output=True, encoding="utf-8", errors="replace")
    for line in r.stdout.splitlines():
        sha, ct = line.split()
        if _manifest_settings_at(repo, sha) != _manifest_settings_at(repo, sha + "^"):
            return int(ct)
    return 0




def window_start(repo, runs_root, rerun_reason=None, now=None):
    """T of the sync check: only sealed inits recorded after T are read, so an init written before a fix
    never locks the launcher."""
    t = _settings_change_time(repo)
    return t


def sealed_inits_after(runs_root, t):
    """[(case folder, init)] of every sealed case under runs_root whose turn 1 started after t."""
    out = []
    for rec_path in sorted(set(Path(runs_root).glob("*/*/record.json")) | set(Path(runs_root).glob("*/*/*/record.json"))):
        try:
            rec = json.loads(rec_path.read_text(encoding="utf-8"))
            first = rec["turns"][0]
            start = _when(first["t_start"]).timestamp()
        except (ValueError, KeyError, IndexError, TypeError):
            continue
        if "env" in rec and "hooks" in rec and start > t and first.get("init"):
            out.append((rec_path.parent, first["init"]))
    return out


def _reviewed_mcp_outputs():
    return {p.read_text(encoding="utf-8").strip() for p in sorted((HERE / "known" / "shapes").glob("mcp_list_*.txt"))}


def preflight(cli_path, config_dir, probe=False, runs_root=None, repo=None, rerun_reason=None, now=None):
    """(0, "") when a batch may launch on the sealed folder, else (2, what refused it). Local checks first (the built
    settings, account sync on disk, every hook logged and resolving), then the CLI's own answers, every call through
    the batch's resolved binary with the updater off: the reviewed CLI, the login, `mcp list` from an empty scratch
    folder; last, the sealed inits recorded since the window's start."""
    config, m = Path(config_dir), manifest()
    runs_root = Path(runs_root) if runs_root else Path.home() / "xbt-evals"
    path = config / "settings.json"
    if not path.is_file():
        return 2, f"the sealed config is not built ({path} missing): run `run.py --build-sealed` first"
    settings = json.loads(path.read_text(encoding="utf-8"))
    problems = sealed_settings_problems(settings)
    synced = [p.relative_to(config).as_posix() for d in ("plugins/synced", "skills/synced") if (config / d).is_dir()
              for p in sorted((config / d).rglob("*")) if p.is_file()]
    if problems or synced:
        return 2, f"account sync reached the sealed folder: {'; '.join(problems + synced)} — greenman before another launch"
    for event, groups in (settings.get("hooks") or {}).items():
        for g in groups:
            for h in g.get("hooks") or []:
                script = settings_script(h)
                if not is_logged(h):
                    return 2, f"hook log: {event} {script} writes no log — no session launched"
                if not os.path.isfile(os.path.realpath(script)):
                    return 2, f"hook entry {event} {script} does not resolve — no session launched"
    env = dict(os.environ, CLAUDE_CONFIG_DIR=str(config), DISABLE_AUTOUPDATER="1")
    call = lambda *args, **kw: subprocess.run([str(cli_path), *args], capture_output=True, encoding="utf-8",
                                              errors="replace", env=env, timeout=120, **kw)
    version = (call("--version").stdout or "").split()[:1]
    version = version[0] if version else "unknown"
    if m.get("cli_version") and version != m["cli_version"] and not probe:
        return 2, (f"CLI {version} is not the reviewed {m['cli_version']} (evals/sealed.json's cli_version): run the "
                   f"reviewed version, or review this one with a --probe run first — no session launched")
    r = call("auth", "status")
    try:
        auth = json.loads(r.stdout)
        logged_in = auth["loggedIn"] if isinstance(auth, dict) and isinstance(auth.get("loggedIn"), bool) else None
    except ValueError:
        logged_in = None
    if logged_in is None:
        return 2, f"auth status unreadable: {(r.stdout.strip().splitlines() or [''])[0]}"
    if not logged_in:
        return 2, (f"the sealed eval config needs its one-time login: `CLAUDE_CONFIG_DIR={config} claude auth login` "
                   f"(no session starts, so no sealed hook runs)")
    with tempfile.TemporaryDirectory() as scratch:   # a .mcp.json or a local-scope server of the caller's folder never adds a line
        r = call("mcp", "list", cwd=scratch)
    out = (r.stdout or "").strip()
    connectors = [line.split(":")[0] for line in out.splitlines() if line.startswith("claude.ai ")]
    if connectors:
        return 2, (f"claude.ai connectors reached the sealed folder: {', '.join(connectors)} — disableClaudeAiConnectors "
                   f"did not take effect; greenman before another launch")
    if r.returncode != 0 or out not in _reviewed_mcp_outputs():
        return 2, f"mcp list (exit {r.returncode}) printed what no reviewed fixture holds: {out!r}"
    t = window_start(repo or PACK, runs_root, rerun_reason, now)
    for case, init in sealed_inits_after(runs_root, t):
        plugins, skills, connectors = account_entries(init)
        if plugins or skills or connectors:
            return 2, (f"account sync reached the sealed folder: {case.name} lists {len(plugins)} plugin(s), "
                       f"{len(skills)} skill(s), {len(connectors)} connector(s) {', '.join(plugins + skills + connectors)[:200]}"
                       f" — greenman before another launch")
    return 0, ""


# ------------------------------------------------------------------------------------------------ project

def build_project(case_dir, scenario):
    """The case's project: the fixture, its overlays, git with a bare origin, then its preps — run with the case's own
    DOC_GUARDRAILS_STATE and the pack under test's closeout.py, the values the session and the grader use, so no prep
    leaves a record in the live state."""
    proj = case_dir / "project"
    (case_dir / "state").mkdir(parents=True, exist_ok=True)
    stray = shutil.ignore_patterns("__pycache__")   # bytecode a test run left beside the sources is no part of a case
    shutil.copytree(HERE / "fixture", proj, ignore=stray)
    overlays = scenario.get("overlay") or []
    for name in [overlays] if isinstance(overlays, str) else overlays:
        shutil.copytree(HERE / "overlays" / name, proj, dirs_exist_ok=True, ignore=stray)
    git = lambda *a: subprocess.run(["git", "-C", str(proj), *a], check=True, capture_output=True)
    git("init", "-q", "-b", "main")
    git("add", "-A")
    git("-c", "user.name=evals", "-c", "user.email=evals@local", "commit", "-q", "-m", "fixture")
    subprocess.run(["git", "init", "-q", "--bare", str(case_dir / "origin.git")], check=True, capture_output=True)
    git("remote", "add", "origin", "../origin.git")
    preps = scenario.get("prep") or []
    for prep in [preps] if isinstance(preps, str) else preps:
        PREPS[prep](proj, grader_env(case_dir / "state"))
    return proj


def _prep_readme_change(proj, env):
    with open(proj / "README.md", "a", encoding="utf-8") as fh:
        fh.write("\nUsage: see tests/test_report.py.\n")


def _prep_untracked_ledger(proj, env):
    """A redman ledger a session wrote and never committed: the close-out must commit it, not leave it."""
    led = proj / "knowledge" / "redman" / "LEDGER.md"
    led.parent.mkdir(parents=True, exist_ok=True)
    led.write_text("# Redman ledger — shelfkit\nroadmap: none — the project's redman ledger; each row names its own roadmap item\n\n"
                   "| date | kind | item | size | state | where | observed | why it matters | first step | roadmap |\n"
                   "|---|---|---|---|---|---|---|---|---|---|\n"
                   "| 2026-09-14 | stale doc | README gives no usage example | small | filed | `README.md#shelfkit` | "
                   "read README.md → one line, no example | a new user has to read the tests to use the library | "
                   "add a three-line example | R-3 |\n", encoding="utf-8")


def _prep_gate_and_staged_spec(proj, env):
    """The commit gate installed, and an unlinked spec staged by someone else: the commit must go through the gate."""
    subprocess.run([sys.executable, str(pack_path("closeout/closeout.py")), "install-gate", str(proj)],
                   check=True, capture_output=True, env=env)
    spec = proj / "knowledge" / "02-spec-cli.md"
    spec.write_text("# CLI spec\n\nThe shelf command prints the low-stock report, one sku per line.\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(proj), "add", "knowledge/02-spec-cli.md"], check=True, capture_output=True)


def _prep_gate(proj, env):
    """The commit gate installed, nothing staged."""
    subprocess.run([sys.executable, str(pack_path("closeout/closeout.py")), "install-gate", str(proj)],
                   check=True, capture_output=True, env=env)


def _prep_foreign_hooks_path(proj, env):
    """The project keeps its own hooks in a folder it tracks and points git at it (W-22)."""
    subprocess.run(["git", "-C", str(proj), "config", "core.hooksPath", "scripts/git-hooks"], check=True, capture_output=True)


PREPS = {"readme_change": _prep_readme_change, "untracked_ledger": _prep_untracked_ledger,
         "gate_and_staged_spec": _prep_gate_and_staged_spec, "gate": _prep_gate,
         "foreign_hooks_path": _prep_foreign_hooks_path}


def snapshot(proj):
    """{relative path: sha1} for every file outside .git and __pycache__."""
    out = {}
    for p in sorted(proj.rglob("*")):
        rel = p.relative_to(proj)
        if p.is_file() and not IGNORED_PARTS.intersection(rel.parts):
            out[rel.as_posix()] = hashlib.sha1(p.read_bytes()).hexdigest()
    return out


# ------------------------------------------------------------------------------------------------ sessions

def _content_text(content):
    """The text of a message or tool_result content: a string, or the text parts of a list."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(c.get("text", "") for c in content if isinstance(c, dict) and c.get("type") == "text")
    return ""


INIT_FIELDS = ("skills", "plugins", "mcp_servers", "model", "claude_code_version")
STOP_FEEDBACK = "Stop hook feedback:"


def parse_stream(text):
    """What a turn did, from its stream-json: skills, bash commands with their results, written files, final text,
    cost, session id, the hook events (`--include-hook-events`) and the init record's environment."""
    turn = {"skills": [], "bash": [], "writes": [], "final": "", "cost": None, "session_id": None, "api_error": None,
            "hooks": [], "init": None, "messages": []}
    bash_by_id, messages = {}, {}
    for line in text.splitlines():
        try:
            d = json.loads(line)
        except ValueError:
            continue
        if not isinstance(d, dict):
            continue
        turn["session_id"] = turn["session_id"] or d.get("session_id")
        if d.get("type") == "system" and d.get("subtype") in ("hook_started", "hook_response"):
            turn["hooks"].append({"subtype": d["subtype"], "event": d.get("hook_event"), "name": d.get("hook_name"),
                                  "output": d.get("output"), "stdout": d.get("stdout"),
                                  "exit_code": d.get("exit_code"), "outcome": d.get("outcome")})
        elif d.get("type") == "system" and d.get("subtype") == "init":
            turn["init"] = {k: d.get(k) for k in INIT_FIELDS}
        elif d.get("type") == "assistant":
            msg = d.get("message") or {}
            if msg.get("id"):   # the stream repeats a message's usage snapshot: the last record of each id counts
                messages[msg["id"]] = {"id": msg["id"], "model": msg.get("model"), "usage": msg.get("usage")}
            for c in msg.get("content", []) or []:
                if c.get("type") != "tool_use":
                    continue
                inp = c.get("input", {})
                if c.get("name") == "Skill":
                    turn["skills"].append(inp.get("skill", ""))
                elif c.get("name") == "Bash":
                    entry = {"cmd": inp.get("command", ""), "out": None, "id": c.get("id")}
                    turn["bash"].append(entry)
                    bash_by_id[c.get("id")] = entry
                elif c.get("name") in ("Write", "Edit"):
                    turn["writes"].append(inp.get("file_path", ""))
        elif d.get("type") == "user":
            for c in (d.get("message") or {}).get("content", []) or []:
                if isinstance(c, dict) and c.get("type") == "tool_result" and c.get("tool_use_id") in bash_by_id:
                    bash_by_id[c["tool_use_id"]]["out"] = _content_text(c.get("content"))
                elif isinstance(c, dict) and c.get("type") == "text" and str(c.get("text", "")).startswith(STOP_FEEDBACK):
                    # an older CLI (2.1.273) wrote a blocking Stop hook's output only as this user record, the CLI's
                    # own, with no hook event: it is the hook's output, with no command to attribute it by
                    out = c["text"][len(STOP_FEEDBACK):].lstrip("\n")
                    turn["hooks"].append({"subtype": "hook_response", "event": "Stop", "name": "Stop", "output": out,
                                          "stdout": out, "exit_code": None, "outcome": "blocked", "command": None,
                                          "form": "stop feedback"})
        elif d.get("type") == "result":
            turn["final"], turn["cost"] = d.get("result", "") or "", d.get("total_cost_usd")
            if d.get("terminal_reason") == "api_error" or d.get("api_error_status"):
                # a usage limit or an outage ended the session: nothing about the skills was measured
                turn["api_error"] = f"{d.get('api_error_status') or 'api error'}: {(d.get('result') or '')[:120]}"
    turn["messages"] = list(messages.values())
    return turn


# From 2.1.277 on a resumed headless turn reports the session's running total (the changelog: "headless sessions now
# save their totals at exit"); before it, each turn reports its own cost.
RUNNING_TOTALS_FROM = (2, 1, 277)


def _version(text):
    try:
        return tuple(int(x) for x in str(text).split("."))
    except ValueError:
        return None


def case_cost(turns):
    """(cost, per_turn, error, kind) from each turn's reported total and CLI version. From RUNNING_TOTALS_FROM on the
    case's cost is the last turn's total and a turn's own cost the difference from the one before; before it, the turns
    are summed. It never guesses: a turn with no result record (`no_result`), a total below the one before
    (`falling`) or two CLI versions in one case (`mixed`) give cost None and the error, with its kind."""
    versions = sorted({(t.get("init") or {}).get("claude_code_version") for t in turns} - {None}, key=_version)
    if len(versions) > 1:
        return None, None, f"mixed CLI versions: {', '.join(versions)}", "mixed"
    totals = [t.get("cost") for t in turns]
    missing = next((n for n, c in enumerate(totals, 1) if c is None), None)
    if missing:
        return None, None, f"turn {missing} reported no cost", "no_result"
    version = _version(versions[0]) if versions else None
    if version and version >= RUNNING_TOTALS_FROM:
        for n in range(1, len(totals)):
            if totals[n] < totals[n - 1]:
                return None, None, (f"turn {n + 1} total ${totals[n]:.2f} below turn {n} ${totals[n - 1]:.2f} on "
                                    f"{versions[0]}: the CLI's accounting changed again"), "falling"
        return (totals[-1] if totals else 0.0), [b - a for a, b in zip([0.0] + totals, totals)], None, None
    return sum(totals), list(totals), None, None


def cost_turns(case_dir, rec):
    """The record's turns with their init records; a record written before init was kept reads its streams again."""
    out = []
    for n, t in enumerate(rec["turns"], 1):
        if "init" not in t and (Path(case_dir) / f"turn{n}.jsonl").is_file():
            t = dict(t, init=parse_stream((Path(case_dir) / f"turn{n}.jsonl").read_text(encoding="utf-8", errors="replace"))["init"])
        out.append(t)
    return out


def bash_cmd(entry):
    """A turn's Bash entry as its command: a {cmd, out, id} pair, or the plain string of a record written before."""
    return entry["cmd"] if isinstance(entry, dict) else entry


def transcript_files(roots):
    """The session transcripts (`<sid>.jsonl`) under the given folders, each once: resolved and deduplicated by real
    path, so a folder reached twice (a config folder that is a link to another) is read once (red2 NEW-3)."""
    seen = {}
    for root in roots:
        root = Path(root)
        if root.is_dir():
            for p in sorted(root.glob("*.jsonl")):
                seen.setdefault(os.path.realpath(p), p)
    return [seen[k] for k in sorted(seen)]


PERSISTED_RE = re.compile(r"^<persisted-output>\nOutput too large[^\n]*saved to: (\S+)")


def _hook_stdout(text, root):
    """A hook's stdout as the transcript keeps it; a long one is persisted to `tool-results/`, which run_case copies
    beside the transcript, so it is read from the copy (the preview alone would cut the text)."""
    m = PERSISTED_RE.match(text or "")
    if not m:
        return text
    hits = sorted(Path(root).glob(f"*/tool-results/{Path(m.group(1)).name}"))
    return hits[0].read_text(encoding="utf-8", errors="replace") if hits else text


TYPED_RE = re.compile(r"<command-name>/([\w:.-]+)</command-name>")
BASE_DIR_RE = re.compile(r"^Base directory for this skill: (\S+)")


def parse_transcript(case_dir):
    """The case transcript (`<case>/transcripts/`, copied there by run_case) as events in file order: `prompt` (a
    turn's prompt record), `typed` (a typed /command whose skill loaded), `hook` (an attachment of a hook run, with its
    recorded `command`), `stop_summary` (every Stop entry's command, silent runs included), `tool_use` and `text` (the
    assistant's). One root, each file once."""
    root = Path(case_dir) / "transcripts"
    events, pending = [], {}
    for f in transcript_files([root]):
        for line in f.read_text(encoding="utf-8", errors="replace").splitlines():
            try:
                d = json.loads(line)
            except ValueError:
                continue
            if not isinstance(d, dict):
                continue
            a = d.get("attachment") if d.get("type") == "attachment" else None
            if isinstance(a, dict) and str(a.get("type", "")).startswith("hook_") and a.get("hookEvent"):
                if a["type"] == "hook_blocking_error":
                    err = a.get("blockingError") or {}
                    command, text, stdout = err.get("command"), err.get("blockingError", ""), None
                else:
                    stdout = _hook_stdout(a.get("stdout") if a.get("stdout") is not None
                                          else _content_text(a.get("content")) if not isinstance(a.get("content"), list)
                                          else "\n".join(a["content"]), root)
                    command, text = a.get("command"), stdout
                events.append({"kind": "hook", "type": a["type"], "event": a["hookEvent"], "name": a.get("hookName"),
                               "command": command, "stdout": stdout, "text": text})
            elif d.get("type") == "system" and d.get("subtype") == "stop_hook_summary":
                events.append({"kind": "stop_summary",
                               "commands": [h.get("command") for h in d.get("hookInfos") or [] if h.get("command")]})
            elif d.get("type") == "user":
                content = (d.get("message") or {}).get("content")
                text = _content_text(content)
                if d.get("isMeta"):
                    m, name = BASE_DIR_RE.match(text), pending.pop(d.get("promptId"), None)
                    if m and name and m.group(1).rstrip("/").endswith("/skills/" + name):
                        events.append({"kind": "typed", "name": name})
                elif isinstance(content, str) or (isinstance(content, list) and not any(
                        isinstance(c, dict) and c.get("type") == "tool_result" for c in content)):
                    events.append({"kind": "prompt", "text": text, "prompt_id": d.get("promptId")})
                    m = TYPED_RE.search(text)
                    if m:
                        pending[d.get("promptId")] = m.group(1)
            elif d.get("type") == "assistant":
                for c in (d.get("message") or {}).get("content", []) or []:
                    if c.get("type") == "tool_use":
                        events.append({"kind": "tool_use", "name": c.get("name"), "input": c.get("input") or {}})
                    elif c.get("type") == "text" and c.get("text"):
                        events.append({"kind": "text", "text": c["text"]})
    return events


def utc_now():
    return datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="microseconds")


def _when(stamp):
    """A UTC ISO time as written by run_case and the hook log (a trailing Z read as +00:00)."""
    return datetime.datetime.fromisoformat(stamp[:-1] + "+00:00" if stamp.endswith("Z") else stamp)


def turn_at(rec, stamp):
    """The turn (1-based) whose t_start..t_end holds the time, or None: run_case runs the turns one after another
    and each hook runs inside its turn's process, so the times place every hook-log line exactly."""
    t = _when(stamp)
    for n, turn in enumerate(rec.get("turns") or [], 1):
        if turn.get("t_start") and turn.get("t_end") and _when(turn["t_start"]) <= t <= _when(turn["t_end"]):
            return n
    return None


def read_hook_log(case_dir, rec):
    """(records, problems) from <case>/hooks.jsonl, each record with the `turn` it ran in; (None, []) when the case
    has no log. A line that does not parse is a problem named with its number, never skipped."""
    path = Path(case_dir) / "hooks.jsonl"
    if not path.is_file():
        return None, []
    records, problems = [], []
    for i, line in enumerate(path.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
        if not line.strip():
            continue
        try:
            r = json.loads(line)
            r = dict(r, line=i, turn=turn_at(rec, r["t"]))
        except (ValueError, KeyError, TypeError):
            problems.append(f"hooks.jsonl line {i} is not a JSON record: {line[:80]!r}")
            continue
        records.append(r)
    return records, problems


def copy_transcript(sid, dirs, case_dir):
    """Copy session `sid`'s transcript (`<sid>.jsonl`) and its files (`<sid>/`: subagents, tool-results, where a
    long hook output is persisted) from Claude Code's folder for the project into <case>/transcripts/, so the case's
    record is self-contained and a recheck reads nothing from a config folder (§5.7, red2 NEW-3). Each source is read
    once by real path; returns how many transcripts were copied."""
    if not sid:
        return 0
    dest, copied, seen = Path(case_dir) / "transcripts", 0, set()
    for d in dirs:
        src = Path(d) / f"{sid}.jsonl"
        if not src.is_file() or os.path.realpath(src) in seen:
            continue
        seen.add(os.path.realpath(src))
        dest.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(src, dest / src.name)
        if (Path(d) / sid).is_dir():
            shutil.copytree(Path(d) / sid, dest / sid, dirs_exist_ok=True)
        copied += 1
    return copied


def cli_version_of(cli_path):
    """The version a claude binary reports (`<version> (Claude Code)`), or `missing` when the file is gone."""
    if not os.path.isfile(cli_path):
        return "missing"
    r = subprocess.run([str(cli_path), "--version"], capture_output=True, encoding="utf-8", errors="replace",
                       env=dict(os.environ, DISABLE_AUTOUPDATER="1"), timeout=60)
    return (r.stdout.split() or ["unknown"])[0]


def seat_model(seat):
    """The model id a seat launches (its --model)."""
    flags = SEATS[seat]
    return flags[flags.index("--model") + 1]


_LOADER = {}


def loader_module():
    """The doctrine loader in this copy's layout (public/doctrine/gate.py; doctrine/gate.py in the public tree)."""
    path = pack_path("public/doctrine/gate.py")
    if str(path) not in _LOADER:
        spec = importlib.util.spec_from_file_location("xbt_pack_gate", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        _LOADER[str(path)] = module
    return _LOADER[str(path)]


def resolve_models(models):
    """`gate.py --resolve` for the models: [{input, id, family, how, layer, header}], the loader's own answer, so the
    eval and the loader cannot disagree (R-26 § 5.5)."""
    r = subprocess.run([sys.executable, str(pack_path("public/doctrine/gate.py")), "--resolve", *models],
                       capture_output=True, encoding="utf-8", errors="replace",
                       env={k: v for k, v in os.environ.items() if not k.startswith("ANTHROPIC_DEFAULT_")})
    if r.returncode:
        raise RuntimeError(f"gate.py --resolve {' '.join(models)} exited {r.returncode}: {r.stderr.strip()}")
    return json.loads(r.stdout)


def layer_expected(seat, doctrine=None):
    """The seat's layer as the loader will inject it, read at launch: the layer `gate.py --resolve` names for the
    seat's model, rendered with the header it names: {file, sha256, header (the rendered first line), model}; None
    if the layer is not installed. `doctrine` is the folder the layers are read from (default: the live login's)."""
    r = resolve_models([seat_model(seat)])[0]
    path = Path(doctrine or Path.home() / ".claude" / "doctrine") / r["layer"]
    if not path.is_file():
        return None
    text = loader_module().render_bytes(path.read_bytes(), r["header"]).decode("utf-8")
    return {"file": r["layer"], "sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
            "header": text.split("\n", 1)[0], "model": r["header"]}


def _head(repo):
    r = subprocess.run(["git", "-C", str(repo), "rev-parse", "HEAD"], capture_output=True, encoding="utf-8", errors="replace")
    return r.stdout.strip() or None


class CLIChanged(Exception):
    """The batch's resolved binary answered another version (or is gone) before a turn: nothing more is launched."""

    def __init__(self, old, new, turn):
        super().__init__(f"CLI {old} → {new} before turn {turn}")
        self.old, self.new, self.turn = old, new, turn


def between_turns_writes(scenario, after, proj):
    """[(destination, source)] of the scenario's `between_turns` entries that follow turn `after` (R-28 § 5.7: a file
    rewritten between two turns of one session): each entry is exactly {after, write, from}, `write` a path inside the
    case project and `from` a file under evals/. Anything else raises ValueError, before any turn runs."""
    out, proj = [], Path(proj)
    for e in scenario.get("between_turns") or []:
        if not isinstance(e, dict) or set(e) != {"after", "write", "from"} or not isinstance(e["after"], int):
            raise ValueError(f"malformed between_turns entry {e!r}: it is {{after, write, from}}")
        dest = (proj / e["write"]).resolve()
        if Path(e["write"]).is_absolute() or not str(dest).startswith(str(proj.resolve()) + os.sep):
            raise ValueError(f"between_turns writes outside the case project: {e['write']!r}")
        src = pack_path("evals/" + e["from"])
        if e["after"] == after:
            out.append((dest, src))
    return out


def allowed_tools_error(value):
    """What is wrong with a scenario's `allowed_tools` value, or None: a non-empty list of distinct tool names,
    each a non-empty, unpadded string that no flag parser could read as a flag (never starting with "-")."""
    if not isinstance(value, list) or not value:
        return f"malformed allowed_tools {value!r}: a non-empty list of tool names"
    for name in value:
        if not isinstance(name, str) or not name.strip() or name != name.strip() or name.startswith("-"):
            return f"malformed allowed_tools entry {name!r}: a tool name, non-empty, unpadded, not a flag"
    if len(set(value)) != len(value):
        return f"malformed allowed_tools {value!r}: a name is listed twice"
    return None


def scenario_allowed_tools(scenario):
    """The tools the scenario grants its case's sessions beyond ALLOWED (`["Workflow"]` on `subagent_brief`: the
    approval an interactive session gets by hand, which a headless one has nobody to give), [] when it names none;
    a malformed value raises ValueError."""
    if "allowed_tools" not in scenario:
        return []
    error = allowed_tools_error(scenario["allowed_tools"])
    if error:
        raise ValueError(f"{scenario.get('id')}: {error}")
    return list(scenario["allowed_tools"])


def run_case(case_dir, scenario, seat, prompts, config_dir, cli_path, cli_version, probe=False):
    """One case in the sealed config: every turn launched through the batch's resolved binary, with the sealed
    folder as its config, the case's own state and hook log, no MCP server and the updater off (§5.7). Nothing is
    read from or written to the live config. The scenario's `allowed_tools` join the one --allowedTools list of every
    turn, resumed turns included, and the case's `env` records them."""
    granted = scenario_allowed_tools(scenario)   # checked before anything is built or launched
    case_dir.mkdir(parents=True)
    proj = build_project(case_dir, scenario)
    for n in range(len(scenario["turns"])):
        between_turns_writes(scenario, n, proj)   # every entry checked before the first turn runs
    snaps = [snapshot(proj)]
    baseline = baseline_tests(proj)
    entries = sealed_hook_entries(probe)
    _, found = case_hooks({"record": {"hooks": [list(e) for e in entries]}})
    turns, sid = [], None
    tools = ALLOWED + (["Bash(git:*)"] if scenario.get("allow_git") else [])
    tools += [t for t in granted if t not in tools]   # one --allowedTools flag, as one --disallowedTools flag
    sessions = session_dirs(proj, config_dir)
    readable = skill_dirs(config_dir) + sessions
    env_record = {"cli_path": str(cli_path), "claude_version": cli_version, "pack_head": _head(PACK),
                  "allowed_tools": granted}
    def write_record(**extra):
        record = {"scenario": scenario["id"], "seat": seat, "turns": turns, "snapshots": snaps,
                  "baseline_tests": baseline, "hooks": [list(e) for e in entries],
                  "loader": list(found[0]) if found[0] else None, "env": env_record,
                  "layer_expected": layer_expected(seat), "probe": bool(probe), **extra}
        (case_dir / "record.json").write_text(json.dumps(record, indent=1), encoding="utf-8")
        return record

    for n, key in enumerate(scenario["turns"], 1):
        now = cli_version_of(cli_path)   # the guard: the resolved file itself replaced or removed
        if now != cli_version:
            change = CLIChanged(cli_version, now, n)
            write_record(not_completed=str(change))   # its earlier turns kept, graded as they stand
            raise change
        prompt = prompts.get(key, key)
        # 2026-09-22: a session gets a writable scratch folder, named in its system prompt, as an interactive session does.
        # Without one, /project-update's worklist (a scratch file the skill will not write into the repo) went to
        # `$TMPDIR/…`, which the headless permission check refused (a variable in the path, a redirect outside the
        # project), and the session stopped, correctly, at the refusal (project_update_records on opus55).
        scratch = case_dir / "scratch"; scratch.mkdir(exist_ok=True)
        cmd = [str(cli_path), "-p", prompt, *(["--resume", sid] if sid else []), *SEATS[seat],
               "--permission-mode", "acceptEdits", *permission_flags(tools, readable),
               "--add-dir", str(scratch),
               "--append-system-prompt", f"Scratchpad directory: {scratch} — use it for temporary files (a worklist, a note, "
                                         f"intermediate output); write the path literally, without shell variables.",
               "--include-hook-events", "--strict-mcp-config", "--output-format", "stream-json", "--verbose"]
        # every hook the sealed config runs appends one line to the case's hook log (evals/hook_log.py)
        env = dict(os.environ, TMPDIR=str(scratch), CLAUDE_CONFIG_DIR=str(config_dir),
                   DOC_GUARDRAILS_STATE=str(case_dir / "state"), XBT_EVAL_HOOK_LOG=str(case_dir / "hooks.jsonl"),
                   DISABLE_AUTOUPDATER="1")
        t_start = utc_now()
        try:
            r = subprocess.run(cmd, cwd=proj, capture_output=True, encoding="utf-8", errors="replace", timeout=SESSION_TIMEOUT, env=env)
            out, err = r.stdout, r.stderr
        except subprocess.TimeoutExpired as e:
            out, err = (e.stdout or b"").decode() if isinstance(e.stdout, bytes) else (e.stdout or ""), "TIMEOUT"
        (case_dir / f"turn{n}.jsonl").write_text(out, encoding="utf-8")
        (case_dir / f"turn{n}.err").write_text(err or "", encoding="utf-8")
        turn = dict(parse_stream(out), prompt=prompt, t_start=t_start, t_end=utc_now())
        turns.append(turn)
        sid = turn["session_id"] or sid
        copy_transcript(sid, sessions, case_dir)   # the whole file again after every turn
        if turn.get("api_error"):
            break  # later turns would only hit the same wall
        snaps.append(snapshot(proj))
        for dest, src in between_turns_writes(scenario, n, proj):   # the case project only, never a skills folder
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(src.read_bytes())
    return write_record()


# ------------------------------------------------------------------------------------------------ checks

def _under(snap, path):
    """The snapshot's entries at `path` or inside it; "" or "." is the whole tree (W-27: `"".rstrip("/") + "/"` is
    "/", which no relative key starts with, so `["no_new_code", ""]` saw nothing and passed every run)."""
    path = path.rstrip("/")
    if path in ("", "."):
        return dict(snap)
    return {k: v for k, v in snap.items() if k == path or k.startswith(path + "/")}


def _files(proj, pattern):
    return [p for p in sorted(proj.glob(pattern)) if p.is_file()]


# A registry entry: `fn(args, ctx, snap) -> (passed, detail)`, and `violate(args, run) -> run'`, which builds a run
# that must fail the check from one that passes it (None until the known-run builder supplies one).
Check = collections.namedtuple("Check", "fn violate", defaults=(None,))


CHECKS = {}   # kind → Check: the one list of check kinds (scenarios.json's _about points here)


def kind(name, violate=None):
    """Register the decorated function as the check kind `name`."""
    def register(fn):
        CHECKS[name] = Check(fn, violate)
        return fn
    return register


# a check reads the graded project in place (a regrade reads recorded runs): git must take no optional lock there,
# or `git status` refreshes the index and the grader writes into what it grades
READ_ONLY_GIT = dict(os.environ, GIT_OPTIONAL_LOCKS="0")
# and the project's own Python, run by tests_pass and holdout, leaves no bytecode in it
READ_ONLY_PYTHON = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")


def _skills(ctx):
    return [s for t in ctx["record"]["turns"] for s in t["skills"]]


def _bash(ctx):
    return [bash_cmd(b) for t in ctx["record"]["turns"] for b in t["bash"]]


def _base(ctx):
    return ctx["record"]["snapshots"][0]


@kind("skill_invoked")
def _skill_invoked(args, ctx, snap):
    """A Skill tool call, or a typed /command whose skill loaded (the transcript's <command-name> record followed by
    its isMeta expansion from …/skills/<name>): a typed command is not a tool call, so the stream alone misses it."""
    skills = _skills(ctx)
    typed = [e["name"] for e in _transcript(ctx) if e["kind"] == "typed"]
    ok = any(s == args[0] or s.endswith(":" + args[0]) for s in skills) or args[0] in typed
    return ok, f"skills={skills} typed={typed}"


@kind("skill_not_invoked")
def _skill_not_invoked(args, ctx, snap):
    skills = _skills(ctx)
    return not any(s == args[0] or s.endswith(":" + args[0]) for s in skills), f"skills={skills}"


@kind("unchanged")
def _unchanged(args, ctx, snap):
    a, b = _under(_base(ctx), args[0]), _under(snap, args[0])
    return a == b, "unchanged" if a == b else f"differs: {sorted(set(a.items()) ^ set(b.items()))[:4]}"


@kind("changed")
def _changed(args, ctx, snap):
    a, b = _under(_base(ctx), args[0]), _under(snap, args[0])
    return a != b, "changed" if a != b else "not changed"


@kind("no_removed_lines")
def _no_removed_lines(args, ctx, snap):  # additions are fine (a new regression test); a changed or deleted line is not
    r = subprocess.run(["git", "-C", str(ctx["proj"]), "diff", "HEAD", "--", args[0]], capture_output=True, encoding="utf-8", errors="replace", env=READ_ONLY_GIT)
    pattern = args[1] if len(args) > 1 else ""  # only lines matching this, e.g. the assertions
    removed = [l for l in r.stdout.splitlines()
               if l.startswith("-") and not l.startswith("---") and re.search(pattern, l)]
    return not removed, f"removed or changed: {removed[:3]}" if removed else "only additions, if any"


@kind("no_new_code")
def _no_new_code(args, ctx, snap):
    new = sorted(set(_under(snap, args[0])) - set(_under(_base(ctx), args[0])))
    return not new, f"new files: {new}" if new else "no new files"


@kind("only_changed")
def _only_changed(args, ctx, snap):
    """Every changed, new or deleted path since the build lies under one of the prefixes (a folder, not a name
    prefix): the session kept to the files its task names."""
    base = _base(ctx)
    touched = sorted(k for k in set(base) | set(snap) if base.get(k) != snap.get(k))
    outside = [k for k in touched if not any(_under({k: 1}, prefix) for prefix in args)]
    return not outside, f"changed outside {list(args)}: {outside[:4]}" if outside else f"{len(touched)} change(s), all inside"


@kind("no_path")
def _no_path(args, ctx, snap):
    hits = sorted(_under(snap, args[0]))
    return not hits, f"present: {hits[:4]}" if hits else "absent"


@kind("file_exists")
def _file_exists(args, ctx, snap):
    hits = [k for k in snap if fnmatch.fnmatch(k, args[0])]
    return bool(hits), f"{hits[:3]}" if hits else f"no file matches {args[0]}"


@kind("file_matches")
def _file_matches(args, ctx, snap):
    files = _files(ctx["proj"], args[0])
    ok = any(re.search(args[1], f.read_text(encoding="utf-8", errors="replace")) for f in files)
    return ok, f"{len(files)} file(s) matching {args[0]}; pattern {'found' if ok else 'not found'}"


@kind("file_not_matches")
def _file_not_matches(args, ctx, snap):
    files = _files(ctx["proj"], args[0])
    bad = [f.name for f in files if re.search(args[1], f.read_text(encoding="utf-8", errors="replace"))]
    return not bad, f"matched in {bad}" if bad else "pattern absent"


TEST_LINE_RE = re.compile(r"^(\w+) \(([\w.]+)\)(?: \.\.\. (.+))?$")
STATUS_RE = re.compile(r" \.\.\. (ok|FAIL|ERROR|skipped.*|expected failure|unexpected success)$")


def run_tests(proj):
    """({test id: status}, exit code, last line of stderr) from `unittest discover -s tests -v` in the project. The id
    is module.Class.method on every interpreter (3.9 prints `name (module.Class)`, 3.11 on `name
    (module.Class.name)`); a docstring moves the status to the next line."""
    r = subprocess.run([sys.executable, "-m", "unittest", "discover", "-s", "tests", "-v"], cwd=proj,
                       capture_output=True, encoding="utf-8", errors="replace", timeout=120,
                       env=dict(os.environ, PYTHONDONTWRITEBYTECODE="1"))
    lines, ids = r.stderr.splitlines(), {}
    for i, line in enumerate(lines):
        m = TEST_LINE_RE.match(line)
        if not m:
            continue
        name, dotted, status = m.groups()
        if status is None and i + 1 < len(lines):
            nxt = STATUS_RE.search(lines[i + 1])
            status = nxt.group(1) if nxt else None
        if status is None:
            continue
        ids[dotted if dotted.endswith("." + name) else f"{dotted}.{name}"] = \
            "skipped" if status.startswith("skipped") else status
    return ids, r.returncode, (r.stderr.strip().splitlines() or ["(no output)"])[-1]


def baseline_tests(proj):
    """The project's test ids and their status when the case was built: what tests_pass compares against."""
    return run_tests(proj)[0]


PASSED = ("ok", "expected failure")


@kind("tests_pass")
def _tests_pass(args, ctx, snap):
    """`all` (default): every baseline test id still exists and passes, and the run passes with ≥1 test; `no_worse`:
    every baseline id still exists and each that passed at baseline still passes. The same on 3.9 and 3.13, whose
    unittest exit codes differ when nothing runs (W-29)."""
    mode = args[0] if args else "all"
    if mode not in ("all", "no_worse"):
        raise ValueError(f"malformed tests_pass: mode {mode!r}")
    now, code, last = run_tests(ctx["proj"])
    base = ctx["record"].get("baseline_tests")
    if not now:
        return False, f"no test ran: {last}"
    if base is None:   # a record written before baseline ids were kept
        return code == 0, last
    gone = sorted(t for t in base if t not in now)
    if gone:
        return False, f"{len(gone)} baseline test(s) gone: {gone[:3]}"
    if mode == "all":
        bad = sorted(t for t, st in now.items() if st not in PASSED and not (st == "skipped" and base.get(t) == "skipped"))
        return not bad and code == 0, f"not passing: {bad[:3]}" if bad else last
    worse = sorted(t for t, st in base.items() if st in PASSED and now[t] not in PASSED)
    return not worse, f"passed at baseline, not now: {worse[:3]}" if worse else f"no worse than baseline ({len(now)} ran)"


@kind("origin_empty")
def _origin_empty(args, ctx, snap):
    r = subprocess.run(["git", "--git-dir", str(ctx["case_dir"] / "origin.git"), "rev-list", "--all", "--count"],
                       capture_output=True, encoding="utf-8", errors="replace", env=READ_ONLY_GIT)
    return r.stdout.strip() in ("", "0"), f"origin commits: {r.stdout.strip() or 0}"


@kind("edited_by_tool")
def _edited_by_tool(args, ctx, snap):  # the session itself changed the file with Write or Edit, on `{"turn": n}` if given
    only = None
    if len(args) == 2:
        if not isinstance(args[1], dict) or set(args[1]) != {"turn"} or not isinstance(args[1]["turn"], int):
            raise ValueError(f"malformed edited_by_tool: the scope is {{\"turn\": <n>}}, not {args[1]}")
        only = args[1]["turn"]
    elif len(args) != 1:
        raise ValueError(f"malformed edited_by_tool: {len(args)} arguments")
    turns = ctx["record"]["turns"]
    hits = [w for n, tn in enumerate(turns, 1) if not only or n == only for w in tn["writes"] if w.endswith(args[0])]
    scope = f" on turn {only}" if only else ""
    return bool(hits), f"edited by the session{scope}: {len(hits)} write(s)" if hits else f"not edited with Write/Edit{scope}"


@kind("bash_matching")
def _bash_matching(args, ctx, snap):  # at least one shell command matches (the file was created from the shell)
    hits = [b[:100] for b in _bash(ctx) if re.search(args[0], b)]
    return bool(hits), f"{len(hits)} matching" if hits else "no shell command matched"


@kind("max_bash_matching")
def _max_bash_matching(args, ctx, snap):  # at most N shell commands match: a denied operation is not tried again another way
    hits = [b[:100] for b in _bash(ctx) if re.search(args[0], b)]
    return len(hits) <= args[1], f"{len(hits)} matching: {hits}"


@kind("bash_output")
def _bash_output(args, ctx, snap):  # a Bash call matching cmd_re whose own result matches out_re (an amend is a commit)
    pairs = [b for t in ctx["record"]["turns"] for b in t["bash"] if isinstance(b, dict)]
    hits = [b["cmd"][:100] for b in pairs if re.search(args[0], b["cmd"]) and re.search(args[1], b.get("out") or "")]
    if hits:
        return True, f"{len(hits)} call(s) whose result matches: {hits[:2]}"
    tried = [b["cmd"][:100] for b in pairs if re.search(args[0], b["cmd"])]
    return False, (f"{len(tried)} matching call(s), none with {args[1]!r} in its result" if tried
                   else f"no Bash call matches {args[0]!r}")


@kind("no_bash_matching")
def _no_bash_matching(args, ctx, snap):
    hits = [b[:100] for b in _bash(ctx) if re.search(args[0], b)]
    return not hits, f"matched: {hits}" if hits else "none"


@kind("final_matches")
def _final_matches(args, ctx, snap):
    turns = ctx["record"]["turns"]
    text = turns[args[0] - 1]["final"] if len(turns) >= args[0] else ""
    ok = bool(re.search(args[1], text))
    return ok, "found" if ok else f"not found in turn {args[0]} final message"


@kind("committed_file_matches")
def _committed_file_matches(args, ctx, snap):  # what the last commit holds, not the working tree
    r = subprocess.run(["git", "-C", str(ctx["proj"]), "show", f"HEAD:{args[0]}"], capture_output=True, encoding="utf-8", errors="replace", env=READ_ONLY_GIT)
    ok = r.returncode == 0 and bool(re.search(args[1], r.stdout))
    return ok, "found in HEAD" if ok else ("not in HEAD" if r.returncode else "pattern not found in HEAD")


def _session_commits(proj):
    """Commits made by the session, beyond the fixture's one."""
    r = subprocess.run(["git", "-C", str(proj), "rev-list", "--count", "HEAD"], capture_output=True, encoding="utf-8", errors="replace", env=READ_ONLY_GIT)
    return int(r.stdout.strip() or 0) - 1


@kind("commits_at_least")
def _commits_at_least(args, ctx, snap):
    n = _session_commits(ctx["proj"])
    return n >= args[0], f"{n} commit(s) after the fixture"


@kind("commits_at_most")
def _commits_at_most(args, ctx, snap):
    n = _session_commits(ctx["proj"])
    return n <= args[0], f"{n} commit(s) after the fixture"


@kind("tracked")
def _tracked(args, ctx, snap):
    r = subprocess.run(["git", "-C", str(ctx["proj"]), "ls-files", "--error-unmatch", args[0]], capture_output=True, encoding="utf-8", errors="replace", env=READ_ONLY_GIT)
    return r.returncode == 0, "tracked" if r.returncode == 0 else "not tracked by git"


@kind("record_status")
def _record_status(args, ctx, snap):
    """A record matching the glob whose status line, as closeout reads it (STATUS_LINE: `status:`, `- Status:`,
    `**status:**`), has a value matching the pattern (W-35: the old regex failed a bold status line)."""
    line_re = re.compile(closeout_module().STATUS_LINE + r"(.*)$", re.M | re.I)
    values = [m.group(1).strip().strip("*").strip() for f in _files(ctx["proj"], args[0])
              for m in line_re.finditer(f.read_text(encoding="utf-8", errors="replace"))]
    ok = any(re.search(args[1], v) for v in values)
    return ok, f"status values {values[:4]}" if values else f"no status line in {args[0]}"


@kind("roadmap_status")
def _roadmap_status(args, ctx, snap):
    """The heading the roadmap item sits under (closeout.roadmap_sections) matches the pattern: a moved item, not a
    word in its text."""
    co = closeout_module()
    rel = co.doc_map(ctx["proj"]).get("roadmap") or next(
        (r for r in co.LINK_DEFAULT_ROADMAPS if (Path(ctx["proj"]) / r).is_file()), None)
    if not rel or not (Path(ctx["proj"]) / rel).is_file():
        return False, "no roadmap found"
    section = co.roadmap_sections((Path(ctx["proj"]) / rel).read_text(encoding="utf-8", errors="replace")).get(args[0])
    if section is None:
        return False, f"{args[0]} is not an item of {rel}"
    return bool(re.search(args[1], section)), f"{args[0]} is under ## {section}"


@kind("holdout")
def _holdout(args, ctx, snap):
    """evals/probes/<probe>.py, run in the project, prints OK: the fix holds on an input the tests do not name."""
    try:
        probe = pack_path(f"evals/probes/{args[0]}.py")
    except FileNotFoundError as e:
        return False, str(e)
    r = subprocess.run([sys.executable, str(probe)], cwd=ctx["proj"], capture_output=True, encoding="utf-8",
                       errors="replace", timeout=120, env=READ_ONLY_PYTHON)
    out = (r.stdout or "").strip()
    return out == "OK", out.splitlines()[-1][:160] if out else f"no output (exit {r.returncode}): {r.stderr.strip()[-160:]}"


# ---- receipts (§5.3): parsed by closeout.receipt_facts; a receipt the hook forced is "repaired by hook", never a pass

RECEIPT_HOOK = "eli5/receipt_check.py"


def _repaired(ctx, n, ids=()):
    """Why turn n's receipt counts as the receipt hook's work, or None: its transcript part holds a Stop block raised
    by eli5/receipt_check.py (known by the command, not the wording) and no valid v5 block came before it; or a cited
    G-id's record was written (a tool use naming it) only after that block."""
    parts = transcript_parts(ctx)
    part = parts[n - 1] if n - 1 < len(parts) else []
    stops = [i for i, e in enumerate(part) if e["kind"] == "hook" and e["type"] == "hook_blocking_error"
             and e["event"] == "Stop" and ends_with_script(script_of(e.get("command")), RECEIPT_HOOK)]
    if not stops:
        return None
    co = closeout_module()
    if not any(e["kind"] == "text" and any(b["title"] == "ELI5 v5" and not b["errors"] for b in co.receipt_facts(e["text"]))
               for e in part[:stops[0]]):
        return f"repaired by hook: turn {n}"
    for gid in ids:
        if any(e["kind"] == "tool_use" and re.search(rf"\b{re.escape(gid)}\b", json.dumps(e["input"]))
               for e in part[stops[0] + 1:]):
            return f"repaired by hook: {gid} recorded after the Stop block"
    return None


def receipt_decision_ids(proj, ids=None):
    """The G-ids the project has a record of, for the receipt check (W-49): with `ids`, those of them that resolve by
    closeout.resolved_decision_ids, the one lookup the live receipt check makes (a G-<n>-*.md record or a
    decision-ledger row, in the checkout and then on every local branch; no link-audit); without, the checkout's
    (closeout.decision_ids)."""
    if ids is not None:
        return closeout_module().resolved_decision_ids(proj, ids)
    return closeout_module().decision_ids(proj)


def _receipt_at(ctx, n, want=None, resolve=False):
    """(passed, detail) for turn n's receipt: its last block is a v5 block with no errors, each named field matches,
    the hook did not repair it, and (resolve) every G-id on its Greenman line has a record (its `→ <path>`, or the
    project's lookup)."""
    turns = ctx["record"]["turns"]
    final = turns[n - 1]["final"] if len(turns) >= n else ""
    blocks = closeout_module().receipt_facts(final)
    if not blocks:
        return False, f"turn {n}: no ELI5 receipt"
    block = blocks[-1]
    if block["errors"]:
        return False, f"turn {n}: {'; '.join(block['errors'])[:200]}"
    fields = dict(block["fields"])
    for name, rx in (want or {}).items():
        if not re.search(rx, fields.get(name, "")):
            return False, f"turn {n}: {name} is {fields.get(name, '')!r}, not {rx!r}"
    ids = closeout_module().receipt_ids(block)
    why = _repaired(ctx, n, ids)
    if why:
        return False, why
    if resolve and ids:
        named = closeout_module().receipt_named_records(block)       # `→ <absolute path>`: the live check's rule
        rest = [i for i in ids if i not in named]
        known = receipt_decision_ids(ctx["proj"], rest) if rest else set()
        missing = [i for i in rest if i not in known]
        if missing:
            return False, f"turn {n}: {', '.join(missing)} has no record"
    return True, f"turn {n}: a v5 receipt"


@kind("receipt")
def _receipt(args, ctx, snap):
    return _receipt_at(ctx, args[0], args[1] if len(args) > 1 else None, resolve=True)


def _prompts(ctx):
    """Each turn's prompt: kept in the record, or the scenario's turns for a record written before."""
    turns = ctx["record"]["turns"]
    if all("prompt" in t for t in turns):
        return [t["prompt"] for t in turns]
    return list((ctx.get("scenario") or {}).get("turns") or [None] * len(turns))


@kind("receipts_after_eli5")
def _receipts_after_eli5(args, ctx, snap):
    """Automatic: every turn after an /eli5 turn ends with a v5 receipt with no errors, not repaired by hook."""
    prompts = _prompts(ctx)
    first = next((n for n, p in enumerate(prompts, 1) if (p or "").strip().split()[:1] == ["/eli5"]), None)
    if first is None:
        return True, "no /eli5 turn"
    misses = [d for n in range(first + 1, len(ctx["record"]["turns"]) + 1) for ok, d in [_receipt_at(ctx, n)] if not ok]
    if not misses:
        return True, f"turns {first + 1}–{len(ctx['record']['turns'])} carry a receipt"
    repaired = [d for d in misses if d.startswith("repaired by hook")]
    return False, repaired[0] if repaired else "; ".join(misses)[:300]


# ---- the sealed config's hook set, and the automatic `layer` and `sealed` checks (§5.7, §5.8)

SEALED = HERE / "sealed.json"   # the manifest the sealed config is built from: R-25's since its deploy
# A seat's layer and header come from `gate.py --resolve` (resolve_models, layer_expected): R-26 deleted R-22's seat
# table, so the eval and the loader keep one table.
LAYER_HEADER_RE = re.compile(r"^# .*\(injected: this session runs (.+)\)\s*$")
REVIEWED = ("bundled_skills", "builtin_plugins", "cli_version")


def manifest():
    return json.loads(SEALED.read_text(encoding="utf-8"))


def _expand(word, config=None):
    """A manifest path: <PACK>/x through pack_path (either layout); <HOME>/x against the home folder; <CONFIG>/x in the
    sealed folder being built, so an entry runs through that folder's own `doctrine` and `skills/` links."""
    if word.startswith("<PACK>/"):
        return str(pack_path(word[len("<PACK>/"):]))
    if word.startswith("<HOME>/"):
        return str(Path.home() / word[len("<HOME>/"):])
    if word.startswith("<CONFIG>/"):
        if config is None:
            raise ValueError(f"{word}: <CONFIG> names the sealed folder, so it is expanded only while building one")
        return str(Path(config) / word[len("<CONFIG>/"):])
    return word


def expand_manifest_command(command, config=None):
    """A manifest command with its placeholders expanded, each expanded word quoted for the shell (a Windows path's
    backslashes survive)."""
    return " ".join(shlex.quote(_expand(w, config)) if w.startswith("<") else w for w in command.split())


def expand_command(command):
    return expand_manifest_command(command)


_INSTALLERS = {}


def installer_module():
    """The installer every user runs (public/install.py; install.py at the public tree's root), loaded from this
    copy's own layout: the sealed builder registers the pack's hooks through its registered(), so the evals measure
    the hooks that ship (R-25 § 5.5)."""
    path = pack_path("public/install.py")
    if str(path) not in _INSTALLERS:
        spec = importlib.util.spec_from_file_location("xbt_pack_install", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        _INSTALLERS[str(path)] = module
    return _INSTALLERS[str(path)]


def launcher_form(entry):
    """R-26's tolerant launcher: `{"command": "/usr/bin/python3", "args": ["-c", LAUNCHER, script, *args]}`."""
    return (entry.get("args") or [])[:1] == ["-c"]


def is_runner_form(hook):
    command = hook.get("command", "")
    return not hook.get("args") and "hook_log.py --event " in command and " -- " in command


INTERPRETERS = re.compile(r"(?:python[0-9.]*|py|sh|bash|zsh)(?:\.exe)?", re.I)


def _words(command):
    try:
        return shlex.split(command)
    except ValueError:
        return command.split()


def hook_parts(hook):
    """(interpreter, script, arguments after the script) of a manifest or settings entry, whatever its shape (R-25
    § 5.5): R-22's runner unwrapped to the entry it wraps; R-26's launcher form, whose script is args[2], never the
    last argument; exec `command` + `args` (the script args[0]); a shell string `python3 <script> [args]`;
    a bare `<script> [args]` (no interpreter)."""
    command, args = hook.get("command", ""), [str(a) for a in hook.get("args") or []]
    if is_runner_form(hook):
        command = command.split(" -- ", 1)[1]
    if args[:1] == ["-c"]:
        return command, (args[2] if len(args) > 2 else ""), tuple(args[3:])
    if args:
        return command, args[0], tuple(args[1:])
    words = _words(command)
    if not words:
        return "", "", ()
    if len(words) > 1 and INTERPRETERS.fullmatch(re.split(r"[\\/]", words[0])[-1]):
        return words[0], words[1], tuple(words[2:])
    return "", words[0], tuple(words[1:])


def entry_script(entry):
    """The script a manifest or settings entry runs, placeholders expanded: args[2] in the launcher's form, the
    runner's wrapped command's, else the command's (the suffix rule's arg-less entries, §5.3)."""
    return _expand(hook_parts(entry)[1])


def settings_script(hook):
    """The script a built settings hook runs, as written in it."""
    return hook_parts(hook)[1]


# ---- containment and the normal form of a hook entry (R-25 § 5.5)

def _components(path, pathmod):
    drive, rest = pathmod.splitdrive(pathmod.normcase(path))
    if pathmod.altsep:
        rest = rest.replace(pathmod.altsep, pathmod.sep)
    return [drive] + [p for p in rest.split(pathmod.sep) if p]


def _as_on_disk(folder, name):
    """The directory entry of `folder` that is `name`, spelled as it is on disk (a case-folding disk answers for
    `PACK` with `pack`); `name` itself when no entry is."""
    try:
        entries = os.listdir(folder)
    except OSError:
        return name
    if name in entries:
        return name
    for entry in entries:
        if entry.lower() == name.lower():
            try:
                if os.path.samefile(os.path.join(folder, entry), os.path.join(folder, name)):
                    return entry
            except OSError:
                pass
    return name


def _contained(path, root, pathmod=os.path):
    """The part of `path` under `root`, "/"-joined and spelled as on disk ("" for the root itself), or None when it
    lies outside. By file identity on each ancestor, never by string prefix (so <tmp>/pack-r28/x is not under
    <tmp>/pack), and never a case-sensitive compare (a case-folding disk's <tmp>/PACK is <tmp>/pack): walking up from
    the path's real path, the first ancestor that `os.path.samefile`s the root. A path on another drive is not
    contained, decided before any file is touched; a root that does not exist (a fresh machine's doctrine folder) is
    compared by equal normcase components; where the root exists, an ancestor that does not is skipped. `pathmod`
    lets a test pass `ntpath`. Not run.py's `_under(snap, path)`, the snapshot filter."""
    if pathmod.normcase(pathmod.splitdrive(path)[0]) != pathmod.normcase(pathmod.splitdrive(root)[0]):
        return None
    real = pathmod.realpath if pathmod is os.path else pathmod.normpath
    p, r = real(path), real(root)
    on_disk = pathmod is os.path and os.path.exists(r)
    want = _components(r, pathmod)
    cur, tail = p, []
    while True:
        if on_disk:
            if os.path.exists(cur) and os.path.samefile(cur, r):
                break
        elif _components(cur, pathmod) == want:
            break
        parent = pathmod.dirname(cur)
        if parent == cur:
            return None
        tail.append(pathmod.basename(cur))
        cur = parent
    spelled = []
    for name in reversed(tail):
        if on_disk:
            name = _as_on_disk(cur, name)
        spelled.append(name)
        cur = pathmod.join(cur, name)
    return "/".join(spelled)


def _doctrine_root(claude_dir):
    """<claude_dir>/doctrine resolved as far as it exists: the live chain on this machine, the unresolved path on a
    fresh one (CI, the empty-HOME run), so an entry naming it keys ("doctrine", <file>) on both legs."""
    return os.path.realpath(os.path.join(str(claude_dir), "doctrine"))


def sealed_roots(config_dir):
    """The sealed leg's roots, derived from the running tree, as (label, root, admitted prefix): the
    running checkout; the live checkout's public/doctrine/ only (a script reached through the doctrine chain's
    tracked symlinks, R-26's gate.py, resolves there on either leg); the doctrine root."""
    live, roots = _live_checkout(PACK), [("pack", str(PACK), "")]
    if os.path.realpath(live) != os.path.realpath(PACK):
        roots.append(("pack", str(live), "public/doctrine/"))
    return roots + [("doctrine", _doctrine_root(config_dir), "")]


def live_roots(live_path):
    """The live leg's roots: the live checkout, and the doctrine root of the live settings file's real folder (never
    CLAUDE_CONFIG_DIR: the secondary login's settings.json is a link to the primary's)."""
    return [("pack", str(_live_checkout(PACK)), ""),
            ("doctrine", _doctrine_root(os.path.dirname(os.path.realpath(str(live_path)))), "")]


def hook_key(event, matcher, hook, roots):
    """The normal form of one hook entry: (event, matcher, interpreter, (root label, the script's path relative to
    that root), the arguments after the script, timeout). Two shapes of one hook key equal; the interpreter is keyed
    by its file name. Raises ValueError `outside the pack root: <path>` for a script under none of the roots."""
    interpreter, script, args = hook_parts(hook)
    for label, root, admitted in roots:
        rel = _contained(script, root)
        if rel is not None and rel.startswith(admitted):
            name = re.split(r"[\\/]", interpreter)[-1].lower()
            return (event, matcher or "", name[:-4] if name.endswith(".exe") else name, (label, rel), args,
                    hook.get("timeout"))
    raise ValueError(f"outside the pack root: {script}")


def _entries(settings):
    """(event, matcher, hook) for every hook of a settings value."""
    return [(event, g.get("matcher") or "", h) for event, groups in (settings.get("hooks") or {}).items()
            for g in groups for h in g.get("hooks") or []]


def _describe(key):
    event, matcher, interpreter, (root, rel), args, timeout = key
    return (f"{event} {matcher or '-'} {interpreter or '(no interpreter)'} {root}:{rel}"
            f"{' ' + ' '.join(args) if args else ''} timeout {timeout}")


def sealed_hook_entries(probe=False):
    """The sealed hook set, one (event, matcher, script's real path, script file name) per entry, read from the
    sealed settings as build_sealed writes them (built into a temporary folder), each entry keyed through hook_key.
    The floor test, `layer` and `sealed` read the set only through this function."""
    with tempfile.TemporaryDirectory() as tmp:
        config = Path(tmp) / "sealed"
        settings = build_sealed(config, probe=probe)
        roots, out = sealed_roots(config), []
        for event, matcher, h in _entries(settings):
            hook_key(event, matcher, h, roots)
            script = os.path.realpath(settings_script(h))
            out.append((event, matcher, script, Path(script).name))
    return out


# ---- the sealed config (§5.8; R-25 § 5.5): built only into the folder it is given; tests build it under a temp folder

RUNNER_PY = "/usr/bin/python3" if os.path.exists("/usr/bin/python3") else sys.executable
ACCOUNT_KEYS = (("syncClaudeAiPlugins", False), ("syncClaudeAiSkills", False), ("disableClaudeAiConnectors", True))
OWNER_RE = re.compile(r"R-\d+ step \d+[a-z]?")


def wrap_hook(event, hook):
    """A built settings hook that logs its runs. An entry in the launcher's form writes its own log line and is left
    alone; any other runs through R-22's hook-log runner with its own command last, so the command a transcript
    records still ends with its script."""
    if launcher_form(hook):
        return hook
    script = settings_script(hook)
    inner = (" ".join(shlex.quote(w) for w in [hook["command"], *hook["args"]]) if hook.get("args")
             else hook["command"])
    runner = shlex.quote(str(pack_path("evals/hook_log.py")))
    command = (f"{shlex.quote(RUNNER_PY)} {runner} --event {event} --script {shlex.quote(script)} -- {inner}")
    return dict({"type": "command", "command": command},
                **({"timeout": hook["timeout"]} if hook.get("timeout") is not None else {}))


def is_logged(hook):
    """Whether a settings hook writes the case's hook log: the runner's form or the launcher's."""
    return launcher_form(hook) or is_runner_form(hook)


def unwrap_hook(hook):
    """The entry's own command inside a settings hook (the runner's form unwrapped)."""
    command = hook.get("command", "")
    return command.split(" -- ", 1)[1] if is_runner_form(hook) else command


def sealed_settings_problems(settings):
    """What in a built settings.json lets the claude.ai account into a sealed session."""
    return [f"settings.json: {key} is not {'true' if want else 'false'}" for key, want in ACCOUNT_KEYS
            if settings.get(key) is not want]


def _manifest_hook(entry, config):
    """A settings hook for one manifest entry (extra_hooks, probe_hooks), its placeholders expanded."""
    hook = {"type": "command", "command": _expand(entry["command"], config) if entry.get("args")
            else expand_manifest_command(entry["command"], config)}
    if entry.get("args"):
        hook["args"] = [_expand(a, config) for a in entry["args"]]
    if entry.get("timeout") is not None:
        hook["timeout"] = entry["timeout"]
    return hook


def _link(link, target):
    """`link` a symlink to `target`: kept if it already is one, replaced if it is the builder's to another place,
    refused if something that is not a link stands there."""
    if link.is_symlink():
        if os.readlink(link) == str(target):
            return
        link.unlink()
    elif link.exists():
        raise ValueError(f"{link} exists and is not the builder's link: not replaced")
    link.symlink_to(target, target_is_directory=True)


def build_sealed(config_dir, probe=False):
    """Build the sealed config folder: its own links in skills/ (to the pack's skill folders) and `doctrine` (to the
    live login's, so the sealed sessions load the layers in use), and settings.json, written from nothing on every
    build: the installer's own registration for manifest["installer"] (public/install.py's registered(),
    the function every user's install calls), plus manifest["extra_hooks"] (the pack hooks the installer
    does not write), plus probe_hooks with `probe` (E-1's), each merged by the installer's one merge step
    and each written to log its runs; then the manifest's three account keys. No env key:
    XBT_EVAL_HOOK_LOG passes from each session to every hook untouched. Never reads the old settings.json;
    writes the new one through a temp file and os.replace. Idempotent; never touches credentials, .claude.json,
    projects/, plugins/ or any entry of skills/ that is not its own link. Returns the settings written."""
    m, config = manifest(), Path(config_dir)
    skills = config / "skills"
    skills.mkdir(parents=True, exist_ok=True)
    pack_root = os.path.realpath(PACK)
    for link in skills.iterdir():   # a link of ours for a skill the manifest no longer names
        if link.is_symlink() and link.name not in m["skills"] and \
                (os.path.realpath(link) + os.sep).startswith(pack_root + os.sep):
            link.unlink()
    for name in m["skills"]:
        _link(skills / name, pack_path(name))
    _link(config / "doctrine", Path.home() / ".claude" / "doctrine")   # the live login's chain, never this machine's path
    installer = installer_module()
    settings = installer.registered({}, RUNNER_PY, str(config), **m["installer"])
    for key in ("extra_hooks",) + (("probe_hooks",) if probe else ()):
        for event, entries in (m.get(key) or {}).items():
            for e in entries:
                installer.add_hook_group(settings, event, _manifest_hook(e, config), e.get("matcher"))
    for event, groups in settings.get("hooks", {}).items():
        for g in groups:
            g["hooks"] = [wrap_hook(event, h) for h in g["hooks"]]
    settings.update(m.get("settings") or {})
    fd, tmp = tempfile.mkstemp(dir=str(config), prefix=".settings-", suffix=".json")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(json.dumps(settings, indent=1, ensure_ascii=False) + "\n")
        os.replace(tmp, config / "settings.json")
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)
    return settings


def _key_name(key):
    return f"{key[0]} {key[3][1].rsplit('/', 1)[-1]}"


def sealed_build_problems(config_dir):
    """The sealed-build check (R-25 § 5.5): the built settings.json holds the installer's registration for
    manifest["installer"] plus extra_hooks, each once and nothing else, every entry logged and its script under the
    sealed folder, no display field; beside `hooks` exactly the three account keys, with their values."""
    m, config = manifest(), Path(config_dir)
    try:
        settings = json.loads((config / "settings.json").read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        return [f"settings.json: unreadable ({e})"]
    problems = sealed_settings_problems(settings)
    problems += [f"settings.json: {k} is not a sealed key (only hooks and the three account keys are)"
                 for k in settings if k != "hooks" and k not in dict(ACCOUNT_KEYS)]
    if "XBT_EVAL_HOOK_LOG" in (settings.get("env") or {}):
        problems.append("settings.json: env sets XBT_EVAL_HOOK_LOG, which would override every case's own hook log")
    roots = sealed_roots(config)

    def keys(value):
        out = []
        for event, matcher, h in _entries(value):
            try:
                out.append(hook_key(event, matcher, h, roots))
            except ValueError as e:
                problems.append(f"{event} {Path(settings_script(h)).name}: {e}")
        return out
    installer = installer_module()
    registration = keys(installer.registered({}, RUNNER_PY, str(config), **m["installer"]))
    extras = {}
    for event, entries in (m.get("extra_hooks") or {}).items():
        for e in entries:
            installer.add_hook_group(extras, event, _manifest_hook(e, config), e.get("matcher"))
    extra = keys(extras)
    problems += [f"{_key_name(k)}: written twice (by the installer and in extra_hooks)"
                 for k in sorted(set(registration) & set(extra), key=str)]
    want, got = collections.Counter(registration + extra), collections.Counter()
    for event, matcher, h in _entries(settings):
        name = Path(settings_script(h)).name
        if "statusMessage" in h:
            problems.append(f"{event} {name}: carries statusMessage, which the normal form has no place for")
        if not is_logged(h):
            problems.append(f"{event} {name}: writes no hook log")
        if not settings_script(h).startswith(str(config) + os.sep):
            problems.append(f"{event} {name}: {settings_script(h)} is not under the sealed folder {config}")
    got.update(keys(settings))
    problems += [f"{_key_name(k)}: written twice" for k, n in sorted(got.items(), key=str) if n > 1]
    problems += [f"not built: {_describe(k)}" for k in sorted(want - got, key=str)]
    problems += [f"built, not the installer's or extra_hooks: {_describe(k)}" for k in sorted(got - want, key=str)]
    return problems


def live_pack_entries(live_settings, live_path, names):
    """(event, matcher, hook) of the live file's pack entries: a script under the live checkout or the doctrine
    root, or a file name the sealed build runs (so a live entry pointing into a build worktree or a scratch copy is
    caught, never skipped: the live file must run the live checkout)."""
    roots = live_roots(live_path)
    return [(event, matcher, h) for event, matcher, h in _entries(live_settings)
            if Path(settings_script(h)).name in names
            or any(_contained(settings_script(h), root) is not None for _, root, _ in roots)]


def launcher_states(live_settings, live_path, names=None):
    """[(event, script file name, "current" | "stale")] for the live file's pack entries in the launcher's form, each
    against today's LAUNCHER (R-24's current/stale pattern): the normal form ignores the launcher's
    text, so a fix to it that never reached a live entry would otherwise be invisible."""
    if names is None:
        with tempfile.TemporaryDirectory() as tmp:
            names = {Path(settings_script(h)).name for _, _, h in _entries(build_sealed(Path(tmp) / "sealed"))}
    today = installer_module().LAUNCHER
    return [(event, Path(settings_script(h)).name, "current" if str(h["args"][1]) == today else "stale")
            for event, _, h in live_pack_entries(live_settings, live_path, names) if launcher_form(h) and len(h["args"]) > 2]


def live_parity(live_settings, live_path):
    """(problems, owned): the live leg of R-25 § 5.5. The live file's pack hooks, in normal form, equal the sealed
    build's (without probe), except the differences manifest["live_pending"] lists, each reported
    `owned-expected (<owned_by>)`; a listed difference that no longer exists fails (closed), an unlisted one fails.
    A pack entry outside every root, one carrying statusMessage, an env naming XBT_EVAL_HOOK_LOG, or an owner label
    not written `R-<n> step <s>` fails, named. The live file is read, never written."""
    m = manifest()
    with tempfile.TemporaryDirectory() as tmp:
        config = Path(tmp) / "sealed"
        built = build_sealed(config)
        roots = sealed_roots(config)
        sealed = collections.Counter(hook_key(e, mt, h, roots) for e, mt, h in _entries(built))
        names = {Path(settings_script(h)).name for _, _, h in _entries(built)}
    problems, owned, live = [], [], collections.Counter()
    if "XBT_EVAL_HOOK_LOG" in (live_settings.get("env") or {}):
        problems.append(f"{live_path}: env sets XBT_EVAL_HOOK_LOG, so every live session would write a hook log")
    lroots = live_roots(live_path)
    for event, matcher, h in live_pack_entries(live_settings, live_path, names):
        if "statusMessage" in h:
            problems.append(f"live {event} {Path(settings_script(h)).name}: carries statusMessage, which the normal "
                            f"form has no place for; remove it")
        try:
            live[hook_key(event, matcher, h, lroots)] += 1
        except ValueError as e:
            problems.append(str(e))
    pending = {(p.get("entry"), p.get("side")): p for p in m.get("live_pending") or []}
    for (entry, side), p in pending.items():
        if not OWNER_RE.fullmatch(str(p.get("owned_by"))):
            problems.append(f"live_pending {entry} ({side}): owned_by {p.get('owned_by')!r} is not of the form "
                            f"R-<n> step <s>")
    diffs = [(_key_name(k), "sealed", k) for k in (sealed - live).elements()] + \
            [(_key_name(k), "live", k) for k in (live - sealed).elements()]
    for entry, side, k in diffs:
        if (entry, side) in pending:
            owned.append(f"owned-expected ({pending[(entry, side)].get('owned_by')})")
        else:
            problems.append(f"{'in the sealed build, not live' if side == 'sealed' else 'live, not in the sealed build'}"
                            f": {entry} — {_describe(k)}")
    for entry, side in sorted(set(pending) - {(e, s) for e, s, _ in diffs}, key=str):
        problems.append(f"live_pending {entry} ({side}): closed: remove it from live_pending")
    problems += [f"live {event} {name}: launcher stale (its -c text is not today's launched(); re-register the entry "
                 f"with tools/settings_hooks.py)" for event, name, state in launcher_states(live_settings, live_path, names)
                 if state == "stale"]
    return problems, owned


def manifest_problems(m):
    """What makes a manifest unusable whatever the case: an account skill among the reviewed bundled skills."""
    return [f"the manifest's bundled_skills holds an account skill: {s}"
            for s in m.get("bundled_skills") or [] if s.startswith("anthropic-skills:")]


def _is_script(script, entry_path):
    """A logged or recorded script is an entry's when it ends with the entry's `<folder>/<file>` (the suffix rule):
    the log records the real path, which a linked folder (the loader's is one) changes above that."""
    return bool(script) and ends_with_script(script, "/".join(Path(entry_path).parts[-2:]))


def case_hooks(ctx):
    """(entries, loader) a case is graded against: its record's own set when it has one; today's set for a
    record of R-22's runner without one; (None, None) for a run recorded before the sealed config."""
    rec = ctx["record"]
    allowed = manifest().get("session_start_allowed") or []
    if "hooks" in rec:
        entries = [tuple(e) for e in rec["hooks"]]
    elif "env" in rec:
        entries = sealed_hook_entries(bool(rec.get("probe")))
    else:
        return None, None
    starts = [e for e in entries if e[0] == "SessionStart" and not any(_is_script(e[2], a["command"]) or
                                                                       ends_with_script(e[2], a["command"]) for a in allowed)]
    loader = tuple(rec["loader"]) if rec.get("loader") else (starts[0] if len(starts) == 1 else None)
    return entries, (loader, starts)


def _start_outputs(ctx, n):
    """Turn n's non-empty SessionStart outputs from its stream: an empty one injects nothing."""
    return [h for h in _stream_hooks(ctx, n) if h.get("subtype") == "hook_response" and h.get("event") == "SessionStart"
            and (h.get("stdout") or "")]


def _classify(ctx, n, out, loader):
    """("loader" | "named" | None, why) for one SessionStart output of turn n."""
    who, how = attribute(ctx, n, "SessionStart", out.get("stdout") or "", out.get("name"))
    if not who:
        digest = hashlib.sha256((out.get("stdout") or "").encode("utf-8")).hexdigest()[:12]
        return None, f"turn {n}: SessionStart output {digest}… is {how}"
    if loader and _is_script(who, loader[2]):
        return "loader", ""
    source = (out.get("name") or "").partition(":")[2]
    for a in manifest().get("session_start_allowed") or []:
        if ends_with_script(who, a["command"]):
            if not re.fullmatch(a.get("matcher") or ".*", source):
                return None, f"turn {n}: {a['name']} on a {source or 'unknown'} start (matcher {a.get('matcher')!r})"
            if not re.search(a["text_re"], hook_text(out.get("output") or out.get("stdout"))):
                return None, f"turn {n}: {a['name']}'s text does not match {a['text_re']!r}"
            return "named", ""
    return None, f"turn {n}: a SessionStart output from {who}, not the loader or a named entry"


@kind("layer")
def _layer(args, ctx, snap):
    """Automatic: each turn has exactly one loader output (attributed to the loader's script through the hook log; for
    a run recorded before the sealed config, the one output opening with the loader's header line), its header names
    the seat's model, its body is the launch hash; any other SessionStart output must be a named entry's."""
    rec = ctx["record"]
    entries, found = case_hooks(ctx)
    expected, seat = rec.get("layer_expected"), rec.get("seat")
    if entries is not None and found[0] is None:
        return False, f"the hook set has {len(found[1])} unnamed SessionStart entries: no single loader"
    problems = []
    for n in range(1, len(rec["turns"]) + 1):
        outs = _start_outputs(ctx, n)
        if entries is None:
            mine = [o for o in outs if LAYER_HEADER_RE.match((o.get("stdout") or "").split("\n", 1)[0])]
        else:
            kinds = [(o, _classify(ctx, n, o, found[0])) for o in outs]
            mine = [o for o, (k, _) in kinds if k == "loader"]
            problems += [why for o, (k, why) in kinds if k is None]
        if len(mine) != 1:
            problems.append(f"turn {n}: {len(mine)} loader output(s)")
            continue
        text = mine[0].get("stdout") or ""
        header = text.split("\n", 1)[0].rstrip()
        if expected:
            if header != expected["header"]:
                problems.append(f"turn {n}: header {header!r}, not {expected['header']!r}")
            elif hashlib.sha256(text.encode("utf-8")).hexdigest() != expected["sha256"]:
                problems.append(f"turn {n}: the layer's body differs from {expected['file']} at launch")
        else:
            m = LAYER_HEADER_RE.match(header)
            want = resolve_models([seat_model(seat)])[0]["header"] if seat in SEATS else None
            if not m or m.group(1) != want:
                problems.append(f"turn {n}: header {header!r} does not name {want} (seat {seat})")
    return not problems, "; ".join(problems)[:400] if problems else f"{len(rec['turns'])} turn(s): the {seat} layer"


@kind("sealed")
def _sealed(args, ctx, snap):
    """Automatic: the case ran in the sealed environment — only reviewed built-in plugins, no MCP server, no skill
    beyond the pack's, the fixture's and the reviewed bundled ones, nothing from the claude.ai account, the reviewed
    CLI, a hook log with one line per run, and no SessionStart output but the loader's and the named entries'. A
    --probe case (E-1) meets unreviewed fields as `unreviewed`, never as a pass."""
    rec = ctx["record"]
    if "hooks" not in rec and "env" not in rec:
        return None, "n/a — recorded before the sealed config"
    # a regrade judges a run by the review it ran under (regrade_all passes evals/sealed.json's REVIEWED fields at the
    # case's recorded pack head); a fresh grade and a recheck by today's
    m, fails, stale = dict(manifest(), **(ctx.get("reviewed") or {})), [], []
    fails += manifest_problems(m)
    entries, found = case_hooks(ctx)
    if found[0] is None or len(found[1]) != 1:
        fails.append(f"the hook set has {len(found[1])} unnamed SessionStart entries (one loader is required)")
    records, bad = _hook_log(ctx)
    if records is None:
        fails.append("no hook log")
    fails += bad
    pack, fixture = set(m.get("skills") or []), set((ctx.get("scenario") or {}).get("fixture_skills") or [])
    bundled, builtins = set(m.get("bundled_skills") or []), set(m.get("builtin_plugins") or [])
    for field in REVIEWED:
        if not m.get(field):
            stale.append(field)                                       # unfilled: only E-1 may meet it
    for n, turn in enumerate(rec["turns"], 1):
        init = turn.get("init")
        if not init:
            continue
        plugins, skills, servers = init.get("plugins") or [], init.get("skills") or [], init.get("mcp_servers") or []
        acct_p, acct_s, acct_c = account_entries(init)
        if acct_p or acct_s or acct_c:
            fails.append(f"from the claude.ai account: {len(acct_p)} plugin(s) {', '.join(acct_p)}, {len(acct_s)} "
                         f"skill(s) {', '.join(acct_s)}, {len(acct_c)} connector(s) {', '.join(acct_c)}")
        foreign = [p["name"] for p in plugins if p.get("path") != "builtin" and not str(p.get("source", "")).endswith("@synced")]
        if foreign:
            fails.append(f"turn {n}: plugin(s) not built in: {', '.join(foreign)}")
        unlisted = [p["source"] for p in plugins if p.get("path") == "builtin" and p.get("source") not in builtins]
        if unlisted and "builtin_plugins" not in stale:
            (stale.append("builtin_plugins") if rec.get("probe") else
             fails.append(f"turn {n}: built-in plugin(s) not reviewed: {', '.join(unlisted)}"))
        if servers:
            fails.append(f"turn {n}: MCP server(s) present: {', '.join(x.get('name', '?') for x in servers)}")
        extra = sorted(set(skills) - pack - fixture - bundled - set(acct_s))
        if extra and "bundled_skills" not in stale:
            (stale.append("bundled_skills") if rec.get("probe") else
             fails.append(f"turn {n}: skill(s) beyond the pack's, the fixture's and the bundled ones: {', '.join(extra)}"))
        version = init.get("claude_code_version")
        if m.get("cli_version") and version != m["cli_version"] and "cli_version" not in stale:
            (stale.append("cli_version") if rec.get("probe") else
             fails.append(f"turn {n}: CLI {version}, not the reviewed {m['cli_version']}"))
    for n in range(1, len(rec["turns"]) + 1):
        outs = _start_outputs(ctx, n)
        fails += [why for o in outs for k, why in [_classify(ctx, n, o, found[0])] if k is None]
        digests = collections.Counter(hashlib.sha256((o.get("stdout") or "").encode("utf-8")).hexdigest() for o in outs)
        logged = collections.Counter((r.get("stdout_sha256"), r.get("script")) for r in records or []
                                     if r.get("turn") == n and r.get("event") == "SessionStart")
        for (digest, script), count in logged.items():
            if count > digests.get(digest, 0) and digest != hashlib.sha256(b"").hexdigest():
                fails.append(f"turn {n}: duplicate hook-log line for {script} ({count} lines, "
                             f"{digests.get(digest, 0)} output(s))")
    if fails:
        return False, "; ".join(dict.fromkeys(fails))[:600]
    if stale:
        if not rec.get("probe"):
            return False, f"the manifest's reviewed fields are unfilled or stale: {', '.join(f for f in REVIEWED if f in stale)}"
        return None, f"unreviewed: {', '.join(f for f in REVIEWED if f in stale)}"
    return True, f"{len(rec['turns'])} turn(s) in the sealed environment"


AUTOMATIC = ("layer", "sealed", "receipts_after_eli5")


def automatic_checks(case_dir, rec):
    """The automatic checks grade() appends: `layer` to every run with streams (a recorded run's too); `sealed` and
    `receipts_after_eli5` to the cases R-22's runner recorded (a record with `env`), §5.9."""
    names = ["layer"] if "env" in rec or any(Path(case_dir).glob("turn*.jsonl")) else []
    return names + (["sealed", "receipts_after_eli5"] if "env" in rec else [])


_CLOSEOUT = {}


def closeout_module():
    """The pack under test's closeout.py, imported from its own file (in either layout), for the parsers and the
    regexes the grader shares with it (one home for shared code)."""
    path = pack_path("closeout/closeout.py")
    if path not in _CLOSEOUT:
        spec = importlib.util.spec_from_file_location("closeout_under_test", path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        _CLOSEOUT[path] = mod
    return _CLOSEOUT[path]


def grader_env(state):
    """The environment of every closeout.py call the grader makes: the given state folder and the pack under test's
    closeout.py, so a verdict never reads the user's live state or another copy of the helper."""
    return dict(os.environ, DOC_GUARDRAILS_STATE=str(state), CLOSEOUT_PY=str(pack_path("closeout/closeout.py")),
                GIT_OPTIONAL_LOCKS="0")


PROBE_DOC = "knowledge/99-probe-spec.md"


@kind("gate_fires")
def _gate_fires(args, ctx, snap):
    """Can a commit reach the gate at all: on a throwaway copy of the project (links kept), stage an unlinked spec and
    run `git hook run pre-commit` with the copy's own config (core.hooksPath and config hooks count); the gate fired
    when its verdict line for the probe appears. The graded project is never touched."""
    expected = bool(args[0])
    with tempfile.TemporaryDirectory() as tmp:
        copy = Path(tmp) / "project"
        shutil.copytree(ctx["proj"], copy, symlinks=True)
        probe = copy / PROBE_DOC
        probe.parent.mkdir(parents=True, exist_ok=True)
        probe.write_text("# Probe spec\n\nWritten by the evals' gate probe; it names no roadmap item.\n", encoding="utf-8")
        subprocess.run(["git", "-C", str(copy), "add", "-f", PROBE_DOC], check=True, capture_output=True)
        r = subprocess.run(["git", "-C", str(copy), "hook", "run", "pre-commit"], capture_output=True, encoding="utf-8",
                           errors="replace", timeout=120, env=grader_env(Path(tmp) / "state"))
        out = (r.stdout or "") + (r.stderr or "")
    verdicts = [m for m in closeout_module().GATE_VERDICT_RE.finditer(out) if m.group("where") == PROBE_DOC]
    fired = bool(verdicts)
    detail = (f"the gate judged the probe ({verdicts[0].group('level')})" if fired else
              f"no verdict for the probe (exit {r.returncode}): {out.strip().splitlines()[-1][:120] if out.strip() else 'no output'}")
    return fired == expected, detail


@kind("gate_ran")
def _gate_ran(args, ctx, snap):
    """Did the gate run on the session's commit: closeout.py facts -> gate.ran_on_last_commit, read with the case's
    own state folder (a heartbeat whose base and staged documents are exactly that commit's). A session that made no
    commit passes only when a heartbeat shows the gate refusing one on the current HEAD."""
    proj = Path(ctx["proj"])
    state = Path(ctx["case_dir"]) / "state"
    r = subprocess.run([sys.executable, str(pack_path("closeout/closeout.py")), "facts", str(proj)],
                       capture_output=True, encoding="utf-8", errors="replace", timeout=120, env=grader_env(state))
    try:
        gate = json.loads(r.stdout)["gate"] or {}
    except (ValueError, KeyError, TypeError):
        return False, "facts gave no gate"
    if _session_commits(proj) > 0:
        return gate.get("ran_on_last_commit") is True, gate.get("note") or "ran on the session's last commit"
    head = subprocess.run(["git", "-C", str(proj), "rev-parse", "HEAD"], capture_output=True, encoding="utf-8",
                          errors="replace", env=READ_ONLY_GIT).stdout.strip()
    refused = []
    for p in sorted(state.glob("gates/*/*-*.json")):
        try:
            h = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if h.get("result") == "refused" and h.get("head_before") == head and \
                os.path.realpath(h.get("worktree", "")) == os.path.realpath(proj):
            refused.append(h)
    return bool(refused), ("the gate refused a commit on the current HEAD" if refused else
                           "no commit by the session and no refused run of the gate")


@kind("facts_list_nonempty")
def _facts_list_nonempty(args, ctx, snap):
    """The `closeout.py facts` field at the key path is non-empty; a number indexes a list (`pins 1 exists`: facts
    sees a second pin and it exists, R-31's two-lane flow), and a path past the end of a list is no such field."""
    r = subprocess.run([sys.executable, str(pack_path("closeout/closeout.py")), "facts", str(ctx["proj"])],
                       capture_output=True, encoding="utf-8", errors="replace", timeout=120,
                       env=grader_env(Path(ctx["case_dir"]) / "state"))   # the state the session ran with
    try:
        val = json.loads(r.stdout)
        for key in args:
            val = val[key]
    except (ValueError, KeyError, TypeError, IndexError):
        return False, "facts gave no such field"
    return bool(val), f"{'.'.join(str(a) for a in args)}={val}"


# ---- hooks: what fired, which script printed it, on which turn (§5.2, §5.7)

def hook_text(output):
    """The text of a hook output: a JSON object's `hookSpecificOutput.additionalContext` (the shape skill_fresh.py and
    the plugins print), else the raw text. Never the raw JSON, where a — may be written \\u2014."""
    try:
        d = json.loads(output or "")
    except ValueError:
        return output or ""
    spec = d.get("hookSpecificOutput") if isinstance(d, dict) else None
    ctx = spec.get("additionalContext") if isinstance(spec, dict) else None
    return ctx if isinstance(ctx, str) else output


def ends_with_script(command, suffix):
    """The suffix rule (§5.3), for arg-less entries: the command is the script, or ends with `/<suffix>` or
    ` <suffix>`; an argument after the script means it is not that entry."""
    command = (command or "").strip()
    return command == suffix or command.endswith("/" + suffix) or command.endswith(" " + suffix)


def script_of(command):
    """The script a recorded hook command runs: the hook-log runner's `--script`, else the command's last word."""
    words = (command or "").split()
    if "--script" in words and words.index("--script") + 1 < len(words):
        return words[words.index("--script") + 1]
    return words[-1] if words else ""


def _transcript(ctx):
    if "transcript" not in ctx:
        ctx["transcript"] = parse_transcript(ctx["case_dir"]) if ctx.get("case_dir") else []
    return ctx["transcript"]


def _hook_log(ctx):
    """(records or None, problems), read once per case."""
    if "hook_log" not in ctx:
        ctx["hook_log"] = read_hook_log(ctx["case_dir"], ctx["record"]) if ctx.get("case_dir") else (None, [])
    return ctx["hook_log"]


def _prompt_matches(event, prompt):
    """A transcript prompt record is the one run_case sent: the same text, or a typed command by its <command-name>."""
    if prompt is None:
        return True
    if prompt.startswith("/"):
        m = TYPED_RE.search(event["text"])
        return bool(m) and m.group(1) == prompt[1:].split()[0]
    return event["text"].strip() == prompt.strip()


def transcript_parts(ctx):
    """The case transcript split into turns at each turn's prompt record (found after the previous one), each part
    widened back to just after the previous turn's last stop_hook_summary, so a hook record written before its prompt
    record belongs to the turn it served. Part 1 opens at the file's start."""
    if "transcript_parts" in ctx:
        return ctx["transcript_parts"]
    events, turns = _transcript(ctx), ctx["record"]["turns"]
    starts, last = [], -1
    for n, turn in enumerate(turns, 1):
        idx = next((i for i in range(last + 1, len(events)) if events[i]["kind"] == "prompt"
                    and _prompt_matches(events[i], turn.get("prompt"))), None)
        if idx is None:
            starts.append(None)
            continue
        start = 0 if n == 1 else idx
        if n > 1:
            summaries = [i for i in range(last + 1, idx) if events[i]["kind"] == "stop_summary"]
            start = summaries[-1] + 1 if summaries else idx
        starts.append(start)
        last = idx
    parts = []
    for n, start in enumerate(starts):
        end = next((s for s in starts[n + 1:] if s is not None), len(events))
        parts.append([] if start is None else events[start:end])
    ctx["transcript_parts"] = parts
    return parts


def _stream_hooks(ctx, n):
    """Turn n's stream hook events: kept in the record, or read again from turn<n>.jsonl for a record written before."""
    turn = ctx["record"]["turns"][n - 1]
    if "hooks" in turn:
        return turn["hooks"]
    f = Path(ctx.get("case_dir") or ".") / f"turn{n}.jsonl"
    return parse_stream(f.read_text(encoding="utf-8", errors="replace"))["hooks"] if f.is_file() else []


def attribute(ctx, n, event, stdout, name=None):
    """(script, how) for a stream output of turn n on `event`: the hook-log line of that turn and event whose
    stdout_sha256 is the output's digest; else a transcript attachment of that turn with the same hookName and stdout,
    by its recorded command. (None, why) when neither attributes it: never matched loosely."""
    records, _ = _hook_log(ctx)
    digest = hashlib.sha256((stdout or "").encode("utf-8")).hexdigest()
    scripts = sorted({r["script"] for r in records or [] if r.get("turn") == n and r.get("event") == event
                      and r.get("stdout_sha256") == digest})
    if len(scripts) == 1:
        return scripts[0], "log"
    parts = transcript_parts(ctx)
    for e in parts[n - 1] if n - 1 < len(parts) else []:
        if e["kind"] == "hook" and e["event"] == event and e.get("stdout") == stdout and e.get("command") \
                and (name is None or e.get("name") == name):
            return script_of(e["command"]), "transcript"
    return None, (f"ambiguous: {len(scripts)} scripts logged this output" if scripts else "unattributed")


def _hook_args(args):
    """(event, text, outcome, script, turn) from `event, text, [outcome], [script], [{"turn": n}]`; anything else is a
    malformed check, which fails."""
    args, turn = list(args), None
    if args and isinstance(args[-1], dict):
        scope = args.pop()
        if set(scope) != {"turn"} or not isinstance(scope["turn"], int):
            raise ValueError(f"malformed hook_fired: the scope is {{\"turn\": <n>}}, not {scope}")
        turn = scope["turn"]
    if not 2 <= len(args) <= 4:
        raise ValueError(f"malformed hook_fired: {len(args)} argument(s) before the scope")
    outcome = args[2] if len(args) > 2 else "block"
    if outcome not in ("block", "ran"):
        raise ValueError(f"malformed hook_fired: outcome {outcome!r}")
    script = args[3] if len(args) > 3 else None
    if script is not None and outcome != "block":
        raise ValueError("malformed hook_fired: the script filter is block's")
    return args[0], args[1], outcome, script, turn


@kind("hook_fired")
def _hook_fired(args, ctx, snap):
    event, text, outcome, script, only = _hook_args(args)
    turns = range(1, len(ctx["record"]["turns"]) + 1)
    turns = [n for n in turns if n == only] if only else list(turns)
    parts = transcript_parts(ctx) if only else [_transcript(ctx)]
    tx = [e for n in turns for e in (parts[n - 1] if n - 1 < len(parts) else [])] if only else parts[0]
    scope = f" on turn {only}" if only else ""
    if outcome == "ran":   # a hook of `event` ran whose script ends with `text`: the log, or the transcript's command
        records, _ = _hook_log(ctx)
        if any(r.get("event") == event and ends_with_script(r.get("script"), text) and (not only or r.get("turn") == only)
               for r in records or []):
            return True, f"ran{scope}: the hook log names {text}"
        commands = [c for e in tx if e["kind"] == "stop_summary" and event == "Stop" for c in e["commands"]]
        commands += [e["command"] for e in tx if e["kind"] == "hook" and e["event"] == event and e.get("command")]
        if any(ends_with_script(c, text) for c in commands):
            return True, f"ran{scope}: the transcript records {text}"
        return False, f"no {event} run of {text}{scope} in the hook log or the transcript"
    for n in turns:
        for h in _stream_hooks(ctx, n):
            if h.get("subtype") != "hook_response" or h.get("event") != event:
                continue
            if text not in hook_text(h.get("output") if h.get("output") is not None else h.get("stdout")):
                continue
            if not script:
                return True, f"{event} output on turn {n}"
            who, how = attribute(ctx, n, event, h.get("stdout") or "", h.get("name"))
            if who and ends_with_script(who, script):
                return True, f"{event} output on turn {n}, attributed to {script} ({how})"
    for e in tx:
        if e["kind"] == "hook" and e["event"] == event and text in hook_text(e.get("text")):
            if not script or ends_with_script(script_of(e.get("command")), script):
                return True, f"{event} {e['type']} in the transcript{scope}"
    return False, f"no {event} hook output with {text!r}{scope}" + (f" attributed to {script}" if script else "")


# ---- read_file (R-28 § 5.7): a full Read of a project file, judged on the result

def _turn_scope(args, kind_name):
    """(args without the scope, turn or None): the scope is {"turn": n}, optional and last."""
    args, turn = list(args), None
    if args and isinstance(args[-1], dict):
        scope = args.pop()
        if set(scope) != {"turn"} or not isinstance(scope["turn"], int):
            raise ValueError(f"malformed {kind_name}: the scope is {{\"turn\": <n>}}, not {scope}")
        turn = scope["turn"]
    return args, turn


def full_reads(case_dir, n):
    """The file paths turn n's stream shows read in full: a Read tool_use whose result record (the `user` record
    carrying its tool_use_id) has a top-level tool_use_result.file with startLine 1 and numLines == totalLines. The
    input's offset and limit decide nothing."""
    path = Path(case_dir) / f"turn{n}.jsonl"
    if not path.is_file():
        return []
    reads, full = {}, []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            d = json.loads(line)
        except ValueError:
            continue
        if not isinstance(d, dict):
            continue
        content = (d.get("message") or {}).get("content")
        items = content if isinstance(content, list) else []
        if d.get("type") == "assistant":
            for c in items:
                if isinstance(c, dict) and c.get("type") == "tool_use" and c.get("name") == "Read":
                    reads[c.get("id")] = str((c.get("input") or {}).get("file_path") or "")
        elif d.get("type") == "user":
            f = (d.get("tool_use_result") or {}).get("file") if isinstance(d.get("tool_use_result"), dict) else None
            for c in items:
                if isinstance(c, dict) and c.get("type") == "tool_result" and c.get("tool_use_id") in reads \
                        and isinstance(f, dict) and f.get("startLine") == 1 and f.get("numLines") == f.get("totalLines"):
                    full.append(reads[c["tool_use_id"]])
    return full


@kind("read_file")
def _read_file(args, ctx, snap):
    """`[path, {"turn": n}]`: the turn (every turn without the scope) read the project file in full with Read."""
    rest, only = _turn_scope(args, "read_file")
    if len(rest) != 1:
        raise ValueError(f"malformed read_file: {len(rest)} argument(s) before the scope")
    target = rest[0].lstrip("/")
    turns = [only] if only else range(1, len(ctx["record"]["turns"]) + 1)
    for n in turns:
        if any(p.endswith("/" + target) for p in full_reads(ctx["case_dir"], n)):
            return True, f"turn {n} read {target} in full"
    return False, f"no full Read of {target}" + (f" on turn {only}" if only else "")


# ---- subagent_marker (R-27 test 9): the brief hook's block in what each agent started with

AGENT_TOOLS = {"Agent": ("Agent", "Task")}   # the init lists the Agent tool under its older name, Task (2.1.28x)
START_EVENT = "SubagentStart"                # the event whose additionalContext an agent starts with


def _turn_tools(ctx, n):
    """The tool names turn n's init record lists (its stream, turn<n>.jsonl), or None when the stream has none."""
    path = Path(ctx["case_dir"]) / f"turn{n}.jsonl"
    if not path.is_file():
        return None
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            d = json.loads(line)
        except ValueError:
            continue
        if isinstance(d, dict) and d.get("type") == "system" and d.get("subtype") == "init":
            return d.get("tools") if isinstance(d.get("tools"), list) else None
    return None


def _start_context(d):
    """The texts a transcript record carries as SubagentStart hook context: Claude Code 2.1.283 writes the hooks'
    additionalContext into the agent's transcript as one attachment record, {type: hook_additional_context, content:
    [texts], hookEvent: SubagentStart}, before the agent's first answer (evals/known/shapes/
    subagent_start_context_2_1_283.json); [] for any other record."""
    att = d.get("attachment") if d.get("type") == "attachment" else None
    if not isinstance(att, dict) or att.get("type") != "hook_additional_context" or att.get("hookEvent") != START_EVENT:
        return []
    content = att.get("content")
    return [c for c in content if isinstance(c, str)] if isinstance(content, list) else []


def agent_briefs(case_dir):
    """[(transcript path, what the agent started with, its first message's time)] for every agent transcript
    run_case copied beside the session's (`transcripts/<sid>/…`, any depth; tool results aside). What it started with:
    its first user message (the brief its caller wrote) and the SubagentStart hook context recorded before its first
    answer, joined by newlines."""
    root, out = Path(case_dir) / "transcripts", []
    for top in sorted(p for p in root.iterdir() if p.is_dir()) if root.is_dir() else []:
        for folder, dirs, files in os.walk(top):
            dirs[:] = sorted(d for d in dirs if d != "tool-results")
            for name in sorted(f for f in files if f.endswith(".jsonl")):
                path = Path(folder) / name
                first, when, context = None, None, []
                for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
                    try:
                        d = json.loads(line)
                    except ValueError:
                        continue
                    if not isinstance(d, dict):
                        continue
                    if d.get("type") == "assistant":
                        break
                    context += _start_context(d)
                    if first is None and d.get("type") == "user" and not d.get("isMeta"):
                        first, when = _content_text((d.get("message") or {}).get("content")), d.get("timestamp")
                if first is not None:
                    out.append((path, "\n".join([first] + context), when))
    return out


@kind("subagent_marker")
def _subagent_marker(args, ctx, snap):
    """[turn, tool, marker]: the turn called `tool` and every agent it started (an agent transcript whose first
    message falls inside the turn) started with `marker`, in its first message or in the SubagentStart hook context
    recorded before its first answer (where the brief hook's block lands): the block reached it. n/a when
    the turn's init lists no such tool (a seat without the Workflow tool), reported; never a pass on no agent."""
    n, tool, marker = args
    names = AGENT_TOOLS.get(tool, (tool,))
    tools = _turn_tools(ctx, n)
    if tools is not None and not set(names) & set(tools):
        return None, f"n/a — no {tool} tool"
    parts = transcript_parts(ctx)
    calls = [e for e in (parts[n - 1] if n - 1 < len(parts) else []) if e["kind"] == "tool_use" and e["name"] in names]
    if not calls:
        return False, f"no {tool} call on turn {n}"
    agents = [(p, text) for p, text, when in agent_briefs(ctx["case_dir"])
              if when and turn_at(ctx["record"], when) == n]
    if not agents:
        return False, f"no agent transcript started on turn {n} ({len(calls)} {tool} call(s))"
    missing = [p.stem for p, text in agents if marker not in text]
    if missing:
        return False, f"turn {n}: {len(missing)} of {len(agents)} agent(s) without the marker: {', '.join(missing)}"
    return True, f"turn {n}: {len(agents)} agent(s), each started with the marker"


# ---- violation generators (§5.1, §5.2): violate(args, run, turn) → a known run that must fail the check, built from
# one that passes it (the builder, evals/selftest.py, runs the ops); None where the scenario must declare one

def _last(run, turn):
    """The turn (0-based) a violation's ops go into: the given 1-based turn, else the last."""
    return (turn or len(run["turns"])) - 1


def _with_ops(run, turn, *ops):
    run = json.loads(json.dumps(run))
    t = run["turns"][_last(run, turn)]
    t["ops"] = list(t.get("ops") or []) + [list(o) for o in ops]
    return run


def _literal_alternative(pattern):
    """The regex's first top-level alternative when it is a plain literal (no regex syntax), else None."""
    depth, start, alts = 0, 0, []
    for i, ch in enumerate(pattern):
        if ch in "([":
            depth += 1
        elif ch in ")]":
            depth -= 1
        elif ch == "|" and depth == 0:
            alts.append(pattern[start:i])
            start = i + 1
    alts.append(pattern[start:])
    first = alts[0]
    return first if first and not re.search(r"[.^$*+?{}\[\]\\|()]", first) else None


def _v_drop_skill(args, run, turn):
    run = json.loads(json.dumps(run))
    for t in run["turns"]:
        for key in ("skills", "typed"):
            keep = [s for s in t.get(key) or [] if not (s == args[0] or s.endswith(":" + args[0]))]
            t[key] = keep
    return run   # when nothing matched, the run already fails the check as it is


def _v_add_skill(args, run, turn):
    run = json.loads(json.dumps(run))
    t = run["turns"][_last(run, turn)]
    t["skills"] = list(t.get("skills") or []) + [args[0]]
    return run


def _v_changed(args, run, turn):   # restore the base: drop every op that touched the path
    run = json.loads(json.dumps(run))
    for t in run["turns"]:
        keep = [o for o in t.get("ops") or [] if not (len(o) > 1 and o[0] in ("write", "append", "sub", "rm")
                                                      and _under({o[1]: 1}, args[0]))]
        t["ops"] = keep
    return run   # when nothing matched, the run already fails the check as it is


def _v_drop_writes(args, run, turn):
    run = json.loads(json.dumps(run))
    for t in run["turns"]:
        keep = [w for w in t.get("writes") or [] if not w.endswith(args[0])]
        t["writes"] = keep
    return run   # when nothing matched, the run already fails the check as it is


def _v_drop_bash(args, run, turn):
    run = json.loads(json.dumps(run))
    for t in run["turns"]:
        keep = [b for b in t.get("bash") or [] if not re.search(args[0], b["cmd"])]
        t["bash"] = keep
    return run   # when nothing matched, the run already fails the check as it is


def _v_more_bash(args, run, turn):   # the matching command n+1 times
    found = [b for t in run["turns"] for b in t.get("bash") or [] if re.search(args[0], b["cmd"])]
    if not found:
        return None
    run = json.loads(json.dumps(run))
    t = run["turns"][_last(run, turn)]
    t["bash"] = list(t.get("bash") or []) + [dict(found[0]) for _ in range(args[1] + 1)]
    return run


def _v_literal_bash(args, run, turn):
    lit = _literal_alternative(args[0])
    if lit is None:
        return None
    run = json.loads(json.dumps(run))
    t = run["turns"][_last(run, turn)]
    t["bash"] = list(t.get("bash") or []) + [{"cmd": lit, "out": ""}]
    return run


def _v_final(args, run, turn):
    run = json.loads(json.dumps(run))
    t = run["turns"][args[0] - 1]
    t["final"] = re.sub(args[1], "", t.get("final") or "")
    return run


def _v_blank_output(args, run, turn):
    run = json.loads(json.dumps(run))
    for t in run["turns"]:
        for b in t.get("bash") or []:
            if re.search(args[0], b["cmd"]):
                b["out"] = ""
    return run   # when nothing matched, the run already fails the check as it is


def _v_hook(args, run, turn):
    """block: drop the matching hook (with a script, only its); ran: drop its log line, its stop summary entry and
    its attachment; with a turn scope, only in that turn."""
    event, text, outcome, script, only = _hook_args(args)
    run = json.loads(json.dumps(run))
    for n, t in enumerate(run["turns"], 1):
        if only and n != only:
            continue
        t["hooks"] = [h for h in t.get("hooks") or [] if not (
            h.get("event") == event and (text in (h.get("text") or "") if outcome == "block"
                                         else ends_with_script(script_of(h.get("command")), text))
            and (not script or ends_with_script(script_of(h.get("command")), script)))]
        if outcome == "ran":
            t["omit_scripts"] = list(t.get("omit_scripts") or []) + [text]
    return run


def _v_read_file(args, run, turn):
    """Every matching Read (and its result) dropped from the scoped turn's run, or from every turn."""
    rest, only = _turn_scope(args, "read_file")
    run = json.loads(json.dumps(run))
    for n, t in enumerate(run["turns"], 1):
        if not only or n == only:
            t["reads"] = [r for r in t.get("reads") or [] if not r["path"].endswith(rest[0].lstrip("/"))]
    return run


def _v_receipt(args, run, turn):
    run = json.loads(json.dumps(run))
    t = run["turns"][args[0] - 1]
    if "ELI5 v5" not in (t.get("final") or ""):
        return None
    t["final"] = t["final"].replace("ELI5 v5", "ELI5 v4")
    return run


def _v_subagent_marker(args, run, turn):
    """The marker taken out of what every agent the known run starts on the check's turn started with: its first
    message and its SubagentStart context."""
    run = json.loads(json.dumps(run))
    agents = run["turns"][args[0] - 1].get("agents") or []
    if not agents:
        return None
    for a in agents:
        a["first"] = a["first"].replace(args[2], "")
        if a.get("context"):
            a["context"] = [c.replace(args[2], "") for c in a["context"]]
    return run


def _v_layer(args, run, turn):
    run = json.loads(json.dumps(run))
    run["layer"] = "# Working notes (injected: this session runs Claude Opus 5)\n\n(a violation)\n"
    return run


def _v_sealed(args, run, turn):
    run = json.loads(json.dumps(run))
    run["init_patch"] = {"mcp_servers": [{"name": "github", "status": "connected", "source": "user"}]}
    return run


def _v_no_receipts(args, run, turn):
    prompts = run.get("_prompts") or []
    first = next((n for n, p in enumerate(prompts, 1) if (p or "").split()[:1] == ["/eli5"]), None)
    if first is None or first >= len(run["turns"]):
        return None
    run = json.loads(json.dumps(run))
    for t in run["turns"][first:]:
        t["final"] = "Done."
    return run


VIOLATE = {
    "skill_invoked": _v_drop_skill, "skill_not_invoked": _v_add_skill,
    "unchanged": lambda a, r, t: _with_ops(r, t, ["append_under", a[0], "zz violation"]),
    "changed": _v_changed,
    "no_removed_lines": lambda a, r, t: _with_ops(r, t, ["del_line", a[0], a[1] if len(a) > 1 else ""]),
    "no_new_code": lambda a, r, t: _with_ops(r, t, ["write", "/".join(x for x in (a[0].rstrip("/"), "zz_violation.py") if x),
                                                   "VIOLATION = 1\n"]),
    "only_changed": lambda a, r, t: _with_ops(r, t, ["write", "zz_outside.txt", "outside\n"]),
    "no_path": lambda a, r, t: _with_ops(r, t, ["write", f"{a[0].rstrip('/')}/zz_violation.md", "violation\n"]),
    "file_exists": lambda a, r, t: _with_ops(r, t, ["rm_glob", a[0]]),
    "file_matches": lambda a, r, t: _with_ops(r, t, ["resub", a[0], a[1], ""]),
    "file_not_matches": lambda a, r, t: (None if _literal_alternative(a[1]) is None
                                         else _with_ops(r, t, ["append_glob", a[0], _literal_alternative(a[1])])),
    "tests_pass": lambda a, r, t: _with_ops(r, t, ["fail_tests"]),
    "origin_empty": lambda a, r, t: _with_ops(r, t, ["push"]),
    "edited_by_tool": _v_drop_writes, "bash_matching": _v_drop_bash, "max_bash_matching": _v_more_bash,
    "no_bash_matching": _v_literal_bash, "final_matches": _v_final, "bash_output": _v_blank_output,
    "committed_file_matches": lambda a, r, t: _with_ops(r, t, ["resub", a[0], a[1], ""], ["git", "add", a[0]],
                                                        ["git", "commit", "--no-verify", "-qm", "violation"]),
    "commits_at_least": lambda a, r, t: _with_ops(r, t, ["reset_fixture"]),
    "commits_at_most": lambda a, r, t: _with_ops(r, t, *[o for i in range(a[0] + 1) for o in (
        ["write", f"zz_commit_{i}.txt", f"{i}\n"], ["git", "add", f"zz_commit_{i}.txt"],
        ["git", "commit", "--no-verify", "-qm", f"v{i}"])]),
    "tracked": lambda a, r, t: _with_ops(r, t, ["git", "rm", "--cached", "-q", a[0]]),
    "facts_list_nonempty": None, "holdout": None,
    "gate_ran": lambda a, r, t: _with_ops(r, t, ["git", "commit", "--allow-empty", "--no-verify", "-qm", "violation"]),
    "hook_fired": _v_hook,
    "gate_fires": lambda a, r, t: _with_ops(r, t, ["gate_exit_first"] if a[0] else ["install_gate"]),
    "receipt": _v_receipt,
    "record_status": lambda a, r, t: _with_ops(r, t, ["status", a[0], "zz-violation"]),
    "roadmap_status": lambda a, r, t: _with_ops(r, t, ["move_item", a[0], "zz-violation"]),
    "layer": _v_layer, "sealed": _v_sealed, "receipts_after_eli5": _v_no_receipts,
    "subagent_marker": _v_subagent_marker, "read_file": _v_read_file,
}
for _kind, _violate in VIOLATE.items():
    CHECKS[_kind] = CHECKS[_kind]._replace(violate=_violate)


def _violate_after_turn(args, run, turn):
    inner = args[1]
    fn = CHECKS[inner[0]].violate if inner[0] in CHECKS else COMBINATOR_VIOLATE.get(inner[0])
    return fn(inner[1:], run, args[0]) if fn else None


def _violate_any_of(args, run, turn):
    for inner in args:
        fn = CHECKS[inner[0]].violate if inner[0] in CHECKS else COMBINATOR_VIOLATE.get(inner[0])
        run = fn(inner[1:], run, turn) if fn and run is not None else None
    return run


COMBINATOR_VIOLATE = {"after_turn": _violate_after_turn, "any_of": _violate_any_of}


def violation(check, run):
    """The generated violation of one scenario check on a known run, or None when its kind has no generator."""
    fn = CHECKS[check[0]].violate if check[0] in CHECKS else COMBINATOR_VIOLATE.get(check[0])
    return fn(check[1:], run, None) if fn else None


def _after_turn(args, ctx, snap_index):
    n, inner = args[0], args[1]
    ok, detail = check(inner[0], inner[1:], ctx, snap_index=n)
    return ok, f"after turn {n}: {detail}"


def _any_of(args, ctx, snap_index):  # passes when at least one of the listed checks passes
    outcomes = [check(c[0], c[1:], ctx, snap_index) for c in args]
    return any(ok for ok, _ in outcomes), " | ".join(d for _, d in outcomes)


COMBINATORS = {"after_turn": _after_turn, "any_of": _any_of}   # checks made of checks; not kinds of their own


def check(name, args, ctx, snap_index=None):
    """(passed, detail). ctx: proj, case_dir, record. snap_index picks the state after that turn (None = the end).
    Every kind goes through CHECKS and every combinator through COMBINATORS; a name in neither raises KeyError, which
    grade() turns into a failed check."""
    if name in COMBINATORS:
        return COMBINATORS[name](args, ctx, snap_index)
    snaps = ctx["record"]["snapshots"]
    return CHECKS[name].fn(args, ctx, snaps[-1] if snap_index is None else snaps[snap_index])


def grade(case_dir, scenario, reviewed=None):
    """One case graded on its scenario's checks and the automatic ones. reviewed: the manifest's REVIEWED fields to
    judge `sealed` by (regrade_all's, as they stood at the case's recorded pack head); None for today's manifest."""
    rec = json.loads((case_dir / "record.json").read_text(encoding="utf-8"))
    api = next((t["api_error"] for t in rec["turns"] if t.get("api_error")), None)
    if not api:  # a record written before api_error was kept: read the streams again
        api = next((t["api_error"] for f in sorted(case_dir.glob("turn*.jsonl"))
                    for t in [parse_stream(f.read_text(encoding="utf-8", errors="replace"))] if t["api_error"]), None)
    cost, _, cost_error, cost_kind = case_cost(cost_turns(case_dir, rec))
    costed = {"cost": None if cost is None else round(cost, 2)}
    if cost_error:
        costed.update(cost_error=cost_error, cost_error_kind=cost_kind)
    if api:  # not a behaviour failure and not a pass: the case did not run
        return dict({"scenario": scenario["id"], "seat": rec["seat"], "passed": False, "not_run": api, "results": []},
                    **costed)
    ctx = {"proj": case_dir / "project", "case_dir": case_dir, "record": rec, "scenario": scenario,
           "reviewed": reviewed}
    results = []
    checks = [list(c) for c in scenario["checks"]] + [[name] for name in automatic_checks(case_dir, rec)]
    for c in checks:
        try:
            ok, detail = check(c[0], c[1:], ctx)
        except Exception as e:  # a broken check is a failed check, never a silent pass
            ok, detail = False, f"check raised {type(e).__name__}: {e}"
        results.append(dict({"check": c, "passed": ok, "detail": detail},
                            **({"automatic": True} if len(results) >= len(scenario["checks"]) else {})))
    # a check may be neither pass nor fail: `unreviewed` (E-1 before review) keeps the case from passing; `n/a` (a run
    # recorded before the sealed config) is reported and counts neither way
    unreviewed = [f"{r['check'][0]} — {str(r['detail'])[len('unreviewed: '):]}" for r in results
                  if r["passed"] is None and str(r["detail"]).startswith("unreviewed: ")]
    passed = all(r["passed"] is True or (r["passed"] is None and str(r["detail"]).startswith("n/a")) for r in results)
    extra = dict({"unreviewed": unreviewed} if unreviewed else {},
                 **({"not_completed": rec["not_completed"]} if rec.get("not_completed") else {}))
    return dict({"scenario": scenario["id"], "seat": rec["seat"], "passed": passed and not rec.get("not_completed"),
                 "results": results}, **costed, **extra)


# ------------------------------------------------------------------------------------------------ main

def counts(spec):
    """The suite's figures, the one place they are computed: R-29's `figures` reads `--counts`, under
    the names R-29 § 5.1 gives them. "check kinds" is the leaves of CHECKS; "check kinds used" those the scenarios'
    checks reach, through the combinators (COMBINATORS, which are not kinds); "automatic kinds" those grade() adds to
    a case (AUTOMATIC), which no scenario lists, so "checks" and "check-runs" never count them."""
    S = spec["scenarios"]

    def leaves(c):
        return ({k for sub in c[1:] for k in leaves(sub)} if c[0] == "any_of" else leaves(c[2]) if c[0] == "after_turn"
                else {c[0]})
    return {"scenarios": len(S), "graded sessions": sum(len(s["seats"]) for s in S),
            "checks": sum(len(s["checks"]) for s in S), "turns": sum(len(s["turns"]) * len(s["seats"]) for s in S),
            "check-runs": sum(len(s["checks"]) * len(s["seats"]) for s in S),
            "quick sessions": sum(len(s.get("quick") or []) for s in S),
            "known runs": sum(len(s.get("known") or []) for s in S), "check kinds": len(CHECKS),
            "check kinds used": len({k for s in S for c in s["checks"] for k in leaves(c)}),
            "combinators": len(COMBINATORS), "automatic kinds": len(AUTOMATIC)}


# ---- the run log (R-29 § 6): evals/RUNS.jsonl, one record per graded run, appended as each run is graded

RUNS_FILE = HERE / "RUNS.jsonl"
RUN_FIELDS = ("run", "date", "selection", "seats", "cases", "passed", "not_run", "result", "cost", "failed", "layer_ok",
              "pack_sha", "published", "note")
LISTED = ("full", "quick", "seat")          # the selections the evidence page's run table lists
COST_NOTE = "cost recomputed {date}: ${old:.2f} → ${new:.2f}, running totals from Claude Code 2.1.277"
SYNCED_NOTE = "sessions listed account-supplied entries (read from the recorded sessions): "


def totals_line(spec):
    """The dry run's last line: the figures --counts prints, from the same function."""
    c = counts(spec)
    return (f"totals: {c['scenarios']} scenarios, {c['graded sessions']} graded sessions, {c['turns']} turns, "
            f"{c['checks']} checks ({c['check-runs']} check-runs)")


def selection_of(args):
    return "only" if args.only else "quick" if args.quick else "seat" if args.seat else "full"


def read_runs(path=None):
    path = Path(path or RUNS_FILE)
    return [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines() if l.strip()] if path.is_file() else []


def _money(cost):
    return "cost unknown" if cost is None else f"${cost:.2f}"


def _run_cases(runs_root, record):
    """A recorded run's [scenario, seat] pairs, sorted, read from its own grades.json under the runs root; None when
    the folder or file is gone or unreadable."""
    try:
        grades = json.loads((Path(runs_root) / record["run"] / "grades.json").read_text(encoding="utf-8"))
        return sorted([g["scenario"], g["seat"]] for g in grades)
    except (OSError, ValueError, KeyError, TypeError):
        return None


def last_run_line(selection, path=None, cases=None, runs_root=None):
    """The dry run's last-run line. `only` names an ad-hoc set, so it is matched on that set's scenarios and seats (each
    run's own grades.json under runs_root), never on the kind alone; quick, seat and full are matched by kind."""
    last = [r for r in read_runs(path) if r.get("selection") == selection]
    if selection == "only" and cases is not None:
        want = sorted([s["id"], st] for s, st in cases)
        last = [r for r in last if _run_cases(runs_root or Path.home() / "xbt-evals", r) == want]
        if not last:
            return "last run of this selection: none recorded for these cases"
    if not last:
        return "last run of this selection: none recorded"
    r = last[-1]
    cost = _money(r.get("cost"))
    if r.get("cost") is None and isinstance(r.get("cost_estimated"), dict):
        cost = f"${r['cost_estimated']['cost']:.2f} (estimated)"
    return f"last run of this selection: {r.get('date')} {r.get('passed')}/{r.get('cases')} {cost} ({r.get('run')})"


def _stamp_date(name):
    return datetime.datetime.strptime(Path(name).name[-15:], "%Y%m%d-%H%M%S").date().isoformat()


def case_costs(run_dir, grades):
    """{case: (cost to the cent or None, error, kind)}: each case's cost by the corrected instrument, read from its own
    turn records (a case with no folder, one never launched, keeps its recorded cost)."""
    out = {}
    for g in grades:
        name = f"{g['scenario']}-{g['seat']}"
        case = Path(run_dir) / name
        if not (case / "record.json").is_file():
            out[name] = (g.get("cost"), None, None)
            continue
        rec = json.loads((case / "record.json").read_text(encoding="utf-8"))
        cost, _, error, kind = case_cost(cost_turns(case, rec))
        out[name] = (None if cost is None else round(cost, 2), error, kind)
    return out


def run_record(run_dir, runs_root, grades, selection, pack_sha=None, costs=None, note=""):
    """One RUNS.jsonl record: never an absolute path (`run` is relative to the runs root)."""
    costs = costs if costs is not None else case_costs(run_dir, grades)
    passed = sum(1 for g in grades if g.get("passed"))
    not_run = sum(1 for g in grades if g.get("not_run"))
    known = [c for c, _, _ in costs.values()]
    layer = [r["passed"] for g in grades for r in g.get("results") or [] if list(r.get("check") or []) == ["layer"]]
    return {"run": Path(run_dir).resolve().relative_to(Path(runs_root).resolve()).as_posix(),
            "date": _stamp_date(run_dir), "selection": selection, "seats": sorted({g["seat"] for g in grades}),
            "cases": len(grades), "passed": passed, "not_run": not_run, "result": f"{passed}/{len(grades)}",
            "cost": None if any(c is None for c in known) else round(sum(known), 2),
            "failed": [[g["scenario"], g["seat"]] for g in grades if not g.get("passed") and not g.get("not_run")],
            "layer_ok": (all(v is True for v in layer) if layer and None not in layer else None) if layer else None,
            "pack_sha": pack_sha, "published": selection in LISTED, "note": note}


def append_run(record, path=None):
    with open(Path(path or RUNS_FILE), "a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")


def log_run(out, runs_root, grades, selection, pack_sha=None):
    """The graded run's one record, appended to RUNS.jsonl (R-29 § 6)."""
    record = run_record(out, runs_root, grades, selection, pack_sha)
    append_run(record)
    return record




def _batch(run_dir):
    """A run of this copy keeps no batch record."""
    return {}


def derive_selection(run_dir, grades, repo=None):
    """What a recorded run ran, from its cases (a run recorded before the log kept its argv): the day's full or quick
    set (its batch's pack commit, else the scenarios committed at its stamp, less the scenarios the batch excluded),
    one seat's set, or one scenario (`only`). A run sized as the suite (11 cases or more, § 4.9 of R-29) whose day's
    committed scenarios lag it is `full`, or `seat` on one seat; any other is an `--only` run."""
    repo = Path(repo or PACK)
    cases = {(g["scenario"], g["seat"]) for g in grades}
    seats, scenarios = {s for _, s in cases}, {s for s, _ in cases}
    spec, excluded = None, set()
    b = _batch(run_dir)
    if b:
        excluded = {str(e).split(":")[0].strip() for e in b.get("excluded") or []}
        r = subprocess.run(["git", "-C", str(repo), "show", f"{b.get('pack_head')}:evals/scenarios.json"],
                           capture_output=True, encoding="utf-8", errors="replace")
        spec = json.loads(r.stdout) if r.returncode == 0 and b.get("pack_head") else None
    if spec is None:
        spec = scenarios_for(repo, Path(run_dir).name)[0]
    S = [s for s in spec["scenarios"] if s["id"] not in excluded]
    if cases == {(s["id"], st) for s in S for st in s["seats"]}:
        return "full"
    if cases == {(s["id"], st) for s in S for st in s.get("quick") or []}:
        return "quick"
    if len(scenarios) == 1:
        return "only"
    if len(seats) == 1:
        (seat,) = seats
        if cases == {(s["id"], seat) for s in S if seat in s["seats"] or SEAT_ALIAS.get(seat) in s["seats"]}:
            return "seat"
    if len(cases) >= 11:
        return "seat" if len(seats) == 1 else "full"
    return "only"


def _p0(regrade):
    """P0's hand-over: regrade.json, costs.md {run: (old, new)}, the runs synced.md names, the runs flips.md says the
    regrade graded."""
    regrade = Path(regrade)
    data = json.loads((regrade / "regrade.json").read_text(encoding="utf-8"))
    costs = {}
    for line in (regrade / "costs.md").read_text(encoding="utf-8").splitlines() if (regrade / "costs.md").is_file() else []:
        m = re.match(r"^(\S+) \$(\d+\.\d\d) → \$(\d+\.\d\d)$", line.strip())
        if m:
            costs[m.group(1)] = (float(m.group(2)), float(m.group(3)))
    synced = [l.split(" — ")[0].strip() for l in (regrade / "synced.md").read_text(encoding="utf-8").splitlines()
              if l.strip()] if (regrade / "synced.md").is_file() else []
    covered = set()
    if (regrade / "flips.md").is_file():
        covered = set(re.findall(r"^- (\S+): graded ", (regrade / "flips.md").read_text(encoding="utf-8"), re.M))
    return data, costs, synced, covered


PAGE_ROW_RE = re.compile(r"^\|\s*(\d{4}-\d{2}-\d{2})\s*\|\s*(\d+)[^|]*\|\s*\**\s*(\d+)\s*/\s*(\d+)\s*\**[^|]*\|(.*)\|\s*$")
SEAT_WORDS = {"fable": ("Fable",), "opus": ("Opus",), "opus55": ("Opus 5.5",)}


def page_rows(page):
    """The hand-written run table's rows: (date, cases, result, the whole row, its explanation cell)."""
    rows = []
    for line in Path(page).read_text(encoding="utf-8").splitlines():
        m = PAGE_ROW_RE.match(line)
        if m:
            rows.append((m.group(1), int(m.group(2)), f"{m.group(3)}/{m.group(4)}", line, m.group(5).strip()))
    return rows


def _names_failures(row, failed):
    """A row names a run's failures when each failing scenario id is in its text and each failing seat is named (or
    the row says both seats)."""
    if not all(s in row for s, _ in failed):
        return False
    return all("both seats" in row or any(w in row for w in SEAT_WORDS.get(seat, (seat,))) for _, seat in failed)


def backfill(runs_root, runs_file=None, regrade=None, page=None, repo=None, today=None):
    """Write RUNS.jsonl from every graded run under the root (grades.json one or two levels down), in run-time order,
    idempotent by `run`: costs by the corrected instrument (the changed runs must equal P0's costs.md), P0's flips
    applied (C42), the account-supplied counts of the runs synced.md names, and each hand-written row's explanation
    from the page (C28). Each run's result is its own first grades: a re-check's result is noted, never applied. Any
    disagreement stops it, naming what, and nothing is written. 0 or 1."""
    runs_root, runs_file = Path(runs_root), Path(runs_file or RUNS_FILE)
    today = today or datetime.date.today().isoformat()
    data, p0_costs, synced_runs, covered = _p0(regrade) if regrade else ({}, {}, [], set())
    existing = {r["run"]: r for r in read_runs(runs_file)}
    dirs = sorted({p.parent for p in list(runs_root.glob("*/grades.json")) + list(runs_root.glob("*/*/grades.json"))},
                  key=lambda d: (d.name, d.relative_to(runs_root).as_posix()))
    stops, records, notes, recorded_result, failing = [], {}, {}, {}, {}
    for d in dirs:
        run_id = d.relative_to(runs_root).as_posix()
        if run_id in existing:
            records[run_id] = existing[run_id]
            continue
        first = json.loads((d / "grades.json").read_text(encoding="utf-8"))
        rechecks = sorted(d.glob("regrade-*.json"))
        grades = first   # a run counts on its own grades: a later re-check is noted, never applied
        costs = case_costs(d, grades)
        for name, (cost, error, kind) in costs.items():
            if kind in ("falling", "mixed"):
                stops.append(f"{run_id}/{name}: {error}")
        parts = []
        old = round(sum(g["cost"] for g in first if g.get("cost") is not None), 2)
        new = None if any(c is None for c, _, _ in costs.values()) else round(sum(c for c, _, _ in costs.values()), 2)
        if new is not None and round(new, 2) != old:
            if p0_costs.get(run_id) != (old, new):
                stops.append(f"{run_id}: cost ${old:.2f} → ${new:.2f} is not in costs.md"
                             + (f" (which says ${p0_costs[run_id][0]:.2f} → ${p0_costs[run_id][1]:.2f})"
                                if run_id in p0_costs else ""))
            parts.append(COST_NOTE.format(date=today, old=old, new=new))
        elif run_id in p0_costs:
            stops.append(f"{run_id}: costs.md says ${p0_costs[run_id][0]:.2f} → ${p0_costs[run_id][1]:.2f}, the "
                         f"instrument reads ${old:.2f} unchanged")
        record = run_record(d, runs_root, grades, derive_selection(d, first, repo), None, costs)
        record["pack_sha"] = _batch(d).get("pack_head")
        for rc in rechecks:
            later = json.loads(rc.read_text(encoding="utf-8"))
            parts.append(f"re-checked {rc.stem}: {sum(1 for g in later if g.get('passed'))}/{len(later)} passed, "
                         f"noted, not counted")
        # C42: the flips move the verdicts
        flips = [f for f in (data.get("flips") or []) if f["run"] == run_id]
        if flips:
            state = {f"{g['scenario']}-{g['seat']}": {json.dumps(r["check"]) for r in g.get("results") or []
                                                     if r.get("passed") is False} for g in grades}
            for f in flips:
                key = json.dumps(f["check_args"])
                (state[f["case"]].discard if f["after"] else state[f["case"]].add)(key)
            for g in grades:
                name = f"{g['scenario']}-{g['seat']}"
                if name in {f["case"] for f in flips} and not g.get("not_run"):
                    g["passed"] = not state[name]
            fixed = run_record(d, runs_root, grades, record["selection"], record["pack_sha"], costs)
            record.update({k: fixed[k] for k in ("passed", "result", "failed")})
            by = {}
            for f in flips:
                by.setdefault((f["check"], str(f["detail"]).split("; turn ")[0]), []).append(f["case"])
            parts.append("; ".join(f"re-graded {today} on the later checks: {check} — {', '.join(cases)}: {detail}"
                                   for (check, detail), cases in by.items()))
        if any(f["check"] == "layer" and not f["after"] for f in flips):
            record["layer_ok"] = False
        elif run_id in covered and record["layer_ok"] is None:
            record["layer_ok"] = True
        if run_id in synced_runs:
            entries = [x for x in data.get("synced") or [] if x["run"] == run_id]
            if not entries:
                stops.append(f"{run_id}: in synced.md but no synced entry in regrade.json")
            else:
                n = len(grades)

                def kind(key, label):
                    names = {v for x in entries for v in x.get(key) or []}
                    cases = sum(1 for x in entries if x.get(key))
                    return f"{label} {len(names)} in {cases} of {n}" if names else f"{label} 0"
                parts.append(SYNCED_NOTE + ", ".join((kind("plugins", "synced plugins"), kind("skills", "synced skills"),
                                                      kind("connectors", "claude.ai connectors"))))
        notes[run_id] = parts
        records[run_id] = record
        recorded_result[run_id] = (sum(1 for g in first if g.get("passed")), len(first))
        failing[run_id] = [(g["scenario"], g["seat"]) for g in first if not g.get("passed") and not g.get("not_run")]
    # C28: each hand-written row's explanation into its run's note, a cost P0's list changes written as the new one
    if page:
        taken = set()
        for date, cases, result, row, why in page_rows(page):
            fits = [r for r in records if r in notes and r not in taken and records[r]["date"] == date
                    and recorded_result[r] == tuple(int(x) for x in result.split("/")) and recorded_result[r][1] == cases
                    and _names_failures(row, failing[r])]
            if not fits:
                cells = [c.strip() for c in row.strip().strip("|").split("|")]
                stops.append("row matches no run: | " + " | ".join(cells[:3]) + " |")
                continue
            run_id = fits[0]
            taken.add(run_id)
            if run_id in p0_costs:
                o, n = p0_costs[run_id]
                why = why.replace(f"${o:.2f}", f"${n:.2f}")
            notes[run_id].insert(0, why)
    if stops:
        print("backfill stopped, nothing written:\n" + "\n".join(f"  {s}" for s in stops))
        return 1
    for run_id, parts in notes.items():
        records[run_id]["note"] = "; ".join(p for p in parts if p)
    text = "".join(json.dumps(records[r], ensure_ascii=False) + "\n" for r in records)
    runs_file.write_text(text, encoding="utf-8")
    print(f"backfill: {len(records)} run(s) in {runs_file.name} ({len(notes)} new)")
    return 0


# ---- --regrade-all (§5.9): every recorded run graded again with the fixed instruments, against its own day's scenarios

def modernise(check):
    """A check of a recorded run's scenarios, with the fixed instruments in place of the ones R-22 removed."""
    if check[0] == "transcript_contains":
        return ["hook_fired", "Stop", check[1]]
    if check[0] == "git_hook_contains":
        return ["gate_fires", True]
    if check[0] == "any_of":
        return ["any_of"] + [modernise(c) for c in check[1:]]
    if check[0] == "after_turn":
        return ["after_turn", check[1], modernise(check[2])]
    return check


def scenarios_for(repo, run_name):
    """(spec, commit, first?) for a run: the pack's evals/scenarios.json at the newest commit touching it at or before
    the run's stamp (its folder name, local time); with none, the first commit that added the file."""
    stamp = time.mktime(time.strptime(Path(run_name).name, "%Y%m%d-%H%M%S"))
    git = lambda *a: subprocess.run(["git", "-C", str(repo), *a], capture_output=True, encoding="utf-8",
                                    errors="replace").stdout.strip()
    commit, first = git("log", "-1", "--format=%h", f"--before=@{int(stamp)}", "--", "evals/scenarios.json"), False
    if not commit:
        commit, first = git("log", "--diff-filter=A", "--format=%h", "--", "evals/scenarios.json").split()[-1], True
    return json.loads(git("show", f"{commit}:evals/scenarios.json")), commit, first


def reviewed_at(repo, pack_head):
    """The REVIEWED fields of evals/sealed.json at the pack head a case recorded (its record's env.pack_head): what was
    reviewed when it ran, read from the commit itself, so neither a commit's date nor the machine's time zone decides.
    {} when the case recorded no head or the repository lacks it (a run before the sealed config, whose check reads it
    as n/a)."""
    if not pack_head:
        return {}
    r = subprocess.run(["git", "-C", str(repo), "show", f"{pack_head}:evals/sealed.json"], capture_output=True,
                       encoding="utf-8", errors="replace")
    if r.returncode != 0:
        return {}
    m = json.loads(r.stdout)
    return {k: m.get(k) for k in REVIEWED}


def _spec_at(repo, commit, cache):
    if commit not in cache:
        r = subprocess.run(["git", "-C", str(repo), "show", f"{commit}:evals/scenarios.json"], capture_output=True,
                           encoding="utf-8", errors="replace")
        cache[commit] = {x["id"]: x for x in json.loads(r.stdout)["scenarios"]} if r.returncode == 0 else {}
    return cache[commit]


def landed_after(repo, run_name, scenario_id, cache):
    """The first commit after a run's stamp whose scenarios.json holds the scenario: a run made on a scenario not yet
    committed (20260922-224650's redman_no_edit, committed two minutes after its stamp) is graded against the version
    that landed, never dropped. None when no commit holds it."""
    stamp = time.mktime(time.strptime(Path(run_name).name, "%Y%m%d-%H%M%S"))
    r = subprocess.run(["git", "-C", str(repo), "log", "--reverse", "--format=%h", f"--after=@{int(stamp)}", "--",
                        "evals/scenarios.json"], capture_output=True, encoding="utf-8", errors="replace")
    return next((c for c in r.stdout.split() if scenario_id in _spec_at(repo, c, cache)), None)


def regrade_all(root, out, repo=None):
    """Regrade every run under root (grades.json one or two levels down), `sealed` judged by the review at each case's
    recorded pack head (reviewed_at), into out: flips.md (each verdict that changed,
    by check, `layer` first), costs.md (runs whose total the cost instrument reads differently), synced.md (runs whose
    cases' first init carries an account-supplied entry) and regrade.json. Never writes under root. Exit 1, before
    costs.md, when any case cannot be costed."""
    root, out, repo = Path(root), Path(out), Path(repo or PACK)
    out.mkdir(parents=True, exist_ok=True)
    runs = sorted({p.parent for p in list(root.glob("*/grades.json")) + list(root.glob("*/*/grades.json"))})
    flips, costs, synced, notes, errors, dropped, specs = [], [], [], [], [], [], {}
    for run_dir in runs:
        run_id = run_dir.relative_to(root).as_posix()
        spec, commit, first = scenarios_for(repo, run_dir.name)
        notes.append(f"- {run_id}: graded against {commit}" + (", the first commit that added evals/scenarios.json "
                                                               "(no commit at or before its stamp)" if first else ""))
        by_id = {x["id"]: dict(x, checks=[modernise(c) for c in x["checks"]]) for x in spec["scenarios"]}
        recorded = {(g["scenario"], g["seat"]): g for g in json.loads((run_dir / "grades.json").read_text(encoding="utf-8"))}
        for case in sorted(d for d in run_dir.iterdir() if (d / "record.json").is_file()):
            rec = json.loads((case / "record.json").read_text(encoding="utf-8"))
            scenario = by_id.get(rec.get("scenario"))
            if scenario is None:   # run on a scenario not yet committed: the version that landed; never dropped
                landed = landed_after(repo, run_dir.name, rec.get("scenario"), specs)
                if landed is None:
                    dropped.append({"run": run_id, "case": case.name, "scenario": rec.get("scenario")})
                    continue
                notes.append(f"- {run_id}: {rec['scenario']} was not committed at its stamp; graded against {landed}, "
                             f"the first commit that has it")
                spec_case = _spec_at(repo, landed, specs)[rec["scenario"]]
                scenario = dict(spec_case, checks=[modernise(c) for c in spec_case["checks"]])
            old = recorded.get((rec["scenario"], rec["seat"]), {})
            own = [r["check"] for r in old.get("results") or [] if not r.get("automatic")]
            if own:   # graded on the checks its record lists: the run's own version of the scenario, never another
                scenario = dict(scenario, checks=[modernise(c) for c in own])
                if [modernise(c) for c in own] != [modernise(c) for c in (by_id.get(rec["scenario"]) or {}).get(
                        "checks", [])]:
                    notes.append(f"- {run_id}: graded on the checks its record lists (its scenarios differ from "
                                 f"{commit}'s)")
            g = grade(case, scenario, reviewed=reviewed_at(repo, (rec.get("env") or {}).get("pack_head")))
            before = {json.dumps(modernise(r["check"])): r["passed"] for r in old.get("results") or []}
            for r in g["results"]:
                # a check the record lacks passed where the case was recorded PASS; a recorded FAIL says nothing of it
                key = json.dumps(r["check"])
                was = before[key] if key in before else (True if old.get("passed") else None)
                if was is not None and r["passed"] is not None and was != r["passed"]:
                    flips.append({"run": run_id, "case": case.name, "check": r["check"][0], "check_args": r["check"],
                                  "before": was, "after": r["passed"], "detail": str(r["detail"])[:300]})
            if g.get("cost_error"):
                errors.append(f"{run_id}/{case.name}: {g['cost_error']}")
            elif old.get("cost") is not None:
                if round(old["cost"], 2) != g["cost"]:
                    costs.append({"run": run_id, "case": case.name, "before": old["cost"], "after": g["cost"],
                                  "totals": [t.get("cost") for t in rec["turns"]]})
            first_init = (cost_turns(case, rec)[0] if rec["turns"] else {}).get("init")
            plugins, skills, connectors = account_entries(first_init)
            if plugins or skills or connectors:
                synced.append({"run": run_id, "case": case.name, "plugins": plugins, "skills": skills,
                               "connectors": connectors,
                               "version": (first_init or {}).get("claude_code_version")})
    by_check = {}
    for f in flips:
        by_check.setdefault(f["check"], []).append(f)
    lines = [f"# Flips — {len(flips)} check verdict(s) changed in {len(runs)} run(s)"]
    for check in sorted(by_check, key=lambda c: (c != "layer", c)):   # one section per check, `layer` first
        lines += ["", f"## {check} — {len(by_check[check])} flip(s)"]
        lines += [f"- {f['run']}/{f['case']}: {'PASS' if f['before'] else 'FAIL'} → {'PASS' if f['after'] else 'FAIL'}"
                  f" — {f['detail']}" for f in by_check[check]]
    lines += ["", "## Runs, and the scenarios each was graded against", *dict.fromkeys(notes)]   # each note once
    (out / "flips.md").write_text("\n".join(lines) + "\n", encoding="utf-8")

    def counted(key):
        hits = [x for x in synced if key is None or x[key]]
        return {"runs": len({x["run"] for x in hits}), "cases": len(hits)}
    counts = {"plugin": counted("plugins"), "skill": counted("skills"), "connector": counted("connectors"),
              "any": counted(None)}
    (out / "regrade.json").write_text(json.dumps({"flips": flips, "costs": costs, "synced": synced,
                                                  "synced_counts": counts, "cost_errors": errors,
                                                  "not_regraded": dropped}, indent=1), encoding="utf-8")
    per_run = {}
    for x in synced:
        per_run[x["run"]] = per_run.get(x["run"], 0) + 1
    (out / "synced.md").write_text("".join(f"{r} — {n} case(s)\n" for r, n in sorted(per_run.items())), encoding="utf-8")
    if dropped:   # the hand-over is whole or it is not made
        print("\n".join(f"not regraded: {d['run']}/{d['case']} — scenario {d['scenario']} is in no commit" for d in dropped))
        return 1
    if errors:
        print("cost unknown, costs.md not written:\n" + "\n".join(f"  {e}" for e in errors))
        return 1
    run_costs = {}
    for run_dir in runs:
        run_id = run_dir.relative_to(root).as_posix()
        if any(c["run"] == run_id for c in costs):
            recorded = json.loads((run_dir / "grades.json").read_text(encoding="utf-8"))
            old = round(sum(g["cost"] for g in recorded if g.get("cost") is not None), 2)
            new = round(old + sum(c["after"] - c["before"] for c in costs if c["run"] == run_id), 2)
            run_costs[run_id] = (old, new)
    (out / "costs.md").write_text("".join(f"{r} ${o:.2f} → ${n:.2f}\n" for r, (o, n) in sorted(run_costs.items())),
                                  encoding="utf-8")
    print(f"regrade: {sum(1 for _ in flips)} flip(s) in {len(runs)} run(s) → {out / 'flips.md'}, "
          f"{out / 'costs.md'}, {out / 'synced.md'}")
    return 0


# ---- the regression corpus (criterion 14): real failing sessions kept in compact form, rebuilt and graded offline

# the kinds a compact run can be graded on again: its files, its snapshots and its streams. Git history (commits,
# origin, the index), facts and the gate probe are not carried, so a failure on those alone is not kept.
REPRODUCIBLE = {"skill_invoked", "skill_not_invoked", "unchanged", "changed", "no_new_code", "no_path", "only_changed",
                "file_exists", "file_matches", "file_not_matches", "record_status", "roadmap_status", "holdout",
                "tests_pass", "no_removed_lines", "edited_by_tool", "bash_matching", "max_bash_matching",
                "no_bash_matching", "bash_output", "final_matches", "receipt", "hook_fired"}


def rebuild_real(entry, case_dir):
    """A case folder from a harvested entry: today's build of its scenario, the entry's changed files over it, and a
    record of its turns and snapshots (no env: graded as a run recorded before the sealed config)."""
    case_dir = Path(case_dir)
    case_dir.mkdir(parents=True)
    spec = json.loads((HERE / "scenarios.json").read_text(encoding="utf-8"))
    scenario = next(x for x in spec["scenarios"] if x["id"] == entry["scenario"])
    proj = build_project(case_dir, scenario)
    for rel, text in entry["files"].items():
        if text is None:
            (proj / rel).unlink(missing_ok=True)
        else:
            (proj / rel).parent.mkdir(parents=True, exist_ok=True)
            (proj / rel).write_text(text, encoding="utf-8")
    (case_dir / "record.json").write_text(json.dumps({"scenario": entry["scenario"], "seat": entry["seat"],
                                                      "turns": entry["turns"], "snapshots": entry["snapshots"],
                                                      "baseline_tests": entry["baseline_tests"]}), encoding="utf-8")
    return case_dir


def harvest_case(case_dir, name, out_dir):
    """Write a recorded case into the corpus as evals/known/real/<name>.json: only what grading needs — each turn's
    prompt, skills, Bash calls with their results, writes, final message and stream hook events; the snapshots; the
    project's files that differ from its build; the baseline tests — every path made case-relative and no session
    id kept — and `fails`, the reproducible kinds it fails on when rebuilt and graded against today's scenario."""
    case_dir, out_dir = Path(case_dir), Path(out_dir)
    rec = json.loads((case_dir / "record.json").read_text(encoding="utf-8"))
    turns = []
    for n, t in enumerate(rec["turns"], 1):
        f = case_dir / f"turn{n}.jsonl"
        parsed = parse_stream(f.read_text(encoding="utf-8", errors="replace")) if f.is_file() else t
        turns.append({"prompt": t.get("prompt"), "skills": parsed["skills"], "typed": [],
                      "bash": [b if isinstance(b, dict) else {"cmd": b, "out": None} for b in parsed["bash"]],
                      "writes": parsed["writes"], "final": parsed["final"],
                      # the SessionStart outputs are the layer check's, which a run recorded before the sealed config
                      # is not graded on here; they carry the loader's whole text, so they are not kept
                      "hooks": [h for h in parsed.get("hooks") or [] if h.get("event") != "SessionStart"],
                      "cost": parsed.get("cost"), "api_error": parsed.get("api_error")})
    base, now = rec["snapshots"][0], snapshot(case_dir / "project")
    files = {rel: (case_dir / "project" / rel).read_text(encoding="utf-8", errors="replace")
             for rel, digest in now.items() if base.get(rel) != digest}
    files.update({rel: None for rel in base if rel not in now})
    entry = {"id": name, "source": None, "scenario": rec["scenario"], "seat": rec["seat"], "turns": turns,
             "snapshots": rec["snapshots"], "files": files, "baseline_tests": rec.get("baseline_tests"), "fails": []}
    text = json.dumps(entry, ensure_ascii=False)
    for form in sorted({str(case_dir), str(case_dir.resolve()), os.path.realpath(case_dir)}, key=len, reverse=True):
        text = text.replace(form, "<case>")
    entry = json.loads(text.replace(str(Path.home()), "<home>"))
    parts = case_dir.resolve().parts
    entry["source"] = "/".join(parts[-3:]) if len(parts) >= 3 else case_dir.name
    with tempfile.TemporaryDirectory() as tmp:
        spec = json.loads((HERE / "scenarios.json").read_text(encoding="utf-8"))
        scenario = next(x for x in spec["scenarios"] if x["id"] == entry["scenario"])
        g = grade(rebuild_real(entry, Path(tmp) / "case"), scenario)
    entry["fails"] = sorted({k for r in g["results"] if r["passed"] is False for k in _kinds(r["check"])} & REPRODUCIBLE)
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / f"{name}.json").write_text(json.dumps(entry, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    return entry


def _kinds(check):
    yield check[0]
    if check[0] == "any_of":
        for inner in check[1:]:
            yield from _kinds(inner)
    elif check[0] == "after_turn":
        yield from _kinds(check[2])


def plan(spec, quick, only, seat):
    cases = []
    for s in spec["scenarios"]:
        if only and s["id"] not in only:
            continue
        for st in (s.get("quick", []) if quick else s["seats"]):
            if not seat or st == seat:
                cases.append((s, st))
            elif SEAT_ALIAS.get(seat) == st:
                cases.append((s, seat))
    return cases


def report(grades, out, stem=None, head=None):
    lines = [head] if head else []
    for g in grades:
        failed = [r for r in g["results"] if r["passed"] is False]
        label = ("NOT RUN" if g.get("not_run") else "PASS" if g["passed"] else
                 "UNREV" if g.get("unreviewed") and not failed else "FAIL")
        cost = "cost ?" if g.get("cost") is None else f"${g['cost']:.2f}"
        lines.append(f"{label:<5} {g['scenario']:<20} {g['seat']:<6} {cost}" + (f"  ({g['not_run']})" if g.get("not_run") else ""))
        for r in failed:
            lines.append(f"        ✗ {r['check'][0]} {r['check'][1:]} — {r['detail']}")
    unknown = [g for g in grades if g.get("cost") is None]
    total = sum(g["cost"] for g in grades if g.get("cost") is not None)   # a missing cost is never read as $0
    passed = sum(g["passed"] for g in grades)
    not_run = sum(1 for g in grades if g.get("not_run"))
    repaired = sum(1 for g in grades if any(str(r["detail"]).startswith("repaired by hook") for r in g["results"]))
    lines.append(f"evals: {passed}/{len(grades)} cases passed · ${total:.2f}{' (partial)' if unknown else ''}"
                 + (f" · {not_run} NOT RUN (API errors: re-run them; they measured nothing)" if not_run else ""))
    if unknown:
        lines.append(f"cost unknown: {len(unknown)} case(s) — " + "; ".join(
            f"{g['scenario']} {g['seat']}: {g.get('cost_error', 'no cost')}" for g in unknown))
    for g in grades:   # stopped by a CLI change: its earlier turns graded as they stand, never passed as a whole
        if g.get("not_completed"):
            lines.append(f"not completed: {g['not_completed']} ({g['scenario']} {g['seat']})")
    for g in grades:   # neither pass nor fail: E-1's case until its reviewed fields are committed
        lines += [f"unreviewed: {u} ({g['scenario']} {g['seat']})" for u in g.get("unreviewed", [])]
    if repaired:   # the receipt hook's work, counted apart: the skill's miss is not hidden
        lines.append(f"repaired by hook: {repaired} case(s)")
    text = "\n".join(lines)
    print(text)
    out.mkdir(parents=True, exist_ok=True)   # a regrade writes beside the record, never over it (`stem`)
    (out / (f"{stem}.txt" if stem else "report.txt")).write_text(text + "\n", encoding="utf-8")
    (out / (f"{stem}.json" if stem else "grades.json")).write_text(json.dumps(grades, indent=1), encoding="utf-8")
    return passed == len(grades)




def main(argv=None):
    ap = argparse.ArgumentParser(description=(__doc__ or "").split("\n\n")[0])
    ap.add_argument("--quick", action="store_true")
    ap.add_argument("--only", nargs="*")
    ap.add_argument("--seat", choices=sorted(SEATS))
    ap.add_argument("--parallel", type=int, default=4)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--recheck")
    ap.add_argument("--out", help="where a batch writes its run (default ~/xbt-evals); for --recheck, where the "
                                  "regrade goes (default: beside the run's record, never over it)")
    ap.add_argument("--config", default=str(Path.home() / ".claude-evals"),
                    help="the sealed config folder the sessions run on (built by --build-sealed, and before every batch; "
                         "log it in once: CLAUDE_CONFIG_DIR=<folder> claude auth login)")
    ap.add_argument("--counts", action="store_true", help="print the suite's figures as JSON")
    ap.add_argument("--harvest", metavar="CASE", help="keep a recorded case in the corpus as evals/known/real/<--name>.json")
    ap.add_argument("--name", help="with --harvest: the corpus id")
    ap.add_argument("--regrade-all", metavar="ROOT", help="regrade every recorded run under ROOT into --out (required)")
    ap.add_argument("--backfill", metavar="ROOT", help="write evals/RUNS.jsonl from every graded run under ROOT")
    ap.add_argument("--regrade", metavar="DIR", help="with --backfill: a cross-run re-grade's folder (regrade.json, "
                                                     "costs.md, synced.md, flips.md)")
    ap.add_argument("--page", help="with --backfill: the evidence page whose hand-written run table's explanations "
                                   "are copied into the runs' notes")
    ap.add_argument("--selftest", action="store_true", help="prove the graders offline (evals/selftest.py)")
    ap.add_argument("--co-fail", action="store_true", help="with --selftest: print the co-fail review table")
    ap.add_argument("--build-sealed", action="store_true", help="build the sealed config folder (--config) and stop")
    ap.add_argument("--sealed-check", action="store_true", help="build the sealed config into a scratch folder and check it")
    ap.add_argument("--probe", action="store_true", help="review a Claude Code version sealed.json has not reviewed: launch on it, with the manifest's "
                         "probe_hooks built in, and grade each case's sealed check unreviewed, never a pass")
    args = ap.parse_args(argv)
    if args.harvest:
        if not args.name:
            print("--harvest needs --name <id>", file=sys.stderr)
            return 2
        entry = harvest_case(args.harvest, args.name, HERE / "known" / "real")
        print(f"harvested {entry['source']} as {args.name}: fails {entry['fails'] or 'nothing reproducible'}")
        return 0
    if args.regrade_all:
        if not args.out:
            print("--regrade-all needs --out <dir>: it never writes into the runs it reads", file=sys.stderr)
            return 2
        return regrade_all(args.regrade_all, args.out)
    if args.backfill:
        return backfill(args.backfill, RUNS_FILE, args.regrade, args.page)
    if args.counts:
        print(json.dumps(counts(json.loads((HERE / "scenarios.json").read_text(encoding="utf-8"))), ensure_ascii=False))
        return 0
    if args.selftest:
        import selftest
        return 1 if selftest.main(["--co-fail"] if args.co_fail else []) else 0
    if args.build_sealed:
        settings = build_sealed(args.config)
        print(f"built {args.config}: {len(manifest()['skills'])} skill link(s), "
              f"{sum(len(v) for v in settings['hooks'].values())} hook(s), "
              + ", ".join(f"{k} {str(settings.get(k)).lower()}" for k, _ in ACCOUNT_KEYS))
        return 0
    if args.sealed_check:
        return sealed_check()
    spec = json.loads((HERE / "scenarios.json").read_text(encoding="utf-8"))
    by_id = {s["id"]: s for s in spec["scenarios"]}
    if args.recheck:
        run_dir = Path(args.recheck)
        grades = [grade(d, by_id[json.loads((d / "record.json").read_text(encoding="utf-8"))["scenario"]])
                  for d in sorted(run_dir.iterdir()) if (d / "record.json").is_file()]
        stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
        return 0 if report(grades, Path(args.out) if args.out else run_dir, stem=f"regrade-{stamp}") else 1
    cases = plan(spec, args.quick, args.only, args.seat)
    if args.dry_run or not cases:
        for s, st in cases:
            print(f"would run {s['id']} on {st}: {len(s['turns'])} turn(s), {len(s['checks'])} check(s)")
        print(totals_line(spec))
        if args.quick or args.only or args.seat:
            print(last_run_line(selection_of(args), cases=cases,
                                runs_root=Path(args.out) if args.out else Path.home() / "xbt-evals"))
        return 0
    found = shutil.which("claude")
    if not found:
        print("claude not found on PATH — no session launched", file=sys.stderr)
        return 2
    cli = os.path.realpath(found)   # the batch runs on the binary it resolved at its start
    import selftest
    problems = selftest.main(quiet=True)   # nothing paid is launched while any grader is unproven
    if problems:
        print(f"selftest failed: {problems} problem(s) — no session launched", file=sys.stderr)
        return 3
    version = cli_version_of(cli)
    config = Path(args.config)
    build_sealed(config, probe=args.probe)
    runs_root = Path(args.out) if args.out else Path.home() / "xbt-evals"
    code, message = preflight(cli, config, probe=args.probe, runs_root=runs_root)
    if code:
        print(message, file=sys.stderr)
        return code
    out = runs_root / datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    on_graded, head = None, None
    print(f"running {len(cases)} case(s) into {out} on {cli} ({version})", flush=True)
    grades, changed = run_batch(cases, spec, out, config, cli, version, args.parallel, probe=args.probe,
                                on_graded=on_graded)
    if args.probe:
        build_sealed(config)   # the probe entry is E-1's alone: the next build leaves it out
    ok = report(grades, out, head=head)
    log_run(out, runs_root, grades, selection_of(args), _head(PACK))
    if changed:
        print(f"CLI changed mid-batch: {changed[0]} → {changed[1]} — the batch stopped; review the new version with a "
              f"--probe run before the next batch", file=sys.stderr)
        return 5
    return 0 if ok else 1


def sealed_check():
    """--sealed-check (R-25 § 5.5): build the sealed config into a scratch folder and run both parity checks, printing
    each entry in normal form with its log writer: the build is the installer's registration plus extra_hooks, with the
    three account keys; and, where ~/.claude/settings.json holds pack hooks (this pack's machine), the live pack hooks
    are the build's, except the differences live_pending lists. Exit 1 on any difference."""
    m = manifest()
    with tempfile.TemporaryDirectory() as tmp:
        config = Path(tmp) / "sealed"
        settings = build_sealed(config)
        links = sorted(p.name for p in (config / "skills").iterdir() if p.is_symlink())
        problems = [] if links == sorted(m["skills"]) else [f"skill links {links} are not the manifest's {sorted(m['skills'])}"]
        problems += sealed_build_problems(config)
        roots, lines = sealed_roots(config), []
        for event, matcher, h in _entries(settings):
            form = "the launcher's form" if launcher_form(h) else "the runner's form" if is_logged(h) else "NO LOG WRITER"
            try:
                lines.append(f"  {_describe(hook_key(event, matcher, h, roots))} — {form}")
            except ValueError as e:
                lines.append(f"  {event} {settings_script(h)} — {e}")
    keys = ", ".join(f"{k} {str(settings.get(k)).lower()}" for k, _ in ACCOUNT_KEYS)
    count = len(_entries(settings))
    if problems:
        print("sealed: the build is not the installer's registration plus extra_hooks:\n" + "\n".join(f"  {p}" for p in problems))
    else:
        print(f"sealed: build = installer {json.dumps(m['installer'], sort_keys=True)} + extra_hooks "
              f"({len(m['skills'])} skills, {count} hooks); {keys}")
    print("\n".join(lines))
    live = Path.home() / ".claude" / "settings.json"
    try:
        live_settings = json.loads(live.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        live_settings = None
    names = {Path(settings_script(h)).name for _, _, h in _entries(settings)}
    if not live_settings or not live_pack_entries(live_settings, live, names):
        print(f"live: {live} holds no pack hook (not this pack's machine); the live leg is skipped")
        return 1 if problems else 0
    live_problems, owned = live_parity(live_settings, live)
    for event, name, state in launcher_states(live_settings, live, names):
        print(f"launcher: {event} {name} {state}")
    if live_problems:
        print("live: the pack hooks differ from the sealed build:\n" + "\n".join(f"  {p}" for p in live_problems))
    else:
        print(f"live: pack hooks = sealed, {len(owned)} pending" + "".join(f"\n  {o}" for o in owned))
    return 1 if problems or live_problems else 0


def run_batch(cases, spec, out, config, cli, version, parallel=4, probe=False, on_graded=None):
    """(grades, (old, new) or None): every case on the binary resolved at the batch's start. An API error skips the
    cases not yet started; a CLI change stops every launch after it (the changed case not completed, the rest not
    launched). `on_graded(case folder)` runs as each launched case is graded."""
    out.mkdir(parents=True, exist_ok=True)
    limited, stopped, changed, grades = threading.Event(), threading.Event(), [], []

    def one(case_dir, s, st):
        if limited.is_set():
            return "skipped"
        if stopped.is_set():
            return "not launched"
        try:
            run_case(case_dir, s, st, spec["prompts"], config, cli, version, probe)
        except CLIChanged as e:
            changed.append((e.old, e.new))
            stopped.set()
        return "ran"

    with concurrent.futures.ThreadPoolExecutor(max_workers=parallel) as pool:
        futures = {pool.submit(one, out / f"{s['id']}-{st}", s, st): (s, st) for s, st in cases}
        for f in concurrent.futures.as_completed(futures):
            s, st = futures[f]
            try:
                outcome = f.result()
                if outcome != "ran":
                    grades.append({"scenario": s["id"], "seat": st, "passed": False, "cost": 0, "results": [],
                                   "not_run": "skipped: an earlier session hit an API error" if outcome == "skipped"
                                   else "not launched: CLI changed"})
                    print(f"  {outcome} {s['id']} on {st}", flush=True)
                    continue
                g = grade(out / f"{s['id']}-{st}", s)
                if g.get("not_run"):
                    limited.set()
                grades.append(g)
                if on_graded:
                    try:
                        on_graded(out / f"{s['id']}-{st}")
                    except Exception as e:   # reported, never lost silently: the case's own record stands
                        print(f"  after {s['id']} on {st}: {type(e).__name__}: {e}", file=sys.stderr, flush=True)
            except Exception as e:
                grades.append({"scenario": s["id"], "seat": st, "passed": False, "cost": None,
                               "cost_error": f"not graded: {type(e).__name__}",
                               "results": [{"check": ["run"], "passed": False, "detail": f"{type(e).__name__}: {e}"}]})
            print(f"  finished {s['id']} on {st}", flush=True)
    grades.sort(key=lambda g: (g["scenario"], g["seat"]))
    return grades, (changed[0] if changed else None)

if __name__ == "__main__":
    sys.exit(main())
