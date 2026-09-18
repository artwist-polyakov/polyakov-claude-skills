#!/bin/sh
# Tests for auto-approve-plan.sh hook script.
# Run from any directory inside a git repo.
# Creates a temporary .codex-review/ structure, runs the hook, checks output.

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROD_SCRIPTS="$SCRIPT_DIR/../skills/codex-review/scripts"
HOOK="$PROD_SCRIPTS/auto-approve-plan.sh"

# --- Setup temp repo ---
TMPDIR_BASE="${TMPDIR:-/tmp}"
TEST_DIR="$TMPDIR_BASE/test-auto-approve-$$"
mkdir -p "$TEST_DIR"
trap 'rm -rf "$TEST_DIR"' EXIT

cd "$TEST_DIR"
git init -q -b main

# No commits — symbolic-ref must still resolve branch name
BRANCH_SLUG="main"

PASS=0
FAIL=0

# Stable fake session IDs for tests
SID_A="11111111-1111-1111-1111-111111111111"
SID_B="22222222-2222-2222-2222-222222222222"

# Build hook stdin JSON with a given session_id
hook_stdin() {
    printf '{"session_id":"%s","hook_event_name":"PermissionRequest","tool_name":"ExitPlanMode"}\n' "$1"
}

# Write a verdict file (single-word content) together with the phase that left
# it. The hook honours an approval only from a plan review, so the marker
# defaults to that; pass a third argument to write a different one.
write_verdict() {
    _wv_path="$1"
    _wv_val="$2"
    _wv_phase="${3:-plan}"
    printf '%s' "$_wv_val" > "$_wv_path"
    printf '%s\n' "$_wv_phase" > "${_wv_path%/*}/verdict.phase"
}

# Claim the branch's current_session.txt for a given session id
claim_session_file() {
    printf '%s\n' "$1" > ".codex-review/$2/current_session.txt"
}

assert_output() {
    _ao_test_name="$1"
    _ao_expected="$2"
    _ao_actual="$3"

    if [ "$_ao_actual" = "$_ao_expected" ]; then
        PASS=$((PASS + 1))
        printf "  PASS: %s\n" "$_ao_test_name"
    else
        FAIL=$((FAIL + 1))
        printf "  FAIL: %s\n" "$_ao_test_name"
        printf "    expected: %s\n" "$_ao_expected"
        printf "    actual:   %s\n" "$_ao_actual"
    fi
}

# JSON validator: prefers python3 (always available on dev/CI), falls back to jq.
# Returns 0 if input is valid JSON, 1 otherwise.
JSON_VALIDATOR=""
if command -v python3 >/dev/null 2>&1; then
    JSON_VALIDATOR="python3 -c 'import sys,json; json.loads(sys.stdin.read())'"
elif command -v jq >/dev/null 2>&1; then
    JSON_VALIDATOR="jq -e . >/dev/null"
fi

assert_valid_json() {
    _avj_test_name="$1"
    _avj_payload="$2"

    if [ -z "$JSON_VALIDATOR" ]; then
        printf "  SKIP: %s (no python3/jq available)\n" "$_avj_test_name"
        return 0
    fi
    if printf '%s' "$_avj_payload" | sh -c "$JSON_VALIDATOR" >/dev/null 2>&1; then
        PASS=$((PASS + 1))
        printf "  PASS: %s\n" "$_avj_test_name"
    else
        FAIL=$((FAIL + 1))
        printf "  FAIL: %s (invalid JSON)\n" "$_avj_test_name"
        printf "    payload: %s\n" "$_avj_payload"
    fi
}

# Extract decision.message from a hook payload. Uses python3 (already required
# by assert_valid_json). Prints empty string on failure so callers can skip.
extract_message() {
    if ! command -v python3 >/dev/null 2>&1; then
        return 0
    fi
    printf '%s' "$1" | python3 -c 'import sys, json
try:
    print(json.loads(sys.stdin.read())["hookSpecificOutput"]["decision"]["message"])
except Exception:
    pass' 2>/dev/null
}

