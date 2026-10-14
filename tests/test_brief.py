"""Tests for doctrine/brief.py, run as the real hook runs it: a separate python process, the SubagentStart JSON on
stdin.

The brief hook gives every agent the key-rules block (doctrine/key-rules.md) as the SubagentStart hook's
`additionalContext`, which Claude Code adds to the new agent's context, whatever started it (the Agent tool, a
Workflow script, a skill); it never blocks and never decides whether anything is allowed."""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

DOCTRINE = Path(__file__).resolve().parent.parent / "doctrine"
BRIEF = DOCTRINE / "brief.py"
RULES = DOCTRINE / "key-rules.md"
MARKER = "<!-- doctrine brief rules v1 -->"


def run(payload, brief=None, **env):
    base = {k: v for k, v in os.environ.items() if k != "DOCTRINE"}
    base.update(env)
    stdin = payload if isinstance(payload, str) else json.dumps(payload)
    r = subprocess.run([sys.executable, str(brief or BRIEF)], input=stdin.encode("utf-8"), env=base, capture_output=True)
    return r.returncode, r.stdout.decode("utf-8"), r.stderr.decode("utf-8")


def start(agent_type="Explore", **extra):
    """The SubagentStart hook input as Claude Code 2.1.283 builds it: the common fields, then the event's own."""
    return dict({"session_id": "s", "transcript_path": "/t/s.jsonl", "cwd": "/p", "permission_mode": "default",
                 "hook_event_name": "SubagentStart", "agent_id": "a1b2c3", "agent_type": agent_type}, **extra)


class Brief(unittest.TestCase):
    def block(self):
        return RULES.read_text(encoding="utf-8").strip("\n")

    def test_every_agent_start_gets_the_block_as_additional_context(self):
        for agent_type in ("Explore", "general-purpose", "workflow-subagent", ""):
            code, out, err = run(start(agent_type))
            self.assertEqual((code, err), (0, ""), agent_type)
            data = json.loads(out)
            # exactly this: no decision, no permission, no continue; the hook never changes whether the agent runs
            self.assertEqual(data, {"hookSpecificOutput": {"hookEventName": "SubagentStart",
                                                           "additionalContext": self.block()}}, agent_type)
            self.assertEqual(data["hookSpecificOutput"]["additionalContext"].count(MARKER), 1)
        self.assertTrue(self.block().startswith(MARKER))

    def test_other_events_and_a_malformed_payload_pass_through(self):
        # the Agent tool's PreToolUse, where an earlier version delivered the rules: nothing, so no agent gets them twice
        agent = {"session_id": "s", "hook_event_name": "PreToolUse", "tool_name": "Agent",
                 "tool_input": {"description": "x", "prompt": "Find the callers."}}
        for payload in (agent, {"hook_event_name": "PreToolUse", "tool_name": "Bash", "tool_input": {"command": "ls"}},
                        {"hook_event_name": "SubagentStop", "agent_id": "a", "agent_type": "Explore"}, {}, []):
            self.assertEqual(run(payload), (0, "", ""), payload)
        # not JSON: exit 0, nothing on stdout, one stderr line
        code, out, err = run("not json")
        self.assertEqual((code, out), (0, ""))
        self.assertEqual(len(err.splitlines()), 1, err)

    def test_doctrine_off_adds_nothing(self):
        self.assertIn(MARKER, run(start())[1])                                   # on: the block
        for value in ("off", "OFF", " Off "):
            self.assertEqual(run(start(), DOCTRINE=value), (0, "", ""), value)   # off: nothing, whatever the case

    def test_a_missing_rules_file_is_a_visible_non_blocking_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            copy = Path(tmp) / "brief.py"
            shutil.copy2(BRIEF, copy)                                    # the rules file is looked for beside it
            code, out, err = run(start(), brief=copy)
        self.assertEqual((code, out), (1, ""))                           # exit 1, never 2 (which would block)
        self.assertEqual(len(err.splitlines()), 1, err)
        self.assertIn("key-rules.md", err)

    def test_the_rules_are_found_beside_the_link_it_runs_through(self):
        """Run through a link, the rules are the link folder's (the doctrine folder's own link to them), like the
        gate's layers."""
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            try:
                os.symlink(BRIEF, folder / "brief.py")
            except (OSError, NotImplementedError):
                self.skipTest("no symlinks here")
            (folder / "key-rules.md").write_text(MARKER + "\nlocal rules\n", encoding="utf-8")
            code, out, _ = run(start(), brief=folder / "brief.py")
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out)["hookSpecificOutput"]["additionalContext"], MARKER + "\nlocal rules")

    def test_the_output_is_json_whatever_the_rules_hold(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            shutil.copy2(BRIEF, folder / "brief.py")
            rules = MARKER + "\nQuote \"this\", a tab\t, a backslash \\ and ünïcödé — then stop.\n"
            (folder / "key-rules.md").write_text(rules, encoding="utf-8")
            code, out, _ = run(start(), brief=folder / "brief.py")
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out)["hookSpecificOutput"]["additionalContext"], rules.strip("\n"))


if __name__ == "__main__":
    unittest.main()
