#!/bin/sh
# Delete all GitHub-backed assets for a Telegraph page using its manifest.
#
# Usage:
#   sh github_delete_page_assets.sh --page-path my-page-path

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
. "$SCRIPT_DIR/common.sh"

check_prerequisites
load_github_config

PAGE_PATH=""

while [ $# -gt 0 ]; do
    case "$1" in
        --page-path) PAGE_PATH="$2"; shift 2 ;;
        *) shift ;;
    esac
done

if [ -z "$PAGE_PATH" ]; then
    echo "Usage: sh github_delete_page_assets.sh --page-path TELEGRAPH_PATH" >&2
    exit 1
fi

TMP_DIR=$(make_secure_tmpdir)
trap 'rm -rf "$TMP_DIR"' EXIT

MANIFEST_REPO_PATH="${GITHUB_MANIFESTS_DIR%/}/${PAGE_PATH}.json"
MANIFEST_RESPONSE="$TMP_DIR/manifest_response.json"
MANIFEST_BODY="$TMP_DIR/manifest_body.json"
ASSETS_LIST="$TMP_DIR/assets.tsv"

set -- \
    -H "Accept: application/vnd.github+json" \
    -H "Authorization: Bearer $GITHUB_TOKEN" \
    -H "X-GitHub-Api-Version: 2022-11-28"
MANIFEST_GET_CODE=$(curl -sS -o "$MANIFEST_RESPONSE" -w '%{http_code}' "$@" \
    "${GITHUB_API}/repos/${GITHUB_ASSETS_REPO}/contents/${MANIFEST_REPO_PATH}?ref=${GITHUB_ASSETS_BRANCH}")

case "$MANIFEST_GET_CODE" in
    200) ;;
    404)
        echo "Error: Manifest not found for page path: ${PAGE_PATH}" >&2
        echo "Expected: ${MANIFEST_REPO_PATH}" >&2
        exit 1
        ;;
    *)
        echo "Error: GitHub manifest lookup failed for ${MANIFEST_REPO_PATH} (HTTP ${MANIFEST_GET_CODE})." >&2
        cat "$MANIFEST_RESPONSE" >&2
        exit 1
        ;;
esac

MANIFEST_SHA=$(python3 - "$MANIFEST_RESPONSE" "$MANIFEST_BODY" <<'PY'
import base64, json, sys
data = json.load(open(sys.argv[1]))
print(data.get("sha", ""))
content = data.get("content", "")
if content:
    decoded = base64.b64decode(content)
    open(sys.argv[2], "w", encoding="utf-8").write(decoded.decode("utf-8"))
PY
)

python3 "$SCRIPT_DIR/github_manifest.py" list < "$MANIFEST_BODY" > "$ASSETS_LIST"

DELETED_COUNT=0
TAB=$(printf '\t')
while IFS="$TAB" read -r ASSET_PATH ASSET_SHA ASSET_CDN_URL ASSET_COMMIT_SHA; do
    [ -z "$ASSET_PATH" ] && continue

    if [ -z "$ASSET_SHA" ]; then
        echo "Warning: Missing SHA for asset ${ASSET_PATH}, fetching current SHA..." >&2
        ASSET_META_RESPONSE="$TMP_DIR/asset_meta_$$.json"
        set -- \
            -H "Accept: application/vnd.github+json" \
            -H "Authorization: Bearer $GITHUB_TOKEN" \
            -H "X-GitHub-Api-Version: 2022-11-28"
        ASSET_META_CODE=$(curl -sS -o "$ASSET_META_RESPONSE" -w '%{http_code}' "$@" \
            "${GITHUB_API}/repos/${GITHUB_ASSETS_REPO}/contents/${ASSET_PATH}?ref=${GITHUB_ASSETS_BRANCH}")
        case "$ASSET_META_CODE" in
            200)
                ASSET_SHA=$(python3 - "$ASSET_META_RESPONSE" <<'PY'
import json, sys
data = json.load(open(sys.argv[1]))
print(data.get("sha", ""))
PY
)
                ;;
            404)
                echo "Warning: Asset already missing: ${ASSET_PATH}" >&2
                continue
                ;;
            *)
                echo "Error: Failed to fetch SHA for ${ASSET_PATH} (HTTP ${ASSET_META_CODE})." >&2
                cat "$ASSET_META_RESPONSE" >&2
                exit 1
                ;;
        esac
    fi

    DELETE_PAYLOAD=$(python3 - "$ASSET_PATH" "$ASSET_SHA" "$GITHUB_ASSETS_BRANCH" <<'PY'
import json, sys
asset_path, sha, branch = sys.argv[1:4]
print(json.dumps({
    "message": f"telegraph-publisher: delete {asset_path}",
    "sha": sha,
    "branch": branch,
}, separators=(",", ":")))
PY
)

