#!/bin/sh
# Tests for --description-file, per-attempt logs, and the saved request copy.
#
# Covers:
#   - --description-file rejects a missing file, an empty file, a description
#     also given inline, and a --plan-file given alongside it
#   - text read from the file reaches Codex verbatim, backticks included
#     (passing the same text as an argument makes the shell execute them)
#   - a log left by an earlier attempt at the same iteration is not overwritten,
#     and neither is a request saved by an attempt that never got a log
#   - the description sent for review is stored next to that attempt's log
#   - the task label is validated as given and never rewritten
#   - a plan past the per-argument limit the kernel enforces still reaches Codex
#     whole, and the prompt sent is kept beside the log; init sends a task of
#     that size whole too, on its own path
#   - a new session archives the prompts along with the logs and requests
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
# The marker is written before anything is parsed, so "codex never ran" can be
# checked without depending on what it was called with.
# `-` in place of the prompt means the real codex reads it from stdin, which is
# how codex-review.sh sends it; the stub records that text the same way.
: > "$(dirname "$0")/../codex-called"
out=""
prompt=""
from_stdin=""
while [ $# -gt 0 ]; do
    case "$1" in
        -o) out="$2"; shift 2 ;;
        -) from_stdin=1; shift ;;
        *) prompt="$1"; shift ;;
    esac
done
if [ -n "$FAKE_CODEX_PROMPT" ]; then
    if [ -n "$from_stdin" ]; then
        cat > "$FAKE_CODEX_PROMPT"
    elif [ -n "$prompt" ]; then
        printf '%s' "$prompt" > "$FAKE_CODEX_PROMPT"
    fi
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

# Callers read run_review through a command substitution, which runs it in a
# subshell — so the exit status goes to a file rather than to a variable.
STATUS_FILE="$(mktemp)"
trap 'rm -f "$STATUS_FILE"' EXIT

run_review() {
    # repo, then codex-review.sh arguments; prints stderr+stdout, never fails.
    # The exit status is recorded so a refusal can be checked for the status it
    # exits with and not only for the text it prints.
    _repo="$1"
    shift
    _out="$( (cd "$_repo" && PATH="$_repo/bin:$PATH" CODEX_HOME="$_repo/codex-home" \
        bash "$REVIEW_CMD" "$@" 2>&1) )" && _st=0 || _st=$?
    printf '%s' "$_st" > "$STATUS_FILE"
    printf '%s' "$_out"
}

assert_refusal() {
    # name, output, needle — a refusal is its message and its exit status
    assert_contains "$1" "$2" "$3"
    assert_status "$1 — exits 1" 1
}

assert_status() {
    # name, expected status
    _got="$(cat "$STATUS_FILE")"
    if [ "$_got" = "$2" ]; then
        pass "$1"
    else
        fail "$1" "expected exit $2, got $_got"
    fi
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
assert_refusal "missing file is refused" "$out" "Description file not found"

: > "$REPO/empty.md"
out="$(run_review "$REPO" code --description-file "$REPO/empty.md")"
assert_refusal "empty file is refused" "$out" "Description file is empty"

printf '\n\n   \n' > "$REPO/blank.md"
out="$(run_review "$REPO" code --description-file "$REPO/blank.md")"
assert_refusal "a file of blank lines is refused too" "$out" "Description file is empty"

printf '%s\n' "$DESC_TEXT" > "$REPO/desc.md"
out="$(run_review "$REPO" code "inline text" --description-file "$REPO/desc.md")"
assert_refusal "inline plus file is refused" "$out" "Pass it one way"

printf 'plan body\n' > "$REPO/plan.md"
out="$(run_review "$REPO" plan --plan-file "$REPO/plan.md" --description-file "$REPO/desc.md")"
assert_refusal "plan-file plus description-file is refused" "$out" "not both"

# On plan the two options name the same input, so either spelling works.
out="$(run_review "$REPO" plan --description-file "$REPO/plan.md")"
assert_status "plan accepts --description-file as its plan" 0

out="$(run_review "$REPO" plan --description-file "$REPO/nope.md")"
assert_refusal "plan reports the file it could not read" "$out" "Description file not found"

rm -rf "$REPO"

echo "=== A path that starts with a dash is a path ==="

REPO="$(make_repo)"
STATE_DIR="$(cd "$REPO" && bash "$STATE_CMD" dir)"
printf 'plan body that starts with a dash in its name\n' > "$REPO/-dash.md"

out="$(run_review "$REPO" plan --plan-file -dash.md)"
assert_status "a plan file named like an option is read" 0
assert_file_exists "its copy is saved beside the state" "$STATE_DIR/plan.md"
assert_file_contains "the copy holds the plan" "$STATE_DIR/plan.md" "starts with a dash in its name"

rm -rf "$REPO"

echo "=== A read that fails is not an empty file ==="

REPO="$(make_repo)"
printf 'a body that never arrives whole\n' > "$REPO/partial.md"
# Stands in for a file the reader cannot finish — a device error, a truncated
# mount, a permission that root would not hit. Only this one file fails: every
# other read is handed to the real cat, so what the test proves is that the
# description read itself stopped the run, and not some later reader.
REAL_CAT="$(command -v cat)"
cat > "$REPO/bin/cat" <<STUB
#!/bin/sh
case "\$*" in
    *partial.md*)
        printf 'half of the '
        exit 1
        ;;
