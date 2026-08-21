#!/bin/sh
# Common functions for Yandex Search API
# Zero external dependencies: python3 stdlib + openssl + curl

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
SKILL_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
CONFIG_FILE="$SKILL_DIR/config/config.json"
CACHE_DIR="$SKILL_DIR/cache"
SEARCH_API_URL="https://searchapi.api.cloud.yandex.net"
IAM_API_URL="https://iam.api.cloud.yandex.net/iam/v1/tokens"
OPERATION_API_URL="https://operation.api.cloud.yandex.net/operations"

# --- Prerequisites check ---

check_python3() {
    if ! command -v python3 >/dev/null 2>&1; then
        echo "Error: python3 is required but not found." >&2
        echo "Install Python 3.7+ and ensure python3 is in PATH." >&2
        exit 1
    fi
}

check_openssl() {
    _ossl_bin="${1:-openssl}"
    if ! command -v "$_ossl_bin" >/dev/null 2>&1; then
        echo "Error: openssl not found at '$_ossl_bin'." >&2
        echo "Install OpenSSL 1.1.1+ or set auth.openssl_bin in config.json." >&2
        exit 1
    fi
    # Check version (need 1.1.1+ for PSS support)
    _ossl_ver="$("$_ossl_bin" version 2>/dev/null || true)"
    case "$_ossl_ver" in
        LibreSSL*)
            echo "Error: LibreSSL detected ($_ossl_ver). OpenSSL 1.1.1+ required for PS256." >&2
            echo "Install OpenSSL via: brew install openssl@3" >&2
            exit 1
            ;;
        "OpenSSL 0."*|"OpenSSL 1.0."*)
            echo "Error: OpenSSL version too old ($_ossl_ver). Need 1.1.1+." >&2
            exit 1
            ;;
    esac
}

check_curl() {
    if ! command -v curl >/dev/null 2>&1; then
        echo "Error: curl is required but not found." >&2
        exit 1
    fi
}

# Run all prerequisite checks
check_prerequisites() {
    check_python3
    check_curl
}

# --- Config loading (JSON via python3) ---

load_config() {
    if [ ! -f "$CONFIG_FILE" ]; then
        echo "Error: config.json not found at $CONFIG_FILE" >&2
        echo "Copy config.example.json to config.json and fill in your values." >&2
        echo "See config/README.md for instructions." >&2
        exit 1
    fi
}

