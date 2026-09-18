#!/bin/sh
# Get or refresh IAM token for Yandex Cloud Search API
# Uses JWT PS256 signed with Service Account key via openssl
# Zero external dependencies: python3 stdlib + openssl + curl

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
. "$SCRIPT_DIR/common.sh"

check_prerequisites
load_config

# Check for cached valid token first
_cached=$(get_cached_iam_token)
if [ -n "$_cached" ]; then
    echo "IAM token is valid (cached)."
    exit 0
fi

echo "Generating new IAM token..." >&2

# Read SA key file path from config
SA_KEY_FILE=$(cfg_get "auth.service_account_key_file")
if [ -z "$SA_KEY_FILE" ]; then
    echo "Error: auth.service_account_key_file not set in config.json" >&2
    exit 1
fi

# Resolve relative path from skill dir
case "$SA_KEY_FILE" in
    /*) ;;
    *) SA_KEY_FILE="$SKILL_DIR/$SA_KEY_FILE" ;;
esac

if [ ! -f "$SA_KEY_FILE" ]; then
    echo "Error: Service account key file not found: $SA_KEY_FILE" >&2
    echo "Create it with: yc iam key create --service-account-name <name> --output <path>" >&2
    exit 1
fi

OPENSSL_BIN=$(cfg_get "auth.openssl_bin" "openssl")
check_openssl "$OPENSSL_BIN"

# Create secure temp directory for PEM and JWT files
SECURE_TMP=$(make_secure_tmpdir)
trap 'rm -rf "$SECURE_TMP"' EXIT INT TERM

# make_secure_tmpdir restores the umask before returning, so set it again here:
# key.pem, the JWT parts and openssl's signature must land 0600, not 0644.
# Restored after the last read out of $SECURE_TMP.
OLD_UMASK=$(umask)
umask 077

# Extract SA credentials and create JWT via python3
python3 << PYEOF
import json, base64, time, os

sa_key_file = "$SA_KEY_FILE"
tmp_dir = "$SECURE_TMP"

with open(sa_key_file) as f:
    sa = json.load(f)

sa_id = sa['service_account_id']
key_id = sa['id']
private_key = sa['private_key']

# Write private key to temp PEM file
pem_path = os.path.join(tmp_dir, 'key.pem')
with open(pem_path, 'w') as f:
    f.write(private_key)

# Create JWT header
header = json.dumps({"typ": "JWT", "alg": "PS256", "kid": key_id}, separators=(',', ':'))
header_b64 = base64.urlsafe_b64encode(header.encode()).rstrip(b'=').decode()

# Create JWT payload
now = int(time.time())
payload = json.dumps({
    "iss": sa_id,
    "aud": "https://iam.api.cloud.yandex.net/iam/v1/tokens",
    "iat": now,
    "exp": now + 3600
}, separators=(',', ':'))
payload_b64 = base64.urlsafe_b64encode(payload.encode()).rstrip(b'=').decode()

# Write signing input
signing_input = f"{header_b64}.{payload_b64}"
signing_path = os.path.join(tmp_dir, 'signing_input.txt')
with open(signing_path, 'w') as f:
    f.write(signing_input)

# Write parts for shell to read
with open(os.path.join(tmp_dir, 'header_payload.txt'), 'w') as f:
    f.write(signing_input)
PYEOF

# Sign with openssl PS256
"$OPENSSL_BIN" dgst -sha256 \
    -sigopt rsa_padding_mode:pss \
    -sigopt rsa_pss_saltlen:-1 \
    -sign "$SECURE_TMP/key.pem" \
    -out "$SECURE_TMP/signature.bin" \
    "$SECURE_TMP/signing_input.txt"

# Base64url encode signature
SIGNATURE=$(cat "$SECURE_TMP/signature.bin" | b64url_encode)

# Assemble JWT
HEADER_PAYLOAD=$(cat "$SECURE_TMP/header_payload.txt")
JWT="${HEADER_PAYLOAD}.${SIGNATURE}"

# Done with the key material.
umask "$OLD_UMASK"

# Exchange JWT for IAM token
IAM_RESPONSE=$(http_request "POST" "$IAM_API_URL" \
    "{\"jwt\":\"$JWT\"}" \
    "Content-Type: application/json")

if [ -z "$IAM_RESPONSE" ]; then
    echo "Error: Empty response from IAM API" >&2
    exit 1
fi

# Extract token and expiry
IAM_RESULT=$(echo "$IAM_RESPONSE" | python3 -c "
import json, sys
from datetime import datetime

d = json.load(sys.stdin)
token = d.get('iamToken', '')
expires_at_str = d.get('expiresAt', '')

if not token:
    print('ERROR: No iamToken in response', file=sys.stderr)
    sys.exit(1)

# Parse RFC3339 expiresAt to unix timestamp
if expires_at_str:
    ts = datetime.fromisoformat(expires_at_str.replace('Z', '+00:00')).timestamp()
    expires_at = int(ts)
else:
    # Fallback: assume 12 hours from now
    import time
    expires_at = int(time.time()) + 43200

print(f'{token}|{expires_at}')
")

IAM_TOKEN=$(echo "$IAM_RESULT" | cut -d'|' -f1)
EXPIRES_AT=$(echo "$IAM_RESULT" | cut -d'|' -f2)

if [ -z "$IAM_TOKEN" ]; then
    echo "Error: Failed to extract IAM token" >&2
    echo "Response: $IAM_RESPONSE" >&2
    exit 1
fi

# Save to cache (atomic)
save_iam_token "$IAM_TOKEN" "$EXPIRES_AT"

echo "IAM token generated and cached successfully."
echo "Expires at: $(python3 -c "from datetime import datetime; print(datetime.fromtimestamp($EXPIRES_AT).isoformat())")"
