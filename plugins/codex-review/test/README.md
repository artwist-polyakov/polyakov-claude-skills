# codex-review tests

Test suite for the `codex-review` plugin. All tests target the production
scripts under `plugins/codex-review/skills/codex-review/scripts/` — there are
no separate test copies.

Run everything from the repository root.

## Layout

```
test/
├── test-auto-approve-plan.sh   # unit tests for the auto-approve hook
├── test-integration.sh         # path-contract tests (hook ↔ state dir)
├── test-state-cache.sh         # cached state path and batched STATUS.md reads
├── test-state-set.sh           # `codex-state.sh set`, one state.json writer
├── test-failure-iteration.sh   # what a failed codex call costs
├── test-verdict-source.sh      # the verdict file decides, the reply never does
├── test-description-file.sh    # --description-file, per-attempt logs, saved request
├── test-severity-calibration.sh # severity scale, finding headings, verdict threshold
├── test-exec-flags.sh          # model and reasoning effort on every codex exec call
├── test-e2e.sh                 # opt-in end-to-end with real codex / claude
└── test-fixtures/              # plan markdown fixtures used by test-e2e.sh
    ├── approve_plan.md         # trivial plan → APPROVED
    ├── reject_plan.md          # asks Codex for CHANGES_REQUESTED
    └── resubmit_plan.md        # resubmit after reject → APPROVED
```

## test-auto-approve-plan.sh

Unit tests for `scripts/auto-approve-plan.sh` — the `PreToolUse` hook that
gates `ExitPlanMode` on the stored verdict.

Covers:

- `AUTO_REVIEW` unset / `false` / `true` / quoted / `export` / leading whitespace
- cold-start deny (no `verdict.txt`)
- `APPROVED` → allow + `verdict.txt` deletion
- `CHANGES_REQUESTED` → deny with resubmit instruction
- stale-verdict guard (second call after allow must deny)
- verdict sanitization (quotes, backslashes, all-garbage, empty) → valid JSON
- `plugin.json` hook commands must use `${CLAUDE_PLUGIN_ROOT}`, not relative paths

No external binaries required (uses `python3` or `jq` for JSON validation;
skips that assertion if neither is available).

Run:

```sh
sh plugins/codex-review/test/test-auto-approve-plan.sh
```

## test-integration.sh

Path-contract tests. Catches drift between where `codex-state.sh dir`
(via `common.sh`) computes the state directory and where the standalone
POSIX `auto-approve-plan.sh` hook looks for `verdict.txt`. A mismatch
would make auto-approve silently stop working.

Scenarios:

1. Basic `main` branch with a commit.
2. Slashed branch name (`feat/my-feature` → `feat-my-feature`).
3. Fresh repo without commits — both sides must use `symbolic-ref`
   (regression: `rev-parse --abbrev-ref HEAD` returns `HEAD` here).
4. Git worktree — state dir must resolve to the MAIN repo
   (via `--git-common-dir`), not the worktree.

Does **not** require the `codex` binary.

Run:

```sh
sh plugins/codex-review/test/test-integration.sh
```

## test-state-cache.sh

Regression tests for state reads on the hot path. Covers:

- `write_status` and the standalone readers reuse the `STATE_DIR` already
  resolved by the entry script;
- state and config helpers can reuse one explicitly resolved review root;
- `write_status` derives the branch from that same state directory;
- string and numeric readers can parse one in-memory snapshot after the source
  file is no longer available;
- archive summaries read their string fields through the same shared snapshot
  parser;
- missing values keep their existing empty-string / zero defaults.

The test checks observable calls and output rather than a machine-dependent
timing threshold.

Run:

```sh
sh plugins/codex-review/test/test-state-cache.sh
```

## test-state-set.sh

Regression tests for `codex-state.sh set` and for `render_state_fields` in
`common.sh` — the one place that describes the shape of `state.json`.

Scenarios:

1. The three counters (`iteration`, `max_iterations`, `reviews_completed`) are
   actually written, a counter can be set back to zero, and `STATUS.md` follows
   the stored numbers.
2. An unknown field name and a counter value that is not a non-negative integer
   exit `1` with a message naming the field, and leave `state.json` byte for
   byte as it was.
3. Writing one field keeps every other field, `reviews_completed` included.
4. A value `state.json` could not give back unchanged — a double quote, a
   backslash, a second line, a control character — is refused rather than
   stored or repaired, and an ordinary value is stored exactly as passed.
