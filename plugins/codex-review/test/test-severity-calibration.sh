#!/bin/sh
# Tests for the severity calibration carried in the review prompt.
#
# Covers:
#   - the scale, the three headings and the verdict threshold reach Codex on
#     both phases, and the plan phase gets its own wording
#   - the pre-existing heading keeps a legacy defect at its own severity
#     instead of flattening it into the nice-to-have pile
#   - the late-round narrowing appears from round 3 and not before, and names
#     the round it is sent for
#   - CODEX_SEVERITY_CALIBRATION=false restores the previous prompt exactly
#   - a project guide still reaches Codex alongside the calibration
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

assert_file_contains() {
    # name, file, needle
    if [ ! -f "$2" ]; then
        fail "$1" "file not found: $2"
        return
    fi
    if grep -qF -- "$3" "$2"; then
        pass "$1"
    else
        fail "$1" "expected $2 to contain: $3"
    fi
}

assert_file_lacks() {
    # name, file, needle
    if [ ! -f "$2" ]; then
        fail "$1" "file not found: $2"
        return
    fi
    if grep -qF -- "$3" "$2"; then
        fail "$1" "expected $2 NOT to contain: $3"
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
# Test stub: reads the prompt from stdin, writes APPROVED to the -o target.
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
    (cd "$repo" && PATH="$repo/bin:$PATH" bash "$STATE_CMD" set session_id test-session >/dev/null 2>&1)
    echo "$repo"
}

run_review() {
    # repo, then codex-review.sh arguments. Never fails the suite on its own.
    _repo="$1"
    shift
    (cd "$_repo" && PATH="$_repo/bin:$PATH" CODEX_HOME="$_repo/codex-home" \
        bash "$REVIEW_CMD" "$@" >/dev/null 2>&1) || true
}

state_dir() {
    (cd "$1" && bash "$STATE_CMD" dir)
}

prompt_file() {
    # repo, phase, iteration — the prompt kept beside that attempt's log
    printf '%s/codex-%s-%s.prompt.md' "$(state_dir "$1")" "$2" "$3"
}

echo "=== Code phase: the scale reaches Codex ==="

REPO="$(make_repo)"
printf 'What changed: nothing much.\n' > "$REPO/desc.md"
run_review "$REPO" code --description-file "$REPO/desc.md"
P="$(prompt_file "$REPO" code 1)"

assert_file_contains "the scale names critical" "$P" "- critical — this change introduces a break that must not ship"
assert_file_contains "the scale names important" "$P" "- important — a real defect below that bar"
assert_file_contains "the scale names minor" "$P" "- minor — worth fixing, does not hold up the work"
assert_file_contains "no competing vocabulary is allowed" "$P" "no numeric ratings, no letter grades, no HIGH/MEDIUM/LOW"
assert_file_contains "ties resolve downwards" "$P" "Between two levels, take the lower one"
assert_file_contains "blocking heading is named" "$P" "'## Blocking'"
assert_file_contains "non-blocking heading is named" "$P" "'## Non-blocking'"
assert_file_contains "pre-existing heading is named" "$P" "'## Pre-existing'"
assert_file_contains "the threshold is stated" "$P" "Write CHANGES_REQUESTED when '## Blocking' has at least one entry"
assert_file_contains "approved-with-findings is normal" "$P" "APPROVED with findings listed under them is a normal verdict"
assert_file_contains "unreachable scenarios get no guard" "$P" "fix: none — record only"
assert_file_contains "a deferral with reasoning is accepted" "$P" "accept the deferral"
assert_file_contains "the loose threshold is replaced" "$P" "The severity scale above sets the verdict"
assert_file_lacks "the loose threshold is gone" "$P" "- If acceptable, respond with APPROVED"

echo "=== A pre-existing defect keeps its own severity ==="

assert_file_contains "legacy findings are graded on the same scale" "$P" \
    "a pre-existing critical finding is reported as"
