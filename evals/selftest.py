#!/usr/bin/env python3
"""The graders proven offline (R-22): every scenario's known runs grade as declared, every check fails on its
violation, and every check kind is load-bearing — before any paid session is graded by them.

  python3 evals/run.py --selftest            one summary line (exit 0), or one line per problem (exit 1)
  python3 evals/run.py --selftest --co-fail  also the co-fail review table

A known run (scenarios.json, each scenario's `known`) is data: per turn the skills it invoked (`skills`), the typed
commands whose skill loaded (`typed`), its Bash calls with their results (`bash`), the files it wrote with
Write/Edit (`writes`), the files it Read (`reads`: path and the result's line counts), its final message (`final`), the
hooks that fired (`hooks`; `stream` puts a non-SessionStart output in the stream too), the agents it started (`agents`:
each one's tool, input, first message and SubagentStart hook context (`context`), written as the agent transcript
beside the session's) and the changes it made to the project (`ops`). A bad run names the kinds it must fail on
(`fails`) and may name kinds that must still pass (`passes`). A run a later row must turn green carries `owned_by: "R-<n> step <s>"` and must grade short of its final
expectation until that row removes the mark. build_known() turns one into a case folder exactly as run_case leaves one — the real
build_project, one stream per turn in the CLI's shape, the case transcript, the hook log, record.json — and grade()
grades it. The streams are synthetic; the records they are built from are harvested (evals/known/shapes/). A known
run's end-of-turn text is re-derived instead of trusted: stop_text_problems() replays the run against the real
turn_check.py and fails on any text the hook would not print, or any fixture-script output the script does not print.
Stdlib only, Python 3.9 or newer.
"""
import concurrent.futures
import copy
import hashlib
import json
import re
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import run  # noqa: E402

SHAPES = HERE / "known" / "shapes"
OWNED_RE = re.compile(r"^R-\d+ step \d+[a-z]?$")
# The reviewed fields of the manifest (bundled skills, built-in plugins, CLI) are filled from R-31's first sealed run.
# Until then a known run's environment is graded against these stand-ins, used only for a field the manifest leaves
# unfilled; once E-1 fills a field, known runs are built and graded with the manifest's own value.
KNOWN_RUN_REVIEW = {"bundled_skills": ["known-run-bundled-skill"], "builtin_plugins": ["telemetry@builtin"],
                    "cli_version": "0.0.0-known-run"}
WORKERS = 8


def load_spec():
    return json.loads((HERE / "scenarios.json").read_text(encoding="utf-8"))


def reviewed_manifest(root):
    """A copy of the manifest with its unfilled reviewed fields filled by KNOWN_RUN_REVIEW, written under root."""
    m = run.manifest()
    for field, value in KNOWN_RUN_REVIEW.items():
        if not m.get(field):
            m[field] = value
    path = Path(root) / "sealed-known-runs.json"
    path.write_text(json.dumps(m), encoding="utf-8")
    return path


# ------------------------------------------------------------------------------------------------ the builder

def _shape(name):
    return json.loads((SHAPES / name).read_text(encoding="utf-8"))


LAYER_TEXT = "# Working notes (injected: this session runs Claude Opus 5)\n\n(the {name} of a known run)\n"


def fixture_layers(folder):
    """A known run's doctrine folder: one fixture file per layer the loader's table names, each written as a real
    layer is (a header naming Claude Opus 5, which the loader rewrites), so layer_expected reads it as it reads the
    live ones."""
    folder.mkdir(parents=True, exist_ok=True)
    t = json.loads(subprocess.run([sys.executable, str(run.pack_path("public/doctrine/gate.py")), "--table"],
                                  capture_output=True, encoding="utf-8", check=True).stdout)
    for name in set(t["measured"].values()) | set(t["family_layer"].values()) | {t["default"]}:
        (folder / name).write_text(LAYER_TEXT.format(name=name), encoding="utf-8")
    return folder


def seat_layer(seat, layer=None):
    """The text the loader injects on a seat: the layer and header `gate.py --resolve` names for the seat's model;
    `layer` ({"layer_of": file, "header": name}, each optional) swaps either, for a run that must fail `layer`."""
    r = run.resolve_models([run.seat_model(seat)])[0]
    layer = layer or {}
    body = LAYER_TEXT.format(name=layer.get("layer_of") or r["layer"])
    return body.replace("Claude Opus 5)", f"{layer.get('header') or r['header']})", 1)


def _at(case_start, n, sec):
    import datetime
    return (case_start + datetime.timedelta(minutes=10 * (n - 1), seconds=sec)).isoformat()


def _sha(text):
    return hashlib.sha256((text or "").encode("utf-8")).hexdigest()


def _git(proj, *args, env=None):
    return subprocess.run(["git", "-C", str(proj), "-c", "user.name=known", "-c", "user.email=known@local", *args],
                          capture_output=True, encoding="utf-8", errors="replace", env=env)


def _text_files(target):
    """The text files at or under a path, as the snapshot sees them (no .git, no __pycache__)."""
    if target.is_file():
        return [target]
    if not target.is_dir():
        return []
    return sorted(p for p in target.rglob("*") if p.is_file() and not run.IGNORED_PARTS.intersection(p.parts))


def _comment(path):
    return "# " if Path(path).suffix in (".py", ".sh", "") else ""


