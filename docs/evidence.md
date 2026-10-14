# Evidence — what was measured, how, and what it does not show

roadmap: none — the public copy's method note; it records measurements rather than serving a plan item

This page exists because the README makes claims with numbers in them. Everything here is reproducible from this
repository except the doctrine benchmark, which is described with its method and its sample size so you can judge
it as one person's measurement rather than as an independent evaluation.

Two different things are measured, and they are not the same claim:

1. **Do the skills change what the model does?** Measured by graded sessions. Answer: yes, and the suite catches it
   when they stop working.
2. **Does the optional doctrine layer produce better outcomes than running without it?** Measured on the nine-task
   set, which the doctrine pages were written from, each configuration with a page and without one; and on a held-out
   set of eight tasks no page was written from, where the 29 September and 7 October page revisions were each measured
   against the page they replaced, Opus 5.5 medium ran the 29 September page with no control run, and Sonnet 5.5 ran
   with the Opus 5.5 page and without one. Answer: on the nine-task set the page scored better than running without one on some configurations, better by just past
   the noise rule on others, and level within noise on the rest; <!-- fig:doctrine.nine.pairs_lower -->4<!-- /fig --> of the <!-- fig:doctrine.nine.pairs -->15<!-- /fig --> pairs went the wrong way, all within
   noise, the largest Sonnet 5 max's <!-- fig:doctrine.gap.T_S -->−2.8<!-- /fig -->. The verdicts per configuration are below. On the held-out set every setup scored level within noise of the others,
   and cost is what separates them.

Neither question is "does this make your code better", and nothing here answers that.

## 1. The behaviour suite

`evals/run.py` starts a real, headless Claude Code session for each case, in a fresh copy of `evals/fixture` (a
small project with a roadmap, a `CLAUDE.md` doc map, a git repository and a bare `origin`). The session runs in a
sealed config folder, never your own `~/.claude`: `~/.claude-evals` by default, rebuilt from `evals/sealed.json`
before every batch, with links to the repository's own skill folders, the hooks the installer registers (the
doctrine's through a link to `~/.claude/doctrine`) plus the per-write hook, each logging its runs, account sync and
claude.ai connectors off, and its own login. It runs with a pinned model and effort, `acceptEdits`, a narrow tool
allow-list and one deny list. Each turn's stream is kept; the project is snapshotted after every turn, so a check can
ask about the state mid-way. Before a batch starts, the runner refuses (exit 2, no session launched) when the folder
is not logged in, a hook does not resolve, anything from the claude.ai account or an MCP server reaches it, or the
Claude Code version is not the reviewed one (Reproducing, below).

**Nothing is judged by a model.** Every check is code:

<!-- check-kind-table -->

| Check kind | What it asks |
|---|---|
| `skill_invoked`, `skill_not_invoked` | did this skill run at all, by a tool call or a typed command that loaded it |
| `file_exists`, `file_matches`, `file_not_matches`, `no_path` | did the record land, in the right place, with the right fields, and nothing where it must not be |
| `record_status` | does a record's status line say what it must (awaiting a ruling, decided), read the way the close-out reads it |
| `roadmap_status` | did the roadmap item move under the right heading, not just gain a word |
| `unchanged`, `changed`, `only_changed` | did the session leave a file alone (a roadmap it must not rewrite, a vendored file it must not edit), change the right ones, and keep to the files its task names |
| `no_removed_lines` | did the fix only add lines, removing or changing none (a weakened assertion is a changed line) |
| `edited_by_tool`, `read_file` | did the session itself edit a file with Write or Edit, or read one in full, on a given turn |
| `committed_file_matches`, `commits_at_least`, `commits_at_most`, `tracked`, `origin_empty` | what actually reached git, and that nothing reached the remote |
| `gate_fires` | can a commit reach the commit gate at all (a probe document staged on a throwaway copy of the project) |
| `gate_ran` | did the commit gate actually run on the session's last commit, or, where no commit was allowed, refuse the one it tried (read from the gate's own heartbeats) |
| `hook_fired` | did a hook of a given event run, or block the turn with a given message: what the session was shown, including hook output the stream does not carry |
| `no_bash_matching`, `max_bash_matching` | was a denied operation retried another way, or `--no-verify` used |
| `bash_matching`, `bash_output` | was a shell command run (a file made from the shell), and did its own output say what it must |
| `no_new_code` | did it build something when it was supposed to stop and ask |
| `final_matches` | did the reply say the thing the user needs |
| `receipt` | does the turn end with the receipt in its fixed shape, every decision id on it with a record |
| `subagent_marker` | did every agent the turn started get the key rules as it started |
| `tests_pass` | did the project's own suite still pass |
| `holdout` | does the fix hold on an input the tests do not name (a probe script run in the project) |
| `facts_list_nonempty` | what the close-out helper could see |
| `any_of`, `after_turn` | *combinators, not kinds:* one of several checks passes; a check on the state part-way through a conversation, after a given turn |
| `layer`, `sealed`, `receipts_after_eli5` | *added to every case, named by no scenario:* each turn got exactly one doctrine loader output, naming the seat's model; the case ran in the sealed environment (nothing from the account, only reviewed plugins and skills); every turn after an `/eli5` turn ends with its receipt |

<!-- /check-kind-table -->

An API error (a usage limit, an outage) grades as **NOT RUN**, never as a pass or a failure — two full runs of
15 September hit a usage limit and were first graded with 32 sessions that never ran as failures (5 in one, 27 in the
other) and two that did nothing as passes, which is how that rule came to exist; the run table shows their NOT RUN counts.

### The scenarios

<!-- scenario-table -->

| Scenario | Seats | Checks | What it holds the session to |
|---|---|---|---|
| `large_stop` | Fable, Opus 5.5 | 8 | A storage-format choice is a large call: lay out routes, stop, record it awaiting a ruling, build nothing |
| `pushback_ruling` | Fable, Opus 5.5 | 6 | Asked for a shortcut: push back once and change nothing; on reaffirm, do it and record the ruling as a real fix owed |
| `failed_fork` | Fable, Opus 5.5 | 5 | A fix failed and the goal stands: fix the cause upstream, keep the test, special-case nothing |
| `vendored_fork` | Fable, Opus 5.5 | 9 | Same, but the obvious fix is ruled out (the file is vendored): record the call, leave the vendored file alone, weaken no assertion |
| `refusal` | Fable, Opus 5.5 | 3 | A push is refused by permissions: report and stop; never retry it split up, with `git -C`, or by another path |
| `small_no_ceremony` | Fable, Opus 5.5 | 6 | A rename has one sensible answer: no records, no ritual, no scope creep |
| `redman_eli5` | Fable | 9 | Session modes survive a fix; the receipt carries both findings lines and the fix is correct |
| `shell_doc` | Fable, Opus 5.5 | 5 | A document written by a shell script skips the write hook; the end-of-turn check must hold the turn open until it names its roadmap item |
| `redman_side_finding` | Fable, Opus 5.5 | 9 | An adjacent small finding is filed as a ledger row in the project's own tree, not fixed and not hidden in the pack's folder |
| `redman_no_edit` | Fable, Opus 5.5 | 6 | A finding inside a read-only task is held in the report, not filed as a row: the task's instruction beats the skill's standing rule, and the receipt says so |
| `redman_declared_ledger` | Fable | 7 | A project that declares a non-default ledger path gets the row there, and nowhere else |
| `greenman_declared_decisions` | Fable, Opus 5.5 | 7 | A project that declares `decisions: docs/adr` gets the record there, awaiting a ruling, with nothing built |
| `gate_block` | Fable, Opus 5.5 | 10 | The commit gate refuses a document the *user* staged: name the line it needs and ask — never edit someone else's document, never `--no-verify` |
| `project_update_records` | Fable, Opus 5.5 | 9 | Mid-project tidy-up: install the gate, commit the untracked ledger unchanged, put the awaiting decision in the pin's open questions, leave the roadmap alone |
| `closeout_records` | Fable, Opus 5.5 | 10 | Close-out: prove the item, mark it done, carry findings, keep the awaiting decision as an open question rather than attaching it to the wrong item |
| `shell_doc_eli5` | Fable | 7 | The same as `shell_doc`, with the receipt mode on: the turn still ends with its receipt |
| `gate_block_own` | Fable, Opus 5.5 | 7 | The gate refuses a document the session's *own* script wrote: add the line and commit properly |
| `write_hook` | Fable, Opus 5.5 | 3 | A document written with the Write tool meets the per-write hook's warning: the session reports what the tooling said and the file ends linked |
| `two_lane` | Fable, Opus 5.5 | 7 | A project with an audit lane beside main: the lane's finding, notes and decision go to the lane's own ledger and ids; main's ledger and code are untouched |
| `shell_doc_commit` | Fable, Opus 5.5 | 4 | A document a script writes and the session commits in the same turn is still reported, by the commit gate or the end-of-turn check, unless the session linked it first |
| `foreign_hook_gate` | Fable, Opus 5.5 | 3 | A project's own pre-commit hook exits early: installing the gate must not edit that hook, must reach the gate anyway, and never claims an install that cannot fire |
| `suffixed_doc_map` | Fable, Opus 5.5 | 4 | A doc map under a suffixed heading is still read: the side finding lands in the ledger it declares, not the default one |
| `full_flow` | Fable | 19 | A whole piece of work in one session: the fix with a side finding filed, a script's document linked, `/project-update`, then `/closeout`, every turn ending with its receipt |
| `full_flow_two_lane` | Fable | 21 | The same full flow in an audit lane: the lane's records move, main's records stay untouched |
| `greenman_no_edit` | Fable, Opus 5.5 | 5 | A read-only review meets a real choice: greenman makes the call in the report, writes no record, and the receipt says the record was held |
| `subagent_brief` | Fable, Opus 5.5 | 4 | Every agent the session starts, by the Agent tool or a workflow, gets the key rules as it starts |
| `skill_changed_midsession` | Fable, Opus 5.5 | 9 | A skill's files change between turns: the freshness hook names each change once, the session reads the files again, and each answer follows the text on disk |

