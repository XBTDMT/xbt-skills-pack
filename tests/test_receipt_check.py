"""Tests for eli5/receipt_check.py, the live receipt check (a Stop hook): with eli5 on, a turn that ran tools and ends
without the receipt block is held once; a Greenman line citing a decision id with no record is held once in a project
listed as held in the pack's receipt_ids.json, and only reported elsewhere. Synthetic transcripts and projects in a temp
folder. Run: python3 -m unittest discover -s tests"""
import git_quiet  # noqa: F401  (first: git's background upkeep off for every fixture; tests/git_quiet.py)
import json
import os
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ELI5 = Path(__file__).resolve().parent.parent / "skills" / "eli5"
HOOK = ELI5 / "receipt_check.py"
sys.path.insert(0, str(ELI5))
sys.path.insert(0, str(ELI5.parent / "closeout"))
import closeout  # noqa: E402
import receipt_check  # noqa: E402
from cpu_clock import cpu_seconds  # noqa: E402  (the speed tests' clock: tests/cpu_clock.py)

PREFIX = "Base directory for this skill: "


def block(greenman="NA"):
    return ("ELI5 v5\n- Did: ran the tests\n- Why: the loader dropped rows\n- Result: worked\n"
            "- Proof: 4 passed\n- Files: shelf/load.py\n\n- Redman: NA\n- Greenman: " + greenman + "\n\n"
            "- Next: R-4\n- Need from you: none\n\n- Goal: R-3 · loader — rows survive · goal changed: no\n"
            "- In one sentence: The loader keeps every row now.\n")


def text(t):
    return {"type": "assistant", "message": {"role": "assistant", "content": [{"type": "text", "text": t}]}}


def bash(cmd="python3 -m unittest"):
    return [{"type": "assistant", "message": {"role": "assistant", "content": [
                {"type": "tool_use", "id": "b1", "name": "Bash", "input": {"command": cmd}}]}},
            {"type": "user", "message": {"role": "user", "content": [
                {"type": "tool_result", "tool_use_id": "b1", "content": "OK"}]}, "toolUseResult": {"stdout": "OK"}}]


def prompt(t):
    return {"type": "user", "message": {"role": "user", "content": t}}


def peer(t="CLEAR to run the suite."):
    """A message from another Claude session, as Claude Code writes it (seen live 2026-09-30): a meta user record that
    starts a turn, with the origin of its prompt."""
    return {"type": "user", "isMeta": True, "origin": {"kind": "peer", "from": "uds:/tmp/cc-socks/1.sock",
                                                       "name": "asset auditor (2)"},
            "promptSource": "system", "turnOrigin": "peer", "promptId": "p-peer",
            "message": {"role": "user", "content": "Another Claude session sent a message:\n<cross-session-message "
                        f"from=\"uds:/tmp/cc-socks/1.sock\" from-name=\"asset auditor (2)\">\n{t}\n"
                        "</cross-session-message>"}}


def idle_notice():
    """The cross-session idle notice: meta, no origin, but a prompt source; it starts a turn too."""
    return {"type": "user", "isMeta": True, "promptSource": "system", "turnOrigin": "system", "promptId": "p-idle",
            "message": {"role": "user", "content": "[Cross-session idle notice] \"main\" is idle now."}}


def skill_load(folder):
    """A skill's text as the Skill tool injects it mid-turn: a meta user record with no prompt origin."""
    return {"type": "user", "isMeta": True, "message": {"role": "user", "content": [
        {"type": "text", "text": f"{PREFIX}{folder}\n\n# /x\n"}]}}


