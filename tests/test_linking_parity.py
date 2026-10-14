"""The W-31 parity table: one document, four entry points, one verdict each (R-22 §5.10).

Each row builds a small project (a doc map, a roadmap, one document, a git setup) and asks every entry point that
judges a document's `roadmap:` line: the Write|Edit hook (`hook.sh`, given the PostToolUse JSON), the end-of-turn check
(`turn_check.check`, given a transcript naming the document and the row's root as its cwd), the commit gate
(`closeout.py link-audit <root> --staged` after `git add`) and the sweep (`link-audit <root> --no-inventory`). Verdicts
are normalised to MISSING, BROKEN:<reason class> or SILENT. Rows green today are the regression net for R-23's
refactor; a row whose route lands in R-23 or R-24 is an expected failure naming the row and step that turn it green,
so that row must remove the mark (an unexpected success fails the suite). Run: python3 -m unittest discover -s tests
"""
import git_quiet  # noqa: F401  (first: git's background upkeep off for every fixture; tests/git_quiet.py)
import inspect
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

PACK = Path(__file__).resolve().parent.parent
HOOK_SH = PACK / "skills" / "doc-guardrails" / "hook.sh"
TURN_CHECK = PACK / "skills" / "doc-guardrails" / "turn_check.py"
CLOSEOUT = PACK / "skills" / "closeout" / "closeout.py"
sys.path.insert(0, str(TURN_CHECK.parent))
import turn_check  # noqa: E402

ZSH = shutil.which("zsh")
DOC_MAP = ("# p\n\n## Doc map\nroadmap: knowledge/00-ROADMAP.md\nid form: bullet tags `R-<n>`\n"
           "linked kinds: knowledge/*spec*.md, knowledge/recon*/**/*.md, knowledge/redman/*.md\n"
           "exempt: README.md, CLAUDE.md{exempt}\n{extra}")
ROADMAP = "# roadmap\n\n## Now\n- R-1 the first item\n- R-2 the second item\n"
LEVEL_RE = re.compile(r"doc-guardrails: (MISSING|BROKEN) — (\S+?): (.*)")


def git(root, *args, check=True):
    return subprocess.run(["git", "-C", str(root), "-c", "user.name=t", "-c", "user.email=t@t", *args],
                          capture_output=True, text=True, check=check)


def reason_class(msg):
    """A BROKEN verdict's reason, as a class the four entry points share whatever their wording."""
    if "without a reason" in msg:
        return "none-without-reason"
    if "names no id" in msg:
        return "no-id"
    if "not in" in msg:
        return "not-in-roadmap"
    return "other"


def verdict(level, msg=""):
    return "SILENT" if level in (None, "LINKED", "NONE") else level if level == "MISSING" else f"BROKEN:{reason_class(msg)}"


