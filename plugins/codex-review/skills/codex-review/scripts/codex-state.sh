#!/bin/bash
# State management for codex-review plugin
# Usage: codex-state.sh {show|reset|get|set} [args]

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck disable=SC1091
source "$SCRIPT_DIR/common.sh"

REVIEW_ROOT="$(get_review_root)"
STATE_DIR="$(get_state_dir "$REVIEW_ROOT")"
STATE_FILE="$STATE_DIR/state.json"

cmd_show() {
    local effective_sid
    effective_sid="$(get_effective_session_id)"

    if [[ -f "$STATE_FILE" ]]; then
        # Replace session_id in output with effective value (config.env takes priority)
        sed "s|\"session_id\"[[:space:]]*:[[:space:]]*\"[^\"]*\"|\"session_id\": \"$effective_sid\"|" "$STATE_FILE"
    else
        render_state_fields \
            session_id="$effective_sid" \
            phase="" \
            iteration=0 \
            max_iterations="$CODEX_MAX_ITERATIONS" \
            last_review_status="" \
            last_review_timestamp="" \
            reviews_completed=0 \
            task_description=""
    fi
}

cmd_reset() {
    acquire_state_lock "codex-state.sh reset" || exit 1
    if [[ "${1:-}" == "--full" ]]; then
        archive_previous_session
        mkdir -p "$STATE_DIR/notes"
        touch "$STATE_DIR/notes/.gitkeep"
        echo "Full reset complete."
    else
        local session_id task_desc
        session_id="$(get_effective_session_id)"
        task_desc="$(read_state_field "task_description")"
        write_state_fields \
            session_id="$session_id" \
            phase="" \
            iteration=0 \
            max_iterations="$CODEX_MAX_ITERATIONS" \
            last_review_status="" \
            last_review_timestamp="" \
            reviews_completed=0 \
            task_description="$task_desc"
        write_status
        echo "Reset complete (session_id preserved)."
    fi
}

cmd_get() {
    local field="${1:?Usage: codex-state.sh get <field>}"
    if [[ "$field" == "session_id" ]]; then
        get_effective_session_id
        return
    fi
    if [[ "$field" == "verdict" ]]; then
        parse_verdict_file "$STATE_DIR/verdict.txt"
        return
    fi
    # The reader is chosen by the field, not by what the first one returned:
    # asking a string reader for an empty value and then retrying with the
    # numeric one answered 0 for a field that holds an empty string, and for a
    # field that does not exist at all.
    case " $STATE_STRING_FIELDS " in
        *" $field "*) read_state_field "$field"; return ;;
    esac
    case " $STATE_NUMBER_FIELDS " in
        *" $field "*) read_state_number "$field"; return ;;
    esac

    echo "ERROR: Unsupported state field: $field" >&2
    echo "Known fields: $STATE_STRING_FIELDS $STATE_NUMBER_FIELDS" >&2
    echo "Also readable: session_id (config.env wins), verdict (from verdict.txt)" >&2
    exit 1
}

cmd_set() {
    local field="${1:?Usage: codex-state.sh set <field> <value>}"
    local value="${2:?Usage: codex-state.sh set <field> <value>}"

    case " $STATE_STRING_FIELDS $STATE_NUMBER_FIELDS " in
        *" $field "*) ;;
        *)
            echo "ERROR: Unsupported state field: $field" >&2
            echo "Known fields: $STATE_STRING_FIELDS $STATE_NUMBER_FIELDS" >&2
            exit 1
            ;;
    esac

    # Taken after the name is checked: a field that does not exist is answered
    # the same way whether or not another run holds the branch.
    acquire_state_lock "codex-state.sh set $field" || exit 1

    # The whole file is rewritten from its current values with the one field
    # replaced. Patching the stored text in place could only reach the fields
    # written as JSON strings, so the three counters stayed as they were while
    # the command still reported the new value.
    local state_json=""
    if [[ -f "$STATE_FILE" ]]; then
        state_json="$(<"$STATE_FILE")"
    fi

    local session_id phase last_review_status last_review_timestamp task_description
    local iteration max_iterations reviews_completed
    session_id="$(read_state_field session_id "$state_json")"
    phase="$(read_state_field phase "$state_json")"
    last_review_status="$(read_state_field last_review_status "$state_json")"
    last_review_timestamp="$(read_state_field last_review_timestamp "$state_json")"
    task_description="$(read_state_field task_description "$state_json")"
    iteration="$(read_state_number iteration "$state_json")"
    reviews_completed="$(read_state_number reviews_completed "$state_json")"
    if state_has_field max_iterations "$state_json"; then
        max_iterations="$(read_state_number max_iterations "$state_json")"
    else
        max_iterations="$CODEX_MAX_ITERATIONS"
    fi

    printf -v "$field" '%s' "$value"

    write_state_fields \
        session_id="$session_id" \
        phase="$phase" \
        iteration="$iteration" \
        max_iterations="$max_iterations" \
        last_review_status="$last_review_status" \
        last_review_timestamp="$last_review_timestamp" \
        reviews_completed="$reviews_completed" \
        task_description="$task_description"
    write_status
    echo "Set $field = $value"
}

# --- Load config for defaults ---
load_config "$REVIEW_ROOT"
unset REVIEW_ROOT

# --- Main ---
case "${1:-}" in
    show)   cmd_show ;;
    dir)    echo "$STATE_DIR" ;;
    reset)  cmd_reset "${2:-}" ;;
    get)    cmd_get "${2:-}" ;;
    set)    cmd_set "${2:-}" "${3:-}" ;;
    *)
        echo "Usage: codex-state.sh {show|reset|dir|get|set} [args]"
        echo "  show              Current state (JSON)"
        echo "  dir               Print state directory path for current branch"
        echo "  reset             Reset iterations/phase (keep session_id)"
        echo "  reset --full      Full reset + delete notes"
        echo "  get <field>       Get a single field (special: 'verdict' reads verdict.txt)"
        echo "  set <field> <val> Set a field: $STATE_STRING_FIELDS $STATE_NUMBER_FIELDS"
        exit 1
        ;;
esac