# ============================
# Test 1: AUTO_REVIEW not set (no config.env) — silent passthrough
# ============================
printf "Test 1: AUTO_REVIEW not set\n"
output="$(echo '{}' | sh "$HOOK" 2>/dev/null)" || true
assert_output "empty output (passthrough)" "" "$output"

# ============================
# Test 2: AUTO_REVIEW=false — silent passthrough
# ============================
printf "Test 2: AUTO_REVIEW=false\n"
mkdir -p .codex-review
printf 'AUTO_REVIEW=false\n' > .codex-review/config.env
output="$(echo '{}' | sh "$HOOK" 2>/dev/null)" || true
assert_output "empty output (passthrough)" "" "$output"

# ============================
# Test 3: AUTO_REVIEW=true, no verdict, no current_session — deny (cold start, claim)
# ============================
printf "Test 3: AUTO_REVIEW=true, no verdict, no session file (cold start)\n"
printf 'AUTO_REVIEW=true\n' > .codex-review/config.env
mkdir -p ".codex-review/$BRANCH_SLUG"
rm -f ".codex-review/$BRANCH_SLUG/verdict.txt"
rm -f ".codex-review/$BRANCH_SLUG/current_session.txt"
output="$(hook_stdin "$SID_A" | sh "$HOOK" 2>/dev/null)" || true
assert_valid_json "cold-start payload is valid JSON" "$output"
case "$output" in
    *'"behavior":"deny"'*) assert_output "deny decision" "yes" "yes" ;;
    *) assert_output "deny decision" "contains behavior:deny" "$output" ;;
esac
# Cold-start message must mention "not claimed" and skill instructions
case "$output" in
    *"not claimed"*) assert_output "cold-start 'not claimed'" "yes" "yes" ;;
    *) assert_output "cold-start 'not claimed'" "contains 'not claimed'" "$output" ;;
esac
case "$output" in
    *"Load skill 'codex-review'"*) assert_output "cold-start mentions skill" "yes" "yes" ;;
    *) assert_output "cold-start mentions skill" "contains skill load" "$output" ;;
esac
# Session file must be claimed after the call
if [ -f ".codex-review/$BRANCH_SLUG/current_session.txt" ]; then
    claimed="$(tr -d '[:space:]' < ".codex-review/$BRANCH_SLUG/current_session.txt")"
    assert_output "current_session.txt claimed with stdin session_id" "$SID_A" "$claimed"
else
    assert_output "current_session.txt created" "yes" "no"
fi

# ============================
# Test 4: matching session + CHANGES_REQUESTED — deny (resubmit)
# ============================
printf "Test 4: AUTO_REVIEW=true, CHANGES_REQUESTED (resubmit)\n"
printf '%s\n' "$SID_A" > ".codex-review/$BRANCH_SLUG/current_session.txt"
write_verdict ".codex-review/$BRANCH_SLUG/verdict.txt" "CHANGES_REQUESTED"
output="$(hook_stdin "$SID_A" | sh "$HOOK" 2>/dev/null)" || true
assert_valid_json "resubmit payload is valid JSON" "$output"
case "$output" in
    *'"behavior":"deny"'*) assert_output "deny decision" "yes" "yes" ;;
    *) assert_output "deny decision" "contains behavior:deny" "$output" ;;
esac
# Resubmit message must contain the verdict value and resubmit instruction
case "$output" in
    *"CHANGES_REQUESTED"*) assert_output "verdict value in message" "yes" "yes" ;;
    *) assert_output "verdict value in message" "contains 'CHANGES_REQUESTED'" "$output" ;;
esac
case "$output" in
    *"resubmit"*) assert_output "resubmit instruction" "yes" "yes" ;;
    *) assert_output "resubmit instruction" "contains 'resubmit'" "$output" ;;