def apply_op(proj, op, env, fixture_head):
    """One change a known run makes to its project. Paths are relative to the project; nothing outside it moves."""
    kind, args = op[0], op[1:]
    if kind == "write":
        p = proj / args[0]
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(args[1], encoding="utf-8")
    elif kind == "append":
        p = proj / args[0]
        p.parent.mkdir(parents=True, exist_ok=True)
        with open(p, "a", encoding="utf-8") as fh:
            fh.write(args[1])
    elif kind == "sub":
        p = proj / args[0]
        text = p.read_text(encoding="utf-8")
        if args[1] not in text:
            raise ValueError(f"sub: {args[1]!r} not in {args[0]}")
        p.write_text(text.replace(args[1], args[2], 1), encoding="utf-8")
    elif kind == "rm":
        (proj / args[0]).unlink()
    elif kind == "rm_glob":
        for p in run._files(proj, args[0]):
            p.unlink()
    elif kind == "resub":
        for p in run._files(proj, args[0]):
            p.write_text(re.sub(args[1], args[2], p.read_text(encoding="utf-8")), encoding="utf-8")
    elif kind == "append_glob":   # the literal into every matching file, or a new file at a literal path
        files = run._files(proj, args[0]) or ([proj / args[0]] if not any(c in args[0] for c in "*?[") else [])
        for p in files:
            p.parent.mkdir(parents=True, exist_ok=True)
            with open(p, "a", encoding="utf-8") as fh:
                fh.write(f"\n{_comment(p)}{args[1]}\n")
    elif kind == "append_under":  # a line into the path, or into the first file under it, or a new file there
        target = proj / args[0]
        files = _text_files(target)
        p = files[0] if files else (target / "zz_violation.md" if target.is_dir() or not target.suffix else target)
        p.parent.mkdir(parents=True, exist_ok=True)
        with open(p, "a", encoding="utf-8") as fh:
            fh.write(f"\n{_comment(p)}{args[1]}\n")
    elif kind == "del_line":      # delete the first line under the path matching the pattern
        for p in _text_files(proj / args[0]):
            lines = p.read_text(encoding="utf-8").splitlines(keepends=True)
            hit = next((i for i, l in enumerate(lines) if l.strip() and re.search(args[1], l)), None)
            if hit is not None:
                del lines[hit]
                p.write_text("".join(lines), encoding="utf-8")
                break
    elif kind == "fail_tests":    # every test of the first test class fails
        for p in sorted((proj / "tests").glob("test_*.py")):
            m = re.search(r"^class (\w+)\(", p.read_text(encoding="utf-8"), re.M)
            if m:
                with open(p, "a", encoding="utf-8") as fh:
                    fh.write(f"\n\ndef _zz_violation(self):\n    self.fail('violation')\n\n\n"
                             f"for _zz in [n for n in dir({m.group(1)}) if n.startswith('test')]:\n"
                             f"    setattr({m.group(1)}, _zz, _zz_violation)\n")
                break
    elif kind == "status":        # every matching record's status value rewritten
        line_re = re.compile(run.closeout_module().STATUS_LINE + r"(.*)$", re.M | re.I)
        for p in run._files(proj, args[0]):
            p.write_text(line_re.sub(lambda m: m.group(0)[:m.start(1) - m.start(0)] + " " + args[1],
                                     p.read_text(encoding="utf-8")), encoding="utf-8")
    elif kind == "move_item":     # the roadmap item's bullet moved under another heading
        rm = proj / "knowledge" / "00-ROADMAP.md"
        lines = rm.read_text(encoding="utf-8").splitlines()
        bullet = next(l for l in lines if re.match(r"^\s*[-*]\s", l) and re.search(rf"\b{re.escape(args[0])}\b", l))
        lines.remove(bullet)
        rm.write_text("\n".join(lines + ["", f"## {args[1]}", bullet]) + "\n", encoding="utf-8")
    elif kind == "git":
        r = _git(proj, *args, env=env)
        if r.returncode != 0:
            raise ValueError(f"git {' '.join(args)}: {r.stderr.strip()[-200:]}")
    elif kind == "git_refused":   # a git command the project's gate must refuse (a commit under `guardrails: block`)
        r = _git(proj, *args, env=env)
        if r.returncode == 0:
            raise ValueError(f"git {' '.join(args)} was expected to be refused and succeeded")
    elif kind == "push":
        (proj / "zz_pushed.txt").write_text("pushed\n", encoding="utf-8")
        _git(proj, "add", "zz_pushed.txt", env=env)
        _git(proj, "commit", "-qm", "pushed", "--no-verify", env=env)
        r = _git(proj, "push", "-q", "origin", "HEAD:main", env=env)
        if r.returncode != 0:
            raise ValueError(f"push: {r.stderr.strip()[-200:]}")
    elif kind == "reset_fixture":
        _git(proj, "reset", "-q", "--soft", fixture_head, env=env)
    elif kind in ("gate_exit_first", "install_gate"):
        hook = Path(_git(proj, "rev-parse", "--git-path", "hooks/pre-commit").stdout.strip())
        hook = hook if hook.is_absolute() else proj / hook
        if kind == "install_gate":
            # what a session's install-gate does: a state that writes nothing (exit 0) or a hand-over (exit 3) is its
            # answer, never a crash of the rebuild; only a helper that could not run at all (exit 2: no git) is
            r = subprocess.run([sys.executable, str(run.pack_path("closeout/closeout.py")), "install-gate", str(proj)],
                               capture_output=True, encoding="utf-8", errors="replace", env=env)
            if r.returncode not in (0, 3):
                raise ValueError(f"install-gate exit {r.returncode}: {(r.stderr or r.stdout).strip()[-200:]}")
        else:
            # the gate's registration as a git config hook removed, and the hook file made to end first: no route to
            # the gate is left
            for key in ("command", "event"):
                _git(proj, "config", "--unset-all", f"hook.doc-guardrails.{key}", env=env)
            if hook.is_file() and "doc-guardrails commit gate" in hook.read_text(encoding="utf-8"):   # a placed block
                text = hook.read_text(encoding="utf-8")
                first, _, rest = text.partition("\n")
                hook.write_text(f"{first}\nexit 0\n{rest}" if first.startswith("#!") else f"exit 0\n{text}",
                                encoding="utf-8")
    else:
        raise ValueError(f"unknown op {kind!r}")


