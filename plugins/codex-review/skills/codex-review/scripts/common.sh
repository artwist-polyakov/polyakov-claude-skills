#!/bin/bash
# Common functions for codex-review plugin

# shellcheck disable=SC2034
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# --- Anti-recursion guard (deterministic, primary defense) ---
guard_recursion() {
    if [[ "${CODEX_REVIEWER:-}" == "1" ]]; then
        echo "ERROR: Recursion detected (CODEX_REVIEWER=1). Aborting." >&2
        exit 1
    fi
}

# --- Project root via git (current worktree or main repo) ---
get_project_root() {
    git rev-parse --show-toplevel 2>/dev/null || {
        echo "ERROR: Not inside a git repository." >&2
        exit 1
    }
}

# --- Main repo root (resolves through worktrees to the original repo) ---
# In a worktree, --show-toplevel returns the worktree root, but .codex-review/
# only exists in the main repo (it's excluded from git). This function always
# returns the main repo root so state files are found regardless of context.
get_main_repo_root() {
    local git_common_dir
    git_common_dir="$(git rev-parse --git-common-dir 2>/dev/null)" || {
        echo "ERROR: Not inside a git repository." >&2
        exit 1
    }
    # --git-common-dir returns the .git dir of the main repo:
    #   - in main repo: ".git" (relative)
    #   - in worktree:  "/abs/path/to/main/.git" (absolute)
    # Parent of .git dir is the repo root in both cases.
    (cd "$git_common_dir/.." && pwd)
}

# --- Current branch name, sanitized for use as directory name ---
get_branch_slug() {
    local branch
    branch="$(git rev-parse --abbrev-ref HEAD 2>/dev/null)" || branch="detached"
    # Replace slashes with dashes: feat/auth/jwt → feat-auth-jwt
    echo "$branch" | tr '/' '-'
}

# --- Root .codex-review/ directory (shared config, per-branch subdirs) ---
get_review_root() {
    local root
    root="$(get_main_repo_root)"
    local review_root="$root/.codex-review"
    mkdir -p "$review_root"
    touch "$review_root/.gitkeep"
    echo "$review_root"
}

# --- State directory (per-branch isolation inside .codex-review/) ---
get_state_dir() {
    local review_root
    review_root="$(get_review_root)"
    local branch
    branch="$(get_branch_slug)"
    local state_dir="$review_root/$branch"
    mkdir -p "$state_dir/notes"
    touch "$state_dir/notes/.gitkeep"
    echo "$state_dir"
}

# --- Load config (shared config.env → env vars → defaults) ---
load_config() {
    local review_root
    review_root="$(get_review_root)"
    local config_file="$review_root/config.env"

    if [[ -f "$config_file" ]]; then
        # shellcheck disable=SC1090
        source "$config_file"
    fi

    CODEX_MODEL="${CODEX_MODEL:-gpt-5.2}"
    CODEX_REASONING_EFFORT="${CODEX_REASONING_EFFORT:-high}"
    CODEX_MAX_ITERATIONS="${CODEX_MAX_ITERATIONS:-5}"
    CODEX_YOLO="${CODEX_YOLO:-true}"
    CODEX_REVIEWER_PROMPT="${CODEX_REVIEWER_PROMPT:-}"
    CODEX_PLAN_GUIDE="${CODEX_PLAN_GUIDE:-}"
    CODEX_CODE_GUIDE="${CODEX_CODE_GUIDE:-}"
}

# --- Read a field from state.json (no jq dependency) ---
read_state_field() {
    local field="$1"
    local state_dir
    state_dir="$(get_state_dir)"
    local state_file="$state_dir/state.json"

    if [[ ! -f "$state_file" ]]; then
        echo ""
        return
    fi

    grep -o "\"$field\"[[:space:]]*:[[:space:]]*\"[^\"]*\"" "$state_file" \
        | head -1 \
        | sed 's/.*:[[:space:]]*"//' \
        | tr -d '"'
}

# --- Read numeric field from state.json ---
read_state_number() {
    local field="$1"
    local state_dir
    state_dir="$(get_state_dir)"
    local state_file="$state_dir/state.json"

    if [[ ! -f "$state_file" ]]; then
        echo "0"
        return
    fi

    local val
    val=$(grep -o "\"$field\"[[:space:]]*:[[:space:]]*[0-9]*" "$state_file" \
        | head -1 \
        | sed 's/.*:[[:space:]]*//')
    echo "${val:-0}"
}

