#!/usr/bin/env bash
# Common functions for Telegram Channel Parser skill

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
CONFIG_FILE="$SCRIPT_DIR/../config/.env"
CACHE_DIR="$SCRIPT_DIR/../cache"

TG_BASE_URL="https://t.me/s"
TG_REQUEST_DELAY="1.5"

load_config() {
    if [ -f "$CONFIG_FILE" ]; then
        if [ -f "$SCRIPT_DIR/sanitize_env.sh" ]; then
            bash "$SCRIPT_DIR/sanitize_env.sh" "$CONFIG_FILE"
        fi
        . "$CONFIG_FILE"
    fi
}
# --------------- Input normalization ---------------
# Accepts: username, @username, https://t.me/username, https://t.me/s/username, t.me/username
normalize_channel() {
    local input="$1"
    # Remove @ prefix
    input="${input#@}"
    # Remove https:// or http://
    input="${input#https://}"
    input="${input#http://}"
    # Remove t.me/s/ or t.me/
    input="${input#t.me/s/}"
    input="${input#t.me/}"
    # Remove trailing slash
    input="${input%/}"
    # Remove any query params
    input="${input%%\?*}"
    echo "$input"
}



cache_dir_for_channel() {
    local dir="$CACHE_DIR/channels/$1"
    mkdir -p "$dir/raw"
    echo "$dir"
}

tg_fetch() {
    sleep "$TG_REQUEST_DELAY"
    curl -s -L \
        -H "Accept-Language: ru-RU,ru;q=0.9,en;q=0.5" \
        -H "Accept: text/html" \
        -A "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36" \
        "$1"
}

parse_posts_from_html() {
    local html_file="$1"
    local out_file="$2"
    awk -f "$SCRIPT_DIR/parse_tg_posts.awk" "$html_file" > "$out_file"
}

parse_channel_info_from_html() {
    local html_file="$1"
    local title desc subs

    # Title is inside <span dir="auto"> within header_title div
    title=$(grep 'tgme_channel_info_header_title' "$html_file" 2>/dev/null | grep -oP '<span[^>]*>\K[^<]+' | head -1 || true)
    # Description
    desc=$(grep 'tgme_channel_info_description' "$html_file" 2>/dev/null | sed 's/<[^>]*>//g;s/^[[:space:]]*//' | head -1 || true)
    # Subscribers from tgme_header_counter or counter_value
    subs=$(grep 'tgme_header_counter' "$html_file" 2>/dev/null | grep -oE '[0-9][0-9.]*K?' | head -1 || true)
    if [ -z "$subs" ]; then
        subs=$(grep 'counter_value' "$html_file" 2>/dev/null | grep -oP 'counter_value[^>]*>\K[^<]+' | head -1 || true)
    fi

    title="${title//\"/\\\"}"
    desc="${desc//\"/\\\"}"

    printf '{"title":"%s","description":"%s","subscribers":"%s"}\n' "$title" "$desc" "$subs"
}

fetch_channel_pages() {
    local channel="$1"
    local limit="${2:-20}"
    local before="${3:-}"
    local after_date="${4:-}"

    local cache_dir
    cache_dir=$(cache_dir_for_channel "$channel")
    local collected=0
    local merged="$cache_dir/posts_merged.tsv"
    : > "$merged"

    while [ "$collected" -lt "$limit" ]; do
        local url
        if [ -n "$before" ]; then
            url="${TG_BASE_URL}/${channel}?before=${before}"
        else
            url="${TG_BASE_URL}/${channel}"
        fi

        local page_file="$cache_dir/raw/page_${before:-latest}.html"

        tg_fetch "$url" > "$page_file"
        if [ ! -s "$page_file" ]; then
            echo "Error: Empty response from $url" >&2
            break
        fi

        local page_tsv="$cache_dir/raw/page_${before:-latest}.tsv"
        parse_posts_from_html "$page_file" "$page_tsv"

        local page_count
        page_count=$(wc -l < "$page_tsv" | tr -d ' ')
        if [ "$page_count" -eq 0 ]; then
            break
        fi

        if [ -n "$after_date" ]; then
            local filtered="$cache_dir/raw/page_${before:-latest}_filtered.tsv"
            awk -F'\t' -v cutoff="$after_date" '$2 >= cutoff { print }' "$page_tsv" > "$filtered"
            local filtered_count
            filtered_count=$(wc -l < "$filtered" | tr -d ' ')
            cat "$filtered" >> "$merged"
            collected=$(( collected + filtered_count ))
            if [ "$filtered_count" -lt "$page_count" ]; then
                break
            fi
        else
            cat "$page_tsv" >> "$merged"
            collected=$(( collected + page_count ))
        fi

        before=$(tail -1 "$page_tsv" | cut -f1)
        if [ -z "$before" ]; then
            break
        fi
    done

    head -n "$limit" "$merged"
}