esac
exec "$REAL_CAT" "\$@"
STUB
chmod +x "$REPO/bin/cat"

PROMPT_LOG="$REPO/prompt.txt"
export FAKE_CODEX_PROMPT="$PROMPT_LOG"
out="$(run_review "$REPO" code --description-file "$REPO/partial.md")"
unset FAKE_CODEX_PROMPT

assert_refusal "a failed read is reported as one" "$out" "Could not read"
if [ -f "$REPO/codex-called" ] || [ -f "$PROMPT_LOG" ]; then
    fail "codex was never called" "the stub ran anyway"
else
    pass "codex was never called"
fi
STATE_DIR="$(cd "$REPO" && bash "$STATE_CMD" dir)"
leftovers=""
for f in "$STATE_DIR"/codex-*.request.md; do
    [ -e "$f" ] && leftovers="$leftovers $f"
done
if [ -n "$leftovers" ]; then
    fail "the half-read text was not saved" "written:$leftovers"
else
    pass "the half-read text was not saved"
fi

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

echo "=== The file is read to its last byte ==="

REPO="$(make_repo)"
STATE_DIR="$(cd "$REPO" && bash "$STATE_CMD" dir)"

# Trailing blank lines and a missing final newline are where reading a file
# through a command substitution loses bytes.
printf 'first line\n\nlast line\n\n\n' > "$REPO/tail.md"
run_review "$REPO" code --description-file "$REPO/tail.md" >/dev/null
if cmp -s "$REPO/tail.md" "$STATE_DIR/codex-code-1.request.md"; then
    pass "trailing blank lines survive"
else
    fail "trailing blank lines survive" \
        "$(od -c "$STATE_DIR/codex-code-1.request.md" | tail -3)"
fi

rm -rf "$REPO"

REPO="$(make_repo)"
STATE_DIR="$(cd "$REPO" && bash "$STATE_CMD" dir)"
printf 'no newline at the end' > "$REPO/tail.md"
run_review "$REPO" code --description-file "$REPO/tail.md" >/dev/null
if cmp -s "$REPO/tail.md" "$STATE_DIR/codex-code-1.request.md"; then
    pass "a file without a final newline is kept as it is"
else
    fail "a file without a final newline is kept as it is" \
        "$(od -c "$STATE_DIR/codex-code-1.request.md" | tail -3)"
fi

rm -rf "$REPO"

echo "=== A prompt past the argument limit still reaches Codex ==="

REPO="$(make_repo)"
STATE_DIR="$(cd "$REPO" && bash "$STATE_CMD" dir)"

# The kernel caps a single argument at 128 KB, so a plan this long cannot be
# passed on the command line at all — the exec fails with "Argument list too
# long" before codex starts. The plan built here is past that cap on purpose.
BIG="$REPO/big-plan.md"
i=0
while [ "$i" -lt 3000 ]; do
    printf 'Stage %04d: prose that makes the plan long enough to matter.\n' "$i"
    i=$((i + 1))
done > "$BIG"
printf 'PLAN TAIL: %s\n' "$MARKER" >> "$BIG"

PROMPT_LOG="$REPO/big-prompt.txt"
export FAKE_CODEX_PROMPT="$PROMPT_LOG"
out="$(run_review "$REPO" plan --plan-file "$BIG")"
unset FAKE_CODEX_PROMPT

