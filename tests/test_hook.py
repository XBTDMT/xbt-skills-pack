"""Tests for doc-guardrails/hook.sh, the optional PostToolUse layer: it reports an unlinked document the moment it is
written, and stays silent about everything else. zsh only, so the whole file skips where there is none."""
import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

HOOK = Path(__file__).resolve().parent.parent / "skills" / "doc-guardrails" / "hook.sh"


@unittest.skipUnless(shutil.which("zsh") and os.name != "nt", "the per-write hook is a zsh script")
class Hook(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name) / "proj"
        (self.root / "knowledge").mkdir(parents=True)
        subprocess.run(["git", "init", "-q"], cwd=self.root, check=True, capture_output=True)
        (self.root / "CLAUDE.md").write_text("# p\n\n## Doc map\nroadmap: knowledge/00-ROADMAP.md\n"
                                             "id form: bullet tags `R-<n>`\nlinked kinds: knowledge/*.md\n"
                                             "guardrails: report\n", encoding="utf-8")
        (self.root / "knowledge" / "00-ROADMAP.md").write_text("## Now\n- R-1 a thing\n", encoding="utf-8")

    def run_hook(self, path, env=None):
        payload = json.dumps({"tool_input": {"file_path": str(path)}})
        r = subprocess.run([str(HOOK)], input=payload, capture_output=True, text=True,
                           env={**os.environ, **(env or {})})
        return r.returncode, r.stdout + r.stderr

    def test_an_unlinked_document_is_reported_the_moment_it_is_written(self):
        doc = self.root / "knowledge" / "02-spec.md"
        doc.write_text("# spec\n", encoding="utf-8")
        code, out = self.run_hook(doc)
        self.assertEqual(code, 2, out)
        self.assertIn("doc-guardrails", out)
        self.assertIn("02-spec.md", out)

    def test_a_linked_document_a_missing_file_and_a_non_document_are_silent(self):
        linked = self.root / "knowledge" / "03-spec.md"
        linked.write_text("# spec\nroadmap: R-1\n", encoding="utf-8")
        for path in (linked, self.root / "knowledge" / "gone.md", self.root / "notes.txt"):
            with self.subTest(path.name):
                self.assertEqual(self.run_hook(path), (0, ""))

    def test_the_helper_is_found_through_the_skills_folder_when_the_hook_is_run_from_a_copy(self):
        # What CLAUDE_CONFIG_DIR does to an install: the hook sits in <config>/skills/doc-guardrails/.
        cfg = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: shutil.rmtree(cfg, ignore_errors=True))
        (cfg / "skills" / "doc-guardrails").mkdir(parents=True)
        (cfg / "skills" / "closeout").mkdir(parents=True)
        shutil.copy2(HOOK, cfg / "skills" / "doc-guardrails" / "hook.sh")
        # `closeout/closeout.py` next to the hook's folder in both trees: this repo's root and the copies' skills/.
        shutil.copy2(HOOK.parent.parent / "closeout" / "closeout.py", cfg / "skills" / "closeout" / "closeout.py")
        doc = self.root / "knowledge" / "04-spec.md"
        doc.write_text("# spec\n", encoding="utf-8")
        payload = json.dumps({"tool_input": {"file_path": str(doc)}})
        r = subprocess.run([str(cfg / "skills" / "doc-guardrails" / "hook.sh")], input=payload, capture_output=True,
                           text=True, env={**os.environ, "CLAUDE_CONFIG_DIR": str(cfg)})
        self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
        self.assertIn("04-spec.md", r.stderr)


if __name__ == "__main__":
    unittest.main()
