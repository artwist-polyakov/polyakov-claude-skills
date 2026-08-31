#!/bin/sh
# Common functions for Yandex Search API
# Zero external dependencies: python3 stdlib + openssl + curl

set -e

SCRIPT_DIR="${YSA_SCRIPT_DIR:-$(cd "$(dirname "$0")" && pwd)}"
SKILL_DIR="${YSA_SKILL_DIR:-$(cd "$SCRIPT_DIR/.." && pwd)}"
# Overridable so the offline tests can point at a throwaway config and cache.
CONFIG_FILE="${YSA_CONFIG_FILE:-$SKILL_DIR/config/config.json}"
CACHE_DIR="${YSA_CACHE_DIR:-$SKILL_DIR/cache}"
SEARCH_API_URL="https://searchapi.api.cloud.yandex.net"
IAM_API_URL="https://iam.api.cloud.yandex.net/iam/v1/tokens"
OPERATION_API_URL="https://operation.api.cloud.yandex.net/operations"

# --- Smart snippets (инфоконтексты) ---
# Инфоконтекст — фрагмент найденной страницы (~500 токенов) с цитатами,
# релевантный запросу. Только синхронный WebSearchService/Search и только
# русская поисковая база. Справка: references/SMART_SNIPPETS.md
#
# Флаг уезжает в metadata.fields запроса.
SMART_SNIPPETS_FLAG_KEY="x-genesis-info-context"
SMART_SNIPPETS_FLAG_VALUE="on"
# Инфоконтексты отдаются только для этого типа поиска.
SMART_SNIPPETS_SEARCH_TYPE="SEARCH_TYPE_RU"
# Потолок документов с инфоконтекстами. В документации его нет, он измерен на
# живом API: при groupsOnPage 21 и выше сервер не обрезает выдачу до 20, а
# роняет её до дефолтных 10 — при той же цене запроса. То есть без клампа
# --results 50 стоит столько же, а возвращает вдвое меньше.
SMART_SNIPPETS_MAX_DOCS=20
# Сколько строк таблицы печатать в stdout: у песочницы жёсткий лимит вывода,
# полные инфоконтексты всегда уходят в файл.
YSA_PRINT_LIMIT="${YSA_PRINT_LIMIT:-20}"

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
elif isinstance(v, bool):
    # JSON true/false, а не питоновские True/False: значение сравнивается в shell.
    print('true' if v else 'false')
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

# --- Request building ---

# folderId один и тот же на весь прогон, а build_search_body зовётся на каждый
# запрос батча — читаем конфиг один раз, чтобы не поднимать python3 повторно.
#
# Кеш обязан наполняться в том шелле, который переживёт запрос. Вызывающие
# скрипты собирают тело как `_body=$(build_search_body ...)`, то есть вся
# функция целиком уходит в подоболочку: присваивание внутри неё умрёт вместе
# с ней. Поэтому кеш греется один раз в самом скрипте (ysa_warm_folder_id),
# а подоболочки просто наследуют готовое значение.
ysa_folder_id() {
    if [ -z "${_YSA_FOLDER_ID_CACHED:-}" ]; then
        _YSA_FOLDER_ID_CACHED=$(cfg_get 'yandex_cloud_folder_id')
    fi
    echo "$_YSA_FOLDER_ID_CACHED"
}

# Прогреть кеш folderId. Звать из тела скрипта, НЕ внутри $( ) и не в пайпе.
ysa_warm_folder_id() {
    ysa_folder_id >/dev/null
}

