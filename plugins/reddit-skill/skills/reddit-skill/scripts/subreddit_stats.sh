#!/bin/sh
# subreddit_stats.sh — extended subreddit metrics: about + rules + moderators.
# Usage: subreddit_stats.sh --subreddit <name> [--no-cache]

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
. "$SCRIPT_DIR/common.sh"
load_config

SUB=""; NO_CACHE=""
while [ $# -gt 0 ]; do
    case "$1" in
        --subreddit|-s) SUB="$2"; shift 2 ;;
        --no-cache)     NO_CACHE=1; shift ;;
        *)              shift ;;
    esac
done
[ -n "$SUB" ] || die "Usage: subreddit_stats.sh --subreddit <name>"
SUB=$(printf '%s' "$SUB" | sed 's|^/*r/||')

DIR="$REDDIT_CACHE_DIR/subreddits"
mkdir -p "$DIR"
ABOUT="$DIR/${SUB}.json"
RULES="$DIR/${SUB}.rules.json"
MODS="$DIR/${SUB}.moderators.json"

fetch_or_cache() {
    _path="$1"; _file="$2"
    if [ -z "$NO_CACHE" ] && [ -s "$_file" ]; then
        return 0
    fi
    reddit_get "$_path" "$_file"
}

fetch_or_cache "/r/${SUB}/about"            "$ABOUT"
fetch_or_cache "/r/${SUB}/about/rules"      "$RULES"
fetch_or_cache "/r/${SUB}/about/moderators" "$MODS"

_A="$ABOUT" _R="$RULES" _M="$MODS" python3 - <<'PY'
import json, os
with open(os.environ["_A"]) as f:
    a = json.load(f).get("data", {})
print(f"r/{a.get('display_name','?')} — {a.get('title','')}")
print(f"  Subscribers   : {a.get('subscribers', 0)}")
print(f"  Active users  : {a.get('accounts_active') or a.get('active_user_count') or 0}")
print(f"  Created (UTC) : {a.get('created_utc','?')}")
print(f"  NSFW          : {a.get('over18', False)}")
print(f"  Type          : {a.get('subreddit_type','?')}")

with open(os.environ["_R"]) as f:
    r = json.load(f)
rules = r.get("rules") or []
print(f"  Rules ({len(rules)}):")
for i, rule in enumerate(rules[:15], 1):
    name = (rule.get("short_name") or rule.get("violation_reason") or "?").strip()
    print(f"    {i}. {name[:120]}")
if len(rules) > 15:
    print(f"    ... ({len(rules) - 15} more in {os.environ['_R']})")

with open(os.environ["_M"]) as f:
    m = json.load(f).get("data", {})
mods = m.get("children") or []
print(f"  Moderators ({len(mods)}):")
for mod in mods[:20]:
    name = mod.get("name", "?")
    perms = ",".join(mod.get("mod_permissions", [])) or "—"
    print(f"    u/{name:<24}  perms: {perms}")
if len(mods) > 20:
    print(f"    ... ({len(mods) - 20} more in {os.environ['_M']})")
PY

echo ""
echo "Cache:"
echo "  about     : $ABOUT"
echo "  rules     : $RULES"
echo "  moderators: $MODS"
