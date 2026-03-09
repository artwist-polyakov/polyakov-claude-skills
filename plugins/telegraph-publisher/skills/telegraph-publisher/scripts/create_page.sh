#!/bin/sh
# Create a Telegraph page with auto-split for large content
# Usage:
#   sh create_page.sh --title "My Article" --content-file content.json
#   sh create_page.sh --title "My Article" --html-file article.html
#   sh create_page.sh --title "My Article" --html "<p>Hello</p>"
#   sh create_page.sh --title "My Article" --html-file big.html --author-name "Author"

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
. "$SCRIPT_DIR/common.sh"
load_config

# Parse arguments
TITLE=""
CONTENT_FILE=""
HTML_FILE=""
HTML_INLINE=""
AUTHOR_NAME=""
AUTHOR_URL=""

while [ $# -gt 0 ]; do
    case "$1" in
        --title)        TITLE="$2"; shift 2 ;;
        --content-file) CONTENT_FILE="$2"; shift 2 ;;
        --html-file)    HTML_FILE="$2"; shift 2 ;;
        --html)         HTML_INLINE="$2"; shift 2 ;;
        --author-name)  AUTHOR_NAME="$2"; shift 2 ;;
        --author-url)   AUTHOR_URL="$2"; shift 2 ;;
        *)              shift ;;
    esac
done

if [ -z "$TITLE" ]; then
    echo "Usage: sh create_page.sh --title TITLE (--content-file FILE | --html-file FILE | --html HTML)" >&2
    exit 1
fi

# --------------- Resolve content to Node JSON ---------------

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

# --------------- Helper: build common author args ---------------
# Appends --data-urlencode author_name/author_url to positional params
# Call: _build_author_args; then use "$@"
_build_author_args() {
    if [ -n "$AUTHOR_NAME" ]; then
        set -- "$@" --data-urlencode "author_name=$AUTHOR_NAME"
    fi
    if [ -n "$AUTHOR_URL" ]; then
        set -- "$@" --data-urlencode "author_url=$AUTHOR_URL"
    fi
    # Return args via stdout, one per line — caller collects
}

# --------------- Check size and split if needed ---------------

_size=$(printf '%s' "$_content" | python3 "$SCRIPT_DIR/content_converter.py" --check-size)

if [ "$_size" -gt 61440 ]; then
    echo "Content size: ${_size} bytes (limit: 64KB). Auto-splitting..." >&2

    # Create work directory
    WORK_DIR="${TPH_TMPDIR}/telegraph_split_$$"
    mkdir -p "$WORK_DIR"
    trap 'rm -rf "$WORK_DIR"' EXIT

    # Split content
    printf '%s' "$_content" | python3 "$SCRIPT_DIR/content_converter.py" --split --output-dir "$WORK_DIR"

    # Count parts
    _part_count=$(ls "$WORK_DIR"/part_*.json 2>/dev/null | wc -l | tr -d ' ')

    if [ "$_part_count" -eq 0 ]; then
        echo "Error: Split produced no parts." >&2
        exit 1
    fi

    echo "Split into $_part_count parts." >&2

    # Create child pages (from last to first), collect URLs into a file
    _urls_file="$WORK_DIR/part_urls.txt"
    : > "$_urls_file"

    _part_num="$_part_count"
    while [ "$_part_num" -ge 1 ]; do
        _part_file="$WORK_DIR/part_${_part_num}.json"
        _part_title="${TITLE} — part ${_part_num}"
        _part_content=$(cat "$_part_file")

        set -- -d "access_token=$TELEGRAPH_ACCESS_TOKEN"
        set -- "$@" --data-urlencode "title=$_part_title"
        set -- "$@" --data-urlencode "content=$_part_content"
        set -- "$@" -d "return_content=false"
        if [ -n "$AUTHOR_NAME" ]; then
            set -- "$@" --data-urlencode "author_name=$AUTHOR_NAME"
        fi
        if [ -n "$AUTHOR_URL" ]; then
            set -- "$@" --data-urlencode "author_url=$AUTHOR_URL"
        fi

        _result=$(telegraph_post "createPage" "$@")

        _result_file="${WORK_DIR}/result_${_part_num}.json"
        printf '%s' "$_result" > "$_result_file"
        _url=$(json_extract_field "$_result_file" "url")
        echo "${_part_num}|${_url}" >> "$_urls_file"

        echo "  Part $_part_num: $_url" >&2
        _part_num=$(( _part_num - 1 ))
    done

    # Build index page content via Python (safe JSON construction)
    _index_content=$(python3 -c "
import json, sys
lines = open('$_urls_file').read().strip().split('\n')
lines.sort(key=lambda l: int(l.split('|')[0]))
total = len(lines)
nodes = [{'tag': 'p', 'children': ['This article was split into ' + str(total) + ' parts due to size.']}]
for line in lines:
    if not line.strip():
        continue
    num, url = line.strip().split('|', 1)
    nodes.append({'tag': 'p', 'children': [{'tag': 'a', 'attrs': {'href': url}, 'children': ['Part ' + num]}]})
print(json.dumps(nodes, ensure_ascii=False))
")

    set -- -d "access_token=$TELEGRAPH_ACCESS_TOKEN"
    set -- "$@" --data-urlencode "title=$TITLE"
    set -- "$@" --data-urlencode "content=$_index_content"
    set -- "$@" -d "return_content=false"
    if [ -n "$AUTHOR_NAME" ]; then
        set -- "$@" --data-urlencode "author_name=$AUTHOR_NAME"
    fi
    if [ -n "$AUTHOR_URL" ]; then
        set -- "$@" --data-urlencode "author_url=$AUTHOR_URL"
    fi

    _result=$(telegraph_post "createPage" "$@")

    _result_file="${WORK_DIR}/result_index.json"
    printf '%s' "$_result" > "$_result_file"
    _url=$(json_extract_field "$_result_file" "url")
    _path=$(json_extract_field "$_result_file" "path")

    echo ""
    echo "=== Article Published (multi-part) ==="
    echo "Index URL: $_url"
    echo "Path:      $_path"
    echo "Parts:     $_part_count"
    exit 0
fi

# --------------- Single page publish ---------------

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

_result=$(telegraph_post "createPage" "$@")

_tmpfile="${TPH_TMPDIR}/telegraph_create_page_$$.json"
printf '%s' "$_result" > "$_tmpfile"

_url=$(json_extract_field "$_tmpfile" "url")
_path=$(json_extract_field "$_tmpfile" "path")
rm -f "$_tmpfile"

echo "=== Page Published ==="
echo "URL:  $_url"
echo "Path: $_path"
