"""Tests for eli5/skill_fresh.py and closeout.py's transcript reader (held_skills, skill_changes): a running or resumed
session keeps the skill text it loaded, so a hook on each prompt (and on resume) names a skill whose file changed since,
and the file to Read, once per change. Synthetic transcripts in a temp folder. Run: python3 -m unittest discover -s tests"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path, PureWindowsPath

ELI5 = Path(__file__).resolve().parent.parent / "skills" / "eli5"
HOOK = ELI5 / "skill_fresh.py"
sys.path.insert(0, str(ELI5.parent / "closeout"))
import closeout  # noqa: E402
from cpu_clock import cpu_seconds  # noqa: E402  (the speed tests' clock: tests/cpu_clock.py)

PREFIX = "Base directory for this skill: "
FRONT = "---\nname: {name}\ndescription: the {name} skill\n---\n\n"
V4 = "# /eli5\n\nEnd every substantial turn with the block.\n\nELI5 v4\n- Did:\n"
V5 = "# /eli5\n\nEnd every substantial turn with the block.\n\nELI5 v5\n- Did:\n"
V6 = "# /eli5\n\nEnd every substantial turn with the block.\n\nELI5 v6\n- Did:\n"


def ts(n):
    return f"2026-09-23T06:{n:02d}:00.000Z"


def load(folder, body, when, meta=True):
    """An isMeta user record: a typed or invoked skill load, as Claude Code writes one."""
    rec = {"type": "user", "timestamp": when, "message": {"role": "user", "content": [
        {"type": "text", "text": f"{PREFIX}{folder}\n\n{body}"}]}}
    if meta:
        rec["isMeta"] = True
    return rec


def reinjection(when, *skills, bundled=True):
    """The invoked_skills attachment a compaction writes after its boundary: one line, several skills."""
    entries = [{"name": Path(str(f)).name, "path": f"userSettings:{Path(str(f)).name}", "content": f"{PREFIX}{f}\n\n{b}"}
               for f, b in skills]
    if bundled:
        entries.append({"name": "artifact-design", "path": "bundled:artifact-design", "content": "# artifact design\n"})
    return {"type": "attachment", "timestamp": when, "attachment": {"type": "invoked_skills", "skills": entries}}


def boundary(when):
    return {"type": "system", "subtype": "compact_boundary", "content": "Conversation compacted", "timestamp": when,
            "compactMetadata": {"trigger": "auto"}}


def summary(when):
    return {"type": "user", "isCompactSummary": True, "timestamp": when, "message": {"role": "user", "content":
            "This session is being continued from a previous conversation that ran out of context."}}


def read(path, content, when, start=1, num=None, total=None):
    """A Read tool call and its result record, carrying toolUseResult.file as the transcript records it: the lines are
    the content split at each newline, so a file ending in one counts a last, empty line (as a real Read reports)."""
    lines = len(content.split("\n"))
    return [{"type": "assistant", "timestamp": when, "message": {"role": "assistant", "content": [
                {"type": "tool_use", "id": f"r-{when}", "name": "Read", "input": {"file_path": str(path)}}]}},
            {"type": "user", "timestamp": when, "message": {"role": "user", "content": [
                {"type": "tool_result", "tool_use_id": f"r-{when}", "content": "(the file)"}]},
             "toolUseResult": {"type": "text", "file": {"filePath": str(path), "content": content,
                                                        "numLines": lines if num is None else num, "startLine": start,
                                                        "totalLines": lines if total is None else total}}}]


def prompt(text, when):
    return {"type": "user", "timestamp": when, "message": {"role": "user", "content": text}}


class Fixture(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(os.path.realpath(tmp.name))
        self.skills = self.tmp / ".claude" / "skills"
        self.n = 0

    def skill(self, name, body, root=None):
        folder = (root or self.skills) / name
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "SKILL.md").write_text(FRONT.format(name=name) + body, encoding="utf-8")
        return folder

    def transcript(self, *records):
        self.n += 1
        path = self.tmp / f"t{self.n}.jsonl"
        flat = []
        for r in records:
            flat.extend(r if isinstance(r, list) else [r])
        path.write_text("".join(json.dumps(r) + "\n" for r in flat), encoding="utf-8")
        return path

    def hook(self, transcript, event="UserPromptSubmit", python=None, session=None):
        # the hook keeps its per-transcript index and its per-session record of what it has named in the state folder:
        # this test's own, never the live one. A session's id is its transcript's name, as Claude Code writes them.
        env = dict(os.environ, DOC_GUARDRAILS_STATE=str(self.tmp / "state"))
        r = subprocess.run([python or sys.executable, str(HOOK)], capture_output=True, encoding="utf-8", env=env,
                           input=json.dumps({"hook_event_name": event, "transcript_path": str(transcript),
                                             "session_id": session or Path(transcript).stem}), timeout=60)
        self.assertEqual(r.returncode, 0, r.stderr)
        return r.stdout

    def append(self, path, *records, newline=True):
        flat = []
        for r in records:
            flat.extend(r if isinstance(r, list) else [r])
        with open(path, "a", encoding="utf-8") as fh:
            fh.write("\n".join(json.dumps(r) for r in flat) + ("\n" if newline else ""))

    def context(self, out):
        return json.loads(out)["hookSpecificOutput"]["additionalContext"]


class Freshness(Fixture):
    def test_changed_skill_announced(self):
        """Known-bad for a hook that never speaks: eli5 loaded with its v4 body while the file holds v5."""
        eli5 = self.skill("eli5", V5)
        out = self.hook(self.transcript(load(eli5, V4, ts(1))))
        d = json.loads(out)
        self.assertEqual(d["hookSpecificOutput"]["hookEventName"], "UserPromptSubmit")
        text = d["hookSpecificOutput"]["additionalContext"]
        self.assertIn(str(eli5 / "SKILL.md"), text)
        self.assertIn("Read", text)
        self.assertIn(ts(1), text)
        self.assertTrue(text.startswith("skill freshness: the eli5 skill changed"), text)
        self.assertEqual(json.loads(json.dumps(d)), d)
        resumed = json.loads(self.hook(self.transcript(load(eli5, V4, ts(1))), event="SessionStart"))
        self.assertEqual(resumed["hookSpecificOutput"]["hookEventName"], "SessionStart")

    def test_same_body_silent(self):
        eli5 = self.skill("eli5", V5)
        self.assertEqual(self.hook(self.transcript(load(eli5, V5, ts(1)))), "")

    def test_typed_command_suffix_silent(self):
        eli5 = self.skill("eli5", V5)
        self.assertEqual(self.hook(self.transcript(load(eli5, V5 + "\n\nARGUMENTS: x", ts(1)))), "")

    def test_secondary_login_one_entry(self):
        """A load through each login's skills link keys to one real folder: one line, not two."""
        real = self.skill("eli5", V5, root=self.tmp / "pack")
        for login in (".claude", ".claude-second-login"):
            (self.tmp / login / "skills").mkdir(parents=True, exist_ok=True)
            os.symlink(real, self.tmp / login / "skills" / "eli5")
        t = self.transcript(load(self.tmp / ".claude" / "skills" / "eli5", V4, ts(1)),
                            load(self.tmp / ".claude-second-login" / "skills" / "eli5", V4, ts(2)))
        text = self.context(self.hook(t))
        self.assertEqual(len(text.splitlines()), 1, text)
        self.assertEqual(len(closeout.held_skills(t)), 1)

    def test_gone_skill(self):
        eli5 = self.skill("eli5", V5)
        t = self.transcript(load(eli5, V5, ts(1)))
        (eli5 / "SKILL.md").unlink()
        text = self.context(self.hook(t))
        self.assertIn("is no longer installed at", text)
        self.assertIn("do not rely on its text", text)

    def test_never_says_invoke(self):
        closeout_skill = self.skill("closeout", "# /closeout\n\nProve what closed.\n")
        text = self.context(self.hook(self.transcript(load(closeout_skill, "# /closeout\n\nOlder text.\n", ts(1)))))
        self.assertIn("Read", text)
        for word in ("invoke", "run /"):
            self.assertNotIn(word, text)

    def test_full_read_counts(self):
        """A full Read of the current file, recorded after the load, holds the current text: silent."""
        eli5 = self.skill("eli5", V5)
        current = (eli5 / "SKILL.md").read_text(encoding="utf-8")
        self.assertEqual(self.hook(self.transcript(load(eli5, V4, ts(1)), read(eli5 / "SKILL.md", current, ts(2)))), "")
        held = closeout.held_skills(self.transcript(load(eli5, V4, ts(1)), read(eli5 / "SKILL.md", current, ts(2))))
        self.assertEqual([v["via"] for v in held.values()], ["read"])

    def test_partial_read_does_not(self):
        eli5 = self.skill("eli5", V5)
        current = (eli5 / "SKILL.md").read_text(encoding="utf-8")
        t = self.transcript(load(eli5, V4, ts(1)), read(eli5 / "SKILL.md", current, ts(2), start=40))
        self.assertIn("eli5", self.context(self.hook(t)))
        t = self.transcript(load(eli5, V4, ts(1)), read(eli5 / "SKILL.md", current, ts(2), num=3, total=9))
        self.assertIn("eli5", self.context(self.hook(t)))

    def test_compaction_reinjection_counts_as_load(self):
        """Known-stale (the dcc22705 shape): v5 loaded, then a compaction whose invoked_skills attachment re-injects
        v4 (stamped before its boundary), while the file holds v5: the session holds v4, the later text in file order."""
        eli5, redman = self.skill("eli5", V5), self.skill("redman", "# /redman\n\nSize what comes up.\n")
        att = reinjection(ts(9), (redman, "# /redman\n\nSize what comes up.\n"), (eli5, V4))
        t = self.transcript(load(eli5, V5, ts(1)), boundary(ts(10)), summary(ts(10)), att)
        text = self.context(self.hook(t))
        self.assertIn("the eli5 skill changed", text)
        self.assertNotIn("redman", text)
        self.assertNotIn("artifact-design", text)
        held = closeout.held_skills(t)
        mine = held[os.path.realpath(eli5)]
        self.assertEqual((mine["via"], mine["loaded_at"], mine["text"]), ("load", ts(9), V4))
        # its known-fresh twin: the attachment carries v5
        fresh = reinjection(ts(9), (redman, "# /redman\n\nSize what comes up.\n"), (eli5, V5))
        self.assertEqual(self.hook(self.transcript(load(eli5, V5, ts(1)), boundary(ts(10)), fresh)), "")
        # a v5 load after the v4 attachment: file order wins, never the timestamps
        self.assertEqual(self.hook(self.transcript(load(eli5, V5, ts(1)), boundary(ts(10)), att,
                                                   load(eli5, V5, ts(2)))), "")

    def test_a_boundary_that_reinjects_nothing(self):
        """The 3fe74ee0 line-15675 shape: a boundary and its summary with no attachment. The reader still gives the
        earlier load; with the file unchanged the hook is silent (a drop is not a change); changed, it names eli5."""
        eli5 = self.skill("eli5", V5)
        t = self.transcript(load(eli5, V5, ts(1)), boundary(ts(10)), summary(ts(10)))
        held = closeout.held_skills(t)[os.path.realpath(eli5)]
        self.assertEqual((held["via"], held["loaded_at"], held["text"]), ("load", ts(1), V5))
        self.assertEqual(self.hook(t), "")
        (eli5 / "SKILL.md").write_text(FRONT.format(name="eli5") + V6, encoding="utf-8")
        self.assertIn("the eli5 skill changed", self.context(self.hook(t)))

    def test_malformed_line_skipped(self):
        eli5 = self.skill("eli5", V5)
        t = self.transcript(load(eli5, V4, ts(1)))
        with open(t, "a", encoding="utf-8") as fh:
            fh.write('{"type": "user", "isMeta": true, "message": "' + PREFIX + str(eli5) + '\n')   # cut short
        self.assertIn("eli5", self.context(self.hook(t)))

    def test_empty_transcript_silent(self):
        self.assertEqual(self.hook(self.transcript()), "")
        self.assertEqual(closeout.held_skills(self.transcript()), {})
        self.assertEqual(self.hook(self.tmp / "not-yet-written.jsonl"), "")   # a new session's first prompt

    def test_a_load_not_marked_meta_or_quoted_in_text_is_not_a_load(self):
        eli5 = self.skill("eli5", V5)
        quoted = {"type": "assistant", "timestamp": ts(2), "message": {"role": "assistant", "content": [
            {"type": "text", "text": f"{PREFIX}{eli5}\n\n{V4}"}]}}
        self.assertEqual(self.hook(self.transcript(load(eli5, V4, ts(1), meta=False), quoted)), "")

    def test_a_read_of_a_skill_file_never_loaded_is_not_held(self):
        eli5 = self.skill("eli5", V5)
        old = FRONT.format(name="eli5") + V4
        self.assertEqual(self.hook(self.transcript(read(eli5 / "SKILL.md", old, ts(1)))), "")

    def test_80mb_under_1s(self):
        """The CI-safe bound: an 80 MB transcript with 50 loads is read in under a second."""
        eli5 = self.skill("eli5", V5)
        filler = json.dumps({"type": "user", "message": {"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": "x", "content": "y" * 16000}]}}) + "\n"
        path = self.tmp / "big.jsonl"
        with open(path, "w", encoding="utf-8") as fh:
            chunk = filler * 100
            for i in range(50):
                fh.write(chunk)
                fh.write(json.dumps(load(eli5, V5 if i < 49 else V4, ts(i % 60))) + "\n")
        self.assertGreater(path.stat().st_size, 80_000_000)
        start = cpu_seconds()
        changes = closeout.skill_changes(closeout.held_skills(path))
        took = cpu_seconds() - start
        self.assertEqual([c[0] for c in changes], ["eli5"])
        self.assertLess(took, 1.0)

    def test_error_is_loud(self):
        """An unreadable transcript is one context line saying the check failed, exit 0: never a broken prompt."""
        text = self.context(self.hook(self.tmp))                  # a folder where the transcript should be
        self.assertTrue(text.startswith("skill freshness check failed ("), text)
        self.assertIn("may be stale", text)

    def test_windows_paths(self):
        """A Windows base directory is read through pathlib: the skill is named by its folder, and a path that does not
        exist here is reported as no longer installed, never a crash."""
        base = PureWindowsPath(r"C:\Users\u\.claude\skills\eli5")
        self.assertEqual(closeout.skill_name(str(base)), "eli5")
        text = self.context(self.hook(self.transcript(load(base, V5, ts(1)))))
        self.assertIn("the eli5 skill this session loaded", text)
        self.assertIn("SKILL.md", text)

    def test_both_pythons_print_the_same(self):
        if not Path("/usr/bin/python3").is_file():
            self.skipTest("no /usr/bin/python3 on this machine")
        eli5 = self.skill("eli5", V5)
        t = self.transcript(load(eli5, V4, ts(1)))
        self.assertEqual(self.hook(t, session="a"), self.hook(t, python="/usr/bin/python3", session="b"))
        self.assertEqual(self.hook(t, session="a"), self.hook(t, python="/usr/bin/python3", session="b"))   # both ""


