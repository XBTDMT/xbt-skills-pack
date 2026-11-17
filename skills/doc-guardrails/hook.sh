#!/bin/zsh
# doc-guardrails — the per-write hook's older entry point (PostToolUse on Write|Edit), kept so a settings entry that
# names hook.sh keeps working. The hook itself is hook.py beside this file (plain Python, so it runs where there is
# no zsh, Windows included): this script hands it the same stdin and takes its output and exit code. See hook.py for
# what it reports. Never edits anything. Budget: two python starts.
set -u
PY="/usr/bin/python3"                                   # macOS always has it; elsewhere take whatever python3 is on PATH
[ -x "$PY" ] || PY="$(command -v python3 2>/dev/null || true)"
[ -n "$PY" ] || exit 0
# hook.py sits in this script's own real folder, wherever the skills were installed (CLAUDE_CONFIG_DIR included).
P="${0:A:h}/hook.py"
if [ ! -f "$P" ]; then printf 'doc-guardrails: hook.py not found next to hook.sh\n' >&2; exit 0; fi
exec "$PY" "$P"
