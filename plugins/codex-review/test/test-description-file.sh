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
    (cd "$_repo" && PATH="$_repo/bin:$PATH" bash "$REVIEW_CMD" "$@" 2>&1) || true
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

echo ""
printf "PASS: %d  FAIL: %d\n" "$PASS" "$FAIL"
[ "$FAIL" -eq 0 ]