UPDATING = "# Updating\n\nStep one.\nStep two.\nStep three.\n"


class SkillFiles(Fixture):
    """Any file the session Read inside a held skill's folder is named when its content changed since that
    Read; SKILL.md keeps its own rule."""

    def closeout_skill(self):
        folder = self.skill("closeout", "# /closeout\n\nRead UPDATING.md in full first.\n")
        (folder / "UPDATING.md").write_text(UPDATING, encoding="utf-8")
        body = (folder / "SKILL.md").read_text(encoding="utf-8").split("---\n\n", 1)[1]
        return folder, body

    def test_a_changed_file_that_was_read_is_named(self):
        """Known-bad for a hook that compares SKILL.md only: UPDATING.md read in full, then rewritten."""
        folder, body = self.closeout_skill()
        t = self.transcript(load(folder, body, ts(1)), read(folder / "UPDATING.md", UPDATING, ts(2)))
        self.assertEqual(self.hook(t), "")
        (folder / "UPDATING.md").write_text(UPDATING.replace("Step two.", "Step two, changed."), encoding="utf-8")
        text = self.context(self.hook(t))
        self.assertEqual(text, f"skill freshness: {folder / 'UPDATING.md'}, a file of the closeout skill, changed on "
                               f"disk after this session read it ({ts(2)}). Read it again with the Read tool, in full, "
                               "before acting on it; do not re-run the skill.")
        self.assertNotIn("SKILL.md", text)
        # a full Read of the new text holds it again
        self.append(t, read(folder / "UPDATING.md", (folder / "UPDATING.md").read_text(encoding="utf-8"), ts(3)))
        self.assertEqual(self.hook(t), "")
        # a line added at the end is a change too, though every line read still matches
        with open(folder / "UPDATING.md", "a", encoding="utf-8") as fh:
            fh.write("Step four.\n")
        self.assertIn("UPDATING.md, a file of the closeout skill, changed", self.context(self.hook(t)))

    def test_both_lines_when_both_changed(self):
        folder, body = self.closeout_skill()
        t = self.transcript(load(folder, body, ts(1)), read(folder / "UPDATING.md", UPDATING, ts(2)))
        self.skill("closeout", "# /closeout\n\nNewer rules.\n")
        (folder / "UPDATING.md").write_text("# Updating\n\nOther.\n", encoding="utf-8")
        lines = self.context(self.hook(t)).splitlines()
        self.assertEqual(len(lines), 2, lines)
        self.assertTrue(lines[0].startswith("skill freshness: the closeout skill changed"), lines)
        self.assertIn("UPDATING.md, a file of the closeout skill, changed", lines[1])

    def test_chunked_reads_hold_each_line_they_read(self):
        """A file read in two chunks is current while every line read matches; a later chunk re-read replaces the
        lines it covers."""
        folder, body = self.closeout_skill()
        head, tail = "# Updating\n\nStep one.", "Step two.\nStep three.\n"
        t = self.transcript(load(folder, body, ts(1)), read(folder / "UPDATING.md", head, ts(2), 1, 3, 6),
                            read(folder / "UPDATING.md", tail, ts(3), 4, 3, 6))
        self.assertEqual(self.hook(t), "")
        (folder / "UPDATING.md").write_text(UPDATING.replace("Step one.", "Step 1."), encoding="utf-8")
        self.assertIn(f"UPDATING.md, a file of the closeout skill, changed on disk after this session read it ({ts(3)})",
                      self.context(self.hook(t)))
        self.append(t, read(folder / "UPDATING.md", "# Updating\n\nStep 1.", ts(4), 1, 3, 6))
        self.assertEqual(self.hook(t), "")

    def test_a_removed_file_is_named(self):
        folder, body = self.closeout_skill()
        t = self.transcript(load(folder, body, ts(1)), read(folder / "UPDATING.md", UPDATING, ts(2)))
        (folder / "UPDATING.md").unlink()
        self.assertEqual(self.context(self.hook(t)),
                         f"skill freshness: {folder / 'UPDATING.md'}, a file of the closeout skill this session read "
                         f"({ts(2)}), is no longer there; do not rely on its text.")

    def test_a_file_in_a_subfolder_counts_and_a_sibling_folder_does_not(self):
        folder, body = self.closeout_skill()
        (folder / "refs").mkdir()
        (folder / "refs" / "A.md").write_text("a\n", encoding="utf-8")
        sibling = self.tmp / ".claude" / "skills" / "closeout-old"
        sibling.mkdir()
        (sibling / "B.md").write_text("b\n", encoding="utf-8")
        t = self.transcript(load(folder, body, ts(1)), read(folder / "refs" / "A.md", "a\n", ts(2)),
                            read(sibling / "B.md", "b\n", ts(3)))
        (folder / "refs" / "A.md").write_text("a2\n", encoding="utf-8")
        (sibling / "B.md").write_text("b2\n", encoding="utf-8")
        text = self.context(self.hook(t))
        self.assertIn(str(folder / "refs" / "A.md"), text)
        self.assertNotIn("B.md", text)

    def test_a_read_through_the_skills_link_keys_to_the_real_file(self):
        real = self.skill("closeout", "# /closeout\n\nRead UPDATING.md.\n", root=self.tmp / "pack")
        (real / "UPDATING.md").write_text(UPDATING, encoding="utf-8")
        self.skills.mkdir(parents=True, exist_ok=True)
        os.symlink(real, self.skills / "closeout")
        body = "# /closeout\n\nRead UPDATING.md.\n"
        t = self.transcript(load(self.skills / "closeout", body, ts(1)),
                            read(self.skills / "closeout" / "UPDATING.md", UPDATING, ts(2)),
                            read(real / "UPDATING.md", UPDATING, ts(3)))
        (real / "UPDATING.md").write_text("changed\n", encoding="utf-8")
        text = self.context(self.hook(t))
        self.assertEqual(len(text.splitlines()), 1, text)
        self.assertIn(f"{real / 'UPDATING.md'}, a file of the closeout skill", text)

    def test_a_read_before_the_skill_was_held_is_not_held(self):
        """As for SKILL.md: a Read counts only once its skill is held, in file order."""
        folder, body = self.closeout_skill()
        t = self.transcript(read(folder / "UPDATING.md", UPDATING, ts(1)), load(folder, body, ts(2)))
        (folder / "UPDATING.md").write_text("changed\n", encoding="utf-8")
        self.assertEqual(self.hook(t), "")

    def test_skill_md_keeps_its_rule(self):
        """A partial Read of SKILL.md holds nothing (it is not an auxiliary file), and a full one still does."""
        folder, _ = self.closeout_skill()
        current = (folder / "SKILL.md").read_text(encoding="utf-8")
        t = self.transcript(load(folder, "# /closeout\n\nOlder.\n", ts(1)),
                            read(folder / "SKILL.md", current, ts(2), start=3, num=2, total=8))
        lines = self.context(self.hook(t)).splitlines()
        self.assertEqual(len(lines), 1, lines)
        self.assertTrue(lines[0].startswith("skill freshness: the closeout skill changed"))
        held = closeout.held_skills(t)[os.path.realpath(folder)]
        self.assertEqual(held["files"], {})
        self.append(t, read(folder / "SKILL.md", current, ts(3)))
        self.assertEqual(self.hook(t), "")

    def test_file_changes_reader(self):
        folder, body = self.closeout_skill()
        t = self.transcript(load(folder, body, ts(1)), read(folder / "UPDATING.md", UPDATING, ts(2)))
        held = closeout.held_skills(t)
        self.assertEqual(closeout.file_changes(held), [])
        (folder / "UPDATING.md").write_text("changed\n", encoding="utf-8")
        self.assertEqual(closeout.file_changes(held),
                         [("closeout", str(folder / "UPDATING.md"), ts(2), "changed")])


