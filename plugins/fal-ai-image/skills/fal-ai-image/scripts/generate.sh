#!/bin/bash
# Generate images using fal.ai nano-banana-pro model

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CONFIG_FILE="$SCRIPT_DIR/../config/.env"
API_BASE="https://queue.fal.run/fal-ai/nano-banana-pro"

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
PROMPT=""
ASPECT_RATIO="1:1"
RESOLUTION="1K"
NUM_IMAGES=1
OUTPUT_FORMAT="png"
OUTPUT_DIR=""
FILENAME="generated"
WEB_SEARCH="false"

# Parse args
while [[ $# -gt 0 ]]; do
    case $1 in
        --prompt|-p) PROMPT="$2"; shift 2 ;;
        --aspect-ratio|-a) ASPECT_RATIO="$2"; shift 2 ;;
        --resolution|-r) RESOLUTION="$2"; shift 2 ;;
        --num-images|-n) NUM_IMAGES="$2"; shift 2 ;;
        --output-format|-f) OUTPUT_FORMAT="$2"; shift 2 ;;
        --output-dir|-o) OUTPUT_DIR="$2"; shift 2 ;;
        --filename) FILENAME="$2"; shift 2 ;;
        --web-search|-w) WEB_SEARCH="true"; shift ;;
        *) echo "Unknown option: $1"; exit 1 ;;
    esac
done

if [[ -z "$PROMPT" ]]; then
    echo "Error: --prompt is required"
    exit 1
fi

# Escape prompt for JSON
PROMPT_ESCAPED=$(echo "$PROMPT" | sed 's/\\/\\\\/g; s/"/\\"/g; s/\n/\\n/g')

# Build JSON
JSON_PAYLOAD="{\"prompt\":\"$PROMPT_ESCAPED\",\"num_images\":$NUM_IMAGES,\"aspect_ratio\":\"$ASPECT_RATIO\",\"resolution\":\"$RESOLUTION\",\"output_format\":\"$OUTPUT_FORMAT\""
if [[ "$WEB_SEARCH" == "true" ]]; then
    JSON_PAYLOAD="$JSON_PAYLOAD,\"enable_web_search\":true"
fi
JSON_PAYLOAD="$JSON_PAYLOAD}"

echo "Submitting request..."
echo "Prompt: ${PROMPT:0:100}..."
echo "Settings: $ASPECT_RATIO, $RESOLUTION, $OUTPUT_FORMAT"
echo ""

# Submit to queue
SUBMIT_RESPONSE=$(curl -s -X POST "$API_BASE" \
    -H "Authorization: Key $FAL_KEY" \
    -H "Content-Type: application/json" \
    -d "$JSON_PAYLOAD")

# Extract request_id via grep (handles optional space after colon)
REQUEST_ID=$(echo "$SUBMIT_RESPONSE" | grep -oE '"request_id":[[:space:]]*"[^"]*"' | head -1 | sed 's/.*"\([^"]*\)"$/\1/')

if [[ -z "$REQUEST_ID" ]]; then
    echo "Error: Failed to submit request"
    echo "$SUBMIT_RESPONSE"
    exit 1
fi

echo "Request ID: $REQUEST_ID"
echo "Waiting for generation..."

# Poll for completion
MAX_ATTEMPTS=60
ATTEMPT=0

while [[ $ATTEMPT -lt $MAX_ATTEMPTS ]]; do
    STATUS_RESPONSE=$(curl -s "$API_BASE/requests/$REQUEST_ID/status" \
        -H "Authorization: Key $FAL_KEY")

    STATUS=$(echo "$STATUS_RESPONSE" | grep -oE '"status":[[:space:]]*"[^"]*"' | head -1 | sed 's/.*"\([^"]*\)"$/\1/')

    case "$STATUS" in
        COMPLETED)
            echo "Generation complete!"
            echo ""
            break
            ;;
        FAILED)
            echo "Error: Generation failed"
            echo "$STATUS_RESPONSE"
            exit 1
            ;;
        IN_PROGRESS|IN_QUEUE|PENDING)
            echo "Status: $STATUS..."
            sleep 2
            ATTEMPT=$((ATTEMPT + 1))
            ;;
        *)
            echo "Status: $STATUS..."
            sleep 2
            ATTEMPT=$((ATTEMPT + 1))
            ;;
    esac
done

if [[ $ATTEMPT -ge $MAX_ATTEMPTS ]]; then
    echo "Error: Timeout waiting for generation"
    exit 1
fi

# Get result
RESULT=$(curl -s "$API_BASE/requests/$REQUEST_ID" \
    -H "Authorization: Key $FAL_KEY")

# Download images if output_dir specified
if [[ -n "$OUTPUT_DIR" ]]; then
    mkdir -p "$OUTPUT_DIR"
    TIMESTAMP=$(date +%Y%m%d_%H%M%S)

    # Extract URLs via grep and download (handles optional space after colon)
    URLS=$(echo "$RESULT" | grep -oE '"url":[[:space:]]*"[^"]*"' | sed 's/.*"\([^"]*\)"$/\1/')
    INDEX=0

    echo "Downloading images..."
    for URL in $URLS; do
        SUFFIX=""
        [[ $NUM_IMAGES -gt 1 ]] && SUFFIX="_$INDEX"
        OUTPUT_PATH="$OUTPUT_DIR/${FILENAME}_${TIMESTAMP}${SUFFIX}.${OUTPUT_FORMAT}"

        if curl -s -o "$OUTPUT_PATH" "$URL"; then
            echo "Saved: $OUTPUT_PATH"
        else
            echo "Warning: Failed to download $URL"
        fi
        INDEX=$((INDEX + 1))
    done
    echo ""
fi

# Output raw JSON for Claude to parse
echo "=== RESULT JSON ==="
echo "$RESULT"
echo "=== END RESULT ==="
echo ""
echo "Note: URLs expire in ~1 hour"
