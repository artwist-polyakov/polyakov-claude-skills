#!/bin/sh
# Tests for parse_submission_id().
set -e

TEST_DIR="$(cd "$(dirname "$0")" && pwd)"
SCRIPTS_DIR="$(cd "$TEST_DIR/.." && pwd)"

# Make common.sh sourceable in tests without forcing the full load_config flow.
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
    if [ "$_got" != "$_want" ]; then
        echo "FAIL: $_msg" >&2
        echo "  got : '$_got'" >&2
        echo "  want: '$_want'" >&2
        exit 1
    fi
}

# 1. Bare base36 id
got=$(parse_submission_id "abc123")
assert_eq "$got" "abc123" "bare id"

# 2. Mixed case bare id → lowercase
got=$(parse_submission_id "AbC123")
assert_eq "$got" "abc123" "uppercase id normalized to lowercase"

# 3. With t3_ prefix
got=$(parse_submission_id "t3_xyz789")
assert_eq "$got" "xyz789" "t3_ prefix stripped"

# 4. Long URL with slug
got=$(parse_submission_id "https://www.reddit.com/r/Python/comments/abc123/some_title/")
assert_eq "$got" "abc123" "long URL with slug"

# 5. URL without slug
got=$(parse_submission_id "https://www.reddit.com/r/Python/comments/abc123/")
assert_eq "$got" "abc123" "URL without slug"

# 6. URL without trailing slash
got=$(parse_submission_id "https://www.reddit.com/r/Python/comments/abc123")
assert_eq "$got" "abc123" "URL without trailing slash"

# 7. Short redd.it URL
got=$(parse_submission_id "https://redd.it/abc123")
assert_eq "$got" "abc123" "redd.it short URL"

# 8. Invalid input → non-zero exit
if parse_submission_id "" >/dev/null 2>&1; then
    echo "FAIL: empty input should fail" >&2
    exit 1
fi
if parse_submission_id "not_a_valid_id_!!!" >/dev/null 2>&1; then
    echo "FAIL: invalid input should fail" >&2
    exit 1
fi

echo "test_url_parse: all assertions passed"
