#!/bin/sh
# Common functions for Yandex Wordstat skill — dual backend (legacy + cloud)
#
# Public API (sourced by other scripts):
#   load_config            — picks backend, exports WORDSTAT_BACKEND, _DETECTED_VIA, _CLOUD_*
#   wordstat_request M P   — request to Wordstat API, always returns LEGACY-shaped JSON
#   print_backend_info     — backend-aware diagnostic block (used by quota.sh)
#   die_with_help MSG      — structured error pointing user at config README
#   json_escape, format_number, json_value, json_string  — legacy helpers (unchanged)
#
# Backend dispatch:
#   - WORDSTAT_BACKEND=legacy → POST api.wordstat.yandex.net/v1/{method} (Bearer OAuth)
#   - WORDSTAT_BACKEND=cloud  → POST searchapi.api.cloud.yandex.net/v2/wordstat/{method}
#                              with AI Studio Api-Key or IAM Bearer + folderId;
#                              response normalized back to legacy shape so callers don't change.
#
# Selection in load_config is STRUCTURAL ONLY — no network, no IAM preflight.
# IAM/network errors surface on the first wordstat_request call.

# Resolve directories. Use $0 because we're sourced from many shells (sh + bash).
# Tests can pre-set WORDSTAT_SCRIPT_DIR / WORDSTAT_SKILL_DIR / WORDSTAT_CONFIG_DIR
# to override the auto-resolution (POSIX sh has no portable way to get the path
# of a sourced script when $0 isn't reliable).
if [ -z "${WORDSTAT_SCRIPT_DIR:-}" ]; then
    WORDSTAT_SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
fi
if [ -z "${WORDSTAT_SKILL_DIR:-}" ]; then
    WORDSTAT_SKILL_DIR="$(cd "$WORDSTAT_SCRIPT_DIR/.." && pwd)"
fi
WORDSTAT_CONFIG_DIR="${WORDSTAT_CONFIG_DIR:-$WORDSTAT_SKILL_DIR/config}"
WORDSTAT_CACHE_DIR="${WORDSTAT_CACHE_DIR:-$WORDSTAT_SKILL_DIR/cache}"

WORDSTAT_LEGACY_API="https://api.wordstat.yandex.net/v1"
WORDSTAT_CLOUD_API="https://searchapi.api.cloud.yandex.net/v2/wordstat"
WORDSTAT_IAM_API="https://iam.api.cloud.yandex.net/iam/v1/tokens"
WORDSTAT_README_URL="https://github.com/artwist-polyakov/polyakov-claude-skills/blob/main/plugins/yandex-wordstat/skills/yandex-wordstat/config/README.md"

# Exported by load_config so callers and die_with_help can read them
WORDSTAT_BACKEND=""
WORDSTAT_BACKEND_DETECTED_VIA=""
WORDSTAT_CLOUD_FOLDER_ID=""
WORDSTAT_CLOUD_AUTH_MODE=""
WORDSTAT_CLOUD_API_KEY=""
WORDSTAT_CLOUD_SA_KEY_PATH=""
WORDSTAT_CLOUD_OPENSSL_BIN=""

# ---------------------------------------------------------------------
# Error helper
# ---------------------------------------------------------------------

die_with_help() {
    _msg="$1"
    _extra="${2:-}"

    {
        printf '[wordstat] %s\n' "$_msg"
        if [ -n "$WORDSTAT_BACKEND" ]; then
            printf 'Backend: %s' "$WORDSTAT_BACKEND"
            [ -n "$WORDSTAT_BACKEND_DETECTED_VIA" ] && \
                printf ' (%s)' "$WORDSTAT_BACKEND_DETECTED_VIA"
            printf '\n'
        fi
        [ -n "$_extra" ] && printf '%s\n' "$_extra"
        printf '\n'
        printf 'Likely the plugin config needs updating. See:\n'
        printf '  %s\n\n' "$WORDSTAT_README_URL"
        printf 'Quick checks:\n'
        printf '  - cloud mode:  config/config.json has yandex_cloud_folder_id?\n'
        printf '                 API key: YANDEX_AI_API_KEY is set and valid?\n'
        printf '                 IAM: auth.service_account_key_file is present and readable?\n'
        printf "                 account has role 'search-api.webSearch.user'?\n"
        printf '  - legacy mode: YANDEX_WORDSTAT_TOKEN still valid? (tokens expire after 1 year)\n'
        printf '  - to switch:   set YANDEX_WORDSTAT_BACKEND=legacy|cloud in config/.env\n'
    } >&2
    exit 1
}