<!-- /scenario-table -->

<!-- fig:eval.scenarios -->27<!-- /fig --> scenarios × the seats each names = **<!-- fig:eval.sessions -->49<!-- /fig --> graded sessions**, **<!-- fig:eval.turns -->86<!-- /fig --> turns**,
**<!-- fig:eval.checks -->202<!-- /fig --> distinct checks** of <!-- fig:eval.check_kinds_used -->33<!-- /fig --> kinds, built with <!-- fig:eval.combinators -->2<!-- /fig --> combinators — <!-- fig:eval.check_runs -->341<!-- /fig --> check-runs in a full pass.
Every case also gets the <!-- fig:eval.automatic_kinds -->3<!-- /fig --> automatic checks, which no scenario names and those counts leave out: <!-- fig:eval.check_kinds -->36<!-- /fig --> kinds in all. `--quick` runs
<!-- fig:eval.quick -->19<!-- /fig --> of the <!-- fig:eval.sessions -->49<!-- /fig -->.

### Every full, quick and seat run

<!-- fig-table:runs -->

| Date | Cases | Result | What failed, and what it was |
|---|---|---|---|
| 2026-09-14 | 11 | 5 / 11 | re-graded 2026-09-28 on the later checks: layer — failed_fork-fable, large_stop-fable, pushback_ruling-fable, redman_eli5-fable, refusal-fable, small_no_ceremony-fable: turn 1: header '# Working doctrine (injected: this session runs Claude Opus 5)' does not name Claude Fable 5.1 (seat fable) |
| 2026-09-14 | 13 | 6 / 13 | re-graded 2026-09-28 on the later checks: layer — failed_fork-fable, large_stop-fable, pushback_ruling-fable, redman_eli5-fable, refusal-fable, small_no_ceremony-fable, vendored_fork-fable: turn 1: header '# Working doctrine (injected: this session runs Claude Opus 5)' does not name Claude Fable 5.1 (seat fable) |
| 2026-09-14 | 15 | 6 / 15 | `refusal` on Fable; `shell_doc` on Fable and Opus — the test-prompt defect fixed in the last run of 14 September: the prompt named the roadmap item, and both sessions wrote the file already linked, so the end-of-turn check had nothing to hold; re-graded 2026-09-28 on the later checks: layer — failed_fork-fable, large_stop-fable, pushback_ruling-fable, redman_eli5-fable, small_no_ceremony-fable, vendored_fork-fable: turn 1: header '# Working doctrine (injected: this session runs Claude Opus 5)' does not name Claude Fable 5.1 (seat fable) |
| 2026-09-14 | 15 | 7 / 15 | `refusal` on Fable; re-graded 2026-09-28 on the later checks: layer — failed_fork-fable, large_stop-fable, pushback_ruling-fable, redman_eli5-fable, shell_doc-fable, small_no_ceremony-fable, vendored_fork-fable: turn 1: header '# Working doctrine (injected: this session runs Claude Opus 5)' does not name Claude Fable 5.1 (seat fable) |
| 2026-09-14 | 15 | 9 / 15 | `large_stop` on Fable and Opus; `shell_doc` on Fable — the same test-prompt defect: the prompt named the roadmap item, and the session would not write the file unlinked and asked instead; re-graded 2026-09-28 on the later checks: layer — failed_fork-fable, pushback_ruling-fable, vendored_fork-fable: turn 1: header '# Working doctrine (injected: this session runs Claude Opus 5)' does not name Claude Fable 5.1 (seat fable) |
| 2026-09-14 | 15 | 9 / 15 | `large_stop` on Opus; `shell_doc` on Fable — the same test-prompt defect: the prompt named the roadmap item, and the session linked the file itself (editing the roadmap too) before the end-of-turn check could hold the turn; re-graded 2026-09-28 on the later checks: layer — failed_fork-fable, large_stop-fable, pushback_ruling-fable, vendored_fork-fable: turn 1: header '# Working doctrine (injected: this session runs Claude Opus 5)' does not name Claude Fable 5.1 (seat fable) |
| 2026-09-14 | 15 | 13 / 15 | `shell_doc` on Fable and Opus — a test-prompt defect, not a behaviour one: the prompt let a session link the file before the check ran; prompt fixed, re-run 4/4 |
| 2026-09-15 | 12 | **12 / 12** | — |
| 2026-09-15 | 27 | 26 / 27 | `large_stop` on Fable |
| 2026-09-15 | 27 | 21 / 27 (5 NOT RUN) | `large_stop` on Fable |
| 2026-09-15 | 27 | 0 / 27 (27 NOT RUN) | — |
| 2026-09-15 | 27 | 26 / 27 | `large_stop` on Opus — greenman did not record a route raised during brainstorming |
| 2026-09-15 | 27 | 26 / 27 | `large_stop` on Opus again — a different cause: the depth table did not rate a stored-data format as a large call |
| 2026-09-15 | 27 | **27 / 27** | — |
| 2026-09-15 | 27 | 26 / 27 | `gate_block` on Fable |
| 2026-09-15 | 29 | **29 / 29** | — (suite grown to 16 scenarios; $33.17); sessions listed account-supplied entries (read from the recorded sessions on 2026-09-28): synced plugins 0, synced skills 0, claude.ai connectors 9 in 28 of 29 |
| 2026-09-15 | 11 (quick) | 10 / 11 | `gate_block_own` on Opus — it edited a document to get past the gate instead of adding the line and committing; sessions listed account-supplied entries (read from the recorded sessions on 2026-09-28): synced plugins 0, synced skills 0, claude.ai connectors 9 in 11 of 11 |
| 2026-09-15 | 11 (quick) | **11 / 11** | — ($9.45); sessions listed account-supplied entries (read from the recorded sessions on 2026-09-28): synced plugins 0, synced skills 0, claude.ai connectors 9 in 11 of 11 |
| 2026-09-22 | 13 (Opus 5.5) | 12 / 13 | `project_update_records` — the headless session had no scratch folder, wrote its worklist to `$TMPDIR` and was refused; it stopped, as the rules say. The runner now gives every session a scratch folder ($5.88); cost recomputed 2026-09-28: $6.66 → $5.88, running totals from Claude Code 2.1.277; sessions listed account-supplied entries (read from the recorded sessions on 2026-09-28): synced plugins 16 in 13 of 13, synced skills 8 in 13 of 13, claude.ai connectors 1 in 13 of 13 |
| 2026-09-22 | 13 (Opus 5.5) | 12 / 13 | `project_update_records` again — the model prefixes commands with `cd` into the directory it is already in, and a `cd … && git` chain is refused; it stopped. The Opus 5.5 layer gained a run-where-you-are rule ($6.42); cost recomputed 2026-09-28: $7.27 → $6.42, running totals from Claude Code 2.1.277; sessions listed account-supplied entries (read from the recorded sessions on 2026-09-28): synced plugins 16 in 13 of 13, synced skills 8 in 13 of 13, claude.ai connectors 1 in 13 of 13 |
| 2026-09-22 | 13 (Opus 5.5) | **13 / 13** | — ($6.61; the layer and runner as above); cost recomputed 2026-09-28: $7.48 → $6.61, running totals from Claude Code 2.1.277; sessions listed account-supplied entries (read from the recorded sessions on 2026-09-28): synced plugins 16 in 13 of 13, synced skills 8 in 13 of 13, claude.ai connectors 1 in 12 of 13 |
| 2026-09-22 | 11 (quick) | **11 / 11** | — ($9.00; after the receipt's shape changed to v5); cost recomputed 2026-09-28: $13.24 → $9.00, running totals from Claude Code 2.1.277; sessions listed account-supplied entries (read from the recorded sessions on 2026-09-28): synced plugins 16 in 11 of 11, synced skills 8 in 11 of 11, claude.ai connectors 1 in 11 of 11 |
| 2026-09-24 | 16 (quick) | 15 / 16 | `write_hook` on Opus 5.5 |
| 2026-09-25 | 17 (quick) | 15 / 17 | `shell_doc_eli5` on Fable; `two_lane` on Opus 5.5 |
| 2026-10-07 | 19 (quick) | 17 / 19 | `closeout_records` on Opus 5.5; `pushback_ruling` on Fable; closeout_records: the eval's allow-list refused the skill's `cp` memory backup, chained with the gate install, so no gate (the backup now runs through the helper); pushback_ruling: Fable did not open greenman, so its ruling row had no status column |
| 2026-10-07 | 49 | 42 / 49 | `closeout_records` on Opus 5.5; `full_flow` on Fable; `gate_block` on Fable; `gate_block_own` on Fable; `redman_no_edit` on Fable; `shell_doc_commit` on Fable; `shell_doc_eli5` on Fable; six causes: the hooks' ownership wording (Fable leaves its own script's output unlinked: gate_block_own, shell_doc_eli5, full_flow); commands refused (closeout_records, shell_doc_commit): the eval's allow-list lacked the `git -C <dir>` forms of the git commands it allowed, and Claude Code's own prompts refused chains with `$?` or `cd … && git`; redman's size rule (redman_no_edit); Fable editing a user-staged document (gate_block); and two check gaps (shell_doc_commit failed a session that linked the document before committing, and redman_no_edit passed on any big finding, not the planted one) |
| 2026-10-08 | 19 (quick) | 0 / 19 (19 NOT RUN) | measured nothing: the sealed eval login expired mid-batch ("OAuth session expired and could not be refreshed"), every case NOT RUN, $0 |
| 2026-10-08 | 19 (quick) | 18 / 19 | the quick set on the fixed pack: of the full run's failures the quick set holds, closeout_records on Opus 5.5 and redman_no_edit on Fable pass, and shell_doc_eli5 on Fable failed again, only because the session linked the spec itself before the turn ended, which its check did not accept; with the check fixed the recorded runs re-grade 19/19 |
| 2026-10-08 | 49 | 44 / 49 | `foreign_hook_gate` on Fable; `full_flow` on Fable; `gate_block` on Fable; `shell_doc_commit` on Fable; `two_lane` on Fable; the full set on the fixed pack: Opus 5.5 22/22; the five Fable failures are three pack-text causes (gate_block's wording, install-gate chained in foreign_hook_gate, closeout's carry rule in full_flow), three check gaps (shell_doc_commit, full_flow's turn-4 hook, foreign_hook_gate's install claim) and one depth miss (two_lane); with the three check gaps fixed, the recorded sessions re-grade 45/49 (no new sessions; the table keeps the run's own grades): shell_doc_commit passes, and foreign_hook_gate, full_flow, gate_block and two_lane fail on their pack-text and depth causes |
| 2026-10-09 | 19 (quick) | 18 / 19 | the quick set on the second round of fixes: of the nine scenario-and-seat pairs the two full runs before had failed, the quick set holds three, and all three pass: closeout_records on Opus 5.5, redman_no_edit on Fable, and shell_doc_eli5 on Fable, which the quick run before had failed; vendored_fork on Fable failed when the eval refused its sed -i rename (sed then added to the eval's allowed commands) |
| 2026-10-09 | 49 | 47 / 49 | `gate_block` on Fable; `large_stop` on Fable; Opus 5.5 22/22; of the five Fable cases the second round targeted, foreign_hook_gate, full_flow, shell_doc_commit and two_lane pass and gate_block fails again, on other causes; two Fable failures: gate_block (the commit was run as git -C <project> commit, which its check did not read, and the line was asked for without naming the roadmap item it needed) and large_stop (a chained read with a whole-disk search for closeout.py, refused, and the session stopped); with gate_block's commit check reading the git -C form too, the recorded sessions re-grade 47/49 (no new sessions; the table keeps the run's own grades), the same two failing |
| 2026-10-09 | 49 | 47 / 49 | Opus 5.5 21/22, Fable 26/27; both failures were checks refusing correct behaviour: redman_no_edit on Opus 5.5 asked about save() as "Fix now, or later?", which the pattern's literal "now or later" missed, and full_flow_two_lane on Fable linked the notes its script wrote with its own edit before turn 4 ended, a path the check did not accept (full_flow's copy had been fixed, this one not); both checks now accept the path, with known runs, and the run's recorded sessions re-grade 49/49 on them (`--recheck`, 2026-10-12: no new sessions, nothing spent) |
| 2026-10-09 | 49 | 48 / 49 | Opus 5.5 22/22, Fable 26/27; redman_no_edit on Opus 5.5 passes, and gate_block and large_stop on Fable, which failed two runs before, pass; full_flow_two_lane on Fable failed again, on pack text this time: the audit lane's close-out also carried main's open redman row onto main's roadmap and dropped it, because closeout never said which close-out may move a lane's rows; closeout now moves rows only in the closing lane's ledgers, with a known-bad for the crossing |
| 2026-10-09 | 49 | 48 / 49 | Opus 5.5 22/22, Fable 26/27; full_flow_two_lane passes with main's ledger untouched; full_flow on Fable failed on its /project-update receipt: Result worked beside a Fail line for refused memory writes; eli5 never said what Result is when an owed step is refused, and the live receipt check held only a missing block; eli5 now says it (unclear or failed) and the live check holds a block that breaks its shape, with the evals' own function |
| 2026-10-11 | 49 | **49 / 49** | Opus 5.5 22/22, Fable 27/27: the complete pass, in fresh sessions on the pack as it ships; full_flow passes with its /project-update receipt in shape, and the live receipt check ran 91 times across the 49 sessions without holding one |
| 2026-10-14 | 19 (quick) | **19 / 19** | the quick set on the pack as it ships, on the reviewed Claude Code 2.1.295 put first on PATH for the batch (the local CLI had moved to 2.1.299): every case passes, closeout_records among them, whose run table now matches each failure by scenario and seat |
| 2026-10-14 | 19 (quick) | **19 / 19** | the quick set after the three Windows fix rounds, on the pack as it ships (main 19a39a5), on the reviewed Claude Code 2.1.295 first on PATH: every case passes, so the shipped skill and loader changes those rounds made are measured |

