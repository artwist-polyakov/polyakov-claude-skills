#!/bin/sh
# find_latest.sh — look up past runs in cache/index.tsv.
# Answers "did I already ask this?" without spending another API call.

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
# shellcheck disable=SC1090
. "$SCRIPT_DIR/common.sh"

usage() {
    cat <<'EOF'
Usage: sh scripts/find_latest.sh [options]

  --script NAME   limit to search | ask | research | fetch
  --match TEXT    case-insensitive substring of the query label
  --limit N       rows to print, newest first (default 10)
  --path          print only the newest matching artifact path (exit 1 if none)

Columns: created_at, script, key, query, artifact path.
EOF
}

SCRIPT_FILTER=""
MATCH=""
LIMIT="10"
PATH_ONLY=""

while [ $# -gt 0 ]; do
    case "$1" in
        --script)  require_value "$1" $#; SCRIPT_FILTER="$2"; shift 2 ;;
        --match)   require_value "$1" $#; MATCH="$2"; shift 2 ;;
        --limit)   require_value "$1" $#; LIMIT="$2"; shift 2 ;;
        --path)    PATH_ONLY=1; shift ;;
        -h|--help) usage; exit 0 ;;
        *)         usage >&2; die "Unknown option: $1" ;;
    esac
done

require_uint "--limit" "$LIMIT" 1

if [ ! -s "$PPLX_INDEX_FILE" ]; then
    if [ -n "$PATH_ONLY" ]; then
        exit 1
    fi
    echo "No runs recorded yet (${PPLX_INDEX_FILE} is empty)."
    exit 0
fi

_IDX="$PPLX_INDEX_FILE" _S="$SCRIPT_FILTER" _M="$MATCH" _N="$LIMIT" _P="$PATH_ONLY" python3 - <<'PY'
import os, sys

script_filter = os.environ.get("_S", "").strip()
match = os.environ.get("_M", "").strip().lower()
limit = int(os.environ.get("_N", "10"))
path_only = bool(os.environ.get("_P", ""))

rows = []
with open(os.environ["_IDX"], encoding="utf-8") as fh:
    for line in fh:
        parts = line.rstrip("\n").split("\t")
        if len(parts) != 5:
            continue
        created, script, key, label, path = parts
        if script_filter and script != script_filter:
            continue
        if match and match not in label.lower():
            continue
        rows.append((created, script, key, label, path))

rows.sort(key=lambda r: r[0], reverse=True)

if path_only:
    for row in rows:
        if os.path.isfile(row[4]):
            print(row[4])
            sys.exit(0)
    sys.exit(1)

if not rows:
    print("No runs match the given filters.")
    sys.exit(0)

for created, script, key, label, path in rows[:limit]:
    state = "" if os.path.isfile(path) else "  (artifact removed)"
    print(f"{created}  {script:<8} {key}  {label[:70]}{state}")
    print(f"    {path}")

if len(rows) > limit:
    print(f"... {len(rows) - limit} older matching runs — raise --limit")
PY
