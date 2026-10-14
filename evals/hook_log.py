#!/usr/bin/env python3
"""The hook log's pass-through runner (R-22): runs one hook entry and records that it ran.

  hook_log.py --event <E> --script <script path> -- <the entry's command…>

The sealed eval config writes every hook entry through this runner (until R-26's launcher writes the log itself). It
runs the entry's own command with the same stdin, environment and working folder, copies its stdout and stderr
through byte for byte and exits with its exit code, so the session sees exactly what it would see unwrapped; the
entry's command stays last, so the command a transcript records still ends with the script.

When XBT_EVAL_HOOK_LOG names a file (run_case sets it to <case>/hooks.jsonl), it appends one JSON line per run, in one
O_APPEND write: {"event", "script" (its real path), "exit", "stdout_sha256" (of the exact bytes printed), "t" (UTC,
when the hook finished)}. The grader attributes each hook output in the stream to its script by that digest. When the
variable is unset (every session outside the evals) nothing is written. Stdlib only, Python 3.9 or newer.
"""
import datetime
import hashlib
import json
import os
import subprocess
import sys


def parse(argv):
    """(event, script, command) from `--event E --script S -- cmd…`; a usage error names what is missing."""
    if "--" not in argv:
        raise SystemExit("hook_log.py: usage: --event <E> --script <path> -- <command…>")
    head, command = argv[:argv.index("--")], argv[argv.index("--") + 1:]
    opts = dict(zip(head[::2], head[1::2]))
    if len(head) % 2 or set(opts) != {"--event", "--script"} or not command:
        raise SystemExit("hook_log.py: usage: --event <E> --script <path> -- <command…>")
    return opts["--event"], opts["--script"], command


def append(path, record):
    """One line, one write, O_APPEND: two hooks finishing together never interleave their lines."""
    fd = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o644)
    try:
        os.write(fd, (json.dumps(record, ensure_ascii=False) + "\n").encode("utf-8"))
    finally:
        os.close(fd)


def main(argv=None):
    event, script, command = parse(sys.argv[1:] if argv is None else argv)
    stdin = sys.stdin.buffer.read() if sys.stdin is not None else b""
    r = subprocess.run(command, input=stdin, capture_output=True)
    sys.stdout.buffer.write(r.stdout)
    sys.stdout.flush()
    sys.stderr.buffer.write(r.stderr)
    sys.stderr.flush()
    code = r.returncode if r.returncode >= 0 else 128 - r.returncode   # killed by a signal: the shell's convention
    log = os.environ.get("XBT_EVAL_HOOK_LOG")
    if log:
        append(log, {"event": event, "script": os.path.realpath(script), "exit": code,
                     "stdout_sha256": hashlib.sha256(r.stdout).hexdigest(),
                     "t": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="microseconds")})
    return code


if __name__ == "__main__":
    sys.exit(main())
