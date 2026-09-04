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

# The state helper's command that clears the iteration counter but keeps the
# session and the notes.
CLEAR_CYCLE_CMD=reset

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

assert_file_exists() {
    # name, file
    if [ -f "$2" ]; then
        pass "$1"
    else
        fail "$1" "file not found: $2"
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
# A marker file makes one review call fail the way a lost connection or an
# exhausted quota does: a non-zero exit with no verdict written. Only a real
# review is failed — `common.sh` probes `codex --version` before every run, and
# failing that probe would abort before any review was attempted.
if [ -n "$from_stdin" ] && [ -f "$(dirname "$0")/../fail-next" ]; then
    rm -f "$(dirname "$0")/../fail-next"
    printf 'stream error: connection reset\n' >&2
    exit 1
fi
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
    (
        # Only the repo's own `.codex-review/config.env` may decide what a run
        # sees. Every setting `load_config` reads is cleared first, so a value
        # exported on the machine running the suite cannot answer an assertion
        # that was written about a repo which configures nothing.
        unset CODEX_MODEL CODEX_REASONING_EFFORT CODEX_MAX_ITERATIONS \
              CODEX_YOLO CODEX_SESSION_ID AUTO_REVIEW CODEX_REVIEWER_PROMPT \
              CODEX_PLAN_GUIDE CODEX_CODE_GUIDE CODEX_SEVERITY_CALIBRATION
        cd "$_repo" && PATH="$_repo/bin:$PATH" CODEX_HOME="$_repo/codex-home" \
            bash "$REVIEW_CMD" "$@" >/dev/null 2>&1
    ) || true
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
assert_file_contains "a non-blocking deferral is accepted" "$P" \
    "Accept a deferral of a '## Non-blocking' or '## Pre-existing' item"
assert_file_contains "a blocking finding needs more than a deferral" "$P" \
    "A '## Blocking' finding answered with a bare deferral stays open"
assert_file_contains "the loose threshold is replaced" "$P" "The severity scale above sets the verdict"
assert_file_lacks "the loose threshold is gone" "$P" "- If acceptable, respond with APPROVED"

echo "=== A pre-existing defect keeps its own severity ==="

assert_file_contains "the heading covers the files the change touches" "$P" \
    "defects that already exist in the files this change touches"
assert_file_contains "the reviewer may not roam beyond those files" "$P" \
    "Do not go looking beyond those files"
assert_file_contains "legacy findings keep their severity" "$P" \
    "never softened or left out because"
assert_file_contains "newly reachable legacy defects block" "$P" \
    "new way, it belongs under '## Blocking' instead"

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
assert_file_lacks "deferring on the plan phase is scoped to non-blocking items" "$P" \
    "Accept an item deferred"
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
    "it is closed, on the three conditions stated above and no others"
assert_file_contains "a late non-critical subject is minor" "$P" \
    "that is not critical is minor by this rule"

rm -rf "$REPO"

echo "=== A failed codex call spends neither a round nor an iteration ==="

REPO="$(make_repo)"
printf 'What changed: nothing much.\n' > "$REPO/desc.md"

# Round 1 dies inside codex with no verdict written: nothing was reviewed.
: > "$REPO/fail-next"
run_review "$REPO" code --description-file "$REPO/desc.md"
if [ -f "$(state_dir "$REPO")/notes/code-review-1.md" ]; then
    fail "a failed call leaves no review note" "note written for a call that failed"
else
    pass "a failed call leaves no review note"
fi

# Two real reviews follow. The failed call left both counters where they were,
# so the first retry is iteration 1 again and keeps its prompt beside the failed
# attempt's log; neither review may carry the narrowing.
run_review "$REPO" code --description-file "$REPO/desc.md"
run_review "$REPO" code --description-file "$REPO/desc.md"
assert_file_lacks "the retry after the failure is not narrowed" \
    "$(state_dir "$REPO")/codex-code-1.2.prompt.md" \
    "Open a subject no earlier round raised only when it is critical"
assert_file_lacks "second review after the failure is not narrowed" \
    "$(prompt_file "$REPO" code 2)" "Open a subject no earlier round raised only when it is critical"

# The third review is the one that narrows, and it says round 3.
run_review "$REPO" code --description-file "$REPO/desc.md"
P="$(prompt_file "$REPO" code 3)"
assert_file_contains "third review after the failure narrows" "$P" \
    "Open a subject no earlier round raised only when it is critical"
assert_file_contains "the failed call cost no round" "$P" "Round 3 of this phase"

rm -rf "$REPO"

echo "=== The round number counts reviews, not iterations ==="

REPO="$(make_repo)"
printf 'What changed: nothing much.\n' > "$REPO/desc.md"
run_review "$REPO" code --description-file "$REPO/desc.md"

# The iteration counter is moved on its own, the way a hand-edited cycle leaves
# it. The next review is the second one this cycle, whatever that counter says.
(cd "$REPO" && bash "$STATE_CMD" set iteration 3 >/dev/null 2>&1)
run_review "$REPO" code --description-file "$REPO/desc.md"
P="$(prompt_file "$REPO" code 4)"
assert_file_lacks "the second review is not narrowed" "$P" \
    "Open a subject no earlier round raised only when it is critical"

# One more review: it is round 3 of this cycle and narrows, while the iteration
# counter it was sent under is 5.
run_review "$REPO" code --description-file "$REPO/desc.md"
P="$(prompt_file "$REPO" code 5)"
assert_file_contains "the third review narrows whatever the counter says" "$P" \
    "Open a subject no earlier round raised only when it is critical"
assert_file_contains "the round number counts reviews" "$P" "Round 3 of this phase"
assert_file_lacks "the iteration number is not used as the round" "$P" "Round 5 of this phase"

rm -rf "$REPO"

echo "=== A cleared cycle starts the round count over ==="

REPO="$(make_repo)"
printf 'What changed: nothing much.\n' > "$REPO/desc.md"
run_review "$REPO" code --description-file "$REPO/desc.md"
run_review "$REPO" code --description-file "$REPO/desc.md"

# The state helper's cycle-clearing command keeps the session and the notes and
# starts the cycle over. The next review is round 1 of the new cycle, not round
# 3 of the old one.
(cd "$REPO" && bash "$STATE_CMD" "$CLEAR_CYCLE_CMD" >/dev/null 2>&1)
run_review "$REPO" code --description-file "$REPO/desc.md"

# The new cycle restarts at iteration 1, and iteration 1 already has a log from
# the old cycle — so this run's prompt is the retry name, not `codex-code-1`.
# Reading `codex-code-1` here would assert against the old cycle's first review
# and pass whether or not the count was cleared.
P="$(state_dir "$REPO")/codex-code-1.2.prompt.md"
assert_file_exists "the new cycle's prompt is kept under its own name" "$P"

assert_file_lacks "the first review of a new cycle is not narrowed" "$P" \
    "Open a subject no earlier round raised only when it is critical"
assert_file_lacks "the notes of the previous cycle do not count" "$P" "Round 3 of this phase"

if grep -q '"reviews_completed": 1' "$(state_dir "$REPO")/state.json"; then
    pass "the round count restarted at 1"
else
    fail "the round count restarted at 1" \
        "$(grep reviews_completed "$(state_dir "$REPO")/state.json")"
fi

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
