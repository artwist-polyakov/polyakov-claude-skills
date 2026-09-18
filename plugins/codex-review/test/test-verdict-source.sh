#!/bin/sh
# Regression tests for where the verdict comes from.
#
# The verdict file is the whole answer: a review counts only when that file
# holds APPROVED or CHANGES_REQUESTED. The reply text is never searched for a
# word — a reply saying "Not APPROVED; changes are required" used to be read as
# approval, which let unreviewed work through.
#
# Does NOT require the `codex` binary — a stub on PATH plays the reviewer and is
# told per call what to write where.

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
#   reply-next   — write this text as the reply (the -o target)
#   verdict-next — write this text to the verdict file the prompt names
# A marker that is absent means the stub writes nothing there. Only a review
# call is affected: `common.sh` probes `codex --version` before every run.
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
if [ -n "$from_stdin" ]; then
    if [ -f "$REPO_DIR/verdict-next" ] && [ -n "$verdict_file" ]; then
        cat "$REPO_DIR/verdict-next" > "$verdict_file"
        rm -f "$REPO_DIR/verdict-next"
    fi
    if [ -n "$out" ] && [ -f "$REPO_DIR/reply-next" ]; then
        cat "$REPO_DIR/reply-next" > "$out"
        rm -f "$REPO_DIR/reply-next"
    fi
    printf 'review done\n'
    exit 0
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

# An approved code round closes the cycle: the next round refuses to run and
# asks for a new task or a reset. The scenarios below carry on with the same
# cycle, so the stored status goes back to the one an open cycle carries. This
# leaves the counters alone, which the assertions read.
reopen_cycle() {
    (
        unset CODEX_REVIEWER CODEX_SESSION_ID CODEX_MAX_ITERATIONS
        cd "$REPO" && bash "$STATE_CMD" set last_review_status CHANGES_REQUESTED
    ) >/dev/null
}

number() {
    sed -n "s/.*\"$1\"[[:space:]]*:[[:space:]]*\\([0-9][0-9]*\\).*/\\1/p" \
        "$STATE_DIR/state.json" | head -1
}
field() {
    sed -n "s/.*\"$1\"[[:space:]]*:[[:space:]]*\"\\(.*\\)\".*/\\1/p" \
        "$STATE_DIR/state.json" | head -1
}

# ============================
# Test 1: a reply that refuses approval is not an approval
# ============================
printf 'Test 1: a reply saying "Not APPROVED" does not approve anything\n'

printf 'Not APPROVED; changes are required.\n' > "$REPO/reply-next"
r="$(run_review)"
assert_eq "the run exits 1" "1" "$(rc_of "$r")"
assert_eq "nothing is approved" "ERROR" "$(field last_review_status)"
assert_eq "the iteration is not spent" "0" "$(number iteration)"
assert_contains "the run says the verdict is missing" "returned no verdict" "$(msg_of "$r")"

# ============================
# Test 2: a reply alone never decides
# ============================
printf 'Test 2: a reply holding the word APPROVED still decides nothing\n'

printf 'Everything here is APPROVED as far as I can tell.\n' > "$REPO/reply-next"
r="$(run_review)"
assert_eq "the run exits 1" "1" "$(rc_of "$r")"
assert_eq "the status is the error" "ERROR" "$(field last_review_status)"
assert_eq "the iteration is still unspent" "0" "$(number iteration)"

if [ -f "$STATE_DIR/notes/code-review-1.md" ]; then
    fail "a round that never happened files no note" "a note was written"
else
    pass "a round that never happened files no note"
fi

# ============================
# Test 3: the reply is kept for a human to read
# ============================
printf 'Test 3: the reply of a round with no verdict is kept beside its log\n'

REPLY_FILES="$(find "$STATE_DIR" -maxdepth 1 -name 'codex-code-1*.reply.md' | wc -l)"
if [ "$REPLY_FILES" -gt 0 ]; then
    pass "the reply is kept as a .reply.md beside the attempt's log"
