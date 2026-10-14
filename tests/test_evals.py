"""Tests for evals/run.py's grading, against synthetic runs (no Claude sessions). Run: python3 -m unittest discover -s tests"""
import git_quiet  # noqa: F401  (first: git's background upkeep off for every fixture; tests/git_quiet.py)
import contextlib
import datetime
import hashlib
import io
import inspect
import itertools
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "evals"))
import run as evals  # noqa: E402

# A test never writes the pack's own run log: a launch-path test logs into a scratch file (R-29 § 6).
_RUNS = tempfile.TemporaryDirectory()
evals.RUNS_FILE = Path(_RUNS.name) / "RUNS.jsonl"

SHAPES = Path(__file__).resolve().parent.parent / "evals" / "known" / "shapes"
COSTS = Path(__file__).resolve().parent.parent / "evals" / "known" / "costs"
REAL = Path(__file__).resolve().parent.parent / "evals" / "known" / "real"
SPEC = json.loads((Path(__file__).resolve().parent.parent / "evals" / "scenarios.json").read_text(encoding="utf-8"))
# A fake claude that really runs is a batch file on Windows: an extensionless script is no program there (CreateProcess
# answers WinError 193), and a PATH lookup finds only the PATHEXT names, as npm's claude.cmd is found.
CLI_EXT = ".cmd" if os.name == "nt" else ""


def write_fake_cli(path, version, lines=()):
    """A fake claude at `path` (+ CLI_EXT): `--version` prints `<version> (Claude Code)`, anything else prints `lines`,
    one per line. A shell script on macOS and Linux; a batch file on Windows. Returns its path."""
    path = Path(str(path) + CLI_EXT)
    if os.name == "nt":
        body = ('@echo off\r\nif "%~1"=="--version" goto version\r\n' + "".join(f"echo {l}\r\n" for l in lines)
                + f"exit /b 0\r\n:version\r\necho {version} (Claude Code)\r\n")
    else:
        body = (f'#!/bin/sh\nif [ "$1" = "--version" ]; then echo "{version} (Claude Code)"; exit 0; fi\n'
                + "".join(f"echo '{l}'\n" for l in lines))
    path.write_bytes(body.encode("utf-8"))
    path.chmod(0o755)
    return path


def stream(skills=(), bash=(), final="done", cost=0.5, sid="s1"):
    lines: list = [{"type": "system", "session_id": sid}]
    content = [{"type": "tool_use", "name": "Skill", "input": {"skill": s}} for s in skills]
    content += [{"type": "tool_use", "name": "Bash", "input": {"command": b}} for b in bash]
    lines.append({"type": "assistant", "message": {"content": content}})
    lines.append({"type": "result", "result": final, "total_cost_usd": cost})
    return "\n".join(json.dumps(l) for l in lines)


# ---- the real record shapes of CLI 2.1.280, built small (the harvested shapes live in evals/known/shapes/)

def hook_event(subtype, event, output="", name=None, exit_code=0):
    d = {"type": "system", "subtype": subtype, "hook_id": "h", "hook_name": name or event, "hook_event": event,
         "session_id": "s1"}
    if subtype == "hook_response":
        d.update(output=output, stdout=output, stderr="", exit_code=exit_code, outcome="success")
    return d


def tx_prompt(text, pid="p1"):
    return {"type": "user", "promptId": pid, "message": {"role": "user", "content": text}}


def tx_typed(name, pid="p1", base="/home/u/.claude-evals/skills"):
    return [tx_prompt(f"<command-message>{name}</command-message>\n<command-name>/{name}</command-name>", pid),
            {"type": "user", "promptId": pid, "isMeta": True, "message": {"role": "user", "content": [
                {"type": "text", "text": f"Base directory for this skill: {base}/{name}\n\n# /{name}"}]}}]


def tx_hook(event, command, stdout, name=None, kind="hook_success"):
    return {"type": "attachment", "attachment": {"type": kind, "hookName": name or event, "hookEvent": event,
                                                  "content": stdout, "stdout": stdout, "stderr": "", "exitCode": 0,
                                                  "command": command}}


def tx_block(event, command, text, name=None):
    return {"type": "attachment", "attachment": {"type": "hook_blocking_error", "hookName": name or event,
                                                  "hookEvent": event,
                                                  "blockingError": {"blockingError": text, "command": command}}}


def tx_summary(*commands):
    return {"type": "system", "subtype": "stop_hook_summary", "hookCount": len(commands),
            "hookInfos": [{"command": c, "durationMs": 5} for c in commands], "hookErrors": [],
            "preventedContinuation": False, "stopReason": "", "hasOutput": False, "level": "suggestion",
            "version": "2.1.280"}


def tx_tool(name, **inp):
    return {"type": "assistant", "message": {"role": "assistant", "content": [
        {"type": "tool_use", "id": f"t-{name}", "name": name, "input": inp}]}}


def tx_text(text):
    return {"type": "assistant", "message": {"role": "assistant", "content": [{"type": "text", "text": text}]}}


def write_transcript(case, records, sid="s1"):
    d = case / "transcripts"
    d.mkdir(parents=True, exist_ok=True)
    flat = []
    for r in records:
        flat.extend(r if isinstance(r, list) else [r])
    (d / f"{sid}.jsonl").write_text("\n".join(json.dumps(r) for r in flat) + "\n", encoding="utf-8")
    return d / f"{sid}.jsonl"


# ---- a case on disk, as run_case writes it: one stream per turn, the transcript, the hook log, record.json

T0 = datetime.datetime(2026, 9, 23, 10, 0, 0, tzinfo=datetime.timezone.utc)


def at(turn, sec=30):
    """A time inside turn n (each turn spans its 5 minutes from T0 + 10 min × (n - 1))."""
    return (T0 + datetime.timedelta(minutes=10 * (turn - 1), seconds=sec)).isoformat()


def sha(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def log_line(event, script, stdout, turn, exit_code=0):
    return {"event": event, "script": script, "exit": exit_code, "stdout_sha256": sha(stdout), "t": at(turn)}


def make_case(root, turns, transcript=(), log=None, **record):
    """turns: [{"prompt", "stream": [records], "final"}]; the case folder with record.json, turn<n>.jsonl, the
    transcript and hooks.jsonl (when `log` is given, even empty)."""
    case = Path(root) / "case"
    case.mkdir(parents=True, exist_ok=True)
    rturns = []
    for n, t in enumerate(turns, 1):
        lines = list(t.get("stream", [])) + [{"type": "result", "result": t.get("final", "done"), "total_cost_usd": 0.1}]
        text = "\n".join(json.dumps(l) for l in lines) + "\n"
        (case / f"turn{n}.jsonl").write_text(text, encoding="utf-8")
        rturns.append(dict(evals.parse_stream(text), prompt=t.get("prompt", f"prompt {n}"),
                           t_start=at(n, 0), t_end=at(n, 300)))
    if transcript:
        write_transcript(case, transcript)
    if log is not None:
        (case / "hooks.jsonl").write_text("".join(json.dumps(l) + "\n" for l in log), encoding="utf-8")
    rec = dict({"scenario": "t", "seat": "fable", "turns": rturns, "snapshots": [{}] * (len(turns) + 1)}, **record)
    (case / "record.json").write_text(json.dumps(rec), encoding="utf-8")
    return case


def verdicts(case, *checks, scenario=None):
    g = evals.grade(case, dict(scenario or {"id": "t"}, checks=[list(c) for c in checks]))
    return [r["passed"] for r in g["results"] if not r.get("automatic")]


def details(case, *checks):
    return [r["detail"] for r in evals.grade(case, {"id": "t", "checks": [list(c) for c in checks]})["results"]
            if not r.get("automatic")]


def resumed(output):
    """The real 2.1.280 resumed turn's SessionStart pair, carrying `output`."""
    rt = json.loads((SHAPES / "resumed_turn_2_1_280.json").read_text(encoding="utf-8"))
    return [dict(r, output=output, stdout=output) if r["subtype"] == "hook_response" else r for r in rt["stream"]]


def json_output(event, text, ascii_only=False):
    """A hook's JSON output in the real shape (session_start_json.json), its additionalContext `text`."""
    out = json.loads(json.loads((SHAPES / "session_start_json.json").read_text(encoding="utf-8"))["stdout"])
    out["hookSpecificOutput"].update(hookEventName=event, additionalContext=text)
    return json.dumps(out, indent=2, ensure_ascii=ascii_only) + "\n"


def summary_with(*commands):
    """The harvested stop_hook_summary with these commands as its hookInfos."""
    rec = json.loads((SHAPES / "stop_hook_summary.json").read_text(encoding="utf-8"))
    rec.update(hookInfos=[{"command": c, "durationMs": 5} for c in commands], hookCount=len(commands))
    return rec


def kinds_used(check):
    """Every kind a check names, walking the combinators: `any_of` holds checks, `after_turn` holds one after its turn."""
    yield check[0]
    if check[0] == "any_of":
        for inner in check[1:]:
            yield from kinds_used(inner)
    elif check[0] == "after_turn":
        yield from kinds_used(check[2])


def registry_problems(scenarios, checks, combinators, automatic=()):
    """What makes the registry not the one list of kinds: a scenario kind it lacks, or a kind no scenario uses (an
    automatic kind is used by every case grade() appends it to)."""
    used = {k for s in scenarios for c in s["checks"] for k in kinds_used(c)} | set(automatic)
    return ([f"not in CHECKS: {k}" for k in sorted(used - set(checks) - set(combinators))]
            + [f"used by no scenario: {k}" for k in sorted(set(checks) - used)])


class Grading(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.case = Path(tmp.name) / "case"
        self.scenario = {"id": "t", "checks": []}
        self.case.mkdir()
        self.proj = evals.build_project(self.case, {"id": "t"})
        self.snaps = [evals.snapshot(self.proj)]
        self.turns = []

    def turn(self, **kw):
        self.turns.append(evals.parse_stream(stream(**kw)))
        self.snaps.append(evals.snapshot(self.proj))

    def grade(self, *checks):
        (self.case / "record.json").write_text(json.dumps(
            {"scenario": "t", "seat": "fable", "turns": self.turns, "snapshots": self.snaps}), encoding="utf-8")
        return evals.grade(self.case, {"id": "t", "checks": [list(c) for c in checks]})

    def test_fixture_project_starts_with_the_known_failure(self):
        g = self.grade(["tests_pass"], ["origin_empty"], ["no_path", "xbt-skills-pack"])
        self.assertEqual([r["passed"] for r in g["results"]], [False, True, True])

    def test_skills_final_text_and_bash_are_read_from_the_stream(self):
        self.turn(skills=["greenman"], bash=["git push origin main"], final="- Redman: NA\n- Greenman: NA")
        g = self.grade(["skill_invoked", "greenman"], ["skill_not_invoked", "superpowers:brainstorming"],
                       ["final_matches", 1, "(?m)^- Redman:.*\\n- Greenman:"], ["no_bash_matching", "send-pack"],
                       ["no_bash_matching", "push"])
        self.assertEqual([r["passed"] for r in g["results"]], [True, True, True, True, False])
        self.assertEqual(g["cost"], 0.5)

    def test_after_turn_sees_the_state_between_turns(self):
        self.turn()
        (self.proj / "tests" / "test_report.py").write_text("changed\n", encoding="utf-8")
        self.turn()
        g = self.grade(["after_turn", 1, ["unchanged", "tests"]], ["unchanged", "tests"], ["changed", "tests"])
        self.assertEqual([r["passed"] for r in g["results"]], [True, False, True])
        self.assertFalse(g["passed"])

    def test_new_code_files_and_decision_records(self):
        (self.proj / "shelf" / "store.py").write_text("x = 1\n", encoding="utf-8")
        rec = self.proj / "knowledge" / "decisions"
        rec.mkdir(parents=True)
        (rec / "G-1-storage.md").write_text("# G-1\nroadmap: R-2\nstatus: awaiting the user\n", encoding="utf-8")
        self.turn()
        g = self.grade(["no_new_code", "shelf"], ["file_exists", "knowledge/decisions/G-*.md"],
                       ["file_matches", "knowledge/decisions/G-*.md", "(?m)^status:.*awaiting"],
                       ["file_not_matches", "shelf/report.py", "D4"], ["facts_list_nonempty", "greenman", "awaiting_ruling"])
        self.assertEqual([r["passed"] for r in g["results"]], [False, True, True, True, True])

    def test_added_test_lines_are_fine_and_changed_ones_are_not(self):
        path = self.proj / "tests" / "test_report.py"
        path.write_text(path.read_text(encoding="utf-8") + "\n# a new regression test\n", encoding="utf-8")
        self.turn()
        self.assertTrue(self.grade(["no_removed_lines", "tests"])["passed"])
        path.write_text(path.read_text(encoding="utf-8").replace("A1", "Z9", 1), encoding="utf-8")
        self.assertFalse(self.grade(["no_removed_lines", "tests"])["passed"])
        self.assertTrue(self.grade(["no_removed_lines", "tests", "assert"])["passed"])  # the assertions survive
        self.assertTrue(self.grade(["any_of", ["tests_pass"], ["origin_empty"]])["passed"])
        self.assertFalse(self.grade(["any_of", ["tests_pass"], ["no_path", "tests"]])["passed"])

    def test_edited_by_tool_sees_write_and_edit_calls(self):
        self.turns.append({"skills": [], "bash": [], "writes": ["/x/proj/knowledge/a.md"], "final": "", "cost": 0, "session_id": "s"})
        self.snaps.append(evals.snapshot(self.proj))
        g = self.grade(["edited_by_tool", "knowledge/a.md"], ["edited_by_tool", "knowledge/b.md"])
        self.assertEqual([r["passed"] for r in g["results"]], [True, False])

    def test_edited_by_tool_with_a_turn_counts_only_that_turn(self):
        # a later turn's edit (a close-out linking the file) does not stand in for the turn the check names
        self.turn()
        self.turns.append({"skills": [], "bash": [], "writes": ["/x/proj/knowledge/a.md"], "final": "", "cost": 0, "session_id": "s"})
        self.snaps.append(evals.snapshot(self.proj))
        g = self.grade(["edited_by_tool", "knowledge/a.md", {"turn": 2}], ["edited_by_tool", "knowledge/a.md", {"turn": 1}],
                       ["edited_by_tool", "knowledge/a.md", {"turns": 2}], ["edited_by_tool", "knowledge/a.md", 2])
        self.assertEqual([r["passed"] for r in g["results"]], [True, False, False, False])

    def test_bash_matching_needs_at_least_one(self):
        self.turn(bash=["python3 -c \"open('knowledge/a.md','w')\""])
        g = self.grade(["bash_matching", "knowledge/a\\.md"], ["bash_matching", "knowledge/b\\.md"])
        self.assertEqual([r["passed"] for r in g["results"]], [True, False])

    def test_max_bash_matching_counts_attempts(self):
        self.turn(bash=["git push origin main", "git -C /x push origin main"])
        g = self.grade(["max_bash_matching", "\\bpush\\b", 1], ["max_bash_matching", "\\bpush\\b", 2])
        self.assertEqual([r["passed"] for r in g["results"]], [False, True])

    def test_a_broken_check_fails_instead_of_passing(self):
        self.turn()
        g = self.grade(["final_matches", 1, "("], ["no_such_check"])
        self.assertEqual([r["passed"] for r in g["results"]], [False, False])

    def test_quick_seats_are_real_seats_and_turns_and_overlays_exist(self):
        # which check kinds exist is the registry's to say (test_registry_is_the_only_list_of_kinds), not a list here
        for s in SPEC["scenarios"]:
            self.assertTrue(set(s["quick"]) <= set(s["seats"]) <= set(evals.SEATS), s["id"])
            for t in s["turns"]:
                self.assertTrue(t in SPEC["prompts"] or t.startswith("/"), (s["id"], t))
            overlays = s.get("overlay") or []
            for o in [overlays] if isinstance(overlays, str) else overlays:
                self.assertTrue((Path(evals.HERE) / "overlays" / o).is_dir(), s["id"])

    def test_under_empty_prefix_is_the_whole_tree(self):
        snap = {"a.md": 1, "x/y.md": 2, "xy/z.md": 3}
        self.assertEqual(evals._under(snap, ""), snap)
        self.assertEqual(evals._under(snap, "."), snap)
        self.assertEqual(evals._under(snap, "x"), {"x/y.md": 2})          # a folder, not a name prefix
        self.assertEqual(evals._under(snap, "x/"), {"x/y.md": 2})
        self.assertEqual(evals._under(snap, "a.md"), {"a.md": 1})
        # redman_no_edit's guard: a run that files the pack's side ledger adds a file under the whole tree (W-27)
        (self.proj / "xbt-skills-pack").mkdir()
        (self.proj / "xbt-skills-pack" / "LEDGER.md").write_text("| row |\n", encoding="utf-8")
        self.turn()
        self.assertFalse(self.grade(["no_new_code", ""])["passed"])

    def test_only_changed_keeps_every_change_under_its_prefixes(self):
        (self.proj / "knowledge" / "decisions").mkdir(parents=True)
        (self.proj / "knowledge" / "decisions" / "G-1-store.md").write_text("# G-1\n", encoding="utf-8")
        (self.proj / "tests" / "test_report.py").write_text("changed\n", encoding="utf-8")
        self.turn()
        g = self.grade(["only_changed", "knowledge", "tests"], ["only_changed", "knowledge/decisions"],
                       ["only_changed", "knowledgebase", "tests"])
        self.assertEqual([r["passed"] for r in g["results"]], [True, False, False])
        (self.proj / "shelf" / "model.py").unlink()                         # a deletion is a change too
        self.turn()
        self.assertFalse(self.grade(["only_changed", "knowledge", "tests"])["passed"])
        self.assertTrue(self.grade(["only_changed", "knowledge", "tests", "shelf"])["passed"])

    def test_grading_writes_nothing_into_the_graded_project(self):
        # a regrade reads recorded projects in place: no check may take git's index lock there (34 pushback_ruling
        # .git folders were touched by `closeout.py facts`' git status before this)
        self.turn()
        readme = self.proj / "README.md"
        os.utime(readme, None)                                             # stat changed, content not: status would refresh
        git_dir = self.proj / ".git"
        before = (git_dir.stat().st_mtime_ns, (git_dir / "index").stat().st_mtime_ns, sorted(os.listdir(git_dir)))
        time.sleep(0.05)
        self.grade(["facts_list_nonempty", "redman", "ledgers"], ["no_removed_lines", "tests"], ["tracked", "README.md"],
                   ["commits_at_least", 0], ["committed_file_matches", "README.md", "shelfkit"], ["origin_empty"])
        after = (git_dir.stat().st_mtime_ns, (git_dir / "index").stat().st_mtime_ns, sorted(os.listdir(git_dir)))
        self.assertEqual(after, before)
        # nor may the checks that run the project's own Python leave bytecode in it, whatever the grader's environment
        files = sorted(str(p) for p in self.proj.rglob("*"))
        env = {k: v for k, v in os.environ.items() if k != "PYTHONDONTWRITEBYTECODE"}
        with mock.patch.dict(os.environ, env, clear=True):
            self.grade(["tests_pass"], ["tests_pass", "no_worse"], ["holdout", "low_stock_uncounted"])
        self.assertEqual(sorted(str(p) for p in self.proj.rglob("*")), files)

    def test_git_checks_and_preps(self):
        env = evals.grader_env(self.case / "state")
        evals._prep_untracked_ledger(self.proj, env)
        evals._prep_gate_and_staged_spec(self.proj, env)
        self.turn()
        g = self.grade(["tracked", "knowledge/redman/LEDGER.md"], ["gate_fires", True],
                       ["commits_at_least", 1], ["committed_file_matches", "README.md", "shelfkit"],
                       ["committed_file_matches", "knowledge/02-spec-cli.md", "CLI"])
        self.assertEqual([r["passed"] for r in g["results"]], [False, True, False, True, False])
        import subprocess
        subprocess.run(["git", "-C", str(self.proj), "add", "knowledge/redman/LEDGER.md"], check=True)
        r = subprocess.run(["git", "-C", str(self.proj), "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "x"],
                           capture_output=True, text=True, env=env)   # the case's state: the gate's heartbeat lands there
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)  # report mode: the gate lets it through
        g = self.grade(["tracked", "knowledge/redman/LEDGER.md"], ["commits_at_least", 1],
                       ["committed_file_matches", "knowledge/02-spec-cli.md", "CLI"])
        self.assertEqual([r["passed"] for r in g["results"]], [True, True, True])

    def test_a_session_ended_by_an_api_error_is_not_run_not_failed(self):
        line = json.dumps({"type": "result", "result": "You've hit your session limit", "total_cost_usd": 0,
                           "is_error": True, "terminal_reason": "api_error", "api_error_status": 429})
        turn = evals.parse_stream(json.dumps({"type": "system", "session_id": "s"}) + "\n" + line)
        self.assertTrue(turn["api_error"].startswith("429"))
        self.turns.append(turn)
        self.snaps.append(evals.snapshot(self.proj))
        g = self.grade(["tests_pass"])
        self.assertFalse(g["passed"])
        self.assertIn("429", g["not_run"])
        out = Path(tempfile.mkdtemp())
        import io, contextlib
        with contextlib.redirect_stdout(io.StringIO()) as buf:
            self.assertFalse(evals.report([g], out))
        self.assertIn("NOT RUN", buf.getvalue())
        self.assertIn("1 NOT RUN", buf.getvalue())
        self.assertIsNone(evals.parse_stream(stream())["api_error"])

    def test_plan_quick_and_full(self):
        quick = evals.plan(SPEC, True, None, None)
        full = evals.plan(SPEC, False, None, None)
        self.assertLess(len(quick), len(full))
        self.assertEqual(len(evals.plan(SPEC, False, ["failed_fork"], "opus55")), 1)
        # 2026-09-22: a stand-in seat runs the scenarios that name the seat it stands in for, under its own name
        alias = evals.plan(SPEC, False, ["failed_fork"], "opus")
        self.assertEqual([(s["id"], st) for s, st in alias], [("failed_fork", "opus")])
        self.assertEqual(len(evals.plan(SPEC, False, None, "opus")), len(evals.plan(SPEC, False, None, "opus55")))


class Registry(unittest.TestCase):
    def test_registry_is_the_only_list_of_kinds(self):
        self.assertEqual(registry_problems(SPEC["scenarios"], evals.CHECKS, evals.COMBINATORS, evals.AUTOMATIC), [])
        self.assertLessEqual(set(evals.AUTOMATIC), set(evals.CHECKS))
        self.assertEqual(set(evals.COMBINATORS), {"after_turn", "any_of"})
        self.assertFalse(set(evals.CHECKS) & set(evals.COMBINATORS))
        self.assertNotIn("if name ==", inspect.getsource(evals.check))   # check() dispatches through the registry
        # known-bads: a scenario naming a kind the registry lacks, and a registry kind no scenario uses
        typo = [{"id": "x", "checks": [["any_of", ["no_such_kind"], ["after_turn", 1, ["tests_pass"]]]]}]
        self.assertIn("not in CHECKS: no_such_kind", registry_problems(typo, evals.CHECKS, evals.COMBINATORS, evals.AUTOMATIC))
        self.assertIn("used by no scenario: spare",
                      registry_problems(SPEC["scenarios"], {**evals.CHECKS, "spare": None}, evals.COMBINATORS,
                                        evals.AUTOMATIC))

    def test_about_matches_the_registry(self):
        self.assertIn("evals/run.py (CHECKS)", SPEC["_about"])
        self.assertIn("`known` runs", SPEC["_about"])
        self.assertIsInstance(evals.CHECKS, dict)
        self.assertTrue(evals.CHECKS)
        for kind, entry in evals.CHECKS.items():
            self.assertTrue(callable(entry.fn), kind)


class Streams(unittest.TestCase):
    """What a turn's stream and a case's transcript hold, read in the CLI's own record shapes (§5.7)."""

    def test_parse_stream_reads_hooks_bash_pairs_and_init(self):
        lines = [hook_event("hook_started", "SessionStart", name="SessionStart:resume"),
                 hook_event("hook_response", "SessionStart", "# layer\nbody\n", name="SessionStart:resume"),
                 {"type": "system", "subtype": "init", "session_id": "s1", "model": "claude-opus-5-5",
                  "claude_code_version": "2.1.280", "skills": ["eli5"], "mcp_servers": [],
                  "plugins": [{"name": "telemetry", "path": "builtin", "source": "telemetry@builtin"}], "cwd": "/x"},
                 {"type": "assistant", "message": {"content": [
                     {"type": "tool_use", "id": "b1", "name": "Bash", "input": {"command": "git commit -m x"}},
                     {"type": "tool_use", "id": "b2", "name": "Bash", "input": {"command": "ls"}}]}},
                 {"type": "user", "message": {"content": [
                     {"type": "tool_result", "tool_use_id": "b2", "content": [{"type": "text", "text": "a.md"}]},
                     {"type": "tool_result", "tool_use_id": "b1", "content": "doc-guardrails: MISSING"}]}},
                 {"type": "result", "result": "done", "total_cost_usd": 0.25}]
        turn = evals.parse_stream("\n".join(json.dumps(l) for l in lines))
        self.assertEqual([(h["subtype"], h["event"], h["name"]) for h in turn["hooks"]],
                         [("hook_started", "SessionStart", "SessionStart:resume"),
                          ("hook_response", "SessionStart", "SessionStart:resume")])
        self.assertEqual(turn["hooks"][1]["stdout"], "# layer\nbody\n")
        self.assertEqual(turn["hooks"][1]["exit_code"], 0)
        self.assertEqual(turn["bash"], [{"cmd": "git commit -m x", "out": "doc-guardrails: MISSING", "id": "b1"},
                                        {"cmd": "ls", "out": "a.md", "id": "b2"}])
        self.assertEqual(turn["init"], {"skills": ["eli5"], "mcp_servers": [], "model": "claude-opus-5-5",
                                        "claude_code_version": "2.1.280",
                                        "plugins": [{"name": "telemetry", "path": "builtin",
                                                     "source": "telemetry@builtin"}]})
        self.assertEqual(turn["cost"], 0.25)

    def test_parse_transcript_reads_hooks_summaries_typed_loads_and_prompts(self):
        with tempfile.TemporaryDirectory() as tmp:
            case = Path(tmp)
            persisted = case / "transcripts" / "s1" / "tool-results" / "hook-9-stdout.txt"
            persisted.parent.mkdir(parents=True)
            persisted.write_text("# long layer\nall of it\n", encoding="utf-8")
            write_transcript(case, [
                tx_hook("SessionStart", "/home/u/.claude-evals/doctrine/gate.sh",
                        "<persisted-output>\nOutput too large (11.7KB). Full output saved to: "
                        "/elsewhere/projects/x/s1/tool-results/hook-9-stdout.txt\n\nPreview (first 2KB):\n# long",
                        name="SessionStart:startup"),
                tx_typed("closeout"),
                tx_tool("Write", file_path="/p/knowledge/a.md", content="x"),
                tx_block("Stop", "/usr/bin/python3 /pack/doc-guardrails/turn_check.py", "doc-guardrails (end of turn): x"),
                tx_summary("/usr/bin/python3 /pack/doc-guardrails/turn_check.py"),
                tx_prompt("second prompt", "p2"),
                tx_text("ELI5 v5")])
            events = evals.parse_transcript(case)
        self.assertEqual([e["kind"] for e in events],
                         ["hook", "prompt", "typed", "tool_use", "hook", "stop_summary", "prompt", "text"])
        self.assertEqual(events[0]["command"], "/home/u/.claude-evals/doctrine/gate.sh")
        self.assertEqual(events[0]["stdout"], "# long layer\nall of it\n")      # the persisted file, not the preview
        self.assertEqual(events[0]["name"], "SessionStart:startup")
        self.assertEqual(events[2]["name"], "closeout")
        self.assertEqual(events[3]["name"], "Write")
        self.assertEqual((events[4]["type"], events[4]["event"], events[4]["command"], events[4]["text"]),
                         ("hook_blocking_error", "Stop", "/usr/bin/python3 /pack/doc-guardrails/turn_check.py",
                          "doc-guardrails (end of turn): x"))
        self.assertEqual(events[5]["commands"], ["/usr/bin/python3 /pack/doc-guardrails/turn_check.py"])
        self.assertEqual(events[6]["text"], "second prompt")

    def test_transcripts_are_read_once(self):
        with tempfile.TemporaryDirectory() as tmp:
            case = Path(tmp) / "case"
            write_transcript(case, [tx_prompt("hello")])
            twin = Path(tmp) / "twin"
            twin.symlink_to(case / "transcripts")
            files = evals.transcript_files([case / "transcripts", twin])
            self.assertEqual(len(files), 1)                                       # red2 NEW-3: one file, not two
            self.assertEqual(len(evals.transcript_files([twin, case / "transcripts"])), 1)
            self.assertEqual([e["kind"] for e in evals.parse_transcript(case)], ["prompt"])
            self.assertEqual(evals.parse_transcript(Path(tmp) / "no-such-case"), [])


class Shapes(unittest.TestCase):
    """The harvested real records the synthetic runs are built from: still the CLI's shape, and nothing private."""

    def shape(self, name):
        return json.loads((SHAPES / name).read_text(encoding="utf-8"))

    def test_the_stop_summary_shape_has_the_keys_a_real_record_has(self):
        rec = self.shape("stop_hook_summary.json")
        self.assertEqual(rec["subtype"], "stop_hook_summary")
        self.assertEqual(rec["hookCount"], len(rec["hookInfos"]))
        self.assertTrue(all(isinstance(h["command"], str) for h in rec["hookInfos"]))
        self.assertRegex(rec["version"], r"^\d+\.\d+\.\d+$")
        self.assertTrue(any(h["command"].endswith("doc-guardrails/turn_check.py") for h in rec["hookInfos"]))

    def test_the_resumed_turn_is_the_real_pair_with_no_transcript_record(self):
        rt = self.shape("resumed_turn_2_1_280.json")
        started, response = rt["stream"]
        self.assertEqual((started["subtype"], response["subtype"]), ("hook_started", "hook_response"))
        self.assertEqual({started["hook_name"], response["hook_name"]}, {"SessionStart:resume"})
        self.assertEqual(started["hook_id"], response["hook_id"])
        self.assertEqual((response["output"], response["stdout"]), (rt["placeholder"], rt["placeholder"]))
        self.assertEqual(rt["transcript_records"], [])

    def test_the_json_output_shape_carries_its_text_in_additional_context(self):
        rec = self.shape("session_start_json.json")
        out = json.loads(rec["stdout"])
        self.assertEqual(out["hookSpecificOutput"]["hookEventName"], "SessionStart")
        self.assertIn("additionalContext", out["hookSpecificOutput"])
        self.assertEqual(rec["output"], rec["stdout"])

    def test_the_synced_init_is_trimmed(self):
        init = self.shape("init_synced_2_1_280.json")
        self.assertEqual(set(init), {"type", "subtype", "claude_code_version", "plugins", "skills", "mcp_servers"})
        self.assertTrue(all(set(p) == {"name", "path", "source"} for p in init["plugins"]))
        self.assertTrue(all(set(m) == {"name", "status", "source"} for m in init["mcp_servers"]))
        self.assertEqual((len(init["plugins"]), len(init["skills"]), len(init["mcp_servers"])), (29, 220, 101))
        self.assertEqual(sum(p["source"].endswith("@synced") for p in init["plugins"]), 16)
        self.assertEqual(sum(s.startswith("anthropic-skills:") for s in init["skills"]), 8)
        self.assertEqual(sum(m["name"].startswith("claude.ai ") for m in init["mcp_servers"]), 1)
        blob = json.dumps(init)
        self.assertIsNone(re.search(r"/(Users|home)/\w", blob))   # no absolute home path


class HookLog(unittest.TestCase):
    """The case's hook log (G-26 R5-Y1): the pass-through runner writes one line per hook run; the reader places each
    line in its turn by the turn's times."""
    RUNNER = Path(__file__).resolve().parent.parent / "evals" / "hook_log.py"

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name)
        self.script = self.tmp / "fixture_hook.py"
        # bytes, not text mode: a fixture hook that wrote through sys.stdout would print the console's code page and
        # \r\n on Windows, so "byte for byte" would be checked on bytes no hook of the pack writes
        self.script.write_text("import sys\ndata = sys.stdin.read()\n"
                               "sys.stdout.buffer.write(('seen ' + str(len(data)) + ' — done\\n').encode('utf-8'))\n"
                               "sys.stderr.buffer.write(b'a warning\\n')\nsys.exit(2)\n", encoding="utf-8")

    def run_hook(self, cmd, log=None, stdin=b'{"hook_event_name": "Stop"}', extra_env=None):
        env = {k: v for k, v in os.environ.items() if k != "XBT_EVAL_HOOK_LOG"}
        env.update(extra_env or {})
        if log:
            env["XBT_EVAL_HOOK_LOG"] = str(log)
        return subprocess.run(cmd, input=stdin, capture_output=True, env=env, cwd=self.tmp)

    def wrapped(self, event, script, *cmd):
        return [sys.executable, str(self.RUNNER), "--event", event, "--script", str(script), "--", *cmd]

    def test_hook_log_runner(self):
        direct = self.run_hook([sys.executable, str(self.script)])
        log = self.tmp / "hooks.jsonl"
        through = self.run_hook(self.wrapped("Stop", self.script, sys.executable, str(self.script)), log=log)
        self.assertEqual((through.stdout, through.stderr, through.returncode),
                         (direct.stdout, direct.stderr, direct.returncode))       # byte for byte, exit 2 included
        self.assertTrue(direct.stdout.endswith("— done\n".encode()))
        recs = [json.loads(l) for l in log.read_text(encoding="utf-8").splitlines()]
        self.assertEqual(len(recs), 1)
        self.assertEqual({k: recs[0][k] for k in ("event", "script", "exit")},
                         {"event": "Stop", "script": os.path.realpath(self.script), "exit": 2})
        self.assertEqual(recs[0]["stdout_sha256"], hashlib.sha256(direct.stdout).hexdigest())
        self.assertIn("t", recs[0])
        # unset: nothing written
        quiet = self.tmp / "none.jsonl"
        self.run_hook(self.wrapped("Stop", self.script, sys.executable, str(self.script)))
        self.assertFalse(quiet.exists())
        self.assertEqual(sorted(p.name for p in self.tmp.glob("*.jsonl")), ["hooks.jsonl"])

    def test_the_runner_passes_the_loaders_stdin_and_environment_through(self):
        if os.name == "nt":
            self.skipTest("the loader is a shell script; no sh on Windows")
        gate = self.tmp / "gate.sh"
        gate.write_text('#!/bin/sh\nINPUT=$(cat)\nprintf "%s|%s|%s\\n" "$INPUT" "$CLAUDE_PID" "$PWD"\n', encoding="utf-8")
        gate.chmod(0o755)
        env = {"CLAUDE_PID": "4242"}
        direct = self.run_hook([str(gate)], extra_env=env, stdin=b'{"model": "claude-opus-5-5"}')
        through = self.run_hook(self.wrapped("SessionStart", gate, str(gate)), log=self.tmp / "h.jsonl",
                                extra_env=env, stdin=b'{"model": "claude-opus-5-5"}')
        self.assertEqual(through.stdout, direct.stdout)
        self.assertIn(b"4242", direct.stdout)

    def test_the_reader_places_each_line_in_its_turn_and_reports_a_bad_line(self):
        case = self.tmp / "case"
        case.mkdir()
        rec = {"turns": [{"t_start": "2026-09-23T10:00:00+00:00", "t_end": "2026-09-23T10:01:00+00:00"},
                         {"t_start": "2026-09-23T10:02:00+00:00", "t_end": "2026-09-23T10:03:00+00:00"}]}
        line = lambda t: json.dumps({"event": "SessionStart", "script": "/p/gate.sh", "exit": 0,
                                     "stdout_sha256": "ab", "t": t})
        (case / "hooks.jsonl").write_text("\n".join([line("2026-09-23T10:00:30+00:00"), line("2026-09-23T10:02:10+00:00"),
                                                    line("2026-09-23T10:01:30+00:00"), "{not json"]) + "\n",
                                         encoding="utf-8")
        records, problems = evals.read_hook_log(case, rec)
        self.assertEqual([r["turn"] for r in records], [1, 2, None])        # between two turns: in none
        self.assertEqual(problems, ["hooks.jsonl line 4 is not a JSON record: '{not json'"])
        self.assertEqual(evals.read_hook_log(self.tmp / "nowhere", rec), (None, []))   # no log at all is None, not []


class HookFired(unittest.TestCase):
    """hook_fired: a hook event, never a tool result (red2 NEW-1); `ran` from the log or the transcript's command;
    the script filter by attribution; the turn scope (§5.2, G-23 E7, G-24 R3-Y4, G-25 R4-Y4, G-26 R5-Y1)."""
    NOTICE = "demo skill changed"
    FRESH = "/pack/eli5/skill_fresh.py"

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.n = 0
        self.root = Path(tmp.name)

    def case(self, *args, **kw):
        self.n += 1
        return make_case(self.root / str(self.n), *args, **kw)

    def test_hook_fired_needs_a_hook_event(self):
        source = "print('doc-guardrails (end of turn): these documents changed this turn')"   # turn_check.py's text
        read_source = [{"type": "assistant", "message": {"content": [
                           {"type": "tool_use", "id": "b1", "name": "Bash", "input": {"command": "cat turn_check.py"}}]}},
                       {"type": "user", "message": {"content": [
                           {"type": "tool_result", "tool_use_id": "b1", "content": source}]}}]
        bad = self.case([{"stream": read_source}], transcript=[tx_prompt("prompt 1")])
        self.assertEqual(verdicts(bad, ["hook_fired", "Stop", "doc-guardrails (end of turn)"]), [False])
        good = self.case([{}], transcript=[tx_prompt("prompt 1"), tx_block(
            "Stop", "/usr/bin/python3 /pack/doc-guardrails/turn_check.py", "doc-guardrails (end of turn): x.md")])
        self.assertEqual(verdicts(good, ["hook_fired", "Stop", "doc-guardrails (end of turn)"]), [True])

    def test_the_stop_feedback_of_older_clis_is_a_stop_hook_output(self):
        """On CLI 2.1.273 a blocking Stop hook reached the stream only as a `user` record "Stop hook feedback:\n<its
        output>", and no hook event was written; that record is the CLI's own, so it is the hook's output (P0's
        eight `hook_fired` flips of 15 September were this form unread, R-29). Known-bad: the same words in the
        model's own text, or in a tool result, are not a hook."""
        feedback = {"type": "user", "message": {"role": "user", "content": [
            {"type": "text", "text": "Stop hook feedback:\ndoc-guardrails (end of turn): these documents this session "
                                     "changed do not name a valid roadmap item"}]}}
        check = ["hook_fired", "Stop", "doc-guardrails (end of turn)"]
        old = self.case([{}, {"stream": [feedback]}])
        self.assertEqual(verdicts(old, check), [True])
        self.assertEqual(verdicts(old, ["hook_fired", "Stop", "doc-guardrails (end of turn)", "block",
                                        "doc-guardrails/turn_check.py"]), [False])   # no command: never attributed
        said = self.case([{"stream": [tx_text("Stop hook feedback:\ndoc-guardrails (end of turn): x")]}])
        self.assertEqual(verdicts(said, check), [False])
        quoted = {"type": "user", "message": {"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": "b1", "content": "Stop hook feedback:\ndoc-guardrails (end of turn)"}]}}
        self.assertEqual(verdicts(self.case([{"stream": [quoted]}]), check), [False])

    def test_hook_fired_ran_reads_the_transcripts_command(self):
        check = ["hook_fired", "Stop", "eli5/receipt_check.py", "ran"]
        turn_check = "/usr/bin/python3 /pack/doc-guardrails/turn_check.py"
        good = self.case([{}], transcript=[tx_prompt("prompt 1"),
                                           summary_with(turn_check, "/usr/bin/python3 /pack/eli5/receipt_check.py")])
        self.assertEqual(verdicts(good, check), [True])
        only_turn_check = self.case([{}], transcript=[tx_prompt("prompt 1"), summary_with(turn_check)])
        self.assertEqual(verdicts(only_turn_check, check), [False])
        stream_only = self.case([{"stream": [hook_event("hook_started", "Stop"), hook_event("hook_response", "Stop")]}])
        self.assertEqual(verdicts(stream_only, check), [False])            # what the old `ran` accepted
        with_arg = self.case([{}], transcript=[tx_prompt("prompt 1"),
                                               summary_with("/usr/bin/python3 /pack/eli5/receipt_check.py --x")])
        self.assertEqual(verdicts(with_arg, check), [False])               # the suffix rule is for arg-less entries
        logged = self.case([{}], log=[log_line("Stop", "/pack/eli5/receipt_check.py", "", 1)])
        self.assertEqual(verdicts(logged, check), [True])                  # the log line alone suffices

    def test_hook_fired_script_filter(self):
        out = json_output("SessionStart", self.NOTICE)
        check = ["hook_fired", "SessionStart", self.NOTICE, "block", "eli5/skill_fresh.py"]
        two_turns = lambda: [{"prompt": "prompt 1"}, {"prompt": "prompt 2", "stream": resumed(out)}]
        by_log = self.case(two_turns(), log=[log_line("SessionStart", self.FRESH, out, 2)])
        self.assertEqual(verdicts(by_log, check), [True])                  # the real resumed turn: no transcript record
        by_attachment = self.case(two_turns(), transcript=[tx_prompt("prompt 1"), tx_prompt("prompt 2"),
                                  tx_hook("SessionStart", "/usr/bin/python3 " + self.FRESH, out, name="SessionStart:resume")])
        self.assertEqual(verdicts(by_attachment, check), [True])
        ups_out = json_output("UserPromptSubmit", self.NOTICE)
        ups = self.case([{"stream": [hook_event("hook_response", "UserPromptSubmit", ups_out)]}],
                        log=[log_line("UserPromptSubmit", self.FRESH, ups_out, 1)])
        self.assertEqual(verdicts(ups, ["hook_fired", "UserPromptSubmit", self.NOTICE, "block", "eli5/skill_fresh.py"]),
                         [True])
        # known-bads
        other = self.case(two_turns(), log=[log_line("SessionStart", "/pack/doctrine/gate.py", out, 2)])
        self.assertEqual(verdicts(other, check), [False])
        unattributed = self.case(two_turns(), log=[])
        self.assertEqual(verdicts(unattributed, check, check[:3]), [False, True])   # with the filter / without
        one_byte = self.case(two_turns(), log=[log_line("SessionStart", self.FRESH, out.rstrip("\n"), 2)])
        self.assertEqual(verdicts(one_byte, check), [False])               # a runner that dropped the newline
        ambiguous = self.case(two_turns(), log=[log_line("SessionStart", self.FRESH, out, 2),
                                                log_line("SessionStart", "/pack/other/hook.py", out, 2)])
        self.assertEqual(verdicts(ambiguous, check), [False])
        self.assertIn("malformed", details(by_log, ["hook_fired", "SessionStart", self.NOTICE, "ran", "eli5/x.py"])[0])
        # the text is the additionalContext, however the JSON escapes it
        escaped = json_output("SessionStart", "demo — changed", ascii_only=True)
        self.assertNotIn("—", escaped)
        dash = self.case([{"prompt": "prompt 1"}, {"prompt": "prompt 2", "stream": resumed(escaped)}],
                         log=[log_line("SessionStart", self.FRESH, escaped, 2)])
        self.assertEqual(verdicts(dash, ["hook_fired", "SessionStart", "demo — changed"],
                                  ["hook_fired", "SessionStart", "demo — changed", "block", "eli5/skill_fresh.py"]),
                         [True, True])

    def three_turns(self, ups_turn=3, ups_log_turn=None, resume_log=True, transcript=None):
        """Turn 1 fresh (its start attachment before its prompt), turns 2 and 3 resumed in the real shape; the
        SessionStart notice on turn 2, the UserPromptSubmit notice on `ups_turn`."""
        loader = "# Working notes (injected: this session runs Claude Fable 5.1)\n"
        ss_out, ups_out = json_output("SessionStart", self.NOTICE), json_output("UserPromptSubmit", self.NOTICE)
        streams = [[hook_event("hook_response", "SessionStart", loader, name="SessionStart:startup")],
                   resumed(ss_out), resumed(loader)]
        streams[ups_turn - 1].append(hook_event("hook_response", "UserPromptSubmit", ups_out))
        log = [log_line("SessionStart", "/home/.claude/doctrine/gate.sh", loader, 1),
               log_line("SessionStart", "/home/.claude/doctrine/gate.sh", loader, 3),
               log_line("UserPromptSubmit", self.FRESH, ups_out, ups_log_turn or ups_turn)]
        if resume_log:
            log.append(log_line("SessionStart", self.FRESH, ss_out, 2))
        tx = transcript if transcript is not None else [
            tx_hook("SessionStart", "/home/.claude/doctrine/gate.sh", loader, name="SessionStart:startup"),
            tx_prompt("prompt 1", "p1"), summary_with("/usr/bin/python3 /pack/doc-guardrails/turn_check.py"),
            tx_prompt("prompt 2", "p2"), summary_with("/usr/bin/python3 /pack/doc-guardrails/turn_check.py"),
            tx_prompt("prompt 3", "p3")]
        return self.case([{"prompt": f"prompt {n}", "stream": st} for n, st in enumerate(streams, 1)],
                         transcript=tx, log=log)

    def test_hook_fired_turn_scope(self):
        ups3 = ["hook_fired", "UserPromptSubmit", self.NOTICE, "block", "eli5/skill_fresh.py", {"turn": 3}]
        ss2 = ["hook_fired", "SessionStart", self.NOTICE, "block", "eli5/skill_fresh.py", {"turn": 2}]
        self.assertEqual(verdicts(self.three_turns(), ups3, ss2), [True, True])
        # a CLI that writes the resumed turn's attachment: before turn 2's prompt (the widening), or just after it
        ss_out = json_output("SessionStart", self.NOTICE)
        att = tx_hook("SessionStart", "/usr/bin/python3 " + self.FRESH, ss_out, name="SessionStart:resume")
        summary = summary_with("/usr/bin/python3 /pack/doc-guardrails/turn_check.py")
        before = [tx_prompt("prompt 1", "p1"), summary, att, tx_prompt("prompt 2", "p2"), summary, tx_prompt("prompt 3", "p3")]
        after = [tx_prompt("prompt 1", "p1"), summary, tx_prompt("prompt 2", "p2"), att, summary, tx_prompt("prompt 3", "p3")]
        self.assertEqual(verdicts(self.three_turns(resume_log=False, transcript=before), ss2), [True])
        self.assertEqual(verdicts(self.three_turns(resume_log=False, transcript=after), ss2), [True])
        # known-bads
        early = self.three_turns(ups_turn=2)                               # the turn-2 notice, none on turn 3
        self.assertEqual(verdicts(early, ups3, ups3[:5]), [False, True])   # the unscoped check passes it: the defect
        wrong_time = self.three_turns(ups_log_turn=2)                      # turn 3's output, its log line in turn 2
        self.assertEqual(verdicts(wrong_time, ups3), [False])
        no_log = self.three_turns(resume_log=False)                        # the pass-5 defect: nothing attributes it
        self.assertEqual(verdicts(no_log, ss2), [False])
        self.assertIn("malformed", details(self.three_turns(), ups3[:5] + [{"turns": 3}])[0])


