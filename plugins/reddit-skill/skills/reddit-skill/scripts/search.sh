#!/bin/sh
# search.sh — search posts site-wide or within a subreddit.
# Usage: search.sh --query "text" [--subreddit <name>] [--sort relevance|hot|new|top|comments]
#                 [--time hour|day|week|month|year|all] [--limit 25] [--no-cache]

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
. "$SCRIPT_DIR/common.sh"
load_config

QUERY=""; SUB=""; SORT="relevance"; TIME="all"; LIMIT="25"; NO_CACHE=""
while [ $# -gt 0 ]; do
    case "$1" in
        --query|-q)     QUERY="$2"; shift 2 ;;
        --subreddit|-s) SUB="$2"; shift 2 ;;
        --sort)         SORT="$2"; shift 2 ;;
        --time|-t)      TIME="$2"; shift 2 ;;
        --limit|-l)     LIMIT="$2"; shift 2 ;;
        --no-cache)     NO_CACHE=1; shift ;;
        *)              shift ;;
    esac
done
[ -n "$QUERY" ] || die "Usage: search.sh --query \"text\" [--subreddit <name>] [--sort S] [--time T] [--limit N]"
SUB=$(printf '%s' "$SUB" | sed 's|^/*r/||')

if [ -n "$SUB" ]; then
    PATH_PART="/r/${SUB}/search"
    SCOPE="r/${SUB}"
    EXTRA_ARGS='--data-urlencode restrict_sr=on'
else
    PATH_PART="/search"
    SCOPE="all of Reddit"
    EXTRA_ARGS=""
fi

KEY=$(cache_key "search|${SUB}|${QUERY}|${SORT}|${TIME}|${LIMIT}")
OUT="$REDDIT_CACHE_DIR/listings/${KEY}.json"
mkdir -p "$REDDIT_CACHE_DIR/listings"

if [ -z "$NO_CACHE" ] && [ -s "$OUT" ]; then
    echo "(cached: $OUT)"
else
    # shellcheck disable=SC2086
    reddit_get "$PATH_PART" "$OUT" \
        --data-urlencode "q=${QUERY}" \
        --data-urlencode "sort=${SORT}" \
        --data-urlencode "t=${TIME}" \
        --data-urlencode "limit=${LIMIT}" \
        $EXTRA_ARGS
fi

echo "Search results for \"${QUERY}\" in ${SCOPE} (sort=${SORT}, time=${TIME}, limit=${LIMIT}):"
print_listing_summary "$OUT" "$LIMIT"
echo ""
echo "Full payload: $OUT"
