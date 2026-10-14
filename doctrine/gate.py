#!/usr/bin/env python3
"""SessionStart hook: print the doctrine layer for the session's model, which Claude Code adds to the context.

The layer comes from one table (MEASURED and FAMILY_LAYER below; `gate.py --table` prints it):
  a measured model id           -> its measured layer (Opus 5.5 -> opus-5-5-layer.md; Opus 5 and Sonnet 5 ->
                                   opus-layer.md; Fable 5.1 -> fable-layer.md)
  another version of a family   -> the layer of that family's nearest measured version at or below it, else of its
  with measured rows               lowest (a newer Opus -> the 5.5 layer; Opus 4.8 -> opus-layer.md)
  Haiku                         -> opus-layer.md
  undetected or unrecognised    -> opus-5-5-layer.md, and its header says so
The layer's first line is rewritten to name the model the session really runs ("an undetected model" when none is
found, the string as found when it names no Claude model); no other byte of the layer changes. An alias (`opus`,
`sonnet`, `haiku`, `fable`, `best`, `opusplan`, `opus[1m]`) is resolved as Claude Code resolves it, honouring
ANTHROPIC_DEFAULT_<FAMILY>_MODEL; ALIASES_FROM names the Claude Code version the defaults were read from.

The model comes from, in order: the hook's stdin JSON (`model` — interactive sessions send it, headless `claude -p`
sessions do not), the DOCTRINE_MODEL environment variable (the `cc` launcher sets it), the last `--model` on the
session process's command line (CLAUDE_PID; macOS and Linux), the daemon's record of a background session (its job
state, or the roster worker whose replPid or session id is this session: its flags, or the transcript it resumed; a
resumed session is a fork with no model anywhere else), the last reply model in the stdin `transcript_path`,
ANTHROPIC_MODEL, then `model` in the user's settings.json. Without the command-line step, every headless
`claude -p --model <fable>` session got the Opus layer.

DOCTRINE=off (any case) injects nothing. DOCTRINE_LAYER=<file> forces one layer whatever the model: a bare name is
looked up next to this script, anything else is a path; its header still names the real model. A chosen or forced
layer that is missing or unreadable injects NOTHING, says so in one stderr line and exits 1, rather than falling back
to a layer nobody asked for.

  gate.py                          the hook (what settings.json runs)
  gate.py --table                  the model table, as JSON
  gate.py --resolve MODEL [...]    each model's id, layer and header, as JSON
  gate.py --status [N] [--json] [--transcript PATH ...]
                                   which layer the N latest sessions really got, read from their injection records
  gate.py --doctor                 the table checked: every layer present, its header parsed, the alias defaults' age

When XBT_EVAL_HOOK_LOG names a file (only the evals set it), a run of the hook appends one JSON line to it
({event, script, exit, stdout_sha256, t}), unless the pack's tolerant launcher ran it and writes that line itself.

Stdlib only, Python 3.9 or newer, Windows, macOS and Linux. Layers are found next to this file (next to the link, when
it is run through one), so the hook command needs no $HOME or %USERPROFILE%.
"""
import json
import os
import re
import sys
from pathlib import Path

HERE = Path(os.path.abspath(__file__)).parent   # never .resolve(): run through a link, the layers are the link's

# The model table. A new model is one row here (and, for the doctrine harness, its arms).
MEASURED = {"claude-opus-5": "opus-layer.md", "claude-opus-5-5": "opus-5-5-layer.md",
            "claude-fable-5-1": "fable-layer.md", "claude-sonnet-5": "opus-layer.md"}    # the measured pairings
FAMILY_LAYER = {"haiku": "opus-layer.md"}                                              # families with no measured row
DEFAULT_LAYER = "opus-5-5-layer.md"                                                    # undetected or unrecognised
ALIASES = {"opus": "claude-opus-5-5", "sonnet": "claude-sonnet-5", "haiku": "claude-haiku-4-5",
           "fable": "claude-fable-5-1", "best": "fable", "opusplan": "opus"}