def build_known(scenario, known, root, prompts):
    """The case folder a known run leaves, as run_case would leave it; returns it."""
    import datetime
    case = Path(root)
    case.mkdir(parents=True, exist_ok=True)
    proj = run.build_project(case, scenario)
    env = run.grader_env(case / "state")
    fixture_head = _git(proj, "rev-parse", "HEAD").stdout.strip()
    m, seat = run.manifest(), known.get("seat") or scenario["seats"][0]
    entries = run.sealed_hook_entries()
    _, found = run.case_hooks({"record": {"hooks": [list(e) for e in entries]}})
    loader = found[0]
    stop_cmds = [f"/usr/bin/python3 {e[2]}" if e[2].endswith(".py") else e[2] for e in entries if e[0] == "Stop"]
    layer = known["layer"] if isinstance(known.get("layer"), str) else seat_layer(seat, known.get("layer"))
    quiet_turns = set(known.get("omit_loader_log") or [])
    init = {"type": "system", "subtype": "init", "session_id": "known", "claude_code_version": m["cli_version"],
            "model": seat, "mcp_servers": [],
            "skills": list(m["skills"]) + list(m["bundled_skills"]) + list(scenario.get("fixture_skills") or []),
            "plugins": [{"name": src.split("@")[0], "path": "builtin", "source": src} for src in m["builtin_plugins"]]}
    init.update(known.get("init_patch") or {})
    start = datetime.datetime(2026, 9, 23, 10, 0, tzinfo=datetime.timezone.utc)
    snaps, turns, transcript, log, agent_files = [run.snapshot(proj)], [], [], [], {}
    baseline = run.baseline_tests(proj)
    resumed = _shape("resumed_turn_2_1_280.json")
    summary = _shape("stop_hook_summary.json")
    for n, (key, t) in enumerate(zip(scenario["turns"], known["turns"]), 1):
        prompt = prompts.get(key, key)
        for dest, src in run.between_turns_writes(scenario, n - 1, proj):   # as run_case writes them, between turns
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(src.read_bytes())
        for op in t.get("ops") or []:
            apply_op(proj, op, env, fixture_head)
        omit = t.get("omit_scripts") or []
        omitted = lambda script: any(run.ends_with_script(script, o) for o in omit)
        stream = []
        if n == 1:
            stream.append({"type": "system", "subtype": "hook_response", "hook_id": "k1", "hook_name": "SessionStart:startup",
                           "hook_event": "SessionStart", "output": layer, "stdout": layer, "stderr": "", "exit_code": 0,
                           "outcome": "success", "session_id": "known"})
            transcript.append({"type": "attachment", "attachment": {
                "type": "hook_success", "hookName": "SessionStart:startup", "hookEvent": "SessionStart", "content": layer,
                "stdout": layer, "stderr": "", "exitCode": 0, "command": loader[2]}})
        else:
            stream += [dict(r, output=layer, stdout=layer) if r["subtype"] == "hook_response" else r for r in resumed["stream"]]
        if n not in quiet_turns:
            log.append({"event": "SessionStart", "script": loader[2], "exit": 0, "stdout_sha256": _sha(layer), "t": _at(start, n, 5)})
        for h in t.get("hooks") or []:
            if h["event"] == "SessionStart":
                stream.append({"type": "system", "subtype": "hook_response", "hook_id": "kx",
                               "hook_name": f"SessionStart:{'startup' if n == 1 else 'resume'}", "hook_event": "SessionStart",
                               "output": h["text"], "stdout": h["text"], "stderr": "", "exit_code": 0, "outcome": "success"})
        stream.append(dict(init))
        if prompt.startswith("/"):
            name = prompt[1:].split()[0]
            transcript.append({"type": "user", "promptId": f"p{n}", "message": {"role": "user", "content":
                               f"<command-message>{name}</command-message>\n<command-name>/{name}</command-name>"}})
            if name in (t.get("typed") or []):
                transcript.append({"type": "user", "promptId": f"p{n}", "isMeta": True, "message": {"role": "user", "content": [
                    {"type": "text", "text": f"Base directory for this skill: /sealed/skills/{name}\n\n# /{name}"}]}})
        else:
            transcript.append({"type": "user", "promptId": f"p{n}", "message": {"role": "user", "content": prompt}})
        uses, results = [], []
        for i, s in enumerate(t.get("skills") or []):
            uses.append({"type": "tool_use", "id": f"s{n}{i}", "name": "Skill", "input": {"skill": s}})
        for i, b in enumerate(t.get("bash") or []):
            uses.append({"type": "tool_use", "id": f"b{n}{i}", "name": "Bash", "input": {"command": b["cmd"]}})
            results.append({"type": "tool_result", "tool_use_id": f"b{n}{i}", "content": b.get("out") or ""})
        for i, w in enumerate(t.get("writes") or []):
            uses.append({"type": "tool_use", "id": f"w{n}{i}", "name": "Write",
                         "input": {"file_path": str(proj / w), "content": "…"}})
        for i, r in enumerate(t.get("reads") or []):   # a Read and its result, judged on tool_use_result.file
            uses.append({"type": "tool_use", "id": f"r{n}{i}", "name": "Read",
                         "input": dict({"file_path": str(proj / r["path"])}, **({"limit": r["limit"]} if "limit" in r else {}))})
            f = proj / r["path"]
            content = f.read_text(encoding="utf-8") if f.is_file() else ""
            results.append({"type": "tool_result", "tool_use_id": f"r{n}{i}", "content": content,
                            "_file": {"filePath": str(f), "content": content, "numLines": r["numLines"],
                                      "startLine": r["startLine"], "totalLines": r["totalLines"]}})
        for i, a in enumerate(t.get("agents") or []):
            uses.append({"type": "tool_use", "id": f"a{n}{i}", "name": a["tool"], "input": a.get("input") or {}})
            records = [{"type": "user", "isSidechain": True, "agentId": f"k{n}{i}", "timestamp": _at(start, n, 30),
                        "message": {"role": "user", "content": a["first"]}}]
            if a.get("context"):   # the SubagentStart hooks' additionalContext, as 2.1.283 records it
                att = _shape("subagent_start_context_2_1_283.json")
                att["attachment"]["content"] = list(a["context"])
                att["rendered"] = [{"content": "<system-reminder>\nSubagentStart hook additional context: "
                                               + "\n".join(a["context"]) + "\n</system-reminder>"}]
                records.append(dict(att, agentId=f"k{n}{i}", timestamp=_at(start, n, 31)))
            agent_files[f"agent-k{n}{i}.jsonl"] = records + [
                {"type": "assistant", "isSidechain": True, "timestamp": _at(start, n, 40),
                 "message": {"role": "assistant", "content": [{"type": "text", "text": "Done."}]}}]
        if uses:
            stream.append({"type": "assistant", "message": {"content": uses}})
            transcript.append({"type": "assistant", "message": {"role": "assistant", "content": uses}})
        for res in results:   # one user record per result, as the CLI writes them; a Read's carries its file
            f = res.pop("_file", None)
            stream.append(dict({"type": "user", "message": {"content": [res]}},
                               **({"tool_use_result": {"type": "text", "file": f}} if f else {})))
        for h in t.get("hooks") or []:
            if h["event"] != "SessionStart" and h.get("stream"):   # the output in the stream's hook events
                stream.append({"type": "system", "subtype": "hook_response", "hook_id": "ky", "hook_name": h["event"],
                               "hook_event": h["event"], "output": h["text"], "stdout": h["text"], "stderr": "",
                               "exit_code": 2 if h.get("outcome", "block") == "block" else 0,
                               "outcome": "success", "session_id": "known"})
            if h["event"] != "SessionStart" and h.get("transcript", True):
                att = ({"type": "hook_blocking_error", "hookName": h["event"], "hookEvent": h["event"],
                        "blockingError": {"blockingError": h["text"], "command": h["command"]}}
                       if h.get("outcome", "block") == "block" else
                       {"type": "hook_success", "hookName": h["event"], "hookEvent": h["event"], "content": h["text"],
                        "stdout": h["text"], "command": h["command"]})
                transcript.append({"type": "attachment", "attachment": att})
            if h.get("log", True):
                log.append({"event": h["event"], "script": run.script_of(h["command"]), "exit": 2 if h.get("outcome", "block") == "block" else 0,
                            "stdout_sha256": _sha(h["text"]), "t": _at(start, n, 60)})
        final = t.get("final", "Done.")
        transcript.append({"type": "assistant", "message": {"role": "assistant", "content": [{"type": "text", "text": final}]}})
        commands = t.get("stop_summary", stop_cmds)
        if not t.get("no_summary"):   # a resumed turn on 2.1.280 may carry no Stop summary record
            transcript.append(dict(summary, hookInfos=[{"command": c, "durationMs": 5} for c in commands
                                                       if not omitted(run.script_of(c))], hookCount=len(commands)))
        for c in commands:
            if not omitted(run.script_of(c)):
                log.append({"event": "Stop", "script": run.script_of(c), "exit": 0, "stdout_sha256": _sha(""),
                            "t": _at(start, n, 290)})
        stream.append({"type": "result", "result": final, "total_cost_usd": 0.0, "session_id": "known"})
        text = "\n".join(json.dumps(r) for r in stream) + "\n"
        (case / f"turn{n}.jsonl").write_text(text, encoding="utf-8")
        turns.append(dict(run.parse_stream(text), prompt=prompt, t_start=_at(start, n, 0), t_end=_at(start, n, 300)))
        snaps.append(run.snapshot(proj))
    (case / "transcripts").mkdir()
    (case / "transcripts" / "known.jsonl").write_text("\n".join(json.dumps(r) for r in transcript) + "\n", encoding="utf-8")
    for name, records in agent_files.items():
        path = case / "transcripts" / "known" / "subagents" / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("\n".join(json.dumps(r) for r in records) + "\n", encoding="utf-8")
    (case / "hooks.jsonl").write_text("".join(json.dumps(r) + "\n" for r in log), encoding="utf-8")
    record = {"scenario": scenario["id"], "seat": seat, "turns": turns, "snapshots": snaps, "baseline_tests": baseline,
              "hooks": [list(e) for e in entries], "loader": list(loader), "env": {"known_run": known.get("label")},
              "layer_expected": run.layer_expected(seat, fixture_layers(case / "fixture-doctrine"))}
    (case / "record.json").write_text(json.dumps(record, indent=1), encoding="utf-8")
    return case


