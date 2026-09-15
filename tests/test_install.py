"""Tests for install.py: a whole install into a temporary Claude folder, with a fake `claude` that records calls."""
import contextlib
import io
import json
import os
import stat
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

PACK = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PACK))
import install  # noqa: E402

FAKE_CLAUDE = """#!{python}
import json, os, sys
log = os.environ["FAKE_CLAUDE_LOG"]
with open(log, "a") as fh:
    fh.write(json.dumps(sys.argv[1:]) + "\\n")
args = sys.argv[1:]
fail = os.environ.get("FAKE_CLAUDE_FAIL", "")
if fail and fail in " ".join(args):
    print("Error: network unreachable", file=sys.stderr)
    sys.exit(1)
if args[:3] == ["plugin", "marketplace", "list"]:
    print(os.environ.get("FAKE_CLAUDE_MARKETPLACES", ""))
"""


class InstallRun(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.claude_dir = self.root / "dot-claude"
        self.profile = self.root / "Documents" / "PowerShell" / "profile.ps1"
        bin_dir = self.root / "bin"
        bin_dir.mkdir()
        # On Windows `shutil.which("claude")` only considers PATHEXT names, so the fake is a .bat that runs the script.
        if os.name == "nt":
            script = bin_dir / "fake_claude.py"
            script.write_text(FAKE_CLAUDE.format(python=sys.executable))
            (bin_dir / "claude.bat").write_text(f'@echo off\r\n"{sys.executable}" "{script}" %*\r\n')
        else:
            fake = bin_dir / "claude"
            fake.write_text(FAKE_CLAUDE.format(python=sys.executable))
            fake.chmod(fake.stat().st_mode | stat.S_IEXEC)
        self.log = self.root / "calls.jsonl"
        env = {"PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}", "FAKE_CLAUDE_LOG": str(self.log)}
        patcher = mock.patch.dict(os.environ, env)
        patcher.start()
        self.addCleanup(patcher.stop)

    def install(self, *extra):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = install.main(["--claude-dir", str(self.claude_dir), "--profile", str(self.profile), *extra])
        return code, out.getvalue()

    def calls(self):
        return [json.loads(l) for l in self.log.read_text().splitlines()] if self.log.exists() else []

    def test_a_default_install_is_the_skills_and_the_end_of_turn_check(self):
        code, out = self.install()
        self.assertEqual(code, 0, out)
        self.assertEqual(sorted(p.name for p in self.claude_dir.iterdir()), ["settings.json", "skills"])
        self.assertEqual(sorted(p.name for p in (self.claude_dir / "skills").iterdir()), sorted(install.SKILLS))
        self.assertIn("greenman", install.SKILLS)
        settings = json.loads((self.claude_dir / "settings.json").read_text())
        self.assertEqual(settings["hooks"], {"Stop": [{"hooks": [{"type": "command", "command": sys.executable,
            "args": [str(self.claude_dir / "skills" / "doc-guardrails" / "turn_check.py")], "timeout": 30}]}]})
        self.assertFalse(self.profile.exists())
        self.assertEqual(self.calls(), [])

    def test_no_doc_check_installs_the_skills_and_nothing_else(self):
        code, out = self.install("--no-doc-check")
        self.assertEqual(code, 0, out)
        self.assertEqual(sorted(p.name for p in self.claude_dir.iterdir()), ["skills"])

    def test_the_turn_check_is_registered_once_keeps_other_stop_hooks_and_runs(self):
        self.claude_dir.mkdir(parents=True)
        (self.claude_dir / "settings.json").write_text(json.dumps(
            {"hooks": {"Stop": [{"hooks": [{"type": "command", "command": "stop.cmd"}]}]}}))
        self.install()
        self.install()
        stop = [h for g in json.loads((self.claude_dir / "settings.json").read_text())["hooks"]["Stop"] for h in g["hooks"]]
        self.assertEqual(stop[0], {"type": "command", "command": "stop.cmd"})
        checks = [h for h in stop if install.is_turn_check_hook(h)]
        self.assertEqual(len(checks), 1)
        import subprocess
        r = subprocess.run([checks[0]["command"], *checks[0]["args"]], input=b"not json", capture_output=True)
        self.assertEqual((r.returncode, r.stdout), (0, b""))  # a malformed payload never breaks a session

    def test_a_full_install_puts_everything_in_place(self):
        code, out = self.install("--with-doctrine", "--with-plugins")
        self.assertEqual(code, 0, out)
        for name in install.DOCTRINE_FILES + ("cc.ps1",):
            self.assertTrue((self.claude_dir / "doctrine" / name).is_file(), name)
        for skill in install.SKILLS:
            self.assertTrue((self.claude_dir / "skills" / skill / "SKILL.md").is_file(), skill)
        self.assertTrue((self.claude_dir / "skills" / "closeout" / "closeout.py").is_file())
        self.assertTrue((self.claude_dir / "CLAUDE.md").is_file())
        hooks = json.loads((self.claude_dir / "settings.json").read_text())["hooks"]["SessionStart"]
        self.assertEqual(len(json.loads((self.claude_dir / "settings.json").read_text())["hooks"]["Stop"]), 1)
        self.assertEqual(hooks, [{"hooks": [{"type": "command", "command": sys.executable,
                                             "args": [str(self.claude_dir / "doctrine" / "gate.py")], "timeout": 10}]}])
        profile = self.profile.read_text()
        self.assertIn(f'. "{self.claude_dir / "doctrine" / "cc.ps1"}"', profile)

    def test_every_plugin_is_installed_then_switched_on_or_off_as_the_manifest_says(self):
        manifest = json.loads((PACK / "plugins.json").read_text())
        self.install("--with-plugins")
        calls = self.calls()
        added = [c[3] for c in calls if c[:3] == ["plugin", "marketplace", "add"]]
        self.assertEqual(sorted(added), sorted(manifest["marketplaces"].values()))
        for p in manifest["plugins"]:
            i = calls.index(["plugin", "install", p["id"], "--scope", "user"])
            self.assertEqual(calls[i + 1], ["plugin", "enable" if p["enabled"] else "disable", p["id"], "--scope", "user"])
        self.assertTrue(all(p["enabled"] for p in manifest["plugins"]))

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
                      "Stop": [{"hooks": [{"type": "command", "command": "stop.cmd"}]}]}}))
        self.profile.parent.mkdir(parents=True)
        self.profile.write_text("Set-Alias ll Get-ChildItem\n")
        (self.claude_dir / "CLAUDE.md").write_text("mine\n")
        code, out = self.install("--with-doctrine")
        self.assertEqual(code, 0, out)
        settings = json.loads((self.claude_dir / "settings.json").read_text())
        self.assertEqual(settings["theme"], "dark")
        self.assertEqual(settings["hooks"]["Stop"][0], {"hooks": [{"type": "command", "command": "stop.cmd"}]})
        self.assertTrue(install.is_turn_check_hook(settings["hooks"]["Stop"][1]["hooks"][0]))  # plus the turn check
        start = [h for g in settings["hooks"]["SessionStart"] for h in g["hooks"]]
        self.assertEqual(start[0], other_hook)
        self.assertEqual(sum(install.is_gate_hook(h) for h in start), 1)
        self.assertTrue(self.profile.read_text().startswith("Set-Alias ll Get-ChildItem\n"))
        self.assertEqual((self.claude_dir / "CLAUDE.md").read_text(), "mine\n")
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
        (old / "SKILL.md").write_text("an older eli5\n")
        self.install("--with-doctrine")
        self.assertEqual(list((self.claude_dir / "skills").glob("*.bak-*")), [])  # never beside the skills: it would load
        backups = list((self.claude_dir / "skill-backups").glob("eli5.bak-*"))
        self.assertEqual(len(backups), 1)
        self.assertEqual((backups[0] / "SKILL.md").read_text(), "an older eli5\n")
        self.assertEqual((old / "SKILL.md").read_bytes(), (PACK / "skills" / "eli5" / "SKILL.md").read_bytes())

    def test_invalid_settings_json_stops_that_step_without_touching_the_file(self):
        self.claude_dir.mkdir(parents=True)
        (self.claude_dir / "settings.json").write_text("{ not json")
        code, out = self.install("--with-doctrine")
        self.assertEqual(code, 1)
        self.assertEqual((self.claude_dir / "settings.json").read_text(), "{ not json")
        self.assertIn("is not valid JSON", out)

    def test_dry_run_writes_nothing(self):
        code, out = self.install("--dry-run")
        self.assertEqual(code, 0, out)
        self.assertFalse(self.claude_dir.exists())
        self.assertFalse(self.profile.exists())
        self.assertEqual(self.calls(), [])

    def test_the_installed_hook_runs_and_injects_the_doctrine(self):
        self.install("--with-doctrine")
        hook = json.loads((self.claude_dir / "settings.json").read_text())["hooks"]["SessionStart"][0]["hooks"][0]
        import subprocess
        r = subprocess.run([hook["command"], *hook["args"]], input=b'{"model": "claude-opus-5"}', capture_output=True,
                           env={**os.environ, "CLAUDE_CONFIG_DIR": str(self.claude_dir)})
        self.assertEqual(r.returncode, 0)
        self.assertTrue(r.stdout.startswith(b"# Working doctrine (injected: this session runs Claude Opus 5)"))


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
