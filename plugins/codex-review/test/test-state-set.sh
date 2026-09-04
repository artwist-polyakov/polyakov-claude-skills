#!/bin/sh
# Regression tests for `codex-state.sh set` and the single renderer every
# writer of state.json goes through.
#
# Does NOT require the `codex` binary — the state script never calls it.

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROD_SCRIPTS="$SCRIPT_DIR/../skills/codex-review/scripts"
STATE_CMD="$PROD_SCRIPTS/codex-state.sh"
COMMON="$PROD_SCRIPTS/common.sh"

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
    return 0
}

assert_eq() {
    if [ "$2" = "$3" ]; then
        pass "$1"
    else
        fail "$1" "expected: [$2]; actual: [$3]"
    fi
}

assert_contains() {
    case "$3" in
        *"$2"*) pass "$1" ;;
        *) fail "$1" "missing '$2' in: $3" ;;
    esac
}

TEST_ROOT="$(mktemp -d)"
trap 'rm -rf "$TEST_ROOT"' EXIT

REPO="$TEST_ROOT/repo"
mkdir -p "$REPO"
(
    cd "$REPO"
    git init -q -b main
    git config user.email "test@test.com"
    git config user.name "Test"
    git commit -q --allow-empty -m "init"
    mkdir -p .codex-review
    printf 'CODEX_MAX_ITERATIONS=7\n' > .codex-review/config.env
)
STATE_DIR="$REPO/.codex-review/main"
STATE_FILE="$STATE_DIR/state.json"

# Runs `set` and prints "<exit code>|<stderr and stdout on one line>".
run_set() {
    _out="$(cd "$REPO" && bash "$STATE_CMD" set "$1" "$2" 2>&1)" && _rc=0 || _rc=$?
    printf '%s|%s\n' "$_rc" "$(printf '%s' "$_out" | tr '\n' ' ')"
}

rc_of() { printf '%s' "${1%%|*}"; }
msg_of() { printf '%s' "${1#*|}"; }

# Reads a value out of state.json without a JSON parser: strings come back
# without their quotes, numbers as written.
field() {
    sed -n "s/.*\"$1\"[[:space:]]*:[[:space:]]*\"\\(.*\\)\".*/\\1/p" "$STATE_FILE" | head -1
}
number() {
    sed -n "s/.*\"$1\"[[:space:]]*:[[:space:]]*\\([0-9][0-9]*\\).*/\\1/p" "$STATE_FILE" | head -1
}

JSON_CHECK=""
if command -v python3 >/dev/null 2>&1; then
    JSON_CHECK="python3"
elif command -v jq >/dev/null 2>&1; then
    JSON_CHECK="jq"
fi

assert_valid_json() {
    case "$JSON_CHECK" in
        python3)
            if python3 -c "import json,sys; json.load(open(sys.argv[1]))" "$STATE_FILE" 2>/dev/null; then
                pass "$1"
            else
                fail "$1" "not valid JSON: $(cat "$STATE_FILE")"
            fi
            ;;
        jq)
            if jq -e . "$STATE_FILE" >/dev/null 2>&1; then
                pass "$1"
            else
                fail "$1" "not valid JSON: $(cat "$STATE_FILE")"
            fi
            ;;
        *)
            printf '  SKIP: %s (no python3 or jq)\n' "$1"
            ;;
    esac
}

# ============================
# Test 1: counters are written
# ============================
printf 'Test 1: set writes the counters, not just the strings\n'

(cd "$REPO" && bash "$STATE_CMD" set session_id sess-1 >/dev/null)
r="$(run_set iteration 2)"
assert_eq "set iteration exits 0" "0" "$(rc_of "$r")"
assert_eq "iteration is stored" "2" "$(number iteration)"

r="$(run_set max_iterations 9)"
assert_eq "max_iterations is stored" "9" "$(number max_iterations)"

r="$(run_set reviews_completed 3)"
assert_eq "reviews_completed is stored" "3" "$(number reviews_completed)"

r="$(run_set iteration 0)"
assert_eq "a counter can be set back to zero" "0" "$(number iteration)"

assert_contains "STATUS.md follows the stored counters" "Iteration: 0/9" \
    "$(cat "$STATE_DIR/STATUS.md")"

# ============================
# Test 2: a refused write changes nothing
# ============================
printf 'Test 2: an unusable field or value is refused, and nothing is written\n'

(cd "$REPO" && bash "$STATE_CMD" set iteration 4 >/dev/null)
before="$(cat "$STATE_FILE")"

