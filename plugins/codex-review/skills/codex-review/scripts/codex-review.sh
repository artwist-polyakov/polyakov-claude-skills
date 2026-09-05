#!/bin/bash
# Main codex-review script: init, plan, code
# Usage: codex-review.sh <init|plan|code> <args> [--max-iter N]
#   init "task description"
#   plan --plan-file <path>   (reads file content, sends it to Codex on stdin)
#   code "description"
#   init|plan|code --description-file <path>  (reads file content instead of argv;
#                                             on plan it names the plan file)
#   init --task-label "<one line>"        (names the task in state.json)
#
# Exit codes:
#   0 — review received (APPROVED or CHANGES_REQUESTED)
#   1 — technical error (codex unavailable, invalid session_id)
#   2 — escalation (max iterations reached)
#   3 — no session (Claude should ask user to create one)

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck disable=SC1091
source "$SCRIPT_DIR/common.sh"

# --- Anti-recursion (primary defense) ---
guard_recursion

# --- Parse arguments ---
COMMAND="${1:-}"
if [[ -z "$COMMAND" ]]; then
    echo "Usage: codex-review.sh <init|plan|code> <args> [--max-iter N]" >&2
    exit 1
fi
shift

DESCRIPTION=""
DESCRIPTION_FILE=""
PLAN_FILE=""
TASK_LABEL=""
TASK_LABEL_SET=0
TASK_LABEL_JSON=""
MAX_ITER=""

while [[ $# -gt 0 ]]; do
    case "$1" in
        --plan-file)
            PLAN_FILE="$2"
            shift 2
            ;;
        --description-file)
            DESCRIPTION_FILE="$2"
            shift 2
            ;;
        --task-label)
            TASK_LABEL="$2"
            TASK_LABEL_SET=1
            shift 2
            ;;
        --max-iter)
            MAX_ITER="$2"
            shift 2
            ;;
        *)
            DESCRIPTION="$1"
            shift
            ;;
    esac
done

# --- Description or plan read from a file ---
# A description written in Markdown normally contains backticks, and a shell
# executes those as command substitution when the text is passed as an argument:
# the review then reaches Codex mangled, or the call dies with
# "some_identifier: command not found". Reading the text from a file keeps it
# verbatim.
#
# Verbatim includes the end of the file: command substitution strips trailing
# newlines, so the text is read with a sentinel character appended and the
# sentinel removed afterwards. Sets FILE_TEXT.
read_review_file() {
    # cat runs first in the && chain, so its failure is the status of the
    # assignment — otherwise the trailing printf would report success over a
    # file that was only half read.
    if ! FILE_TEXT="$(cat -- "$1" && printf 'x')"; then
        echo "ERROR: Could not read $1" >&2
        return 1
    fi
    FILE_TEXT="${FILE_TEXT%x}"
}

# A file holding nothing but whitespace has no review in it.
file_text_is_blank() {
    [[ -z "${FILE_TEXT//[[:space:]]/}" ]]
}

if [[ -n "$DESCRIPTION_FILE" ]]; then
    if [[ -n "$PLAN_FILE" ]]; then
        echo "ERROR: Use either --plan-file or --description-file, not both." >&2
        exit 1
    fi
    if [[ -n "$DESCRIPTION" ]]; then
        echo "ERROR: Description given both inline and via --description-file. Pass it one way." >&2
        exit 1
    fi
    if [[ ! -f "$DESCRIPTION_FILE" ]]; then
        echo "ERROR: Description file not found: $DESCRIPTION_FILE" >&2
        exit 1
    fi
    if [[ "$COMMAND" == "plan" ]]; then
        # A plan is a description that happens to live in a file, and --plan-file
        # already reads one. The two options name the same input here.
        PLAN_FILE="$DESCRIPTION_FILE"
        DESCRIPTION_FILE=""
    else
        read_review_file "$DESCRIPTION_FILE" || exit 1
        if file_text_is_blank; then
            echo "ERROR: Description file is empty: $DESCRIPTION_FILE" >&2
            exit 1
        fi
        DESCRIPTION="$FILE_TEXT"
    fi
fi

# --- Validate arguments per command ---
if [[ "$COMMAND" == "plan" ]]; then
    if [[ -z "$PLAN_FILE" ]]; then
        echo "ERROR: --plan-file is required for plan review." >&2
        echo "Usage: codex-review.sh plan --plan-file <path> [--max-iter N]" >&2
        exit 1
    fi
    if [[ ! -f "$PLAN_FILE" ]]; then
        echo "ERROR: Plan file not found: $PLAN_FILE" >&2
        exit 1
    fi
    read_review_file "$PLAN_FILE" || exit 1
    if file_text_is_blank; then
        echo "ERROR: Plan file is empty: $PLAN_FILE" >&2
        exit 1
    fi
    DESCRIPTION="$FILE_TEXT"