class Row:
    """One project, one document. `files` are written and committed first; `doc` is then written and left uncommitted
    (or, outside git, just written)."""

    def __init__(self, tmp, doc, text, files=None, git_repo=True, claude_md=None, above=False, worktree=False):
        self.tmp = Path(tmp)
        self.base = self.tmp / "proj"
        self.root = self.base / "project" if above else self.base
        self.root.mkdir(parents=True)
        (self.base / "CLAUDE.md").write_text(claude_md if claude_md is not None else DOC_MAP.format(exempt="", extra=""),
                                             encoding="utf-8")
        for rel, body in {"knowledge/00-ROADMAP.md": ROADMAP, "README.md": "# p\n", **(files or {})}.items():
            p = self.base / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(body, encoding="utf-8")
        self.git_repo = git_repo
        if git_repo:
            git(self.base, "init", "-q", "-b", "main")
            git(self.base, "add", "-A")
            git(self.base, "commit", "-qm", "base")
            if worktree:   # the document lives in a linked worktree of the project
                wt = self.tmp / "wt"
                git(self.base, "worktree", "add", "-q", "-b", "side", str(wt))
                self.root = wt
        self.doc = self.root / doc
        self.doc.parent.mkdir(parents=True, exist_ok=True)
        self.doc.write_text(text, encoding="utf-8")
        self.rel = self.doc.relative_to(self.root).as_posix()

    def hook(self):
        payload = json.dumps({"tool_name": "Write", "tool_input": {"file_path": str(self.doc)}})
        r = subprocess.run([ZSH, str(HOOK_SH)], input=payload, capture_output=True, text=True, cwd=self.root)
        m = LEVEL_RE.search(r.stderr) if r.returncode == 2 else None
        return verdict(m.group(1), m.group(3)) if m else "SILENT"

    def end_payload(self):
        """What a real session gives the end-of-turn check: its transcript, holding the Write of the row's document,
        and its cwd, the row's root (so the check judges the row after R-24's evidence rule too; G-22 D39)."""
        transcript = self.tmp / "transcript.jsonl"
        stamp = time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime())
        transcript.write_text(json.dumps({"type": "assistant", "timestamp": stamp, "message": {"content": [
            {"type": "tool_use", "id": "w1", "name": "Write", "input": {"file_path": str(self.doc), "content": "…"}}]}})
            + "\n", encoding="utf-8")
        return {"session_id": f"parity-{self.tmp.name}", "transcript_path": str(transcript), "cwd": str(self.root)}

    def end(self):
        kwargs = {"temp_dirs": ()} if "temp_dirs" in inspect.signature(turn_check.check).parameters else {}
        out = turn_check.check(self.end_payload(), states=self.tmp / "state", now=time.time() + 5, **kwargs)
        if not out:
            return "SILENT"
        for line in json.loads(out)["reason"].splitlines():
            # the line names why the document is the session's in brackets after its path (written by…, changed…,
            # committed in…); the path is read without it
            m = re.match(r"- (.+?)(?: \((?:written|changed|committed)[^)]*\))?: (MISSING|BROKEN) — (.*)", line)
            if m and (m.group(1) == self.rel or m.group(1).endswith("/" + self.rel)):
                return verdict(m.group(2), m.group(3))
        return "SILENT"

    def gate(self):
        git(self.root, "add", self.rel)
        env = dict(os.environ, CLOSEOUT_PY=str(CLOSEOUT), DOC_GUARDRAILS_STATE=str(self.tmp / "state"))
        r = subprocess.run([sys.executable, str(CLOSEOUT), "link-audit", str(self.root), "--staged"],
                           capture_output=True, text=True, env=env)
        found = [m for m in LEVEL_RE.finditer(r.stdout) if m.group(2) == self.rel]
        return verdict(found[0].group(1), found[0].group(3)) if found else "SILENT"

    def sweep(self):
        r = subprocess.run([sys.executable, str(CLOSEOUT), "link-audit", str(self.root), "--no-inventory"],
                           capture_output=True, text=True)
        for line in r.stdout.splitlines():
            m = re.match(r"^(\S+(?: \S+)?)\s+(\S+)  (.*)$", line)
            if m and m.group(2) == self.rel:
                return verdict(m.group(1).strip(), m.group(3))
        return "SILENT"

    def verdicts(self):
        out = {"hook": self.hook(), "end": self.end(), "sweep": self.sweep()}
        if self.git_repo:
            out["gate"] = self.gate()
        return out