r="$(run_set bogus_field 5)"
assert_eq "an unknown field exits 1" "1" "$(rc_of "$r")"
assert_contains "an unknown field says so" "Unsupported state field: bogus_field" "$(msg_of "$r")"
assert_contains "an unknown field lists the known ones" "reviews_completed" "$(msg_of "$r")"
assert_eq "an unknown field leaves state.json untouched" "$before" "$(cat "$STATE_FILE")"

for bad in abc -1 2.5 "" 08 9999999999; do
    r="$(run_set iteration "$bad")"
    assert_eq "iteration refuses [$bad]" "1" "$(rc_of "$r")"
done
assert_contains "a value that is not a number says what it expects" \
    "iteration expects a whole number without a leading zero" "$(msg_of "$(run_set iteration abc)")"
assert_contains "a counter too long to add to says so" \
    "iteration is 10 digits, the limit is 9" "$(msg_of "$(run_set iteration 9999999999)")"
assert_eq "a refused counter leaves state.json untouched" "$before" "$(cat "$STATE_FILE")"

# ============================
# Test 3: the other fields survive a write
# ============================
printf 'Test 3: writing one field keeps every other field\n'

(
    cd "$REPO"
    bash "$STATE_CMD" set session_id sess-keep >/dev/null
    bash "$STATE_CMD" set phase code >/dev/null
    bash "$STATE_CMD" set iteration 2 >/dev/null
    bash "$STATE_CMD" set max_iterations 6 >/dev/null
    bash "$STATE_CMD" set reviews_completed 1 >/dev/null
    bash "$STATE_CMD" set task_description "Keep every field" >/dev/null
    bash "$STATE_CMD" set last_review_timestamp 2026-09-04T00:00:00Z >/dev/null
    bash "$STATE_CMD" set last_review_status CHANGES_REQUESTED >/dev/null
)
assert_eq "session_id survives" "sess-keep" "$(field session_id)"
assert_eq "phase survives" "code" "$(field phase)"
assert_eq "iteration survives" "2" "$(number iteration)"
assert_eq "max_iterations survives" "6" "$(number max_iterations)"
assert_eq "reviews_completed survives" "1" "$(number reviews_completed)"
assert_eq "task_description survives" "Keep every field" "$(field task_description)"
assert_eq "last_review_timestamp survives" "2026-09-04T00:00:00Z" "$(field last_review_timestamp)"
assert_eq "last_review_status is the one just written" "CHANGES_REQUESTED" "$(field last_review_status)"

# ============================
# Test 4: values state.json cannot give back are refused
# ============================
printf 'Test 4: a value that would not survive storage is refused, not repaired\n'

before="$(cat "$STATE_FILE")"

r="$(run_set task_description 'fix "the" bug')"
assert_eq "a double quote exits 1" "1" "$(rc_of "$r")"
assert_contains "a double quote names the field" "task_description must not contain a double quote" \
    "$(msg_of "$r")"

r="$(run_set task_description 'C:\tmp\out')"
assert_eq "a backslash exits 1" "1" "$(rc_of "$r")"

r="$(run_set session_id "$(printf 'two\nlines')")"
assert_eq "a second line exits 1" "1" "$(rc_of "$r")"

r="$(run_set phase "$(printf 'a\tb')")"
assert_eq "a control character exits 1" "1" "$(rc_of "$r")"

assert_eq "a refused value leaves state.json untouched" "$before" "$(cat "$STATE_FILE")"
assert_valid_json "state.json stays valid JSON"

r="$(run_set task_description 'Fix the parser: keep spaces')"
assert_eq "an ordinary value is stored as written" "Fix the parser: keep spaces" "$(field task_description)"

# ============================
# Test 5: a first write builds the whole file
# ============================
printf 'Test 5: set on a missing state.json writes every field\n'

rm -f "$STATE_FILE"
r="$(run_set phase implementing)"
assert_eq "the first set exits 0" "0" "$(rc_of "$r")"
assert_eq "phase is the value just set" "implementing" "$(field phase)"
assert_eq "max_iterations comes from config.env" "7" "$(number max_iterations)"
assert_eq "iteration starts at zero" "0" "$(number iteration)"
assert_eq "reviews_completed starts at zero" "0" "$(number reviews_completed)"
assert_valid_json "a file written from scratch is valid JSON"

# ============================
# Test 6: one renderer describes the file
# ============================
printf 'Test 6: state.json has a single writer\n'

render() {
    bash -c '
        set -euo pipefail
        source "$1"
        shift
        render_state_fields "$@"
    ' _ "$COMMON" "$@" 2>&1
}
render_rc() {
    render "$@" >/dev/null 2>&1 && printf '0' || printf '1'
}

full_args='session_id=s phase=code iteration=1 max_iterations=5 last_review_status=APPROVED last_review_timestamp=t reviews_completed=1 task_description=Task'

