"""Tests for closeout/closeout.py. Run: python3 -m unittest discover -s tests (Python 3.9 or newer)."""
import git_quiet  # noqa: F401  (first: git's background upkeep off for every fixture; tests/git_quiet.py)
import contextlib
import datetime
import io
import json
import os
import re
import subprocess
import sys
import tempfile
import unittest
from unittest import mock
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "skills" / "closeout"))
import closeout  # noqa: E402

closeout.config_dirs = lambda: []  # never read the real memory from a test


def sh(cwd, *args):
    return subprocess.run(args, cwd=cwd, check=True, capture_output=True, text=True).stdout.strip()


class Repo:
    def __init__(self, path):
        self.path = Path(path)
        sh(self.path, "git", "init", "-q", "-b", "main")
        sh(self.path, "git", "config", "user.email", "t@t")
        sh(self.path, "git", "config", "user.name", "t")

    def write(self, rel, text):
        p = self.path / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        # Bytes, not write_text: Windows text mode would rewrite every \n as \r\n, so a fixture meant to carry LF
        # endings would never reach the code under test with the endings the test is about.
        p.write_bytes(text.encode("utf-8"))
        return p

    def commit(self, msg, *rels):
        sh(self.path, "git", "add", "--", *rels)
        sh(self.path, "git", "commit", "-q", "-m", msg)
        return sh(self.path, "git", "rev-parse", "--short", "HEAD")


class Base(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self._tmp.name).resolve()

    def tearDown(self):
        self._tmp.cleanup()


class DayZero(Base):
    def test_folder_without_git(self):
        (self.dir / "notes.md").write_text("hi", encoding="utf-8")
        f = closeout.facts(self.dir)
        self.assertIsNone(f["git"])
        self.assertEqual(f["pins"], [])
        self.assertIn("closeout", closeout.tree(self.dir, 2) + "closeout")

    def test_repo_without_commits(self):
        Repo(self.dir)
        f = closeout.facts(self.dir)
        self.assertIsNone(f["git"]["head"])
        self.assertEqual(f["git"]["recent"], [])


class Facts(Base):
    def test_marker_counts_only_commits_that_touch_more_than_the_pin(self):
        r = Repo(self.dir)
        r.write("app.py", "1")
        a = r.commit("a", "app.py")
        r.write("knowledge/00-CURRENT.md", f"# pin\nlast_marker: {a}\nupdated: 2026-01-01\n\n## Next\nship it\n")
        r.commit("pin", "knowledge/00-CURRENT.md")
        r.write("app.py", "2")
        b = r.commit("code", "app.py")
        pin = closeout.facts(self.dir)["pins"][0]
        self.assertEqual(pin["updated"], "2026-01-01")
        self.assertEqual(pin["next"], "ship it")
        self.assertEqual(pin["since_marker"]["state"], "behind")
        self.assertEqual(pin["since_marker"]["commits_not_in_pin"], 1)
        self.assertTrue(pin["since_marker"]["commits"][0].startswith(b))

    def test_marker_on_a_sibling_branch_is_diverged(self):
        r = Repo(self.dir)
        r.write("a", "1")
        r.commit("a", "a")
        sh(self.dir, "git", "checkout", "-q", "-b", "side")
        r.write("b", "1")
        side = r.commit("b", "b")
        sh(self.dir, "git", "checkout", "-q", "main")
        r.write("knowledge/00-CURRENT.md", f"last_marker: {side}\n")
        self.assertEqual(closeout.facts(self.dir)["pins"][0]["since_marker"]["state"], "diverged")

    def test_dirty_state_is_reported_by_kind(self):
        r = Repo(self.dir)
        r.write("a.md", "1")
        r.write("b.md", "1")
        r.commit("init", "a.md", "b.md")
        r.write("a.md", "2")
        r.write("b.md", "2")
        sh(self.dir, "git", "add", "b.md")
        r.write("new.txt", "x")
        g = closeout.facts(self.dir)["git"]
        self.assertEqual(g["modified_unstaged"], ["a.md"])
        self.assertEqual(g["staged"], ["b.md"])
        self.assertEqual(g["untracked"], ["new.txt"])
        self.assertIsNone(g["upstream"])

    def test_handoff_and_overview_candidates(self):
        r = Repo(self.dir)
        for rel in ("HANDOFF-2026-09-07.md", "docs/handoff/X-HANDOFF-2026-09-11.md", "docs/project-overview.html",
                    "docs/other.html"):
            r.write(rel, "x")
        d = closeout.facts(self.dir)["docs"]
        self.assertEqual(d["handoff_candidates"], ["HANDOFF-2026-09-07.md", "docs/handoff/X-HANDOFF-2026-09-11.md"])
        self.assertEqual(d["overview_candidates"], ["docs/project-overview.html"])

    def test_memory_for_this_folder_counted_once_with_index_problems(self):
        cfg = self.dir / "cfg"
        m = cfg / "projects" / re.sub(r"[^A-Za-z0-9]", "-", str(self.dir)) / "memory"
        m.mkdir(parents=True)
        (m / "MEMORY.md").write_text("- [A](a.md) — x\n- [Gone](gone.md) — y\n", encoding="utf-8")
        (m / "a.md").write_text("a", encoding="utf-8")
        (m / "b.md").write_text("b", encoding="utf-8")
        closeout.config_dirs = lambda: [cfg, cfg]  # a linked second account resolves to the same folder
        try:
            mem = closeout.facts(self.dir)["memory"]
        finally:
            closeout.config_dirs = lambda: []
        self.assertEqual(len(mem), 1)
        self.assertEqual((mem[0]["entries"], mem[0]["not_in_index"], mem[0]["index_points_at_missing"]),
                         (2, ["b.md"], ["gone.md"]))



class Tree(Base):
    def test_depth_collapses_to_counts_and_ignored_files_are_left_out(self):
        r = Repo(self.dir)
        r.write(".gitignore", "build/\n")
        r.write("src/a/b.py", "")
        r.write("src/a/c.py", "")
        r.write("src/top.py", "")
        r.write("build/out.bin", "")
        r.write("README.md", "")
        self.assertTrue(closeout.tree(self.dir, 2).splitlines()[0].endswith("(0 files)"))  # nothing tracked yet
        sh(self.dir, "git", "add", ".")
        out = closeout.tree(self.dir, 2)
        self.assertIn("a/  (2 files)", out)
        self.assertIn("top.py", out)
        self.assertNotIn("build", out)
        self.assertTrue(out.splitlines()[0].endswith("(5 files)"))

    def test_tracking_generated_and_hand_edit_rules(self):
        r = Repo(self.dir)
        r.write("CLAUDE.md", "# rules\n- **Never hand-edit `registry.json`.** Use the tool.\n"
                             "- The roadmap is the source of truth for status.\n- Unrelated line.\n")
        r.write("registry.json", "{}")
        r.write("docs/INDEX.md", "<!-- Generated by scripts/index.py — do not edit this file -->\n# index\n")
        r.write("gates/INDEX.md", "# GATES INDEX\n\n**DERIVED — do not hand-edit.** Regenerate: `make gates`\n")
        r.write("data.js", 'window.D = {\n  "generated": "2026-09-11T13:14:37",\n  "n": 1\n}\n')
        r.write("project.yml", "# The .xcodeproj is generated from this file; run xcodegen.\nname: X\n")
        for rel in ("knowledge/00-PROJECT-ROADMAP.md", "docs/SPRINT-LEDGER.md", "docs/dashboard.md",
                    "knowledge/00-PROJECT-BRAIN.md", "docs/explanation.md", "knowledge/02-ADDENDUM-sprint-15.10.md",
                    "knowledge/00-HANDOFF-sprint49.md", "knowledge/00-OPEN-ITEMS.md",
                    "knowledge/sprint-15-preflight-report.md", "knowledge/sections/brain-01-premise-audit.md",
                    "docs/backups/pre_launch/TODO.md", "starter/templates/00-ROADMAP.md", "docs/archive/HANDOFF.md"):
            r.write(rel, "x\n")
        d = closeout.facts(self.dir)["docs"]
        self.assertEqual(d["tracking_candidates"], ["docs/SPRINT-LEDGER.md", "knowledge/00-OPEN-ITEMS.md",
                                                    "knowledge/00-PROJECT-BRAIN.md", "knowledge/00-PROJECT-ROADMAP.md"])
        self.assertEqual(d["generated"], ["data.js", "docs/INDEX.md", "gates/INDEX.md"])
        self.assertEqual(d["never_hand_edit"], ["registry.json"])
        self.assertEqual([l.split(":")[1] for l in d["rules_in_claude_md"]], ["2", "3"])

    def test_untracked_files_appear_only_when_named(self):
        r = Repo(self.dir)
        r.write("a.py", "")
        r.commit("a", "a.py")
        r.write("theirs.html", "")
        r.write("HANDOFF.md", "")
        out = closeout.tree(self.dir, 2, also=["HANDOFF.md"])
        self.assertIn("HANDOFF.md", out)
        self.assertNotIn("theirs.html", out)


class Stamp(Base):
    def setUp(self):
        super().setUp()
        self.r = Repo(self.dir)
        self.r.write("x", "1")
        self.head = self.r.commit("x", "x")

    def test_inserts_after_a_leading_comment(self):
        pin = self.r.write("knowledge/00-CURRENT.md", "<!-- the pin -->\n## State\nbody\n")
        closeout.stamp(pin, None, today="2026-09-11")
        self.assertEqual(pin.read_text(encoding="utf-8").splitlines()[:3],
                         ["<!-- the pin -->", f"last_marker: {self.head}", "updated: 2026-09-11"])

    def test_replaces_a_combined_line_with_two(self):
        pin = self.r.write("p.md", "# pin\nlast_marker: 0000000        updated: 2026-01-01\nrest\n")
        closeout.stamp(pin, None, today="2026-09-11")
        text = pin.read_text(encoding="utf-8")
        self.assertEqual(text.count("last_marker:"), 1)
        self.assertIn(f"last_marker: {self.head}\nupdated: 2026-09-11\nrest", text)

    def test_refuses_when_the_pin_freezes_its_marker(self):
        pin = self.r.write("p.md", "> DO NOT ADVANCE `last_marker` TO ANY BRANCH HEAD\nlast_marker: abc1234\n")
        with self.assertRaises(SystemExit):
            closeout.stamp(pin, None)
        closeout.stamp(pin, None, force=True, today="2026-09-11")
        self.assertIn(f"last_marker: {self.head}", pin.read_text(encoding="utf-8"))

    def test_keep_marker_moves_only_the_date(self):
        pin = self.r.write("p.md", "> DO NOT ADVANCE `last_marker`\nlast_marker: abc1234        updated: 2026-01-01\nx\n")
        self.assertEqual(closeout.stamp(pin, None, keep_marker=True, today="2026-09-11"), "abc1234")
        self.assertIn("last_marker: abc1234\nupdated: 2026-09-11\nx", pin.read_text(encoding="utf-8"))
        pin2 = self.r.write("q.md", "# pin\nbody\n")
        closeout.stamp(pin2, None, keep_marker=True, today="2026-09-11")
        self.assertEqual(pin2.read_text(encoding="utf-8"), "# pin\nupdated: 2026-09-11\nbody\n")

    def test_a_pin_keeps_its_own_line_endings(self):
        crlf = self.r.path / "crlf.md"
        crlf.write_bytes(b"# pin\r\nupdated: 2026-01-01\r\nbody\r\n")
        closeout.stamp(crlf, None, keep_marker=True, today="2026-09-11")
        self.assertEqual(crlf.read_bytes(), b"# pin\r\nupdated: 2026-09-11\r\nbody\r\n")
        closeout.stamp(crlf, None, today="2026-09-11")
        self.assertNotIn(b"\n", crlf.read_bytes().replace(b"\r\n", b""))
        lf = self.r.path / "lf.md"
        lf.write_bytes(b"# pin\nbody\n")            # bytes on both platforms, so the file really does carry LF endings
        closeout.stamp(lf, None, today="2026-09-11")
        self.assertNotIn(b"\r", lf.read_bytes())

    def test_refuses_a_marker_that_is_not_a_commit(self):
        pin = self.r.write("p.md", "# pin\n")
        with self.assertRaises(SystemExit):
            closeout.stamp(pin, "deadbee")


class Hunks(Base):
    def test_stage_only_my_hunk_and_leave_theirs_in_the_working_tree(self):
        r = Repo(self.dir)
        r.write("README.md", "title\nold fact\nmiddle\nend\n")
        r.commit("init", "README.md")
        r.write("README.md", "title\nnew fact\nmiddle\nend\n\n## their draft\n- theirs\n")
        _, _, (_, hunks) = closeout.file_hunks(self.dir / "README.md")
        self.assertEqual(len(hunks), 2)
        closeout.stage_hunks(self.dir / "README.md", [1])
        self.assertEqual(sh(self.dir, "git", "show", ":README.md"), "title\nnew fact\nmiddle\nend")
        self.assertIn("## their draft", (self.dir / "README.md").read_text(encoding="utf-8"))
        self.assertIn("their draft", sh(self.dir, "git", "diff", "--", "README.md"))

    def test_a_later_hunk_alone_applies_at_the_right_place(self):
        r = Repo(self.dir)
        r.write("a.md", "1\n2\n3\n4\n5\n6\n")
        r.commit("init", "a.md")
        r.write("a.md", "0\n0\n1\n2\n3\n4\n5\nsix\n")
        closeout.stage_hunks(self.dir / "a.md", [2])
        self.assertEqual(sh(self.dir, "git", "show", ":a.md"), "1\n2\n3\n4\n5\nsix")

    def test_refuses_a_hunk_that_does_not_exist(self):
        r = Repo(self.dir)
        r.write("a.md", "1\n")
        r.commit("init", "a.md")
        r.write("a.md", "2\n")
        with self.assertRaises(SystemExit):
            closeout.stage_hunks(self.dir / "a.md", [2])


# A root HANDOFF.md is a declared handoff (R-29 § 5.4): the fixtures whose subject is something else carry the section
# its check reads, empty of open items as their project is.
OPEN_ITEMS = "\n## Open items\n- none open\n"


