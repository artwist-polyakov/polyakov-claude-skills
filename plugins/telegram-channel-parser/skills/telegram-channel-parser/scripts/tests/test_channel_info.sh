#!/usr/bin/env bash
set -e

TEST_DIR="$(cd "$(dirname "$0")" && pwd)"
. "$TEST_DIR/../common.sh"

page="$(mktemp)"
trap 'rm -f "$page"' EXIT
cat > "$page" <<'EOF'
<div class="tgme_channel_info_header_title_wrap">
  <div class="tgme_channel_info_header_title"><span dir="auto">Название канала</span></div>
</div>
<div class="tgme_channel_info_counters"><div class="tgme_channel_info_counter"><span class="counter_value">9.1M</span> <span class="counter_type">subscribers</span></div><div class="tgme_channel_info_counter"><span class="counter_value">16</span> <span class="counter_type">photos</span></div></div>
EOF

expected='{"title":"Название канала","description":"","subscribers":"9.1M"}'
actual=$(parse_channel_info_from_html "$page")
[ "$actual" = "$expected" ] || { echo "FAIL: неверные сведения о канале: $actual" >&2; exit 1; }

printf '%s\n' '<div class="tgme_header_counter">9.1M subscribers</div>' >> "$page"
actual=$(parse_channel_info_from_html "$page")
[ "$actual" = "$expected" ] || { echo "FAIL: неверный счётчик из заголовка: $actual" >&2; exit 1; }
