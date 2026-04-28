#!/bin/sh
# subreddit_subscribe.sh — subscribe / unsubscribe the authenticated user to a subreddit.
# Usage: subreddit_subscribe.sh --subreddit <name> [--unsubscribe] --confirm
#
# Double safeguard: REDDIT_ENABLE_WRITE=1 + --confirm.

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
. "$SCRIPT_DIR/common.sh"
load_config

SUB=""; UNSUB=""; CONFIRM=""
while [ $# -gt 0 ]; do
    case "$1" in
        --subreddit|-s) SUB="$2"; shift 2 ;;
        --unsubscribe)  UNSUB=1; shift ;;
        --confirm)      CONFIRM=1; shift ;;
        *)              shift ;;
    esac
done

[ -n "$SUB" ] || die "Usage: subreddit_subscribe.sh --subreddit <name> [--unsubscribe] --confirm"
SUB=$(printf '%s' "$SUB" | sed 's|^/*r/||')

if [ -n "$UNSUB" ]; then
    ACTION_VERB="unsubscribe from"
    ACTION_VAL="unsub"
else
    ACTION_VERB="subscribe to"
    ACTION_VAL="sub"
fi

ACTION="${ACTION_VERB} r/${SUB}"
if ! require_write_enabled "$CONFIRM" "$ACTION"; then
    echo "  subreddit : r/${SUB}"
    echo "  action    : ${ACTION_VAL}"
    exit 0
fi

OUT="$REDDIT_TMPDIR/reddit_subscribe.$$.json"
trap 'rm -f "$OUT"' EXIT

reddit_post "/api/subscribe" "$OUT" \
    --data-urlencode "action=${ACTION_VAL}" \
    --data-urlencode "sr_name=${SUB}" \
    --data-urlencode "api_type=json"

# /api/subscribe returns {} on success
echo "OK: ${ACTION}"
