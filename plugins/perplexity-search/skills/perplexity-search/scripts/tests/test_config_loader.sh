#!/bin/sh
# .env parsing: quoting, defaults, profile resolution, API key validation.

set -e

TESTS_DIR="$(cd "$(dirname "$0")" && pwd)"
SKILL_DIR="$(cd "$TESTS_DIR/../.." && pwd)"
PPLX_SCRIPT_DIR="$SKILL_DIR/scripts"
PPLX_SKILL_DIR="$SKILL_DIR"
export PPLX_SCRIPT_DIR PPLX_SKILL_DIR

TMP_DIR=$(mktemp -d "${TMPDIR:-/tmp}/pplx_config_test.XXXXXX")
trap 'rm -rf "$TMP_DIR"' EXIT INT TERM

PPLX_CONFIG_FILE="$TMP_DIR/.env"
PPLX_CACHE_DIR="$TMP_DIR/cache"
export PPLX_CONFIG_FILE PPLX_CACHE_DIR

# Deliberately unquoted values with spaces — sanitize_env.sh must fix them.
cat > "$PPLX_CONFIG_FILE" <<'EOF'
# comment line
PERPLEXITY_API_KEY=pplx-test-key

PPLX_PRESET=high
PPLX_PROFILES=news,science
PPLX_PROFILE_NEWS_LABEL=Tech news and blogs
PPLX_PROFILE_NEWS_DOMAINS=techcrunch.com,theverge.com
PPLX_PROFILE_NEWS_RECENCY=week
PPLX_PROFILE_SCIENCE_LABEL="Peer reviewed"
PPLX_PROFILE_SCIENCE_DOMAINS=nature.com
EOF

# shellcheck disable=SC1090
. "$PPLX_SCRIPT_DIR/common.sh"

load_config

[ "$PERPLEXITY_API_KEY" = "pplx-test-key" ] || { echo "key not loaded"; exit 1; }
[ "$PPLX_PRESET" = "high" ] || { echo "preset not loaded"; exit 1; }
[ -n "$PPLX_PRESET_EXPLICIT" ] || { echo "a configured preset must be marked explicit"; exit 1; }
[ "$PPLX_PROFILE_NEWS_LABEL" = "Tech news and blogs" ] || { echo "unquoted label broken"; exit 1; }
[ "$PPLX_PROFILE_SCIENCE_LABEL" = "Peer reviewed" ] || { echo "quoted label broken"; exit 1; }

# Defaults applied for anything the file did not set.
[ -z "$PPLX_MODEL" ] || { echo "model should be empty when unset"; exit 1; }
[ "$PPLX_CONTEXT_SIZE" = "medium" ] || { echo "context size default missing"; exit 1; }
[ "$PPLX_MAX_RESULTS" = "10" ] || { echo "max results default missing"; exit 1; }
[ "$PPLX_CACHE_TTL" = "900" ] || { echo "cache ttl default missing"; exit 1; }
[ -d "$PPLX_CACHE_DIR" ] || { echo "cache dir not created"; exit 1; }

# sanitize_env.sh rewrote the file in place.
grep -q '^PPLX_PROFILE_NEWS_LABEL="Tech news and blogs"$' "$PPLX_CONFIG_FILE" || {
    echo "sanitize_env did not quote the value"; exit 1
}

# --- profile resolution ---
PPLX_ARG_DOMAINS=""
PPLX_ARG_RECENCY=""
PPLX_ARG_CONTEXT_SIZE=""
resolve_profile "news"
[ "$PPLX_ARG_DOMAINS" = "techcrunch.com,theverge.com" ] || { echo "profile domains not applied"; exit 1; }
[ "$PPLX_ARG_RECENCY" = "week" ] || { echo "profile recency not applied"; exit 1; }
[ "$PPLX_PROFILE_LABEL" = "Tech news and blogs" ] || { echo "profile label not exported"; exit 1; }

# An explicit flag beats the profile.
PPLX_ARG_DOMAINS=""
PPLX_ARG_RECENCY="day"
PPLX_ARG_CONTEXT_SIZE=""
resolve_profile "news"
[ "$PPLX_ARG_RECENCY" = "day" ] || { echo "explicit flag lost to profile"; exit 1; }

