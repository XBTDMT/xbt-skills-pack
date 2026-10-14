"""Tests for install.py: a whole install into a temporary Claude folder, with a fake `claude` that records calls.

The same file runs in every copy of the pack (the public repo, the kit) and on CI, and each copy has its own
install-defaults.json, so every test names the defaults it installs with (`--defaults`): PUBLIC, the public copy's
(the skills and the doc check), or ALL_ON, the kit's (everything). No test depends on which copy it sits in."""
import contextlib
import io
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

PACK = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PACK))
import install  # noqa: E402

PUBLIC = {"doctrine": False, "plugins": False, "doc_check": True, "skill_checks": True}
ALL_ON = {"doctrine": True, "plugins": True, "doc_check": True, "skill_checks": True}

FAKE_CLAUDE = """#!{python}
import json, os, sys
log = os.environ["FAKE_CLAUDE_LOG"]
with open(log, "a", encoding="utf-8") as fh:
    fh.write(json.dumps(sys.argv[1:]) + "\\n")
args = sys.argv[1:]
fail = os.environ.get("FAKE_CLAUDE_FAIL", "")
if fail and fail in " ".join(args):
    print("Error: network unreachable", file=sys.stderr)
    sys.exit(1)
if args[:3] == ["plugin", "marketplace", "list"]:
    print(os.environ.get("FAKE_CLAUDE_MARKETPLACES", ""))
"""


def write_defaults(folder, options, about="test fixture"):
    """An install-defaults.json in its own folder, so a message naming the file reads as the real one would."""
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / "install-defaults.json"
    path.write_text(json.dumps(dict({"_about": about}, **options)), encoding="utf-8")
    return path


