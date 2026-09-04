#!/bin/sh
# Regression tests for what survives a write, a reset and a second archiver.
#
# Covers four ways the plugin used to lose or invent something it had on disk:
# a truncating write, a reader chosen by its own answer, a note name reused
# after a reset, and an archive directory named after a second. The last
# scenario holds the archiver to reporting a directory it cannot make.
#
# Does NOT require the `codex` binary — a stub on PATH plays the reviewer.

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROD_SCRIPTS="$SCRIPT_DIR/../skills/codex-review/scripts"
REVIEW_CMD="$PROD_SCRIPTS/codex-review.sh"
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
    [ -n "$verdict_file" ] && printf 'CHANGES_REQUESTED\n' > "$verdict_file"
fi
[ -n "$out" ] && printf 'A review.\n' > "$out"
printf 'session sess_teststub01 ready\n'
exit 0
STUB
chmod +x "$REPO/bin/codex"

(cd "$REPO" && bash "$STATE_CMD" set session_id test-session >/dev/null)
STATE_DIR="$REPO/.codex-review/main"
printf 'What changed: nothing much.\n' > "$REPO/desc.md"

run_review() {
    (
        unset CODEX_REVIEWER CODEX_MODEL CODEX_REASONING_EFFORT \
              CODEX_MAX_ITERATIONS CODEX_YOLO CODEX_SESSION_ID AUTO_REVIEW \
              CODEX_REVIEWER_PROMPT CODEX_PLAN_GUIDE CODEX_CODE_GUIDE \
              CODEX_SEVERITY_CALIBRATION
        cd "$REPO" && PATH="$REPO/bin:$PATH" CODEX_HOME="$REPO/codex-home" \
            bash "$REVIEW_CMD" code --description-file "$REPO/desc.md" >/dev/null 2>&1
    ) || true
}

state_cmd() {
    (
        unset CODEX_REVIEWER CODEX_SESSION_ID CODEX_MAX_ITERATIONS
        cd "$REPO" && bash "$STATE_CMD" "$@" 2>&1
    )
}

orphan_count() {
    find "$STATE_DIR" -maxdepth 1 -name '.state.json.*' | wc -l
}

rc_of() { printf '%s' "${1%%|*}"; }
msg_of() { printf '%s' "${1#*|}"; }

# Prints "<exit code>|<output on one line>"; `timeout` bounds a run that would
# otherwise never end. $1, when given, is the state directory to archive.
archive_run() {
    _dir="${1:-$STATE_DIR}"
    _out="$(
        timeout 10 bash -c '
            set -uo pipefail
            PATH="$3:$PATH"
            source "$1"
            STATE_DIR="$2"
            CODEX_MAX_ITERATIONS=5
            archive_previous_session
        ' _ "$COMMON" "$_dir" "$REPO/bin" 2>&1
    )" && _rc=0 || _rc=$?
    printf '%s|%s\n' "$_rc" "$(printf '%s' "$_out" | tr '\n' ' ')"
}

# Archives $1 once the gate file appears, so two of these claim their directory
# at the same moment rather than one after the other.
archive_at_gate() {
    timeout 10 bash -c '
        set -uo pipefail
        PATH="$3:$PATH"
        source "$1"
        STATE_DIR="$2"
        CODEX_MAX_ITERATIONS=5
        while [ ! -e "$4" ]; do :; done
        archive_previous_session
    ' _ "$COMMON" "$1" "$REPO/bin" "$GATE" >/dev/null 2>&1
}

# ============================
# Test 1: a write that cannot finish leaves the old state alone
# ============================
printf 'Test 1: a write that dies part way through does not empty state.json\n'

state_cmd set phase code >/dev/null
BEFORE="$(cat "$STATE_DIR/state.json")"

# common.sh is a bash script, so every call into it runs under bash even though
# the suite itself is POSIX sh.
write_state_in() {
    # $1 = extra shell setup (a ulimit, or nothing), $2 = json to write
    bash -c '
        set -uo pipefail
        eval "$1"
        source "$2"
        STATE_DIR="$3"
        write_state "$4"
    ' _ "$1" "$COMMON" "$STATE_DIR" "$2" >/dev/null 2>&1
}

