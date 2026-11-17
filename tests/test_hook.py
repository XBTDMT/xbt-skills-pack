"""Tests for the optional PostToolUse layer, doc-guardrails/hook.py, and hook.sh, the older entry point that runs it:
an unlinked document is reported the moment it is written, and everything else is silent. hook.py needs no shell, so
its tests run everywhere; hook.sh is a zsh script, so its tests, and the parity of the two, skip where there is none."""
import git_quiet  # noqa: F401  (first: git's background upkeep off for every fixture; tests/git_quiet.py)
import importlib.util
import json
import ntpath
import os
import posixpath
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HOOK = Path(__file__).resolve().parent.parent / "skills" / "doc-guardrails" / "hook.sh"
INSTALL = Path(__file__).resolve().parent.parent / "install.py"
HOOK_PY = HOOK.with_name("hook.py")
CLOSEOUT = HOOK.parent.parent / "closeout" / "closeout.py"   # beside the hook's folder in this repo and in the copies
ZSH = shutil.which("zsh") and os.name != "nt"
NOT_FOUND = "doc-guardrails: closeout.py not found next to the hook or in the skills folder\n"


def load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class Project(unittest.TestCase):
    """A project with a doc map and a roadmap, and the two ways to run the hook on a payload."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(os.path.realpath(tmp.name))
        self.root = self.tmp / "proj"
        (self.root / "knowledge").mkdir(parents=True)
        subprocess.run(["git", "init", "-q"], cwd=self.root, check=True, capture_output=True)
        (self.root / "CLAUDE.md").write_text("# p\n\n## Doc map\nroadmap: knowledge/00-ROADMAP.md\n"
                                             "id form: bullet tags `R-<n>`\nlinked kinds: knowledge/*.md\n"
                                             "guardrails: report\n", encoding="utf-8")
        (self.root / "knowledge" / "00-ROADMAP.md").write_text("## Now\n- R-1 a thing\n", encoding="utf-8")

    def doc(self, name, text):
        path = self.root / "knowledge" / name
        path.write_text(text, encoding="utf-8")
        return path

    def payload(self, path):
        return json.dumps({"tool_name": "Write", "tool_input": {"file_path": str(path)}})

    def raw(self, command, data, env=None, cwd=None):
        """(exit code, stdout, stderr) of a hook command given `data` (text or bytes) on stdin."""
        data = data.encode("utf-8") if isinstance(data, str) else data
        r = subprocess.run([str(c) for c in command], input=data, capture_output=True, cwd=cwd,
                           env={**os.environ, **(env or {})})
        return r.returncode, r.stdout.decode("utf-8"), r.stderr.decode("utf-8")

    def py(self, data, hook=HOOK_PY, **kw):
        return self.raw([sys.executable, hook], data, **kw)

    def copy(self, with_helper):
        """A Claude Code folder holding a copy of the hook (what CLAUDE_CONFIG_DIR does to an install), and the helper
        beside it or not."""
        cfg = self.tmp / "cfg"
        (cfg / "skills" / "doc-guardrails").mkdir(parents=True)
        for hook in (HOOK, HOOK_PY):
            shutil.copy2(hook, cfg / "skills" / "doc-guardrails" / hook.name)
        if with_helper:
            (cfg / "skills" / "closeout").mkdir(parents=True)
            shutil.copy2(CLOSEOUT, cfg / "skills" / "closeout" / "closeout.py")
        return cfg


class HookPy(Project):
    def test_an_unlinked_document_is_reported_the_moment_it_is_written(self):
        doc = self.doc("02-spec.md", "# spec\n")
        code, out, err = self.py(self.payload(doc))
        self.assertEqual((code, out), (2, ""), err)
        self.assertIn("doc-guardrails", err)
        self.assertIn("02-spec.md", err)
        # the audit's own words, whole: its stdout, one newline at the end, on stderr
        audit = subprocess.run([sys.executable, str(CLOSEOUT), "link-audit", str(doc.parent), "--file", str(doc)],
                               capture_output=True)
        self.assertEqual(audit.returncode, 2)
        self.assertEqual(err, audit.stdout.decode("utf-8").replace("\r\n", "\n").rstrip("\n") + "\n")

    def test_a_linked_document_a_missing_file_and_a_non_document_are_silent(self):
        linked = self.doc("03-spec.md", "# spec\nroadmap: R-1\n")
        notes = self.root / "notes.txt"
        notes.write_text("# notes\n", encoding="utf-8")
        for path in (linked, self.root / "knowledge" / "gone.md", notes, self.root / "knowledge"):
            with self.subTest(path.name):
                self.assertEqual(self.py(self.payload(path)), (0, "", ""))

    def test_the_helper_is_found_through_the_skills_folder_when_the_hook_is_run_from_a_copy(self):
        cfg = self.copy(with_helper=True)
        doc = self.doc("04-spec.md", "# spec\n")
        code, out, err = self.py(self.payload(doc), hook=cfg / "skills" / "doc-guardrails" / "hook.py",
                                 env={"CLAUDE_CONFIG_DIR": str(self.tmp / "nowhere")})   # found from its own folder
        self.assertEqual((code, out), (2, ""), err)
        self.assertIn("04-spec.md", err)

    def test_the_helper_is_found_from_the_hooks_real_folder_through_a_link(self):
        link = self.tmp / "linked-hook.py"
        try:
            link.symlink_to(HOOK_PY)
        except (OSError, NotImplementedError):
            self.skipTest("no symlinks here")
        doc = self.doc("05-spec.md", "# spec\n")
        code, _, err = self.py(self.payload(doc), hook=link, env={"CLAUDE_CONFIG_DIR": str(self.tmp / "nowhere")})
        self.assertEqual(code, 2, err)

    def test_claude_config_dir_is_the_fallback_and_home_when_it_is_unset_or_empty(self):
        cfg = self.copy(with_helper=True)
        stray = self.tmp / "stray" / "hooks" / "hook.py"            # no closeout/ beside this copy's folder
        stray.parent.mkdir(parents=True)
        shutil.copy2(HOOK_PY, stray)
        doc = self.doc("06-spec.md", "# spec\n")
        code, _, err = self.py(self.payload(doc), hook=stray, env={"CLAUDE_CONFIG_DIR": str(cfg)})
        self.assertEqual(code, 2, err)
        self.assertIn("06-spec.md", err)
        home = self.tmp / "home"
        shutil.copytree(cfg, home / ".claude")
        for value in ("", None):
            with self.subTest(value=value):
                env = {k: v for k, v in os.environ.items() if k != "CLAUDE_CONFIG_DIR"}
                env.update({"HOME": str(home), "USERPROFILE": str(home)}, **({} if value is None else {"CLAUDE_CONFIG_DIR": value}))
                r = subprocess.run([sys.executable, str(stray)], input=self.payload(doc).encode("utf-8"),
                                   capture_output=True, env=env)
                self.assertEqual(r.returncode, 2, r.stderr)
        # known-bad: a folder that holds no helper is not a silent pass for the wrong reason: it says so
        code, out, err = self.py(self.payload(doc), hook=stray, env={"CLAUDE_CONFIG_DIR": str(self.tmp / "nowhere")})
        self.assertEqual((code, out, err), (0, "", NOT_FOUND))

    def test_a_missing_helper_is_one_line_and_never_a_block(self):
        cfg = self.copy(with_helper=False)
        hook = cfg / "skills" / "doc-guardrails" / "hook.py"
        doc = self.doc("07-spec.md", "# spec\n")
        self.assertEqual(self.py(self.payload(doc), hook=hook, env={"CLAUDE_CONFIG_DIR": str(cfg)}), (0, "", NOT_FOUND))
        # only a document that exists gets as far as looking for the helper
        self.assertEqual(self.py(self.payload(self.root / "notes.txt"), hook=hook, env={"CLAUDE_CONFIG_DIR": str(cfg)}),
                         (0, "", ""))

    def test_bad_or_empty_input_is_silent(self):
        self.doc("08-spec.md", "# spec\n")
        for data in (b"", b"{", b"not json", b"\xff\xfe\x00", b"[]", b"null", b'"x.md"', b"{}", b'{"tool_input": null}',
                     b'{"tool_input": "knowledge/08-spec.md"}', b'{"tool_input": {}}', b'{"tool_input": {"file_path": null}}',
                     b'{"tool_input": {"file_path": 7}}', b'{"tool_input": {"file_path": ""}}',
                     b'{"tool_input": {"file_path": ["knowledge/08-spec.md"]}}'):
            with self.subTest(data=data):
                self.assertEqual(self.py(data, cwd=self.root), (0, "", ""))

    def test_a_relative_path_is_judged_from_the_working_folder(self):
        self.doc("09-spec.md", "# spec\n")
        (self.root / "10-top.md").write_text("# top\n", encoding="utf-8")       # not of a linked kind
        code, _, err = self.py(json.dumps({"tool_input": {"file_path": "knowledge/09-spec.md"}}), cwd=self.root)
        self.assertEqual(code, 2, err)
        self.assertIn("09-spec.md", err)
        self.assertEqual(self.py(json.dumps({"tool_input": {"file_path": "10-top.md"}}), cwd=self.root), (0, "", ""))

    def test_a_windows_path_is_read_as_one(self):
        hook = load(HOOK_PY, "xbt_hook_under_test")
        win = "C:\\Users\\me\\proj\\knowledge\\02-spec.md"
        raw = json.dumps({"tool_name": "Edit", "tool_input": {"file_path": win}}).encode("utf-8")
        self.assertEqual(hook.written_file(raw), win)
        self.assertEqual(hook.folder(win, ntpath), "C:\\Users\\me\\proj\\knowledge")
        self.assertEqual(hook.folder("D:/work/proj/02-spec.md", ntpath), "D:/work/proj")
        self.assertEqual(hook.folder("C:\\02-spec.md", ntpath), "C:\\")
        self.assertEqual(hook.folder("\\\\box\\share\\docs\\02-spec.md", ntpath), "\\\\box\\share\\docs")
        self.assertEqual([hook.folder("02-spec.md", p) for p in (ntpath, posixpath)], [".", "."])
        self.assertEqual(hook.folder("/02-spec.md", posixpath), "/")
        # the helper lookup on Windows spellings: beside the hook's folder first, then the Claude Code folder's skills
        seen = []

        class Win:
            join, dirname = staticmethod(ntpath.join), staticmethod(ntpath.dirname)
            expanduser = staticmethod(lambda p: "C:\\Users\\me")

            @staticmethod
            def isfile(p):
                seen.append(p)
                return p == "D:\\cfg\\skills\\closeout\\closeout.py"

        here = "C:\\Users\\me\\.claude\\skills\\doc-guardrails"
        self.assertEqual(hook.helper(here, {"CLAUDE_CONFIG_DIR": "D:\\cfg"}, Win), "D:\\cfg\\skills\\closeout\\closeout.py")
        self.assertEqual(seen, [here + "\\..\\closeout\\closeout.py", "D:\\cfg\\skills\\closeout\\closeout.py"])
        del seen[:]
        self.assertIsNone(hook.helper(here, {"CLAUDE_CONFIG_DIR": ""}, Win))
        self.assertEqual(seen[1], "C:\\Users\\me\\.claude\\skills\\closeout\\closeout.py")
        # end to end: on Windows such a path is a real file and is judged; elsewhere it names no file, so: silent
        self.assertEqual(self.py(raw), (0, "", ""))
        if os.name == "nt":
            doc = self.doc("11-spec.md", "# spec\n")
            self.assertIn("\\", str(doc))
            self.assertEqual(self.py(self.payload(doc))[0], 2)

    def test_it_reports_through_the_installers_launcher(self):
        """The form the installer writes: the launcher runs the hook in its own process, with its exit code."""
        install = load(INSTALL, "xbt_install_for_hook_test")
        entry = install.launched(sys.executable, str(HOOK_PY), 15)
        doc = self.doc("12-spec.md", "# spec\n")
        code, out, err = self.raw([entry["command"], *entry["args"]], self.payload(doc))
        self.assertEqual((code, out), (2, ""), err)
        self.assertEqual(err, self.py(self.payload(doc))[2])
        linked = self.doc("13-spec.md", "# spec\nroadmap: R-1\n")
        self.assertEqual(self.raw([entry["command"], *entry["args"]], self.payload(linked)), (0, "", ""))

    def test_it_needs_no_shell_and_no_second_matcher(self):
        text = HOOK_PY.read_text(encoding="utf-8")
        for word in ("fnmatch", ".glob(", "shell=True", "os.fork", "import pwd"):
            self.assertNotIn(word, text)
        # cheap for a write that is not a document: nothing of closeout is loaded by importing the hook
        r = subprocess.run([sys.executable, "-c", "import runpy, sys; sys.stdin = open(sys.argv[2]); "
                            "m = runpy.run_path(sys.argv[1], run_name='not_main'); print('closeout' in sys.modules)",
                            str(HOOK_PY), os.devnull], capture_output=True, encoding="utf-8")
        self.assertEqual(r.stdout.strip(), "False", r.stderr)


@unittest.skipUnless(ZSH, "hook.sh is a zsh script")
class Hook(Project):
    def run_hook(self, path, env=None):
        code, out, err = self.raw([HOOK], self.payload(path), env=env)
        return code, out + err

    def test_an_unlinked_document_is_reported_the_moment_it_is_written(self):
        doc = self.doc("02-spec.md", "# spec\n")
        code, out = self.run_hook(doc)
        self.assertEqual(code, 2, out)
        self.assertIn("doc-guardrails", out)
        self.assertIn("02-spec.md", out)

    def test_a_linked_document_a_missing_file_and_a_non_document_are_silent(self):
        linked = self.doc("03-spec.md", "# spec\nroadmap: R-1\n")
        for path in (linked, self.root / "knowledge" / "gone.md", self.root / "notes.txt"):
            with self.subTest(path.name):
                self.assertEqual(self.run_hook(path), (0, ""))

    def test_the_helper_is_found_through_the_skills_folder_when_the_hook_is_run_from_a_copy(self):
        # What CLAUDE_CONFIG_DIR does to an install: the hook sits in <config>/skills/doc-guardrails/.
        cfg = self.copy(with_helper=True)
        doc = self.doc("04-spec.md", "# spec\n")
        code, out, err = self.raw([cfg / "skills" / "doc-guardrails" / "hook.sh"], self.payload(doc),
                                  env={"CLAUDE_CONFIG_DIR": str(cfg)})
        self.assertEqual(code, 2, out + err)
        self.assertIn("04-spec.md", err)

    def test_hook_sh_without_hook_py_says_so_and_never_blocks(self):
        cfg = self.copy(with_helper=True)
        (cfg / "skills" / "doc-guardrails" / "hook.py").unlink()
        doc = self.doc("05-spec.md", "# spec\n")
        code, out, err = self.raw([cfg / "skills" / "doc-guardrails" / "hook.sh"], self.payload(doc))
        self.assertEqual((code, out), (0, ""))
        self.assertEqual(err, "doc-guardrails: hook.py not found next to hook.sh\n")


@unittest.skipUnless(ZSH, "hook.sh is a zsh script")
class Parity(Project):
    """hook.sh and hook.py: the same exit code, stdout and stderr for the same input."""

    def both(self, data, sh=HOOK, py=HOOK_PY, **kw):
        a, b = self.raw([sh], data, **kw), self.py(data, hook=py, **kw)
        self.assertEqual(a, b)
        return a

    def test_the_two_agree_on_every_kind_of_input(self):
        unlinked = self.doc("02-spec.md", "# spec\n")
        broken = self.doc("03-spec.md", "# spec\nroadmap: R-99\n")
        linked = self.doc("04-spec.md", "# spec\nroadmap: R-1\n")
        none = self.doc("05-spec.md", "# spec\nroadmap: none — a scratch note\n")
        self.assertEqual(self.both(self.payload(unlinked))[0], 2)
        self.assertEqual(self.both(self.payload(broken))[0], 2)
        self.assertEqual(self.both(json.dumps({"tool_input": {"file_path": "knowledge/02-spec.md"}}), cwd=self.root)[0], 2)
        for data in (self.payload(linked), self.payload(none), self.payload(self.root / "knowledge" / "gone.md"),
                     self.payload(self.root / "CLAUDE.md"), self.payload(self.root / "notes.txt"),
                     self.payload("C:\\Users\\me\\proj\\knowledge\\02-spec.md"), b"", b"{", b"[]", b"\xff\xfe",
                     b'{"tool_input": null}', b'{"tool_input": {"file_path": 7}}'):
            with self.subTest(data=data):
                self.assertEqual(self.both(data), (0, "", ""))

    def test_the_two_agree_from_a_copy_with_and_without_the_helper(self):
        cfg = self.copy(with_helper=True)
        folder = cfg / "skills" / "doc-guardrails"
        doc = self.doc("06-spec.md", "# spec\n")
        self.assertEqual(self.both(self.payload(doc), sh=folder / "hook.sh", py=folder / "hook.py")[0], 2)
        shutil.rmtree(cfg / "skills" / "closeout")
        self.assertEqual(self.both(self.payload(doc), sh=folder / "hook.sh", py=folder / "hook.py",
                                   env={"CLAUDE_CONFIG_DIR": str(cfg)}), (0, "", NOT_FOUND))


if __name__ == "__main__":
    unittest.main()
