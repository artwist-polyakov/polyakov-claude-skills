#!/usr/bin/env bash
# Copy the skill to a writable runtime directory before running scripts.

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
SKILL_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
SKILL_NAME="$(basename "$SKILL_DIR")"
RUN_DIR="${1:-/home/claude/$SKILL_NAME}"
RUN_PARENT="$(dirname "$RUN_DIR")"
RUN_BASE="$(basename "$RUN_DIR")"

mkdir -p "$RUN_PARENT"
RUN_PARENT="$(cd "$RUN_PARENT" && pwd)"
RUN_DIR="$RUN_PARENT/$RUN_BASE"

if [ "$SKILL_DIR" != "$RUN_DIR" ]; then
    mkdir -p "$RUN_DIR"
    cp -R "$SKILL_DIR"/. "$RUN_DIR"/
fi

if [ -f "$RUN_DIR/config/.env" ]; then
    bash "$RUN_DIR/scripts/sanitize_env.sh" "$RUN_DIR/config/.env"
fi

printf '%s\n' "$RUN_DIR"
