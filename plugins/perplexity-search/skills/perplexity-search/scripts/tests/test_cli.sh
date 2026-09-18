#!/bin/sh
# CLI contract of the entry-point scripts: --help, bad flags, missing values,
# and input validation. Nothing here reaches the network.

set -e

TESTS_DIR="$(cd "$(dirname "$0")" && pwd)"
SKILL_DIR="$(cd "$TESTS_DIR/../.." && pwd)"
SCRIPTS="$SKILL_DIR/scripts"

TMP_DIR=$(mktemp -d "${TMPDIR:-/tmp}/pplx_cli_test.XXXXXX")
trap 'rm -rf "$TMP_DIR"' EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

# A key in the environment plus a config path that does not exist keeps
# load_config happy without touching the developer's real .env.
PPLX_CONFIG_FILE="$TMP_DIR/missing.env"
PPLX_CACHE_DIR="$TMP_DIR/cache"
PERPLEXITY_API_KEY="pplx-test-key"
export PPLX_CONFIG_FILE PPLX_CACHE_DIR PERPLEXITY_API_KEY

expect_fail() {
    _label="$1"
    shift
    if "$@" >/dev/null 2>&1; then
        echo "expected failure: $_label"
        exit 1
    fi
}

for s in search ask research fetch_url cache_grep find_latest; do
    sh "$SCRIPTS/$s.sh" --help >/dev/null || { echo "$s.sh --help failed"; exit 1; }
    # --help must work before any config is loaded, i.e. without a key
    ( unset PERPLEXITY_API_KEY; sh "$SCRIPTS/$s.sh" --help ) >/dev/null 2>&1 || {
        echo "$s.sh --help requires an API key"; exit 1
    }
done

# Required arguments
expect_fail "search without --query"      sh "$SCRIPTS/search.sh"
expect_fail "ask without --query"         sh "$SCRIPTS/ask.sh"
expect_fail "research without --query"    sh "$SCRIPTS/research.sh"
expect_fail "fetch_url without --url"     sh "$SCRIPTS/fetch_url.sh"
expect_fail "cache_grep without pattern"  sh "$SCRIPTS/cache_grep.sh"

# Unknown flags are refused instead of silently ignored
expect_fail "search unknown flag"  sh "$SCRIPTS/search.sh" --query q --bogus
expect_fail "ask unknown flag"     sh "$SCRIPTS/ask.sh" --query q --bogus

# A trailing flag with no value reports itself
expect_fail "search trailing flag" sh "$SCRIPTS/search.sh" --query
expect_fail "ask trailing flag"    sh "$SCRIPTS/ask.sh" --query q --preset

# Value validation happens before any HTTP call
expect_fail "bad recency"       sh "$SCRIPTS/search.sh" --query q --recency decade
expect_fail "bad context size"  sh "$SCRIPTS/search.sh" --query q --context-size huge
expect_fail "bad date"          sh "$SCRIPTS/search.sh" --query q --after 2026
expect_fail "max-results 0"     sh "$SCRIPTS/search.sh" --query q --max-results 0
expect_fail "max-results 21"    sh "$SCRIPTS/search.sh" --query q --max-results 21
expect_fail "non-numeric limit" sh "$SCRIPTS/search.sh" --query q --limit many
expect_fail "unknown profile"   sh "$SCRIPTS/search.sh" --query q --profile nosuch
expect_fail "unknown tool"      sh "$SCRIPTS/ask.sh" --query q --tools web_search,teleport
expect_fail "missing schema"    sh "$SCRIPTS/ask.sh" --query q --schema "$TMP_DIR/nope.json"
expect_fail "bad url"           sh "$SCRIPTS/fetch_url.sh" --url "ftp://example.com/x"
expect_fail "bad resume id"     sh "$SCRIPTS/research.sh" --resume 'abc;rm -rf /'
expect_fail "bad cache type"    sh "$SCRIPTS/cache_grep.sh" pattern --type nope

# cache_grep on an empty cache is a normal, quiet exit
OUT=$(sh "$SCRIPTS/cache_grep.sh" anything)
case "$OUT" in
    *"No "*) : ;;
    *) echo "cache_grep on empty cache printed: $OUT"; exit 1 ;;
esac

# find_latest with no index is likewise not an error
sh "$SCRIPTS/find_latest.sh" >/dev/null || { echo "find_latest failed on empty index"; exit 1; }

echo PASS