class InstallRun(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.claude_dir = self.root / "dot-claude"
        self.profile = self.root / "Documents" / "PowerShell" / "profile.ps1"
        self.defaults = {"public": write_defaults(self.root / "defaults-public", PUBLIC),
                         "all_on": write_defaults(self.root / "defaults-all-on", ALL_ON)}
        bin_dir = self.root / "bin"
        bin_dir.mkdir()
        # On Windows `shutil.which("claude")` only considers PATHEXT names, so the fake is a .bat that runs the script.
        if os.name == "nt":
            script = bin_dir / "fake_claude.py"
            script.write_text(FAKE_CLAUDE.format(python=sys.executable), encoding="utf-8")
            (bin_dir / "claude.bat").write_text(f'@echo off\r\n"{sys.executable}" "{script}" %*\r\n', encoding="utf-8")
        else:
            fake = bin_dir / "claude"
            fake.write_text(FAKE_CLAUDE.format(python=sys.executable), encoding="utf-8")
            fake.chmod(fake.stat().st_mode | stat.S_IEXEC)
        self.bin_dir = bin_dir
        self.log = self.root / "calls.jsonl"
        env = {"PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}", "FAKE_CLAUDE_LOG": str(self.log)}
        patcher = mock.patch.dict(os.environ, env)
        patcher.start()
        self.addCleanup(patcher.stop)

    def install(self, *extra, defaults="public"):
        out = io.StringIO()
        path = self.defaults[defaults] if defaults in self.defaults else defaults
        with contextlib.redirect_stdout(out):
            code = install.main(["--claude-dir", str(self.claude_dir), "--profile", str(self.profile),
                                 "--defaults", str(path), *extra])
        return code, out.getvalue()

    def calls(self):
        return [json.loads(l) for l in self.log.read_text(encoding="utf-8").splitlines()] if self.log.exists() else []

    def hooks(self):
        return json.loads((self.claude_dir / "settings.json").read_text(encoding="utf-8"))["hooks"]

    def test_a_default_install_is_the_skills_and_the_end_of_turn_check(self):
        code, out = self.install()
        self.assertEqual(code, 0, out)
        self.assertEqual(sorted(p.name for p in self.claude_dir.iterdir()), ["settings.json", "skills"])
        self.assertEqual(sorted(p.name for p in (self.claude_dir / "skills").iterdir()), sorted(install.SKILLS))
        self.assertIn("greenman", install.SKILLS)
        eli5 = self.claude_dir / "skills" / "eli5"
        fresh = install.launched(sys.executable, str(eli5 / "skill_fresh.py"), 5)
        self.assertEqual(self.hooks(), {
            "Stop": [{"hooks": [install.launched(sys.executable, str(self.claude_dir / "skills" / "doc-guardrails" /
                                                                     "turn_check.py"), 30)]},
                     {"hooks": [install.launched(sys.executable, str(eli5 / "receipt_check.py"), 10)]}],
            "UserPromptSubmit": [{"hooks": [fresh]}], "SessionStart": [{"matcher": "resume", "hooks": [fresh]}]})
        self.assertFalse(self.profile.exists())
        self.assertEqual(self.calls(), [])

    def test_no_switches_follow_the_defaults_file(self):
        code, out = self.install()                                          # PUBLIC: today's default, kept
        self.assertEqual(code, 0, out)
        self.assertEqual(sorted(p.name for p in self.claude_dir.iterdir()), ["settings.json", "skills"])
        self.assertIn("Doctrine\n  skipped", out)
        self.assertEqual(self.calls(), [])
        shutil.rmtree(self.claude_dir)
        code, out = self.install(defaults="all_on")                         # the kit's: everything, no switch
        self.assertEqual(code, 0, out)
        for name in install.DOCTRINE_FILES + ("cc.ps1",):
            self.assertTrue((self.claude_dir / "doctrine" / name).is_file(), name)
        self.assertTrue((self.claude_dir / "CLAUDE.md").is_file())
        hooks = self.hooks()
        self.assertEqual(sum(install.is_gate_hook(h) for g in hooks["SessionStart"] for h in g["hooks"]), 1)
        self.assertEqual(sum(install.is_turn_check_hook(h) for g in hooks["Stop"] for h in g["hooks"]), 1)
        self.assertIn(["plugin", "marketplace", "list"], self.calls())
        self.assertTrue(any(c[:2] == ["plugin", "install"] for c in self.calls()))
        self.assertIn(f'. "{self.claude_dir / "doctrine" / "cc.ps1"}"', self.profile.read_text(encoding="utf-8"))

    def test_skip_plugins_is_accepted_and_wins_over_the_defaults(self):
        code, out = self.install("--skip-plugins", defaults="all_on")
        self.assertEqual(code, 0, out)
        self.assertEqual([c for c in self.calls() if c[:1] == ["plugin"]], [])
        self.assertTrue((self.claude_dir / "doctrine" / "gate.py").is_file())    # the rest of the defaults still hold
        self.assertIn("Plugins\n  skipped (--skip-plugins)", out)
        shutil.rmtree(self.claude_dir)
        code, out = self.install("--skip-plugins", "--with-plugins", defaults="all_on")
        self.assertEqual(code, 2, out)
        self.assertIn("--skip-plugins and --with-plugins", out)
        self.assertFalse(self.claude_dir.exists())

    def test_the_old_kit_profile_block_is_replaced_in_place_keeping_bom_and_crlf(self):
        self.profile.parent.mkdir(parents=True)
        self.profile.write_bytes(b"\xef\xbb\xbf" + ("$CcEngineerModel = 'claude-sonnet-5'\r\n# >>> claude starter kit >>>\r\n"
                                                     ". \"C:\\old\\cc.ps1\"\r\n# <<< claude starter kit <<<\r\n"
                                                     "Write-Host hi\r\n").encode("utf-8"))
        code, out = self.install(defaults="all_on")
        self.assertEqual(code, 0, out)
        raw = self.profile.read_bytes()
        self.assertTrue(raw.startswith(b"\xef\xbb\xbf"), raw[:8])
        text = raw[3:].decode("utf-8")
        self.assertNotIn("\n", text.replace("\r\n", ""))                       # every line CRLF
        lines = text.split("\r\n")
        self.assertEqual(lines, ["$CcEngineerModel = 'claude-sonnet-5'", install.PROFILE_BEGIN,
                                 f'. "{self.claude_dir / "doctrine" / "cc.ps1"}"', install.PROFILE_END,
                                 "Write-Host hi", ""])
        again = self.install(defaults="all_on")
        self.assertEqual(again[0], 0)
        self.assertEqual(self.profile.read_bytes(), raw)                         # a second run changes nothing

    def test_both_markers_present_end_as_one_block(self):
        block = lambda begin, end, body: f"{begin}\n{body}\n{end}\n"
        legacy = install.LEGACY_MARKERS[0]
        new = block(install.PROFILE_BEGIN, install.PROFILE_END, '. "new"')
        for text in ("top\n" + block(*legacy, '. "old"') + "middle\n" + block(install.PROFILE_BEGIN, install.PROFILE_END, '. "cur"') + "end\n",
                     "top\n" + block(install.PROFILE_BEGIN, install.PROFILE_END, '. "cur"') + "middle\n" + block(*legacy, '. "old"') + "end\n"):
            out = install.replace_block(text, new)
            self.assertEqual(out.count(install.PROFILE_BEGIN), 1, out)
            self.assertNotIn(legacy[0], out)
            self.assertNotIn(legacy[1], out)
            self.assertIn(new, out)
            self.assertEqual([l for l in out.splitlines() if not l.startswith(("#", ". "))], ["top", "middle", "end"])
        # the legacy block alone is replaced where it stands
        self.assertEqual(install.replace_block("a\n" + block(*legacy, '. "old"') + "b\n", new), "a\n" + new + "b\n")

    def test_the_skills_installed_are_the_folders_shipped(self):
        shipped = sorted(p.name for p in (PACK / "skills").iterdir() if (p / "SKILL.md").is_file())
        self.assertEqual(list(install.SKILLS), shipped)                         # the real copy: no second list
        copy = self.root / "copy"
        for name in ("alpha", "beta"):
            (copy / "skills" / name).mkdir(parents=True)
            (copy / "skills" / name / "SKILL.md").write_text(f"# {name}\n", encoding="utf-8")
        (copy / "skills" / "notes").mkdir()                                    # a folder without SKILL.md is no skill
        shutil.copy2(PACK / "install.py", copy / "install.py")
        write_defaults(copy, PUBLIC)
        r = subprocess.run([sys.executable, str(copy / "install.py"), "--claude-dir", str(self.claude_dir),
                            "--no-doc-check"], capture_output=True, text=True, timeout=120)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(sorted(p.name for p in (self.claude_dir / "skills").iterdir()), ["alpha", "beta"])

    def test_settings_written_by_the_old_kit_installer_end_with_one_gate_and_one_turn_check(self):
        self.claude_dir.mkdir(parents=True)
        old = {"hooks": {"SessionStart": [{"hooks": [{"type": "command", "command": sys.executable,
                                                      "args": [str(self.claude_dir / "doctrine" / "gate.py")], "timeout": 10}]}],
                         "Stop": [{"hooks": [{"type": "command", "command": sys.executable,
                                              "args": [str(self.claude_dir / "skills" / "doc-guardrails" / "turn_check.py")],
                                              "timeout": 30}]}]}}
        (self.claude_dir / "settings.json").write_text(json.dumps(old, indent=2) + "\n", encoding="utf-8")
        code, out = self.install(defaults="all_on")
        self.assertEqual(code, 0, out)
        hooks = self.hooks()
        self.assertEqual(sum(install.is_gate_hook(h) for g in hooks["SessionStart"] for h in g["hooks"]), 1)
        self.assertEqual(sum(install.is_turn_check_hook(h) for g in hooks["Stop"] for h in g["hooks"]), 1)

    def test_a_missing_or_bad_defaults_file_stops_the_install(self):
        cases = {"missing": (None, "not found"),
                 "not-json": ("not json", "not valid JSON"),
                 "not-bool": ('{"doctrine": "yes", "plugins": false, "doc_check": true}', "doctrine is not true or false"),
                 "extra-key": ('{"doctrine": true, "plugins": false, "doc_check": true, "extra": 1}', "unknown key extra"),
                 "missing-key": ('{"doctrine": true, "plugins": false}', "doc_check is missing"),
                 "not-object": ("[1, 2]", "not a JSON object")}
        for name, (body, why) in cases.items():
            with self.subTest(name):
                path = self.root / name / "install-defaults.json"
                if body is not None:
                    path.parent.mkdir(parents=True)
                    path.write_text(body, encoding="utf-8")
                for extra in ((), ("--print-options",)):
                    code, out = self.install(*extra, defaults=str(path))
                    self.assertEqual(code, 2, out)
                    self.assertIn(f"install-defaults.json: {why}", out)
                    self.assertIn("run git pull (or restore the file) and run setup again", out)
                    self.assertFalse(self.claude_dir.exists(), "something was written")
                    self.assertFalse(self.profile.exists())
                    self.assertEqual(self.calls(), [])

    def test_print_options_reports_the_effective_options(self):
        for defaults, extra, want in (("public", (), PUBLIC),
                                      ("public", ("--with-doctrine", "--with-plugins"), ALL_ON),
                                      ("public", ("--no-doc-check",), dict(PUBLIC, doc_check=False)),
                                      ("all_on", (), ALL_ON),
                                      ("all_on", ("--skip-plugins",), dict(ALL_ON, plugins=False)),
                                      ("all_on", ("--skip-plugins", "--no-doc-check"),
                                       dict(ALL_ON, plugins=False, doc_check=False))):
            with self.subTest(defaults=defaults, extra=extra):
                code, out = self.install("--print-options", *extra, defaults=defaults)
                self.assertEqual(code, 0, out)
                self.assertEqual(out.strip(), json.dumps(want))
                self.assertFalse(self.claude_dir.exists())
        self.assertEqual(self.install("--print-options", "--skip-plugins", "--with-plugins")[0], 2)

    def test_the_turn_check_is_registered_once_keeps_other_stop_hooks_and_runs(self):
        self.claude_dir.mkdir(parents=True)
        (self.claude_dir / "settings.json").write_text(json.dumps(
            {"hooks": {"Stop": [{"hooks": [{"type": "command", "command": "stop.cmd"}]}]}}), encoding="utf-8")
        self.install()
        self.install()
        stop = [h for g in self.hooks()["Stop"] for h in g["hooks"]]
        self.assertEqual(stop[0], {"type": "command", "command": "stop.cmd"})
        checks = [h for h in stop if install.is_turn_check_hook(h)]
        self.assertEqual(len(checks), 1)
        r = subprocess.run([checks[0]["command"], *checks[0]["args"]], input=b"not json", capture_output=True)
        self.assertEqual((r.returncode, r.stdout), (0, b""))  # a malformed payload never breaks a session

    def test_the_brief_hook_is_registered_once_on_subagent_start_and_other_hooks_are_kept(self):
        """The doctrine's brief hook: one SubagentStart entry with no matcher (every agent, whatever started
        it), in the launcher's form, only with the doctrine; an older install's PreToolUse Agent entry is replaced,
        never kept beside it; every other hook stays; with brief.py gone the entry exits 0 (the launcher)."""
        other, watcher = {"type": "command", "command": "guard.cmd"}, {"type": "command", "command": "agents.cmd"}
        brief = self.claude_dir / "doctrine" / "brief.py"
        old = install.launched(sys.executable, str(brief), 5)                  # an earlier install's registration
        self.claude_dir.mkdir(parents=True)
        (self.claude_dir / "settings.json").write_text(json.dumps(
            {"hooks": {"PreToolUse": [{"matcher": "Bash", "hooks": [other]}, {"matcher": "Agent", "hooks": [old]}],
                       "SubagentStart": [{"matcher": "Explore", "hooks": [watcher]}]}}), encoding="utf-8")
        self.install("--with-doctrine")
        self.install("--with-doctrine")
        hooks = self.hooks()
        self.assertEqual(hooks["PreToolUse"], [{"matcher": "Bash", "hooks": [other]}])
        briefs = [(event, g, h) for event, gs in hooks.items() for g in gs for h in g["hooks"] if install.is_brief_hook(h)]
        self.assertEqual(len(briefs), 1, hooks)
        event, group, entry = briefs[0]
        self.assertEqual(event, "SubagentStart")
        self.assertEqual(group, {"hooks": [install.launched(sys.executable, str(brief), 5)]})   # no matcher: every agent
        self.assertEqual(hooks["SubagentStart"][0], {"matcher": "Explore", "hooks": [watcher]})
        self.assertTrue(brief.is_file() and (self.claude_dir / "doctrine" / "key-rules.md").is_file())
        brief.unlink()
        r = subprocess.run([entry["command"], *entry["args"]], input=b'{"hook_event_name": "SubagentStart"}',
                           capture_output=True)
        self.assertEqual((r.returncode, r.stdout), (0, b""))
        # an older install whose only PreToolUse hook was the brief's: the event goes, never left an empty list
        shutil.rmtree(self.claude_dir)
        self.claude_dir.mkdir(parents=True)
        (self.claude_dir / "settings.json").write_text(json.dumps(
            {"hooks": {"PreToolUse": [{"matcher": "Agent", "hooks": [old]}]}}), encoding="utf-8")
        self.install("--with-doctrine")
        self.assertNotIn("PreToolUse", self.hooks())
        self.assertEqual(self.hooks()["SubagentStart"], [{"hooks": [install.launched(sys.executable, str(brief), 5)]}])
        # without the doctrine: none
        shutil.rmtree(self.claude_dir)
        self.install()
        self.assertNotIn("PreToolUse", self.hooks())
        self.assertNotIn("SubagentStart", self.hooks())
        self.assertFalse(any(install.is_brief_hook(h) for gs in self.hooks().values() for g in gs for h in g["hooks"]))

    def test_no_doc_check_installs_the_skills_and_nothing_else(self):
        code, out = self.install("--no-doc-check", "--no-skill-checks")
        self.assertEqual(code, 0, out)
        self.assertEqual(sorted(p.name for p in self.claude_dir.iterdir()), ["skills"])

    def test_skill_checks_registered_once(self):
        """Two installs: one UserPromptSubmit, one SessionStart (resume) and one Stop entry for the eli5 scripts, each
        through the tolerant launcher, timeouts 5, 5 and 10, the scripts under the install's folder; other hooks kept;
        --no-skill-checks registers none; each entry run with its script absent exits 0."""
        settings = self.claude_dir / "settings.json"
        self.claude_dir.mkdir(parents=True)
        mine = {"type": "command", "command": "/usr/local/bin/my-prompt-hook"}
        settings.write_text(json.dumps({"hooks": {"UserPromptSubmit": [{"hooks": [mine]}]}}), encoding="utf-8")
        for _ in range(2):
            code, out = self.install()
            self.assertEqual(code, 0, out)
        hooks = self.hooks()
        eli5 = self.claude_dir / "skills" / "eli5"
        fresh = install.launched(sys.executable, str(eli5 / "skill_fresh.py"), 5)
        receipt = install.launched(sys.executable, str(eli5 / "receipt_check.py"), 10)
        self.assertEqual(hooks["UserPromptSubmit"], [{"hooks": [mine]}, {"hooks": [fresh]}])
        self.assertEqual(hooks["SessionStart"], [{"matcher": "resume", "hooks": [fresh]}])
        self.assertEqual(sum(h == receipt for g in hooks["Stop"] for h in g["hooks"]), 1)
        self.assertEqual(sum(install.is_turn_check_hook(h) for g in hooks["Stop"] for h in g["hooks"]), 1)
        for entry in (fresh, receipt):
            (eli5 / Path(entry["args"][2]).name).unlink(missing_ok=True)
            r = subprocess.run([entry["command"], *entry["args"]], input=b'{"hook_event_name": "Stop"}',
                               capture_output=True, timeout=60)
            self.assertEqual((r.returncode, r.stdout), (0, b""))    # the plain exec form would exit 2: a block
        shutil.rmtree(self.claude_dir)
        code, out = self.install("--no-skill-checks")
        self.assertEqual(code, 0, out)
        text = json.dumps(self.hooks())
        self.assertNotIn("skill_fresh.py", text)
        self.assertNotIn("receipt_check.py", text)
        self.assertIn("turn_check.py", text)
        self.assertEqual(install.registered({}, "/py", "/cfg"), install.registered({}, "/py", "/cfg", skill_checks=True))

    def test_a_full_install_puts_everything_in_place(self):
        code, out = self.install("--with-doctrine", "--with-plugins")
        self.assertEqual(code, 0, out)
        for name in install.DOCTRINE_FILES + ("cc.ps1",):
            self.assertTrue((self.claude_dir / "doctrine" / name).is_file(), name)
        for skill in install.SKILLS:
            self.assertTrue((self.claude_dir / "skills" / skill / "SKILL.md").is_file(), skill)
        self.assertTrue((self.claude_dir / "skills" / "closeout" / "closeout.py").is_file())
        self.assertTrue((self.claude_dir / "CLAUDE.md").is_file())
        hooks = self.hooks()
        self.assertEqual(len(hooks["Stop"]), 2)                         # the turn check and the receipt check
        self.assertEqual(hooks["SessionStart"][0], {"hooks": [install.launched(sys.executable,
                                                                               str(self.claude_dir / "doctrine" / "gate.py"), 10)]})
        self.assertEqual(hooks["SessionStart"][1]["matcher"], "resume")
        profile = self.profile.read_text(encoding="utf-8")
        self.assertIn(f'. "{self.claude_dir / "doctrine" / "cc.ps1"}"', profile)

    def test_every_plugin_is_installed_then_switched_on_or_off_as_the_manifest_says(self):
        manifest = json.loads((PACK / "plugins.json").read_text(encoding="utf-8"))
        self.install("--with-plugins")
        calls = self.calls()
        added = [c[3] for c in calls if c[:3] == ["plugin", "marketplace", "add"]]
        self.assertEqual(sorted(added), sorted(manifest["marketplaces"].values()))
        for p in manifest["plugins"]:
            i = calls.index(["plugin", "install", p["id"], "--scope", "user"])
            self.assertEqual(calls[i + 1], ["plugin", "enable" if p["enabled"] else "disable", p["id"], "--scope", "user"])

    def test_a_marketplace_already_present_is_not_added_again(self):
        with mock.patch.dict(os.environ, {"FAKE_CLAUDE_MARKETPLACES": "claude-plugins-official"}):
            self.install("--with-plugins")
        added = [c[3] for c in self.calls() if c[:3] == ["plugin", "marketplace", "add"]]
        self.assertNotIn("anthropics/claude-plugins-official", added)
        self.assertIn("obra/superpowers-marketplace", added)

    def test_a_failed_plugin_is_reported_and_the_rest_still_install(self):
        with mock.patch.dict(os.environ, {"FAKE_CLAUDE_FAIL": "install context7@"}):
            code, out = self.install("--with-plugins")
        self.assertEqual(code, 1)
        self.assertIn("plugin context7@claude-plugins-official did not install: Error: network unreachable", out)
        self.assertIn(["plugin", "install", "superpowers@claude-plugins-official", "--scope", "user"], self.calls())

    def test_running_it_again_changes_nothing_and_backs_up_nothing(self):
        self.install("--with-doctrine", "--with-plugins")
        snapshot = sorted(p.relative_to(self.root).as_posix() for p in self.root.rglob("*") if "calls" not in p.name)
        code, out = self.install("--with-doctrine")
        self.assertEqual(code, 0)
        self.assertNotIn("backup", out)
        self.assertNotIn("write ", out)
        self.assertEqual(snapshot, sorted(p.relative_to(self.root).as_posix() for p in self.root.rglob("*")
                                          if "calls" not in p.name))

    def test_existing_settings_and_profile_are_kept_and_an_old_gate_hook_is_replaced(self):
        self.claude_dir.mkdir(parents=True)
        other_hook = {"type": "command", "command": "echo hi"}
        (self.claude_dir / "settings.json").write_text(json.dumps({
            "theme": "dark",
            "hooks": {"SessionStart": [{"hooks": [other_hook, {"type": "command", "command": "py C:/old/gate.py"}]}],
                      "Stop": [{"hooks": [{"type": "command", "command": "stop.cmd"}]}]}}), encoding="utf-8")
        self.profile.parent.mkdir(parents=True)
        self.profile.write_text("Set-Alias ll Get-ChildItem\n", encoding="utf-8")
        (self.claude_dir / "CLAUDE.md").write_text("mine\n", encoding="utf-8")
        code, out = self.install("--with-doctrine")
        self.assertEqual(code, 0, out)
        settings = json.loads((self.claude_dir / "settings.json").read_text(encoding="utf-8"))
        self.assertEqual(settings["theme"], "dark")
        self.assertEqual(settings["hooks"]["Stop"][0], {"hooks": [{"type": "command", "command": "stop.cmd"}]})
        self.assertTrue(install.is_turn_check_hook(settings["hooks"]["Stop"][1]["hooks"][0]))  # plus the turn check
        start = [h for g in settings["hooks"]["SessionStart"] for h in g["hooks"]]
        self.assertEqual(start[0], other_hook)
        self.assertEqual(sum(install.is_gate_hook(h) for h in start), 1)
        self.assertTrue(self.profile.read_text(encoding="utf-8").startswith("Set-Alias ll Get-ChildItem\n"))
        self.assertEqual((self.claude_dir / "CLAUDE.md").read_text(encoding="utf-8"), "mine\n")
        self.assertEqual(len(list(self.claude_dir.glob("settings.json.bak-*"))), 1)

    def test_a_profile_keeps_its_encoding_and_a_new_one_is_written_with_a_bom(self):
        # Windows PowerShell 5.1 reads a profile without a BOM as ANSI, so a new profile gets one and an existing
        # one keeps whatever it had: rewriting someone's accented lines into mojibake is not ours to do.
        self.profile.parent.mkdir(parents=True)
        self.profile.write_bytes("\ufeff# caf\u00e9\n".encode("utf-8"))
        self.assertEqual(self.install("--with-doctrine")[0], 0)
        after = self.profile.read_bytes()
        self.assertTrue(after.startswith(b"\xef\xbb\xbf"), after[:8])
        self.assertIn("caf\u00e9", after.decode("utf-8-sig"))

        self.profile.write_bytes("# plain ascii\n".encode("utf-8"))
        self.assertEqual(self.install("--with-doctrine")[0], 0)
        self.assertFalse(self.profile.read_bytes().startswith(b"\xef\xbb\xbf"))   # no BOM added to an existing file

        self.profile.unlink()
        self.assertEqual(self.install("--with-doctrine")[0], 0)
        self.assertTrue(self.profile.read_bytes().startswith(b"\xef\xbb\xbf"))    # a new profile: BOM, so 5.1 reads UTF-8

    def test_a_skill_that_differs_is_backed_up_before_it_is_replaced(self):
        old = self.claude_dir / "skills" / "eli5"
        old.mkdir(parents=True)
        (old / "SKILL.md").write_text("an older eli5\n", encoding="utf-8")
        self.install("--with-doctrine")
        self.assertEqual(list((self.claude_dir / "skills").glob("*.bak-*")), [])  # never beside the skills: it would load
        backups = list((self.claude_dir / "skill-backups").glob("eli5.bak-*"))
        self.assertEqual(len(backups), 1)
        self.assertEqual((backups[0] / "SKILL.md").read_text(encoding="utf-8"), "an older eli5\n")
        self.assertEqual((old / "SKILL.md").read_bytes(), (PACK / "skills" / "eli5" / "SKILL.md").read_bytes())

    def test_invalid_settings_json_stops_that_step_without_touching_the_file(self):
        self.claude_dir.mkdir(parents=True)
        (self.claude_dir / "settings.json").write_text("{ not json", encoding="utf-8")
        code, out = self.install("--with-doctrine")
        self.assertEqual(code, 1)
        self.assertEqual((self.claude_dir / "settings.json").read_text(encoding="utf-8"), "{ not json")
        self.assertIn("is not valid JSON", out)

    def test_dry_run_writes_nothing(self):
        for defaults in ("public", "all_on"):
            code, out = self.install("--dry-run", defaults=defaults)
            self.assertEqual(code, 0, out)
            self.assertFalse(self.claude_dir.exists())
            self.assertFalse(self.profile.exists())
            self.assertEqual(self.calls(), [])

    def test_the_installed_hook_runs_and_injects_the_doctrine(self):
        self.install("--with-doctrine")
        hook = self.hooks()["SessionStart"][0]["hooks"][0]
        r = subprocess.run([hook["command"], *hook["args"]], input=b'{"model": "claude-opus-5"}', capture_output=True,
                           env={**os.environ, "CLAUDE_CONFIG_DIR": str(self.claude_dir)})
        self.assertEqual(r.returncode, 0)
        self.assertTrue(r.stdout.startswith(b"# Working doctrine (injected: this session runs Claude Opus 5)"))

    @unittest.skipUnless(os.name == "nt" and shutil.which("powershell"), "setup.ps1 runs on the Windows CI leg only")
    def test_setup_ps1_follows_the_defaults_file(self):
        copy = self.root / "tree"
        shutil.copytree(PACK, copy, ignore=shutil.ignore_patterns(".git", "__pycache__"))

        def setup(options, *switches):
            write_defaults(copy, options)
            env = dict(os.environ, CLAUDE_CONFIG_DIR=str(self.claude_dir))
            r = subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File",
                                str(copy / "setup.ps1"), "-DryRun", *switches], capture_output=True, text=True,
                               timeout=600, env=env)
            return r.returncode, r.stdout + r.stderr

        code, out = setup(PUBLIC)
        self.assertEqual(code, 0, out)
        self.assertIn("Doctrine\n  skipped", out.replace("\r\n", "\n"))
        self.assertNotIn("plugin install", out)
        code, out = setup(ALL_ON)
        self.assertEqual(code, 0, out)
        after = out.replace("\r\n", "\n").partition("Doctrine\n")[2]
        self.assertIn("write", after.split("\n", 1)[0], out)                     # the Doctrine step writes, not skips
        self.assertIn("plugin install", out)
        code, out = setup(ALL_ON, "-SkipPlugins")
        self.assertEqual(code, 0, out)
        self.assertNotIn("plugin install", out)
        self.assertFalse(self.claude_dir.exists())


