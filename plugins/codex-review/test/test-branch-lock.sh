#!/bin/sh
# Regression tests for the lock a review command holds over its branch.
#
# Reading the iteration counter, sending the round and writing the result back
# is one sequence. Two runs of the same branch used to interleave freely: they
# read the same counter, deleted each other's verdict file and filed their
# notes under one number.
#
# Does NOT require the `codex` binary — a stub on PATH plays the reviewer.

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
if [ -n "$from_stdin" ]; then
    prompt="$(mktemp)"
    cat > "$prompt"
    verdict_file="$(sed -n 's|^After your review, write your verdict to \(.*\)$|\1|p' "$prompt" | head -1)"
    rm -f "$prompt"
    # `slow-next` holds this call — and so the lock its caller holds — until
    # the scenario lets go, rather than for a length of time that a slow
    # machine could outrun.
    REPO_DIR="$(dirname "$(dirname "$0")")"
    if [ -f "$REPO_DIR/slow-next" ]; then
        rm -f "$REPO_DIR/slow-next"
        : > "$REPO_DIR/reviewer-ready"
        while [ ! -e "$REPO_DIR/reviewer-release" ]; do
            sleep 0.1 2>/dev/null || true
        done
        rm -f "$REPO_DIR/reviewer-release"
    fi
    [ -n "$verdict_file" ] && printf 'CHANGES_REQUESTED\n' > "$verdict_file"
fi
[ -n "$out" ] && printf 'A review.\n' > "$out"
printf 'session sess_teststub01 ready\n'
exit 0
STUB
chmod +x "$REPO/bin/codex"

STATE_DIR="$REPO/.codex-review/main"
LOCK="$STATE_DIR/.lock"
HOST="$(uname -n 2>/dev/null || echo "unknown host")"

# $RUN_BIN goes in front of PATH: the scenarios that need an operation to fail
# put a stub there and take it away afterwards.
RUN_BIN="$REPO/bin"
MV_FAIL=""
RM_FAIL=""
export MV_FAIL RM_FAIL

state_cmd() {
    _out="$(
        unset CODEX_REVIEWER CODEX_SESSION_ID CODEX_MAX_ITERATIONS
        cd "$REPO" && PATH="$RUN_BIN:$PATH" bash "$STATE_CMD" "$@" 2>&1
    )" && _rc=0 || _rc=$?
    printf '%s|%s\n' "$_rc" "$(printf '%s' "$_out" | tr '\n' ' ')"
}

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

rc_of() { printf '%s' "${1%%|*}"; }
msg_of() { printf '%s' "${1#*|}"; }

# Puts a lock in place on behalf of $1 (pid), $2 (host), $3 (command) and, when
# given, $4 (uid — this user by default).
plant_lock() {
    rm -rf "$LOCK"
    mkdir -p "$LOCK"
    {
        printf 'pid=%s\n' "$1"
        printf 'uid=%s\n' "${4:-$(id -u)}"
        printf 'host=%s\n' "$2"
        printf 'command=%s\n' "$3"
        printf 'started=%s\n' "2026-09-04T12:00:00Z"
    } > "$LOCK/owner"
}

# A lock with no owner record at all, as a killed claim leaves between creating
# the directory and publishing its record.
plant_bare_lock() {
    rm -rf "$LOCK"
    mkdir -p "$LOCK"
}

# An `mv` that refuses the target named in MV_FAIL and passes everything else to
# the real one. A file permission would not do here: the directory it would have
# to sit on is the one the code under test creates.
mkdir -p "$REPO/failbin"
cat > "$REPO/failbin/mv" <<'MVSTUB'
#!/bin/sh
for arg in "$@"; do
    case "$arg" in
        -*) continue ;;
    esac
    case "${MV_FAIL:-}" in
        "") ;;
        *)
            case "$arg" in
                *"$MV_FAIL"*)
                    printf 'mv: cannot move %s: Permission denied\n' "$arg" >&2
                    exit 1
                    ;;
            esac
            ;;
    esac