# ---------------------------------------------------------------------
# Legacy helpers (kept for compatibility with bash callers)
# ---------------------------------------------------------------------

json_escape() {
    printf '%s' "$1" | sed 's/\\/\\\\/g; s/"/\\"/g; s/	/\\t/g'
}

format_number() {
    printf "%'d" "$1" 2>/dev/null || echo "$1"
}

# Extract a numeric/literal JSON value (no string quoting)
json_value() {
    _jv_json="$1"; _jv_key="$2"
    printf '%s' "$_jv_json" | grep -o "\"$_jv_key\":[^,}]*" | head -1 | sed 's/.*://' | tr -d '"[:space:]'
}

# Extract a JSON string value
json_string() {
    _js_json="$1"; _js_key="$2"
    printf '%s' "$_js_json" | grep -o "\"$_js_key\":\"[^\"]*\"" | head -1 | sed 's/.*:"//' | tr -d '"'
}

# ---------------------------------------------------------------------
# Backend selection — load_config
# ---------------------------------------------------------------------

# Read .env if present (legacy creds + override). Sourced into current shell.
_load_env_file() {
    _env_file="$WORDSTAT_CONFIG_DIR/.env"
    if [ -f "$_env_file" ]; then
        # shellcheck disable=SC1090
        . "$_env_file"
    fi
}

# Read a value from config.json. Usage: _cfg_get "key" or "auth.openssl_bin"
# Returns empty string on missing key, missing file, or parse error.
_cfg_get() {
    _cfg_file="$WORDSTAT_CONFIG_DIR/config.json"
    [ -f "$_cfg_file" ] || { echo ""; return 0; }
    _CFG_FILE="$_cfg_file" _CFG_KEY="$1" python3 - <<'PYEOF' 2>/dev/null
import json, os, sys
try:
    with open(os.environ["_CFG_FILE"]) as f:
        cfg = json.load(f)
except Exception:
    print("")
    sys.exit(0)
v = cfg
for part in os.environ["_CFG_KEY"].split("."):
    if isinstance(v, dict) and part in v:
        v = v[part]
    else:
        v = None
        break
print("" if v is None else v)
PYEOF
}