class HookMatching(unittest.TestCase):
    def test_only_the_packs_own_hook_files_are_recognised(self):
        for cmd, gate, turn in (("python3 ~/hooks/aggregate.py", False, False), ("py C:/old/gate.py", True, False),
                                ('"C:/Program Files/py.exe" "C:/x/doctrine/gate.py"', True, False),
                                ("python3 /x/skills/doc-guardrails/turn_check.py", False, True),
                                ("python3 ~/hooks/my_turn_check.py", False, False)):
            hook = {"type": "command", "command": cmd}
            self.assertEqual((install.is_gate_hook(hook), install.is_turn_check_hook(hook)), (gate, turn), cmd)
        self.assertTrue(install.is_gate_hook({"command": "/usr/bin/python3", "args": ["/c/doctrine/gate.py"]}))
        self.assertFalse(install.is_gate_hook({"command": "/usr/bin/python3", "args": ["/c/navigate.py"]}))
        self.assertTrue(install.is_brief_hook(install.launched("/py", "/c/doctrine/brief.py", 5)))
        self.assertFalse(install.is_brief_hook({"type": "command", "command": "python3 ~/hooks/my_brief.py"}))
        self.assertFalse(install.is_brief_hook(install.launched("/py", "/c/doctrine/gate.py", 10)))

    def test_registered_is_pure_and_builds_every_path_under_the_folder_it_is_given(self):
        base = {"theme": "dark", "hooks": {"Stop": [{"hooks": [{"type": "command", "command": "stop.cmd"}]}]}}
        before = json.dumps(base)
        out = install.registered(base, "/py", "/cfg", doctrine=True, doc_check=True, skill_checks=False)
        self.assertEqual(json.dumps(base), before)                               # the value passed in is untouched
        self.assertEqual(out["theme"], "dark")
        self.assertEqual(out["hooks"]["SessionStart"], [{"hooks": [install.launched("/py", str(Path("/cfg") / "doctrine" / "gate.py"), 10)]}])
        self.assertEqual(out["hooks"]["Stop"][1]["hooks"][0],
                         install.launched("/py", str(Path("/cfg") / "skills" / "doc-guardrails" / "turn_check.py"), 30))
        self.assertEqual(out["hooks"]["SubagentStart"],
                         [{"hooks": [install.launched("/py", str(Path("/cfg") / "doctrine" / "brief.py"), 5)]}])
        self.assertNotIn("PreToolUse", out)                                      # the brief is SubagentStart's
        self.assertNotIn("SubagentStart", install.registered({}, "/py", "/cfg", doctrine=False))
        self.assertEqual(install.registered({}, "/py", "/cfg", doctrine=False, doc_check=False, skill_checks=False), {})
        self.assertEqual(install.registered({}, "/py", "/cfg"), install.registered({}, "/py", "/cfg", doc_check=True))