ASSET_DELETE_RESPONSE="$TMP_DIR/delete_$$.json"
    set -- \
        -H "Accept: application/vnd.github+json" \
        -H "Authorization: Bearer $GITHUB_TOKEN" \
        -H "X-GitHub-Api-Version: 2022-11-28" \
        -H "Content-Type: application/json"
    ASSET_DELETE_CODE=$(printf '%s' "$DELETE_PAYLOAD" | curl -sS -o "$ASSET_DELETE_RESPONSE" -w '%{http_code}' "$@" \
    -X DELETE \
    -d @- \
    "${GITHUB_API}/repos/${GITHUB_ASSETS_REPO}/contents/${ASSET_PATH}")

    case "$ASSET_DELETE_CODE" in
        200)
            DELETED_COUNT=$(( DELETED_COUNT + 1 ))
            echo "Deleted asset: ${ASSET_PATH}" >&2
            ;;
        404)
            echo "Warning: Asset already missing: ${ASSET_PATH}" >&2
            ;;
        *)
            echo "Error: Failed to delete asset ${ASSET_PATH} (HTTP ${ASSET_DELETE_CODE})." >&2
            cat "$ASSET_DELETE_RESPONSE" >&2
            exit 1
            ;;
    esac
done < "$ASSETS_LIST"

MANIFEST_DELETE_PAYLOAD=$(python3 - "$MANIFEST_REPO_PATH" "$MANIFEST_SHA" "$GITHUB_ASSETS_BRANCH" <<'PY'
import json, sys
manifest_path, sha, branch = sys.argv[1:4]
print(json.dumps({
    "message": f"telegraph-publisher: delete manifest {manifest_path}",
    "sha": sha,
    "branch": branch,
}, separators=(",", ":")))
PY
)

MANIFEST_DELETE_RESPONSE="$TMP_DIR/delete_manifest.json"
set -- \
    -H "Accept: application/vnd.github+json" \
    -H "Authorization: Bearer $GITHUB_TOKEN" \
    -H "X-GitHub-Api-Version: 2022-11-28" \
    -H "Content-Type: application/json"
MANIFEST_DELETE_CODE=$(printf '%s' "$MANIFEST_DELETE_PAYLOAD" | curl -sS -o "$MANIFEST_DELETE_RESPONSE" -w '%{http_code}' "$@" \
    -X DELETE \
    -d @- \
    "${GITHUB_API}/repos/${GITHUB_ASSETS_REPO}/contents/${MANIFEST_REPO_PATH}")

case "$MANIFEST_DELETE_CODE" in
    200)
        echo "Deleted manifest: ${MANIFEST_REPO_PATH}" >&2
        ;;
    *)
        echo "Error: Failed to delete manifest ${MANIFEST_REPO_PATH} (HTTP ${MANIFEST_DELETE_CODE})." >&2
        cat "$MANIFEST_DELETE_RESPONSE" >&2
        exit 1
        ;;
esac

echo "Deleted ${DELETED_COUNT} asset(s) for page path ${PAGE_PATH}."