# --- Effective session_id: config.env → state.json ---
get_effective_session_id() {
    local sid="${CODEX_SESSION_ID:-}"
    if [[ -z "$sid" ]]; then
        sid="$(read_state_field "session_id")"
    fi
    echo "$sid"
}

# --- Write state.json ---
write_state() {
    local json="$1"
    local state_dir
    state_dir="$(get_state_dir)"
    echo "$json" > "$state_dir/state.json"
}

# --- Write STATUS.md from current state.json ---
write_status() {
    local state_dir
    state_dir="$(get_state_dir)"
    local status_file="$state_dir/STATUS.md"

    local task phase iteration max_iter review_status
    task="$(read_state_field "task_description")"
    phase="$(read_state_field "phase")"
    iteration="$(read_state_number "iteration")"
    max_iter="$(read_state_number "max_iterations")"
    review_status="$(read_state_field "last_review_status")"

    local branch
    branch="$(get_branch_slug)"

    {
        echo "# Active Codex Review"
        echo "- Task: ${task:-not set}"
        echo "- Branch: ${branch}"
        echo "- Phase: ${phase:-initialized}"
        echo "- Iteration: ${iteration}/${max_iter}"
        echo "- Last status: ${review_status:-pending}"
        echo "- Journal: \`.codex-review/${branch}/notes/\`"
    } > "$status_file"
}

# --- Remove STATUS.md (review complete or full reset) ---
remove_status() {
    local state_dir
    state_dir="$(get_state_dir)"
    rm -f "$state_dir/STATUS.md"
}

