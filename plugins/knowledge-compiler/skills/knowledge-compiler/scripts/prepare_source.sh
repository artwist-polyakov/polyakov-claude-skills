#!/bin/sh
# Extract a source into cache/jobs/<job-id>/source.txt.

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
SKILL_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
CACHE_DIR="${KNOWLEDGE_COMPILER_CACHE_DIR:-$SKILL_DIR/cache}"
UV_BIN="${UV:-uv}"

INPUT=""
MODE="auto"
JOB_ID=""

usage() {
    cat <<'EOF'
Usage:
  sh scripts/prepare_source.sh --input <file.pdf|file.epub|file.txt|file.md> [--mode auto|text|technical] [--job-id id]
EOF
}

while [ "$#" -gt 0 ]; do
    case "$1" in
        --input)
            INPUT="${2:-}"
            shift 2
            ;;
        --mode)
            MODE="${2:-auto}"
            shift 2
            ;;
        --job-id)
            JOB_ID="${2:-}"
            shift 2
            ;;
        -h|--help)
            usage
            exit 0
            ;;
        *)
            echo "Unknown argument: $1" >&2
            usage >&2
            exit 2
            ;;
    esac
done

if [ -z "$INPUT" ]; then
    echo "Missing --input" >&2
    usage >&2
    exit 2
fi

mkdir -p "$CACHE_DIR/jobs"

if ! command -v "$UV_BIN" >/dev/null 2>&1; then
    echo "Error: uv not found. Install uv to run Python scripts with declared dependencies." >&2
    exit 1
fi

if [ -n "$JOB_ID" ]; then
    exec "$UV_BIN" run --script "$SCRIPT_DIR/extract_source.py" \
        --input "$INPUT" \
        --mode "$MODE" \
        --cache-dir "$CACHE_DIR/jobs" \
        --job-id "$JOB_ID"
else
    exec "$UV_BIN" run --script "$SCRIPT_DIR/extract_source.py" \
        --input "$INPUT" \
        --mode "$MODE" \
        --cache-dir "$CACHE_DIR/jobs"
fi
