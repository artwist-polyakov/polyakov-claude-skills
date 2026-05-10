#!/usr/bin/env bash

set -e

TEST_DIR="$(cd "$(dirname "$0")" && pwd)"
SKILL_DIR="$(cd "$TEST_DIR/../.." && pwd)"
COMMON_SH="$SKILL_DIR/scripts/common.sh"
TMP_DIR="$(mktemp -d "${TMPDIR:-/tmp}/tg_config_test.XXXXXX")"

cleanup() {
    rm -rf "$TMP_DIR"
}

trap cleanup EXIT INT TERM

cat > "$TMP_DIR/.env" <<'EOF'
TG_CATEGORIES=ai
TG_DEFAULT_CATEGORY=ai
TG_CHANNELS_AI_LABEL=AI и технологии
TG_CHANNELS_AI=countwithsasha,evilfreelancer
EOF

# shellcheck disable=SC1090
. "$COMMON_SH"

SCRIPT_DIR="$SKILL_DIR/scripts"
CONFIG_FILE="$TMP_DIR/.env"
CACHE_DIR="$TMP_DIR/cache"

load_config >/dev/null

[ "$TG_CATEGORIES" = "ai" ]
[ "$TG_DEFAULT_CATEGORY" = "ai" ]
[ "$TG_CHANNELS_AI_LABEL" = "AI и технологии" ]
[ "$TG_CHANNELS_AI" = "countwithsasha,evilfreelancer" ]
grep -q '^TG_CHANNELS_AI_LABEL="AI и технологии"$' "$TMP_DIR/.env"
