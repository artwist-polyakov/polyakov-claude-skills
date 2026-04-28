#!/bin/sh
# comment_reply.sh — reply to a post (t3_*) or a comment (t1_*).
# Usage: comment_reply.sh --parent-id <t1_xxx | t3_xxx> --content "..." --confirm
#
# Double safeguard: REDDIT_ENABLE_WRITE=1 + --confirm.

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
. "$SCRIPT_DIR/common.sh"
load_config

PARENT=""; CONTENT=""; CONFIRM=""
while [ $# -gt 0 ]; do
    case "$1" in
        --parent-id|-p) PARENT="$2"; shift 2 ;;
        --content|-c)   CONTENT="$2"; shift 2 ;;
        --confirm)      CONFIRM=1; shift ;;
        *)              shift ;;
    esac
done

[ -n "$PARENT" ]  || die "Usage: comment_reply.sh --parent-id <t1_xxx | t3_xxx> --content \"...\" --confirm"
[ -n "$CONTENT" ] || die "--content is required"

# Validate fullname format
case "$PARENT" in
    t1_*|t3_*) : ;;
    *)
        # If user passed bare base36 id, assume submission (t3_)
        _id=$(parse_submission_id "$PARENT") && PARENT="t3_${_id}" || \
            die "--parent-id must be t1_<id> (comment) or t3_<id> (post). Got: $PARENT"
        ;;
esac

ACTION="reply to ${PARENT} (${#CONTENT} chars)"
if ! require_write_enabled "$CONFIRM" "$ACTION"; then
    echo "  parent : ${PARENT}"
    echo "  body   : ${CONTENT}"
    exit 0
fi

OUT="$REDDIT_TMPDIR/reddit_comment.$$.json"
trap 'rm -f "$OUT"' EXIT

reddit_post "/api/comment" "$OUT" \
    --data-urlencode "thing_id=${PARENT}" \
    --data-urlencode "text=${CONTENT}" \
    --data-urlencode "api_type=json"

_F="$OUT" python3 - <<'PY'
import json, os
with open(os.environ["_F"]) as f:
    d = json.load(f)
j = d.get("json", {}) if isinstance(d, dict) else {}
errs = j.get("errors") or []
if errs:
    print("ERRORS:")
    for e in errs:
        print(f"  - {e}")
    raise SystemExit(2)
things = (j.get("data") or {}).get("things") or []
if not things:
    print("(no thing returned)")
    raise SystemExit
t = things[0].get("data", {})
print("Replied:")
print(f"  id        : {t.get('id','?')}")
print(f"  name      : {t.get('name','?')}")
print(f"  permalink : https://www.reddit.com{t.get('permalink','')}")
PY