5. `set` against a missing `state.json` writes the whole file, with
   `max_iterations` taken from `config.env`.
6. The renderer refuses a field left out, a field given twice, and an argument
   without a name; neither entry script carries its own copy of the file's
   literal.

Does **not** require the `codex` binary. JSON validity is asserted through
`python3` or `jq`; that one assertion is skipped if neither is available.

Run:

```sh
sh plugins/codex-review/test/test-state-set.sh
```

## test-description-file.sh

Covers the file-based description path and the artefacts a review run leaves
behind.

Scenarios:

1. `--description-file` refuses a missing file, a file holding nothing but
   blank lines, a description also passed inline, and a `--plan-file` passed
   alongside it. Every refusal in the suite is checked for its message and for
   exit `1` (`assert_refusal`). On `plan` the option names the plan file, the
   same input `--plan-file` names.
2. A read that dies part way through is reported as a read error, not as an
   empty file, and nothing is sent: a `cat` stub on `PATH` prints half the text
   and fails, so the case runs for root as well. A path whose name starts with
   a dash is treated as a path, not as an option.
3. Text read from the file reaches the Codex prompt verbatim — backticks
   included, which is what passing the same text as an argument destroys. The
   saved copy matches the source file byte for byte, trailing blank lines and a
   missing final newline included (`cmp`), for a review and for the task text
   `init` stores alike.
4. A log left by an earlier attempt at the same iteration is kept; the retry
   writes `codex-<phase>-<N>.2.log` beside it. A request saved by an attempt
   that died before its log appeared holds the number just as a log does.
5. The description sent for review is stored next to that attempt's log as
   `codex-<phase>-<N>.request.md`.
6. `init` refuses a description longer than one line unless `--task-label` names
   the task, and the refusal lands before the session is opened — no log, no
   request file, nothing recorded.
7. With `--task-label` given, `state.json` stays valid JSON and stores the label
   exactly as passed, `STATUS.md` points at the full text, and the description
   itself is kept in `codex-init.request.md`.
8. A label that would not survive being stored and read back is refused rather
   than repaired: a double quote, a backslash, a carriage return, a tab, a space
   at either end, more than 200 characters, two lines, or empty — an empty
   `--task-label` is an error, not a request to fall back to the description. So
   is `--task-label` passed to a phase other than `init`, empty value included.
9. A single-line description still names itself — no label needed. Read from a
   file, its trailing newline is not carried into the name.
10. The skill's README recommends the single `.codex-review/` rule, so current
    and future review artefacts stay local without maintaining a file-by-file
    ignore list.
11. Opening a new session archives the saved requests and the prompts together
    with their logs, leaving none behind for the next attempt numbering to
    overwrite — `codex-init.prompt.md` carries a fixed name, so a prompt left in
    place would be overwritten rather than kept.
12. A plan past the 128 KB the kernel allows in one argument is still sent whole:
    the run succeeds, the prompt the stub receives holds the plan's last line,
    and the prompt is kept beside the log as `codex-<phase>-<N>.prompt.md`.
13. `init` sends a task of that size whole as well — it builds and sends its
    prompt on its own path, and keeps it in `codex-init.prompt.md`.

Does **not** require the `codex` binary — a stub on `PATH` records the prompt
and writes the verdict. The prompt reaches it on stdin, which is how
`codex-review.sh` sends it: the stub is called with `-` in place of the text.

Run:

```sh
sh plugins/codex-review/test/test-description-file.sh
```

## test-severity-calibration.sh

Covers the severity calibration `build_review_prompt` adds to every review it
sends.

Scenarios:

1. The three-word scale, the tie-break rule and the ban on any other severity
   vocabulary reach Codex on the code phase, together with the three headings
   `## Blocking`, `## Non-blocking` and `## Pre-existing` and the threshold that
   ties `CHANGES_REQUESTED` to a non-empty `## Blocking`.
2. A defect that already exists in the files the change touches keeps its own
   severity under `## Pre-existing` instead of being flattened into the
   nice-to-have pile, and moves to `## Blocking` when the change makes it
   reachable in a new way. The heading is scoped to those files — the reviewer is
   told not to roam the rest of the repository.
3. The plan phase gets the same scale in its own wording — what the plan leaves
   broken, an unverified behaviour change, a plan that need not enumerate every
   failure mode — with no code-phase wording leaking in.