esac
# Cold-start "not claimed" must NOT appear in this branch
case "$output" in
    *"not claimed"*) assert_output "no cold-start mix-up" "absent" "present" ;;
    *) assert_output "no cold-start mix-up" "yes" "yes" ;;
esac

# ============================
# Test 5: matching session + APPROVED — allow
# ============================
printf "Test 5: AUTO_REVIEW=true, APPROVED — allow + cleanup\n"
printf '%s\n' "$SID_A" > ".codex-review/$BRANCH_SLUG/current_session.txt"
write_verdict ".codex-review/$BRANCH_SLUG/verdict.txt" "APPROVED"
output="$(hook_stdin "$SID_A" | sh "$HOOK" 2>/dev/null)" || true
assert_valid_json "allow payload is valid JSON" "$output"
case "$output" in
    *'"behavior":"allow"'*) assert_output "allow decision" "yes" "yes" ;;
    *) assert_output "allow decision" "contains behavior:allow" "$output" ;;
esac
# verdict.txt must be deleted after allow to prevent stale auto-approve
if [ -f ".codex-review/$BRANCH_SLUG/verdict.txt" ]; then
    assert_output "verdict.txt deleted after allow" "deleted" "still exists"
else
    assert_output "verdict.txt deleted after allow" "yes" "yes"
fi
# verdict.phase goes with it — a marker without its verdict says nothing, and
# the next verdict brings its own
if [ -f ".codex-review/$BRANCH_SLUG/verdict.phase" ]; then
    assert_output "verdict.phase deleted after allow" "deleted" "still exists"
else
    assert_output "verdict.phase deleted after allow" "yes" "yes"
fi
# current_session.txt must survive (so next ExitPlanMode in the same session
# does not waste a claim cycle)
if [ -f ".codex-review/$BRANCH_SLUG/current_session.txt" ]; then
    assert_output "current_session.txt preserved after allow" "yes" "yes"
else
    assert_output "current_session.txt preserved after allow" "yes" "no"
fi

# ============================
# Test 6: APPROVED with whitespace — allow
# ============================
printf "Test 6: verdict with trailing whitespace\n"
printf '%s\n' "$SID_A" > ".codex-review/$BRANCH_SLUG/current_session.txt"
printf '  APPROVED \n' > ".codex-review/$BRANCH_SLUG/verdict.txt"
printf 'plan\n' > ".codex-review/$BRANCH_SLUG/verdict.phase"
output="$(hook_stdin "$SID_A" | sh "$HOOK" 2>/dev/null)" || true
case "$output" in
    *'"behavior":"allow"'*) assert_output "allow despite whitespace" "yes" "yes" ;;
    *) assert_output "allow despite whitespace" "contains behavior:allow" "$output" ;;
esac

# ============================
# Test 7: AUTO_REVIEW with quotes in config — parsed correctly
# ============================
printf "Test 7: AUTO_REVIEW with quotes\n"
printf 'AUTO_REVIEW="true"\n' > .codex-review/config.env
printf '%s\n' "$SID_A" > ".codex-review/$BRANCH_SLUG/current_session.txt"
write_verdict ".codex-review/$BRANCH_SLUG/verdict.txt" "APPROVED"
output="$(hook_stdin "$SID_A" | sh "$HOOK" 2>/dev/null)" || true
case "$output" in
    *'"behavior":"allow"'*) assert_output "quoted value parsed" "yes" "yes" ;;
    *) assert_output "quoted value parsed" "contains behavior:allow" "$output" ;;
esac

# ============================
# Test 8: repo with commits — branch resolved correctly
# ============================
printf "Test 8: repo with commits\n"
TEST_DIR2="$TMPDIR_BASE/test-auto-approve-commits-$$"
mkdir -p "$TEST_DIR2"
cd "$TEST_DIR2"
git init -q -b feat/my-feature
git config user.email "test@test.com"
git config user.name "Test"
git commit -q --allow-empty -m "init"

