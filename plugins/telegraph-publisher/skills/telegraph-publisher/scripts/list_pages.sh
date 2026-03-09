#!/bin/sh
# List Telegraph pages for the account
# Usage: sh list_pages.sh [--offset 0] [--limit 50]

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
. "$SCRIPT_DIR/common.sh"
load_config

# Parse arguments
OFFSET=0
LIMIT=50

while [ $# -gt 0 ]; do
    case "$1" in
        --offset) OFFSET="$2"; shift 2 ;;
        --limit)  LIMIT="$2"; shift 2 ;;
        *)        shift ;;
    esac
done

_result=$(telegraph_post "getPageList" \
    -d "access_token=$TELEGRAPH_ACCESS_TOKEN" \
    -d "offset=$OFFSET" \
    -d "limit=$LIMIT")

echo "$_result" | python3 "$SCRIPT_DIR/parse_response.py" page_list
