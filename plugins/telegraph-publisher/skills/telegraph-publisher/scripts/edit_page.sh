#!/bin/sh
# Edit an existing Telegraph page
# Usage:
#   sh edit_page.sh --path "Page-Title-03-09" --title "New Title" --content-file content.json
#   sh edit_page.sh --path "Page-Title-03-09" --title "New Title" --html-file article.html
#   sh edit_page.sh --path "Page-Title-03-09" --title "New Title" --html "<p>Inline HTML</p>"

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
. "$SCRIPT_DIR/common.sh"
load_config

# Parse arguments
PAGE_PATH=""
TITLE=""
CONTENT_FILE=""
HTML_FILE=""
HTML_INLINE=""
AUTHOR_NAME=""
AUTHOR_URL=""

while [ $# -gt 0 ]; do
    case "$1" in
        --path)         PAGE_PATH="$2"; shift 2 ;;
        --title)        TITLE="$2"; shift 2 ;;
        --content-file) CONTENT_FILE="$2"; shift 2 ;;
        --html-file)    HTML_FILE="$2"; shift 2 ;;
        --html)         HTML_INLINE="$2"; shift 2 ;;
        --author-name)  AUTHOR_NAME="$2"; shift 2 ;;
        --author-url)   AUTHOR_URL="$2"; shift 2 ;;
        *)              shift ;;
    esac
done

if [ -z "$PAGE_PATH" ] || [ -z "$TITLE" ]; then
    echo "Usage: sh edit_page.sh --path PATH --title TITLE (--content-file FILE | --html-file FILE | --html HTML)" >&2
    exit 1
fi

# Resolve content
if [ -n "$CONTENT_FILE" ]; then
    if [ ! -f "$CONTENT_FILE" ]; then
        echo "Error: Content file not found: $CONTENT_FILE" >&2
        exit 1
    fi
    _content=$(cat "$CONTENT_FILE")
elif [ -n "$HTML_FILE" ]; then
    if [ ! -f "$HTML_FILE" ]; then
        echo "Error: HTML file not found: $HTML_FILE" >&2
        exit 1
    fi
    _content=$(python3 "$SCRIPT_DIR/content_converter.py" < "$HTML_FILE")
elif [ -n "$HTML_INLINE" ]; then
    _content=$(printf '%s' "$HTML_INLINE" | python3 "$SCRIPT_DIR/content_converter.py")
else
    echo "Error: Provide --content-file, --html-file, or --html" >&2
    exit 1
fi

# Build request as proper argv
set -- -d "access_token=$TELEGRAPH_ACCESS_TOKEN"
set -- "$@" --data-urlencode "title=$TITLE"
set -- "$@" --data-urlencode "content=$_content"
set -- "$@" -d "return_content=false"

if [ -n "$AUTHOR_NAME" ]; then
    set -- "$@" --data-urlencode "author_name=$AUTHOR_NAME"
fi
if [ -n "$AUTHOR_URL" ]; then
    set -- "$@" --data-urlencode "author_url=$AUTHOR_URL"
fi

_result=$(telegraph_post "editPage/$PAGE_PATH" "$@")

_tmpfile="${TPH_TMPDIR}/telegraph_edit_$$.json"
printf '%s' "$_result" > "$_tmpfile"

_url=$(json_extract_field "$_tmpfile" "url")
_path=$(json_extract_field "$_tmpfile" "path")
rm -f "$_tmpfile"

echo "=== Page Updated ==="
echo "URL:  $_url"
echo "Path: $_path"
