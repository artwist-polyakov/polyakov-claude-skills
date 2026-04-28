#!/bin/sh
# Tests require_write_enabled() — the double safeguard for write commands.
set -e

TEST_DIR="$(cd "$(dirname "$0")" && pwd)"
SCRIPTS_DIR="$(cd "$TEST_DIR/.." && pwd)"

TMP_BASE="${TMPDIR:-/tmp}/reddit-test-guard.$$"
mkdir -p "$TMP_BASE/cache" "$TMP_BASE/config"
trap 'rm -rf "$TMP_BASE"' EXIT

# Helper: run code in subshell, return its exit status without tripping set -e.
run_subshell() {
    set +e
    (
        REDDIT_SCRIPT_DIR="$SCRIPTS_DIR"
        REDDIT_SKILL_DIR="$TMP_BASE"
        REDDIT_CONFIG_DIR="$TMP_BASE/config"
        REDDIT_CACHE_DIR="$TMP_BASE/cache"
        # shellcheck disable=SC1091
        . "$SCRIPTS_DIR/common.sh"
        eval "$1"
    ) >/dev/null 2>&1
    _rc=$?
    set -e
    return $_rc
}

# --- Case 1: REDDIT_ENABLE_WRITE not set → die (non-zero) ---
if run_subshell '
    REDDIT_AUTH_MODE=user
    unset REDDIT_ENABLE_WRITE
    require_write_enabled "1" "test action"
'; then
    echo "FAIL: case1 should fail without REDDIT_ENABLE_WRITE=1" >&2
    exit 1
fi

# --- Case 2: app-only mode → die ---
if run_subshell '
    REDDIT_AUTH_MODE=app-only
    REDDIT_ENABLE_WRITE=1
    require_write_enabled "1" "test action"
'; then
    echo "FAIL: case2 should fail in app-only mode" >&2
    exit 1
fi

# --- Case 3: no --confirm → return non-zero (dry-run path) ---
if run_subshell '
    REDDIT_AUTH_MODE=user
    REDDIT_ENABLE_WRITE=1
    require_write_enabled "" "test action"
'; then
    echo "FAIL: case3 dry-run should signal non-zero" >&2
    exit 1
fi

# --- Case 4: all conditions met → return 0 ---
if ! run_subshell '
    REDDIT_AUTH_MODE=user
    REDDIT_ENABLE_WRITE=1
    require_write_enabled "1" "test action"
'; then
    echo "FAIL: case4 with all conditions should return 0" >&2
    exit 1
fi

# --- Case 5a: post_create.sh without REDDIT_ENABLE_WRITE → die, no curl ---
SENTINEL="$TMP_BASE/curl_was_called"
cat > "$TMP_BASE/curl" <<EOF
#!/bin/sh
echo "called" > "$SENTINEL"
exit 0
EOF
chmod +x "$TMP_BASE/curl"

cat > "$TMP_BASE/config/.env" <<'EOF'
REDDIT_CLIENT_ID=x
REDDIT_CLIENT_SECRET=y
REDDIT_USER_AGENT="test"
REDDIT_USERNAME=alice
REDDIT_PASSWORD=hunter2
EOF
# REDDIT_ENABLE_WRITE intentionally NOT set → script dies, no curl

set +e
PATH="$TMP_BASE:$PATH" \
    REDDIT_SCRIPT_DIR="$SCRIPTS_DIR" \
    REDDIT_SKILL_DIR="$TMP_BASE" \
    REDDIT_CONFIG_DIR="$TMP_BASE/config" \
    REDDIT_CACHE_DIR="$TMP_BASE/cache" \
    sh "$SCRIPTS_DIR/post_create.sh" \
        --subreddit test --title "hello" --content "world" >/dev/null 2>&1
set -e

if [ -f "$SENTINEL" ]; then
    echo "FAIL: case5a (no ENABLE_WRITE) — post_create.sh called curl!" >&2
    exit 1
fi

# --- Case 5b: post_create.sh with REDDIT_ENABLE_WRITE=1 but no --confirm
#              → dry-run, no curl ---
rm -f "$SENTINEL"

cat > "$TMP_BASE/config/.env" <<'EOF'
REDDIT_CLIENT_ID=x
REDDIT_CLIENT_SECRET=y
REDDIT_USER_AGENT="test"
REDDIT_USERNAME=alice
REDDIT_PASSWORD=hunter2
REDDIT_ENABLE_WRITE=1
EOF

set +e
PATH="$TMP_BASE:$PATH" \
    REDDIT_SCRIPT_DIR="$SCRIPTS_DIR" \
    REDDIT_SKILL_DIR="$TMP_BASE" \
    REDDIT_CONFIG_DIR="$TMP_BASE/config" \
    REDDIT_CACHE_DIR="$TMP_BASE/cache" \
    sh "$SCRIPTS_DIR/post_create.sh" \
        --subreddit test --title "hello" --content "world" >/dev/null 2>&1
_rc=$?
set -e

if [ -f "$SENTINEL" ]; then
    echo "FAIL: case5b (ENABLE_WRITE=1, no --confirm) — post_create.sh called curl!" >&2
    exit 1
fi
# Dry-run path is the success case for the script (exit 0 after printing).
if [ "$_rc" != "0" ]; then
    echo "FAIL: case5b dry-run should exit 0 (got $_rc)" >&2
    exit 1
fi

echo "test_write_guard: all assertions passed"
