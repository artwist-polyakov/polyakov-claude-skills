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
    branch="$(git symbolic-ref --short HEAD 2>/dev/null)" \
        || branch="$(git rev-parse --abbrev-ref HEAD 2>/dev/null)" \
        || branch="detached"
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
# An optional root lets one command resolve .codex-review once for config + state.
# Entry scripts pass that argument; standalone helpers intentionally omit it.
# shellcheck disable=SC2120
get_state_dir() {
    local review_root="${1:-}"
    if [[ -z "$review_root" ]]; then
        review_root="$(get_review_root)"
    fi
    local branch
    branch="$(get_branch_slug)"
    local state_dir="$review_root/$branch"
    mkdir -p "$state_dir/notes"
    touch "$state_dir/notes/.gitkeep"
    echo "$state_dir"
}

# --- State directory for the current command ---
# Entry scripts resolve STATE_DIR once. Reuse it so helpers do not repeat git,
# mkdir and touch calls; standalone callers still get the normal fallback.
ensure_state_dir() {
    if [[ -z "${STATE_DIR:-}" ]]; then
        STATE_DIR="$(get_state_dir)"
    fi
}

# --- Load config (shared config.env → env vars → defaults) ---
# Accepts the same optional pre-resolved root as get_state_dir.
load_config() {
    local review_root="${1:-}"
    if [[ -z "$review_root" ]]; then
        review_root="$(get_review_root)"
    fi
    local config_file="$review_root/config.env"

    if [[ -f "$config_file" ]]; then
        # shellcheck disable=SC1090
        source "$config_file"
    fi

    CODEX_MODEL="${CODEX_MODEL:-}"
    CODEX_REASONING_EFFORT="${CODEX_REASONING_EFFORT:-}"
    CODEX_MAX_ITERATIONS="${CODEX_MAX_ITERATIONS:-5}"
    CODEX_YOLO="${CODEX_YOLO:-true}"
    AUTO_REVIEW="${AUTO_REVIEW:-false}"
    CODEX_REVIEWER_PROMPT="${CODEX_REVIEWER_PROMPT:-}"
    CODEX_PLAN_GUIDE="${CODEX_PLAN_GUIDE:-}"
    CODEX_CODE_GUIDE="${CODEX_CODE_GUIDE:-}"
    CODEX_SEVERITY_CALIBRATION="${CODEX_SEVERITY_CALIBRATION:-true}"
}

