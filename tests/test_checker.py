"""The checker core (closeout.py): one matcher, one verdict, a doc map that is typed and loud, the roadmap-line rule,
dotted ids, struck citations, ledgers read by header, and lanes. Every test runs under both Pythons.
Run: python3 -m unittest discover -s tests"""
import git_quiet  # noqa: F401  (first: git's background upkeep off for every fixture; tests/git_quiet.py)
import contextlib
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path, PurePosixPath
from unittest import mock

PACK = Path(__file__).resolve().parent.parent
CLOSEOUT_DIR = PACK / "skills" / "closeout"
HOOK_SH = PACK / "skills" / "doc-guardrails" / "hook.sh"
LINKING = PACK / "skills" / "doc-guardrails" / "LINKING.md"
sys.path.insert(0, str(CLOSEOUT_DIR))
import closeout  # noqa: E402

CLOSEOUT = CLOSEOUT_DIR / "closeout.py"
closeout.config_dirs = lambda: []   # never read the real memory from a test
ZSH = shutil.which("zsh")

ROADMAP_TAGS = "# roadmap\n\n## Now\n- R-1 the first item\n- R-2 the second item\n"
ROADMAP_ROWS = "# roadmap\n\n| id | item |\n|---|---|\n| 129 | a |\n| 247 | b |\n| 249 | c |\n| 986 | d |\n"
LANE_ROADMAP = ("# audit roadmap\n\n| id | item |\n|---|---|\n| A-1 | one |\n| A-14 | fourteen |\n| A-50 | fifty |\n"
                "| A-53 | fifty-three |\n")
MAP = ("# p\n\n## Doc map\nroadmap: knowledge/00-ROADMAP.md\nid form: bullet tags `R-<n>`\n"
       "linked kinds: knowledge/*spec*.md, knowledge/redman/*.md\nexempt: README.md, CLAUDE.md\n")
TWO_LANES = ("# p\n\n## Doc map\nroadmap: knowledge/00-PROJECT-ROADMAP.md\nid form: table rows | <n> |\n"
             "linked kinds: knowledge/*spec*.md\nexempt: README.md, CLAUDE.md\n\n"
             "## Doc map: lane audit\nroadmap: knowledge/audit/AUDIT-ROADMAP.md\nid form: table rows | A-<n> |\n"
             "pin: knowledge/audit/00-AUDIT-CURRENT.md\nbranch: audit-*\nlinked kinds: knowledge/audit/**/*.md\n"
             "ledgers: knowledge/audit/redman/LEDGER.md\ndecisions: knowledge/audit/greenman\ndecision ids: G-A<n>\n")
LEDGER_HEAD = "| date | kind | item | size | state | where |\n|---|---|---|---|---|---|\n"


def git(root, *args, check=True):
    return subprocess.run(["git", "-C", str(root), "-c", "user.name=t", "-c", "user.email=t@t", *args],
                          capture_output=True, text=True, check=check)


def cli(*args, cwd=None, env=None):
    return subprocess.run([sys.executable, str(CLOSEOUT), *map(str, args)], capture_output=True, text=True, cwd=cwd,
                          env=env)


class Base(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name).resolve()
        self.root = self.tmp / "p"
        self.root.mkdir()

    def write(self, rel, text, root=None):
        p = (root or self.root) / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(text.encode("utf-8"))
        return p

    def project(self, files, git_repo=True, root=None):
        root = root or self.root
        for rel, text in files.items():
            self.write(rel, text, root)
        if git_repo:
            git(root, "init", "-q", "-b", "main")
            git(root, "add", "-A")
            git(root, "commit", "-qm", "base")
        return root

    def problems(self, root=None, level=None):
        ctx = closeout.link_context(root or self.root)
        return [p for p in ctx["problems"] if level is None or p["level"] == level]

    def sweep(self, root=None, *args):
        r = cli("link-audit", root or self.root, "--no-inventory", *args)
        return r.stdout.splitlines(), r.returncode

    def verdict(self, rel, root=None):
        root = root or self.root
        return closeout.judge(closeout.link_context(root), rel)


# ---------------------------------------------------------------------------------------------------- step 2

class OneVerdict(Base):
    def test_every_entry_point_calls_judge(self):
        self.project({"CLAUDE.md": MAP, "knowledge/00-ROADMAP.md": ROADMAP_TAGS, "README.md": "# p\n"})
        self.write("knowledge/02-spec-a.md", "# a\n")
        calls = []
        real = closeout.judge

        def counting(ctx, rel, *a, **kw):
            calls.append(rel)
            return real(ctx, rel, *a, **kw)
        with mock.patch.object(closeout, "judge", counting):
            closeout.link_check_file(self.root, self.root / "knowledge/02-spec-a.md")
            self.assertEqual(calls, ["knowledge/02-spec-a.md"])
            git(self.root, "add", "knowledge/02-spec-a.md")
            calls.clear()
            closeout.link_check_staged(self.root)
            self.assertEqual(calls, ["knowledge/02-spec-a.md"])
            calls.clear()
            closeout.link_audit(self.root, inventory=False)
            self.assertEqual(calls, ["knowledge/02-spec-a.md"])

    def test_step_two_changes_nothing(self):
        """The verdicts and their messages, character for character, on a battery every layer keeps."""
        self.project({"CLAUDE.md": MAP, "knowledge/00-ROADMAP.md": ROADMAP_TAGS, "README.md": "# p\n"})
        docs = {"knowledge/02-spec-missing.md": ("# a\n", "MISSING", "no `roadmap:` line in the first 40 lines"),
                "knowledge/02-spec-none.md": ("# a\nroadmap: none\n", "BROKEN", "`roadmap: none` without a reason"),
                "knowledge/02-spec-noid.md": ("# a\nroadmap: later\n", "BROKEN", "`roadmap:` names no id: later"),
                "knowledge/02-spec-bad.md": ("# a\nroadmap: R-9\n", "BROKEN",
                                             "roadmap id(s) not in knowledge/00-ROADMAP.md: R-9")}
        for rel, (text, _, _) in docs.items():
            self.write(rel, text)
        self.write("knowledge/02-spec-ok.md", "# a\nroadmap: R-1, R-2\n")
        self.write("knowledge/02-spec-why.md", "# a\nroadmap: none — a note\n")
        for rel, (_, level, msg) in docs.items():
            self.assertEqual(closeout.link_check_file(self.root, self.root / rel), (level, msg), rel)
        self.assertIsNone(closeout.link_check_file(self.root, self.root / "knowledge/02-spec-ok.md"))
        found, counts = closeout.link_audit(self.root, inventory=False)
        by = {w: (l, m) for l, w, m in found}
        for rel, (_, level, msg) in docs.items():
            self.assertEqual(by[rel], (level, msg))
        self.assertEqual(by["knowledge/02-spec-ok.md"], ("LINKED", "R-1, R-2"))
        self.assertEqual(by["knowledge/02-spec-why.md"], ("NONE", "none — a note"))
        self.assertEqual((counts["LINKED"], counts["NONE"], counts["MISSING"], counts["BROKEN"]), (1, 1, 1, 3))


