#!/bin/sh
# Issue and cache an IAM token with the real iam_token_get.sh, openssl and curl
# stubbed. Every path contains a quote and a backslash: values pasted into
# Python code break on them, native Windows Python cannot open MSYS paths
# pasted there anyway, and echo in dash or macOS sh mangles the backslash.

set -eu

TESTS_DIR="$(cd "$(dirname "$0")" && pwd)"
SOURCE_SCRIPTS_DIR="$(cd "$TESTS_DIR/.." && pwd)"
root=$(mktemp -d "${TMPDIR:-/tmp}/ysa_iam_test.XXXXXX")
trap 'rm -rf "$root"' EXIT INT TERM
td="$root/it's\\new"

mkdir -p "$td/skill/scripts" "$td/skill/config" "$td/skill/cache" "$td/bin" "$td/tmp"
cp "$SOURCE_SCRIPTS_DIR/common.sh" "$SOURCE_SCRIPTS_DIR/iam_token_get.sh" "$td/skill/scripts/"

cat > "$td/skill/config/config.json" <<'EOF'
{
  "yandex_cloud_folder_id": "b1g-test-folder",
  "auth": {"service_account_key_file": "config/service-account.json"}
}
EOF
cat > "$td/skill/config/service-account.json" <<'EOF'
{"id": "test-key-id", "service_account_id": "test-sa-id", "private_key": "test-private-key"}
EOF

cat > "$td/bin/openssl" <<'EOF'
#!/bin/sh
[ "$1" = version ] && { echo "OpenSSL 3.0.0 test-stub"; exit 0; }
while [ "$#" -gt 0 ]; do
    [ "$1" = -out ] && printf 'test-signature' > "$2"
    shift
done
exit 0
EOF

cat > "$td/bin/curl" <<'EOF'
#!/bin/sh
while [ "$#" -gt 0 ]; do
    case "$1" in
        -o) printf '{"iamToken":"test-iam-token","expiresAt":"2099-01-01T00:00:00Z"}' > "$2"; shift 2 ;;
        -D) : > "$2"; shift 2 ;;
        *) shift ;;
    esac
done
printf 200
EOF
chmod +x "$td/bin/openssl" "$td/bin/curl"

PATH="$td/bin:$PATH"
TMPDIR="$td/tmp"
export PATH TMPDIR

sh "$td/skill/scripts/iam_token_get.sh" > "$td/issue.out" 2>&1 || {
    cat "$td/issue.out"
    echo 'FAIL: token issue failed'
    exit 1
}
grep -Fq '"iam_token": "test-iam-token"' "$td/skill/cache/iam_token.json" || {
    echo 'FAIL: issued token was not cached'
    exit 1
}

sh "$td/skill/scripts/iam_token_get.sh" > "$td/cached.out" 2>&1
grep -Fq 'IAM token is valid (cached).' "$td/cached.out" || {
    echo 'FAIL: cached token was not reused'
    exit 1
}

echo PASS
