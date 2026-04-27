#!/bin/sh
# me.sh — current user identity. Requires user mode (password grant).
# GET /api/v1/me

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
. "$SCRIPT_DIR/common.sh"
load_config
require_user_mode

OUT="$REDDIT_CACHE_DIR/me.json"
reddit_get "/api/v1/me" "$OUT"

_F="$OUT" python3 - <<'PY'
import json, os
with open(os.environ["_F"]) as f:
    d = json.load(f)
print(f"Username       : u/{d.get('name','?')}")
print(f"ID             : {d.get('id','?')}")
print(f"Comment karma  : {d.get('comment_karma', 0)}")
print(f"Link karma     : {d.get('link_karma', 0)}")
print(f"Total karma    : {d.get('total_karma', d.get('comment_karma',0)+d.get('link_karma',0))}")
print(f"Created (UTC)  : {d.get('created_utc', '?')}")
print(f"Verified email : {d.get('has_verified_email', False)}")
print(f"Is mod         : {d.get('is_mod', False)}")
PY
echo ""
echo "Full payload: $OUT"