class Audit(Base):
    def setUp(self):
        super().setUp()
        self.r = Repo(self.dir)
        self.r.write("src/app.py", "")
        self.r.commit("init", "src/app.py")

    def levels(self, found, level):
        return [f for f in found if f[0] == level]

    def test_audit_reports_anothers_staged_file_as_one_warning(self):
        self.r.write("other.md", "theirs")
        self.r.write("HANDOFF.md", "mine")
        sh(self.dir, "git", "add", "other.md", "HANDOFF.md")
        found = closeout.audit(self.dir, ["HANDOFF.md"])
        self.assertEqual(self.levels(found, "FAIL"), [])
        (staged,) = [f for f in found if f[1] == "staged"]
        self.assertEqual(staged[0], "WARN")
        self.assertIn("1 staged file(s) this close-out did not write: other.md", staged[2])
        self.assertNotIn("restore", staged[2])
        self.assertNotIn("unstage", staged[2])

    def broken_roadmap(self):
        self.r.write("knowledge/00-ROADMAP.md", "## Now\n- R-1 see `src/gone.py`\n")
        self.r.commit("roadmap", "knowledge/00-ROADMAP.md")

    def test_audit_reports_a_pre_existing_broken_citation_without_failing(self):
        self.broken_roadmap()
        self.r.write("HANDOFF.md", "fine\n")
        found = closeout.audit(self.dir, ["HANDOFF.md"])
        self.assertEqual(self.levels(found, "FAIL"), [])
        (pre,) = self.levels(found, "PRE-EXISTING")
        self.assertEqual(pre[1], "knowledge/00-ROADMAP.md:2")
        self.assertIn("linking NO FILE: src/gone.py (reported, not this pass's)", pre[2])

    def test_audit_fails_on_a_broken_citation_this_pass_added(self):
        self.r.write("knowledge/00-ROADMAP.md", "## Now\n- R-1 a\n")
        self.r.commit("roadmap", "knowledge/00-ROADMAP.md")
        self.r.write("knowledge/00-ROADMAP.md", "## Now\n- R-1 a\n- R-2 see `src/gone.py`\n")
        fails = self.levels(closeout.audit(self.dir, ["knowledge/00-ROADMAP.md"]), "FAIL")
        self.assertEqual([f[1] for f in fails], ["knowledge/00-ROADMAP.md:3"])

    def test_audit_roadmap_line_pre_existing_versus_fresh(self):
        self.r.write("knowledge/00-ROADMAP.md", "## Now\n- R-1 a\n")
        self.r.write("knowledge/02-spec-a.md", "# a\nroadmap: R-9\n")
        self.r.commit("base", "knowledge")
        self.r.write("knowledge/02-spec-a.md", "# a\nroadmap: R-9\n\nmore text\n")   # the old line kept
        found = closeout.audit(self.dir, ["knowledge/02-spec-a.md"])
        self.assertEqual(self.levels(found, "FAIL"), [])
        self.assertEqual([f[1] for f in self.levels(found, "PRE-EXISTING")], ["knowledge/02-spec-a.md"])
        self.r.write("knowledge/02-spec-b.md", "# b\nroadmap: R-8\n")                 # written by this pass
        fails = self.levels(closeout.audit(self.dir, ["knowledge/02-spec-b.md"]), "FAIL")
        self.assertEqual([f[1] for f in fails], ["knowledge/02-spec-b.md"])

    def test_paths_that_exist_pass_and_missing_ones_warn(self):
        self.r.write("HANDOFF.md", "Run `python3 src/app.py --x` then read `app.py` and `src/gone.py:12`; "
                                   "see [ok](src/app.py) and [bad](nope.md); `git log a..HEAD`; `https://x.y/z`; "
                                   "`stats/*.json`; `origin/<name>`.\n" + OPEN_ITEMS)
        warns = self.levels(closeout.audit(self.dir, ["HANDOFF.md"]), "WARN")
        self.assertEqual(sorted(w[2] for w in warns), ["link target 'nope.md' not found", "path `src/gone.py` not found"])

    def test_a_citation_with_an_anchor_checks_the_path_not_the_anchor(self):
        # LINKING.md cites documents as path plus anchor; the anchor is link-audit's to check, the path is audit's
        self.r.write("HANDOFF.md", "see `src/app.py#§4.2 Layout` and `src/gone.py#Top`\n" + OPEN_ITEMS)
        warns = self.levels(closeout.audit(self.dir, ["HANDOFF.md"]), "WARN")
        self.assertEqual([w[2] for w in warns], ["path `src/gone.py` not found"])

    def test_only_lines_this_closeout_added_are_checked(self):
        self.r.write("OLD-HANDOFF.md", "line one\nsee `src/long_gone.py` TBD\n")
        self.r.commit("old", "OLD-HANDOFF.md")
        self.r.write("OLD-HANDOFF.md", "> Superseded by HANDOFF.md.\n\nline one\nsee `src/long_gone.py` TBD\n")
        self.assertEqual(closeout.audit(self.dir, ["OLD-HANDOFF.md"]), [])
        self.r.write("OLD-HANDOFF.md", "> Superseded by `src/nope.py`.\n\nline one\nsee `src/long_gone.py` TBD\n")
        self.assertEqual([f[1] for f in closeout.audit(self.dir, ["OLD-HANDOFF.md"])], ["OLD-HANDOFF.md:1"])

    def test_hand_editing_a_protected_or_generated_file_warns(self):
        self.r.write("CLAUDE.md", "- Never hand-edit `registry.json`.\n")
        self.r.write("registry.json", "{}")
        self.r.write("docs/INDEX.md", "<!-- AUTO-GENERATED -->\n")
        self.r.commit("rules", "CLAUDE.md", "registry.json", "docs/INDEX.md")
        self.r.write("registry.json", "{\"a\": 1}")
        self.r.write("docs/INDEX.md", "<!-- AUTO-GENERATED -->\nnew\n")
        warns = self.levels(closeout.audit(self.dir, ["registry.json", "docs/INDEX.md"]), "WARN")
        self.assertEqual([w[1] for w in warns], ["registry.json", "docs/INDEX.md"])

    def test_things_that_only_look_like_paths_are_not_checked(self):
        self.r.write("src/My Tool.app/x", "")
        self.r.write("HANDOFF.md", "Run `/directory-update`; `src/app.py::TestCase`; `rm -rf \"src/My Tool.app\"`; "
                                   "branch `rt/L1-app`; `~/no_such_top_dir_xyz/b`; `journal/YYYY-MM-DD.json`; `.html`; "
                                   f"`71259 {self.dir}/src/My Tool.app/x`.\n" + OPEN_ITEMS)
        self.assertEqual(closeout.audit(self.dir, ["HANDOFF.md"]), [])

    def test_html_without_charset_fails_and_code_paths_are_checked(self):
        self.r.write("docs/o.html", "<title>x</title><p><code>src/app.py</code> <code>src/missing.py</code></p>"
                                    '<a href="../src/app.py">a</a>')
        found = closeout.audit(self.dir, ["docs/o.html"])
        self.assertEqual(len(self.levels(found, "FAIL")), 1)
        self.assertIn("charset", self.levels(found, "FAIL")[0][2])
        self.assertEqual([w[2] for w in self.levels(found, "WARN")], ["path `src/missing.py` not found"])

    def test_secret_and_conflict_markers_fail(self):
        self.r.write("HANDOFF.md", "key sk-ant-api03-AAAAAAAAAAAAAAAAAAAAAAAA\n<<<<<<< HEAD\n")
        self.assertEqual(len(self.levels(closeout.audit(self.dir, ["HANDOFF.md"]), "FAIL")), 2)

    def test_project_shaped_secrets_fail_and_placeholders_do_not(self):
        self.r.write("HANDOFF.md", "TEST_DATABASE_URL=postgres://app:hunter2@127.0.0.1:5432/app\n"
                                   "DATA_API_KEY=abcdefghijklmnopqrstuvwxyz0123\n"
                                   "password: \"correct-horse-battery-staple\"\n")
        self.assertEqual(len(self.levels(closeout.audit(self.dir, ["HANDOFF.md"]), "FAIL")), 3)
        self.r.write("HANDOFF.md", "DATA_API_KEY=\nAPI_KEY=<your key>\npostgres://127.0.0.1:5432/app\n"
                                   "password is read from Keychain\nsecret: none\n"
                                   "postgresql://app:<password>@127.0.0.1:5432/db `postgresql://u:p@127.0.0.1/`\n"
                                   "--dsn \"postgresql://app:$DB_PW@127.0.0.1:5432\" app:CHANGE_ME@127.0.0.1\n"
                                   "const apiKey = process.env.DATA_API_KEY ?? process.env.OTHER_LONG_NAME\n")
        self.assertEqual(self.levels(closeout.audit(self.dir, ["HANDOFF.md"]), "FAIL"), [])

    def test_pin_needs_updated_and_a_next_heading(self):
        self.r.write("knowledge/00-CURRENT.md", "# pin\nnothing\n")
        fails = self.levels(closeout.audit(self.dir, ["knowledge/00-CURRENT.md"], "knowledge/00-CURRENT.md"), "FAIL")
        self.assertEqual(len(fails), 2)
        self.r.write("knowledge/00-CURRENT.md", "# pin\nupdated: 2026-09-23\n\n## Next action\nship\n")
        self.assertEqual(closeout.audit(self.dir, ["knowledge/00-CURRENT.md"], "knowledge/00-CURRENT.md",
                                        today="2026-09-23"), [])

    def test_link_audit_table_roadmap(self):
        self.r.write("knowledge/00-PROJECT-ROADMAP.md", "| 1 | a [spec `knowledge/02-SPEC-a.md#§2 Scope`] |\n"
                                                        "| 2 | b [spec `knowledge/02-SPEC-gone.md`] |\n"
                                                        "| 3 | c [see `knowledge/02-SPEC-a.md#§9 Nope`] |\n")
        self.r.write("knowledge/02-SPEC-a.md", "# a\nroadmap: 1\n\n## §2 Scope\n")
        self.r.write("knowledge/02-SPEC-b.md", "# b\n**roadmap:** 2, 7\n")
        self.r.write("knowledge/02-SPEC-c.md", "# c\nroadmap: none — a study, serves no row\n")
        self.r.write("knowledge/02-SPEC-d.md", "# d\nroadmap: none\n")
        self.r.write("knowledge/02-SPEC-e.md", "# e\nno line at all\n")
        found, counts = closeout.link_audit(self.dir)
        by = {w: (l, m) for l, w, m in found}
        self.assertEqual(by["knowledge/02-SPEC-a.md"][0], "LINKED")
        self.assertEqual(by["knowledge/02-SPEC-b.md"], ("BROKEN", "roadmap id(s) not in knowledge/00-PROJECT-ROADMAP.md: 7"))
        self.assertEqual(by["knowledge/02-SPEC-c.md"][0], "NONE")
        self.assertEqual(by["knowledge/02-SPEC-d.md"][0], "BROKEN")
        self.assertEqual(by["knowledge/02-SPEC-e.md"][0], "MISSING")
        self.assertEqual(by["knowledge/00-PROJECT-ROADMAP.md:2"], ("NO FILE", "knowledge/02-SPEC-gone.md"))
        self.assertEqual(by["knowledge/00-PROJECT-ROADMAP.md:3"][0], "NO ANCHOR")
        self.assertEqual(counts["OK"], 1)
        out = io.StringIO()                       # the report goes to the terminal; a passing run should be quiet
        with contextlib.redirect_stdout(out):
            self.assertEqual(closeout.main(["link-audit", str(self.dir)]), 0)
            self.assertEqual(closeout.main(["link-audit", str(self.dir), "--strict"]), 1)
        self.assertIn("link-audit:", out.getvalue())

    def test_a_report_names_ids_in_the_projects_own_form_and_says_when_there_is_no_claude_md(self):
        (self.dir / "CLAUDE.md").unlink(missing_ok=True)
        self.r.write("knowledge/00-ROADMAP.md", "## Now\n- R-1 a thing\n")
        self.r.write("knowledge/02-SPEC-z.md", "# z\nroadmap: R-99\n")
        found, _ = closeout.link_audit(self.dir, inventory=False)
        by = {w: (l, m) for l, w, m in found}
        self.assertIn("no CLAUDE.md with a `## Doc map`", by["CLAUDE.md"][1])
        self.assertEqual(by["knowledge/02-SPEC-z.md"][0], "BROKEN")
        self.assertIn("R-99", by["knowledge/02-SPEC-z.md"][1])      # the id as the author wrote it, not "99"
        self.r.write("knowledge/02-SPEC-z.md", "# z\nroadmap: R-1\n")
        found, _ = closeout.link_audit(self.dir, inventory=False)
        self.assertEqual({w: m for l, w, m in found if w.endswith("02-SPEC-z.md")}["knowledge/02-SPEC-z.md"], "R-1")

    def test_link_audit_writes_the_unlinked_inventory_and_audit_reports_linking(self):
        self.r.write("knowledge/00-PROJECT-ROADMAP.md", "| 1 | a [spec `knowledge/02-SPEC-gone.md`] |\n")
        self.r.write("knowledge/02-SPEC-e.md", "# The e spec\nno line\n")
        found, counts = closeout.link_audit(self.dir, today="2026-09-23")
        inv = self.dir / "knowledge" / "UNLINKED-2026-09-23.md"  # the project's own tree
        self.assertTrue(inv.is_file())
        text = inv.read_text(encoding="utf-8")
        self.assertIn("| `knowledge/02-SPEC-e.md` | The e spec |", text)
        self.assertIn("roadmap: none —", text)
        levels = closeout.audit(self.dir, ["knowledge/02-SPEC-e.md"])
        warns = [m for l, w, m in levels if l == "WARN" and w == "linking"]
        # the roadmap's broken citation is not on a line this pass wrote: reported, never a failure of this pass
        pre = [m for l, w, m in levels if l == "PRE-EXISTING" and "linking" in m]
        self.assertEqual(len(warns), 1)
        self.assertEqual([l for l, w, m in levels if l == "FAIL"], [])
        self.assertEqual(len(pre), 1)
        self.assertIn("NO FILE", pre[0])
        fails = [m for l, w, m in closeout.audit(self.dir, ["knowledge/00-PROJECT-ROADMAP.md"]) if l == "FAIL"]
        self.assertEqual(len(fails), 1)   # the roadmap is new, so every line of it is this pass's
        self.assertIn("NO FILE", fails[0])
        self.r.write("knowledge/02-SPEC-e.md", "# The e spec\nroadmap: 1\n")
        self.r.write("knowledge/00-PROJECT-ROADMAP.md", "| 1 | a [spec `knowledge/02-SPEC-e.md`] |\n")
        closeout.link_audit(self.dir, today="2026-09-23")
        self.assertFalse(inv.exists())  # empty backlog → no file
        closeout.link_audit(self.dir, today="2026-09-23")
        self.assertFalse(inv.exists())  # and a clean project never grows one

    def crossing_midnight(self):
        """`datetime.date` whose today() is 2026-09-23 on its first call and 2026-09-24 from then on: a test that
        reads the clock apart from the code under test fails when the day turns between the two reads (W-34)."""
        real, calls = datetime.date, []

        class Crossing(real):
            @classmethod
            def today(cls):
                calls.append(1)
                return cls(2026, 9, 23 if len(calls) == 1 else 24)
        return mock.patch("datetime.date", Crossing)

    def test_audit_and_link_audit_take_today(self):
        pin = "knowledge/00-CURRENT.md"
        # the pin: the old form reads the clock, then audit reads it again, a day later
        with self.crossing_midnight():
            today = datetime.date.today().isoformat()
            self.r.write(pin, f"# pin\nupdated: {today}\n\n## Next action\nship\n")
            old = closeout.audit(self.dir, [pin], pin)
        self.assertEqual([m for _, _, m in old], ["updated: 2026-09-23 is not today"])   # the known-bad: it fails
        with self.crossing_midnight():
            self.r.write(pin, "# pin\nupdated: 2026-09-23\n\n## Next action\nship\n")
            self.assertEqual(closeout.audit(self.dir, [pin], pin, today="2026-09-23"), [])
        # the inventory: link_audit reads the clock, then the old form reads it again to name the file
        self.r.write("knowledge/00-PROJECT-ROADMAP.md", "| 1 | a |\n")
        self.r.write("knowledge/02-SPEC-e.md", "# The e spec\nno line\n")
        with self.crossing_midnight():
            closeout.link_audit(self.dir)
            old_inv = self.dir / "knowledge" / f"UNLINKED-{datetime.date.today().isoformat()}.md"
        self.assertFalse(old_inv.is_file())                                                 # the known-bad: it fails
        with self.crossing_midnight():
            closeout.link_audit(self.dir, today="2026-09-23")
        self.assertTrue((self.dir / "knowledge" / "UNLINKED-2026-09-23.md").is_file())

    def test_an_inventory_left_in_the_pack_folder_moves_back_to_the_projects_tree(self):
        self.r.write("knowledge/00-PROJECT-ROADMAP.md", "| 1 | a |\n")
        self.r.write("knowledge/02-SPEC-e.md", "# e\nno line\n")
        old = self.r.write("xbt-skills-pack/doc-guardrails/UNLINKED-2026-01-01.md", "# old inventory\n")
        closeout.link_audit(self.dir)
        self.assertFalse(old.exists())
        self.assertEqual(len(list((self.dir / "knowledge").glob("UNLINKED-*.md"))), 1)
        self.r.write("knowledge/02-SPEC-e.md", "# e\nroadmap: 1\n")
        closeout.link_audit(self.dir)
        self.assertEqual(list(self.dir.rglob("UNLINKED-*.md")), [])

    def test_skill_ledgers_and_decision_records_are_linked_kinds_by_default(self):
        self.r.write("knowledge/00-ROADMAP.md", "## Now\n- R-1 build it\n")
        self.r.write("knowledge/decisions/G-1-storage.md", "# G-1\nroadmap: R-1\n")
        self.r.write("knowledge/redman/LEDGER.md", "# ledger\nno line\n")
        self.r.write("xbt-skills-pack/LEDGER.md", "# pack side ledger\nroadmap: none — the pack's side ledger\n")
        found, _ = closeout.link_audit(self.dir, inventory=False)
        by = {w: l for l, w, m in found}
        self.assertEqual(by["knowledge/decisions/G-1-storage.md"], "LINKED")
        self.assertEqual(by["knowledge/redman/LEDGER.md"], "MISSING")
        self.assertEqual(by["xbt-skills-pack/LEDGER.md"], "NONE")
        self.assertEqual(closeout.link_check_file(self.dir, self.dir / "knowledge/redman/LEDGER.md")[0], "MISSING")

    def test_facts_reads_the_declared_ledgers_the_defaults_and_flags_untracked_records(self):
        self.r.write("CLAUDE.md", "# x\n\n## Doc map\nroadmap: knowledge/00-ROADMAP.md\n"
                                  "ledgers: knowledge/audit/redman/LEDGER.md\ndecisions: knowledge/audit/decisions\n")
        self.r.write("knowledge/00-ROADMAP.md", "## Now\n- R-1 a\n")
        # each ledger carries its header row: a ledger is read by its `state` column, never by position
        head = "| date | kind | item | size | state | where |\n|---|---|---|---|---|---|\n"
        self.r.write("knowledge/redman/LEDGER.md", head + "| d | k | i | small | filed | w |\n| d | k | i | small | closed — x | w |\n")
        self.r.write("knowledge/audit/redman/LEDGER.md", head + "| d | k | i | big | asked · later | w |\n")
        self.r.write("xbt-skills-pack/redman/LEDGER.md", head + "| d | k | i | small | filed | w |\n")  # an older layout, still read
        self.r.write("xbt-skills-pack/LEDGER.md", head + "| d | k | i | small | filed | w |\n")  # the pack's side ledger: not the project's
        self.r.write("knowledge/decisions/G-1-a.md", "# G-1\nroadmap: none — test\nstatus: decided\n")
        self.r.write("knowledge/audit/decisions/G-2-b.md", "# G-2\nroadmap: none — test\nstatus: awaiting the user\n")
        self.r.write("knowledge/decisions/G-3-c.md", "# G-3\nroadmap: none — test\n"
                     "- Status: ruled by the user 2026-09-14: \"do it anyway\" — shortcut taken — real fix owed\n")  # a bullet, as written live
        sh(self.dir, "git", "add", "knowledge/redman/LEDGER.md", "CLAUDE.md", "knowledge/00-ROADMAP.md")
        sh(self.dir, "git", "commit", "-qm", "base")
        f = closeout.facts(self.dir)
        self.assertEqual(f["redman"]["open_rows"], 3)
        self.assertEqual(f["redman"]["ledgers"], ["knowledge/audit/redman/LEDGER.md", "knowledge/redman/LEDGER.md",
                                                  "xbt-skills-pack/redman/LEDGER.md"])
        self.assertEqual(f["pack_side_ledger"], {"path": "xbt-skills-pack/LEDGER.md", "open_rows": 1})
        self.assertEqual({k: f["greenman"][k] for k in ("decisions", "awaiting_ruling", "real_fix_owed")},
                         {"decisions": 3, "awaiting_ruling": ["knowledge/audit/decisions/G-2-b.md"],
                          "real_fix_owed": ["knowledge/decisions/G-3-c.md"]})
        self.assertIn("knowledge/audit/redman/LEDGER.md", f["untracked_records"])
        self.assertNotIn("knowledge/redman/LEDGER.md", f["untracked_records"])
        warns = [m for l, w, m in closeout.audit(self.dir, []) if l == "WARN" and w == "records"]
        self.assertEqual(len(warns), 1)
        self.assertIn("not in git", warns[0])

    def test_declared_ledgers_and_decisions_are_linked_kinds_even_when_the_map_lists_its_own(self):
        self.r.write("CLAUDE.md", "# x\n\n## Doc map\nroadmap: knowledge/00-ROADMAP.md\nlinked kinds: docs/specs/*.md\n"
                                  "ledgers: knowledge/redman/LEDGER.md, lanes/audit/LEDGER.md\ndecisions: docs/adr\n")
        self.r.write("knowledge/00-ROADMAP.md", "## Now\n- R-1 a\n")
        for rel in ("lanes/audit/LEDGER.md", "lanes/audit/REDMAN-2026-09-14-x.md", "docs/adr/G-1-x.md", "docs/adr/LEDGER.md"):
            self.r.write(rel, "# no line\n")
        found, _ = closeout.link_audit(self.dir, inventory=False)
        missing = sorted(w for l, w, m in found if l == "MISSING")
        self.assertEqual(missing, ["docs/adr/G-1-x.md", "docs/adr/LEDGER.md", "lanes/audit/LEDGER.md",
                                   "lanes/audit/REDMAN-2026-09-14-x.md"])  # a deep run sits beside its lane's ledger
        self.assertEqual(closeout.link_check_file(self.dir, self.dir / "docs/adr/G-1-x.md")[0], "MISSING")

    def test_facts_flags_an_untracked_decisions_ledger_and_a_shortcut_owed_in_a_ledger_row(self):
        self.r.write("knowledge/00-ROADMAP.md", "## Now\n- R-1 a\n")
        self.r.write("knowledge/decisions/LEDGER.md",
                     "# Greenman ledger\nroadmap: none — ledger\n\n| date | id | the choice | depth | status |\n|---|---|---|---|---|\n"
                     "| 2026-09-14 | S | skip the flaky retry test | S | ruled by the user 2026-09-14: \"skip it\" — shortcut taken — real fix owed |\n"
                     "| 2026-09-14 | G-2 | storage | L | awaiting the user |\n"
                     "| 2026-09-14 | S | rename | S | decided |\n")
        self.r.write("knowledge/decisions/G-2-storage.md", "# G-2\nroadmap: R-1\nstatus: awaiting the user\n")
        f = closeout.facts(self.dir)
        self.assertIn("knowledge/decisions/LEDGER.md", f["untracked_records"])
        self.assertEqual(f["greenman"]["awaiting_ruling"], ["knowledge/decisions/G-2-storage.md"])  # once, not again from its row
        self.assertEqual(f["greenman"]["real_fix_owed"], ["knowledge/decisions/LEDGER.md: skip the flaky retry test"])

    def test_the_gate_judges_the_staged_copy_not_the_working_tree(self):
        self.r.write("knowledge/00-ROADMAP.md", "## Now\n- R-1 a\n")
        self.r.write("knowledge/02-SPEC-new.md", "# new\nno line\n")
        sh(self.dir, "git", "add", "knowledge/00-ROADMAP.md", "knowledge/02-SPEC-new.md")
        self.r.write("knowledge/02-SPEC-new.md", "# new\nroadmap: R-1\n")  # fixed on disk, not staged
        self.assertEqual([w for l, w, m in closeout.link_check_staged(self.dir)[0]], ["knowledge/02-SPEC-new.md"])
        sh(self.dir, "git", "add", "knowledge/02-SPEC-new.md")
        self.r.write("knowledge/02-SPEC-new.md", "# new\nbroken again on disk only\n")
        self.assertEqual(closeout.link_check_staged(self.dir)[0], [])

    def test_staged_check_reports_or_blocks_by_the_doc_map(self):
        self.r.write("knowledge/00-ROADMAP.md", "## Now\n- R-1 a\n")
        self.r.write("knowledge/02-SPEC-new.md", "# new\nno line\n")
        self.r.write("knowledge/02-SPEC-linked.md", "# linked\nroadmap: R-1\n")
        self.r.write("knowledge/02-SPEC-unstaged.md", "# not staged\n")
        sh(self.dir, "git", "add", "knowledge/00-ROADMAP.md", "knowledge/02-SPEC-new.md", "knowledge/02-SPEC-linked.md")
        found, mode = closeout.link_check_staged(self.dir)
        self.assertEqual(mode, "report")
        self.assertEqual([(l, w) for l, w, m in found], [("MISSING", "knowledge/02-SPEC-new.md")])
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.assertEqual(closeout.main(["link-audit", str(self.dir), "--staged"]), 0)
        self.assertIn("knowledge/02-SPEC-new.md", out.getvalue())
        self.r.write("CLAUDE.md", "# x\n\n## Doc map\nroadmap: knowledge/00-ROADMAP.md\nguardrails: block\n")
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.assertEqual(closeout.main(["link-audit", str(self.dir), "--staged"]), 1)
        self.assertIn("A document someone else wrote", out.getvalue())
        self.assertIn("a generator's or a script's output is the session's", out.getvalue())   # G-54
        self.assertIn("Never --no-verify", out.getvalue())
        self.r.write("CLAUDE.md", "# x\n\n## Doc map\nroadmap: knowledge/00-ROADMAP.md\nguardrails: off\n")
        self.assertEqual(closeout.link_check_staged(self.dir), ([], "off"))

    def gate_state_folder(self):
        """A temporary state folder for install_gate's registration record (never the live one)."""
        state = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: __import__("shutil").rmtree(state, ignore_errors=True))
        p = mock.patch.dict(os.environ, {"DOC_GUARDRAILS_STATE": str(state)})
        p.start()
        self.addCleanup(p.stop)
        return state

    def needs_config_hooks(self):
        r = subprocess.run(["git", "hook", "list", "pre-commit"], cwd=self.dir, capture_output=True, text=True)
        if r.returncode == 129 or "unknown subcommand" in r.stderr:
            self.skipTest("git <2.54 on PATH: config hooks unsupported")

    def test_install_gate_registers_once_keeps_a_foreign_hook_and_gates_real_commits(self):
        self.needs_config_hooks()
        state = self.gate_state_folder()
        hooks = self.dir / ".git" / "hooks"
        hooks.mkdir(parents=True, exist_ok=True)
        (hooks / "pre-commit").write_text("#!/bin/sh\necho theirs\n", encoding="utf-8")
        closeout.install_gate(self.dir)
        closeout.install_gate(self.dir)  # twice: still one registration
        values = subprocess.run(["git", "-C", str(self.dir), "config", "--get-all", "hook.doc-guardrails.command"],
                                capture_output=True, text=True).stdout.splitlines()
        self.assertEqual(values, [closeout.GATE_LAUNCHER])
        self.assertEqual((hooks / "pre-commit").read_text(encoding="utf-8"), "#!/bin/sh\necho theirs\n")
        self.assertEqual(len(list(state.glob("gates/*/registered.json"))), 1)   # the record lands in the temp state
        self.r.write("knowledge/00-ROADMAP.md", "## Now\n- R-1 a\n")
        self.r.write("knowledge/02-SPEC-new.md", "# new\nno line\n")
        sh(self.dir, "git", "add", "-A")
        env = {**os.environ, "CLOSEOUT_PY": str(Path(closeout.__file__).resolve()), "CLOSEOUT_PYTHON": sys.executable}
        r = subprocess.run(["git", "commit", "-qm", "report only"], cwd=self.dir, capture_output=True, text=True, env=env)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("MISSING", r.stdout + r.stderr)
        self.r.write("CLAUDE.md", "# x\n\n## Doc map\nroadmap: knowledge/00-ROADMAP.md\nguardrails: block\n")
        self.r.write("knowledge/02-SPEC-two.md", "# two\nno line\n")
        sh(self.dir, "git", "add", "-A")
        r = subprocess.run(["git", "commit", "-qm", "blocked"], cwd=self.dir, capture_output=True, text=True, env=env)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("knowledge/02-SPEC-two.md", r.stdout + r.stderr)

    def test_the_gate_finds_its_helper_at_run_time_and_fails_safe_when_it_cannot_run(self):
        self.needs_config_hooks()
        self.gate_state_folder()
        closeout.install_gate(self.dir)
        value = subprocess.run(["git", "-C", str(self.dir), "config", "--get", "hook.doc-guardrails.command"],
                               capture_output=True, text=True).stdout
        self.assertNotIn(Path(sys.executable).as_posix(), value)                  # no interpreter baked in
        self.assertNotIn(Path(closeout.__file__).absolute().as_posix(), value)    # no copy of the pack baked in
        self.r.write("knowledge/00-ROADMAP.md", "## Now\n- R-1 a\n")
        self.r.write("knowledge/02-SPEC-new.md", "# new\nno line\n")
        sh(self.dir, "git", "add", "-A")
        bare = {**os.environ, "HOME": str(self.dir), "CLOSEOUT_PY": str(self.dir / "missing.py")}
        r = subprocess.run(["git", "commit", "-qm", "report, helper missing"], cwd=self.dir, capture_output=True, text=True, env=bare)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("gate skipped", r.stdout + r.stderr)
        self.r.write("CLAUDE.md", "# x\n\n## Doc map\nroadmap: knowledge/00-ROADMAP.md\nguardrails: block\n")
        self.r.write("knowledge/02-SPEC-two.md", "# two\n")
        sh(self.dir, "git", "add", "-A")
        r = subprocess.run(["git", "commit", "-qm", "block, helper missing"], cwd=self.dir, capture_output=True, text=True, env=bare)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("cannot run", r.stdout + r.stderr)
        import shutil
        r = subprocess.run([shutil.which("git"), "commit", "-qm", "block, no python on PATH"], cwd=self.dir,
                           capture_output=True, text=True,
                           env={**os.environ, "PATH": str(self.dir / "nonexistent"), "HOME": str(self.dir)})
        # an empty or useless PATH (git still prepends its own exec-path): the launcher falls back to /usr/bin:/bin
        # to read the mode, so a `guardrails: block` project is refused, never passed as "gate skipped"
        self.assertNotEqual(r.returncode, 0, r.stderr)
        self.assertIn("commit refused", r.stdout + r.stderr)
        r = subprocess.run([shutil.which("git"), "commit", "-qm", "block, empty PATH"], cwd=self.dir,
                           capture_output=True, text=True, env={**os.environ, "PATH": "", "HOME": str(self.dir)})
        self.assertNotEqual(r.returncode, 0, r.stderr)
        self.assertIn("commit refused", r.stdout + r.stderr)

    def test_install_gate_refuses_a_folder_without_git_and_never_edits_a_hook_inside_the_tree(self):
        self.gate_state_folder()
        plain = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: __import__("shutil").rmtree(plain, ignore_errors=True))
        with self.assertRaises(ValueError):
            closeout.install_gate(plain)
        self.assertEqual(list(plain.iterdir()), [])  # no stray pre-commit file
        self.assertEqual(closeout.main(["install-gate", str(plain)]), 2)
        self.assertFalse(closeout.hook_in_tree(self.dir, self.dir / ".git" / "hooks" / "pre-commit"))
        # a hooks folder in the tree is not the pack's: nothing is written there (W-5), whichever route registers
        sh(self.dir, "git", "config", "core.hooksPath", ".husky")
        state = closeout.install_gate(self.dir)
        self.assertEqual(state.hook, None)
        self.assertIn(state.code, (0, 3))
        self.assertFalse((self.dir / ".husky" / "pre-commit").exists())
        self.assertTrue(closeout.hook_in_tree(self.dir, self.dir / ".husky" / "pre-commit"))

    def test_only_the_leading_id_list_counts_so_numbers_in_the_reason_are_not_ids(self):
        self.assertEqual(closeout.cited_ids("954, 968 — merged 2026-09-14 in a018f, see 9270", "table rows"), ["954", "968"])
        self.assertEqual(closeout.cited_ids("rows 148.6 and 731; filed 2026-09-15", "table rows"), ["148.6", "731"])
        self.assertEqual(closeout.cited_ids("R-3, R-4 — replaces R-9 (dropped)", "bullet tags"), ["3", "4", "9"])  # R- ids are explicit
        self.assertEqual(closeout.cited_ids("the audit branch, merged 2026-09-14", "table rows"), [])
        # ids cited in prose, as real specs write them
        self.assertEqual(closeout.cited_ids("refines row 129 (stays partial — IV-rank still pending)", "table rows"), ["129"])
        self.assertEqual(closeout.cited_ids("umbrella row 360 (amended for WS6); deferred options at rows 361–366", "table rows"),
                         ["360", "361", "366"])
        self.assertEqual(closeout.cited_ids("§21, rows 247–249. Frame inherited from sprint 15", "table rows"), ["247", "249"])
        self.assertEqual(closeout.cited_ids("new backlog row (to be filed) — universe v2", "table rows"), [])
        self.r.write("knowledge/00-PROJECT-ROADMAP.md", "| id | item |\n|---|---|\n| 954 | a |\n| 968 | b |\n")
        self.r.write("CLAUDE.md", "# x\n\n## Doc map\nroadmap: knowledge/00-PROJECT-ROADMAP.md\nid form: table rows\nlinked kinds: knowledge/*HANDOFF*.md\n")
        self.r.write("knowledge/00-HANDOFF-x.md", "# h\nroadmap: 954, 968 — merged 2026-09-14, commit 9270abc\n")
        self.assertIsNone(closeout.link_check_file(self.dir, self.dir / "knowledge/00-HANDOFF-x.md"))
        found, _ = closeout.link_audit(self.dir, inventory=False)
        self.assertEqual([l for l, w, m in found if w == "knowledge/00-HANDOFF-x.md"], ["LINKED"])

    def test_link_audit_prose_roadmap_and_doc_map(self):
        self.r.write("CLAUDE.md", "# x\n\n## Doc map\nroadmap: knowledge/00-ROADMAP.md\nid form: bullet tags `R-<n>`\n"
                                  "linked kinds: docs/specs/*.md\nexempt: docs/specs/README.md\n\n## Other\n")
        self.r.write("knowledge/00-ROADMAP.md", "## Now\n- R-1 build it (`docs/specs/one.md#Plan`)\n- untagged thing\n")
        self.r.write("docs/specs/one.md", "# one\nroadmap: R-1\n## Plan\n")
        self.r.write("docs/specs/two.md", "# two\nroadmap: R-9\n")
        self.r.write("docs/specs/README.md", "exempt\n")
        self.r.write("knowledge/02-SPEC-not-a-kind.md", "not in the map's kinds\n")
        found, counts = closeout.link_audit(self.dir)
        by = {w: (l, m) for l, w, m in found}
        self.assertNotIn("CLAUDE.md", by)  # a map exists, no defaults note
        self.assertIn("1 without an `R-<n>` id", by["knowledge/00-ROADMAP.md"][1])
        self.assertEqual(by["docs/specs/one.md"][0], "LINKED")
        self.assertEqual(by["docs/specs/two.md"][0], "BROKEN")
        self.assertNotIn("docs/specs/README.md", by)
        self.assertNotIn("knowledge/02-SPEC-not-a-kind.md", by)
        self.assertEqual(counts["OK"], 1)

    def test_cli_exit_code(self):
        self.r.write("HANDOFF.md", "fine\n" + OPEN_ITEMS)
        cwd = os.getcwd()
        out = io.StringIO()  # keep the audit's report off the test run's own output, so its last line is the verdict
        try:
            os.chdir(self.dir)
            with contextlib.redirect_stdout(out):
                self.assertEqual(closeout.main(["audit", "--changed", "HANDOFF.md"]), 0)
                self.r.write("HANDOFF.md", "<<<<<<< x\n" + OPEN_ITEMS)
                self.assertEqual(closeout.main(["audit", "--changed", "HANDOFF.md"]), 1)
        finally:
            os.chdir(cwd)
        self.assertIn("audit: 1 fail, 0 pre-existing broken, 0 warn", out.getvalue())

    def test_open_worklist_items_fail(self):
        wl = self.r.write("wl.md", "- [x] README.md:2 — 235 tests → unchanged (swift test)\n"
                                   "- [ ] README.md:4 — 3 commits\n")
        fails = self.levels(closeout.audit(self.dir, ["src/app.py"], worklist=wl), "FAIL")
        self.assertEqual(len(fails), 1)
        self.assertIn("README.md:4", fails[0][2])
        wl.write_text("- [x] README.md:2 — 235 tests → unchanged (swift test)\n"
                      "- [x] README.md:4 — 3 commits → now 5 (git rev-list --count)\n", encoding="utf-8")
        self.assertEqual(self.levels(closeout.audit(self.dir, ["src/app.py"], worklist=wl), "FAIL"), [])
        wl.write_text("- [x] README.md:2 — 235 tests\n- [x] README.md:4 — 3 commits →   \n", encoding="utf-8")  # ticked, nothing found
        fails = self.levels(closeout.audit(self.dir, ["src/app.py"], worklist=wl), "FAIL")
        self.assertEqual([f[1] for f in fails], ["wl.md:1", "wl.md:2"])
        self.assertIn("no finding", fails[0][2])
        self.assertEqual(len(self.levels(closeout.audit(self.dir, ["src/app.py"], worklist=self.dir / "none.md"),
                                         "FAIL")), 1)


    def test_baseline_warns_only_when_asked(self):
        self.r.write("HANDOFF.md", "fine\n" + OPEN_ITEMS)
        self.assertEqual(closeout.audit(self.dir, ["HANDOFF.md"]), [])
        warns = self.levels(closeout.audit(self.dir, ["HANDOFF.md"], check_baseline=True), "WARN")
        self.assertEqual([w[1] for w in warns],
                         ["baseline:readme", "baseline:claude_md", "baseline:pin", "baseline:overview", "baseline:roadmap",
                          "baseline:brain"])


