#!/bin/sh
# Get Telegraph account info
# Usage: sh account_info.sh [--fields short_name,author_name,author_url,auth_url,page_count]

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
. "$SCRIPT_DIR/common.sh"
load_config

# Parse arguments
FIELDS="short_name,author_name,author_url,page_count"

while [ $# -gt 0 ]; do
    case "$1" in
        --fields) FIELDS="$2"; shift 2 ;;
        --with-auth-url) FIELDS="short_name,author_name,author_url,auth_url,page_count"; shift ;;
        *)        shift ;;
    esac
done

# Convert comma-separated fields to JSON array format for API
# Telegraph expects: fields=["short_name","author_name"]
_fields_json="["
_first=1
_ifs_save="$IFS"
IFS=","
for _f in $FIELDS; do
    if [ "$_first" = "1" ]; then
        _fields_json="${_fields_json}\"$_f\""
        _first=0
    else
        _fields_json="${_fields_json},\"$_f\""
    fi
done
IFS="$_ifs_save"
_fields_json="${_fields_json}]"

_result=$(telegraph_post "getAccountInfo" \
    -d "access_token=$TELEGRAPH_ACCESS_TOKEN" \
    --data-urlencode "fields=$_fields_json")

# Use parse_response.py for human-readable output
echo "$_result" | python3 "$SCRIPT_DIR/parse_response.py" account_info
