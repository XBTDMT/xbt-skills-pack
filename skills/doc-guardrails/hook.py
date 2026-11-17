#!/usr/bin/env python3
"""doc-guardrails, the per-write layer: a PostToolUse hook on Write|Edit. Reads the tool input from stdin, and when
the file written is a markdown document of a linked kind with no `roadmap:` line (or a broken one, or a bare row id
the checker does not read), says so on stderr and exits 2 so the session sees it while the work is still in hand.
Silent otherwise (exit 0): not markdown, the file is gone, no roadmap in this project, `guardrails: off` in the doc
map, or the file is linked. Never edits anything.

The verdict is closeout.py's (`link-audit <the file's folder> --file <the file>`), run as its own process by the
interpreter that runs this hook, so nothing of closeout is loaded for a write that is not a document. Budget: two
python starts, and only one when the file is not an existing `.md`. The helper sits beside this script's own real
folder, wherever the skills were installed; failing that, in `CLAUDE_CONFIG_DIR` (else `~/.claude`) `skills/closeout`.
A helper that is nowhere is one stderr line and exit 0. This layer is optional: the end-of-turn check sees every
document however it was written. `hook.sh` beside this file is the older entry point and runs this one.

No shell and no POSIX-only call, so it runs on Windows too. Stdlib only, Python 3.9+.
"""
import json
import os
import shutil
import subprocess
import sys

NOT_FOUND = "doc-guardrails: closeout.py not found next to the hook or in the skills folder\n"


def written_file(raw):
    """`tool_input.file_path` of the PostToolUse JSON in `raw` (bytes), as text; "" for anything else: empty or
    invalid input, a payload that is not an object, no tool input."""
    try:
        return str(json.loads(raw).get("tool_input", {}).get("file_path", "")).rstrip("\n")
    except Exception:
        return ""


def folder(file, path=os.path):
    """The folder of `file` as the audit's root argument; "." for a bare file name."""
    return path.dirname(file) or "."


def helper(here, environ=None, path=os.path):
    """closeout.py: beside the hook's own real folder first, then in the Claude Code folder's skills; None if neither."""
    environ = os.environ if environ is None else environ
    config = environ.get("CLAUDE_CONFIG_DIR") or path.join(path.expanduser("~"), ".claude")
    for found in (path.join(here, "..", "closeout", "closeout.py"),
                  path.join(config, "skills", "closeout", "closeout.py")):
        if path.isfile(found):
            return found
    return None


def interpreter():
    """The Python that runs the audit: the one running this hook; without one, /usr/bin/python3, then PATH's."""
    if sys.executable:
        return sys.executable
    if os.access("/usr/bin/python3", os.X_OK):
        return "/usr/bin/python3"
    return shutil.which("python3") or shutil.which("python")


def say(data):
    """`data` (bytes) and a newline on stderr, as bytes where the stream has a byte layer."""
    data = data + b"\n"
    try:
        sys.stderr.flush()
        sys.stderr.buffer.write(data)
        sys.stderr.buffer.flush()
    except AttributeError:
        sys.stderr.write(data.decode("utf-8", "replace"))


def main(raw):
    file = written_file(raw)
    if not file.endswith(".md") or not os.path.isfile(file):
        return 0
    script = helper(os.path.dirname(os.path.realpath(__file__)))
    if script is None:
        # through say(), as bytes: a text-mode write turns the line's "\n" into "\r\n" on Windows, so the one line
        # this hook prints itself would differ by platform (public CI run 44024678005, both Windows jobs)
        say(NOT_FOUND.rstrip("\n").encode("utf-8"))
        return 0
    python = interpreter()
    if not python:
        return 0
    try:
        r = subprocess.run([python, script, "link-audit", folder(file), "--file", file], stdin=subprocess.DEVNULL,
                           stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    except OSError:
        return 0
    out = r.stdout.replace(b"\r\n", b"\n").rstrip(b"\n")
    if r.returncode == 2 and out:
        say(out)
        return 2
    return 0


if __name__ == "__main__":
    try:
        data = sys.stdin.buffer.read() if sys.stdin is not None else b""
    except Exception:
        data = b""
    sys.exit(main(data))
