#!/bin/sh
# subreddit_top.sh — top posts from a subreddit.
# Usage: subreddit_top.sh --subreddit <name> [--time hour|day|week|month|year|all]
#                        [--limit 25] [--include-comments [--comments-per-post 25]]
#                        [--no-cache]
#
# --include-comments fetches /comments/{id} for each top post and stores the
# combined artifact under cache/top_with_comments/<key>/. Use sparingly —
# each post = 1 extra API call.

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
. "$SCRIPT_DIR/common.sh"
load_config

SUB=""; TIME="day"; LIMIT="25"; NO_CACHE=""
INCLUDE_COMMENTS=""; COMMENTS_PER_POST="25"
while [ $# -gt 0 ]; do
    case "$1" in
        --subreddit|-s)        SUB="$2"; shift 2 ;;
        --time|-t)             TIME="$2"; shift 2 ;;
        --limit|-l)            LIMIT="$2"; shift 2 ;;
        --include-comments)    INCLUDE_COMMENTS=1; shift ;;
        --comments-per-post)   COMMENTS_PER_POST="$2"; shift 2 ;;
        --no-cache)            NO_CACHE=1; shift ;;
        *)                     shift ;;
    esac
done
[ -n "$SUB" ] || die "Usage: subreddit_top.sh --subreddit <name> [--time T] [--limit N] [--include-comments]"
SUB=$(printf '%s' "$SUB" | sed 's|^/*r/||')

KEY=$(cache_key "top|${SUB}|${TIME}|${LIMIT}")
OUT="$REDDIT_CACHE_DIR/listings/${KEY}.json"
mkdir -p "$REDDIT_CACHE_DIR/listings"

if [ -z "$NO_CACHE" ] && [ -s "$OUT" ]; then
    echo "(cached: $OUT)"
else
    reddit_get "/r/${SUB}/top" "$OUT" \
        --data-urlencode "t=${TIME}" \
        --data-urlencode "limit=${LIMIT}"
fi

echo "Top posts in r/${SUB} (time=${TIME}, limit=${LIMIT}):"
print_listing_summary "$OUT" "$LIMIT"
echo ""
echo "Full payload: $OUT"

# --- Include-comments: per-post fetch ----------------------------------------
if [ -n "$INCLUDE_COMMENTS" ]; then
    # Detail cache key MUST include COMMENTS_PER_POST — otherwise raising the
    # limit later would silently re-use shorter cached files.
    DETAIL_KEY=$(cache_key "top_with_comments|${SUB}|${TIME}|${LIMIT}|cpp=${COMMENTS_PER_POST}")
    DETAIL_DIR="$REDDIT_CACHE_DIR/top_with_comments/${DETAIL_KEY}"
    mkdir -p "$DETAIL_DIR"

    # Extract post ids from listing
    IDS=$(_F="$OUT" python3 - <<'PY'
import json, os
with open(os.environ["_F"]) as f:
    d = json.load(f)
ids = []
for c in d.get("data", {}).get("children", []):
    if c.get("kind") == "t3":
        cid = c.get("data", {}).get("id")
        if cid:
            ids.append(cid)
print(" ".join(ids))
PY
)

    if [ -z "$IDS" ]; then
        echo "(no post ids found in listing)"
        exit 0
    fi

    echo ""
    echo "Fetching comments for each post (limit=${COMMENTS_PER_POST}/post)..."
    _count=0
    for _id in $IDS; do
        _count=$((_count + 1))
        _file="$DETAIL_DIR/${_id}.json"
        if [ -z "$NO_CACHE" ] && [ -s "$_file" ]; then
            continue
        fi
        reddit_get "/comments/${_id}" "$_file" \
            --data-urlencode "limit=${COMMENTS_PER_POST}" \
            >/dev/null
    done
    echo "Comments cached for ${_count} posts: ${DETAIL_DIR}"
fi
