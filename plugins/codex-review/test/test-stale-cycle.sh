#!/bin/sh
# Regression tests for a cycle that outlives the task it was opened for.
#
# The review state of a branch survives anything done to the code: the state
# directory is outside git. A task abandoned or finished mid-branch therefore
# leaves a cycle behind, and the next task walks into it — inheriting its round
# number, its narrowed review scope and its verdict.
#
# Covers three ways that used to go wrong: an approval left by a code review
# standing in for a plan review, a round sent into a cycle already closed by an
# approval, and a round that never said which task it was continuing.
#
# Does NOT require the `codex` binary — a stub on PATH plays the reviewer.

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROD_SCRIPTS="$SCRIPT_DIR/../skills/codex-review/scripts"
REVIEW_CMD="$PROD_SCRIPTS/codex-review.sh"
STATE_CMD="$PROD_SCRIPTS/codex-state.sh"
HOOK_CMD="$PROD_SCRIPTS/auto-approve-plan.sh"

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

assert_lacks() {
    case "$3" in
        *"$2"*) fail "$1" "found '$2' in: $3" ;;
        *) pass "$1" ;;
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

# The stub writes whatever `verdict-next` holds, defaulting to a request for
# changes so a cycle stays open unless a scenario closes it on purpose.
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
verdict_file=""
if [ -n "$from_stdin" ]; then
    prompt="$(mktemp)"
    cat > "$prompt"
    verdict_file="$(sed -n 's|^After your review, write your verdict to \(.*\)$|\1|p' "$prompt" | head -1)"
    rm -f "$prompt"
fi
REPO_DIR="$(dirname "$(dirname "$0")")"
if [ -n "$verdict_file" ]; then
    if [ -f "$REPO_DIR/verdict-next" ]; then
        cat "$REPO_DIR/verdict-next" > "$verdict_file"
        rm -f "$REPO_DIR/verdict-next"
    else
        printf 'CHANGES_REQUESTED\n' > "$verdict_file"
    fi
fi
[ -n "$out" ] && printf 'A review.\n' > "$out"
printf 'session sess_teststub01 ready\n'
exit 0
STUB
chmod +x "$REPO/bin/codex"

STATE_DIR="$REPO/.codex-review/main"
SESSION="11111111-2222-3333-4444-555555555555"

# Runs one command and prints "<exit code>|<output on one line>".
review() {
    _out="$(
        unset CODEX_REVIEWER CODEX_MODEL CODEX_REASONING_EFFORT \
              CODEX_MAX_ITERATIONS CODEX_YOLO CODEX_SESSION_ID AUTO_REVIEW \
              CODEX_REVIEWER_PROMPT CODEX_PLAN_GUIDE CODEX_CODE_GUIDE \
              CODEX_SEVERITY_CALIBRATION
        cd "$REPO" && PATH="$REPO/bin:$PATH" CODEX_HOME="$REPO/codex-home" \
            bash "$REVIEW_CMD" "$@" 2>&1
    )" && _rc=0 || _rc=$?
    printf '%s|%s\n' "$_rc" "$(printf '%s' "$_out" | tr '\n' ' ')"
}

state_cmd() {
    (
        unset CODEX_REVIEWER CODEX_SESSION_ID CODEX_MAX_ITERATIONS
        cd "$REPO" && bash "$STATE_CMD" "$@" 2>&1
    )
}

hook() {
    (
        cd "$REPO" && printf '{"session_id":"%s"}' "$SESSION" | sh "$HOOK_CMD"
    )
}

rc_of() { printf '%s' "${1%%|*}"; }
msg_of() { printf '%s' "${1#*|}"; }

state_cmd set session_id test-session >/dev/null
printf 'What changed: nothing much.\n' > "$REPO/desc.md"

# ============================
# Test 1: the verdict says which review left it
# ============================
printf 'Test 1: a verdict carries the phase that produced it\n'

review init "TASK-A" >/dev/null
r="$(review code --description-file "$REPO/desc.md")"
assert_eq "the round runs" "0" "$(rc_of "$r")"
assert_eq "the marker names the code phase" "code" \
    "$(cat "$STATE_DIR/verdict.phase" 2>/dev/null || printf '<none>')"

review init "TASK-A again" >/dev/null
r="$(review plan --plan-file "$REPO/desc.md")"
assert_eq "a plan round runs" "0" "$(rc_of "$r")"
assert_eq "the marker names the plan phase" "plan" \
    "$(cat "$STATE_DIR/verdict.phase" 2>/dev/null || printf '<none>')"

# ============================
# Test 2: the plan gate takes an approval only from a plan review
# ============================
printf 'Test 2: an approval left by a code review does not pass a plan\n'

printf 'AUTO_REVIEW=true\n' > "$REPO/.codex-review/config.env"
printf '%s\n' "$SESSION" > "$STATE_DIR/current_session.txt"

