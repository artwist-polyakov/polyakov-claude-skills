#!/bin/sh
# user_info.sh — Reddit user profile metadata.
# Usage: user_info.sh --username <name> [--no-cache]

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
. "$SCRIPT_DIR/common.sh"
load_config

USERNAME=""
NO_CACHE=""
while [ $# -gt 0 ]; do
    case "$1" in
        --username|-u) USERNAME="$2"; shift 2 ;;
        --no-cache)    NO_CACHE=1; shift ;;
        *)             shift ;;
    esac
done

[ -n "$USERNAME" ] || die "Usage: user_info.sh --username <name>"

# Strip leading u/ or /u/
USERNAME=$(printf '%s' "$USERNAME" | sed 's|^/*u/||')

OUT="$REDDIT_CACHE_DIR/users/${USERNAME}.json"
mkdir -p "$REDDIT_CACHE_DIR/users"

if [ -z "$NO_CACHE" ] && [ -s "$OUT" ]; then
    echo "(cached: $OUT)"
else
    reddit_get "/user/${USERNAME}/about" "$OUT"
fi

_F="$OUT" python3 - <<'PY'
import json, os
with open(os.environ["_F"]) as f:
    d = json.load(f).get("data", {})
print(f"u/{d.get('name','?')} (id={d.get('id','?')})")
print(f"  Created (UTC) : {d.get('created_utc','?')}")
print(f"  Comment karma : {d.get('comment_karma',0)}")
print(f"  Link karma    : {d.get('link_karma',0)}")
print(f"  Total karma   : {d.get('total_karma', d.get('comment_karma',0)+d.get('link_karma',0))}")
print(f"  Is gold       : {d.get('is_gold', False)}")
print(f"  Is employee   : {d.get('is_employee', False)}")
print(f"  Verified      : {d.get('has_verified_email', False)}")
sub = d.get("subreddit") or {}
if isinstance(sub, dict):
    print(f"  Profile sub   : r/{sub.get('display_name','?')}  ({sub.get('subscribers',0)} subs)")
    pd = sub.get("public_description") or ""
    if pd:
        print(f"  Bio           : {pd.strip()[:200]}")
PY
echo ""
echo "Full payload: $OUT"