class Fixture(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(os.path.realpath(tmp.name))
        self.eli5 = self.tmp / "skills" / "eli5"
        self.eli5.mkdir(parents=True)
        (self.eli5 / "SKILL.md").write_text("---\nname: eli5\n---\n\n# /eli5\n\n```\nELI5 v5\n- Did:\n```\n",
                                            encoding="utf-8")
        self.proj = self.tmp / "proj"
        (self.proj / "knowledge" / "decisions").mkdir(parents=True)
        (self.proj / ".git").mkdir()
        (self.proj / "knowledge" / "decisions" / "LEDGER.md").write_text(
            "# Greenman ledger\n\n| date | id | the choice | depth | status |\n|---|---|---|---|---|\n"
            "| 2026-09-14 | G-1 | a small call | S | decided |\n", encoding="utf-8")
        self.ids = self.tmp / "pack" / "tools" / "receipt_ids.json"
        self.n = 0
        env = mock.patch.dict(os.environ, {"DOC_GUARDRAILS_STATE": str(self.tmp / "state")})   # never the live folder
        env.start()
        self.addCleanup(env.stop)

    def held(self, *roots):
        self.ids.parent.mkdir(parents=True, exist_ok=True)
        self.ids.write_text(json.dumps({"held": [{"root": str(r), "since": "2026-09-22T21:24:01-07:00", "ids": 1,
                                                  "checked": "2026-09-25"} for r in roots]}), encoding="utf-8")

    def transcript(self, *records, eli5=True):
        self.n += 1
        path = self.tmp / f"t{self.n}.jsonl"
        flat = [prompt("/eli5"), {"type": "user", "isMeta": True, "message": {"role": "user", "content": [
            {"type": "text", "text": f"{PREFIX}{self.eli5}\n\n# /eli5\n"}]}}, text("Receipts are on.")] if eli5 else []
        for r in records:
            flat.extend(r if isinstance(r, list) else [r])
        path.write_text("".join(json.dumps(r) + "\n" for r in flat), encoding="utf-8")
        return path

    def check(self, transcript, active=False, cwd=None, last=None):
        payload = {"hook_event_name": "Stop", "session_id": "s1", "transcript_path": str(transcript),
                   "stop_hook_active": active, "cwd": str(cwd or self.proj)}
        if last is not None:
            payload["last_assistant_message"] = last
        out = receipt_check.check(payload, ids_file=self.ids)
        return json.loads(out) if out else None


class MissingBlock(Fixture):
    def test_missing_receipt_blocks_once(self):
        """Known-bad for a hook that lets a receipt-less tool-using turn end."""
        t = self.transcript(prompt("fix the loader"), bash(), text("Fixed it."))
        d = self.check(t)
        self.assertEqual(d["decision"], "block")
        self.assertIn("no ELI5 v5 block", d["reason"])
        self.assertIn(str(self.eli5 / "SKILL.md"), d["reason"])
        self.assertIsNone(self.check(t, active=True))

    def test_receipt_present_silent(self):
        self.assertIsNone(self.check(self.transcript(prompt("fix"), bash(), text("Fixed.\n\n" + block()))))

    def test_a_block_that_breaks_its_shape_is_held_once_naming_each_error(self):
        """Known-bad for a hook that only looks for the title (FULL 20261009-124414, full_flow on Fable: `Result:
        worked` with a Fail line for a refused memory write, caught only offline by the evals' receipt check)."""
        fail = block().replace("- Proof: 4 passed\n", "- Proof: 4 passed\n- Fail: the memory write was refused\n")
        d = self.check(self.transcript(prompt("update the docs"), bash(), text("Done.\n\n" + fail)))
        self.assertEqual(d["decision"], "block")
        self.assertIn("Fail is present while Result is worked", d["reason"])
        self.assertIn("unclear (part done) or failed", d["reason"])            # answered from the facts
        self.assertIn(str(self.eli5 / "SKILL.md"), d["reason"])
        self.assertIsNone(self.check(self.transcript(prompt("update the docs"), bash(), text("Done.\n\n" + fail)),
                                     active=True))                              # once: the continuation is not held
        unclear = fail.replace("- Result: worked", "- Result: unclear")
        self.assertIsNone(self.check(self.transcript(prompt("update the docs"), bash(), text("Done.\n\n" + unclear))))
        # every error is named, and only the Fail hint where it applies
        bad = block().replace("- Result: worked", "- Result: mostly").replace("- In one sentence: The loader keeps every row now.\n", "")
        d = self.check(self.transcript(prompt("fix"), bash(), text("Fixed.\n\n" + bad)))
        self.assertIn("Result is 'mostly'", d["reason"])
        self.assertIn("In one sentence is missing", d["reason"])
        self.assertNotIn("part done", d["reason"])

    def test_a_shape_hold_also_carries_the_id_check(self):
        """One stop, one hold: a malformed block that also cites an unrecorded id names both (the continuation is not
        checked again)."""
        self.held(self.proj)
        bad = block("G-9 (M): awaiting you — which store?").replace("- Proof: 4 passed\n", "- Proof: \n")
        d = self.check(self.transcript(prompt("fix"), bash(), text("Fixed.\n\n" + bad)))
        self.assertIn("Proof is empty", d["reason"])
        self.assertIn("G-9", d["reason"])

    def test_the_stop_inputs_last_message_is_read_before_the_transcript(self):
        """The Stop hook can run before the turn's final message is in the transcript file (seen live 2026-09-29: the
        message 110 ms before the hook); Claude Code passes it as `last_assistant_message`, which is read first."""
        t = self.transcript(prompt("fix"), bash(), text("Working on it."))          # the final message not yet written
        self.assertIsNone(self.check(t, last="Fixed.\n\n" + block()))
        d = self.check(t, last="Fixed.")                                           # the final message truly lacks it
        self.assertEqual(d["decision"], "block")

    def test_without_the_field_the_transcript_is_read_as_before(self):
        self.assertIsNone(self.check(self.transcript(prompt("fix"), bash(), text("Fixed.\n\n" + block()))))
        self.assertEqual(self.check(self.transcript(prompt("fix"), bash(), text("Fixed.")))["decision"], "block")

    def test_an_older_block_is_not_the_current_one(self):
        d = self.check(self.transcript(prompt("fix"), bash(), text("Fixed.\n\n" + block().replace("ELI5 v5", "ELI5 v4"))))
        self.assertEqual(d["decision"], "block")

    def test_no_tools_silent(self):
        self.assertIsNone(self.check(self.transcript(prompt("what is R-3?"), text("R-3 is the loader."))))

    def test_a_peer_message_only_acknowledged_is_silent(self):
        """Known-bad for a reader that takes a peer session's message (a meta record) for part of the previous turn:
        live 2026-09-29, two acknowledgements with no tool call were held "this turn ran tools" because the reply
        before them had run Bash. A peer message starts its own turn; the cross-session idle notice too."""
        for notice in (peer(), idle_notice()):
            t = self.transcript(prompt("fix"), bash(), text("Fixed.\n\n" + block()), notice, text("Noted, holding."))
            self.assertFalse(closeout.turn_facts(t)["ran_tools"])
            self.assertEqual(closeout.turn_facts(t)["last_text"], "Noted, holding.")
            self.assertIsNone(self.check(t))
            self.assertIsNone(self.check(t, last="Noted, holding."))

    def test_a_peer_message_acted_on_is_still_held(self):
        """The other half: a turn started by a peer message that runs a tool and ends with no block is held."""
        for notice in (peer(), idle_notice()):
            t = self.transcript(prompt("fix"), bash(), text("Fixed.\n\n" + block()), notice, bash("git log -1"),
                                text("Their commit is in."))
            self.assertTrue(closeout.turn_facts(t)["ran_tools"])
            self.assertEqual(self.check(t)["decision"], "block")

    def test_a_skill_load_inside_a_turn_does_not_end_it(self):
        """A meta record with no prompt origin (the Skill tool's injected text) is not a turn start: the tool call before
        it still counts."""
        t = self.transcript(prompt("close out"), bash(), skill_load(self.tmp / "skills" / "closeout"), text("Done."))
        self.assertTrue(closeout.turn_facts(t)["ran_tools"])
        self.assertEqual(self.check(t)["decision"], "block")

    def test_eli5_not_loaded_silent(self):
        self.assertIsNone(self.check(self.transcript(prompt("fix"), bash(), text("Fixed."), eli5=False)))

    def test_a_turn_that_crossed_a_compaction_is_still_held(self):
        """A real prompt, a Bash call, then an automatic compaction with no re-injection and its summary record (a
        user record, not isMeta), then a final text with no block: still a tool-using turn, held once, the reason naming
        the absolute eli5/SKILL.md. Known-bad: a reader that takes the summary for the last prompt sees no tool."""
        t = self.transcript(prompt("fix the loader"), bash(),
                            {"type": "system", "subtype": "compact_boundary", "content": "Conversation compacted"},
                            {"type": "user", "isCompactSummary": True, "message": {"role": "user", "content":
                             "This session is being continued from a previous conversation."}},
                            text("Fixed it."))
        facts = closeout.turn_facts(t)
        self.assertTrue(facts["ran_tools"])
        self.assertEqual(facts["last_text"], "Fixed it.")
        d = self.check(t)
        self.assertEqual(d["decision"], "block")
        self.assertIn(str(self.eli5 / "SKILL.md"), d["reason"])

    def test_the_continuation_after_two_blocks_is_silent(self):
        """Stop hooks run in parallel: when another hook blocked the same stop too, the continuation's stop arrives
        with stop_hook_active true, and this hook prints nothing whatever the other did."""
        t = self.transcript(prompt("fix"), bash(), text("Fixed."))
        self.assertIsNone(self.check(t, active=True))

    def test_the_hook_as_a_process(self):
        t = self.transcript(prompt("fix"), bash(), text("Fixed."))
        r = subprocess.run([sys.executable, str(HOOK)], capture_output=True, encoding="utf-8", timeout=60,
                           input=json.dumps({"hook_event_name": "Stop", "transcript_path": str(t),
                                             "stop_hook_active": False, "cwd": str(self.proj)}))
        self.assertEqual((r.returncode, json.loads(r.stdout)["decision"]), (0, "block"))
        r = subprocess.run([sys.executable, str(HOOK)], capture_output=True, encoding="utf-8", timeout=60,
                           input="not json")
        self.assertEqual((r.returncode, r.stdout), (0, ""))                   # an internal error: never a block
        self.assertIn("receipt check failed", r.stderr)


class DecisionIds(Fixture):
    def cites(self, gid):
        return self.transcript(prompt("decide"), bash(),
                               text("Done.\n\n" + block(f"{gid} (M): csv over sqlite → knowledge/decisions/{gid}-x.md")))

    def test_unrecorded_g_id_blocks_once(self):
        """Known-bad: a held project whose ledger has G-1 only; the receipt cites G-2."""
        self.held(self.proj)
        d = self.check(self.cites("G-2"))
        self.assertEqual(d["decision"], "block")
        self.assertIn("G-2", d["reason"])
        (self.proj / "knowledge" / "decisions" / "G-2-x.md").write_text("# G-2\n", encoding="utf-8")
        self.assertIsNone(self.check(self.cites("G-2")))
        (self.proj / "knowledge" / "decisions" / "G-2-x.md").unlink()
        with open(self.proj / "knowledge" / "decisions" / "LEDGER.md", "a", encoding="utf-8") as fh:
            fh.write("| 2026-09-15 | G-2 | another call | S | decided |\n")
        self.assertIsNone(self.check(self.cites("G-2")))
        self.assertIsNone(self.check(self.cites("G-9"), active=True))

    def test_a_lane_qualified_id_resolves_through_the_lane_map(self):
        self.held(self.proj)
        (self.proj / "CLAUDE.md").write_text(
            "# p\n\n## Doc map\nroadmap: knowledge/00-ROADMAP.md\n\n## Doc map: lane audit\n"
            "roadmap: knowledge/audit/AUDIT-ROADMAP.md\nid form: table rows | A-<n> |\n"
            "decisions: knowledge/audit/greenman\ndecision ids: G-A<n>\n", encoding="utf-8")
        (self.proj / "knowledge" / "audit" / "greenman").mkdir(parents=True)
        self.assertEqual(self.check(self.cites("G-A3"))["decision"], "block")
        (self.proj / "knowledge" / "audit" / "greenman" / "G-A3-lane.md").write_text("# G-A3\n", encoding="utf-8")
        self.assertIsNone(self.check(self.cites("G-A3")))

    def test_the_record_path_the_line_names_resolves_the_id(self):
        """Known-bad for a check that ignores the `→ <path>` eli5 prescribes (live 2026-09-30 on G-44): the record
        sits uncommitted in a build worktree outside the Stop's checkout, and the line names its absolute path. A
        readable `G-<n>-*.md` there resolves that id; a path that is missing, unreadable, another id's record (G-4
        is not G-44) or not a record keeps the hold; an id with no record anywhere still holds the turn."""
        self.held(self.proj)
        build = self.tmp / "proj-build" / "knowledge" / "decisions"
        build.mkdir(parents=True)
        rec = build / "G-44-flickering-builtin-skill.md"
        rec.write_text("# G-44\n", encoding="utf-8")

        def line(*parts):
            return self.transcript(prompt("decide"), bash(), text("Done.\n\n" + block(" · ".join(parts))))
        self.assertIsNone(self.check(line(f"G-44 (L): awaiting you — keep it? → {rec}")))
        self.assertIsNone(self.check(line(f"G-44 (M): a over b → `{rec}`")))               # a code span around it
        d = self.check(line(f"G-44 (M): a over b → {rec} · G-45 (M): c over d → {build / 'G-45-x.md'}"))
        self.assertEqual(d["decision"], "block")                                         # G-45's file is not there
        self.assertIn("G-45", d["reason"])
        self.assertNotIn("G-44", d["reason"])
        self.assertEqual(self.check(line(f"G-4 (M): a over b → {rec}"))["decision"], "block")     # another id's file
        other = build / "notes.md"
        other.write_text("# not a record\n", encoding="utf-8")
        self.assertEqual(self.check(line(f"G-44 (M): a over b → {other}"))["decision"], "block")
        self.assertEqual(self.check(line("G-44 (M): a over b"))["decision"], "block")         # no path, no record
        self.assertEqual(self.check(line("G-44 (M): a over b → knowledge/decisions/G-44-x.md"))["decision"], "block")
        (build / "G-46-dir.md").mkdir()                                                    # not a readable file
        self.assertEqual(self.check(line(f"G-46 (M): a over b → {build / 'G-46-dir.md'}"))["decision"], "block")
        if hasattr(os, "geteuid") and os.geteuid() != 0:
            locked = build / "G-47-locked.md"
            locked.write_text("# G-47\n", encoding="utf-8")
            locked.chmod(0)
            self.addCleanup(locked.chmod, 0o644)
            self.assertEqual(self.check(line(f"G-47 (M): a over b → {locked}"))["decision"], "block")
        rec.unlink()
        self.assertEqual(self.check(line(f"G-44 (M): a over b → {rec}"))["decision"], "block")    # gone: held again

    def test_the_named_record_is_one_function_for_every_reader(self):
        """The evals' receipt check reads the same `→ <path>`: closeout.receipt_named_records, one function."""
        rec = self.tmp / "elsewhere" / "G-9-x.md"
        rec.parent.mkdir()
        rec.write_text("# G-9\n", encoding="utf-8")
        b = closeout.receipt_facts(block(f"G-9 (M): a over b → {rec} · G-8 (M): c → /nowhere/G-8-x.md"))[0]
        self.assertEqual(closeout.receipt_named_records(b), {"G-9": str(rec)})

    def test_two_unrecorded_ids_one_block_naming_both(self):
        self.held(self.proj)
        t = self.transcript(prompt("decide"), bash(), text("Done.\n\n" + block("G-7 (M): a over b → x · G-8 (M): c")))
        d = self.check(t)
        self.assertIn("G-7", d["reason"])
        self.assertIn("G-8", d["reason"])

    def test_id_lookup_is_fast_and_shared(self):
        self.held(self.proj)
        d = self.proj / "knowledge" / "decisions"
        for i in range(10, 510):
            (d / f"G-{i}-r.md").write_text(f"# G-{i}\n", encoding="utf-8")
        with open(d / "LEDGER.md", "a", encoding="utf-8") as fh:
            fh.writelines(f"| 2026-09-15 | G-{i} | call {i} | S | decided |\n" for i in range(600, 1100))
        start = cpu_seconds()
        self.assertEqual(self.check(self.cites("G-2000"))["decision"], "block")
        self.assertLess(cpu_seconds() - start, 1.0)
        self.assertIsNone(self.check(self.cites("G-1099")))
        # the eval's receipt check makes the same lookup: one function, so the two cannot disagree (a copy without
        # the evals, the kit, has only the one side)
        evals_dir = Path(__file__).resolve().parent.parent / "evals"
        if (evals_dir / "run.py").is_file():
            sys.path.insert(0, str(evals_dir))
            import run as evals
            self.assertEqual(evals.receipt_decision_ids(self.proj), closeout.decision_ids(self.proj))

    def test_unheld_project_reports_only(self):
        """Known-bad for a block where the precondition has not held: no file, an unreadable one, or one listing
        another root → a report (systemMessage), never a decision; the project's root listed → a block, from a
        subfolder too (the one root rule); a worktree is its own root."""
        t = self.cites("G-2")
        for state in ("absent", "unreadable", "another"):
            if state == "unreadable":
                self.ids.parent.mkdir(parents=True, exist_ok=True)
                self.ids.write_text("{not json", encoding="utf-8")
            elif state == "another":
                self.held(self.tmp / "elsewhere")
            d = self.check(t)
            self.assertNotIn("decision", d, state)
            self.assertIn("G-2", d["systemMessage"], state)
            self.assertIn("reports only in this project", d["systemMessage"])
        self.held(self.proj)
        self.assertEqual(self.check(t)["decision"], "block")
        (self.proj / "shelf").mkdir()
        self.assertEqual(self.check(t, cwd=self.proj / "shelf")["decision"], "block")
        wt = self.tmp / "proj-wt"
        wt.mkdir()
        (wt / ".git").write_text(f"gitdir: {self.proj}/.git/worktrees/proj-wt\n", encoding="utf-8")
        self.assertEqual(closeout.cwd_root(wt), str(wt))
        self.assertNotIn("decision", self.check(t, cwd=wt))
        for d in (self.check(t), self.check(t, cwd=wt)):
            words = d.get("reason") or d.get("systemMessage")
            self.assertIn("may live outside", words)
            for bad in ("write the record", "write a record", "greenman step 9"):
                self.assertNotIn(bad, words.lower())

    def test_the_shipped_hook_has_no_precondition_mode(self):
        source = HOOK.read_text(encoding="utf-8")
        self.assertNotIn("--" + "precondition", source)          # split, so no shipped file holds the old option
        self.assertIsNone(re.search(r"^\s*(?:from|import)\s+(?:tools\b|receipt_ids\b)", source, re.M))


LANE_MAP = ("# p\n\n## Doc map\nroadmap: knowledge/00-ROADMAP.md\n\n## Doc map: lane audit\n"
            "roadmap: knowledge/audit/AUDIT-ROADMAP.md\nid form: table rows | A-<n> |\nbranch: audit-*\n"
            "decisions: knowledge/audit/greenman\ndecision ids: G-A<n>\n")


def sh(cwd, *cmd):
    r = subprocess.run(list(cmd), cwd=str(cwd), capture_output=True, encoding="utf-8",
                       env=dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@t", GIT_COMMITTER_NAME="t",
                                GIT_COMMITTER_EMAIL="t@t"))
    if r.returncode:
        raise AssertionError(f"{cmd}: {r.stderr}")
    return r.stdout