BRANCH_SLUG2="feat-my-feature"
mkdir -p ".codex-review"
printf 'AUTO_REVIEW=true\n' > .codex-review/config.env
mkdir -p ".codex-review/$BRANCH_SLUG2"
printf '%s\n' "$SID_A" > ".codex-review/$BRANCH_SLUG2/current_session.txt"
write_verdict ".codex-review/$BRANCH_SLUG2/verdict.txt" "APPROVED"
output="$(hook_stdin "$SID_A" | sh "$HOOK" 2>/dev/null)" || true
case "$output" in
    *'"behavior":"allow"'*) assert_output "allow with commits + slash branch" "yes" "yes" ;;
    *) assert_output "allow with commits + slash branch" "contains behavior:allow" "$output" ;;
esac
cd "$TEST_DIR"
rm -rf "$TEST_DIR2"

# ============================
# Test 9: stale verdict protection — second call after allow must deny
# ============================
printf "Test 9: stale verdict protection\n"
printf 'AUTO_REVIEW=true\n' > .codex-review/config.env
printf '%s\n' "$SID_A" > ".codex-review/$BRANCH_SLUG/current_session.txt"
write_verdict ".codex-review/$BRANCH_SLUG/verdict.txt" "APPROVED"
# First call — allow (and delete verdict.txt)
output1="$(hook_stdin "$SID_A" | sh "$HOOK" 2>/dev/null)" || true
case "$output1" in
    *'"behavior":"allow"'*) assert_output "first call: allow" "yes" "yes" ;;
    *) assert_output "first call: allow" "contains behavior:allow" "$output1" ;;
esac
# Second call — must deny (no verdict.txt left, but session still claimed)
output2="$(hook_stdin "$SID_A" | sh "$HOOK" 2>/dev/null)" || true
case "$output2" in
    *'"behavior":"deny"'*) assert_output "second call: deny (no stale verdict)" "yes" "yes" ;;
    *) assert_output "second call: deny (no stale verdict)" "contains behavior:deny" "$output2" ;;
esac
# Should be the "no verdict" path, not "not claimed"
case "$output2" in
    *"No Codex plan verdict found"*) assert_output "second call: no-verdict path" "yes" "yes" ;;
    *) assert_output "second call: no-verdict path" "contains 'No Codex plan verdict found'" "$output2" ;;
esac

# ============================
# Test 10: parser regression — `export AUTO_REVIEW=true` form
# ============================
# Regression: the previous grep|sed parser silently missed this form,
# while common.sh (which uses `.`) honored it. Hook now sources too.
printf "Test 10: parser regression — 'export AUTO_REVIEW=true'\n"
printf 'export AUTO_REVIEW=true\n' > .codex-review/config.env
printf '%s\n' "$SID_A" > ".codex-review/$BRANCH_SLUG/current_session.txt"
write_verdict ".codex-review/$BRANCH_SLUG/verdict.txt" "APPROVED"
output="$(hook_stdin "$SID_A" | sh "$HOOK" 2>/dev/null)" || true
case "$output" in
    *'"behavior":"allow"'*) assert_output "'export' form honored" "yes" "yes" ;;
    *) assert_output "'export' form honored" "contains behavior:allow" "$output" ;;
esac

# ============================
# Test 11: parser regression — leading whitespace
# ============================
printf "Test 11: parser regression — leading whitespace\n"
printf '  AUTO_REVIEW=true\n' > .codex-review/config.env
printf '%s\n' "$SID_A" > ".codex-review/$BRANCH_SLUG/current_session.txt"
write_verdict ".codex-review/$BRANCH_SLUG/verdict.txt" "APPROVED"
output="$(hook_stdin "$SID_A" | sh "$HOOK" 2>/dev/null)" || true
case "$output" in
    *'"behavior":"allow"'*) assert_output "leading whitespace honored" "yes" "yes" ;;
    *) assert_output "leading whitespace honored" "contains behavior:allow" "$output" ;;
esac

# Restore plain config for any subsequent tests
printf 'AUTO_REVIEW=true\n' > .codex-review/config.env

