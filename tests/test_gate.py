"""Tests for doctrine/gate.py, run as the real hook runs it: a separate python process, JSON on stdin."""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

GATE = Path(__file__).resolve().parent.parent / "doctrine" / "gate.py"


def run(stdin="", gate=None, args=(), **env):
    base = {k: v for k, v in os.environ.items()
            if not k.startswith(CLEAN)}
    base.update(env)
    r = subprocess.run([sys.executable, str(gate or GATE), *args], input=stdin.encode("utf-8"), env=base, capture_output=True)
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

    def test_opus_5_5_gets_its_own_layer(self):
        # Opus 5.5 shipped 2026-09-22 and got a layer of its own, measured the same day (the figures, and the setup they
        # ran on: https://github.com/XBTDMT/xbt-skills-pack/blob/main/docs/evidence.md); its header already names the
        # model, so nothing is rewritten. Before that the generic "opus" match called it Opus 5.
        code, out, _ = run(json.dumps({"model": "claude-opus-5-5"}), **self.env)
        self.assertEqual(code, 0)
        self.assertTrue(out.startswith("# Working notes (injected: this session runs Claude Opus 5.5)"), out.splitlines()[0])
        self.assertNotIn("Opus 5.5.5", out)

    def test_a_forced_layer_that_already_names_the_model_is_not_rewritten(self):
        # A layer written for Opus 5.5 starts "runs Claude Opus 5.5"; the substitution of "Claude Opus 5" would give "5.5.5".
        layer = Path(self.cfg.name) / "opus-5-5-layer.md"
        layer.write_text("# Working notes (injected: this session runs Claude Opus 5.5)\n\nbody\n", encoding="utf-8")
        _, out, _ = run(json.dumps({"model": "claude-opus-5-5"}), DOCTRINE_LAYER=str(layer), **self.env)
        self.assertEqual(out, "# Working notes (injected: this session runs Claude Opus 5.5)\n\nbody\n")

    def test_a_layer_saved_with_crlf_is_injected_with_lf(self):
        # A Windows checkout (git's core.autocrlf) or editor saves a layer with CRLF; the session gets the text as
        # measured, LF throughout, on every platform. The known-bad is the file's bytes passed through as they are.
        layer = Path(self.cfg.name) / "crlf-layer.md"
        layer.write_bytes(b"# Working notes (injected: this session runs Claude Opus 5.5)\r\n\r\nbody \xe2\x80\x94 two\r\n")
        code, out, _ = run(json.dumps({"model": "claude-fable-5-1"}), DOCTRINE_LAYER=str(layer), **self.env)
        self.assertEqual((code, out), (0, "# Working notes (injected: this session runs Claude Fable 5.1)\n\nbody \u2014 two\n"))
        self.assertIn(b"\r\n", layer.read_bytes())

    def test_sonnet_gets_the_opus_layer_naming_sonnet(self):
        _, out, _ = run(json.dumps({"model": "claude-sonnet-5"}), **self.env)
        self.assertTrue(out.startswith("# Working doctrine (injected: this session runs Claude Sonnet 5)"))
        self.assertEqual(out.count("Claude Sonnet 5"), 1)  # only the header is rewritten

    def test_no_model_on_stdin_falls_back_to_the_launcher_then_settings(self):
        _, out, _ = run("{}", DOCTRINE_MODEL="claude-fable-5-1", **self.env)
        self.assertIn("Claude Fable 5.1", out.splitlines()[0])
        Path(self.cfg.name, "settings.json").write_text(json.dumps({"model": "sonnet"}), encoding="utf-8")
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
        parent.write_text(json.dumps({"type": "assistant", "message": {"model": "claude-fable-5-1"}}) + "\n", encoding="utf-8")
        (cfg / "daemon" / "roster.json").write_text(json.dumps({"workers": [
            {"replPid": 4242, "sessionId": "s1", "dispatch": {"launch": {"flagArgs": ["--effort", "low", "--model", "claude-fable-5-1"]}}},
            {"replPid": 4343, "sessionId": "s2", "dispatch": {"launch": {"flagArgs": [], "transcriptPath": str(parent)}}}]}), encoding="utf-8")
        _, out, _ = run(json.dumps({"session_id": "new"}), CLAUDE_PID="4242", **self.env)
        self.assertIn("Claude Fable 5.1", out.splitlines()[0])
        _, out, _ = run(json.dumps({"session_id": "new"}), CLAUDE_PID="4343", **self.env)
        self.assertIn("Claude Fable 5.1", out.splitlines()[0])
        _, out, _ = run(json.dumps({"session_id": "s1"}), CLAUDE_PID="1", **self.env)
        self.assertIn("Claude Fable 5.1", out.splitlines()[0])
        job = cfg / "job"; job.mkdir()
        (job / "state.json").write_text(json.dumps({"respawnFlags": ["--model", "claude-fable-5-1"]}), encoding="utf-8")
        _, out, _ = run("{}", CLAUDE_PID="1", CLAUDE_JOB_DIR=str(job), CLAUDE_CONFIG_DIR=str(cfg / "none"))
        self.assertIn("Claude Fable 5.1", out.splitlines()[0])
        (cfg / "daemon" / "roster.json").write_text("not json", encoding="utf-8")
        _, out, _ = run(json.dumps({"session_id": "s1"}), CLAUDE_PID="1", **self.env)
        # a broken record falls through, never crashes; nothing else known: the undetected header (G-21)
        self.assertEqual(first(out), "# Working notes (injected: this session runs an undetected model)")

    def test_doctrine_off_injects_nothing(self):
        code, out, _ = run(json.dumps({"model": "claude-opus-5"}), DOCTRINE="off", **self.env)
        self.assertEqual((code, out), (0, ""))

    def test_forced_layer_by_name_and_an_unreadable_one_injects_nothing(self):
        _, out, _ = run(json.dumps({"model": "claude-opus-5"}), DOCTRINE_LAYER="fable-layer.md", **self.env)
        self.assertTrue(out.startswith("# Working notes"))
        code, out, err = run("{}", DOCTRINE_LAYER="missing.md", **self.env)
        self.assertEqual((code, out), (1, ""))          # exit 1, as a missing chosen layer (G-21 I.3)
        self.assertIn("injecting nothing", err)

    def test_output_is_utf8_whatever_the_locale(self):
        _, out, _ = run(json.dumps({"model": "claude-opus-5"}), PYTHONIOENCODING="ascii", **self.env)
        self.assertIn("I'll…", out)  # a non-ASCII character that an ASCII or legacy-code-page console refuses

    # ---- R-26: the model table, the honest header, the loud missing layer (tests 1-12)

    def test_the_opus_alias_is_the_opus_5_5_layer(self):
        _, out, _ = run(json.dumps({"model": "opus"}), **self.env)
        self.assertEqual(first(out), "# Working notes (injected: this session runs Claude Opus 5.5)")
        self.assertEqual(out.split("\n", 1)[1], (GATE.parent / "opus-5-5-layer.md").read_text(encoding="utf-8").split("\n", 1)[1])

    def test_an_undetected_model_gets_the_5_5_layer_and_says_so(self):
        code, out, _ = run("{}", **self.env)
        self.assertEqual(code, 0)
        self.assertEqual(first(out), "# Working notes (injected: this session runs an undetected model)")
        self.assertEqual(out.split("\n", 1)[1], (GATE.parent / "opus-5-5-layer.md").read_text(encoding="utf-8").split("\n", 1)[1])

    def test_mythos_is_named_mythos(self):
        _, table, _ = run(args=["--table"], **self.env)
        if "mythos" not in json.loads(table)["family_layer"]:
            self.skipTest("this copy has no Mythos row (the public copy)")
        _, out, _ = run(json.dumps({"model": "claude-mythos-5-1"}), **self.env)
        self.assertEqual(first(out), "# Working notes (injected: this session runs Claude Mythos 5.1)")
        self.assertEqual(out.split("\n", 1)[1], (GATE.parent / "fable-layer.md").read_text(encoding="utf-8").split("\n", 1)[1])

    def test_a_forced_layer_names_the_real_model(self):
        _, out, _ = run(json.dumps({"model": "claude-opus-5-5"}), DOCTRINE_LAYER="fable-layer.md", **self.env)
        self.assertEqual(first(out), "# Working notes (injected: this session runs Claude Opus 5.5)")
        _, out, _ = run(json.dumps({"model": "claude-opus-5"}), DOCTRINE_LAYER="opus-5-5-layer.md", **self.env)
        self.assertEqual(first(out), "# Working notes (injected: this session runs Claude Opus 5)")

    def test_versions_are_named_as_written(self):
        for model, name in (("claude-fable-5", "Claude Fable 5"), ("claude-sonnet-5-5", "Claude Sonnet 5.5"),
                            ("claude-haiku-4-5", "Claude Haiku 4.5")):
            _, out, _ = run(json.dumps({"model": model}), **self.env)
            self.assertIn(f"this session runs {name})", first(out), model)

    def test_the_family_version_rule(self):
        opus55 = (GATE.parent / "opus-5-5-layer.md").read_text(encoding="utf-8").split("\n", 1)[1]
        opus = (GATE.parent / "opus-layer.md").read_text(encoding="utf-8").split("\n", 1)[1]
        _, out, _ = run(json.dumps({"model": "claude-opus-6"}), **self.env)
        self.assertEqual((first(out), out.split("\n", 1)[1]),
                         ("# Working notes (injected: this session runs Claude Opus 6)", opus55))
        _, out, _ = run(json.dumps({"model": "claude-opus-4-8"}), **self.env)
        self.assertEqual((first(out), out.split("\n", 1)[1]),
                         ("# Working doctrine (injected: this session runs Claude Opus 4.8)", opus))
        _, out, _ = run(json.dumps({"model": "claude-sonnet-4-5"}), **self.env)
        self.assertEqual(out.split("\n", 1)[1], opus)

    def test_an_unrecognised_string_is_named_as_found(self):
        _, out, _ = run(json.dumps({"model": "vendor/model-x"}), **self.env)
        self.assertEqual(first(out), "# Working notes (injected: this session runs vendor/model-x)")
        self.assertEqual(out.split("\n", 1)[1], (GATE.parent / "opus-5-5-layer.md").read_text(encoding="utf-8").split("\n", 1)[1])

    def test_doctrine_off_in_any_case(self):
        for word in ("OFF", "Off", "off"):
            self.assertEqual(run(json.dumps({"model": "claude-opus-5"}), DOCTRINE=word, **self.env)[:2], (0, ""), word)

    def test_alias_env_override(self):
        _, out, _ = run(json.dumps({"model": "opus"}), ANTHROPIC_DEFAULT_OPUS_MODEL="claude-opus-5", **self.env)
        self.assertEqual(first(out), "# Working doctrine (injected: this session runs Claude Opus 5)")
        for model in ("opus[1m]", "us.anthropic.claude-opus-5-5-v1:0"):
            _, out, _ = run(json.dumps({"model": model}), **self.env)
            self.assertEqual(first(out), "# Working notes (injected: this session runs Claude Opus 5.5)", model)

    def test_a_resumed_session_is_found_from_its_stdin_transcript(self):
        t = Path(self.cfg.name) / "resumed.jsonl"
        t.write_text("\n".join(json.dumps(r) for r in (
            {"type": "assistant", "message": {"model": "claude-opus-5-5"}},
            {"type": "assistant", "message": {"model": "claude-fable-5-1"}})) + "\n", encoding="utf-8")
        _, out, _ = run(json.dumps({"transcript_path": str(t)}), **self.env)
        self.assertEqual(first(out), "# Working notes (injected: this session runs Claude Fable 5.1)")

    def test_a_missing_layer_is_loud(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = fixture_doctrine(tmp, {k: v for k, v in FIXTURE_LAYERS.items() if k != "opus-5-5-layer.md"})
            code, out, err = run(json.dumps({"model": "claude-opus-5-5"}), gate=d / "gate.py", **self.env)
            self.assertEqual((code, out), (1, ""))
            self.assertEqual(len(err.splitlines()), 1, err)
            self.assertIn("opus-5-5-layer.md", err)
            self.assertNotIn("Traceback", err)
            code, out, err = run(json.dumps({"model": "claude-opus-5"}), gate=d / "gate.py", DOCTRINE_LAYER="missing.md",
                                 **self.env)
            self.assertEqual((code, out, len(err.splitlines())), (1, "", 1))
            self.assertIn("missing.md", err)

    def test_it_finds_its_layers_through_a_symlink(self):
        with tempfile.TemporaryDirectory() as a, tempfile.TemporaryDirectory() as b:
            src = fixture_doctrine(a)
            other = {k: v.replace("body", "body beside the link") for k, v in FIXTURE_LAYERS.items()}
            for name, text in other.items():
                Path(b, name).write_text(text, encoding="utf-8")
            try:
                os.symlink(src / "gate.py", Path(b) / "gate.py")
            except (OSError, NotImplementedError) as e:
                self.skipTest(f"symlinks are refused here ({e})")
            _, out, _ = run(json.dumps({"model": "claude-fable-5-1"}), gate=Path(b) / "gate.py", **self.env)
            self.assertEqual(out, other["fable-layer.md"])

    # ---- the command line (tests 13-16)

    def transcript(self, path, records):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("".join(json.dumps(r) + "\n" for r in records), encoding="utf-8")
        return path

    @staticmethod
    def injection(stdout, command="/home/u/.claude/doctrine/gate.py", name="SessionStart:startup", code=0, stderr="",
                  args=None, content=None, ts="2026-09-25T10:00:00.000Z"):
        att = {"type": "hook_success", "hookName": name, "hookEvent": "SessionStart", "content": content or stdout,
               "stdout": stdout, "stderr": stderr, "exitCode": code, "command": command}
        if args is not None:
            att["args"] = args
        return {"type": "attachment", "timestamp": ts, "attachment": att}

    @staticmethod
    def reply(model):
        return {"type": "assistant", "message": {"model": model, "content": [{"type": "text", "text": "ok"}]}}

    def status(self, d, *paths, extra=(), **env):
        code, out, err = run(gate=d / "gate.py", args=["--status", *extra, "--transcript", *map(str, paths)],
                             **dict(self.env, **env))
        self.assertEqual(code, 0, err)
        return out

    def test_status_reads_the_latest_injection_record(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = fixture_doctrine(tmp)
            (d / "harness" / "live" / "layers").mkdir(parents=True)
            dated = "# Working notes (injected: this session runs Claude Opus 5.5)\n\na dated body\n"
            (d / "harness" / "live" / "layers" / "opus-5-5-layer-2026-09-22.md").write_text(dated, encoding="utf-8")
            L, t = FIXTURE_LAYERS, Path(tmp) / "t"
            fable, o55 = L["fable-layer.md"], L["opus-5-5-layer.md"]
            # (a) a cat of a layer inside a tool result is not an injection
            a = self.transcript(t / "a.jsonl", [{"type": "user", "message": {"content": [
                {"type": "tool_result", "content": fable}]}}, self.reply("claude-fable-5-1")])
            # (b) two injections: the latest wins; (c) a resume onto another layer is reported
            b = self.transcript(t / "b.jsonl", [self.injection(fable), self.reply("claude-fable-5-1"),
                                                 self.injection(o55, name="SessionStart:resume"),
                                                 self.reply("claude-opus-5-5")])
            # (d) a forced dated layer is named by file
            dd = self.transcript(t / "d.jsonl", [self.injection(dated), self.reply("claude-opus-5-5")])
            # (e) exit 1 with stderr: nothing injected
            e = self.transcript(t / "e.jsonl", [self.injection("", code=1, stderr="gate.py: layer x is missing or unreadable; injecting nothing"),
                                                 self.reply("claude-opus-5-5")])
            # (f) exec form, the launcher's -c form included, and the old shell loader
            f = self.transcript(t / "f.jsonl", [self.injection(o55, command="/usr/bin/python3 -c 'import os' /home/u/.claude/doctrine/gate.py"),
                                                 self.reply("claude-opus-5-5")])
            f2 = self.transcript(t / "f2.jsonl", [self.injection(fable, command="/usr/bin/python3",
                                                                 args=["-c", "import os", "/h/.claude/doctrine/gate.py"]),
                                                  self.reply("claude-fable-5-1")])
            f3 = self.transcript(t / "f3.jsonl", [self.injection(fable, command="/h/.claude/doctrine/gate.sh"),
                                                  self.reply("claude-fable-5-1")])
            # (g) a persisted content with the full stdout: identified from stdout
            g = self.transcript(t / "g.jsonl", [self.injection(fable, content="<persisted-output>preview</persisted-output>"),
                                                 self.reply("claude-fable-5-1")])
            # a mismatch: the header names Opus 5.5, the replies are Fable
            m = self.transcript(t / "m.jsonl", [self.injection(o55), self.reply("claude-fable-5-1"),
                                                 self.reply("claude-haiku-4-5")])
            u = self.transcript(t / "u.jsonl", [self.injection(o55.replace("Claude Opus 5.5", "an undetected model")),
                                                 self.reply("claude-opus-5-5")])
            out = self.status(d, a, b, dd, e, f, f2, f3, g, m, u)
            by = {}
            for line in out.splitlines():
                for p in (a, b, dd, e, f, f2, f3, g, m, u):
                    if line.endswith(f"/{p.name}"):
                        by[p.name] = line
            self.assertIn("no injection record", by["a.jsonl"])
            self.assertIn("SessionStart:resume  Claude Opus 5.5  opus-5-5-layer.md  exit 0  reply claude-opus-5-5", by["b.jsonl"])
            self.assertIn("Claude Opus 5.5  opus-5-5-layer-2026-09-22.md  exit 0", by["d.jsonl"])
            self.assertIn("nothing injected  exit 1", by["e.jsonl"])
            self.assertIn("missing or unreadable", by["e.jsonl"])
            self.assertIn("Claude Opus 5.5  opus-5-5-layer.md  exit 0", by["f.jsonl"])
            self.assertIn("Claude Fable 5.1  fable-layer.md  exit 0", by["f2.jsonl"])
            self.assertIn("Claude Fable 5.1  fable-layer.md  exit 0", by["f3.jsonl"])
            self.assertIn("Claude Fable 5.1  fable-layer.md  exit 0", by["g.jsonl"])
            self.assertIn("reply claude-fable-5-1  MISMATCH", by["m.jsonl"])       # Haiku replies are skipped
            self.assertIn("UNDETECTED", by["u.jsonl"])
            for name in ("b.jsonl", "d.jsonl", "f.jsonl", "f2.jsonl", "g.jsonl"):
                self.assertNotIn("MISMATCH", by[name], name)
            self.assertNotIn(fable.split("\n")[2], out)                           # never the layer text itself
            j = json.loads(run(gate=d / "gate.py", args=["--status", "--json", "--transcript", str(b), str(e)],
                               **self.env)[1])
            self.assertEqual([s["layer"] for s in j["sessions"]], ["opus-5-5-layer.md", None])
            self.assertEqual(j["sessions"][0]["header"], "Claude Opus 5.5")
            self.assertEqual(j["sessions"][1]["exit"], 1)

    @unittest.skipIf(os.name == "nt", "the session process's command line is read with ps")
    def test_gate_now_ignores_claude_pid(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = fixture_doctrine(tmp)
            Path(self.cfg.name, "settings.json").write_text(json.dumps({"model": "opus"}), encoding="utf-8")
            t = self.transcript(Path(tmp) / "s.jsonl", [self.injection(FIXTURE_LAYERS["fable-layer.md"]),
                                                        self.reply("claude-fable-5-1")])
            fake = subprocess.Popen(["bash", "-c", "exec -a \"claude --model claude-sonnet-5\" sleep 30"])
            self.addCleanup(lambda: (fake.kill(), fake.wait()))
            time.sleep(0.3)
            plain = self.status(d, t)
            self.assertEqual(self.status(d, t, CLAUDE_PID=str(fake.pid)), plain)
            self.assertIn("gate now: opus → Claude Opus 5.5 · opus-5-5-layer.md", plain)
            self.assertIn("gate now: claude-fable-5-1 → Claude Fable 5.1 · fable-layer.md", plain)

    def test_table_and_resolve_are_json(self):
        code, out, _ = run(args=["--table"], **self.env)
        self.assertEqual(code, 0)
        table = json.loads(out)
        self.assertEqual(table["schema"], 1)
        self.assertLessEqual({"claude-opus-5", "claude-opus-5-5", "claude-fable-5-1", "claude-sonnet-5"}, set(table["measured"]))
        self.assertEqual(table["default"], "opus-5-5-layer.md")
        self.assertEqual(table["alias_env"], "ANTHROPIC_DEFAULT_<FAMILY>_MODEL")
        for key in ("family_layer", "aliases", "aliases_from"):
            self.assertIn(key, table)
        code, out, _ = run(args=["--resolve", "opus", "claude-opus-6"], **self.env)
        self.assertEqual(code, 0)
        got = json.loads(out)
        self.assertEqual(got, [
            {"input": "opus", "id": "claude-opus-5-5", "family": "opus", "how": "alias", "layer": "opus-5-5-layer.md",
             "header": "Claude Opus 5.5"},
            {"input": "claude-opus-6", "id": "claude-opus-6", "family": "opus", "how": "id", "layer": "opus-5-5-layer.md",
             "header": "Claude Opus 6"}])
        got = json.loads(run(args=["--resolve", "opus", "", "vendor/model-x"], ANTHROPIC_DEFAULT_OPUS_MODEL="claude-opus-5",
                             **self.env)[1])
        self.assertEqual([(g["id"], g["how"], g["layer"], g["header"]) for g in got],
                         [("claude-opus-5", "alias", "opus-layer.md", "Claude Opus 5"),
                          ("", "undetected", "opus-5-5-layer.md", "an undetected model"),
                          ("vendor/model-x", "unknown", "opus-5-5-layer.md", "vendor/model-x")])
        code, _, err = run(args=["--bogus"], **self.env)
        self.assertEqual(code, 2)
        self.assertIn("usage", err.lower())

    def test_doctor_flags_a_missing_layer(self):
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as nopath:
            d = fixture_doctrine(tmp)
            code, out, _ = run(gate=d / "gate.py", args=["--doctor"], PATH=nopath, **self.env)
            self.assertEqual(code, 0, out)
            self.assertNotIn("FAIL", out)
            (d / "fable-layer.md").unlink()
            (d / "opus-layer.md").write_text("no header here\n", encoding="utf-8")
            code, out, _ = run(gate=d / "gate.py", args=["--doctor"], PATH=nopath, **self.env)
            self.assertEqual(code, 1)
            self.assertRegex(out, r"FAIL .*fable-layer\.md")
            self.assertRegex(out, r"FAIL .*opus-layer\.md")

    # ---- the case hook log (test 17a; G-26 R5-Y1)

    def test_the_loader_logs_its_run_once_and_only_when_asked(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            log = tmp / "hooks.jsonl"
            stdin = json.dumps({"hook_event_name": "SessionStart", "model": "claude-opus-5-5"})
            code, out, err = run_raw((), stdin, XBT_EVAL_HOOK_LOG=str(log), **self.env)
            self.assertEqual(code, 0)
            lines = log.read_text(encoding="utf-8").splitlines()
            self.assertEqual(len(lines), 1)
            rec = json.loads(lines[0])
            self.assertEqual(set(rec), {"event", "script", "exit", "stdout_sha256", "t"})
            self.assertEqual((rec["event"], rec["exit"], rec["stdout_sha256"], rec["script"]),
                             ("SessionStart", 0, hashlib.sha256(out).hexdigest(), os.path.realpath(GATE)))
            self.assertTrue(rec["t"].endswith("Z"))
            for unset in ({}, {"XBT_EVAL_HOOK_LOG": ""}):
                with tempfile.TemporaryDirectory() as quiet:
                    self.assertEqual(run_raw((), stdin, **dict(self.env, TMPDIR=quiet, **unset)), (code, out, err))
                    self.assertEqual(os.listdir(quiet), [])
            # a missing layer: one line, exit 1, the digest of empty stdout
            d = fixture_doctrine(tmp / "x", {k: v for k, v in FIXTURE_LAYERS.items() if k != "opus-5-5-layer.md"})
            log2 = tmp / "missing.jsonl"
            code, out, _ = run_raw((), stdin, gate=d / "gate.py", XBT_EVAL_HOOK_LOG=str(log2), **self.env)
            rec2 = json.loads(log2.read_text(encoding="utf-8"))
            self.assertEqual((code, out, rec2["exit"], rec2["stdout_sha256"]), (1, b"", 1, hashlib.sha256(b"").hexdigest()))
            # through the launcher entry install.py builds: one line (the launcher's), equal to the direct run's but t
            entry = installer().launched(sys.executable, str(GATE), 10)
            log3 = tmp / "launched.jsonl"
            r = subprocess.run([entry["command"], *entry["args"]], input=stdin.encode("utf-8"), capture_output=True,
                               env=clean_env(XBT_EVAL_HOOK_LOG=str(log3), **self.env))
            self.assertEqual(r.returncode, 0, r.stderr)
            lines3 = log3.read_text(encoding="utf-8").splitlines()
            self.assertEqual(len(lines3), 1)
            rec3 = json.loads(lines3[0])
            self.assertEqual({k: v for k, v in rec3.items() if k != "t"}, {k: v for k, v in rec.items() if k != "t"})

    # ---- G-34 point 2: a live launcher entry is reported current or stale

    def test_status_reports_each_launcher_entry_current_or_stale(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            d = fixture_doctrine(root)
            shutil.copy2(INSTALLER, root / "install.py")
            inst = installer()
            fresh = inst.launched("/usr/bin/python3", str(d / "gate.py"), 10)
            stale = dict(fresh, args=["-c", fresh["args"][1] + "\n# an older launcher\n", *fresh["args"][2:]])
            turn = inst.launched("/usr/bin/python3", "/x/doc-guardrails/turn_check.py", 30)
            Path(self.cfg.name, "settings.json").write_text(json.dumps({"hooks": {
                "SessionStart": [{"hooks": [fresh]}], "Stop": [{"hooks": [dict(turn, args=["-c", "old", turn["args"][2]])]}],
                "PostToolUse": [{"matcher": "Write|Edit", "hooks": [{"type": "command", "command": "/x/hook.sh"}]}]}}),
                encoding="utf-8")
            out = run(gate=d / "gate.py", args=["--status", "0"], **self.env)[1]
            self.assertIn("launcher: SessionStart gate.py current", out)
            self.assertIn("launcher: Stop turn_check.py stale", out)
            self.assertNotIn("hook.sh", out)
            Path(self.cfg.name, "settings.json").write_text(json.dumps({"hooks": {
                "SessionStart": [{"hooks": [stale]}]}}), encoding="utf-8")
            self.assertIn("launcher: SessionStart gate.py stale", run(gate=d / "gate.py", args=["--status", "0"], **self.env)[1])
            (root / "install.py").unlink()
            self.assertIn("launcher: not checked", run(gate=d / "gate.py", args=["--status", "0"], **self.env)[1])


# ---- helpers (after the class, so the tests above keep their line numbers)
import hashlib  # noqa: E402
import importlib.util  # noqa: E402
import shutil  # noqa: E402
import time  # noqa: E402

ROOT = GATE.parent.parent
INSTALLER = ROOT / "install.py" if (ROOT / "install.py").is_file() else ROOT / "public" / "install.py"
CLEAN = ("DOCTRINE", "CLAUDE_CONFIG_DIR", "CLAUDE_PID", "ANTHROPIC_MODEL", "ANTHROPIC_DEFAULT_", "CLAUDE_JOB_DIR",
         "XBT_EVAL_HOOK_LOG")
# Fixture layers, written in the shape of the real ones (their first lines are the real first lines).
FIXTURE_LAYERS = {
    "opus-layer.md": "# Working doctrine (injected: this session runs Claude Opus 5)\n\nthe opus body\n",
    "opus-5-5-layer.md": "# Working notes (injected: this session runs Claude Opus 5.5)\n\nthe opus 5.5 body\n",
    "fable-layer.md": "# Working notes (injected: this session runs Claude Fable 5.1)\n\nthe fable body\n",
}


def clean_env(**env):
    base = {k: v for k, v in os.environ.items() if not k.startswith(CLEAN)}
    base.update(env)
    return base


def run_raw(args=(), stdin="", gate=GATE, **env):
    r = subprocess.run([sys.executable, str(gate), *args], input=stdin.encode("utf-8"), env=clean_env(**env),
                       capture_output=True)
    return r.returncode, r.stdout, r.stderr


def first(out):
    return out.splitlines()[0] if out else ""


def installer():
    spec = importlib.util.spec_from_file_location("xbt_install_for_gate_tests", INSTALLER)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def fixture_doctrine(root, layers=FIXTURE_LAYERS):
    """A doctrine folder under root: a copy of gate.py with the fixture layers beside it."""
    d = Path(root) / "doctrine"
    d.mkdir(parents=True, exist_ok=True)
    shutil.copy2(GATE, d / "gate.py")
    for name, text in layers.items():
        (d / name).write_text(text, encoding="utf-8")
    return d


if __name__ == "__main__":
    unittest.main()
