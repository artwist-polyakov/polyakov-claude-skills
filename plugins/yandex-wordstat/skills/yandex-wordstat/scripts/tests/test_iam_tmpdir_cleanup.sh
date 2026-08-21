#!/bin/sh
# Regression test: issuing an IAM token must not leave the service-account
# private key behind in $TMPDIR.
#
# The original bug: _save_iam_token assigned the unprefixed global $_tmp, so by
# the time _iam_token_issue ran `rm -rf "$_tmp"` the variable no longer pointed
# at the secure temp directory, and the `trap - EXIT INT TERM` on the next line
# disarmed the trap that still held the correct path. Every token issue left a
# $TMPDIR/wordstat_XXXXXX/key.pem on disk forever.
#
# No network: openssl and curl are replaced by stubs on PATH.

set -e

TESTS_DIR="$(cd "$(dirname "$0")" && pwd)"
SCRIPTS_DIR="$(cd "$TESTS_DIR/.." && pwd)"
SKILL_DIR="$(cd "$SCRIPTS_DIR/.." && pwd)"

# Sandbox: our own TMPDIR so we count only dirs this test creates, and the
# user's real $TMPDIR is never touched.
SANDBOX=$(mktemp -d "${TMPDIR:-/tmp}/wsiamtest_XXXXXX")
trap 'rm -rf "$SANDBOX"' EXIT INT TERM

TMPDIR="$SANDBOX/tmp"
mkdir -p "$TMPDIR"
export TMPDIR

BIN="$SANDBOX/bin"
mkdir -p "$BIN"

# --- stub openssl -----------------------------------------------------------
# `version` must not look like LibreSSL/1.0 or _check_openssl dies.
# `dgst` records the mode of key.pem (so we can assert 0600 while the file
# still exists) and writes a fake signature to the -out path.
cat > "$BIN/openssl" <<'STUB'
#!/bin/sh
case "$1" in
    version) echo "OpenSSL 3.0.0 test-stub"; exit 0 ;;
esac
_out=""
_key=""
while [ $# -gt 0 ]; do
    case "$1" in
        -out)   _out="$2"; shift 2 ;;
        -sign)  _key="$2"; shift 2 ;;
        *)      shift ;;
    esac
done
[ -n "$_key" ] && ls -l "$_key" | cut -d' ' -f1 > "$WS_TEST_KEYMODE_FILE"
printf 'fake-signature-bytes' > "$_out"
exit 0
STUB
chmod +x "$BIN/openssl"

# --- stub curl --------------------------------------------------------------
cat > "$BIN/curl" <<'STUB'
#!/bin/sh
# _iam_token_issue posts the JWT and expects {"iamToken":..,"expiresAt":..}
echo '{"iamToken":"test-iam-token-xyz","expiresAt":"2099-01-01T00:00:00Z"}'
exit 0
STUB
chmod +x "$BIN/curl"

PATH="$BIN:$PATH"
export PATH

WS_TEST_KEYMODE_FILE="$SANDBOX/keymode"
export WS_TEST_KEYMODE_FILE

# --- fake SA key ------------------------------------------------------------
# openssl is stubbed, so the private_key body only has to be a string.
cat > "$SANDBOX/sa_key.json" <<'SAKEY'
{
  "id": "ajetestkeyid000000000",
  "service_account_id": "ajetestsaid0000000000",
  "private_key": "-----BEGIN PRIVATE KEY-----\nTEST-NOT-A-REAL-KEY\n-----END PRIVATE KEY-----\n"
}
SAKEY

# --- load common.sh with the cache pointed at the sandbox -------------------
WORDSTAT_SCRIPT_DIR="$SCRIPTS_DIR"
WORDSTAT_SKILL_DIR="$SKILL_DIR"
WORDSTAT_CACHE_DIR="$SANDBOX/cache"
export WORDSTAT_SCRIPT_DIR WORDSTAT_SKILL_DIR WORDSTAT_CACHE_DIR
# shellcheck disable=SC1091
. "$SCRIPTS_DIR/common.sh"

