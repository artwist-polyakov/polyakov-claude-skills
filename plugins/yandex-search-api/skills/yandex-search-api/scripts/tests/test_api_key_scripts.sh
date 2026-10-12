#!/bin/sh
# Exercise sync, async-submit, and async-poll through a fake HTTP transport.

set -eu

TESTS_DIR="$(cd "$(dirname "$0")" && pwd)"
SOURCE_SKILL_DIR="$(cd "$TESTS_DIR/../.." && pwd)"
root="${TMPDIR:-/tmp}/ysa_api_key_scripts_test_$$"
# A quote and a backslash catch values pasted into Python code: they break it,
# and native Windows Python cannot open MSYS paths pasted there anyway.
td="$root/it's\\new"
trap 'rm -rf "$root"' EXIT INT TERM

mkdir -p "$td/skill/scripts" "$td/skill/config" "$td/bin"
for script in common.sh web_search_sync.sh web_search_async.sh; do
    cp "$SOURCE_SKILL_DIR/scripts/$script" "$td/skill/scripts/"
done
# No cache directory: exercise a first run on a fresh installation.

cat > "$td/skill/config/config.json" <<'EOF'
{
  "yandex_cloud_folder_id": "b1g-test-folder",
  "auth": {"mode": "api_key"},
  "search": {"region_id": "225", "results_per_page": 1},
  "async": {"poll_interval_minutes": 0, "max_wait_minutes": 1}
}
EOF
cat > "$td/skill/config/.env" <<'EOF'
YANDEX_AI_API_KEY=test-api-secret
EOF

cat > "$td/skill/scripts/iam_token_get.sh" <<'EOF'
#!/bin/sh
echo called > "${IAM_MARKER:?}"
exit 97
EOF
chmod +x "$td/skill/scripts/iam_token_get.sh"

FAKE_RAW_B64=$(python3 - <<'PY'
import base64
xml = b'''<yandexsearch><response><results><grouping><group><doc>
<url>https://example.com/</url><title>Example result</title>
<passages><passage>Example snippet</passage></passages>
<domain>example.com</domain></doc></group></grouping></results></response></yandexsearch>'''
print(base64.b64encode(xml).decode())
PY
)

cat > "$td/bin/curl" <<'EOF'
#!/bin/sh
response_file=""
headers_file=""
body_file=""
url=""

{
    echo '--- request ---'
    printf '%s\n' "$@"
} >> "${CURL_CAPTURE:?}"

while [ "$#" -gt 0 ]; do
    case "$1" in
        -o)
            response_file="$2"
            shift 2
            ;;
        -D)
            headers_file="$2"
            shift 2
            ;;
        --data-binary)
            body_file="${2#@}"
            shift 2
            ;;
        http*)
            url="$1"
            shift
            ;;
        *)
            shift
            ;;
    esac
done

case "$url" in
    */v2/web/searchAsync)
        if grep -q 'failing query' "$body_file"; then
            printf '{"id":"op-fail"}' > "$response_file"
        else
            printf '{"id":"op-test"}' > "$response_file"
        fi
        ;;
    */operations/op-test)
        printf '{"done":true,"response":{"rawData":"%s"}}' "$FAKE_RAW_B64" > "$response_file"
        ;;
    */operations/op-fail)
        printf '%s' "$FAKE_OP_ERROR" > "$response_file"
        ;;
    */v2/web/search)
        printf '{"rawData":"%s"}' "$FAKE_RAW_B64" > "$response_file"
        ;;
    *)
        printf '{"code":3,"message":"unexpected fake URL"}' > "$response_file"
        : > "$headers_file"
        printf '400'
        exit 0
        ;;
esac

: > "$headers_file"
printf '200'
EOF
chmod +x "$td/bin/curl"

# The quote must reach the operation record intact instead of breaking it.
FAKE_OP_ERROR='{"done":true,"error":{"message":"Can'\''t parse \"query\""}}'
CURL_CAPTURE="$td/curl-args"
IAM_MARKER="$td/iam-called"
PATH="$td/bin:$PATH"
export CURL_CAPTURE IAM_MARKER FAKE_RAW_B64 FAKE_OP_ERROR PATH

sh "$td/skill/scripts/web_search_sync.sh" \
    --query 'example smoke query' --results 1 >/dev/null

printf '%s\n' 'example smoke query' 'failing query' > "$td/queries.txt"
sh "$td/skill/scripts/web_search_async.sh" \
    --file "$td/queries.txt" --results 1 --poll-interval 0 --max-wait 1 >/dev/null

# shellcheck disable=SC1091
. "$TESTS_DIR/helpers.sh"
export _YSA_T_ERROR="Can't parse \"query\""
check "$td/skill/cache/ops/op-fail.json" \
    'data["status"] == "error" and data["error"] == os.environ["_YSA_T_ERROR"]' \
    'FAIL: the failed operation was not recorded with its error message'

[ ! -e "$IAM_MARKER" ] || {
    echo 'FAIL: an API-key script invoked IAM token generation'
    exit 1
}
if grep -Fq 'Authorization: Bearer' "$CURL_CAPTURE"; then
    echo 'FAIL: a script sent Bearer authorization in API-key mode'
    exit 1
fi
api_key_count=$(grep -Fc 'Authorization: Api-Key test-api-secret' "$CURL_CAPTURE")
[ "$api_key_count" -eq 5 ] || {
    echo "FAIL: expected 5 API-key requests, got $api_key_count"
    exit 1
}

result_file=$(find "$td/skill/cache/results" -name '*.json' -type f | head -n 1)
[ -n "$result_file" ] || {
    echo 'FAIL: scripts produced no parsed result file'
    exit 1
}
grep -Fq 'https://example.com/' "$result_file" || {
    echo 'FAIL: parsed result does not contain the fake search document'
    exit 1
}

echo PASS
