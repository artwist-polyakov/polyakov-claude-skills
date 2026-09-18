#!/bin/sh
# Подмена curl для автономных проверок zoomkit.sh.

set -e

METHOD=GET
HEADERS_FILE=""
OUTPUT_FILE=""
AUTH_HEADER=""
ACCEPT_HEADER=""
CONTENT_TYPE=""
DATA_FILE=""
URL=""
CURLRC_DISABLED=0
CURLRC_FIRST=0
REQUEST_HEADERS_MODE=""
CONNECT_TIMEOUT=""
MAX_TIME=""

if [ "${1:-}" = -q ]; then
    CURLRC_FIRST=1
fi

while [ $# -gt 0 ]; do
    case "$1" in
        -q)
            CURLRC_DISABLED=1
            shift
            ;;
        --silent|--show-error)
            shift
            ;;
        --connect-timeout)
            CONNECT_TIMEOUT=$2
            shift 2
            ;;
        --max-time)
            MAX_TIME=$2
            shift 2
            ;;
        --request)
            METHOD=$2
            shift 2
            ;;
        --dump-header)
            HEADERS_FILE=$2
            shift 2
            ;;
        --output)
            OUTPUT_FILE=$2
            shift 2
            ;;
        --header)
            case "$2" in
                @*)
                    HEADER_SOURCE=${2#@}
                    if REQUEST_HEADERS_MODE=$(stat -f '%Lp' "$HEADER_SOURCE" 2>/dev/null); then
                        :
                    elif REQUEST_HEADERS_MODE=$(stat -c '%a' "$HEADER_SOURCE" 2>/dev/null); then
                        :
                    fi
                    while IFS= read -r HEADER_LINE || [ -n "$HEADER_LINE" ]; do
                        case "$HEADER_LINE" in
                            Authorization:*) AUTH_HEADER=$HEADER_LINE ;;
                            Accept:*) ACCEPT_HEADER=$HEADER_LINE ;;
                            Content-Type:*) CONTENT_TYPE=$HEADER_LINE ;;
                        esac
                    done < "$HEADER_SOURCE"
                    ;;
                Authorization:*) AUTH_HEADER=$2 ;;
                Accept:*) ACCEPT_HEADER=$2 ;;
                Content-Type:*) CONTENT_TYPE=$2 ;;
            esac
            shift 2
            ;;
        --data-binary)
            DATA_FILE=${2#@}
            shift 2
            ;;
        http://*|https://*)
            URL=$1
            shift
            ;;
        *)
            printf 'fake_curl: неизвестный параметр: %s\n' "$1" >&2
            exit 64
            ;;
    esac
done

[ -n "$HEADERS_FILE" ] || exit 65
[ -n "$OUTPUT_FILE" ] || exit 66
[ -n "$URL" ] || exit 67

STATUS=${MOCK_STATUS:-200}
printf 'HTTP/1.1 %s Mock\r\n' "$STATUS" > "$HEADERS_FILE"
printf 'Content-Type: application/json\r\n' >> "$HEADERS_FILE"
if [ -n "${MOCK_RATE_RESET:-}" ]; then
    printf 'X-RateLimit-Reset: %s\r\n' "$MOCK_RATE_RESET" >> "$HEADERS_FILE"
fi
printf '\r\n' >> "$HEADERS_FILE"

SEQUENCE_USED=0
if [ -n "${MOCK_REPORT_SEQUENCE_DIR:-}" ]; then
    case "$URL" in
        */stats/reports/[0-9]*)
            SEQUENCE_COUNTER=${MOCK_REPORT_COUNTER_FILE:-"$MOCK_REPORT_SEQUENCE_DIR/counter"}
            SEQUENCE_NUMBER=0
            if [ -f "$SEQUENCE_COUNTER" ]; then
                SEQUENCE_NUMBER=$(sed -n '1p' "$SEQUENCE_COUNTER")
            fi
            case "$SEQUENCE_NUMBER" in
                ''|*[!0-9]*) SEQUENCE_NUMBER=0 ;;
            esac
            SEQUENCE_NUMBER=$((SEQUENCE_NUMBER + 1))
            printf '%s\n' "$SEQUENCE_NUMBER" > "$SEQUENCE_COUNTER"
            SEQUENCE_FILE="$MOCK_REPORT_SEQUENCE_DIR/$SEQUENCE_NUMBER.json"
            [ -f "$SEQUENCE_FILE" ] || {
                printf 'fake_curl: нет ответа последовательности %s\n' "$SEQUENCE_FILE" >&2
                exit 68
            }
            cp "$SEQUENCE_FILE" "$OUTPUT_FILE"
            SEQUENCE_USED=1
            ;;
    esac
fi

if [ "$SEQUENCE_USED" -eq 1 ]; then
    :
elif [ -n "${MOCK_BODY_FILE:-}" ]; then
    cp "$MOCK_BODY_FILE" "$OUTPUT_FILE"
else
    case "$URL" in
        */token)
            printf '%s\n' '{"id":"key-id","name":"Проверка","expires_at":"2027-01-10T00:00:00+03:00","expires_in_days":100,"expires_soon":false}' > "$OUTPUT_FILE"
            ;;
        */billing/balance)
            printf '%s\n' '{"balance":4300,"daily_tariff":116,"days_left":37}' > "$OUTPUT_FILE"
            ;;
        */billing/invoices)
            printf '%s\n' '[{"id":2,"number":"ZK/2","created_at":"2026-09-01T10:00:00+03:00","amount":7000,"pay_amount":7000,"status":"wait"},{"id":1,"number":"ZK/1","created_at":"2026-08-01T10:00:00+03:00","amount":5000,"pay_amount":5000,"status":"paid"}]' > "$OUTPUT_FILE"
            ;;
        *)
            printf '%s\n' '{"ok":true}' > "$OUTPUT_FILE"
            ;;
    esac
fi

if [ -n "${MOCK_LOG:-}" ]; then
    {
        printf 'METHOD=%s\n' "$METHOD"
        printf 'URL=%s\n' "$URL"
        printf 'AUTH=%s\n' "$AUTH_HEADER"
        printf 'ACCEPT=%s\n' "$ACCEPT_HEADER"
        printf 'CONTENT_TYPE=%s\n' "$CONTENT_TYPE"
        printf 'DATA_FILE=%s\n' "$DATA_FILE"
        printf 'CURLRC_DISABLED=%s\n' "$CURLRC_DISABLED"
        printf 'CURLRC_FIRST=%s\n' "$CURLRC_FIRST"
        printf 'REQUEST_HEADERS_MODE=%s\n' "$REQUEST_HEADERS_MODE"
        printf 'CONNECT_TIMEOUT=%s\n' "$CONNECT_TIMEOUT"
        printf 'MAX_TIME=%s\n' "$MAX_TIME"
        if [ -n "$DATA_FILE" ] && [ -f "$DATA_FILE" ]; then
            printf 'DATA='
            tr -d '\n\r' < "$DATA_FILE"
            printf '\n'
        fi
    } > "$MOCK_LOG"
fi
