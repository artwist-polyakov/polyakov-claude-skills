#!/bin/sh
# Get Telegraph page views, optionally for a calendar period
# Usage: sh page_views.sh --path "Page-Title-03-09" [--year YYYY [--month M [--day D [--hour H]]]]

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
. "$SCRIPT_DIR/common.sh"

usage() {
    echo "Usage: sh page_views.sh --path PATH [--year YYYY [--month M [--day D [--hour H]]]]"
}

require_value() {
    _rv_option="$1"
    _rv_value="${2-}"
    case "$_rv_value" in
        ""|--*)
            echo "Error: $_rv_option requires a value." >&2
            usage >&2
            exit 1
            ;;
    esac
}

validate_integer_range() {
    _vir_name="$1"
    _vir_value="$2"
    _vir_min="$3"
    _vir_max="$4"

    case "$_vir_value" in
        *[!0-9]*)
            echo "Error: $_vir_name must be an integer." >&2
            exit 1
            ;;
    esac

    # Avoid overflowing the shell's integer comparison on malformed input.
    if [ "${#_vir_value}" -gt "${#_vir_max}" ]; then
        echo "Error: $_vir_name must be between $_vir_min and $_vir_max." >&2
        exit 1
    fi

    if [ "$_vir_value" -lt "$_vir_min" ] || [ "$_vir_value" -gt "$_vir_max" ]; then
        echo "Error: $_vir_name must be between $_vir_min and $_vir_max." >&2
        exit 1
    fi
}

PAGE_PATH=""
YEAR=""
MONTH=""
DAY=""
HOUR=""

while [ $# -gt 0 ]; do
    case "$1" in
        --path)
            require_value "$1" "${2-}"
            PAGE_PATH="$2"
            shift 2
            ;;
        --year)
            require_value "$1" "${2-}"
            YEAR="$2"
            shift 2
            ;;
        --month)
            require_value "$1" "${2-}"
            MONTH="$2"
            shift 2
            ;;
        --day)
            require_value "$1" "${2-}"
            DAY="$2"
            shift 2
            ;;
        --hour)
            require_value "$1" "${2-}"
            HOUR="$2"
            shift 2
            ;;
        -h|--help)
            usage
            exit 0
            ;;
        *)
            echo "Error: Unknown argument: $1" >&2
            usage >&2
            exit 1
            ;;
    esac
done

if [ -z "$PAGE_PATH" ]; then
    echo "Error: --path is required." >&2
    usage >&2
    exit 1
fi

if [ -n "$MONTH" ] && [ -z "$YEAR" ]; then
    echo "Error: --month requires --year." >&2
    exit 1
fi
if [ -n "$DAY" ] && [ -z "$MONTH" ]; then
    echo "Error: --day requires --month." >&2
    exit 1
fi
if [ -n "$HOUR" ] && [ -z "$DAY" ]; then
    echo "Error: --hour requires --day." >&2
    exit 1
fi

[ -z "$YEAR" ] || validate_integer_range "year" "$YEAR" 2000 2100
[ -z "$MONTH" ] || validate_integer_range "month" "$MONTH" 1 12
[ -z "$DAY" ] || validate_integer_range "day" "$DAY" 1 31
# Telegraph rejects 24 even though its API documentation lists 0-24.
[ -z "$HOUR" ] || validate_integer_range "hour" "$HOUR" 0 23

check_prerequisites

# Telegraph accepts path as a parameter or an endpoint suffix. Form encoding
# keeps the user-provided public page path out of the request URL.
set -- --data-urlencode "path=$PAGE_PATH"
[ -z "$YEAR" ] || set -- "$@" --data-urlencode "year=$YEAR"
[ -z "$MONTH" ] || set -- "$@" --data-urlencode "month=$MONTH"
[ -z "$DAY" ] || set -- "$@" --data-urlencode "day=$DAY"
[ -z "$HOUR" ] || set -- "$@" --data-urlencode "hour=$HOUR"

_result=$(telegraph_post "getViews" "$@")
printf '%s\n' "$_result" | python3 "$SCRIPT_DIR/parse_response.py" page_views
