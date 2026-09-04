#!/bin/sh
# Regression tests for what a failed `codex exec` costs.
#
# Does NOT require the `codex` binary — a stub on PATH plays the reviewer and is
# told per call whether to fail, and whether to leave a verdict behind first.

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROD_SCRIPTS="$SCRIPT_DIR/../skills/codex-review/scripts"
REVIEW_CMD="$PROD_SCRIPTS/codex-review.sh"
STATE_CMD="$PROD_SCRIPTS/codex-state.sh"

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
mkdir -p "$REPO/bin"
(
    cd "$REPO"
    git init -q -b main
    git config user.email "test@test.com"
    git config user.name "Test"
    git commit -q --allow-empty -m "init"
)

# The stub reads two marker files beside the repo:
#   fail-next    — this review call exits 1
#   verdict-next — before failing, write this word to verdict.txt
# Only a review call is affected: `common.sh` probes `codex --version` before
# every run, and failing that probe would abort before any review was attempted.
cat > "$REPO/bin/codex" <<'STUB'
#!/bin/sh
out=""
from_stdin=""
while [ $# -gt 0 ]; do
    case "$1" in
        -o) out="$2"; shift 2 ;;
        -) from_stdin=1; shift ;;
        *) shift ;;
    esac
done
[ -n "$from_stdin" ] && cat >/dev/null
REPO_DIR="$(dirname "$(dirname "$0")")"
if [ -n "$from_stdin" ] && [ -f "$REPO_DIR/fail-next" ]; then
    rm -f "$REPO_DIR/fail-next"
    if [ -f "$REPO_DIR/verdict-next" ]; then
        cat "$REPO_DIR/verdict-next" > "$REPO_DIR/.codex-review/main/verdict.txt"
        rm -f "$REPO_DIR/verdict-next"
    fi
    printf 'stream error: connection reset\n' >&2
    exit 1
fi
[ -n "$out" ] && printf 'APPROVED\n' > "$out"
printf 'session sess_teststub01 ready\n'
exit 0
STUB
chmod +x "$REPO/bin/codex"

(cd "$REPO" && bash "$STATE_CMD" set session_id test-session >/dev/null)
STATE_DIR="$REPO/.codex-review/main"
printf 'What changed: nothing much.\n' > "$REPO/desc.md"

# Runs one review and prints "<exit code>|<output on one line>".
run_review() {
    _out="$(
        unset CODEX_REVIEWER CODEX_MODEL CODEX_REASONING_EFFORT \
              CODEX_MAX_ITERATIONS CODEX_YOLO CODEX_SESSION_ID AUTO_REVIEW \
              CODEX_REVIEWER_PROMPT CODEX_PLAN_GUIDE CODEX_CODE_GUIDE \
              CODEX_SEVERITY_CALIBRATION
        cd "$REPO" && PATH="$REPO/bin:$PATH" CODEX_HOME="$REPO/codex-home" \
            bash "$REVIEW_CMD" code --description-file "$REPO/desc.md" 2>&1
    )" && _rc=0 || _rc=$?
    printf '%s|%s\n' "$_rc" "$(printf '%s' "$_out" | tr '\n' ' ')"
}

rc_of() { printf '%s' "${1%%|*}"; }
msg_of() { printf '%s' "${1#*|}"; }

number() {
    sed -n "s/.*\"$1\"[[:space:]]*:[[:space:]]*\\([0-9][0-9]*\\).*/\\1/p" \
        "$STATE_DIR/state.json" | head -1
}
field() {
    sed -n "s/.*\"$1\"[[:space:]]*:[[:space:]]*\"\\(.*\\)\".*/\\1/p" \
        "$STATE_DIR/state.json" | head -1
}

# ============================
# Test 1: a call that comes back with nothing costs nothing
# ============================
printf 'Test 1: a failed call spends no iteration\n'

: > "$REPO/fail-next"
r="$(run_review)"
assert_eq "a failed call exits 1" "1" "$(rc_of "$r")"
assert_contains "the failure is reported" "Codex exec failed" "$(msg_of "$r")"
assert_contains "the run says the iteration is intact" "Iteration not consumed: still 0/" \
    "$(msg_of "$r")"
assert_eq "the iteration counter stands still" "0" "$(number iteration)"
assert_eq "the round counter stands still" "0" "$(number reviews_completed)"
assert_eq "the stored status is the error" "ERROR" "$(field last_review_status)"

if [ -f "$STATE_DIR/notes/code-review-1.md" ]; then
    fail "a failed call writes no review note" "a note was written"
else
    pass "a failed call writes no review note"
fi