# Positive control: the same call, unrestricted, does change the file — without
# this the assertion below would pass just as well on a write that never ran.
write_state_in ':' '{"control": 1}' || true
if [ "$BEFORE" = "$(cat "$STATE_DIR/state.json" 2>/dev/null || printf '<no state.json>')" ]; then
    fail "the control write reaches state.json" "the file did not change"
else
    pass "the control write reaches state.json"
fi
printf '%s\n' "$BEFORE" > "$STATE_DIR/state.json"
BEFORE="$(cat "$STATE_DIR/state.json")"

# `ulimit -f 0` lets a file be created but not filled: the write dies on the
# first byte, which is the shape of a full disk.
write_state_in 'ulimit -f 0' '{"wrecked": true}' || true

assert_eq "state.json is the one that was there" "$BEFORE" \
    "$(cat "$STATE_DIR/state.json" 2>/dev/null || printf '<no state.json>')"

# ============================
# Test 2: the next write clears what the killed one left
# ============================
printf 'Test 2: a temporary file left by a killed write is cleaned up\n'

state_cmd set phase implementing >/dev/null
assert_eq "no temporary state files are left behind" "0" "$(orphan_count)"
assert_eq "the write that followed took effect" "implementing" \
    "$(state_cmd get phase)"

# A temporary file belonging to a process that is still running is not touched.
printf 'not mine\n' > "$STATE_DIR/.state.json.$$"
state_cmd set phase code >/dev/null
if [ -f "$STATE_DIR/.state.json.$$" ]; then
    pass "a temporary file of a live process is left alone"
else
    fail "a temporary file of a live process is left alone" "it was removed"
fi
rm -f "$STATE_DIR/.state.json.$$"

# ============================
# Test 3: an empty field reads as empty, not as zero
# ============================
printf 'Test 3: get answers with what the field holds\n'

state_cmd reset >/dev/null
assert_eq "an empty string field is empty" "" "$(state_cmd get phase)"
assert_eq "an empty status field is empty" "" "$(state_cmd get last_review_status)"
assert_eq "a counter is a number" "0" "$(state_cmd get iteration)"

state_cmd set phase code >/dev/null
assert_eq "a string field that holds something returns it" "code" "$(state_cmd get phase)"

out="$(state_cmd get bogus_field)" && rc=0 || rc=$?
assert_eq "an unknown field exits 1" "1" "$rc"
assert_contains "an unknown field says so" "Unsupported state field: bogus_field" "$out"
assert_contains "an unknown field lists what can be read" "verdict" "$out"

assert_eq "session_id is still readable" "test-session" "$(state_cmd get session_id)"

# ============================
# Test 4: a reset does not put the next round's note over the previous one
# ============================
printf 'Test 4: notes of an earlier cycle survive a reset\n'

state_cmd reset >/dev/null
run_review
FIRST_NOTE="$STATE_DIR/notes/code-review-1.md"
if [ -f "$FIRST_NOTE" ]; then
    pass "the first cycle leaves its note"
else
    fail "the first cycle leaves its note" "no note at $FIRST_NOTE"
fi
FIRST_CONTENT="$(cat "$FIRST_NOTE" 2>/dev/null || printf '<no note>')"

state_cmd reset >/dev/null
run_review
assert_eq "the first note is untouched" "$FIRST_CONTENT" \
    "$(cat "$FIRST_NOTE" 2>/dev/null || printf '<no note>')"
if [ -f "$STATE_DIR/notes/code-review-1.2.md" ]; then
    pass "the new cycle's note takes the next free name"
else
    fail "the new cycle's note takes the next free name" \
        "$(find "$STATE_DIR/notes" -name 'code-review-*' | tr '\n' ' ')"
fi

# ============================
# Test 5: two archive runs at once get a directory each
# ============================
printf 'Test 5: archives claimed at the same moment do not share a directory\n'

ARCHIVE="$REPO/.codex-review/archive"
rm -rf "$ARCHIVE"

# The directory is named after the second the archive was made in, so the case
# under test is two runs inside one second. Waiting for that to happen by itself
# would make the outcome depend on how fast the machine is; a `date` stub on
# PATH puts both runs in the same second every time.
cat > "$REPO/bin/date" <<'DATESTUB'
#!/bin/sh
case "$*" in
    *%Y%m%dT*) printf '20260904T120000Z\n' ;;
    *) printf '2026-09-04T12:00:00Z\n' ;;
