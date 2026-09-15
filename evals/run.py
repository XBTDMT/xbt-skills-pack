#!/usr/bin/env python3
"""Behaviour tests for the skills pack: real headless Claude Code sessions in a contained project, graded in code.

  python3 evals/run.py --quick              the subset to run after any skill or doctrine change (8 sessions)
  python3 evals/run.py                      every scenario on every seat it names
  python3 evals/run.py --only failed_fork   one scenario (on its seats; --seat narrows further)
  python3 evals/run.py --dry-run            print what would run, run nothing
  python3 evals/run.py --recheck <run dir>  grade a finished run again without new sessions

Each session gets its own copy of evals/fixture (plus an overlay if the scenario names one) under
~/xbt-evals/<stamp>/<scenario>-<seat>/project, with git and a bare `origin`. Sessions use the real ~/.claude setup
(skills, doctrine hook), a pinned model and effort, acceptEdits, a narrow tool allow-list, one deny list (`git push`, and
Write and Edit inside the skill folders) and the skill folders added as readable directories.
Every turn's stream is kept, and the project's files are snapshotted after each turn so checks can ask about the
state mid-conversation. Exit 1 when any check fails. Stdlib only, Python 3.9 or newer.
"""
import argparse
import concurrent.futures
import datetime
import fnmatch
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import threading
from pathlib import Path

HERE = Path(__file__).resolve().parent
PACK = HERE.parent
SEATS = {"fable": ["--model", "claude-fable-5-1", "--effort", "low"],
         "opus": ["--model", "claude-opus-5", "--effort", "high"]}
ALLOWED = ["Read", "Write", "Edit", "Glob", "Grep", "Skill", "Bash(python3:*)", "Bash(git status:*)",
           "Bash(git diff:*)", "Bash(git log:*)", "Bash(git add:*)", "Bash(git commit:*)", "Bash(ls:*)", "Bash(cat:*)"]


def skill_dirs():
    """The skills folders and the real folders their links point into (this pack), so a session can read a skill's
    reference files (greenman's REFERENCE.md) as a real session can. Without them the read is denied and a session
    that honours the refusal rule writes no record (large_stop on opus, 2026-09-14)."""
    roots = {Path.home() / ".claude" / "skills",
             Path(os.environ.get("CLAUDE_CONFIG_DIR") or Path.home() / ".claude") / "skills"}
    dirs = set()
    for root in roots:
        if root.is_dir():
            dirs.add(root)
            dirs.add(root.resolve())
            dirs.update(child.resolve().parent for child in root.iterdir() if child.is_symlink())
    return sorted(str(d) for d in dirs)


def session_dirs(proj):
    """Claude Code's own folder for a session in `proj` (under each config folder's `projects/`), created so it can be
    added read-only: a session reads its own saved tool output there, which a real session can do too
    (large_stop on opus, 2026-09-15: a read of the persisted SessionStart output was denied)."""
    name = re.sub(r"[^A-Za-z0-9]", "-", str(Path(proj).resolve()))
    roots = {Path.home() / ".claude", Path(os.environ.get("CLAUDE_CONFIG_DIR") or Path.home() / ".claude")}
    out = []
    for root in sorted(roots):
        d = root / "projects" / name
        d.mkdir(parents=True, exist_ok=True)
        out.append(str(d))
    return out


def permission_flags(tools, dirs):
    """The allow-list, one deny list (git push, and Write and Edit inside the skill folders), and the skill folders
    as readable directories. One --disallowedTools flag, so no deny can be dropped by a second."""
    deny = ["Bash(git push:*)", *(f"{tool}(/{d}/**)" for d in dirs for tool in ("Write", "Edit"))]
    return ["--allowedTools", *tools, "--disallowedTools", *deny, *(["--add-dir", *dirs] if dirs else [])]


SKILL_DIRS = skill_dirs()
SESSION_TIMEOUT = 1200
IGNORED_PARTS = {".git", "__pycache__"}


# ------------------------------------------------------------------------------------------------ project

