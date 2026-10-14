#!/usr/bin/env python3
"""E-1's UserPromptSubmit probe (R-22 §5.8, `probe_hooks`): prints R-28's JSON shape and exits 0, so R-31's first
sealed run can record where a UserPromptSubmit hook's output lands (the stream, the transcript, or neither). Built
into the sealed config only by `run.py --probe`. Stdlib only."""
import json
import sys

sys.stdin.read()
print(json.dumps({"hookSpecificOutput": {"hookEventName": "UserPromptSubmit",
                                         "additionalContext": "eval probe: a UserPromptSubmit hook ran; nothing to do"}}))