# ============================
# Test 12: sanitization — verdict with JSON-breaking quotes
# ============================
# Raw `"` inside verdict.txt would break the JSON string literal in the deny
# payload if not stripped. `tr -cd '[:alpha:]_'` must remove it. Falls into
# the "unknown verdict" branch after sanitization.
printf "Test 12: sanitization — verdict with double quotes\n"
printf '%s\n' "$SID_A" > ".codex-review/$BRANCH_SLUG/current_session.txt"
printf 'CHANGES"REQUESTED' > ".codex-review/$BRANCH_SLUG/verdict.txt"
output="$(hook_stdin "$SID_A" | sh "$HOOK" 2>/dev/null)" || true
assert_valid_json "payload with quoted verdict is valid JSON" "$output"
msg="$(extract_message "$output")"
case "$msg" in
    *'"'*) assert_output "quotes stripped from message" "no quotes" "FOUND: $msg" ;;
    *CHANGESREQUESTED*) assert_output "sanitized verdict in message" "yes" "yes" ;;
    *) assert_output "sanitized verdict in message" "contains CHANGESREQUESTED" "$msg" ;;
esac

# ============================
# Test 13: sanitization — verdict with backslash
# ============================
# Raw `\` would escape the following char in a JSON string literal. Must be
# stripped before interpolation.
printf "Test 13: sanitization — verdict with backslash\n"
printf '%s\n' "$SID_A" > ".codex-review/$BRANCH_SLUG/current_session.txt"
printf 'CHANGES\\REQUESTED' > ".codex-review/$BRANCH_SLUG/verdict.txt"
output="$(hook_stdin "$SID_A" | sh "$HOOK" 2>/dev/null)" || true
assert_valid_json "payload with backslash verdict is valid JSON" "$output"
msg="$(extract_message "$output")"
# Build the literal backslash via printf so shellcheck doesn't flag the
# pattern as an ambiguous single-quote escape (SC1003 false positive on
# `*'\\'*` or `bs='\'`).
bs="$(printf '\134')"
case "$msg" in
    *"$bs"*) assert_output "backslash stripped from message" "no backslash" "FOUND: $msg" ;;
    *CHANGESREQUESTED*) assert_output "sanitized verdict in message" "yes" "yes" ;;
    *) assert_output "sanitized verdict in message" "contains CHANGESREQUESTED" "$msg" ;;
esac

# ============================
# Test 14: sanitization fallback — all-garbage verdict → "unknown"
# ============================
# verdict.txt with only non-alpha chars → sanitized to empty → fallback label.
printf "Test 14: fallback — all-garbage verdict\n"
printf '%s\n' "$SID_A" > ".codex-review/$BRANCH_SLUG/current_session.txt"
printf '!!!@@@###' > ".codex-review/$BRANCH_SLUG/verdict.txt"
output="$(hook_stdin "$SID_A" | sh "$HOOK" 2>/dev/null)" || true
assert_valid_json "payload with garbage verdict is valid JSON" "$output"
msg="$(extract_message "$output")"
case "$msg" in
    *"Unknown Codex verdict value: unknown"*) assert_output "unknown fallback in message" "yes" "yes" ;;
    *) assert_output "unknown fallback in message" "contains 'Unknown Codex verdict value: unknown'" "$msg" ;;
esac

# ============================
# Test 15: sanitization fallback — empty verdict file → "unknown"
# ============================
printf "Test 15: fallback — empty verdict file\n"
printf '%s\n' "$SID_A" > ".codex-review/$BRANCH_SLUG/current_session.txt"
: > ".codex-review/$BRANCH_SLUG/verdict.txt"
output="$(hook_stdin "$SID_A" | sh "$HOOK" 2>/dev/null)" || true
assert_valid_json "payload with empty verdict is valid JSON" "$output"
msg="$(extract_message "$output")"
case "$msg" in
    *"Unknown Codex verdict value: unknown"*) assert_output "unknown fallback in message" "yes" "yes" ;;
    *) assert_output "unknown fallback in message" "contains 'Unknown Codex verdict value: unknown'" "$msg" ;;