elif [[ -z "$DESCRIPTION" && "$COMMAND" != "status" ]]; then
    echo "ERROR: Description is required." >&2
    echo "Usage: codex-review.sh <init|code> \"description\" [--max-iter N]" >&2
    echo "   or: codex-review.sh <init|code> --description-file <path> [--max-iter N]" >&2
    exit 1
fi

# --- Task label that names this session in state.json (init only) ---
# Checked here, before the session is created or anything is archived, so a
# rejected label costs nothing.
if [[ $TASK_LABEL_SET -eq 1 && "$COMMAND" != "init" ]]; then
    echo "ERROR: --task-label applies to init — it names the task for the whole session." >&2
    exit 1
fi
if [[ "$COMMAND" == "init" ]]; then
    if [[ $TASK_LABEL_SET -eq 1 ]]; then
        TASK_LABEL_JSON="$(task_label_for_state "$TASK_LABEL" \
            'Fix the value passed to --task-label.')" || exit 1
    else
        # The description is a document, not a name: its surrounding
        # whitespace — a trailing newline above all — is not part of what the
        # caller called the task.
        LABEL_FROM_DESCRIPTION="$DESCRIPTION"
        LABEL_FROM_DESCRIPTION="${LABEL_FROM_DESCRIPTION#"${LABEL_FROM_DESCRIPTION%%[![:space:]]*}"}"
        LABEL_FROM_DESCRIPTION="${LABEL_FROM_DESCRIPTION%"${LABEL_FROM_DESCRIPTION##*[![:space:]]}"}"
        TASK_LABEL_JSON="$(task_label_for_state "$LABEL_FROM_DESCRIPTION" \
            'Name the task yourself: --task-label "<one line>". The full description still goes to Codex and to codex-init.request.md.')" || exit 1
    fi
fi

# --- Load config & state ---
REVIEW_ROOT="$(get_review_root)"
load_config "$REVIEW_ROOT"

# The limit is checked as soon as both of its sources are known and before
# anything else runs — the codex probe included: it reaches state.json as a JSON
# number, and a value the file cannot hold would otherwise surface only at the
# write that follows the codex call, with the session already opened.
MAX_ITERATIONS="$(state_counter_value "${MAX_ITER:-$CODEX_MAX_ITERATIONS}" "the iteration limit" \
    'Pass --max-iter <N>, or fix CODEX_MAX_ITERATIONS in .codex-review/config.env.')" || exit 1

check_codex_installed

STATE_DIR="$(get_state_dir "$REVIEW_ROOT")"
unset REVIEW_ROOT

# Held for the whole command. Everything that follows reads the state, sends a
# round and writes the result back, and that sequence has to be one run's alone.
acquire_state_lock "codex-review.sh $COMMAND" || exit 1

SESSION_ID="$(get_effective_session_id)"

# --- Build the flags shared by every codex exec call ---
# Keeping one list prevents init and review calls from drifting apart. The
# wrapper also avoids expanding an empty array under macOS Bash 3.2 + set -u.
CODEX_EXEC_FLAGS=()
if [[ -n "$CODEX_MODEL" ]]; then
    CODEX_EXEC_FLAGS+=("--model" "$CODEX_MODEL")
fi

# codex exec has no dedicated flag for this; the value reaches the CLI as a
# config override and is parsed as TOML, hence the inner quotes.
if [[ -n "$CODEX_REASONING_EFFORT" ]]; then
    CODEX_EXEC_FLAGS+=("-c" "model_reasoning_effort=\"$CODEX_REASONING_EFFORT\"")
fi

if [[ "$CODEX_FAST_MODE" == "true" ]]; then
    CODEX_EXEC_FLAGS+=("-c" 'service_tier="fast"')
fi

if [[ "$CODEX_YOLO" == "true" ]]; then
    CODEX_EXEC_FLAGS+=("--yolo")
fi

run_codex_exec() {
    if [[ ${#CODEX_EXEC_FLAGS[@]} -eq 0 ]]; then
        CODEX_REVIEWER=1 codex exec "$@"
    else
        CODEX_REVIEWER=1 codex exec "${CODEX_EXEC_FLAGS[@]}" "$@"
    fi
}

# --- Reviewer role prompt (reusable base) ---
reviewer_role_prompt() {
    cat <<'ROLE'
You are a code reviewer for this project.
You will review plans and code changes submitted by another AI agent (Claude Code).

Focus areas:
- Code quality, readability, maintainability
- Bugs, edge cases, error handling
- Security vulnerabilities
- Architecture and design decisions
- Test coverage adequacy

When reviewing:
- You can inspect the repository yourself — you are in the same working directory
- If the work is acceptable, respond with APPROVED
- If changes are needed, provide specific actionable feedback
- Do NOT run scripts from .codex-review/ — you are the reviewer, not the implementer
- Do NOT look into .codex-review/archive/ — it contains previous session artifacts and is not relevant
- IMPORTANT: This is a non-interactive session. Never ask for confirmation, permission, or clarification — act immediately on instructions
ROLE
}

# --- Default reviewer prompt for init ---
default_reviewer_prompt() {
    local task_desc="$1"
    local marker="$2"
    local role
    role="$(reviewer_role_prompt)"
    cat <<PROMPT
$role

Task: $task_desc

This message sets up your reviewer role. Plan and code reviews will arrive as follow-up messages — you will inspect the codebase then.
For now, confirm you are ready by responding with "Ready for review".
[session-marker: $marker]
PROMPT
}

# --- Custom init prompt (role + user instructions) ---
custom_init_prompt() {
    local custom_instructions="$1"
    local task_desc="$2"
    local marker="$3"
    local role
    role="$(reviewer_role_prompt)"
    cat <<PROMPT
$role

$custom_instructions

Task: $task_desc
[session-marker: $marker]
PROMPT
}

# --- Extract session_id from codex output (fallback method) ---
extract_session_id() {
    local output="$1"
    local sid
    sid=$(echo "$output" | grep -oE 'sess_[a-zA-Z0-9_-]+' | head -1)
    if [[ -z "$sid" ]]; then
        sid=$(echo "$output" | grep -oE '[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}' | head -1)
    fi
    echo "$sid"
}

# --- Extract session_id from log or marker, exit on failure ---
resolve_new_session_id() {
    local marker="$1"
    local log_file="$2"

    local new_session_id
    new_session_id="$(find_session_by_marker "$marker")"

    if [[ -z "$new_session_id" ]]; then
        echo "Marker search failed, trying log regex..." >&2
        new_session_id="$(extract_session_id "$(cat "$log_file" 2>/dev/null)")"
    fi

    if [[ -z "$new_session_id" ]]; then
        echo "WARNING: Could not extract session_id." >&2
        echo "Log from codex:" >&2
        cat "$log_file" >&2
        echo "" >&2
        echo "Please set session_id manually:" >&2
        echo "  bash codex-state.sh set session_id <YOUR_SESSION_ID>" >&2
        exit 1
    fi

    echo "$new_session_id"
}

# --- Save review note ---
save_note() {
    local phase="$1"
    local iteration="$2"
    local content="$3"
    # The iteration number is not unique over the life of a branch:
    # `codex-state.sh reset` starts the count over while the notes of the
    # previous cycle stay on disk. A taken name is stepped over the way
    # next_attempt_log steps over a taken log.
    local note_base="$STATE_DIR/notes/${phase}-review-${iteration}"
    local note_file="${note_base}.md"
    local attempt=2
    while [[ -e "$note_file" ]]; do
        note_file="${note_base}.${attempt}.md"
        attempt=$((attempt + 1))
    done
    {
        echo "# $(echo "$phase" | awk '{print toupper(substr($0,1,1)) substr($0,2)}') Review #${iteration}"
        echo "Date: $(date -u +"%Y-%m-%dT%H:%M:%SZ")"
        echo ""
        echo "$content"
    } > "$note_file"
}

# --- Pick a log path that does not overwrite an earlier attempt ---
# A run that dies before writing its verdict (killed process, lost connection)
# leaves the reasoning in its log, and the iteration counter does not advance —
# so the next attempt would reuse the same name and destroy the only record.
next_attempt_log() {
    local base="$1"
    local candidate="$base.log"
    local attempt=2
    # Both files of the pair count as taken: the request is written before the
    # log exists, so a run killed in between would otherwise have its request
    # overwritten by the retry.
    while [[ -e "$candidate" || -e "${candidate%.log}.request.md" ]]; do
        candidate="$base.$attempt.log"
        attempt=$((attempt + 1))
    done
    echo "$candidate"
}

# --- Update state.json ---
update_state() {
    local phase="$1"
    local iteration="$2"
    local status="$3"
    local reviews_completed="$4"
    local timestamp
    timestamp="$(date -u +"%Y-%m-%dT%H:%M:%SZ")"
    local task_desc
    task_desc="$(read_state_field "task_description")"

    write_state_fields \
        session_id="$SESSION_ID" \
        phase="$phase" \
        iteration="$iteration" \
        max_iterations="$MAX_ITERATIONS" \
        last_review_status="$status" \
        last_review_timestamp="$timestamp" \
        reviews_completed="$reviews_completed" \
        task_description="$task_desc"
}

# --- Format output ---
print_result() {
    local phase="$1"
    local iteration="$2"
    local max="$3"
    local session="$4"
    local response="$5"
    local status="$6"

    echo ""
    echo "=== CODEX REVIEW ==="
    echo "Phase: $phase"
    echo "Iteration: ${iteration}/${max}"
    echo "Session: $session"
    echo ""
    echo "$response"
    echo ""
    echo "=== END REVIEW ==="
    echo "Status: $status"
}

# --- Severity scale and verdict threshold ---
# One scale for both phases, so a finding means the same thing on a plan and on
# a diff. Only `## Blocking` sets the verdict; `## Pre-existing` keeps its own
# severity so a legacy defect is reported at its real weight instead of being
# flattened into the nice-to-have pile.
severity_calibration() {
    local phase="$1"
    local critical important minor phase_note scope_subject

    if [[ "$phase" == "plan" ]]; then
        scope_subject="the plan"
        critical="the plan as written leaves a break that must not ship: a main flow it does not
  cover, data lost or corrupted, an access check it drops, a migration or deploy it breaks, or a
  contract another component relies on that it changes without saying so"
        important="a real gap below that bar, with a scenario you can state in one sentence as
  \"who does what, in normal operation, and what goes wrong\": an actor the system has, inputs it
  produces, no coincidence of independent failures required. A plan that changes behaviour
  without naming how that behaviour will be verified is also important"
        minor="worth raising, does not hold up the plan. This is the ceiling for: a gap whose
  scenario needs a sub-second race window, simultaneous independent failures, or inputs the
  running system cannot produce; wording, section order, naming; a tightening that would be nice
  to have"
        phase_note="A plan does not have to enumerate every failure mode to be approvable: an item that belongs
under '## Non-blocking' may be deferred to the test plan, to implementation, or to a follow-up task."
    else
        scope_subject="this change"
        critical="this change introduces a break that must not ship: a wrong result on a path a
  caller reaches, data lost or corrupted, an access check that no longer holds, a crash on an
  ordinary input, or an error swallowed in a way that hides one of those. Name the mechanism:
  the input or state, then what happens"
        important="a real defect below that bar, with a scenario you can state in one sentence as
  \"who does what, in normal operation, and what goes wrong\": an actor the system has, inputs it
  produces, no coincidence of independent failures required. A bug fix shipping without the
  regression test that would fail without it is also important"
        minor="worth fixing, does not hold up the work. This is the ceiling for: a defect whose
  scenario needs a sub-second race window, simultaneous independent failures, or inputs the
  running system cannot produce; a missing test or coverage gap, apart from the regression-test
  rule above; a type that admits a state the code forbids; comment, naming, or wording"
        phase_note="Test hygiene, naming, and comment wording never set the verdict on their own."
    fi

    cat <<CALIBRATION

Severity scale — grade every finding with one of these three words and no other vocabulary
(no numeric ratings, no letter grades, no HIGH/MEDIUM/LOW):
- critical — $critical.
- important — $important.
- minor — $minor.

Between two levels, take the lower one: a finding whose severity you cannot back with a stated,
reachable scenario belongs one level down.

Group your findings under these headings, and use exactly these headings:
- '## Blocking' — critical or important findings in $scope_subject.
- '## Non-blocking' — minor findings in $scope_subject.
- '## Pre-existing' — defects that already exist in the files $scope_subject touches, found while
  reading them. Do not go looking beyond those files: code $scope_subject does not touch is
  outside this review. Grade what you do find on the same scale and keep the scenario line — a
  pre-existing critical finding is reported as critical here, never softened or left out because
  it is outside the current work. If $scope_subject makes a pre-existing defect reachable in a
  new way, it belongs under '## Blocking' instead — say explicitly what makes it newly reachable.

Verdict:
- Write CHANGES_REQUESTED when '## Blocking' has at least one entry.
- Write APPROVED otherwise. '## Pre-existing' and '## Non-blocking' never set the verdict.
  APPROVED with findings listed under them is a normal verdict, not a concession — say so
  plainly rather than inflating a minor finding to justify another round.
- $phase_note
- Propose no guard, branch, or fallback for a scenario the running system cannot produce.
  Report such a scenario as minor, spell the scenario out, and write 'fix: none — record only'.
- Accept a deferral of a '## Non-blocking' or '## Pre-existing' item the implementer answers with
  reasoning, instead of raising it again. A '## Blocking' finding closes on one of three things
  only: the new work leaves its scenario unreachable, the reasoning shows the scenario was never
  reachable, or the implementer states that the user decided to defer it. Say which one closed it.
  A '## Blocking' finding answered with a bare deferral stays open.
- Raise a finding from an earlier round again only when the new work still leaves its scenario
  reachable.
CALIBRATION
}

# --- Late-round narrowing ---
# Every round is free to open new ground, and the verdict never settles. From
# this round on, only a critical finding may open a new subject.
NARROW_FROM_ROUND=3

# --- Rounds that actually produced a review ---
# `reviews_completed` in state.json counts the rounds of the current review
# cycle that came back with a review. It is what narrowing is measured against:
# a run that came back without a verdict advances neither counter, a run that
# came back with one advances both, and note files outlive
# `codex-state.sh reset`, so the notes on disk do not track the current cycle.
# Reset to 0 by init, by a phase change, and by `codex-state.sh reset`; a
# state.json written before this field existed reads as 0.

late_round_narrowing() {
    local round="$1"
    cat <<NARROWING

Round $round of this phase. Earlier rounds have already covered this ground:
- Work through the '## Blocking' findings of the earlier rounds first and say, for each, whether
  it is closed, on the three conditions stated above and no others.
- Open a subject no earlier round raised only when it is critical. A subject first surfacing this
  late that is not critical is minor by this rule: report it under '## Non-blocking' and let the
  verdict stand on the earlier findings.
NARROWING
}

# --- Build phase-specific prompt ---
build_review_prompt() {
    local phase="$1"
    local description="$2"
    local round="${3:-1}"
    local skill_path
    skill_path="$(cd "$SCRIPT_DIR/.." && pwd)"

    local phase_instructions
    if [[ "$phase" == "plan" ]]; then
        phase_instructions="You are reviewing a proposed implementation plan.
The full plan text is provided above in 'Description from Claude'. Do NOT read plan files from disk — use the text above as the single source of truth.

Focus areas:
- Correctness: does the approach solve the stated problem?
- Completeness: are requirements and edge cases covered?
- Architecture: are there risks or better alternatives?
- Scope: not too broad, not too narrow?
- Clarity: is the implementation strategy clear and unambiguous?
- Readiness: is the plan specific enough to start coding — are there gaps, undefined decisions, or missing details that would block implementation?"
    else
        phase_instructions="You are reviewing code changes against the previously approved plan.

Focus areas:
- Plan adherence: does the implementation match the approved plan? Note any deviations or missing parts
- Correctness: bugs, edge cases, off-by-one errors
- Security: injection, auth, data exposure vulnerabilities
- Error handling: failure modes, missing validations
- Code quality: readability, maintainability, naming, structure
- Tests: are critical paths covered? Are tests meaningful, not just nominal?
- Merge readiness: is this code ready to merge as-is, or are there blockers?"
    fi

    local guide=""
    if [[ "$phase" == "plan" ]]; then
        guide="$CODEX_PLAN_GUIDE"
    else
        guide="$CODEX_CODE_GUIDE"
    fi

    local guide_section=""
    if [[ -n "$guide" ]]; then
        guide_section="
Additional review guidance from project maintainer:
$guide
"
    fi

    # With the calibration on, the threshold is stated once, in the scale above;
    # repeating a looser "if acceptable" here would compete with it.
    local calibration_section=""
    local verdict_lines="- If acceptable, respond with APPROVED
- If changes needed, provide specific actionable feedback"
    if [[ "$CODEX_SEVERITY_CALIBRATION" != "false" ]]; then
        calibration_section="$(severity_calibration "$phase")"
        if [[ $round -ge $NARROW_FROM_ROUND ]]; then
            calibration_section="$calibration_section
$(late_round_narrowing "$round")"
        fi
        verdict_lines="- The severity scale above sets the verdict and the headings above set where each finding goes
- Give every finding you report a specific, actionable fix, whichever heading it sits under"
    fi

    cat <<PROMPT
You are reviewing work by Claude Code on this project.
Phase: $phase

Description from Claude:
$description

$phase_instructions
$calibration_section
$guide_section
General instructions:
$verdict_lines
- You can inspect the code yourself — you're in the same directory
- The codex-review skill is at: $skill_path

After your review, write your verdict to $STATE_DIR/verdict.txt
Write exactly one word: APPROVED or CHANGES_REQUESTED
The directory exists. The file is cleared before each review — always create it fresh.
PROMPT
}

# =====================
# COMMAND: init
# =====================
cmd_init() {
    local task_desc="$DESCRIPTION"

    # Archive previous session artifacts
    archive_previous_session

    # Clear verdict to prevent stale auto-approve (AUTO_REVIEW hook)
    rm -f "$STATE_DIR/verdict.txt" "$STATE_DIR/verdict.phase"

    # Warn if config.env already has a session
    if [[ -n "${CODEX_SESSION_ID:-}" ]]; then
        echo "WARNING: CODEX_SESSION_ID is already set in config.env: $CODEX_SESSION_ID" >&2
        echo "Init will create a NEW session. Update config.env afterwards or remove CODEX_SESSION_ID to use state.json." >&2
    fi

    # Generate marker for session identification
    local marker
    marker="$(generate_uuid)"

    # Build reviewer prompt
    local prompt
    if [[ -n "$CODEX_REVIEWER_PROMPT" ]]; then
        prompt="$(custom_init_prompt "$CODEX_REVIEWER_PROMPT" "$task_desc" "$marker")"
    else
        prompt="$(default_reviewer_prompt "$task_desc" "$marker")"
    fi

    local output_file="$STATE_DIR/last_response.txt"
    local log_file="$STATE_DIR/codex-init.log"

    # The task text in full, beside the log of the run that opened the session.
    # state.json keeps only the caller's one-line label (see --task-label).
    printf '%s' "$task_desc" > "$STATE_DIR/codex-init.request.md"

    # The prompt goes to codex on stdin, so it is written to a file first. As an
    # argument it would be capped by the per-argument limit the kernel enforces
    # (128 KB on Linux), which a long task description reaches on its own.
    # A regular file also ends at EOF, so codex never waits on input the way it
    # did when stdin was an open pipe.
    local prompt_file="$STATE_DIR/codex-init.prompt.md"
    printf '%s' "$prompt" > "$prompt_file"

    echo "Creating Codex session..." >&2
    printf '\033[1;33m>>> Monitor: tail -f %s\033[0m\n' "$log_file" >&2

    # Run in the foreground, and so under the branch lock this command holds:
    # bash runs a trap for a signal received during a foreground command only
    # after that command finishes, so an interrupted run gives the branch back
    # when the reviewer has stopped writing to this state directory, not while.
    run_codex_exec \
        -o "$output_file" \
        - < "$prompt_file" > "$log_file" 2>&1 || {
        echo "ERROR: Failed to create Codex session." >&2
        cat "$log_file" >&2
        exit 1
    }

    # Extract session_id
    SESSION_ID="$(resolve_new_session_id "$marker" "$log_file")"

    write_state_fields \
        session_id="$SESSION_ID" \
        phase="initialized" \
        iteration=0 \
        max_iterations="$MAX_ITERATIONS" \
        last_review_status="" \
        last_review_timestamp="$(date -u +"%Y-%m-%dT%H:%M:%SZ")" \
        reviews_completed=0 \
        task_description="$TASK_LABEL_JSON"

    write_status
    echo "Session created: $SESSION_ID"
}

# =====================
# COMMAND: plan / code
# =====================
cmd_review() {
    local phase="$1"

    # Check session exists
    if [[ -z "$SESSION_ID" ]]; then
        echo ""
        echo "=== CODEX REVIEW ==="
        echo "Phase: $phase"
        echo ""
        echo "No active Codex session found."
        echo ""
        echo "=== END REVIEW ==="
        echo "Status: NO_SESSION"
        exit 3
    fi

    # A code review that came back APPROVED ends the cycle: STATUS.md is
    # removed and nothing else in this session refers to it. A round sent after
    # that is either the next task or more work on this one, and the two need
    # different things — a new session, or the counter back at one. Continuing
    # silently gives neither: the round arrives with a number the reviewer reads
    # as "late in this cycle, raise nothing new".
    local closed_phase closed_status
    closed_phase="$(read_state_field "phase")"
    closed_status="$(read_state_field "last_review_status")"
    if [[ "$closed_phase" == "code" && "$closed_status" == "APPROVED" ]]; then
        echo "ERROR: the last code review of this branch came back APPROVED — that cycle is closed." >&2
        echo "" >&2
        echo "Next task:            codex-review.sh init \"<task>\"" >&2
        echo "                      Archives this cycle and opens a new Codex session." >&2
        echo "More on this task:    codex-state.sh reset, then run this command again." >&2
        echo "                      The count starts at round 1 and the reviewer reads the" >&2
        echo "                      new work in full; the task name and session are kept." >&2
        echo "" >&2
        echo "Nothing was changed." >&2
        exit 1
    fi

    # What the branch carried into this command, read before anything resets
    # it: the phase change below zeroes the counters and stamps the timestamp
    # with the current time, and the line that names the task is the only place
    # a cycle left behind by an abandoned task becomes visible.
    local prior_rounds prior_reviews prior_round_time prior_task
    prior_rounds="$(read_state_number "iteration")"
    prior_reviews="$(read_state_number "reviews_completed")"
    prior_round_time="$(read_state_field "last_review_timestamp")"
    prior_task="$(read_state_field "task_description")"

    # Reset iteration counter on phase change (e.g. plan → code)
    local previous_phase
    previous_phase="$(read_state_field "phase")"
    if [[ -n "$previous_phase" && "$previous_phase" != "$phase" ]]; then
        local task_desc
        task_desc="$(read_state_field "task_description")"
        write_state_fields \
            session_id="$SESSION_ID" \
            phase="$previous_phase" \
            iteration=0 \
            max_iterations="$MAX_ITERATIONS" \
            last_review_status="" \
            last_review_timestamp="$(date -u +"%Y-%m-%dT%H:%M:%SZ")" \
            reviews_completed=0 \
            task_description="$task_desc"
        echo "Phase changed ($previous_phase → $phase), iteration counter reset." >&2
    fi

    # Check iteration limit
    local current_iteration
    current_iteration="$(read_state_number "iteration")"
    local next_iteration=$((current_iteration + 1))

    if [[ $next_iteration -gt $MAX_ITERATIONS ]]; then
        echo ""
        echo "=== CODEX REVIEW ==="
        echo "Phase: $phase"
        echo "Iteration: ${next_iteration}/${MAX_ITERATIONS}"
        echo "Session: $SESSION_ID"
        echo ""
        echo "Maximum iterations ($MAX_ITERATIONS) reached."
        echo "Review notes are in: $STATE_DIR/notes/"
        echo ""
        echo "=== END REVIEW ==="
        echo "Status: ESCALATE"
        exit 2
    fi

    # Save plan file copy for history
    if [[ "$phase" == "plan" && -n "$PLAN_FILE" ]]; then
        cp -- "$PLAN_FILE" "$STATE_DIR/plan.md"
        echo "Plan saved to: $STATE_DIR/plan.md" >&2
    fi

    local codex_prompt
    # Narrowing counts rounds that came back with a review. A call that came
    # back without a verdict advances neither counter, and a counter moved by
    # hand does not make a round, so the two numbers can differ.
    local reviews_completed review_round
    reviews_completed="$(read_state_number "reviews_completed")"
    review_round=$((reviews_completed + 1))

    codex_prompt="$(build_review_prompt "$phase" "$DESCRIPTION" "$review_round")"

    # Both files this run is read back through are cleared before the call, so
    # whatever is found afterwards was written by this run. A reply left by the
    # previous round would otherwise be filed as this round's note.
    local output_file="$STATE_DIR/last_response.txt"
    rm -f "$STATE_DIR/verdict.txt" "$STATE_DIR/verdict.phase" "$output_file"

    # Call codex with resume
    local log_file
    log_file="$(next_attempt_log "$STATE_DIR/codex-${phase}-${next_iteration}")"

    # What was sent, stored next to the log of the run that sent it. A run that
    # dies before its verdict otherwise leaves no record of what it reviewed.
    printf '%s' "$DESCRIPTION" > "${log_file%.log}.request.md"

    # The prompt goes to codex on stdin — see cmd_init for why it is a file.
    local prompt_file="${log_file%.log}.prompt.md"
    printf '%s' "$codex_prompt" > "$prompt_file"

    # Every round names the task it belongs to. A cycle left behind by an
    # abandoned task is indistinguishable from one still in progress, and the
    # name is what tells them apart, so it is printed unconditionally rather
    # than on a rule about which rounds deserve it. A round that follows
    # earlier ones on this branch also says when the last of them ran.
    if [[ $prior_rounds -ge 1 || $prior_reviews -ge 1 ]]; then
        echo "Continuing task: ${prior_task:-<unnamed>} (previous round ${prior_round_time:-unknown})." >&2
    else
        echo "Task: ${prior_task:-<unnamed>}." >&2
    fi

    echo "Sending $phase for review (iteration ${next_iteration}/${MAX_ITERATIONS})..." >&2
    printf '\033[1;33m>>> Monitor: tail -f %s\033[0m\n' "$log_file" >&2

    # Run in the foreground — see cmd_init for what that settles about the
    # branch lock and signals.
    local exit_code=0
    run_codex_exec \
        -o "$output_file" \
        resume "$SESSION_ID" \
        - < "$prompt_file" > "$log_file" 2>&1 || exit_code=$?

    # The verdict file is the whole answer. It is removed before every request,
    # so a valid word in it was written by this run: the review happened, the
    # round is spent and that word is the verdict, whatever the exit status says
    # afterwards. Nothing in it means no round — an exhausted quota, a dropped
    # network, a dead session or a reviewer that answered without deciding would
    # otherwise walk the counter to the limit and escalate a cycle in which
    # nothing was ever settled.
    local status
    status="$(parse_verdict_file "$STATE_DIR/verdict.txt")"

    local output
    output=$(cat "$output_file" 2>/dev/null || echo "")

    if [[ -z "$status" ]]; then
        if [[ $exit_code -ne 0 ]]; then
            echo "ERROR: Codex exec failed (exit $exit_code)." >&2
            cat "$log_file" >&2
        else
            echo "ERROR: the reviewer returned no verdict." >&2
            echo "Expected APPROVED or CHANGES_REQUESTED in $STATE_DIR/verdict.txt." >&2
        fi
        # The reply is kept beside the attempt's own log rather than filed as a
        # round's note: there was no round to file it under, and the next
        # attempt reuses the same iteration number.
        if [[ -n "${output//[[:space:]]/}" ]]; then
            printf '%s' "$output" > "${log_file%.log}.reply.md"
            echo "The reply is kept in ${log_file%.log}.reply.md" >&2
        fi
        # STATUS.md is rewritten too: left alone it keeps showing the previous
        # round and its status, and the error stays invisible in the file a
        # human reads.
        update_state "$phase" "$current_iteration" "ERROR" "$reviews_completed"
        write_status
        echo "Iteration not consumed: still ${current_iteration}/${MAX_ITERATIONS}." >&2
        exit 1
    fi

    # The phase that asked travels with the verdict. The ExitPlanMode hook of
    # AUTO_REVIEW mode reads verdict.txt on its own, and an APPROVED left by a
    # code review would otherwise let the next task's plan through unreviewed.
    printf '%s\n' "$phase" > "$STATE_DIR/verdict.phase"

    if [[ $exit_code -ne 0 ]]; then
        echo "WARNING: codex exec exited $exit_code after writing its verdict; the round counts." >&2
        # The call died before writing a reply. The note says that rather than
        # sitting there empty.
        if [[ -z "${output//[[:space:]]/}" ]]; then
            output="codex exec exited $exit_code after writing the verdict $status. No reply text was saved; the run is logged in ${log_file##*/}, one level up from this note."
        fi
    fi

    # Save note
    save_note "$phase" "$next_iteration" "$output"

    # Update state
    update_state "$phase" "$next_iteration" "$status" "$review_round"

    # Update or remove STATUS.md
    if [[ "$phase" == "code" && "$status" == "APPROVED" ]]; then
        remove_status
    else
        write_status
    fi

    # Print result
    print_result "$phase" "$next_iteration" "$MAX_ITERATIONS" "$SESSION_ID" "$output" "$status"
}

# --- Main ---
case "$COMMAND" in
    init)   cmd_init ;;
    plan)   cmd_review "plan" ;;
    code)   cmd_review "code" ;;
    *)
        echo "Usage: codex-review.sh <init|plan|code> <args> [--max-iter N]" >&2
        echo "" >&2
        echo "Commands:" >&2
        echo "  init \"task\"                    Create a new Codex session for the given task" >&2
        echo "  plan --plan-file <path>        Submit plan for review (reads file, sends it on stdin)" >&2
        echo "  code \"description\"             Submit code for review" >&2
        echo "" >&2
        echo "Options:" >&2
        echo "  --description-file <path>      Read the init/code description from a file" >&2
        echo "                                 instead of argv (keeps backticks verbatim)" >&2
        echo "  --task-label \"<one line>\"      init: names the task in state.json." >&2
        echo "                                 Required when the description spans" >&2
        echo "                                 more than one line" >&2
        echo "  --max-iter N                   Override the iteration limit for this call" >&2
        echo "" >&2
        echo "Exit codes:" >&2
        echo "  0 — Review received (APPROVED or CHANGES_REQUESTED)" >&2
        echo "  1 — Technical error" >&2
        echo "  2 — Escalation (max iterations)" >&2
        echo "  3 — No session" >&2
        exit 1
        ;;
esac