class Matcher(unittest.TestCase):
    ROWS = [("knowledge/*audit*.md", "knowledge/audit/b-spec.md", False),   # fnmatch said True
            ("knowledge/*audit*.md", "knowledge/x-audit.md", True),
            ("knowledge/audit/**", "knowledge/audit/a.md", True),
            ("knowledge/audit/**", "knowledge/audit/sub/b.md", True),       # Path.glob on 3.9 said nothing
            ("a/**/b.md", "a/b.md", True),
            ("a/**/b.md", "a/x/y/b.md", True),
            ("docs/*.md", "docs/x/y.md", False),
            ("docs/*.md", "docs/x.md", True),
            ("r?.md", "ra.md", True),
            ("r?.md", "rab.md", False),
            ("[ab].md", "a.md", True),
            ("[ab].md", "c.md", False),
            ("[!a].md", "b.md", True),
            ("[!a].md", "a.md", False),
            ("knowledge/recon*/**/*.md", "knowledge/recon-a/deep/notes.md", True),
            ("knowledge/recon*/**/*.md", "knowledge/recon-a/notes.md", True),
            ("*.md", "a/b.md", False)]

    def test_matcher_table(self):
        for pat, rel, want in self.ROWS:
            self.assertEqual(closeout.kind_match(rel, [pat]), want, (pat, rel))
            if hasattr(PurePosixPath, "full_match"):   # Python 3.13: the semantics LINKING names
                self.assertEqual(PurePosixPath(rel).full_match(pat), want, (pat, rel))

    def test_a_bad_pattern_raises_a_named_error_never_a_crash(self):
        for pat in ("knowledge/x**y/*.md", "docs\\x.md", "[n-c].md"):
            with self.assertRaises(closeout.PatternError) as e:
                closeout.compile_pattern(pat)
            self.assertIn(pat, str(e.exception))
            self.assertFalse(closeout.kind_match("knowledge/xay/a.md", [pat]))
        self.assertTrue(issubclass(closeout.PatternError, ValueError))

    def test_expand_is_kind_match_over_the_tree(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for rel in ("a/x.md", "a/b/y.md", ".hidden/z.md", ".git/x.md", "c.txt"):
                (root / rel).parent.mkdir(parents=True, exist_ok=True)
                (root / rel).write_text("x", encoding="utf-8")
            (root / "link").symlink_to(root / "a", target_is_directory=True)
            self.assertEqual(closeout.expand(root, ["**/*.md"]), [".hidden/z.md", "a/b/y.md", "a/x.md"])
            self.assertEqual(closeout.expand(root, ["a/*.md", "a/**/*.md"]), ["a/b/y.md", "a/x.md"])
            self.assertEqual(closeout.expand(root, ["a/x**/*.md", "missing/*.md"]), [])


# ---------------------------------------------------------------------------------------------------- step 3a

class MapRead(Base):
    def base(self, claude_md, extra=None, git_repo=True):
        files = {"CLAUDE.md": claude_md, "knowledge/00-ROADMAP.md": ROADMAP_TAGS, "README.md": "# p\n"}
        files.update(extra or {})
        return self.project(files, git_repo=git_repo)

    def test_a_nested_document_is_judged_by_no_layer_or_by_all(self):
        self.base(MAP.replace("knowledge/*spec*.md, knowledge/redman/*.md", "knowledge/*audit*.md"))
        self.write("knowledge/audit/x-spec.md", "# x\n")
        self.assertIsNone(closeout.link_check_file(self.root, self.root / "knowledge/audit/x-spec.md"))
        found, _ = closeout.link_audit(self.root, inventory=False)
        self.assertNotIn("knowledge/audit/x-spec.md", [w for _, w, _ in found])
        git(self.root, "add", "knowledge/audit/x-spec.md")
        self.assertEqual(closeout.link_check_staged(self.root)[0], [])

    def test_the_suffixed_heading_is_read(self):
        self.base(MAP.replace("## Doc map", "## Doc map — where the documents live"))
        dm = closeout.doc_map(self.root)
        self.assertEqual(dm["roadmap"], "knowledge/00-ROADMAP.md")
        self.assertIn("linked kinds", dm)

    def test_a_look_alike_heading_is_an_error_not_silence(self):
        self.base("# p\n\n## The doc map\nroadmap: knowledge/00-ROADMAP.md\n\n### Doc Map\nroadmap: knowledge/00-ROADMAP.md\n")
        errors = self.problems(level="ERROR")
        self.assertEqual([e["where"] for e in errors], ["CLAUDE.md:3", "CLAUDE.md:6"])
        self.assertIn("heading '## The doc map' looks like a doc map but is not `## Doc map` (not read)", errors[0]["message"])
        self.assertTrue(errors[1]["message"].endswith(": make it a level-two heading"))
        self.assertEqual({k: v for k, v in closeout.doc_map(self.root).items() if k != "_source"}, {})

    def test_prose_kinds_fall_back_to_the_defaults_and_say_so(self):
        self.base("# p\n\n## Doc map\nroadmap: knowledge/00-ROADMAP.md\nlinked kinds: knowledge/*.md specs, audits\n")
        self.write("knowledge/x-spec.md", "# x\n")
        r = cli("link-audit", self.root, "--file", self.root / "knowledge/x-spec.md")
        self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
        self.assertIn("MISSING", r.stdout)
        lines, _ = self.sweep()
        self.assertEqual(lines[0], "CONFIG    CLAUDE.md:5  ERROR `linked kinds`: 2 entries are not a path pattern "
                                   "('knowledge/*.md specs', 'audits'); the defaults (LINKING § 5) are used")

    def test_plain_word_kinds_and_an_unknown_key(self):
        self.base("# p\n\n## Doc map\nroadmap: knowledge/00-ROADMAP.md\nlinked kinds: spec, audit, recon\n"
                  "excluded: README.md\nspecs: knowledge/specs\n")
        errors = self.problems(level="ERROR")
        self.assertEqual(len(errors), 3, errors)
        self.assertIn("('spec', 'audit', 'recon')", errors[0]["message"])
        self.assertIn("unknown key `excluded` (not read); to keep documents out of the check, use `exempt`",
                      errors[1]["message"])
        self.assertIn("unknown key `specs` (not read)", errors[2]["message"])

    def test_decisions_naming_a_file(self):
        self.base("# p\n\n## Doc map\nroadmap: knowledge/00-ROADMAP.md\ndecisions: knowledge/SPRINT-LEDGER.md\n",
                  {"knowledge/SPRINT-LEDGER.md": "# sprints\n", "knowledge/decisions/LEDGER.md": "# decisions\n"})
        (error,) = self.problems(level="ERROR")
        self.assertIn("`decisions: knowledge/SPRINT-LEDGER.md` names a file", error["message"])
        self.assertEqual(closeout.decision_ledgers(self.root), [self.root / "knowledge/decisions/LEDGER.md"])
        self.assertNotIn("knowledge/SPRINT-LEDGER.md/*.md", closeout.link_context(self.root)["kinds"])

    def test_two_roadmaps_are_named_and_checks_stay_on(self):
        self.base(MAP.replace("roadmap: knowledge/00-ROADMAP.md", "roadmap: knowledge/00-ROADMAP.md, knowledge/01-B.md"),
                  {"knowledge/01-B.md": ROADMAP_TAGS})
        (error,) = self.problems(level="ERROR")
        self.assertIn("`roadmap` names 2 paths; one roadmap per lane", error["message"])
        self.write("knowledge/x-spec.md", "# x\n")
        self.assertEqual(closeout.link_check_file(self.root, self.root / "knowledge/x-spec.md")[0], "MISSING")
        git(self.root, "add", "knowledge/x-spec.md")
        self.assertEqual([f[0] for f in closeout.link_check_staged(self.root)[0]], ["MISSING"])

    def test_a_double_star_inside_a_component_on_both_pythons(self):
        self.base("# p\n\n## Doc map\nroadmap: knowledge/00-ROADMAP.md\nlinked kinds: knowledge/audit**\n")
        lines, code = self.sweep()
        self.assertEqual(code, 0, lines)
        self.assertTrue(lines[0].startswith("CONFIG    CLAUDE.md:5  ERROR `linked kinds`: 1 entry is not a path pattern"),
                        lines)

    def test_guardrails_is_an_exact_word(self):
        for value, mode, errors in (("reporting", "report", 1), ("Block", "block", 0), ("off", "off", 0),
                                    (" report ", "report", 0)):
            with self.subTest(value=value):
                self.write("CLAUDE.md", f"# p\n\n## Doc map\nroadmap: knowledge/00-ROADMAP.md\nguardrails: {value}\n")
                self.write("knowledge/00-ROADMAP.md", ROADMAP_TAGS)
                ctx = closeout.link_context(self.root)
                self.assertEqual((ctx["mode"], len([p for p in ctx["problems"] if p["level"] == "ERROR"])), (mode, errors))

    def test_duplicate_and_unknown_keys_and_prose(self):
        self.base("# p\n\n## Doc map\nNote: the roadmap is below.\n\nroadmap: knowledge/00-ROADMAP.md\n"
                  "roadmap: knowledge/other.md\nledger: knowledge/redman/LEDGER.md\nthis is not a key\n### Notes\n")
        messages = [p["message"] for p in self.problems(level="ERROR")]
        self.assertEqual(len(messages), 4, messages)
        self.assertIn("`roadmap` given twice (the second is not read)", messages[0])
        self.assertIn("unknown key `ledger` (not read); nearest known key: `ledgers`", messages[1])
        self.assertIn("line not read (not `key: value`)", messages[2])
        self.assertIn("sub-heading '### Notes' inside the doc map is not read", messages[3])
        self.assertEqual(closeout.doc_map(self.root)["roadmap"], "knowledge/00-ROADMAP.md")

    def test_the_registry_equals_linkings_table(self):
        text = LINKING.read_text(encoding="utf-8")
        section = text.split("## 3 ·", 1)[1].split("\n## 4 ·", 1)[0]
        table = {}
        for line in section.splitlines():
            m = re.match(r"^\| (`[^|]+`) \| ([^|]+) \|", line)
            if m and "key" not in m.group(1):
                where = {w.strip().split(" ")[0] for w in m.group(2).split(";")}
                for key in re.findall(r"`([^`]+)`", m.group(1)):
                    table[key] = where
        registry = {k: set(v["where"]) for k, v in closeout.KEYS.items()}
        self.assertEqual(table, registry)

    def test_the_hook_reports_map_errors_only_on_the_map_file(self):
        self.base("# p\n\n## Doc map\nroadmap: knowledge/00-ROADMAP.md\nguardrails: reporting\nexcluded: x\n")
        r = cli("link-audit", self.root, "--file", self.root / "CLAUDE.md")
        self.assertEqual(r.returncode, 2)
        self.assertTrue(r.stdout.startswith("doc-guardrails: CLAUDE.md — the doc map you just wrote has 2 problem(s): "
                                            "CLAUDE.md:5: `guardrails: reporting` is not off, report or block"), r.stdout)
        self.assertIn("A map another lane or person owns is not edited to quiet this", r.stdout)
        self.write("knowledge/x-spec.md", "# x\nroadmap: R-1\n")
        r = cli("link-audit", self.root, "--file", self.root / "knowledge/x-spec.md")
        self.assertEqual((r.returncode, r.stdout), (0, ""))
        self.write("CLAUDE.md", MAP)
        self.assertEqual(cli("link-audit", self.root, "--file", self.root / "CLAUDE.md").returncode, 0)

    def test_the_gate_prints_one_line_and_never_refuses_over_the_map(self):
        for mode in ("report", "block"):
            with self.subTest(mode=mode):
                self.setUp()
                self.base(MAP + f"guardrails: {mode}\nexcluded: x\n")
                self.write("knowledge/a-spec.md", "# a\nroadmap: R-1\n")
                git(self.root, "add", "knowledge/a-spec.md")
                r = cli("link-audit", self.root, "--staged")
                self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
                self.assertEqual(r.stderr.count("doc-guardrails: the doc map has 1 problem"), 1, r.stderr)
                self.write("knowledge/b-spec.md", "# b\n")
                git(self.root, "add", "knowledge/b-spec.md")
                r = cli("link-audit", self.root, "--staged")
                self.assertEqual(r.returncode, 1 if mode == "block" else 0)
                self.assertEqual(r.stderr.count("doc-guardrails: the doc map has 1 problem"), 1)

    def test_audit_counts_a_map_error_as_a_warning(self):
        self.base("# p\n\n## Doc map\nroadmap: knowledge/00-ROADMAP.md\nguardrails: reporting\n")
        found = closeout.audit(self.root, [], today="2026-09-23")
        mapped = [f for f in found if f[1] == "doc-map"]
        self.assertEqual([f[0] for f in mapped], ["WARN"])
        self.assertTrue(mapped[0][2].startswith("CLAUDE.md:5 `guardrails: reporting`"))
        self.assertFalse([f for f in found if f[0] == "FAIL"])

    def test_facts_carries_the_map_problems_and_a_stable_hash(self):
        self.base("# p\n\n## Doc map\nroadmap: knowledge/00-ROADMAP.md\nguardrails: reporting\n")
        f = closeout.facts(self.root)["doc_map"]
        self.assertEqual((f["source"], f["heading_line"], f["errors"], f["warnings"]), ("CLAUDE.md", 3, 1, 0))
        self.assertEqual(f["problems"][0]["where"], "CLAUDE.md:5")
        self.assertEqual(closeout.facts(self.root)["doc_map"]["hash"], f["hash"])
        self.write("CLAUDE.md", "# p\n\n## Doc map\nroadmap: knowledge/00-ROADMAP.md\nguardrails: report\n")
        self.assertNotEqual(closeout.facts(self.root)["doc_map"]["hash"], f["hash"])
        self.assertEqual(len(f["hash"]), 12)

    def test_outside_git_the_layers_find_the_project(self):
        self.base(MAP, git_repo=False)
        doc = self.write("knowledge/02-spec-a.md", "# a\n")
        self.assertEqual(closeout.link_check_file(doc.parent, doc)[0], "MISSING")
        self.assertEqual(closeout.project_root(doc.parent), self.root)
        if ZSH:
            payload = json.dumps({"tool_name": "Write", "tool_input": {"file_path": str(doc)}})
            r = subprocess.run([ZSH, str(HOOK_SH)], input=payload, capture_output=True, text=True, cwd=self.tmp)
            self.assertEqual(r.returncode, 2, r.stderr)
            self.assertIn("MISSING", r.stderr)

    def test_doc_map_command(self):
        self.base(MAP + "guardrails: reporting\n")
        r = cli("doc-map", self.root)
        self.assertEqual(r.returncode, 1, r.stdout)
        self.assertIn('doc-map: CLAUDE.md:3 "## Doc map"', r.stdout)
        self.assertIn("lane main: roadmap knowledge/00-ROADMAP.md (bullet tags `R-<n>`, 2 ids)", r.stdout)
        self.assertIn("knowledge/*spec*.md → 0 files (declared)", r.stdout)
        self.assertTrue(r.stdout.rstrip().endswith("doc-map: 1 ERROR · 2 WARN · 0 NOTE"), r.stdout)
        self.write("CLAUDE.md", MAP)
        r = cli("doc-map", self.root)
        self.assertEqual(r.returncode, 0, r.stdout)
        self.assertIn("CLAUDE.md:6  `linked kinds` entry `knowledge/*spec*.md` matches no file", r.stdout)

    def test_the_fast_path_never_walks(self):
        self.project({"CLAUDE.md": MAP, "knowledge/00-ROADMAP.md": ROADMAP_TAGS, "README.md": "# p\n"})
        self.write("knowledge/02-spec-a.md", "# a\n")
        git(self.root, "add", "knowledge/02-spec-a.md")

        def no_walk(*a, **kw):
            raise AssertionError("the fast path walked the tree")
        with mock.patch.object(os, "walk", no_walk), mock.patch.object(Path, "glob", no_walk), \
                mock.patch.object(Path, "rglob", no_walk):
            self.assertEqual(closeout.link_check_file(self.root, self.root / "knowledge/02-spec-a.md")[0], "MISSING")
            self.assertEqual([f[0] for f in closeout.link_check_staged(self.root)[0]], ["MISSING"])

    def test_warnings_never_reach_the_hook_the_gate_or_the_stop_message(self):
        self.base(MAP.replace("knowledge/redman/*.md", "docs/redteam-*/**/*.md"))
        ctx = closeout.link_context(self.root, deep=True)
        self.assertEqual([p["level"] for p in ctx["problems"]], ["WARN", "WARN"])
        self.assertEqual(cli("link-audit", self.root, "--file", self.root / "CLAUDE.md").returncode, 0)
        self.write("knowledge/a-spec.md", "# a\nroadmap: R-1\n")
        git(self.root, "add", "knowledge/a-spec.md")
        self.assertEqual(cli("link-audit", self.root, "--staged").stderr, "")
        self.assertIsNone(closeout.stop_config_message(ctx))


# ---------------------------------------------------------------------------------------------------- step 3b

class RoadmapLine(Base):
    def test_the_roadmap_line_rule(self):
        v = closeout.roadmap_value
        self.assertEqual(v("# AUDIT ROADMAP: x\nroadmap: none — y\n"), "none — y")
        self.assertIsNone(v("# a\nThe layer is not on this roadmap: the brief says so.\n"))
        self.assertEqual(v("# a\n**Sprint:** 36 · **ROADMAP:** refines row 129\n"), "refines row 129")
        self.assertEqual(v("# a\n**The record.** Roadmap: §21, rows 247–249.\n"), "§21, rows 247–249.")
        self.assertEqual(v("# a\n- roadmap: R-3\n"), "R-3")
        self.assertEqual(v("# a\n> **roadmap:** R-3\n"), "R-3")
        self.assertIsNone(v("# T            roadmap: none — m\n"))
        self.assertEqual(closeout.cited_ids("refines row 129", "table rows"), ["129"])
        self.assertEqual(closeout.cited_ids("§21, rows 247–249.", "table rows"), ["247", "249"])

    def test_dotted_ids_are_ids(self):
        self.project({"CLAUDE.md": MAP, "README.md": "# p\n",
                      "knowledge/00-ROADMAP.md": "# r\n\n## Now\n- R-1 a\n- R-1.23 b\n"})
        self.write("knowledge/a-spec.md", "# a\nroadmap: R-1.99\n")
        self.write("knowledge/b-spec.md", "# b\nroadmap: R-1.23\n")
        self.assertEqual(self.verdict("knowledge/a-spec.md"),
                         ("BROKEN", "roadmap id(s) not in knowledge/00-ROADMAP.md: R-1.99"))
        self.assertEqual(self.verdict("knowledge/b-spec.md"), ("LINKED", "R-1.23"))
        self.write("knowledge/00-ROADMAP.md", "| id | item |\n|---|---|\n| 148.6 | a |\n")
        self.write("CLAUDE.md", MAP.replace("bullet tags `R-<n>`", "table rows | <n> |"))
        self.write("knowledge/c-spec.md", "# c\nroadmap: 148.6\n")
        self.assertEqual(self.verdict("knowledge/c-spec.md"), ("LINKED", "148.6"))

    ROADMAP = ("# r\n\n| id | item |\n|---|---|\n| 1 | a ~~[impl `gone/x.py`]~~ ⛔ retired |\n"
               "| 2 | ~~`gone/y.md`~~ corrected |\n")

    def test_a_struck_dead_citation_is_not_a_finding(self):
        self.project({"CLAUDE.md": MAP.replace("bullet tags `R-<n>`", "table rows | <n> |"), "README.md": "# p\n",
                      "knowledge/00-ROADMAP.md": self.ROADMAP})
        found, counts = closeout.link_audit(self.root, inventory=False)
        self.assertEqual(counts["NO FILE"], 0, found)
        self.write("README.md", "# p\n\nsee ~~`gone/w.py`~~\n")
        found = closeout.audit(self.root, ["README.md"], today="2026-09-23")
        self.assertEqual([f for f in found if f[0] == "FAIL"], [])
        self.assertEqual([f for f in found if "not found" in f[2]], [])

    def test_a_live_broken_citation_still_fails(self):
        self.project({"CLAUDE.md": MAP.replace("bullet tags `R-<n>`", "table rows | <n> |"), "README.md": "# p\n",
                      "knowledge/00-ROADMAP.md": self.ROADMAP + "| 3 | `gone/z.md` |\n| 4 | ~~old~~ now `gone/v.md` |\n"
                      "| 5 | ~~ `gone/u.md` |\n"})
        found, _ = closeout.link_audit(self.root, inventory=False)
        nofile = sorted(m for l, _, m in found if l == "NO FILE")
        self.assertIn("gone/z.md", nofile)
        self.assertIn("gone/v.md", nofile)
        self.assertIn("gone/u.md", nofile)
        # the audit still reports both live citations (the struck skip stays narrow); a citation on a roadmap line this
        # pass did not write is reported as pre-existing, never skipped (the audit's rule since R-24)
        reported = [f for f in closeout.audit(self.root, ["README.md"], today="2026-09-23")
                    if f[0] in ("FAIL", "PRE-EXISTING") and "linking NO FILE" in f[2]]
        self.assertTrue(any("gone/z.md" in f[2] for f in reported))
        self.assertTrue(any("gone/v.md" in f[2] for f in reported))
        self.assertFalse(any("gone/y.md" in f[2] for f in reported))

    def test_roadmap_sections_follows_the_id_regex(self):
        text = "# r\n\n## Done\n- R-1 one\n\n## Now\n- R-1.23 dotted\n"
        sections = closeout.roadmap_sections(text)
        self.assertEqual((sections["R-1"], sections["R-1.23"]), ("Done", "Now"))


class LaneSections(unittest.TestCase):
    def test_roadmap_sections_takes_the_lanes_prefix(self):
        self.assertEqual(closeout.roadmap_sections("## Now\n- A-14 a lane item\n", prefix="A-"), {"A-14": "Now"})


# ---------------------------------------------------------------------------------------------------- step 3c

class Records(Base):
    def facts(self):
        return closeout.facts(self.root)

    def test_a_deep_run_is_listed_not_counted(self):
        self.project({"knowledge/redman/LEDGER.md": LEDGER_HEAD + "| d | k | i | small | filed | w |\n",
                      "knowledge/redman/REDMAN-2026-09-20-x.md": "# deep run\n" + LEDGER_HEAD
                      + "| d | k | i | small | filed | w |\n\nconverged after 3 passes\n"})
        f = self.facts()["redman"]
        self.assertEqual(f["open_rows"], 1)
        self.assertEqual(f["ledgers"], ["knowledge/redman/LEDGER.md"])
        self.assertEqual([d["path"] for d in f["deep_runs"]], ["knowledge/redman/REDMAN-2026-09-20-x.md"])
        self.assertIn("converged", f["deep_runs"][0]["last_state"])

    def test_unknown_states_count_open_and_are_named(self):
        self.project({"knowledge/redman/LEDGER.md": LEDGER_HEAD + "| d | k | i | small | ADOPTED 2026-09-20 — kept | w |\n"
                      "| d | k | i | small | asked · the owner asked for more | w |\n| d | k | i | small | closed — x | w |\n"})
        f = self.facts()["redman"]
        self.assertEqual(f["open_rows"], 2)
        self.assertEqual(f["unreadable_states"], ["knowledge/redman/LEDGER.md:3: 'ADOPTED 2026-09-20 — kept'",
                                                  "knowledge/redman/LEDGER.md:4: 'asked · the owner asked for more'"])

    def test_a_greenman_ledger_without_status_is_unreadable(self):
        self.project({"knowledge/decisions/LEDGER.md": "| id | date | depth | decision | chosen |\n|---|---|---|---|---|\n"
                      "| G-1 | d | M | x | shortcut taken |\n"})
        g = self.facts()["greenman"]
        self.assertEqual(g["unreadable_ledgers"], ["knowledge/decisions/LEDGER.md: no `status` column"])
        self.assertEqual(g["real_fix_owed"], [])

    def test_an_empty_well_formed_ledger_is_read_not_unreadable(self):
        """Ledger row 2026-09-24 (misread ledger): a ledger with its header and no rows yet was reported as having no
        `state` (or `status`) column; the column is read from the header, so it holds zero open rows."""
        self.project({"knowledge/redman/LEDGER.md": LEDGER_HEAD,
                      "knowledge/decisions/LEDGER.md": "| date | id | the choice | depth | status |\n|---|---|---|---|---|\n"})
        f, g = self.facts()["redman"], self.facts()["greenman"]
        self.assertEqual((f["open_rows"], f["unreadable_ledgers"]), (0, []))
        self.assertEqual(g["unreadable_ledgers"], [])
        # known-bad, kept: a header without the column is still named
        self.project({"knowledge/redman/LEDGER.md": "| date | kind |\n|---|---|\n"})
        self.assertEqual(self.facts()["redman"]["unreadable_ledgers"], ["knowledge/redman/LEDGER.md: no `state` column"])

    def test_a_row_with_more_cells_than_its_header_is_named_not_read_shifted(self):
        """Ledger triage 2026-10-06: a `|` inside a cell (a code span quoting a table) split one row into extra cells,
        so its state was read from the wrong column, silently (the 2026-09-26 `ledger id column` row read as closed).
        An escaped `\\|` is part of its cell (GitHub's table rule); any other row whose cells outnumber the header's
        counts open and is named."""
        self.project({"knowledge/redman/LEDGER.md": LEDGER_HEAD
                      + "| d | k | quotes `| id |` here | small | filed | w |\n"
                      + "| d | k | quotes `\\| id \\|` here | small | closed — x | w |\n"})
        f = self.facts()["redman"]
        self.assertEqual(f["open_rows"], 1)
        self.assertEqual(f["unreadable_states"],
                         ["knowledge/redman/LEDGER.md:3: 8 cells under a 6-column header (a `|` inside a cell?)"])
        rows, _, _ = closeout.ledger_table(self.root / "knowledge/redman/LEDGER.md")
        self.assertEqual((rows[1]["item"], rows[1]["state"]), ("quotes `| id |` here", "closed — x"))

    def test_rows_after_a_break_continue_the_table(self):
        self.project({"knowledge/redman/LEDGER.md": LEDGER_HEAD + "| d | k | i | small | filed | w |\n\n"
                      "| d | k | i | small | filed | w |\n\n## Old\n\n| a | b |\n|---|---|\n| x | filed |\n"})
        f = self.facts()["redman"]
        self.assertEqual(f["open_rows"], 2)
        self.assertEqual(f["rows_after_a_break"], ["knowledge/redman/LEDGER.md:5"])
        rows, notes, problem = closeout.ledger_table(self.root / "knowledge/redman/LEDGER.md")
        self.assertEqual(len(rows), 3)
        self.assertEqual(rows[-1].get("b"), "filed")

    def test_a_headerless_table_is_unreadable(self):
        self.project({"knowledge/redman/LEDGER.md": "| d | k | i | small | filed | w |\n"})
        f = self.facts()["redman"]
        self.assertEqual((f["open_rows"], f["unreadable_ledgers"]),
                         (0, ["knowledge/redman/LEDGER.md: unreadable: no header row"]))

    def test_undeclared_record_folders_are_named(self):
        self.project({"knowledge/greenman/G-7-x.md": "# G-7\n", "evals/x/knowledge/decisions/G-1-y.md": "# G-1\n",
                      "knowledge/decisions/G-2-z.md": "# G-2\n"})
        self.assertEqual(self.facts()["greenman"]["undeclared_record_folders"], ["knowledge/greenman"])

    def test_the_header_names_are_normalised(self):
        self.project({"knowledge/redman/LEDGER.md": "| date | **State** | `roadmap:` |\n|---|---|---|\n| d | filed | 1 |\n"})
        rows, _, problem = closeout.ledger_table(self.root / "knowledge/redman/LEDGER.md")
        self.assertIsNone(problem)
        self.assertEqual((rows[0]["state"], rows[0]["roadmap"]), ("filed", "1"))


# ---------------------------------------------------------------------------------------------------- step 4

class Lanes(Base):
    def two_lanes(self, extra=None, git_repo=True):
        files = {"CLAUDE.md": TWO_LANES, "README.md": "# p\n", "knowledge/00-PROJECT-ROADMAP.md": ROADMAP_ROWS,
                 "knowledge/audit/AUDIT-ROADMAP.md": LANE_ROADMAP}
        files.update(extra or {})
        return self.project(files, git_repo=git_repo)

    def judged(self, value):
        self.write("knowledge/x-spec.md", f"# x\nroadmap: {value}\n")
        return self.verdict("knowledge/x-spec.md")

    def test_lane_ids_resolve_by_prefix(self):
        self.two_lanes()
        self.assertEqual(self.judged("A-9999 (x); project row 986"),
                         ("BROKEN", "roadmap id(s) not in knowledge/audit/AUDIT-ROADMAP.md: A-9999"))
        self.assertEqual(self.judged("A-53"), ("LINKED", "A-53"))
        self.assertEqual(self.judged("A-14 (x); project row 986"), ("LINKED", "986, A-14"))
        self.assertEqual(self.judged("A-14 (x); project row 9999"),
                         ("BROKEN", "roadmap id(s) not in knowledge/00-PROJECT-ROADMAP.md: 9999"))
        self.assertEqual(self.judged("A-1…A-50 (the tracker)"), ("LINKED", "A-1, A-50"))
        self.assertEqual(self.judged("A-1…A-99"), ("BROKEN", "roadmap id(s) not in knowledge/audit/AUDIT-ROADMAP.md: A-99"))
        self.setUp()
        self.project({"CLAUDE.md": MAP, "README.md": "# p\n", "knowledge/00-ROADMAP.md": ROADMAP_TAGS})
        self.assertEqual(self.judged("S-3 the thing"), ("BROKEN", "`roadmap:` names no id: S-3 the thing"))

    def test_the_project_block_ends_at_the_lane_heading(self):
        self.two_lanes()
        dm = closeout.doc_map(self.root)
        self.assertEqual(dm["roadmap"], "knowledge/00-PROJECT-ROADMAP.md")
        self.assertNotIn("branch", dm)
        self.assertEqual([l["name"] for l in closeout.link_context(self.root)["lanes"]], ["main", "audit"])

    def test_lane_validation(self):
        bad = (TWO_LANES + "\n## Doc map: lane main\nroadmap: knowledge/00-PROJECT-ROADMAP.md\nid form: table rows | B-<n> |\n"
               "\n## Doc map: lane second\nroadmap: knowledge/audit/AUDIT-ROADMAP.md\nid form: table rows | A-<n> |\n"
               "\n## Doc map: lane third\nexempt: x.md\ndecision ids: GA<n>\n")
        self.two_lanes({"CLAUDE.md": bad})
        messages = [p["message"] for p in self.problems(level="ERROR")]
        self.assertTrue(any("a lane cannot be named `main`" in m for m in messages), messages)
        self.assertTrue(any("id prefix `A-` is lane `audit`'s" in m for m in messages), messages)
        self.assertTrue(any("`exempt` is a project key" in m for m in messages), messages)
        self.assertTrue(any("`decision ids: GA<n>` is not `G-<letters><n>`" in m for m in messages), messages)
        self.assertTrue(any("lane `third` has no `roadmap`" in m for m in messages), messages)

    def test_lane_documents_in_every_entry_point(self):
        self.two_lanes()
        doc = self.write("knowledge/audit/sub/a-note.md", "# a\nroadmap: A-9999\n")
        want = ("BROKEN", "roadmap id(s) not in knowledge/audit/AUDIT-ROADMAP.md: A-9999")
        self.assertEqual(closeout.link_check_file(self.root, doc), want)
        git(self.root, "add", "knowledge/audit/sub/a-note.md")
        self.assertEqual(closeout.link_check_staged(self.root)[0], [(want[0], "knowledge/audit/sub/a-note.md", want[1])])
        found, _ = closeout.link_audit(self.root, inventory=False)
        self.assertIn((want[0], "knowledge/audit/sub/a-note.md", want[1]), found)

    def branch_record(self, branch, rel, text):
        git(self.root, "checkout", "-q", "-b", branch)
        self.write(rel, text)
        git(self.root, "add", "-A")
        git(self.root, "commit", "-qm", f"on {branch}")
        git(self.root, "checkout", "-q", "main")

    def test_facts_lanes_pins_and_next_ids(self):
        self.two_lanes({"knowledge/00-CURRENT.md": "# pin\n", "knowledge/audit/00-AUDIT-CURRENT.md": "# lane pin\n",
                        "knowledge/decisions/LEDGER.md": "| id | status | the choice |\n|---|---|---|\n| G-852 | decided | x |\n",
                        "knowledge/audit/greenman/LEDGER.md": "| id | status | the choice |\n|---|---|---|\n| G-39 | decided | x |\n"})
        self.branch_record("audit-x", "knowledge/audit/greenman/G-47-x.md", "# G-47\n")
        f = closeout.facts(self.root)
        self.assertEqual(f["lanes"]["audit"]["next_decision_id"], "G-A48")
        self.assertEqual(f["lanes"]["main"]["next_decision_id"], "G-853")
        self.assertIn("audit-x", " ".join(f["lanes"]["audit"]["next_decision_id_from"]))
        self.assertEqual({(p["path"], p["lane"]) for p in f["pins"]},
                         {("knowledge/00-CURRENT.md", "main"), ("knowledge/audit/00-AUDIT-CURRENT.md", "audit")})
        lane_pin = next(p for p in f["pins"] if p["lane"] == "audit")
        self.assertIs(lane_pin["stamp_here"], False)

    def test_doc_map_prints_no_pin_for_a_lane_that_declares_none(self):
        """Known-bad for a display that prints the project's default pin on a lane with none (a project's review
        lane, 2026-09-30): the loader holds None, which the stamp guard compares, so the line shows `pin -` as it shows
        `branch -`. A lane's declared pin is printed; the main lane's pin is the default when it declares none."""
        self.two_lanes({"knowledge/reviews/REVIEWS.md": "# reviews\n\n| id | item |\n|---|---|\n| RV-1 | one |\n"})
        self.write("CLAUDE.md", TWO_LANES + "\n## Doc map: lane review\nroadmap: knowledge/reviews/REVIEWS.md\n"
                   "id form: table rows | RV-<n> |\nbranch: review-lane\nledgers: knowledge/reviews/LEDGER.md\n")
        lanes = {ln["name"]: ln for ln in closeout.link_context(self.root)["lanes"]}
        self.assertIsNone(lanes["review"]["pin"])
        out = cli("doc-map", self.root).stdout
        line = {ln.split(":", 1)[0]: ln for ln in out.splitlines() if ln.startswith("lane ")}
        self.assertIn("· pin - · branch review-lane ·", line["lane review"])
        self.assertIn("· pin knowledge/audit/00-AUDIT-CURRENT.md · branch audit-* ·", line["lane audit"])
        self.assertIn(f"· pin {closeout.PIN_DEFAULT} · branch - ·", line["lane main"])

    def test_records_are_grouped_by_lane(self):
        self.two_lanes({"knowledge/redman/LEDGER.md": LEDGER_HEAD + "| d | k | i | s | filed | w |\n",
                        "knowledge/audit/redman/LEDGER.md": LEDGER_HEAD + "| d | k | i | s | filed | w |\n"
                        "| d | k | i | s | asked · later | w |\n"})
        f = closeout.facts(self.root)
        self.assertEqual(f["redman"]["by_lane"], {"main": 1, "audit": 2})
        self.assertEqual(f["lanes"]["audit"]["ledgers"], ["knowledge/audit/redman/LEDGER.md"])
        self.assertEqual(f["lanes"]["audit"]["decisions"], "knowledge/audit/greenman")

    def test_stamp_refuses_a_lane_pin_off_the_lane_branch(self):
        self.two_lanes({"knowledge/audit/00-AUDIT-CURRENT.md": "# lane pin\n"})
        git(self.root, "branch", "audit-x")
        wt = self.tmp / "lane"
        git(self.root, "worktree", "add", "-q", str(wt), "audit-x")
        with self.assertRaises(SystemExit) as e:
            closeout.stamp(self.root / "knowledge/audit/00-AUDIT-CURRENT.md", None)
        self.assertIn("is lane audit's pin and this checkout is on main, not audit-*", str(e.exception))
        self.assertIn(str(wt), str(e.exception))
        with self.assertRaises(SystemExit):
            closeout.stamp(self.root / "knowledge/audit/00-AUDIT-CURRENT.md", None, force=True)
        closeout.stamp(wt / "knowledge/audit/00-AUDIT-CURRENT.md", None)
        self.assertIn("last_marker:", (wt / "knowledge/audit/00-AUDIT-CURRENT.md").read_text(encoding="utf-8"))

    def test_memory_from_a_worktree_is_the_projects_own(self):
        self.two_lanes()
        wt = self.tmp / "lane"
        git(self.root, "worktree", "add", "-q", "-b", "audit-x", str(wt))
        cfg = self.tmp / "cfg"
        mem = cfg / "projects" / closeout.escape(self.root.resolve()) / "memory"
        mem.mkdir(parents=True)
        (mem / "x.md").write_text("a note\n", encoding="utf-8")
        with mock.patch.object(closeout, "config_dirs", lambda: [cfg]):
            report = closeout.memory_report(wt)
        self.assertEqual([(d["kind"], d["entries"]) for d in report["dirs"]], [("own", 1)])
        self.assertEqual(closeout.project_identity(wt), self.root.resolve())

    def test_link_audit_lane_limits_documents_not_ids(self):
        self.two_lanes()
        self.write("knowledge/audit/a-note.md", "# a\nroadmap: A-14; project row 986\n")
        self.write("knowledge/x-spec.md", "# x\n")
        found, _ = closeout.link_audit(self.root, inventory=False, lane="audit")
        judged = [(l, w) for l, w, _ in found if l in ("LINKED", "NONE", "MISSING", "BROKEN")]
        self.assertEqual(judged, [("LINKED", "knowledge/audit/a-note.md")])

    def test_document_lane_is_the_most_specific_kind(self):
        self.two_lanes()
        ctx = closeout.link_context(self.root)
        self.assertEqual(closeout.document_lane(ctx, "knowledge/audit/sub/a.md"), "audit")
        self.assertEqual(closeout.document_lane(ctx, "knowledge/x-spec.md"), "main")
        self.assertEqual(closeout.document_lane(ctx, "elsewhere.md"), "main")

    def test_a_citation_on_a_lane_branch_says_so(self):
        self.two_lanes()
        self.branch_record("audit-x", "knowledge/audit/AS-07-spec.md", "# AS-07\n")
        self.write("knowledge/00-PROJECT-ROADMAP.md", ROADMAP_ROWS + "| 1023 | see `knowledge/audit/AS-07-spec.md` |\n")
        found, _ = closeout.link_audit(self.root, inventory=False)
        (hit,) = [(l, m) for l, _, m in found if l == "NO FILE"]
        self.assertEqual(hit, ("NO FILE", "knowledge/audit/AS-07-spec.md (exists on lane audit's branch audit-x, "
                                          "not in this checkout)"))


# ---------------------------------------------------------------------------------------------------- a bare row after a prefixed id

class BareRowAfterPrefixedId(Base):
    """`A-53, 99999`: the bare number is not read (a bare row id counts only first in the value or after `row`, which
    keeps dates and figures out), so it was dropped without a word (redman ledger, 2026-09-24, "silent drop"). The rule
    stays; the document earns a NOTE through the per-file check, the sweep and facts."""

    def said(self, value):
        """(the verdict, the sweep's NOTE lines for the document, the per-file check's (rc, stdout), facts' notes)"""
        Lanes.two_lanes(self, {"knowledge/00-PROJECT-ROADMAP.md": ROADMAP_ROWS + "| 1023 | e |\n",
                               "knowledge/audit/AUDIT-ROADMAP.md": LANE_ROADMAP + "| A-51 | fifty-one |\n"})
        verdict = Lanes.judged(self, value)
        lines, _ = self.sweep()
        swept = [l for l in lines if l.startswith("NOTE") and "knowledge/x-spec.md" in l]
        r = cli("link-audit", self.root, "--file", self.root / "knowledge/x-spec.md")
        return verdict, swept, (r.returncode, r.stdout), closeout.facts(self.root)["guardrails"]["notes"]

    def assertNoted(self, value, number, linked):
        verdict, swept, (rc, out), notes = self.said(value)
        self.assertEqual(verdict, ("LINKED", linked))                   # the rule stays: the number is still not read
        self.assertEqual(len(swept), 1, swept)
        self.assertIn(f"bare {number} after a prefixed id is not read", swept[0])
        self.assertEqual(rc, 2, out)
        self.assertIn(f"doc-guardrails: NOTE — {self.root / 'knowledge/x-spec.md'}: ", out)
        self.assertIn(f"write it first or as `row {number}`", out)
        self.assertEqual(len(notes), 1, notes)
        self.assertTrue(notes[0].startswith("knowledge/x-spec.md: ") and f"`row {number}`" in notes[0], notes)

    def assertQuiet(self, value, linked):
        verdict, swept, (rc, out), notes = self.said(value)
        self.assertEqual(verdict, ("LINKED", linked))
        self.assertEqual(swept, [])
        self.assertEqual((rc, out), (0, ""))
        self.assertEqual(notes, [])

    def test_a_missing_bare_row_after_a_prefixed_id_is_noted(self):
        self.assertNoted("A-53, 99999", "99999", "A-53")

    def test_an_existing_bare_row_after_a_prefixed_id_is_noted(self):
        self.assertNoted("A-51, 1023", "1023", "A-51")

    def test_a_bare_row_first_is_read_and_quiet(self):
        self.assertQuiet("1023, A-51", "1023, A-51")

    def test_a_row_after_the_word_row_is_read_and_quiet(self):
        self.assertQuiet("A-51; row 1023", "1023, A-51")

    def test_a_date_in_the_reason_is_no_id_and_quiet(self):
        self.assertQuiet("A-51 — see the 2026-09-20 run", "A-51")
        self.assertEqual(closeout.bare_after_prefixed("A-51 — 2026-09-20 run, 3 files"), [])
        self.assertEqual(closeout.bare_after_prefixed("A-51, 3 files changed"), [])
        self.assertEqual(closeout.bare_after_prefixed("A-1…A-50, 17, 18 (x), 19"), ["17", "18"])


# ---------------------------------------------------------------------------------------------------- ids outside the sequence

OUTSIDE_MAIN = ("# p\n\n## Doc map\nroadmap: knowledge/00-PROJECT-ROADMAP.md\nid form: table rows | <n> |\n"
                "linked kinds: knowledge/*spec*.md\nexempt: README.md, CLAUDE.md\ndecisions: knowledge/greenman\n"
                "decision ids outside sequence: G-6, G-852\n\n"
                "## Doc map: lane audit\nroadmap: knowledge/audit/AUDIT-ROADMAP.md\nid form: table rows | A-<n> |\n"
                "branch: audit-*\nlinked kinds: knowledge/audit/**/*.md\ndecisions: knowledge/audit/greenman\n"
                "decision ids: G-A<n>\ndecision ids outside sequence: G-1..G-52\n")
GREENMAN_HEAD = "| date | id | the choice | depth | status |\n|---|---|---|---|---|\n"


class OutsideSequence(Base):
    """A two-lane project shaped like the one that asked for it: main numbers G-7…G-12 in its folder beside two ids
    recorded before the folder existed (G-6, G-852); the lane recorded G-1…G-52 before it took the form G-A<n>."""

    def build(self, claude_md=OUTSIDE_MAIN):
        files = {"CLAUDE.md": claude_md, "README.md": "# p\n", "knowledge/00-PROJECT-ROADMAP.md": ROADMAP_ROWS,
                 "knowledge/audit/AUDIT-ROADMAP.md": LANE_ROADMAP,
                 "knowledge/greenman/LEDGER.md": GREENMAN_HEAD + "| d | G-852 | a ruling before the folder | L | decided |\n"
                 "| d | G-7 | vendoring | L | decided |\n",
                 "knowledge/audit/greenman/LEDGER.md": GREENMAN_HEAD + "".join(
                     f"| d | G-{n} | an audit call | M | decided |\n" for n in (1, 7, 8, 30, 46))}
        for n in range(7, 13):
            files[f"knowledge/greenman/G-{n}-main-record.md"] = f"# G-{n}\nroadmap: none — a record\n"
        for n in range(47, 53):
            files[f"knowledge/audit/greenman/G-{n}-lane-record.md"] = f"# G-{n}\nroadmap: none — a record\n"
        files["knowledge/audit/greenman/G-7-lane-seven.md"] = "# G-7 (the lane's)\nroadmap: none — a record\n"
        return self.project(files)

    def test_main_counts_only_its_sequence(self):
        self.build()
        lanes = closeout.facts(self.root)["lanes"]
        self.assertEqual(lanes["main"]["next_decision_id"], "G-13")        # the old reading: G-853
        self.assertEqual(lanes["audit"]["next_decision_id"], "G-A53")
        self.assertEqual(self.problems(level="ERROR"), [])

    def test_a_citation_resolves_in_the_citing_documents_lane(self):
        self.build()
        self.assertEqual(closeout.resolve_decision_id(self.root, "G-7", "knowledge/x-spec.md"),
                         "knowledge/greenman/G-7-main-record.md")
        self.assertEqual(closeout.resolve_decision_id(self.root, "G-7", "knowledge/audit/a-note.md"),
                         "knowledge/audit/greenman/G-7-lane-seven.md")
        self.assertEqual(closeout.resolve_decision_id(self.root, "G-30", "knowledge/audit/a-note.md"),
                         "knowledge/audit/greenman/LEDGER.md:6")
        self.assertIsNone(closeout.resolve_decision_id(self.root, "G-30", "knowledge/x-spec.md"))   # not main's
        self.assertEqual(closeout.decision_ids(self.root, lane="audit") & {"G-7", "G-52", "G-852"}, {"G-7", "G-52"})

    def test_a_ledger_is_read_by_its_id_column(self):
        """The id is the cell under the header's `id` wherever it stands (the by-name rule), in the project's
        lookup, a lane's, and a citation's; a table whose header has no `id` falls back to the first two cells.
        Known-bad for the second-cell reading: an `id`-first ledger resolved none of its rows, and a
        `supersedes` cell was read as an id."""
        self.build()
        self.write("knowledge/greenman/LEDGER.md",
                   "# ledger\n\n| id | date | depth | decision | status | roadmap |\n|---|---|---|---|---|---|\n"
                   "| G-80 | 2026-09-18 | L | close R-1 | decided | R-1 |\n| G-90 | 2026-09-18 | M | burst | decided | R-2 |\n\n"
                   "| id | supersedes | decision | status |\n|---|---|---|---|\n| G-40 | G-39 | x | decided |\n\n"
                   "| date | depth | id | decision | status |\n|---|---|---|---|---|\n| d | M | G-20 | y | decided |\n\n"
                   "| date | ref | decision |\n|---|---|---|\n| d | G-50 | z |\n| G-51 | d | z |\n")
        self.write("knowledge/audit/greenman/LEDGER.md",
                   "# lane ledger\n\n| id | date | depth | decision | status |\n|---|---|---|---|---|\n"
                   "| G-A60 | d | M | a lane call | decided |\n\n"
                   "| date | depth | id | decision | status |\n|---|---|---|---|---|\n| d | M | G-A61 | another | decided |\n")
        ids = closeout.decision_ids(self.root)
        self.assertLessEqual({"G-80", "G-90", "G-40", "G-20", "G-50", "G-51", "G-A60", "G-A61"}, ids)
        self.assertNotIn("G-39", ids)
        lane = closeout.decision_ids(self.root, lane="audit")
        self.assertLessEqual({"G-A60", "G-A61"}, lane)
        self.assertEqual(closeout.resolve_decision_id(self.root, "G-80", "knowledge/x-spec.md"),
                         "knowledge/greenman/LEDGER.md:5")
        self.assertEqual(closeout.resolve_decision_id(self.root, "G-20", "knowledge/x-spec.md"),
                         "knowledge/greenman/LEDGER.md:14")
        self.assertIsNone(closeout.resolve_decision_id(self.root, "G-39", "knowledge/x-spec.md"))
        self.assertEqual(closeout.resolve_decision_id(self.root, "G-A61", "knowledge/audit/a-note.md"),
                         "knowledge/audit/greenman/LEDGER.md:9")
        self.assertEqual(closeout.ledger_row_ids(
            "| id | date |\n|---|---|\n| G-1 | d |\n| date | id |\n|---|---|\n| d | G-2 |\n"), [(3, "G-1"), (6, "G-2")])

    def test_an_id_listed_outside_with_no_record_fails(self):
        self.build()
        self.assertEqual(closeout.resolve_decision_id(self.root, "G-852", "knowledge/x-spec.md"),
                         "knowledge/greenman/LEDGER.md:3")
        self.assertIsNone(closeout.resolve_decision_id(self.root, "G-6", "knowledge/x-spec.md"))   # listed, not recorded

    def test_a_malformed_value_is_a_config_error(self):
        for value, bad in (("G-6, G-x", "'G-x'"), ("G-10..G-2", "'G-10..G-2'"), ("G-1..G-A5", "'G-1..G-A5'"),
                           ("852", "'852'")):
            with self.subTest(value=value):
                self.setUp()
                self.build(OUTSIDE_MAIN.replace("decision ids outside sequence: G-6, G-852",
                                                f"decision ids outside sequence: {value}"))
                (error,) = self.problems(level="ERROR")
                self.assertIn("`decision ids outside sequence`", error["message"])
                self.assertIn(bad, error["message"])
                self.assertEqual(error["where"], "CLAUDE.md:9")


if __name__ == "__main__":
    unittest.main()