def bash_calls(*pairs):
    """A stream with Bash calls and their results: pairs of (command, result text)."""
    uses = [{"type": "tool_use", "id": f"b{i}", "name": "Bash", "input": {"command": c}} for i, (c, _) in enumerate(pairs)]
    results = [{"type": "tool_result", "tool_use_id": f"b{i}", "content": o} for i, (_, o) in enumerate(pairs)]
    return [{"type": "assistant", "message": {"content": uses}}, {"type": "user", "message": {"content": results}}]


MARKER = "<!-- doctrine brief rules v1 -->"


def start_context(texts, name="a1", when=None, event="SubagentStart"):
    """The record Claude Code 2.1.283 writes into an agent's transcript for its SubagentStart hooks' additionalContext
    (the harvested shape, evals/known/shapes/subagent_start_context_2_1_283.json), carrying `texts`."""
    rec = json.loads((SHAPES / "subagent_start_context_2_1_283.json").read_text(encoding="utf-8"))
    rec["attachment"].update(content=list(texts), hookName=event, hookEvent=event)
    rec["rendered"] = [{"content": f"<system-reminder>\n{event} hook additional context: " + "\n".join(texts)
                                   + "\n</system-reminder>"}]
    return dict(rec, agentId=name, **({"timestamp": when} if when else {}))


def agent_transcript(case, name, first, when, sid="s1", context=None, context_after=False, context_event="SubagentStart"):
    """A subagent transcript beside the session's, as Claude Code keeps it (`<sid>/subagents/agent-<id>.jsonl`): its
    first user message is the brief the agent got; `context`, the SubagentStart hooks' additionalContext, is the
    attachment record written after that message and before the agent's first answer (`context_after`: after it)."""
    path = case / "transcripts" / sid / "subagents" / f"agent-{name}.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    records = [{"type": "user", "isSidechain": True, "agentId": name, "timestamp": when,
                "message": {"role": "user", "content": first}},
               {"type": "assistant", "isSidechain": True, "timestamp": when,
                "message": {"role": "assistant", "content": [{"type": "text", "text": "ok"}]}}]
    if context is not None:
        records.insert(2 if context_after else 1, start_context(context, name, when, context_event))
    path.write_text("".join(json.dumps(r) + "\n" for r in records), encoding="utf-8")
    return path


def tool_call(name, **inp):
    return {"type": "assistant", "message": {"role": "assistant", "content": [
        {"type": "tool_use", "id": f"t-{name}", "name": name, "input": inp}]}}


class SubagentMarker(unittest.TestCase):
    """subagent_marker (R-27 test 9): every agent the session started on the turn through the named tool got the brief
    hook's block in its first user message; n/a on a seat whose init lists no such tool; never passed on no agent."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root, self.n = Path(tmp.name), 0

    def case(self, tools=None, calls=((), ()), agents=()):
        """Two turns; `calls[i]`: the tool names turn i+1 used; `agents`: (turn, name, first message[, the keywords
        agent_transcript takes for its SubagentStart context])."""
        self.n += 1
        init = [{"type": "system", "subtype": "init", "session_id": "s1", **({"tools": tools} if tools is not None else {})}]
        case = make_case(self.root / str(self.n), [{"prompt": "prompt 1", "stream": init}, {"prompt": "prompt 2", "stream": init}],
                         transcript=[tx_prompt("prompt 1", "p1"), [tool_call(c, prompt="x") for c in calls[0]],
                                     tx_prompt("prompt 2", "p2"), [tool_call(c, script="x") for c in calls[1]]])
        for turn, name, first, *context in agents:
            agent_transcript(case, name, first, at(turn), **(context[0] if context else {}))
        return case

    def test_every_agent_of_the_turn_carries_the_marker(self):
        brief = f"Find the callers.\n\n{MARKER}\nRules for this agent"
        good = self.case(["Task", "Workflow"], (["Agent"], ["Workflow"]), [(1, "a1", brief), (2, "w1", brief)])
        self.assertEqual(verdicts(good, ["subagent_marker", 1, "Agent", MARKER], ["subagent_marker", 2, "Workflow", MARKER]),
                         [True, True])
        # known-bad: one agent of the turn without the marker fails, naming it; another turn's agents are not counted
        mixed = self.case(["Task"], (["Agent"], ["Agent"]), [(1, "a1", brief), (1, "a2", "Find the callers."), (2, "a3", brief)])
        self.assertEqual(verdicts(mixed, ["subagent_marker", 1, "Agent", MARKER], ["subagent_marker", 2, "Agent", MARKER]),
                         [False, True])
        self.assertIn("agent-a2", details(mixed, ["subagent_marker", 1, "Agent", MARKER])[0])

    def test_the_subagent_start_context_carries_the_marker(self):
        """The brief hook answers SubagentStart, so the block reaches an agent as the hook_additional_context
        record Claude Code writes into the agent's transcript before its first answer, not in the brief the caller
        wrote; each agent of the turn, the Agent tool's and a Workflow's alike, passes on that record."""
        block = [f"{MARKER}\nRules for this agent, added to every brief by the doctrine's brief hook:"]
        plain = "Find the callers."
        good = self.case(["Task", "Workflow"], (["Agent"], ["Workflow"]),
                         [(1, "a1", plain, {"context": block}), (2, "w1", plain, {"context": block})])
        self.assertEqual(verdicts(good, ["subagent_marker", 1, "Agent", MARKER], ["subagent_marker", 2, "Workflow", MARKER]),
                         [True, True])
        # known-bad, as in the proof run where the hook was the Agent tool's PreToolUse: the Workflow's agent started with neither
        bare = self.case(["Task", "Workflow"], (["Agent"], ["Workflow"]),
                         [(1, "a1", plain, {"context": block}), (2, "w1", plain)])
        self.assertEqual(verdicts(bare, ["subagent_marker", 1, "Agent", MARKER], ["subagent_marker", 2, "Workflow", MARKER]),
                         [True, False])
        self.assertIn("agent-w1", details(bare, ["subagent_marker", 2, "Workflow", MARKER])[0])
        # known-bads: the context of another event, a context without the marker, and one written only after the
        # agent's first answer do not count
        for label, extra in (("another event", {"context": block, "context_event": "SessionStart"}),
                             ("no marker", {"context": ["Rules for this agent"]}),
                             ("after the first answer", {"context": block, "context_after": True})):
            case = self.case(["Task"], (["Agent"], []), [(1, "a1", plain, extra)])
            self.assertEqual(verdicts(case, ["subagent_marker", 1, "Agent", MARKER]), [False], label)

    def test_no_agent_is_a_failure_never_a_pass(self):
        called = self.case(["Task", "Workflow"], (["Agent"], []))
        self.assertEqual(verdicts(called, ["subagent_marker", 1, "Agent", MARKER]), [False])
        self.assertIn("no agent transcript", details(called, ["subagent_marker", 1, "Agent", MARKER])[0])
        uncalled = self.case(["Task", "Workflow"], ([], ["Workflow"]), [(2, "w1", MARKER)])
        self.assertEqual(verdicts(uncalled, ["subagent_marker", 1, "Agent", MARKER]), [False])
        self.assertIn("no Agent call on turn 1", details(uncalled, ["subagent_marker", 1, "Agent", MARKER])[0])

    def test_a_seat_without_the_tool_is_na_and_reported(self):
        case = self.case(["Task", "Bash"], (["Agent"], []), [(1, "a1", MARKER)])
        self.assertEqual(verdicts(case, ["subagent_marker", 2, "Workflow", MARKER]), [None])
        self.assertEqual(details(case, ["subagent_marker", 2, "Workflow", MARKER]), ["n/a — no Workflow tool"])
        g = evals.grade(case, {"id": "t", "checks": [["subagent_marker", 2, "Workflow", MARKER]]})
        mine = [r for r in g["results"] if not r.get("automatic")]
        self.assertEqual([(r["passed"], r["detail"]) for r in mine], [(None, "n/a — no Workflow tool")])
        self.assertNotIn("subagent_marker", [r["check"][0] for r in g["results"] if r["passed"] is False])   # n/a never fails
        # the Agent tool is listed as Task in the init: present
        self.assertEqual(verdicts(case, ["subagent_marker", 1, "Agent", MARKER]), [True])


class BashOutput(unittest.TestCase):
    REFUSAL ="doc-guardrails: MISSING — knowledge/02-spec-cli.md: no `roadmap:` line in the first 40 lines"
    check = ["bash_output", "git commit", "doc-guardrails"]

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)

    def test_bash_output_pairs_the_command_with_its_result(self):
        good = make_case(self.root / "a", [{"stream": bash_calls(("git commit -m spec", self.REFUSAL))}])
        self.assertEqual(verdicts(good, self.check), [True])
        elsewhere = make_case(self.root / "b", [{"stream": bash_calls(("git commit -m spec", "[main 1a2b] spec"),
                                                                       ("cat hook.log", self.REFUSAL))}])
        self.assertEqual(verdicts(elsewhere, self.check), [False])
        # the amend pair (§5.2): commit then amend, the gate's report in both → PASS, and the branch has one commit
        amend = make_case(self.root / "c", [{"stream": bash_calls(("git commit -m spec", self.REFUSAL),
                                                                  ("git commit --amend -m spec", self.REFUSAL))}])
        self.assertEqual(verdicts(amend, self.check), [True])
        skipped = make_case(self.root / "d", [{"stream": bash_calls(("git commit --no-verify -m spec", "[main 1a2b] spec"),
                                                                    ("git commit --amend --no-verify -m spec", "[main 3c4d] spec"))}])
        self.assertEqual(verdicts(skipped, self.check), [False])
        (self.root / "e").mkdir()
        proj = evals.build_project(self.root / "e", {"id": "t"})
        commit = lambda *a: subprocess.run(["git", "-C", str(proj), "-c", "user.name=t", "-c", "user.email=t@t",
                                            "commit", "-qm", "x", *a], check=True, capture_output=True)
        (proj / "README.md").write_text("changed\n", encoding="utf-8")
        subprocess.run(["git", "-C", str(proj), "add", "-A"], check=True)
        commit()
        commit("--amend")
        ctx = {"proj": proj, "case_dir": self.root / "e", "record": {"turns": [], "snapshots": [{}]}}
        self.assertEqual([evals.check(k, [1], ctx)[0] for k in ("commits_at_least", "commits_at_most")], [True, True])


class PackPaths(unittest.TestCase):
    """One resolver for every path run.py takes into the pack, in this repo's layout and the public tree's (§5.7)."""
    PRIVATE = ["closeout/closeout.py", "doc-guardrails/hook.sh", "doc-guardrails/turn_check.py", "public/install.py"]
    PUBLIC = ["skills/closeout/closeout.py", "skills/doc-guardrails/hook.sh", "skills/doc-guardrails/turn_check.py",
              "install.py"]

    def tree(self, files):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        for rel in files:
            (root / rel).parent.mkdir(parents=True, exist_ok=True)
            (root / rel).write_text("x\n", encoding="utf-8")
        real = evals.PACK
        evals.PACK = root
        self.addCleanup(setattr, evals, "PACK", real)
        return root

    def test_pack_paths_resolve_in_both_layouts(self):
        for files in (self.PRIVATE, self.PUBLIC):
            root = self.tree(files)
            for rel in ("closeout/closeout.py", "doc-guardrails/hook.sh", "doc-guardrails/turn_check.py",
                        "public/install.py"):
                found = evals.pack_path(rel)
                self.assertTrue(found.is_file(), (files, rel))
                self.assertIn(found.relative_to(root).as_posix(), files)
        # known-bads: neither layout is an error naming both paths, never a skip; the old rule misses the installer
        root = self.tree(["README.md"])
        with self.assertRaises(FileNotFoundError) as e:
            evals.pack_path("closeout/closeout.py")
        self.assertIn(str(root / "closeout" / "closeout.py"), str(e.exception))
        self.assertIn(str(root / "skills" / "closeout" / "closeout.py"), str(e.exception))
        self.assertEqual(evals._public_rel("public/install.py"), "install.py")
        self.assertNotEqual(evals._public_rel("public/install.py"), "skills/public/install.py")   # the old rule
        self.assertEqual(evals._public_rel("evals/hook_log.py"), "evals/hook_log.py")



def git_version():
    out = subprocess.run(["git", "--version"], capture_output=True, text=True).stdout
    return tuple(int(x) for x in out.split()[2].split(".")[:2])


class GateRan(unittest.TestCase):
    """gate_ran: did the gate run on the session's commit (a heartbeat for exactly those bytes), or, when the session
    made no commit, did it refuse one — read from the case's own state folder, never the live one."""

    def setUp(self):
        r = subprocess.run(["git", "hook", "list", "pre-commit"], capture_output=True, text=True)
        if r.returncode == 129 or "unknown subcommand" in r.stderr:
            self.skipTest("git <2.54 on PATH: config hooks unsupported")
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.case = Path(tmp.name) / "case"
        self.case.mkdir()
        self.proj = evals.build_project(self.case, {"id": "t"})
        self.env = evals.grader_env(self.case / "state")
        evals._prep_gate(self.proj, self.env)

    def git(self, *args, check=True):
        return subprocess.run(["git", "-C", str(self.proj), "-c", "user.name=t", "-c", "user.email=t@t", *args],
                              capture_output=True, text=True, env=self.env, check=check)

    def spec(self, name="02-spec-cli.md", text="# CLI spec\n"):
        (self.proj / "knowledge" / name).write_text(text, encoding="utf-8")
        self.git("add", f"knowledge/{name}")

    def verdict(self):
        (self.case / "record.json").write_text(json.dumps({"scenario": "t", "seat": "fable", "turns": [],
                                                           "snapshots": [evals.snapshot(self.proj)]}), encoding="utf-8")
        return verdicts(self.case, ["gate_ran"])[0]

    def test_a_reported_commit_with_its_heartbeat_passes(self):
        self.spec()
        self.git("commit", "-qm", "spec")
        self.assertTrue(self.verdict())

    def test_a_no_verify_commit_fails_though_the_registration_is_there(self):
        self.spec()
        self.git("commit", "-q", "--no-verify", "-m", "spec")
        self.assertFalse(self.verdict())

    def test_an_amend_without_the_gate_fails(self):
        self.spec()
        self.git("commit", "-qm", "spec")
        self.spec("02-spec-more.md")
        self.git("commit", "-q", "--amend", "--no-verify", "--no-edit")
        self.assertFalse(self.verdict())   # a first-parent rule would pass it: the amend base is the replaced commit

    def test_a_refused_commit_counts_when_the_session_made_none(self):
        self.assertFalse(self.verdict())   # no commit, no run
        (self.proj / "CLAUDE.md").write_text((self.proj / "CLAUDE.md").read_text(encoding="utf-8")
                                             + "guardrails: block\n" if (self.proj / "CLAUDE.md").exists() else
                                             "# p\n\n## Doc map\nroadmap: knowledge/00-ROADMAP.md\nguardrails: block\n",
                                             encoding="utf-8")
        self.spec()
        r = self.git("commit", "-qm", "spec", check=False)
        self.assertNotEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertTrue(self.verdict())


