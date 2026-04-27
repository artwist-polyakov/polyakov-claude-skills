#!/bin/sh
# auth_check.sh — verify that OAuth credentials work in app-only mode.
# Calls GET https://oauth.reddit.com/r/all/new?limit=1 with a fresh Bearer.

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
. "$SCRIPT_DIR/common.sh"
load_config

OUT="$REDDIT_TMPDIR/reddit_auth_check.$$.json"
trap 'rm -f "$OUT"' EXIT

reddit_get "/r/all/new" "$OUT" --data-urlencode "limit=1"

# Sanity-check shape
_F="$OUT" python3 - <<'PY' || die "Token call returned unexpected payload" "$(cat "$OUT" | head -c 500)"
import json, os, sys
with open(os.environ["_F"]) as f:
    d = json.load(f)
if not (isinstance(d, dict) and d.get("kind") == "Listing"):
    sys.exit(1)
PY

echo "OK: ${REDDIT_AUTH_MODE} token works"
echo "    User-Agent: $REDDIT_USER_AGENT"
echo "    Endpoint  : $REDDIT_OAUTH_BASE"