# --- Archive previous session artifacts ---
archive_previous_session() {
    local state_dir
    state_dir="$(get_state_dir)"
    local review_root
    review_root="$(get_review_root)"
    local has_artifacts=false

    # Check if there's anything to archive
    for f in "$state_dir"/state.json "$state_dir"/verdict.txt "$state_dir"/last_response.txt "$state_dir"/STATUS.md; do
        if [[ -f "$f" ]]; then has_artifacts=true; break; fi
    done
    if ls "$state_dir"/notes/*.md &>/dev/null; then has_artifacts=true; fi
    if ls "$state_dir"/codex-*.log &>/dev/null; then has_artifacts=true; fi

    if [[ "$has_artifacts" == "false" ]]; then
        return
    fi

    local timestamp
    timestamp="$(date -u +"%Y%m%dT%H%M%SZ")"
    local archive_dir="$review_root/archive/${timestamp}"
    mkdir -p "$archive_dir/notes"

    # Generate summary.json before moving artifacts (non-critical, must not block archiving)
    generate_archive_summary "$state_dir" "$archive_dir" "$timestamp" || \
        echo "WARNING: Failed to generate summary.json for archive." >&2

    # Move artifacts
    for f in state.json verdict.txt last_response.txt STATUS.md; do
        [[ -f "$state_dir/$f" ]] && mv "$state_dir/$f" "$archive_dir/"
    done
    mv "$state_dir"/codex-*.log "$archive_dir/" 2>/dev/null || true
    mv "$state_dir"/notes/*.md "$archive_dir/notes/" 2>/dev/null || true

    echo "Previous session archived to: $archive_dir" >&2
}

# --- Generate summary.json for archive ---
generate_archive_summary() {
    local state_dir="$1"
    local archive_dir="$2"
    local archived_at="$3"

    local task_desc="" session_id="" final_verdict="" last_status=""
    local plan_iters=0 code_iters=0

    # Read from state.json (still in state_dir at this point)
    if [[ -f "$state_dir/state.json" ]]; then
        task_desc="$(grep -o '"task_description"[[:space:]]*:[[:space:]]*"[^"]*"' "$state_dir/state.json" \
            | head -1 | sed 's/.*:[[:space:]]*"//;s/"$//')"
        session_id="$(grep -o '"session_id"[[:space:]]*:[[:space:]]*"[^"]*"' "$state_dir/state.json" \
            | head -1 | sed 's/.*:[[:space:]]*"//;s/"$//')"
        last_status="$(grep -o '"last_review_status"[[:space:]]*:[[:space:]]*"[^"]*"' "$state_dir/state.json" \
            | head -1 | sed 's/.*:[[:space:]]*"//;s/"$//')"
    fi

    # Read final verdict
    if [[ -f "$state_dir/verdict.txt" ]]; then
        final_verdict="$(tr -d '[:space:]' < "$state_dir/verdict.txt")"
    fi
    if [[ -z "$final_verdict" ]]; then
        final_verdict="$last_status"
    fi

    # Count review iterations from notes
    # shellcheck disable=SC2012
    plan_iters=$(ls "$state_dir"/notes/plan-review-*.md 2>/dev/null | wc -l)
    # shellcheck disable=SC2012
    code_iters=$(ls "$state_dir"/notes/code-review-*.md 2>/dev/null | wc -l)

    local total_iters=$((plan_iters + code_iters))

    # Escape task_desc for JSON (replace " with \", newlines with \n)
    task_desc="$(echo "$task_desc" | sed 's/\\/\\\\/g; s/"/\\"/g' | tr '\n' ' ')"

    local branch
    branch="$(get_branch_slug)"

    cat > "$archive_dir/summary.json" <<SUMMARY_EOF
{
  "branch": "$branch",
  "task_description": "$task_desc",
  "session_id": "$session_id",
  "plan_iterations": $plan_iters,
  "code_iterations": $code_iters,
  "total_iterations": $total_iters,
  "final_verdict": "$final_verdict",
  "archived_at": "$archived_at"
}
SUMMARY_EOF
}

# --- Generate UUID ---
generate_uuid() {
    cat /proc/sys/kernel/random/uuid 2>/dev/null || uuidgen 2>/dev/null || {
        # Last resort: pseudo-random hex
        od -x /dev/urandom 2>/dev/null | head -1 | awk '{print $2$3"-"$4"-"$5"-"$6"-"$7$8$9}'
    }
}

# --- Codex sessions directory for today ---
get_sessions_dir() {
    local codex_home="${CODEX_HOME:-$HOME/.codex}"
    local today
    today="$(date -u +%Y/%m/%d)"
    echo "$codex_home/sessions/$today"
}

# --- Find session_id by marker UUID in today's session files ---
find_session_by_marker() {
    local marker="$1"
    local sessions_dir
    sessions_dir="$(get_sessions_dir)"

    if [[ ! -d "$sessions_dir" ]]; then
        echo ""
        return
    fi

    local found_file
    found_file=$(grep -rl "$marker" "$sessions_dir"/ 2>/dev/null | head -1)

    if [[ -z "$found_file" ]]; then
        echo ""
        return
    fi

    # Primary: read session_meta.payload.id from first line via jq
    if command -v jq &>/dev/null; then
        local sid
        sid=$(head -1 "$found_file" | jq -r '.payload.id // empty' 2>/dev/null)
        if [[ -n "$sid" ]]; then
            echo "$sid"
            return
        fi
    fi

    # Fallback: extract UUID via grep from first line (no jq)
    local sid
    sid=$(head -1 "$found_file" | grep -oE '"id":"[^"]+"' | head -1 | sed 's/"id":"//;s/"//')
    echo "$sid"
}

# --- Check codex is installed and not blocked by sandbox ---
check_codex_installed() {
    if ! command -v codex &>/dev/null; then
        echo "ERROR: 'codex' CLI not found in PATH." >&2
        echo "Install: npm install -g @openai/codex" >&2
        exit 1
    fi

    # Preflight: detect sandbox crash (macOS SCDynamicStore panic)
    local ver_output
    ver_output=$(codex --version 2>&1) || true
    if echo "$ver_output" | grep -q "SCDynamicStore\|Attempted to create a NULL object\|dynamic_store\.rs"; then
        echo "ERROR: codex CLI crashes due to sandbox restrictions (macOS SCDynamicStore)." >&2
        echo "Claude Code must run codex-review scripts with dangerouslyDisableSandbox: true." >&2
        echo "This is a known issue: the sandbox blocks macOS system configuration APIs that codex needs." >&2
        exit 1
    fi
}
