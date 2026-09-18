#!/usr/bin/env bash
# Compare multiple channels: subscribers, avg views, posting frequency, engagement
# Usage: bash scripts/compare_channels.sh --channels "ch1,ch2,ch3" [--limit 30]

set -e
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
. "$SCRIPT_DIR/common.sh"

load_config
parse_common_params "$@"
require_channels

if ! [[ "$LIMIT" =~ ^[1-9][0-9]*$ ]]; then
    echo "Error: --limit must be a positive integer." >&2
    exit 1
fi

echo "=== Channel Comparison ==="
echo ""
printf "%-25s %12s %10s %12s %8s\n" "Channel" "Subscribers" "Avg Views" "Avg Reactions" "Posts"
printf "%-25s %12s %10s %12s %8s\n" "-------" "-----------" "---------" "-------------" "-----"

_old_ifs="$IFS"
IFS=','
for _channel in $CHANNELS; do
    _channel=$(echo "$_channel" | sed 's/^[[:space:]]*//;s/[[:space:]]*$//')
    [ -z "$_channel" ] && continue

    _cache_dir=$(cache_dir_for_channel "$_channel")

    # Fetch posts and save the fresh latest page for channel info
    _posts_file="$_cache_dir/posts.tsv"
    fetch_channel_pages "$_channel" "$LIMIT" "" "" > "$_posts_file" 2>/dev/null || true

    _html_file="$_cache_dir/raw/page_latest.html"

    _subs=""
    if [ -s "$_html_file" ]; then
        _info=$(parse_channel_info_from_html "$_html_file")
        _subs=$(echo "$_info" | grep -o '"subscribers":"[^"]*"' | sed 's/.*"subscribers":"//;s/"//')
    fi

    if [ ! -s "$_posts_file" ]; then
        printf "%-25s %12s %10s %12s %8s\n" "@$_channel" "${_subs:-?}" "?" "?" "0"
        continue
    fi

    # Calculate metrics
    # TSV cols: $1=id $2=date $3=views $4=reactions
    _metrics=$(awk -F'\t' '
    {
        v = $3; r = $4
        gsub(/[[:space:]]/, "", v)
        if (v ~ /[Kk]$/) { sub(/[Kk]$/, "", v); v = v * 1000 }
        if (v ~ /[Mm]$/) { sub(/[Mm]$/, "", v); v = v * 1000000 }
        if (v + 0 > 0) total_v += v
        if (r + 0 > 0) total_r += r
        n++
    }
    END {
        avg_v = (n > 0) ? int(total_v / n) : 0
        avg_r = (n > 0) ? int(total_r / n) : 0
        printf "%d\t%d\t%d", avg_v, avg_r, n
    }
    ' "$_posts_file")

    _avg_v=$(echo "$_metrics" | cut -f1)
    _avg_r=$(echo "$_metrics" | cut -f2)
    _post_count=$(echo "$_metrics" | cut -f3)

    printf "%-25s %12s %10s %12s %8s\n" \
        "@$_channel" "${_subs:-?}" "$_avg_v" "$_avg_r" "$_post_count"
done
IFS="$_old_ifs"

echo ""
echo "(based on last $LIMIT posts per channel)"