assert_status "a plan past the argument limit is sent" 0
# The marker sits on the last line, so finding it proves the plan was not cut
# short somewhere in the middle.
assert_file_contains "the plan's last line reaches Codex" "$PROMPT_LOG" "$MARKER"
if [ "$(wc -c < "$PROMPT_LOG")" -ge "$(wc -c < "$BIG")" ]; then
    pass "the whole plan is in the prompt"
else
    fail "the whole plan is in the prompt" \
        "prompt $(wc -c < "$PROMPT_LOG") bytes, plan $(wc -c < "$BIG") bytes"
fi
assert_file_exists "the prompt sent is kept beside the log" "$STATE_DIR/codex-plan-1.prompt.md"

rm -rf "$REPO"

echo "=== init sends a task past the argument limit too ==="

# init builds and sends its prompt on its own path, so the plan case above does
# not cover it.
REPO="$(make_repo)"
STATE_DIR="$(cd "$REPO" && bash "$STATE_CMD" dir)"

BIG="$REPO/big-task.md"
i=0
while [ "$i" -lt 3000 ]; do
    printf 'Requirement %04d: prose that makes the task long enough to matter.\n' "$i"
    i=$((i + 1))
done > "$BIG"
printf 'TASK TAIL: %s\n' "$MARKER" >> "$BIG"

PROMPT_LOG="$REPO/big-init-prompt.txt"
export FAKE_CODEX_PROMPT="$PROMPT_LOG"
out="$(run_review "$REPO" init --description-file "$BIG" --task-label "a very long task")"
unset FAKE_CODEX_PROMPT

assert_status "a task past the argument limit is sent" 0
assert_file_contains "the task's last line reaches Codex" "$PROMPT_LOG" "$MARKER"
assert_file_exists "init keeps the prompt it sent" "$STATE_DIR/codex-init.prompt.md"

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

echo "=== A request saved without its log keeps its number ==="

REPO="$(make_repo)"
printf '%s\n' "$DESC_TEXT" > "$REPO/desc.md"
STATE_DIR="$(cd "$REPO" && bash "$STATE_CMD" dir)"

# The request is written before the run that sends it creates its log, so a run
# killed in that window leaves a request with no log beside it.
printf 'description of the killed run\n' > "$STATE_DIR/codex-code-1.request.md"

(cd "$REPO" && PATH="$REPO/bin:$PATH" \
    bash "$REVIEW_CMD" code --description-file "$REPO/desc.md" >/dev/null 2>&1) || true

assert_file_contains "the orphaned request is intact" \
    "$STATE_DIR/codex-code-1.request.md" "description of the killed run"
assert_file_contains "the retry saves its own request" \
    "$STATE_DIR/codex-code-1.2.request.md" "$MARKER"

rm -rf "$REPO"

echo "=== init: the caller names the task ==="

assert_eq_str() {
    if [ "$2" = "$3" ]; then pass "$1"; else fail "$1" "expected [$3], got [$2]"; fi
}

REPO="$(make_repo)"
# A description as it comes out of a file: several lines, a double quote, a
# backslash — none of which can live in a state.json value.
cat > "$REPO/task.md" <<'TASK'
Add a retry to `beforeSend` so empty payloads are dropped
Context: the queue calls it with a "stale" batch and a path like C:\tmp\out
Done when: no empty payload reaches the transport
TASK

STATE_DIR="$(cd "$REPO" && bash "$STATE_CMD" dir)"

out="$(run_review "$REPO" init --description-file "$REPO/task.md")"
assert_refusal "multi-line description without a label is refused" "$out" "single line"
assert_contains "the refusal says what to pass" "$out" "--task-label"
if [ -f "$STATE_DIR/codex-init.log" ] || [ -f "$STATE_DIR/codex-init.request.md" ]; then
    fail "refusal happens before the session is opened" "init left artefacts behind"
else
    pass "refusal happens before the session is opened"
fi
# Read the file rather than `get`: that subcommand reports an empty string as 0.
if grep -q '"task_description"[[:space:]]*:[[:space:]]*""' "$STATE_DIR/state.json" 2>/dev/null; then
    pass "nothing was recorded for the task"
else
    fail "nothing was recorded for the task" "$(cat "$STATE_DIR/state.json" 2>/dev/null)"
fi

LABEL='JWT auth: middleware + refresh endpoint'
run_review "$REPO" init --description-file "$REPO/task.md" --task-label "$LABEL" >/dev/null