# --- Read a field from state.json (no jq dependency) ---
# An optional second argument supplies an in-memory snapshot. This lets callers
# that need several fields read the file once without maintaining a stale cache.
read_state_field() {
    local field="$1"
    local state_json

    if [[ $# -ge 2 ]]; then
        state_json="$2"
    else
        ensure_state_dir
        local state_file="$STATE_DIR/state.json"
        if [[ ! -f "$state_file" ]]; then
            echo ""
            return
        fi
        state_json="$(<"$state_file")"
    fi

    local pattern="\"${field}\"[[:space:]]*:[[:space:]]*\"([^\"]*)\""
    if [[ "$state_json" =~ $pattern ]]; then
        printf '%s\n' "${BASH_REMATCH[1]}"
    else
        echo ""
    fi
}

# --- Read numeric field from state.json ---
read_state_number() {
    local field="$1"
    local state_json

    if [[ $# -ge 2 ]]; then
        state_json="$2"
    else
        ensure_state_dir
        local state_file="$STATE_DIR/state.json"
        if [[ ! -f "$state_file" ]]; then
            echo "0"
            return
        fi
        state_json="$(<"$state_file")"
    fi

    local pattern="\"${field}\"[[:space:]]*:[[:space:]]*([0-9]+)"
    if [[ "$state_json" =~ $pattern ]]; then
        printf '%s\n' "${BASH_REMATCH[1]}"
    else
        echo "0"
    fi
}

# --- Effective session_id: config.env → state.json ---
get_effective_session_id() {
    local sid="${CODEX_SESSION_ID:-}"
    if [[ -z "$sid" ]]; then
        sid="$(read_state_field "session_id")"
    fi
    echo "$sid"
}

# --- A value state.json can store and give back unchanged ---
# Values in state.json are written into string literals and read back with a
# quote-delimited match that does not decode JSON escapes. So a stored value has
# to survive that round trip unchanged: one line, no double quote (readers cut
# the value there), no backslash and no control character (a JSON-escaped one
# would be read back as the escape itself, e.g. C:\tmp coming out as C:\\tmp).
# Anything else is refused rather than rewritten — a mangled value in front of
# every reader is worse than an error, and only the caller knows what it meant
# to store.
#
# `what` names the value in the message; `hint` says how to fix it. Prints the
# value exactly as it came in; on rejection prints the reason to stderr and
# returns 1 (the caller must pass that on — a bare exit inside $(...) would only
# leave the subshell).
state_string_value() {
    local value="$1"
    local what="$2"
    local hint="$3"

    if [[ "$value" == *$'\n'* || "$value" == *$'\r'* ]]; then
        echo "ERROR: $what must be a single line. $hint" >&2
        return 1
    fi
    if [[ "$value" == *[$'\001'-$'\037']* ]]; then
        echo "ERROR: $what contains control characters. $hint" >&2
        return 1
    fi
    if [[ "$value" == *'"'* ]]; then
        echo "ERROR: $what must not contain a double quote — every reader of state.json cuts the value there. $hint" >&2
        return 1
    fi
    if [[ "$value" == *'\'* ]]; then
        echo "ERROR: $what must not contain a backslash — readers of state.json do not decode JSON escapes, so it would come back doubled. $hint" >&2
        return 1
    fi

    printf '%s' "$value"
}

# --- A counter state.json can store and this script can add to ---
# The counters are written as JSON numbers, so only a canonical decimal is
# storable: a leading zero produces a file no parser accepts. The digit limit
# keeps the stored value inside the range shell arithmetic adds to — a counter
# at the edge of that range wraps to a negative on the next +1, and the limit
# check that guards a review cycle would never fire again.
#
# `what` names the value in the message; `hint` says how to fix it. Prints the
# value as it came in; on rejection prints the reason to stderr and returns 1.
STATE_COUNTER_MAX_DIGITS=9

state_counter_value() {
    local value="$1"
    local what="$2"
    local hint="$3"

    if [[ ! "$value" =~ ^(0|[1-9][0-9]*)$ ]]; then
        echo "ERROR: $what expects a whole number without a leading zero, got: $value. $hint" >&2
        return 1
    fi
    if [[ ${#value} -gt $STATE_COUNTER_MAX_DIGITS ]]; then
        echo "ERROR: $what is ${#value} digits, the limit is $STATE_COUNTER_MAX_DIGITS. $hint" >&2
        return 1
    fi

    printf '%s' "$value"
}

# --- Task label for state.json ---
# The label is a stored value, so it is checked as one first, and then against
# what a name has to be on top of that: present, one line the caller can read
# back in STATUS.md, and short enough to sit on that line.
#
# Prints the label exactly as it came in; on rejection prints the reason to stderr and
# returns 1 (the caller must pass that on — a bare exit inside $(...) would only
# leave the subshell).
TASK_LABEL_MAX=200

task_label_for_state() {
    local label="$1"
    local hint="$2"

    # Everything is checked on the value as it arrived. Nothing here rewrites
    # the label — not even trimming it, which would put a name in state.json
    # that the caller never wrote.
    state_string_value "$label" "task label" "$hint" > /dev/null || return 1

    if [[ -z "$label" ]]; then
        echo "ERROR: task label is empty. $hint" >&2
        return 1
    fi
    if [[ "$label" == " "* || "$label" == *" " ]]; then
        echo "ERROR: task label has a space at its start or end — readers of state.json keep it, so trim it yourself rather than have it stored. $hint" >&2
        return 1
    fi
    if [[ ${#label} -gt $TASK_LABEL_MAX ]]; then
        echo "ERROR: task label is ${#label} characters, the limit is $TASK_LABEL_MAX. $hint" >&2
        return 1
    fi

    # No escaping: everything that would need it has been refused above, so the
    # label reaches every reader exactly as the caller wrote it.
    printf '%s' "$label"
}

# --- Fields of state.json, in the order they are written ---
# Every writer used to carry its own copy of the file's literal, and a field
# added to one copy reached the file only through that one writer. These two
# lists and render_state_fields are the single description of the file's shape.
STATE_STRING_FIELDS="session_id phase last_review_status last_review_timestamp task_description"
STATE_NUMBER_FIELDS="iteration max_iterations reviews_completed"

# --- Is a field present in a state.json snapshot? ---
# read_state_field answers "" and read_state_number answers 0 for a field that
# is absent — the same answer they give for an empty string and for a zero. A
# caller that has to tell an absent field from a written one asks here.
state_has_field() {
    local field="$1"
    local state_json="$2"
    local pattern="\"${field}\"[[:space:]]*:"
    [[ "$state_json" =~ $pattern ]]
}

# --- Render state.json from named fields ---
# Called as: render_state_fields session_id=... phase=... — every field of the
# file, by name. Names rather than positions: three of the fields are counters
# that read alike, and a writer that swapped two of them would produce a
# plausible file. An unknown name, a name given twice, a name left out, a
# counter this file cannot hold and a string that state.json cannot give back
# unchanged are all errors — every path that writes the file passes through
# here, so a value that would break it never reaches the disk.
render_state_fields() {
    local session_id phase last_review_status last_review_timestamp task_description
    local iteration max_iterations reviews_completed
    local seen=" "
    local arg key value field

    for arg in "$@"; do
        if [[ "$arg" != *=* ]]; then
            echo "ERROR: state field must be given as name=value, got: $arg" >&2
            return 1
        fi
        key="${arg%%=*}"
        value="${arg#*=}"

        case " $STATE_STRING_FIELDS $STATE_NUMBER_FIELDS " in
            *" $key "*) ;;
            *)
                echo "ERROR: Unsupported state field: $key" >&2
                echo "Known fields: $STATE_STRING_FIELDS $STATE_NUMBER_FIELDS" >&2
                return 1
                ;;
        esac
        if [[ "$seen" == *" $key "* ]]; then
            echo "ERROR: State field given twice: $key" >&2
            return 1
        fi
        case " $STATE_NUMBER_FIELDS " in
            *" $key "*)
                state_counter_value "$value" "$key" \
                    "Pass a counter state.json can store." > /dev/null || return 1
                ;;
        esac
        case " $STATE_STRING_FIELDS " in
            *" $key "*)
                state_string_value "$value" "$key" \
                    "Pass a value state.json can store as written." > /dev/null || return 1
                ;;
        esac

        printf -v "$key" '%s' "$value"
        seen="$seen$key "
    done

    for field in $STATE_STRING_FIELDS $STATE_NUMBER_FIELDS; do
        if [[ "$seen" != *" $field "* ]]; then
            echo "ERROR: State field not given: $field" >&2
            return 1
        fi
    done

    cat <<STATE
{
  "session_id": "$session_id",
  "phase": "$phase",
  "iteration": $iteration,
  "max_iterations": $max_iterations,
  "last_review_status": "$last_review_status",
  "last_review_timestamp": "$last_review_timestamp",
  "reviews_completed": $reviews_completed,
  "task_description": "$task_description"
}
STATE
}

# --- Write state.json from named fields ---
write_state_fields() {
    local json
    json="$(render_state_fields "$@")" || return 1
    write_state "$json"
}

# --- Write state.json ---
# The file is replaced, never truncated in place: a write that dies part way
# through — a full disk above all — would otherwise leave the session with an
# empty or half-written state.json and nothing to fall back on. The temporary
# file sits in the same directory, so the rename that publishes it is atomic.
write_state() {
    local json="$1"
    ensure_state_dir
    local state_file="$STATE_DIR/state.json"
    local tmp_file="$STATE_DIR/.state.json.$$"

    # A write killed by a signal leaves its temporary file behind — the cleanup
    # below never runs for it. The name carries the pid that made it, so an
    # orphan can be told from the file of a run that is still going; a pid that
    # answers is left alone, which also covers a pid reused by something else.
    local orphan orphan_pid
    for orphan in "$STATE_DIR"/.state.json.*; do
        [[ -e "$orphan" ]] || continue
        orphan_pid="${orphan##*.}"
        [[ "$orphan_pid" =~ ^[0-9]+$ ]] || continue
        kill -0 "$orphan_pid" 2>/dev/null && continue
        rm -f "$orphan"
    done

    if ! printf '%s\n' "$json" > "$tmp_file"; then
        rm -f "$tmp_file"
        echo "ERROR: Failed to write $tmp_file." >&2
        return 1
    fi
    if ! mv "$tmp_file" "$state_file"; then
        rm -f "$tmp_file"
        echo "ERROR: Failed to replace $state_file." >&2
        return 1
    fi
}

# --- Write STATUS.md from current state.json ---
write_status() {
    ensure_state_dir
    local state_dir="$STATE_DIR"
    local state_file="$state_dir/state.json"
    local status_file="$state_dir/STATUS.md"
    local state_json=""

    if [[ -f "$state_file" ]]; then
        state_json="$(<"$state_file")"
    fi

    local task phase iteration max_iter review_status
    task="$(read_state_field "task_description" "$state_json")"
    phase="$(read_state_field "phase" "$state_json")"
    iteration="$(read_state_number "iteration" "$state_json")"
    max_iter="$(read_state_number "max_iterations" "$state_json")"
    review_status="$(read_state_field "last_review_status" "$state_json")"

    local branch="${state_dir##*/}"

    {
        echo "# Active Codex Review"
        echo "- Task: ${task:-not set}"
        if [[ -f "$state_dir/codex-init.request.md" ]]; then
            echo "- Task text: \`.codex-review/${branch}/codex-init.request.md\`"
        fi
        echo "- Branch: ${branch}"
        echo "- Phase: ${phase:-initialized}"
        echo "- Iteration: ${iteration}/${max_iter}"
        echo "- Last status: ${review_status:-pending}"
        echo "- Journal: \`.codex-review/${branch}/notes/\`"
    } > "$status_file"
}

# --- Remove STATUS.md (review complete or full reset) ---
remove_status() {
    ensure_state_dir
    rm -f "$STATE_DIR/STATUS.md"
}

# --- Parse verdict file ---
# Reads single-word verdict file and prints normalized verdict string.
# Output: "APPROVED", "CHANGES_REQUESTED", or empty for missing/unknown.
parse_verdict_file() {
    local file="$1"
    [[ -f "$file" ]] || return 0
    local raw
    raw="$(tr -d '[:space:]' < "$file")"
    case "$raw" in
        APPROVED|CHANGES_REQUESTED) echo "$raw" ;;
        *) : ;;
    esac
}

# --- Archive previous session artifacts ---
archive_previous_session() {
    ensure_state_dir
    local state_dir="$STATE_DIR"
    local review_root="${state_dir%/*}"
    local has_artifacts=false

    # Check if there's anything to archive
    for f in "$state_dir"/state.json "$state_dir"/verdict.txt "$state_dir"/last_response.txt "$state_dir"/STATUS.md; do
        if [[ -f "$f" ]]; then has_artifacts=true; break; fi
    done
    if ls "$state_dir"/notes/*.md &>/dev/null; then has_artifacts=true; fi
    if ls "$state_dir"/codex-*.log &>/dev/null; then has_artifacts=true; fi
    if ls "$state_dir"/codex-*.request.md &>/dev/null; then has_artifacts=true; fi
    if ls "$state_dir"/codex-*.prompt.md &>/dev/null; then has_artifacts=true; fi
    if ls "$state_dir"/codex-*.reply.md &>/dev/null; then has_artifacts=true; fi

    if [[ "$has_artifacts" == "false" ]]; then
        return
    fi

    local timestamp
    timestamp="$(date -u +"%Y%m%dT%H%M%SZ")"
    # Two sessions archived inside the same second would otherwise share a
    # directory and mix their artefacts. `mkdir` without -p fails when the
    # directory is already there, and that failure is the claim: whoever
    # created it owns that name, and the next one moves on to a suffix.
    # Only an existing directory sends the loop on; every other failure --
    # a read-only or full filesystem, a denied permission -- ends the archiver.
    if ! mkdir -p "$review_root/archive"; then
        echo "ERROR: Failed to create $review_root/archive. Nothing was archived." >&2
        return 1
    fi
    local archive_base="$review_root/archive/${timestamp}"
    local archive_dir="$archive_base"
    local suffix=2
    while ! mkdir "$archive_dir" 2>/dev/null; do
        if [[ ! -d "$archive_dir" ]]; then
            echo "ERROR: Failed to create archive directory $archive_dir. Nothing was archived." >&2
            return 1
        fi
        archive_dir="${archive_base}-${suffix}"
        suffix=$((suffix + 1))
    done
    if ! mkdir -p "$archive_dir/notes"; then
        echo "ERROR: Failed to create $archive_dir/notes. Nothing was archived." >&2
        return 1
    fi

    # Generate summary.json before moving artifacts (non-critical, must not block archiving)
    generate_archive_summary "$state_dir" "$archive_dir" "$timestamp" || \
        echo "WARNING: Failed to generate summary.json for archive." >&2

    # Move artifacts
    for f in state.json verdict.txt last_response.txt STATUS.md; do
        [[ -f "$state_dir/$f" ]] && mv "$state_dir/$f" "$archive_dir/"
    done
    mv "$state_dir"/codex-*.log "$archive_dir/" 2>/dev/null || true
    # Requests travel with the logs of the attempts that sent them: left behind,
    # they would be overwritten once a new session reuses the attempt numbers.
    mv "$state_dir"/codex-*.request.md "$archive_dir/" 2>/dev/null || true
    # The prompts travel with them: codex-init.prompt.md carries a fixed name, so
    # a new session would overwrite it where it stands.
    mv "$state_dir"/codex-*.prompt.md "$archive_dir/" 2>/dev/null || true
    # So do the replies of rounds that came back without a verdict — they are
    # named after the attempt, and a new session reuses those numbers.
    mv "$state_dir"/codex-*.reply.md "$archive_dir/" 2>/dev/null || true
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
        local state_json
        state_json="$(<"$state_dir/state.json")"
        task_desc="$(read_state_field "task_description" "$state_json")"
        session_id="$(read_state_field "session_id" "$state_json")"
        last_status="$(read_state_field "last_review_status" "$state_json")"
    fi

    # Read final verdict via format-agnostic helper
    final_verdict="$(parse_verdict_file "$state_dir/verdict.txt")"
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

    local branch="${state_dir##*/}"

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
