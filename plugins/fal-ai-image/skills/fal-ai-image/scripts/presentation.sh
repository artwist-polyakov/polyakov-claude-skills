#!/bin/bash
# Combine slide images into PDF presentation
# Uses ImageMagick, GraphicsMagick, or fallback instructions

set -e

# Defaults
SLIDES_DIR=""
OUTPUT="presentation.pdf"
ORDER=""

# Parse args
while [[ $# -gt 0 ]]; do
    case $1 in
        --slides-dir|-d) SLIDES_DIR="$2"; shift 2 ;;
        --output|-o) OUTPUT="$2"; shift 2 ;;
        --order) ORDER="$2"; shift 2 ;;
        *) echo "Unknown option: $1"; exit 1 ;;
    esac
done

if [[ -z "$SLIDES_DIR" ]]; then
    echo "Error: --slides-dir is required"
    echo "Usage: $0 --slides-dir ./slides --output presentation.pdf [--order 1,2,3]"
    exit 1
fi

if [[ ! -d "$SLIDES_DIR" ]]; then
    echo "Error: Directory not found: $SLIDES_DIR"
    exit 1
fi

# Build file list
if [[ -n "$ORDER" ]]; then
    # Use specified order
    FILES=""
    IFS=',' read -ra INDICES <<< "$ORDER"
    for idx in "${INDICES[@]}"; do
        # Find file matching pattern *_${idx}.* or slide_${idx}.*
        FOUND=$(find "$SLIDES_DIR" -type f \( -name "*_${idx}.png" -o -name "*_${idx}.jpg" -o -name "*_${idx}.jpeg" -o -name "*_${idx}.webp" -o -name "slide_${idx}.*" -o -name "slide${idx}.*" \) 2>/dev/null | head -1)
        if [[ -n "$FOUND" ]]; then
            FILES="$FILES $FOUND"
        fi
    done
else
    # Natural sort order
    FILES=$(find "$SLIDES_DIR" -type f \( -name "*.png" -o -name "*.jpg" -o -name "*.jpeg" -o -name "*.webp" \) | sort -V)
fi

if [[ -z "$FILES" ]]; then
    echo "Error: No image files found in $SLIDES_DIR"
    exit 1
fi

FILE_COUNT=$(echo "$FILES" | wc -w)
echo "Found $FILE_COUNT slide(s)"
echo "Output: $OUTPUT"
echo ""

# Try different tools in order of preference
if command -v convert &> /dev/null; then
    echo "Using ImageMagick..."
    # shellcheck disable=SC2086
    convert $FILES "$OUTPUT"
    echo "Created: $OUTPUT"
elif command -v gm &> /dev/null; then
    echo "Using GraphicsMagick..."
    # shellcheck disable=SC2086
    gm convert $FILES "$OUTPUT"
    echo "Created: $OUTPUT"
elif command -v img2pdf &> /dev/null; then
    echo "Using img2pdf..."
    # shellcheck disable=SC2086
    img2pdf $FILES -o "$OUTPUT"
    echo "Created: $OUTPUT"
else
    echo "=== NO PDF TOOL FOUND ==="
    echo ""
    echo "Slides are ready in: $SLIDES_DIR"
    echo "Files:"
    for f in $FILES; do
        echo "  - $f"
    done
    echo ""
    echo "To create PDF manually, install one of:"
    echo "  - ImageMagick: brew install imagemagick / apt install imagemagick"
    echo "  - GraphicsMagick: brew install graphicsmagick / apt install graphicsmagick"
    echo "  - img2pdf: pip install img2pdf"
    echo ""
    echo "Then run:"
    echo "  convert $FILES $OUTPUT"
    exit 0
fi

echo ""
echo "Done!"