def snapshot(*roots):
    """Every file under the roots, .git included, with its size and modification time: what 'writes nothing' means."""
    out = {}
    for root in roots:
        for p in Path(root).rglob("*"):
            if p.is_file():
                st = p.stat()
                out[str(p)] = (st.st_size, st.st_mtime_ns)
    return out


class BranchIds(Fixture):
    """An id the working tree does not record is looked for on every local branch, in the decisions folders
    the doc map declares: record names by `git ls-tree`, ledger rows by `git show`; read-only git, cached by commit in
    the state folder, nothing written into the project."""

    def repo(self):
        main = self.tmp / "main"
        (main / "knowledge" / "decisions").mkdir(parents=True)
        (main / "knowledge" / "audit" / "greenman").mkdir(parents=True)
        (main / "CLAUDE.md").write_text(LANE_MAP, encoding="utf-8")
        (main / "knowledge" / "decisions" / "LEDGER.md").write_text(
            "| id | date | decision | status |\n|---|---|---|---|\n| G-1 | d | a call | decided |\n", encoding="utf-8")
        (main / "knowledge" / "audit" / "greenman" / ".keep").write_text("", encoding="utf-8")
        sh(main, "git", "init", "-q", "-b", "main")
        sh(main, "git", "config", "gc.auto", "0")                  # the fixture's own commits must not start git's
        sh(main, "git", "config", "maintenance.auto", "false")     # background upkeep, which races the snapshot below
        sh(main, "git", "add", "-A")
        sh(main, "git", "commit", "-qm", "base")
        lane = self.tmp / "lane"
        sh(main, "git", "worktree", "add", "-q", "-b", "audit-as-03", str(lane))
        (lane / "knowledge" / "audit" / "greenman" / "G-A64-a-lane-call.md").write_text("# G-A64\n", encoding="utf-8")
        sh(lane, "git", "add", "-A")
        sh(lane, "git", "commit", "-qm", "lane record")
        side = self.tmp / "side"
        sh(main, "git", "worktree", "add", "-q", "-b", "side", str(side))
        with open(side / "knowledge" / "decisions" / "LEDGER.md", "a", encoding="utf-8") as fh:
            fh.write("| G-77 | d | a ledger-only call | decided |\n")
        sh(side, "git", "add", "-A")
        sh(side, "git", "commit", "-qm", "side row")
        return main, lane, side

    def cites_in(self, gids):
        return self.transcript(prompt("decide"), bash(), text("Done.\n\n" + block(" · ".join(
            f"{g} (M): a over b → x" for g in gids))))

    def test_a_record_on_another_branch_resolves(self):
        """Known-bad for the working-tree-only lookup: the lane's G-A64 exists only on its branch, the side branch's
        G-77 only as a ledger row there; from main's checkout both resolve, an id on no branch still holds the turn."""
        main, lane, side = self.repo()
        self.assertFalse({"G-A64", "G-77"} & closeout.decision_ids(main))
        self.assertEqual(closeout.resolved_decision_ids(main, ["G-1", "G-A64", "G-77", "G-99"]), {"G-1", "G-A64", "G-77"})
        self.held(main)
        self.assertIsNone(self.check(self.cites_in(["G-A64", "G-77"]), cwd=main))
        d = self.check(self.cites_in(["G-A64", "G-99"]), cwd=main)
        self.assertEqual(d["decision"], "block")
        self.assertIn("G-99", d["reason"])
        self.assertNotIn("G-A64", d["reason"])
        self.assertIn("in this checkout or on any local branch", d["reason"])
        self.assertIsNone(self.check(self.cites_in(["G-A64"]), cwd=side))       # from any worktree of the repository

    def test_read_only_and_cached_by_commit(self):
        main, lane, side = self.repo()
        before = snapshot(main, lane, side)
        calls, real = [], subprocess.run

        def run(cmd, *a, **kw):
            if cmd and cmd[0] == "git":
                calls.append((cmd, (kw.get("env") or {}).get("GIT_OPTIONAL_LOCKS")))
            return real(cmd, *a, **kw)
        state = self.tmp / "state"
        with mock.patch.object(closeout.subprocess, "run", run):
            self.assertEqual(closeout.resolved_decision_ids(main, ["G-A64"], cache=state / "decision-ids"), {"G-A64"})
            # the branch lookup's calls (the working tree's own lookup asks git only for the top, as before)
            branch = [(c, lock) for c, lock in calls if c[3:5] != ["rev-parse", "--show-toplevel"] or len(c) > 5]
            first = [c for c, _ in branch]
            self.assertTrue(branch and all(lock == "0" for _, lock in branch), branch)
            self.assertTrue(any("ls-tree" in c for c in first) and any("show" in c for c in first), first)
            self.assertTrue(all(c[3] in ("rev-parse", "for-each-ref", "ls-tree", "show") for c in first), first)
            calls.clear()
            closeout._BRANCH_IDS.clear()                                        # a new process: only the file is left
            self.assertEqual(closeout.resolved_decision_ids(main, ["G-A64"], cache=state / "decision-ids"), {"G-A64"})
            self.assertFalse([c for c, _ in calls if "ls-tree" in c or "show" in c], calls)   # same heads: the cache
        self.assertEqual(len(list((state / "decision-ids").iterdir())), 1)
        self.assertEqual(snapshot(main, lane, side), before)                   # nothing written into the project
        # a branch that moves is read again at its new head
        (lane / "knowledge" / "audit" / "greenman" / "G-A65-next.md").write_text("# G-A65\n", encoding="utf-8")
        sh(lane, "git", "add", "-A")
        sh(lane, "git", "commit", "-qm", "another")
        self.assertEqual(closeout.resolved_decision_ids(main, ["G-A65"], cache=state / "decision-ids"), {"G-A65"})

    def test_outside_a_repository_nothing_is_read(self):
        (self.proj / ".git").rmdir()
        self.assertEqual(closeout.branch_decision_ids(self.proj), set())
        self.assertEqual(closeout.resolved_decision_ids(self.proj, ["G-1", "G-5"]), {"G-1"})

    def test_many_worktrees_and_branches_well_inside_the_budget(self):
        """Nine worktrees and 25 branches, each at its own commit: the first lookup (no cache) is well inside the Stop
        hook's 10 s timeout."""
        main, lane, side = self.repo()
        for i in range(22):
            sh(main, "git", "branch", f"b{i}", "main")
            sh(main, "git", "checkout", "-q", f"b{i}")
            (main / "knowledge" / "decisions" / f"G-{100 + i}-branch-call.md").write_text("# r\n", encoding="utf-8")
            sh(main, "git", "add", "-A")
            sh(main, "git", "commit", "-qm", f"b{i}")
        sh(main, "git", "checkout", "-q", "main")
        for i in range(6):
            sh(main, "git", "worktree", "add", "-q", str(self.tmp / f"wt{i}"), f"b{i}")
        worktrees = sh(main, "git", "worktree", "list", "--porcelain").count("worktree ")
        branches = len(sh(main, "git", "for-each-ref", "refs/heads/").splitlines())
        self.assertEqual((worktrees, branches), (9, 25))
        self.held(main)
        start = cpu_seconds()
        d = self.check(self.cites_in(["G-121", "G-A64", "G-404"]), cwd=main)
        took = cpu_seconds() - start
        self.assertEqual(d["decision"], "block")
        self.assertIn("G-404", d["reason"])
        self.assertNotIn("G-121", d["reason"])
        self.assertLess(took, 3.0)


class RootRule(unittest.TestCase):
    def test_cwd_root_walks_up_to_the_first_git_folder(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(os.path.realpath(tmp))
            (root / "a" / ".git").mkdir(parents=True)
            (root / "a" / "b" / "c").mkdir(parents=True)
            self.assertEqual(closeout.cwd_root(root / "a" / "b" / "c"), str(root / "a"))
            self.assertEqual(closeout.cwd_root(root / "x"), str(root / "x"))              # no .git above: itself
            os.symlink(root / "a" / "b", root / "link")
            self.assertEqual(closeout.cwd_root(root / "link"), str(root / "a"))           # the realpath first


if __name__ == "__main__":
    unittest.main()