ALIASES_FROM = "2.1.283"                        # the Claude Code version the alias defaults were read from
ALIAS_ENV = "ANTHROPIC_DEFAULT_<FAMILY>_MODEL"
ALIAS_FAMILIES = ("opus", "sonnet", "haiku", "fable")   # the family aliases Claude Code lets that variable override
FAMILY_NAMES = "opus|sonnet|haiku|fable"
ID_RE = re.compile(rf"claude-({FAMILY_NAMES})-(\d+)(?:-(\d{{1,2}}))?(?![0-9])")
NAME_RE = re.compile(rf"^(?:claude\s+)?({FAMILY_NAMES})\s+(\d+)(?:\.(\d{{1,2}}))?$")   # a header's name, "Claude Opus 5.5"
HEADER_RE = re.compile(r"^(#.*\(injected: this session runs )([^)]*)(\).*)$")
UNDETECTED = "an undetected model"
GATE_RECORD_RE = re.compile(r"doctrine[\\/]+gate\.(py|sh)\b")


def config_dir():
    return Path(os.environ["CLAUDE_CONFIG_DIR"]).expanduser() if os.environ.get("CLAUDE_CONFIG_DIR") \
        else Path.home() / ".claude"


# ------------------------------------------------------------------------------------------------ detection

def process_model(pid):
    """The last --model on the session process's command line, or "". `ps` prints arguments unquoted, prompt text
    included, so split on whitespace (a model name has no spaces). Not available on Windows."""
    if not str(pid).isdigit() or os.name == "nt":
        return ""
    import subprocess
    try:
        args = subprocess.run(["ps", "-o", "args=", "-p", str(pid)], capture_output=True, encoding="utf-8",
                              errors="replace").stdout.split()
    except OSError:
        return ""
    return flag_model(args)


def flag_model(args):
    model = ""
    for i, a in enumerate(args):  # the last --model wins, as on the command line
        if a == "--model" and i + 1 < len(args):
            model = args[i + 1]
        elif isinstance(a, str) and a.startswith("--model="):
            model = a.split("=", 1)[1]
    return model


def transcript_model(path):
    """The model of the last assistant message in a transcript (read from its last 2 MB), or ""."""
    try:
        with open(path, "rb") as fh:
            fh.seek(0, 2)
            fh.seek(max(0, fh.tell() - 2_000_000))
            hits = re.findall(rb'"model":\s*"(claude-[a-z0-9.-]+)"', fh.read())
        return hits[-1].decode() if hits else ""
    except (OSError, TypeError, ValueError):
        return ""


def daemon_model(data):
    """A background session's model from Claude Code's own records. Internal files, read only; anything missing or in
    another shape returns "" and the next source is tried."""
    try:
        return flag_model(json.loads((Path(os.environ["CLAUDE_JOB_DIR"]) / "state.json").read_text(encoding="utf-8")).get("respawnFlags") or [])
    except (KeyError, OSError, ValueError, AttributeError):
        pass
    pid = os.environ.get("CLAUDE_PID", "")
    sid = data.get("session_id")
    try:
        workers = json.loads((config_dir() / "daemon" / "roster.json").read_text(encoding="utf-8")).get("workers") or []
    except (OSError, ValueError, AttributeError):
        return ""
    for w in (workers.values() if isinstance(workers, dict) else workers):
        if not isinstance(w, dict):
            continue
        if not ((pid and pid in (str(w.get("replPid")), str(w.get("pid")))) or (sid and w.get("sessionId") == sid)):
            continue
        dispatch = w.get("dispatch") or {}
        launch = dispatch.get("launch") or {}
        model = flag_model((launch.get("flagArgs") or []) + (dispatch.get("respawnFlags") or []))
        if not model and launch.get("transcriptPath"):
            model = transcript_model(launch["transcriptPath"])
        if model:
            return model
    return ""


def settings_model():
    try:
        model = json.loads((config_dir() / "settings.json").read_text(encoding="utf-8")).get("model") or ""
    except (OSError, ValueError, AttributeError):
        return ""
    return model if isinstance(model, str) else ""