# Read a value from config.json
# Usage: cfg_get "yandex_cloud_folder_id"
# Usage: cfg_get "auth.service_account_key_file"
# Usage: cfg_get "search.region_id" "225"  (with default)
cfg_get() {
    _key="$1"
    _default="${2:-}"
    _val=$(python3 -c "
import json, sys
with open('$CONFIG_FILE') as f:
    cfg = json.load(f)
keys = '$_key'.split('.')
v = cfg
for k in keys:
    if isinstance(v, dict) and k in v:
        v = v[k]
    else:
        v = None
        break
if v is None:
    d = '$_default'
    print(d if d else '')
else:
    print(v)
" 2>/dev/null)
    echo "$_val"
}

# --- Temp file management ---

# Create secure temp directory (0700)
# Usage: _tmpdir=$(make_secure_tmpdir)
# The umask is restored before returning, so the directory is 0700 but files
# written into it follow the caller's umask. A caller that puts secrets there
# must set its own umask 077 around those writes (see iam_token_get.sh).
# POSIX sh has no locals: variables are prefixed to avoid clobbering a
# caller's variable of the same name.
make_secure_tmpdir() {
    _mstd_old_umask=$(umask)
    umask 077
    _mstd_td=$(mktemp -d "${TMPDIR:-/tmp}/ysa_XXXXXX")
    umask "$_mstd_old_umask"
    echo "$_mstd_td"
}

# --- JSON helpers (via python3) ---

# Extract field from JSON file
# Usage: json_file_get "file.json" "field.nested"
json_file_get() {
    _file="$1"
    _key="$2"
    python3 -c "
import json, sys
with open('$_file') as f:
    d = json.load(f)
keys = '$_key'.split('.')
v = d
for k in keys:
    if isinstance(v, dict) and k in v:
        v = v[k]
    else:
        print('')
        sys.exit(0)
print(v if v is not None else '')
" 2>/dev/null
}

# Extract field from JSON string on stdin
# Usage: echo '{"a":1}' | json_stdin_get "a"
json_stdin_get() {
    _key="$1"
    python3 -c "
import json, sys
d = json.load(sys.stdin)
keys = '$_key'.split('.')
v = d
for k in keys:
    if isinstance(v, dict) and k in v:
        v = v[k]
    else:
        print('')
        sys.exit(0)
print(v if v is not None else '')
"
}

# --- Base64 helpers (python3 stdlib, cross-platform) ---

# Base64 decode from stdin to stdout (binary)
b64_decode() {
    python3 -c "
import base64, sys
data = sys.stdin.read()
sys.stdout.buffer.write(base64.b64decode(data))
"
}

# Base64url encode from stdin to stdout (no padding)
b64url_encode() {
    python3 -c "
import base64, sys
data = sys.stdin.buffer.read()
print(base64.urlsafe_b64encode(data).rstrip(b'=').decode())
"
}

# --- HTTP helpers with retry ---

# HTTP request with retry (3 attempts, exponential backoff)
# Usage: http_request "POST" "url" "body_file_or_empty" "header1" "header2" ...
# body is passed via temp file to avoid shell injection
# Writes response to stdout, returns 0 on success, 1 on error
http_request() {
    _method="$1"
    _url="$2"
    _body="$3"
    shift 3

    _max_retries=3
    _attempt=0
    _backoff=2

    # Save headers to a persistent temp file BEFORE retry loop
    # so that set -- inside the loop doesn't lose them
    _hr_tmpdir=$(make_secure_tmpdir)
    _hr_headers_file="$_hr_tmpdir/headers_saved"
    : > "$_hr_headers_file"
    for _h in "$@"; do
        printf '%s\n' "$_h" >> "$_hr_headers_file"
    done

    while [ "$_attempt" -lt "$_max_retries" ]; do
        _attempt=$((_attempt + 1))

        _tmpdir_http=$(make_secure_tmpdir)
        _resp_file="$_tmpdir_http/response"
        _header_file="$_tmpdir_http/headers"
        _body_file="$_tmpdir_http/body"

        # Write body to temp file to avoid shell quoting issues
        if [ -n "$_body" ]; then
            printf '%s' "$_body" > "$_body_file"
        fi

        # Build curl args via set -- to avoid word splitting on spaces in headers
        set -- curl -s -w '%{http_code}' -o "$_resp_file" -D "$_header_file" -X "$_method"
        while IFS= read -r _hline; do
            set -- "$@" -H "$_hline"
        done < "$_hr_headers_file"
        if [ -n "$_body" ]; then
            set -- "$@" --data-binary "@$_body_file"
        fi
        set -- "$@" "$_url"

        _status=$("$@" 2>/dev/null) || _status="000"

        case "$_status" in
            2[0-9][0-9])
                cat "$_resp_file"
                rm -rf "$_tmpdir_http" "$_hr_tmpdir"
                return 0
                ;;
            401)
                cat "$_resp_file"
                rm -rf "$_tmpdir_http" "$_hr_tmpdir"
                return 1
                ;;
            403)
                echo "Error: 403 Forbidden. Check:" >&2
                echo "  - Role 'search-api.webSearch.user' assigned to SA" >&2
                echo "  - Correct folder_id in config.json" >&2
                cat "$_resp_file" >&2
                rm -rf "$_tmpdir_http" "$_hr_tmpdir"
                return 1
                ;;
            5[0-9][0-9]|000)
                if [ "$_attempt" -lt "$_max_retries" ]; then
                    echo "Request failed (status=$_status), retry in ${_backoff}s... ($_attempt/$_max_retries)" >&2
                    sleep "$_backoff"
                    _backoff=$((_backoff * 2))
                else
                    echo "Error: Request failed after $_max_retries attempts (last status=$_status)" >&2
                    cat "$_resp_file" >&2 2>/dev/null || true
                fi
                rm -rf "$_tmpdir_http"
                ;;
            *)
                echo "Error: HTTP $_status" >&2
                cat "$_resp_file" >&2 2>/dev/null || true
                rm -rf "$_tmpdir_http" "$_hr_tmpdir"
                return 1
                ;;
        esac
    done
    rm -rf "$_hr_tmpdir"
    return 1
}