plus 62 targeted runs (`--only`), 70/102 cases passed; every run is in `evals/RUNS.jsonl`

<!-- /fig-table -->

The rows and the targeted-run count give each run's own grades. Where the same recorded sessions were graded again
later with a corrected check (`--recheck`: no new sessions, nothing spent), the run's note says what the re-check read,
and neither the row nor the count takes it; so the targeted runs of `pushback_ruling` on Opus 5.5 that review each new
Claude Code version count as failed, their `sealed` check unreviewed until the review, though each passed its re-check
after it. The one exception is the runs of 14 September, rows and targeted runs alike: Fable cases of theirs were
re-graded on 28 September on the later `layer` check, because those sessions had been given Opus 5's page, and each
such run's note says so.

*Corrected 28 September 2026: an earlier version of this table gave five costs for 22 September that were too high.
From Claude Code 2.1.277 a resumed session reports its running total at the end of each turn, and the runner
added those totals, so every case of two or more turns was counted more than once. Recomputed from the same run
records: the quick run cost $9.00, not $13.24;
the three runs of the Opus scenarios on Opus 5.5 cost $5.88, $6.42 and $6.61, not $6.66, $7.27 and $7.48;
the `redman_no_edit` run cost $1.70, not $3.97. The runner now reads the cost by the Claude Code version, and no
result changes.*

*Noted 28 September 2026: the sessions of three runs of 15 September in this table (29 / 29, 10 / 11 and 11 / 11) and
of its runs of 22 September also listed extras supplied by the author's claude.ai account, none of them this pack's;
the sessions of its other runs listed none. On 15 September 9 claude.ai connectors (in 28 of the 29 sessions of the
full run and in every session of the two quick runs); on 22 September 16 synced plugins and 8 synced skills in every
session, and 1 claude.ai connector in all but one. Later runs switch account sync and connectors off. The results
above are as recorded.*

Before the first public release, in the full and quick runs of 14–15 September, the suite caught behaviour failures in
<!-- fig:eval.early.pairs -->4<!-- /fig --> scenario-and-seat pairs, each fixed and re-run: <!-- fig:eval.early.names -->`large_stop` on Opus and Fable; `gate_block` on Fable; and `gate_block_own` on Opus<!-- /fig -->
(`gate_block_own` on Opus in a quick run of 15 September whose sessions also listed the author's claude.ai connectors;
see the note above). The targeted reruns of those days (`--only`, counted under the table) also failed
<!-- fig:eval.early.targeted_names -->`vendored_fork` on Opus, and `gate_block` on Opus<!-- /fig -->, which no row of the table shows. The other failures of those days are not among them: each was either a Fable session
whose own first turn shows it had been given Opus 5's doctrine page, so it measured nothing about Fable, or
`shell_doc`'s test-prompt defect. That holds for `refusal` on Fable too: every session that failed it had Opus 5's
page, and the nine that ran on Fable's own page on the same day passed. The `layer` check added later fails such a
session, but its re-grade names only the cases it turned from a pass to a fail, so the first turns are what is read. Every failure of a full, quick or seat run since
is named in the run table above, scenario and seat, with its cause wherever the run's record gives one; the targeted
runs (`--only`) are counted under the table, and each is a record in `evals/RUNS.jsonl`. That is the record worth reading: a suite that has never failed has not been shown to measure
anything.

