#!/bin/sh
# Upload a local asset to GitHub Contents API and register it in the page manifest.
#
# Usage:
#   sh github_upload.sh --file ./hero.webp --page-path my-page-path
#   sh github_upload.sh --file ./hero.webp --page-path my-page-path --name hero.webp

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
. "$SCRIPT_DIR/common.sh"

check_prerequisites
load_github_config

FILE=""
PAGE_PATH=""
ASSET_NAME=""

while [ $# -gt 0 ]; do
    case "$1" in
        --file) FILE="$2"; shift 2 ;;
        --page-path) PAGE_PATH="$2"; shift 2 ;;
        --name) ASSET_NAME="$2"; shift 2 ;;
        *) shift ;;
    esac
done

if [ -z "$FILE" ] || [ -z "$PAGE_PATH" ]; then
    echo "Usage: sh github_upload.sh --file FILE --page-path TELEGRAPH_PATH [--name NAME]" >&2
    exit 1
fi

if [ ! -f "$FILE" ]; then
    echo "Error: File not found: $FILE" >&2
    exit 1
fi

if [ -z "$ASSET_NAME" ]; then
    ASSET_NAME=$(slugify_filename "$FILE")
fi

TMP_DIR=$(make_secure_tmpdir)
trap 'rm -rf "$TMP_DIR"' EXIT

REPO_PATH="${GITHUB_ASSETS_BASE_DIR%/}/${PAGE_PATH}/${ASSET_NAME}"
MANIFEST_REPO_PATH="${GITHUB_MANIFESTS_DIR%/}/${PAGE_PATH}.json"
FILE_B64=$(base64 < "$FILE" | tr -d '\n')

ASSET_GET_RESPONSE="$TMP_DIR/asset_get.json"
ASSET_PUT_RESPONSE="$TMP_DIR/asset_put.json"
MANIFEST_GET_RESPONSE="$TMP_DIR/manifest_get.json"
MANIFEST_BODY="$TMP_DIR/manifest_body.json"
MANIFEST_PUT_RESPONSE="$TMP_DIR/manifest_put.json"

ASSET_SHA=""
MANIFEST_SHA=""

set -- \
    -H "Accept: application/vnd.github+json" \
    -H "Authorization: Bearer $GITHUB_TOKEN" \
    -H "X-GitHub-Api-Version: 2022-11-28"
ASSET_GET_CODE=$(curl -sS -o "$ASSET_GET_RESPONSE" -w '%{http_code}' "$@" \
    "${GITHUB_API}/repos/${GITHUB_ASSETS_REPO}/contents/${REPO_PATH}?ref=${GITHUB_ASSETS_BRANCH}")

case "$ASSET_GET_CODE" in
    200)
        ASSET_SHA=$(python3 - "$ASSET_GET_RESPONSE" <<'PY'
import json, sys
data = json.load(open(sys.argv[1]))
print(data.get("sha", ""))
PY
)
        ;;
    404) ;;
    *)
        echo "Error: GitHub asset lookup failed for ${REPO_PATH} (HTTP ${ASSET_GET_CODE})." >&2
        cat "$ASSET_GET_RESPONSE" >&2
        exit 1
        ;;
esac

ASSET_PAYLOAD=$(python3 - "$REPO_PATH" "$ASSET_SHA" "$GITHUB_ASSETS_BRANCH" "$FILE_B64" <<'PY'
import json, sys
repo_path, sha, branch, content = sys.argv[1:5]
payload = {
    "message": f"telegraph-publisher: upsert {repo_path}",
    "content": content,
    "branch": branch,
}
if sha:
    payload["sha"] = sha
print(json.dumps(payload, separators=(",", ":")))
PY
)

set -- \
    -H "Accept: application/vnd.github+json" \
    -H "Authorization: Bearer $GITHUB_TOKEN" \
    -H "X-GitHub-Api-Version: 2022-11-28" \
    -H "Content-Type: application/json"
ASSET_PUT_CODE=$(printf '%s' "$ASSET_PAYLOAD" | curl -sS -o "$ASSET_PUT_RESPONSE" -w '%{http_code}' "$@" \
    -X PUT \
    -d @- \
    "${GITHUB_API}/repos/${GITHUB_ASSETS_REPO}/contents/${REPO_PATH}")

case "$ASSET_PUT_CODE" in
    200|201) ;;
    *)
        echo "Error: GitHub asset upload failed for ${REPO_PATH} (HTTP ${ASSET_PUT_CODE})." >&2
        cat "$ASSET_PUT_RESPONSE" >&2
        exit 1
        ;;
