"""Tests for closeout/closeout.py. Run: python3 -m unittest discover -s tests (Python 3.9 or newer)."""
import contextlib
import datetime
import io
import os
import re
import subprocess
import sys
import tempfile
import unittest
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


class Audit(Base):
    def setUp(self):
        super().setUp()
        self.r = Repo(self.dir)
        self.r.write("src/app.py", "")
        self.r.commit("init", "src/app.py")

    def levels(self, found, level):
        return [f for f in found if f[0] == level]

    def test_someone_elses_staged_file_fails(self):
        self.r.write("other.md", "theirs")
        self.r.write("HANDOFF.md", "mine")
        sh(self.dir, "git", "add", "other.md", "HANDOFF.md")
        fails = self.levels(closeout.audit(self.dir, ["HANDOFF.md"]), "FAIL")
        self.assertEqual([f[1] for f in fails], ["other.md"])

    def test_paths_that_exist_pass_and_missing_ones_warn(self):
        self.r.write("HANDOFF.md", "Run `python3 src/app.py --x` then read `app.py` and `src/gone.py:12`; "
                                   "see [ok](src/app.py) and [bad](nope.md); `git log a..HEAD`; `https://x.y/z`; "
                                   "`stats/*.json`; `origin/<name>`.\n")
        warns = self.levels(closeout.audit(self.dir, ["HANDOFF.md"]), "WARN")
        self.assertEqual(sorted(w[2] for w in warns), ["link target 'nope.md' not found", "path `src/gone.py` not found"])

    def test_a_citation_with_an_anchor_checks_the_path_not_the_anchor(self):
        # LINKING.md cites documents as path plus anchor; the anchor is link-audit's to check, the path is audit's
        self.r.write("HANDOFF.md", "see `src/app.py#§4.2 Layout` and `src/gone.py#Top`\n")
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
                                   f"`71259 {self.dir}/src/My Tool.app/x`.\n")
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
        today = datetime.date.today().isoformat()
        self.r.write("knowledge/00-CURRENT.md", f"# pin\nupdated: {today}\n\n## Next action\nship\n")
        self.assertEqual(closeout.audit(self.dir, ["knowledge/00-CURRENT.md"], "knowledge/00-CURRENT.md"), [])

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
        found, counts = closeout.link_audit(self.dir)
        inv = self.dir / "knowledge" / f"UNLINKED-{datetime.date.today().isoformat()}.md"  # the project's own tree
        self.assertTrue(inv.is_file())
        text = inv.read_text(encoding="utf-8")
        self.assertIn("| `knowledge/02-SPEC-e.md` | The e spec |", text)
        self.assertIn("roadmap: none —", text)
        levels = closeout.audit(self.dir, ["knowledge/02-SPEC-e.md"])
        warns = [m for l, w, m in levels if l == "WARN" and w == "linking"]
        fails = [m for l, w, m in levels if l == "FAIL" and "linking" in m]
        self.assertEqual(len(warns), 1)
        self.assertEqual(len(fails), 1)
        self.assertIn("NO FILE", fails[0])
        self.r.write("knowledge/02-SPEC-e.md", "# The e spec\nroadmap: 1\n")
        self.r.write("knowledge/00-PROJECT-ROADMAP.md", "| 1 | a [spec `knowledge/02-SPEC-e.md`] |\n")
        closeout.link_audit(self.dir)
        self.assertFalse(inv.exists())  # empty backlog → no file
        closeout.link_audit(self.dir)
        self.assertFalse(inv.exists())  # and a clean project never grows one

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
        self.r.write("knowledge/redman/LEDGER.md", "| d | k | i | small | filed | w |\n| d | k | i | small | closed — x | w |\n")
        self.r.write("knowledge/audit/redman/LEDGER.md", "| d | k | i | big | asked · later | w |\n")
        self.r.write("xbt-skills-pack/redman/LEDGER.md", "| d | k | i | small | filed | w |\n")  # an older layout, still read
        self.r.write("xbt-skills-pack/LEDGER.md", "| d | k | i | small | filed | w |\n")  # the pack's side ledger: not the project's
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
        self.assertEqual(f["greenman"], {"decisions": 3, "awaiting_ruling": ["knowledge/audit/decisions/G-2-b.md"],
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
        self.assertIn("Never --no-verify", out.getvalue())
        self.r.write("CLAUDE.md", "# x\n\n## Doc map\nroadmap: knowledge/00-ROADMAP.md\nguardrails: off\n")
        self.assertEqual(closeout.link_check_staged(self.dir), ([], "off"))

    def test_install_gate_adds_one_marked_block_keeps_a_foreign_hook_and_gates_real_commits(self):
        hooks = self.dir / ".git" / "hooks"
        hooks.mkdir(parents=True, exist_ok=True)
        (hooks / "pre-commit").write_text("#!/bin/sh\necho theirs\n", encoding="utf-8")
        closeout.install_gate(self.dir)
        closeout.install_gate(self.dir)  # twice: still one block
        text = (hooks / "pre-commit").read_text(encoding="utf-8")
        self.assertIn("echo theirs", text)
        self.assertEqual(text.count(closeout.GATE_BEGIN), 1)
        self.r.write("knowledge/00-ROADMAP.md", "## Now\n- R-1 a\n")
        self.r.write("knowledge/02-SPEC-new.md", "# new\nno line\n")
        sh(self.dir, "git", "add", "-A")
        env = {**os.environ, "CLOSEOUT_PY": str(Path(closeout.__file__).resolve())}
        r = subprocess.run(["git", "commit", "-qm", "report only"], cwd=self.dir, capture_output=True, text=True, env=env)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("MISSING", r.stdout + r.stderr)
        self.r.write("CLAUDE.md", "# x\n\n## Doc map\nroadmap: knowledge/00-ROADMAP.md\nguardrails: block\n")
        self.r.write("knowledge/02-SPEC-two.md", "# two\nno line\n")
        sh(self.dir, "git", "add", "-A")
        r = subprocess.run(["git", "commit", "-qm", "blocked"], cwd=self.dir, capture_output=True, text=True, env=env)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("knowledge/02-SPEC-two.md", r.stdout + r.stderr)

    def test_the_gate_names_its_interpreter_and_helper_and_fails_safe_when_it_cannot_run(self):
        hook = closeout.install_gate(self.dir)
        text = hook.read_text(encoding="utf-8")
        self.assertIn(Path(sys.executable).as_posix(), text)                       # no python3 on PATH needed
        self.assertIn(Path(closeout.__file__).absolute().as_posix(), text)        # no ~/.claude/skills needed
        self.r.write("knowledge/00-ROADMAP.md", "## Now\n- R-1 a\n")
        self.r.write("knowledge/02-SPEC-new.md", "# new\nno line\n")
        sh(self.dir, "git", "add", "-A")
        # The environment as it is, with the helper pointed at a file that is not there: a literal POSIX PATH would
        # hide git on Windows and fail the test for the wrong reason.
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
        r = subprocess.run([shutil.which("git"), "commit", "-qm", "block, gate runs from its recorded paths"], cwd=self.dir,
                           capture_output=True, text=True,
                           env={**os.environ, "PATH": str(self.dir / "nonexistent"), "HOME": str(self.dir)})
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("knowledge/02-SPEC-two.md", r.stdout + r.stderr)             # refused by the real check, not by a crash

    def test_install_gate_refuses_a_folder_without_git_and_flags_a_hook_inside_the_tree(self):
        plain = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: __import__("shutil").rmtree(plain, ignore_errors=True))
        with self.assertRaises(ValueError):
            closeout.install_gate(plain)
        self.assertEqual(list(plain.iterdir()), [])  # no stray pre-commit file
        self.assertEqual(closeout.main(["install-gate", str(plain)]), 2)
        self.assertFalse(closeout.hook_in_tree(self.dir, closeout.install_gate(self.dir)))
        sh(self.dir, "git", "config", "core.hooksPath", ".husky")
        self.assertTrue(closeout.hook_in_tree(self.dir, closeout.install_gate(self.dir)))

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
        self.r.write("HANDOFF.md", "fine\n")
        cwd = os.getcwd()
        out = io.StringIO()  # keep the audit's report off the test run's own output, so its last line is the verdict
        try:
            os.chdir(self.dir)
            with contextlib.redirect_stdout(out):
                self.assertEqual(closeout.main(["audit", "--changed", "HANDOFF.md"]), 0)
                self.r.write("HANDOFF.md", "<<<<<<< x\n")
                self.assertEqual(closeout.main(["audit", "--changed", "HANDOFF.md"]), 1)
        finally:
            os.chdir(cwd)
        self.assertIn("audit: 1 fail, 0 warn", out.getvalue())

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
        self.r.write("HANDOFF.md", "fine\n")
        self.assertEqual(closeout.audit(self.dir, ["HANDOFF.md"]), [])
        warns = self.levels(closeout.audit(self.dir, ["HANDOFF.md"], check_baseline=True), "WARN")
        self.assertEqual([w[1] for w in warns],
                         ["baseline:readme", "baseline:claude_md", "baseline:pin", "baseline:overview", "baseline:roadmap",
                          "baseline:brain"])


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


if __name__ == "__main__":
    unittest.main()