# ------------------------------------------------------------------------------------------------ grading

class Book:
    """One selftest pass: every case built once (in a thread pool; the checks are mostly subprocesses), graded
    against the known-run manifest, kept until the pass ends."""

    def __init__(self, spec=None, workers=WORKERS):
        self.spec = spec or load_spec()
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.workers = workers
        self.cases, self.n = {}, 0
        self._saved = run.SEALED
        run.SEALED = reviewed_manifest(self.root)

    def close(self):
        run.SEALED = self._saved
        self.tmp.cleanup()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    def build(self, jobs):
        """{key: case folder} for [(key, scenario, known run)], each built once."""
        todo = [(k, s, r, self.root / f"c{self.n + i}") for i, (k, s, r) in enumerate(
            [(k, s, r) for k, s, r in jobs if k not in self.cases])]
        self.n += len(todo)

        def one(job):
            key, scenario, known, folder = job
            try:
                return key, build_known(scenario, known, folder, self.spec["prompts"])
            except Exception as e:           # a run that cannot be built is a problem, never a pass
                return key, e
        with concurrent.futures.ThreadPoolExecutor(max_workers=self.workers) as pool:
            for key, case in pool.map(one, todo):
                self.cases[key] = case
        return {k: self.cases[k] for k, _, _ in jobs}

    def grade(self, pairs):
        """{key: grade} for [(key, scenario)], graded in the pool."""
        def one(item):
            key, scenario = item
            case = self.cases[key]
            if isinstance(case, Exception):
                return key, {"passed": False, "results": [], "built": f"{type(case).__name__}: {case}"}
            return key, run.grade(case, scenario)
        with concurrent.futures.ThreadPoolExecutor(max_workers=self.workers) as pool:
            return dict(pool.map(one, pairs))