# shellcheck disable=SC2086
assert_eq "every field given renders" "0" "$(render_rc $full_args)"
# shellcheck disable=SC2086
assert_contains "the rendered file carries the counter as a number" '"iteration": 1,' "$(render $full_args)"

assert_eq "a missing field is an error" "1" \
    "$(render_rc session_id=s phase=code iteration=1 max_iterations=5)"
assert_contains "a missing field is named" "State field not given" \
    "$(render session_id=s phase=code iteration=1 max_iterations=5)"

# shellcheck disable=SC2086
assert_eq "a field given twice is an error" "1" "$(render_rc $full_args phase=plan)"
# shellcheck disable=SC2086
assert_eq "an argument without a name is an error" "1" "$(render_rc $full_args stray)"

for script in codex-review.sh codex-state.sh; do
    if grep -q '"max_iterations"[[:space:]]*:' "$PROD_SCRIPTS/$script"; then
        fail "$script writes state.json through the shared renderer" \
            "it still carries its own copy of the file's literal"
    else
        pass "$script writes state.json through the shared renderer"
    fi
done

# ============================
# Test 7: the iteration limit is checked before anything happens
# ============================
printf 'Test 7: a limit state.json cannot hold stops the run before it starts\n'

REVIEW_CMD="$PROD_SCRIPTS/codex-review.sh"
LIMIT_REPO="$TEST_ROOT/limit-repo"
STUB_BIN="$TEST_ROOT/stub-bin"
CODEX_CALLED="$TEST_ROOT/codex-was-called"
mkdir -p "$LIMIT_REPO" "$STUB_BIN"
(
    cd "$LIMIT_REPO"
    git init -q -b main
    git config user.email "test@test.com"
    git config user.name "Test"
    git commit -q --allow-empty -m "init"
)
cat > "$STUB_BIN/codex" <<STUB
#!/bin/sh
if [ "\$1" = "--version" ]; then
    echo "codex-stub"
    exit 0
fi
touch "$CODEX_CALLED"
exit 0
STUB
chmod +x "$STUB_BIN/codex"

(cd "$LIMIT_REPO" && bash "$STATE_CMD" set session_id sess-limit >/dev/null)
LIMIT_STATE="$LIMIT_REPO/.codex-review/main/state.json"

# Every setting `load_config` reads is cleared, and so is the reviewer guard:
# run from inside a review, an inherited CODEX_REVIEWER=1 would abort the run
# before it ever reached the limit and answer these assertions for the wrong
# reason.
run_init() {
    (
        unset CODEX_REVIEWER CODEX_MODEL CODEX_REASONING_EFFORT \
              CODEX_MAX_ITERATIONS CODEX_YOLO CODEX_SESSION_ID AUTO_REVIEW \
              CODEX_REVIEWER_PROMPT CODEX_PLAN_GUIDE CODEX_CODE_GUIDE \
              CODEX_SEVERITY_CALIBRATION
        cd "$LIMIT_REPO" && PATH="$STUB_BIN:$PATH" \
            bash "$REVIEW_CMD" init "task" --max-iter "$1" 2>&1
    )
}

# Asserts the whole refusal: the exit code, the message, and that the run cost
# nothing — no codex call, no archive, the stored state untouched.
assert_limit_refused() {
    _label="$1"
    _value="$2"
    rm -f "$CODEX_CALLED"
    # Read defensively: a run that spends the state instead of refusing leaves
    # no file behind, and this assertion has to report that rather than die.
    _before="$(cat "$LIMIT_STATE" 2>/dev/null || printf '<no state.json>')"
    _out="$(run_init "$_value")" && _rc=0 || _rc=$?

    assert_eq "$_label exits 1" "1" "$_rc"
    assert_contains "$_label names the option to fix" "--max-iter" "$_out"
    assert_contains "$_label names the value it refused" "the iteration limit" "$_out"
    if [ -f "$CODEX_CALLED" ]; then
        fail "$_label calls no codex" "the stub recorded a call"
    else
        pass "$_label calls no codex"
    fi
    if [ -d "$LIMIT_REPO/.codex-review/archive" ]; then
        fail "$_label archives nothing" "an archive directory was created"
    else
        pass "$_label archives nothing"
    fi
    assert_eq "$_label leaves the stored state untouched" "$_before" \
        "$(cat "$LIMIT_STATE" 2>/dev/null || printf '<no state.json>')"
}

assert_limit_refused "a limit that is not a number" "abc"
assert_limit_refused "a limit with a leading zero" "08"
assert_limit_refused "a limit too long to add to" "9999999999"

printf '\nPASS: %d  FAIL: %d\n' "$PASS" "$FAIL"
[ "$FAIL" -eq 0 ]