if json_valid "$STATE_DIR/state.json"; then
    pass "state.json stays valid JSON"
else
    fail "state.json stays valid JSON" "$(cat "$STATE_DIR/state.json")"
fi

assert_eq_str "the label is stored as given" \
    "$(cd "$REPO" && bash "$STATE_CMD" get task_description)" "$LABEL"
assert_file_contains "STATUS.md points at the full text" \
    "$STATE_DIR/STATUS.md" "codex-init.request.md"
assert_file_contains "full task text is kept beside the log" \
    "$STATE_DIR/codex-init.request.md" 'C:\tmp\out'
assert_file_contains "full task text keeps its last line" \
    "$STATE_DIR/codex-init.request.md" "no empty payload reaches the transport"

echo "=== init saves the task text to its last byte ==="

rm -rf "$REPO"
REPO="$(make_repo)"
STATE_DIR="$(cd "$REPO" && bash "$STATE_CMD" dir)"
printf 'task line\n\nlast line\n\n\n' > "$REPO/init-tail.md"
run_review "$REPO" init --description-file "$REPO/init-tail.md" --task-label "tail task" >/dev/null
if cmp -s "$REPO/init-tail.md" "$STATE_DIR/codex-init.request.md"; then
    pass "trailing blank lines survive into the saved task"
else
    fail "trailing blank lines survive into the saved task" \
        "$(od -c "$STATE_DIR/codex-init.request.md" | tail -3)"
fi
rm -rf "$REPO"

REPO="$(make_repo)"
STATE_DIR="$(cd "$REPO" && bash "$STATE_CMD" dir)"
printf 'task without a final newline' > "$REPO/init-tail.md"
run_review "$REPO" init --description-file "$REPO/init-tail.md" --task-label "tail task" >/dev/null
if cmp -s "$REPO/init-tail.md" "$STATE_DIR/codex-init.request.md"; then
    pass "a task without a final newline is kept as it is"
else
    fail "a task without a final newline is kept as it is" \
        "$(od -c "$STATE_DIR/codex-init.request.md" | tail -3)"
fi
rm -rf "$REPO"

echo "=== init: a label that no reader could handle is refused ==="

REPO="$(make_repo)"
cat > "$REPO/task.md" <<'TASK'
Add a retry to `beforeSend` so empty payloads are dropped
Context: the queue calls it with a "stale" batch and a path like C:\tmp\out
Done when: no empty payload reaches the transport
TASK
STATE_DIR="$(cd "$REPO" && bash "$STATE_CMD" dir)"

out="$(run_review "$REPO" init --description-file "$REPO/task.md" --task-label 'says "stale" batch')"
assert_refusal "double quote in the label is refused" "$out" "double quote"

LONG="$(printf 'x%.0s' $(seq 1 201))"
out="$(run_review "$REPO" init --description-file "$REPO/task.md" --task-label "$LONG")"
assert_refusal "over-long label is refused" "$out" "the limit is 200"

out="$(run_review "$REPO" init --description-file "$REPO/task.md" --task-label "$(printf 'first\nsecond')")"
assert_refusal "two-line label is refused" "$out" "single line"

# A bare carriage return is invisible in a terminal but would land raw inside
# state.json and break every reader of it.
out="$(run_review "$REPO" init --description-file "$REPO/task.md" --task-label "$(printf 'first\rsecond')")"
assert_refusal "carriage return in the label is refused" "$out" "single line"

out="$(run_review "$REPO" init --description-file "$REPO/task.md" --task-label 'path C:\tmp\out')"
assert_refusal "backslash in the label is refused" "$out" "backslash"

out="$(run_review "$REPO" init --description-file "$REPO/task.md" --task-label '   ')"
assert_refusal "a label of spaces is refused" "$out" "space at its start or end"

out="$(run_review "$REPO" init --description-file "$REPO/task.md" --task-label 'padded name ')"
assert_refusal "a label padded with a space is refused, not trimmed" "$out" "space at its start or end"

# Given explicitly, an empty label is an error rather than a request to fall
# back to the description.
out="$(run_review "$REPO" init --description-file "$REPO/task.md" --task-label '')"
assert_refusal "an explicitly empty label is refused" "$out" "empty"

# A tab at the end used to be trimmed away before the checks could see it.
out="$(run_review "$REPO" init --description-file "$REPO/task.md" --task-label "$(printf 'trailing\t')")"
assert_refusal "a tab at the edge of the label is refused" "$out" "control characters"

