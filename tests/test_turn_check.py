"""Tests for doc-guardrails/turn_check.py, the Stop hook that checks documents changed during a turn, whatever wrote them."""
import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

HOOK = Path(__file__).resolve().parent.parent / "skills" / "doc-guardrails" / "turn_check.py"
sys.path.insert(0, str(HOOK.parent))
import turn_check  # noqa: E402


def sh(cwd, *args):
    return subprocess.run(args, cwd=cwd, check=True, capture_output=True, text=True).stdout


class TurnCheck(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name) / "proj"
        self.state = Path(tmp.name) / "state"
        self.root.mkdir()
        sh(self.root, "git", "init", "-q", "-b", "main")
        self.write("knowledge/00-ROADMAP.md", "## Now\n- R-1 a\n")
        self.write("knowledge/02-SPEC-old.md", "# old, unlinked, from before the turn\n")
        old = time.time() - 7200
        os.utime(self.root / "knowledge/02-SPEC-old.md", (old, old))
        sh(self.root, "git", "add", "-A")
        sh(self.root, "git", "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "base")

    def write(self, rel, text):
        p = self.root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
        return p

    def payload(self, **kw):
        return {"session_id": "s1", "cwd": str(self.root), "hook_event_name": "Stop", "stop_hook_active": False, **kw}

    def run_check(self, **kw):
        return turn_check.check(self.payload(**kw), self.state)

    def test_a_document_written_from_the_shell_this_turn_blocks_the_turn_once_with_the_list(self):
        self.run_check()  # an earlier turn ended: this is the baseline
        time.sleep(0.02)
        subprocess.run([sys.executable, "-c", "open('knowledge/02-SPEC-new.md','w',encoding='utf-8').write('# new\\nno line\\n')"],
                       cwd=self.root, check=True)
        self.write("knowledge/02-SPEC-linked.md", "# linked\nroadmap: R-1\n")
        out = self.run_check()
        self.assertIsNotNone(out)
        decision = json.loads(out)
        self.assertEqual(decision["decision"], "block")
        self.assertIn("knowledge/02-SPEC-new.md", decision["reason"])
        self.assertNotIn("02-SPEC-linked.md", decision["reason"])
        self.assertNotIn("02-SPEC-old.md", decision["reason"])  # older than the turn: the backlog, not this turn's work
        self.assertIsNone(self.run_check(stop_hook_active=True))  # never blocks a continuation: no loop

    def test_silent_when_nothing_changed_the_guardrails_are_off_or_there_is_no_repository(self):
        self.run_check()
        self.assertIsNone(self.run_check())
        time.sleep(0.02)
        self.write("CLAUDE.md", "# x\n\n## Doc map\nroadmap: knowledge/00-ROADMAP.md\nguardrails: off\n")
        self.write("knowledge/02-SPEC-new.md", "# new\n")
        self.assertIsNone(self.run_check())
        outside = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: __import__("shutil").rmtree(outside, ignore_errors=True))
        self.assertIsNone(turn_check.check({"session_id": "s2", "cwd": str(outside), "stop_hook_active": False}, self.state))

    def test_the_first_turn_of_a_session_looks_back_one_hour(self):
        self.write("knowledge/02-SPEC-fresh.md", "# fresh\n")
        out = self.run_check()
        self.assertIn("knowledge/02-SPEC-fresh.md", json.loads(out)["reason"])

    def test_the_script_reads_stdin_and_prints_the_decision(self):
        self.write("knowledge/02-SPEC-fresh.md", "# fresh\n")
        env = {**os.environ, "DOC_GUARDRAILS_STATE": str(self.state)}
        r = subprocess.run([sys.executable, str(HOOK)], input=json.dumps(self.payload()), capture_output=True,
                           text=True, env=env, timeout=30)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(json.loads(r.stdout)["decision"], "block")
        r = subprocess.run([sys.executable, str(HOOK)], input="not json", capture_output=True, text=True, env=env)
        self.assertEqual((r.returncode, r.stdout), (0, ""))  # a malformed payload never breaks the session


def ts(epoch):
    import datetime
    return datetime.datetime.fromtimestamp(epoch, datetime.timezone.utc).isoformat().replace("+00:00", "Z")


