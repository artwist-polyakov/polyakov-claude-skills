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

printf '\nPASS: %d  FAIL: %d\n' "$PASS" "$FAIL"
[ "$FAIL" -eq 0 ]