printf 'code description\n' > "$REPO/desc.md"
out="$(run_review "$REPO" code --description-file "$REPO/desc.md" --task-label "$LABEL")"
assert_refusal "the label belongs to init only" "$out" "applies to init"

out="$(run_review "$REPO" code --description-file "$REPO/desc.md" --task-label '')"
assert_refusal "an empty label does not slip past that guard" "$out" "applies to init"

rm -rf "$REPO"

echo "=== init: a one-line description names itself ==="

REPO="$(make_repo)"
STATE_DIR="$(cd "$REPO" && bash "$STATE_CMD" dir)"
run_review "$REPO" init "Implement JWT authentication for API" >/dev/null
assert_eq_str "single-line description becomes the label" \
    "$(cd "$REPO" && bash "$STATE_CMD" get task_description)" \
    "Implement JWT authentication for API"

rm -rf "$REPO"

# Read from a file the same description carries a trailing newline, which is
# part of the document and not of the name.
REPO="$(make_repo)"
STATE_DIR="$(cd "$REPO" && bash "$STATE_CMD" dir)"
printf 'Implement JWT authentication for API\n' > "$REPO/one-line.md"
run_review "$REPO" init --description-file "$REPO/one-line.md" >/dev/null
assert_eq_str "a one-line file names the task without its newline" \
    "$(cd "$REPO" && bash "$STATE_CMD" get task_description)" \
    "Implement JWT authentication for API"

rm -rf "$REPO"

echo "=== README ignores the whole local review directory ==="

IGNORE_LIST="$(sed -n '/^### \.gitignore$/,/^### AGENTS\.md$/p' "$PROD_SCRIPTS/../README.md" \
    | awk '/^```$/ { if (inside) exit; inside = 1; next } inside { print }')"

assert_eq_str "the README ignores every current and future review artefact" \
    "$IGNORE_LIST" ".codex-review/"

echo "=== a new session archives saved requests with their logs ==="

REPO="$(make_repo)"
cat > "$REPO/task.md" <<'TASK'
Add a retry to `beforeSend` so empty payloads are dropped
Done when: no empty payload reaches the transport
TASK
STATE_DIR="$(cd "$REPO" && bash "$STATE_CMD" dir)"
run_review "$REPO" init --description-file "$REPO/task.md" --task-label "retry in beforeSend" >/dev/null

printf 'code description\n' > "$REPO/desc.md"
run_review "$REPO" code --description-file "$REPO/desc.md" >/dev/null
assert_file_exists "review wrote its request" "$STATE_DIR/codex-code-1.request.md"

run_review "$REPO" init --description-file "$REPO/task.md" --task-label "retry in beforeSend" >/dev/null

if ls "$STATE_DIR"/codex-*.request.md >/dev/null 2>&1; then
    # Only the request of the session just opened may remain.
    leftovers=""
    for f in "$STATE_DIR"/codex-*.request.md; do
        case "$f" in
            */codex-init.request.md) ;;
            *) leftovers="$leftovers $f" ;;
        esac
    done
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

# The prompt of the review just archived must not stay behind either. Its own
# name is unique per attempt, but codex-init.prompt.md is fixed, so a session
# left in place would be overwritten by the next one.
leftovers=""
for f in "$STATE_DIR"/codex-*.prompt.md; do
    case "$f" in
        */codex-init.prompt.md) ;;
        *) [ -e "$f" ] && leftovers="$leftovers $f" ;;
    esac
done
if [ -z "$leftovers" ]; then
    pass "old prompts left the active state dir"
else
    fail "old prompts left the active state dir" "still there: $leftovers"
fi

if ls "$REVIEW_ROOT"/archive/*/codex-code-1.prompt.md >/dev/null 2>&1; then
    pass "archived prompt sits next to its log"
else
    fail "archived prompt sits next to its log" "not found under $REVIEW_ROOT/archive/"
fi

if ls "$REVIEW_ROOT"/archive/*/codex-init.prompt.md >/dev/null 2>&1; then
    pass "the replaced session's init prompt is archived, not overwritten"
else
    fail "the replaced session's init prompt is archived, not overwritten" \
        "not found under $REVIEW_ROOT/archive/"
fi

rm -rf "$REPO"

echo ""
printf "PASS: %d  FAIL: %d\n" "$PASS" "$FAIL"
[ "$FAIL" -eq 0 ]
