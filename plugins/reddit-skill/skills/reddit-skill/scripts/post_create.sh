#!/bin/sh
# post_create.sh — submit a new post to a subreddit.
# Usage: post_create.sh --subreddit <name> --title "..." [--content "..." | --url "..."]
#                     [--flair-id <id>] [--nsfw] [--spoiler] --confirm
#
# Double safeguard:
#   - REDDIT_ENABLE_WRITE=1 in config/.env
#   - --confirm flag on the command line
# Without both → dry-run only (no network call).

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
. "$SCRIPT_DIR/common.sh"
load_config

SUB=""; TITLE=""; CONTENT=""; URL=""; FLAIR=""; NSFW=""; SPOILER=""; CONFIRM=""
while [ $# -gt 0 ]; do
    case "$1" in
        --subreddit|-s) SUB="$2"; shift 2 ;;
        --title)        TITLE="$2"; shift 2 ;;
        --content|-c)   CONTENT="$2"; shift 2 ;;
        --url)          URL="$2"; shift 2 ;;
        --flair-id)     FLAIR="$2"; shift 2 ;;
        --nsfw)         NSFW=1; shift ;;
        --spoiler)      SPOILER=1; shift ;;
        --confirm)      CONFIRM=1; shift ;;
        *)              shift ;;
    esac
done

[ -n "$SUB" ]   || die "Usage: post_create.sh --subreddit <name> --title \"...\" [--content \"...\" | --url \"...\"] --confirm"
[ -n "$TITLE" ] || die "--title is required"
SUB=$(printf '%s' "$SUB" | sed 's|^/*r/||')

if [ -n "$URL" ] && [ -n "$CONTENT" ]; then
    die "Pass either --url (link post) or --content (self post), not both"
fi

if [ -n "$URL" ]; then
    KIND="link"
else
    KIND="self"
fi

ACTION="post in r/${SUB} (kind=${KIND}, title=\"${TITLE}\")"
if ! require_write_enabled "$CONFIRM" "$ACTION"; then
    # Dry-run mode — print what would be sent
    echo "  subreddit : r/${SUB}"
    echo "  kind      : ${KIND}"
    echo "  title     : ${TITLE}"
    [ -n "$CONTENT" ] && echo "  text      : ${CONTENT}"
    [ -n "$URL" ]     && echo "  url       : ${URL}"
    [ -n "$FLAIR" ]   && echo "  flair_id  : ${FLAIR}"
    [ -n "$NSFW" ]    && echo "  nsfw      : true"
    [ -n "$SPOILER" ] && echo "  spoiler   : true"
    exit 0
fi

OUT="$REDDIT_TMPDIR/reddit_post_create.$$.json"
trap 'rm -f "$OUT"' EXIT

# Build form-data args
set -- --data-urlencode "sr=${SUB}" \
       --data-urlencode "title=${TITLE}" \
       --data-urlencode "kind=${KIND}" \
       --data-urlencode "api_type=json" \
       --data-urlencode "resubmit=true" \
       --data-urlencode "sendreplies=true"

if [ -n "$CONTENT" ]; then
    set -- "$@" --data-urlencode "text=${CONTENT}"
fi
if [ -n "$URL" ]; then
    set -- "$@" --data-urlencode "url=${URL}"
fi
if [ -n "$FLAIR" ]; then
    set -- "$@" --data-urlencode "flair_id=${FLAIR}"
fi
if [ -n "$NSFW" ]; then
    set -- "$@" --data-urlencode "nsfw=true"
fi
if [ -n "$SPOILER" ]; then
    set -- "$@" --data-urlencode "spoiler=true"
fi

reddit_post "/api/submit" "$OUT" "$@"

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
data = j.get("data") or {}
print("Submitted:")
print(f"  url   : {data.get('url','?')}")
print(f"  id    : {data.get('id','?')}")
print(f"  name  : {data.get('name','?')}")
PY