class GateFires(unittest.TestCase):
    """gate_fires: can a commit reach the gate at all — a probe on a throwaway copy, never the graded project (§5.2)."""
    FOREIGN = {"fall_through": "#!/bin/sh\necho foreign checks\n",
               "exit_first": "#!/bin/sh\necho foreign checks\nexit 0\n",
               "exit_without_py": "#!/bin/sh\ngit diff --cached --name-only | grep -q '\\.py$' || exit 0\n"}

    def project(self, foreign=None, hooks_path=None):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        case = Path(tmp.name) / "case"
        case.mkdir()
        proj = evals.build_project(case, {"id": "t"})
        if hooks_path:
            subprocess.run(["git", "-C", str(proj), "config", "core.hooksPath", hooks_path], check=True)
        if foreign:
            hooks = proj / (hooks_path or ".git/hooks")
            hooks.mkdir(parents=True, exist_ok=True)
            (hooks / "pre-commit").write_text(self.FOREIGN[foreign], encoding="utf-8")
            (hooks / "pre-commit").chmod(0o755)
        return case, proj

    def fires(self, case, proj):
        (case / "record.json").write_text(json.dumps({"scenario": "t", "seat": "fable", "turns": [],
                                                      "snapshots": [evals.snapshot(proj)]}), encoding="utf-8")
        return verdicts(case, ["gate_fires", True], ["gate_fires", False])

    def test_gate_fires_probe(self):
        if os.name == "nt":
            self.skipTest("git hooks run through sh; the probe's shapes are POSIX shell")
        # the gate is registered as a git config hook, which git runs before any hook file: a foreign hook that ends
        # first, in git's own folder or in the project's tree, can no longer keep it from firing; the hook file is
        # never edited
        for hooks_path in (None, "scripts/git-hooks"):
            for foreign, expected in ((None, True), ("fall_through", True), ("exit_first", True),
                                      ("exit_without_py", True)):
                case, proj = self.project(foreign, hooks_path)
                hook = proj / (hooks_path or ".git/hooks") / "pre-commit"
                text = hook.read_bytes() if hook.exists() else None
                evals._prep_gate(proj, evals.grader_env(case / "state"))
                self.assertEqual(hook.read_bytes() if hook.exists() else None, text)
                before = (evals.snapshot(proj), subprocess.run(["git", "-C", str(proj), "status", "--porcelain"],
                                                                capture_output=True, text=True).stdout)
                self.assertEqual(self.fires(case, proj), [expected, not expected], (hooks_path, foreign))
                after = (evals.snapshot(proj), subprocess.run(["git", "-C", str(proj), "status", "--porcelain"],
                                                               capture_output=True, text=True).stdout)
                self.assertEqual(after, before, "the graded project is never touched")
        # no hook at all
        case, proj = self.project()
        self.assertEqual(self.fires(case, proj), [False, True])
        # a hook that prints only the launcher's "gate skipped" line, or only a "gate error" line: not a verdict
        for line in ("doc-guardrails: gate skipped: no python or closeout.py found (report-only project, commit continues)",
                     "doc-guardrails: gate error: closeout.py raised ValueError"):
            case, proj = self.project()
            hook = proj / ".git" / "hooks" / "pre-commit"
            hook.write_text(f"#!/bin/sh\necho '{line}' >&2\n", encoding="utf-8")
            hook.chmod(0o755)
            self.assertEqual(self.fires(case, proj), [False, True], line)

    def test_a_config_hook_fires(self):
        if os.name == "nt":
            self.skipTest("git hooks run through sh")
        if git_version() < (2, 54):
            self.skipTest(f"config hooks (hook.<name>.command) need git 2.54; this is {git_version()}")
        case, proj = self.project("exit_first")                      # a foreign file hook that exits first
        gate = Path(case) / "gate.sh"
        gate.write_text('#!/bin/sh\nexec "${CLOSEOUT_PYTHON:-python3}" "$CLOSEOUT_PY" link-audit . --staged\n',
                        encoding="utf-8")
        gate.chmod(0o755)
        for key, value in (("hook.doc-guardrails.event", "pre-commit"), ("hook.doc-guardrails.command", str(gate))):
            subprocess.run(["git", "-C", str(proj), "config", key, value], check=True)
        self.assertEqual(self.fires(case, proj), [True, False])

    def test_the_probe_runs_with_its_own_state_and_the_pack_under_test(self):
        if os.name == "nt":
            self.skipTest("git hooks run through sh")
        case, proj = self.project()
        evals._prep_gate(proj, evals.grader_env(case / "state"))
        real, seen = subprocess.run, []

        def recording(cmd, *a, **kw):
            seen.append((cmd, kw.get("env")))
            return real(cmd, *a, **kw)
        with mock.patch.object(evals.subprocess, "run", recording):
            self.assertEqual(self.fires(case, proj), [True, False])
        probes = [env for cmd, env in seen if cmd[:1] == ["git"] and "hook" in cmd and "run" in cmd]
        self.assertEqual(len(probes), 2)
        for env in probes:
            self.assertEqual(env["CLOSEOUT_PY"], str(evals.pack_path("closeout/closeout.py")))
            state = Path(env["DOC_GUARDRAILS_STATE"])
            self.assertFalse(str(state).startswith(str(case)), "the probe's state is its temp copy's, not the case's")
            self.assertNotEqual(state, Path.home() / ".claude" / "doc-guardrails-state")


class GraderState(unittest.TestCase):
    def test_the_grader_reads_facts_with_the_cases_state(self):
        with tempfile.TemporaryDirectory() as tmp:
            case = Path(tmp) / "case"
            case.mkdir()
            proj = evals.build_project(case, {"id": "t"})
            decoy = Path(tmp) / "live-state"
            decoy.mkdir()
            (case / "record.json").write_text(json.dumps({"scenario": "t", "seat": "fable", "turns": [],
                                                          "snapshots": [evals.snapshot(proj)]}), encoding="utf-8")
            real, seen = subprocess.run, []

            def recording(cmd, *a, **kw):
                seen.append((cmd, kw.get("env")))
                return real(cmd, *a, **kw)
            with mock.patch.dict(os.environ, {"DOC_GUARDRAILS_STATE": str(decoy)}), \
                    mock.patch.object(evals.subprocess, "run", recording):
                verdicts(case, ["facts_list_nonempty", "redman", "ledgers"])
            calls = [env for cmd, env in seen if "facts" in cmd]
            self.assertEqual(len(calls), 1)
            self.assertIsNotNone(calls[0], "the facts call must be given an environment, not inherit the process's")
            self.assertEqual(calls[0]["DOC_GUARDRAILS_STATE"], str(case / "state"))
            self.assertEqual(calls[0]["CLOSEOUT_PY"], str(evals.pack_path("closeout/closeout.py")))
            self.assertNotIn(str(decoy), json.dumps(calls[0]))                 # the decoy is never passed on
            self.assertEqual(list(decoy.iterdir()), [])

    def test_facts_are_read_by_a_key_path(self):
        """R-31 §5.6 (FULL's flows): "both pins seen by `facts`" reads facts → pins, a list, by a key path of any
        length whose numbers index a list; a path that runs past the list is no such field, never a raised check."""
        with tempfile.TemporaryDirectory() as tmp:
            case = Path(tmp) / "case"
            case.mkdir()
            proj = evals.build_project(case, {"id": "t", "overlay": "two_lane"})
            (case / "record.json").write_text(json.dumps({"scenario": "t", "seat": "fable", "turns": [],
                                                          "snapshots": [evals.snapshot(proj)]}), encoding="utf-8")
            g = evals.grade(case, {"id": "t", "checks": [["facts_list_nonempty", "pins", 1, "exists"],
                                                         ["facts_list_nonempty", "lanes", "audit"],
                                                         ["facts_list_nonempty", "pins", 2]]})
            self.assertEqual([r["passed"] for r in g["results"]], [True, True, False])
            self.assertEqual(g["results"][0]["detail"], "pins.1.exists=True")
            self.assertEqual(g["results"][2]["detail"], "facts gave no such field")    # not "check raised IndexError"
            # known-bad: the lane's pin removed — facts still lists it, and says it does not exist
            (proj / "knowledge" / "audit" / "00-AUDIT-CURRENT.md").unlink()
            g = evals.grade(case, {"id": "t", "checks": [["facts_list_nonempty", "pins", 1, "exists"]]})
            self.assertEqual((g["passed"], g["results"][0]["detail"]), (False, "pins.1.exists=False"))


class TypedSkills(unittest.TestCase):
    def test_typed_command_counts_as_invoked(self):
        with tempfile.TemporaryDirectory() as tmp:
            typed = make_case(Path(tmp) / "a", [{"prompt": "/closeout"}], transcript=[tx_typed("closeout")])
            self.assertEqual(verdicts(typed, ["skill_invoked", "closeout"], ["skill_invoked", "eli5"]), [True, False])
            mention = make_case(Path(tmp) / "b", [{"prompt": "Run /closeout when you are done."}],
                                transcript=[tx_prompt("Run /closeout when you are done.")])
            self.assertEqual(verdicts(mention, ["skill_invoked", "closeout"]), [False])
            # a <command-name> with no skill loaded after it (an unknown command) is not a load either
            unloaded = make_case(Path(tmp) / "c", [{"prompt": "/closeout"}],
                                 transcript=[tx_typed("closeout")[0]])
            self.assertEqual(verdicts(unloaded, ["skill_invoked", "closeout"]), [False])
            # another skill's folder does not count for this one
            elsewhere = make_case(Path(tmp) / "d", [{"prompt": "/closeout"}],
                                  transcript=[tx_typed("closeout", base="/home/u/other")])
            self.assertEqual(verdicts(elsewhere, ["skill_invoked", "closeout"]), [False])


class Records(unittest.TestCase):
    """Records are read with closeout's own parsers, not a regex of the eval's (W-35)."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.case = Path(tmp.name) / "case"
        self.case.mkdir()
        self.proj = evals.build_project(self.case, {"id": "t"})

    def grade(self, *checks):
        (self.case / "record.json").write_text(json.dumps({"scenario": "t", "seat": "fable", "turns": [],
                                                           "snapshots": [evals.snapshot(self.proj)]}), encoding="utf-8")
        return verdicts(self.case, *checks)

    def write(self, rel, text):
        (self.proj / rel).parent.mkdir(parents=True, exist_ok=True)
        (self.proj / rel).write_text(text, encoding="utf-8")

    def test_records_are_read_with_closeouts_parsers(self):
        status = ["record_status", "knowledge/decisions/G-*.md", "awaiting"]
        for line, expected in (("**status:** awaiting the user", True), ("status: awaiting the user", True),
                               ("- Status: awaiting your ruling", True), ("status: decided", False)):
            self.write("knowledge/decisions/G-1-store.md", f"# G-1\nroadmap: R-2\n{line}\n")
            self.assertEqual(self.grade(status), [expected], line)
        self.write("knowledge/decisions/G-1-store.md", "# G-1\nroadmap: R-2\n**status:** awaiting the user\n")
        self.assertEqual(self.grade(["file_matches", "knowledge/decisions/G-*.md", "(?m)^status:.*awaiting"]), [False],
                         "the old regex failed the bold status line: the defect")
        self.write("knowledge/decisions/G-1-store.md", "# G-1\nwe were awaiting data; status is below\nstatus: decided\n")
        self.assertEqual(self.grade(status), [False], "a word in the prose is not the status")
        # the roadmap: the heading R-1 sits under
        done = ["roadmap_status", "R-1", "(?i)^done"]
        roadmap = self.proj / "knowledge" / "00-ROADMAP.md"
        text = roadmap.read_text(encoding="utf-8")
        self.write("knowledge/00-ROADMAP.md", text.replace("- R-1 Low-stock", "- R-1 (done 2026-09-23) Low-stock"))
        self.assertEqual(self.grade(done), [False], "R-1 left under ## Now with 'done' in its text")
        self.assertEqual(self.grade(["any_of", ["file_matches", "knowledge/00-ROADMAP.md", "(?s)## Done.*R-1"],
                                     ["file_matches", "knowledge/00-ROADMAP.md", "(?im)^.*R-1\\b.*\\b(done|closed)\\b"]]),
                         [True], "the old any_of passed the unmoved R-1: the defect")
        moved = text.replace("- R-1 Low-stock report is correct for out-of-stock and uncounted items.\n", "")
        self.write("knowledge/00-ROADMAP.md", moved + "\n## Done\n- R-1 Low-stock report is correct (2026-09-23).\n")
        self.assertEqual(self.grade(done, ["roadmap_status", "R-2", "(?i)^now"]), [True, True])
        self.assertEqual(self.grade(["roadmap_status", "R-9", "."]), [False])       # an id the roadmap lacks

    def test_roadmap_sections(self):
        co = evals.closeout_module()
        text = ("# r\n## Now\n- R-2 persist (after R-1)\n\n### detail\n- R-4 sub item\n## Done\n"
                "* R-1 low stock\nprose naming R-7 is not an item\n")
        self.assertEqual(co.roadmap_sections(text), {"R-2": "Now", "R-4": "Now", "R-1": "Done"})


class Holdout(unittest.TestCase):
    def project(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        case = Path(tmp.name) / "case"
        case.mkdir()
        return case, evals.build_project(case, {"id": "t", "overlay": "failed_fork"})

    def grade(self, case, proj, *checks):
        (case / "record.json").write_text(json.dumps({"scenario": "t", "seat": "fable", "turns": [],
                                                      "snapshots": [evals.snapshot(proj)]}), encoding="utf-8")
        return verdicts(case, *checks)

    def test_holdout_probe(self):
        holdout = ["holdout", "low_stock_uncounted"]
        case, proj = self.project()
        self.assertEqual(self.grade(case, proj, holdout), [False])          # the fixture as shipped: R-1 unfixed
        report = proj / "shelf" / "report.py"
        report.write_text(report.read_text(encoding="utf-8").replace(
            "    for item in itms:\n", "    for item in itms:\n        if item.sku == \"D4\":\n            continue\n"),
            encoding="utf-8")                                              # a fix keyed on the known sku
        self.assertEqual(self.grade(case, proj, ["tests_pass"], holdout), [True, False])
        case, proj = self.project()
        load = proj / "shelf" / "load.py"
        load.write_text(load.read_text(encoding="utf-8").replace('int(row["qty"] or 0)',
                                                                 'int(row["qty"]) if row["qty"] else None'),
                        encoding="utf-8")                                  # the loader fix
        self.assertEqual(self.grade(case, proj, ["tests_pass"], holdout), [True, True])
        self.assertEqual(self.grade(case, proj, ["holdout", "no_such_probe"]), [False])


class TestsPass(unittest.TestCase):
    """tests_pass against the fixture's baseline test ids, the same on every interpreter (W-29)."""

    def project(self, overlay=None):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        case = Path(tmp.name) / "case"
        case.mkdir()
        proj = evals.build_project(case, {"id": "t", "overlay": overlay} if overlay else {"id": "t"})
        return case, proj, evals.baseline_tests(proj)

    def grade(self, case, proj, baseline, *checks):
        (case / "record.json").write_text(json.dumps({"scenario": "t", "seat": "fable", "turns": [],
                                                      "snapshots": [evals.snapshot(proj)],
                                                      "baseline_tests": baseline}), encoding="utf-8")
        return verdicts(case, *checks)

    def test_baseline_ids_are_read_from_the_verbose_run(self):
        _, _, baseline = self.project()
        self.assertEqual(baseline, {
            "test_report.LowStock.test_low_stock_reports_every_counted_item_at_or_below_reorder": "FAIL",
            "test_report.LowStock.test_nothing_low": "ok"})

    def test_tests_pass_fails_when_the_test_file_is_deleted(self):
        case, proj, baseline = self.project("r1_fixed")
        self.assertEqual(set(baseline.values()), {"ok"})
        self.assertEqual(self.grade(case, proj, baseline, ["tests_pass"]), [True])
        (proj / "tests" / "test_report.py").unlink()
        self.assertEqual(self.grade(case, proj, baseline, ["tests_pass"], ["tests_pass", "no_worse"]), [False, False])
        # a record written before baseline ids were kept: still ≥1 test must run (3.9's unittest exits 0 on none)
        self.assertEqual(self.grade(case, proj, None, ["tests_pass"]), [False])
        # one baseline test removed and the rest left passing: gone is a failure, in either mode
        case, proj, baseline = self.project("r1_fixed")
        tests = proj / "tests" / "test_report.py"
        text = tests.read_text(encoding="utf-8")
        tests.write_text(text[:text.index("    def test_nothing_low")] + text[text.index('\n\nif __name__'):], encoding="utf-8")
        self.assertEqual(self.grade(case, proj, baseline, ["tests_pass"], ["tests_pass", "no_worse"]), [False, False])

    def test_tests_pass_no_worse(self):
        case, proj, baseline = self.project()
        self.assertEqual(self.grade(case, proj, baseline, ["tests_pass"], ["tests_pass", "no_worse"]), [False, True])
        report = proj / "shelf" / "report.py"
        report.write_text(report.read_text(encoding="utf-8").replace("def low_stock(itms):", "def low_stock(items):"),
                          encoding="utf-8")                                 # the half rename: NameError everywhere
        self.assertEqual(self.grade(case, proj, baseline, ["tests_pass", "no_worse"]), [False])
        report.write_text(report.read_text(encoding="utf-8").replace("in itms:", "in items:"), encoding="utf-8")
        self.assertEqual(self.grade(case, proj, baseline, ["tests_pass", "no_worse"]), [True])   # R-1 still red, nothing worse
        self.assertIn("malformed", details(case, ["tests_pass", "some"])[0])


RECEIPT = ("ELI5 v5\n- Did: fixed the report\n- Why: uncounted items were reported\n- Result: worked\n"
           "- Proof: 2 tests pass\n- Files: shelf/report.py\n\n- Redman: filed 1 small → knowledge/redman/LEDGER.md\n"
           "- Greenman: NA\n\n- Next: R-2\n- Need from you: none\n\n- Goal: R-1 · report — right skus · goal changed: no\n"
           "- In one sentence: The report is right now; nothing waits on you.\n")
RECEIPT_CHECK = "/usr/bin/python3 /pack/eli5/receipt_check.py"