def build_project(case_dir, scenario):
    proj = case_dir / "project"
    shutil.copytree(HERE / "fixture", proj)
    overlays = scenario.get("overlay") or []
    for name in [overlays] if isinstance(overlays, str) else overlays:
        shutil.copytree(HERE / "overlays" / name, proj, dirs_exist_ok=True)
    git = lambda *a: subprocess.run(["git", "-C", str(proj), *a], check=True, capture_output=True)
    git("init", "-q", "-b", "main")
    git("add", "-A")
    git("-c", "user.name=evals", "-c", "user.email=evals@local", "commit", "-q", "-m", "fixture")
    subprocess.run(["git", "init", "-q", "--bare", str(case_dir / "origin.git")], check=True, capture_output=True)
    git("remote", "add", "origin", "../origin.git")
    preps = scenario.get("prep") or []
    for prep in [preps] if isinstance(preps, str) else preps:
        PREPS[prep](proj)
    return proj


def _prep_readme_change(proj):
    with open(proj / "README.md", "a", encoding="utf-8") as fh:
        fh.write("\nUsage: see tests/test_report.py.\n")


def _prep_untracked_ledger(proj):
    """A redman ledger a session wrote and never committed: the close-out must commit it, not leave it."""
    led = proj / "knowledge" / "redman" / "LEDGER.md"
    led.parent.mkdir(parents=True, exist_ok=True)
    led.write_text("# Redman ledger — shelfkit\nroadmap: none — the project's redman ledger; each row names its own roadmap item\n\n"
                   "| date | kind | item | size | state | where | observed | why it matters | first step | roadmap |\n"
                   "|---|---|---|---|---|---|---|---|---|---|\n"
                   "| 2026-09-14 | stale doc | README gives no usage example | small | filed | `README.md#shelfkit` | "
                   "read README.md → one line, no example | a new user has to read the tests to use the library | "
                   "add a three-line example | R-3 |\n", encoding="utf-8")


