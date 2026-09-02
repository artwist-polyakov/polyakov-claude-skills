#!/usr/bin/env bash
# Digest as JSON — ready to inject into React artifact
# Usage: bash scripts/digest_json.sh --period today [--channels "ch1,ch2"] [--out path]
# Output: writes JSON file, prints path to stdout
# JSON shape: { "posts": [...], "channels": {...} }

set -e
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
. "$SCRIPT_DIR/common.sh"

load_config
parse_common_params "$@"
require_channels

if [ -z "$PERIOD" ]; then
    PERIOD="today"
fi

_after_date=$(period_to_after_date "$PERIOD")
if [ -z "$_after_date" ]; then
    echo "Error: invalid --period '$PERIOD'." >&2
    exit 1
fi

# Output file — user can override with --csv (reusing the flag)
_out_dir="$CACHE_DIR"
mkdir -p "$_out_dir"
_outfile="${CSV_OUT:-$_out_dir/digest_${PERIOD}.json}"

# Temp files for streaming — avoid bash variable limits
_posts_tmp="${TMPDIR:-/tmp}/tg_posts_$$.jsonl"
_channels_tmp="${TMPDIR:-/tmp}/tg_channels_$$.jsonl"
trap 'rm -f "$_posts_tmp" "$_channels_tmp"' EXIT
: > "$_posts_tmp"
: > "$_channels_tmp"

_old_ifs="$IFS"
IFS=','
for _channel in $CHANNELS; do
    _channel=$(echo "$_channel" | sed 's/^[[:space:]]*//;s/[[:space:]]*$//')
    [ -z "$_channel" ] && continue

    _cache_dir=$(cache_dir_for_channel "$_channel")
    _html_file="$_cache_dir/raw/page_latest.html"

    # Fetch posts and save the fresh latest page for channel info
    _result=$(fetch_channel_pages "$_channel" "50" "" "$_after_date" 2>/dev/null) || true

    # Channel info → append to channels temp
    if [ -s "$_html_file" ]; then
        _info=$(parse_channel_info_from_html "$_html_file")
        _title=$(echo "$_info" | sed 's/.*"title":"//;s/".*//')
        _subs=$(echo "$_info" | sed 's/.*"subscribers":"//;s/".*//')
        printf '"%s":{"title":"%s","subscribers":"%s"}\n' "$_channel" "$_title" "$_subs" >> "$_channels_tmp"
    fi

    # Posts → stream TSV through awk → append JSONL to posts temp
    [ -z "$_result" ] && continue

    echo "$_result" | awk -F'\t' -v ch="$_channel" '
    {
        id = $1; date = $2; views = $3; reactions = $4
        fwd_from = $5; fwd_link = $6; text = $7; media = $8

        gsub(/\\/, "\\\\", text)
        gsub(/"/, "\\\"", text)
        gsub(/\t/, " ", text)
        gsub(/\r/, "", text)

        gsub(/\\/, "\\\\", fwd_from)
        gsub(/"/, "\\\"", fwd_from)

        printf "{\"id\":\"%s\",\"channel\":\"%s\",\"date\":\"%s\",\"views\":\"%s\",\"reactions\":\"%s\"", id, ch, date, views, reactions

        if (fwd_from != "") printf ",\"fwd_from\":\"%s\"", fwd_from
        if (fwd_link != "") printf ",\"fwd_link\":\"%s\"", fwd_link
        if (media != "") printf ",\"mediaUrl\":\"%s\"", media

        printf ",\"text\":\"%s\"}\n", text
    }' >> "$_posts_tmp"

    echo "  @$_channel: done" >&2
done
IFS="$_old_ifs"

# Assemble final JSON from temp files → write directly to output file
{
    printf '{"posts":['
    # Join JSONL lines with commas
    awk 'NR>1{printf ","}{printf "%s",$0}' "$_posts_tmp"
    printf '],"channels":{'
    awk 'NR>1{printf ","}{printf "%s",$0}' "$_channels_tmp"
    printf '}}\n'
} > "$_outfile"

_size=$(wc -c < "$_outfile" | tr -d ' ')
_count=$(wc -l < "$_posts_tmp" | tr -d ' ')
echo "Digest: $_count posts, ${_size} bytes → $_outfile" >&2
echo "$_outfile"