class Receipts(unittest.TestCase):
    """receipt and the automatic receipts_after_eli5 (red2 NEW-2), and "repaired by hook", never a pass (G-22 D15)."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root, self.n = Path(tmp.name), 0

    def case(self, final3, transcript3=(), final2="ok", ledger=None):
        """/redman, /eli5, then the task; turn 3's final and its transcript part as given; the project's decision
        ledger holding `ledger` rows."""
        self.n += 1
        turns = [{"prompt": "/redman", "final": "on"}, {"prompt": "/eli5", "final": final2},
                 {"prompt": "Fix R-1.", "final": final3}]
        tx = [tx_typed("redman", "p1"), tx_typed("eli5", "p2"), tx_prompt("Fix R-1.", "p3"), *transcript3]
        case = make_case(self.root / str(self.n), turns, transcript=tx, env={})
        (case / "project" / "knowledge" / "decisions").mkdir(parents=True)
        if ledger:
            (case / "project" / "knowledge" / "decisions" / "LEDGER.md").write_text(ledger, encoding="utf-8")
        return case

    def automatic(self, case):
        g = evals.grade(case, {"id": "t", "turns": ["/redman", "/eli5", "Fix R-1."], "checks": []})
        return {r["check"][0]: (r["passed"], r["detail"]) for r in g["results"]}, g

    def test_every_turn_after_eli5_carries_a_receipt(self):
        missing, _ = self.automatic(self.case("Fixed it."))
        self.assertFalse(missing["receipts_after_eli5"][0])                       # red2 NEW-2
        good, _ = self.automatic(self.case("Fixed it.\n\n" + RECEIPT))
        self.assertTrue(good["receipts_after_eli5"][0])
        repaired_case = self.case("Fixed it.\n\n" + RECEIPT, [tx_text("Fixed it."),
                                  tx_block("Stop", RECEIPT_CHECK, "eli5: this turn ends without an ELI5 v5 block"),
                                  tx_text("Fixed it.\n\n" + RECEIPT)])
        repaired, g = self.automatic(repaired_case)
        self.assertEqual(repaired["receipts_after_eli5"], (False, "repaired by hook: turn 3"))
        out = Path(self.root) / "report"
        out.mkdir()
        import io, contextlib
        with contextlib.redirect_stdout(io.StringIO()) as buf:
            evals.report([g], out)
        self.assertIn("\nrepaired by hook: 1 case(s)", buf.getvalue())
        # a Stop block from another hook, after a valid receipt, is not a repair
        other = self.case("Fixed it.\n\n" + RECEIPT, [tx_text("Fixed it.\n\n" + RECEIPT),
                          tx_block("Stop", "/usr/bin/python3 /pack/doc-guardrails/turn_check.py", "doc-guardrails: x")])
        self.assertTrue(self.automatic(other)[0]["receipts_after_eli5"][0])
        # no /eli5 turn: nothing to require
        plain = make_case(self.root / "plain", [{"prompt": "Fix R-1.", "final": "Fixed."}], env={})
        self.assertTrue({r["check"][0]: r["passed"] for r in evals.grade(plain, {"id": "t", "checks": []})["results"]}
                        ["receipts_after_eli5"])

    def test_receipt_fields_and_the_g_id_written_after_the_stop_block(self):
        cited = RECEIPT.replace("- Greenman: NA", "- Greenman: G-9 (M): csv over sqlite")
        row = "| 2026-09-23 | G-9 | csv over sqlite | M | decided |\n"
        good = self.case("Done.\n\n" + cited, [tx_tool("Write", file_path="/p/knowledge/decisions/LEDGER.md",
                                                         content=row)], ledger=row)
        self.assertEqual(verdicts(good, ["receipt", 3, {"Redman": "^(?!off\\b)(?!filed 0)"}], ["receipt", 3, {}]),
                         [True, True])
        self.assertEqual(verdicts(self.case("Done.\n\n" + cited.replace("filed 1 small", "off")),
                                  ["receipt", 3, {"Redman": "^(?!off\\b)(?!filed 0)"}]), [False])   # Redman: off
        self.assertEqual(verdicts(self.case("Done.\n\n" + cited.replace("ELI5 v5", "ELI5 v4")), ["receipt", 3, {}]),
                         [False])
        unrecorded = self.case("Done.\n\n" + cited)                        # W-49: no record of G-9 anywhere
        self.assertEqual(verdicts(unrecorded, ["receipt", 3, {}]), [False])
        self.assertEqual(details(unrecorded, ["receipt", 3, {}]), ["turn 3: G-9 has no record"])
        other = self.case("Done.\n\n" + cited, ledger=row.replace("G-9", "G-90"))   # G-90 is not G-9
        self.assertEqual(verdicts(other, ["receipt", 3, {}]), [False])
        late = self.case("Done.\n\n" + cited, [tx_text("Done.\n\n" + cited),
                         tx_block("Stop", RECEIPT_CHECK, "eli5: G-9 has no record"),
                         tx_tool("Write", file_path="/p/knowledge/decisions/LEDGER.md", content=row)], ledger=row)
        self.assertEqual(details(late, ["receipt", 3, {}]), ["repaired by hook: G-9 recorded after the Stop block"])
        self.assertEqual(verdicts(late, ["receipt", 3, {}]), [False])
        # the live check's first lookup: the record the line names by its absolute path, outside the project
        rec = Path(self.root) / "build" / "G-9-csv-over-sqlite.md"
        named = cited.replace("csv over sqlite", f"csv over sqlite → {rec}")
        self.assertEqual(verdicts(self.case("Done.\n\n" + named), ["receipt", 3, {}]), [False])   # not written yet
        rec.parent.mkdir()
        rec.write_text("# G-9\n", encoding="utf-8")
        self.assertEqual(verdicts(self.case("Done.\n\n" + named), ["receipt", 3, {}]), [True])


PACK_SKILLS = ["axiom-macos", "axiom-swiftui", "closeout", "doc-guardrails", "eli5", "greenman", "project-update", "redman"]


class SealedAndLayer(unittest.TestCase):
    """The automatic `layer` and `sealed` checks, the loader found by attribution through the case's hook log
    (§5.7; G-23 E8, G-24 R3-Y3, G-25 R4-Y2, G-26 R5-Y1/R5-Y4, G-27 F1-Y1, G-28 F2-X4/F2-Y1)."""
    LOADER = "/home/u/.claude/doctrine/gate.sh"
    LAYER = "# Working notes (injected: this session runs Claude Fable 5.1)\n\nHold four things.\n"
    HOOKS = [["SessionStart", "", LOADER, "gate.sh"],
             ["PostToolUse", "Write|Edit", "/pack/doc-guardrails/hook.sh", "hook.sh"],
             ["Stop", "", "/pack/doc-guardrails/turn_check.py", "turn_check.py"]]
    LAUNCHED = [["SessionStart", "", "/pack/doctrine/gate.py", "gate.py"]] + HOOKS[1:]   # R-26's set
    NOTICE = {"name": "resume notice", "command": "eli5/skill_fresh.py", "matcher": "resume", "text_re": "^skill freshness"}
    FRESH = "/pack/eli5/skill_fresh.py"

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root, self.n = Path(tmp.name), 0
        real = evals.SEALED
        self.addCleanup(setattr, evals, "SEALED", real)
        self.manifest()

    def manifest(self, **changes):
        m = json.loads((Path(evals.HERE) / "sealed.json").read_text(encoding="utf-8"))
        m.update(bundled_skills=["update-config"], builtin_plugins=["agents-md@builtin", "telemetry@builtin"],
                 cli_version="2.1.281")
        m.update(changes)
        self.n += 1
        path = self.root / f"sealed-{self.n}.json"
        path.write_text(json.dumps(m), encoding="utf-8")
        evals.SEALED = path

    def init(self, **changes):
        d = {"type": "system", "subtype": "init", "session_id": "s1", "claude_code_version": "2.1.281",
             "skills": PACK_SKILLS + ["update-config"], "mcp_servers": [],
             "plugins": [{"name": "agents-md", "path": "builtin", "source": "agents-md@builtin"},
                         {"name": "telemetry", "path": "builtin", "source": "telemetry@builtin"}]}
        d.update(changes)
        return d

    def case(self, turns=3, layer=None, extra=(), init=None, log_edit=None, no_log=False, drop=(), loader_script=None,
             **record):
        """A sealed case: each turn's loader output (turn 1 fresh, later turns in the real resumed shape) with its
        log line; `extra` adds (turn, output, source, logged script or None) SessionStart outputs."""
        layer = layer if layer is not None else [self.LAYER] * turns
        loader = loader_script or self.LOADER
        turn_list, log = [], []
        for n in range(1, turns + 1):
            st, out = [], layer[n - 1]
            if out is not None:
                st += [hook_event("hook_response", "SessionStart", out, name="SessionStart:startup")] if n == 1 else resumed(out)
                log.append(log_line("SessionStart", loader, out, n))
            for tn, output, source, script in extra:
                if tn == n:
                    st.append(hook_event("hook_response", "SessionStart", output, name=f"SessionStart:{source}"))
                    if script:
                        log.append(log_line("SessionStart", script, output, n))
            st.append(init or self.init())
            turn_list.append({"prompt": f"prompt {n}", "stream": st})
        if log_edit:
            log = log_edit(log)
        rec = {"hooks": self.HOOKS, "loader": self.HOOKS[0], "env": {},
               "layer_expected": {"file": "fable-layer.md", "sha256": sha(self.LAYER), "header": self.LAYER.splitlines()[0]}}
        rec.update(record)
        for key in drop:
            rec.pop(key, None)
        self.n += 1
        return make_case(self.root / f"c{self.n}", turn_list, log=None if no_log else log, **rec)

    def auto(self, case, scenario=None):
        g = evals.grade(case, dict(scenario or {"id": "t"}, checks=[]))
        return {r["check"][0]: (r["passed"], r["detail"]) for r in g["results"]}

    def verdict(self, case, name="sealed", scenario=None):
        return self.auto(case, scenario)[name]

    def test_layer_check(self):
        good = self.auto(self.case())
        self.assertEqual((good["layer"][0], good["sealed"][0]), (True, True), good)
        opus5 = self.LAYER.replace("Claude Fable 5.1", "Claude Opus 5")          # the 2026-09-14 shape
        self.assertFalse(self.verdict(self.case(layer=[opus5] * 3, log_edit=None), "layer")[0])
        self.assertFalse(self.verdict(self.case(layer=[self.LAYER + "changed\n"] * 3), "layer")[0])   # not the launch hash
        self.assertFalse(self.verdict(self.case(layer=[None, self.LAYER, self.LAYER]), "layer")[0])   # no injection
        superpowers = json_output("SessionStart", "<EXTREMELY_IMPORTANT> You have superpowers.")
        plugin = self.auto(self.case(extra=[(1, superpowers, "startup", "/plugins/superpowers/hooks/session-start.sh")]))
        self.assertEqual((plugin["layer"][0], plugin["sealed"][0]), (False, False))   # an unnamed second injection
        # a named non-loader output on a resumed turn is not a second injection
        self.manifest(session_start_allowed=[self.NOTICE])
        notice = json_output("SessionStart", "skill freshness: demo changed")
        named = self.auto(self.case(extra=[(2, notice, "resume", self.FRESH)]))
        self.assertEqual((named["layer"][0], named["sealed"][0]), (True, True), named)
        self.manifest()
        # the loader found by attribution, never by its command string: R-26's launcher form
        launched = self.auto(self.case(loader_script="/pack/doctrine/gate.py", hooks=self.LAUNCHED,
                                       loader=self.LAUNCHED[0]))
        self.assertEqual((launched["layer"][0], launched["sealed"][0]), (True, True), launched)
        two = self.HOOKS + [["SessionStart", "", "/pack/other/start.sh", "start.sh"]]
        self.assertFalse(self.verdict(self.case(hooks=two))[0])                # two unnamed SessionStart entries
        # the real resumed turn: turns 2 and 3 in the 2.1.280 shape, no transcript record, the log's line per turn
        passed, detail = self.verdict(self.case(no_log=True))
        self.assertFalse(passed)
        self.assertTrue(detail.startswith("no hook log"), detail)
        other = self.auto(self.case(log_edit=lambda log: [dict(l, script="/pack/other/start.sh") if i == 1 else l
                                                          for i, l in enumerate(log)]))
        self.assertFalse(other["layer"][0])
        self.assertIn("turn 2", other["layer"][1])
        self.assertFalse(other["sealed"][0])

    def test_a_recheck_grades_a_case_against_its_own_hooks(self):
        case = self.case()
        self.assertEqual((self.verdict(case)[0], self.verdict(case, "layer")[0]), (True, True))
        entries = [tuple(e) for e in self.LAUNCHED]
        with mock.patch.object(evals, "sealed_hook_entries", lambda probe=False: entries):
            self.assertEqual((self.verdict(case)[0], self.verdict(case, "layer")[0]), (True, True))
            stripped = self.case(drop=("hooks", "loader"))                   # the record without its hook set
            self.assertFalse(self.verdict(stripped)[0])
        # a run recorded before R-22's runner: no hooks, no env, no log; layer by the header rule, sealed not graded
        plugin = json_output("SessionStart", "<EXTREMELY_IMPORTANT> You have superpowers.")
        old = self.case(drop=("hooks", "loader", "env", "layer_expected"), no_log=True,
                        extra=[(1, plugin, "startup", None)])
        before = self.auto(old)
        self.assertNotIn("sealed", before)
        self.assertTrue(before["layer"][0], before)
        with mock.patch.object(evals, "sealed_hook_entries", lambda probe=False: entries):
            self.assertEqual(self.auto(old)["layer"], before["layer"])
        second = self.case(drop=("hooks", "loader", "env", "layer_expected"), no_log=True,
                           extra=[(2, self.LAYER, "resume", None)])
        self.assertFalse(self.verdict(second, "layer")[0])                    # two outputs carrying the header
        opus5 = self.case(drop=("hooks", "loader", "env", "layer_expected"), no_log=True,
                          layer=[self.LAYER.replace("Claude Fable 5.1", "Claude Opus 5")] * 3)
        self.assertFalse(self.verdict(opus5, "layer")[0])                     # the 40 flips' shape

    def test_a_loader_run_logged_twice_fails_sealed(self):
        dup = self.case(log_edit=lambda log: log + [log[1]])
        passed, detail = self.verdict(dup)
        self.assertFalse(passed)
        self.assertIn("duplicate", detail)
        self.assertIn("turn 2", detail)

    def test_sealed_check(self):
        superpowers = {"name": "superpowers", "path": "<home>/.claude/plugins/cache/superpowers", "source": "superpowers@market"}
        for init, why in ((self.init(plugins=self.init()["plugins"] + [superpowers]), "superpowers"),
                          (self.init(mcp_servers=[{"name": "github", "status": "connected", "source": "user"}]), "github"),
                          (self.init(skills=PACK_SKILLS + ["update-config", "my-own-skill"]), "my-own-skill")):
            passed, detail = self.verdict(self.case(init=init))
            self.assertFalse(passed, why)
            self.assertIn(why, detail)
        telemetry = self.init(plugins=[{"name": "telemetry", "path": "builtin", "source": "telemetry@builtin"}])
        self.assertTrue(self.verdict(self.case(init=telemetry))[0])           # a listed built-in may be absent
        extra = self.init(plugins=self.init()["plugins"] + [{"name": "lsp", "path": "builtin", "source": "lsp@builtin"}])
        self.assertIn("lsp@builtin", self.verdict(self.case(init=extra))[1])
        fake = self.init(plugins=[{"name": "telemetry", "path": "/x/telemetry", "source": "telemetry@builtin"}])
        self.assertFalse(self.verdict(self.case(init=fake))[0])
        # the named notice: on a resume start it passes, on a startup start it fails, and text_re reads the context
        notice = json_output("SessionStart", "skill freshness: demo changed")
        self.manifest(session_start_allowed=[self.NOTICE])
        self.assertTrue(self.verdict(self.case(extra=[(2, notice, "resume", self.FRESH)]))[0])
        self.assertFalse(self.verdict(self.case(extra=[(1, notice, "startup", self.FRESH)]))[0])
        self.manifest(session_start_allowed=[dict(self.NOTICE, text_re="^\\{")])
        self.assertFalse(self.verdict(self.case(extra=[(2, notice, "resume", self.FRESH)]))[0])
        self.manifest()
        # fixture skills
        demo = self.init(skills=PACK_SKILLS + ["update-config", "demo"])
        self.assertTrue(self.verdict(self.case(init=demo), scenario={"id": "t", "fixture_skills": ["demo"]})[0])
        self.assertFalse(self.verdict(self.case(init=demo))[0])
        demo2 = self.init(skills=PACK_SKILLS + ["update-config", "demo", "demo2"])
        passed, detail = self.verdict(self.case(init=demo2), scenario={"id": "t", "fixture_skills": ["demo"]})
        self.assertFalse(passed)
        self.assertIn("demo2", detail)
        # account-supplied entries: the harvested 2.1.280 init
        synced = json.loads((SHAPES / "init_synced_2_1_280.json").read_text(encoding="utf-8"))
        passed, detail = self.verdict(self.case(init=synced))
        self.assertFalse(passed)
        self.assertRegex(detail, r"from the claude\.ai account: 16 plugin\(s\) .*, 8 skill\(s\) .*, 1 connector\(s\) ")
        one_skill = self.init(skills=PACK_SKILLS + ["update-config", "anthropic-skills:skill-3"])
        self.assertIn("1 skill(s) anthropic-skills:skill-3", self.verdict(self.case(init=one_skill))[1])
        one_conn = self.init(mcp_servers=[{"name": "claude.ai connector-1", "status": "connected", "source": "claudeai"}])
        self.assertIn("1 connector(s) claude.ai connector-1", self.verdict(self.case(init=one_conn))[1])
        self.manifest(bundled_skills=["update-config", "anthropic-skills:pdf"])
        self.assertIn("anthropic-skills:pdf", self.verdict(self.case())[1])   # the manifest itself is refused
        # a --probe case before review (the E-1 shape)
        self.manifest(bundled_skills=[], builtin_plugins=[], cli_version=None)
        probe = self.case(turns=2, probe=True)
        self.assertEqual(self.verdict(probe), (None, "unreviewed: bundled_skills, builtin_plugins, cli_version"))
        g = evals.grade(probe, {"id": "t", "checks": []})
        import io, contextlib
        (self.root / "rep").mkdir()
        with contextlib.redirect_stdout(io.StringIO()) as buf:
            evals.report([g], self.root / "rep")
        self.assertIn("\nunreviewed: sealed — bundled_skills, builtin_plugins, cli_version", buf.getvalue())
        self.assertFalse(self.verdict(self.case(turns=2, probe=True, init=synced))[0])   # not unreviewed: FAIL
        self.assertFalse(self.verdict(self.case(turns=2))[0])                 # only E-1 meets an unreviewed manifest
        self.manifest()
        self.assertTrue(self.verdict(probe)[0])                               # the recheck after review
        newer = self.init(claude_code_version="2.1.282")
        self.assertEqual(self.verdict(self.case(turns=2, probe=True, init=newer)), (None, "unreviewed: cli_version"))
        self.assertFalse(self.verdict(self.case(turns=2, init=newer))[0])

    def test_a_regrade_judges_sealed_by_what_was_reviewed_when_the_run_ran(self):
        """Ledger row 2026-09-28 (regrade CLI): a run made on the CLI reviewed that day fails `sealed` against a
        later review only when graded with today's manifest; given the reviewed fields at its stamp, it passes."""
        self.manifest(cli_version="2.1.283")                                  # today's review moved on
        ran = self.case(init=self.init(claude_code_version="2.1.281"))       # reviewed when it ran
        self.assertFalse(self.verdict(ran)[0])                                # the known-bad: today's review
        g = evals.grade(ran, {"id": "t", "checks": []}, reviewed={"cli_version": "2.1.281"})
        self.assertTrue({r["check"][0]: r["passed"] for r in g["results"]}["sealed"])
        g = evals.grade(ran, {"id": "t", "checks": []}, reviewed={"cli_version": "2.1.280"})
        self.assertFalse({r["check"][0]: r["passed"] for r in g["results"]}["sealed"])   # a wrong CLI still fails

    def test_sealed_hook_entries_resolve_in_both_layouts(self):
        """R-25: the entries are the built settings' (the installer's registration plus the extras), read through
        hook_key, in either layout; each script is its real path, under the pack or the home's doctrine chain."""
        m = json.loads((Path(evals.HERE) / "sealed.json").read_text(encoding="utf-8"))
        for public in (False, True):
            with tempfile.TemporaryDirectory() as tmp:
                pack, home = Path(os.path.realpath(tmp)) / "pack", Path(os.path.realpath(tmp)) / "home"
                skills = pack / "skills" if public else pack
                for name in m["skills"]:
                    (skills / name).mkdir(parents=True, exist_ok=True)
                for rel in ("doc-guardrails/hook.sh", "doc-guardrails/turn_check.py", "eli5/skill_fresh.py",
                            "eli5/receipt_check.py"):
                    (skills / rel).write_text("x\n", encoding="utf-8")
                (pack / "evals").mkdir()
                (pack / "evals" / "hook_log.py").write_text("x\n", encoding="utf-8")
                installer = pack / ("install.py" if public else "public/install.py")
                installer.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(evals.installer_module().__file__, installer)
                (home / ".claude" / "doctrine").mkdir(parents=True)
                (home / ".claude" / "doctrine" / "gate.py").write_text("x\n", encoding="utf-8")   # R-26 § 5.9
                (home / ".claude" / "doctrine" / "brief.py").write_text("x\n", encoding="utf-8")  # R-27 § 5.2
                real_pack = evals.PACK
                evals.PACK = pack
                try:
                    with mock.patch.object(Path, "home", lambda: home):
                        entries = evals.sealed_hook_entries()
                finally:
                    evals.PACK = real_pack
                by_event = {}
                for e in entries:                                   # each event's first entry: the gate, the turn check
                    by_event.setdefault(e[0], e)
                self.assertEqual(sorted(by_event), ["PostToolUse", "SessionStart", "Stop", "SubagentStart", "UserPromptSubmit"])
                self.assertTrue(by_event["UserPromptSubmit"][2].startswith(str(skills) + os.sep), public)   # R-28
                self.assertEqual(by_event["SubagentStart"][1:], ("", str(home / ".claude" / "doctrine" / "brief.py"), "brief.py"))   # every agent, no matcher
                self.assertEqual(by_event["SessionStart"][2], str(home / ".claude" / "doctrine" / "gate.py"))
                self.assertEqual(by_event["SessionStart"][3], "gate.py")
                self.assertEqual(by_event["PostToolUse"][1], "Write|Edit")
                for event in ("PostToolUse", "Stop"):
                    self.assertTrue(Path(by_event[event][2]).is_file(), (public, event))
                    self.assertTrue(by_event[event][2].startswith(str(skills) + os.sep), (public, event))


class TranscriptCopy(unittest.TestCase):
    def test_the_session_transcript_and_its_files_are_copied_into_the_case(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = Path(tmp) / "config" / "projects" / "-x-proj"
            (config / "s1" / "tool-results").mkdir(parents=True)
            (config / "s1.jsonl").write_text(json.dumps(tx_prompt("hello")) + "\n", encoding="utf-8")
            (config / "s1" / "tool-results" / "hook-1-stdout.txt").write_text("long\n", encoding="utf-8")
            (config / "other.jsonl").write_text("{}\n", encoding="utf-8")        # another session: not copied
            twin = Path(tmp) / "twin"
            twin.symlink_to(config)
            case = Path(tmp) / "case"
            case.mkdir()
            self.assertEqual(evals.copy_transcript("s1", [str(config), str(twin)], case), 1)
            self.assertEqual(sorted(p.relative_to(case).as_posix() for p in (case / "transcripts").rglob("*") if p.is_file()),
                             ["transcripts/s1.jsonl", "transcripts/s1/tool-results/hook-1-stdout.txt"])
            (config / "s1.jsonl").write_text(json.dumps(tx_prompt("hello")) + "\n" + json.dumps(tx_prompt("again")) + "\n",
                                             encoding="utf-8")
            evals.copy_transcript("s1", [str(config)], case)                    # after the next turn: the whole file again
            self.assertEqual([e["text"] for e in evals.parse_transcript(case)], ["hello", "again"])
            self.assertEqual(evals.copy_transcript(None, [str(config)], case), 0)


class KnownRuns(unittest.TestCase):
    """The graders proven on runs that must pass and runs that must fail (R-22 §7 A–C; red2 NEW-1). One selftest
    pass is shared by the class: every case is built once."""

    @classmethod
    def setUpClass(cls):
        import selftest
        cls.selftest = selftest
        cls.book = selftest.Book(SPEC)

    @classmethod
    def tearDownClass(cls):
        cls.book.close()

    def synthetic(self, scenarios):
        spec = dict(SPEC, scenarios=scenarios)
        book = self.selftest.Book(spec, workers=2)
        self.addCleanup(book.close)
        return book

    def test_every_scenario_declares_good_and_bad_runs(self):
        for s in SPEC["scenarios"]:
            expects = {k.get("expect") for k in s.get("known") or []}   # an owned run is a declared one
            self.assertEqual(expects, {"pass", "fail"}, s["id"])
            self.assertTrue(s.get("covers"), s["id"])
            for k in s["known"]:
                self.assertEqual(len(k["turns"]), len(s["turns"]), (s["id"], k.get("label")))
                self.assertTrue(k.get("label"), s["id"])
                if k["expect"] == "fail":
                    self.assertTrue(k.get("fails"), (s["id"], k["label"]))

    def test_r27s_three_scenarios(self):
        """R-27 test 9: `redman_no_edit` extended with a big finding held under the no-edit task, `greenman_no_edit`
        (a read-only review with a real choice: no G file, no row, the held form) and `subagent_brief` (the brief
        hook's block in each subagent's first message, both seats, in the quick floor on opus55), each with a
        known-bad that fails offline; their seats and quick marks as R-31 plans them."""
        by_id = {s["id"]: s for s in SPEC["scenarios"]}
        for sid, seats, quick in (("greenman_no_edit", ["fable", "opus55"], []),
                                  ("subagent_brief", ["fable", "opus55"], ["opus55"])):
            self.assertEqual((by_id[sid]["seats"], by_id[sid]["quick"]), (seats, quick), sid)
        no_edit = by_id["redman_no_edit"]
        self.assertEqual(no_edit["overlay"], "no_edit_big")
        self.assertIn(["final_matches", 3, "(?m)^- Redman: held \\d+ — no edits allowed"], no_edit["checks"])
        self.assertIn(["final_matches", 3, "(?i)\\bsave\\b[^\\n]*now,? or later\\?"], no_edit["checks"])   # the planted save()
        self.assertTrue(any("big finding" in k["label"] and k["expect"] == "fail" for k in no_edit["known"]))
        # "Fix now, or later?" is the same question; a question about another finding does not stand in for save()'s
        self.assertEqual([k["expect"] for k in no_edit["known"] if "with a comma" in k["label"] or "another finding" in k["label"]],
                         ["pass", "fail"])
        green = by_id["greenman_no_edit"]
        self.assertIn(["no_path", "knowledge/decisions"], green["checks"])
        self.assertTrue(any(c[0] == "final_matches" and "Greenman: held — no edits allowed" in c[2] for c in green["checks"]))
        brief = by_id["subagent_brief"]
        self.assertEqual(len(brief["turns"]), 2)                       # one case, the second turn resumed
        self.assertIn("layer:SubagentStart brief.py", brief["covers"])                # the hook's event
        self.assertIn(["subagent_marker", 1, "Agent", MARKER], brief["checks"])       # required: at top level
        self.assertIn(["subagent_marker", 2, "Workflow", MARKER], brief["checks"])
        self.assertIn(["hook_fired", "SubagentStart", "brief.py", "ran", {"turn": 1}], brief["checks"])
        self.assertFalse(any("PreToolUse" in json.dumps(c) for c in brief["checks"]))
        bad = [k for k in brief["known"] if k["expect"] == "fail"]
        self.assertTrue(any(k["fails"] == ["subagent_marker"] for k in bad), bad)
        # the known runs in the real shape: the block reaches each agent as its SubagentStart context, the brief the
        # caller wrote stays as written
        good = [a for k in brief["known"] if k["expect"] == "pass" for t in k["turns"] for a in t.get("agents") or []]
        self.assertTrue(good)
        for a in good:
            self.assertNotIn(MARKER, a["first"], a)
            self.assertTrue(any(c.startswith(MARKER) for c in a.get("context") or []), a)
        self.assertTrue(any(k["expect"] == "pass" and "no Workflow tool" in k["label"] for k in brief["known"]))
        self.assertEqual(MARKER, evals.pack_path("public/doctrine/key-rules.md").read_text(encoding="utf-8").splitlines()[0])

    def test_known_runs_grade_as_declared(self):
        problems, ok, owned = self.selftest.known_problems(self.book)
        self.assertEqual(problems, [])
        self.assertEqual(ok, sum(len(s["known"]) for s in SPEC["scenarios"]) - sum(owned.values()))
        # known-bads: an owned good run that already passes, and an owner not of the form R-<n> step <s>
        s = next(x for x in SPEC["scenarios"] if x["id"] == "redman_no_edit")
        good = self.selftest.first_good(s)
        early = dict(s, known=[dict(good, owned_by="R-99 step 1"), dict(good, label="bad owner", owned_by="later")])
        problems, _, _ = self.selftest.known_problems(self.synthetic([early]))
        self.assertIn(f"owned run passed: redman_no_edit {good['label']} — R-99 step 1 must remove owned_by in its commit",
                      problems)
        self.assertTrue(any("'later' is not of the form" in p for p in problems), problems)
        # a known-bad's `passes`: checks of those kinds must pass, so a run failing more than it declares is not as declared
        bad = next(k for k in s["known"] if k["expect"] == "fail")
        wider = dict(s, known=[dict(bad, passes=["no_new_code"])])
        problems, _, _ = self.selftest.known_problems(self.synthetic([wider]))
        self.assertTrue(any("must pass no_new_code" in p for p in problems), problems)

    def test_the_ruled_route_scenarios_no_longer_wait_on_an_owner(self):
        ids = {s["id"] for s in SPEC["scenarios"]}
        self.assertLessEqual({"two_lane", "shell_doc_commit", "foreign_hook_gate", "suffixed_doc_map"}, ids)
        _, _, owned = self.selftest.known_problems(self.book)
        self.assertEqual(owned, {})

    def test_the_flows_known_runs(self):
        """R-31 §7 test 5 (§5.6 "FULL's two flows", §6; G-28 F2-X6): R-1's full flow and its two-lane version in R-22's
        framework — the good run PASSES, "closeout without the handoff" and "a lane finding filed in main's ledger"
        FAIL, every check fails on its violation; no known run carries `owned_by`, since they land after R-23 and
        R-24. Fable only, in no quick (FULL is their first paid run), priced by §6's one analogue list."""
        by_id = {s["id"]: s for s in SPEC["scenarios"]}
        self.assertIn("full_flow", by_id)
        self.assertIn("full_flow_two_lane", by_id)
        flow, lane = by_id["full_flow"], by_id["full_flow_two_lane"]
        for s in (flow, lane):
            self.assertEqual((s["seats"], s["quick"]), (["fable"], []), s["id"])
            self.assertEqual(s["turns"][:2], ["/redman", "/eli5"], s["id"])
            self.assertEqual([t.split()[0] for t in s["turns"][-2:]], ["/project-update", "/closeout"], s["id"])
            self.assertEqual([k["label"] for k in s["known"] if "owned_by" in k], [], s["id"])
            self.assertLessEqual({"redman", "eli5", "doc-guardrails", "project-update", "closeout"}, set(s["covers"]))
        self.assertIn("two_lane", lane["overlay"])
        # R-1's directory steps are not run (S row of 2026-09-23): no turn names them
        self.assertEqual([t for s in (flow, lane) for t in s["turns"] if t.startswith("/directory")], [])
        self.assertEqual([k["expect"] for k in flow["known"]], ["pass", "fail"])
        self.assertIn("closeout without the handoff", flow["known"][1]["label"])
        self.assertEqual([k["expect"] for k in lane["known"]], ["pass", "fail", "fail", "pass", "fail", "fail"])
        self.assertIn("closeout without the handoff", lane["known"][1]["label"])
        self.assertIn("lane finding filed in main's ledger", lane["known"][2]["label"])
        # the script's document is linked on its own turn: held by the end-of-turn check or edited by the session then,
        # and the check must have run there; a later turn's edit does not stand in
        self.assertIn("with its own edit before turn 4 ends", lane["known"][3]["label"])
        self.assertIn("links them only at close-out", lane["known"][4]["label"])
        # a lane's close-out moves rows only in its own lane's ledgers: main's waits for main's close-out
        self.assertIn("carries main's open ledger row", lane["known"][5]["label"])
        for s, doc in ((flow, "knowledge/02-spec-cli.md"), (lane, "knowledge/audit/02-audit-notes.md")):
            self.assertIn(["hook_fired", "Stop", "doc-guardrails/turn_check.py", "ran", {"turn": 4}], s["checks"], s["id"])
            self.assertIn(["any_of", ["hook_fired", "Stop", "doc-guardrails (end of turn)", {"turn": 4}],
                           ["edited_by_tool", doc, {"turn": 4}]], s["checks"], s["id"])

        book = self.synthetic([flow, lane])
        problems, ok, owned = self.selftest.known_problems(book)
        self.assertEqual((problems, owned), ([], {}))
        self.assertEqual(ok, 8)                                  # three good runs PASS, five bad runs FAIL as declared
        problems, ok, _ = self.selftest.violation_problems(book)
        self.assertEqual(problems, [])
        self.assertEqual(ok, len(flow["checks"]) + len(lane["checks"]))
        # the bad runs fail on the handoff, and on where the lane's finding went
        grades = book.grade([(("full_flow", "known", 1), flow), (("full_flow_two_lane", "known", 2), lane)])
        self.assertIn("file_exists", self.selftest.failed_kinds(grades[("full_flow", "known", 1)]))
        self.assertLessEqual({"file_matches", "unchanged"},
                             self.selftest.failed_kinds(grades[("full_flow_two_lane", "known", 2)]))

    def test_every_check_fails_on_its_violation(self):
        problems, ok, table = self.selftest.violation_problems(self.book)
        self.assertEqual(problems, [])
        # a scenario whose good run waits on its owner has no good run to violate yet
        self.assertEqual(ok, sum(len(s["checks"]) for s in SPEC["scenarios"] if self.selftest.first_good(s)))

    def test_co_fail_is_reviewed(self):
        self.assertEqual(self.selftest.co_fail_problems(SPEC), [])
        # known-bads: an entry without its why; a violation that fails every check (it proves nothing)
        s = next(x for x in SPEC["scenarios"] if x["id"] == "large_stop")
        self.assertEqual(self.selftest.co_fail_problems(dict(SPEC, scenarios=[dict(s, co_fail={"4": {"checks": [7]}})])),
                         ["large_stop co_fail 4: no why"])
        wipe = {"id": "wipe", "turns": ["SMALL"], "seats": ["fable"], "quick": [],
                "checks": [["file_exists", "README.md"], ["file_matches", "shelf/report.py", "low_stock"],
                           ["tests_pass", "no_worse"]],
                "violations": {"0": {"ops": [["rm_glob", "**/*"]]}},
                "known": [{"label": "good", "expect": "pass", "turns": [{"final": "ok"}]}]}
        problems, _, _ = self.selftest.violation_problems(self.synthetic([wipe]))
        self.assertIn("wipe check 0: its violation fails every check of the scenario", problems)

    def test_a_kind_without_a_violation_is_a_failure(self):
        self.assertEqual(self.selftest.violation_jobs(SPEC)[1], [])
        self.assertIsNone(evals.CHECKS["holdout"].violate)                 # declared where used
        evals.CHECKS["zz_no_generator"] = evals.Check(lambda a, c, s: (True, "ok"), None)
        self.addCleanup(evals.CHECKS.pop, "zz_no_generator")
        synthetic = {"id": "zz", "turns": ["SMALL"], "seats": ["fable"], "checks": [["zz_no_generator"]],
                     "known": [{"label": "good", "expect": "pass", "turns": [{}]}]}
        self.assertEqual(self.selftest.violation_jobs(dict(SPEC, scenarios=[synthetic]))[1],
                         ["zz check 0 zz_no_generator: no violation generator and none declared"])

    def test_quick_is_a_floor(self):
        self.assertEqual(self.selftest.floor_problems(SPEC), [])
        # the layers are read from the sealed hook set, never typed here; known-bads: a hook on a new event and a
        # second hook on an event already covered, each with no quick case covering it (the per-event key passed this)
        real = evals.SEALED
        self.addCleanup(setattr, evals, "SEALED", real)
        for event, script, want in (("PreCompact", "zz_new_event.py", "layer:PreCompact zz_new_event.py"),
                                    ("Stop", "zz_second_stop.py", "layer:Stop zz_second_stop.py")):
            m = json.loads(real.read_text(encoding="utf-8"))
            m["extra_hooks"].setdefault(event, []).append(
                {"command": f"/usr/bin/python3 <CONFIG>/skills/eli5/{script}", "timeout": 5, "reason": "known-bad"})
            with tempfile.TemporaryDirectory() as tmp:
                evals.SEALED = Path(tmp) / "sealed.json"
                evals.SEALED.write_text(json.dumps(m), encoding="utf-8")
                entries = evals.sealed_hook_entries()
                evals.SEALED = real
            self.assertEqual(self.selftest.floor_problems(SPEC, entries), [f"{want}: no quick case covers it"])
        # a quick case that names a layer but backs it only inside an any_of does not count
        weak = [dict(s, checks=[["any_of", c, ["origin_empty"]] if c[0] == "hook_fired" else c for c in s["checks"]])
                if s["id"] in ("shell_doc_eli5", "shell_doc") else s for s in SPEC["scenarios"]]
        problems = self.selftest.floor_problems(dict(SPEC, scenarios=weak))
        self.assertTrue(any(p.startswith("layer:Stop turn_check.py: covered by") for p in problems), problems)

    def test_every_kind_is_load_bearing(self):
        bearing, rest = self.selftest.load_bearing(self.book)
        self.assertEqual(rest, [])
        self.assertEqual(sorted(bearing), sorted(evals.CHECKS))
        # known-bad: a kind no bad run exercises is not load-bearing
        evals.CHECKS["zz_unused"] = evals.Check(lambda a, c, s: (False, "never used"), None)
        self.addCleanup(evals.CHECKS.pop, "zz_unused")
        self.assertEqual(self.selftest.load_bearing(self.book, kinds=["zz_unused"]), ([], ["zz_unused"]))


class EndOfTurnHold(unittest.TestCase):
    """G-33: R-24's end-of-turn contract makes a script's document the session's only when the session wrote, named
    or committed it; a name in the output of its own writing-capable call counts when the file changed inside that
    call's window. The fixture scripts say what they wrote, so the hold their scenarios expect is one the real
    turn_check.py gives, and every known Stop text is what turn_check.py prints for its run's shape."""

    SCRIPTS = [(["spec_script"], "python3 tools/make_spec.py", "knowledge/02-spec-cli.md"),
               (["two_lane", "lane_script"], "python3 tools/make_notes.py", "knowledge/audit/02-audit-notes.md")]

    @classmethod
    def setUpClass(cls):
        import selftest
        cls.selftest = selftest

    def replay(self, overlays):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        return self.selftest.StopReplay({"id": "t", "overlay": overlays}, Path(tmp.name) / "case")

    def test_each_fixture_script_names_what_it_wrote_and_the_end_of_turn_check_holds_it(self):
        for overlays, cmd, doc in self.SCRIPTS:
            with self.subTest(cmd=cmd):
                r = self.replay(overlays)
                r.stop()                                            # the turn before: nothing to hold
                self.assertEqual(r.bash(cmd), f"wrote {doc}")
                reason = r.stop()
                self.assertIsNotNone(reason, f"{cmd}: turn_check.py let the turn end")
                self.assertIn(f"- {doc} (written by a command this session ran): MISSING — no `roadmap:` line in the first "
                              f"40 lines", reason)   # G-54: a generator's output is named as the session's own

    def test_a_script_that_names_nothing_is_not_held(self):
        """The old output: the same file, changed inside the same call's window, is not attributed when the call's
        output does not name it — R-24's contract, which the scenarios must meet rather than route around."""
        for overlays, cmd, doc in self.SCRIPTS:
            with self.subTest(cmd=cmd):
                r = self.replay(overlays)
                r.stop()
                r.bash(cmd, out="done")
                self.assertTrue((r.proj / doc).is_file())
                self.assertIsNone(r.stop())

    def test_every_known_stop_text_is_what_turn_check_prints(self):
        ok, problems = self.selftest.stop_text_problems(SPEC)
        self.assertEqual(problems, [])
        held = sum(1 for s in SPEC["scenarios"] for k in s["known"]
                   if any(self.selftest.turn_check_hooks(t) for t in k["turns"]))
        self.assertEqual(ok, held)
        self.assertGreaterEqual(held, 8)
        # known-bads: a hand-written Stop text the hook no longer prints; a script output the script does not print
        s = json.loads(json.dumps(next(x for x in SPEC["scenarios"] if x["id"] == "shell_doc_eli5")))
        self.selftest.turn_check_hooks(s["known"][0]["turns"][1])[0]["text"] = (
            "doc-guardrails (end of turn): these documents changed this turn and do not name a valid roadmap item "
            "(missing or broken): knowledge/02-spec-cli.md")
        s["known"][1]["turns"][1]["bash"][0]["out"] = "done"
        # and W-28's shape carrying a source line turn_check.py no longer has
        s["known"][1]["turns"][1]["bash"][1]["out"] = '    print("doc-guardrails (end of turn): these documents changed")'
        ok, problems = self.selftest.stop_text_problems(dict(SPEC, scenarios=[s]))
        held_here = sum(1 for k in s["known"] if any(self.selftest.turn_check_hooks(t) for t in k["turns"]))
        self.assertEqual(ok, held_here - 1)                  # every held run but the doctored one (R-28's two stay right)
        self.assertEqual(len(problems), 3, problems)
        self.assertIn("turn_check.py prints", problems[0])
        self.assertIn("`python3 tools/make_spec.py` prints 'wrote knowledge/02-spec-cli.md'; the run records 'done'",
                      problems[1])
        self.assertIn("which turn_check.py does not hold", problems[2])


class SealedBuilder(unittest.TestCase):
    """build_sealed writes exactly the manifest into a sealed config folder — always a temp folder here, never the real
    one (§5.8; tests 23, 15b's builder half, 23a's builder half)."""
    KEYS = {"syncClaudeAiPlugins": False, "syncClaudeAiSkills": False, "disableClaudeAiConnectors": True}

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name)
        self.config = self.tmp / "sealed"
        home = self.tmp / "home"
        patcher = mock.patch.object(Path, "home", lambda: home)
        patcher.start()
        self.addCleanup(patcher.stop)
        real = evals.SEALED
        self.addCleanup(setattr, evals, "SEALED", real)

    def manifest(self, **changes):
        m = json.loads((Path(evals.HERE) / "sealed.json").read_text(encoding="utf-8"))
        m.update(changes)
        path = self.tmp / f"sealed-{len(list(self.tmp.glob('sealed-*.json')))}.json"
        path.write_text(json.dumps(m), encoding="utf-8")
        evals.SEALED = path
        return m

    def settings(self):
        return json.loads((self.config / "settings.json").read_text(encoding="utf-8"))

    def built_keys(self, settings):
        """[(normal form, logged?)] of a built settings.json's entries (R-25: the runner's wrapper unwrapped)."""
        roots = evals.sealed_roots(self.config)
        return sorted((evals.hook_key(e, mt, h, roots), evals.is_logged(h)) for e, mt, h in evals._entries(settings))

    def expected(self, m, probe=False):
        """The installer's registration for manifest["installer"], plus extra_hooks (and probe_hooks), every entry
        logged (R-25 § 5.5: the builder's source is install.py's registered(), no longer a literal hook list)."""
        installer = evals.installer_module()
        want = installer.registered({}, evals.RUNNER_PY, str(self.config), **m["installer"])
        for key in ("extra_hooks",) + (("probe_hooks",) if probe else ()):
            for e, v in m[key].items():
                for x in v:
                    installer.add_hook_group(want, e, evals._manifest_hook(x, self.config), x.get("matcher"))
        roots = evals.sealed_roots(self.config)
        return sorted((evals.hook_key(e, mt, h, roots), True) for e, mt, h in evals._entries(want))

    def snapshot(self):
        return {p.relative_to(self.config).as_posix(): (os.readlink(p) if p.is_symlink() else p.read_bytes() if p.is_file() else None)
                for p in sorted(self.config.rglob("*"))}

    def test_sealed_builder_writes_exactly_the_manifest(self):
        m = self.manifest()
        for rel, body in ((".credentials.json", b'{"token": "x"}'), (".claude.json", b"{}"), ("plugins/synced/x", b"x"),
                          ("skills/synced/y", b"y")):
            (self.config / rel).parent.mkdir(parents=True, exist_ok=True)
            (self.config / rel).write_bytes(body)
        evals.build_sealed(self.config)
        links = sorted(p.name for p in (self.config / "skills").iterdir() if p.is_symlink())
        self.assertEqual(links, sorted(m["skills"]))
        for name in m["skills"]:
            self.assertEqual((self.config / "skills" / name).resolve(), evals.pack_path(name).resolve())
        settings = self.settings()
        self.assertEqual(self.built_keys(settings), self.expected(m))           # every entry logged
        self.assertNotIn("hooks", m)                                             # no literal hook list (R-25)
        self.assertEqual({k: v for k, v in settings.items() if k != "hooks"}, self.KEYS)
        self.assertEqual(evals.sealed_settings_problems(settings), [])
        for rel, body in ((".credentials.json", b'{"token": "x"}'), (".claude.json", b"{}"), ("plugins/synced/x", b"x"),
                          ("skills/synced/y", b"y")):
            self.assertEqual((self.config / rel).read_bytes(), body, rel)       # what login and sync wrote is left alone
        before = self.snapshot()
        evals.build_sealed(self.config)
        self.assertEqual(self.snapshot(), before)                                # idempotent
        # probe: exactly probe_hooks added, and removed by the next plain build
        evals.build_sealed(self.config, probe=True)
        self.assertEqual(self.built_keys(self.settings()), self.expected(m, probe=True))
        evals.build_sealed(self.config)
        self.assertEqual(self.snapshot(), before)
        # a link of ours for a skill the manifest dropped goes; a folder that is not ours stays
        self.manifest(skills=[s for s in m["skills"] if s != "eli5"])
        evals.build_sealed(self.config)
        self.assertFalse((self.config / "skills" / "eli5").exists())
        self.assertEqual((self.config / "skills" / "synced" / "y").read_bytes(), b"y")
        # known-bad: a manifest whose settings lack the connectors key builds settings the check names
        self.manifest(settings={k: v for k, v in self.KEYS.items() if k != "disableClaudeAiConnectors"})
        evals.build_sealed(self.config)
        self.assertEqual(evals.sealed_settings_problems(self.settings()),
                         ["settings.json: disableClaudeAiConnectors is not true"])

    def test_an_entry_in_the_launchers_form_is_left_alone(self):
        launched = {"command": "/usr/bin/python3", "args": ["-c", "LAUNCHER", "<CONFIG>/skills/eli5/zz_launched.py"], "timeout": 10}
        m = self.manifest(extra_hooks=dict(json.loads((Path(evals.HERE) / "sealed.json").read_text(encoding="utf-8"))["extra_hooks"],
                                           SessionStart=[launched]))
        evals.build_sealed(self.config)
        groups = self.settings()["hooks"]["SessionStart"]
        gate, start = groups[0]["hooks"][0], groups[-1]["hooks"][0]           # the installer's first, the extra last
        self.assertEqual((start["command"], start["args"][:2]), ("/usr/bin/python3", ["-c", "LAUNCHER"]))
        self.assertTrue(evals.is_logged(start))                                  # the launcher writes its own log line
        self.assertEqual(gate["args"][:2], ["-c", evals.installer_module().LAUNCHER])   # the installer's gate (R-26 § 5.9)
        self.assertTrue(evals.launcher_form(self.settings()["hooks"]["Stop"][0]["hooks"][0]))   # R-26: the turn check too
        post = self.settings()["hooks"]["PostToolUse"][0]["hooks"][0]["command"]
        words = shlex.split(post)                   # as the shell reads it (sh; Git Bash on Windows): paths are quoted
        at = next(i for i, w in enumerate(words) if re.split(r"[\\/]", w)[-1] == "hook_log.py")
        self.assertEqual(re.split(r"[\\/]", words[at])[-2:], ["evals", "hook_log.py"])
        self.assertEqual(words[at + 1:at + 4], ["--event", "PostToolUse", "--script"])
        self.assertEqual(re.split(r"[\\/]", words[-1])[-2:], ["doc-guardrails", "hook.sh"])   # the entry's own command stays last

    def test_the_builder_links_the_skills_in_either_layout(self):
        for public in (False, True):
            pack = self.tmp / ("public" if public else "private")
            m = self.manifest()
            for name in m["skills"]:
                (pack / ("skills/" + name if public else name)).mkdir(parents=True, exist_ok=True)
            for rel in ("doc-guardrails/hook.sh", "doc-guardrails/turn_check.py", "evals/hook_log.py"):
                p = pack / (rel if not public or rel.startswith("evals/") else "skills/" + rel)
                p.parent.mkdir(parents=True, exist_ok=True)
                p.write_text("x\n", encoding="utf-8")
            installer = pack / ("install.py" if public else "public/install.py")     # the builder registers through it
            installer.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(evals.installer_module().__file__, installer)
            real = evals.PACK
            evals.PACK = pack
            try:
                config = self.tmp / f"sealed-{public}"
                evals.build_sealed(config)
                for name in m["skills"]:
                    self.assertEqual((config / "skills" / name).resolve(), evals.pack_path(name).resolve())
            finally:
                evals.PACK = real