done
exec /bin/mv "$@"
MVSTUB
chmod +x "$REPO/failbin/mv"

# An `rm` that refuses the target named in RM_FAIL, for the run whose claim
# fails and then cannot take back the directory it made.
cat > "$REPO/failbin/rm" <<'RMSTUB'
#!/bin/sh
for arg in "$@"; do
    case "$arg" in
        -*) continue ;;
    esac
    case "${RM_FAIL:-}" in
        "") ;;
        *)
            case "$arg" in
                *"$RM_FAIL"*)
                    printf 'rm: cannot remove %s: Permission denied\n' "$arg" >&2
                    exit 1
                    ;;
            esac
            ;;
    esac
done
exec /bin/rm "$@"
RMSTUB
chmod +x "$REPO/failbin/rm"

# A pid that is certainly not running: claimed, then reaped.
dead_pid() {
    _dp="$(sh -c 'echo $$')"
    printf '%s' "$_dp"
}

state_cmd set session_id test-session >/dev/null
printf 'What changed: nothing much.\n' > "$REPO/desc.md"

# ============================
# Test 1: a branch already in use is refused
# ============================
printf 'Test 1: a second run on a branch in use is turned away\n'

plant_lock "$$" "$HOST" "codex-review.sh code"

r="$(state_cmd set phase implementing)"
assert_eq "a state write is refused" "1" "$(rc_of "$r")"
assert_contains "the refusal says the branch is in use" "is in use by another run" "$(msg_of "$r")"
assert_contains "it names the holding command" "codex-review.sh code" "$(msg_of "$r")"
assert_contains "it names the holding process" "pid $$" "$(msg_of "$r")"
assert_contains "it names when the holder started" "2026-09-04T12:00:00Z" "$(msg_of "$r")"
assert_contains "it says to wait for the run that holds it" "Wait for it to finish" \
    "$(msg_of "$r")"
assert_contains "it says it changed nothing" "Nothing was changed" "$(msg_of "$r")"
assert_eq "the field was not written" "" "$(msg_of "$(state_cmd get phase)")"

r="$(review code --description-file "$REPO/desc.md")"
assert_eq "a review is refused too" "1" "$(rc_of "$r")"
assert_contains "the review refusal says the same" "is in use by another run" "$(msg_of "$r")"
if [ -f "$STATE_DIR/codex-code-1.log" ]; then
    fail "the refused review sent nothing" "an attempt log was written"
else
    pass "the refused review sent nothing"
fi

r="$(state_cmd reset)"
assert_eq "a reset is refused too" "1" "$(rc_of "$r")"

if [ -d "$LOCK" ]; then
    pass "the refused runs leave the lock alone"
else
    fail "the refused runs leave the lock alone" "the lock is gone"
fi

# ============================
# Test 2: reading the state needs no lock
# ============================
printf 'Test 2: reading works while the branch is in use\n'

assert_eq "get answers" "0" "$(rc_of "$(state_cmd get session_id)")"
assert_eq "show answers" "0" "$(rc_of "$(state_cmd show)")"
assert_eq "dir answers" "0" "$(rc_of "$(state_cmd dir)")"

# ============================
# Test 3: a lock left behind is reported, not taken
# ============================
printf 'Test 3: a lock whose owner is gone is refused and explained\n'

plant_lock "$(dead_pid)" "$HOST" "codex-review.sh code"
r="$(state_cmd set phase implementing)"
assert_eq "the run is refused" "1" "$(rc_of "$r")"
assert_contains "it says the holder is no longer running" "no longer running" "$(msg_of "$r")"
assert_contains "it says which directory to remove" "$LOCK" "$(msg_of "$r")"
assert_contains "it says it changed nothing" "Nothing was changed" "$(msg_of "$r")"
assert_eq "the field was not written" "" "$(msg_of "$(state_cmd get phase)")"
if [ -d "$LOCK" ]; then
    pass "the lock is left for a person to remove"
else
    fail "the lock is left for a person to remove" "it was removed"
fi