def failing(grade, scenario):
    """The indices of the scenario's own checks that failed (the automatic ones apart)."""
    return {i for i, r in enumerate(grade["results"][:len(scenario["checks"])]) if r["passed"] is False}


def kinds_in(check):
    yield check[0]
    if check[0] == "any_of":
        for inner in check[1:]:
            yield from kinds_in(inner)
    elif check[0] == "after_turn":
        yield from kinds_in(check[2])


def failed_kinds(grade):
    return {k for r in grade["results"] if r["passed"] is False for k in kinds_in(r["check"])}


def _prompts_of(scenario, spec):
    return [spec["prompts"].get(k, k) for k in scenario["turns"]]


def known_jobs(spec):
    return [((s["id"], "known", i), s, dict(k, _prompts=_prompts_of(s, spec)))
            for s in spec["scenarios"] for i, k in enumerate(s.get("known") or [])]


def as_declared(known, g):
    """(True, "") when a graded known run is what it declares: a pass passes; a fail fails on every kind in `fails`
    and on none in `passes` (checks of those kinds must pass, so a run failing more than it says is not as declared)."""
    if known["expect"] == "pass":
        if g["passed"]:
            return True, ""
        bad = [f"{r['check'][0]}: {str(r['detail'])[:120]}" for r in g["results"] if r["passed"] is not True]
        return False, f"expected PASS, got FAIL: {bad[:3]}"
    failed = failed_kinds(g)
    missing = [k for k in known.get("fails") or [] if k not in failed]
    extra = [k for k in known.get("passes") or [] if k in failed]
    if g["passed"] or missing:
        return False, f"expected FAIL on {known.get('fails')}, {'got PASS' if g['passed'] else f'did not fail on {missing}'}"
    if extra:
        return False, f"must pass {', '.join(extra)}, which failed"
    return True, ""


def known_problems(book):
    """Every known run grades as declared: a pass passes; a fail fails on every kind in `fails`; an owned run
    grades short of its final expectation, and an unexpected pass names its owner."""
    problems, owned, ok = [], {}, 0
    jobs = known_jobs(book.spec)
    book.build(jobs)
    grades = book.grade([(key, s) for key, s, _ in jobs])
    for key, scenario, known in jobs:
        g, label = grades[key], known.get("label", "?")
        where = f"{scenario['id']} {label!r}"
        if g.get("built"):
            problems.append(f"{where}: could not be built: {g['built']}")
            continue
        owner = known.get("owned_by")
        if owner is not None and not OWNED_RE.match(str(owner)):
            problems.append(f"{where}: owned_by {owner!r} is not of the form 'R-<n> step <s>'")
            continue
        declared, why = as_declared(known, g)
        if owner:   # it must still grade short of its final expectation; the owning row removes the mark
            if declared:
                problems.append(f"owned run passed: {scenario['id']} {label} — {owner} must remove owned_by in its commit")
            else:
                owned[owner] = owned.get(owner, 0) + 1
        elif declared:
            ok += 1
        else:
            problems.append(f"{where}: {why}")
    return problems, ok, owned


LAYER_RUNS = HERE / "known" / "layer_runs.json"


def layer_run_jobs(spec):
    """R-26 test 26: the `layer` known runs, each a scenario's first good run on a seat with the loader output it
    names (evals/known/layer_runs.json)."""
    by_id = {s["id"]: s for s in spec["scenarios"]}
    jobs = []
    for i, r in enumerate(json.loads(LAYER_RUNS.read_text(encoding="utf-8"))["runs"]):
        scenario = by_id[r["scenario"]]
        base = dict(first_good(scenario), _prompts=_prompts_of(scenario, spec))
        known = dict(base, **{k: v for k, v in r.items() if k != "scenario"})
        jobs.append(((scenario["id"], "layer", i), scenario, known))
    return jobs


def layer_run_problems(book):
    """Each layer known run grades as declared (a pass passes; a fail fails on `layer`)."""
    jobs = layer_run_jobs(book.spec)
    book.build(jobs)
    grades = book.grade([(key, s) for key, s, _ in jobs])
    problems, ok = [], 0
    for key, scenario, known in jobs:
        g, where = grades[key], f"{scenario['id']} layer run {known.get('label', '?')!r}"
        if g.get("built"):
            problems.append(f"{where}: could not be built: {g['built']}")
            continue
        declared, why = as_declared(known, g)
        if declared:
            ok += 1
        else:
            problems.append(f"{where}: {why}")
    return problems, ok


def first_good(scenario):
    return next((k for k in scenario.get("known") or [] if k.get("expect") == "pass" and not k.get("owned_by")), None)


def violation_of(scenario, index, good, spec):
    """(run or None, source) for the scenario's check at index: the declared violation, else the generated one."""
    declared = (scenario.get("violations") or {}).get(str(index))
    base = dict(good, _prompts=_prompts_of(scenario, spec))
    if declared:
        v = copy.deepcopy(base)
        t = v["turns"][-1]
        t["ops"] = list(t.get("ops") or []) + declared.get("ops", [])
        t["bash"] = list(t.get("bash") or []) + declared.get("bash", [])
        return v, "declared"
    return run.violation(scenario["checks"][index], base), "generated"