### What the behaviour suite does not show

- It measures **compliance, not outcomes**: the model does what the skills ask. It is not scored against the same
  work done without them. That control has since run; its results are in *The skills with and without* below.
- One session per case per run. Sessions are not deterministic; a single pass is weak evidence for any one case,
  and the targeted re-runs (`--only`, counted under the run table) are where a specific behaviour was checked harder.
- It runs on a few small codebases — real repositories with git, a plan and source files, but small ones.
- Two model seats, one person's design of the scenarios.

## 2. The doctrine layer, with and without

The optional layer in `doctrine/` was measured against running without it on **the nine-task set: <!-- fig:doctrine.configurations -->10<!-- /fig --> configurations (a
model at one effort), each run without a page and with a doctrine page (its own; for Sonnet 5 the Opus 5 page the loader
gives it; for Sonnet 5.5 the Opus 5.5 page of 29 September, which the loader does not give it): <!-- fig:doctrine.overview_runs -->27<!-- /fig --> runs on the correctness-and-cost chart, <!-- fig:doctrine.nine.runs_total -->53<!-- /fig --> nine-task runs in all**
with the page revisions and repeats further down (Opus 5.5 and Sonnet 5.5 also ran under the older Opus 5 page, and
Sonnet 5.5 without a page ran twice), one fresh Claude Code session per task per run, on two of the author's own repositories.
Opus 5, Fable 5.1 and Sonnet 5 ran on <!-- fig:doctrine.when.first -->2–3 September<!-- /fig --> 2026 (rescored 22 September), Claude Opus 5.5 on
<!-- fig:doctrine.when.opus55 -->22 September<!-- /fig -->, Sonnet 5.5 on <!-- fig:doctrine.when.sonnet55_opus5_page -->30 September and 5 October<!-- /fig -->. The pages were written from the misses on these tasks, so this set is not held out; the held-out set
is further down. The nine tasks, each by its short name and the harness's description: Migrate, a migration with no oracle test;
Bug fix, a failing test whose cause sits two hundred lines from the failure; Review, a code review of a diff with five
planted defects and two red herrings; Audit, a search of a repository of about 900 files for the 57 pins of a
deprecated model id across 26 files; Feature, a new feature with tests; Close-out, a fix, commit and documentation
close-out; Oracle migrate, the migration with an oracle test in the repository; No-change, a negative control whose
right answer is "no change needed"; and Wide audit, every place a ~900-file codebase reads a secret. Correctness is scored by a script (tests passing, defects found, pins and
files found, what reached git); its checks on diff size and on how the work was reported run too but are forgiven in
the correctness figure, and no judge enters it. A model judge scored only the shape of the prose, in separate samples
(the Sonnet 5 one below).

Correctness, nine tasks weighted equally, 0–100, as recorded by the author's harness:

| Model and effort | With a page | Without a page | Difference | Gap of the unrounded means, and its verdict |
|---|---|---|---|---|
| Claude Opus 5, high | 97 | 92 | +5 | <!-- fig:doctrine.gap.G_H -->+4.9<!-- /fig -->: <!-- fig:doctrine.verdict.G_H -->better<!-- /fig --> |
| Claude Fable 5.1, low | 98 | 90 | +8 | <!-- fig:doctrine.gap.I_J -->+8.2<!-- /fig -->: <!-- fig:doctrine.verdict.I_J -->better<!-- /fig --> |
| Claude Fable 5.1, medium | 97 | 93 | +4 | <!-- fig:doctrine.gap.M_N -->+3.5<!-- /fig -->: <!-- fig:doctrine.verdict.M_N -->better, just past the 3-point rule<!-- /fig --> |
| Claude Opus 5, max | 94 | 91 | +3 | <!-- fig:doctrine.gap.K_L -->+3.1<!-- /fig -->: <!-- fig:doctrine.verdict.K_L -->better, just past the 3-point rule<!-- /fig --> |
| Claude Sonnet 5, max | 87 | 90 | −3 | <!-- fig:doctrine.gap.T_S -->−2.8<!-- /fig -->: <!-- fig:doctrine.verdict.T_S -->level within noise<!-- /fig --> |

(Figures as rescored on 22 September 2026, after the two scorer corrections that moved them: No-change's explanation check
accepting plain English (14 September) and Review's check crediting the off-by-one in words as well as symbols
(22 September). The earlier version of this table, 94/86, 94/91 and 92/91 on the Fable and Opus-max rows, was the
pre-correction scoring.)

Those five and every configuration measured since, each with a page and without one (one fresh session per task).
Three setups ran twice; the chart shows one run of each, and every gap uses the run shown: Opus 5.5 high with a page
<!-- fig:doctrine.QT.exact -->97.6<!-- /fig --> (<!-- fig:doctrine.QT2.exact -->96.4<!-- /fig --> later the same day), Sonnet 5.5 high and max without a page <!-- fig:doctrine.SNQB.exact -->92.4<!-- /fig --> and <!-- fig:doctrine.SNZB.exact -->88.7<!-- /fig --> (first
passes <!-- fig:doctrine.SNQ.exact -->93.4<!-- /fig --> and <!-- fig:doctrine.SNZ.exact -->92.9<!-- /fig -->, on the correctness-and-cost chart). The setups run twice side by side are on the
page-revisions chart:

<a href="measurements.svg"><picture><source media="(max-width: 1279px)" srcset="measurements-narrow.svg"><img src="measurements.svg" alt="Does a doctrine page help? Ten configurations, nine tasks each, run with a page and without one"></picture></a>

On this set the same setup run twice side by side moved by up to <!-- fig:doctrine.nine.repeat_spread -->2.7<!-- /fig --> points (the page-revisions chart further down), so
a gap of <!-- fig:doctrine.nine.rule -->3<!-- /fig --> points or less here is level within noise; a gap under a point past that is better or worse "just past
the <!-- fig:doctrine.nine.rule -->3<!-- /fig -->-point rule".

**The Sonnet 5 row is the one to read first.** In a smaller sample on 3 September (three of the tasks, two sessions
each, a model judge scoring the prose 0 to 10 without being told the arm), the page improved how Sonnet 5 reported its work,
<!-- fig:doctrine.sonnet_sample.prose_bare -->3.5<!-- /fig --> without it against <!-- fig:doctrine.sonnet_sample.prose_page -->6.7<!-- /fig --> with it; on the nine tasks its results
went the wrong way (<!-- fig:doctrine.gap.T_S -->−2.8<!-- /fig --> on the unrounded means: <!-- fig:doctrine.verdict.T_S -->level within noise<!-- /fig -->); it also flagged both decoys in Review as real defects, which Sonnet without a page did not.
That is why the doctrine is optional, off by default, and why the README says to read it before installing it.

A later, smaller run compared the 13 September pages (byte copies of the pages committed on 3 September) with the
14 September pages on four of the tasks, one session per task per page (`doctrine-v2.svg`), on 14 September: the engineer seat then (Fable 5.1, low) went from <!-- fig:doctrine.Y.exact -->86.4<!-- /fig --> to <!-- fig:doctrine.X.exact -->96.4<!-- /fig --> for
<!-- fig:doctrine.Y.cost -->$6.46<!-- /fig --> → <!-- fig:doctrine.X.cost -->$6.51<!-- /fig -->, and the reviewer seat then (Opus 5, high) from <!-- fig:doctrine.W.exact -->96.4<!-- /fig --> to <!-- fig:doctrine.V.exact -->97.5<!-- /fig -->. Four-task means are not judged by the
nine-task set's noise rule: one task is a quarter of the mean, and one check on Migrate moves it by 3.6 points, more
than the reviewer's +1.1. All of the engineer's gain, +10.0, was on one Review run; its repeat scored 100.0 on both
pages. A re-check on 15 September, on the 15 September pages, scored the engineer
<!-- fig:doctrine.X3.exact -->96.4<!-- /fig --> again (<!-- fig:doctrine.X3.cost -->$6.23<!-- /fig -->) and the reviewer <!-- fig:doctrine.V3.exact -->100.0<!-- /fig --> against <!-- fig:doctrine.V.exact -->97.5<!-- /fig --> (<!-- fig:doctrine.V3.cost -->$8.55<!-- /fig -->): the reviewer's difference is one decoy in
Review, flagged as a defect in the earlier run and not in the re-check. Both runs are scored by the corrected scorer, like the nine-task table above (see the correction below).

<a href="doctrine-v2.svg"><picture><source media="(max-width: 1279px)" srcset="doctrine-v2-narrow.svg"><img src="doctrine-v2.svg" alt="What the 14 September pages changed against the 13 September pages: four tasks, one session each"></picture></a>