def session_model(data):
    """The raw model string from the first source that has one, or "" (the detection chain, in order)."""
    model = data.get("model")
    if isinstance(model, dict):
        model = model.get("id") or model.get("display_name") or ""
    sources = (lambda: os.environ.get("DOCTRINE_MODEL", ""),
               lambda: process_model(os.environ.get("CLAUDE_PID", "")),
               lambda: daemon_model(data),
               lambda: transcript_model(data["transcript_path"]) if data.get("transcript_path") else "",
               lambda: os.environ.get("ANTHROPIC_MODEL", ""),
               settings_model)
    model = model if isinstance(model, str) else ""
    for source in sources:
        if model.strip():
            break
        model = source() or ""
    return model.strip()


# ------------------------------------------------------------------------------------------------ the table

def _plain(text):
    """Lower case, trimmed, a trailing `[...]` (the context-window suffix, `opus[1m]`) dropped."""
    return re.sub(r"\[[^\]]*\]$", "", (text or "").strip().lower()).strip()


def canonical(raw):
    """(id, family, version, how) for a raw model string. `how` is `id`, `alias`, `unknown` (a string with no Claude
    id in it; the id is the string as found) or `undetected` (empty; the id is "")."""
    text, how = _plain(raw), "id"
    if not text:
        return "", "", (), "undetected"
    for _ in range(3):                                     # an alias resolves up to three hops (best -> fable -> id)
        if text not in ALIASES:
            break
        how = "alias"
        override = os.environ.get(f"ANTHROPIC_DEFAULT_{text.upper()}_MODEL", "") if text in ALIAS_FAMILIES else ""
        text = _plain(override) if override.strip() else ALIASES[text]
    m = ID_RE.search(text) or NAME_RE.match(text)
    if not m:
        return (raw or "").strip(), "", (), "unknown"
    family, major, minor = m.group(1), m.group(2), m.group(3)
    ident = f"claude-{family}-{major}" + (f"-{minor}" if minor is not None else "")
    return ident, family, (int(major), int(minor or 0)), how


def choose(ident, family, version):
    """The layer file for a canonical model: its measured row; else, in a family with measured rows, the nearest
    measured version at or below it, else that family's lowest; else the family's layer; else the default."""
    if ident in MEASURED:
        return MEASURED[ident]
    rows = sorted(((int(m.group(2)), int(m.group(3) or 0)), layer) for m, layer in
                  ((ID_RE.fullmatch(i), layer) for i, layer in MEASURED.items()) if m and m.group(1) == family)
    if rows and version:
        below = [row for row in rows if row[0] <= version]
        return (below[-1] if below else rows[0])[1]
    return FAMILY_LAYER.get(family, DEFAULT_LAYER)


def header_name(ident, how):
    """The name the header gives: "Claude <Family> <major>[.<minor>]", the undetected phrase, or the string as found."""
    if how == "undetected":
        return UNDETECTED
    m = ID_RE.fullmatch(ident)
    if how == "unknown" or not m:
        return ident
    return f"Claude {m.group(1).capitalize()} {m.group(2)}" + (f".{m.group(3)}" if m.group(3) is not None else "")


def resolve(raw):
    """What the table gives one model string (the `--resolve` schema)."""
    ident, family, version, how = canonical(raw)
    return {"input": raw, "id": ident, "family": family, "how": how, "layer": choose(ident, family, version),
            "header": header_name(ident, how)}


def table():
    return {"schema": 1, "measured": MEASURED, "family_layer": FAMILY_LAYER, "default": DEFAULT_LAYER,
            "aliases": ALIASES, "aliases_from": ALIASES_FROM, "alias_env": ALIAS_ENV}


# ------------------------------------------------------------------------------------------------ rendering

def render_bytes(data, name):
    """The layer's bytes with its first line's model replaced by `name`; a first line that does not parse is passed
    through unchanged. Every other byte is the file's own."""
    head, sep, rest = data.partition(b"\n")
    try:
        line = head.decode("utf-8")
    except UnicodeDecodeError:
        return data
    cr = "\r" if line.endswith("\r") else ""
    m = HEADER_RE.match(line[:-1] if cr else line)
    if not m:
        return data
    return (m.group(1) + name + m.group(3) + cr).encode("utf-8") + sep + rest