# Собрать тело запроса POST /v2/web/search.
# Usage: build_search_body "query" "region" "groups_on_page" "page" "snippets_on"
# snippets_on: 1 — просить smart snippets, иначе обычная выдача.
# SEARCH_TYPE/FAMILY_MODE/FIX_TYPO берутся из окружения вызывающего скрипта;
# дефолты продублированы здесь, чтобы забытая переменная не уехала в API
# пустой строкой.
build_search_body() {
    _YSA_QUERY="$1" \
    _YSA_REGION="$2" \
    _YSA_GROUPS="$3" \
    _YSA_PAGE="$4" \
    _YSA_SNIPPETS_ON="$5" \
    _YSA_SEARCH_TYPE="${SEARCH_TYPE:-SEARCH_TYPE_RU}" \
    _YSA_FAMILY_MODE="${FAMILY_MODE:-FAMILY_MODE_MODERATE}" \
    _YSA_FIX_TYPO="${FIX_TYPO:-FIX_TYPO_MODE_ON}" \
    _YSA_FOLDER_ID="$(ysa_folder_id)" \
    _YSA_FLAG_KEY="$SMART_SNIPPETS_FLAG_KEY" \
    _YSA_FLAG_VALUE="$SMART_SNIPPETS_FLAG_VALUE" \
    python3 -c '
import json
import os

env = os.environ
body = {
    "query": {
        "searchType": env["_YSA_SEARCH_TYPE"],
        "queryText": env["_YSA_QUERY"],
        "familyMode": env["_YSA_FAMILY_MODE"],
        "fixTypoMode": env["_YSA_FIX_TYPO"],
        "page": int(env["_YSA_PAGE"]),
    },
    "sortSpec": {},
    "groupSpec": {
        "groupMode": "GROUP_MODE_FLAT",
        "groupsOnPage": int(env["_YSA_GROUPS"]),
        "docsInGroup": 1,
    },
    "maxPassages": 3,
    "region": env["_YSA_REGION"],
    "l10n": "LOCALIZATION_RU",
    "folderId": env["_YSA_FOLDER_ID"],
}

if env["_YSA_SNIPPETS_ON"] == "1":
    body["metadata"] = {"fields": {env["_YSA_FLAG_KEY"]: env["_YSA_FLAG_VALUE"]}}

print(json.dumps(body, ensure_ascii=False))
'
}

# --- Response parsing ---

# Разобрать ответ Search API в единый JSON-массив.
# Usage: parse_search_response "decoded_rawData_file" > "output.json"
#
# Форматов два, и выбирает их сервер: обычная выдача приходит XML, а запрос с
# инфоконтекстами — JSON {"docs": [...]}. Формат определяется по первому
# непробельному символу, чтобы вызывающему коду не приходилось это знать.
#
# Каждый элемент результата: position, url, title, snippet, domain, extract.
# extract — инфоконтекст; пустая строка, когда его в ответе нет.
# Путь уезжает в python через окружение, чтобы кавычки в нём не ломали код.
parse_search_response() {
    _YSA_BODY_FILE="$1" python3 -c '
import json
import os
import sys
import xml.etree.ElementTree as ET
from urllib.parse import urlsplit


def text_of(elem):
    """Собрать текст элемента: Яндекс оборачивает совпадения в <hlword>."""
    if elem is None:
        return ""
    return "".join(elem.itertext()).strip()


def parse_xml(path):
    results = []
    position = 0
    for grouping in ET.parse(path).getroot().iter("grouping"):
        for group in grouping.iter("group"):
            for doc in group.iter("doc"):
                position += 1

                snippet = ""
                for passage in doc.iter("passage"):
                    snippet = text_of(passage)
                    if snippet:
                        break

                url = doc.find("url")
                domain = doc.find("domain")
                results.append({
                    "position": position,
                    "url": url.text if url is not None else "",
                    "title": text_of(doc.find("title")),
                    "snippet": snippet[:300],
                    "domain": domain.text if domain is not None else "",
                    "extract": "",
                })
    return results


def parse_infocontext(path):
    """Ответ с инфоконтекстами: docs[] с Num/DocumentTitle/FullUrl/Description
    и самим info_context.

    Живой API кладёт метаданные во вложенный объект rich_data, а документация
    показывает их на верхнем уровне документа. Встречаются обе формы, поэтому
    каждое поле ищется сперва в rich_data, потом на верхнем уровне."""
    with open(path, encoding="utf-8") as fh:
        payload = json.load(fh)

    def field(doc, name):
        rich = doc.get("rich_data")
        if isinstance(rich, dict) and rich.get(name) not in (None, ""):
            return rich[name]
        return doc.get(name)

    results = []
    for index, doc in enumerate(payload.get("docs") or [], start=1):
        url = field(doc, "FullUrl") or ""
        # Num приводим к числу: отрисовка печатает позицию через %d, и строка
        # уронила бы её уже после оплаченного поиска.
        try:
            position = int(field(doc, "Num"))
        except (TypeError, ValueError):
            position = index
        results.append({
            "position": position,
            "url": url,
            "title": field(doc, "DocumentTitle") or "",
            "snippet": (field(doc, "Description") or "")[:300],
            "domain": urlsplit(url).netloc,
            "extract": doc.get("info_context") or "",
        })
    return results


body_file = os.environ["_YSA_BODY_FILE"]

try:
    with open(body_file, encoding="utf-8", errors="replace") as fh:
        head = fh.read(64).lstrip()
    parse = parse_infocontext if head.startswith(("{", "[")) else parse_xml
    json.dump(parse(body_file), sys.stdout, ensure_ascii=False, indent=2)
except ET.ParseError as exc:
    print(json.dumps({"error": "XML parse error: %s" % exc, "raw_saved": True}))
except ValueError as exc:
    print(json.dumps({"error": "JSON parse error: %s" % exc, "raw_saved": True}))
except Exception as exc:  # парсер не должен ронять уже оплаченный поиск
    print(json.dumps({"error": str(exc), "raw_saved": True}))
'
}

