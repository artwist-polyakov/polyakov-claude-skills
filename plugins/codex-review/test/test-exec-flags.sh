#!/bin/sh
# Tests for the flags every `codex exec` call carries.
#
# Covers:
#   - the configured model, reasoning effort, and Fast service tier reach the
#     call that opens the session, not only the calls that send reviews
#   - both call sites open with the same flags, so a session cannot be created
#     under one setting and reviewed under another
#   - an unset model/effort and disabled Fast mode are passed as nothing at all
#
# Does NOT require the real `codex` binary: a stub on PATH records the argv of
# every exec call, so the assertions read what the CLI would have received.

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROD_SCRIPTS="$SCRIPT_DIR/../skills/codex-review/scripts"
REVIEW_CMD="$PROD_SCRIPTS/codex-review.sh"

PASS=0
FAIL=0

pass() {
    PASS=$((PASS + 1))
    printf "  PASS: %s\n" "$1"
}

fail() {
    FAIL=$((FAIL + 1))
    printf "  FAIL: %s\n" "$1"
    [ -n "$2" ] && printf "    %s\n" "$2"
}

# The stub writes one argument per line, so a whole argument is matched as a
# whole line — `-c` must not be satisfied by a `-c` sitting inside some other
# string, and `--model` must not be satisfied by `--model-something`.
assert_argv_has() {
    # name, argv file, exact argument
    if [ ! -f "$2" ]; then
        fail "$1" "no call recorded: $2"
        return
    fi
    if grep -qxF -- "$3" "$2"; then
        pass "$1"
    else
        fail "$1" "expected the call to carry: $3"
    fi
}

assert_argv_lacks() {
    # name, argv file, exact argument
    if [ ! -f "$2" ]; then
        fail "$1" "no call recorded: $2"
        return
    fi
    if grep -qxF -- "$3" "$2"; then
        fail "$1" "expected the call NOT to carry: $3"
    else
        pass "$1"
    fi
}

# --- Repo with a codex stub on PATH ------------------------------------------
make_repo() {
    repo="$(mktemp -d)"
    git -C "$repo" init -q
    mkdir -p "$repo/bin"
    cat > "$repo/bin/codex" <<'STUB'
#!/bin/sh
# Test stub: records the argv of every exec call, one argument per line, then
# reads the prompt from stdin and writes APPROVED to the -o target.
# `common.sh` probes `codex --version` before every run; that probe is not an
# exec call and is not recorded, so the numbering counts review calls only.
_dir="$(dirname "$0")/.."
if [ "$1" = "exec" ]; then
    _n=1
    while [ -f "$_dir/argv-$_n.txt" ]; do _n=$((_n + 1)); done
    for _a in "$@"; do printf '%s\n' "$_a"; done > "$_dir/argv-$_n.txt"
fi
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
[ -n "$out" ] && printf 'APPROVED\n' > "$out"
printf 'session sess_teststub01 ready\n'
exit 0
STUB
    chmod +x "$repo/bin/codex"
    echo "$repo"
}

run_review() {
    # repo, then codex-review.sh arguments. Never fails the suite on its own.
    _repo="$1"
    shift
    (
        # Only the repo's own `.codex-review/config.env` may decide what a run
        # sees. Every setting `load_config` reads is cleared first, so a value
        # exported on the machine running the suite cannot answer an assertion
        # that was written about a repo which configures nothing.
        unset CODEX_MODEL CODEX_REASONING_EFFORT CODEX_FAST_MODE \
              CODEX_MAX_ITERATIONS \
              CODEX_YOLO CODEX_SESSION_ID AUTO_REVIEW CODEX_REVIEWER_PROMPT \
              CODEX_PLAN_GUIDE CODEX_CODE_GUIDE CODEX_SEVERITY_CALIBRATION
        cd "$_repo" && PATH="$_repo/bin:$PATH" CODEX_HOME="$_repo/codex-home" \
            bash "$REVIEW_CMD" "$@" >/dev/null 2>&1
    ) || true
}

argv_file() {
    # repo, call number — the argv of the Nth exec call the run made
    printf '%s/argv-%s.txt' "$1" "$2"
}

