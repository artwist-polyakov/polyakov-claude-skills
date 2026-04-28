#!/bin/sh
# Tests for cache_key() determinism and uniqueness.
set -e

TEST_DIR="$(cd "$(dirname "$0")" && pwd)"
SCRIPTS_DIR="$(cd "$TEST_DIR/.." && pwd)"

REDDIT_SCRIPT_DIR="$SCRIPTS_DIR"
REDDIT_SKILL_DIR="$(cd "$SCRIPTS_DIR/.." && pwd)"
REDDIT_CONFIG_DIR="$REDDIT_SKILL_DIR/config"
REDDIT_CACHE_DIR="${TMPDIR:-/tmp}/reddit-test-cache.$$"
mkdir -p "$REDDIT_CACHE_DIR"
trap 'rm -rf "$REDDIT_CACHE_DIR"' EXIT

# shellcheck disable=SC1091
. "$SCRIPTS_DIR/common.sh"

assert_eq() {
    _got="$1"; _want="$2"; _msg="$3"
    [ "$_got" = "$_want" ] || { echo "FAIL: $_msg (got='$_got' want='$_want')" >&2; exit 1; }
}

# 1. Determinism: same input → same output
k1=$(cache_key "search|python|new|all|25")
k2=$(cache_key "search|python|new|all|25")
assert_eq "$k1" "$k2" "same input → same key"

# 2. Different inputs → different keys
k3=$(cache_key "search|python|new|all|26")
if [ "$k1" = "$k3" ]; then
    echo "FAIL: different inputs produced same key" >&2
    exit 1
fi

# 3. Format: 8 hex chars
case "$k1" in
    [0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f]) : ;;
    *)
        echo "FAIL: cache_key not 8-hex format: '$k1'" >&2
        exit 1
        ;;
esac

# 4. Empty input still produces a key
k4=$(cache_key "")
[ -n "$k4" ] || { echo "FAIL: empty input → empty key" >&2; exit 1; }

echo "test_cache_key: all assertions passed"