class FakeCLI:
    """A `claude` binary replaying reviewed outputs; every call is recorded (argv, cwd and whether it was an empty
    folder at the time, env). Anything else goes to the real subprocess.run."""

    def __init__(self, cli, auth=(0, '{"loggedIn": true, "authMethod": "claude.ai"}'), mcp=None,
                 version="2.1.281 (Claude Code)"):
        self.cli, self.auth, self.version = cli, auth, version
        self.mcp = mcp if mcp is not None else (0, (SHAPES / "mcp_list_empty_2_1_281.txt").read_text(encoding="utf-8"))
        self.calls, self.real = [], subprocess.run

    def __call__(self, cmd, *args, **kw):
        if not cmd or str(cmd[0]) != str(self.cli):
            return self.real(cmd, *args, **kw)
        cwd = kw.get("cwd")
        self.calls.append({"argv": list(cmd[1:]), "cwd": cwd, "empty": bool(cwd) and not any(Path(cwd).iterdir()),
                           "env": kw.get("env")})
        if cmd[1:] == ["--version"]:
            code, out = 0, self.version + "\n"
        elif cmd[1:] == ["auth", "status"]:
            code, out = self.auth
        elif cmd[1:] == ["mcp", "list"]:
            code, out = self.mcp
            mcp_json = Path(cwd or os.getcwd()) / ".mcp.json"
            if mcp_json.is_file():   # the folder's own servers are listed too (§4)
                out = "".join(f"{n}: npx {n} - ✓ Connected\n" for n in json.loads(mcp_json.read_text())["mcpServers"])
        else:
            code, out = 1, "unexpected call\n"
        return subprocess.CompletedProcess(cmd, code, out, "")


class Preflight(unittest.TestCase):
    """preflight(): every refusal before any session, exit 2 naming what it read (§5.8; tests 25a, 15b's half)."""
    AUTH_OUT = json.loads((SHAPES / "auth_status_logged_out_2_1_281.json").read_text(encoding="utf-8"))
    SYNCED = json.loads((SHAPES / "init_synced_2_1_280.json").read_text(encoding="utf-8"))
    CLI = "2.1.281"   # the fixture's reviewed version, the one FakeCLI prints; never the live manifest's, which moves

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name)
        self.config, self.runs = self.tmp / "sealed", self.tmp / "runs"
        self.runs.mkdir()
        home = self.tmp / "home"
        (home / ".claude" / "doctrine").mkdir(parents=True)
        (home / ".claude" / "doctrine" / "gate.sh").write_text("#!/bin/sh\n", encoding="utf-8")
        (home / ".claude" / "doctrine" / "gate.py").write_text("print()\n", encoding="utf-8")   # the loader's target (R-26 § 5.9)
        (home / ".claude" / "doctrine" / "brief.py").write_text("print()\n", encoding="utf-8")   # the brief hook's (R-27)
        self.home = home
        for p in (mock.patch.object(Path, "home", lambda: home),):
            p.start()
            self.addCleanup(p.stop)
        real = evals.SEALED
        self.addCleanup(setattr, evals, "SEALED", real)
        self.manifest()
        self.cli = self.tmp / "versions" / self.CLI
        self.cli.parent.mkdir()
        self.cli.write_text("#!/bin/sh\n", encoding="utf-8")
        self.repo = self.tmp / "repo"
        self.repo_manifest(settings=True)

    def manifest(self, **changes):
        m = json.loads((Path(evals.HERE) / "sealed.json").read_text(encoding="utf-8"))
        m["cli_version"] = self.CLI
        m.update(changes)
        path = self.tmp / f"sealed-{len(list(self.tmp.glob('sealed-*.json')))}.json"
        path.write_text(json.dumps(m), encoding="utf-8")
        evals.SEALED = path
        evals.build_sealed(self.config)

    def git(self, *args, env=None):
        return subprocess.run(["git", "-C", str(self.repo), "-c", "user.name=t", "-c", "user.email=t@t", *args],
                              capture_output=True, text=True, check=True, env=env)

    def commit_manifest(self, when, **manifest):
        """Commit evals/sealed.json in the fixture repository at the given unix time."""
        path = self.repo / "evals" / "sealed.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(manifest), encoding="utf-8")
        self.git("add", "-A")
        stamp = f"@{int(when)} +0000"
        self.git("commit", "-qm", f"manifest at {when}", env=dict(os.environ, GIT_AUTHOR_DATE=stamp, GIT_COMMITTER_DATE=stamp))

    def repo_manifest(self, settings):
        self.repo.mkdir(exist_ok=True)
        self.git("init", "-q", "-b", "main")
        self.commit_manifest(1_000_000, hooks={}, settings={"syncClaudeAiPlugins": False, "syncClaudeAiSkills": False})
        self.T = 2_000_000                                         # the third key lands at T
        self.commit_manifest(self.T, hooks={}, settings={"syncClaudeAiPlugins": False, "syncClaudeAiSkills": False,
                                                         "disableClaudeAiConnectors": True})

    def recorded_case(self, name, t_start, init):
        import datetime as dt
        case = self.runs / "run" / name
        case.mkdir(parents=True)
        stamp = dt.datetime.fromtimestamp(t_start, dt.timezone.utc).isoformat()
        (case / "record.json").write_text(json.dumps({"scenario": "t", "seat": "fable", "env": {}, "hooks": [],
                                                      "turns": [{"t_start": stamp, "t_end": stamp, "init": init}]}),
                                          encoding="utf-8")

    def preflight(self, fake=None, **kw):
        fake = fake or FakeCLI(self.cli)
        with mock.patch.object(evals.subprocess, "run", fake):
            code, message = evals.preflight(str(self.cli), self.config, runs_root=self.runs, repo=self.repo, **kw)
        return code, message, fake

    def test_preflight_reads_what_the_cli_prints(self):
        self.assertEqual(self.preflight()[:2], (0, ""))
        # login: the harvested logged-out JSON (exit 1), a logged-in one, and stdout that is not JSON
        code, message, _ = self.preflight(FakeCLI(self.cli, auth=(self.AUTH_OUT["exit"], self.AUTH_OUT["stdout"])))
        self.assertEqual(code, 2)
        self.assertIn(f"`CLAUDE_CONFIG_DIR={self.config} claude auth login`", message)
        self.assertNotIn("claude\n", message)                                     # never an interactive session
        code, message, _ = self.preflight(FakeCLI(self.cli, auth=(0, "Logged in as someone\n")))
        self.assertEqual((code, message), (2, "auth status unreadable: Logged in as someone"))
        # mcp list: every reviewed empty-list fixture passes; a server line, a claude.ai line, a changed output or a
        # non-zero exit refuses, quoting what it read
        for fixture in sorted(SHAPES.glob("mcp_list_*.txt")):
            text = fixture.read_text(encoding="utf-8")
            self.assertEqual(self.preflight(FakeCLI(self.cli, mcp=(0, text)))[:2], (0, ""), fixture.name)
            code, message, _ = self.preflight(FakeCLI(self.cli, mcp=(0, text + "claude.ai Example: https://x - ✓ Connected\n")))
            self.assertEqual(code, 2)
            self.assertIn("claude.ai Example", message)
            self.assertIn("disableClaudeAiConnectors", message)
        code, message, _ = self.preflight(FakeCLI(self.cli, mcp=(0, "github: npx github - ✓ Connected\n")))
        self.assertEqual(code, 2)
        self.assertIn("github: npx github - ✓ Connected", message)
        self.assertEqual(self.preflight(FakeCLI(self.cli, mcp=(1, (SHAPES / "mcp_list_empty_2_1_281.txt").read_text())))[0], 2)
        # every CLI call through the resolved path, with the updater off and the sealed folder as its config
        _, _, fake = self.preflight()
        self.assertEqual(sorted(c["argv"][0] for c in fake.calls), ["--version", "auth", "mcp"])
        for c in fake.calls:
            self.assertEqual((c["env"]["DISABLE_AUTOUPDATER"], c["env"]["CLAUDE_CONFIG_DIR"]), ("1", str(self.config)))

    def test_mcp_list_runs_from_an_empty_scratch_folder(self):
        (self.tmp / "here").mkdir()
        (self.tmp / "here" / ".mcp.json").write_text(json.dumps({"mcpServers": {"local-db": {}}}), encoding="utf-8")
        cwd = os.getcwd()
        os.chdir(self.tmp / "here")
        self.addCleanup(os.chdir, cwd)
        code, _, fake = self.preflight()
        self.assertEqual(code, 0)
        call = next(c for c in fake.calls if c["argv"] == ["mcp", "list"])
        self.assertTrue(call["empty"])
        self.assertNotEqual(Path(call["cwd"]).resolve(), (self.tmp / "here").resolve())
        self.assertFalse(Path(call["cwd"]).exists())                                # gone after the call
        # known-bad: the old shape, the call made in the caller's folder, lists that folder's server and refuses
        old = FakeCLI(self.cli)
        self.assertIn("local-db", old([str(self.cli), "mcp", "list"], cwd=str(self.tmp / "here")).stdout)

    def test_the_sealed_folder_must_hold_no_account_sync(self):
        settings = json.loads((self.config / "settings.json").read_text(encoding="utf-8"))
        for key in ("syncClaudeAiSkills", "disableClaudeAiConnectors"):
            (self.config / "settings.json").write_text(json.dumps({k: v for k, v in settings.items() if k != key}))
            code, message, _ = self.preflight()
            self.assertEqual(code, 2)
            self.assertIn(key, message)
        (self.config / "settings.json").write_text(json.dumps(settings))
        (self.config / "plugins" / "synced" / "acct").mkdir(parents=True)
        (self.config / "plugins" / "synced" / "acct" / "x.json").write_text("{}")
        code, message, _ = self.preflight()
        self.assertEqual(code, 2)
        self.assertIn("plugins/synced/acct/x.json", message)

    def test_the_sync_window_starts_at_the_last_settings_change(self):
        self.assertEqual(evals.window_start(self.repo, self.runs), self.T)
        self.recorded_case("before", self.T - 100, self.SYNCED)                   # recorded before the fix
        self.assertEqual(self.preflight()[0], 0)
        # known-bad: the old rule, "the newest sealed init on record", reads that init and would refuse every launch
        newest = max(self.runs.rglob("record.json"), key=lambda p: json.loads(p.read_text())["turns"][0]["t_start"])
        self.assertTrue(any(evals.account_entries(json.loads(newest.read_text())["turns"][0]["init"])))
        self.commit_manifest(self.T + 50, hooks={}, cli_version="2.1.282",
                             settings={"syncClaudeAiPlugins": False, "syncClaudeAiSkills": False,
                                       "disableClaudeAiConnectors": True})
        self.assertEqual(evals.window_start(self.repo, self.runs), self.T)        # a cli_version change does not move T
        self.recorded_case("after", self.T + 100, self.SYNCED)
        code, message, _ = self.preflight()
        self.assertEqual(code, 2)
        self.assertIn("account sync reached the sealed folder", message)
        connector = {"type": "system", "subtype": "init", "plugins": [], "skills": [],
                     "mcp_servers": [{"name": "claude.ai connector-1", "status": "connected", "source": "claudeai"}]}
        shutil.rmtree(self.runs / "run" / "after")
        self.recorded_case("after", self.T + 100, connector)
        self.assertIn("claude.ai connector-1", self.preflight()[1])


    def test_every_hook_logs_and_resolves(self):
        settings = json.loads((self.config / "settings.json").read_text(encoding="utf-8"))
        plain = json.loads(json.dumps(settings))
        stop = plain["hooks"]["Stop"][0]["hooks"][0]                     # the launcher's form since R-26 (G-34 point 1)
        plain["hooks"]["Stop"][0]["hooks"][0] = {"type": "command", "timeout": stop.get("timeout"),
                                                 "command": f"/usr/bin/python3 {shlex.quote(evals.settings_script(stop))}"}
        (self.config / "settings.json").write_text(json.dumps(plain))
        code, message, fake = self.preflight()
        self.assertEqual(code, 2)
        self.assertIn("hook log: Stop", message)
        self.assertIn("turn_check.py writes no log — no session launched", message)
        self.assertEqual(fake.calls, [])                                           # refused before any CLI call
        # a script that is a link to nothing: refused, naming the entry; once the link resolves, it passes
        target = self.tmp / "brief.py"
        link = self.tmp / "brief-link.py"
        link.symlink_to(target)
        dangling = json.loads(json.dumps(settings))
        # a shell string names its path quoted, as the sealed builder writes one (a Windows path's backslashes are
        # escapes to the shell, so unquoted they would never reach the hook)
        dangling["hooks"]["PreToolUse"] = [{"hooks": [evals.wrap_hook("PreToolUse", {
            "command": f"/usr/bin/python3 {shlex.quote(str(link))}"})]}]
        (self.config / "settings.json").write_text(json.dumps(dangling))
        code, message, _ = self.preflight()
        self.assertEqual((code, message), (2, f"hook entry PreToolUse {link} does not resolve — no session launched"))
        target.write_text("print()\n", encoding="utf-8")
        self.assertEqual(self.preflight()[0], 0)

    def test_the_brief_entry_is_refused_when_its_doctrine_link_dangles(self):
        """R-27 (G-27 F1-Y11): the sealed build's own brief entry names <sealed>/doctrine/brief.py, which resolves only
        through the doctrine folder's link; a link to nothing is refused before any launch, naming the entry — the
        tolerant launcher would otherwise run every case with no brief and exit 0."""
        entries = [h for g in json.loads((self.config / "settings.json").read_text(encoding="utf-8"))["hooks"]["SubagentStart"]
                   for h in g["hooks"]]
        self.assertEqual([Path(evals.settings_script(h)).name for h in entries], ["brief.py"])   # the installer's entry
        self.assertEqual(self.preflight()[:2], (0, ""))
        brief = self.home / ".claude" / "doctrine" / "brief.py"
        brief.unlink()
        brief.symlink_to(self.tmp / "nowhere" / "public" / "doctrine" / "brief.py")    # committed before its target lands
        code, message, fake = self.preflight()
        self.assertEqual((code, message),
                         (2, f"hook entry SubagentStart {self.config / 'doctrine' / 'brief.py'} does not resolve — no session launched"))
        self.assertEqual(fake.calls, [])                                           # refused before any CLI call

    def test_the_reviewed_cli(self):
        self.manifest(cli_version="2.1.281")
        self.assertEqual(self.preflight()[0], 0)
        code, message, _ = self.preflight(FakeCLI(self.cli, version="2.1.282 (Claude Code)"))
        self.assertEqual((code, message), (2, "CLI 2.1.282 is not the reviewed 2.1.281 (evals/sealed.json's cli_version): "
                                               "run the reviewed version, or review this one with a --probe run first "
                                               "— no session launched"))
        self.assertEqual(self.preflight(FakeCLI(self.cli, version="2.1.282 (Claude Code)"), probe=True)[0], 0)


class BatchBinary(unittest.TestCase):
    """A batch runs on the binary it resolved at its start; a change to that file stops it (test 23d)."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name)
        home = self.tmp / "home"
        home.mkdir()
        p = mock.patch.object(Path, "home", lambda: home)
        p.start()
        self.addCleanup(p.stop)
        self.versions, self.bin = self.tmp / "versions", self.tmp / "bin"
        self.versions.mkdir()
        self.bin.mkdir()
        for v in ("2.1.281", "2.1.282"):
            self.fake(self.versions / v, v)
        self.claude = self.bin / ("claude" + CLI_EXT)
        self.claude.symlink_to(self.versions / ("2.1.281" + CLI_EXT))
        self.config = self.tmp / "sealed"
        evals.build_sealed(self.config)
        self.scenarios = [{"id": "c1", "turns": ["SMALL"], "seats": ["fable"], "checks": []},
                          {"id": "c2", "turns": ["SMALL", "SMALL"], "seats": ["fable"], "checks": []},
                          {"id": "c3", "turns": ["SMALL"], "seats": ["fable"], "checks": []}]

    def fake(self, path, version):
        """`path` is the fake's name without CLI_EXT."""
        write_fake_cli(path, version, [
            json.dumps({"type": "system", "subtype": "init", "session_id": "s1", "claude_code_version": version}),
            json.dumps({"type": "result", "result": "done", "total_cost_usd": 0.1, "session_id": "s1"})])

    def batch(self, after_case2_turn1=None):
        """Run the three cases one at a time; `after_case2_turn1` acts right after case 2's first turn."""
        real, calls = subprocess.run, []

        def recording(cmd, *a, **kw):
            r = real(cmd, *a, **kw)
            if cmd and "-p" in cmd:
                calls.append({"cmd": list(map(str, cmd)), "env": kw.get("env"), "stdout": r.stdout})
                if len([c for c in calls if "-p" in c["cmd"]]) == 2 and after_case2_turn1:
                    after_case2_turn1()
            elif cmd and str(cmd[0]).startswith(str(self.versions)):
                calls.append({"cmd": list(map(str, cmd)), "env": kw.get("env"), "stdout": r.stdout})
            return r
        cli = os.path.realpath(str(self.claude))
        with mock.patch.object(evals.subprocess, "run", recording):
            grades, changed = evals.run_batch([(s, "fable") for s in self.scenarios], SPEC, self.tmp / "out",
                                              self.config, cli, evals.cli_version_of(cli), parallel=1)
        return grades, changed, calls

    def launches(self, calls):
        return [c for c in calls if "-p" in c["cmd"]]

    def test_a_batch_runs_on_the_binary_it_started_with(self):
        swap = lambda: (self.claude.unlink(), self.claude.symlink_to(self.versions / ("2.1.282" + CLI_EXT)))
        grades, changed, calls = self.batch(swap)
        self.assertIsNone(changed)
        self.assertEqual(len(self.launches(calls)), 4)                  # 1 + 2 + 1 turns, all launched
        resolved = os.path.realpath(self.versions / ("2.1.281" + CLI_EXT))
        for c in calls:
            self.assertEqual(c["cmd"][0], resolved)
            self.assertEqual(c["env"]["DISABLE_AUTOUPDATER"], "1")
        for c in self.launches(calls):
            self.assertIn('"claude_code_version": "2.1.281"', c["stdout"])
        for rec in (self.tmp / "out").rglob("record.json"):
            self.assertEqual(json.loads(rec.read_text())["env"]["cli_path"], resolved)
        self.assertNotIn("DISABLE_AUTOUPDATER", (self.config / "settings.json").read_text(encoding="utf-8"))
        # known-bad: launching by the name `claude` after the swap runs the other version
        by_name = subprocess.run([shutil.which("claude", path=str(self.bin)), "--version"], capture_output=True,
                                 text=True)
        self.assertTrue(by_name.stdout.startswith("2.1.282"))

    def test_a_changed_binary_stops_the_batch(self):
        name = self.versions / "2.1.281"
        resolved = Path(str(name) + CLI_EXT)
        for action, new in ((lambda: self.fake(name, "2.1.282"), "2.1.282"), (lambda: resolved.unlink(), "missing")):
            self.fake(name, "2.1.281")                                    # each round starts on 2.1.281
            shutil.rmtree(self.tmp / "out", ignore_errors=True)
            grades, changed, calls = self.batch(action)
            self.assertEqual(changed, ("2.1.281", new))
            self.assertEqual(len(self.launches(calls)), 2)               # nothing launched after the change
            by = {g["scenario"]: g for g in grades}
            self.assertEqual(by["c2"]["not_completed"], f"CLI 2.1.281 → {new} before turn 2")
            self.assertEqual(len(json.loads((self.tmp / "out" / "c2-fable" / "record.json").read_text())["turns"]), 1)
            self.assertEqual(by["c3"]["not_run"], "not launched: CLI changed")
            import io, contextlib
            with contextlib.redirect_stdout(io.StringIO()) as buf:
                evals.report(grades, self.tmp / "out")
            self.assertIn(f"not completed: CLI 2.1.281 → {new} before turn 2", buf.getvalue())
            self.assertIn("not launched: CLI changed", buf.getvalue())
        # known-bad: reading the version once per batch would have run case 2's turn 2 on the replaced file
        self.fake(name, "2.1.282")
        self.assertEqual(evals.cli_version_of(str(resolved)), "2.1.282")

    def test_no_claude_on_path_launches_nothing(self):
        import io, contextlib
        real, seen = subprocess.run, []
        with mock.patch.dict(os.environ, {"PATH": str(self.tmp / "empty")}), \
                mock.patch.object(evals.subprocess, "run", lambda *a, **k: seen.append(a) or real(*a, **k)), \
                mock.patch.object(evals, "preflight", lambda *a, **k: self.fail("preflight called")), \
                contextlib.redirect_stderr(io.StringIO()) as err:
            code = evals.main(["--only", "small_no_ceremony", "--seat", "fable", "--out", str(self.tmp / "out"),
                               "--config", str(self.config)])
        self.assertEqual(code, 2)
        self.assertIn("claude not found on PATH — no session launched", err.getvalue())
        self.assertEqual(seen, [])
        self.assertFalse((self.tmp / "out").exists())
        # known-bad: the unguarded resolve
        with self.assertRaises(TypeError):
            os.path.realpath(None)