# The flags a call opens with are everything before `-o`, which is where the
# per-call arguments start: the output path, then `resume <id>` on a review.
# Comparing that block across the two call sites is what catches a flag added
# to one of them and not the other.
flags_block() {
    sed -n '1,/^-o$/p' "$1"
}

echo "=== The configured model, effort, and Fast mode reach every call ==="

REPO="$(make_repo)"
mkdir -p "$REPO/.codex-review"
cat > "$REPO/.codex-review/config.env" <<'CFG'
CODEX_MODEL=stub-model
CODEX_REASONING_EFFORT=xhigh
CODEX_FAST_MODE=true
CFG
printf 'A task.\n' > "$REPO/task.md"
printf 'What changed: nothing much.\n' > "$REPO/desc.md"

# Opening the session is the first exec call, the review that follows is the
# second — both are made against the same config.
run_review "$REPO" init --description-file "$REPO/task.md"
run_review "$REPO" code --description-file "$REPO/desc.md"

INIT_ARGV="$(argv_file "$REPO" 1)"
REVIEW_ARGV="$(argv_file "$REPO" 2)"

assert_argv_has "the session is opened with the configured model" \
    "$INIT_ARGV" "--model"
assert_argv_has "the model name reaches the call that opens the session" \
    "$INIT_ARGV" "stub-model"
assert_argv_has "the session is opened with a config override" \
    "$INIT_ARGV" "-c"
assert_argv_has "the configured effort reaches the call that opens the session" \
    "$INIT_ARGV" 'model_reasoning_effort="xhigh"'
assert_argv_has "Fast mode reaches the call that opens the session" \
    "$INIT_ARGV" 'service_tier="fast"'

assert_argv_has "the review carries the configured model" \
    "$REVIEW_ARGV" "stub-model"
assert_argv_has "the review carries the configured effort" \
    "$REVIEW_ARGV" 'model_reasoning_effort="xhigh"'
assert_argv_has "the review carries Fast mode" \
    "$REVIEW_ARGV" 'service_tier="fast"'

if [ -f "$INIT_ARGV" ] && [ -f "$REVIEW_ARGV" ] &&
    [ "$(flags_block "$INIT_ARGV")" = "$(flags_block "$REVIEW_ARGV")" ]; then
    pass "both calls open with the same flags"
else
    fail "both calls open with the same flags" \
        "session: $(flags_block "$INIT_ARGV" | tr '\n' ' ')| review: $(flags_block "$REVIEW_ARGV" | tr '\n' ' ')"
fi

rm -rf "$REPO"

echo "=== Unset model/effort and disabled Fast mode are passed as nothing ==="

REPO="$(make_repo)"
mkdir -p "$REPO/.codex-review"
printf 'CODEX_YOLO=false\n' > "$REPO/.codex-review/config.env"
printf 'A task.\n' > "$REPO/task.md"
printf 'What changed: nothing much.\n' > "$REPO/desc.md"

run_review "$REPO" init --description-file "$REPO/task.md"
run_review "$REPO" code --description-file "$REPO/desc.md"

INIT_ARGV="$(argv_file "$REPO" 1)"
REVIEW_ARGV="$(argv_file "$REPO" 2)"

assert_argv_lacks "no model flag when no model is configured" \
    "$INIT_ARGV" "--model"
assert_argv_lacks "no empty model name is passed" \
    "$INIT_ARGV" ""
assert_argv_lacks "no config override when no effort is configured" \
    "$INIT_ARGV" "-c"
assert_argv_lacks "no yolo flag when it is disabled" \
    "$INIT_ARGV" "--yolo"
assert_argv_lacks "Fast mode is absent from the session-opening call by default" \
    "$INIT_ARGV" 'service_tier="fast"'
assert_argv_lacks "the review passes no model flag either" \
    "$REVIEW_ARGV" "--model"
assert_argv_lacks "the review passes no config override either" \
    "$REVIEW_ARGV" "-c"
assert_argv_lacks "the review does not enable Fast mode by default" \
    "$REVIEW_ARGV" 'service_tier="fast"'

rm -rf "$REPO"

echo ""
echo "PASS: $PASS  FAIL: $FAIL"
[ "$FAIL" -eq 0 ] || exit 1