def shell_read(path, content, when):
    """A Bash tool call that prints a file (`cat`) and its result: the shell, not the Read tool."""
    return [{"type": "assistant", "timestamp": when, "message": {"role": "assistant", "content": [
                {"type": "tool_use", "id": f"b-{when}", "name": "Bash", "input": {"command": f"cat {path}"}}]}},
            {"type": "user", "timestamp": when, "message": {"role": "user", "content": [
                {"type": "tool_result", "tool_use_id": f"b-{when}", "content": content}]},
             "toolUseResult": {"stdout": content, "stderr": "", "interrupted": False}}]


class NamedOnce(Fixture):
    """Each change is named once per session. It is named again only when the file changes anew (other content), when
    the text the session holds changes (a load, a compaction's re-injection, a Read), or after a compaction (its summary
    replaced the notice). A resume alone is not a reason: the transcript it replays carries the notice."""

    def fresh_state(self):
        return self.tmp / "state" / "skill-fresh"

    def named(self, session):
        return json.loads((self.fresh_state() / f"{session}.json").read_text(encoding="utf-8"))["named"]

    def test_a_change_is_named_on_the_first_prompt_and_not_on_the_second(self):
        """Known-bad for a hook that names the same files on every prompt until they are read with the Read tool."""
        eli5 = self.skill("eli5", V5)
        t = self.transcript(load(eli5, V4, ts(1)))
        self.assertIn("the eli5 skill changed", self.context(self.hook(t)))
        self.append(t, prompt("next", ts(2)))
        self.assertEqual(self.hook(t), "")
        self.append(t, prompt("and the next", ts(3)))
        self.assertEqual(self.hook(t), "")

    def test_a_new_edit_to_the_same_file_is_named_again(self):
        eli5 = self.skill("eli5", V5)
        t = self.transcript(load(eli5, V4, ts(1)))
        self.assertIn("the eli5 skill changed", self.context(self.hook(t)))
        self.assertEqual(self.hook(t), "")
        self.skill("eli5", V6)                                  # a new content: a new change
        self.assertIn("the eli5 skill changed", self.context(self.hook(t)))
        self.assertEqual(self.hook(t), "")
        self.skill("eli5", V5)                                  # back to a content named before: still a new change
        self.assertIn("the eli5 skill changed", self.context(self.hook(t)))
        self.assertEqual(self.hook(t), "")

    def test_a_read_tool_read_clears_it_and_a_shell_read_does_not(self):
        eli5 = self.skill("eli5", V5)
        t = self.transcript(load(eli5, V4, ts(1)))
        self.assertIn("the eli5 skill changed", self.context(self.hook(t)))
        current = (eli5 / "SKILL.md").read_text(encoding="utf-8")
        self.append(t, shell_read(eli5 / "SKILL.md", current, ts(2)))
        self.assertEqual(self.hook(t), "")                      # not a Read: still a change, but named already
        self.assertEqual(len(self.named(t.stem)), 1)
        self.append(t, read(eli5 / "SKILL.md", current, ts(3)))
        self.assertEqual(self.hook(t), "")                      # the Read tool holds the current text: cleared
        self.assertEqual(self.named(t.stem), [])
        self.skill("eli5", V6)
        self.assertIn("the eli5 skill changed", self.context(self.hook(t)))
        self.assertEqual(self.hook(t), "")

    def test_another_file_of_the_skill_is_named_once_too(self):
        folder, body = SkillFiles.closeout_skill(self)
        t = self.transcript(load(folder, body, ts(1)), read(folder / "UPDATING.md", UPDATING, ts(2)))
        self.assertEqual(self.hook(t), "")
        (folder / "UPDATING.md").write_text(UPDATING.replace("Step two.", "Step 2."), encoding="utf-8")
        self.assertIn("UPDATING.md, a file of the closeout skill, changed", self.context(self.hook(t)))
        self.assertEqual(self.hook(t), "")
        self.append(t, shell_read(folder / "UPDATING.md", "(printed)", ts(3)))
        self.assertEqual(self.hook(t), "")
        (folder / "UPDATING.md").write_text(UPDATING.replace("Step two.", "Step II."), encoding="utf-8")
        self.assertIn("UPDATING.md, a file of the closeout skill, changed", self.context(self.hook(t)))
        self.assertEqual(self.hook(t), "")
        self.append(t, read(folder / "UPDATING.md", (folder / "UPDATING.md").read_text(encoding="utf-8"), ts(4)))
        self.assertEqual(self.hook(t), "")
        self.assertEqual(self.named(t.stem), [])
        (folder / "UPDATING.md").unlink()                       # gone is a change too, named once
        self.assertIn("is no longer there", self.context(self.hook(t)))
        self.assertEqual(self.hook(t), "")

    def test_the_wording_names_the_read_tool(self):
        eli5 = self.skill("eli5", V5)
        (eli5 / "NOTE.md").write_text("one\n", encoding="utf-8")
        t = self.transcript(load(eli5, V4, ts(1)), read(eli5 / "NOTE.md", "one\n", ts(2)))
        (eli5 / "NOTE.md").write_text("two\n", encoding="utf-8")
        self.assertEqual(self.context(self.hook(t)).splitlines(), [
            f"skill freshness: the eli5 skill changed on disk after this session loaded it ({ts(1)}). Read "
            f"{eli5 / 'SKILL.md'} again with the Read tool, in full, before acting on its rules; do not re-run the skill.",
            f"skill freshness: {eli5 / 'NOTE.md'}, a file of the eli5 skill, changed on disk after this session read it "
            f"({ts(2)}). Read it again with the Read tool, in full, before acting on it; do not re-run the skill."])

    def test_a_resume_and_its_prompt_name_it_once(self):
        """SessionStart on resume names the change; the prompt that follows, in the same session, does not repeat it.
        Another session on the same transcript (a fork) has named nothing yet."""
        eli5 = self.skill("eli5", V5)
        t = self.transcript(load(eli5, V4, ts(1)))
        out = json.loads(self.hook(t, event="SessionStart"))
        self.assertEqual(out["hookSpecificOutput"]["hookEventName"], "SessionStart")
        self.assertIn("the eli5 skill changed", out["hookSpecificOutput"]["additionalContext"])
        self.assertEqual(self.hook(t), "")
        self.assertEqual(self.hook(t, event="SessionStart"), "")      # resumed again, nothing new
        self.assertIn("the eli5 skill changed", self.context(self.hook(t, session="a-fork")))

    def test_a_compaction_names_it_again_once(self):
        """A compaction's summary replaces the notice, so a change still open is named once more: after a boundary that
        re-injects nothing, and after one that re-injects the older text."""
        eli5 = self.skill("eli5", V5)
        t = self.transcript(load(eli5, V4, ts(1)))
        self.assertIn("the eli5 skill changed", self.context(self.hook(t)))
        self.assertEqual(self.hook(t), "")
        self.append(t, boundary(ts(10)), summary(ts(10)))
        self.assertIn("the eli5 skill changed", self.context(self.hook(t)))
        self.assertEqual(self.hook(t), "")
        self.append(t, boundary(ts(20)), summary(ts(20)), reinjection(ts(19), (eli5, V4)))
        self.assertIn(f"the eli5 skill changed on disk after this session loaded it ({ts(19)})",
                      self.context(self.hook(t)))
        self.assertEqual(self.hook(t), "")
        self.assertEqual(self.hook(t, event="SessionStart"), "")

    def test_the_record_is_kept_in_the_state_folder_only(self):
        eli5 = self.skill("eli5", V5)
        t = self.transcript(load(eli5, V4, ts(1)))
        before = sorted(p for p in self.tmp.rglob("*") if "state" not in p.parts)
        self.assertIn("the eli5 skill changed", self.context(self.hook(t)))
        self.assertEqual(self.hook(t), "")
        self.assertEqual(sorted(p for p in self.tmp.rglob("*") if "state" not in p.parts), before)
        self.assertEqual(sorted(p.name for p in self.fresh_state().iterdir()), [f"{t.stem}.json"])
        self.assertEqual([n[0] for n in self.named(t.stem)], [str(eli5 / "SKILL.md")])
        # an id that is not a plain name stays a file inside the folder
        self.hook(t, session="../x y")
        self.assertEqual(len([p for p in self.fresh_state().iterdir() if p.name != f"{t.stem}.json"]), 1)
        self.assertEqual([p for p in self.fresh_state().iterdir() if p.name.startswith(".")], [])   # no temp left
        # an input without a session id is keyed by the transcript's name
        env = dict(os.environ, DOC_GUARDRAILS_STATE=str(self.tmp / "state"))
        t2 = self.transcript(load(eli5, V4, ts(1)))
        runs = [subprocess.run([sys.executable, str(HOOK)], capture_output=True, encoding="utf-8", env=env, timeout=60,
                               input=json.dumps({"hook_event_name": "UserPromptSubmit", "transcript_path": str(t2)}))
                for _ in range(2)]
        self.assertIn("the eli5 skill changed", runs[0].stdout)
        self.assertEqual(runs[1].stdout, "")
        self.assertTrue((self.fresh_state() / f"{t2.stem}.json").is_file())

    def test_records_untouched_for_14_days_are_cleared(self):
        eli5 = self.skill("eli5", V5)
        self.fresh_state().mkdir(parents=True)
        old, recent = self.fresh_state() / "old-session.json", self.fresh_state() / "recent-session.json"
        for p in (old, recent):
            p.write_text(json.dumps({"version": 1, "named": []}), encoding="utf-8")
        stale = time.time() - 15 * 86400
        os.utime(old, (stale, stale))
        self.hook(self.transcript(load(eli5, V4, ts(1))))
        self.assertFalse(old.exists())
        self.assertTrue(recent.exists())

    def test_a_broken_record_is_read_as_empty(self):
        eli5 = self.skill("eli5", V5)
        t = self.transcript(load(eli5, V4, ts(1)))
        self.assertIn("eli5", self.context(self.hook(t)))
        for junk in ("{not json", json.dumps({"version": 0}), json.dumps([1, 2]), json.dumps({"version": 1, "named": 3})):
            (self.fresh_state() / f"{t.stem}.json").write_text(junk, encoding="utf-8")
            self.assertIn("eli5", self.context(self.hook(t)), junk)
            self.assertEqual(self.hook(t), "", junk)


