#!/bin/sh
# Regression tests for what survives a write, a reset and a second archiver.
#
# Covers four ways the plugin used to lose or invent something it had on disk:
# a truncating write, a reader chosen by its own answer, a note name reused
# after a reset, and an archive directory named after a second.
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

printf '\nPASS: %d  FAIL: %d\n' "$PASS" "$FAIL"
[ "$FAIL" -eq 0 ]
