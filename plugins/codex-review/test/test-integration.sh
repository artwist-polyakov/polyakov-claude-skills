#!/bin/sh
# Path contract tests for codex-review plugin.
#
# Goal: catch drift between where `codex-state.sh dir` (via common.sh)
# computes the state directory and where `auto-approve-plan.sh` (standalone
# POSIX hook) looks for verdict.txt. If the two ever disagree, auto-approve
# silently breaks — these tests make that drift loud.
#
# Strategy: set up temp repos in various configurations, write
# APPROVED to <state_dir>/verdict.txt, run the hook, expect "allow".
# If the hook looks in a different path, it returns cold-start "deny".
#
# Does NOT require the `codex` binary — state scripts don't call it.

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROD_SCRIPTS="$SCRIPT_DIR/../skills/codex-review/scripts"
HOOK="$PROD_SCRIPTS/auto-approve-plan.sh"
STATE_CMD="$PROD_SCRIPTS/codex-state.sh"

PASS=0
FAIL=0

assert_eq() {
    test_name="$1"
    expected="$2"
    actual="$3"

    if [ "$actual" = "$expected" ]; then
        PASS=$((PASS + 1))
        printf "  PASS: %s\n" "$test_name"
    else
        FAIL=$((FAIL + 1))
        printf "  FAIL: %s\n" "$test_name"
        printf "    expected: %s\n" "$expected"
        printf "    actual:   %s\n" "$actual"
    fi
}

assert_allow() {
    test_name="$1"
    output="$2"
    case "$output" in
        *'"behavior":"allow"'*)
            PASS=$((PASS + 1))
            printf "  PASS: %s\n" "$test_name"
            ;;
        *)
            FAIL=$((FAIL + 1))
            printf "  FAIL: %s (hook did not allow — path mismatch?)\n" "$test_name"
            printf "    output: %s\n" "$output"
            ;;
    esac
}

# Stable fake session_id for integration tests. Must match the hook's stdin
# regex: hex chars + dashes only (mirrors real Claude UUID format).
SID_INT="abcdef01-2345-6789-abcd-ef0123456789"

hook_stdin() {
    printf '{"session_id":"%s","hook_event_name":"PermissionRequest","tool_name":"ExitPlanMode"}\n' "$1"
}

run_hook_in() {
    # Run hook from the given directory with a valid session_id in stdin.
    (cd "$1" && hook_stdin "$SID_INT" | sh "$HOOK" 2>/dev/null) || true
}

# Claim the session in the given state dir so the hook can proceed past
# the session-binding guard and actually read verdict.txt.
claim_session_in() {
    printf '%s\n' "$SID_INT" > "$1/current_session.txt"
}

git_init_repo() {
    # $1 = dir, $2 = initial branch
    mkdir -p "$1"
    (
        cd "$1"
        git init -q -b "$2"
        git config user.email "test@test.com"
        git config user.name "Test"
    )
}

TMPDIR_BASE="${TMPDIR:-/tmp}"

# ============================
# Test 1: basic main branch with a commit
# ============================
printf "Test 1: basic main branch — state dir matches hook path\n"
T1="$TMPDIR_BASE/test-int-main-$$"
git_init_repo "$T1" "main"
(
    cd "$T1"
    git commit -q --allow-empty -m "init"

    mkdir -p .codex-review
    printf 'AUTO_REVIEW=true\n' > .codex-review/config.env

    state_dir="$(bash "$STATE_CMD" dir)"
)
state_dir="$(cd "$T1" && bash "$STATE_CMD" dir)"
assert_eq "codex-state.sh dir = <repo>/.codex-review/main" \
    "$T1/.codex-review/main" "$state_dir"

claim_session_in "$state_dir"
printf 'APPROVED' > "$state_dir/verdict.txt"
printf 'plan\n' > "$state_dir/verdict.phase"
out1="$(run_hook_in "$T1")"
assert_allow "hook reads verdict from same dir" "$out1"
rm -rf "$T1"