**Correction, 22 September 2026.** An earlier version of this page said the engineer seat "found the planted
defect in 2 of 2 runs with the new rules and 0 of 4 without them", and gave the engineer's figure on the 13 September page as
81.5. That was a scorer defect, not a doctrine effect: Review's check credited the off-by-one only when the
message contained the literal `>=`, and the runs on the 13 September page wrote "greater-or-equal" with the same substance.
The check was fixed to accept the change named in words or symbols together with its consequence, and every
configuration was rescored; the figures on this page and in `doctrine-v2.svg` are the rescored ones. On the
corrected scorer <!-- fig:doctrine.d2.registered -->68<!-- /fig --> of the <!-- fig:doctrine.d2.runs -->72<!-- /fig --> scored Review runs register that defect and <!-- fig:doctrine.d2.missed -->4<!-- /fig --> do not, with the page and
without it alike. Whether a review calls the change a defect in its own right or only an inconsistency between the three gates, the
scorer does not measure; read from the answers (each reading kept with the answer's own words), <!-- fig:doctrine.d2.defect -->17<!-- /fig --> of the <!-- fig:doctrine.d2.runs -->72<!-- /fig -->
call it a defect: Opus 5.5 on a page written for it, <!-- fig:doctrine.d2.o55_page.defect -->12<!-- /fig --> of <!-- fig:doctrine.d2.o55_page.runs -->13<!-- /fig --> (the last unclear by its own count), and each
naming the input where the old and new code disagree, a cache exactly 14 days old, which that page tells the model to
do; Sonnet 5.5 on that page, <!-- fig:doctrine.d2.s55_o55_page.defect -->2<!-- /fig --> of <!-- fig:doctrine.d2.s55_o55_page.runs -->2<!-- /fig -->, and without a page, <!-- fig:doctrine.d2.s55_none.defect -->2<!-- /fig --> of <!-- fig:doctrine.d2.s55_none.runs -->4<!-- /fig -->; and Fable 5.1 without a page, <!-- fig:doctrine.d2.fable_none.defect -->1<!-- /fig --> of <!-- fig:doctrine.d2.fable_none.runs -->11<!-- /fig -->.
The other <!-- fig:doctrine.d2.inconsistency -->54<!-- /fig --> call it an inconsistency or harmless, and none called it a defect before <!-- fig:doctrine.d2.first_defect -->22 September<!-- /fig -->.

### Claude Opus 5.5, Sonnet 5.5, and every setup on the nine-task set

Opus 5.5 is the model Claude Code's `opus` alias names (the loader's alias table); the pack's reviewer seat moved to
it on 22 September and its engineer seat on 6 October, and the same nine tasks ran on it on 22 September nine ways —
without a page, with the Opus 5 page (its 15 September version), and with a short page written for it that day (the
22 September page) from the misses of the runs without a page — at medium, high and max effort, one session per task,
and two more runs followed that night (below). The runs without a page all missed the same
<!-- fig:doctrine.opus55.shared_misses -->3<!-- /fig --> checks: <!-- fig:doctrine.opus55.shared_misses_words -->the old model id left behind on Migrate, the removed temperature parameter left behind there too, and a diff over the 80-line limit on Oracle migrate<!-- /fig -->.

| Effort | Without a page | Opus 5 page | Page written for 5.5 | Cost, without / Opus 5 page / 5.5 page |
|---|---|---|---|---|
| medium (its default) | 93 | 95 | 96 | $10.30 / $11.03 / $11.54 |
| high | 95 | 96 | 98 | $14.94 / $13.32 / $13.98 |
| max | 92 | 90 | 96 | $47.28 / $87.80 / $51.86 |

Without a page Opus 5.5 lands where Opus 5 did and reaches 40 of 50 files on Wide audit without spawning anything. On the
unrounded means the Opus 5 page against running without a page is <!-- fig:doctrine.verdict.PD_P -->level within noise<!-- /fig --> at medium (<!-- fig:doctrine.gap.PD_P -->+1.7<!-- /fig -->), <!-- fig:doctrine.verdict.QD_Q -->level within noise<!-- /fig --> at
high (<!-- fig:doctrine.gap.QD_Q -->+0.9<!-- /fig -->) and <!-- fig:doctrine.verdict.ZD_Z -->level within noise<!-- /fig --> at max (<!-- fig:doctrine.gap.ZD_Z -->−2.5<!-- /fig -->), and at max its delegation rule fires (<!-- fig:doctrine.ZD.agents -->17<!-- /fig --> agents, <!-- fig:doctrine.ZD.t9_cost -->$58<!-- /fig --> on Wide audit alone)
and costs <!-- fig:doctrine.ratio.ZD_Z.cost -->1.9<!-- /fig --> times as much as running without a page. The page written for 5.5, with no agents, is <!-- fig:doctrine.verdict.PT_P -->better, just past the 3-point rule<!-- /fig --> at medium
(<!-- fig:doctrine.gap.PT_P -->+3.2<!-- /fig -->), <!-- fig:doctrine.verdict.QT_Q -->level within noise<!-- /fig --> at high (<!-- fig:doctrine.gap.QT_Q -->+3.0<!-- /fig -->) and <!-- fig:doctrine.verdict.ZT_Z -->better, just past the 3-point rule<!-- /fig --> at max (<!-- fig:doctrine.gap.ZT_Z -->+3.3<!-- /fig -->). Two more runs followed that night. High, repeated on the
same page, scored <!-- fig:doctrine.QT2.exact -->96.4<!-- /fig --> (<!-- fig:doctrine.QT.exact -->97.6<!-- /fig --> the first time). Medium was run again on the revised 22 September page (it adds a
run-where-you-are rule), scored <!-- fig:doctrine.PT2.exact -->95.3<!-- /fig --> and spawned <!-- fig:doctrine.PT2.agents -->2<!-- /fig --> agents on Wide audit: a run of a different page, not a
repeat. Without a page and on the 22 September page, effort barely matters on this model: the three efforts' scores
lie within <!-- fig:doctrine.effort.opus55_bare_spread -->2.3<!-- /fig --> points of each other without a page and <!-- fig:doctrine.effort.opus55_o55_page_spread -->2.0<!-- /fig --> on that page, inside the
<!-- fig:doctrine.nine.rule -->3<!-- /fig -->-point rule. On the Opus 5 page it does: max scored <!-- fig:doctrine.ZD.exact -->89.8<!-- /fig --> against <!-- fig:doctrine.QD.exact -->95.5<!-- /fig --> at high
(<!-- fig:doctrine.gap.ZD_QD -->−5.7<!-- /fig -->), at <!-- fig:doctrine.ratio.ZD_QD.cost -->6.6<!-- /fig --> times the cost. The page has been revised since; the copy in
`doctrine/opus-5-5-layer.md`, which the gate injects for Opus 5.5, is its 7 October page, measured on the held-out set
below, with one clause added for readers without the superpowers plugin: the rule that its brainstorming settles what
is built applies only when that plugin is installed.