class Modes(unittest.TestCase):
    """run.py's launch path refuses while the selftest fails (test 25); --selftest, --build-sealed and --sealed-check."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name)
        home = self.tmp / "home"
        home.mkdir()
        p = mock.patch.object(Path, "home", lambda: home)
        p.start()
        self.addCleanup(p.stop)
        self.bin = self.tmp / "bin"
        self.bin.mkdir()
        write_fake_cli(self.bin / "claude", "2.1.281")
        self.config = self.tmp / "sealed"

    def main(self, *argv, selftest=0, preflight=(0, "")):
        import io, contextlib
        import selftest as st
        real, calls = subprocess.run, []

        def recording(cmd, *a, **kw):
            calls.append(list(map(str, cmd)))
            return real(cmd, *a, **kw)
        out, err = io.StringIO(), io.StringIO()
        with mock.patch.dict(os.environ, {"PATH": f"{self.bin}{os.pathsep}{os.environ['PATH']}"}), \
                mock.patch.object(st, "main", lambda argv=None, quiet=False: selftest), \
                mock.patch.object(evals, "preflight", lambda *a, **k: preflight), \
                mock.patch.object(evals, "run_batch", lambda *a, **k: self.fail("a batch was launched")), \
                mock.patch.object(evals.subprocess, "run", recording), \
                contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = evals.main(list(argv) + ["--out", str(self.tmp / "out"), "--config", str(self.config)])
        return code, out.getvalue() + err.getvalue(), [c for c in calls if "-p" in c]

    def test_run_refuses_to_launch_while_selftest_fails(self):
        code, text, launches = self.main(selftest=3)
        self.assertEqual(code, 3)
        self.assertIn("selftest failed: 3 problem(s) — no session launched", text)
        self.assertEqual(launches, [])
        code, text, launches = self.main(selftest=0, preflight=(2, "auth status unreadable: x"))
        self.assertEqual((code, launches), (2, []))
        self.assertIn("auth status unreadable: x", text)

    def test_the_selftest_mode_runs_the_selftest(self):
        import selftest as st
        seen = []
        with mock.patch.object(st, "main", lambda argv=None, quiet=False: seen.append(argv) or 0):
            self.assertEqual(evals.main(["--selftest", "--co-fail"]), 0)
            self.assertEqual(evals.main(["--selftest"]), 0)
        self.assertEqual(seen, [["--co-fail"], []])

    def test_build_sealed_and_sealed_check(self):
        import io, contextlib
        with contextlib.redirect_stdout(io.StringIO()) as out:
            self.assertEqual(evals.main(["--build-sealed", "--config", str(self.config)]), 0)
        self.assertTrue((self.config / "settings.json").is_file())
        self.assertIn(str(self.config), out.getvalue())
        with contextlib.redirect_stdout(io.StringIO()) as out:
            self.assertEqual(evals.main(["--sealed-check"]), 0)
        text = out.getvalue()
        m = evals.manifest()
        hooks = len(evals.sealed_hook_entries())
        self.assertIn(f"sealed: build = installer {json.dumps(m['installer'], sort_keys=True)} + extra_hooks "
                      f"({len(m['skills'])} skills, {hooks} hooks)", text)
        self.assertIn("the live leg is skipped", text)                           # a home with no pack hooks
        self.assertIn("syncClaudeAiPlugins false, syncClaudeAiSkills false, disableClaudeAiConnectors true", text)
        self.assertIn("Stop", text)
        self.assertIn("runner", text)


class CaseCost(unittest.TestCase):
    """A case's cost is the CLI's own accounting read by version (criterion 21, G-27 F1-Y2)."""

    def turns(self, name):
        return [evals.parse_stream(p.read_text(encoding="utf-8")) for p in sorted((COSTS / name).glob("turn*.jsonl"))]

    def synthetic(self, *pairs):
        """Turns from (version, total or None) pairs."""
        return [{"init": {"claude_code_version": v}, "cost": c} for v, c in pairs]

    def test_case_cost_reads_the_cli_accounting_by_version(self):
        cost, per_turn, error, kind = evals.case_cost(self.turns("redman_no_edit-fable-2_1_280"))
        self.assertEqual((round(cost, 2), error, kind), (1.30, None, None))            # the last running total
        self.assertEqual([round(x, 3) for x in per_turn], [0.587, 0.580, 0.134])        # each turn its difference
        self.assertEqual(round(sum(t["cost"] for t in self.turns("redman_no_edit-fable-2_1_280")), 2), 3.06)  # summed: wrong
        cost, per_turn, error, _ = evals.case_cost(self.turns("pushback_ruling-fable-2_1_272"))
        self.assertEqual((round(cost, 2), error), (0.70, None))                           # before 2.1.277: summed
        self.assertEqual(round(max(t["cost"] for t in self.turns("pushback_ruling-fable-2_1_272")), 2), 0.53)  # max: wrong
        for turns, kind, text in (
                (self.synthetic(("2.1.280", 0.6), ("2.1.280", 0.5)), "falling",
                 "turn 2 total $0.50 below turn 1 $0.60 on 2.1.280: the CLI's accounting changed again"),
                (self.synthetic(("2.1.276", 0.6), ("2.1.277", 0.9)), "mixed", "mixed CLI versions: 2.1.276, 2.1.277"),
                (self.synthetic(("2.1.280", 0.6), ("2.1.280", None)), "no_result", "turn 2 reported no cost")):
            cost, per_turn, error, k = evals.case_cost(turns)
            self.assertEqual((cost, error, k), (None, text, kind))

    def test_an_uncostable_case_is_counted_apart(self):
        with tempfile.TemporaryDirectory() as tmp:
            costed = make_case(Path(tmp) / "a", [{"prompt": "p"}])
            g_ok = evals.grade(costed, {"id": "ok", "checks": []})
            broken = make_case(Path(tmp) / "b", [{"prompt": "p"}])
            rec = json.loads((broken / "record.json").read_text())
            rec["turns"][0]["cost"] = None
            (broken / "record.json").write_text(json.dumps(rec))
            g_bad = evals.grade(broken, {"id": "bad", "checks": []})
            self.assertEqual((g_bad["cost"], g_bad["cost_error"], g_bad["cost_error_kind"]),
                             (None, "turn 1 reported no cost", "no_result"))
            import io, contextlib
            with contextlib.redirect_stdout(io.StringIO()) as buf:
                evals.report([g_ok, g_bad], Path(tmp))
            text = buf.getvalue()
            self.assertIn("cost unknown: 1 case(s)", text)
            self.assertIn("$0.10 (partial)", text)                                      # a null is never $0

    def test_the_partial_stream_keeps_one_message_per_id(self):
        text = (COSTS / "redman_no_edit-fable-2_1_280" / "turn3.jsonl").read_text(encoding="utf-8")
        records = [json.loads(l) for l in text.splitlines() if '"assistant"' in l]
        partial = "\n".join(l for l in text.splitlines() if '"result"' not in l)
        messages = evals.parse_stream(partial)["messages"]
        self.assertEqual(len(messages), 3)
        self.assertEqual(len(records), 6)                                                # one per record: counted twice
        for m in messages:
            last = [r["message"] for r in records if r["message"]["id"] == m["id"]][-1]
            self.assertEqual((m["model"], m["usage"]), (last["model"], last["usage"]))


class Counts(unittest.TestCase):
    def test_counts_match_the_scenarios(self):
        import io, contextlib
        with contextlib.redirect_stdout(io.StringIO()) as out:
            self.assertEqual(evals.main(["--counts"]), 0)
        counts = json.loads(out.getvalue())
        S = SPEC["scenarios"]
        used = set()

        def walk(c):   # a combinator's own checks, at any depth: any_of's every item, after_turn's third element
            for sub in (c[1:] if c[0] == "any_of" else [c[2]] if c[0] == "after_turn" else []):
                walk(sub)
            if c[0] not in evals.COMBINATORS:
                used.add(c[0])
        for s in S:
            for c in s["checks"]:
                walk(c)
        self.assertEqual(counts, {
            "scenarios": len(S), "graded sessions": sum(len(s["seats"]) for s in S),
            "checks": sum(len(s["checks"]) for s in S), "turns": sum(len(s["turns"]) * len(s["seats"]) for s in S),
            "check-runs": sum(len(s["checks"]) * len(s["seats"]) for s in S), "quick sessions": sum(len(s["quick"]) for s in S),
            "known runs": sum(len(s.get("known") or []) for s in S), "check kinds": len(evals.CHECKS),
            "check kinds used": len(used), "combinators": len(evals.COMBINATORS), "automatic kinds": len(evals.AUTOMATIC)})
        self.assertEqual(counts["graded sessions"], len(evals.plan(SPEC, False, None, None)))   # what a full run grades
        self.assertEqual(counts["quick sessions"], len(evals.plan(SPEC, True, None, None)))
        # every kind is used by a scenario or added automatically, never both; the combinators are not kinds
        self.assertEqual(used | set(evals.AUTOMATIC), set(evals.CHECKS))
        self.assertFalse(used & set(evals.AUTOMATIC) or used & set(evals.COMBINATORS))
        self.assertEqual(counts["check kinds used"] + counts["automatic kinds"], counts["check kinds"])
        # known-bad: one scenario fewer is not the same figures; a kind only a combinator reaches is still counted
        self.assertNotEqual(evals.counts(dict(SPEC, scenarios=S[1:])), counts)
        nested = {"id": "n", "seats": ["fable"], "turns": ["p"], "checks": [["after_turn", 1, ["any_of", ["holdout", "p"]]]]}
        self.assertEqual(evals.counts({"scenarios": [nested]})["check kinds used"], 1)


class Recheck(unittest.TestCase):
    def test_recheck_never_overwrites_the_record(self):
        import io, contextlib
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp) / "20260923-120000"
            case = make_case(run_dir / "x", [{"prompt": "p"}])
            case.rename(run_dir / "refusal-fable")
            rec = json.loads((run_dir / "refusal-fable" / "record.json").read_text())
            rec["scenario"] = "refusal"
            (run_dir / "refusal-fable" / "record.json").write_text(json.dumps(rec))
            (run_dir / "x").rmdir()
            recorded = b'[{"scenario": "refusal", "seat": "fable", "passed": true, "cost": 3.06, "results": []}]\n'
            (run_dir / "grades.json").write_bytes(recorded)
            (run_dir / "report.txt").write_bytes(b"PASS refusal\n")
            with contextlib.redirect_stdout(io.StringIO()):
                evals.main(["--recheck", str(run_dir)])
            self.assertEqual((run_dir / "grades.json").read_bytes(), recorded)            # the record is never rewritten
            self.assertEqual((run_dir / "report.txt").read_bytes(), b"PASS refusal\n")
            written = sorted(p.name for p in run_dir.glob("regrade-*"))
            self.assertEqual([n.rsplit(".", 1)[1] for n in written], ["json", "txt"])
            regraded = json.loads((run_dir / written[0]).read_text())
            self.assertEqual([g["scenario"] for g in regraded], ["refusal"])
            out = Path(tmp) / "elsewhere"
            with contextlib.redirect_stdout(io.StringIO()):
                evals.main(["--recheck", str(run_dir), "--out", str(out)])
            self.assertEqual(sorted(p.suffix for p in out.glob("regrade-*")), [".json", ".txt"])
            self.assertEqual((run_dir / "grades.json").read_bytes(), recorded)


class RegradeAll(unittest.TestCase):
    """--regrade-all: every recorded run graded again with the fixed instruments, against the scenarios of its own day;
    flips.md, costs.md and synced.md for R-29 (§5.9; test 27a). Only temp fixtures here: the real regrade is step 11's."""
    SYNCED = json.loads((SHAPES / "init_synced_2_1_280.json").read_text(encoding="utf-8"))
    SCENARIOS = {"prompts": {}, "scenarios": [{"id": "s", "turns": ["p"], "seats": ["fable"], "quick": [], "checks": []},
                                              {"id": "s3", "turns": ["p", "q", "r"], "seats": ["fable"], "quick": [],
                                               "checks": []}]}

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name)
        self.repo, self.root, self.out = self.tmp / "repo", self.tmp / "runs", self.tmp / "regrade"
        (self.repo / "evals").mkdir(parents=True)
        subprocess.run(["git", "-C", str(self.repo), "init", "-q", "-b", "main"], check=True)
        self.commit(self.stamp("20260914-214911"), self.SCENARIOS)                     # the file first added at 21:49
        self.commit(self.stamp("20260914-230000"), dict(self.SCENARIOS, _about="later"))

    def stamp(self, name):
        return time.mktime(time.strptime(name, "%Y%m%d-%H%M%S"))                         # a run's stamp: local time

    def commit(self, when, spec):
        (self.repo / "evals" / "scenarios.json").write_text(json.dumps(spec), encoding="utf-8")
        subprocess.run(["git", "-C", str(self.repo), "add", "-A"], check=True)
        env = dict(os.environ, GIT_AUTHOR_DATE=f"@{int(when)} +0000", GIT_COMMITTER_DATE=f"@{int(when)} +0000")
        subprocess.run(["git", "-C", str(self.repo), "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm",
                        str(when)], check=True, env=env)

    def run_dir(self, name, cases):
        """A recorded run: cases as (case name, scenario, [(version, total)], init of turn 1, recorded cost, passed)."""
        d = self.root / name
        d.mkdir(parents=True)
        grades = []
        for case, scenario, turns, init, cost, passed in cases:
            c = d / case
            c.mkdir()
            rec_turns = []
            for n, (version, total) in enumerate(turns, 1):
                lines = [dict(init if n == 1 and init else {"type": "system", "subtype": "init"}, claude_code_version=version),
                         {"type": "result", "result": "done", "total_cost_usd": total}]
                text = "\n".join(json.dumps(l) for l in lines) + "\n"
                (c / f"turn{n}.jsonl").write_text(text, encoding="utf-8")
                rec_turns.append(evals.parse_stream(text))
            (c / "record.json").write_text(json.dumps({"scenario": scenario, "seat": "fable", "turns": rec_turns,
                                                       "snapshots": [{}] * (len(turns) + 1)}), encoding="utf-8")
            grades.append({"scenario": scenario, "seat": "fable", "passed": passed, "cost": cost, "results": []})
        (d / "grades.json").write_text(json.dumps(grades), encoding="utf-8")
        return d

    def regrade(self):
        import io, contextlib
        with contextlib.redirect_stdout(io.StringIO()) as buf:
            code = evals.regrade_all(self.root, self.out, repo=self.repo)
        return code, buf.getvalue()

    def test_regrade_all_commit_rule_costs_and_synced(self):
        v280 = [("2.1.280", 0.5872112500000001), ("2.1.280", 1.1672625), ("2.1.280", 1.3013065000000001)]
        self.run_dir("20260914-214600", [("a-fable", "s", [("2.1.272", 0.5)], None, 0.5, True)])     # before any commit
        self.run_dir("20260914-215000", [("a-fable", "s", [("2.1.272", 0.5)], None, 0.5, True)])
        self.run_dir("nested/20260922-224650", [("b-fable", "s3", v280, None, 3.06, True),
                                                ("c-fable", "s", [("2.1.280", 0.40)], None, 0.40, True)])
        self.run_dir("20260915-100000", [("d-fable", "s", [("2.1.272", 0.2)], self.SYNCED, 0.2, True)])
        connector = {"type": "system", "subtype": "init", "plugins": [], "skills": [],
                     "mcp_servers": [{"name": "claude.ai connector-1", "status": "connected", "source": "claudeai"}]}
        self.run_dir("20260915-110000", [("e-fable", "s", [("2.1.273", 0.2)], connector, 0.2, True)])
        plugins_only = dict(self.SYNCED, mcp_servers=[])
        self.run_dir("20260915-120000", [("f-fable", "s", [("2.1.273", 0.2)], plugins_only, 0.2, True),
                                         ("g-fable", "s", [("2.1.273", 0.2)], connector, 0.2, True)])
        code, _ = self.regrade()
        self.assertEqual(code, 0)
        flips = (self.out / "flips.md").read_text(encoding="utf-8")
        first = subprocess.run(["git", "-C", str(self.repo), "log", "--diff-filter=A", "--format=%h", "--",
                                "evals/scenarios.json"], capture_output=True, text=True).stdout.split()[-1]
        self.assertIn(f"20260914-214600: graded against {first}, the first commit that added evals/scenarios.json "
                      f"(no commit at or before its stamp)", flips)
        self.assertIn(f"20260914-215000: graded against {first}", flips)
        self.assertNotIn("20260914-215000: graded against " + first + ", the first commit", flips)
        # known-bad: `git log --before=<stamp> -1` alone finds no commit for the 21:46 run
        before = subprocess.run(["git", "-C", str(self.repo), "log", "-1", "--format=%h",
                                 f"--before=@{int(self.stamp('20260914-214600'))}"], capture_output=True, text=True).stdout
        self.assertEqual(before.strip(), "")
        self.assertEqual((self.out / "costs.md").read_text(encoding="utf-8").strip().splitlines(),
                         ["nested/20260922-224650 $3.46 → $1.70"])                # changed runs only
        synced = (self.out / "synced.md").read_text(encoding="utf-8").strip().splitlines()
        self.assertEqual(synced, ["20260915-100000 — 1 case(s)", "20260915-110000 — 1 case(s)", "20260915-120000 — 2 case(s)"])
        counts = json.loads((self.out / "regrade.json").read_text(encoding="utf-8"))["synced_counts"]
        self.assertEqual(counts, {"plugin": {"runs": 2, "cases": 2}, "skill": {"runs": 2, "cases": 2},
                                  "connector": {"runs": 3, "cases": 3}, "any": {"runs": 3, "cases": 4}})
        # known-bad: G-27's rule, plugins and skills only, leaves the connector-only run out
        g27 = sorted({p.parent.parent.name for p in self.root.rglob("turn1.jsonl")
                      for init in [evals.parse_stream(p.read_text())["init"]] if any(evals.account_entries(init)[:2])})
        self.assertEqual(g27, ["20260915-100000", "20260915-120000"])
        self.assertNotEqual(len(g27), len(synced))

    def test_sealed_is_regraded_against_the_review_the_case_ran_under(self):
        """Ledger row 2026-09-28 (regrade CLI): --regrade-all graded `sealed` against today's reviewed CLI, so E0 and Q1
        showed 33 false flips. Each case is graded with the reviewed fields of evals/sealed.json at the pack head its
        record names (env.pack_head): neither a commit's date nor the machine's time zone decides which review."""
        def review(**fields):
            (self.repo / "evals" / "sealed.json").write_text(json.dumps(dict({"skills": ["eli5"]}, **fields)),
                                                              encoding="utf-8")
            subprocess.run(["git", "-C", str(self.repo), "add", "-A"], check=True)
            subprocess.run(["git", "-C", str(self.repo), "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm",
                            "review"], check=True)
            return subprocess.run(["git", "-C", str(self.repo), "rev-parse", "HEAD"], capture_output=True,
                                  text=True).stdout.strip()
        then = review(cli_version="2.1.272", bundled_skills=["a"], builtin_plugins=["b@builtin"])
        review(cli_version="2.1.283", bundled_skills=["a"], builtin_plugins=["b@builtin"])
        day = {"bundled_skills": ["a"], "builtin_plugins": ["b@builtin"], "cli_version": "2.1.272"}
        self.assertEqual(evals.reviewed_at(self.repo, then), day)
        self.assertEqual(evals.reviewed_at(self.repo, None), {})                 # no head recorded: no override
        self.assertEqual(evals.reviewed_at(self.repo, "0" * 40), {})             # a head this repository lacks
        d = self.run_dir("20260915-100000", [("a-fable", "s", [("2.1.272", 0.2)], None, 0.2, True)])
        rec = json.loads((d / "a-fable" / "record.json").read_text(encoding="utf-8"))
        (d / "a-fable" / "record.json").write_text(json.dumps(dict(rec, env={"pack_head": then})), encoding="utf-8")
        seen, real = [], evals.grade
        with mock.patch.object(evals, "grade", lambda case, scenario, reviewed=None: seen.append(reviewed) or
                               real(case, scenario, reviewed=reviewed)):
            code, _ = self.regrade()
        self.assertEqual((code, seen), (0, [day]))

    def test_a_falling_total_stops_the_hand_over(self):
        self.run_dir("20260922-224650", [("b-fable", "s3", [("2.1.280", 0.6), ("2.1.280", 0.5), ("2.1.280", 0.7)],
                                          None, 1.8, True)])
        code, text = self.regrade()
        self.assertEqual(code, 1)
        self.assertIn("20260922-224650/b-fable: turn 2 total $0.50 below turn 1 $0.60", text)
        self.assertFalse((self.out / "costs.md").exists())

    def test_a_run_made_on_an_uncommitted_scenario_is_graded_not_dropped(self):
        # the 20260922-224650 shape: its scenario landed in a commit two minutes after the run's stamp
        later = dict(self.SCENARIOS, scenarios=self.SCENARIOS["scenarios"] + [
            {"id": "late", "turns": ["p", "q", "r"], "seats": ["fable"], "quick": [], "checks": []}])
        self.commit(self.stamp("20260922-224854"), later)
        v280 = [("2.1.280", 0.5872112500000001), ("2.1.280", 1.1672625), ("2.1.280", 1.3013065000000001)]
        self.run_dir("20260922-224650", [("late-fable", "late", v280, self.SYNCED, 3.06, True)])
        code, _ = self.regrade()
        self.assertEqual(code, 0)
        head = subprocess.run(["git", "-C", str(self.repo), "log", "-1", "--format=%h"], capture_output=True,
                              text=True).stdout.strip()
        self.assertIn(f"20260922-224650: late was not committed at its stamp; graded against {head}, the first commit "
                      f"that has it", (self.out / "flips.md").read_text(encoding="utf-8"))
        self.assertEqual((self.out / "costs.md").read_text(encoding="utf-8").strip(), "20260922-224650 $3.06 → $1.30")
        self.assertEqual((self.out / "synced.md").read_text(encoding="utf-8").strip(), "20260922-224650 — 1 case(s)")
        # a scenario no commit has is never dropped silently: listed, and the hand-over stops
        self.run_dir("20260922-230000", [("ghost-fable", "ghost", [("2.1.280", 0.1)], None, 0.1, True)])
        code, text = self.regrade()
        self.assertEqual(code, 1)
        self.assertIn("not regraded: 20260922-230000/ghost-fable — scenario ghost is in no commit", text)
        self.assertEqual(json.loads((self.out / "regrade.json").read_text())["not_regraded"],
                         [{"run": "20260922-230000", "case": "ghost-fable", "scenario": "ghost"}])

    def test_a_check_a_failed_record_never_ran_is_not_a_flip(self):
        fable = "# Working notes (injected: this session runs Claude Fable 5.1)\n\nbody\n"
        d = self.run_dir("20260915-140000", [("j-fable", "s", [("2.1.272", 0.1)], None, 0.1, False)])
        f = d / "j-fable" / "turn1.jsonl"
        f.write_text(json.dumps({"type": "system", "subtype": "hook_response", "hook_event": "SessionStart",
                                 "hook_name": "SessionStart:startup", "output": fable, "stdout": fable}) + "\n"
                     + f.read_text(encoding="utf-8"), encoding="utf-8")
        rec = json.loads((d / "j-fable" / "record.json").read_text())
        rec["turns"] = [evals.parse_stream(f.read_text(encoding="utf-8"))]
        (d / "j-fable" / "record.json").write_text(json.dumps(rec))
        self.regrade()
        # recorded FAIL, layer passes now: the old grader never ran layer, so no verdict of its changed
        self.assertEqual(json.loads((self.out / "regrade.json").read_text())["flips"], [])

    def test_a_check_the_run_was_not_graded_with_is_not_a_flip(self):
        """A run graded with scenarios its working tree held and no commit at its stamp did (final-v3 on 14 September:
        its record grades `knowledge/decisions/…`, the commit before it still said `xbt-skills-pack/greenman/…`) is
        regraded on the checks its record lists, modernised; a check of another version is never a flip (R-29, P0's
        66 flips of that kind). Known-bad: grading the commit's version reports the flip."""
        spec = {"prompts": {}, "scenarios": [{"id": "s", "turns": ["p"], "seats": ["fable"], "quick": [],
                                              "checks": [["final_matches", 1, "shipped"]]}]}
        self.commit(self.stamp("20260914-231000"), spec)
        d = self.run_dir("20260914-233157", [("s-fable", "s", [("2.1.272", 0.1)], None, 0.1, True)])
        grades = json.loads((d / "grades.json").read_text())
        grades[0]["results"] = [{"check": ["final_matches", 1, "done"], "passed": True, "detail": "matched"}]
        (d / "grades.json").write_text(json.dumps(grades))
        self.regrade()
        behaviour = lambda: [f for f in json.loads((self.out / "regrade.json").read_text(encoding="utf-8"))["flips"]
                             if f["check"] != "layer"]                  # the fixture has no loader output: layer flips
        self.assertEqual(behaviour(), [])
        self.assertIn("20260914-233157: graded on the checks its record lists", (self.out / "flips.md").read_text())
        # the known-bad: the same case graded against the commit's version flips
        g = evals.grade(d / "s-fable", spec["scenarios"][0])
        self.assertEqual([r["passed"] for r in g["results"] if not r.get("automatic")], [False])
        # a recorded check R-22 replaced is regraded in its modern form, and compared with its recorded verdict
        grades[0]["results"] = [{"check": ["transcript_contains", "doc-guardrails (end of turn)"], "passed": True,
                                 "detail": "found"}]
        (d / "grades.json").write_text(json.dumps(grades))
        self.regrade()
        (flip,) = behaviour()
        self.assertEqual((flip["check_args"], flip["before"], flip["after"]),
                         (["hook_fired", "Stop", "doc-guardrails (end of turn)"], True, False))

    def test_the_layer_flips_are_listed_first_every_case_named(self):
        fable = "# Working notes (injected: this session runs Claude Fable 5.1)\n\nbody\n"
        opus = fable.replace("Claude Fable 5.1", "Claude Opus 5")
        d = self.run_dir("20260915-130000", [("h-fable", "s", [("2.1.272", 0.1)], None, 0.1, True),
                                             ("i-fable", "s", [("2.1.272", 0.1)], None, 0.1, True)])
        for case, layer in (("h-fable", opus), ("i-fable", fable)):
            f = d / case / "turn1.jsonl"
            f.write_text(json.dumps({"type": "system", "subtype": "hook_response", "hook_event": "SessionStart",
                                     "hook_name": "SessionStart:startup", "output": layer, "stdout": layer}) + "\n"
                         + f.read_text(encoding="utf-8"), encoding="utf-8")
            rec = json.loads((d / case / "record.json").read_text())
            rec["turns"] = [evals.parse_stream(f.read_text(encoding="utf-8"))]
            (d / case / "record.json").write_text(json.dumps(rec))
        self.regrade()
        flips = (self.out / "flips.md").read_text(encoding="utf-8")
        self.assertTrue(flips.split("\n## ")[1].startswith("layer"), flips)
        self.assertIn("20260915-130000/h-fable", flips)
        self.assertNotIn("i-fable", flips)
        entries = json.loads((self.out / "regrade.json").read_text(encoding="utf-8"))["flips"]
        self.assertEqual([(e["case"], e["check"]) for e in entries], [("h-fable", "layer")])