# Unknown profile fails loudly.
if ( resolve_profile "nosuch" ) >/dev/null 2>&1; then
    echo "unknown profile accepted"; exit 1
fi

# Injection-shaped profile id is rejected before eval.
if ( resolve_profile 'news;id' ) >/dev/null 2>&1; then
    echo "unsafe profile id accepted"; exit 1
fi

# A preset that only comes from the built-in default must NOT read as explicit,
# or research.sh/fetch_url.sh would lose their own tuned fallbacks.
(
    PPLX_CONFIG_FILE="$TMP_DIR/missing.env"
    PERPLEXITY_API_KEY="pplx-test-key"
    PPLX_PRESET=""
    PPLX_PRESET_EXPLICIT=""
    load_config >/dev/null
    [ "$PPLX_PRESET" = "medium" ] || { echo "default preset missing"; exit 1; }
    [ -z "$PPLX_PRESET_EXPLICIT" ] || { echo "the built-in default must not read as explicit"; exit 1; }
) || exit 1

# --- inline comments: the file must read the way a POSIX shell reads it ---
(
    ENV2="$TMP_DIR/comments.env"
    cat > "$ENV2" <<'EOF'
PERPLEXITY_API_KEY=pplx-commented-key # рабочий ключ
PPLX_PRESET=high  # подороже, зато лучше
PPLX_PROFILE_A_LABEL=Tech news # мой список
PPLX_PROFILE_B_LABEL="Quoted # not a comment" # but this one is
PPLX_PROFILE_C_LABEL='single # kept'
PPLX_PROFILE_D_DOMAINS=example.com/a#b
PPLX_COUNTRY=RU
EOF

    # PPLX_PROFILE_A_LABEL is deliberately unquoted-with-spaces, which a plain
    # shell cannot load — that is what sanitize_env.sh is for. Everything else
    # here is already valid shell, so the loader must agree with shell semantics.
    PPLX_CONFIG_FILE="$ENV2"
    load_config >/dev/null

    [ "$PERPLEXITY_API_KEY" = "pplx-commented-key" ] || { echo "key: [$PERPLEXITY_API_KEY]"; exit 1; }
    [ "$PPLX_PRESET" = "high" ] || { echo "preset: [$PPLX_PRESET]"; exit 1; }
    [ "$PPLX_PROFILE_A_LABEL" = "Tech news" ] || { echo "unquoted+comment: [$PPLX_PROFILE_A_LABEL]"; exit 1; }
    # A '#' inside quotes is data, not a comment.
    [ "$PPLX_PROFILE_B_LABEL" = "Quoted # not a comment" ] || { echo "quoted hash: [$PPLX_PROFILE_B_LABEL]"; exit 1; }
    [ "$PPLX_PROFILE_C_LABEL" = "single # kept" ] || { echo "single quoted hash: [$PPLX_PROFILE_C_LABEL]"; exit 1; }
    # A '#' that does not start a word is data too (URL paths, anchors).
    [ "$PPLX_PROFILE_D_DOMAINS" = "example.com/a#b" ] || { echo "mid-word hash: [$PPLX_PROFILE_D_DOMAINS]"; exit 1; }
    [ "$PPLX_COUNTRY" = "RU" ] || { echo "country: [$PPLX_COUNTRY]"; exit 1; }

    # sanitize_env.sh must leave the file loadable by a plain shell as well.
    ( . "$ENV2" ) >/dev/null 2>&1 || { echo "sanitize_env broke shell compatibility"; exit 1; }
    grep -q '^PPLX_PROFILE_A_LABEL="Tech news" # мой список$' "$ENV2" || {
        echo "sanitize_env folded the comment into the quotes:"
        grep '^PPLX_PROFILE_A_LABEL' "$ENV2"
        exit 1
    }
) || exit 1

# --- API key validation ---
if (
    PPLX_CONFIG_FILE="$TMP_DIR/missing.env"
    PERPLEXITY_API_KEY='bad"key'
    load_config
) >/dev/null 2>&1; then
    echo "malformed API key accepted"; exit 1
fi

if (
    PPLX_CONFIG_FILE="$TMP_DIR/missing.env"
    PERPLEXITY_API_KEY=""
    load_config
) >/dev/null 2>&1; then
    echo "missing API key accepted"; exit 1
fi

echo PASS