4. The late-round narrowing is absent on rounds 1 and 2, appears on round 3,
   names the round it was sent for, and caps a subject first raised that late at
   `minor` unless it is `critical`.
5. `CODEX_SEVERITY_CALIBRATION=false` restores the previous prompt: no scale, no
   headings, and the two original verdict lines back in place.
6. A codex call that fails spends neither a round nor an iteration: after a
   failed call the next two reviews still carry no narrowing and the third one
   does. A counter moved on its own does not become the round number — the
   review sent under iteration 5 reports round 3.
7. Clearing the cycle with the state helper starts the round count over — notes
   from the previous cycle survive it and must not push the next review into a
   narrowed round.
8. A project's `CODEX_CODE_GUIDE` still reaches Codex alongside the calibration.

Does **not** require the `codex` binary — a stub on `PATH` accepts the prompt on
stdin and writes the verdict; the assertions read the prompt copy the run keeps
as `codex-<phase>-<N>.prompt.md`. A `fail-next` marker file makes the stub fail
one review call, and only a review call: `common.sh` probes `codex --version`
before every run, and failing that probe would abort before any review ran.
`run_review` clears every setting `load_config` reads, so an exported
`CODEX_SEVERITY_CALIBRATION` or `CODEX_MAX_ITERATIONS` cannot decide the
outcome of an assertion.

Run:

```sh
sh plugins/codex-review/test/test-severity-calibration.sh
```

## test-exec-flags.sh

Covers the flags `codex-review.sh` puts on every `codex exec` call.

Scenarios:

1. `CODEX_MODEL` and `CODEX_REASONING_EFFORT` reach the call that opens the
   session as `--model <name>` and `-c model_reasoning_effort="<effort>"`, and
   the review that follows carries the same pair.
2. The two call sites open with an identical flag block — everything up to
   `-o`, where the per-call arguments begin. A flag added to one site and not
   the other fails here, which is what keeps a session from being created under
   one setting and reviewed under another.
3. With neither setting configured, no `--model` and no `-c` are passed at all,
   and no empty argument goes out in their place.

Does **not** require the `codex` binary — a stub on `PATH` records the argv of
every exec call, one argument per line, as `argv-<N>.txt` beside the repo. The
`codex --version` probe `common.sh` runs before every review is not an exec
call and is not recorded, so `argv-1.txt` is the session call and `argv-2.txt`
the review.

`run_review` clears every setting `load_config` reads before each run, so the
assertions answer to the repo's own `config.env` and not to a `CODEX_MODEL` or
`CODEX_REASONING_EFFORT` exported on the machine running the suite.

Run:

```sh
sh plugins/codex-review/test/test-exec-flags.sh
```

## test-failure-iteration.sh

Regression tests for what a `codex exec` that fails costs the review cycle.

Scenarios:

1. A call that comes back with nothing exits `1`, leaves both counters where
   they were, stores `ERROR`, writes no review note, and says the iteration was
   not consumed.
2. `STATUS.md` is rewritten for that failure instead of keeping the previous
   round and its status.
3. Six failures in a row leave the counter at zero — a run of dropped calls
   cannot walk a cycle to its limit and escalate a review that never happened.
4. A review that does come back spends its iteration and its round.
5. A call that wrote its verdict and then died still counts: the round is spent,
   the rescued verdict is the stored status, and the note says the reply was
   never written rather than carrying the previous round's reply.
6. A stale reply file on disk does not rescue a failed call.
7. A rescued `APPROVED` closes the cycle the same way a normal one does, down to
   removing `STATUS.md`.

Does **not** require the `codex` binary — a stub on `PATH` is told per call
whether to fail and whether to leave a verdict behind first.

Run:

```sh
sh plugins/codex-review/test/test-failure-iteration.sh
```

## test-verdict-source.sh

Regression tests for where the verdict comes from. The verdict file is the whole
answer; the reply text never decides.

Scenarios:

1. A reply saying "Not APPROVED; changes are required" approves nothing: the run
   exits `1`, stores `ERROR` and spends no iteration.
2. A reply holding the word `APPROVED` decides nothing either, and no note is
   filed for a round that did not happen.
3. That reply is kept beside the attempt's log as `codex-<phase>-<N>.reply.md`,
   and the run says where it is.
4. The verdict file decides against the reply, both ways round, and those rounds
   are spent.
5. A verdict file holding anything but the two words — `APPROVED with caveats`,
   `approved`, `LGTM`, nothing at all — is no verdict.
