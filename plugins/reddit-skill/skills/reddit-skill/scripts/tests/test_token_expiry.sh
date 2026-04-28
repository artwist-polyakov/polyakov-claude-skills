#!/bin/sh
# Tests token cache: fresh tokens are returned, expired/wrong-mode tokens are not.
set -e

TEST_DIR="$(cd "$(dirname "$0")" && pwd)"
SCRIPTS_DIR="$(cd "$TEST_DIR/.." && pwd)"

TMP_BASE="${TMPDIR:-/tmp}/reddit-test-token.$$"
mkdir -p "$TMP_BASE/cache"
trap 'rm -rf "$TMP_BASE"' EXIT

REDDIT_SCRIPT_DIR="$SCRIPTS_DIR"
REDDIT_SKILL_DIR="$TMP_BASE"
REDDIT_CONFIG_DIR="$TMP_BASE/config"
REDDIT_CACHE_DIR="$TMP_BASE/cache"

# shellcheck disable=SC1091
. "$SCRIPTS_DIR/common.sh"

NOW=$(date +%s)

# 1. Fresh app-only token → returned
cat > "$TMP_BASE/cache/token.json" <<EOF
{"access_token":"fresh_token_xyz","expires_at":$((NOW + 3600)),"scope":"*","mode":"app-only"}
EOF
REDDIT_AUTH_MODE="app-only"
got=$(_get_cached_token)
[ "$got" = "fresh_token_xyz" ] || {
    echo "FAIL: fresh token should be returned (got='$got')" >&2
    exit 1
}

# 2. Expired token → empty
cat > "$TMP_BASE/cache/token.json" <<EOF
{"access_token":"old_token","expires_at":$((NOW - 100)),"scope":"*","mode":"app-only"}
EOF
got=$(_get_cached_token)
[ -z "$got" ] || {
    echo "FAIL: expired token should be filtered out (got='$got')" >&2
    exit 1
}

# 3. Token expiring within safety margin (<30s) → filtered
cat > "$TMP_BASE/cache/token.json" <<EOF
{"access_token":"borderline","expires_at":$((NOW + 10)),"scope":"*","mode":"app-only"}
EOF
got=$(_get_cached_token)
[ -z "$got" ] || {
    echo "FAIL: token within 30s margin should be filtered (got='$got')" >&2
    exit 1
}

# 4. Wrong mode (cache=user, asking app-only) → empty
cat > "$TMP_BASE/cache/token.json" <<EOF
{"access_token":"user_tok","expires_at":$((NOW + 3600)),"scope":"identity","mode":"user"}
EOF
REDDIT_AUTH_MODE="app-only"
got=$(_get_cached_token)
[ -z "$got" ] || {
    echo "FAIL: cross-mode cache hit (got='$got')" >&2
    exit 1
}

# 5. Same mode → returned
REDDIT_AUTH_MODE="user"
got=$(_get_cached_token)
[ "$got" = "user_tok" ] || {
    echo "FAIL: same-mode token should be returned (got='$got')" >&2
    exit 1
}

# 6. No cache file → empty
rm -f "$TMP_BASE/cache/token.json"
got=$(_get_cached_token)
[ -z "$got" ] || {
    echo "FAIL: no cache file should yield empty (got='$got')" >&2
    exit 1
}

echo "test_token_expiry: all assertions passed"