class RealFailures(unittest.TestCase):
    """The regression corpus: real sessions that failed for a behaviour reason still fail on their named checks
    (criterion 14; test 28)."""

    def test_harvested_real_failures_still_fail(self):
        index = json.loads((REAL / "INDEX.json").read_text(encoding="utf-8"))
        self.assertTrue(index["entries"])
        kept = [e for e in index["entries"] if e["kept"]]
        self.assertTrue(kept)
        for e in index["entries"]:
            self.assertTrue(e["source"], e)
            self.assertTrue(e.get("fails") if e["kept"] else str(e.get("reason") or "").strip(), e)
        self.assertEqual(sorted(p.stem for p in REAL.glob("*.json") if p.name != "INDEX.json"), sorted(e["id"] for e in kept))
        by_id = {s["id"]: s for s in SPEC["scenarios"]}
        with tempfile.TemporaryDirectory() as tmp:
            for e in kept:
                entry = json.loads((REAL / f"{e['id']}.json").read_text(encoding="utf-8"))
                case = evals.rebuild_real(entry, Path(tmp) / e["id"])
                g = evals.grade(case, by_id[entry["scenario"]])
                failed = {k for r in g["results"] if r["passed"] is False for k in kinds_used(r["check"])}
                self.assertFalse(g["passed"], e["id"])
                for kind in entry["fails"]:
                    self.assertIn(kind, failed, (e["id"], kind))

    def test_a_harvest_is_compact_and_case_relative(self):
        with tempfile.TemporaryDirectory() as tmp:
            case = make_case(Path(tmp) / "r", [{"prompt": "p", "final": f"wrote {tmp}/r/case/project/x.md",
                                                "stream": bash_calls(("cat /opt/someone/secret.txt", "ok"))}])
            proj = evals.build_project(case, {"id": "t"})
            base = evals.snapshot(proj)
            (proj / "x.md").write_text("new\n", encoding="utf-8")
            (proj / "README.md").unlink()
            rec = json.loads((case / "record.json").read_text())
            rec.update(scenario="refusal", snapshots=[base, evals.snapshot(proj)])
            (case / "record.json").write_text(json.dumps(rec))
            entry = evals.harvest_case(case, "t-1", Path(tmp) / "real")
        self.assertEqual(set(entry), {"id", "source", "scenario", "seat", "turns", "snapshots", "files", "baseline_tests", "fails"})
        text = json.dumps(entry)
        self.assertNotIn(tmp, text)
        self.assertIn("<case>/project/x.md", text)
        self.assertEqual(entry["files"]["x.md"], "new\n")
        self.assertIsNone(entry["files"]["README.md"])                                   # deleted since the build
        self.assertNotIn("session_id", json.dumps(entry["turns"]))