esac

# ============================
# Test 16: plugin.json hook commands use ${CLAUDE_PLUGIN_ROOT}, not relative paths
# ============================
printf "Test 16: plugin.json hook paths use CLAUDE_PLUGIN_ROOT\n"
# Resolve the plugin.json of the source tree this test belongs to.
# Layout: <plugin_root>/test/test-auto-approve-plan.sh
PLUGIN_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"         # .../plugins/codex-review
PLUGIN_JSON="$PLUGIN_ROOT/.claude-plugin/plugin.json"
if [ -f "$PLUGIN_JSON" ]; then
    # Extract all "command" values from hooks and check for relative paths
    bad_paths="$(grep '"command"' "$PLUGIN_JSON" | grep -v 'CLAUDE_PLUGIN_ROOT' | grep -E '\./|[^/]scripts/' || true)"
    if [ -z "$bad_paths" ]; then
        assert_output "no relative paths in hook commands" "yes" "yes"
    else
        assert_output "no relative paths in hook commands" "" "$bad_paths"
    fi
else
    assert_output "plugin.json exists" "yes" "no"
fi

# Ensure plain config for session-binding tests
printf 'AUTO_REVIEW=true\n' > .codex-review/config.env

# ============================
# Test 17: session mismatch — deny + overwrite current_session.txt
# ============================
printf "Test 17: session mismatch (different stdin session_id)\n"
printf '%s\n' "$SID_A" > ".codex-review/$BRANCH_SLUG/current_session.txt"
write_verdict ".codex-review/$BRANCH_SLUG/verdict.txt" "APPROVED"
output="$(hook_stdin "$SID_B" | sh "$HOOK" 2>/dev/null)" || true
assert_valid_json "mismatch payload is valid JSON" "$output"
case "$output" in
    *'"behavior":"deny"'*) assert_output "mismatch: deny" "yes" "yes" ;;
    *) assert_output "mismatch: deny" "contains behavior:deny" "$output" ;;
esac
case "$output" in
    *"Claude session changed"*) assert_output "mismatch message" "yes" "yes" ;;
    *) assert_output "mismatch message" "contains 'Claude session changed'" "$output" ;;
esac
# current_session.txt must be overwritten with the new (stdin) session_id
claimed="$(tr -d '[:space:]' < ".codex-review/$BRANCH_SLUG/current_session.txt")"
assert_output "current_session.txt overwritten with stdin sid" "$SID_B" "$claimed"
# Stale verdict from previous session must be purged
if [ -f ".codex-review/$BRANCH_SLUG/verdict.txt" ]; then
    assert_output "stale verdict purged on mismatch" "deleted" "still exists"
else
    assert_output "stale verdict purged on mismatch" "yes" "yes"
fi

# ============================
# Test 18: unknown verdict value (alpha, no sanitization) — deny
# ============================
# Complements Tests 14/15 which exercise the unknown branch via sanitization.
# This one feeds a clean alpha value that survives `tr -cd '[:alpha:]_'` intact
# but is neither APPROVED nor CHANGES_REQUESTED.
printf "Test 18: unknown verdict value\n"
printf '%s\n' "$SID_A" > ".codex-review/$BRANCH_SLUG/current_session.txt"
write_verdict ".codex-review/$BRANCH_SLUG/verdict.txt" "MAYBE"
output="$(hook_stdin "$SID_A" | sh "$HOOK" 2>/dev/null)" || true
case "$output" in
    *'"behavior":"deny"'*) assert_output "unknown verdict: deny" "yes" "yes" ;;
    *) assert_output "unknown verdict: deny" "contains behavior:deny" "$output" ;;