def gate_commit(root, tmp):
    """Install the gate and commit a staged unlinked spec through it: the gate's verdict for the spec, as the commit's
    output shows it, or SILENT when the commit never reached the gate."""
    env = dict(os.environ, CLOSEOUT_PY=str(CLOSEOUT), DOC_GUARDRAILS_STATE=str(Path(tmp) / "state"))
    subprocess.run([sys.executable, str(CLOSEOUT), "install-gate", str(root)], capture_output=True, text=True, env=env)
    spec = Path(root) / "knowledge" / "02-spec-gate.md"
    spec.write_text("# gate spec\n", encoding="utf-8")
    git(root, "add", "knowledge/02-spec-gate.md")
    r = subprocess.run(["git", "-C", str(root), "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "x"],
                       capture_output=True, text=True, env=env)
    found = [m for m in LEVEL_RE.finditer(r.stdout + r.stderr) if m.group(2) == "knowledge/02-spec-gate.md"]
    return found[0].group(1) if found else "SILENT"


class Parity(unittest.TestCase):
    """Rows and expectations of SPEC R-22 §5.10."""

    def setUp(self):
        if ZSH is None:
            self.skipTest("zsh is absent (hook.sh is a zsh script): the Write|Edit leg cannot run here")
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name)

    def row(self, *args, **kwargs):
        return Row(self.tmp, *args, **kwargs)

    def everywhere(self, v, gate=True):
        return dict({"hook": v, "end": v, "sweep": v}, **({"gate": v} if gate else {}))

    # ---- green today: the regression net for R-23's refactor

    def test_missing(self):
        self.assertEqual(self.row("knowledge/02-spec-a.md", "# a\n").verdicts(), self.everywhere("MISSING"))

    def test_linked(self):
        self.assertEqual(self.row("knowledge/02-spec-a.md", "# a\nroadmap: R-1\n").verdicts(), self.everywhere("SILENT"))

    def test_none_without_reason(self):
        self.assertEqual(self.row("knowledge/02-spec-a.md", "# a\nroadmap: none\n").verdicts(),
                         self.everywhere("BROKEN:none-without-reason"))

    def test_names_no_id(self):
        self.assertEqual(self.row("knowledge/02-spec-a.md", "# a\nroadmap: later\n").verdicts(),
                         self.everywhere("BROKEN:no-id"))

    def test_id_not_in_roadmap(self):
        self.assertEqual(self.row("knowledge/02-spec-a.md", "# a\nroadmap: R-99\n").verdicts(),
                         self.everywhere("BROKEN:not-in-roadmap"))

    def test_exempt(self):
        row = self.row("knowledge/02-spec-exempt.md", "# a\n",
                       claude_md=DOC_MAP.format(exempt=", knowledge/02-spec-exempt.md", extra=""))
        self.assertEqual(row.verdicts(), self.everywhere("SILENT"))

    def test_the_unlinked_inventory(self):
        self.assertEqual(self.row("knowledge/UNLINKED-2026-09-23.md", "# UNLINKED\n").verdicts(), self.everywhere("SILENT"))

    def test_untracked_file_in_a_new_folder(self):
        self.assertEqual(self.row("knowledge/recon-a/deep/notes.md", "# notes\n").verdicts(), self.everywhere("MISSING"))

    def test_guardrails_off_under_a_plain_heading(self):
        row = self.row("knowledge/02-spec-a.md", "# a\n", claude_md=DOC_MAP.format(exempt="", extra="guardrails: off\n"))
        self.assertEqual(row.verdicts(), {"hook": "SILENT", "end": "SILENT", "gate": "SILENT", "sweep": "MISSING"})

    # ---- red today, owned by R-23 (G-13, G-14, G-15)

    def test_a_dotted_id_not_in_the_roadmap(self):
        self.assertEqual(self.row("knowledge/02-spec-a.md", "# a\nroadmap: R-1.99\n").verdicts(),
                         self.everywhere("BROKEN:not-in-roadmap"))

    def test_a_nested_document_under_a_single_star(self):
        row = self.row("knowledge/sub/notes.md", "# notes\n", claude_md=DOC_MAP.replace(
            "knowledge/*spec*.md, knowledge/recon*/**/*.md", "knowledge/*.md").format(exempt="", extra=""))
        self.assertEqual(row.verdicts(), self.everywhere("SILENT"))

    def test_guardrails_off_under_a_suffixed_heading(self):
        row = self.row("knowledge/02-spec-a.md", "# a\n", claude_md=DOC_MAP.replace(
            "## Doc map", "## Doc map — where documents live").format(exempt="", extra="guardrails: off\n"))
        self.assertEqual(row.verdicts(), {"hook": "SILENT", "end": "SILENT", "gate": "SILENT", "sweep": "MISSING"})

    LANE = ("\n## Doc map: lane audit\nroadmap: knowledge/audit/AUDIT-ROADMAP.md\nid form: table rows | A-<n> |\n"
            "linked kinds: knowledge/audit/**/*.md\n")
    LANE_ROADMAP = {"knowledge/audit/AUDIT-ROADMAP.md": "# audit\n\n| id | item |\n|---|---|\n| A-1 | one |\n| A-2 | two |\n"}

    def test_a_lane_id_in_its_lane(self):
        row = self.row("knowledge/02-spec-audit.md", "# notes\nroadmap: A-2\n", files=self.LANE_ROADMAP,
                       claude_md=DOC_MAP.format(exempt="", extra=self.LANE))
        self.assertEqual(row.verdicts(), self.everywhere("SILENT"))

    def test_a_lane_id_not_in_its_lane(self):
        row = self.row("knowledge/02-spec-audit.md", "# notes\nroadmap: A-9999\n", files=self.LANE_ROADMAP,
                       claude_md=DOC_MAP.format(exempt="", extra=self.LANE))
        self.assertEqual(row.verdicts(), self.everywhere("BROKEN:not-in-roadmap"))

    def test_outside_git_with_a_doc_map_above(self):
        row = self.row("knowledge/02-spec-a.md", "# a\n", git_repo=False, above=True,
                       files={"project/knowledge/00-ROADMAP.md": ROADMAP})
        self.assertEqual(row.verdicts(), self.everywhere("MISSING", gate=False))   # the gate: n/a outside git

    def test_two_declared_roadmaps_without_lanes(self):
        row = self.row("knowledge/02-spec-a.md", "# a\n", files={"knowledge/01-OTHER-ROADMAP.md": ROADMAP},
                       claude_md=DOC_MAP.replace("roadmap: knowledge/00-ROADMAP.md",
                                                 "roadmap: knowledge/00-ROADMAP.md, knowledge/01-OTHER-ROADMAP.md")
                       .format(exempt="", extra=""))
        self.assertEqual(row.verdicts(), self.everywhere("MISSING"))   # the checks stay on (W-2)

    # ---- the gate (G-16), green since R-24; in-tree hooks folders carry `gate: config-hook` (G-28 F2-X1)

    def hooks_row(self, foreign):
        row = self.row("knowledge/02-spec-a.md", "# a\nroadmap: R-1\n",
                       files={"scripts/git-hooks/pre-commit": foreign},
                       claude_md=DOC_MAP.format(exempt="", extra="gate: config-hook\n"))
        (row.root / "scripts" / "git-hooks" / "pre-commit").chmod(0o755)
        git(row.root, "add", "-A")
        git(row.root, "commit", "-qm", "hooks")
        git(row.root, "config", "core.hooksPath", "scripts/git-hooks")
        return row

    def test_a_commit_through_the_gate_under_an_in_tree_hooks_path(self):
        row = self.hooks_row("#!/bin/sh\necho project checks\n")
        before = (row.root / "scripts" / "git-hooks" / "pre-commit").read_text(encoding="utf-8")
        self.assertEqual(gate_commit(row.root, self.tmp), "MISSING")
        self.assertEqual((row.root / "scripts" / "git-hooks" / "pre-commit").read_text(encoding="utf-8"), before)

    def test_a_foreign_hook_that_exits_first_still_reaches_the_gate(self):
        row = self.hooks_row("#!/bin/sh\necho project checks\nexit 0\n")
        self.assertEqual(gate_commit(row.root, self.tmp), "MISSING")

    # ---- a linked worktree judges as its main checkout does

    def test_a_linked_worktree(self):
        self.assertEqual(self.row("knowledge/02-spec-a.md", "# a\n", worktree=True).verdicts(), self.everywhere("MISSING"))

    # ---- the end leg's payload

    def test_every_rows_end_payload_names_its_document(self):
        row = self.row("knowledge/02-spec-a.md", "# a\n")
        payload = row.end_payload()
        text = Path(payload["transcript_path"]).read_text(encoding="utf-8")
        self.assertIn(str(row.doc), text)
        self.assertEqual(payload["cwd"], str(row.root))
        # known-bad: a transcript naming another file gives the end leg nothing to attribute, so it goes silent
        other = self.tmp / "other.jsonl"
        other.write_text(text.replace(str(row.doc), str(row.root / "knowledge" / "elsewhere.md")), encoding="utf-8")
        self.assertNotIn(str(row.doc), other.read_text(encoding="utf-8"))
        self.assertIsNone(turn_check.check(dict(payload, transcript_path=str(other), session_id="parity-other"),
                                           states=self.tmp / "state2", now=time.time() + 5))


if __name__ == "__main__":
    unittest.main()
