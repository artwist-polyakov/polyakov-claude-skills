#!/bin/sh
# Tests for --description-file, per-attempt logs, and the saved request copy.
#
# Covers:
#   - --description-file rejects a missing file, an empty file, a description
#     also given inline, and a --plan-file given alongside it
#   - text read from the file reaches Codex verbatim, backticks included
#     (passing the same text as an argument makes the shell execute them)
#   - a log left by an earlier attempt at the same iteration is not overwritten
#   - the description sent for review is stored next to that attempt's log
#
# Does NOT require the real `codex` binary: a stub on PATH records the prompt
# and writes the verdict, so the whole review path runs offline.

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROD_SCRIPTS="$SCRIPT_DIR/../skills/codex-review/scripts"
REVIEW_CMD="$PROD_SCRIPTS/codex-review.sh"
STATE_CMD="$PROD_SCRIPTS/codex-state.sh"

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

assert_contains() {
    # name, haystack, needle
    case "$2" in
        *"$3"*) pass "$1" ;;
        *) fail "$1" "expected to contain: $3" ;;
    esac
}

assert_file_contains() {
    # name, file, needle
    if [ ! -f "$2" ]; then
        fail "$1" "file not found: $2"
        return
    fi
    if grep -qF "$3" "$2"; then
        pass "$1"
    else
        fail "$1" "expected $2 to contain: $3"
    fi
}

assert_file_exists() {
    if [ -f "$2" ]; then
        pass "$1"
    else
        fail "$1" "file not found: $2"
    fi
}

# A description written the way one normally writes about code: backticks and a
# dollar sign, both of which a shell would act on if this were an argument.
DESC_TEXT='What changed: `beforeSend` now drops empty payloads. Cost: $0 extra calls.'
MARKER='`beforeSend` now drops empty payloads'

# --- Repo with a codex stub on PATH ------------------------------------------
make_repo() {
    repo="$(mktemp -d)"
    git -C "$repo" init -q
    mkdir -p "$repo/bin"
    cat > "$repo/bin/codex" <<'STUB'
#!/bin/sh
# Test stub: records the prompt it was given, writes the verdict to -o target.
out=""
prompt=""
while [ $# -gt 0 ]; do
    case "$1" in
        -o) out="$2"; shift 2 ;;
        *) prompt="$1"; shift ;;
    esac
done
if [ -n "$FAKE_CODEX_PROMPT" ] && [ -n "$prompt" ]; then
    printf '%s' "$prompt" > "$FAKE_CODEX_PROMPT"
fi
[ -n "$out" ] && printf 'APPROVED\n' > "$out"
# Printed to the run log, where `init` looks for the new session id.
printf 'session sess_teststub01 ready\n'
exit 0
STUB
    chmod +x "$repo/bin/codex"
    (cd "$repo" && PATH="$repo/bin:$PATH" bash "$STATE_CMD" set session_id test-session >/dev/null 2>&1)
    echo "$repo"
}

run_review() {
    # repo, then codex-review.sh arguments; prints stderr+stdout, never fails
    _repo="$1"
    shift
    (cd "$_repo" && PATH="$_repo/bin:$PATH" CODEX_HOME="$_repo/codex-home" \
        bash "$REVIEW_CMD" "$@" 2>&1) || true
}

json_valid() {
    # Prefer python3, fall back to jq; skip the check if neither is available.
    if command -v python3 >/dev/null 2>&1; then
        python3 -c 'import json,sys; json.load(open(sys.argv[1]))' "$1" 2>/dev/null
    elif command -v jq >/dev/null 2>&1; then
        jq empty "$1" >/dev/null 2>&1
    else
        return 0
    fi
}

echo "=== Argument validation ==="

REPO="$(make_repo)"

out="$(run_review "$REPO" code --description-file "$REPO/nope.md")"
assert_contains "missing file is refused" "$out" "Description file not found"

: > "$REPO/empty.md"
out="$(run_review "$REPO" code --description-file "$REPO/empty.md")"
assert_contains "empty file is refused" "$out" "Description file is empty"

printf '%s\n' "$DESC_TEXT" > "$REPO/desc.md"
out="$(run_review "$REPO" code "inline text" --description-file "$REPO/desc.md")"
assert_contains "inline plus file is refused" "$out" "Pass it one way"

printf 'plan body\n' > "$REPO/plan.md"
out="$(run_review "$REPO" plan --plan-file "$REPO/plan.md" --description-file "$REPO/desc.md")"
assert_contains "plan-file plus description-file is refused" "$out" "not both"

rm -rf "$REPO"

echo "=== Description reaches Codex verbatim ==="

REPO="$(make_repo)"
printf '%s\n' "$DESC_TEXT" > "$REPO/desc.md"
PROMPT_LOG="$REPO/prompt.txt"