WORDSTAT_BACKEND="cloud"
WORDSTAT_CLOUD_FOLDER_ID="b1g-test-folder"
WORDSTAT_CLOUD_SA_KEY_PATH="$SANDBOX/sa_key.json"
WORDSTAT_CLOUD_OPENSSL_BIN="$BIN/openssl"
WORDSTAT_IAM_API="http://127.0.0.1:0/stubbed"

# find, not a glob: zsh aborts on an unmatched glob, and the harness should
# run under whatever /bin/sh (or shell) the caller has.
count_leftovers() {
    find "$TMPDIR" -maxdepth 1 -name 'wordstat_*' 2>/dev/null | wc -l | tr -d ' \n'
}

fail() {
    echo "  FAIL: $1"
    echo "  leftovers in $TMPDIR:"
    ls -la "$TMPDIR" || true
    exit 1
}

# --- 1. direct call: _iam_token_issue must clean up ------------------------
before=$(count_leftovers)
tok=$(_iam_token_issue)
after=$(count_leftovers)

[ "$tok" = "test-iam-token-xyz" ] || fail "expected stub token, got '$tok'"
[ "$before" = "0" ] || fail "sandbox TMPDIR was not clean before the call ($before)"
[ "$after" = "0" ] || fail "_iam_token_issue leaked $after wordstat_* dir(s) holding key.pem"
echo "  ok: _iam_token_issue leaves no wordstat_* dir"

# --- 2. the key.pem it wrote was 0600, not 0644 ----------------------------
mode=$(cat "$WS_TEST_KEYMODE_FILE" 2>/dev/null || echo "")
case "$mode" in
    -rw-------*) echo "  ok: key.pem was 0600 while on disk" ;;
    "")          fail "openssl stub never saw key.pem" ;;
    *)           fail "key.pem was '$mode', expected -rw------- (umask 077 must stay in effect)" ;;
esac

# --- 3. the cached token file is 0600 --------------------------------------
# _save_iam_token now runs after _iam_token_issue restores its umask, so it
# must still be setting umask 077 itself.
cache_mode=$(ls -l "$WORDSTAT_CACHE_DIR/iam_token.json" | cut -d' ' -f1)
case "$cache_mode" in
    -rw-------*) echo "  ok: cached iam_token.json is 0600" ;;
    *)           fail "cached iam_token.json was '$cache_mode', expected -rw-------" ;;
esac

# --- 4. production path: _iam_token_get inside a command substitution -------
# This is how _cloud_request calls it. The cold-cache branch runs
# _iam_token_issue inside a subshell, where the EXIT trap behaves differently.
rm -rf "$WORDSTAT_CACHE_DIR"
tok2=$(_iam_token_get)
after2=$(count_leftovers)

[ "$tok2" = "test-iam-token-xyz" ] || fail "expected stub token from _iam_token_get, got '$tok2'"
[ "$after2" = "0" ] || fail "_iam_token_get (cold cache) leaked $after2 wordstat_* dir(s)"
echo "  ok: _iam_token_get with a cold cache leaves no wordstat_* dir"

# --- 5. warm cache issues nothing and still leaks nothing -------------------
tok3=$(_iam_token_get)
after3=$(count_leftovers)
[ "$tok3" = "test-iam-token-xyz" ] || fail "warm cache returned '$tok3'"
[ "$after3" = "0" ] || fail "_iam_token_get (warm cache) leaked $after3 wordstat_* dir(s)"
echo "  ok: _iam_token_get with a warm cache leaves no wordstat_* dir"

# --- 6. error path: the trap must still clean up ----------------------------
# Make the IAM exchange fail (curl returns empty) -> die_with_help -> exit 1
# inside the command substitution's subshell. The EXIT trap is the only thing
# that can remove the directory there.
cat > "$BIN/curl" <<'STUB'
#!/bin/sh
exit 0
STUB
chmod +x "$BIN/curl"

rm -rf "$WORDSTAT_CACHE_DIR"
tok4=$( _iam_token_get 2>/dev/null ) || true
after4=$(count_leftovers)
[ -z "$tok4" ] || fail "expected no token on the failure path, got '$tok4'"
[ "$after4" = "0" ] || fail "failure path leaked $after4 wordstat_* dir(s) holding key.pem"
echo "  ok: failing token issue leaves no wordstat_* dir (EXIT trap fires)"

echo "  all IAM tmpdir cleanup assertions passed"