else
    fail "the reply is kept as a .reply.md beside the attempt's log" \
        "no reply file under $STATE_DIR"
fi
assert_contains "the run says where the reply is" ".reply.md" "$(msg_of "$r")"

# ============================
# Test 4: the verdict file decides, against the reply
# ============================
printf 'Test 4: the verdict file decides even when the reply says otherwise\n'

printf 'CHANGES_REQUESTED\n' > "$REPO/verdict-next"
printf 'Looks good to me, APPROVED.\n' > "$REPO/reply-next"
r="$(run_review)"
assert_eq "the run exits 0" "0" "$(rc_of "$r")"
assert_eq "the stored verdict is the one from the file" "CHANGES_REQUESTED" \
    "$(field last_review_status)"
assert_eq "the round is spent" "1" "$(number iteration)"

printf 'APPROVED\n' > "$REPO/verdict-next"
printf 'I would not merge this.\n' > "$REPO/reply-next"
r="$(run_review)"
assert_eq "an approval in the file approves" "APPROVED" "$(field last_review_status)"
assert_eq "that round is spent too" "2" "$(number iteration)"
reopen_cycle

# ============================
# Test 5: only the two words count
# ============================
printf 'Test 5: a verdict file holding anything else is no verdict\n'

for bad in 'APPROVED with caveats' 'approved' 'LGTM' ''; do
    printf '%s\n' "$bad" > "$REPO/verdict-next"
    printf 'A reply.\n' > "$REPO/reply-next"
    r="$(run_review)"
    assert_eq "a verdict file holding [$bad] exits 1" "1" "$(rc_of "$r")"
done
assert_eq "none of them spent an iteration" "2" "$(number iteration)"

# ============================
# Test 6: surrounding whitespace is not a problem
# ============================
printf 'Test 6: a verdict written with whitespace around it still counts\n'

printf '\n  APPROVED  \n\n' > "$REPO/verdict-next"
printf 'A reply.\n' > "$REPO/reply-next"
r="$(run_review)"
assert_eq "the padded verdict is read" "APPROVED" "$(field last_review_status)"
assert_eq "its round is spent" "3" "$(number iteration)"
reopen_cycle

# ============================
# Test 7: the kept reply is archived with the session it belongs to
# ============================
printf 'Test 7: a kept reply travels into the archive with its own session\n'

# One more round with no verdict, so a reply file is on disk. Earlier scenarios
# left reply files of their own, so this one is found by its own text rather
# than by taking whichever the directory lists first.
MARKER='the-reply-this-scenario-is-about'
printf '%s\n' "$MARKER" > "$REPO/reply-next"
r="$(run_review)"
KEPT="$(grep -l -- "$MARKER" "$STATE_DIR"/codex-code-*.reply.md 2>/dev/null | head -1)"
if [ -n "$KEPT" ]; then
    pass "a reply file is waiting in the state directory"
else
    fail "a reply file is waiting in the state directory" "none holding the marker"
fi

# Opening a session for the next task archives the previous one's artefacts.
(
    unset CODEX_REVIEWER CODEX_SESSION_ID AUTO_REVIEW
    cd "$REPO" && PATH="$REPO/bin:$PATH" CODEX_HOME="$REPO/codex-home" \
        bash "$REVIEW_CMD" init "the next task" >/dev/null 2>&1
) || true

KEPT_NAME="$(basename "$KEPT")"
ARCHIVED="$(find "$REPO/.codex-review/archive" -name "$KEPT_NAME" 2>/dev/null | wc -l)"
LEFT="$(find "$STATE_DIR" -maxdepth 1 -name 'codex-code-*.reply.md' 2>/dev/null | wc -l)"
assert_eq "that reply is in the archive" "1" "$ARCHIVED"
assert_eq "no reply is left for the next attempt to overwrite" "0" "$LEFT"

printf '\nPASS: %d  FAIL: %d\n' "$PASS" "$FAIL"
[ "$FAIL" -eq 0 ]
