#!/bin/bash
# Main codex-review script: init, plan, code
# Usage: codex-review.sh <init|plan|code> <args> [--max-iter N]
#   init "task description"
#   plan --plan-file <path>   (reads file content, passes inline to Codex)
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
load_config
check_codex_installed

STATE_DIR="$(get_state_dir)"

MAX_ITERATIONS="${MAX_ITER:-$CODEX_MAX_ITERATIONS}"
SESSION_ID="$(get_effective_session_id)"

# --- Build yolo flags (as array to avoid word splitting) ---
YOLO_FLAG=()
if [[ "$CODEX_YOLO" == "true" ]]; then
    YOLO_FLAG=("--yolo")
fi

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

# --- Read verdict from file, fallback to text parsing ---
read_verdict() {
    local output="$1"
    local verdict_file="$STATE_DIR/verdict.txt"

    # Primary: read from verdict file (format-agnostic via helper)
    local file_verdict
    file_verdict="$(parse_verdict_file "$verdict_file")"
    if [[ "$file_verdict" == "APPROVED" || "$file_verdict" == "CHANGES_REQUESTED" ]]; then
        echo "$file_verdict"
        return
    fi

    # Fallback: parse response text
    if echo "$output" | grep -qiE '(^|\W)APPROVED(\W|$)'; then
        echo "APPROVED"
    else
        echo "CHANGES_REQUESTED"
    fi
}

# --- Save review note ---
save_note() {
    local phase="$1"
    local iteration="$2"
    local content="$3"
    local note_file="$STATE_DIR/notes/${phase}-review-${iteration}.md"
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
    local timestamp
    timestamp="$(date -u +"%Y-%m-%dT%H:%M:%SZ")"
    local task_desc
    task_desc="$(read_state_field "task_description")"

    write_state "{
  \"session_id\": \"$SESSION_ID\",
  \"phase\": \"$phase\",
  \"iteration\": $iteration,
  \"max_iterations\": $MAX_ITERATIONS,
  \"last_review_status\": \"$status\",
  \"last_review_timestamp\": \"$timestamp\",
  \"task_description\": \"$task_desc\"
}"
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

# --- Build phase-specific prompt ---
build_review_prompt() {
    local phase="$1"
    local description="$2"
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

    cat <<PROMPT
You are reviewing work by Claude Code on this project.
Phase: $phase

Description from Claude:
$description

$phase_instructions
$guide_section
General instructions:
- If acceptable, respond with APPROVED
- If changes needed, provide specific actionable feedback
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
    rm -f "$STATE_DIR/verdict.txt"

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

    echo "Creating Codex session..." >&2
    printf '\033[1;33m>>> Monitor: tail -f %s\033[0m\n' "$log_file" >&2

    local MODEL_FLAG=()
    if [[ -n "$CODEX_MODEL" ]]; then
        MODEL_FLAG=("--model" "$CODEX_MODEL")
    fi

    CODEX_REVIEWER=1 codex exec \
        "${MODEL_FLAG[@]}" \
        "${YOLO_FLAG[@]}" \
        -o "$output_file" \
        "$prompt" </dev/null > "$log_file" 2>&1 || {
        echo "ERROR: Failed to create Codex session." >&2
        cat "$log_file" >&2
        exit 1
    }

    # Extract session_id
    SESSION_ID="$(resolve_new_session_id "$marker" "$log_file")"

    write_state "{
  \"session_id\": \"$SESSION_ID\",
  \"phase\": \"initialized\",
  \"iteration\": 0,
  \"max_iterations\": $MAX_ITERATIONS,
  \"last_review_status\": \"\",
  \"last_review_timestamp\": \"$(date -u +"%Y-%m-%dT%H:%M:%SZ")\",
  \"task_description\": \"$TASK_LABEL_JSON\"
}"

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

    # Reset iteration counter on phase change (e.g. plan → code)
    local previous_phase
    previous_phase="$(read_state_field "phase")"
    if [[ -n "$previous_phase" && "$previous_phase" != "$phase" ]]; then
        local task_desc
        task_desc="$(read_state_field "task_description")"
        write_state "{
  \"session_id\": \"$SESSION_ID\",
  \"phase\": \"$previous_phase\",
  \"iteration\": 0,
  \"max_iterations\": $MAX_ITERATIONS,
  \"last_review_status\": \"\",
  \"last_review_timestamp\": \"$(date -u +"%Y-%m-%dT%H:%M:%SZ")\",
  \"task_description\": \"$task_desc\"
}"
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
    codex_prompt="$(build_review_prompt "$phase" "$DESCRIPTION")"

    # Clean previous verdict before calling codex
    rm -f "$STATE_DIR/verdict.txt"

    # Call codex with resume
    local output_file="$STATE_DIR/last_response.txt"
    local log_file
    log_file="$(next_attempt_log "$STATE_DIR/codex-${phase}-${next_iteration}")"

    # What was sent, stored next to the log of the run that sent it. A run that
    # dies before its verdict otherwise leaves no record of what it reviewed.
    printf '%s' "$DESCRIPTION" > "${log_file%.log}.request.md"

    echo "Sending $phase for review (iteration ${next_iteration}/${MAX_ITERATIONS})..." >&2
    printf '\033[1;33m>>> Monitor: tail -f %s\033[0m\n' "$log_file" >&2

    local MODEL_FLAG=()
    if [[ -n "$CODEX_MODEL" ]]; then
        MODEL_FLAG=("--model" "$CODEX_MODEL")
    fi

    local REASONING_FLAG=()
    if [[ -n "$CODEX_REASONING_EFFORT" ]]; then
        REASONING_FLAG=("-c" "model_reasoning_effort=\"$CODEX_REASONING_EFFORT\"")
    fi

    CODEX_REVIEWER=1 codex exec \
        "${MODEL_FLAG[@]}" \
        "${REASONING_FLAG[@]}" \
        "${YOLO_FLAG[@]}" \
        -o "$output_file" \
        resume "$SESSION_ID" \
        "$codex_prompt" </dev/null > "$log_file" 2>&1 || {
        local exit_code=$?
        echo "ERROR: Codex exec failed (exit $exit_code)." >&2
        cat "$log_file" >&2
        update_state "$phase" "$next_iteration" "ERROR"
        exit 1
    }

    local output
    output=$(cat "$output_file" 2>/dev/null || echo "")

    # Read verdict (file → fallback to text parsing)
    local status
    status="$(read_verdict "$output")"

    # Save note
    save_note "$phase" "$next_iteration" "$output"

    # Update state
    update_state "$phase" "$next_iteration" "$status"

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
        echo "  plan --plan-file <path>        Submit plan for review (reads file, passes inline)" >&2
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