# Resolve a path: absolute as-is, relative resolved against skill dir
_resolve_path() {
    _rp="$1"
    case "$_rp" in
        /*) printf '%s\n' "$_rp" ;;
        *)  printf '%s/%s\n' "$WORDSTAT_SKILL_DIR" "$_rp" ;;
    esac
}

# Detect cloud structural config. Sets WORDSTAT_CLOUD_* variables on success.
# Returns 0 if cloud is structurally configured, 1 if not, 2 if config.json is
# present but malformed (caller should die loudly).
_detect_cloud_config() {
    _cfg_file="$WORDSTAT_CONFIG_DIR/config.json"
    [ -f "$_cfg_file" ] || return 1

    _folder=$(_cfg_get yandex_cloud_folder_id)
    _auth_mode=$(_cfg_get auth.mode)
    _sa_rel=$(_cfg_get auth.service_account_key_file)
    _ossl=$(_cfg_get auth.openssl_bin)

    if [ -z "$_folder" ]; then
        WORDSTAT_BACKEND_DETECTED_VIA="cloud (config.json present but yandex_cloud_folder_id missing)"
        return 2
    fi
    WORDSTAT_CLOUD_FOLDER_ID="$_folder"

    if [ -z "$_auth_mode" ]; then
        if [ -n "${YANDEX_AI_API_KEY:-}" ] && [ -z "$_sa_rel" ]; then
            _auth_mode="api_key"
        else
            _auth_mode="iam"
        fi
    fi

    case "$_auth_mode" in
        api_key)
            if [ -z "${YANDEX_AI_API_KEY:-}" ]; then
                WORDSTAT_BACKEND_DETECTED_VIA="cloud (auth.mode=api_key but YANDEX_AI_API_KEY missing)"
                return 2
            fi
            WORDSTAT_CLOUD_AUTH_MODE="api_key"
            WORDSTAT_CLOUD_API_KEY="$YANDEX_AI_API_KEY"
            return 0
            ;;
        iam)
            if [ -z "$_sa_rel" ]; then
                WORDSTAT_BACKEND_DETECTED_VIA="cloud (auth.mode=iam but auth.service_account_key_file missing)"
                return 2
            fi
            _sa_resolved=$(_resolve_path "$_sa_rel")
            if [ ! -r "$_sa_resolved" ]; then
                WORDSTAT_CLOUD_SA_KEY_PATH="$_sa_resolved"
                WORDSTAT_BACKEND_DETECTED_VIA="cloud (SA key file not found at resolved path)"
                return 2
            fi
            WORDSTAT_CLOUD_AUTH_MODE="iam"
            WORDSTAT_CLOUD_SA_KEY_PATH="$_sa_resolved"
            WORDSTAT_CLOUD_OPENSSL_BIN="${_ossl:-openssl}"
            return 0
            ;;
        *)
            WORDSTAT_BACKEND_DETECTED_VIA="cloud (invalid auth.mode=$_auth_mode)"
            return 2
            ;;
    esac
}

load_config() {
    _load_env_file

    # 1. Explicit override
    if [ -n "${YANDEX_WORDSTAT_BACKEND:-}" ]; then
        case "$YANDEX_WORDSTAT_BACKEND" in
            cloud)
                _rc=0
                _detect_cloud_config || _rc=$?
                if [ "$_rc" = "2" ]; then
                    WORDSTAT_BACKEND="cloud"
                    die_with_help "YANDEX_WORDSTAT_BACKEND=cloud but config is incomplete: $WORDSTAT_BACKEND_DETECTED_VIA"
                fi
                if [ "$_rc" = "1" ]; then
                    WORDSTAT_BACKEND="cloud"
                    die_with_help "YANDEX_WORDSTAT_BACKEND=cloud but config/config.json is missing"
                fi
                WORDSTAT_BACKEND="cloud"
                WORDSTAT_BACKEND_DETECTED_VIA="explicit override"
                return 0
                ;;
            legacy)
                if [ -z "${YANDEX_WORDSTAT_TOKEN:-}" ]; then
                    WORDSTAT_BACKEND="legacy"
                    WORDSTAT_BACKEND_DETECTED_VIA="explicit override"
                    die_with_help "YANDEX_WORDSTAT_BACKEND=legacy but YANDEX_WORDSTAT_TOKEN is not set"
                fi
                WORDSTAT_BACKEND="legacy"
                WORDSTAT_BACKEND_DETECTED_VIA="explicit override"
                return 0
                ;;
            *)
                die_with_help "Invalid YANDEX_WORDSTAT_BACKEND='$YANDEX_WORDSTAT_BACKEND' (expected 'legacy' or 'cloud')"
                ;;
        esac
    fi

    # 2. Cloud structurally configured → cloud (cloud wins on tie)
    _rc=0
    _detect_cloud_config || _rc=$?
    if [ "$_rc" = "0" ]; then
        WORDSTAT_BACKEND="cloud"
        WORDSTAT_BACKEND_DETECTED_VIA="auto: config.json present"
        return 0
    fi
    if [ "$_rc" = "2" ]; then
        # Malformed cloud config → fail loudly, do NOT silently fall back
        WORDSTAT_BACKEND="cloud"
        die_with_help "config/config.json present but invalid: $WORDSTAT_BACKEND_DETECTED_VIA"
    fi

    # 3. Legacy creds present → legacy
    if [ -n "${YANDEX_WORDSTAT_TOKEN:-}" ]; then
        WORDSTAT_BACKEND="legacy"
        WORDSTAT_BACKEND_DETECTED_VIA="auto: YANDEX_WORDSTAT_TOKEN set"
        return 0
    fi

    # 4. Nothing
    die_with_help "No Wordstat credentials found"
}

# ---------------------------------------------------------------------
# Backend info — used by quota.sh
# ---------------------------------------------------------------------

print_backend_info() {
    case "$WORDSTAT_BACKEND" in
        legacy)
            echo "Backend: legacy ($WORDSTAT_BACKEND_DETECTED_VIA)"
            echo ""
            echo "=== Endpoints ==="
            echo "  POST $WORDSTAT_LEGACY_API/topRequests"
            echo "  POST $WORDSTAT_LEGACY_API/dynamics"
            echo "  POST $WORDSTAT_LEGACY_API/regions"
            echo ""
            echo "=== API Limits ==="
            echo "  - Rate limit: 10 requests/second"
            echo "  - Daily quota: 1000 requests"
            echo ""
            echo "Note: This API is deprecated for new users. Existing tokens still work."
            ;;
        cloud)
            echo "Backend: cloud ($WORDSTAT_BACKEND_DETECTED_VIA)"
            echo "  folder_id: $WORDSTAT_CLOUD_FOLDER_ID"
            echo "  auth:      $WORDSTAT_CLOUD_AUTH_MODE"
            if [ "$WORDSTAT_CLOUD_AUTH_MODE" = "iam" ]; then
                echo "  SA key:    $WORDSTAT_CLOUD_SA_KEY_PATH"
            fi
            echo ""
            echo "=== Endpoints ==="
            echo "  POST $WORDSTAT_CLOUD_API/topRequests"
            echo "  POST $WORDSTAT_CLOUD_API/dynamics"
            echo "  POST $WORDSTAT_CLOUD_API/regions"
            echo ""
            echo "=== API Limits ==="
            echo "  Wordstat in Search API is currently in Preview."
            echo "  See https://yandex.cloud/ru/docs/search-api/pricing for current limits and billing."
            ;;
        *)
            echo "Backend: (not configured)"
            ;;
    esac
}

# ---------------------------------------------------------------------
# IAM token — JWT PS256 with SA key (inline-copied from yandex-search-api)
# ---------------------------------------------------------------------

_make_secure_tmpdir() {
    _old_umask=$(umask)
    umask 077
    _td=$(mktemp -d "${TMPDIR:-/tmp}/wordstat_XXXXXX")
    umask "$_old_umask"
    echo "$_td"
}

_check_openssl() {
    _ossl="$1"
    if ! command -v "$_ossl" >/dev/null 2>&1; then
        die_with_help "openssl not found at '$_ossl'" \
            "Install OpenSSL 1.1.1+ or set auth.openssl_bin in config/config.json"
    fi
    _ossl_ver=$("$_ossl" version 2>/dev/null || true)
    case "$_ossl_ver" in
        LibreSSL*)
            die_with_help "LibreSSL detected ($_ossl_ver) — OpenSSL 1.1.1+ required for PS256" \
                "macOS users: brew install openssl@3 and set auth.openssl_bin to the homebrew path"
            ;;
        "OpenSSL 0."*|"OpenSSL 1.0."*)
            die_with_help "OpenSSL too old ($_ossl_ver), need 1.1.1+"
            ;;
    esac
}

_get_cached_iam_token() {
    _cf="$WORDSTAT_CACHE_DIR/iam_token.json"
    [ -f "$_cf" ] || return 0
    _CACHE_FILE="$_cf" python3 - <<'PYEOF' 2>/dev/null
import json, os, time
cf = os.environ["_CACHE_FILE"]
try:
    with open(cf) as f:
        d = json.load(f)
    exp = d.get("expires_at", 0)
    if exp - time.time() > 300:
        print(d["iam_token"])
except Exception:
    pass
PYEOF
}

_save_iam_token() {
    _tok="$1"
    _exp="$2"
    mkdir -p "$WORDSTAT_CACHE_DIR"
    _cf="$WORDSTAT_CACHE_DIR/iam_token.json"
    _old_umask=$(umask)
    umask 077
    _tmp="$WORDSTAT_CACHE_DIR/.iam_token_tmp_$$.json"
    _SAVE_TOKEN="$_tok" _SAVE_EXP="$_exp" _TMP_FILE="$_tmp" python3 - <<'PYEOF'
import json, os
d = {"iam_token": os.environ["_SAVE_TOKEN"], "expires_at": int(os.environ["_SAVE_EXP"])}
with open(os.environ["_TMP_FILE"], "w") as f:
    json.dump(d, f)
PYEOF
    mv "$_tmp" "$_cf"
    umask "$_old_umask"
}

# Issue a fresh IAM token from the SA key. Echoes token on stdout.
_iam_token_issue() {
    _check_openssl "$WORDSTAT_CLOUD_OPENSSL_BIN"

    if [ ! -r "$WORDSTAT_CLOUD_SA_KEY_PATH" ]; then
        die_with_help "Service account key file not readable: $WORDSTAT_CLOUD_SA_KEY_PATH"
    fi

    _tmp=$(_make_secure_tmpdir)
    # shellcheck disable=SC2064
    trap "rm -rf '$_tmp'" EXIT INT TERM

    # Build JWT header + payload, write key.pem and signing_input.txt
    _SA_KEY="$WORDSTAT_CLOUD_SA_KEY_PATH" _TMP="$_tmp" python3 - <<'PYEOF' || die_with_help "Failed to build JWT from SA key"
import json, base64, time, os, sys
sa_key_file = os.environ["_SA_KEY"]
tmp_dir = os.environ["_TMP"]
try:
    with open(sa_key_file) as f:
        sa = json.load(f)
    sa_id = sa["service_account_id"]
    key_id = sa["id"]
    private_key = sa["private_key"]
except Exception as e:
    print(f"SA key parse error: {e}", file=sys.stderr)
    sys.exit(1)

with open(os.path.join(tmp_dir, "key.pem"), "w") as f:
    f.write(private_key)

header = json.dumps({"typ": "JWT", "alg": "PS256", "kid": key_id}, separators=(",", ":"))
header_b64 = base64.urlsafe_b64encode(header.encode()).rstrip(b"=").decode()

now = int(time.time())
payload = json.dumps({
    "iss": sa_id,
    "aud": "https://iam.api.cloud.yandex.net/iam/v1/tokens",
    "iat": now,
    "exp": now + 3600,
}, separators=(",", ":"))
payload_b64 = base64.urlsafe_b64encode(payload.encode()).rstrip(b"=").decode()

signing_input = f"{header_b64}.{payload_b64}"
with open(os.path.join(tmp_dir, "signing_input.txt"), "w") as f:
    f.write(signing_input)
with open(os.path.join(tmp_dir, "header_payload.txt"), "w") as f:
    f.write(signing_input)
PYEOF

    "$WORDSTAT_CLOUD_OPENSSL_BIN" dgst -sha256 \
        -sigopt rsa_padding_mode:pss \
        -sigopt rsa_pss_saltlen:-1 \
        -sign "$_tmp/key.pem" \
        -out "$_tmp/signature.bin" \
        "$_tmp/signing_input.txt" 2>/dev/null \
        || die_with_help "openssl PS256 signing failed"

    _sig=$(python3 -c "
import base64, sys
with open('$_tmp/signature.bin', 'rb') as f:
    print(base64.urlsafe_b64encode(f.read()).rstrip(b'=').decode())
")
    _hp=$(cat "$_tmp/header_payload.txt")
    _jwt="${_hp}.${_sig}"

    _resp=$(curl -s -X POST "$WORDSTAT_IAM_API" \
        -H "Content-Type: application/json" \
        -d "{\"jwt\":\"$_jwt\"}")

    if [ -z "$_resp" ]; then
        die_with_help "Empty response from IAM API"
    fi

    # Parse token + expiry
    _result=$(printf '%s' "$_resp" | python3 -c "
import json, sys
from datetime import datetime
try:
    d = json.load(sys.stdin)
except Exception as e:
    print('PARSE_ERROR:' + str(e))
    sys.exit(0)
tok = d.get('iamToken', '')
exp_s = d.get('expiresAt', '')
if not tok:
    print('NO_TOKEN:' + json.dumps(d)[:300])
    sys.exit(0)
if exp_s:
    try:
        ts = datetime.fromisoformat(exp_s.replace('Z', '+00:00')).timestamp()
        exp = int(ts)
    except Exception:
        import time
        exp = int(time.time()) + 43200
else:
    import time
    exp = int(time.time()) + 43200
print(f'{tok}|{exp}')
")
    case "$_result" in
        PARSE_ERROR:*) die_with_help "IAM response parse error: ${_result#PARSE_ERROR:}" "Raw: $_resp" ;;
        NO_TOKEN:*)    die_with_help "IAM response missing iamToken" "${_result#NO_TOKEN:}" ;;
    esac

    _tok=$(printf '%s' "$_result" | cut -d'|' -f1)
    _exp=$(printf '%s' "$_result" | cut -d'|' -f2)
    _save_iam_token "$_tok" "$_exp"

    rm -rf "$_tmp"
    trap - EXIT INT TERM
    printf '%s' "$_tok"
}

_iam_token_get() {
    _cached=$(_get_cached_iam_token)
    if [ -n "$_cached" ]; then
        printf '%s' "$_cached"
        return 0
    fi
    _iam_token_issue
}

# ---------------------------------------------------------------------
# Request translation + response normalization (cloud ↔ legacy)
# ---------------------------------------------------------------------

# Translate legacy-shape params JSON → cloud request body JSON.
# Args: $1 = method (topRequests|dynamics|regions), $2 = legacy params JSON
# Output: cloud-shape JSON on stdout.
# Exits 1 with die_with_help on dynamics preflight failure.
_xlate_request() {
    _method="$1"
    _params="$2"
    _METHOD="$_method" _PARAMS="$_params" _FOLDER="$WORDSTAT_CLOUD_FOLDER_ID" \
    python3 - <<'PYEOF'
import json, os, re, sys

method = os.environ["_METHOD"]
params = json.loads(os.environ["_PARAMS"])
folder = os.environ["_FOLDER"]

DEVICE_MAP = {
    "all": "DEVICE_ALL",
    "desktop": "DEVICE_DESKTOP",
    "phone": "DEVICE_PHONE",
    "tablet": "DEVICE_TABLET",
}
PERIOD_MAP = {
    "monthly": "PERIOD_MONTHLY",
    "weekly": "PERIOD_WEEKLY",
    "daily": "PERIOD_DAILY",
}
REGION_TYPE_MAP = {
    "all": "REGION_ALL",
    "cities": "REGION_CITIES",
    "regions": "REGION_REGIONS",
}

def map_devices(d):
    if d is None:
        return ["DEVICE_ALL"]
    if isinstance(d, list):
        return [DEVICE_MAP.get(x, x) if isinstance(x, str) and not x.startswith("DEVICE_") else x for x in d]
    return [DEVICE_MAP.get(d, "DEVICE_ALL")]

def map_regions(r):
    if r is None:
        return None
    return [str(x) for x in r]

def to_rfc3339(d):
    # Accept either YYYY-MM-DD or already-RFC3339
    if not d:
        return d
    if "T" in d:
        return d
    return d + "T00:00:00Z"

if method == "topRequests":
    body = {"phrase": params["phrase"]}
    if "numPhrases" in params:
        body["numPhrases"] = str(params["numPhrases"])
    if "regions" in params:
        body["regions"] = map_regions(params["regions"])
    if "devices" in params:
        body["devices"] = map_devices(params["devices"])
    body["folderId"] = folder

elif method == "dynamics":
    # ---- Preflight: cloud only allows '+' operator at weekly/monthly ----
    period = params.get("period", "monthly")
    phrase = params.get("phrase", "")
    if period != "daily":
        # Token-boundary detection of operators that cloud rejects at non-daily.
        # Hyphen inside word (санкт-петербург, премиум-класс) MUST pass.
        # Token-leading -, !, or any of " ( | ) trigger the failure. + is allowed.
        bad_ops = []
        if re.search(r'(^|\s)-\S', phrase):
            bad_ops.append("- (minus-word)")
        if re.search(r'(^|\s)!\S', phrase):
            bad_ops.append("! (exact form)")
        if '"' in phrase:
            bad_ops.append('" (exact phrase)')
        if "(" in phrase or ")" in phrase or "|" in phrase:
            bad_ops.append("( | ) (grouping)")
        if bad_ops:
            print("PREFLIGHT_FAIL:" + ", ".join(bad_ops), file=sys.stderr)
            sys.exit(2)

    body = {
        "phrase": phrase,
        "period": PERIOD_MAP.get(period, period),
        "fromDate": to_rfc3339(params["fromDate"]),
    }
    if "toDate" in params and params["toDate"]:
        body["toDate"] = to_rfc3339(params["toDate"])
    if "regions" in params:
        body["regions"] = map_regions(params["regions"])
    if "devices" in params:
        body["devices"] = map_devices(params["devices"])
    body["folderId"] = folder

elif method == "regions":
    body = {"phrase": params["phrase"]}
    if "regionType" in params:
        body["region"] = REGION_TYPE_MAP.get(params["regionType"], params["regionType"])
    if "devices" in params:
        body["devices"] = map_devices(params["devices"])
    body["folderId"] = folder

else:
    print(f"UNKNOWN_METHOD:{method}", file=sys.stderr)
    sys.exit(2)

print(json.dumps(body, ensure_ascii=False))
PYEOF
}

# Normalize cloud response JSON → legacy shape JSON.
# Args: $1 = method, $2 = path to cloud response file (optional; if missing, spool stdin)
# Output: legacy-shape JSON on stdout
#
# Implementation note: cloud responses for topRequests --limit 2000 can be
# multi-MB. Passing through env var is unsafe (ARG_MAX / E2BIG). We use a file
# path. If the caller already has the response in a file (e.g. _cloud_request),
# pass it as $2 to skip the spool step.
_normalize_response() {
    _method="$1"
    _nr_owns_tmp=0
    if [ -n "${2:-}" ]; then
        _nr_tmp="$2"
    else
        _nr_tmp="${TMPDIR:-/tmp}/wordstat_norm_$$.json"
        cat > "$_nr_tmp"
        _nr_owns_tmp=1
    fi
    _METHOD="$_method" _RESP_FILE="$_nr_tmp" python3 - <<'PYEOF'
import json, os, sys
method = os.environ["_METHOD"]
try:
    with open(os.environ["_RESP_FILE"], "r", encoding="utf-8") as f:
        d = json.load(f)
except Exception as e:
    print(json.dumps({"error": f"Cloud response parse error: {e}"}))
    sys.exit(0)

# Translate cloud error JSON to legacy {"error": ...}
if "code" in d and "message" in d and "results" not in d and "topRequests" not in d:
    print(json.dumps({"error": d.get("message", "cloud error"), "code": d.get("code")}))
    sys.exit(0)

def to_int(v):
    if v is None:
        return 0
    try:
        return int(v)
    except (TypeError, ValueError):
        return v

if method == "topRequests":
    out = {}
    if "totalCount" in d:
        out["totalCount"] = to_int(d["totalCount"])
    out["topRequests"] = [
        {"phrase": r.get("phrase", ""), "count": to_int(r.get("count", 0))}
        for r in d.get("results", [])
    ]
    out["associations"] = [
        {"phrase": r.get("phrase", ""), "count": to_int(r.get("count", 0))}
        for r in d.get("associations", [])
    ]

elif method == "dynamics":
    out = {
        "data": [
            {
                "date": r.get("date", ""),
                "count": to_int(r.get("count", 0)),
                "share": r.get("share", 0),
            }
            for r in d.get("results", [])
        ]
    }

elif method == "regions":
    out = {
        "regions": [
            {
                "regionId": to_int(r.get("region", 0)),
                "count": to_int(r.get("count", 0)),
                "share": r.get("share", 0),
                "affinity": to_int(r.get("affinityIndex", r.get("affinity", 0))),
            }
            for r in d.get("results", [])
        ]
    }
else:
    out = d

# Compact separators — no spaces. Matches the legacy API JSON shape that
# existing grep/sed parsers in top_requests.sh, dynamics.sh, regions_stats.sh expect.
# E.g. "topRequests":[{"phrase":"...","count":123}] not "topRequests": [{"phrase": "...", "count": 123}]
print(json.dumps(out, ensure_ascii=False, separators=(",", ":")))
PYEOF
    [ "$_nr_owns_tmp" = "1" ] && rm -f "$_nr_tmp"
    return 0
}

# ---------------------------------------------------------------------
# wordstat_request — public dispatcher
# ---------------------------------------------------------------------

# Legacy backend: direct curl to api.wordstat.yandex.net/v1
_legacy_request() {
    _method="$1"
    _params="$2"
    curl -s -X POST "$WORDSTAT_LEGACY_API/$_method" \
        -H "Authorization: Bearer $YANDEX_WORDSTAT_TOKEN" \
        -H "Content-Type: application/json; charset=utf-8" \
        -H "Accept-Language: ru" \
        -d "$_params"
}

# Cloud backend: translate, authorize, POST, normalize
_cloud_request() {
    _method="$1"
    _params="$2"

    # 1. Translate request
    _xlate_out=$(_xlate_request "$_method" "$_params" 2>&1)
    _xlate_rc=$?
    if [ "$_xlate_rc" != "0" ]; then
        case "$_xlate_out" in
            *PREFLIGHT_FAIL:*)
                _ops=${_xlate_out#*PREFLIGHT_FAIL:}
                die_with_help \
                    "Cloud Wordstat dynamics: at weekly/monthly granularity, only '+' operator is allowed. Found: $_ops" \
                    "Either switch --period to daily, or remove these operators from --phrase. See: https://aistudio.yandex.ru/docs/ru/search-api/operations/wordstat-getdynamics.html"
                ;;
            *UNKNOWN_METHOD:*)
                die_with_help "Unknown wordstat method: ${_xlate_out#*UNKNOWN_METHOD:}"
                ;;
            *)
                die_with_help "Request translation failed" "$_xlate_out"
                ;;
        esac
    fi
    _cloud_body="$_xlate_out"

    # 2. Select the configured authorization header.
    case "$WORDSTAT_CLOUD_AUTH_MODE" in
        api_key)
            _auth_header="Authorization: Api-Key $WORDSTAT_CLOUD_API_KEY"
            ;;
        iam)
            _tok=$(_iam_token_get)
            if [ -z "$_tok" ]; then
                die_with_help "Failed to obtain IAM token"
            fi
            _auth_header="Authorization: Bearer $_tok"
            ;;
        *)
            die_with_help "Unknown cloud auth mode: $WORDSTAT_CLOUD_AUTH_MODE"
            ;;
    esac

    # 3. POST with retry on 5xx and refresh on 401
    _attempt=0
    _max_attempts=3
    _backoff=2
    while [ "$_attempt" -lt "$_max_attempts" ]; do
        _attempt=$((_attempt + 1))
        _tmp=$(_make_secure_tmpdir)
        _resp_file="$_tmp/resp"
        _status=$(curl -s -o "$_resp_file" -w '%{http_code}' \
            -X POST "$WORDSTAT_CLOUD_API/$_method" \
            -H "$_auth_header" \
            -H "Content-Type: application/json" \
            -d "$_cloud_body")

        case "$_status" in
            2[0-9][0-9])
                _normalize_response "$_method" "$_resp_file"
                rm -rf "$_tmp"
                return 0
                ;;
            401)
                # IAM tokens can be refreshed once. Static API keys cannot.
                if [ "$WORDSTAT_CLOUD_AUTH_MODE" = "iam" ] && [ "$_attempt" = "1" ]; then
                    rm -f "$WORDSTAT_CACHE_DIR/iam_token.json"
                    _tok=$(_iam_token_issue)
                    _auth_header="Authorization: Bearer $_tok"
                    rm -rf "$_tmp"
                    continue
                fi
                _err=$(cat "$_resp_file" 2>/dev/null)
                rm -rf "$_tmp"
                if [ "$WORDSTAT_CLOUD_AUTH_MODE" = "api_key" ]; then
                    die_with_help "Cloud Wordstat 401 Unauthorized: AI Studio API key was rejected" "$_err"
                fi
                die_with_help "Cloud Wordstat 401 Unauthorized after token refresh" "$_err"
                ;;
            403)
                _err=$(cat "$_resp_file" 2>/dev/null)
                rm -rf "$_tmp"
                die_with_help "Cloud Wordstat 403 Forbidden" \
                    "Check role 'search-api.webSearch.user', API-key scope 'yc.search-api.execute', and folder $WORDSTAT_CLOUD_FOLDER_ID. Raw: $_err"
                ;;
            5[0-9][0-9]|000)
                if [ "$_attempt" -lt "$_max_attempts" ]; then
                    rm -rf "$_tmp"
                    sleep "$_backoff"
                    _backoff=$((_backoff * 2))
                    continue
                fi
                _err=$(cat "$_resp_file" 2>/dev/null)
                rm -rf "$_tmp"
                die_with_help "Cloud Wordstat $_status after $_max_attempts retries" "$_err"
                ;;
            *)
                _err=$(cat "$_resp_file" 2>/dev/null)
                rm -rf "$_tmp"
                die_with_help "Cloud Wordstat HTTP $_status" "$_err"
                ;;
        esac
    done
}

# Public entry point
wordstat_request() {
    _method="$1"
    _params="$2"

    if [ -z "$WORDSTAT_BACKEND" ]; then
        die_with_help "wordstat_request called before load_config"
    fi

    case "$WORDSTAT_BACKEND" in
        legacy) _legacy_request "$_method" "$_params" ;;
        cloud)  _cloud_request "$_method" "$_params" ;;
        *)      die_with_help "Unknown backend: $WORDSTAT_BACKEND" ;;
    esac
}
