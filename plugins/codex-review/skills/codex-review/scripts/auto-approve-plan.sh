#!/bin/sh
# PermissionRequest hook for ExitPlanMode.
#
# When AUTO_REVIEW=true, this hook binds Codex verdict to the current Claude
# session via .codex-review/<branch>/current_session.txt:
#
#   - session_id missing from stdin     → deny "invalid stdin"
#   - current_session.txt missing       → claim + deny (untrusted start)
#   - current_session.txt mismatches    → overwrite + deny (session changed)
#   - session matches:
#       * no verdict                     → deny "run plan review"
#       * APPROVED, verdict.phase=plan   → allow + remove verdict and marker
#       * APPROVED, any other marker     → remove both + deny "run plan review"
#       * CHANGES_REQUESTED               → deny "resubmit"
#       * unknown value                   → deny "unknown verdict"
#
# verdict.phase names the review that produced the verdict. A code review
# leaves the same APPROVED, and the round that wrote it removed STATUS.md, so
# without the marker the next task's plan passes on the previous task's
# approval.
#
# When AUTO_REVIEW!=true: exit silently (normal UI dialog).

set -e

# --- Locate git repo root ---
git_common_dir="$(git rev-parse --git-common-dir 2>/dev/null)" || exit 0
repo_root="$(cd "$git_common_dir/.." && pwd)"

# --- Read config.env ---
# Source in a subshell to match common.sh behavior exactly (handles `export`,
# leading whitespace, quoted values, etc). Side effects stay isolated.
config_file="$repo_root/.codex-review/config.env"
# shellcheck source=/dev/null
AUTO_REVIEW="$( . "$config_file" 2>/dev/null; echo "${AUTO_REVIEW:-false}" )"

# Not auto mode — exit silently (normal UI dialog)
if [ "$AUTO_REVIEW" != "true" ]; then
    exit 0
fi

# --- Find branch state dir ---
branch="$(git symbolic-ref --short HEAD 2>/dev/null)" \
    || branch="$(git rev-parse --abbrev-ref HEAD 2>/dev/null)" \
    || branch="detached"
branch_slug="$(echo "$branch" | tr '/' '-')"
state_dir="$repo_root/.codex-review/$branch_slug"
mkdir -p "$state_dir"
verdict_file="$state_dir/verdict.txt"
phase_file="$state_dir/verdict.phase"
session_file="$state_dir/current_session.txt"

# --- Read hook stdin ---
stdin_json="$(cat)"

# --- Parse session_id from stdin JSON ---
# Claude sends a UUID in "session_id". Parse without jq (may not be available
# in the hook execution environment). Match hex+dash chars only so the value
# is safe to interpolate into a JSON string literal.
stdin_session="$(printf '%s' "$stdin_json" \
    | grep -oE '"session_id"[[:space:]]*:[[:space:]]*"[0-9a-fA-F-]+"' \
    | head -n1 \
    | sed -E 's/.*"([0-9a-fA-F-]+)"$/\1/')"

# --- Helpers ---

emit_allow() {
    printf '{"hookSpecificOutput":{"hookEventName":"PermissionRequest","decision":{"behavior":"allow"}}}\n'
}

# $1: deny message (must be JSON-safe; these are all hardcoded ASCII)
emit_deny() {
    printf '{"hookSpecificOutput":{"hookEventName":"PermissionRequest","decision":{"behavior":"deny","message":"%s"}}}\n' "$1"
}

# Atomically write session_id to current_session.txt (tmp.$$ → mv on same FS).
claim_session() {
    tmp="$session_file.tmp.$$"
    printf '%s\n' "$stdin_session" > "$tmp"
    mv "$tmp" "$session_file"
}

# --- Validate stdin ---
if [ -z "$stdin_session" ]; then
    emit_deny "Invalid hook stdin: missing session_id. This is an internal error; report it to plugin maintainers."
    exit 0
fi

# --- Check current session binding ---
if [ ! -f "$session_file" ]; then
    # 5A: missing claim → untrusted, purge any orphan verdict and claim session
    rm -f "$verdict_file" "$phase_file"
    claim_session
    emit_deny "Codex plan review not claimed for this Claude session. Load skill 'codex-review' and run plan review first: init + plan --plan-file <path>."
    exit 0
fi

current_session="$(tr -d '[:space:]' < "$session_file" 2>/dev/null || echo "")"
if [ "$current_session" != "$stdin_session" ]; then
    # 5B: session mismatch → another Claude session owned this state, stale
    rm -f "$verdict_file" "$phase_file"
    claim_session
    emit_deny "Claude session changed. The previous Codex plan verdict belongs to a different session. Load skill 'codex-review' and re-run plan review for this session: init + plan --plan-file <path>."
    exit 0
fi

# --- Session matches: consult verdict.txt ---

if [ ! -f "$verdict_file" ]; then
    emit_deny "No Codex plan verdict found. Load skill 'codex-review' and run plan review before ExitPlanMode: init + plan --plan-file <path>."
    exit 0
fi

# Read single-word verdict. Strip whitespace, then restrict to [A-Za-z_]
# so the value is safe to interpolate into the JSON deny message below.
verdict="$(tr -d '[:space:]' < "$verdict_file" | tr -cd '[:alpha:]_')"

# Which review left that word. A cycle ends with an APPROVED written by the code
# phase, and STATUS.md is removed with it, so nothing on disk says a review is
# running — the next task's plan would ride through on it. Restricted to letters
# for the same reason as the verdict above.
verdict_phase=""
if [ -f "$phase_file" ]; then
    verdict_phase="$(tr -d '[:space:]' < "$phase_file" | tr -cd '[:alpha:]_')"
fi

case "$verdict" in
    APPROVED)
        if [ "$verdict_phase" != "plan" ]; then
            rm -f "$verdict_file" "$phase_file"
            if [ -n "$verdict_phase" ]; then
                emit_deny "The Codex verdict on file was left by a $verdict_phase review, not by a plan review. Load skill 'codex-review' and run plan review before ExitPlanMode: init + plan --plan-file <path>."
            else
                emit_deny "The Codex verdict on file does not record which review left it, so it cannot stand for this plan. Load skill 'codex-review' and run plan review before ExitPlanMode: init + plan --plan-file <path>."
            fi
            exit 0
        fi
        rm -f "$verdict_file" "$phase_file"
        emit_allow
        ;;
    CHANGES_REQUESTED)
        emit_deny "Codex plan verdict is CHANGES_REQUESTED. Address feedback and resubmit via 'codex-review.sh plan --plan-file <path>' — do NOT call ExitPlanMode until APPROVED."
        ;;
    *)
        [ -n "$verdict" ] || verdict="unknown"
        printf '{"hookSpecificOutput":{"hookEventName":"PermissionRequest","decision":{"behavior":"deny","message":"Unknown Codex verdict value: %s. Re-run plan review via '\''codex-review.sh plan --plan-file <path>'\''."}}}\n' "$verdict"
        ;;
esac