def _prep_gate_and_staged_spec(proj):
    """The commit gate installed, and an unlinked spec staged by someone else: the commit must go through the gate."""
    subprocess.run([sys.executable, str(PACK / "closeout" / "closeout.py"), "install-gate", str(proj)],
                   check=True, capture_output=True)
    spec = proj / "knowledge" / "02-spec-cli.md"
    spec.write_text("# CLI spec\n\nThe shelf command prints the low-stock report, one sku per line.\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(proj), "add", "knowledge/02-spec-cli.md"], check=True, capture_output=True)


def _prep_gate(proj):
    """The commit gate installed, nothing staged."""
    subprocess.run([sys.executable, str(PACK / "closeout" / "closeout.py"), "install-gate", str(proj)],
                   check=True, capture_output=True)


PREPS = {"readme_change": _prep_readme_change, "untracked_ledger": _prep_untracked_ledger,
         "gate_and_staged_spec": _prep_gate_and_staged_spec, "gate": _prep_gate}


def snapshot(proj):
    """{relative path: sha1} for every file outside .git and __pycache__."""
    out = {}
    for p in sorted(proj.rglob("*")):
        rel = p.relative_to(proj)
        if p.is_file() and not IGNORED_PARTS.intersection(rel.parts):
            out[rel.as_posix()] = hashlib.sha1(p.read_bytes()).hexdigest()
    return out


# ------------------------------------------------------------------------------------------------ sessions

def parse_stream(text):
    """What a turn did, from its stream-json: skills, bash commands, written files, final text, cost, session id."""
    turn = {"skills": [], "bash": [], "writes": [], "final": "", "cost": None, "session_id": None, "api_error": None}
    for line in text.splitlines():
        try:
            d = json.loads(line)
        except ValueError:
            continue
        turn["session_id"] = turn["session_id"] or d.get("session_id")
        if d.get("type") == "assistant":
            for c in d.get("message", {}).get("content", []) or []:
                if c.get("type") != "tool_use":
                    continue
                inp = c.get("input", {})
                if c.get("name") == "Skill":
                    turn["skills"].append(inp.get("skill", ""))
                elif c.get("name") == "Bash":
                    turn["bash"].append(inp.get("command", ""))
                elif c.get("name") in ("Write", "Edit"):
                    turn["writes"].append(inp.get("file_path", ""))
        elif d.get("type") == "result":
            turn["final"], turn["cost"] = d.get("result", "") or "", d.get("total_cost_usd")
            if d.get("terminal_reason") == "api_error" or d.get("api_error_status"):
                # a usage limit or an outage ended the session: nothing about the skills was measured
                turn["api_error"] = f"{d.get('api_error_status') or 'api error'}: {(d.get('result') or '')[:120]}"
    return turn


def run_case(case_dir, scenario, seat, prompts):
    case_dir.mkdir(parents=True)
    proj = build_project(case_dir, scenario)
    snaps = [snapshot(proj)]
    turns, sid = [], None
    tools = ALLOWED + (["Bash(git:*)"] if scenario.get("allow_git") else [])
    for n, key in enumerate(scenario["turns"], 1):
        prompt = prompts.get(key, key)
        cmd = ["claude", "-p", prompt, *(["--resume", sid] if sid else []), *SEATS[seat],
               "--permission-mode", "acceptEdits", *permission_flags(tools, SKILL_DIRS + session_dirs(proj)), "--output-format", "stream-json", "--verbose"]
        try:
            r = subprocess.run(cmd, cwd=proj, capture_output=True, text=True, timeout=SESSION_TIMEOUT)
            out, err = r.stdout, r.stderr
        except subprocess.TimeoutExpired as e:
            out, err = (e.stdout or b"").decode() if isinstance(e.stdout, bytes) else (e.stdout or ""), "TIMEOUT"
        (case_dir / f"turn{n}.jsonl").write_text(out, encoding="utf-8")
        (case_dir / f"turn{n}.err").write_text(err or "", encoding="utf-8")
        turn = parse_stream(out)
        turns.append(turn)
        sid = turn["session_id"] or sid
        if turn.get("api_error"):
            break  # later turns would only hit the same wall
        snaps.append(snapshot(proj))
    record = {"scenario": scenario["id"], "seat": seat, "turns": turns, "snapshots": snaps}
    (case_dir / "record.json").write_text(json.dumps(record, indent=1), encoding="utf-8")
    return record


# ------------------------------------------------------------------------------------------------ checks

def _under(snap, path):
    return {k: v for k, v in snap.items() if k == path or k.startswith(path.rstrip("/") + "/")}


def _files(proj, pattern):
    return [p for p in sorted(proj.glob(pattern)) if p.is_file()]


def check(name, args, ctx, snap_index=None):
    """(passed, detail). ctx: proj, case_dir, record. snap_index picks the state after that turn (None = the end)."""
    proj, rec = ctx["proj"], ctx["record"]
    snaps = rec["snapshots"]
    snap = snaps[-1] if snap_index is None else snaps[snap_index]
    base = snaps[0]
    skills = [s for t in rec["turns"] for s in t["skills"]]
    if name == "after_turn":
        n, inner = args[0], args[1]
        ok, detail = check(inner[0], inner[1:], ctx, snap_index=n)
        return ok, f"after turn {n}: {detail}"
    if name == "any_of":  # passes when at least one of the listed checks passes
        outcomes = [check(c[0], c[1:], ctx, snap_index) for c in args]
        return any(ok for ok, _ in outcomes), " | ".join(d for _, d in outcomes)
    if name == "skill_invoked":
        return any(s == args[0] or s.endswith(":" + args[0]) for s in skills), f"skills={skills}"
    if name == "skill_not_invoked":
        return not any(s == args[0] or s.endswith(":" + args[0]) for s in skills), f"skills={skills}"
    if name == "unchanged":
        a, b = _under(base, args[0]), _under(snap, args[0])
        return a == b, "unchanged" if a == b else f"differs: {sorted(set(a.items()) ^ set(b.items()))[:4]}"
    if name == "changed":
        a, b = _under(base, args[0]), _under(snap, args[0])
        return a != b, "changed" if a != b else "not changed"
    if name == "no_removed_lines":  # additions are fine (a new regression test); a changed or deleted line is not
        r = subprocess.run(["git", "-C", str(proj), "diff", "HEAD", "--", args[0]], capture_output=True, text=True)
        pattern = args[1] if len(args) > 1 else ""  # only lines matching this, e.g. the assertions
        removed = [l for l in r.stdout.splitlines()
                   if l.startswith("-") and not l.startswith("---") and re.search(pattern, l)]
        return not removed, f"removed or changed: {removed[:3]}" if removed else "only additions, if any"
    if name == "no_new_code":
        new = sorted(set(_under(snap, args[0])) - set(_under(base, args[0])))
        return not new, f"new files: {new}" if new else "no new files"
    if name == "no_path":
        hits = sorted(_under(snap, args[0]))
        return not hits, f"present: {hits[:4]}" if hits else "absent"
    if name == "file_exists":
        hits = [k for k in snap if fnmatch.fnmatch(k, args[0])]
        return bool(hits), f"{hits[:3]}" if hits else f"no file matches {args[0]}"
    if name == "file_matches":
        files = _files(proj, args[0])
        ok = any(re.search(args[1], f.read_text(encoding="utf-8", errors="replace")) for f in files)
        return ok, f"{len(files)} file(s) matching {args[0]}; pattern {'found' if ok else 'not found'}"
    if name == "file_not_matches":
        files = _files(proj, args[0])
        bad = [f.name for f in files if re.search(args[1], f.read_text(encoding="utf-8", errors="replace"))]
        return not bad, f"matched in {bad}" if bad else "pattern absent"
    if name == "tests_pass":
        r = subprocess.run([sys.executable, "-m", "unittest", "discover", "-s", "tests"], cwd=proj,
                           capture_output=True, text=True, timeout=120)
        return r.returncode == 0, (r.stderr.strip().splitlines() or ["(no output)"])[-1]
    if name == "origin_empty":
        r = subprocess.run(["git", "--git-dir", str(ctx["case_dir"] / "origin.git"), "rev-list", "--all", "--count"],
                           capture_output=True, text=True)
        return r.stdout.strip() in ("", "0"), f"origin commits: {r.stdout.strip() or 0}"
    if name == "edited_by_tool":  # the session itself changed the file with Write or Edit
        hits = [w for tn in rec["turns"] for w in tn["writes"] if w.endswith(args[0])]
        return bool(hits), f"edited by the session: {len(hits)} write(s)" if hits else "not edited with Write/Edit"
    if name == "not_written_by_tool":  # the file came from somewhere other than the Write/Edit tools (a shell command)
        hits = [w for tn in rec["turns"] for w in tn["writes"] if w.endswith(args[0])]
        return not hits, f"written by the Write/Edit tool: {hits}" if hits else "not written by the Write/Edit tool"
    if name == "bash_matching":  # at least one shell command matches (the file was created from the shell)
        hits = [b[:100] for tn in rec["turns"] for b in tn["bash"] if re.search(args[0], b)]
        return bool(hits), f"{len(hits)} matching" if hits else "no shell command matched"
    if name == "max_bash_matching":  # at most N shell commands match: a denied operation is not tried again another way
        hits = [b[:100] for tn in rec["turns"] for b in tn["bash"] if re.search(args[0], b)]
        return len(hits) <= args[1], f"{len(hits)} matching: {hits}"
    if name == "transcript_contains":  # what the session saw, including hook feedback the stream does not carry
        sids = {tn["session_id"] for tn in rec["turns"] if tn["session_id"]}
        texts = [p.read_text(encoding="utf-8", errors="replace") for sid in sids
                 for base in (Path(os.environ.get("CLAUDE_CONFIG_DIR") or Path.home() / ".claude") / "projects",)
                 for p in base.glob(f"*/{sid}.jsonl")]
        ok = any(args[0] in tx for tx in texts)
        return ok, f"{len(texts)} transcript(s); {'found' if ok else 'not found'}: {args[0]!r}"
    if name == "no_bash_matching":
        hits = [b[:100] for t in rec["turns"] for b in t["bash"] if re.search(args[0], b)]
        return not hits, f"matched: {hits}" if hits else "none"
    if name == "final_matches":
        turns = rec["turns"]
        text = turns[args[0] - 1]["final"] if len(turns) >= args[0] else ""
        ok = bool(re.search(args[1], text))
        return ok, "found" if ok else f"not found in turn {args[0]} final message"
    if name == "committed_file_matches":  # what the last commit holds, not the working tree
        r = subprocess.run(["git", "-C", str(proj), "show", f"HEAD:{args[0]}"], capture_output=True, text=True)
        ok = r.returncode == 0 and bool(re.search(args[1], r.stdout))
        return ok, "found in HEAD" if ok else ("not in HEAD" if r.returncode else "pattern not found in HEAD")
    if name == "commits_at_least":  # commits made by the session, beyond the fixture's one
        r = subprocess.run(["git", "-C", str(proj), "rev-list", "--count", "HEAD"], capture_output=True, text=True)
        n = int(r.stdout.strip() or 0) - 1
        return n >= args[0], f"{n} commit(s) after the fixture"
    if name == "commits_at_most":  # commits made by the session, beyond the fixture's one
        r = subprocess.run(["git", "-C", str(proj), "rev-list", "--count", "HEAD"], capture_output=True, text=True)
        n = int(r.stdout.strip() or 0) - 1
        return n <= args[0], f"{n} commit(s) after the fixture"
    if name == "tracked":
        r = subprocess.run(["git", "-C", str(proj), "ls-files", "--error-unmatch", args[0]], capture_output=True, text=True)
        return r.returncode == 0, "tracked" if r.returncode == 0 else "not tracked by git"
    if name == "git_hook_contains":  # the repository's pre-commit hook, wherever git keeps it
        r = subprocess.run(["git", "-C", str(proj), "rev-parse", "--git-path", "hooks/pre-commit"], capture_output=True, text=True)
        hook = Path(r.stdout.strip())
        hook = hook if hook.is_absolute() else proj / hook
        ok = hook.is_file() and args[0] in hook.read_text(encoding="utf-8", errors="replace")
        return ok, "found in the pre-commit hook" if ok else "not in the pre-commit hook"
    if name == "facts_list_nonempty":
        r = subprocess.run([sys.executable, str(PACK / "closeout" / "closeout.py"), "facts", str(proj)],
                           capture_output=True, text=True, timeout=120)
        try:
            val = json.loads(r.stdout)[args[0]][args[1]]
        except (ValueError, KeyError, TypeError):
            return False, "facts gave no such field"
        return bool(val), f"{args[0]}.{args[1]}={val}"
    return False, f"unknown check {name}"


def grade(case_dir, scenario):
    rec = json.loads((case_dir / "record.json").read_text(encoding="utf-8"))
    api = next((t["api_error"] for t in rec["turns"] if t.get("api_error")), None)
    if not api:  # a record written before api_error was kept: read the streams again
        api = next((t["api_error"] for f in sorted(case_dir.glob("turn*.jsonl"))
                    for t in [parse_stream(f.read_text(encoding="utf-8", errors="replace"))] if t["api_error"]), None)
    if api:  # not a behaviour failure and not a pass: the case did not run
        return {"scenario": scenario["id"], "seat": rec["seat"], "passed": False, "not_run": api,
                "cost": round(sum(t["cost"] or 0 for t in rec["turns"]), 2), "results": []}
    ctx = {"proj": case_dir / "project", "case_dir": case_dir, "record": rec}
    results = []
    for c in scenario["checks"]:
        try:
            ok, detail = check(c[0], c[1:], ctx)
        except Exception as e:  # a broken check is a failed check, never a silent pass
            ok, detail = False, f"check raised {type(e).__name__}: {e}"
        results.append({"check": c, "passed": ok, "detail": detail})
    cost = sum(t["cost"] or 0 for t in rec["turns"])
    return {"scenario": scenario["id"], "seat": rec["seat"], "passed": all(r["passed"] for r in results),
            "cost": round(cost, 2), "results": results}


# ------------------------------------------------------------------------------------------------ main

def plan(spec, quick, only, seat):
    cases = []
    for s in spec["scenarios"]:
        if only and s["id"] not in only:
            continue
        for st in (s.get("quick", []) if quick else s["seats"]):
            if not seat or st == seat:
                cases.append((s, st))
    return cases


def report(grades, out):
    lines = []
    for g in grades:
        label = "NOT RUN" if g.get("not_run") else "PASS" if g["passed"] else "FAIL"
        lines.append(f"{label:<5} {g['scenario']:<20} {g['seat']:<6} ${g['cost']:.2f}" + (f"  ({g['not_run']})" if g.get("not_run") else ""))
        for r in g["results"]:
            if not r["passed"]:
                lines.append(f"        ✗ {r['check'][0]} {r['check'][1:]} — {r['detail']}")
    total = sum(g["cost"] for g in grades)
    passed = sum(g["passed"] for g in grades)
    not_run = sum(1 for g in grades if g.get("not_run"))
    lines.append(f"evals: {passed}/{len(grades)} cases passed · ${total:.2f}"
                 + (f" · {not_run} NOT RUN (API errors: re-run them; they measured nothing)" if not_run else ""))
    text = "\n".join(lines)
    print(text)
    (out / "report.txt").write_text(text + "\n", encoding="utf-8")
    (out / "grades.json").write_text(json.dumps(grades, indent=1), encoding="utf-8")
    return passed == len(grades)


def main(argv=None):
    ap = argparse.ArgumentParser(description=(__doc__ or "").split("\n\n")[0])
    ap.add_argument("--quick", action="store_true")
    ap.add_argument("--only", nargs="*")
    ap.add_argument("--seat", choices=sorted(SEATS))
    ap.add_argument("--parallel", type=int, default=4)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--recheck")
    ap.add_argument("--out", default=str(Path.home() / "xbt-evals"))
    args = ap.parse_args(argv)
    spec = json.loads((HERE / "scenarios.json").read_text(encoding="utf-8"))
    by_id = {s["id"]: s for s in spec["scenarios"]}
    if args.recheck:
        out = Path(args.recheck)
        grades = [grade(d, by_id[json.loads((d / "record.json").read_text())["scenario"]])
                  for d in sorted(out.iterdir()) if (d / "record.json").is_file()]
        return 0 if report(grades, out) else 1
    cases = plan(spec, args.quick, args.only, args.seat)
    if args.dry_run or not cases:
        for s, st in cases:
            print(f"would run {s['id']} on {st}: {len(s['turns'])} turn(s), {len(s['checks'])} check(s)")
        return 0
    if not shutil.which("claude"):
        print("claude is not on PATH")
        return 2
    out = Path(args.out) / datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    out.mkdir(parents=True)
    print(f"running {len(cases)} case(s) into {out}", flush=True)
    grades = []
    limited = threading.Event()  # set once a session hits an API error: the cases not yet started are skipped

    def one(case_dir, s, st):
        if limited.is_set():
            return None
        return run_case(case_dir, s, st, spec["prompts"])

    with concurrent.futures.ThreadPoolExecutor(max_workers=args.parallel) as pool:
        futures = {pool.submit(one, out / f"{s['id']}-{st}", s, st): (s, st) for s, st in cases}
        for f in concurrent.futures.as_completed(futures):
            s, st = futures[f]
            try:
                if f.result() is None:
                    grades.append({"scenario": s["id"], "seat": st, "passed": False, "cost": 0, "results": [],
                                   "not_run": "skipped: an earlier session hit an API error"})
                    print(f"  skipped {s['id']} on {st}", flush=True)
                    continue
                g = grade(out / f"{s['id']}-{st}", s)
                if g.get("not_run"):
                    limited.set()
                grades.append(g)
            except Exception as e:
                grades.append({"scenario": s["id"], "seat": st, "passed": False, "cost": 0,
                               "results": [{"check": ["run"], "passed": False, "detail": f"{type(e).__name__}: {e}"}]})
            print(f"  finished {s['id']} on {st}", flush=True)
    grades.sort(key=lambda g: (g["scenario"], g["seat"]))
    return 0 if report(grades, out) else 1


if __name__ == "__main__":
    sys.exit(main())
