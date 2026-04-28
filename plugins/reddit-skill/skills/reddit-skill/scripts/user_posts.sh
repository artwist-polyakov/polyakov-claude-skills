#!/bin/sh
# user_posts.sh — submissions by a user.
# Usage: user_posts.sh --username <name> [--sort new|hot|top|controversial]
#                     [--time hour|day|week|month|year|all] [--limit 25] [--no-cache]

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
. "$SCRIPT_DIR/common.sh"
load_config

USERNAME=""; SORT="new"; TIME="all"; LIMIT="25"; NO_CACHE=""
while [ $# -gt 0 ]; do
    case "$1" in
        --username|-u) USERNAME="$2"; shift 2 ;;
        --sort)        SORT="$2"; shift 2 ;;
        --time|-t)     TIME="$2"; shift 2 ;;
        --limit|-l)    LIMIT="$2"; shift 2 ;;
        --no-cache)    NO_CACHE=1; shift ;;
        *)             shift ;;
    esac
done
[ -n "$USERNAME" ] || die "Usage: user_posts.sh --username <name> [--sort N] [--time T] [--limit N]"
USERNAME=$(printf '%s' "$USERNAME" | sed 's|^/*u/||')

KEY=$(cache_key "user-posts|${USERNAME}|${SORT}|${TIME}|${LIMIT}")
OUT="$REDDIT_CACHE_DIR/listings/${KEY}.json"
mkdir -p "$REDDIT_CACHE_DIR/listings"

if [ -z "$NO_CACHE" ] && [ -s "$OUT" ]; then
    echo "(cached: $OUT)"
else
    reddit_get "/user/${USERNAME}/submitted" "$OUT" \
        --data-urlencode "sort=${SORT}" \
        --data-urlencode "t=${TIME}" \
        --data-urlencode "limit=${LIMIT}"
fi

echo "Posts by u/${USERNAME} (sort=${SORT}, time=${TIME}, limit=${LIMIT}):"
print_listing_summary "$OUT" "$LIMIT"
echo ""
echo "Full payload: $OUT"