class Index(Fixture):
    """The per-transcript index: the hook reads only what was appended since its last
    run, so its time no longer grows with the transcript; the result is always the one a whole read gives."""

    def state(self):
        return self.tmp / "state" / "skill-index"

    def test_the_hook_keeps_its_index_in_the_state_folder_only(self):
        eli5 = self.skill("eli5", V5)
        t = self.transcript(load(eli5, V5, ts(1)))
        before = sorted(p for p in self.tmp.rglob("*") if "state" not in p.parts)
        self.assertEqual(self.hook(t), "")
        self.assertEqual(sorted(p for p in self.tmp.rglob("*") if "state" not in p.parts), before)
        files = list(self.state().iterdir())
        self.assertEqual(len(files), 1, files)
        self.assertEqual(json.loads(files[0].read_text(encoding="utf-8"))["size"], t.stat().st_size)

    def test_only_new_bytes_are_read(self):
        """White-box: a record in the middle rewritten in place (same size, same bytes at both ends) is not read again,
        so the index, not a re-read, gave the answer; appended records are read."""
        eli5 = self.skill("eli5", V5)
        t = self.transcript(prompt("hello " * 800, ts(0)), load(eli5, V5, ts(1)), prompt("hello " * 800, ts(2)))
        self.assertEqual(self.hook(t), "")
        raw = t.read_bytes()
        t.write_bytes(raw.replace(b"End every substantial turn", b"End every substantial TURN", 1))
        self.assertEqual(self.hook(t), "")                               # the index holds V5: not re-read
        self.assertIn("eli5", self.context(self.hook(self.transcript(*[json.loads(l) for l in
                                                                        t.read_text().splitlines()]))))
        self.append(t, load(eli5, V4, ts(3)))
        self.assertIn("the eli5 skill changed", self.context(self.hook(t)))
        self.append(t, load(eli5, V5, ts(4)))
        self.assertEqual(self.hook(t), "")

    def test_a_rewritten_transcript_is_read_again(self):
        eli5 = self.skill("eli5", V5)
        t = self.transcript(load(eli5, V4, ts(1)), prompt("x" * 5000, ts(2)))
        self.assertIn("eli5", self.context(self.hook(t)))
        t.write_text(json.dumps(load(eli5, V5, ts(3))) + "\n", encoding="utf-8")          # shorter
        self.assertEqual(self.hook(t), "")
        grown = json.dumps(load(eli5, V4, ts(4))) + "\n" + json.dumps(prompt("y" * 9000, ts(5))) + "\n"
        t.write_text(grown, encoding="utf-8")                                              # longer, other bytes
        self.assertIn("eli5", self.context(self.hook(t)))

    def test_an_unfinished_last_line_is_read_but_not_kept(self):
        eli5 = self.skill("eli5", V5)
        t = self.transcript(load(eli5, V5, ts(1)))
        self.append(t, load(eli5, V4, ts(2)), newline=False)
        self.assertIn("the eli5 skill changed", self.context(self.hook(t)))
        index = json.loads(next(self.state().iterdir()).read_text(encoding="utf-8"))
        self.assertLess(index["size"], t.stat().st_size)
        with open(t, "a", encoding="utf-8") as fh:
            fh.write("\n")
        self.append(t, load(eli5, V5, ts(3)))
        self.assertEqual(self.hook(t), "")

    def test_a_broken_index_is_rebuilt(self):
        eli5 = self.skill("eli5", V5)
        t = self.transcript(load(eli5, V4, ts(1)))
        self.assertIn("eli5", self.context(self.hook(t)))
        for junk in ("{not json", json.dumps({"version": 0}), json.dumps([1, 2])):
            next(self.state().iterdir()).write_text(junk, encoding="utf-8")
            shutil.rmtree(self.tmp / "state" / "skill-fresh")      # what was named, forgotten: the notice is the reading's
            self.assertIn("eli5", self.context(self.hook(t)), junk)

    def test_indexed_reading_equals_a_whole_read(self):
        """Step by step, the indexed reader gives exactly what a whole read gives: loads, a compaction's boundary and
        re-injection, full and partial Reads of SKILL.md and of other files, malformed lines."""
        eli5, redman = self.skill("eli5", V5), self.skill("redman", "# /redman\n\nSize.\n")
        (redman / "DEEP-RUN.md").write_text("a\nb\n", encoding="utf-8")
        current = (eli5 / "SKILL.md").read_text(encoding="utf-8")
        steps = [load(eli5, V4, ts(1)), read(redman / "DEEP-RUN.md", "a\nb\n", ts(2)), load(redman, "# /redman\n", ts(3)),
                 read(redman / "DEEP-RUN.md", "a", ts(4), 1, 1, 3), read(eli5 / "SKILL.md", current, ts(5)),
                 boundary(ts(6)), reinjection(ts(6), (eli5, V4)), "{broken", read(eli5 / "SKILL.md", current, ts(7), 2),
                 read(redman / "DEEP-RUN.md", "a\nb\n", ts(8)), load(eli5, V5, ts(9))]
        t = self.transcript()
        index = self.tmp / "idx"
        for step in steps:
            if step == "{broken":
                with open(t, "a", encoding="utf-8") as fh:
                    fh.write("{broken\n")
            else:
                self.append(t, step)
            self.assertEqual(closeout.read_session(t, index=index), closeout.read_session(t), step)
        self.assertEqual(closeout.read_session(t)[1], 1)                     # the one boundary, counted once
        self.assertEqual(len(list(index.iterdir())), 1)

    def test_warm_run_on_80mb_is_fast(self):
        """The CI-safe bound for the index: after the first read of an 80 MB transcript, a prompt's worth of new
        records is read in well under the hook's budget."""
        eli5 = self.skill("eli5", V5)
        filler = json.dumps({"type": "user", "message": {"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": "x", "content": "y" * 16000}]}}) + "\n"
        path = self.tmp / "big.jsonl"
        with open(path, "w", encoding="utf-8") as fh:
            for i in range(50):
                fh.write(filler * 100)
                fh.write(json.dumps(load(eli5, V5, ts(i % 60))) + "\n")
        self.assertGreater(path.stat().st_size, 80_000_000)
        index = self.tmp / "idx"
        start = cpu_seconds()
        self.assertEqual(closeout.skill_changes(closeout.held_skills(path, index=index)), [])
        self.assertLess(cpu_seconds() - start, 1.0)
        self.append(path, prompt("next", ts(1)), load(eli5, V4, ts(2)))
        start = cpu_seconds()
        changes = closeout.skill_changes(closeout.held_skills(path, index=index))
        self.assertLess(cpu_seconds() - start, 0.05)
        self.assertEqual([c[0] for c in changes], ["eli5"])

    def test_the_index_folder_is_the_state_folder(self):
        import session_skills
        self.assertEqual(session_skills.index_dir(), closeout.state_dir() / "skill-index")


if __name__ == "__main__":
    unittest.main()