class WindowsShapes(unittest.TestCase):
    """The Windows shapes the CI's Windows legs met (2026-10-14), simulated with ntpath so every platform runs them."""

    @staticmethod
    def ntpath_with(links):
        """ntpath, with a realpath that follows `links` ({link: target}) as Windows' realpath would."""
        import ntpath
        import types

        def realpath(p):
            p = ntpath.normpath(p)
            for link, target in links.items():
                if p.lower() == link.lower() or p.lower().startswith(link.lower() + "\\"):
                    return target + p[len(link):]
            return p
        return types.SimpleNamespace(**{n: getattr(ntpath, n) for n in (
            "splitdrive", "normcase", "normpath", "dirname", "basename", "join", "sep", "altsep")}, realpath=realpath)

    def test_a_link_into_another_drive_is_contained_where_it_lands(self):
        # the sealed folder's skills\eli5 on C: is a link into the pack's checkout on D: (the CI's own layout)
        nt = self.ntpath_with({r"C:\Temp\sealed\skills\eli5": r"D:\a\pack\skills\eli5"})
        self.assertEqual(evals._contained(r"C:\Temp\sealed\skills\eli5\skill_fresh.py", r"D:\a\pack", pathmod=nt),
                         "skills/eli5/skill_fresh.py")
        self.assertIsNone(evals._contained(r"C:\Temp\sealed\skills\other\x.py", r"D:\a\pack", pathmod=nt))
        # known-bad: the drives of the paths as written differ, which refused it before any link was read
        self.assertNotEqual(nt.splitdrive(r"C:\Temp\sealed\skills\eli5\skill_fresh.py")[0], nt.splitdrive(r"D:\a\pack")[0])

    def test_a_root_through_a_link_to_a_missing_folder_keeps_its_drive(self):
        # Windows' realpath keeps \\?\ on a path through a link whose target does not exist yet (<config>\doctrine
        # on a machine with no ~/.claude/doctrine): the root and the script must still compare on one drive
        nt = self.ntpath_with({r"C:\T\sealed\doctrine": "\\\\?\\C:\\Users\\me\\.claude\\doctrine"})
        root = evals._realpath(r"C:\T\sealed\doctrine", nt)
        self.assertEqual(root, r"C:\Users\me\.claude\doctrine")
        self.assertEqual(evals._contained(r"C:\T\sealed\doctrine\gate.py", r"C:\T\sealed\doctrine", pathmod=nt),
                         "gate.py")
        self.assertEqual(evals._plain("\\\\?\\UNC\\srv\\share\\x", nt), r"\\srv\share\x")
        import posixpath
        self.assertEqual(evals._plain("/a/b", posixpath), "/a/b")
        # known-bad: with the prefix kept, the root's drive is \\?\C:, never the script's C:
        self.assertNotEqual(nt.splitdrive("\\\\?\\C:\\Users\\me\\.claude\\doctrine")[0], "C:")

    def test_a_root_through_a_link_to_nowhere_is_compared_as_written(self):
        # on the CI's Windows legs realpath answered a path through a link to a folder not there yet, and that link,
        # inconsistently (simulated here by an 8.3 short name expanded in one only); with no folder to identify, the
        # paths as written decide: <config>\doctrine\gate.py is under <config>\doctrine
        import ntpath
        import types
        short, long_ = r"C:\Users\RUNNER~1\T\sealed", r"C:\Users\runneradmin\T\sealed"

        def realpath(p):
            p = ntpath.normpath(p)
            return p.replace(short, long_) if p.lower().endswith("gate.py") else p
        nt = types.SimpleNamespace(**{n: getattr(ntpath, n) for n in (
            "splitdrive", "normcase", "normpath", "dirname", "basename", "join", "sep", "altsep")}, realpath=realpath)
        script, root = short + r"\doctrine\gate.py", short + r"\doctrine"
        self.assertEqual(evals._contained(script, root, pathmod=nt), "gate.py")
        self.assertIsNone(evals._contained(short + r"\doctrine-old\gate.py", root, pathmod=nt))
        # known-bad: the real paths alone, which disagree
        self.assertIsNone(evals._walk_up(realpath(script), realpath(root), False, nt))

    def test_the_doctrine_root_of_a_link_to_nowhere_is_the_link(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg = Path(tmp) / "sealed"
            cfg.mkdir()
            self.assertEqual(evals._doctrine_root(cfg), os.path.abspath(cfg / "doctrine"))        # no folder at all
            try:
                (cfg / "doctrine").symlink_to(Path(tmp) / "home" / ".claude" / "doctrine", target_is_directory=True)
            except (OSError, NotImplementedError) as e:
                self.skipTest(f"symlinks are refused here ({e})")
            self.assertEqual(evals._doctrine_root(cfg), os.path.abspath(cfg / "doctrine"))        # a link to nowhere
            self.assertEqual(evals._contained(str(cfg / "doctrine" / "gate.py"), evals._doctrine_root(cfg)), "gate.py")
            (Path(tmp) / "home" / ".claude" / "doctrine").mkdir(parents=True)
            self.assertEqual(evals._doctrine_root(cfg), os.path.realpath(Path(tmp) / "home" / ".claude" / "doctrine"))

    def test_recorded_windows_paths_meet_the_slash_suffixes(self):
        # the SessionStart entry allowed on resume is told from the loader by the suffix rule, on a Windows path too:
        # with "/" alone, both stayed starts, no loader was found and every known run failed to build
        entries = [("SessionStart", "", r"C:\cfg\doctrine\gate.py", "gate.py"),
                   ("SessionStart", "resume", r"D:\a\pack\skills\eli5\skill_fresh.py", "skill_fresh.py"),
                   ("Stop", "", r"D:\a\pack\skills\doc-guardrails\turn_check.py", "turn_check.py")]
        _, (loader, starts) = evals.case_hooks({"record": {"hooks": [list(e) for e in entries]}})
        self.assertEqual((loader, starts), (entries[0], [entries[0]]))
        self.assertTrue(evals.ends_with_script(r"'C:\cfg\skills\doc-guardrails\hook.sh'", "doc-guardrails/hook.sh"))
        self.assertFalse(evals.ends_with_script(r"C:\cfg\skills\doc-guardrails\hook.sh --x", "doc-guardrails/hook.sh"))
        self.assertEqual(evals._slashed(r"C:\proj\shelf\report.py"), "C:/proj/shelf/report.py")

    def test_a_glob_of_the_project_never_reaches_into_git(self):
        # git's objects are read-only, and Windows refuses to unlink them: a violation removing "**/*" (the files a
        # check sees, as the snapshot does) stopped there; the checks never read .git either
        with tempfile.TemporaryDirectory() as tmp:
            proj = Path(tmp)
            (proj / ".git" / "objects").mkdir(parents=True)
            (proj / ".git" / "objects" / "ab").write_text("x", encoding="utf-8")
            (proj / "__pycache__").mkdir()
            (proj / "__pycache__" / "m.pyc").write_text("x", encoding="utf-8")
            (proj / "a.md").write_text("x", encoding="utf-8")
            self.assertEqual(evals._files(proj, "**/*"), [proj / "a.md"])
            self.assertEqual(sorted(evals.snapshot(proj)), ["a.md"])

    def test_a_quoted_runner_is_still_the_runner(self):
        py, runner = r"C:\Python\python.exe", r"D:\a\pack\evals\hook_log.py"
        script = r"C:\Temp\sealed\skills\doc-guardrails\hook.sh"
        hook = {"type": "command", "command": f"{shlex.quote(py)} {shlex.quote(runner)} --event PostToolUse --script "
                                              f"{shlex.quote(script)} -- {shlex.quote(script)}"}
        self.assertTrue(evals.is_runner_form(hook))
        self.assertTrue(evals.is_logged(hook))
        self.assertEqual(evals.settings_script(hook), script)
        self.assertFalse(evals.is_runner_form({"command": f"{shlex.quote(py)} {shlex.quote(script)} -- x"}))
        self.assertFalse(evals.is_runner_form({"command": "python3 /p/hook_log.py.bak --event Stop -- x"}))
        # known-bad: the substring test, which a closing quote after the runner's path defeats
        self.assertNotIn("hook_log.py --event ", hook["command"])

    def test_a_case_path_in_json_is_found_whatever_its_separators(self):
        case = r"C:\Users\RUNNER~1\Temp\tmpab\r\case"
        text = json.dumps({"final": case.replace("\\tmpab\\", "\\tmpab/").replace("\\case", "/case") + "/project/x.md",
                           "out": case + "\\project\\y.md"})
        got = json.loads(evals.json_path_re({case}).sub("<case>", text))
        self.assertEqual(got, {"final": "<case>/project/x.md", "out": "<case>\\project\\y.md"})
        self.assertNotIn(case, text)                       # known-bad: the raw path is never in the JSON text
        self.assertEqual(evals.json_path_re({"/home/u/r/case"}).sub("<case>", '"/home/u/r/case/x"'), '"<case>/x"')
        self.assertEqual(evals.json_path_re(set()).sub("<case>", '"a"'), '"a"')

    def test_a_permission_rule_names_a_windows_folder_in_posix_form(self):
        import ntpath
        import posixpath
        self.assertEqual(evals.rule_path(r"C:\Data\me\.claude-evals\skills", ntpath), "//c/Data/me/.claude-evals/skills")
        self.assertEqual(evals.rule_path("/home/me/.claude-evals/skills", posixpath), "//home/me/.claude-evals/skills")


class Permissions(unittest.TestCase):
    def test_one_deny_list_keeps_git_push_and_makes_the_skill_folders_read_only(self):
        flags = evals.permission_flags(["Read", "Bash(ls:*)"], ["/a/skills", "/pack"])
        self.assertEqual(flags.count("--disallowedTools"), 1)  # a second flag could drop the push deny
        deny = flags[flags.index("--disallowedTools") + 1:flags.index("--add-dir")]
        self.assertEqual(deny, ["Bash(git push:*)", "Write(//a/skills/**)", "Edit(//a/skills/**)",
                                "Write(//pack/**)", "Edit(//pack/**)"])
        self.assertEqual(flags[flags.index("--add-dir") + 1:], ["/a/skills", "/pack"])
        self.assertEqual(evals.permission_flags(["Read"], []), ["--allowedTools", "Read", "--disallowedTools",
                                                                "Bash(git push:*)"])

    def test_session_dirs_are_claude_codes_folder_for_the_project_in_the_sealed_config(self):
        with tempfile.TemporaryDirectory() as tmp:
            config, proj = Path(tmp) / "sealed", Path(tmp) / "my_proj"
            proj.mkdir()
            dirs = evals.session_dirs(proj, config)
            # Claude Code's projects/ name: the working directory with every non-alphanumeric character as "-", so
            # /home/me/my_proj is -home-me-my-proj and C:\\Data\\my_proj is C--Data-my-proj
            name = str(proj.resolve())
            for ch in "/\\:_.":
                name = name.replace(ch, "-")
            self.assertEqual([Path(d) for d in dirs], [config / "projects" / name])
            self.assertTrue(all(Path(d).is_dir() for d in dirs))

    def test_skill_dirs_are_the_sealed_skills_and_the_folders_their_links_point_into(self):
        with tempfile.TemporaryDirectory() as tmp:
            config, pack = Path(tmp) / "sealed", Path(tmp) / "pack"
            (pack / "greenman").mkdir(parents=True)
            (config / "skills").mkdir(parents=True)
            (config / "skills" / "greenman").symlink_to(pack / "greenman")
            dirs = evals.skill_dirs(config)
        self.assertIn(str((config / "skills").resolve()), dirs)
        self.assertIn(str(pack.resolve()), dirs)


class Isolation(unittest.TestCase):
    """A sealed case writes nothing under the live config, and its preps run with its own state (tests 23b, 23c)."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name)
        self.home = self.tmp / "home"
        (self.home / ".claude" / "skills").mkdir(parents=True)
        (self.home / ".claude" / "projects").mkdir()
        (self.home / ".claude" / "skills" / "redman").symlink_to(evals.pack_path("redman"))
        p = mock.patch.object(Path, "home", lambda: self.home)
        p.start()
        self.addCleanup(p.stop)
        self.config = self.tmp / "sealed"
        evals.build_sealed(self.config)

    def listing(self):
        return sorted(str(p.relative_to(self.home)) for p in (self.home / ".claude").rglob("*"))

    def fake_claude(self, seen):
        real = subprocess.run

        def run(cmd, *a, **kw):
            if cmd and str(cmd[0]).endswith("claude-bin"):
                seen.append((list(cmd), kw.get("env")))
                if cmd[1:] == ["--version"]:
                    return subprocess.CompletedProcess(cmd, 0, "2.1.281 (Claude Code)\n", "")
                lines = [{"type": "system", "subtype": "init", "session_id": "s1", "claude_code_version": "2.1.281"},
                         {"type": "result", "result": "done", "total_cost_usd": 0.1, "session_id": "s1"}]
                return subprocess.CompletedProcess(cmd, 0, "\n".join(json.dumps(l) for l in lines), "")
            return real(cmd, *a, **kw)
        return run

    def allowed(self, path, case):
        """A path a sealed case may name: under the sealed folder, the case folder or the pack a sealed link resolves into."""
        p = os.path.realpath(path)
        roots = [self.config, case] + [Path(os.path.realpath(l)).parent for l in (self.config / "skills").iterdir()]
        return any(p == os.path.realpath(r) or p.startswith(os.path.realpath(r) + os.sep) for r in roots)

    def test_a_sealed_case_writes_nothing_under_the_live_config(self):
        before, seen = self.listing(), []
        scenario = {"id": "t", "turns": ["SMALL"], "seats": ["fable"], "checks": []}
        case = self.tmp / "out" / "t-fable"
        (self.tmp / "claude-bin").write_text("#!/bin/sh\n", encoding="utf-8")   # the resolved file exists; runs are faked
        with mock.patch.object(evals.subprocess, "run", self.fake_claude(seen)):
            evals.run_case(case, scenario, "fable", SPEC["prompts"], self.config, str(self.tmp / "claude-bin"), "2.1.281")
        self.assertEqual(self.listing(), before)
        launches = [(c, e) for c, e in seen if "-p" in c]
        self.assertEqual(len(launches), 1)
        cmd, env = launches[0]
        def values(flag):   # every value after each occurrence of the flag, up to the next flag
            out = []
            for i, a in enumerate(cmd):
                if a == flag:
                    out += list(itertools.takewhile(lambda x: not x.startswith("--"), cmd[i + 1:]))
            return out
        add_dirs, deny = values("--add-dir"), values("--disallowedTools")
        self.assertTrue(add_dirs)
        for d in add_dirs:
            self.assertTrue(self.allowed(d, case), d)
        for pattern in deny[1:]:                                           # after `Bash(git push:*)`
            ruled = re.sub(r"^\w+\(/|/\*\*\)$", "", pattern)               # the rule's path: /x/y; /c/x/y on Windows
            if os.name == "nt":
                ruled = re.sub(r"^/([a-z])/", lambda m: m.group(1).upper() + ":\\", ruled)
            self.assertTrue(self.allowed(ruled, case), pattern)
        self.assertEqual(env["CLAUDE_CONFIG_DIR"], str(self.config))
        self.assertEqual(env["DOC_GUARDRAILS_STATE"], str(case / "state"))
        self.assertIn("--strict-mcp-config", cmd)
        self.assertIn("--include-hook-events", cmd)
        # known-bads: the old shapes named the live config — its skills folder, and a projects folder made there
        self.assertFalse(self.allowed(self.home / ".claude" / "skills", case))
        self.assertFalse(self.allowed(self.home / ".claude" / "projects" / "-x", case))
        self.assertFalse(hasattr(evals, "SKILL_DIRS"))

    def test_preps_run_with_the_cases_state(self):
        decoy = self.tmp / "live-state"
        decoy.mkdir()
        real, calls = subprocess.run, []

        def recording(cmd, *a, **kw):
            calls.append((list(map(str, cmd)), kw.get("env")))
            return real(cmd, *a, **kw)
        for sid in ("gate_block", "gate_block_own"):
            scenario = next(s for s in SPEC["scenarios"] if s["id"] == sid)
            case = self.tmp / sid
            case.mkdir()
            with mock.patch.dict(os.environ, {"DOC_GUARDRAILS_STATE": str(decoy)}), \
                    mock.patch.object(evals.subprocess, "run", recording):
                evals.build_project(case, scenario)
            gates = [e for c, e in calls if "install-gate" in c]
            self.assertTrue(gates, sid)
            for env in gates:
                self.assertIsNotNone(env, f"{sid}: install-gate ran with the process environment")
                self.assertEqual(env["DOC_GUARDRAILS_STATE"], str(case / "state"))
                self.assertEqual(env["CLOSEOUT_PY"], str(evals.pack_path("closeout/closeout.py")))
            self.assertTrue((case / "state").is_dir())
            calls.clear()
        self.assertEqual(list(decoy.iterdir()), [])                       # the decoy live state gained nothing

    # ---- G-38: a scenario's `allowed_tools` grants those tools to every `claude` call of its case, and nothing else

    def launches(self, scenario):
        """(every session launch's command, the record, the case folder) of a faked run of the scenario."""
        seen = []
        case = self.tmp / "out" / f"{scenario['id']}-fable"
        (self.tmp / "claude-bin").write_text("#!/bin/sh\n", encoding="utf-8")   # the resolved file exists; runs are faked
        with mock.patch.object(evals.subprocess, "run", self.fake_claude(seen)):
            rec = evals.run_case(case, scenario, "fable", SPEC["prompts"], self.config, str(self.tmp / "claude-bin"),
                                 "2.1.281")
        return [c for c, _ in seen if "-p" in c], rec, case

    @staticmethod
    def allowed_values(cmd):
        i = cmd.index("--allowedTools")
        return list(itertools.takewhile(lambda x: not x.startswith("--"), cmd[i + 1:]))

    def test_a_scenarios_allowed_tools_reach_every_turn_of_its_case(self):
        scenario = {"id": "g", "turns": ["SMALL", "SMALL"], "seats": ["fable"], "checks": [], "allowed_tools": ["Workflow"]}
        cmds, rec, case = self.launches(scenario)
        self.assertEqual(len(cmds), 2)
        self.assertNotIn("--resume", cmds[0])
        self.assertEqual(cmds[1][cmds[1].index("--resume") + 1], "s1")          # the resumed turn carries it too
        for cmd in cmds:
            self.assertEqual(cmd.count("--allowedTools"), 1)                    # one allow flag, as one deny flag
            self.assertEqual(self.allowed_values(cmd), evals.ALLOWED + ["Workflow"])
            self.assertNotIn("Workflow", cmd[cmd.index("--disallowedTools"):cmd.index("--add-dir")])
        self.assertEqual(rec["env"]["allowed_tools"], ["Workflow"])            # a graded run shows the grant
        self.assertEqual(json.loads((case / "record.json").read_text())["env"]["allowed_tools"], ["Workflow"])

    def test_without_allowed_tools_the_command_is_todays(self):
        plain = {"id": "p", "turns": ["SMALL", "SMALL"], "seats": ["fable"], "checks": []}
        cmds, rec, case = self.launches(plain)
        proj, scratch = case / "project", case / "scratch"
        readable = evals.skill_dirs(self.config) + evals.session_dirs(proj, self.config)
        self.assertEqual(len(cmds), 2)
        for n, cmd in enumerate(cmds):                                           # today's command, spelled out
            self.assertEqual(cmd, [str(self.tmp / "claude-bin"), "-p", SPEC["prompts"].get("SMALL", "SMALL"),
                                   *(["--resume", "s1"] if n else []), *evals.SEATS["fable"],
                                   "--permission-mode", "acceptEdits", *evals.permission_flags(evals.ALLOWED, readable),
                                   "--add-dir", str(scratch),
                                   "--append-system-prompt", f"Scratchpad directory: {scratch} — use it for temporary files "
                                   f"(a worklist, a note, intermediate output); write the path literally, without shell "
                                   f"variables.",
                                   "--include-hook-events", "--strict-mcp-config", "--output-format", "stream-json",
                                   "--verbose"])
        self.assertEqual(rec["env"]["allowed_tools"], [])
        # the same scenario with a grant differs by that one name and nothing else
        granted, _, _ = self.launches(dict(plain, id="q", allowed_tools=["Workflow"]))
        self.assertEqual(len(granted), 2)
        for before, after in zip(cmds, granted):
            after = [a.replace("/q-fable/", "/p-fable/").replace("-q-fable-", "-p-fable-") for a in after]
            after.remove("Workflow")
            self.assertEqual(after, before)

    def test_a_malformed_allowed_tools_is_refused_before_any_turn(self):
        import selftest
        for n, bad in enumerate(("Workflow", [], [""], ["  "], [3], None, ["Workflow", "Workflow"],
                                 ["--dangerously-skip-permissions"])):
            scenario = {"id": f"bad{n}", "turns": ["SMALL"], "seats": ["fable"], "checks": [], "allowed_tools": bad}
            with self.assertRaises(ValueError, msg=repr(bad)):
                self.launches(scenario)
            self.assertFalse((self.tmp / "out" / f"bad{n}-fable").exists(), repr(bad))   # nothing built, nothing launched
            self.assertEqual(selftest.schema_problems({"scenarios": [scenario]}),
                             [f"bad{n}: {evals.allowed_tools_error(bad)}"], repr(bad))
        self.assertEqual(selftest.schema_problems(SPEC), [])

    def test_only_subagent_brief_is_granted_the_workflow_tool(self):
        path = Path(__file__).resolve().parent.parent / "evals" / "scenarios.json"
        text = path.read_text(encoding="utf-8")
        self.assertEqual(json.dumps(json.loads(text), indent=1, ensure_ascii=False) + "\n", text)   # its written form
        self.assertEqual({s["id"]: s["allowed_tools"] for s in SPEC["scenarios"] if "allowed_tools" in s},
                         {"subagent_brief": ["Workflow"]})



# ---- R-28: the scenario skill_changed_midsession, the read_file kind, the receipt hook's floor check (tests 25-27a)

def scenario_of(sid):
    return next(s for s in SPEC["scenarios"] if s["id"] == sid)


def known_of(scenario, label_part):
    return next(k for k in scenario["known"] if label_part in k["label"])


class SkillChangedRunner(unittest.TestCase):
    """Test 25: `between_turns` writes each file into the case project only, after the turn it follows and before the
    next one; nothing under the live config moves."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name)
        self.home = self.tmp / "home"
        (self.home / ".claude" / "skills").mkdir(parents=True)
        p = mock.patch.object(Path, "home", lambda: self.home)
        p.start()
        self.addCleanup(p.stop)
        self.config = self.tmp / "sealed"
        evals.build_sealed(self.config)

    def test_between_turns_edit(self):
        scenario = scenario_of("skill_changed_midsession")
        self.assertEqual([(b["after"], b["write"]) for b in scenario["between_turns"]],
                         [(1, ".claude/skills/demo/SKILL.md"), (2, ".claude/skills/demo/SKILL.md"),
                          (2, ".claude/skills/demo/NOTE.md")])
        seen, notes, real = [], [], subprocess.run
        case = self.tmp / "out" / "c"

        def run(cmd, *a, **kw):
            if cmd and str(cmd[0]).endswith("claude-bin"):
                if cmd[1:] == ["--version"]:
                    return subprocess.CompletedProcess(cmd, 0, "2.1.282 (Claude Code)\n", "")
                demo = Path(kw["cwd"]) / ".claude" / "skills" / "demo" / "SKILL.md"
                seen.append(re.findall(r"DEMO-MARK: (\w+)", demo.read_text(encoding="utf-8")))
                notes.append(re.findall(r"DEMO-NOTE: (\w+)", (demo.parent / "NOTE.md").read_text(encoding="utf-8")))
                lines = [{"type": "system", "subtype": "init", "session_id": "s1", "claude_code_version": "2.1.282"},
                         {"type": "result", "result": "done", "total_cost_usd": 0.1, "session_id": "s1"}]
                return subprocess.CompletedProcess(cmd, 0, "\n".join(json.dumps(l) for l in lines), "")
            return real(cmd, *a, **kw)
        live = sorted(str(p) for p in (self.home / ".claude").rglob("*"))
        pack_skills = sorted(str(p) for p in evals.PACK.rglob("SKILL.md") if ".git" not in p.parts)
        (self.tmp / "claude-bin").write_text("#!/bin/sh\n", encoding="utf-8")
        with mock.patch.object(evals.subprocess, "run", run):
            evals.run_case(case, scenario, "fable", SPEC["prompts"], self.config, str(self.tmp / "claude-bin"), "2.1.282")
        self.assertEqual(seen, [["alpha"], ["beta"], ["gamma"]])          # each write before the turn it precedes
        self.assertEqual(notes, [["one"], ["one"], ["two"]])              # the skill's other file, rewritten with v3
        self.assertEqual(sorted(str(p) for p in (self.home / ".claude").rglob("*")), live)
        self.assertEqual(sorted(str(p) for p in evals.PACK.rglob("SKILL.md") if ".git" not in p.parts), pack_skills)
        # a malformed entry, or one that would write outside the case project, is refused before anything runs
        for bad in ({"after": 1, "write": "../escape.md", "from": "overlays/skill_demo_v2/SKILL.md"},
                    {"after": 1, "write": "x.md"}):
            with self.assertRaises(ValueError):
                evals.between_turns_writes(dict(scenario, between_turns=[bad]), 1, case / "project")


class SkillChangedKnownRuns(unittest.TestCase):
    """Tests 26, 26a, 27 and 27a in R-22's known-run framework."""

    @classmethod
    def setUpClass(cls):
        import selftest
        cls.selftest = selftest

    def book(self, *scenarios):
        book = self.selftest.Book(dict(SPEC, scenarios=list(scenarios)), workers=2)
        self.addCleanup(book.close)
        return book

    def graded(self, scenario, known):
        book = self.book(scenario)
        key = (scenario["id"], "x", 0)
        book.build([(key, scenario, dict(known, _prompts=[SPEC["prompts"].get(t, t) for t in scenario["turns"]]))])
        return book, book.cases[key]

    def verdict(self, case, check):
        rec = json.loads((case / "record.json").read_text(encoding="utf-8"))
        ctx = {"proj": case / "project", "case_dir": case, "record": rec, "scenario": {}}
        return evals.check(check[0], check[1:], ctx)

    def test_the_scenario_as_planned(self):
        s = scenario_of("skill_changed_midsession")
        self.assertEqual((s["seats"], s["quick"], s["fixture_skills"]), (["fable", "opus55"], ["fable"], ["demo"]))
        self.assertEqual(len(s["turns"]), 3)
        self.assertIn(["hook_fired", "SessionStart", "demo skill changed", "block", "eli5/skill_fresh.py", {"turn": 2}],
                      s["checks"])
        self.assertIn(["read_file", ".claude/skills/demo/SKILL.md", {"turn": 2}], s["checks"])
        self.assertIn(["hook_fired", "SessionStart", "demo skill changed", "block", "eli5/skill_fresh.py",
                       {"turn": 3}], s["checks"])
        # a file the skill has the session read, rewritten with the second rewrite, is named and read again
        self.assertIn(["hook_fired", "SessionStart", "NOTE.md, a file of the demo skill, changed", "block",
                       "eli5/skill_fresh.py", {"turn": 3}], s["checks"])
        # each change is named once: the resume names it, and the prompt that follows runs without repeating it
        self.assertIn(["hook_fired", "UserPromptSubmit", "eli5/skill_fresh.py", "ran", {"turn": 3}], s["checks"])
        self.assertFalse([c for c in s["checks"] if c[:2] == ["hook_fired", "UserPromptSubmit"] and c[3] != "ran"])
        for k in s["known"]:
            for t in k["turns"][1:]:
                self.assertEqual([h["text"] for h in t["hooks"] if h["event"] == "UserPromptSubmit"], [""], k["label"])
        self.assertIn(["read_file", ".claude/skills/demo/NOTE.md", {"turn": 3}], s["checks"])
        self.assertIn(["final_matches", 3, "^(?![\\s\\S]*DEMO-NOTE: one)[\\s\\S]*DEMO-NOTE: two"], s["checks"])
        self.assertEqual(known_of(s, "not NOTE.md")["expect"], "fail")
        self.assertLessEqual({"layer:UserPromptSubmit skill_fresh.py", "layer:SessionStart skill_fresh.py"}, set(s["covers"]))
        self.assertFalse([c for c in s["checks"] if c[0] == "any_of"])

    def test_skill_changed_known_bad(self):
        """Test 26: turn 2 on the old text, turn 3 on v2's, and turn-2 notice present with turn-3 notice absent each fail
        (the last only through the turn scope); every synthetic run carries a hook log and no hook attachment in turns
        2 and 3; the known-good passes through the log, and fails with turn 2's skill_fresh.py log line removed."""
        s = scenario_of("skill_changed_midsession")
        problems, ok, owned = self.selftest.known_problems(self.book(s))
        self.assertEqual((problems, owned), ([], {}))
        self.assertEqual(ok, len(s["known"]))
        for part in ("DEMO-MARK: alpha", "DEMO-MARK: beta", "turn-3 notice absent"):
            self.assertEqual(known_of(s, part)["expect"], "fail", part)
        absent = known_of(s, "turn-3 notice absent")
        _, case = self.graded(s, absent)
        scoped = ["hook_fired", "SessionStart", "demo skill changed", "block", "eli5/skill_fresh.py", {"turn": 3}]
        self.assertFalse(self.verdict(case, scoped)[0])
        self.assertTrue(self.verdict(case, scoped[:-1])[0])                    # unscoped, turn 2's notice passes it
        good = self.selftest.first_good(s)
        _, case = self.graded(s, good)
        events = [json.loads(l) for l in (case / "transcripts" / "known.jsonl").read_text(encoding="utf-8").splitlines()]
        attachments = [e for e in events if e.get("type") == "attachment"
                       and e["attachment"].get("hookEvent") in ("SessionStart", "UserPromptSubmit")]
        self.assertEqual(len(attachments), 1)                                  # turn 1's loader output only
        self.assertTrue((case / "hooks.jsonl").is_file())
        check = next(c for c in s["checks"] if c[:2] == ["hook_fired", "SessionStart"])
        self.assertTrue(self.verdict(case, check)[0])
        unlogged = json.loads(json.dumps(good))
        for h in unlogged["turns"][1]["hooks"]:
            if h["event"] == "SessionStart":
                h["log"] = False
        _, case = self.graded(s, unlogged)
        ok, detail = self.verdict(case, check)
        self.assertFalse(ok, detail)

    def test_read_file_kind(self):
        """Test 26a: judged on the stream's tool_use_result.file, in the turn the scope names."""
        s = scenario_of("skill_changed_midsession")
        check = ["read_file", ".claude/skills/demo/SKILL.md", {"turn": 2}]
        want = {"good": True, "limit: 2000": True, "re-invokes /demo": False, "partial Read": False,
                "Read in turn 1 only": False}
        for part, passes in want.items():
            _, case = self.graded(s, known_of(s, part))
            self.assertEqual(self.verdict(case, check)[0], passes, part)
        _, case = self.graded(s, known_of(s, "Read in turn 1 only"))
        self.assertTrue(self.verdict(case, check[:-1])[0])                     # unscoped, turn 1's Read passes it
        violated = evals.violation(check, dict(self.selftest.first_good(s)))
        _, case = self.graded(s, violated)
        self.assertFalse(self.verdict(case, check)[0])
        bearing, rest = self.selftest.load_bearing(self.book(s), kinds=["read_file"])
        self.assertEqual((bearing, rest), (["read_file"], []))

    def test_the_receipt_floor_check_reads_the_hook_log(self):
        """Test 27: redman_eli5 requires the receipt hook's `ran`, read from the case hook log; the transcript's Stop
        summary is read when present and never required; a stream hook_response alone never passes."""
        s = scenario_of("redman_eli5")
        check = ["hook_fired", "Stop", "eli5/receipt_check.py", "ran"]
        self.assertIn(check, s["checks"])
        self.assertIn("layer:Stop receipt_check.py", s["covers"])
        for part, passes in (("fixes R-1", True), ("no stop summary record", True), ("never ran", False)):
            _, case = self.graded(s, known_of(s, part))
            self.assertEqual(self.verdict(case, check)[0], passes, part)
        _, case = self.graded(s, known_of(s, "fixes R-1"))
        summary = [json.loads(l) for l in (case / "transcripts" / "known.jsonl").read_text(encoding="utf-8").splitlines()
                   if '"stop_hook_summary"' in l]
        self.assertTrue(any(i["command"].endswith("eli5/receipt_check.py") for r in summary for i in r["hookInfos"]))
        never = known_of(s, "never ran")
        self.assertTrue(any(h.get("stream") and h["event"] == "Stop" for t in never["turns"] for h in t.get("hooks") or []))

    def test_two_stop_blocks_in_one_stop(self):
        """Test 27a: Stop hooks run in parallel, so one stop can carry turn_check.py's block and receipt_check.py's; the
        turn is graded "repaired by hook" by the receipt check's command, in either order, and the doc-guardrails
        block is its own check. Known-bad: a grader that reads only the first blocking output misses it."""
        s = scenario_of("shell_doc_eli5")
        runs = [k for k in s["known"] if "two blocks in one stop" in k["label"]]
        self.assertEqual(len(runs), 2)
        for k in runs:
            _, case = self.graded(s, k)
            ok, detail = self.verdict(case, ["receipt", 2, {}])
            self.assertEqual((ok, detail), (False, "repaired by hook: turn 2"), k["label"])
            self.assertTrue(self.verdict(case, ["hook_fired", "Stop", "doc-guardrails (end of turn)"])[0])

        def first_block_only(ctx, n, ids=()):
            part = evals.transcript_parts(ctx)[n - 1]
            first = next((e for e in part if e["kind"] == "hook" and e["type"] == "hook_blocking_error"
                          and e["event"] == "Stop"), None)
            if first and evals.ends_with_script(evals.script_of(first.get("command")), evals.RECEIPT_HOOK):
                return f"repaired by hook: turn {n}"
            return None
        turn_check_first = next(k for k in runs if "turn_check.py's first" in k["label"])
        _, case = self.graded(s, turn_check_first)
        with mock.patch.object(evals, "_repaired", first_block_only):
            self.assertTrue(self.verdict(case, ["receipt", 2, {}])[0])       # the mutant grades the turn a pass



# ---- the run log (R-29 § 6): every graded run one RUNS.jsonl record; the backfill; the dry-run totals line

PAGE = """### Every run

| Date | Cases | Result | What failed, and what it was |
|---|---|---|---|
| 2026-09-15 | 3 | 2 / 3 | `beta` on Opus — it stopped short ($0.90) |
| 2026-09-22 | 2 (quick) | **2 / 2** | — ($15.24; after the receipt's shape changed to v5) |

Four distinct behaviour defects were caught.
"""


class RunLog(unittest.TestCase):
    """RUNS.jsonl: one record per graded run, the backfill over every recorded run (tests 19-21, 24, 26, 27)."""
    SPEC = {"prompts": {}, "scenarios": [
        {"id": "alpha", "turns": ["p"], "seats": ["fable", "opus"], "quick": ["fable"], "checks": []},
        {"id": "beta", "turns": ["p"], "seats": ["opus"], "quick": ["opus"], "checks": []}]}

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name)
        self.root, self.runs, self.p0 = self.tmp / "runs", self.tmp / "RUNS.jsonl", self.tmp / "p0"
        self.repo = self.tmp / "repo"
        (self.repo / "evals").mkdir(parents=True)
        subprocess.run(["git", "-C", str(self.repo), "init", "-q", "-b", "main"], check=True)
        (self.repo / "evals" / "scenarios.json").write_text(json.dumps(self.SPEC), encoding="utf-8")
        subprocess.run(["git", "-C", str(self.repo), "add", "-A"], check=True)
        env = dict(os.environ, GIT_AUTHOR_DATE="@1700000000 +0000", GIT_COMMITTER_DATE="@1700000000 +0000")
        subprocess.run(["git", "-C", str(self.repo), "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm",
                        "spec"], check=True, env=env)
        self.p0.mkdir()
        self.regrade_json({})

    def case(self, run_dir, name, turns, passed=True, init=None):
        """A recorded case: `turns` as [(version, total)], its record and streams; returns its grades entry."""
        c = run_dir / name
        c.mkdir(parents=True)
        rec_turns = []
        for n, (version, total) in enumerate(turns, 1):
            first = dict(init or {"type": "system", "subtype": "init"}) if n == 1 else {"type": "system", "subtype": "init"}
            lines = [dict(first, claude_code_version=version)]
            if total is not None:
                lines.append({"type": "result", "result": "done", "total_cost_usd": total})
            text = "\n".join(json.dumps(l) for l in lines) + "\n"
            (c / f"turn{n}.jsonl").write_text(text, encoding="utf-8")
            rec_turns.append(evals.parse_stream(text))
        scenario, seat = name.rsplit("-", 1)
        (c / "record.json").write_text(json.dumps({"scenario": scenario, "seat": seat, "turns": rec_turns,
                                                   "snapshots": [{}] * (len(turns) + 1)}), encoding="utf-8")
        summed = round(sum(round(x, 2) for _, x in turns if x is not None), 2) if turns else 0
        return {"scenario": scenario, "seat": seat, "passed": passed, "cost": summed,
                "results": [] if passed else [{"check": ["tests_pass"], "passed": False, "detail": "FAILED"}]}

    def mkrun(self, rel, cases):
        d = self.root / rel
        d.mkdir(parents=True)
        grades = [self.case(d, *c) for c in cases]
        (d / "grades.json").write_text(json.dumps(grades), encoding="utf-8")
        return d

    def regrade_json(self, extra, costs="", synced=""):
        doc = dict({"flips": [], "costs": [], "synced": [], "synced_counts": {}, "cost_errors": [], "not_regraded": []},
                   **extra)
        (self.p0 / "regrade.json").write_text(json.dumps(doc), encoding="utf-8")
        (self.p0 / "costs.md").write_text(costs, encoding="utf-8")
        (self.p0 / "synced.md").write_text(synced, encoding="utf-8")

    def backfill(self, page=None, today="2026-09-28"):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = evals.backfill(self.root, self.runs, regrade=self.p0, page=page, repo=self.repo, today=today)
        return code, out.getvalue()

    def records(self):
        return [json.loads(l) for l in self.runs.read_text(encoding="utf-8").splitlines()] if self.runs.is_file() else []

    def test_backfill_idempotent(self):
        self.mkrun("20260915-100000", [("alpha-fable", [("2.1.272", 0.2)]), ("alpha-opus", [("2.1.272", 0.3)]),
                                     ("beta-opus", [("2.1.272", 0.4)], False)])
        self.mkrun("nested/20260915-110000", [("alpha-fable", [("2.1.272", 0.2)])])
        self.assertEqual(self.backfill()[0], 0)
        first = self.records()
        self.assertEqual(self.backfill()[0], 0)
        self.assertEqual(self.records(), first)                                  # one record per run, in time order
        self.assertEqual([r["run"] for r in first], ["20260915-100000", "nested/20260915-110000"])
        full, only = first
        self.assertEqual(sorted(full), sorted(evals.RUN_FIELDS))
        self.assertEqual((full["selection"], full["seats"], full["cases"], full["passed"], full["result"], full["cost"],
                          full["failed"], full["date"], full["published"]),
                         ("full", ["fable", "opus"], 3, 2, "2/3", 0.9, [["beta", "opus"]], "2026-09-15", True))
        self.assertEqual((only["selection"], only["published"]), ("only", False))
        self.assertFalse(any(str(self.tmp) in l for l in self.runs.read_text().splitlines()))  # never an absolute path

    def test_backfill_counts_a_run_on_its_own_grades_and_notes_its_recheck(self):
        """The evidence page's rule: a run counts on its own grades; a later re-check of the same sessions is noted,
        never applied. Known-bad: the record taking the re-check's pass as the run's result."""
        d = self.mkrun("20260915-100000", [("alpha-fable", [("2.1.272", 0.2)], False)])
        later = json.loads((d / "grades.json").read_text(encoding="utf-8"))
        later[0].update(passed=True, results=[])
        (d / "regrade-20260920-120000.json").write_text(json.dumps(later), encoding="utf-8")
        self.assertEqual(self.backfill()[0], 0)
        (rec,) = self.records()
        self.assertEqual((rec["passed"], rec["cases"]), (0, 1))
        self.assertIn("re-checked regrade-20260920-120000: 1/1 passed, noted, not counted", rec["note"])

    def test_backfill_cost_by_cli_version(self):
        """Known-bad included (G-27 F1-Y2): the record's cost is the corrected instrument's; a falling total stops."""
        self.mkrun("20260922-224650", [("alpha-fable", [("2.1.280", 0.5872), ("2.1.280", 1.1673), ("2.1.280", 1.3013)])])
        self.mkrun("20260915-140257", [("alpha-fable", [("2.1.273", 0.5444), ("2.1.273", 0.5417), ("2.1.273", 0.4887)])])
        self.regrade_json({}, costs="20260922-224650 $3.06 → $1.30\n")
        self.assertEqual(self.backfill()[0], 0)
        by = {r["run"]: r for r in self.records()}
        self.assertEqual(by["20260922-224650"]["cost"], 1.30)
        self.assertEqual(by["20260922-224650"]["note"],
                         "cost recomputed 2026-09-28: $3.06 → $1.30, running totals from Claude Code 2.1.277")
        self.assertEqual((by["20260915-140257"]["cost"], by["20260915-140257"]["note"]), (1.57, ""))
        # a changed run P0's list does not name stops the backfill: the two instruments must agree
        self.runs.unlink()
        self.regrade_json({}, costs="")
        code, out = self.backfill()
        self.assertEqual((code, self.records()), (1, []))
        self.assertIn("20260922-224650: cost $3.06 → $1.30 is not in costs.md", out)
        # the known-bad: a total lower than the one before stops the backfill naming the case, and writes nothing
        self.mkrun("20260922-230000", [("alpha-fable", [("2.1.280", 0.6), ("2.1.280", 0.5)])])
        self.regrade_json({}, costs="20260922-224650 $3.06 → $1.30\n")
        code, out = self.backfill()
        self.assertEqual((code, self.records()), (1, []))
        self.assertIn("20260922-230000/alpha-fable: turn 2 total $0.50 below turn 1 $0.60", out)

    def test_regenerated_table_keeps_row_explanation(self):
        """Known-bad included (G-27 Addenda): each hand-written row's explanation is copied into its run's note, a cost
        P0's list changes written as the new figure; a row that matches no run stops the backfill."""
        sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "closeout"))
        import closeout
        self.mkrun("20260915-100000", [("alpha-fable", [("2.1.273", 0.3)]), ("alpha-opus", [("2.1.273", 0.3)]),
                                     ("beta-opus", [("2.1.273", 0.3)], False)])
        self.mkrun("20260922-213751", [("alpha-fable", [("2.1.280", 5.0), ("2.1.280", 6.0)]),
                                     ("beta-opus", [("2.1.280", 4.24)])])
        self.regrade_json({}, costs="20260922-213751 $15.24 → $10.24\n")
        page = self.tmp / "evidence.md"
        page.write_text(PAGE, encoding="utf-8")
        code, out = self.backfill(page=page)
        self.assertEqual(code, 0, out)
        by = {r["run"]: r for r in self.records()}
        self.assertTrue(by["20260922-213751"]["note"].startswith("— ($10.24; after the receipt's shape changed to v5)"))
        self.assertTrue(by["20260915-100000"]["note"].startswith("`beta` on Opus — it stopped short ($0.90)"))
        # the page as C28 leaves it: the marked block, filled by `figures --write` from the records
        proj = self.tmp / "proj"
        (proj / "evals").mkdir(parents=True)
        (proj / "evals" / "RUNS.jsonl").write_text(self.runs.read_text(encoding="utf-8"), encoding="utf-8")
        (proj / "tools").mkdir()
        (proj / "tools" / "figures.json").write_text(json.dumps({"docs": ["evidence.md"], "figures": {}, "tables": {
            "runs": {"file": "evals/RUNS.jsonl", "where": {"selection": ["full", "quick", "seat"]},
                     "summary_of": {"selection": "only"}, "columns": ["date", "cases_label", "result_label",
                                                                      "failed_note"]}}}), encoding="utf-8")
        (proj / "CLAUDE.md").write_text("# p\n\n## Doc map\nfigures: tools/figures.json\n", encoding="utf-8")
        (proj / "evidence.md").write_text("### Every full, quick and seat run\n\n<!-- fig-table:runs -->\n"
                                          "<!-- /fig-table -->\n\nFour distinct behaviour defects were caught.\n",
                                          encoding="utf-8")
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(closeout.main(["figures", str(proj), "--write"]), 0)
        text = (proj / "evidence.md").read_text(encoding="utf-8")
        self.assertIn("| 2026-09-22 | 2 (quick) | **2 / 2** | — ($10.24; after the receipt's shape changed to v5)", text)
        self.assertIn("| 2026-09-15 | 3 | 2 / 3 | `beta` on Opus — it stopped short ($0.90) |", text)
        self.assertNotIn("($15.24;", text)
        self.assertEqual(closeout.main(["figures", str(proj), "--check"]), 0)
        # the known-bad: a row that matches no run stops the backfill, naming it
        self.runs.unlink()
        page.write_text(PAGE.replace("2 / 3", "1 / 3"), encoding="utf-8")
        code, out = self.backfill(page=page)
        self.assertEqual((code, self.records()), (1, []))
        self.assertIn("row matches no run: | 2026-09-15 | 3 | 1 / 3 |", out)

    def test_synced_runs_are_noted(self):
        """Known-bad included (G-28 F2-Z2, F2-Z8; G-29 F3-Z4): a run synced.md names gets its per-kind counts from
        regrade.json, distinct entries by name; synced.md's own case counts are never read."""
        syn = lambda run, case, p=(), s=(), c=(): {"run": run, "case": case, "plugins": list(p), "skills": list(s),
                                                   "connectors": list(c), "version": "2.1.280"}
        self.mkrun("20260922-192134", [("alpha-fable", [("2.1.280", 0.1)]), ("alpha-opus", [("2.1.280", 0.1)])])
        self.mkrun("20260915-140257", [("alpha-fable", [("2.1.273", 0.1)]), ("alpha-opus", [("2.1.273", 0.1)]),
                                     ("beta-opus", [("2.1.273", 0.1)])])
        self.mkrun("20260915-150000", [("alpha-fable", [("2.1.273", 0.1)])])
        # the counting pair: one plugin under two paths, one connector with two statuses (read from the inits)
        pair = [{"name": "x", "source": "x@synced", "path": f"/p/x~g{n}"} for n in (2, 3)]
        statuses = ({"name": "claude.ai Z", "status": "connected"}, {"name": "claude.ai Z", "status": "failed"})
        inits = [{"type": "system", "subtype": "init", "plugins": pair, "skills": ["anthropic-skills:y"],
                  "mcp_servers": [st]} for st in statuses]
        d = self.root / "20260922-193818"
        d.mkdir(parents=True)
        grades = [self.case(d, name, [("2.1.280", 0.1)], init=init) for name, init in zip(("alpha-fable", "alpha-opus"), inits)]
        (d / "grades.json").write_text(json.dumps(grades), encoding="utf-8")
        entries = [evals.account_entries(evals.parse_stream((d / n / "turn1.jsonl").read_text())["init"])
                   for n in ("alpha-fable", "alpha-opus")]
        self.assertEqual(len({json.dumps(p) for i in inits for p in i["plugins"]}), 2)      # whole entries: 2, wrong
        self.assertEqual(len({json.dumps(i["mcp_servers"][0]) for i in inits}), 2)
        self.regrade_json({"synced": [
            syn("20260922-192134", "alpha-fable", ["x"], ["anthropic-skills:y"], ["claude.ai Z"]),
            syn("20260922-192134", "alpha-opus", ["x"], ["anthropic-skills:y"]),
            syn("20260915-140257", "alpha-fable", c=["claude.ai A", "claude.ai B"]),
            syn("20260915-140257", "alpha-opus", c=["claude.ai A", "claude.ai B"]),
            syn("20260922-193818", "alpha-fable", *entries[0]), syn("20260922-193818", "alpha-opus", *entries[1])]},
            synced="20260915-140257 — 9 case(s)\n20260922-192134 — 2 case(s)\n20260922-193818 — 2 case(s)\n")
        code, out = self.backfill()
        self.assertEqual(code, 0, out)
        by = {r["run"]: r["note"] for r in self.records()}
        head = "sessions listed account-supplied entries (read from the recorded sessions): "
        self.assertEqual(by["20260922-192134"], head + "synced plugins 1 in 2 of 2, synced skills 1 in 2 of 2, "
                                                       "claude.ai connectors 1 in 1 of 2")
        self.assertEqual(by["20260915-140257"], head + "synced plugins 0, synced skills 0, claude.ai connectors 2 in 2 of 3")
        self.assertEqual(by["20260922-193818"], head + "synced plugins 1 in 2 of 2, synced skills 1 in 2 of 2, "
                                                       "claude.ai connectors 1 in 2 of 2")
        self.assertNotIn("account-supplied", by["20260915-150000"])
        # the known-bad: a run synced.md names with no entry in regrade.json stops the backfill, writing nothing
        self.runs.unlink()
        self.regrade_json({"synced": []}, synced="20260915-140257 — 2 case(s)\n")
        code, out = self.backfill()
        self.assertEqual((code, self.records()), (1, []))
        self.assertIn("20260915-140257: in synced.md but no synced entry in regrade.json", out)

    def test_flips_move_the_record(self):
        """C42: a re-graded verdict moves the run's record, with a dated note; the layer flag follows it."""
        self.mkrun("20260914-213447", [("alpha-fable", [("2.1.272", 0.1)]), ("alpha-opus", [("2.1.272", 0.1)])])
        detail = "turn 1: header '# Working doctrine (injected: this session runs Claude Opus 5)' does not name Claude Fable 5.1 (seat fable)"
        self.regrade_json({"flips": [{"run": "20260914-213447", "case": "alpha-fable", "check": "layer",
                                      "check_args": ["layer"], "before": True, "after": False, "detail": detail}]})
        (self.p0 / "flips.md").write_text("## Runs, and the scenarios each was graded against\n- 20260914-213447: graded "
                                          "against abc\n", encoding="utf-8")
        self.assertEqual(self.backfill()[0], 0)
        (r,) = self.records()
        self.assertEqual((r["passed"], r["result"], r["failed"], r["layer_ok"]), (1, "1/2", [["alpha", "fable"]], False))
        self.assertEqual(r["note"], f"re-graded 2026-09-28 on the later checks: layer — alpha-fable: {detail}")

    def test_run_appends_one_record(self):
        """A graded run appends exactly one record with every field; `run` is relative to the runs root."""
        out = self.mkrun("20260928-120000", [("alpha-fable", [("2.1.283", 0.25)])])
        grades = json.loads((out / "grades.json").read_text())
        with mock.patch.object(evals, "RUNS_FILE", self.runs):
            evals.log_run(out, self.root, grades, "only", pack_sha="a" * 40)
        (r,) = self.records()
        self.assertEqual(sorted(r), sorted(evals.RUN_FIELDS))
        self.assertEqual((r["run"], r["selection"], r["pack_sha"], r["result"], r["cost"], r["layer_ok"]),
                         ("20260928-120000", "only", "a" * 40, "1/1", 0.25, None))

    def test_the_launch_path_logs_the_run(self):
        """main() appends the record after the report; --dry-run appends nothing."""
        def batch(cases, spec, out, *a, **k):
            self.assertEqual([(s["id"], st) for s, st in cases], [("refusal", "fable")])
            return [dict(self.case(out, "refusal-fable", [("2.1.283", 0.1)]), results=[])], None
        (self.tmp / "bin").mkdir()
        write_fake_cli(self.tmp / "bin" / "claude", "2.1.283")
        import selftest as st
        with mock.patch.object(evals, "RUNS_FILE", self.runs), mock.patch.object(evals, "run_batch", batch), \
                mock.patch.object(evals, "preflight", lambda *a, **k: (0, "")), \
                mock.patch.object(evals, "build_sealed", lambda *a, **k: None), \
                mock.patch.object(st, "main", lambda argv=None, quiet=False: 0), \
                mock.patch.dict(os.environ, {"PATH": f"{self.tmp / 'bin'}{os.pathsep}{os.environ['PATH']}"}), \
                contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            evals.main(["--only", "refusal", "--seat", "fable", "--out", str(self.root), "--config", str(self.tmp / "c")])
            self.assertEqual(len(self.records()), 1)
            evals.main(["--dry-run", "--out", str(self.root)])
        (r,) = self.records()
        self.assertEqual(r["selection"], "only")

    def test_dry_run_totals(self):
        """The dry run's last line is the totals line, from the function --counts prints; with a selection, the last
        run of that selection from RUNS.jsonl follows it."""
        def lines(*argv):
            with mock.patch.object(evals, "RUNS_FILE", self.runs), contextlib.redirect_stdout(io.StringIO()) as out:
                self.assertEqual(evals.main(["--dry-run", *argv, "--out", str(self.root)]), 0)
            return out.getvalue().strip().splitlines()
        c = evals.counts(SPEC)
        self.assertEqual(lines()[-1], f"totals: {c['scenarios']} scenarios, {c['graded sessions']} graded sessions, "
                                      f"{c['turns']} turns, {c['checks']} checks ({c['check-runs']} check-runs)")
        self.runs.write_text("".join(json.dumps(r) + "\n" for r in (
            {"run": "a", "date": "2026-09-22", "selection": "quick", "cases": 11, "passed": 11, "cost": 9.0},
            {"run": "b", "date": "2026-09-25", "selection": "full", "cases": 43, "passed": 41, "cost": 30.1},
            {"run": "c", "date": "2026-09-25", "selection": "quick", "cases": 17, "passed": 15, "cost": 8.34})),
            encoding="utf-8")
        quick = lines("--quick")
        self.assertTrue(quick[-2].startswith("totals: "))
        self.assertEqual(quick[-1], "last run of this selection: 2026-09-25 15/17 $8.34 (c)")
        self.assertEqual(lines()[-1].split(":")[0], "totals")                   # the full dry run: totals last

    def test_an_only_dry_run_names_the_last_run_of_the_same_cases(self):
        """Ledger row 2026-09-28 (selection match): an `--only` selection is matched on its scenarios and seats, read
        from each recorded run's own grades.json, never on the kind alone: B's four-scenario dry run had printed
        E-1's one-scenario run."""
        def last(*argv):
            with mock.patch.object(evals, "RUNS_FILE", self.runs), contextlib.redirect_stdout(io.StringIO()) as out:
                self.assertEqual(evals.main(["--dry-run", *argv, "--out", str(self.root)]), 0)
            return out.getvalue().strip().splitlines()[-1]
        self.mkrun("20260928-122600", [("refusal-opus55", [("2.1.283", 0.26)])])
        self.mkrun("20260928-213010", [("refusal-fable", [("2.1.283", 0.5)]), ("refusal-opus55", [("2.1.283", 0.5)])])
        self.mkrun("20260929-110714", [("pushback_ruling-fable", [("2.1.283", 0.4)])])
        self.runs.write_text("".join(json.dumps(r) + "\n" for r in (
            {"run": "20260928-122600", "date": "2026-09-28", "selection": "only", "cases": 1, "passed": 1, "cost": 0.26},
            {"run": "20260928-213010", "date": "2026-09-28", "selection": "only", "cases": 2, "passed": 2, "cost": 1.0},
            {"run": "20260929-110714", "date": "2026-09-29", "selection": "only", "cases": 1, "passed": 1, "cost": 0.4},
            {"run": "gone", "date": "2026-09-30", "selection": "only", "cases": 1, "passed": 1, "cost": 0.1})),
            encoding="utf-8")
        self.assertEqual(last("--only", "refusal"), "last run of this selection: 2026-09-28 2/2 $1.00 (20260928-213010)")
        self.assertEqual(last("--only", "refusal", "--seat", "opus55"),
                         "last run of this selection: 2026-09-28 1/1 $0.26 (20260928-122600)")
        self.assertEqual(last("--only", "pushback_ruling", "--seat", "opus55"),
                         "last run of this selection: none recorded for these cases")



if __name__ == "__main__":
    unittest.main()
