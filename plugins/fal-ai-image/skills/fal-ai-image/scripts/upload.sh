#!/bin/bash
# Upload local file to fal.ai storage and return URL
# Usage: ./upload.sh --file /path/to/image.png
# Output: URL that can be used in edit.sh --image-urls

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CONFIG_FILE="$SCRIPT_DIR/../config/.env"

# Load config
if [[ -f "$CONFIG_FILE" ]]; then
    # shellcheck disable=SC1090
    source "$CONFIG_FILE"
fi

if [[ -z "$FAL_KEY" ]]; then
    echo "Error: FAL_KEY not found. Set in config/.env or environment."
    exit 1
fi

# Defaults
FILE_PATH=""
OUTPUT_MODE="url"  # url or base64

# Parse args
while [[ $# -gt 0 ]]; do
    case $1 in
        --file|-f) FILE_PATH="$2"; shift 2 ;;
        --base64) OUTPUT_MODE="base64"; shift ;;
        *) echo "Unknown option: $1"; exit 1 ;;
    esac
done

if [[ -z "$FILE_PATH" ]]; then
    echo "Error: --file is required"
    echo "Usage: $0 --file /path/to/image.png"
    exit 1
fi

if [[ ! -f "$FILE_PATH" ]]; then
    echo "Error: File not found: $FILE_PATH"
    exit 1
fi

# Detect MIME type
MIME_TYPE=$(file -b --mime-type "$FILE_PATH")

# If base64 mode, just output data URI
if [[ "$OUTPUT_MODE" == "base64" ]]; then
    BASE64_DATA=$(base64 -w0 "$FILE_PATH" 2>/dev/null || base64 "$FILE_PATH")
    echo "data:$MIME_TYPE;base64,$BASE64_DATA"
    exit 0
fi

# Upload to fal.ai storage
FILENAME=$(basename "$FILE_PATH")

echo "Uploading $FILENAME to fal.ai storage..." >&2

# Step 1: Get presigned upload URL
INITIATE_RESPONSE=$(curl -s -X POST "https://rest.alpha.fal.ai/storage/upload/initiate" \
    -H "Authorization: Key $FAL_KEY" \
    -H "Content-Type: application/json" \
    -d "{\"file_name\": \"$FILENAME\", \"content_type\": \"$MIME_TYPE\"}")

UPLOAD_URL=$(echo "$INITIATE_RESPONSE" | grep -oE '"upload_url":[[:space:]]*"[^"]*"' | head -1 | sed 's/.*"\([^"]*\)"$/\1/')
FILE_URL=$(echo "$INITIATE_RESPONSE" | grep -oE '"file_url":[[:space:]]*"[^"]*"' | head -1 | sed 's/.*"\([^"]*\)"$/\1/')

if [[ -z "$UPLOAD_URL" || -z "$FILE_URL" ]]; then
    echo "Error: Failed to get upload URL" >&2
    echo "$INITIATE_RESPONSE" >&2

    # Fallback to base64 data URI
    echo "Falling back to base64 data URI..." >&2
    BASE64_DATA=$(base64 -w0 "$FILE_PATH" 2>/dev/null || base64 "$FILE_PATH")
    echo "data:$MIME_TYPE;base64,$BASE64_DATA"
    exit 0
fi

# Step 2: Upload file to presigned URL
UPLOAD_RESULT=$(curl -s -X PUT "$UPLOAD_URL" \
    -H "Content-Type: $MIME_TYPE" \
    --data-binary "@$FILE_PATH")

echo "Uploaded: $FILENAME" >&2
echo "$FILE_URL"