class Attribution(unittest.TestCase):
    """With a transcript, only documents this session changed are judged: another lane's edit in the same checkout is
    not this session's to fix."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name)
        self.state = self.tmp / "state"
        self.main = self.repo("main")
        self.lane = self.repo("lane")
        self.entries = []
        self.transcript = self.tmp / "session" / "s1.jsonl"
        self.transcript.parent.mkdir()

    def repo(self, name):
        root = self.tmp / name
        (root / "knowledge").mkdir(parents=True)
        (root / "knowledge" / "00-ROADMAP.md").write_text("## Now\n- R-1 a\n", encoding="utf-8")
        sh(root, "git", "init", "-q", "-b", "main")
        sh(root, "git", "add", "-A")
        sh(root, "git", "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "base")
        return root

    def tool(self, uid, name, inp, start, end, transcript=None):
        rows = [{"type": "assistant", "timestamp": ts(start), "message": {"content": [{"type": "tool_use", "id": uid, "name": name, "input": inp}]}},
                {"type": "user", "timestamp": ts(end), "message": {"content": [{"type": "tool_result", "tool_use_id": uid, "content": "ok"}]}}]
        target = transcript or self.transcript
        with open(target, "a", encoding="utf-8") as fh:
            for r in rows:
                fh.write(json.dumps(r) + "\n")

    def touch(self, path, text, when):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        os.utime(path, (when, when))

    def check(self, now):
        return turn_check.check({"session_id": "s1", "cwd": str(self.main), "stop_hook_active": False,
                                 "transcript_path": str(self.transcript)}, self.state, now=now)

    def test_another_sessions_edit_is_ignored_and_this_sessions_writes_are_judged_wherever_they_happened(self):
        t0 = time.time() - 600
        self.touch(self.main / "knowledge" / "02-spec-theirs.md", "# theirs\n", t0 + 100)        # no tool of ours ran then
        self.tool("u1", "Write", {"file_path": str(self.main / "knowledge" / "02-spec-mine.md")}, t0 + 200, t0 + 201)
        self.touch(self.main / "knowledge" / "02-spec-mine.md", "# mine\n", t0 + 200.5)
        self.tool("u2", "Bash", {"command": f"cd {self.lane} && python3 make_spec.py"}, t0 + 300, t0 + 310)
        self.touch(self.lane / "knowledge" / "02-spec-script.md", "# from a script\n", t0 + 305)
        self.touch(self.lane / "knowledge" / "02-spec-lane-other.md", "# someone else in the lane\n", t0 + 400)
        out = self.check(now=t0 + 500)
        self.assertIsNotNone(out)
        reason = json.loads(out)["reason"]
        self.assertIn("02-spec-mine.md", reason)
        self.assertIn("02-spec-script.md", reason)
        self.assertNotIn("02-spec-theirs.md", reason)
        self.assertNotIn("02-spec-lane-other.md", reason)

    def test_only_another_sessions_edits_means_silence(self):
        t0 = time.time() - 600
        self.touch(self.main / "knowledge" / "02-spec-theirs.md", "# theirs\n", t0 + 100)
        self.tool("u1", "Bash", {"command": "ls"}, t0 + 200, t0 + 201)
        self.assertIsNone(self.check(now=t0 + 500))

    def test_a_subagents_writes_count_as_this_sessions(self):
        t0 = time.time() - 600
        self.transcript.write_text("", encoding="utf-8")  # the session's own transcript exists; the write is in its subagent's
        sub = self.transcript.parent / "s1" / "subagents" / "agent-x.jsonl"
        sub.parent.mkdir(parents=True)
        self.tool("a1", "Write", {"file_path": str(self.lane / "knowledge" / "02-spec-agent.md")}, t0 + 200, t0 + 201, transcript=sub)
        self.touch(self.lane / "knowledge" / "02-spec-agent.md", "# agent\n", t0 + 200.5)
        out = self.check(now=t0 + 500)
        self.assertIn("02-spec-agent.md", json.loads(out)["reason"])

    def test_an_unreadable_transcript_falls_back_to_every_changed_document(self):
        t0 = time.time() - 600
        self.touch(self.main / "knowledge" / "02-spec-theirs.md", "# theirs\n", t0 + 100)
        out = turn_check.check({"session_id": "s2", "cwd": str(self.main), "stop_hook_active": False,
                                "transcript_path": str(self.tmp / "missing.jsonl")}, self.state, now=t0 + 500)
        self.assertIn("02-spec-theirs.md", json.loads(out)["reason"])


if __name__ == "__main__":
    unittest.main()
