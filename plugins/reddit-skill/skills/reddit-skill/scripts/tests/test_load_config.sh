#!/bin/sh
# Tests load_config() auth-mode selection logic without network.
set -e

TEST_DIR="$(cd "$(dirname "$0")" && pwd)"
SCRIPTS_DIR="$(cd "$TEST_DIR/.." && pwd)"

TMP_BASE="${TMPDIR:-/tmp}/reddit-test-config.$$"
mkdir -p "$TMP_BASE/config" "$TMP_BASE/cache"
trap 'rm -rf "$TMP_BASE"' EXIT

# Note: .env values must be valid sh assignments. Quote anything with spaces/parens.

# --- Case 1: app-only mode (no username/password) ---
cat > "$TMP_BASE/config/.env" <<'EOF'
REDDIT_CLIENT_ID=test_id
REDDIT_CLIENT_SECRET=test_secret
REDDIT_USER_AGENT="test:reddit-skill:0.0.1 (by /u/test)"
EOF

REDDIT_SCRIPT_DIR="$SCRIPTS_DIR" \
REDDIT_SKILL_DIR="$TMP_BASE" \
REDDIT_CONFIG_DIR="$TMP_BASE/config" \
REDDIT_CACHE_DIR="$TMP_BASE/cache" \
sh -c '
    unset REDDIT_USERNAME REDDIT_PASSWORD
    . "'"$SCRIPTS_DIR"'/common.sh"
    load_config
    [ "$REDDIT_AUTH_MODE" = "app-only" ] || { echo "FAIL: case1 mode=$REDDIT_AUTH_MODE" >&2; exit 1; }
    [ "$REDDIT_CLIENT_ID" = "test_id" ] || { echo "FAIL: case1 client_id=$REDDIT_CLIENT_ID" >&2; exit 1; }
'

# --- Case 2: user mode (username + password set) ---
cat > "$TMP_BASE/config/.env" <<'EOF'
REDDIT_CLIENT_ID=test_id
REDDIT_CLIENT_SECRET=test_secret
REDDIT_USER_AGENT="test:reddit-skill:0.0.1 (by /u/test)"
REDDIT_USERNAME=alice
REDDIT_PASSWORD=hunter2
EOF

REDDIT_SCRIPT_DIR="$SCRIPTS_DIR" \
REDDIT_SKILL_DIR="$TMP_BASE" \
REDDIT_CONFIG_DIR="$TMP_BASE/config" \
REDDIT_CACHE_DIR="$TMP_BASE/cache" \
sh -c '
    . "'"$SCRIPTS_DIR"'/common.sh"
    load_config
    [ "$REDDIT_AUTH_MODE" = "user" ] || { echo "FAIL: case2 mode=$REDDIT_AUTH_MODE" >&2; exit 1; }
'

# --- Case 3: missing CLIENT_ID → die (subshell exits non-zero) ---
cat > "$TMP_BASE/config/.env" <<'EOF'
REDDIT_CLIENT_SECRET=test_secret
REDDIT_USER_AGENT="test:reddit-skill:0.0.1 (by /u/test)"
EOF

if REDDIT_SCRIPT_DIR="$SCRIPTS_DIR" \
   REDDIT_SKILL_DIR="$TMP_BASE" \
   REDDIT_CONFIG_DIR="$TMP_BASE/config" \
   REDDIT_CACHE_DIR="$TMP_BASE/cache" \
   sh -c '
       unset REDDIT_CLIENT_ID REDDIT_USERNAME REDDIT_PASSWORD
       . "'"$SCRIPTS_DIR"'/common.sh"
       load_config
   ' >/dev/null 2>&1; then
    echo "FAIL: case3 should fail without CLIENT_ID" >&2
    exit 1
fi

# --- Case 4: missing USER_AGENT → die ---
cat > "$TMP_BASE/config/.env" <<'EOF'
REDDIT_CLIENT_ID=test_id
REDDIT_CLIENT_SECRET=test_secret
EOF

if REDDIT_SCRIPT_DIR="$SCRIPTS_DIR" \
   REDDIT_SKILL_DIR="$TMP_BASE" \
   REDDIT_CONFIG_DIR="$TMP_BASE/config" \
   REDDIT_CACHE_DIR="$TMP_BASE/cache" \
   sh -c '
       unset REDDIT_USER_AGENT REDDIT_USERNAME REDDIT_PASSWORD
       . "'"$SCRIPTS_DIR"'/common.sh"
       load_config
   ' >/dev/null 2>&1; then
    echo "FAIL: case4 should fail without USER_AGENT" >&2
    exit 1
fi

echo "test_load_config: all assertions passed"
