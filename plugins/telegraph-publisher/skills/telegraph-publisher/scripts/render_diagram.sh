#!/bin/sh
# Render a PlantUML or Mermaid diagram via public rendering server
#
# Usage:
#   sh render_diagram.sh --type plantuml --file diagram.puml
#   sh render_diagram.sh --type mermaid --file diagram.mmd
#   sh render_diagram.sh --type mermaid --file diagram.mmd --upload
#
# Without --upload: outputs render URL (image hosted on public server)
# With --upload: downloads PNG, uploads to Telegraph, outputs Telegraph URL
#
# PRIVACY: Diagram source is sent to a public server (plantuml.com / mermaid.ink).
# Do not use for confidential content.

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
. "$SCRIPT_DIR/common.sh"

# Parse arguments
DIAGRAM_TYPE=""
DIAGRAM_FILE=""
DO_UPLOAD=""
INSECURE=""
GITHUB_PAGE_PATH=""
GITHUB_NAME=""

while [ $# -gt 0 ]; do
    case "$1" in
        --type)     DIAGRAM_TYPE="$2"; shift 2 ;;
        --file)     DIAGRAM_FILE="$2"; shift 2 ;;
        --upload)   DO_UPLOAD="1"; shift ;;
        --insecure) INSECURE="1"; shift ;;
        --github-page-path) GITHUB_PAGE_PATH="$2"; shift 2 ;;
        --github-name) GITHUB_NAME="$2"; shift 2 ;;
        *)          shift ;;
    esac
done

if [ -z "$DIAGRAM_TYPE" ] || [ -z "$DIAGRAM_FILE" ]; then
    echo "Usage: sh render_diagram.sh --type plantuml|mermaid --file FILE [--upload] [--github-page-path PATH] [--github-name NAME]" >&2
    exit 1
fi

case "$DIAGRAM_TYPE" in
    plantuml|mermaid) ;;
    *)
        echo "Error: Unsupported diagram type: $DIAGRAM_TYPE" >&2
        echo "Supported: plantuml, mermaid" >&2
        exit 1
        ;;
esac

if [ ! -f "$DIAGRAM_FILE" ]; then
    echo "Error: File not found: $DIAGRAM_FILE" >&2
    exit 1
fi

# Privacy warning
echo "WARNING: Diagram source will be sent to a public rendering server." >&2
case "$DIAGRAM_TYPE" in
    plantuml) echo "Server: plantuml.com" >&2 ;;
    mermaid)  echo "Server: mermaid.ink" >&2 ;;
esac
echo "Do not use for confidential content." >&2
echo "" >&2

# Get render URL
_render_url=$(python3 "$SCRIPT_DIR/diagram_encode.py" "$DIAGRAM_TYPE" < "$DIAGRAM_FILE")

if [ -z "$_render_url" ]; then
    echo "Error: Failed to encode diagram." >&2
    exit 1
fi

if [ -z "$DO_UPLOAD" ] && [ -z "$GITHUB_PAGE_PATH" ]; then
    # Just output the render URL
    echo "$_render_url"
    exit 0
fi

# Download and upload to Telegraph
echo "Downloading rendered diagram..." >&2

_tmpdir="${TPH_TMPDIR}/telegraph_diagram_$$"
mkdir -p "$_tmpdir"
trap 'rm -rf "$_tmpdir"' EXIT

_png_file="$_tmpdir/diagram.png"
_headers_file="$_tmpdir/headers.txt"

set -- -s -f -w '%{http_code}' -o "$_png_file" -D "$_headers_file"
if [ -n "$INSECURE" ]; then
    set -- "$@" -k
fi
_http_code=$(curl "$@" "$_render_url" 2>/dev/null) || {
    echo "Error: Failed to download diagram from rendering server." >&2
    echo "URL: $_render_url" >&2
    if [ "$DIAGRAM_TYPE" = "plantuml" ]; then
        echo "Note: PlantUML server may return an error image instead of HTTP error for invalid diagrams." >&2
        echo "If the download succeeded but the image shows an error, check your diagram syntax." >&2
    fi
    exit 1
}

# Verify content-type is image
_content_type=$(grep -i 'content-type' "$_headers_file" | head -1 | sed 's/.*:[[:space:]]*//' | tr -d '\r\n' | tr '[:upper:]' '[:lower:]')
case "$_content_type" in
    image/*) ;;
    *)
        echo "Warning: Unexpected content-type: $_content_type" >&2
        echo "Expected image/*. The rendering server may have returned an error." >&2
        ;;
esac

# Check file is not empty
_file_size=$(wc -c < "$_png_file" | tr -d ' ')
if [ "$_file_size" -eq 0 ]; then
    echo "Error: Downloaded file is empty." >&2
    exit 1
fi

if [ -n "$GITHUB_PAGE_PATH" ]; then
    echo "Downloaded ${_file_size} bytes. Uploading to GitHub assets..." >&2
    echo "Uploading diagram to GitHub assets..." >&2
    set -- --file "$_png_file" --page-path "$GITHUB_PAGE_PATH"
    if [ -n "$GITHUB_NAME" ]; then
        set -- "$@" --name "$GITHUB_NAME"
    fi
    _github_url=$(sh "$SCRIPT_DIR/github_upload.sh" "$@" 2>/dev/null) || {
        echo "Error: Upload to GitHub assets failed." >&2
        echo "Render URL (use directly): $_render_url" >&2
        exit 1
    }
    echo "$_github_url"
elif [ -n "$DO_UPLOAD" ]; then
    echo "Downloaded ${_file_size} bytes. Uploading to Telegraph..." >&2
    set -- --file "$_png_file"
    if [ -n "$INSECURE" ]; then
        set -- "$@" --insecure
    fi
    _telegraph_url=$(sh "$SCRIPT_DIR/upload.sh" "$@" 2>/dev/null) || {
        echo "Error: Upload to Telegraph failed." >&2
        echo "Render URL (use directly): $_render_url" >&2
        exit 1
    }
    echo "$_telegraph_url"
fi

if [ "$DIAGRAM_TYPE" = "plantuml" ]; then
    echo "" >&2
    echo "Note: PlantUML server does not return HTTP errors for invalid diagrams." >&2
    echo "Verify the rendered image visually before publishing." >&2
fi