esac
DATESTUB
chmod +x "$REPO/bin/date"

# Two state directories under one review root: they compete for the archive
# name and nothing else, so neither run depends on what the other moves.
OTHER_DIR="$REPO/.codex-review/other-branch"
mkdir -p "$OTHER_DIR/notes"
printf 'first session\n' > "$STATE_DIR/last_response.txt"
printf 'second session\n' > "$OTHER_DIR/last_response.txt"

# Both runs park on the gate file, so they reach the claim together; a check
# followed by a create would hand them the same directory.
GATE="$REPO/gate"
rm -f "$GATE"
archive_at_gate "$STATE_DIR" &
FIRST_PID=$!
archive_at_gate "$OTHER_DIR" &
SECOND_PID=$!
sleep 1
: > "$GATE"
wait "$FIRST_PID" && FIRST_RC=0 || FIRST_RC=$?
wait "$SECOND_PID" && SECOND_RC=0 || SECOND_RC=$?

assert_eq "the first run succeeded" "0" "$FIRST_RC"
assert_eq "the second run succeeded" "0" "$SECOND_RC"

DIRS="$(find "$ARCHIVE" -mindepth 1 -maxdepth 1 -type d | wc -l)"
assert_eq "each archive run got its own directory" "2" "$DIRS"

MIXED=0
CONTENTS=""
for d in "$ARCHIVE"/*/; do
    if [ -f "$d/last_response.txt" ]; then
        CONTENTS="$CONTENTS $(cat "$d/last_response.txt")"
    else
        MIXED=1
    fi
done
assert_eq "each directory holds the session it archived" "0" "$MIXED"
assert_contains "one directory holds the first session" "first session" "$CONTENTS"
assert_contains "the other holds the second" "second session" "$CONTENTS"

# ============================
# Test 6: an archive that cannot be created stops instead of trying forever
# ============================
printf 'Test 6: an archive directory that cannot be created ends the run\n'

# The archiver only runs when there is something to archive.
printf 'third session\n' > "$STATE_DIR/last_response.txt"

# A read-only archive root: the timestamped directory cannot be created and
# does not exist afterwards either, which is the case a loop looking only at
# `mkdir` failing would take for a name collision and retry forever.
chmod 555 "$ARCHIVE"
r="$(archive_run)"
chmod 755 "$ARCHIVE"

assert_eq "the run ends with an error" "1" "$(rc_of "$r")"
assert_contains "the error names the directory" "archive" "$(msg_of "$r")"
assert_contains "the error says nothing was archived" "Nothing was archived" \
    "$(msg_of "$r")"
if [ -f "$STATE_DIR/last_response.txt" ]; then
    pass "the artefacts are left where they were"
else
    fail "the artefacts are left where they were" "last_response.txt is gone"
fi

# A file sitting where the archive root belongs: the root itself cannot be made.
rm -rf "$ARCHIVE"
printf 'not a directory\n' > "$ARCHIVE"
r="$(archive_run)"
rm -f "$ARCHIVE"

assert_eq "a blocked archive root also ends the run" "1" "$(rc_of "$r")"
assert_contains "that error says nothing was archived too" "Nothing was archived" \
    "$(msg_of "$r")"

# ============================
# Test 7: an artifact that cannot be moved is reported, not passed over
# ============================
printf 'Test 7: a move into the archive that fails stops the archiver\n'

rm -rf "$ARCHIVE"
printf 'fourth session\n' > "$STATE_DIR/last_response.txt"

# Moving a file out of a directory needs write permission on that directory,
# so a read-only state directory makes every move fail while leaving the
# artifacts readable.
chmod 555 "$STATE_DIR"
r="$(archive_run)"
chmod 755 "$STATE_DIR"

assert_eq "the archiver reports the failure" "1" "$(rc_of "$r")"
assert_contains "the message names the file that stayed" "last_response.txt" \
    "$(msg_of "$r")"
if [ -f "$STATE_DIR/last_response.txt" ]; then
    pass "the artifact is still where it was"
else
    fail "the artifact is still where it was" "last_response.txt is gone"
fi

printf '\nPASS: %d  FAIL: %d\n' "$PASS" "$FAIL"
[ "$FAIL" -eq 0 ]