(cd "$REPO" && PATH="$REPO/bin:$PATH" FAKE_CODEX_PROMPT="$PROMPT_LOG" \
    bash "$REVIEW_CMD" code --description-file "$REPO/desc.md" >/dev/null 2>&1) || true

assert_file_contains "backticks survive into the Codex prompt" "$PROMPT_LOG" "$MARKER"

STATE_DIR="$(cd "$REPO" && bash "$STATE_CMD" dir)"
assert_file_exists "first attempt writes its log" "$STATE_DIR/codex-code-1.log"
assert_file_contains "the sent description is saved" "$STATE_DIR/codex-code-1.request.md" "$MARKER"

rm -rf "$REPO"

echo "=== A killed attempt keeps its log ==="

REPO="$(make_repo)"
printf '%s\n' "$DESC_TEXT" > "$REPO/desc.md"
STATE_DIR="$(cd "$REPO" && bash "$STATE_CMD" dir)"

# A run killed before it could write its verdict leaves the log behind and does
# not advance the iteration counter, so the next call reuses the same number.
printf 'reasoning of the killed run\n' > "$STATE_DIR/codex-code-1.log"

(cd "$REPO" && PATH="$REPO/bin:$PATH" \
    bash "$REVIEW_CMD" code --description-file "$REPO/desc.md" >/dev/null 2>&1) || true

assert_file_contains "earlier log is intact" "$STATE_DIR/codex-code-1.log" "reasoning of the killed run"
assert_file_exists "retry writes a second log" "$STATE_DIR/codex-code-1.2.log"
assert_file_contains "retry saves its own description" "$STATE_DIR/codex-code-1.2.request.md" "$MARKER"

rm -rf "$REPO"

echo "=== init keeps state.json valid and the task text in full ==="

REPO="$(make_repo)"
# A description as it comes out of a file: several lines, a double quote, a
# backslash — each of which would break a value embedded in state.json.
cat > "$REPO/task.md" <<'TASK'
Add a retry to `beforeSend` so empty payloads are dropped
Context: the queue calls it with a "stale" batch and a path like C:\tmp\out
Done when: no empty payload reaches the transport
TASK

run_review "$REPO" init --description-file "$REPO/task.md" >/dev/null

STATE_DIR="$(cd "$REPO" && bash "$STATE_CMD" dir)"

if json_valid "$STATE_DIR/state.json"; then
    pass "state.json stays valid JSON"
else
    fail "state.json stays valid JSON" "$(cat "$STATE_DIR/state.json")"
fi

label="$(cd "$REPO" && bash "$STATE_CMD" get task_description)"
case "$label" in
    "") fail "task label survives the round trip" "empty task_description" ;;
    *"beforeSend"*) pass "task label survives the round trip" ;;
    *) fail "task label survives the round trip" "got: $label" ;;
esac

lines="$(printf '%s\n' "$label" | wc -l | tr -d ' ')"
assert_eq_num() {
    if [ "$2" = "$3" ]; then pass "$1"; else fail "$1" "expected $3, got $2"; fi
}
assert_eq_num "task label is a single line" "$lines" "1"

assert_file_contains "full task text is kept beside the log" \
    "$STATE_DIR/codex-init.request.md" 'C:\tmp\out'
assert_file_contains "full task text keeps its last line" \
    "$STATE_DIR/codex-init.request.md" "no empty payload reaches the transport"

echo "=== a new session archives saved requests with their logs ==="

printf 'code description\n' > "$REPO/desc.md"
run_review "$REPO" code --description-file "$REPO/desc.md" >/dev/null
assert_file_exists "review wrote its request" "$STATE_DIR/codex-code-1.request.md"

run_review "$REPO" init --description-file "$REPO/task.md" >/dev/null

if ls "$STATE_DIR"/codex-*.request.md >/dev/null 2>&1; then
    # Only the request of the session just opened may remain.
    leftovers="$(ls "$STATE_DIR"/codex-*.request.md | grep -v 'codex-init.request.md$' || true)"
    if [ -z "$leftovers" ]; then
        pass "old requests left the active state dir"
    else
        fail "old requests left the active state dir" "still there: $leftovers"
    fi
else
    fail "old requests left the active state dir" "init did not write its own request"
fi

REVIEW_ROOT="$(dirname "$STATE_DIR")"
if ls "$REVIEW_ROOT"/archive/*/codex-code-1.request.md >/dev/null 2>&1; then
    pass "archived request sits next to its log"
else
    fail "archived request sits next to its log" "not found under $REVIEW_ROOT/archive/"
fi

rm -rf "$REPO"

echo ""
printf "PASS: %d  FAIL: %d\n" "$PASS" "$FAIL"
[ "$FAIL" -eq 0 ]
