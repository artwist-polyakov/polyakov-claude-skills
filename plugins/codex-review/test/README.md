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
├── test-description-file.sh    # --description-file, per-attempt logs, saved request
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
10. Opening a new session archives the saved requests together with their logs,
   leaving none behind for the next attempt numbering to overwrite.

Does **not** require the `codex` binary — a stub on `PATH` records the prompt
and writes the verdict.

Run:

```sh
sh plugins/codex-review/test/test-description-file.sh
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

All four scripts exit `0` on success and `1` if any assertion failed.
`test-e2e.sh` additionally exits `0` (skip) when `CODEX_E2E` is not set.
