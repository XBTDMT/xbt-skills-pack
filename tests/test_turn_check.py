"""Tests for doc-guardrails/turn_check.py, the Stop hook that judges the documents a session is shown to have written,
named or committed this turn. Run: python3 -m unittest discover -s tests"""
import git_quiet  # noqa: F401  (first: git's background upkeep off for every fixture; tests/git_quiet.py)
import datetime
import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

HOOK = Path(__file__).resolve().parent.parent / "skills" / "doc-guardrails" / "turn_check.py"
sys.path.insert(0, str(HOOK.parent))
import turn_check  # noqa: E402
from cpu_clock import cpu_seconds  # noqa: E402  (the speed tests' clock: tests/cpu_clock.py)

SENTENCE = "A document someone else wrote is not edited to get past this check"


def sh(cwd, *args):
    return subprocess.run(args, cwd=cwd, check=True, capture_output=True, encoding="utf-8").stdout


def ts(epoch):
    return datetime.datetime.fromtimestamp(epoch, datetime.timezone.utc).isoformat().replace("+00:00", "Z")


class Session(unittest.TestCase):
    """Two repositories, a session transcript, another session's transcript under a fixture config folder."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name).resolve()
        self.state = self.tmp / "state"
        self.cfg = self.tmp / "cfg"
        (self.cfg / "projects" / "p").mkdir(parents=True)
        self.main = self.repo("main")
        self.lane = self.repo("lane")
        self.transcript = self.tmp / "session" / "s1.jsonl"
        self.transcript.parent.mkdir()
        self.transcript.write_text("", encoding="utf-8")
        self.t0 = time.time() - 600

    def repo(self, name):
        root = self.tmp / name
        (root / "knowledge").mkdir(parents=True)
        (root / "knowledge" / "00-ROADMAP.md").write_text("## Now\n- R-1 a\n", encoding="utf-8")
        sh(root, "git", "init", "-q", "-b", "main")
        sh(root, "git", "add", "-A")
        sh(root, "git", "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "base")
        old = time.time() - 7200
        os.utime(root / "knowledge" / "00-ROADMAP.md", (old, old))
        return root

    def tool(self, uid, name, inp, start, end, out="ok", transcript=None):
        rows = [{"type": "assistant", "timestamp": ts(start),
                 "message": {"content": [{"type": "tool_use", "id": uid, "name": name, "input": inp}]}},
                {"type": "user", "timestamp": ts(end),
                 "message": {"content": [{"type": "tool_result", "tool_use_id": uid, "content": out}]}}]
        with open(transcript or self.transcript, "a", encoding="utf-8") as fh:
            for r in rows:
                fh.write(json.dumps(r) + "\n")

    def touch(self, path, text, when):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        os.utime(path, (when, when))

    def check(self, now=None, **kw):
        payload = {"session_id": "s1", "cwd": str(kw.pop("cwd", self.main)), "stop_hook_active": False,
                   "transcript_path": str(kw.pop("transcript", self.transcript))}
        kw.setdefault("temp_dirs", ())
        kw.setdefault("config_dirs", [self.cfg])
        return turn_check.check(payload, self.state, now=self.t0 + 500 if now is None else now, **kw)

    def reason(self, out):
        self.assertIsNotNone(out)
        return json.loads(out).get("reason", "")


class Evidence(Session):
    def test_another_sessions_shell_write_inside_my_window_is_silent(self):
        self.tool("u1", "Bash", {"command": "python3 long_job.py"}, self.t0 + 100, self.t0 + 300)
        self.touch(self.main / "knowledge" / "02-spec-theirs.md", "# theirs\n", self.t0 + 200)   # inside my window
        self.assertIsNone(self.check())

    def test_a_touch_with_unchanged_content_is_silent(self):
        spec = self.main / "knowledge" / "02-spec-dirty.md"
        self.touch(spec, "# dirty\n", self.t0 - 4000)
        self.tool("u0", "Bash", {"command": "ls"}, self.t0, self.t0 + 1)
        self.check(now=self.t0 + 10)                                  # the previous Stop saw it, unlinked
        self.tool("u1", "Bash", {"command": f"git stash && git stash pop # {spec.name}"}, self.t0 + 100, self.t0 + 110)
        os.utime(spec, (self.t0 + 105, self.t0 + 105))              # autostash: touched, content unchanged
        self.assertIsNone(self.check())

    def test_written_and_committed_in_one_call_blocks_naming_the_commit(self):
        self.tool("u1", "Bash", {"command": "python3 make.py && git add -A && git commit -qm spec"}, self.t0 + 100,
                  self.t0 + 110)
        self.touch(self.main / "knowledge" / "02-spec-made.md", "# made\n", self.t0 + 105)
        sh(self.main, "git", "add", "-A")
        sh(self.main, "git", "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "spec")
        head = sh(self.main, "git", "rev-parse", "HEAD").strip()
        with mock.patch.object(turn_check, "commits_this_turn",
                               lambda root, since, windows: [(head, ["knowledge/02-spec-made.md"])]):
            reason = self.reason(self.check())
        self.assertIn(f"knowledge/02-spec-made.md (committed in {head[:8]} this turn; written this turn): MISSING", reason)

    def test_committed_through_the_reflog_this_turn(self):
        now = time.time()
        self.t0 = now - 60
        self.tool("u1", "Bash", {"command": "git add -A && git commit -qm spec"}, now - 30, now + 30)
        (self.main / "knowledge" / "02-spec-reflog.md").write_text("# r\n", encoding="utf-8")
        sh(self.main, "git", "add", "-A")
        sh(self.main, "git", "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "spec")
        reason = self.reason(self.check(now=now + 40))
        self.assertIn("knowledge/02-spec-reflog.md (committed in", reason)

    def test_unnamed_output_committed_in_a_later_call_blocks(self):
        now = time.time()
        self.t0 = now - 120
        self.tool("u1", "Bash", {"command": "python3 make.py"}, now - 100, now - 90)
        (self.main / "knowledge" / "02-spec-out.md").write_text("# out\n", encoding="utf-8")
        self.tool("u2", "Bash", {"command": "git add -A && git commit -qm x"}, now - 20, now + 20)
        sh(self.main, "git", "add", "-A")
        sh(self.main, "git", "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "x")
        self.assertIn("knowledge/02-spec-out.md (committed in", self.reason(self.check(now=now + 30)))

    def test_other_sessions_index_reads_odd_lines_and_still_finds_the_write(self):
        """Lines that mention tool_use but are not a message with content (a list, a null message, null content) are
        skipped, never an error, and a real Write on the next line is still indexed (E5)."""
        other = self.cfg / "projects" / "-other" / "o1.jsonl"
        other.parent.mkdir(parents=True, exist_ok=True)
        target = self.main / "knowledge" / "02-spec-theirs.md"
        lines = ['["tool_use", "Write"]', '{"type": "tool_use", "message": null}',
                 '{"message": {"content": null}, "note": "tool_use Write"}', '"tool_use Write"',
                 json.dumps({"message": {"content": [{"type": "tool_use", "name": "Write",
                                                      "input": {"file_path": str(target)}}]}})]
        other.write_text("\n".join(lines) + "\n", encoding="utf-8")
        found = turn_check.other_sessions_written([self.cfg], [self.transcript], 0)
        self.assertEqual(found, {turn_check.same_path(target)})

    def test_another_sessions_write_of_a_file_i_only_read_is_silent(self):
        spec = self.main / "knowledge" / "02-spec-shared.md"
        self.tool("u1", "Bash", {"command": f"cat {spec}"}, self.t0 + 100, self.t0 + 101)
        self.touch(spec, "# theirs\n", self.t0 + 150)
        other = self.cfg / "projects" / "p" / "other.jsonl"
        self.tool("o1", "Write", {"file_path": str(spec), "content": "# theirs\n"}, self.t0 + 149, self.t0 + 150,
                  transcript=other)
        self.assertIsNone(self.check())
        other.unlink()                                                 # the known-bad: nobody else wrote it
        self.state = self.tmp / "state2"
        self.assertIn("02-spec-shared.md", self.reason(self.check()))

    def test_another_sessions_commit_in_my_window_is_silent(self):
        self.tool("u1", "Bash", {"command": "sleep 60"}, self.t0 + 100, self.t0 + 200)
        (self.main / "knowledge" / "02-spec-theirs.md").write_text("# t\n", encoding="utf-8")
        sh(self.main, "git", "add", "-A")
        sh(self.main, "git", "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "theirs")
        self.assertIsNone(self.check())

    def test_the_reason_says_someone_elses_document_is_not_edited(self):
        self.tool("u1", "Write", {"file_path": str(self.main / "knowledge" / "02-spec-mine.md")}, self.t0 + 100,
                  self.t0 + 101)
        self.touch(self.main / "knowledge" / "02-spec-mine.md", "# mine\n", self.t0 + 100.5)
        self.assertIn(SENTENCE, self.reason(self.check()))

    def test_each_line_says_why_the_document_is_this_sessions(self):
        """G-54: a document a command the session ran wrote is named as the session's, as one its tools wrote is, so
        the escape for someone else's document is not read as covering a generator's output."""
        self.tool("u1", "Write", {"file_path": str(self.main / "knowledge" / "02-spec-mine.md")}, self.t0 + 100,
                  self.t0 + 101)
        self.touch(self.main / "knowledge" / "02-spec-mine.md", "# mine\n", self.t0 + 100.5)
        self.tool("u2", "Bash", {"command": "python3 tools/make_spec.py"}, self.t0 + 200, self.t0 + 210,
                  out="wrote knowledge/02-spec-made.md")
        self.touch(self.main / "knowledge" / "02-spec-made.md", "# made\n", self.t0 + 205)
        reason = self.reason(self.check())
        self.assertIn("knowledge/02-spec-mine.md (written by this session's tools): MISSING", reason)
        self.assertIn("knowledge/02-spec-made.md (written by a command this session ran): MISSING", reason)
        self.assertIn("one a command it ran wrote (a generator, a script) is its own to link", reason)
        self.assertNotIn("written before this turn", reason)

    def test_a_document_written_before_the_turn_and_committed_in_it_may_be_someone_elses(self):
        """G-54: the one listed document that can be someone else's (a document the user staged, committed by the
        session this turn) says so, and only it carries the escape."""
        now = time.time()
        self.t0 = now - 60
        staged = self.main / "knowledge" / "02-spec-staged.md"
        staged.write_text("# staged by the user\n", encoding="utf-8")
        os.utime(staged, (now - 3600, now - 3600))
        self.tool("u1", "Bash", {"command": "git commit -qm spec"}, now - 30, now + 30)
        sh(self.main, "git", "add", "-A")
        sh(self.main, "git", "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "spec")
        reason = self.reason(self.check(now=now + 40))
        self.assertIn("knowledge/02-spec-staged.md (committed in", reason)
        self.assertIn("written before this turn: if someone else wrote it, leave it and say so): MISSING", reason)

    def test_hostile_tokens_raise_nothing(self):
        self.tool("u1", "Bash", {"command": "ls knowledge/[n-c].md knowledge/x**y.md [ knowledge/*.md"}, self.t0 + 100,
                  self.t0 + 110)
        self.touch(self.main / "knowledge" / "02-spec-g.md", "# g\n", self.t0 + 105)
        self.assertIn("02-spec-g.md", self.reason(self.check()))   # the valid glob names it; the bad ones name nothing

    def test_named_in_my_calls_output_within_its_window_blocks(self):
        self.tool("u1", "Bash", {"command": "python3 make.py"}, self.t0 + 100, self.t0 + 110,
                  out="wrote knowledge/02-spec-out.md")
        self.touch(self.main / "knowledge" / "02-spec-out.md", "# out\n", self.t0 + 105)
        self.assertIn("02-spec-out.md", self.reason(self.check()))

    def test_heredoc_and_script_content_name_the_file(self):
        self.tool("u1", "Write", {"file_path": str(self.main / "make.py"),
                                  "content": "open('knowledge/02-spec-s.md','w').write('# s')"}, self.t0 + 90, self.t0 + 91)
        self.tool("u2", "Bash", {"command": "python3 make.py"}, self.t0 + 100, self.t0 + 110)
        self.touch(self.main / "knowledge" / "02-spec-s.md", "# s\n", self.t0 + 105)
        self.tool("u3", "Bash", {"command": "cat > knowledge/02-spec-h.md <<'EOF'\n# h\nEOF"}, self.t0 + 120, self.t0 + 121)
        self.touch(self.main / "knowledge" / "02-spec-h.md", "# h\n", self.t0 + 120.5)
        reason = self.reason(self.check())
        self.assertIn("02-spec-s.md", reason)
        self.assertIn("02-spec-h.md", reason)

    def test_read_only_tool_windows_do_not_count(self):
        self.tool("u1", "Read", {"file_path": str(self.main / "README.md")}, self.t0 + 100, self.t0 + 200)
        self.tool("u2", "Agent", {"prompt": "look around"}, self.t0 + 300, self.t0 + 400)
        self.touch(self.main / "knowledge" / "02-spec-a.md", "# a\n", self.t0 + 150)
        self.touch(self.main / "knowledge" / "02-spec-b.md", "# b\n", self.t0 + 350)
        self.assertIsNone(self.check())

    def test_a_subagents_writes_count_as_this_sessions(self):
        sub = self.transcript.parent / "s1" / "subagents" / "agent-x.jsonl"
        sub.parent.mkdir(parents=True)
        self.tool("a1", "Write", {"file_path": str(self.lane / "knowledge" / "02-spec-agent.md")}, self.t0 + 200,
                  self.t0 + 201, transcript=sub)
        self.touch(self.lane / "knowledge" / "02-spec-agent.md", "# agent\n", self.t0 + 200.5)
        self.assertIn("02-spec-agent.md", self.reason(self.check()))

    def test_this_sessions_writes_are_judged_wherever_they_happened_and_nothing_else(self):
        self.touch(self.main / "knowledge" / "02-spec-theirs.md", "# theirs\n", self.t0 + 100)
        self.tool("u1", "Write", {"file_path": str(self.main / "knowledge" / "02-spec-mine.md")}, self.t0 + 200,
                  self.t0 + 201)
        self.touch(self.main / "knowledge" / "02-spec-mine.md", "# mine\n", self.t0 + 200.5)
        self.tool("u2", "Bash", {"command": "python3 make_spec.py knowledge/02-spec-script.md"},
                  self.t0 + 300, self.t0 + 310)
        self.touch(self.main / "knowledge" / "02-spec-script.md", "# from a script\n", self.t0 + 305)
        self.touch(self.main / "knowledge" / "02-spec-unnamed.md", "# an unnamed script output\n", self.t0 + 306)
        self.tool("u3", "Bash", {"command": f"cd {self.lane} && python3 make_spec.py knowledge/02-spec-lane.md"},
                  self.t0 + 400, self.t0 + 410)
        self.touch(self.lane / "knowledge" / "02-spec-lane.md", "# a script in another project\n", self.t0 + 405)
        reason = self.reason(self.check())
        self.assertIn("02-spec-mine.md", reason)
        self.assertIn("02-spec-script.md", reason)
        self.assertNotIn("02-spec-theirs.md", reason)
        self.assertNotIn("02-spec-unnamed.md", reason)   # neither named nor committed: the gate's and the sweep's
        self.assertNotIn("02-spec-lane.md", reason)      # named in another project (G-40): that project's gate and sweep


class OwnRoots(Session):
    """G-40: naming (a call's input, or its output inside its window) makes a document the session's only inside the
    session's own roots — the git repository of its cwd and that repository's worktrees, derived from the common git
    dir. In any other project a document is the session's only when its write tools wrote it or its own `git commit`
    committed it."""

    DOC = "30-redesign-spec.md"

    def worktree(self, name, *config):
        wt = self.tmp / name
        sh(self.main, "git", *config, "worktree", "add", "-q", "-b", name, str(wt))
        old = time.time() - 7200
        os.utime(wt / "knowledge" / "00-ROADMAP.md", (old, old))
        return wt

    def test_another_projects_file_named_by_a_read_only_output_while_it_changes_is_not_held(self):
        """The first hold of 2026-09-28: a read-only command's output names a file another project's own session is
        writing, inside the call's window."""
        doc = self.lane / "knowledge" / self.DOC
        self.tool("u1", "Bash", {"command": f"git -C {self.lane} status --short && ls -l {self.lane}/knowledge"},
                  self.t0 + 100, self.t0 + 110, out=f" M knowledge/{self.DOC}\n-rw-r--r-- 1 t t 9 {doc}")
        self.touch(doc, "# theirs, mid-write\n", self.t0 + 105)
        self.assertIsNone(self.check())

    def test_another_projects_file_named_in_my_own_ls_and_grep_is_not_held(self):
        """The second hold of 2026-09-28: this session's own `ls`/`grep`, checking who wrote the file, named it."""
        doc = self.lane / "knowledge" / self.DOC
        self.touch(doc, "# theirs\n", self.t0 + 50)
        self.tool("u1", "Bash", {"command": f"ls -l {doc}"}, self.t0 + 100, self.t0 + 101)
        self.tool("u2", "Bash", {"command": f"grep -n roadmap {doc}"}, self.t0 + 110, self.t0 + 111)
        self.assertIsNone(self.check())

    def test_the_same_naming_in_my_own_repository_still_holds(self):
        """The known-bad controls of the two tests above: the same calls and output, the file in the cwd's repo."""
        shown, named = self.main / "knowledge" / self.DOC, self.main / "knowledge" / "31-named-spec.md"
        self.tool("u1", "Bash", {"command": f"git -C {self.main} status --short && ls -l {self.main}/knowledge"},
                  self.t0 + 100, self.t0 + 110, out=f" M knowledge/{self.DOC}\n-rw-r--r-- 1 t t 9 {shown}")
        self.touch(shown, "# mine\n", self.t0 + 105)
        self.touch(named, "# mine\n", self.t0 + 50)
        self.tool("u2", "Bash", {"command": f"ls -l {named}"}, self.t0 + 120, self.t0 + 121)
        self.tool("u3", "Bash", {"command": f"grep -n roadmap {named}"}, self.t0 + 130, self.t0 + 131)
        reason = self.reason(self.check())
        self.assertIn(f"knowledge/{self.DOC} (written by a command this session ran): MISSING", reason)
        self.assertIn("knowledge/31-named-spec.md (changed this turn and named in this session's commands): MISSING",
                      reason)

    def test_the_same_naming_in_a_worktree_of_my_repository_still_holds(self):
        """The worktrees come from the common git dir, whichever side the cwd is on, with absolute or relative links."""
        for name, config in (("main-wt", ()), ("main-rel", ("-c", "worktree.useRelativePaths=true"))):
            with self.subTest(worktree=name):
                wt = self.worktree(name, *config)
                for cwd, where in ((self.main, wt), (wt, self.main)):
                    doc = where / "knowledge" / f"32-{name}-spec.md"
                    self.touch(doc, "# mine\n", self.t0 + 50)
                    self.tool(f"{name}-{cwd.name}", "Bash", {"command": f"ls -l {doc}"}, self.t0 + 100, self.t0 + 101)
                    self.state = self.tmp / f"state-{name}-{cwd.name}"
                    self.assertIn(f"32-{name}-spec.md (changed this turn and named in this session's commands): MISSING",
                                  self.reason(self.check(cwd=cwd)))
                    doc.unlink()

    def test_the_repositories_own_roots_are_read_from_the_common_git_dir(self):
        wt = self.worktree("main-wt")
        want = {turn_check.same_path(self.main), turn_check.same_path(wt)}
        self.assertEqual(turn_check.own_roots(self.main / "knowledge"), want)
        self.assertEqual(turn_check.own_roots(wt / "knowledge"), want)
        self.assertEqual(turn_check.own_roots(self.lane), {turn_check.same_path(self.lane)})
        plain = self.tmp / "plain"
        plain.mkdir()
        (plain / "CLAUDE.md").write_text("# p\n", encoding="utf-8")
        self.assertEqual(turn_check.own_roots(plain), {turn_check.same_path(plain)})

    def test_a_write_tool_write_into_another_project_still_holds(self):
        doc = self.lane / "knowledge" / self.DOC
        self.tool("u1", "Write", {"file_path": str(doc), "content": "# mine\n"}, self.t0 + 100, self.t0 + 101)
        self.touch(doc, "# mine\n", self.t0 + 100.5)
        self.assertIn(f"knowledge/{self.DOC} (written by this session's tools): MISSING", self.reason(self.check()))

    def test_my_own_commit_in_another_project_still_holds(self):
        now = time.time()
        self.t0 = now - 60
        self.tool("u1", "Bash", {"command": f"git -C {self.lane} add -A && git -C {self.lane} commit -qm spec"},
                  now - 30, now + 30)
        (self.lane / "knowledge" / self.DOC).write_text("# r\n", encoding="utf-8")
        sh(self.lane, "git", "add", "-A")
        sh(self.lane, "git", "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "spec")
        self.assertIn(f"knowledge/{self.DOC} (committed in", self.reason(self.check(now=now + 40)))


class State(Session):
    def test_a_kill_after_the_scan_still_reports_at_the_next_stop(self):
        self.tool("u1", "Write", {"file_path": str(self.main / "knowledge" / "02-spec-k.md")}, self.t0 + 100, self.t0 + 101)
        self.touch(self.main / "knowledge" / "02-spec-k.md", "# k\n", self.t0 + 100.5)
        with mock.patch.object(turn_check, "save_state", side_effect=SystemExit):
            with self.assertRaises(SystemExit):
                self.check()
        self.assertIn("02-spec-k.md", self.reason(self.check(now=self.t0 + 510)))

    def test_state_file_per_session_no_unknown_pruned_and_migrated(self):
        self.state.mkdir()
        (self.state / "old-marker").write_text("1", encoding="utf-8")
        stale = self.state / "old-marker"
        os.utime(stale, (time.time() - 20 * 86400,) * 2)
        (self.state / "s1").write_text(str(self.t0 + 50), encoding="utf-8")   # a legacy marker for this session
        self.tool("u1", "Write", {"file_path": str(self.main / "knowledge" / "02-spec-m.md")}, self.t0 + 100, self.t0 + 101)
        self.touch(self.main / "knowledge" / "02-spec-m.md", "# m\n", self.t0 + 100.5)
        self.check()
        self.assertFalse(stale.exists())
        self.assertFalse((self.state / "s1").exists())
        data = json.loads((self.state / "sessions" / "s1.json").read_text(encoding="utf-8"))
        self.assertEqual(data["version"], 2)
        turn_check.check({"cwd": str(self.main), "stop_hook_active": False, "transcript_path": str(self.transcript)},
                         self.state, now=self.t0 + 600, temp_dirs=(), config_dirs=[self.cfg])
        self.assertFalse((self.state / "unknown").exists())
        self.assertFalse((self.state / "sessions" / "unknown.json").exists())

    def test_offsets_read_only_what_was_appended(self):
        self.tool("u1", "Bash", {"command": "ls"}, self.t0 + 100, self.t0 + 101)
        self.check()
        seen = []
        real = turn_check.read_new

        def spy(path, offset, since):
            entries, size = real(path, offset, since)
            seen.append(len(entries or []))
            offsets.append(offset)
            return entries, size
        offsets, size = [], self.transcript.stat().st_size
        self.tool("u2", "Bash", {"command": "pwd"}, self.t0 + 510, self.t0 + 511)
        with mock.patch.object(turn_check, "read_new", spy):
            self.check(now=self.t0 + 520)
        self.assertEqual(seen, [2])   # only the two new lines
        # the saved offset is found again (a whole read filtered by time would also give 2 here): the state's key and
        # the lookup's are the same str, through the JSON round trip
        self.assertEqual(offsets, [size])
        saved = json.loads((self.state / "sessions" / "s1.json").read_text(encoding="utf-8"))["offsets"]
        self.assertEqual(saved, {str(self.transcript): self.transcript.stat().st_size})
        self.transcript.write_text("", encoding="utf-8")   # truncated: read whole, filtered by time
        self.tool("u3", "Bash", {"command": "pwd"}, self.t0 + 530, self.t0 + 531)
        seen.clear()
        with mock.patch.object(turn_check, "read_new", spy):
            self.check(now=self.t0 + 540)
        self.assertEqual(seen, [2])

    def test_budget_carries_unchecked_roots_to_the_next_stop(self):
        self.tool("u1", "Write", {"file_path": str(self.main / "knowledge" / "02-spec-b.md")}, self.t0 + 100, self.t0 + 101)
        self.touch(self.main / "knowledge" / "02-spec-b.md", "# b\n", self.t0 + 100.5)
        out = json.loads(self.check(budget=0))
        self.assertIn("not checked this turn (time budget)", out.get("systemMessage", "") + out.get("reason", ""))
        self.assertIn("02-spec-b.md", self.reason(self.check(now=self.t0 + 510)))

    def test_no_transcript_gives_a_system_message_not_a_block(self):
        self.touch(self.main / "knowledge" / "02-spec-theirs.md", "# theirs\n", self.t0 + 100)
        out = json.loads(self.check(transcript=self.tmp / "missing.jsonl"))
        self.assertNotIn("decision", out)
        self.assertIn("no transcript to attribute from", out["systemMessage"])

    def test_map_problem_is_a_system_message_once_per_map_and_never_a_block(self):
        (self.main / "CLAUDE.md").write_text("# p\n\n## Doc map\nroadmap: knowledge/00-ROADMAP.md\nguardrails: reporting\n",
                                             encoding="utf-8")
        self.tool("u1", "Bash", {"command": "ls"}, self.t0 + 100, self.t0 + 101)
        out = json.loads(self.check())
        self.assertNotIn("decision", out)
        self.assertIn("the doc map in", out["systemMessage"])
        self.tool("u2", "Bash", {"command": "ls"}, self.t0 + 510, self.t0 + 511)
        self.assertIsNone(self.check(now=self.t0 + 520))                 # the same map: said once
        (self.main / "CLAUDE.md").write_text("# p\n\n## Doc map\nroadmap: knowledge/00-ROADMAP.md\nguardrails: blocking\n",
                                             encoding="utf-8")
        self.tool("u3", "Bash", {"command": "ls"}, self.t0 + 530, self.t0 + 531)
        self.assertIn("the doc map in", json.loads(self.check(now=self.t0 + 540))["systemMessage"])


class Roots(Session):
    def test_a_temp_repo_beside_a_real_cwd_is_silent_and_judged_when_it_is_the_cwd(self):
        fixture = self.tmp / "fixture"
        (fixture / "knowledge").mkdir(parents=True)
        (fixture / "knowledge" / "00-ROADMAP.md").write_text("## Now\n- R-1 a\n", encoding="utf-8")
        sh(fixture, "git", "init", "-q")
        self.tool("u1", "Write", {"file_path": str(fixture / "knowledge" / "02-spec-fixture.md")}, self.t0 + 100,
                  self.t0 + 101)
        self.touch(fixture / "knowledge" / "02-spec-fixture.md", "# f\n", self.t0 + 100.5)
        self.assertIsNone(self.check(temp_dirs=[str(self.tmp)]))   # a throwaway repository beside the real cwd
        self.state = self.tmp / "state2"
        self.assertIn("02-spec-fixture.md", self.reason(self.check(cwd=fixture, temp_dirs=[str(self.tmp)])))

    def test_outside_git_a_written_document_is_judged(self):
        plain = self.tmp / "plain"
        (plain / "knowledge").mkdir(parents=True)
        (plain / "CLAUDE.md").write_text("# p\n", encoding="utf-8")
        (plain / "knowledge" / "00-ROADMAP.md").write_text("## Now\n- R-1 a\n", encoding="utf-8")
        self.tool("u1", "Write", {"file_path": str(plain / "knowledge" / "02-spec-a.md")}, self.t0 + 100, self.t0 + 101)
        self.touch(plain / "knowledge" / "02-spec-a.md", "# a\n", self.t0 + 100.5)
        self.assertIn("02-spec-a.md (written by this session's tools): MISSING", self.reason(self.check(cwd=plain)))

    def test_silent_when_the_guardrails_are_off_or_it_continues(self):
        (self.main / "CLAUDE.md").write_text("# p\n\n## Doc map\nroadmap: knowledge/00-ROADMAP.md\nguardrails: off\n",
                                             encoding="utf-8")
        self.tool("u1", "Write", {"file_path": str(self.main / "knowledge" / "02-spec-o.md")}, self.t0 + 100, self.t0 + 101)
        self.touch(self.main / "knowledge" / "02-spec-o.md", "# o\n", self.t0 + 100.5)
        self.assertIsNone(self.check())
        self.assertIsNone(turn_check.check({"stop_hook_active": True}, self.state))

    def test_the_script_reads_stdin_and_prints_the_decision(self):
        self.tool("u1", "Write", {"file_path": str(self.main / "knowledge" / "02-spec-fresh.md")}, time.time() - 5,
                  time.time() - 4)
        (self.main / "knowledge" / "02-spec-fresh.md").write_text("# fresh\n", encoding="utf-8")
        env = {**os.environ, "DOC_GUARDRAILS_STATE": str(self.state), "CLOSEOUT_CONFIG_DIRS": str(self.cfg)}
        payload = {"session_id": "s1", "cwd": str(self.main), "stop_hook_active": False,
                   "transcript_path": str(self.transcript)}
        r = subprocess.run([sys.executable, str(HOOK)], input=json.dumps(payload), capture_output=True, encoding="utf-8",
                           env=env, cwd=self.main, timeout=60)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(json.loads(r.stdout)["decision"], "block")
        r = subprocess.run([sys.executable, str(HOOK)], input="not json", capture_output=True, encoding="utf-8", env=env)
        self.assertEqual((r.returncode, r.stdout), (0, ""))  # a malformed payload never breaks the session


class Performance(Session):
    def test_tokens_are_the_whole_text_scan_split_by_token(self):
        """tokens() splits before matching; it names exactly what a scan of the whole text with the two patterns
        names (the reference below), over random text built from the characters that matter."""
        import random

        def reference(text):
            out = {m.group(0) for m in turn_check.PATH_RE.finditer(text)}
            out.update(m.group(0) for m in turn_check.MD_TOKEN_RE.finditer(text) if m.group(0) and m.group(0) != ".md")
            return out
        rng = random.Random(24)
        alphabet = list("ab/~:.\\ C=,{}()'\"`;|&<>\n\t") + ["md", ".md", "C:\\", "\\\\", "~/", "x.md "]
        for _ in range(4000):
            text = "".join(rng.choice(alphabet) for _ in range(rng.randint(0, 40)))
            self.assertEqual(turn_check.tokens(text), reference(text), repr(text))
        for text in ("cat /r/src/f.py # " + "x" * 50, "--file=/abs/x.md", "see notes.md, then C:\\repo\\a.md",
                     r"\\box\share\d.md", "{a.md}", "=b.md", "a.md.md", ".md"):
            self.assertEqual(turn_check.tokens(text), reference(text), text)

    def test_performance_80mb_transcript_5000_paths_3_repos(self):
        repos = [self.main, self.lane, self.repo("third")]
        filler = "x" * 17000
        with open(self.transcript, "w", encoding="utf-8") as fh:
            stamp = ts(self.t0 + 100)
            for i in range(5000):
                root = repos[i % 3]
                fh.write(json.dumps({"type": "assistant", "timestamp": stamp, "message": {"content": [
                    {"type": "tool_use", "id": f"u{i}", "name": "Bash",
                     "input": {"command": f"cat {root}/src/f{i}.py # {filler}"}}]}}) + "\n")
                fh.write(json.dumps({"type": "user", "timestamp": stamp, "message": {"content": [
                    {"type": "tool_result", "tool_use_id": f"u{i}", "content": "ok"}]}}) + "\n")
        self.assertGreater(self.transcript.stat().st_size, 80 * 1024 * 1024)
        calls = []
        real = turn_check.git

        def spy(root, *args):
            calls.append(args[0])
            return real(root, *args)
        with mock.patch.object(turn_check, "git", spy):
            start = cpu_seconds()
            self.check()
            first = cpu_seconds() - start
            calls_first = list(calls)
            start = cpu_seconds()
            self.check(now=self.t0 + 510)
            second = cpu_seconds() - start
        self.assertLess(first, 5.0)
        self.assertEqual(calls_first, ["status"] * 3)
        self.assertLess(second, 0.5)


if __name__ == "__main__":
    unittest.main()
