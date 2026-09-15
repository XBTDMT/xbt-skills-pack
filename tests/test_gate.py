"""Tests for doctrine/gate.py, run as the real hook runs it: a separate python process, JSON on stdin."""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

GATE = Path(__file__).resolve().parent.parent / "doctrine" / "gate.py"


def run(stdin="", **env):
    base = {k: v for k, v in os.environ.items()
            if not k.startswith(("DOCTRINE", "CLAUDE_CONFIG_DIR", "CLAUDE_PID", "ANTHROPIC_MODEL", "CLAUDE_JOB_DIR"))}
    base.update(env)
    r = subprocess.run([sys.executable, str(GATE)], input=stdin.encode("utf-8"), env=base, capture_output=True)
    return r.returncode, r.stdout.decode("utf-8"), r.stderr.decode("utf-8")


class Gate(unittest.TestCase):
    def setUp(self):
        self.cfg = tempfile.TemporaryDirectory()
        self.addCleanup(self.cfg.cleanup)
        self.env = {"CLAUDE_CONFIG_DIR": self.cfg.name}  # never read the real settings.json

    def test_opus_gets_the_opus_layer_naming_opus(self):
        code, out, _ = run(json.dumps({"model": "claude-opus-5"}), **self.env)
        self.assertEqual(code, 0)
        self.assertTrue(out.startswith("# Working doctrine (injected: this session runs Claude Opus 5)"))

    def test_fable_gets_the_fable_layer(self):
        _, out, _ = run(json.dumps({"model": {"id": "claude-fable-5-1"}}), **self.env)
        self.assertTrue(out.startswith("# Working notes (injected: this session runs Claude Fable 5.1)"))

    def test_sonnet_gets_the_opus_layer_naming_sonnet(self):
        _, out, _ = run(json.dumps({"model": "claude-sonnet-5"}), **self.env)
        self.assertTrue(out.startswith("# Working doctrine (injected: this session runs Claude Sonnet 5)"))
        self.assertEqual(out.count("Claude Sonnet 5"), 1)  # only the header is rewritten

    def test_no_model_on_stdin_falls_back_to_the_launcher_then_settings(self):
        _, out, _ = run("{}", DOCTRINE_MODEL="claude-fable-5-1", **self.env)
        self.assertIn("Claude Fable 5.1", out.splitlines()[0])
        Path(self.cfg.name, "settings.json").write_text(json.dumps({"model": "sonnet"}))
        _, out, _ = run("", **self.env)
        self.assertIn("Claude Sonnet 5", out.splitlines()[0])

    @unittest.skipIf(os.name == "nt", "the session process's command line is read with ps")
    def test_a_headless_session_is_found_from_its_process_command_line(self):
        # claude -p sends no model on stdin; the prompt comes first and may hold an apostrophe or "--model"
        fake = subprocess.Popen(["bash", "-c", "exec -a \"claude -p Let's mention --model opus --model claude-fable-5-1\" sleep 30"])
        def stop():
            fake.kill()
            fake.wait()          # reaped, or the interpreter warns that the child is still running
        self.addCleanup(stop)
        import time
        time.sleep(0.3)
        _, out, _ = run("{}", CLAUDE_PID=str(fake.pid), **self.env)
        self.assertTrue(out.startswith("# Working notes (injected: this session runs Claude Fable 5.1)"))
        _, out, _ = run("{}", CLAUDE_PID="999999999", ANTHROPIC_MODEL="claude-fable-5-1", **self.env)
        self.assertIn("Claude Fable 5.1", out.splitlines()[0])

    def test_a_resumed_background_session_is_found_from_the_daemons_records(self):
        # a resumed session is a fork: no model on stdin, and its process has no --model
        cfg = Path(self.cfg.name)
        (cfg / "daemon").mkdir()
        parent = cfg / "parent.jsonl"
        parent.write_text(json.dumps({"type": "assistant", "message": {"model": "claude-fable-5-1"}}) + "\n")
        (cfg / "daemon" / "roster.json").write_text(json.dumps({"workers": [
            {"replPid": 4242, "sessionId": "s1", "dispatch": {"launch": {"flagArgs": ["--effort", "low", "--model", "claude-fable-5-1"]}}},
            {"replPid": 4343, "sessionId": "s2", "dispatch": {"launch": {"flagArgs": [], "transcriptPath": str(parent)}}}]}))
        _, out, _ = run(json.dumps({"session_id": "new"}), CLAUDE_PID="4242", **self.env)
        self.assertIn("Claude Fable 5.1", out.splitlines()[0])
        _, out, _ = run(json.dumps({"session_id": "new"}), CLAUDE_PID="4343", **self.env)
        self.assertIn("Claude Fable 5.1", out.splitlines()[0])
        _, out, _ = run(json.dumps({"session_id": "s1"}), CLAUDE_PID="1", **self.env)
        self.assertIn("Claude Fable 5.1", out.splitlines()[0])
        job = cfg / "job"; job.mkdir()
        (job / "state.json").write_text(json.dumps({"respawnFlags": ["--model", "claude-fable-5-1"]}))
        _, out, _ = run("{}", CLAUDE_PID="1", CLAUDE_JOB_DIR=str(job), CLAUDE_CONFIG_DIR=str(cfg / "none"))
        self.assertIn("Claude Fable 5.1", out.splitlines()[0])
        (cfg / "daemon" / "roster.json").write_text("not json")
        _, out, _ = run(json.dumps({"session_id": "s1"}), CLAUDE_PID="1", **self.env)
        self.assertIn("Claude Opus 5", out.splitlines()[0])  # a broken record falls through, never crashes

    def test_nothing_known_is_the_opus_layer(self):
        _, out, _ = run("not json", **self.env)
        self.assertIn("Claude Opus 5", out.splitlines()[0])

    def test_doctrine_off_injects_nothing(self):
        code, out, _ = run(json.dumps({"model": "claude-opus-5"}), DOCTRINE="off", **self.env)
        self.assertEqual((code, out), (0, ""))

    def test_forced_layer_by_name_and_an_unreadable_one_injects_nothing(self):
        _, out, _ = run(json.dumps({"model": "claude-opus-5"}), DOCTRINE_LAYER="fable-layer.md", **self.env)
        self.assertTrue(out.startswith("# Working notes"))
        code, out, err = run("{}", DOCTRINE_LAYER="missing.md", **self.env)
        self.assertEqual((code, out), (0, ""))
        self.assertIn("injecting nothing", err)

    def test_output_is_utf8_whatever_the_locale(self):
        _, out, _ = run(json.dumps({"model": "claude-opus-5"}), PYTHONIOENCODING="ascii", **self.env)
        self.assertIn("I'll…", out)  # a non-ASCII character that an ASCII or legacy-code-page console refuses


if __name__ == "__main__":
    unittest.main()