class CommitGate(unittest.TestCase):
    """The commit gate's installer never writes a hook file the pack does not own, and registers nothing where the
    project keeps its hooks in a folder it tracks until its doc map opts in (`gate: config-hook`)."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name).resolve()
        p = mock.patch.dict(os.environ, {"DOC_GUARDRAILS_STATE": str(self.tmp / "state")})
        p.start()
        self.addCleanup(p.stop)

    def managed_repo(self, hooks_path="absolute", gate_line=False):
        """A project that commits `scripts/git-hooks/pre-commit` and points core.hooksPath at it (main worktree `as`),
        with a linked worktree `as_lane` on its own branch."""
        main = self.tmp / "as"
        main.mkdir()
        r = Repo(main)
        r.write("scripts/git-hooks/pre-commit", "#!/bin/sh\ngit diff --cached --name-only | grep -q '\\.py$' || exit 0\n")
        (main / "scripts/git-hooks/pre-commit").chmod(0o755)
        r.write("CLAUDE.md", "# p\n\n## Doc map\nroadmap: knowledge/00-ROADMAP.md\n" + ("gate: config-hook\n" if gate_line else ""))
        r.write("knowledge/00-ROADMAP.md", "## Now\n- R-1 a\n")
        r.commit("base", "scripts", "CLAUDE.md", "knowledge")
        value = str(main / "scripts" / "git-hooks") if hooks_path == "absolute" else "scripts/git-hooks"
        sh(main, "git", "config", "core.hooksPath", value)
        sh(main, "git", "worktree", "add", "-q", "-b", "lane", str(self.tmp / "as_lane"))
        return main, self.tmp / "as_lane"

    def everything(self, *roots):
        """Every file in the trees, the common config and git's own hooks folder, with its bytes."""
        out = {}
        for root in roots:
            for f in sorted(Path(root).rglob("*")):
                if f.is_file() and ".git" not in f.relative_to(root).parts[:1]:
                    out[str(f)] = f.read_bytes()
        common = Path(roots[0]) / ".git"
        out["config"] = (common / "config").read_bytes()
        for f in sorted((common / "hooks").glob("*")) if (common / "hooks").is_dir() else []:
            out[str(f)] = f.read_bytes()
        return out

    def keys(self, root):
        return subprocess.run(["git", "-C", str(root), "config", "--get-regexp", r"^hook\.doc-guardrails"],
                              capture_output=True, text=True).returncode

    def test_the_safety_half_alone_writes_no_hook_file_it_does_not_own(self):
        main, lane = self.managed_repo()
        before = self.everything(main, lane)
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = closeout.main(["install-gate", str(lane)])
        self.assertEqual(code, 0, out.getvalue())
        self.assertTrue(out.getvalue().startswith("commit gate: hooks managed by the project: see its doc map ("),
                        out.getvalue())
        self.assertEqual(self.everything(main, lane), before)
        self.assertEqual(self.keys(main), 1)
        # main's map opts in, and the hook at core.hooksPath is the project's: no hook file in either tree is written
        (main / "CLAUDE.md").write_text("# p\n\n## Doc map\nroadmap: knowledge/00-ROADMAP.md\ngate: config-hook\n",
                                        encoding="utf-8")
        hook = main / "scripts" / "git-hooks" / "pre-commit"
        text = hook.read_bytes()
        with contextlib.redirect_stdout(io.StringIO()):
            closeout.main(["install-gate", str(main)])
            closeout.main(["install-gate", str(lane)])
        self.assertEqual(hook.read_bytes(), text)
        self.assertFalse((lane / "scripts" / "git-hooks" / "pre-commit").read_bytes() != text)

    def test_hooks_managed_by_project(self):
        main, lane = self.managed_repo()
        self.assertEqual(closeout.hooks_managed_by_project(main), str(main / "scripts" / "git-hooks"))
        self.assertEqual(closeout.hooks_managed_by_project(lane), str(main / "scripts" / "git-hooks"))
        sh(main, "git", "config", "core.hooksPath", "scripts/git-hooks")
        self.assertEqual(closeout.hooks_managed_by_project(main), "scripts/git-hooks")
        for value in ("untracked-hooks", str(self.tmp / "elsewhere"), ".git/hooks"):
            (main / "untracked-hooks").mkdir(exist_ok=True)
            (self.tmp / "elsewhere").mkdir(exist_ok=True)
            sh(main, "git", "config", "core.hooksPath", value)
            self.assertIsNone(closeout.hooks_managed_by_project(main), value)
        sh(main, "git", "config", "--unset", "core.hooksPath")
        self.assertIsNone(closeout.hooks_managed_by_project(main))

    def test_containment_is_by_file_identity_never_a_string_prefix(self):
        main, lane = self.managed_repo()
        # a sibling folder outside every worktree, sharing main's path as a string prefix
        (self.tmp / "as_hooks").mkdir()
        (self.tmp / "as_hooks" / "pre-commit").write_text("#!/bin/sh\n", encoding="utf-8")
        sh(main, "git", "config", "core.hooksPath", str(self.tmp / "as_hooks"))
        self.assertIsNone(closeout.hooks_managed_by_project(main))
        # a tracked folder in the sibling linked worktree: attributed to the lane, whose ls-files lists it
        (lane / "lhooks").mkdir()
        (lane / "lhooks" / "pre-commit").write_text("#!/bin/sh\n", encoding="utf-8")
        sh(lane, "git", "add", "lhooks")
        sh(lane, "git", "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "lane hooks")
        sh(main, "git", "config", "core.hooksPath", str(lane / "lhooks"))
        self.assertEqual(closeout.hooks_managed_by_project(main), str(lane / "lhooks"))
        self.assertEqual(closeout.path_within(str(lane / "lhooks"), [str(main), str(lane)])[0], str(lane))
        self.assertTrue(str(lane / "lhooks").startswith(str(main)))   # the string-prefix rule would say main's
        # a worktree nested in main's tree: the deepest match
        sh(main, "git", "worktree", "add", "-q", "-b", "nested", str(main / ".wt" / "nested"))
        nested = main / ".wt" / "nested"
        (nested / "nh").mkdir()
        (nested / "nh" / "pre-commit").write_text("#!/bin/sh\n", encoding="utf-8")
        sh(nested, "git", "add", "nh")
        sh(nested, "git", "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "nested hooks")
        sh(main, "git", "config", "core.hooksPath", str(nested / "nh"))
        self.assertEqual(closeout.hooks_managed_by_project(main), str(nested / "nh"))
        self.assertEqual(closeout.path_within(str(nested / "nh"), [str(main), str(lane), str(nested)])[0], str(nested))
        # a second drive, simulated with ntpath: not contained, and nothing raises
        import ntpath
        self.assertIsNone(closeout.path_within("D:\\hooks", ["C:\\repo"], pathmod=ntpath))
        with self.assertRaises(ValueError):
            ntpath.commonpath(["D:\\hooks", "C:\\repo"])   # the known-bad: commonpath raises across drives

    def test_a_case_folded_hooks_path_is_the_worktree_it_names(self):
        main, _ = self.managed_repo()
        folded = Path(str(main).replace("/as", "/AS")) / "Scripts" / "Git-Hooks"
        if not (folded.exists() and os.path.samefile(folded, main / "scripts" / "git-hooks")):
            self.skipTest("the temp folder's file system does not fold case: the case-folded path is not the same folder")
        sh(main, "git", "config", "core.hooksPath", str(folded))
        self.assertEqual(closeout.hooks_managed_by_project(main), str(folded))
        # known-bad: commonpath finds the folded path in no worktree
        self.assertNotEqual(os.path.commonpath([os.path.realpath(str(folded)), str(main)]), str(main))

    # ---- commit 2: the config hook, the launcher, the heartbeat, facts, the registrations and the hold

    HELPER = str(Path(closeout.__file__).resolve())

    def env(self, **extra):
        return {**os.environ, "CLOSEOUT_PY": self.HELPER, "CLOSEOUT_PYTHON": sys.executable,
                "DOC_GUARDRAILS_STATE": str(self.tmp / "state"), **extra}

    def config_hooks(self):
        r = subprocess.run(["git", "hook", "list", "pre-commit"], cwd=self.tmp, capture_output=True, text=True)
        if r.returncode == 129 or "unknown subcommand" in r.stderr:
            self.skipTest("git <2.54 on PATH: config hooks unsupported")

    def shim(self):
        """A git on PATH that has no config hooks (`hook list` answers 129), everything else passed through."""
        real = subprocess.run(["git", "--exec-path"], capture_output=True, text=True).stdout.strip()
        git_bin = __import__("shutil").which("git")
        bindir = self.tmp / "shim"
        bindir.mkdir(exist_ok=True)
        (bindir / "git").write_text('#!/bin/sh\ncase " $* " in *" hook list "*) echo "error: unknown subcommand: list" '
                                    f'>&2; exit 129;; esac\nexec "{git_bin}" "$@"\n', encoding="utf-8")
        (bindir / "git").chmod(0o755)
        return mock.patch.dict(os.environ, {"PATH": f"{bindir}{os.pathsep}{os.environ['PATH']}"})

    def plain_repo(self, name="p", claude_md="# p\n\n## Doc map\nroadmap: knowledge/00-ROADMAP.md\n"):
        root = self.tmp / name
        root.mkdir()
        r = Repo(root)
        r.write("CLAUDE.md", claude_md)
        r.write("knowledge/00-ROADMAP.md", "## Now\n- R-1 a\n")
        r.commit("base", "CLAUDE.md", "knowledge")
        return root, r

    def install(self, root, env=None):
        out = io.StringIO()
        with mock.patch.dict(os.environ, env or {}), contextlib.redirect_stdout(out):
            code = closeout.main(["install-gate", str(root)])
        return code, out.getvalue()

    def commit_spec(self, root, name="02-spec-x.md", *flags, env=None):
        (root / "knowledge").mkdir(exist_ok=True)
        (root / "knowledge" / name).write_text("# x\n", encoding="utf-8")
        subprocess.run(["git", "-C", str(root), "add", f"knowledge/{name}"], check=True)
        return subprocess.run(["git", "-C", str(root), "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", name,
                               *flags], capture_output=True, text=True, env=env or self.env())

    def heartbeats(self):
        return [json.loads(p.read_text(encoding="utf-8")) for p in sorted((self.tmp / "state" / "gates").glob("*/*-*.json"))]

    def foreign(self, root, body):
        hooks = root / "scripts" / "git-hooks"
        hooks.mkdir(parents=True, exist_ok=True)
        (hooks / "pre-commit").write_text(body, encoding="utf-8")
        (hooks / "pre-commit").chmod(0o755)
        subprocess.run(["git", "-C", str(root), "add", "scripts"], check=True)
        subprocess.run(["git", "-C", str(root), "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "hooks"],
                       check=True, capture_output=True)
        subprocess.run(["git", "-C", str(root), "config", "core.hooksPath", str(hooks)], check=True)

    OPTED = "# p\n\n## Doc map\nroadmap: knowledge/00-ROADMAP.md\ngate: config-hook\n"

    def test_gate_runs_before_a_foreign_hook_that_exits_0(self):
        self.config_hooks()
        root, _ = self.plain_repo(claude_md=self.OPTED)
        self.foreign(root, "#!/bin/sh\ngit diff --cached --name-only | grep -q '\\.py$' || exit 0\n")
        code, out = self.install(root)
        self.assertEqual(code, 0, out)
        r = self.commit_spec(root)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("doc-guardrails: MISSING — knowledge/02-spec-x.md", r.stdout + r.stderr)
        (hb,) = self.heartbeats()
        self.assertEqual((hb["helper"], hb["result"]), (self.HELPER, "report"))

    def test_gate_runs_before_exit_1_and_exec_endings(self):
        self.config_hooks()
        root, _ = self.plain_repo(claude_md=self.OPTED)
        self.foreign(root, "#!/bin/sh\nexit 1\n")
        self.install(root)
        r = self.commit_spec(root)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("doc-guardrails: MISSING — knowledge/02-spec-x.md", r.stdout + r.stderr)
        (root / "scripts" / "git-hooks" / "pre-commit").write_text("#!/bin/sh\nexec true\n", encoding="utf-8")
        r = self.commit_spec(root, "02-spec-y.md")
        self.assertIn("doc-guardrails: MISSING — knowledge/02-spec-y.md", r.stdout + r.stderr)

    def test_gate_runs_in_a_linked_worktree(self):
        self.config_hooks()
        root, _ = self.plain_repo(claude_md=self.OPTED)
        self.foreign(root, "#!/bin/sh\nexit 0\n")
        self.install(root)
        subprocess.run(["git", "-C", str(root), "worktree", "add", "-q", "-b", "lane", str(self.tmp / "lane")], check=True)
        r = self.commit_spec(self.tmp / "lane")
        self.assertIn("doc-guardrails: MISSING — knowledge/02-spec-x.md", r.stdout + r.stderr)

    def test_install_from_a_worktree_never_edits_a_tree_file(self):
        self.config_hooks()
        root, _ = self.plain_repo(claude_md=self.OPTED)
        self.foreign(root, "#!/bin/sh\nexit 0\n")
        subprocess.run(["git", "-C", str(root), "worktree", "add", "-q", "-b", "lane", str(self.tmp / "lane")], check=True)
        before = self.everything(root, self.tmp / "lane")
        before.pop("config")
        code, out = self.install(self.tmp / "lane")
        self.assertEqual(code, 0, out)
        self.assertTrue(out.startswith("commit gate: runs as a git config hook (git "), out)
        after = self.everything(root, self.tmp / "lane")
        after.pop("config")
        self.assertEqual(after, before)
        with self.shim():
            subprocess.run(["git", "-C", str(root), "config", "--unset-all", "hook.doc-guardrails.command"])
            subprocess.run(["git", "-C", str(root), "config", "--unset-all", "hook.doc-guardrails.event"])
            code, out = self.install(self.tmp / "lane")
        self.assertEqual(code, 3, out)
        self.assertIn("commit gate: NOT installed", out)
        self.assertIn(closeout.GATE_BEGIN, out)   # the hand-over quotes the block
        after = self.everything(root, self.tmp / "lane")
        after.pop("config")
        self.assertEqual(after, before)

    def test_fallback_places_the_block_first_in_a_pack_owned_hook(self):
        root, _ = self.plain_repo()
        hook = root / ".git" / "hooks" / "pre-commit"
        hook.write_text("#!/bin/bash\n# the project's checks\nset -e\necho theirs\nexit 0\n", encoding="utf-8")
        hook.chmod(0o755)
        with self.shim():
            code, out = self.install(root)
        self.assertEqual(code, 0, out)
        self.assertTrue(out.startswith(f"commit gate: runs first in {hook}"), out)
        text = hook.read_text(encoding="utf-8")
        self.assertLess(text.index(closeout.GATE_BEGIN), text.index("echo theirs"))
        self.assertEqual(closeout.gate_reach(text), [])
        with self.shim():
            r = self.commit_spec(root, env=self.env(PATH=os.environ["PATH"]))
        self.assertIn("doc-guardrails: MISSING — knowledge/02-spec-x.md", r.stdout + r.stderr)

    def test_a_python_hook_is_never_edited(self):
        root, _ = self.plain_repo()
        hook = root / ".git" / "hooks" / "pre-commit"
        hook.write_text("#!/usr/bin/env python3\nprint('theirs')\n", encoding="utf-8")
        with self.shim():
            code, out = self.install(root)
        self.assertEqual(code, 3, out)
        self.assertEqual(hook.read_text(encoding="utf-8"), "#!/usr/bin/env python3\nprint('theirs')\n")

    def test_launcher_finds_the_helper_at_run_time(self):
        self.config_hooks()
        root, _ = self.plain_repo()
        copy = self.tmp / "copy" / "closeout.py"
        copy.parent.mkdir()
        __import__("shutil").copy(self.HELPER, copy)
        subprocess.run([sys.executable, str(copy), "install-gate", str(root)], check=True, capture_output=True,
                       env=self.env())
        copy.unlink()
        value = subprocess.run(["git", "-C", str(root), "config", "--get", "hook.doc-guardrails.command"],
                               capture_output=True, text=True).stdout
        self.assertNotIn(str(copy), value)
        self.assertNotIn(sys.executable, value)
        cfg = self.tmp / "cfg"
        (cfg / "skills" / "closeout").mkdir(parents=True)
        (cfg / "skills" / "closeout" / "closeout.py").symlink_to(self.HELPER)
        env = {k: v for k, v in self.env(CLAUDE_CONFIG_DIR=str(cfg)).items() if k != "CLOSEOUT_PY"}
        r = self.commit_spec(root, env=env)
        self.assertIn("doc-guardrails: MISSING — knowledge/02-spec-x.md", r.stdout + r.stderr)
        self.assertEqual(self.heartbeats()[-1]["helper"], self.HELPER)
        env = {k: v for k, v in self.env(CLAUDE_CONFIG_DIR=str(self.tmp / "none"), HOME=str(self.tmp / "none")).items()
               if k != "CLOSEOUT_PY"}
        r = self.commit_spec(root, "02-spec-y.md", env=env)
        self.assertEqual(r.returncode, 0)
        self.assertIn("gate skipped: the gate cannot run", r.stderr)
        (root / "CLAUDE.md").write_text("# p\n\n## Doc map\nroadmap: knowledge/00-ROADMAP.md\nguardrails: block\n",
                                        encoding="utf-8")
        r = self.commit_spec(root, "02-spec-z.md", env=env)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("cannot run", r.stderr)

    def test_install_twice_one_registration_and_old_block_removed(self):
        self.config_hooks()
        root, _ = self.plain_repo()
        hook = root / ".git" / "hooks" / "pre-commit"
        hook.write_bytes(b"#!/bin/sh\r\necho theirs\r\n" + closeout.GATE_BEGIN.encode() + b"\r\nold\r\n"
                         + closeout.GATE_END.encode() + b"\r\necho after\r\n")
        self.install(root)
        self.install(root)
        values = subprocess.run(["git", "-C", str(root), "config", "--get-all", "hook.doc-guardrails.command"],
                                capture_output=True, text=True).stdout.splitlines()
        self.assertEqual(len(values), 1)
        self.assertEqual(hook.read_bytes(), b"#!/bin/sh\r\necho theirs\r\necho after\r\n")

    def gate(self, root):
        return closeout.facts(root)["gate"]

    def test_facts_says_whether_the_gate_ran_on_the_last_commit(self):
        self.config_hooks()
        root, _ = self.plain_repo()
        self.install(root)
        self.commit_spec(root)
        self.assertIs(self.gate(root)["ran_on_last_commit"], True)
        r = self.commit_spec(root, "02-spec-b.md", "--no-verify")
        g = self.gate(root)
        self.assertIs(g["ran_on_last_commit"], False)
        self.assertTrue(g["note"].startswith("did not run on "), g["note"])
        (root / "knowledge" / "02-spec-c.md").write_text("# c\n", encoding="utf-8")
        subprocess.run(["git", "-C", str(root), "add", "knowledge/02-spec-c.md"], check=True)
        subprocess.run(["git", "-C", str(root), "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "--amend",
                        "--no-edit"], check=True, capture_output=True, env=self.env())
        self.assertIs(self.gate(root)["ran_on_last_commit"], True)       # the amend's base is the commit it replaced
        (root / "knowledge" / "02-spec-d.md").write_text("# d\n", encoding="utf-8")
        subprocess.run(["git", "-C", str(root), "add", "knowledge/02-spec-d.md"], check=True)
        subprocess.run(["git", "-C", str(root), "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "--amend",
                        "--no-edit", "--no-verify"], check=True, capture_output=True, env=self.env())
        self.assertIs(self.gate(root)["ran_on_last_commit"], False)

    def test_install_gate_prints_one_of_its_states_with_its_exit_code(self):
        self.config_hooks()
        root, _ = self.plain_repo()
        code, out = self.install(root)
        self.assertEqual(code, 0)
        self.assertRegex(out.splitlines()[0], r"^commit gate: runs as a git config hook \(git [0-9.]+; every worktree: 1\)$")
        self.assertEqual(out.splitlines()[1], "last ran: never")
        code, out = self.install(root)
        self.assertIn("already registered", out.splitlines()[0])
        plain = self.tmp / "plain"
        plain.mkdir()
        self.assertEqual(self.install(plain)[0], 2)
        held, _ = self.plain_repo("held")
        (self.tmp / "state" / "gates").mkdir(parents=True, exist_ok=True)
        (self.tmp / "state" / "gates" / "HOLD").write_text("rollback rehearsal\n", encoding="utf-8")
        code, out = self.install(held)
        self.assertEqual(code, 0)
        self.assertEqual(out.splitlines()[0], f"commit gate: registration held for a pack rollback "
                                              f"({self.tmp / 'state' / 'gates' / 'HOLD'}); nothing written")

    def test_heartbeat_only_with_the_flag_and_bounded(self):
        root, _ = self.plain_repo()
        (root / "knowledge" / "02-spec-x.md").write_text("# x\n", encoding="utf-8")
        subprocess.run(["git", "-C", str(root), "add", "knowledge/02-spec-x.md"], check=True)
        with contextlib.redirect_stdout(io.StringIO()):
            closeout.main(["link-audit", str(root), "--staged"])
            self.assertEqual(self.heartbeats(), [])
            for _ in range(60):
                closeout.main(["link-audit", str(root), "--staged", "--heartbeat"])
        self.assertEqual(len(self.heartbeats()), 50)

    def test_gate_error_never_refuses_a_report_only_commit(self):
        root, _ = self.plain_repo()
        (root / "knowledge" / "02-spec-x.md").write_text("# x\n", encoding="utf-8")
        subprocess.run(["git", "-C", str(root), "add", "knowledge/02-spec-x.md"], check=True)
        err = io.StringIO()
        with mock.patch.object(closeout, "judge", side_effect=RuntimeError("boom")), \
                contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(err):
            self.assertEqual(closeout.main(["link-audit", str(root), "--staged", "--heartbeat"]), 0)
            (root / "CLAUDE.md").write_text("# p\n\n## Doc map\nroadmap: knowledge/00-ROADMAP.md\nguardrails: block\n",
                                            encoding="utf-8")
            self.assertEqual(closeout.main(["link-audit", str(root), "--staged", "--heartbeat"]), 1)
        self.assertIn("doc-guardrails: gate error: RuntimeError: boom", err.getvalue())
        self.assertEqual([h["result"] for h in self.heartbeats()], ["error", "error"])

    def test_a_declining_repository_is_never_registered(self):
        root, _ = self.plain_repo()
        hook = root / ".git" / "hooks" / "pre-commit"
        hook.write_text("#!/bin/sh\n" + closeout.GATE_BEGIN + "\nold\n" + closeout.GATE_END + "\necho mine\n",
                        encoding="utf-8")
        subprocess.run(["git", "-C", str(root), "config", "hook.doc-guardrails.enabled", "false"], check=True)
        code, out = self.install(root)
        self.assertEqual((code, out.splitlines()[0]), (0, "commit gate: declined by the repository "
                                                          "(hook.doc-guardrails.enabled=false in local config)"))
        keys = subprocess.run(["git", "-C", str(root), "config", "--show-scope", "--get-regexp", r"^hook\."],
                              capture_output=True, text=True).stdout.splitlines()
        self.assertEqual(keys, ["local\thook.doc-guardrails.enabled false"])
        self.assertEqual(hook.read_text(encoding="utf-8"), "#!/bin/sh\necho mine\n")
        self.assertEqual(self.gate(root)["form"], "declined")
        subprocess.run(["git", "-C", str(root), "config", "--unset", "hook.doc-guardrails.enabled"], check=True)
        glob = self.tmp / "global.config"
        glob.write_text("[hook \"doc-guardrails\"]\n\tenabled = false\n", encoding="utf-8")
        with self.shim():
            code, out = self.install(root, env={"GIT_CONFIG_GLOBAL": str(glob)})
        self.assertIn("(hook.doc-guardrails.enabled=false in global config)", out)
        self.assertEqual(hook.read_text(encoding="utf-8"), "#!/bin/sh\necho mine\n")

    def launcher_commit(self, root, helper, claude_md=None, name="02-spec-l.md"):
        if claude_md is not None:
            (root / "CLAUDE.md").write_text(claude_md, encoding="utf-8")
        return self.commit_spec(root, name, env=self.env(CLOSEOUT_PY=str(helper)))

    def test_the_launcher_never_refuses_a_report_only_commit_across_versions(self):
        self.config_hooks()
        root, _ = self.plain_repo()
        self.install(root)
        old = self.tmp / "old.py"      # a helper that does not know the gate's arguments (argparse, exit 2)
        old.write_text("import sys\nsys.stderr.write('closeout.py: error: unrecognized arguments: --heartbeat\\n')\n"
                       "sys.exit(2)\n", encoding="utf-8")
        one = self.tmp / "one.py"
        one.write_text("import sys\nsys.exit(1)\n", encoding="utf-8")
        boom = self.tmp / "boom.py"
        boom.write_text("raise ImportError('broken')\n", encoding="utf-8")
        report = "# p\n\n## Doc map\nroadmap: knowledge/00-ROADMAP.md\n"
        block = report + "guardrails: block\n"
        for n, (helper, words) in enumerate(((old, "closeout.py exit 2"), (one, "closeout.py exit 1"),
                                             (boom, "closeout.py exit 1"), (self.tmp / "missing.py", "cannot run"))):
            r = self.launcher_commit(root, helper, report, f"02-spec-r{n}.md")
            self.assertEqual(r.returncode, 0, (helper, r.stderr))
            self.assertIn("gate skipped", r.stderr)
            self.assertIn(words, r.stderr)
            r = self.launcher_commit(root, helper, block, f"02-spec-b{n}.md")
            self.assertEqual(r.returncode, 1, (helper, r.stderr))
            if words != "closeout.py exit 1":   # exit 1 under block is the helper's own verdict or refusal
                self.assertIn("commit refused", r.stderr)
        cases = [("## Doc map\nguardrails: block\n", True), ("## Doc map\n  Guardrails: BLOCK\n", True),
                 ("## Doc map\nguardrails: blocked\n", False), ("## Doc map\r\nguardrails: block\r\n", True),
                 ("## Doc map — where it lives\nguardrails: block\n", True),
                 ("## Doc map · x\nguardrails: block\n", True),
                 ("## Doc map\nroadmap: knowledge/00-ROADMAP.md\n\n## Doc map: lane audit\nguardrails: block\n", False),
                 ("### Doc map\nguardrails: block\n", False),
                 ("## Doc map\nguardrails: report\n\n## Notes\nguardrails: block\n", False)]
        for n, (text, is_block) in enumerate(cases):
            with self.subTest(text=text):
                r = self.launcher_commit(root, one, "# p\n\n" + text, f"02-spec-c{n}.md")
                self.assertEqual(r.returncode, 1 if is_block else 0, r.stderr)
                ctx_block = closeout.link_context(root)["mode"] == "block"
                self.assertEqual(ctx_block, is_block)
        # no map in CLAUDE.md, a pin map saying block: block; a CLAUDE.md map saying report beside it: report
        (root / "knowledge" / "00-CURRENT.md").write_text("# pin\n\n## Doc map\nguardrails: block\n", encoding="utf-8")
        r = self.launcher_commit(root, one, "# p\nno map\n", "02-spec-pin.md")
        self.assertEqual(r.returncode, 1, r.stderr)
        r = self.launcher_commit(root, one, "# p\n\n## Doc map\nguardrails: report\n", "02-spec-pin2.md")
        self.assertEqual(r.returncode, 0, r.stderr)

    def registered(self, name):
        root, _ = self.plain_repo(name)
        self.install(root)
        return root

    def registrations(self, env=None):
        out = io.StringIO()
        with mock.patch.dict(os.environ, env or {}), contextlib.redirect_stdout(out):
            code = closeout.main(["gate-registrations"])
        return code, out.getvalue().splitlines()

    def unset(self, root):
        for k in ("command", "event"):
            subprocess.run(["git", "-C", str(root), "config", "--unset-all", f"hook.doc-guardrails.{k}"])

    def test_gate_registrations_lists_and_refuses_until_every_one_is_unset(self):
        self.config_hooks()
        a, b = self.registered("a"), self.registered("b")
        code, lines = self.registrations()
        self.assertEqual(code, 1)
        self.assertEqual(sorted(l.split()[0] for l in lines), ["registered", "registered"])
        other = self.tmp / "other-state"
        with mock.patch.dict(os.environ, {"DOC_GUARDRAILS_STATE": str(other)}):
            self.registered("c")                  # an eval case's own state folder: never listed here
        self.unset(a)
        code, lines = self.registrations()
        self.assertEqual((code, sorted(l.split()[0] for l in lines)), (1, ["registered", "unset"]))
        self.unset(b)
        self.assertEqual(self.registrations()[0], 0)
        d = self.registered("d")
        __import__("shutil").rmtree(d)
        code, lines = self.registrations()
        self.assertEqual(code, 0)
        self.assertTrue(any(l.startswith("gone") for l in lines), lines)
        self.assertFalse(any(str(self.tmp / "c") in l for l in lines))

    def test_the_rollback_hold_stops_every_registration_and_lets_the_list_shrink(self):
        self.config_hooks()
        a, b = self.registered("a"), self.registered("b")
        hold = self.tmp / "state" / "gates" / "HOLD"
        hold.write_text("rollback rehearsal\n", encoding="utf-8")
        self.assertEqual(self.registrations()[0], 1)
        self.unset(a)
        config = (a / ".git" / "config").read_bytes()
        code, out = self.install(a)
        self.assertEqual(code, 0)
        self.assertTrue(out.startswith("commit gate: registration held for a pack rollback ("), out)
        self.assertEqual(self.keys(a), 1)
        self.assertEqual((a / ".git" / "config").read_bytes(), config)
        c, _ = self.plain_repo("c")
        with self.shim():
            code, out = self.install(c)
        self.assertTrue(out.startswith("commit gate: registration held"))
        self.assertFalse((c / ".git" / "hooks" / "pre-commit").exists())
        self.unset(b)
        self.assertEqual(self.registrations()[0], 0)
        other = self.tmp / "other-state" / "gates"          # a hold in another state folder stops nothing here
        other.mkdir(parents=True)
        (other / "HOLD").write_text("x", encoding="utf-8")
        hold.unlink()
        code, out = self.install(a)
        self.assertTrue(out.startswith("commit gate: runs as a git config hook"), out)
        self.assertEqual(self.registrations()[0], 1)

    def test_gate_registrations_labels_a_harness_task_repository(self):
        self.config_hooks()
        runs = self.tmp / "runs"
        (runs / "SI1").mkdir(parents=True)
        (self.tmp / "runs_other").mkdir()
        task, _ = self.plain_repo("runs/SI1/t6")
        other, _ = self.plain_repo("runs_other/t6")
        self.install(task)
        self.install(other)
        with mock.patch.object(closeout, "HARNESS_ROOTS", (runs,)):
            code, lines = self.registrations()
            self.assertEqual(code, 1)
            by = {l.split()[1]: l for l in lines}
            self.assertTrue(by[str(task)].endswith("harness task: unset pack-side"), by)
            self.assertNotIn("harness task", by[str(other)])
            self.unset(task)
            self.assertEqual(self.registrations()[0], 1)
            self.unset(other)
            self.assertEqual(self.registrations()[0], 0)
        self.install(task)
        with mock.patch.object(closeout, "HARNESS_ROOTS", ()):
            self.assertFalse(any("harness task" in l for l in self.registrations()[1]))

    def test_gate_verdict_re_matches_every_verdict_line_and_nothing_else(self):
        root, _ = self.plain_repo()
        (root / "knowledge" / "02-spec-x.md").write_text("# x\n", encoding="utf-8")
        (root / "knowledge" / "02-spec-y.md").write_text("# y\nroadmap: R-9\n", encoding="utf-8")
        subprocess.run(["git", "-C", str(root), "add", "knowledge"], check=True)
        for claude_md in (None, "# p\n\n## Doc map\nroadmap: knowledge/00-ROADMAP.md\nguardrails: block\n"):
            if claude_md:
                (root / "CLAUDE.md").write_text(claude_md, encoding="utf-8")
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                closeout.main(["link-audit", str(root), "--staged"])
            verdicts = [l for l in out.getvalue().splitlines() if " — knowledge/" in l]
            self.assertEqual(len(verdicts), 2)
            self.assertTrue(all(closeout.GATE_VERDICT_RE.match(l) for l in verdicts))
        for line in ("doc-guardrails: gate error: RuntimeError: boom",
                     "doc-guardrails: gate skipped: the gate cannot run (no python or closeout.py found); report-only project, commit continues",
                     "doc-guardrails: commit refused: the gate cannot run (no python or closeout.py found) and this project says guardrails: block"):
            self.assertIsNone(closeout.GATE_VERDICT_RE.match(line))

    def test_a_project_placed_block_wins_and_is_never_edited(self):
        self.config_hooks()
        root, _ = self.plain_repo()
        launcher = closeout.GATE_LAUNCHER
        self.foreign(root, "#!/bin/sh\n" + closeout.GATE_BEGIN + "\n( " + launcher + " ) || exit 1\n"
                     + closeout.GATE_END + "\necho theirs\nexit 0\n")
        hook = root / "scripts" / "git-hooks" / "pre-commit"
        text = hook.read_bytes()
        code, out = self.install(root)
        self.assertEqual(code, 0, out)
        self.assertEqual(out.splitlines()[0], f"commit gate: runs first in {hook} (placed by the project)")
        self.assertEqual(self.keys(root), 1)
        self.assertEqual(hook.read_bytes(), text)
        r = self.commit_spec(root)
        self.assertEqual((r.stdout + r.stderr).count("doc-guardrails: MISSING — knowledge/02-spec-x.md"), 1)

    def test_a_project_managed_hooks_folder_is_never_registered_uninvited(self):
        for form in ("absolute", "relative"):
            with self.subTest(form=form):
                self.setUp()
                main, lane = self.managed_repo(form)
                before = self.everything(main, lane)
                code, out = self.install(lane)
                self.assertEqual(code, 0)
                self.assertTrue(out.startswith("commit gate: hooks managed by the project: see its doc map ("), out)
                self.assertEqual(self.keys(main), 1)
                self.assertEqual(self.everything(main, lane), before)
                self.assertFalse(list((self.tmp / "state").glob("gates/*/registered.json")))
                g = self.gate(main)
                self.assertEqual((g["form"], g["ran_on_last_commit"]), ("project-managed", None))
                self.assertEqual(self.install(main)[0], 0)
                self.assertEqual(self.everything(main, lane), before)
                with self.shim():
                    code, out = self.install(main)
                self.assertEqual(code, 0)
                self.assertTrue(out.startswith("commit gate: hooks managed by the project"), out)

    def test_the_doc_map_line_opts_the_project_in(self):
        self.config_hooks()
        main, lane = self.managed_repo()
        (main / "CLAUDE.md").write_text(self.OPTED, encoding="utf-8")
        subprocess.run(["git", "-C", str(main), "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qam", "opt in"],
                       check=True, capture_output=True)
        code, out = self.install(main)
        self.assertTrue(out.startswith("commit gate: runs as a git config hook ("), out)
        self.assertEqual(len(list((self.tmp / "state").glob("gates/*/registered.json"))), 1)
        for where in (main, lane):
            r = self.commit_spec(where, f"02-spec-{where.name}.md")
            self.assertIn(f"doc-guardrails: MISSING — knowledge/02-spec-{where.name}.md", r.stdout + r.stderr)
        self.assertEqual({h["helper"] for h in self.heartbeats()}, {self.HELPER})
        config = (main / ".git" / "config").read_bytes()
        code, out = self.install(lane)
        self.assertEqual(code, 0)
        self.assertIn("registered by the project's opt-in, left as it is", out.splitlines()[0])
        self.assertEqual((main / ".git" / "config").read_bytes(), config)
        # a typo, or the line in a lane section: not read, nothing registered
        for text in (self.OPTED.replace("config-hook", "config-hooks"),
                     "# p\n\n## Doc map\nroadmap: knowledge/00-ROADMAP.md\n\n## Doc map: lane x\nroadmap: "
                     "knowledge/00-ROADMAP.md\nid form: table rows | X-<n> |\ngate: config-hook\n"):
            self.unset(main)
            (main / "CLAUDE.md").write_text(text, encoding="utf-8")
            code, out = self.install(main)
            self.assertTrue(out.startswith("commit gate: hooks managed by the project"), out)
            self.assertTrue(any(p["key"] == "gate" or "gate" in p["message"] for p in closeout.link_context(main)["problems"]))

    def test_no_project_hooks_folder_means_no_change(self):
        self.config_hooks()
        for claude_md in ("# p\n\n## Doc map\nroadmap: knowledge/00-ROADMAP.md\n", self.OPTED):
            with self.subTest(claude_md=claude_md):
                self.setUp()
                root, _ = self.plain_repo(claude_md=claude_md)
                self.assertTrue(self.install(root)[1].startswith("commit gate: runs as a git config hook"))
        for value in ("untracked-hooks", "outside", ".git/hooks"):
            with self.subTest(value=value):
                self.setUp()
                root, _ = self.plain_repo()
                (root / "untracked-hooks").mkdir()
                (self.tmp / "outside").mkdir()
                path = str(self.tmp / "outside") if value == "outside" else value
                subprocess.run(["git", "-C", str(root), "config", "core.hooksPath", path], check=True)
                self.assertIsNone(closeout.hooks_managed_by_project(root))
                self.assertTrue(self.install(root)[1].startswith("commit gate: runs as a git config hook"))

    def test_a_git_error_in_the_managed_test_registers_nothing(self):
        main, lane = self.managed_repo()
        (main / ".git" / "index").write_bytes(b"garbage")
        with self.assertRaises(closeout.GitProbeError) as e:
            closeout.hooks_managed_by_project(main)
        self.assertIn("git ls-files", str(e.exception))
        self.assertIn("exit 128", str(e.exception))
        self.assertIn("index file smaller than expected", str(e.exception))
        before = self.everything(main, lane)
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.assertEqual(closeout.main(["install-gate", str(main)]), 0)
        self.assertTrue(out.getvalue().startswith("commit gate: not registered: could not tell whether the project "
                                                  "manages its hooks (git ls-files"), out.getvalue())
        self.assertEqual(self.keys(main), 1)
        self.assertEqual(self.everything(main, lane), before)
        self.assertFalse(list((self.tmp / "state").glob("gates/*/registered.json")))
        self.assertEqual(closeout.facts(main)["gate"]["form"], "undetermined")


class Baseline(Base):
    """Every project carries the same six docs; a pass creates the ones it lacks."""

    def test_missing_and_present(self):
        r = Repo(self.dir)
        for rel in ("README.md", "HANDOFF-2026-09-07.md", "docs/project-overview.html", "knowledge/00-SPRINT-LEDGER.md",
                    "docs/DECISIONS.md"):
            r.write(rel, "x")
        b = closeout.facts(self.dir)["baseline"]
        self.assertEqual(b["missing"], ["claude_md", "pin", "roadmap", "brain"])  # a ledger or decision log is not a plan
        self.assertEqual(b["present"]["handoff"], ["HANDOFF-2026-09-07.md"])
        for rel in ("CLAUDE.md", "knowledge/00-CURRENT.md", "knowledge/00-OPEN-ITEMS.md", "knowledge/00-BRAIN.md"):
            r.write(rel, "x")
        b = closeout.facts(self.dir)["baseline"]
        self.assertEqual(b["missing"], [])
        self.assertEqual(b["present"]["roadmap"], ["knowledge/00-OPEN-ITEMS.md"])
        self.assertEqual(b["present"]["brain"], ["knowledge/00-BRAIN.md"])

    def test_a_dated_handoff_pointed_at_as_the_pin_is_not_a_pin(self):
        docs = {"readme": None, "claude_md": False, "handoff_candidates": [], "overview_candidates": [],
                "tracking_candidates": []}
        b = closeout.baseline(docs, [{"path": "HANDOFF-2026-09-07.md", "exists": True}])
        self.assertIn("pin", b["missing"])


def load_fresh():
    """A second copy of the module with the real config_dirs — the one imported above is stubbed out."""
    import importlib.util
    spec = importlib.util.spec_from_file_location("closeout_fresh", closeout.__file__)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def escaped(path):
    return re.sub(r"[^A-Za-z0-9]", "-", str(path))


class ConfigDirs(unittest.TestCase):
    def test_env_names_the_only_config_dirs(self):
        fresh, old = load_fresh(), os.environ.get("CLOSEOUT_CONFIG_DIRS")
        os.environ["CLOSEOUT_CONFIG_DIRS"] = os.pathsep.join(["/tmp/a", "~/b"])
        try:
            self.assertEqual(fresh.config_dirs(), [Path("/tmp/a"), Path.home() / "b"])
        finally:
            if old is None:
                os.environ.pop("CLOSEOUT_CONFIG_DIRS")
            else:
                os.environ["CLOSEOUT_CONFIG_DIRS"] = old


class MemoryReport(Base):
    """Memory about a project lives in its own folder AND in the home folder's memory (sessions started from ~)."""

    def setUp(self):
        super().setUp()
        self.proj = self.dir / "garden_app"
        (self.proj / "knowledge").mkdir(parents=True)
        (self.proj / "knowledge" / "00-CURRENT.md").write_text("pin", encoding="utf-8")
        self.cfg = self.dir / "cfg"
        self.own = self.mem(escaped(self.proj))
        self.home = self.mem(escaped(Path.home()))
        self.other = self.mem("-Users-x-other-project")
        closeout.config_dirs = lambda: [self.cfg, self.cfg]  # a linked second account resolves to the same folders

    def tearDown(self):
        closeout.config_dirs = lambda: []
        super().tearDown()

    def mem(self, name):
        m = self.cfg / "projects" / name / "memory"
        m.mkdir(parents=True)
        return m

    def test_entries_about_the_project_are_found_in_every_memory_folder(self):
        (self.own / "mine.md").write_text("anything", encoding="utf-8")
        (self.own / "MEMORY.md").write_text("- [Mine](mine.md) — x\n", encoding="utf-8")
        (self.home / "a.md").write_text("garden_app ships on Friday", encoding="utf-8")
        (self.home / "b.md").write_text("the Garden-App widget", encoding="utf-8")
        (self.home / "c.md").write_text("recipe_app only; garden_apps is another word", encoding="utf-8")
        (self.other / "d.md").write_text(f"see {self.proj}/README.md", encoding="utf-8")
        (self.other / "e.md").write_text("nothing here", encoding="utf-8")
        r = closeout.memory_report(self.proj)
        self.assertEqual([(d["kind"], d["about_project"]) for d in r["dirs"]],
                         [("own", ["mine.md"]), ("home", ["a.md", "b.md"]), ("other", ["d.md"])])
        self.assertEqual([(d["kind"], d["about_project"]) for d in closeout.facts(self.proj)["memory_elsewhere"]],
                         [("home", ["a.md", "b.md"]), ("other", ["d.md"])])

    def test_index_problems_own_folder_all_elsewhere_only_this_projects(self):
        (self.own / "mine.md").write_text("x", encoding="utf-8")
        (self.own / "extra.md").write_text("x", encoding="utf-8")
        (self.own / "MEMORY.md").write_text("- [Mine](mine.md) — x\n- [Mine](mine.md) — x\n- [Gone](gone.md) — y\n", encoding="utf-8")
        (self.home / "a.md").write_text("garden_app", encoding="utf-8")
        (self.home / "a2.md").write_text("garden_app", encoding="utf-8")
        (self.home / "n.md").write_text("recipe_app", encoding="utf-8")
        (self.home / "loose.md").write_text("garden_app, missing from the index", encoding="utf-8")
        (self.home / "MEMORY.md").write_text(
            "- [A](a.md) — garden_app START HERE: HANDOFF-1\n- [A](a.md) — garden_app START HERE: HANDOFF-1\n"
            "- [A2](a2.md) — ⇒ garden_app — START HERE: HANDOFF-2\n"
            "- [See](a.md) — ⇒ START HERE: another project's start, only linked to an entry about this one\n"
            "- [N](n.md) — recipe_app START HERE\n- [N](n.md) — recipe_app START HERE\n"
            "- [Old](old-garden.md) — garden_app pointer to a deleted entry\n- [X](x-gone.md) — unrelated\n", encoding="utf-8")
        own, home = closeout.memory_report(self.proj)["dirs"][:2]
        self.assertEqual((own["duplicate_index_lines"], own["not_in_index"], own["index_points_at_missing"]),
                         (["- [Mine](mine.md) — x"], ["extra.md"], ["gone.md"]))
        self.assertEqual(home["duplicate_index_lines"], ["- [A](a.md) — garden_app START HERE: HANDOFF-1"])
        self.assertEqual(home["entry_points"], ["- [A](a.md) — garden_app START HERE: HANDOFF-1",
                                                "- [A2](a2.md) — ⇒ garden_app — START HERE: HANDOFF-2"])
        self.assertEqual(home["not_in_index"], ["loose.md"])
        self.assertEqual(home["index_points_at_missing"], ["old-garden.md"])

    def test_paths_an_entry_names_that_are_gone(self):
        (self.proj / "validation").mkdir()
        # A code span is split with shlex, where a backslash escapes the next character, so an entry names its paths
        # with forward slashes. `rooted` drops the drive as well: a shared folder's entry is only checked for a rooted
        # path, and on Windows the temporary folder sits on a drive letter, which is not one.
        tmp, proj = self.dir.as_posix(), self.proj.as_posix()
        rooted = "/" + self.dir.relative_to(self.dir.anchor).as_posix()
        (self.own / "mine.md").write_text(f"read `{tmp}/gone.md` and `knowledge/00-CURRENT.md`, "
                                          "not `knowledge/GONE.md`; an ellipsis is prose: `validation/…`",
                                          encoding="utf-8")
        (self.home / "a.md").write_text(f"garden_app: see `{proj}` and `{rooted}/nope/x.md`; "
                                        "`knowledge/GONE.md` has no folder to be relative to here", encoding="utf-8")
        dead = closeout.memory_report(self.proj)["dead_paths"]
        self.assertEqual([(d["dir"], d["entry"], d["path"]) for d in dead],
                         [("own", "mine.md", f"{tmp}/gone.md"), ("own", "mine.md", "knowledge/GONE.md"),
                          ("home", "a.md", f"{rooted}/nope/x.md")])


    def test_backup_copies_every_folder_it_lists_and_never_writes_over_one(self):
        (self.own / "mine.md").write_text("mine", encoding="utf-8")
        (self.own / "MEMORY.md").write_text("- [Mine](mine.md) — x\n", encoding="utf-8")
        (self.home / "a.md").write_text("garden_app ships on Friday", encoding="utf-8")
        (self.home / "n.md").write_text("recipe_app only", encoding="utf-8")
        (self.other / "e.md").write_text("nothing here", encoding="utf-8")
        dest = self.dir / "scratch"
        made = closeout.memory_backup(self.proj, dest)
        self.assertEqual([(m["kind"], Path(m["to"]).name, m["files"]) for m in made],
                         [("own", "memory-backup-own", 2), ("home", "memory-backup-home", 2)])
        self.assertEqual((dest / "memory-backup-own" / "mine.md").read_text(encoding="utf-8"), "mine")
        self.assertEqual((dest / "memory-backup-home" / "n.md").read_text(encoding="utf-8"), "recipe_app only")
        self.assertFalse((dest / "memory-backup-other").exists())   # the other folder says nothing about this project
        (self.own / "mine.md").write_text("changed", encoding="utf-8")
        with self.assertRaises(FileExistsError):
            closeout.memory_backup(self.proj, dest)
        self.assertEqual((dest / "memory-backup-own" / "mine.md").read_text(encoding="utf-8"), "mine")

    def test_backup_from_the_command_line(self):
        (self.own / "mine.md").write_text("mine", encoding="utf-8")
        env = {**os.environ, "CLOSEOUT_CONFIG_DIRS": str(self.cfg)}
        cmd = [sys.executable, str(Path(closeout.__file__).resolve()), "memory", str(self.proj),
               "--backup", str(self.dir / "scratch")]
        r = subprocess.run(cmd, capture_output=True, text=True, env=env)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual([b["kind"] for b in json.loads(r.stdout)["backups"]], ["own"])
        again = subprocess.run(cmd, capture_output=True, text=True, env=env)
        self.assertEqual((again.returncode, again.stdout), (1, ""))
        self.assertIn("never writes over another", again.stderr)

class Receipt(unittest.TestCase):
    """closeout.receipt_facts: the ELI5 v5 receipt as fields, groups and errors (R-22 §5.3); one home for the parser the
    evals and R-28's live check both use."""
    GOOD = ("Some prose before.\n\nELI5 v5\n- Did: ran the 41 ingest tests\n- Why: the loader dropped rows (see R-3)\n"
            "- Result: worked\n- Proof: 41 passed in 2.1 s\n- Files: shelf/load.py\n\n"
            "- Redman: filed 1 small → knowledge/redman/LEDGER.md\n- Greenman: G-4 (M): kept csv over sqlite\n\n"
            "- Next: R-4\n- Need from you: none\n\n- Goal: R-3 · loader — rows survive · goal changed: no\n"
            "- In one sentence: The loader keeps every row now; nothing waits on you.\n")

    def errors(self, text):
        blocks = closeout.receipt_facts(text)
        self.assertEqual(len(blocks), 1, text)
        return blocks[0]["errors"]

    def test_receipt_facts(self):
        block = closeout.receipt_facts(self.GOOD)[0]
        self.assertEqual(block["errors"], [])
        self.assertEqual(block["title"], "ELI5 v5")
        self.assertEqual([n for n, _ in block["fields"]][:3], ["Did", "Why", "Result"])
        self.assertEqual(block["groups"], [["Did", "Why", "Result", "Proof", "Files"], ["Redman", "Greenman"],
                                           ["Next", "Need from you"], ["Goal", "In one sentence"]])
        self.assertEqual(closeout.receipt_ids(block), ["G-4"])
        self.assertEqual(closeout.receipt_facts("no receipt here\n"), [])
        bad = {
            "title": self.GOOD.replace("ELI5 v5", "ELI5 v4"),
            "order": self.GOOD.replace("- Next: R-4\n- Need from you: none", "- Need from you: none\n- Next: R-4"),
            "result": self.GOOD.replace("Result: worked", "Result: mostly"),
            "fail when worked": self.GOOD.replace("- Files:", "- Fail: nothing\n- Files:"),
            "no fail when failed": self.GOOD.replace("Result: worked", "Result: failed"),
            "empty proof": self.GOOD.replace("- Proof: 41 passed in 2.1 s", "- Proof:"),
            "reason rule": self.GOOD.replace("the loader dropped rows (see R-3)", "the user asked for it (2026-09-22)"),
            "no goal": self.GOOD.replace("- Goal: R-3 · loader — rows survive · goal changed: no\n", ""),
            "no sentence": self.GOOD.replace("- In one sentence: The loader keeps every row now; nothing waits on you.\n", ""),
            "lone dash": self.GOOD.replace("- Files: shelf/load.py\n\n", "- Files: shelf/load.py\n-\n"),
        }
        for name, text in bad.items():
            self.assertTrue(self.errors(text), name)
        self.assertEqual(self.errors(self.GOOD.replace("- Proof: 41 passed in 2.1 s", "- Proof: none — not verified")), [])
        failed = self.GOOD.replace("Result: worked", "Result: failed").replace("- Files:", "- Fail: the fixture broke; pin it\n- Files:")
        self.assertEqual(self.errors(failed), [])
        self.assertEqual(self.errors(self.GOOD.replace("the loader dropped rows (see R-3)",
                                                       "the loader drops the rows you asked about")), [])  # a reason
        self.assertIn("because the user asked", " ".join(self.errors(self.GOOD.replace(
            "the loader dropped rows (see R-3)", "because the user asked"))))


class ReceiptTexts(unittest.TestCase):
    """What the close-out skills tell a session to report about the audit and the commit gate says what holds (R-24
    §5.7): the pre-existing lines and whose each warning is; the gate's state line and whether it ran on the last
    commit; and another writer's staged files left as they are, yours committed by name."""
    SKILLS = Path(__file__).resolve().parent.parent / "skills"
    AUDIT = "the audit (0 fail; N pre-existing broken, reported; each warn, whose it is and why it stays)"
    GATE = "the commit gate (its `install-gate` state line; ran on the last commit: yes, no or no commit)"

    def text(self, *parts):
        return " ".join((self.SKILLS.joinpath(*parts)).read_text(encoding="utf-8").split())

    def test_the_receipt_and_commit_texts_say_what_holds(self):
        for skill in ("closeout", "project-update"):
            text = self.text(skill, "SKILL.md")
            self.assertIn(self.AUDIT + "; " + self.GATE + ";", text, skill)
            self.assertNotIn("installed, already there, or no", text, skill)
            self.assertNotIn("each warn and why it stays", text, skill)
        self.assertIn("| registered where missing, its state reported; committed |",
                      self.text("project-update", "SKILL.md"))
        updating = self.text("closeout", "UPDATING.md")
        self.assertIn("commit yours by name, `git commit -m \"<message>\" -- <your files>`", updating)
        self.assertNotIn("lists your files and nothing else", updating)


class DecisionIds(Base):
    """closeout.decision_ids: the G-ids a project has a record of (R-28 §5.3; built by R-22, its first user)."""

    def setUp(self):
        super().setUp()
        self.r = Repo(self.dir)
        self.r.write("x", "1")
        self.r.commit("x", "x")

    def test_decision_ids_reads_records_and_ledger_rows(self):
        self.r.write("knowledge/decisions/G-1-store.md", "# G-1\nroadmap: R-2\nstatus: decided\n")
        self.r.write("knowledge/decisions/G-12-cli.md", "# G-12\n")
        self.r.write("knowledge/decisions/LEDGER.md",
                     "# Greenman ledger\n\n| date | id | the choice | depth | status |\n|---|---|---|---|---|\n"
                     "| 2026-09-14 | G-2 | a small call | S | decided |\n"
                     "prose naming G-7 is not a row\n| 2026-09-15 | see G-8 | not an id cell | S | decided |\n")
        self.assertEqual(closeout.decision_ids(self.dir), {"G-1", "G-12", "G-2"})
        # a decisions folder the doc map declares
        self.r.write("CLAUDE.md", "# p\n\n## Doc map\nroadmap: knowledge/00-ROADMAP.md\ndecisions: docs/adr\n")
        self.r.write("docs/adr/G-3-auth.md", "# G-3\n")
        self.r.write("docs/adr/LEDGER.md", "| 2026-09-16 | G-4 | x | S | decided |\n")
        self.assertEqual(closeout.decision_ids(self.dir), {"G-1", "G-12", "G-2", "G-3", "G-4"})

    def test_decision_ids_runs_no_link_audit_and_no_git_status(self):
        self.r.write("knowledge/decisions/G-1-store.md", "# G-1\n")
        calls = []
        real_git = closeout.run_git

        def recording(folder, *args):
            calls.append(args)
            return real_git(folder, *args)

        def refuse(*a, **k):
            raise AssertionError("decision_ids ran a link-audit")
        with mock.patch.object(closeout, "run_git", recording), mock.patch.object(closeout, "link_audit", refuse):
            self.assertEqual(closeout.decision_ids(self.dir), {"G-1"})
        self.assertFalse([a for a in calls if "status" in a], calls)
        self.assertEqual(closeout.decision_ids(self.dir / "nowhere"), set())


class Worklist(Base):
    def items(self, text):
        return [l for l in text.splitlines() if l.startswith("- [ ]")]

    def test_every_line_with_a_figure_becomes_an_item_plus_one_per_doc(self):
        (self.dir / "README.md").write_text(
            "# Title\n235 tests pass\nStep 1 is fine\n3 commits ahead\nlast seen 2026-09-07\n"
            "fixed in `7f24ee4`\nplain prose\n90% done\nversion v2\nbuilt with Xcode 26.1\n", encoding="utf-8")
        items = self.items(closeout.worklist(self.dir, ["README.md"]))
        self.assertIn("whole doc", items[0])
        self.assertEqual([int(re.search(r"README\.md:(\d+)", i).group(1)) for i in items[1:]], [2, 4, 5, 6, 8, 10])

    def test_html_skips_style_and_script_and_tags(self):
        (self.dir / "o.html").write_text('<meta charset="utf-8">\n<style>\n.a{margin:12px}\n</style>\n'
                                         "<p>42 feeds</p>\n<p>no figure</p>\n<script>\nvar x=10;\n</script>\n", encoding="utf-8")
        items = self.items(closeout.worklist(self.dir, ["o.html"]))
        self.assertEqual(items[1:], ["- [ ] o.html:5 — 42 feeds"])

    def test_a_line_inside_a_code_block_is_tagged(self):
        (self.dir / "d.md").write_text("42 feeds\n```sql\nINSERT INTO t VALUES (12, 'x');\n```\n88 rows\n", encoding="utf-8")
        self.assertEqual(self.items(closeout.worklist(self.dir, ["d.md"]))[1:],
                         ["- [ ] d.md:1 — 42 feeds", "- [ ] d.md:3 [code] — INSERT INTO t VALUES (12, 'x');",
                          "- [ ] d.md:5 — 88 rows"])

    def test_html_pre_is_tagged(self):
        (self.dir / "o.html").write_text('<meta charset="utf-8">\n<pre>\ngarden_app/  (12 files)\n</pre>\n'
                                         "<p>7 gates</p>\n", encoding="utf-8")
        self.assertEqual(self.items(closeout.worklist(self.dir, ["o.html"]))[1:],
                         ["- [ ] o.html:3 [code] — garden_app/ (12 files)", "- [ ] o.html:5 — 7 gates"])

    def test_cli(self):
        (self.dir / "README.md").write_text("12 tests\n", encoding="utf-8")
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.assertEqual(closeout.main(["worklist", str(self.dir), "--docs", "README.md"]), 0)
            self.assertEqual(closeout.main(["memory", str(self.dir)]), 0)
        self.assertIn("- [ ] README.md:1 — 12 tests", out.getvalue())



# ---------------------------------------------------------------------------------------------------- figures (R-29 § 5.1)

COUNTS = {"scenarios": 3, "graded sessions": 31, "checks": 106, "turns": 46, "check-runs": 193, "quick sessions": 12,
          "check kinds": 23}
# a project's own `evals/run.py`, standing in for R-22's `--counts`: each call leaves one line in calls.log
COUNTS_STUB = """import json, os, sys
here = os.path.dirname(os.path.abspath(__file__))
open(os.path.join(here, "calls.log"), "a").write("x\\n")
spec = json.load(open(os.path.join(here, "scenarios.json")))
counts = dict(%s, scenarios=len(spec["scenarios"]))
if "--counts" in sys.argv:
    print(json.dumps(counts))
"""
SUITE = ["eval.scenarios", "eval.sessions", "eval.turns", "eval.checks", "eval.check_runs", "eval.check_kinds",
         "eval.quick"]
FIELDS = {"eval.scenarios": "scenarios", "eval.sessions": "graded sessions", "eval.turns": "turns",
          "eval.checks": "checks", "eval.check_runs": "check-runs", "eval.check_kinds": "check kinds",
          "eval.quick": "quick sessions"}


def figures_decl(docs, extra=None, mentions=None, tables=None):
    figs = {n: {"kind": "json_command", "argv": ["evals/run.py", "--counts"], "field": FIELDS[n]} for n in SUITE}
    figs.update(extra or {})
    out = {"_about": "test", "docs": docs, "figures": figs}
    if mentions is not None:
        out["mentions"] = mentions
    if tables is not None:
        out["tables"] = tables
    return json.dumps(out, indent=1)


def run_main(*args):
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        code = closeout.main([str(a) for a in args])
    return code, out.getvalue()


class Figures(Base):
    """`closeout.py figures`: every span a declared doc carries holds its source's value (R-29 tests 1-6, 25, 28)."""

    def setUp(self):
        super().setUp()
        self.r = Repo(self.dir)
        self.r.write("CLAUDE.md", "# p\n\n## Doc map\nroadmap: knowledge/00-ROADMAP.md\nfigures: tools/figures.json\n")
        self.r.write("knowledge/00-ROADMAP.md", "# r\n\n## Now\n- R-1 a\n")
        self.stub(self.dir, ["a", "b", "c"])
        self.r.write("tools/figures.json", figures_decl(["README.md", "public/docs/evidence.md"]))

    def stub(self, where, ids, counts=None):
        (where / "evals").mkdir(parents=True, exist_ok=True)
        (where / "evals" / "run.py").write_text(COUNTS_STUB % json.dumps(counts or COUNTS), encoding="utf-8")
        (where / "evals" / "scenarios.json").write_text(json.dumps({"scenarios": [{"id": i} for i in ids]}),
                                                        encoding="utf-8")

    def calls(self, where=None):
        log = (where or self.dir) / "evals" / "calls.log"
        return len(log.read_text().splitlines()) if log.is_file() else 0

    def test_span_matches(self):
        self.r.write("README.md", "# p\n\n<!-- fig:eval.sessions -->31<!-- /fig --> graded sessions\n")
        code, out = run_main("figures", self.dir, "--check")
        self.assertEqual(code, 0, out)
        self.assertEqual(out.strip().splitlines()[-1], "figures: 1 span(s), 0 stale, 0 unknown, 0 missing")

    def test_stale_span_fails(self):
        """Known-bad: a span edited to a wrong value fails, naming file:line, the figure, the stated and the computed
        value; `audit` FAILs on it when the line is this pass's and WARNs when it is not."""
        self.r.write("README.md", "# p\n\n<!-- fig:eval.sessions -->29<!-- /fig --> graded sessions\n")
        code, out = run_main("figures", self.dir, "--check")
        self.assertEqual(code, 1)
        self.assertIn("STALE README.md:3 eval.sessions: says 29, source says 31", out)
        self.assertEqual(out.strip().splitlines()[-1], "figures: 1 span(s), 1 stale, 0 unknown, 0 missing")
        fails = [f for f in closeout.audit(self.dir, ["README.md"]) if f[0] == "FAIL"]
        self.assertEqual([(f[1], "eval.sessions" in f[2]) for f in fails], [("README.md:3", True)])
        self.r.commit("base", ".")
        self.r.write("other.md", "x\n")
        found = closeout.audit(self.dir, ["other.md"])
        self.assertEqual([f for f in found if f[0] == "FAIL"], [])
        self.assertTrue(any(f[0] == "WARN" and f[1] == "README.md:3" and "says 29" in f[2] for f in found), found)

    def test_write_rewrites_spans_only(self):
        body = ("# p\n\n<!-- fig:eval.sessions -->29<!-- /fig --> graded sessions, "
                "<!-- fig:eval.checks -->1<!-- /fig --> checks.\r\ntrailing  spaces  \n\nend")
        self.r.write("README.md", body)
        outside = lambda t: re.sub(r"<!-- fig:[\w.]+ -->.*?<!-- /fig -->", "", t, flags=re.S)
        code, out = run_main("figures", self.dir, "--write")
        self.assertEqual(code, 0, out)
        after = (self.dir / "README.md").read_bytes().decode("utf-8")
        self.assertIn("<!-- fig:eval.sessions -->31<!-- /fig -->", after)
        self.assertIn("<!-- fig:eval.checks -->106<!-- /fig -->", after)
        self.assertEqual(outside(after), outside(body))            # no other byte moved, CR and spaces included
        self.assertEqual(run_main("figures", self.dir, "--check")[0], 0)

    def test_unknown_name_fails(self):
        self.r.write("README.md", "<!-- fig:eval.nope -->3<!-- /fig -->\n")
        code, out = run_main("figures", self.dir, "--check")
        self.assertEqual(code, 1)
        self.assertIn("UNKNOWN README.md:1 eval.nope", out)
        self.assertEqual(out.strip().splitlines()[-1], "figures: 1 span(s), 0 stale, 1 unknown, 0 missing")

    def test_missing_mention_fails(self):
        self.r.write("tools/figures.json", figures_decl(
            ["public/docs/evidence.md"],
            mentions=[{"doc": "public/docs/evidence.md", "each": {"file": "evals/scenarios.json", "list": "scenarios",
                                                                 "field": "id"}, "form": "`{}`"}]))
        self.r.write("public/docs/evidence.md", "| `a` | x |\n| `b` | y |\nand c in prose, not in its form\n")
        code, out = run_main("figures", self.dir, "--check")
        self.assertEqual(code, 1)
        self.assertEqual([l for l in out.splitlines() if l.startswith("MISSING")],
                         ["MISSING public/docs/evidence.md: `c`"])

    def test_no_key_no_change(self):
        """Without `figures:`, a declared handoff, a `G-` record or an inventory, `audit` and `facts` say what they
        said before R-29 (the golden was captured on the code before this row), and `figures` prints nothing."""
        d = self.dir / "plain"
        d.mkdir()
        r = Repo(d)
        for rel, text in {"CLAUDE.md": "# p\n\n## Doc map\nroadmap: knowledge/00-ROADMAP.md\n",
                          "knowledge/00-ROADMAP.md": "# r\n\n## Now\n- R-1 a\n\n## Done\n- R-2 b, 2026-09-01\n",
                          "knowledge/redman/LEDGER.md": "# L\nroadmap: none — ledger\n\n| date | kind | item | size | state "
                                                        "| where |\n|---|---|---|---|---|---|\n| 2026-09-01 | k | an open "
                                                        "finding | small | filed | x |\n",
                          "knowledge/02-spec-a.md": "# a\n\nno roadmap line\n",
                          "README.md": "# p\n31 graded sessions\n"}.items():
            r.write(rel, text)
        r.commit("base", ".")
        r.write("README.md", "# p\n31 graded sessions\n29 checks\n")
        golden_audit = [["WARN", "linking", "1 document(s) of a linked kind carry no `roadmap:` line — `closeout.py "
                                            "link-audit` writes the inventory (LINKING.md)"]]
        self.assertEqual([list(f) for f in closeout.audit(d, ["README.md"], today="2026-09-28")], golden_audit)
        f = closeout.facts(d)
        golden_facts = {"baseline": {"missing": ["pin", "handoff", "overview", "brain"],
                                     "present": {"claude_md": ["CLAUDE.md"], "readme": ["README.md"],
                                                 "roadmap": ["knowledge/00-ROADMAP.md"]}},
                        "greenman": {"awaiting_ruling": [], "decisions": 0, "real_fix_owed": [],
                                     "undeclared_record_folders": [], "unreadable_ledgers": []},
                        "redman": {"by_lane": {"main": 1}, "deep_runs": [], "ledgers": ["knowledge/redman/LEDGER.md"],
                                   "open_rows": 1, "rows_after_a_break": [], "unreadable_ledgers": [],
                                   "unreadable_states": []}, "untracked_records": []}
        self.assertEqual({k: f[k] for k in golden_facts}, golden_facts)

    def test_no_key_figures_prints_nothing(self):
        (self.dir / "CLAUDE.md").write_text("# p\n\n## Doc map\nroadmap: knowledge/00-ROADMAP.md\n", encoding="utf-8")
        self.assertEqual(run_main("figures", self.dir, "--check"), (0, ""))
        self.assertEqual(run_main("figures", self.dir, "--write"), (0, ""))

    def test_json_command_kind(self):
        # one --counts call serves every suite figure of a run
        self.r.write("README.md", "".join(f"<!-- fig:{n} -->{COUNTS[FIELDS[n]] if n != 'eval.scenarios' else 3}"
                                          f"<!-- /fig -->\n" for n in SUITE))
        code, out = run_main("figures", self.dir, "--check")
        self.assertEqual((code, self.calls()), (0, 1), out)
        # a missing field, a non-zero exit and unparsable output are each UNKNOWN naming the command
        self.stub(self.dir, ["a"], counts={k: v for k, v in COUNTS.items() if k != "checks"})
        code, out = run_main("figures", self.dir, "--check")
        self.assertIn("UNKNOWN README.md:4 eval.checks: `evals/run.py --counts` gave no field 'checks'", out)
        for body, why in (("import sys\nsys.exit(3)\n", "exited 3"), ("print('not json')\n", "printed no JSON")):
            (self.dir / "evals" / "run.py").write_text(body, encoding="utf-8")
            code, out = run_main("figures", self.dir, "--check")
            self.assertEqual(code, 1)
            self.assertIn(f"UNKNOWN README.md:1 eval.scenarios: `evals/run.py --counts` {why}", out)
        # json_count: the rows matching `where`; a named figure whose file is absent is UNKNOWN, an unnamed one costs
        # nothing
        self.stub(self.dir, ["a", "b", "c"])
        extra = {"rows.a": {"kind": "json_count", "file": "data/rows.json", "list": "rows", "where": {"chart": "a"}},
                 "rows.gone": {"kind": "json_count", "file": "data/gone.json", "list": "rows", "where": {}},
                 "rows.unnamed": {"kind": "json_count", "file": "data/none.json", "list": "rows", "where": {}}}
        self.r.write("tools/figures.json", figures_decl(["README.md"], extra=extra))
        self.r.write("data/rows.json", json.dumps({"rows": [{"chart": "a"}, {"chart": "b"}, {"chart": "a"}]}))
        self.r.write("README.md", "<!-- fig:rows.a -->2<!-- /fig -->\n<!-- fig:rows.gone -->0<!-- /fig -->\n")
        code, out = run_main("figures", self.dir, "--check")
        self.assertEqual(code, 1)
        self.assertIn("UNKNOWN README.md:2 rows.gone: data/gone.json is not there", out)
        self.assertNotIn("rows.a", out)
        self.assertNotIn("rows.unnamed", out)
        # the doctrine repo is the folder the project's tools/export.py names, never a ~ path in the declaration
        doctrine = self.dir / "doctrine-repo"
        (doctrine / "harness").mkdir(parents=True)
        (doctrine / "harness" / "arms.json").write_text(json.dumps({"rows": [{"chart": "x"}] * 4}), encoding="utf-8")
        exp = ("from pathlib import Path\nPACK = Path(__file__).resolve().parent.parent\nDOCTRINE = Path(%r)\n"
               "SHARED = [(PACK, 'a/SKILL.md', 'skills/a/SKILL.md'), (PACK, 'a/X.md', 'skills/a/X.md'),\n"
               "          (PACK, 'b/SKILL.md', 'skills/b/SKILL.md'), (DOCTRINE, 'l.md', 'doctrine/l.md')]\n"
               "KIT_ONLY = [%s]\n")
        self.r.write("tools/export.py", exp % (str(doctrine), ""))
        extra = {"doctrine.rows": {"kind": "json_count", "repo": "doctrine", "file": "harness/arms.json",
                                   "list": "rows", "where": {"chart": "x"}},
                 "kit.skills": {"kind": "shipped_skills", "target": "kit"}}
        self.r.write("tools/figures.json", figures_decl(["README.md"], extra=extra))
        self.r.write("README.md", "<!-- fig:doctrine.rows -->4<!-- /fig -->, <!-- fig:kit.skills -->2<!-- /fig -->\n")
        self.assertEqual(run_main("figures", self.dir, "--check")[0], 0)
        self.r.write("tools/export.py", exp % (str(doctrine), "(PACK, 'c/SKILL.md', 'skills/c/SKILL.md')"))
        code, out = run_main("figures", self.dir, "--check")
        self.assertIn("STALE README.md:1 kit.skills: says 2, source says 3", out)

    def test_public_tree_counts_its_own_scenarios(self):
        """Known-bad included (G-27 Addenda): a public page is computed in the built public tree, which may leave a
        scenario out; this repo's own documents keep this repo's values."""
        self.r.write("tools/figures.json", figures_decl(
            ["README.md", "public/docs/evidence.md"],
            mentions=[{"doc": "public/docs/evidence.md", "each": {"file": "evals/scenarios.json", "list": "scenarios",
                                                                 "field": "id"}, "form": "`{}`"}]))
        self.r.write("public/docs/evidence.md", "<!-- fig:eval.scenarios -->2<!-- /fig --> scenarios: `a`, `b`\n")
        self.r.write("README.md", "<!-- fig:eval.scenarios -->3<!-- /fig --> scenarios\n")
        tree = self.dir / "built"
        self.stub(tree, ["a", "b"])
        code, out = run_main("figures", self.dir, "--check", "--public-tree", tree)
        self.assertEqual(code, 0, out)
        code, out = run_main("figures", self.dir, "--check")
        self.assertEqual(code, 1)
        self.assertIn("STALE public/docs/evidence.md:1 eval.scenarios: says 2, source says 3", out)
        self.assertIn("MISSING public/docs/evidence.md: `c`", out)
        self.assertNotIn("README.md:1", out.replace("public/docs/evidence.md", ""))

    def runs_file(self, records):
        self.r.write("evals/RUNS.jsonl", "".join(json.dumps(x) + "\n" for x in records))

    def test_estimated_cost_needs_the_word(self):
        """Known-bad included (G-29 F3-X9): an estimated case cost reaches a span only with "(estimated)"; a null
        cost with no estimate is UNKNOWN, never an earlier run's figure."""
        entry = {"case": "large_stop-fable", "basis": 1.05, "floor": 0.64, "estimate": 1.05, "unpriced": [],
                 "reason": "turn 2 reported no cost"}
        self.assertEqual(len(entry), 6)
        earlier = {"run": "r1", "date": "2026-09-20", "selection": "full", "cost": 29.1, "result": "29/29"}
        last = {"run": "r2", "date": "2026-09-28", "selection": "full", "cost": None, "result": "49/49",
                "cost_estimated": {"cost": 31.5, "cases": [entry]}}
        self.runs_file([earlier, last])
        extra = {"run.full.cost": {"kind": "jsonl_last", "file": "evals/RUNS.jsonl", "where": {"selection": "full"},
                                   "field": "cost", "format": "${:.2f}"}}
        self.r.write("tools/figures.json", figures_decl(["README.md"], extra=extra))
        self.r.write("README.md", "about <!-- fig:run.full.cost -->$31.50 (estimated)<!-- /fig -->\n")
        self.assertEqual(run_main("figures", self.dir, "--check")[0], 0)
        self.r.write("README.md", "about <!-- fig:run.full.cost -->$30.00<!-- /fig -->\n")
        self.assertEqual(run_main("figures", self.dir, "--write")[0], 0)
        self.assertEqual((self.dir / "README.md").read_text(), "about <!-- fig:run.full.cost -->$31.50 (estimated)"
                                                                "<!-- /fig -->\n")
        self.r.write("README.md", "about <!-- fig:run.full.cost -->$31.50<!-- /fig -->\n")
        code, out = run_main("figures", self.dir, "--check")
        self.assertEqual(code, 1)
        self.assertIn("STALE README.md:1 run.full.cost: says $31.50, source says $31.50 (estimated)", out)
        self.runs_file([earlier, dict(last, cost_estimated=None)])
        code, out = run_main("figures", self.dir, "--check")
        self.assertIn("UNKNOWN README.md:1 run.full.cost: r2 has no cost", out)
        self.assertNotIn("29.10", out)


class RunsTable(unittest.TestCase):
    """The run table names every failure, scenario and seat together: a note covers a failure only by naming both, so
    no failure hides behind a note that names another seat's, or a longer scenario that starts with its name."""
    SPEC = {"where": {"selection": ["full"]}}

    def cell(self, failed, note, seats=("fable", "opus")):
        rec = {"date": "2026-09-14", "selection": "full", "cases": 9, "passed": 9 - len(failed), "seats": list(seats),
               "failed": [list(f) for f in failed], "note": note}
        return closeout.runs_table([rec], self.SPEC).splitlines()[-1].rsplit(" | ", 1)[1][:-2]

    def test_a_note_naming_scenario_and_seat_covers_that_failure(self):
        self.assertEqual(self.cell([("large_stop", "fable")], "layer — large_stop-fable: header"),
                         "layer — large_stop-fable: header")
        self.assertEqual(self.cell([("large_stop", "opus")], "`large_stop` on Opus again"), "`large_stop` on Opus again")
        self.assertEqual(self.cell([("x", "fable"), ("x", "opus")], "`x` on Fable and Opus — fixed"),
                         "`x` on Fable and Opus — fixed")
        self.assertEqual(self.cell([("x", "opus55")], "x on Opus 5.5 asked", ("fable", "opus55")), "x on Opus 5.5 asked")
        self.assertEqual(self.cell([("x", "opus55")], "`x` — it stopped", ("opus55",)), "`x` — it stopped")

    def test_known_bad_the_other_seat_is_not_covered(self):
        # 2026-09-14's re-graded note names large_stop-fable; large_stop failed on Opus too
        self.assertEqual(self.cell([("large_stop", "fable"), ("large_stop", "opus")], "layer — large_stop-fable: x"),
                         "`large_stop` on Opus; layer — large_stop-fable: x")
        self.assertEqual(self.cell([("x", "opus55")], "x on Opus asked", ("fable", "opus55")),
                         "`x` on Opus 5.5; x on Opus asked")
        self.assertEqual(self.cell([("x", "fable")], "the cause of x", ("fable", "opus")), "`x` on Fable; the cause of x")

    def test_known_bad_a_longer_scenario_is_not_its_prefix(self):
        for short, longer in (("full_flow", "full_flow_two_lane"), ("shell_doc", "shell_doc_eli5"),
                              ("shell_doc", "shell_doc_commit")):
            with self.subTest(short=short, longer=longer):
                for note in (f"{longer} on Fable failed", f"{longer}-fable: header", f"`{longer}` stopped"):
                    self.assertEqual(self.cell([(short, "fable")], note, ("fable",)), f"`{short}` on Fable; {note}")
        self.assertEqual(self.cell([("two_lane", "fable")], "full_flow_two_lane on Fable", ("fable",)),
                         "`two_lane` on Fable; full_flow_two_lane on Fable")


class StateChecks(Base):
    """`audit`'s state checks (R-29 § 5.4; tests 7-11): the handoff's Open items where a handoff is declared, a record
    owed on a Done item, the inventory, the decisions ledger in git, and BACKLOG.md as a plan."""
    LEDGER = ("# L\nroadmap: none — ledger\n\n| date | kind | item | size | state | where |\n"
              "|---|---|---|---|---|---|\n| 2026-09-20 | defect | the importer drops the header row on Windows | small "
              "| filed | x |\n| 2026-09-19 | defect | an old one | small | closed — fixed | x |\n")

    def setUp(self):
        super().setUp()
        self.r = Repo(self.dir)
        self.r.write("CLAUDE.md", "# p\n\n## Doc map\nroadmap: knowledge/00-ROADMAP.md\n")
        self.r.write("knowledge/00-ROADMAP.md", "# r\n\n## Now\n- R-3 the importer\n\n## Done\n- R-1 done, 2026-09-01\n")
        self.r.write("knowledge/redman/LEDGER.md", self.LEDGER)
        self.r.commit("base", ".")

    def handoff_findings(self, found):
        return [f for f in found if "handoff" in f[2]]

    def test_handoff_omits_open_row(self):
        """Known-bad: a root HANDOFF.md whose Open items leave out an open ledger row fails, naming the row."""
        self.r.write("HANDOFF.md", "# h\n\n## Open items\n- R-3 the importer\n\n## Start prompt\nx\n")
        found = self.handoff_findings(closeout.audit(self.dir, ["HANDOFF.md"]))
        self.assertEqual(len(found), 1, found)
        self.assertEqual(found[0][:2], ("FAIL", "HANDOFF.md"))
        self.assertIn("handoff Open items omit: knowledge/redman/LEDGER.md 2026-09-20 "
                      "(the importer drops the header row on Windows)", found[0][2])
        self.r.write("HANDOFF.md", "# h\n\n## Open items\n- R-3 the importer\n- the importer drops the header row on "
                                   "Windows (ledger)\n")
        self.assertEqual(self.handoff_findings(closeout.audit(self.dir, ["HANDOFF.md"])), [])
        self.r.write("HANDOFF.md", "# h\n\n## Open items\n- the importer drops the header row on Windows\n")
        found = self.handoff_findings(closeout.audit(self.dir, ["HANDOFF.md"]))
        self.assertIn("handoff Open items omit: R-3", found[0][2])            # the Now item, by its id

    def test_a_decision_kept_only_as_a_ledger_row_is_an_open_item(self):
        """What `facts` lists as awaiting or owing a real fix includes a decision ledger's row with no `G-` file (an S
        call, or a project that keeps its decisions only as rows); the handoff must name it too."""
        self.r.write("knowledge/decisions/LEDGER.md", "# ledger\nroadmap: none — ledger\n\n| date | id | the choice | "
                                                      "status |\n|---|---|---|---|\n| 2026-09-20 | G-8 | where the store "
                                                      "lives | awaiting a ruling |\n")
        self.r.commit("ledger", "knowledge/decisions/LEDGER.md")
        self.assertEqual(len(closeout.facts(self.dir)["greenman"]["awaiting_ruling"]), 1)
        self.r.write("HANDOFF.md", "# h\n\n## Open items\n- R-3 the importer\n- the importer drops the header row on "
                                   "Windows\n")
        found = self.handoff_findings(closeout.audit(self.dir, ["HANDOFF.md"]))
        self.assertEqual(len(found), 1, found)
        self.assertIn("omit: knowledge/decisions/LEDGER.md: where the store lives", found[0][2])
        self.r.write("HANDOFF.md", "# h\n\n## Open items\n- R-3 the importer\n- the importer drops the header row on "
                                   "Windows\n- G-8, where the store lives, awaits a ruling\n")
        self.assertEqual(self.handoff_findings(closeout.audit(self.dir, ["HANDOFF.md"])), [])

    def test_handoff_check_needs_a_declared_handoff(self):
        self.r.write("knowledge/2026-09-20-session-handoff.md", "# h\nroadmap: R-3\n\n## Open items\n- nothing\n")
        self.assertEqual(self.handoff_findings(closeout.audit(self.dir, ["knowledge/2026-09-20-session-handoff.md"])),
                         [])
        self.r.write("CLAUDE.md", "# p\n\n## Doc map\nroadmap: knowledge/00-ROADMAP.md\n"
                                  "handoff: knowledge/2026-09-20-session-handoff.md\n")
        found = self.handoff_findings(closeout.audit(self.dir, ["knowledge/2026-09-20-session-handoff.md"]))
        self.assertEqual([f[0] for f in found], ["FAIL"])
        self.assertIn("omit: knowledge/redman/LEDGER.md 2026-09-20", found[0][2])
        self.r.write("knowledge/2026-09-20-session-handoff.md", "# h\nroadmap: R-3\n\nno sections\n")
        found = self.handoff_findings(closeout.audit(self.dir, ["knowledge/2026-09-20-session-handoff.md"]))
        self.assertEqual([(f[0], f[2]) for f in found], [("WARN", "handoff has no Open items section")])
        self.r.write("CLAUDE.md", "# p\n\n## Doc map\nroadmap: knowledge/00-ROADMAP.md\nhandoff: knowledge/gone.md\n")
        problems = [p["message"] for p in closeout.load_doc_map(self.dir)["problems"]]
        self.assertTrue(any("handoff: knowledge/gone.md" in m for m in problems), problems)

    def test_record_owed_on_done_warns(self):
        self.r.write("knowledge/decisions/G-4-plan.md", "# G-4\nroadmap: R-1\nstatus: decided; the suite on the new "
                                                          "seat stays owed after the harness pass\n")
        self.r.write("knowledge/decisions/G-5-now.md", "# G-5\nroadmap: R-3\nstatus: awaiting a ruling\n")
        found = [f for f in closeout.audit(self.dir, ["knowledge/decisions/G-4-plan.md"]) if "owed" in f[2] or
                 "Done" in f[2]]
        self.assertEqual([(f[0], f[1]) for f in found], [("WARN", "knowledge/decisions/G-4-plan.md")])
        self.assertIn("R-1 is Done", found[0][2])

    def test_inventory_stale_and_stray(self):
        head = ("# UNLINKED — {n} document(s) with no `roadmap:` line — 2026-09-01\n"
                "roadmap: none — this is the linking backlog itself, written by `closeout.py link-audit`; delete it "
                "when empty\n")
        self.r.write("knowledge/UNLINKED-2026-09-01.md", head.format(n=3))
        self.r.write("knowledge/02-spec-a.md", "# a\n")
        self.r.write("knowledge/02-spec-b.md", "# b\n")
        stray = self.r.write("knowledge/audit/findings/UNLINKED-2026-09-01.md", head.format(n=2))
        found = closeout.audit(self.dir, ["knowledge/02-spec-a.md"])
        stale = [f for f in found if f[1] == "knowledge/UNLINKED-2026-09-01.md"]
        self.assertEqual([(f[0], "lists 3" in f[2] and "2 now" in f[2]) for f in stale], [("WARN", True)])
        strays = [f for f in found if f[1] == "knowledge/audit/findings/UNLINKED-2026-09-01.md"]
        self.assertEqual([(f[0], "stray inventory" in f[2]) for f in strays], [("WARN", True)])
        self.assertTrue(stray.is_file())                                      # reported, never deleted

    def test_untracked_decisions_ledger_warns_in_audit(self):
        self.r.write("knowledge/decisions/LEDGER.md", "# ledger\nroadmap: none — ledger\n\n| date | id | status |\n"
                                                      "|---|---|---|\n| 2026-09-20 | S | decided |\n")
        self.assertIn("knowledge/decisions/LEDGER.md", closeout.facts(self.dir)["untracked_records"])
        found = [f for f in closeout.audit(self.dir, ["knowledge/02-x.md"]) if f[1] == "records"]
        self.assertEqual(len(found), 1, found)
        self.assertIn("knowledge/decisions/LEDGER.md", found[0][2])

    def test_backlog_is_a_roadmap(self):
        for rel in ("BACKLOG.md", "knowledge/00-SPRINT-LEDGER.md", "docs/DECISION-LOG.md"):
            self.r.write(rel, "x\n")
        present = closeout.facts(self.dir)["baseline"]["present"]["roadmap"]
        self.assertIn("BACKLOG.md", present)
        self.assertNotIn("knowledge/00-SPRINT-LEDGER.md", present)
        self.assertNotIn("docs/DECISION-LOG.md", present)
        self.assertTrue(closeout.RECORD_RE.search("00_decision_log.md"))     # `_` is not a letter, as it is to \b
        self.assertIsNone(closeout.RECORD_RE.search("backlog"))


if __name__ == "__main__":
    unittest.main()