assert_file_contains "legacy findings may not be dropped" "$P" \
    "Never soften it or leave it out because it is outside the current work"
assert_file_contains "newly reachable legacy defects block" "$P" \
    "makes a pre-existing defect reachable in a new way, it belongs under '## Blocking'"

rm -rf "$REPO"

echo "=== Plan phase: same scale, plan wording ==="

REPO="$(make_repo)"
printf 'What: a plan.\n' > "$REPO/plan.md"
run_review "$REPO" plan --plan-file "$REPO/plan.md"
P="$(prompt_file "$REPO" plan 1)"

assert_file_contains "critical is about what the plan leaves broken" "$P" \
    "- critical — the plan as written leaves a break that must not ship"
assert_file_contains "an unverified behaviour change is important" "$P" \
    "without naming how that behaviour will be verified is also important"
assert_file_contains "a plan need not enumerate every failure mode" "$P" \
    "A plan does not have to enumerate every failure mode to be approvable"
assert_file_contains "the plan phase gets the three headings too" "$P" "'## Pre-existing'"
assert_file_lacks "no code-phase wording leaks into the plan prompt" "$P" \
    "this change introduces a break"

rm -rf "$REPO"

echo "=== Late-round narrowing starts at round 3 ==="

REPO="$(make_repo)"
printf 'What changed: nothing much.\n' > "$REPO/desc.md"
run_review "$REPO" code --description-file "$REPO/desc.md"
run_review "$REPO" code --description-file "$REPO/desc.md"

assert_file_lacks "round 1 opens any subject" "$(prompt_file "$REPO" code 1)" \
    "Open a subject no earlier round raised only when it is critical"
assert_file_lacks "round 2 opens any subject" "$(prompt_file "$REPO" code 2)" \
    "Open a subject no earlier round raised only when it is critical"

run_review "$REPO" code --description-file "$REPO/desc.md"
P="$(prompt_file "$REPO" code 3)"

assert_file_contains "round 3 narrows what may be opened" "$P" \
    "Open a subject no earlier round raised only when it is critical"
assert_file_contains "round 3 says which round it is" "$P" "Round 3 of this phase"
assert_file_contains "earlier blockers are worked through first" "$P" \
    "A finding is closed when the new work leaves its scenario unreachable"
assert_file_contains "a late non-critical subject is minor" "$P" \
    "that is not critical is minor by this rule"

rm -rf "$REPO"

echo "=== The switch restores the previous prompt ==="

REPO="$(make_repo)"
mkdir -p "$REPO/.codex-review"
printf 'CODEX_SEVERITY_CALIBRATION=false\n' > "$REPO/.codex-review/config.env"
printf 'What changed: nothing much.\n' > "$REPO/desc.md"
run_review "$REPO" code --description-file "$REPO/desc.md"
P="$(prompt_file "$REPO" code 1)"

assert_file_lacks "no scale when switched off" "$P" "Severity scale"
assert_file_lacks "no headings when switched off" "$P" "'## Pre-existing'"
assert_file_contains "the previous threshold is back" "$P" "- If acceptable, respond with APPROVED"
assert_file_contains "the previous feedback line is back" "$P" \
    "- If changes needed, provide specific actionable feedback"

rm -rf "$REPO"

echo "=== A project guide survives alongside the calibration ==="

REPO="$(make_repo)"
mkdir -p "$REPO/.codex-review"
printf 'CODEX_CODE_GUIDE="Do not run the test suite."\n' > "$REPO/.codex-review/config.env"
printf 'What changed: nothing much.\n' > "$REPO/desc.md"
run_review "$REPO" code --description-file "$REPO/desc.md"
P="$(prompt_file "$REPO" code 1)"

assert_file_contains "the project guide is still sent" "$P" "Do not run the test suite."
assert_file_contains "the calibration is sent with it" "$P" "Severity scale"

rm -rf "$REPO"

echo ""
echo "PASS: $PASS  FAIL: $FAIL"
[ "$FAIL" -eq 0 ] || exit 1