def violation_jobs(spec):
    jobs, missing = [], []
    for s in spec["scenarios"]:
        good = first_good(s)
        if good is None:
            continue
        for i, c in enumerate(s["checks"]):
            v, source = violation_of(s, i, good, spec)
            if v is None:
                missing.append(f"{s['id']} check {i} {c[0]}: no violation generator and none declared")
            else:
                jobs.append(((s["id"], "violation", i), s, v))
    return jobs, missing


def violation_problems(book):
    """Each check fails on its violation of the scenario's first good run, and exactly with its co_fail set; a
    violation that fails every check of its scenario proves nothing about the one it targets."""
    jobs, problems = violation_jobs(book.spec)
    book.build(jobs)
    grades = book.grade([(key, s) for key, s, _ in jobs])
    table, ok = [], 0
    for key, scenario, _ in jobs:
        i, g = key[2], grades[key]
        if g.get("built"):
            problems.append(f"{scenario['id']} check {i}: violation could not be built: {g['built']}")
            continue
        co = (scenario.get("co_fail") or {}).get(str(i)) or {}
        want = {i} | set(co.get("checks") or [])
        got = failing(g, scenario)
        table.append((scenario["id"], i, scenario["checks"][i], sorted(got - {i}), co.get("why", "")))
        if len(scenario["checks"]) > 1 and got == set(range(len(scenario["checks"]))):
            problems.append(f"{scenario['id']} check {i}: its violation fails every check of the scenario")
        elif got != want:
            problems.append(f"{scenario['id']} check {i} {scenario['checks'][i][0]}: its violation fails {sorted(got)}, "
                            f"declared {sorted(want)}")
        else:
            ok += 1
    return problems, ok, table


def co_fail_problems(spec):
    """Every co_fail entry says why, in one sentence."""
    return [f"{s['id']} co_fail {i}: no why" for s in spec["scenarios"]
            for i, entry in (s.get("co_fail") or {}).items() if not str(entry.get("why") or "").strip()]


def schema_problems(spec):
    """Every scenario's optional `allowed_tools` is well formed, so a malformed grant stops the selftest that runs
    before any paid launch rather than one case mid-batch."""
    return [f"{s['id']}: {run.allowed_tools_error(s['allowed_tools'])}" for s in spec["scenarios"]
            if "allowed_tools" in s and run.allowed_tools_error(s["allowed_tools"])]


def automatic_jobs(spec):
    """Each automatic kind's violation of every scenario's first good run it applies to."""
    jobs = []
    for s in spec["scenarios"]:
        good = first_good(s)
        if good is None:
            continue
        base = dict(good, _prompts=_prompts_of(s, spec))
        for name in run.AUTOMATIC:
            v = run.CHECKS[name].violate([], base, None)
            if v is not None:
                jobs.append(((s["id"], "automatic", name), s, v))
    return jobs


def load_bearing(book, kinds=None):
    """(bearing kinds, the rest): a kind is load-bearing when, its check mutated to always pass in-process, at least
    one bad run — a declared known-bad or a generated violation — flips to PASS."""
    bad = [(k, s, r) for k, s, r in known_jobs(book.spec) if r.get("expect") == "fail" and not r.get("owned_by")]
    bad += violation_jobs(book.spec)[0] + automatic_jobs(book.spec)
    book.build(bad)
    base = book.grade([(k, s) for k, s, _ in bad])
    bearing, rest = [], []
    for kind in sorted(kinds or run.CHECKS):
        candidates = [(k, s) for k, s, _ in bad if not base[k]["passed"] and not base[k].get("built")
                      and kind in failed_kinds(base[k])]
        saved = run.CHECKS[kind]
        run.CHECKS[kind] = saved._replace(fn=lambda args, ctx, snap: (True, "mutated: always passes"))
        try:
            flipped = any(g["passed"] for g in book.grade(candidates).values()) if candidates else False
        finally:
            run.CHECKS[kind] = saved
        (bearing if flipped else rest).append(kind)
    return bearing, rest


# ------------------------------------------------------------------------------------------------ the real failures

def real_failures(spec):
    """(still failing, problems): each kept case of evals/known/real/, rebuilt and graded, fails on its `fails`."""
    real = HERE / "known" / "real"
    index = json.loads((real / "INDEX.json").read_text(encoding="utf-8")) if (real / "INDEX.json").is_file() else {"entries": []}
    by_id, ok, problems = {s["id"]: s for s in spec["scenarios"]}, 0, []
    with tempfile.TemporaryDirectory() as tmp:
        for e in (e for e in index["entries"] if e["kept"]):
            entry = json.loads((real / f"{e['id']}.json").read_text(encoding="utf-8"))
            g = run.grade(run.rebuild_real(entry, Path(tmp) / e["id"]), by_id[entry["scenario"]])
            missing = [k for k in entry["fails"] if k not in failed_kinds(g)]
            if g["passed"] or missing:
                problems.append(f"real failure {e['id']} no longer fails on {missing or entry['fails']}")
            else:
                ok += 1
    return ok, problems


# ------------------------------------------------------------------------------------------------ the end-of-turn texts

# A known run's Stop text is data the graders read, so a contract change in turn_check.py can leave a known-good run
# carrying a hold the hook no longer gives, and the selftest still passes. Each is re-derived here instead.
SCRIPT_CALL_RE = re.compile(r"^python3 tools/[\w.-]+\.py$")   # a fixture script, run as the scenarios' prompts say
COMMIT_SHA_RE = re.compile(r"(committed in )[0-9a-f]{8}")     # a replayed commit has its own hash


def turn_check_module():
    """The pack under test's turn_check.py, imported from whichever layout this copy sits in."""
    folder = str(run.pack_path("doc-guardrails/turn_check.py").parent)
    if folder not in sys.path:
        sys.path.insert(0, folder)
    import turn_check
    return turn_check