class Launcher(unittest.TestCase):
    """R-26's tolerant launcher (G-22 D11): every hook entry the pack writes runs its script through it, so a missing
    script is one stderr line and exit 0, never a blocked prompt, agent call or stop; and, only when the evals ask,
    it writes the case hook log's one line per run (G-26 R5-Y1)."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(os.path.realpath(tmp.name))
        self.env = {k: v for k, v in os.environ.items() if k != "XBT_EVAL_HOOK_LOG"}

    def script(self, name, body):
        path = self.root / "scripts" / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body, encoding="utf-8")
        return path

    def launch(self, entry, stdin=b"", **env):
        r = subprocess.run([entry["command"], *entry["args"]], input=stdin, capture_output=True,
                           env=dict(self.env, **env), timeout=60)
        return r.returncode, r.stdout, r.stderr

    ECHO = ("import sys\n"
            "data = sys.stdin.read()\n"
            "print('text:' + data)\n"
            "sys.stdout.flush()\n"
            "sys.stdout.buffer.write(b'bytes:' + data.encode('utf-8') + b'\\n')\n"
            "print('argv', sys.argv[1:], file=sys.stderr)\n"
            "sys.exit(3)\n")

    def test_this_machines_gate_hook_is_recognised_so_none_is_added(self):
        """Test 24 (NEW-C2): the live entry this row writes is the installer's own gate hook."""
        claude_dir = self.root / "dot-claude"
        claude_dir.mkdir()
        defaults = write_defaults(self.root / "defaults", PUBLIC)
        live = install.launched("/usr/bin/python3", str(claude_dir / "doctrine" / "gate.py"), 10)
        shell = {"type": "command", "command": str(claude_dir / "doctrine" / "gate.sh"), "timeout": 10}
        for entry, want in ((live, 1), (shell, 2)):      # gate.sh: two hooks, the known-bad the live switch retires
            (claude_dir / "settings.json").write_text(json.dumps({"hooks": {"SessionStart": [{"hooks": [entry]}]}}),
                                                      encoding="utf-8")
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(install.main(["--claude-dir", str(claude_dir), "--defaults", str(defaults),
                                               "--with-doctrine", "--no-doc-check", "--no-skill-checks"]), 0)
            start = [h for g in json.loads((claude_dir / "settings.json").read_text(encoding="utf-8"))["hooks"]
                     ["SessionStart"] for h in g["hooks"]]
            self.assertEqual(len(start), want, start)
            self.assertEqual(sum(install.is_gate_hook(h) for h in start), 1)

    def test_a_launched_hook_whose_script_is_missing_exits_0(self):
        """Test 25 (G-22 D11, G-23 E12, G-24 R3-Y12, G-25 R4-Y8)."""
        gone = self.root / "gone" / "turn_check.py"
        code, out, err = self.launch(install.launched(sys.executable, str(gone), 30), b'{"hook_event_name": "Stop"}')
        self.assertEqual((code, out), (0, b""))
        self.assertEqual(err.decode("utf-8").splitlines(), [f"hook script absent: {gone}; nothing run"])
        # known-bad: the plain exec entry with the same missing script exits 2, which a Stop hook reads as a block
        r = subprocess.run([sys.executable, str(gone)], input=b"{}", capture_output=True)
        self.assertEqual(r.returncode, 2)
        echo = self.script("echo.py", self.ECHO)
        code, out, err = self.launch(install.launched(sys.executable, str(echo), 30), b'{"a": 1}')
        self.assertEqual(code, 3)
        self.assertEqual(out.replace(b"\r\n", b"\n"), b'text:{"a": 1}\nbytes:{"a": 1}\n')
        self.assertIn("argv []", err.decode("utf-8"))
        entry = install.launched(sys.executable, str(echo), 10, args=("--brief",))
        self.assertIn("argv ['--brief']", self.launch(entry)[2].decode("utf-8"))
        self.assertEqual(install.hook_files(entry), {"echo.py"})
        for args in ((), ("--brief",), ("--brief", "x")):
            self.assertEqual(set(install.launched(sys.executable, str(echo), 10, args=args)), {"type", "command", "args", "timeout"})
        import inspect
        self.assertEqual(list(inspect.signature(install.launched).parameters), ["python_exe", "script", "timeout", "args"])

    def test_the_launcher_logs_only_when_asked(self):
        """Test 25a (G-26 R5-Y1)."""
        import hashlib
        import threading
        echo = self.script("echo.py", self.ECHO)
        linked = self.root / "linked"
        try:
            os.symlink(echo.parent, linked)
            given = linked / "echo.py"                  # the path given and the real path differ
        except (OSError, NotImplementedError):
            given = echo
        entry = install.launched(sys.executable, str(given), 30)
        stdin = json.dumps({"hook_event_name": "Stop", "x": "é"}).encode("utf-8")
        with tempfile.TemporaryDirectory() as quiet:
            plain = self.launch(entry, stdin, TMPDIR=quiet)
            self.assertEqual(self.launch(entry, stdin, TMPDIR=quiet, XBT_EVAL_HOOK_LOG=""), plain)
            self.assertEqual(os.listdir(quiet), [])
        log = self.root / "hooks.jsonl"
        logged = self.launch(entry, stdin, XBT_EVAL_HOOK_LOG=str(log))
        self.assertEqual(logged, plain)                                    # same stdout, stderr and rc 3
        self.assertIn(b"text:" + stdin, plain[1])                         # the script got the same stdin bytes
        lines = log.read_text(encoding="utf-8").splitlines()
        self.assertEqual(len(lines), 1)
        rec = json.loads(lines[0])
        self.assertEqual(set(rec), {"event", "script", "exit", "stdout_sha256", "t"})
        self.assertEqual((rec["event"], rec["script"], rec["exit"], rec["stdout_sha256"]),
                         ("Stop", os.path.realpath(echo), 3, hashlib.sha256(plain[1]).hexdigest()))
        self.assertRegex(rec["t"], r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\.\d{3}Z$")
        for body, want in (("import sys\nsys.exit(None)\n", 0), ("raise ValueError('boom')\n", 1),
                           ("import sys\nsys.exit('bye')\n", 1)):
            one = self.root / f"one-{want}-{len(body)}.jsonl"
            code, _, err = self.launch(install.launched(sys.executable, str(self.script(f"s{len(body)}.py", body)), 5),
                                       b"not json", XBT_EVAL_HOOK_LOG=str(one))
            rec = json.loads(one.read_text(encoding="utf-8"))
            self.assertEqual((code, rec["exit"], rec["event"]), (want, want, ""))
            if "boom" in body:
                self.assertIn(b"Traceback", err)
                self.assertIn(b"ValueError: boom", err)
        absent = self.root / "absent.jsonl"
        code, out, _ = self.launch(install.launched(sys.executable, str(self.root / "nope.py"), 5), stdin,
                                   XBT_EVAL_HOOK_LOG=str(absent))
        self.assertEqual((code, out, absent.exists()), (0, b"", False))   # nothing ran: no line
        bad = self.launch(entry, stdin, XBT_EVAL_HOOK_LOG=str(self.root / "no-such-folder" / "hooks.jsonl"))
        self.assertEqual((bad[0], bad[1]), (plain[0], plain[1]))
        self.assertIn(b"hook log not written:", bad[2])
        both = self.root / "both.jsonl"
        runs = [threading.Thread(target=self.launch, args=(entry, stdin), kwargs={"XBT_EVAL_HOOK_LOG": str(both)})
                for _ in range(2)]
        for t in runs:
            t.start()
        for t in runs:
            t.join()
        self.assertEqual([json.loads(l)["exit"] for l in both.read_text(encoding="utf-8").splitlines()], [3, 3])

    def test_the_turn_check_runs_through_the_launcher_so_a_missing_script_never_blocks(self):
        """G-34 point 1: the Stop entry is the launcher's too, so a turn_check.py that is gone never blocks a stop."""
        out = install.registered({}, sys.executable, str(self.root / "cfg"), doctrine=True, doc_check=True)
        for event in ("SessionStart", "Stop", "SubagentStart"):
            entry = out["hooks"][event][0]["hooks"][0]
            self.assertEqual(entry["args"][:2], ["-c", install.LAUNCHER], event)
            self.assertEqual(self.launch(entry, b'{"hook_event_name": "Stop"}')[:2], (0, b""), event)


FAKE_CLAUDE_ENV = """import json, os, sys
with open(os.environ["FAKE_CLAUDE_LOG"], "a", encoding="utf-8") as fh:
    fh.write(json.dumps({"argv": sys.argv[1:], "env": {k: os.environ.get(k) for k in
                                                      ("DOCTRINE", "DOCTRINE_MODEL", "CLAUDE_CONFIG_DIR")}}) + "\\n")
"""


class CcSeats(unittest.TestCase):
    """powershell/cc.ps1 takes the macOS launcher's shape: cc [opus|fable|sonnet|skills|default] [1|2] [claude args].
    The seat table is read from the script's word loop, one `if`/`elseif` line per word, as a person reads it."""
    SOURCE = PACK / "powershell" / "cc.ps1"
    OPUS = {"seat": "reviewer", "model": "claude-opus-5-5", "effort": "high"}
    ENGINEER = {"seat": "engineer", "model": "$CcEngineerModel", "effort": "medium"}
    FABLE = {"seat": "fable", "model": "claude-fable-5-1", "effort": "low"}
    SONNET = {"seat": "helper", "model": "claude-sonnet-5-5", "effort": "high"}

    @classmethod
    def setUpClass(cls):
        cls.text = cls.SOURCE.read_text(encoding="utf-8")
        cls.words = {}
        for cond, body in re.findall(r"^\s*(?:if|elseif)\s*\((.*\$word.*)\)\s*\{(.*)\}\s*$", cls.text, re.M):
            sets = {name: (lit[1:-1] if lit.startswith("'") else lit)
                    for name, lit in re.findall(r"\$(\w+)\s*=\s*('[^']*'|\$\w+)", body)}
            for word in re.findall(r"'([^']+)'", cond):
                cls.words[word] = sets

    def table(self):
        r = subprocess.run([sys.executable, str(PACK / "doctrine" / "gate.py"), "--table"], capture_output=True,
                           text=True, timeout=60)
        self.assertEqual(r.returncode, 0, r.stderr)
        return json.loads(r.stdout)["measured"]

    def test_the_seat_and_account_words(self):
        self.assertEqual(self.words, {
            "opus": self.OPUS, "reviewer": self.OPUS,
            "fable": self.FABLE, "eng": self.ENGINEER, "engineer": self.ENGINEER,
            "sonnet": self.SONNET, "helper": self.SONNET,
            "skills": {"seat": "skills"}, "default": {"seat": "default"},
            "1": {"account": "primary"}, "primary": {"account": "primary"},
            "2": {"account": "secondary"}, "secondary": {"account": "secondary"}})   # no `plain`: it is `skills`

    def test_bare_cc_is_the_engineer_on_the_primary_account_and_the_engineer_model_is_opus_5_5_unless_set(self):
        self.assertRegex(self.text, r"(?m)^\s*\$seat = 'engineer'; \$account = 'primary'; \$model = \$CcEngineerModel; "
                                    r"\$effort = 'medium'$")
        self.assertRegex(self.text, r"(?m)^if \(-not \$CcEngineerModel\) \{ \$CcEngineerModel = 'claude-opus-5-5' \}$")

    def test_models_are_pinned_ids_and_every_open_seat_has_a_measured_page(self):
        models = {s["model"] for s in self.words.values() if "model" in s} - {"$CcEngineerModel"} | {"claude-opus-5-5"}
        for model in models:
            self.assertRegex(model, r"^claude-[a-z]+(-\d+)+$")                  # an exact id, never an alias
        ready = re.search(r"(?m)^\$CcSonnetReady = \$(true|false)$", self.text)
        self.assertIsNotNone(ready, "the Sonnet switch is set where the script loads, not left to the profile")
        measured = self.table()
        # The helper runs Sonnet 5.5 with the doctrine off (measured without working notes): it needs no page, and
        # gate.py has none for it. Every other pinned model runs its measured page.
        for model in models - {"claude-sonnet-5-5"}:
            self.assertIn(model, measured, f"{model} is pinned in cc.ps1 but gate.py has no measured page for it")
        self.assertNotIn("claude-sonnet-5-5", measured)
        self.assertEqual(ready.group(1), "true")
        helper = re.search(r"(?ms)^\s*\} elseif \(\$seat -eq 'helper'\) \{\n(.*?)\n\s*\} elseif", self.text)
        self.assertIsNotNone(helper, "the helper seat has its own launch branch")
        self.assertIn("$env:DOCTRINE = 'off'", helper.group(1))
        self.assertIn("'--model', $model, '--effort', $effort", helper.group(1))

    def test_skills_drops_the_doctrine_default_is_safe_mode_and_account_2_is_a_folder_you_set(self):
        self.assertIn("$env:DOCTRINE = 'off'", self.text)
        self.assertIn("'--safe-mode'", self.text)
        self.assertIn("$env:CLAUDE_CONFIG_DIR = $CcSecondaryDir", self.text)
        self.assertNotRegex(self.text, r"(?m)^\s*\$CcSecondaryDir\s*=")         # never a default login folder

    @unittest.skipUnless(os.name == "nt" and shutil.which("powershell"), "cc.ps1 runs on the Windows CI leg only")
    def test_each_form_runs_claude_with_its_seat_and_restores_the_shell(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            script = root / "fake_claude.py"
            script.write_text(FAKE_CLAUDE_ENV, encoding="utf-8")
            (root / "claude.bat").write_text(f'@echo off\r\n"{sys.executable}" "{script}" %*\r\n', encoding="utf-8")
            log = root / "calls.jsonl"
            env = {k: v for k, v in os.environ.items() if k not in ("DOCTRINE", "DOCTRINE_MODEL", "CLAUDE_CONFIG_DIR")}
            env.update(PATH=f"{root}{os.pathsep}{env['PATH']}", FAKE_CLAUDE_LOG=str(log))
            second = str(root / "second")

            def cc(line, secondary=True):
                pre = f"$CcSecondaryDir = '{second}'; " if secondary else ""
                command = (f"{pre}. '{self.SOURCE}'; {line}; $code = $LASTEXITCODE; "
                           "Write-Output ('after: [' + $env:DOCTRINE + '] [' + $env:CLAUDE_CONFIG_DIR + ']'); exit $code")
                if log.exists():
                    log.unlink()
                r = subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", command],
                                   capture_output=True, text=True, timeout=120, env=env)
                calls = [json.loads(l) for l in log.read_text(encoding="utf-8").splitlines()] if log.exists() else []
                return r.returncode, r.stdout + r.stderr, calls

            fable = {"DOCTRINE": "on", "DOCTRINE_MODEL": "claude-fable-5-1", "CLAUDE_CONFIG_DIR": None}
            for line, argv, seen in (
                    ("cc", ["--model", "claude-opus-5-5", "--effort", "medium"],
                     {"DOCTRINE": "on", "DOCTRINE_MODEL": "claude-opus-5-5", "CLAUDE_CONFIG_DIR": None}),
                    ("cc fable 1 --continue", ["--model", "claude-fable-5-1", "--effort", "low", "--continue"], fable),
                    ("cc opus 2 --resume", ["--model", "claude-opus-5-5", "--effort", "high", "--resume"],
                     {"DOCTRINE": "on", "DOCTRINE_MODEL": "claude-opus-5-5", "CLAUDE_CONFIG_DIR": second}),
                    ("cc 2 opus", ["--model", "claude-opus-5-5", "--effort", "high"],
                     {"DOCTRINE": "on", "DOCTRINE_MODEL": "claude-opus-5-5", "CLAUDE_CONFIG_DIR": second}),
                    ("cc skills", [], {"DOCTRINE": "off", "DOCTRINE_MODEL": None, "CLAUDE_CONFIG_DIR": None}),
                    ("cc sonnet", ["--model", "claude-sonnet-5-5", "--effort", "high"],
                     {"DOCTRINE": "off", "DOCTRINE_MODEL": None, "CLAUDE_CONFIG_DIR": None}),
                    ("cc default -p hi", ["--safe-mode", "-p", "hi"],
                     {"DOCTRINE": None, "DOCTRINE_MODEL": None, "CLAUDE_CONFIG_DIR": None})):
                code, out, calls = cc(line)
                self.assertEqual((code, calls), (0, [{"argv": argv, "env": seen}]), f"{line}\n{out}")
                self.assertIn("claude seat:", out, line)
                self.assertIn("after: [] []", out, line)                         # the seat lived for that run only
            for line, secondary, says in (("cc opus 2", False, "$CcSecondaryDir"),):
                code, out, calls = cc(line, secondary)
                self.assertEqual((code, calls), (2, []), f"{line}\n{out}")
                self.assertIn(says, out, line)


class ProfileBlock(unittest.TestCase):
    def test_block_is_added_once_and_replaced_in_place(self):
        block = f"{install.PROFILE_BEGIN}\n. \"a\"\n{install.PROFILE_END}\n"
        first = install.replace_block("x\ny", block)
        self.assertEqual(first, "x\ny\n\n" + block)
        again = install.replace_block(first + "z\n", block.replace('"a"', '"b"'))
        self.assertEqual(again, "x\ny\n\n" + block.replace('"a"', '"b"') + "z\n")
        self.assertEqual(install.replace_block("", block), block)


if __name__ == "__main__":
    unittest.main()