print_tsv_head() {
    local file="$1"
    local n="${2:-30}"
    if [ -f "$file" ]; then
        head -n "$n" "$file"
        local total
        total=$(wc -l < "$file" | tr -d ' ')
        if [ "$total" -gt "$n" ]; then
            local remaining=$(( total - n ))
            echo "... ${remaining} more rows, full data in: $file"
        fi
    fi
}

normalize_views() {
    local val="$1"
    case "$val" in
        *[Kk]) val="${val%[Kk]}"; echo "$val" | awk '{printf "%d", $1 * 1000}' ;;
        *[Mm]) val="${val%[Mm]}"; echo "$val" | awk '{printf "%d", $1 * 1000000}' ;;
        *)     echo "$val" | sed 's/[^0-9]//g' ;;
    esac
}

parse_common_params() {
    CHANNEL=""
    CHANNELS=""
    LIMIT="20"
    BEFORE=""
    AFTER_DATE=""
    QUERY=""
    SORT_BY="views"
    CSV_OUT=""
    PERIOD=""

    while [ $# -gt 0 ]; do
        case "$1" in
            --channel)     CHANNEL="$2"; shift 2 ;;
            --channels)    CHANNELS="$2"; shift 2 ;;
            --limit)       LIMIT="$2"; shift 2 ;;
            --before)      BEFORE="$2"; shift 2 ;;
            --after-date)  AFTER_DATE="$2"; shift 2 ;;
            --query)       QUERY="$2"; shift 2 ;;
            --sort)        SORT_BY="$2"; shift 2 ;;
            --csv)         CSV_OUT="$2"; shift 2 ;;
            --period)      PERIOD="$2"; shift 2 ;;
            *)             shift ;;
        esac
    done
}

require_channel() {
    if [ -z "$CHANNEL" ]; then
        echo "Error: --channel <username> is required." >&2
        exit 1
    fi
    CHANNEL=$(normalize_channel "$CHANNEL")
}

require_channels() {
    if [ -z "$CHANNELS" ]; then
        # Try default category from .env
        local _cat="${TG_DEFAULT_CATEGORY:-ai}"
        _cat=$(echo "$_cat" | tr '[:lower:]' '[:upper:]')
        eval "_cat_channels=\${TG_CHANNELS_${_cat}:-}"
        if [ -n "$_cat_channels" ]; then
            CHANNELS="$_cat_channels"
        elif [ -n "$TG_CHANNELS" ]; then
            CHANNELS="$TG_CHANNELS"
        else
            echo "Error: --channels required. Copy config/.env.example to config/.env or pass --channels \"ch1,ch2\"." >&2
            exit 1
        fi
    fi
    # Normalize each channel in the list
    local normalized=""
    local IFS=","
    for ch in $CHANNELS; do
        ch=$(normalize_channel "$ch")
        if [ -n "$normalized" ]; then
            normalized="${normalized},${ch}"
        else
            normalized="$ch"
        fi
    done
    CHANNELS="$normalized"
}

period_to_after_date() {
    case "$1" in
        today)     date +%Y-%m-%d ;;
        yesterday) date -d "yesterday" +%Y-%m-%d 2>/dev/null || date -v-1d +%Y-%m-%d ;;
        week)      date -d "7 days ago" +%Y-%m-%d 2>/dev/null || date -v-7d +%Y-%m-%d ;;
        [0-9]*)    date -d "$1 days ago" +%Y-%m-%d 2>/dev/null || date -v-"${1}d" +%Y-%m-%d ;;
        *)         echo "" ;;
    esac
}