def turn_check_hooks(turn):
    """A known turn's Stop hooks that turn_check.py printed."""
    return [h for h in turn.get("hooks") or []
            if h["event"] == "Stop" and run.script_of(h["command"]).endswith("turn_check.py")]


class StopReplay:
    """One session replayed against the real turn_check.py: the scenario's project built as a case's is, each
    call written to a transcript in the CLI's shape with its window, and the Stop hook judged in-process with this
    folder's own state and no other session. `recorded` applies a known turn as it is recorded; `bash` runs a call for
    real in the project, so its output and the times of the files it writes are real. Nothing outside the folder is
    written."""

    def __init__(self, scenario, folder):
        import time
        self._time = time.time
        self.folder = Path(folder)
        self.folder.mkdir(parents=True, exist_ok=True)
        self.proj = run.build_project(self.folder, scenario)
        self.env = dict(run.grader_env(self.folder / "state"), GIT_AUTHOR_NAME="known", GIT_AUTHOR_EMAIL="known@local",
                        GIT_COMMITTER_NAME="known", GIT_COMMITTER_EMAIL="known@local")
        self.fixture_head = _git(self.proj, "rev-parse", "HEAD").stdout.strip()
        self.transcript = self.folder / "session" / "known.jsonl"
        self.transcript.parent.mkdir()
        self.transcript.write_text("", encoding="utf-8")
        self.others = self.folder / "no-other-sessions"
        self.others.mkdir()
        self.n = 0

    def call(self, name, inp, out, start, end):
        import datetime
        stamp = lambda t: datetime.datetime.fromtimestamp(t, datetime.timezone.utc).isoformat().replace("+00:00", "Z")
        self.n += 1
        uid = f"known-{self.n}"
        rows = [{"type": "assistant", "timestamp": stamp(start), "message": {"role": "assistant", "content": [
                    {"type": "tool_use", "id": uid, "name": name, "input": inp}]}},
                {"type": "user", "timestamp": stamp(end), "message": {"role": "user", "content": [
                    {"type": "tool_result", "tool_use_id": uid, "content": out}]}}]
        with open(self.transcript, "a", encoding="utf-8") as fh:
            fh.write("".join(json.dumps(r) + "\n" for r in rows))

    def recorded(self, turn):
        """A turn as its known run records it: its changes applied, its calls with their recorded outputs."""
        start = self._time()
        for op in turn.get("ops") or []:
            apply_op(self.proj, op, self.env, self.fixture_head)
        for b in turn.get("bash") or []:
            self.call("Bash", {"command": b["cmd"]}, b.get("out") or "", start, self._time())
        for w in turn.get("writes") or []:
            self.call("Write", {"file_path": str(self.proj / w), "content": "…"}, "ok", start, self._time())

    def bash(self, cmd, out=None):
        """A Bash call run for real in the project; its result in the transcript is its real output unless `out`
        stands in for it. Returns the real output."""
        start = self._time()
        r = subprocess.run(cmd, shell=True, cwd=self.proj, env=self.env, capture_output=True, encoding="utf-8",
                           errors="replace")
        real = (r.stdout + r.stderr).strip()
        self.call("Bash", {"command": cmd}, real if out is None else out, start, self._time())
        return real

    def stop(self):
        """The Stop hook's block reason, or None when it lets the turn end."""
        out = turn_check_module().check({"session_id": "known", "cwd": str(self.proj), "stop_hook_active": False,
                                         "transcript_path": str(self.transcript)},
                                        self.folder / "turn-check-state", temp_dirs=(), config_dirs=[self.others])
        return json.loads(out).get("reason") if out else None


def stop_text_problems(spec):
    """(re-derived, problems): every known run's Stop text from turn_check.py is what turn_check.py prints for the
    run's shape — its earlier turns as recorded (a Stop after each), then the held turn's Bash calls (its first
    `after_bash`, else all) run for real — and each fixture script it runs records what the script prints."""
    problems, ok = [], 0
    with tempfile.TemporaryDirectory() as tmp:
        for s in spec["scenarios"]:
            for i, known in enumerate(s.get("known") or []):
                held = [n for n, t in enumerate(known["turns"]) if turn_check_hooks(t)]
                if not held:
                    continue
                where = f"{s['id']} {known.get('label', '?')!r}"
                if len(held) > 1 or len(turn_check_hooks(known["turns"][held[0]])) > 1:
                    problems.append(f"{where}: more than one end-of-turn hold; only one per run is re-derived")
                    continue
                n = held[0]
                turn, hook = known["turns"][n], turn_check_hooks(known["turns"][n])[0]
                if hook.get("outcome", "block") != "block":
                    problems.append(f"{where} turn {n + 1}: an end-of-turn hook that is not a block is not re-derived")
                    continue
                try:
                    replay = StopReplay(s, Path(tmp) / f"{s['id']}-{i}")
                    for earlier in known["turns"][:n]:
                        replay.recorded(earlier)
                        replay.stop()
                    calls = turn.get("bash") or []
                    for b in calls[:hook.get("after_bash", len(calls))]:
                        real = replay.bash(b["cmd"])
                        if SCRIPT_CALL_RE.match(b["cmd"]) and real != (b.get("out") or ""):
                            problems.append(f"{where} turn {n + 1}: `{b['cmd']}` prints {real!r}; the run records "
                                            f"{b.get('out')!r}")
                    got = replay.stop()
                except Exception as e:           # a run that cannot be replayed is a problem, never a pass
                    problems.append(f"{where}: could not be replayed: {type(e).__name__}: {e}")
                    continue
                norm = lambda text: COMMIT_SHA_RE.sub(r"\1<sha>", text or "")
                if got is None or norm(got) != norm(hook["text"]):
                    problems.append(f"{where} turn {n + 1}: turn_check.py prints {got!r} for this shape; the run "
                                    f"records {hook['text']!r}")
                else:
                    ok += 1
        printed = {}   # every other run of a fixture script records what it prints too
        source = set(run.pack_path("doc-guardrails/turn_check.py").read_text(encoding="utf-8").splitlines())
        for s in spec["scenarios"]:
            for known in s.get("known") or []:
                for n, t in enumerate(known["turns"], 1):
                    for b in t.get("bash") or []:
                        if b["cmd"].startswith("cat ") and b["cmd"].endswith("/turn_check.py"):
                            stale = [l for l in (b.get("out") or "").splitlines() if l not in source]
                            if stale:   # W-28's shape prints the hook's real source, never a line it no longer has
                                problems.append(f"{s['id']} {known.get('label', '?')!r} turn {n}: `{b['cmd']}` records "
                                                f"{stale[0]!r}, which turn_check.py does not hold")
                            continue
                        if not SCRIPT_CALL_RE.match(b["cmd"]):
                            continue
                        key = (json.dumps(s.get("overlay")), json.dumps(s.get("prep")), b["cmd"])
                        if key not in printed:
                            try:
                                printed[key] = StopReplay(s, Path(tmp) / f"script-{len(printed)}").bash(b["cmd"])
                            except Exception as e:
                                printed[key] = e
                        real = printed[key]
                        where = f"{s['id']} {known.get('label', '?')!r} turn {n}"
                        if isinstance(real, Exception):
                            problems.append(f"{where}: `{b['cmd']}` could not be run: {type(real).__name__}: {real}")
                        elif real != (b.get("out") or "") and not turn_check_hooks(t):
                            problems.append(f"{where}: `{b['cmd']}` prints {real!r}; the run records {b.get('out')!r}")
    return ok, problems


