#!/bin/sh
# Verify API-key auth at the real auth_request boundary without network access.

set -eu

TESTS_DIR="$(cd "$(dirname "$0")" && pwd)"
SOURCE_SCRIPTS_DIR="$(cd "$TESTS_DIR/.." && pwd)"
td="${TMPDIR:-/tmp}/ysa_api_key_test_$$"
trap 'rm -rf "$td"' EXIT INT TERM

mkdir -p "$td/skill/scripts" "$td/skill/config" "$td/skill/cache" "$td/bin"
cp "$SOURCE_SCRIPTS_DIR/common.sh" "$td/skill/scripts/common.sh"

cat > "$td/skill/config/config.json" <<'EOF'
{
  "yandex_cloud_folder_id": "b1g-test-folder",
  "auth": {"mode": "api_key"}
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

cat > "$td/bin/curl" <<'EOF'
#!/bin/sh
response_file=""
headers_file=""

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
        *)
            shift
            ;;
    esac
done

printf '{}' > "$response_file"
: > "$headers_file"
printf '200'
EOF
chmod +x "$td/bin/curl"

cat > "$td/skill/scripts/harness.sh" <<'EOF'
#!/bin/sh
set -eu
. "$(dirname "$0")/common.sh"
load_config
auth_request POST 'https://searchapi.api.cloud.yandex.net/v2/web/search' '{}'
EOF
chmod +x "$td/skill/scripts/harness.sh"

CURL_CAPTURE="$td/curl-args"
IAM_MARKER="$td/iam-called"
PATH="$td/bin:$PATH"
export CURL_CAPTURE IAM_MARKER PATH

sh "$td/skill/scripts/harness.sh" >/dev/null

grep -Fxq 'Authorization: Api-Key test-api-secret' "$CURL_CAPTURE" || {
    echo 'FAIL: API-key header was not sent'
    exit 1
}
if grep -Fq 'Authorization: Bearer' "$CURL_CAPTURE"; then
    echo 'FAIL: Bearer header leaked into API-key mode'
    exit 1
fi
[ ! -e "$IAM_MARKER" ] || {
    echo 'FAIL: API-key mode invoked IAM token generation'
    exit 1
}
[ ! -e "$td/skill/cache/iam_token.json" ] || {
    echo 'FAIL: API-key mode created an IAM token cache'
    exit 1
}

rm "$td/skill/config/.env" "$CURL_CAPTURE"
if sh "$td/skill/scripts/harness.sh" >"$td/missing-key.out" 2>&1; then
    echo 'FAIL: explicit API-key mode succeeded without a key'
    exit 1
fi
grep -Fq 'auth.mode=api_key requires YANDEX_AI_API_KEY' "$td/missing-key.out" || {
    echo 'FAIL: missing API-key diagnostic is not actionable'
    exit 1
}
[ ! -e "$CURL_CAPTURE" ] || {
    echo 'FAIL: missing API key reached HTTP transport'
    exit 1
}
[ ! -e "$IAM_MARKER" ] || {
    echo 'FAIL: missing API key fell back to IAM'
    exit 1
}

cat > "$td/skill/config/config.json" <<'EOF'
{
  "yandex_cloud_folder_id": "b1g-test-folder",
  "auth": {
    "mode": "iam",
    "service_account_key_file": "config/service-account.json"
  }
}
EOF
cat > "$td/skill/cache/iam_token.json" <<'EOF'
{"iam_token":"test-iam-secret","expires_at":4102444800}
EOF
rm -f "$IAM_MARKER" "$CURL_CAPTURE"

sh "$td/skill/scripts/harness.sh" >/dev/null

grep -Fxq 'Authorization: Bearer test-iam-secret' "$CURL_CAPTURE" || {
    echo 'FAIL: IAM mode no longer sends the cached Bearer token'
    exit 1
}
if grep -Fq 'Authorization: Api-Key' "$CURL_CAPTURE"; then
    echo 'FAIL: API-key header leaked into IAM mode'
    exit 1
fi
[ ! -e "$IAM_MARKER" ] || {
    echo 'FAIL: valid cached IAM token triggered regeneration'
    exit 1
}

echo 'test_api_key_auth: all passed'