def hook(stdin_bytes, out, err):
    """The hook: the rendered layer written to `out` (bytes), exit 0; a missing or unreadable layer: nothing on `out`,
    one line on `err`, exit 1."""
    if os.environ.get("DOCTRINE", "on").strip().lower() == "off":
        return 0
    try:
        text = stdin_bytes.decode("utf-8", "replace")
        data = json.loads(text) if text.strip() else {}
    except ValueError:
        data = {}
    r = resolve(session_model(data if isinstance(data, dict) else {}))
    forced = os.environ.get("DOCTRINE_LAYER")
    path = (Path(forced) if ("/" in forced or "\\" in forced) else HERE / forced) if forced else HERE / r["layer"]
    try:
        body = path.read_bytes()
    except OSError:
        err.write(f"gate.py: layer {path} is missing or unreadable; injecting nothing\n")
        return 1
    out.write(render_bytes(body, r["header"]))
    return 0


# ------------------------------------------------------------------------------------------------ the case hook log

class _Digest:
    """A binary stream that passes every write through and keeps a SHA-256 of what was written."""

    def __init__(self, stream):
        import hashlib
        self.stream, self.sha = stream, hashlib.sha256()

    def write(self, data):
        self.sha.update(data)
        return self.stream.write(data)


def log_line(path, event, script, code, digest):
    """One line appended to the case hook log in one O_APPEND write; a failure costs one stderr line, never the hook."""
    import datetime
    t = datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")
    line = json.dumps({"event": event, "script": os.path.realpath(script), "exit": code, "stdout_sha256": digest,
                       "t": t}, ensure_ascii=False) + "\n"
    try:
        fd = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o644)
        try:
            os.write(fd, line.encode("utf-8"))
        finally:
            os.close(fd)
    except OSError as e:
        sys.stderr.write(f"hook log not written: {e}\n")


# ------------------------------------------------------------------------------------------------ --status

def layer_files():
    """(name, body bytes) of the layers beside this file, then the dated ones under harness/live/layers/ (so a live
    file wins over its identical dated copy)."""
    found = []
    for folder in (HERE, HERE / "harness" / "live" / "layers"):
        try:
            paths = sorted(p for p in folder.iterdir() if p.suffix == ".md" and p.is_file())
        except OSError:
            continue
        for p in paths:
            try:
                found.append((p.name, p.read_bytes().replace(b"\r\n", b"\n").partition(b"\n")[2]))
            except OSError:
                pass
    return found


def identify(stdout, layers=None):
    """(header model or None, layer file or None, why) for an injected text: the model its first line names, and the
    layer file whose body (the text after the first line) equals the injected body. Importable by any reader."""
    if not stdout:
        return None, None, "nothing injected"
    head, _, body = stdout.replace("\r\n", "\n").partition("\n")
    m = HEADER_RE.match(head)
    model, body = (m.group(2) if m else None), body.encode("utf-8")
    for name, text in (layer_files() if layers is None else layers):
        if text == body:
            return model, name, ""
    return model, None, "text matches no layer file"


def _records(path):
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            for line in fh:
                try:
                    rec = json.loads(line)
                except ValueError:
                    continue
                if isinstance(rec, dict):
                    yield rec
    except OSError:
        return


def read_session(path):
    """(the latest SessionStart injection record of the loader, the latest reply model) of one transcript."""
    latest, reply = None, None
    for rec in _records(path):
        if rec.get("type") == "attachment":
            att = rec.get("attachment")
            if not isinstance(att, dict) or att.get("hookEvent") != "SessionStart":
                continue
            command = " ".join([str(att.get("command") or "")] + [str(a) for a in att.get("args") or []])
            if GATE_RECORD_RE.search(command):
                latest = dict(att, timestamp=rec.get("timestamp"))
        elif rec.get("type") == "assistant":
            model = (rec.get("message") or {}).get("model") or ""
            if isinstance(model, str) and model.startswith("claude-") and "haiku" not in model:
                reply = model   # Haiku skipped: background calls are recorded as Haiku
    return latest, reply


