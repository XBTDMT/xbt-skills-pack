#!/bin/zsh
# doc-guardrails — PostToolUse hook on Write|Edit. Reads the tool input from stdin, and when the file written is a
# markdown document of a linked kind with no `roadmap:` line (or a broken one), says so on stderr and exits 2 so the
# session sees it while the work is still in hand. Silent otherwise: not markdown, no roadmap in this project,
# `guardrails: off` in the doc map, or the file is linked. Never edits anything. Budget: two python starts.
# This layer is optional: the end-of-turn check sees every document however it was written.
# NOT installed by the installer: this is a zsh script for macOS and Linux. On Windows the other two layers
# (the end-of-turn check and the commit gate) still run; this one waits on a Python port.
set -u
PY="/usr/bin/python3"                                   # macOS always has it; elsewhere take whatever python3 is on PATH
[ -x "$PY" ] || PY="$(command -v python3 2>/dev/null || true)"
[ -n "$PY" ] || exit 0
IN="$(cat 2>/dev/null || true)"
FILE="$(printf '%s' "$IN" | "$PY" -c 'import json,sys
try: d=json.load(sys.stdin); print(d.get("tool_input",{}).get("file_path",""))
except Exception: print("")' 2>/dev/null)"
case "$FILE" in *.md) ;; *) exit 0 ;; esac
[ -f "$FILE" ] || exit 0
DIR="$(dirname "$FILE")"
# The helper sits beside this script's own folder, wherever the skills were installed (CLAUDE_CONFIG_DIR included),
# so it is found from here rather than from a guessed home.
H="${0:A:h}/../closeout/closeout.py"
[ -f "$H" ] || H="${CLAUDE_CONFIG_DIR:-$HOME/.claude}/skills/closeout/closeout.py"
if [ ! -f "$H" ]; then printf 'doc-guardrails: closeout.py not found next to the hook or in the skills folder\n' >&2; exit 0; fi
OUT="$("$PY" "$H" link-audit "$DIR" --file "$FILE" 2>/dev/null)"; RC=$?
if [ "$RC" -eq 2 ] && [ -n "$OUT" ]; then printf '%s\n' "$OUT" >&2; exit 2; fi
exit 0