rm -rf "$LOCK"
r="$(state_cmd set phase implementing)"
assert_eq "once the lock is removed the run goes ahead" "0" "$(rc_of "$r")"
assert_eq "the write happened" "implementing" "$(msg_of "$(state_cmd get phase)")"

# ============================
# Test 4: a holder this machine cannot judge
# ============================
printf 'Test 4: a lock claimed elsewhere is refused without a guess\n'

plant_lock "$(dead_pid)" "some-other-machine" "codex-review.sh code"
r="$(state_cmd set phase code)"
assert_eq "the run is refused" "1" "$(rc_of "$r")"
assert_contains "the refusal names the other machine" "some-other-machine" "$(msg_of "$r")"
assert_contains "it does not claim to know" "cannot be told from here" "$(msg_of "$r")"
assert_lacks "it does not call the holder gone" "no longer running" "$(msg_of "$r")"
assert_eq "the field kept its value" "implementing" "$(msg_of "$(state_cmd get phase)")"

# A pid of another user tells nothing: `kill -0` refuses a live process of
# another user exactly as it refuses a pid that does not exist.
plant_lock "$(dead_pid)" "$HOST" "codex-review.sh code" "$(( $(id -u) + 1 ))"
r="$(state_cmd set phase code)"
assert_eq "a lock of another user is refused" "1" "$(rc_of "$r")"
assert_contains "it does not call that holder gone either" "cannot be told from here" \
    "$(msg_of "$r")"

plant_bare_lock
r="$(state_cmd set phase code)"
assert_eq "a lock with no owner record is refused" "1" "$(rc_of "$r")"
assert_contains "the refusal says the holder is unrecorded" "an unrecorded command" "$(msg_of "$r")"
assert_contains "it still says which directory to remove" "$LOCK" "$(msg_of "$r")"

rm -rf "$LOCK"

# ============================
# Test 5: a command releases what it took
# ============================
printf 'Test 5: the lock is released when the command ends\n'

r="$(state_cmd set phase code)"
assert_eq "the write runs" "0" "$(rc_of "$r")"
if [ -d "$LOCK" ]; then
    fail "a finished write leaves no lock" "the lock is still there"
else
    pass "a finished write leaves no lock"
fi

r="$(review code --description-file "$REPO/desc.md")"
assert_eq "the review runs" "0" "$(rc_of "$r")"
if [ -d "$LOCK" ]; then
    fail "a finished review leaves no lock" "the lock is still there"
else
    pass "a finished review leaves no lock"
fi

# A value the state file cannot hold is refused after the lock is taken, so
# this is the failing path that has a lock to release.
r="$(state_cmd set iteration abc)"
assert_eq "a rejected value exits 1" "1" "$(rc_of "$r")"
assert_contains "the rejection is about the value" "whole number" "$(msg_of "$r")"
if [ -d "$LOCK" ]; then
    fail "a command that failed mid-way leaves no lock" "the lock is still there"
else
    pass "a command that failed mid-way leaves no lock"
fi
assert_eq "the branch is free again" "0" "$(rc_of "$(state_cmd set phase code)")"

# ============================
# Test 6: a command meets a lock that is genuinely held
# ============================
printf 'Test 6: a run that starts while another holds the branch is turned away\n'

# The reviewer stub reports itself ready and then waits to be let go, so the
# review holds the lock for exactly as long as this scenario needs and no
# assertion rides on a length of time.
rm -f "$REPO/reviewer-ready" "$REPO/reviewer-release"
: > "$REPO/slow-next"
(
    unset CODEX_REVIEWER CODEX_MODEL CODEX_REASONING_EFFORT \
          CODEX_MAX_ITERATIONS CODEX_YOLO CODEX_SESSION_ID AUTO_REVIEW \
          CODEX_REVIEWER_PROMPT CODEX_PLAN_GUIDE CODEX_CODE_GUIDE \
          CODEX_SEVERITY_CALIBRATION
    cd "$REPO" && PATH="$REPO/bin:$PATH" CODEX_HOME="$REPO/codex-home" \
        bash "$REVIEW_CMD" code --description-file "$REPO/desc.md" \
        > "$REPO/out-review" 2>&1
) &
REVIEW_PID=$!

