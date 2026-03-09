#!/bin/sh
# Create or manage a Telegraph account
# Usage:
#   sh create_account.sh --name "Author Name" [--author-url "https://..."]
#   sh create_account.sh --revoke   # Rotate token (requires existing token in config)

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
. "$SCRIPT_DIR/common.sh"

# Parse arguments
SHORT_NAME=""
AUTHOR_NAME=""
AUTHOR_URL=""
REVOKE=""

while [ $# -gt 0 ]; do
    case "$1" in
        --name)       SHORT_NAME="$2"; AUTHOR_NAME="$2"; shift 2 ;;
        --author-url) AUTHOR_URL="$2"; shift 2 ;;
        --revoke)     REVOKE="1"; shift ;;
        *)            shift ;;
    esac
done

if [ -n "$REVOKE" ]; then
    load_config
    echo "Revoking access token and generating new one..." >&2

    _result=$(telegraph_post "revokeAccessToken" \
        -d "access_token=$TELEGRAPH_ACCESS_TOKEN")

    _tmpfile="${TPH_TMPDIR}/telegraph_revoke_$$.json"
    printf '%s' "$_result" > "$_tmpfile"

    _new_token=$(json_extract_field "$_tmpfile" "access_token")
    _auth_url=$(json_extract_field "$_tmpfile" "auth_url")
    rm -f "$_tmpfile"

    echo "=== Token Revoked ==="
    echo "New access_token: $_new_token"
    echo "Auth URL (open in browser, valid 5 min): $_auth_url"
    echo ""
    echo "Update your config/.env with the new token."
    exit 0
fi

if [ -z "$SHORT_NAME" ]; then
    echo "Usage: sh create_account.sh --name \"Your Name\" [--author-url URL]" >&2
    echo "       sh create_account.sh --revoke" >&2
    exit 1
fi

# Build curl args as proper argv
set -- --data-urlencode "short_name=$SHORT_NAME"
if [ -n "$AUTHOR_NAME" ]; then
    set -- "$@" --data-urlencode "author_name=$AUTHOR_NAME"
fi
if [ -n "$AUTHOR_URL" ]; then
    set -- "$@" --data-urlencode "author_url=$AUTHOR_URL"
fi

_result=$(telegraph_post "createAccount" "$@")

_tmpfile="${TPH_TMPDIR}/telegraph_create_$$.json"
printf '%s' "$_result" > "$_tmpfile"

_token=$(json_extract_field "$_tmpfile" "access_token")
_auth_url=$(json_extract_field "$_tmpfile" "auth_url")
_short=$(json_extract_field "$_tmpfile" "short_name")
rm -f "$_tmpfile"

echo "=== Telegraph Account Created ==="
echo "Short name:    $_short"
echo "Access token:  $_token"
echo "Auth URL:      $_auth_url"
echo ""
echo "Next steps:"
echo "1. Save the token to config/.env:"
echo "   TELEGRAPH_ACCESS_TOKEN=$_token"
echo ""
echo "2. Open Auth URL in browser (valid 5 min, single use)"
echo "   to bind this account to your browser session."
echo "   After that, pages you create will be visible at telegra.ph"
