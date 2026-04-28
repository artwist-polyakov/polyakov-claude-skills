#!/bin/sh
# submission.sh — fetch a submission (post) by id or URL, with comments.
# Usage: submission.sh (--id <base36> | --url <url>) [--limit-comments 25] [--no-cache]

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
. "$SCRIPT_DIR/common.sh"
load_config

ARG=""; LIMIT="25"; NO_CACHE=""
while [ $# -gt 0 ]; do
    case "$1" in
        --id)              ARG="$2"; shift 2 ;;
        --url)             ARG="$2"; shift 2 ;;
        --limit-comments)  LIMIT="$2"; shift 2 ;;
        --no-cache)        NO_CACHE=1; shift ;;
        *)                 shift ;;
    esac
done
[ -n "$ARG" ] || die "Usage: submission.sh (--id <base36> | --url <url>) [--limit-comments N]"

ID=$(parse_submission_id "$ARG") || die "Could not parse submission id from: $ARG" \
    "Expected base36 id (e.g. abc123), t3_<id>, or a Reddit URL with /comments/<id>/."

KEY=$(cache_key "submission|${ID}|${LIMIT}")
OUT="$REDDIT_CACHE_DIR/listings/${KEY}.json"
mkdir -p "$REDDIT_CACHE_DIR/listings"

if [ -z "$NO_CACHE" ] && [ -s "$OUT" ]; then
    echo "(cached: $OUT)"
else
    reddit_get "/comments/${ID}" "$OUT" --data-urlencode "limit=${LIMIT}"
fi

_F="$OUT" _N="$LIMIT" python3 - <<'PY'
import json, os
n = int(os.environ["_N"])
with open(os.environ["_F"]) as f:
    d = json.load(f)
# /comments/{id} returns [post_listing, comment_listing]
if not (isinstance(d, list) and len(d) >= 2):
    print("(unexpected payload shape)")
    raise SystemExit
post = d[0]["data"]["children"][0]["data"]
comments = d[1]["data"]["children"]
print(f"Post: {post.get('title','')}")
print(f"  r/{post.get('subreddit','?')}  by u/{post.get('author','?')}")
print(f"  Score: {post.get('score',0)} ↑  Comments: {post.get('num_comments',0)} 💬  UpvoteRatio: {post.get('upvote_ratio','?')}")
print(f"  URL: https://www.reddit.com{post.get('permalink','')}")
body = (post.get('selftext') or '').strip()
if body:
    print(f"  Body: {body[:400]}")
print()
shown = 0
print(f"Top-level comments (showing up to {n}):")
for c in comments[:n]:
    if c.get("kind") != "t1":
        continue
    cd = c["data"]
    body = (cd.get("body") or "").replace("\n", " ").strip()[:200]
    score = cd.get("score", 0)
    author = cd.get("author", "?")
    print(f"  [{score:>5} ↑] u/{author}: {body}")
    shown += 1
total = sum(1 for c in comments if c.get("kind") == "t1")
if total > shown:
    print(f"  ... ({total - shown} more in cache file)")
PY

echo ""
echo "Full payload: $OUT"