WAITED=0
while [ ! -e "$REPO/reviewer-ready" ] && [ "$WAITED" -lt 200 ]; do
    sleep 0.1 2>/dev/null || true
    WAITED=$((WAITED + 1))
done
rm -f "$REPO/reviewer-ready"
if [ -d "$LOCK" ]; then
    pass "the running review holds the branch"
else
    fail "the running review holds the branch" "no lock appeared"
fi

r="$(state_cmd set phase implementing)"
assert_eq "a write started meanwhile is refused" "1" "$(rc_of "$r")"
assert_contains "the refusal names the review that holds it" "codex-review.sh code" \
    "$(msg_of "$r")"
assert_contains "it names the process still running" "pid " "$(msg_of "$r")"
assert_contains "it says the holder is running" "still running" "$(msg_of "$r")"

r2="$(review code --description-file "$REPO/desc.md")"
assert_eq "a second review is refused as well" "1" "$(rc_of "$r2")"

: > "$REPO/reviewer-release"
REVIEW_RC=0
wait "$REVIEW_PID" || REVIEW_RC=$?
assert_eq "the holding review finished cleanly" "0" "$REVIEW_RC"
if [ -d "$LOCK" ]; then
    fail "the review releases the branch when it ends" "the lock is still there"
else
    pass "the review releases the branch when it ends"
fi
assert_eq "the branch is usable again" "0" "$(rc_of "$(state_cmd set phase code)")"

# ============================
# Test 7: the lock is not an artifact of the session
# ============================
printf 'Test 7: a new session is refused by a lock and leaves nothing in the archive\n'

plant_lock "$(dead_pid)" "$HOST" "codex-review.sh code"
r="$(review init "another task")"
assert_eq "init on a held branch is refused" "1" "$(rc_of "$r")"
assert_contains "the refusal explains the lock" "is in use by another run" "$(msg_of "$r")"

rm -rf "$LOCK"
r="$(review init "another task")"
assert_eq "init runs once the branch is free" "0" "$(rc_of "$r")"
LOCKS="$(find "$REPO/.codex-review/archive" -name '.lock' 2>/dev/null | wc -l)"
assert_eq "no lock reached the archive" "0" "$LOCKS"
if [ -d "$LOCK" ]; then
    fail "init leaves no lock behind" "the lock is still there"
else
    pass "init leaves no lock behind"
fi

# ============================
# Test 8: a claim that cannot record its owner is not a claim
# ============================
printf 'Test 8: a lock whose owner cannot be recorded is given back\n'

rm -rf "$LOCK"
BEFORE_PHASE="$(msg_of "$(state_cmd get phase)")"

RUN_BIN="$REPO/failbin:$REPO/bin"
MV_FAIL=".owner.tmp"
r="$(state_cmd set phase implementing)"
MV_FAIL=""
RUN_BIN="$REPO/bin"

assert_eq "the command fails" "1" "$(rc_of "$r")"
assert_contains "it says the owner could not be recorded" "Failed to record the owner" \
    "$(msg_of "$r")"
assert_contains "it says it changed nothing" "Nothing was changed" "$(msg_of "$r")"
assert_eq "the field kept its value" "$BEFORE_PHASE" "$(msg_of "$(state_cmd get phase)")"
if [ -d "$LOCK" ]; then
    fail "the half-made lock is given back" "the lock is still there"
else
    pass "the half-made lock is given back"
fi
assert_eq "the branch is usable again" "0" "$(rc_of "$(state_cmd set phase code)")"

# ============================
# Test 9: a shell asked to stop keeps the branch until its reviewer is done
# ============================
printf 'Test 9: a stop signal waits for the reviewer before the branch is given back\n'