def _when(ts, path):
    import datetime
    try:
        t = datetime.datetime.fromisoformat(str(ts).replace("Z", "+00:00")).astimezone()
    except (TypeError, ValueError):
        try:
            t = datetime.datetime.fromtimestamp(os.path.getmtime(path))
        except OSError:
            return "--    --:--"
    return t.strftime("%m-%d %H:%M")


def session_status(path, layers):
    rec, reply = read_session(path)
    out = {"transcript": str(path), "project": f"{Path(path).parent.name}/{Path(path).name}", "reply": reply,
           "record": rec is not None, "hookName": None, "header": None, "layer": None, "exit": None, "stderr": None,
           "why": "no injection record", "flags": [], "time": _when(None, path)}
    if rec is None:
        return out
    stdout = rec.get("stdout")
    if stdout is None:     # an older record: its content, unless that is only the persisted preview
        content = str(rec.get("content") or "")
        stdout = "" if content.startswith("<persisted-output>") else content
    model, layer, why = identify(stdout, layers)
    out.update(hookName=rec.get("hookName"), header=model, layer=layer, exit=rec.get("exitCode"),
               stderr=(rec.get("stderr") or "").strip() or None, why=why, time=_when(rec.get("timestamp"), path))
    if model == UNDETECTED:
        out["flags"].append("UNDETECTED")
    elif model and reply and canonical(model)[0] != canonical(reply)[0]:
        out["flags"].append("MISMATCH")
    return out


def status_line(s):
    if not s["record"]:
        return f"{s['time']}  no injection record  reply {s['reply'] or 'unknown'}  {s['project']}"
    if s["why"] == "nothing injected":
        what = f"nothing injected  exit {s['exit']}" + (f"  stderr: {s['stderr']}" if s["stderr"] else "")
    else:
        what = f"{s['header'] or '(header unparsed)'}  {s['layer'] or s['why']}  exit {s['exit']}"
    flags = "".join(f"  {f}" for f in s["flags"])
    return f"{s['time']}  {s['hookName']}  {what}  reply {s['reply'] or 'unknown'}{flags}  {s['project']}"


def launcher_report(settings):
    """[(event, script name, "current" | "stale")] for the settings file's launcher-form entries, each against the
    entry today's `launched()` builds (the pack's install.py, found beside the real gate.py's folder); None when that
    installer cannot be read (an installed copy of gate.py has none beside it)."""
    import importlib.util
    installer = Path(os.path.realpath(__file__)).parent.parent / "install.py"
    try:
        spec = importlib.util.spec_from_file_location("xbt_gate_status_install", installer)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        launched = module.launched
    except (OSError, AttributeError, ImportError, SyntaxError):
        return None
    rows = []
    for event, groups in (settings.get("hooks") or {}).items():
        for group in groups or []:
            for h in (group.get("hooks") or []) if isinstance(group, dict) else []:
                args = [str(a) for a in h.get("args") or []]
                if args[:1] != ["-c"] or len(args) < 3:
                    continue
                want = launched(h.get("command"), args[2], h.get("timeout"), args=tuple(args[3:]))
                rows.append((event, re.split(r"[\\/]", args[2])[-1], "current" if want["args"][1] == args[1] else "stale"))
    return rows