esac
case "$output" in
    *"Unknown Codex verdict"*) assert_output "unknown verdict message" "yes" "yes" ;;
    *) assert_output "unknown verdict message" "contains 'Unknown Codex verdict'" "$output" ;;
esac

# ============================
# Test 19: invalid stdin (missing session_id) — deny
# ============================
printf "Test 19: invalid stdin (no session_id)\n"
printf '%s\n' "$SID_A" > ".codex-review/$BRANCH_SLUG/current_session.txt"
write_verdict ".codex-review/$BRANCH_SLUG/verdict.txt" "APPROVED"
output="$(printf '{}\n' | sh "$HOOK" 2>/dev/null)" || true
case "$output" in
    *'"behavior":"deny"'*) assert_output "invalid stdin: deny" "yes" "yes" ;;
    *) assert_output "invalid stdin: deny" "contains behavior:deny" "$output" ;;
esac
case "$output" in
    *"Invalid hook stdin"*) assert_output "invalid stdin message" "yes" "yes" ;;
    *) assert_output "invalid stdin message" "contains 'Invalid hook stdin'" "$output" ;;
esac
# verdict.txt MUST NOT be touched when stdin is invalid (fail-closed, no side effect)
if [ -f ".codex-review/$BRANCH_SLUG/verdict.txt" ]; then
    assert_output "verdict preserved on invalid stdin" "yes" "yes"
else
    assert_output "verdict preserved on invalid stdin" "yes" "no"
fi

# ============================
# Test 20: cold-start claim does NOT leak previous verdict
# ============================
printf "Test 20: cold start purges orphan verdict\n"
rm -f ".codex-review/$BRANCH_SLUG/current_session.txt"
write_verdict ".codex-review/$BRANCH_SLUG/verdict.txt" "APPROVED"
output="$(hook_stdin "$SID_A" | sh "$HOOK" 2>/dev/null)" || true
case "$output" in
    *'"behavior":"deny"'*) assert_output "cold-start with orphan verdict: deny" "yes" "yes" ;;
    *) assert_output "cold-start with orphan verdict: deny" "contains behavior:deny" "$output" ;;
esac
case "$output" in
    *"not claimed"*) assert_output "cold-start message (orphan)" "yes" "yes" ;;
    *) assert_output "cold-start message (orphan)" "contains 'not claimed'" "$output" ;;
esac
# Orphan verdict must be purged (cannot trust any verdict without a session claim)
if [ -f ".codex-review/$BRANCH_SLUG/verdict.txt" ]; then
    assert_output "orphan verdict purged on cold start" "deleted" "still exists"
else
    assert_output "orphan verdict purged on cold start" "yes" "yes"
fi

# ============================
# Test 21: second call in same session after successful allow yields deny, not allow
# ============================
# Redundant safety check: covers the end-to-end stale-verdict replay scenario
# from a different angle — verifies that an attacker who saves the "allowed"
# response cannot replay it (because verdict.txt is consumed).
printf "Test 21: allow is one-shot (no replay)\n"
printf '%s\n' "$SID_A" > ".codex-review/$BRANCH_SLUG/current_session.txt"
write_verdict ".codex-review/$BRANCH_SLUG/verdict.txt" "APPROVED"
first="$(hook_stdin "$SID_A" | sh "$HOOK" 2>/dev/null)" || true
second="$(hook_stdin "$SID_A" | sh "$HOOK" 2>/dev/null)" || true
case "$first" in
    *'"behavior":"allow"'*) assert_output "replay test: first allow" "yes" "yes" ;;
    *) assert_output "replay test: first allow" "contains behavior:allow" "$first" ;;
esac
case "$second" in
    *'"behavior":"deny"'*) assert_output "replay test: second deny" "yes" "yes" ;;
    *) assert_output "replay test: second deny" "contains behavior:deny" "$second" ;;
esac

# ============================
# Summary
# ============================
printf "\n=== Results: %d passed, %d failed ===\n" "$PASS" "$FAIL"
if [ "$FAIL" -gt 0 ]; then
    exit 1
fi