# ============================
# Test 2: STATUS.md follows the failure
# ============================
printf 'Test 2: STATUS.md shows the failed round, not the previous one\n'

assert_contains "STATUS.md carries the error" "Last status: ERROR" "$(cat "$STATE_DIR/STATUS.md")"
assert_contains "STATUS.md carries the unspent counter" "Iteration: 0/" \
    "$(cat "$STATE_DIR/STATUS.md")"

# ============================
# Test 3: repeated failures never reach the limit
# ============================
printf 'Test 3: repeated failures do not walk the counter to the limit\n'

i=0
while [ "$i" -lt 6 ]; do
    : > "$REPO/fail-next"
    r="$(run_review)"
    i=$((i + 1))
done
assert_eq "six failures leave the counter at zero" "0" "$(number iteration)"
assert_contains "the sixth failure is still an error, not an escalation" \
    "Codex exec failed" "$(msg_of "$r")"
assert_eq "the sixth failure still exits 1" "1" "$(rc_of "$r")"

# ============================
# Test 4: a review that comes back spends its iteration
# ============================
printf 'Test 4: a review that comes back spends its iteration\n'

r="$(run_review)"
assert_eq "a completed review exits 0" "0" "$(rc_of "$r")"
assert_eq "the iteration counter advances" "1" "$(number iteration)"
assert_eq "the round counter advances" "1" "$(number reviews_completed)"

# ============================
# Test 5: a verdict written before the call died still counts
# ============================
printf 'Test 5: a call that wrote its verdict before dying spends its iteration\n'

: > "$REPO/fail-next"
printf 'CHANGES_REQUESTED\n' > "$REPO/verdict-next"
# The previous round's reply is still on disk when this call starts. It must not
# be filed as this round's note.
printf 'A REPLY FROM THE PREVIOUS ROUND\n' > "$STATE_DIR/last_response.txt"
r="$(run_review)"
assert_eq "the rescued round exits 0" "0" "$(rc_of "$r")"
assert_contains "the run says the call died after its verdict" \
    "after writing its verdict" "$(msg_of "$r")"
assert_eq "the rescued round advances the iteration" "2" "$(number iteration)"
assert_eq "the rescued round advances the round counter" "2" "$(number reviews_completed)"
assert_eq "the rescued verdict is the stored status" "CHANGES_REQUESTED" \
    "$(field last_review_status)"

NOTE="$STATE_DIR/notes/code-review-2.md"
if [ -f "$NOTE" ]; then
    pass "the rescued round leaves a note"
    case "$(cat "$NOTE")" in
        *"A REPLY FROM THE PREVIOUS ROUND"*)
            fail "the note is not the previous round's reply" "the old reply was filed as this note" ;;
        *) pass "the note is not the previous round's reply" ;;
    esac
    assert_contains "the note says why it has no reply text" \
        "No reply text was saved" "$(cat "$NOTE")"
    assert_contains "the note names the log file that holds the run" \
        "codex-code-2.log" "$(cat "$NOTE")"
else
    fail "the rescued round leaves a note" "no note at $NOTE"
fi

# ============================
# Test 6: a dead call is not read as a verdict
# ============================
printf 'Test 6: half a reply is not a verdict\n'

# The stub writes APPROVED into the reply file on a successful call only, so a
# reply file left over from the previous round is on disk when this call dies.
# The text fallback would read it as approval; a failed call must not consult it.
printf 'APPROVED\n' > "$STATE_DIR/last_response.txt"
: > "$REPO/fail-next"
r="$(run_review)"
assert_eq "a failed call with a stale reply on disk exits 1" "1" "$(rc_of "$r")"
assert_eq "it does not spend an iteration" "2" "$(number iteration)"
assert_eq "it does not store an approval" "ERROR" "$(field last_review_status)"

# ============================
# Test 7: a rescued approval ends the cycle the same way
# ============================
printf 'Test 7: a rescued APPROVED closes the review\n'

: > "$REPO/fail-next"
printf 'APPROVED\n' > "$REPO/verdict-next"
r="$(run_review)"
assert_eq "the rescued approval exits 0" "0" "$(rc_of "$r")"
assert_eq "the rescued approval advances the iteration" "3" "$(number iteration)"
assert_eq "the rescued approval is the stored status" "APPROVED" "$(field last_review_status)"
if [ -f "$STATE_DIR/STATUS.md" ]; then
    fail "an approved code round removes STATUS.md" "STATUS.md is still there"
else
    pass "an approved code round removes STATUS.md"
fi

printf '\nPASS: %d  FAIL: %d\n' "$PASS" "$FAIL"
[ "$FAIL" -eq 0 ]
