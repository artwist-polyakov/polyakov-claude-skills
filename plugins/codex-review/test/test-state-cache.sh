#!/bin/sh
# Regression tests for the cached state path and batched STATUS.md reads.

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
COMMON="$SCRIPT_DIR/../skills/codex-review/scripts/common.sh"

PASS=0
FAIL=0

pass() {
    PASS=$((PASS + 1))
    printf '  PASS: %s\n' "$1"
}

fail() {
    FAIL=$((FAIL + 1))
    printf '  FAIL: %s\n' "$1"
    [ -n "${2:-}" ] && printf '    %s\n' "$2"
}

assert_eq() {
    if [ "$2" = "$3" ]; then
        pass "$1"
    else
        fail "$1" "expected: $2; actual: $3"
    fi
}

assert_file_contains() {
    if grep -qF -- "$3" "$2"; then
        pass "$1"
    else
        fail "$1" "missing '$3' in $2"
    fi
}

TEST_ROOT="$(mktemp -d)"
trap 'rm -rf "$TEST_ROOT"' EXIT
STATE_DIR="$TEST_ROOT/cached-branch"
mkdir -p "$STATE_DIR/notes"

cat > "$STATE_DIR/state.json" <<'JSON'
{
  "session_id": "session-test",
  "phase": "code",
  "iteration": 3,
  "max_iterations": 5,
  "last_review_status": "CHANGES_REQUESTED",
  "reviews_completed": 2,
  "task_description": "Cached task"
}
JSON

printf 'Test 1: STATUS.md reuses the resolved state context\n'
values="$(bash -c '
    set -euo pipefail
    source "$1"
    STATE_DIR="$2"

    get_state_dir() {
        echo "get_state_dir must not run after STATE_DIR is set" >&2
        return 97
    }
    get_branch_slug() {
        echo "get_branch_slug must not run while writing STATUS.md" >&2
        return 98
    }

    write_status
    direct_phase="$(read_state_field phase)"
    direct_iteration="$(read_state_number iteration)"

    snapshot="$(<"$STATE_DIR/state.json")"
    mv "$STATE_DIR/state.json" "$STATE_DIR/state.saved.json"

    snapshot_phase="$(read_state_field phase "$snapshot")"
    snapshot_iteration="$(read_state_number iteration "$snapshot")"
    missing_field="$(read_state_field missing "")"
    missing_number="$(read_state_number missing "")"

    printf "%s|%s|%s|%s|%s|%s" \
        "$direct_phase" "$direct_iteration" \
        "$snapshot_phase" "$snapshot_iteration" \
        "$missing_field" "$missing_number"
' _ "$COMMON" "$STATE_DIR")" || {
    fail "cached state operations complete without resolving paths again" "$values"
    values=""
}

assert_eq "direct readers reuse STATE_DIR and snapshot readers need no file" \
    "code|3|code|3||0" "$values"

STATUS_FILE="$STATE_DIR/STATUS.md"
assert_file_contains "STATUS.md keeps the task" "$STATUS_FILE" "- Task: Cached task"
assert_file_contains "STATUS.md derives the cached branch" "$STATUS_FILE" "- Branch: cached-branch"
assert_file_contains "STATUS.md keeps the phase" "$STATUS_FILE" "- Phase: code"
assert_file_contains "STATUS.md keeps both iteration values" "$STATUS_FILE" "- Iteration: 3/5"
assert_file_contains "STATUS.md keeps the verdict" "$STATUS_FILE" "- Last status: CHANGES_REQUESTED"

printf '\nTest 2: path helpers reuse one resolved review root\n'
REVIEW_ROOT="$TEST_ROOT/provided-review-root"
mkdir -p "$REVIEW_ROOT"
printf 'CODEX_MAX_ITERATIONS=9\n' > "$REVIEW_ROOT/config.env"

root_values="$(bash -c '
    set -euo pipefail
    source "$1"

    get_review_root() {
        echo "get_review_root must not run when a root is provided" >&2
        return 97
    }
    get_branch_slug() {
        printf "%s\n" "cached-branch"
    }

    state_dir="$(get_state_dir "$2")"
    load_config "$2"
    printf "%s|%s" "$state_dir" "$CODEX_MAX_ITERATIONS"
' _ "$COMMON" "$REVIEW_ROOT")" || {
    fail "path helpers accept the already resolved review root" "$root_values"
    root_values=""
}

assert_eq "state and config paths share one explicit root" \
    "$REVIEW_ROOT/cached-branch|9" "$root_values"

printf '\nTest 3: archive summary uses one shared state snapshot\n'
ARCHIVE_STATE_DIR="$TEST_ROOT/archive-branch"
ARCHIVE_DIR="$TEST_ROOT/archive-output"
FIELD_LOG="$TEST_ROOT/archive-fields.log"
mkdir -p "$ARCHIVE_STATE_DIR/notes" "$ARCHIVE_DIR"
cp "$STATE_DIR/state.saved.json" "$ARCHIVE_STATE_DIR/state.json"
touch "$ARCHIVE_STATE_DIR/notes/plan-review-1.md"
touch "$ARCHIVE_STATE_DIR/notes/code-review-1.md"

archive_fields="$(bash -c '
    set -euo pipefail
    source "$1"
    expected_json="$(<"$2/state.json")"
    archive_state_dir="$2"
    field_log="$4"

    read_state_field() {
        if [[ $# -ne 2 || "$2" != "$expected_json" ]]; then
            echo "archive reader must receive the same in-memory snapshot" >&2
            return 98
        fi
        printf "%s\n" "$1" >> "$field_log"
        rm -f "$archive_state_dir/state.json"
        case "$1" in
            task_description) echo "Archive task" ;;
            session_id) echo "archive-session" ;;
            last_review_status) echo "APPROVED" ;;
            *) return 99 ;;
        esac
    }

    generate_archive_summary "$2" "$3" "20260831T190000Z"
    cat "$field_log"
' _ "$COMMON" "$ARCHIVE_STATE_DIR" "$ARCHIVE_DIR" "$FIELD_LOG")" || {
    fail "archive summary completes from one shared snapshot" "$archive_fields"
    archive_fields=""
}

expected_archive_fields="$(printf 'task_description\nsession_id\nlast_review_status\n')"
assert_eq "archive summary reads all strings through the shared parser" \
    "$expected_archive_fields" "$archive_fields"
assert_file_contains "archive summary keeps the task" \
    "$ARCHIVE_DIR/summary.json" '"task_description": "Archive task'
assert_file_contains "archive summary keeps the session" \
    "$ARCHIVE_DIR/summary.json" '"session_id": "archive-session"'
assert_file_contains "archive summary keeps the final status" \
    "$ARCHIVE_DIR/summary.json" '"final_verdict": "APPROVED"'

printf '\nPASS: %d  FAIL: %d\n' "$PASS" "$FAIL"
[ "$FAIL" -eq 0 ]