rm -f "$REPO/reviewer-ready" "$REPO/reviewer-release"
: > "$REPO/slow-next"
(
    unset CODEX_REVIEWER CODEX_MODEL CODEX_REASONING_EFFORT \
          CODEX_MAX_ITERATIONS CODEX_YOLO CODEX_SESSION_ID AUTO_REVIEW \
          CODEX_REVIEWER_PROMPT CODEX_PLAN_GUIDE CODEX_CODE_GUIDE \
          CODEX_SEVERITY_CALIBRATION
    cd "$REPO" && PATH="$REPO/bin:$PATH" CODEX_HOME="$REPO/codex-home" \
        bash "$REVIEW_CMD" code --description-file "$REPO/desc.md" \
        > "$REPO/out-signalled" 2>&1
) &
SIGNALLED_PID=$!

WAITED=0
while [ ! -e "$REPO/reviewer-ready" ] && [ "$WAITED" -lt 200 ]; do
    sleep 0.1 2>/dev/null || true
    WAITED=$((WAITED + 1))
done
rm -f "$REPO/reviewer-ready"

SHELL_PID="$(sed -n 's/^pid=//p' "$LOCK/owner" 2>/dev/null | head -1)"
if [ -n "$SHELL_PID" ]; then
    pass "the lock names the shell running the review"
else
    fail "the lock names the shell running the review" "no pid in the record"
fi

# Only the shell is signalled, and the reviewer is still writing. A shell that
# gave the branch back here would leave the next run working beside it.
if kill -TERM "$SHELL_PID" 2>/dev/null; then
    pass "the stop signal reaches the shell"
else
    fail "the stop signal reaches the shell" "kill -TERM $SHELL_PID failed"
fi
sleep 0.5 2>/dev/null || true
if [ -d "$LOCK" ]; then
    pass "the branch is still held while the reviewer writes"
else
    fail "the branch is still held while the reviewer writes" "the lock is gone"
fi
r="$(state_cmd set phase implementing)"
assert_eq "a run started meanwhile is still refused" "1" "$(rc_of "$r")"

: > "$REPO/reviewer-release"
SIGNALLED_RC=0
wait "$SIGNALLED_PID" || SIGNALLED_RC=$?
assert_eq "the signalled run ends on the signal it was sent" "143" "$SIGNALLED_RC"

WAITED=0
while [ -d "$LOCK" ] && [ "$WAITED" -lt 100 ]; do
    sleep 0.1 2>/dev/null || true
    WAITED=$((WAITED + 1))
done
if [ -d "$LOCK" ]; then
    fail "the branch is given back once the reviewer is done" "the lock is still there"
    rm -rf "$LOCK"
else
    pass "the branch is given back once the reviewer is done"
fi
assert_eq "the branch is usable again" "0" "$(rc_of "$(state_cmd set phase code)")"

# ============================
# Test 10: a half-made lock that cannot be taken back is named
# ============================
printf 'Test 10: a claim that can neither record nor undo itself says so\n'

rm -rf "$LOCK"
RUN_BIN="$REPO/failbin:$REPO/bin"
MV_FAIL=".owner.tmp"
RM_FAIL=".lock"
r="$(state_cmd set phase implementing)"
MV_FAIL=""
RM_FAIL=""
RUN_BIN="$REPO/bin"

assert_eq "the command fails" "1" "$(rc_of "$r")"
assert_contains "it says the owner could not be recorded" "Failed to record the owner" \
    "$(msg_of "$r")"
assert_contains "it says the lock is still there" "still there" "$(msg_of "$r")"
assert_contains "it names the directory to remove" "$LOCK" "$(msg_of "$r")"
assert_lacks "it does not claim the lock is gone" "Nothing was changed." "$(msg_of "$r")"
if [ -d "$LOCK" ]; then
    pass "the lock it could not remove is left where it is"
else
    fail "the lock it could not remove is left where it is" "it is gone"
fi
rm -rf "$LOCK"
assert_eq "the branch is usable again" "0" "$(rc_of "$(state_cmd set phase code)")"

# ============================
# Test 11: a run removes only the lock that is still its own
# ============================
printf 'Test 11: a lock replaced under a running command is left alone\n'