esac

ASSET_META=$(python3 - "$ASSET_PUT_RESPONSE" <<'PY'
import json, sys
data = json.load(open(sys.argv[1]))
content = data.get("content", {})
commit = data.get("commit", {})
print(content.get("path", ""))
print(content.get("sha", ""))
print(commit.get("sha", ""))
PY
)

ASSET_PATH=$(printf '%s\n' "$ASSET_META" | sed -n '1p')
ASSET_SHA=$(printf '%s\n' "$ASSET_META" | sed -n '2p')
COMMIT_SHA=$(printf '%s\n' "$ASSET_META" | sed -n '3p')

if [ -z "$ASSET_PATH" ] || [ -z "$ASSET_SHA" ] || [ -z "$COMMIT_SHA" ]; then
    echo "Error: Unexpected GitHub upload response." >&2
    cat "$ASSET_PUT_RESPONSE" >&2
    exit 1
fi

CDN_URL="https://cdn.jsdelivr.net/gh/${GITHUB_ASSETS_REPO}@${COMMIT_SHA}/${ASSET_PATH}"

: > "$MANIFEST_BODY"
set -- \
    -H "Accept: application/vnd.github+json" \
    -H "Authorization: Bearer $GITHUB_TOKEN" \
    -H "X-GitHub-Api-Version: 2022-11-28"
MANIFEST_GET_CODE=$(curl -sS -o "$MANIFEST_GET_RESPONSE" -w '%{http_code}' "$@" \
    "${GITHUB_API}/repos/${GITHUB_ASSETS_REPO}/contents/${MANIFEST_REPO_PATH}?ref=${GITHUB_ASSETS_BRANCH}")

case "$MANIFEST_GET_CODE" in
    200)
        MANIFEST_SHA=$(python3 - "$MANIFEST_GET_RESPONSE" "$MANIFEST_BODY" <<'PY'
import base64, json, sys
data = json.load(open(sys.argv[1]))
print(data.get("sha", ""))
content = data.get("content", "")
if content:
    decoded = base64.b64decode(content)
    open(sys.argv[2], "w", encoding="utf-8").write(decoded.decode("utf-8"))
PY
)
        ;;
    404) ;;
    *)
        echo "Error: GitHub manifest lookup failed for ${MANIFEST_REPO_PATH} (HTTP ${MANIFEST_GET_CODE})." >&2
        cat "$MANIFEST_GET_RESPONSE" >&2
        exit 1
        ;;
esac

MANIFEST_JSON=$(python3 "$SCRIPT_DIR/github_manifest.py" merge \
    "$PAGE_PATH" "$ASSET_PATH" "$ASSET_SHA" "$CDN_URL" "$COMMIT_SHA" < "$MANIFEST_BODY")
MANIFEST_B64=$(printf '%s' "$MANIFEST_JSON" | base64 | tr -d '\n')

MANIFEST_PAYLOAD=$(python3 - "$MANIFEST_REPO_PATH" "$MANIFEST_SHA" "$GITHUB_ASSETS_BRANCH" "$MANIFEST_B64" <<'PY'
import json, sys
repo_path, sha, branch, content = sys.argv[1:5]
payload = {
    "message": f"telegraph-publisher: update manifest {repo_path}",
    "content": content,
    "branch": branch,
}
if sha:
    payload["sha"] = sha
print(json.dumps(payload, separators=(",", ":")))
PY
)

set -- \
    -H "Accept: application/vnd.github+json" \
    -H "Authorization: Bearer $GITHUB_TOKEN" \
    -H "X-GitHub-Api-Version: 2022-11-28" \
    -H "Content-Type: application/json"
MANIFEST_PUT_CODE=$(printf '%s' "$MANIFEST_PAYLOAD" | curl -sS -o "$MANIFEST_PUT_RESPONSE" -w '%{http_code}' "$@" \
    -X PUT \
    -d @- \
    "${GITHUB_API}/repos/${GITHUB_ASSETS_REPO}/contents/${MANIFEST_REPO_PATH}")

case "$MANIFEST_PUT_CODE" in
    200|201) ;;
    *)
        echo "Error: GitHub manifest update failed for ${MANIFEST_REPO_PATH} (HTTP ${MANIFEST_PUT_CODE})." >&2
        cat "$MANIFEST_PUT_RESPONSE" >&2
        exit 1
        ;;
esac

echo "$CDN_URL"
echo "GitHub asset stored: ${ASSET_PATH}" >&2
echo "Manifest updated: ${MANIFEST_REPO_PATH}" >&2