printf 'APPROVED\n' > "$STATE_DIR/verdict.txt"
printf 'code\n' > "$STATE_DIR/verdict.phase"
out="$(hook)"
assert_contains "a code approval is refused" '"behavior":"deny"' "$out"
assert_contains "the refusal says which review left it" "left by a code review" "$out"
if [ -f "$STATE_DIR/verdict.txt" ] || [ -f "$STATE_DIR/verdict.phase" ]; then
    fail "the refused verdict is cleared" "it is still on disk"
else
    pass "the refused verdict is cleared"
fi

printf 'APPROVED\n' > "$STATE_DIR/verdict.txt"
printf 'plan\n' > "$STATE_DIR/verdict.phase"
out="$(hook)"
assert_contains "a plan approval passes" '"behavior":"allow"' "$out"
if [ -f "$STATE_DIR/verdict.phase" ]; then
    fail "the marker is cleared with the verdict it used" "verdict.phase is still there"
else
    pass "the marker is cleared with the verdict it used"
fi

printf 'APPROVED\n' > "$STATE_DIR/verdict.txt"
rm -f "$STATE_DIR/verdict.phase"
out="$(hook)"
assert_contains "an unmarked approval is refused" '"behavior":"deny"' "$out"
assert_contains "the refusal says the origin is unknown" "does not record which review" "$out"

rm -f "$REPO/.codex-review/config.env" "$STATE_DIR/verdict.txt" "$STATE_DIR/verdict.phase"

# ============================
# Test 3: a cycle closed by an approval is not continued in silence
# ============================
printf 'Test 3: a round sent into a closed cycle is refused\n'

review init "TASK-A" >/dev/null
printf 'APPROVED\n' > "$REPO/verdict-next"
r="$(review code --description-file "$REPO/desc.md")"
assert_eq "the approved round runs" "0" "$(rc_of "$r")"
assert_eq "the cycle is closed" "APPROVED" "$(state_cmd get last_review_status)"

r="$(review code --description-file "$REPO/desc.md")"
assert_eq "the next code round is refused" "1" "$(rc_of "$r")"
assert_contains "the refusal says the cycle is closed" "that cycle is closed" "$(msg_of "$r")"
assert_contains "the refusal names the way to a new task" "init" "$(msg_of "$r")"
assert_contains "the refusal names the way to more work on this one" \
    "codex-state.sh reset" "$(msg_of "$r")"
assert_contains "the refusal says it changed nothing" "Nothing was changed" "$(msg_of "$r")"
assert_eq "the counter did not move" "1" "$(state_cmd get iteration)"

r="$(review plan --plan-file "$REPO/desc.md")"
assert_eq "a plan round is refused the same way" "1" "$(rc_of "$r")"

# ============================
# Test 4: both ways out of a closed cycle work
# ============================
printf 'Test 4: a reset reopens the cycle, an init opens a new one\n'

state_cmd reset >/dev/null
r="$(review code --description-file "$REPO/desc.md")"
assert_eq "the round after a reset runs" "0" "$(rc_of "$r")"
assert_eq "it is the first round again" "1" "$(state_cmd get iteration)"
assert_eq "the task keeps its name" "TASK-A" "$(state_cmd get task_description)"

printf 'APPROVED\n' > "$REPO/verdict-next"
review code --description-file "$REPO/desc.md" >/dev/null
r="$(review init "TASK-B")"
assert_eq "init runs on a closed cycle" "0" "$(rc_of "$r")"
assert_eq "the new task is named" "TASK-B" "$(state_cmd get task_description)"
assert_eq "the counter starts over" "0" "$(state_cmd get iteration)"
if [ -f "$STATE_DIR/verdict.phase" ]; then
    fail "init clears the previous marker" "verdict.phase survived init"
else
    pass "init clears the previous marker"
fi
ARCHIVED="$(find "$REPO/.codex-review/archive" -name 'verdict.phase' | wc -l)"
if [ "$ARCHIVED" -gt 0 ]; then
    pass "the marker travels into the archive"
else
    fail "the marker travels into the archive" "no verdict.phase under the archive"
fi

# ============================
# Test 5: every round after the first says what it continues
# ============================
printf 'Test 5: a round names the task it is continuing\n'

r="$(review code --description-file "$REPO/desc.md")"
assert_lacks "the first round says nothing about continuing" "Continuing task" "$(msg_of "$r")"

r="$(review code --description-file "$REPO/desc.md")"
assert_contains "the second round says it is continuing" "Continuing task" "$(msg_of "$r")"
assert_contains "it names the task" "TASK-B" "$(msg_of "$r")"
assert_contains "it names the previous round" "previous round 20" "$(msg_of "$r")"

printf '\nPASS: %d  FAIL: %d\n' "$PASS" "$FAIL"
[ "$FAIL" -eq 0 ]