# Authenticated request (adds IAM token and folder-id headers)
# Usage: auth_request "POST" "url" "body"
auth_request() {
    _ar_method="$1"
    _ar_url="$2"
    _ar_body="$3"

    _iam_token=$(get_cached_iam_token)
    if [ -z "$_iam_token" ]; then
        echo "Error: No valid IAM token. Run iam_token_get.sh first." >&2
        return 1
    fi

    _folder_id=$(cfg_get "yandex_cloud_folder_id")
    if [ -z "$_folder_id" ]; then
        echo "Error: yandex_cloud_folder_id not set in config.json" >&2
        return 1
    fi

    _result=$(http_request "$_ar_method" "$_ar_url" "$_ar_body" \
        "Authorization: Bearer $_iam_token" \
        "x-folder-id: $_folder_id" \
        "Content-Type: application/json") || {
        # On 401, auto-refresh token and retry once
        if echo "$_result" 2>/dev/null | python3 -c "import sys,json; d=json.load(sys.stdin); exit(0 if d.get('code')==16 else 1)" 2>/dev/null; then
            echo "Token expired, auto-refreshing..." >&2
            rm -f "$CACHE_DIR/iam_token.json"
            sh "$SCRIPT_DIR/iam_token_get.sh" >&2 || { echo "Error: Token refresh failed" >&2; return 1; }
            _iam_token=$(get_cached_iam_token)
            if [ -z "$_iam_token" ]; then
                echo "Error: Token refresh produced no token" >&2
                return 1
            fi
            # Retry with new token
            _result=$(http_request "$_ar_method" "$_ar_url" "$_ar_body" \
                "Authorization: Bearer $_iam_token" \
                "x-folder-id: $_folder_id" \
                "Content-Type: application/json") || return 1
        else
            return 1
        fi
    }
    echo "$_result"
}

# --- IAM Token cache ---

get_cached_iam_token() {
    _cache_file="$CACHE_DIR/iam_token.json"
    if [ ! -f "$_cache_file" ]; then
        return 0
    fi

    # Check expiry with 5-minute safety window
    _is_valid=$(python3 -c "
import json, time
with open('$_cache_file') as f:
    d = json.load(f)
exp = d.get('expires_at', 0)
now = time.time()
if exp - now > 300:
    print(d['iam_token'])
else:
    print('')
" 2>/dev/null)

    if [ -n "$_is_valid" ]; then
        echo "$_is_valid"
    else
        # Token expired, remove cache
        rm -f "$_cache_file"
    fi
}

save_iam_token() {
    _token="$1"
    _expires_at="$2"
    _cache_file="$CACHE_DIR/iam_token.json"

    # Atomic write with restricted permissions: tmp -> rename
    _old_umask=$(umask)
    umask 077
    _tmp_file="$CACHE_DIR/.iam_token_tmp_$$.json"
    # Pass token via environment to avoid shell injection
    _SAVE_TOKEN="$_token" python3 -c "
import json, os
d = {'iam_token': os.environ['_SAVE_TOKEN'], 'expires_at': $_expires_at}
with open('$_tmp_file', 'w') as f:
    json.dump(d, f)
"
    mv "$_tmp_file" "$_cache_file"
    umask "$_old_umask"
}

# --- XML parsing (python3 xml.etree.ElementTree) ---

# Parse Yandex Search API XML response to JSON
# Usage: parse_search_xml "input.xml" > "output.json"
parse_search_xml() {
    _xml_file="$1"
    python3 -c "
import xml.etree.ElementTree as ET
import json
import sys

def clean_hl(elem):
    parts = []
    if elem.text:
        parts.append(elem.text)
    for child in elem:
        if child.text:
            parts.append(child.text)
        if child.tail:
            parts.append(child.tail)
    return ''.join(parts).strip()

try:
    tree = ET.parse('$_xml_file')
    root = tree.getroot()

    results = []
    pos = 0

    for grouping in root.iter('grouping'):
        for group in grouping.iter('group'):
            for doc in group.iter('doc'):
                pos += 1
                url_elem = doc.find('url')
                title_elem = doc.find('title')
                snippet = ''
                for passages in doc.iter('passages'):
                    for passage in passages.iter('passage'):
                        snippet = clean_hl(passage)
                        if snippet:
                            break
                    if snippet:
                        break

                domain_elem = doc.find('domain')

                entry = {
                    'position': pos,
                    'url': url_elem.text if url_elem is not None else '',
                    'title': clean_hl(title_elem) if title_elem is not None else '',
                    'snippet': snippet[:300] if snippet else '',
                    'domain': domain_elem.text if domain_elem is not None else '',
                }
                results.append(entry)

    json.dump(results, sys.stdout, ensure_ascii=False, indent=2)
except ET.ParseError as e:
    print(json.dumps({'error': f'XML parse error: {e}', 'raw_saved': True}), file=sys.stdout)
    sys.exit(0)
except Exception as e:
    print(json.dumps({'error': str(e), 'raw_saved': True}), file=sys.stdout)
    sys.exit(0)
"
}

# --- Hash helper ---

file_hash() {
    _input="$1"
    echo "$_input" | python3 -c "
import hashlib, sys
print(hashlib.md5(sys.stdin.read().strip().encode()).hexdigest()[:12])
"
}