# Someone removes the lock of a run that is still working — the one thing the
# refusal warns against — and the branch is claimed again. What the old run
# finds on its way out is not its lock: it may name the new holder, or name
# nobody yet while that holder is writing its record. Removing either would let
# a third command in beside the new holder.
for REPLACEMENT in named bare same-pid; do
    rm -f "$REPO/reviewer-ready" "$REPO/reviewer-release"
    : > "$REPO/slow-next"
    (
        unset CODEX_REVIEWER CODEX_MODEL CODEX_REASONING_EFFORT \
              CODEX_MAX_ITERATIONS CODEX_YOLO CODEX_SESSION_ID AUTO_REVIEW \
              CODEX_REVIEWER_PROMPT CODEX_PLAN_GUIDE CODEX_CODE_GUIDE \
              CODEX_SEVERITY_CALIBRATION
        cd "$REPO" && PATH="$REPO/bin:$PATH" CODEX_HOME="$REPO/codex-home" \
            bash "$REVIEW_CMD" code --description-file "$REPO/desc.md" \
            > "$REPO/out-replaced" 2>&1
    ) &
    HOLDER_PID=$!

    WAITED=0
    while [ ! -e "$REPO/reviewer-ready" ] && [ "$WAITED" -lt 200 ]; do
        sleep 0.1 2>/dev/null || true
        WAITED=$((WAITED + 1))
    done
    if [ -e "$REPO/reviewer-ready" ]; then
        pass "the review reports itself under way ($REPLACEMENT)"
    else
        # Without this the replacement would be planted on a branch nobody
        # holds, and every assertion below would pass for the wrong reason.
        fail "the review reports itself under way ($REPLACEMENT)" "no ready marker"
        # The run may be stuck before the stub ever reads the release marker,
        # so it is given a bounded chance to end and killed after it.
        : > "$REPO/reviewer-release"
        WAITED=0
        while kill -0 "$HOLDER_PID" 2>/dev/null && [ "$WAITED" -lt 100 ]; do
            sleep 0.1 2>/dev/null || true
            WAITED=$((WAITED + 1))
        done
        kill -KILL "$HOLDER_PID" 2>/dev/null || true
        wait "$HOLDER_PID" 2>/dev/null || true
        rm -rf "$LOCK"
        continue
    fi
    rm -f "$REPO/reviewer-ready"

    case "$REPLACEMENT" in
        named)
            plant_lock "$$" "$HOST" "codex-state.sh set phase"
            ;;
        bare)
            plant_bare_lock
            ;;
        same-pid)
            # A state directory on a shared filesystem is reached from more
            # than one machine, and process numbers are handed out per machine,
            # so the replacement can carry the number the old run is holding.
            HELD_PID="$(sed -n 's/^pid=//p' "$LOCK/owner" 2>/dev/null | head -1)"
            plant_lock "$HELD_PID" "some-other-machine" "codex-state.sh set phase"
            ;;
    esac

    : > "$REPO/reviewer-release"
    HOLDER_RC=0
    wait "$HOLDER_PID" || HOLDER_RC=$?
    assert_eq "the run that lost its lock still finishes ($REPLACEMENT)" "0" "$HOLDER_RC"
    if [ -d "$LOCK" ]; then
        pass "the replacement lock survives the old run ($REPLACEMENT)"
    else
        fail "the replacement lock survives the old run ($REPLACEMENT)" "it was removed"
    fi
    case "$REPLACEMENT" in
        named)
            assert_eq "the replacement still names its own holder" "$$" \
                "$(sed -n 's/^pid=//p' "$LOCK/owner" 2>/dev/null | head -1)"
            ;;
        same-pid)
            assert_eq "the replacement from another machine is untouched" \
                "some-other-machine" \
                "$(sed -n 's/^host=//p' "$LOCK/owner" 2>/dev/null | head -1)"
            ;;
    esac
    rm -rf "$LOCK"
done

assert_eq "the branch is usable again" "0" "$(rc_of "$(state_cmd set phase code)")"

printf '\nPASS: %d  FAIL: %d\n' "$PASS" "$FAIL"
[ "$FAIL" -eq 0 ]