def status(argv):
    n, as_json, paths, taking = 5, False, [], False
    for a in argv:
        if a == "--json":
            as_json, taking = True, False
        elif a == "--transcript":
            taking = True
        elif taking:
            paths.append(a)
        elif a.isdigit():
            n = int(a)
        else:
            return usage()
    if not paths:
        root = config_dir() / "projects"
        found = [p for p in root.rglob("*.jsonl") if "subagents" not in p.parts] if root.is_dir() and n else []
        paths = [str(p) for p in sorted(found, key=os.path.getmtime, reverse=True)[:n]]
    layers = layer_files()
    sessions = [session_status(p, layers) for p in paths]
    default = settings_model()
    gate_now = []
    for model in [default] + sorted({s["reply"] for s in sessions if s["reply"]} - {default}):
        r = resolve(model)
        gate_now.append({"model": model or "undetected", "header": r["header"], "layer": r["layer"]})
    try:
        settings = json.loads((config_dir() / "settings.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        settings = {}
    launchers = launcher_report(settings if isinstance(settings, dict) else {})
    if as_json:
        print(json.dumps({"sessions": sessions, "gate_now": gate_now,
                          "launchers": None if launchers is None else
                          [{"event": e, "script": s, "state": st} for e, s, st in launchers]},
                         ensure_ascii=False, indent=1))
        return 0
    for s in sessions:
        print(status_line(s))
    print()
    for g in gate_now:
        print(f"gate now: {g['model']} → {g['header']} · {g['layer']}")
    if launchers is None:
        print("launcher: not checked (no install.py beside this gate.py's folder)")
    for event, script, state in launchers or []:
        print(f"launcher: {event} {script} {state}")
    return 0


# ------------------------------------------------------------------------------------------------ --doctor

def doctor():
    import shutil
    import subprocess
    fails = 0
    claude = shutil.which("claude")
    if claude:
        try:
            words = subprocess.run([claude, "--version"], capture_output=True, encoding="utf-8", errors="replace",
                                   timeout=60).stdout.split()
        except (OSError, subprocess.SubprocessError):
            words = []
        version = words[0] if words else "unknown"
        if version != ALIASES_FROM:
            print(f"WARN  Claude Code {version}; the alias defaults were read from {ALIASES_FROM}: re-read the alias "
                  f"defaults, RUNBOOK step 1")
        else:
            print(f"ok    Claude Code {version} is ALIASES_FROM")
    else:
        print(f"WARN  claude is not on PATH: the alias defaults ({ALIASES_FROM}) are not checked against it")
    for name in sorted(set(MEASURED.values()) | set(FAMILY_LAYER.values()) | {DEFAULT_LAYER}):
        path = HERE / name
        try:
            head = path.read_bytes().partition(b"\n")[0].decode("utf-8").rstrip("\r")
        except (OSError, UnicodeDecodeError):
            print(f"FAIL  {path}: missing or unreadable")
            fails += 1
            continue
        if HEADER_RE.match(head):
            print(f"ok    {name}")
        else:
            print(f"FAIL  {path}: its first line does not parse as a header: {head[:80]!r}")
            fails += 1
    for alias in ALIASES:
        r = resolve(alias)
        print(f"alias {alias} -> {r['id']} · {r['layer']} · {r['header']}")
    return 1 if fails else 0


# ------------------------------------------------------------------------------------------------ entry

def usage():
    sys.stderr.write("usage: gate.py [--table | --resolve MODEL [MODEL ...] | --status [N] [--json] "
                     "[--transcript PATH ...] | --doctor]\n")
    return 2


def main(argv):
    if not argv:
        stdin = sys.stdin.buffer.read() if getattr(sys.stdin, "buffer", None) is not None else b""
        out = sys.stdout.buffer if getattr(sys.stdout, "buffer", None) is not None else sys.stdout
        log = os.environ.get("XBT_EVAL_HOOK_LOG", "")
        if not log or globals().get("__xbt_launched__"):   # under the launcher, it writes the one log line
            code = hook(stdin, out, sys.stderr)
            sys.stdout.flush()
            return code
        digest = _Digest(out)
        code = hook(stdin, digest, sys.stderr)
        sys.stdout.flush()
        try:
            event = json.loads(stdin.decode("utf-8", "replace")).get("hook_event_name") or ""
        except (ValueError, AttributeError):
            event = ""
        log_line(log, event if isinstance(event, str) else "", __file__, code, digest.sha.hexdigest())
        return code
    if argv == ["--table"]:
        print(json.dumps(table(), ensure_ascii=False, indent=1))
        return 0
    if argv[0] == "--resolve" and len(argv) > 1:
        print(json.dumps([resolve(m) for m in argv[1:]], ensure_ascii=False, indent=1))
        return 0
    if argv[0] == "--status":
        return status(argv[1:])
    if argv == ["--doctor"]:
        return doctor()
    return usage()


if __name__ == "__main__":
    # Windows consoles and pipes default to a legacy code page; the layers are UTF-8 (em dashes, curly quotes).
    for stream in (sys.stdin, sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    sys.exit(main(sys.argv[1:]))