# ------------------------------------------------------------------------------------------------ the quick floor

HOUSE_SKILLS = ("closeout", "project-update", "doc-guardrails", "redman", "greenman", "eli5")


def required(check):
    """A check and what it holds that must pass too: after_turn's inner check is required, any_of's branches are not."""
    yield check
    if check[0] == "after_turn":
        yield from required(check[2])


def backs(item, check):
    """Whether a required check exercises a house skill or an always-on layer (criterion 11)."""
    kind, args = check[0], check[1:]
    if item.startswith("layer:"):
        if item == "layer:commit-gate":
            return kind == "gate_fires" and args[:1] == [True]
        event, script = item[len("layer:"):].split(" ", 1)
        if kind != "hook_fired" or args[0] != event:
            return False
        _, text, outcome, filt, _ = run._hook_args(args)
        suffix = text if outcome == "ran" else filt
        return suffix is None or suffix.endswith(script)
    if item == "eli5":
        return kind == "receipt"
    if item == "doc-guardrails":
        return kind in ("hook_fired", "gate_fires")
    return kind == "skill_invoked" and args[:1] == [item]


def floor_problems(spec, entries=None):
    """--quick is a floor (criterion 11): every house skill and every always-on layer — each entry of the sealed hook
    set keyed on (event, script file name), and the commit gate — is named in the `covers` of a quick case with a
    required check that exercises it. The loader's entry is covered on every case by the automatic `layer` check."""
    entries = list(run.sealed_hook_entries() if entries is None else entries)
    _, found = run.case_hooks({"record": {"hooks": [list(e) for e in entries]}})
    loader = found[0]
    layers = [f"layer:{e[0]} {e[3]}" for e in entries if not (loader and tuple(e) == tuple(loader))]
    quick = [s for s in spec["scenarios"] if s.get("quick")]
    problems = []
    for item in list(HOUSE_SKILLS) + list(dict.fromkeys(layers)) + ["layer:commit-gate"]:
        cases = [s for s in quick if item in (s.get("covers") or [])]
        if not cases:
            problems.append(f"{item}: no quick case covers it")
        elif not any(backs(item, c) for s in cases for top in s["checks"] for c in required(top)):
            problems.append(f"{item}: covered by {', '.join(s['id'] for s in cases)} but no required check backs it")
    return problems


def main(argv=None, quiet=False):
    """Run every proof; print the summary line (or, quiet, nothing but the problems) and return the number of
    problems, 0 when the graders are proven."""
    argv = list(sys.argv[1:] if argv is None else argv)
    spec = load_spec()
    with Book(spec) as book:
        kp, k_ok, owned = known_problems(book)
        lp, l_ok = layer_run_problems(book)
        vp, v_ok, table = violation_problems(book)
        cp = co_fail_problems(spec)
        bearing, rest = load_bearing(book)
    real_ok, rp = real_failures(spec)
    stop_ok, sp = stop_text_problems(spec)
    problems = (kp + lp + vp + cp + rp + sp + floor_problems(spec) + schema_problems(spec)
                + [f"not load-bearing: {k}" for k in rest])
    if "--co-fail" in argv:
        print("scenario | check | violation fails also | why")
        for sid, i, c, also, why in table:
            print(f"{sid} | {i} {json.dumps(c, ensure_ascii=False)} | {also or '—'} | {why or '—'}")
    owners = ", ".join(f"{o}: {n}" for o, n in sorted(owned.items()))
    if problems:
        for p in problems:
            print(p)
    elif not quiet:
        print(f"selftest: {k_ok} known runs as declared · {sum(owned.values())} owned-expected"
              f"{f' ({owners})' if owners else ''} · {l_ok} layer runs as declared · {v_ok} checks fail on their violation · "
              f"{len(bearing)} of {len(run.CHECKS)} kinds load-bearing · {real_ok} real failures still fail · "
              f"{stop_ok} end-of-turn holds re-derived · 0 problems")
    return len(problems)


if __name__ == "__main__":
    sys.exit(1 if main() else 0)