# --- Cache hygiene ---

# Удалить пак, оставшийся от прошлого поиска той же фразы.
# Usage: drop_stale_pack "<hash>"
#
# Хэш считается только от текста запроса, поэтому прогон без инфоконтекстов —
# хоть синхронный с --no-snippets, хоть асинхронный, который их вовсе не умеет —
# перезапишет .raw и .json, но оставит .md от прошлого раза. Агент знает путь
# из SKILL.md и прочитает вчерашние выдержки как ответ на сегодняшний запрос.
drop_stale_pack() {
    rm -f "$CACHE_DIR/results/$1.md"
}

# --- Rendering ---

# Собрать markdown-пак с выдержками: один файл, который агент читает вместо
# повторного поиска. Печатает количество документов с непустой выдержкой.
# Usage: render_snippet_pack "results.json" "pack.md" "query" "region"
render_snippet_pack() {
    _YSA_JSON_FILE="$1" \
    _YSA_MD_FILE="$2" \
    _YSA_QUERY="$3" \
    _YSA_REGION="$4" \
    python3 -c '
import json
import os

with open(os.environ["_YSA_JSON_FILE"], encoding="utf-8") as fh:
    results = json.load(fh)

if not isinstance(results, list):
    raise SystemExit("cannot render a pack from a parse error")

query = os.environ["_YSA_QUERY"]
region = os.environ["_YSA_REGION"]
with_extract = sum(1 for r in results if r.get("extract"))

lines = [
    "# %s" % query,
    "",
    "Регион %s · документов: %d · с выдержками: %d" % (region, len(results), with_extract),
    "",
    "Источник: Yandex Search API, инфоконтексты — подготовленные фрагменты",
    "найденных страниц с цитатами из документа, релевантные запросу.",
]

for r in results:
    lines += [
        "",
        "---",
        "",
        "## %d. %s" % (r.get("position", 0), r.get("title") or "(без заголовка)"),
        "",
        r.get("url") or "",
        "",
    ]
    if r.get("extract"):
        lines.append(r["extract"])
    elif r.get("snippet"):
        lines += ["_Выдержка недоступна, ниже короткий сниппет выдачи._", "", r["snippet"]]
    else:
        lines.append("_Текста нет: ни выдержки, ни сниппета._")

with open(os.environ["_YSA_MD_FILE"], "w", encoding="utf-8") as fh:
    fh.write("\n".join(lines) + "\n")

print(with_extract)
'
}

