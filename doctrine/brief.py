#!/usr/bin/env python3
"""SubagentStart hook: give every agent the doctrine's key rules as it starts, whatever started it.

A subagent starts from the brief its caller writes, not from the session's doctrine, so the rules that matter most in
any agent (evidence for every claim, a permission denial is a stop, no edits where the task forbids them) are handed
to the agent itself: key-rules.md, next to this file, is the one source of the block. Claude Code runs the
SubagentStart hooks on every agent runner path (the Agent tool, a Workflow script's agent(), a skill's fork) and adds
their `additionalContext` to the new agent's context, so the hook answers one event and needs no matcher. It never
blocks and never sets a decision or a permission: whether the agent runs is not this hook's to decide.

  DOCTRINE=off (any case)            nothing added
  another event                      nothing added (a payload that is not JSON: one stderr line); the Agent tool's
                                     PreToolUse, where an earlier version put the rules, included, so no agent gets
                                     them twice
  key-rules.md missing               one stderr line and exit 1: a visible hook error that never blocks the agent
                                     (exit 2 would)

Registered through the pack's tolerant launcher, so a missing brief.py is one stderr line and exit 0, never a blocked
agent. Stdlib only, Python 3.9 or newer, Windows, macOS and Linux. key-rules.md is found next to this file (next to the
link, when it is run through one), so the hook command needs no $HOME or %USERPROFILE%.
"""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))   # never realpath: run through a link, the rules are the link's
RULES = os.path.join(HERE, "key-rules.md")
EVENT = "SubagentStart"


def main():
    if os.environ.get("DOCTRINE", "on").strip().lower() == "off":
        return 0
    raw = sys.stdin.buffer.read() if sys.stdin is not None else b""
    try:
        payload = json.loads(raw.decode("utf-8"))
    except ValueError as e:
        sys.stderr.write(f"brief hook: the hook input is not JSON ({e}); nothing added\n")
        return 0
    if not isinstance(payload, dict) or payload.get("hook_event_name") != EVENT:
        return 0
    try:
        with open(RULES, encoding="utf-8") as fh:
            block = fh.read().strip("\n")
    except OSError as e:
        sys.stderr.write(f"brief hook: {RULES} unreadable ({e.strerror or e}); the agent starts without the key rules\n")
        return 1
    sys.stdout.write(json.dumps({"hookSpecificOutput": {"hookEventName": EVENT, "additionalContext": block}}) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