6. A verdict written with blank lines and spaces around it still counts.
7. A kept reply is archived with the session it belongs to, so the next session
   reusing that attempt number cannot overwrite it.

Does **not** require the `codex` binary — a stub on `PATH` is told per call what
to write as the reply and what to write to the verdict file the prompt names.

Run:

```sh
sh plugins/codex-review/test/test-verdict-source.sh
```

## test-e2e.sh

Opt-in end-to-end tests that exercise real `codex` / `claude` CLIs.
Guarded by `CODEX_E2E=1` so CI and casual runs don't burn quota —
without the env var the script exits 0 with a skip message.

Prerequisites:

- `codex` binary in `PATH`, authenticated — always required
- `claude` binary in `PATH`, authenticated — only for the `stale` scenario
- Network access

Scenarios (selectable by name):

| name      | cost                              | what it tests |
|-----------|-----------------------------------|---------------|
| `approve`  | ~2 codex calls                   | init + approve cycle, hook allow, `verdict.txt` cleanup, stale guard on second call |
| `reject`   | ~3 codex calls                   | init + reject, hook deny w/ resubmit message, resubmit in same session → APPROVED |
| `filedesc` | ~3 codex calls                   | `--description-file` and `--task-label`: refusals cost no session, the label is stored as given, `state.json` stays valid, backticks and `$` reach codex verbatim, saved requests are archived with their logs |
| `stale`    | 1 real `claude` run + ~2 codex   | stale `.codex-review/<branch>/` artifacts from a prior task must be archived by `init` — must NOT silently auto-approve the new task |

Total for all scenarios: ~8 codex calls + 1 claude run, roughly 5–7 minutes.

`filedesc` is the only scenario that asserts something on the **codex side**
rather than in the plugin's own files: the description it sends carries
`` `beforeSend` ``, `$HOME` and a `$(…)` shape, and the check looks for that
text in the codex run log or in the reply codex wrote. Passed as a
command-line argument the shell would have executed all three first, so this
is what tells the file-based path apart from the old one.

The `stale` scenario invokes `claude -p --plugin-dir ...` with a
5-minute hard timeout. It intentionally does not assert the new plan
review completed — only that stale `state.json` / `verdict.txt` / notes
were moved into `.codex-review/archive/<ts>/` by `archive_previous_session`
in `common.sh`. See the comment block above `scenario_stale` in
`test-e2e.sh` for the rationale on why the production hook itself cannot
be exercised from a `-p` session.

Run:

```sh
# all scenarios
CODEX_E2E=1 sh plugins/codex-review/test/test-e2e.sh

# a subset
CODEX_E2E=1 sh plugins/codex-review/test/test-e2e.sh approve
CODEX_E2E=1 sh plugins/codex-review/test/test-e2e.sh approve reject
CODEX_E2E=1 sh plugins/codex-review/test/test-e2e.sh stale
```

## Fixtures

`test-fixtures/*.md` are plan-mode inputs fed to `codex-review.sh plan
--plan-file <fixture>` by the e2e scenarios:

- **`approve_plan.md`** — trivial "do nothing" plan; Codex should return
  `APPROVED`.
- **`reject_plan.md`** — explicitly instructs Codex to respond
  `CHANGES_REQUESTED`. Used to exercise the reject path.
- **`resubmit_plan.md`** — follow-up plan in the **same** Codex session
  that explicitly acknowledges the transition from the reject test and
  asks for `APPROVED`. A plain `approve_plan.md` would not work here
  because the earlier `reject_plan.md` instruction "sticks" in the
  session and keeps producing `CHANGES_REQUESTED`.

Two further fixtures are fed to `--description-file` by the `filedesc`
scenario — as an `init` task and as a code description, not as plans:

- **`task_description.md`** — a task text of several lines carrying a quoted
  phrase, a `C:\tmp\out` path and backticked identifiers: none of them can live
  in a `state.json` value, which is why that scenario has to pass
  `--task-label`.
- **`code_description.md`** — a code description carrying `` `beforeSend` ``,
  `$HOME` and a `$(…)` shape, asking for `APPROVED`. The metacharacters are the
  point: they are what a shell would have eaten on the argv path.

## Exit codes

All ten scripts exit `0` on success and `1` if any assertion failed.
`test-e2e.sh` additionally exits `0` (skip) when `CODEX_E2E` is not set.