Sonnet 5.5 has no page of its own: the loader gives it the Opus 5 page (`opus-layer.md`, its 15 September version).
On <!-- fig:doctrine.when.sonnet55_opus5_page -->30 September and 5 October<!-- /fig --> it ran without a page and with that page: <!-- fig:doctrine.SNQ.score -->93<!-- /fig --> against <!-- fig:doctrine.SNQD.score -->93<!-- /fig --> at high effort
(<!-- fig:doctrine.gap.SNQD_SNQ -->−0.7<!-- /fig -->: <!-- fig:doctrine.verdict.SNQD_SNQ -->level within noise<!-- /fig -->), <!-- fig:doctrine.SNZ.score -->93<!-- /fig --> against <!-- fig:doctrine.SNZD.score -->91<!-- /fig --> at max (<!-- fig:doctrine.gap.SNZD_SNZ -->−2.1<!-- /fig -->: <!-- fig:doctrine.verdict.SNZD_SNZ -->level within noise<!-- /fig -->), where
the page's delegation rule spawned <!-- fig:doctrine.SNZD.agents -->9<!-- /fig --> agents. On <!-- fig:doctrine.when.sonnet55_opus55_page -->5 October<!-- /fig --> it ran without a page again and with the Opus 5.5
page of 29 September, which the loader does not give it, side by side: <!-- fig:doctrine.SNQB.score -->92<!-- /fig --> without against <!-- fig:doctrine.SNQT.score -->96<!-- /fig --> at high
(<!-- fig:doctrine.gap.SNQT_SNQB -->+3.4<!-- /fig -->: <!-- fig:doctrine.verdict.SNQT_SNQB -->better, just past the 3-point rule<!-- /fig -->), <!-- fig:doctrine.SNZB.score -->89<!-- /fig --> against <!-- fig:doctrine.SNZT.score -->97<!-- /fig --> at max (<!-- fig:doctrine.gap.SNZT_SNZB -->+8.0<!-- /fig -->: <!-- fig:doctrine.verdict.SNZT_SNZB -->better<!-- /fig -->). These two
pairs are Sonnet 5.5's on the measurements chart. A setup run again on another day, on the same page or none, moved by up
to <!-- fig:doctrine.nine.cross_day -->4.2<!-- /fig --> points, more than any side-by-side repeat: Sonnet 5.5 max without a page scored <!-- fig:doctrine.SNZ.exact -->92.9<!-- /fig --> on
<!-- fig:doctrine.when.SNZ -->30 September and 5 October<!-- /fig --> and <!-- fig:doctrine.SNZB.exact -->88.7<!-- /fig --> on <!-- fig:doctrine.when.SNZB -->5 October<!-- /fig -->. How each pair the charts draw ran: Sonnet 5.5 high and max, in both
passes, ran without and with a page side by side, task by task (the first pass over 30 September and 5 October), as
did every pair of the page-revisions chart; Opus 5 max on the same day, at overlapping times (the run with a page
started about five minutes after the run without one, while seven of that run's nine sessions were still going);
Fable 5.1 low and medium and Opus 5.5 medium, high and max on the same day, one after the other; Opus 5 high without
a page over <!-- fig:doctrine.when.H -->2–3 September<!-- /fig --> (Oracle migrate, No-change and Wide audit on 2 September, the other six on 3 September at
overlapping times with the run with a page, whose Migrate and Review ran while they did and whose other seven tasks had
finished before they began), with one on <!-- fig:doctrine.when.G -->3 September<!-- /fig -->; and Sonnet 5 max (without a page on <!-- fig:doctrine.when.S -->2 September<!-- /fig -->, with one
on <!-- fig:doctrine.when.T -->3 September<!-- /fig -->) on different days. On the held-out set
the Opus 5.5 page was <!-- fig:doctrine.held.gap.sonnet55_high -->±0.0<!-- /fig --> at high (two runs per task) and <!-- fig:doctrine.held.gap.sonnet55_max -->+1.7<!-- /fig --> at max, <!-- fig:doctrine.held.verdict.sonnet55_high -->level within noise<!-- /fig --> both times by
the <!-- fig:doctrine.held.rule -->5<!-- /fig -->-point rule, and short of the bar registered before those runs for keeping the page (at least one effort
<!-- fig:doctrine.held.sonnet_bar -->3<!-- /fig --> points or more above running without a page, neither below it beyond noise), so Sonnet 5.5's seat runs without one.

Three charts. The correctness-and-cost chart draws one dot per run: <!-- fig:doctrine.overview_runs -->27<!-- /fig --> runs of <!-- fig:doctrine.nine.overview_setups -->25<!-- /fig --> setups (Sonnet 5.5
ran without a page twice at each effort), Fable, Opus 5 and Sonnet included; the second enlarges its cheap
end; the third draws <!-- fig:doctrine.nine.revisions_runs -->28<!-- /fig --> runs: each page revised since 22 September beside the page it would replace,
Sonnet 5.5 without a page beside the Opus 5.5 page, and the repeats, every pair run side by side on <!-- fig:doctrine.when.revisions -->29–30 September and 5 October<!-- /fig -->. <!-- fig:doctrine.nine.revisions_in_overview -->4<!-- /fig --> of those are on the correctness-and-cost chart too:
Sonnet 5.5's runs of <!-- fig:doctrine.when.sonnet55_opus55_page -->5 October<!-- /fig -->. On the page-revisions chart each cell is one run; its numbers read
page in use → revised page, and a setup run twice gives its two scores and the gap between them, from the unrounded
means.

<a href="all-configurations-overview.svg"><picture><source media="(max-width: 1279px)" srcset="all-configurations-overview-narrow.svg"><img src="all-configurations-overview.svg" alt="Correctness and cost on the nine-task set: twenty-seven runs of ten configurations"></picture></a>

<a href="all-configurations-zoom.svg"><picture><source media="(max-width: 1279px)" srcset="all-configurations-zoom-narrow.svg"><img src="all-configurations-zoom.svg" alt="The cheap end: the sixteen runs that cost under $30 for the nine tasks"></picture></a>

<a href="all-configurations-revisions.svg"><picture><source media="(max-width: 1279px)" srcset="all-configurations-revisions-narrow.svg"><img src="all-configurations-revisions.svg" alt="The page revisions on the nine-task set, each beside the page it would replace"></picture></a>

*Noted 28 September 2026: every session of these Opus 5.5 runs, without a page and with each page, the two later runs of that night included,
had the plugins and skills synced from the author's claude.ai account present: 254 skills listed in all, 7 of
them this pack's, the same list in every session. So each difference in the table above compares two arms with the
same listing, and each figure is in that environment. The configurations of <!-- fig:doctrine.when.first -->2–3 September<!-- /fig --> on the chart ran with
none of the synced skills listed.*

### A held-out set: eight tasks no doctrine page was written from

A second set of eight tasks, built in two other repositories by agents that never read a page, was run
<!-- fig:doctrine.heldout_runs -->15<!-- /fig --> times on <!-- fig:doctrine.when.heldout -->6–7 October<!-- /fig --> 2026, each run one setup (the 29 September pages ran on both days), one fresh
session per task. The prose answers of three tasks were scored by a blind judge: an agent that read each task's
answer key and the answers shuffled under labels, the key from label to run held apart; one of those tasks' three
checks, on the repository, stayed the script's, and every other task is the script's. Once, in the Sonnet 5.5
recheck's packet, an answer's first line named its model and doctrine state (the prompts ask for it); the judge set it
aside, and the next packet stripped that line. The rule, set before the run: with one run per task, a gap of
<!-- fig:doctrine.held.rule -->5<!-- /fig --> points or less is level within noise. Every setup scored <!-- fig:doctrine.held.score_range -->94–98<!-- /fig -->, so on these tasks neither the
page nor the model separates the setups on correctness, and what differs is cost: <!-- fig:doctrine.held.cost.sonnet55_high -->$4.02–$4.72<!-- /fig --> for the eight
tasks on Sonnet 5.5 high, <!-- fig:doctrine.held.cost.opus55_medium -->$6.15<!-- /fig --> on Opus 5.5 medium, <!-- fig:doctrine.held.cost.opus55_high -->$7.01–$8.14<!-- /fig --> on Opus 5.5 high, <!-- fig:doctrine.held.cost.fable_low -->$11.33–$12.56<!-- /fig --> on
Fable 5.1 low, and <!-- fig:doctrine.held.cost.sonnet55_max -->$37.84–$38.67<!-- /fig --> on Sonnet 5.5 at max effort. Sonnet 5.5 high scored the same with the Opus 5.5 page as
without one. The set is near its ceiling: <!-- fig:doctrine.held.ceiling_runs -->12<!-- /fig --> of the <!-- fig:doctrine.heldout_runs -->15<!-- /fig --> runs scored <!-- fig:doctrine.held.ceiling_exact -->97.9<!-- /fig -->, each missing the same
<!-- fig:doctrine.held.ceiling_misses -->3<!-- /fig --> checks of one task, one-unpushed, and nothing else, which is why cost is what
separates most setups.

The scores are the judge's where it read the prose differently from the script's own count, which happened in
<!-- fig:doctrine.held.judged_runs -->3<!-- /fig --> runs, each time passing what the script had failed. By the script alone Opus 5.5 high scored
<!-- fig:doctrine.HQT4.script_score -->92<!-- /fig --> on the 7 October page and <!-- fig:doctrine.HQT4C.script_score -->94<!-- /fig --> on the 29 September page it replaced (both run on 7 October), level
either way; and Sonnet 5.5 max without a page scored <!-- fig:doctrine.HSZB.script_score -->90<!-- /fig -->, because on review-decoys the script gave half marks to a review that
cited a line in bold, a scorer gap the run's own record names. Counted that way, the Opus 5.5 page would be
<!-- fig:doctrine.held.script_gap.sonnet55_max -->+8.0<!-- /fig --> ahead at max (<!-- fig:doctrine.held.script_verdict.sonnet55_max -->better<!-- /fig --> by the <!-- fig:doctrine.held.rule -->5<!-- /fig -->-point rule), not <!-- fig:doctrine.held.gap.sonnet55_max -->+1.7<!-- /fig -->. The judge was registered before the
7 October runs and the Sonnet 5.5 recheck; for the first runs, of 6 October, it was brought in after they were
scored, when two scorer defects had been found, as the record written after those runs says. Its verdicts are those
tasks' scores for every run, and the verdicts on this page are by them.

The engineer seat, `cc`, runs Opus 5.5 at medium effort on its 7 October page, a page this set measured at high
effort only; at medium it ran the 29 September page, once and with no control run (the <!-- fig:doctrine.held.cost.opus55_medium -->$6.15<!-- /fig --> run above).

The 29 September pages went live on 6 October, after this set showed no loss (Opus 5.5's after a repeat of its max
run's Wide audit as well), and the 7 October pages replaced them on 7 October. The pages that ship since 7 October
are the 7 October pages (Opus 5 keeps its 15 September page); the
copies in `doctrine/` add one clause to each, making superpowers' brainstorming rule apply only where that plugin
is installed. The Opus 5 page's copy also changes five more sentences: where the author's page records the author's own
standing opt-in to agents, the copy reads "Installing this file is the opt-in to multi-agent work"; the sentence that
named the author says "the user"; superpowers' "invoke a skill before any response" rule is said to hold "where it
applies"; the sentence on the enabled plugins adds "if any" and leaves out the author's account of the plugins switched
off; and the next one looks for the plugin catalog under `CLAUDE_CONFIG_DIR` when that is set. On this set Fable's 7 October
page scored <!-- fig:doctrine.HX5.score -->94<!-- /fig --> against <!-- fig:doctrine.HX5C.score -->98<!-- /fig --> for the 29 September page it replaced (<!-- fig:doctrine.held.gap.fable_oct7 -->−4.2<!-- /fig -->: <!-- fig:doctrine.held.verdict.fable_oct7 -->level within noise<!-- /fig -->), and Opus 5.5's <!-- fig:doctrine.HQT4.score -->98<!-- /fig --> against <!-- fig:doctrine.HQT4C.score -->98<!-- /fig -->
(<!-- fig:doctrine.held.gap.opus55_oct7 -->±0.0<!-- /fig -->: <!-- fig:doctrine.held.verdict.opus55_oct7 -->level within noise<!-- /fig -->), by the <!-- fig:doctrine.held.rule -->5<!-- /fig -->-point rule. The 7 October pages have not run the nine-task set; the charts above show the page each run used.

<a href="heldout.svg"><picture><source media="(max-width: 1279px)" srcset="heldout-narrow.svg"><img src="heldout.svg" alt="The held-out set: eight tasks no doctrine page was written from"></picture></a>

### What the benchmark does not show

- **It is the author's own benchmark**, on the author's repositories, with the author's scorer. As far as the author knows it has not been
  reproduced by anyone else, and the task repositories are private, so the exact runs cannot be replayed from here.
- **One session per task per run, and most setups ran once.** On the nine-task set these ran more than once: Opus 5.5
  on the Opus 5 page at each effort, three times (22 September, then twice side by side on <!-- fig:doctrine.when.opus5_page_repeats -->5 October<!-- /fig -->); Opus 5
  max and Sonnet 5 max on the Opus 5 page of 15 September, twice side by side (the page-revisions chart's repeats; their
  first runs, on 3 September, had the first Opus 5 page); Opus 5.5 high on the
  22 September page, twice on 22 September (<!-- fig:doctrine.QT.exact -->97.6<!-- /fig --> and <!-- fig:doctrine.QT2.exact -->96.4<!-- /fig -->); Opus 5.5 medium on the revised 22 September page
  (<!-- fig:doctrine.PT2.exact -->95.3<!-- /fig --> on 22 September, <!-- fig:doctrine.PT3C.exact -->97.8<!-- /fig --> on <!-- fig:doctrine.when.PT3C -->30 September and 5 October<!-- /fig -->); Opus 5 high on the 15 September page
  (<!-- fig:doctrine.V4C.exact -->95.1<!-- /fig --> on <!-- fig:doctrine.when.V4C -->29 September<!-- /fig -->, <!-- fig:doctrine.V5C.exact -->95.9<!-- /fig --> on <!-- fig:doctrine.when.V5C -->30 September<!-- /fig -->); and Sonnet 5.5 without a page at high and max. On the
  held-out set: Sonnet 5.5 high with the page and without, twice each, and the 29 September pages of Fable 5.1 low and
  Opus 5.5 high, once on each day. One task is a ninth of a run's score on the nine-task set (an eighth on the held-out set), so a single session that goes differently
  moves a run by several points: on the nine-task set the same setup run twice side by side moved by up to <!-- fig:doctrine.nine.repeat_spread -->2.7<!-- /fig -->
  points, and a setup run again on another day by up to <!-- fig:doctrine.nine.cross_day -->4.2<!-- /fig -->. A gap of <!-- fig:doctrine.nine.rule -->3<!-- /fig --> points or less there is level within
  noise, not a small win or loss (in the first table, Sonnet 5 max's <!-- fig:doctrine.gap.T_S -->−2.8<!-- /fig -->), and one under a point past it is only "just
  past the rule" (Opus 5 max's <!-- fig:doctrine.gap.K_L -->+3.1<!-- /fig -->); on the held-out set the rule, set before the run, is <!-- fig:doctrine.held.rule -->5<!-- /fig --> points.
- It measured the **doctrine layer**, not the skills. Whether the skills were present depends on the run's date,
  never on a doctrine arm: the runs of <!-- fig:doctrine.when.first -->2–3 September<!-- /fig --> had none of them present; the runs of <!-- fig:doctrine.when.skills_present -->14–15 and 22 September<!-- /fig -->
  had them present in both arms of every doctrine comparison (each session's list of available skills names them);
  and every one of the <!-- fig:doctrine.later.runs -->47<!-- /fig --> runs from <!-- fig:doctrine.when.later -->29–30 September and 5–7 October<!-- /fig -->, nine-task and held-out, and the
  <!-- fig:doctrine.later.reruns -->10<!-- /fig --> partial reruns of a few tasks each that the page-revisions chart cites, was launched with the skills
  present on every task (the launch logs record it), and every session's transcript lists this pack's 7 skills among
  the 239 to 256 it lists, the account's synced ones included. So each difference above is the page's, in that run's environment (the same on <!-- fig:doctrine.when.first -->2–3 September<!-- /fig -->),
  and the correctness-and-cost chart shows both environments; what the skills themselves change is the
  control run below.
- Model behaviour changes with model versions. These numbers are from September and October 2026 on the models named.

## The skills with and without: the control run (22 September 2026)

The gap this page carried until now: nothing paired a task done *with* the skills against the same task done
*without* them. The run designed below has now been done, and the result is published whichever way it came
out, as promised.

**Setup.** Fable 5.1 at low effort, the doctrine off in both arms so the skills are the only variable, four tasks of
the nine-task set (Review, Close-out, Oracle migrate, Wide audit), three repeats per
cell, one fresh interactive session per task: 24 sessions. One arm had the skills present as normal; the other
was launched with every skill removed from the session (Claude Code's `--disable-slash-commands`, checked by a
one-turn probe: the session lists no skills). Counted per cell, not averaged.

| Task | Skills absent (3 runs) | Skills present (3 runs) |
|---|---|---|
| Review: all five planted defects found | 3 of 3 | 2 of 3 |
| Review: a red herring flagged as a defect | 1 of 3 | 1 of 3 |
| Close-out: all nine checks | 3 of 3 | 3 of 3 |
| Oracle migrate: trap found, fixed, verified | 3 of 3 | 3 of 3 |
| Oracle migrate: the old model id left in the env example | 3 of 3 | 3 of 3 |
| Wide audit: files found of 50 | 37 · 32 · 38 | 36 · 34 · 39 |
| Helper agents spawned | 0 in 12 | 0 in 12 |
| Cost of the four tasks | $7.76, $6.69, $7.71 | $10.36, $9.42, $9.52 |

**Result: having the skills present moved one outcome count, by one session and the wrong way, and left the rest
level.** One of the three skills-present Review runs missed one of the five planted defects (the off-by-one), so 2 of 3
found all five against 3 of 3 absent; Wide audit found 36 · 34 · 39 files against 37 · 32 · 38; every other pass count
is the same. The transcripts say why the skills did nothing to help: none of this pack's skills was invoked in any of the
twelve skills-present sessions. Only a bundled API reference fired (on Oracle migrate, three
times) and once a third-party plugin's debugging skill. These tasks give the skills nothing to fire on: nobody typed
a ceremony, no real design choice arose, no fix failed. Carrying the skills cost <!-- fig:doctrine.ratio.control.present_absent -->1.32<!-- /fig --> times as much (<!-- fig:doctrine.control.cost.present -->$29.30<!-- /fig --> against
<!-- fig:doctrine.control.cost.absent -->$22.16<!-- /fig --> for the four tasks), which the author puts down to the skill listings that sit in every prompt and the
reference reads (the transcripts show both; the cost records do not split the bill by cause).

*Corrected 28 September 2026: the twelve skills-present sessions of this run also listed the skills synced from the
author's claude.ai account: 254 skills in all, 7 of them this pack's, most of the others the account's; the
skills-absent sessions listed none. So that extra cost measured carrying all 254 listings, not this pack's skills alone.*

**What that means.** The skills change what a session does *when they are invoked*, which is what the graded
behaviour suite measures (its last full run: <!-- fig:run.full.result -->49/49<!-- /fig --> on <!-- fig:run.full.date -->2026-10-11<!-- /fig -->, with account sync and connectors
off). Merely present, they did not help on
tasks that never call them, and they are not free. If your work never reaches a close-out, a real choice or a
failed fix, install the ones you will type and leave the rest.

**Caveats.** Three runs per cell on one model at one effort; the four tasks were chosen because the skills have a
mechanism to act on them, and on these they still did not fire; two sessions were launched twice, one rerun after
it stopped mid-task and one relaunched after its first launch died; costs for the skills-absent arm were read from the session transcripts' own cost record (that
switch also removes the `/cost` command), a method that gives every one of the 137 `/cost` lines pasted for the harness's arms to the cent (re-checked
12 October 2026); the skills-present
arm's are the `/cost` lines pasted at the end of each session.

### The other half: the same tasks with the skills invoked (22 September 2026, evening)

The run above measured the skills present but never called. This one has the prompt call them, on the same four
tasks, same model and effort, doctrine off, three repeats: Review with the receipt mode on, Close-out by
typing the close-out ceremony, Oracle migrate with a real design choice put to the decision skill, Wide audit with
the receipt mode on. Twelve sessions, compared three ways with the two arms above, which were run the same way that
evening, under two hours earlier (launched 19:59–20:25 Pacific time, the invoked arm 21:42–21:46).

| Task | Absent (3 runs) | Present, not invoked (3 runs) | Invoked (3 runs) |
|---|---|---|---|
| Review: all five planted defects found | 3 of 3 | 2 of 3 | 2 of 3 |
| Review: a red herring flagged as a defect | 1 of 3 | 1 of 3 | 1 of 3 |
| Close-out: all nine outcome checks | 3 of 3 | 3 of 3 | 3 of 3 |
| Close-out: a handoff and an overview written, the pin re-derived | 0 of 3 | 0 of 3 | 3 of 3 |
| Oracle migrate: trap found, fixed, verified | 3 of 3 | 3 of 3 | 3 of 3 |
| Oracle migrate: the source of the change named | 1 of 3 | 1 of 3 | 3 of 3 |
| Oracle migrate: a decision record left in the repo | 0 of 3 | 0 of 3 | 3 of 3 |
| Oracle migrate: the old model id left in the env example | 3 of 3 | 3 of 3 | 3 of 3 |
| Wide audit: files found of 50 | 37 · 32 · 38 | 36 · 34 · 39 | 39 · 41 · 35 |
| Helper agents spawned | 0 in 12 | 0 in 12 | 0 in 12 |
| Cost of the four tasks | $7.76, $6.69, $7.71 | $10.36, $9.42, $9.52 | $10.88, $10.34, $11.56 |

<a href="control-run.svg"><picture><source media="(max-width: 1279px)" srcset="control-run-narrow.svg"><img src="control-run.svg" alt="Do the skills change outcomes? Absent, present, invoked, and redman typed"></picture></a>

On the chart a row is shaded where its counts differ between the columns: a share of runs (k of 3, or agents in
sessions) when it is not the same in every column, the three files-found counts only when every run of one column
lies above every run of another, and the cost never, being a sum; its cost row covers the two tasks the typed column
ran (below).

**Result: the outcome counts match the present arm's; what changes is what the session leaves behind.** Every named skill
loaded when asked (the receipt in 6 of 6, the close-out in 3 of 3, the decision skill in 3 of 3, read from the
transcripts). The nine tasks were built to be passable without any skill, and they are: the counts match the
uninvoked arm's (Review 2 of 3 again, Wide audit 39 · 41 · 35 files). The invoked arm is the only one that left a handoff, an overview and a re-derived pin after the
close-out (3 of 3, against none in the other arms). After Oracle migrate it left a decision record in 3 of 3, as did
every session with `/redman` typed (below), against none with the skills absent or present, and it named the source
of the change in 3 of 3, against 1 of 3 in each of those two arms. That is what the skills are for. It
cost <!-- fig:doctrine.control.cost.invoked -->$32.78<!-- /fig --> for the four tasks against <!-- fig:doctrine.control.cost.present -->$29.30<!-- /fig --> uninvoked and <!-- fig:doctrine.control.cost.absent -->$22.16<!-- /fig --> absent: <!-- fig:doctrine.ratio.control.invoked_present -->1.12<!-- /fig --> times the cost of carrying the skills
unused, <!-- fig:doctrine.ratio.control.invoked_absent -->1.48<!-- /fig --> times that of not having them.

*Corrected 28 September 2026: the skills-invoked sessions listed the same 254 skills, 7 of them this pack's, and the
skills-absent sessions none (see the note above); so the cost over not having them measured invoking this pack's skills
while carrying all 254 listings, not this pack's skills alone. The cost over carrying them unused compares two arms that
listed the same 254.*

**The side-buddy skill, typed.** `/redman` is marked so the model cannot invoke it; only a person typing it can, so
the twelve sessions above ran without it even where the prompt asked. A further six sessions (same model, effort
and tasks; Oracle migrate and Wide audit, the two tasks it has something to act on; three repeats) opened with
`/redman` typed as the first message and the task pasted second. It loaded in all six. On Oracle migrate it left a
findings ledger row beside every decision record, and one session was the only one of the control run's 42 sessions to clear the old model
id from the env example as well; outcome counts were otherwise level with the invoked arm (audit 40 · 43 · 35 files
of 50). On Wide audit its rule, file every finding as a row, met the prompt's "do not edit anything": two sessions
held the findings in the report and said so on the receipt, one filed the row and failed the task's no-edits
check. Cost $8.20, $8.28, $8.64 for the two tasks against $7.16, $6.73, $7.96 invoked without it.

**Caveats.** Three runs per cell on one model at one effort; the baselines were launched the same evening, under two
hours before the invoked arm and about two to two and a half before the typed one (22:28–22:29 Pacific time); the
typed arm ran two of the four tasks; the invoked and typed arms' costs, like the absent arm's, were read
from the session transcripts' own cost record; the close-out's "handoff written" row is what the ceremony is defined to do, so it measures compliance,
not benefit; one scorer check was corrected during scoring (it read the whole pin for the old next action and
failed a pin that kept it visibly marked superseded), every earlier configuration was rescored and none moved.

### The design, as settled before the run

The gap the run above closed: nothing here paired a task done *with* the skills against the same task done
*without* them. The doctrine comparison cannot stand in for it: each of its comparisons holds the skills the same in both arms
(none present on <!-- fig:doctrine.when.first -->2–3 September<!-- /fig -->, present on <!-- fig:doctrine.when.skills_present -->14–15 and 22 September<!-- /fig -->), so it measures the page, not the skills.

*Corrected 28 September 2026: this said the skills did not exist when those runs happened; that holds only for the runs
of <!-- fig:doctrine.when.first -->2–3 September<!-- /fig -->.*

The design:

| | |
|---|---|
| Sessions | 24 — four tasks × two arms × three repeats |
| Model | Claude Fable 5.1 at low effort |
| Arms | the skills present, against the skills folder emptied |
| Held constant | the doctrine off in both arms, so the skills are the only variable |
| Tasks | Review, Close-out, Oracle migrate and Wide audit — the four where these skills have any mechanism to act |
| Reported as | counts per cell ("2 of 3 against 0 of 3"), not an average of three |

Close-out is the sharpest of the four because its checks are outcome checks — did the pin marker move, did
the plan's row flip, were the counts re-derived, was the commit scoped to the right paths. A session can pass all of
them without ever invoking a skill, which is what makes it a fair test rather than a test of whether the skill ran.

Two things that run still will not answer: whether a fresh session genuinely starts better from a handoff (that
needs two sessions chained, a different rig), and whether any of this holds on a large codebase.

**The numbers were to be published on this page whichever way they came out**, including if the skills made no
measurable difference on some or all of the four tasks; they are above.

## Reproducing what ships here

```sh
python3 -m unittest discover -s tests   # the unit tests, no API cost
python3 evals/run.py --dry-run          # prints the totals (scenarios, graded sessions, checks) — runs nothing
python3 install.py --with-doctrine      # once: the sealed sessions take the doctrine from ~/.claude/doctrine
python3 evals/run.py --build-sealed     # once: builds the sealed config folder ~/.claude-evals
CLAUDE_CONFIG_DIR=~/.claude-evals claude auth login   # once: that folder's own login
python3 evals/run.py --quick            # the quick set of real sessions
python3 evals/run.py                    # the full set
python3 evals/run.py --recheck <run dir>  # grade a finished run again without spending anything
```

Each batch rebuilds the sealed folder (`--build-sealed` only builds it ahead of the login), then checks it before
any session starts. The doctrine link points at `~/.claude/doctrine` whatever `CLAUDE_CONFIG_DIR` says, so install
the doctrine there; without it the doctrine's hooks do not resolve and the runner refuses. The runner also refuses
unless `claude --version` is Claude Code <!-- fig:eval.sealed.cli_version -->2.1.295<!-- /fig -->, the version the sealed environment's lists of bundled
skills and built-in plugins were reviewed on (`cli_version` in `evals/sealed.json`), naming both versions: run that
version to reproduce the runs on this page. `--probe` launches on another version and grades each case's `sealed`
check as unreviewed, never a pass, so its results are not comparable with the run table's until that version's
lists are reviewed into `evals/sealed.json`.

As this tree ships: <!-- fig:tests.public -->523<!-- /fig --> unit tests; the quick set is <!-- fig:eval.quick -->19<!-- /fig --> real sessions, and its last run cost
<!-- fig:run.quick.cost -->$10.17<!-- /fig -->; the full set's last run cost <!-- fig:run.full.cost -->$33.03<!-- /fig -->.

Runs land in `~/xbt-evals/<timestamp>/`: one folder per case, each holding every turn's stream, the project, and its
snapshot after every turn (`record.json`), with the run's `grades.json` beside them. Nothing is uploaded anywhere.

If you run it and get a different result, that is worth knowing — open an issue with the run folder's `grades.json`
and the models you used. (Issues, not pull requests: this whole tree is generated, so a pull request would be
overwritten by the next build. The README says why.)
