#!/bin/sh
# subreddit_info.sh — subreddit /about metadata.
# Usage: subreddit_info.sh --subreddit <name> [--no-cache]

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
. "$SCRIPT_DIR/common.sh"
load_config

SUB=""; NO_CACHE=""
while [ $# -gt 0 ]; do
    case "$1" in
        --subreddit|-s) SUB="$2"; shift 2 ;;
        --no-cache)     NO_CACHE=1; shift ;;
        *)              shift ;;
    esac
done
[ -n "$SUB" ] || die "Usage: subreddit_info.sh --subreddit <name>"
SUB=$(printf '%s' "$SUB" | sed 's|^/*r/||')

OUT="$REDDIT_CACHE_DIR/subreddits/${SUB}.json"
mkdir -p "$REDDIT_CACHE_DIR/subreddits"

if [ -z "$NO_CACHE" ] && [ -s "$OUT" ]; then
    echo "(cached: $OUT)"
else
    reddit_get "/r/${SUB}/about" "$OUT"
fi

_F="$OUT" python3 - <<'PY'
import json, os
with open(os.environ["_F"]) as f:
    d = json.load(f).get("data", {})
print(f"r/{d.get('display_name','?')} — {d.get('title','')}")
print(f"  Subscribers   : {d.get('subscribers', 0)}")
print(f"  Active users  : {d.get('accounts_active') or d.get('active_user_count') or 0}")
print(f"  Created (UTC) : {d.get('created_utc','?')}")
print(f"  NSFW          : {d.get('over18', False)}")
print(f"  Type          : {d.get('subreddit_type','?')}")
print(f"  Lang          : {d.get('lang','?')}")
desc = (d.get('public_description') or '').strip()
if desc:
    print(f"  Description   : {desc[:300]}")
PY
echo ""
echo "Full payload: $OUT"
