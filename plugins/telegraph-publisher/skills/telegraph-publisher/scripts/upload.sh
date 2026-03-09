#!/bin/sh
# Upload a local image/video to Telegraph
# Uses unofficial but stable telegra.ph/upload endpoint (no token required)
#
# Usage:
#   sh upload.sh --file /path/to/image.png
#   sh upload.sh --file /path/to/image.png --insecure   # skip SSL verification
#
# Output: full URL (https://telegra.ph/file/...)
#
# NOTE: This endpoint is NOT part of the official Telegraph API.
# It may change or become unavailable without notice.
# May fail behind corporate proxies/VPNs that intercept HTTPS.

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
. "$SCRIPT_DIR/common.sh"

# Parse arguments
FILE=""
INSECURE=""

while [ $# -gt 0 ]; do
    case "$1" in
        --file)     FILE="$2"; shift 2 ;;
        --insecure) INSECURE="1"; shift ;;
        *)          shift ;;
    esac
done

if [ -z "$FILE" ]; then
    echo "Usage: sh upload.sh --file /path/to/image.png [--insecure]" >&2
    exit 1
fi

if [ ! -f "$FILE" ]; then
    echo "Error: File not found: $FILE" >&2
    exit 1
fi

# --------------- Validation ---------------

# Check file extension
_ext=$(echo "$FILE" | sed 's/.*\.//' | tr '[:upper:]' '[:lower:]')
case "$_ext" in
    jpg|jpeg|png|gif|webp|mp4) ;;
    *)
        echo "Error: Unsupported file type: .$_ext" >&2
        echo "Supported: jpg, jpeg, png, gif, webp, mp4" >&2
        exit 1
        ;;
esac

# Check file size (<5MB)
_size=$(wc -c < "$FILE" | tr -d ' ')
_max_size=5242880
if [ "$_size" -gt "$_max_size" ]; then
    echo "Error: File too large ($(( _size / 1024 ))KB). Maximum: 5MB." >&2
    exit 1
fi

# Best-effort MIME type check (if 'file' command available)
if command -v file >/dev/null 2>&1; then
    _mime=$(file --mime-type -b "$FILE" 2>/dev/null || true)
    if [ -n "$_mime" ]; then
        case "$_mime" in
            image/*|video/*) ;;
            *)
                echo "Warning: MIME type '$_mime' does not look like image/video." >&2
                echo "Proceeding anyway (extension-based check passed)." >&2
                ;;
        esac
    fi
fi

# --------------- Upload ---------------

echo "Uploading $(( _size / 1024 ))KB file to Telegraph..." >&2

_tmpfile="${TPH_TMPDIR}/telegraph_upload_$$.json"
trap 'rm -f "$_tmpfile"' EXIT

# Build curl args
set -- -s -w '%{http_code}' -o "$_tmpfile"

# Skip SSL verification for HTTPS-intercepting proxies/VPNs
if [ -n "$INSECURE" ]; then
    set -- "$@" -k
    echo "Warning: SSL verification disabled (--insecure mode)." >&2
fi

set -- "$@" -F "file=@$FILE" "https://telegra.ph/upload"

_http_code=$(curl "$@")

if [ "$_http_code" -ge 400 ] 2>/dev/null; then
    echo "Error: Upload failed with HTTP $_http_code" >&2
    cat "$_tmpfile" >&2
    echo "" >&2
    echo "Possible causes:" >&2
    echo "  - Corporate proxy/VPN intercepting HTTPS (try --insecure)" >&2
    echo "  - Telegraph upload endpoint temporarily unavailable" >&2
    echo "Fallback: use a public image URL directly." >&2
    exit 1
fi

# Parse response: expect [{"src":"/file/..."}]
_src=$(grep -o '"src"[[:space:]]*:[[:space:]]*"[^"]*"' "$_tmpfile" | head -1 | sed 's/.*"src"[[:space:]]*:[[:space:]]*"//;s/"$//')

if [ -z "$_src" ]; then
    echo "Error: Unexpected response format from upload endpoint:" >&2
    cat "$_tmpfile" >&2
    echo "" >&2
    echo "Fallback: use a public image URL directly." >&2
    exit 1
fi

_full_url="https://telegra.ph${_src}"
echo "$_full_url"
echo "Uploaded successfully." >&2