# Компактный индекс результатов в stdout.
# Usage: render_results_table "results.json" "pack.md_or_empty" "full|line" "label"
#
# Когда выдержки просили, печатается одна строка на документ, а тексты остаются
# в паке: 20 выдержек по ~500 токенов не помещаются в буфер вывода песочницы и
# превращаются в молчаливый отказ. В режиме "line" на весь запрос печатается
# одна строка — иначе батч из десяти запросов упирается в тот же лимит.
render_results_table() {
    _YSA_JSON_FILE="$1" \
    _YSA_PACK_FILE="${2:-}" \
    _YSA_MODE="${3:-full}" \
    _YSA_LABEL="${4:-}" \
    _YSA_LIMIT="$YSA_PRINT_LIMIT" \
    python3 -c '
import json
import os


def cut(text, width):
    text = " ".join((text or "").split())
    return text if len(text) <= width else text[: width - 1] + "…"


with open(os.environ["_YSA_JSON_FILE"], encoding="utf-8") as fh:
    results = json.load(fh)

mode = os.environ["_YSA_MODE"]
label = os.environ.get("_YSA_LABEL") or ""
pack = os.environ.get("_YSA_PACK_FILE") or ""

if isinstance(results, dict) and "error" in results:
    prefix = "  %-40s " % cut(label, 40) if mode == "line" else "  "
    print("%sОшибка разбора ответа: %s" % (prefix, results["error"]))
    raise SystemExit(0)

with_extract = sum(1 for r in results if r.get("extract"))

if mode == "line":
    # Без пака (обычная выдача) в батче не остаётся ни одного пути к ответу,
    # поэтому подставляем разобранный JSON.
    print("  %-40s  %2d док., выдержек %2d  %s" % (
        cut(label, 40), len(results), with_extract,
        pack or os.environ["_YSA_JSON_FILE"]))
    raise SystemExit(0)

limit = int(os.environ["_YSA_LIMIT"])

# Вид выбирается по тому, ЗАПРАШИВАЛИСЬ ли выдержки (пак собран), а не по тому,
# сколько их пришло: когда флаг не сработал и with_extract == 0, развёрнутый
# список из 20 документов по три строки выносит буфер stdout песочницы.
if pack or with_extract:
    shown = results[:limit]
    print("    #  символов  домен                     заголовок")
    for r in shown:
        print("  %3d  %8d  %-24s  %s" % (
            r.get("position", 0),
            len(r.get("extract") or ""),
            cut(r.get("domain"), 24),
            cut(r.get("title"), 60),
        ))
else:
    # Три строки на документ — вчетверо дороже таблицы, поэтому потолок ниже.
    shown = results[: min(limit, 10)]
    for r in shown:
        print("  %d. %s" % (r.get("position", 0), cut(r.get("title"), 80)))
        print("     %s" % (r.get("url") or ""))
        if r.get("snippet"):
            print("     %s" % cut(r.get("snippet"), 120))
        print()

print()
if len(results) > len(shown):
    print("  ... ещё %d результатов — см. файлы ниже" % (len(results) - len(shown)))
print("  Всего: %d, с выдержками: %d" % (len(results), with_extract))
if pack:
    size = os.path.getsize(pack) if os.path.exists(pack) else 0
    human = "%d KB" % (size // 1024) if size >= 1024 else "%d B" % size
    print("  Пак:  %s (%s) — читай его вместо повторного поиска" % (pack, human))
'
}

# --- Hash helper ---

file_hash() {
    _input="$1"
    echo "$_input" | python3 -c "
import hashlib, sys
print(hashlib.md5(sys.stdin.read().strip().encode()).hexdigest()[:12])
"
}
