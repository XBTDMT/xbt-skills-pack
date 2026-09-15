#!/usr/bin/env python3
"""SessionStart hook: print the doctrine layer for the session's model, which Claude Code adds to the context.

  Opus / Sonnet / Haiku / other -> opus-layer.md   (its first line is rewritten to name the real model)
  Fable                         -> fable-layer.md

The model comes from, in order: the hook's stdin JSON (`model` — interactive sessions send it, headless `claude -p`
sessions do not), the DOCTRINE_MODEL environment variable (the `cc` launcher sets it), the last `--model` on the
session process's command line (CLAUDE_PID; macOS and Linux), the daemon's record of a background session (its job
state, or the roster worker whose replPid or session id is this session: its flags, or the transcript it resumed; a
resumed session is a fork with no model anywhere else), ANTHROPIC_MODEL, then `model` in the user's settings.json. None of those: the Opus layer, naming Claude Opus 5. Without the command-line step, every headless
`claude -p --model <fable>` session got the Opus layer.

DOCTRINE=off injects nothing. DOCTRINE_LAYER=<file> forces one layer whatever the model: a bare name is looked
up next to this script, anything else is a path. An unreadable DOCTRINE_LAYER injects NOTHING and says so on
stderr, rather than falling back to a layer nobody asked for.

Stdlib only, Python 3.9 or newer, Windows, macOS and Linux. Layers are found next to this file, so the hook
command needs no $HOME or %USERPROFILE%.
"""
import json
import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
HEADER_MODEL = "Claude Opus 5"  # the name opus-layer.md's first line is written with


def config_dir():
    return Path(os.environ["CLAUDE_CONFIG_DIR"]).expanduser() if os.environ.get("CLAUDE_CONFIG_DIR") \
        else Path.home() / ".claude"


def process_model(pid):
    """The last --model on the session process's command line, or "". `ps` prints arguments unquoted, prompt text
    included, so split on whitespace (a model name has no spaces). Not available on Windows."""
    if not str(pid).isdigit() or os.name == "nt":
        return ""
    import subprocess
    try:
        args = subprocess.run(["ps", "-o", "args=", "-p", str(pid)], capture_output=True, text=True).stdout.split()
    except OSError:
        return ""
    model = ""
    for i, a in enumerate(args):
        if a == "--model" and i + 1 < len(args):
            model = args[i + 1]
        elif a.startswith("--model="):
            model = a.split("=", 1)[1]
    return model


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
    import re
    try:
        with open(path, "rb") as fh:
            fh.seek(0, 2)
            fh.seek(max(0, fh.tell() - 2_000_000))
            hits = re.findall(rb'"model":\s*"(claude-[a-z0-9.-]+)"', fh.read())
        return hits[-1].decode() if hits else ""
    except OSError:
        return ""


def daemon_model(data):
    """A background session's model from Claude Code's own records. Internal files, read only; anything missing or in
    another shape returns "" and the next source is tried."""
    try:
        return flag_model(json.loads((Path(os.environ["CLAUDE_JOB_DIR"]) / "state.json").read_text()).get("respawnFlags") or [])
    except (KeyError, OSError, ValueError, AttributeError):
        pass
    pid = os.environ.get("CLAUDE_PID", "")
    sid = data.get("session_id") if isinstance(data, dict) else None
    try:
        workers = json.loads((config_dir() / "daemon" / "roster.json").read_text()).get("workers") or []
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


def session_model(stdin_text):
    try:
        data = json.loads(stdin_text) if stdin_text.strip() else {}
    except ValueError:
        data = {}
    model = data.get("model") if isinstance(data, dict) else None
    if isinstance(model, dict):
        model = model.get("id") or model.get("display_name") or ""
    if not model:
        model = os.environ.get("DOCTRINE_MODEL", "")
    if not model:
        model = process_model(os.environ.get("CLAUDE_PID", ""))
    if not model:
        model = daemon_model(data)
    if not model:
        model = os.environ.get("ANTHROPIC_MODEL", "")
    if not model:
        try:
            model = json.loads((config_dir() / "settings.json").read_text(encoding="utf-8")).get("model") or ""
        except (OSError, ValueError, AttributeError):
            model = ""
    return str(model).lower()


def display_name(model):
    # A Sonnet session told it "runs Claude Opus 5" read that as a mismatch and refused the task, so the
    # header names the model that is really running.
    for key, name in (("sonnet", "Claude Sonnet 5"), ("haiku", "Claude Haiku"), ("fable", "Claude Fable 5.1"),
                      ("opus", "Claude Opus 5")):
        if key in model:
            return name
    return model or "Claude Opus 5"


def render(layer, model):
    text = layer.read_text(encoding="utf-8")
    first, sep, rest = text.partition("\n")
    return first.replace(HEADER_MODEL, display_name(model)) + sep + rest


def main(stdin_text, out=sys.stdout, err=sys.stderr):
    if os.environ.get("DOCTRINE", "on").lower() == "off":
        return 0
    model = session_model(stdin_text)
    forced = os.environ.get("DOCTRINE_LAYER")
    if forced:
        layer = Path(forced) if ("/" in forced or "\\" in forced) else HERE / forced
        if not layer.is_file():
            print(f"gate.py: DOCTRINE_LAYER={forced} is not readable; injecting nothing", file=err)
            return 0
    else:
        layer = HERE / ("fable-layer.md" if "fable" in model else "opus-layer.md")
    out.write(render(layer, model))
    return 0


if __name__ == "__main__":
    # Windows consoles and pipes default to a legacy code page; the layers are UTF-8 (em dashes, curly quotes).
    for stream in (sys.stdin, sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    sys.exit(main(sys.stdin.read()))
