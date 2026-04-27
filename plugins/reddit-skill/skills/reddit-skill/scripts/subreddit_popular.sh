#!/bin/sh
# subreddit_popular.sh — list popular subreddits (Reddit's "trending" feed).
# Equivalent to PRAW's reddit.subreddits.popular(limit=N).
# Usage: subreddit_popular.sh [--limit 25] [--no-cache]

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
. "$SCRIPT_DIR/common.sh"
load_config

LIMIT="25"; NO_CACHE=""
while [ $# -gt 0 ]; do
    case "$1" in
        --limit|-l) LIMIT="$2"; shift 2 ;;
        --no-cache) NO_CACHE=1; shift ;;
        *)          shift ;;
    esac
done

KEY=$(cache_key "popular|${LIMIT}")
OUT="$REDDIT_CACHE_DIR/listings/${KEY}.json"
mkdir -p "$REDDIT_CACHE_DIR/listings"

if [ -z "$NO_CACHE" ] && [ -s "$OUT" ]; then
    echo "(cached: $OUT)"
else
    reddit_get "/subreddits/popular" "$OUT" --data-urlencode "limit=${LIMIT}"
fi

echo "Popular subreddits (top-${LIMIT}):"
print_listing_summary "$OUT" "$LIMIT"
echo ""
echo "Full payload: $OUT"