# ============================
# Test 2: slashed branch name — must be normalized to dashes
# ============================
printf "Test 2: slashed branch (feat/my-feature → feat-my-feature)\n"
T2="$TMPDIR_BASE/test-int-slash-$$"
git_init_repo "$T2" "feat/my-feature"
(
    cd "$T2"
    git commit -q --allow-empty -m "init"
    mkdir -p .codex-review
    printf 'AUTO_REVIEW=true\n' > .codex-review/config.env
)
state_dir2="$(cd "$T2" && bash "$STATE_CMD" dir)"
assert_eq "slash → dash in dir name" \
    "$T2/.codex-review/feat-my-feature" "$state_dir2"

claim_session_in "$state_dir2"
printf 'APPROVED' > "$state_dir2/verdict.txt"
printf 'plan\n' > "$state_dir2/verdict.phase"
out2="$(run_hook_in "$T2")"
assert_allow "hook reads from slash-normalized dir" "$out2"
rm -rf "$T2"

# ============================
# Test 3: fresh repo without commits — symbolic-ref fallback
# ============================
# Regression: previously the hook used `git rev-parse --abbrev-ref HEAD`
# which returns "HEAD" in a fresh repo, causing a path mismatch with
# common.sh (which uses symbolic-ref). Both now use symbolic-ref first.
printf "Test 3: fresh repo without commits (symbolic-ref fallback)\n"
T3="$TMPDIR_BASE/test-int-nocommit-$$"
git_init_repo "$T3" "main"
(
    cd "$T3"
    mkdir -p .codex-review
    printf 'AUTO_REVIEW=true\n' > .codex-review/config.env
)
state_dir3="$(cd "$T3" && bash "$STATE_CMD" dir)"
assert_eq "no-commit repo uses branch name, not 'HEAD'" \
    "$T3/.codex-review/main" "$state_dir3"

claim_session_in "$state_dir3"
printf 'APPROVED' > "$state_dir3/verdict.txt"
printf 'plan\n' > "$state_dir3/verdict.phase"
out3="$(run_hook_in "$T3")"
assert_allow "hook allows in no-commit repo" "$out3"
rm -rf "$T3"

# ============================
# Test 4: git worktree — state dir must live in the MAIN repo, not worktree
# ============================
# common.sh::get_main_repo_root uses --git-common-dir to always resolve to the
# original repo even from a worktree. auto-approve-plan.sh does the same.
# If either drifts, verdict.txt in the main repo is invisible from the worktree
# (or vice versa) and auto-approve silently stops working.
printf "Test 4: git worktree — state dir lives in main repo\n"
T4="$TMPDIR_BASE/test-int-worktree-$$"
git_init_repo "$T4" "main"
(
    cd "$T4"
    git commit -q --allow-empty -m "init"
    mkdir -p .codex-review
    printf 'AUTO_REVIEW=true\n' > .codex-review/config.env
    # Create worktree on a new branch
    git worktree add -q -b feat/wt "$T4-wt" 2>/dev/null
)
# From inside the worktree, state dir should still point to the main repo
state_dir4="$(cd "$T4-wt" && bash "$STATE_CMD" dir)"
assert_eq "worktree state dir lives in MAIN repo" \
    "$T4/.codex-review/feat-wt" "$state_dir4"

claim_session_in "$state_dir4"
printf 'APPROVED' > "$state_dir4/verdict.txt"
printf 'plan\n' > "$state_dir4/verdict.phase"
out4="$(run_hook_in "$T4-wt")"
assert_allow "hook (run from worktree) reads main-repo verdict" "$out4"

# Cleanup worktree properly
(cd "$T4" && git worktree remove --force "$T4-wt" 2>/dev/null) || true
rm -rf "$T4" "$T4-wt"

# ============================
# Summary
# ============================
printf "\n=== Integration results: %d passed, %d failed ===\n" "$PASS" "$FAIL"
if [ "$FAIL" -gt 0 ]; then
    exit 1
fi
