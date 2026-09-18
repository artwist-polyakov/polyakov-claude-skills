#!/bin/sh
# POST /v1/agent request body: presets, tools, web_search filters, schemas.

set -e

TESTS_DIR="$(cd "$(dirname "$0")" && pwd)"
SKILL_DIR="$(cd "$TESTS_DIR/../.." && pwd)"
PPLX_SCRIPT_DIR="$SKILL_DIR/scripts"
PPLX_SKILL_DIR="$SKILL_DIR"
export PPLX_SCRIPT_DIR PPLX_SKILL_DIR

TMP_DIR=$(mktemp -d "${TMPDIR:-/tmp}/pplx_agent_body_test.XXXXXX")
trap 'rm -rf "$TMP_DIR"' EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

PPLX_CONFIG_FILE="$TMP_DIR/missing.env"
PPLX_CACHE_DIR="$TMP_DIR/cache"
PERPLEXITY_API_KEY="pplx-test-key"
export PPLX_CONFIG_FILE PPLX_CACHE_DIR PERPLEXITY_API_KEY

# shellcheck disable=SC1090
. "$PPLX_SCRIPT_DIR/common.sh"
load_config

BODY="$TMP_DIR/body.json"

field() {
    _F="$1" _K="$2" python3 -c '
import json, os, sys
with open(os.environ["_F"], encoding="utf-8") as fh:
    value = json.load(fh)
for part in os.environ["_K"].split("."):
    if isinstance(value, list) and part.isdigit() and int(part) < len(value):
        value = value[int(part)]
    elif isinstance(value, dict) and part in value:
        value = value[part]
    else:
        print("<missing>")
        sys.exit(0)
print(json.dumps(value, ensure_ascii=False, sort_keys=True))
'
}

reset_args() {
    PPLX_ARG_INPUT=""
    PPLX_ARG_PRESET=""
    PPLX_ARG_MODEL=""
    PPLX_ARG_TOOLS=""
    PPLX_ARG_INSTRUCTIONS=""
    PPLX_ARG_DOMAINS=""
    PPLX_ARG_RECENCY=""
    PPLX_ARG_AFTER=""
    PPLX_ARG_BEFORE=""
    PPLX_ARG_UPDATED_AFTER=""
    PPLX_ARG_UPDATED_BEFORE=""
    PPLX_ARG_CONTEXT_SIZE=""
    PPLX_ARG_MAX_RESULTS=""
    PPLX_ARG_COUNTRY=""
    PPLX_ARG_LANGUAGE=""
    PPLX_ARG_MAX_OUTPUT_TOKENS=""
    PPLX_ARG_SCHEMA_FILE=""
    PPLX_ARG_BACKGROUND=""
}

# --- minimal body falls back to the medium preset and web_search ---
reset_args
PPLX_ARG_INPUT="what happened today"
build_agent_body "$BODY"
[ "$(field "$BODY" input)" = '"what happened today"' ] || { echo "input missing"; exit 1; }
[ "$(field "$BODY" preset)" = '"medium"' ] || { echo "default preset missing"; exit 1; }
[ "$(field "$BODY" tools.0.type)" = '"web_search"' ] || { echo "default tool missing"; exit 1; }

# --- preset and model can coexist; model overrides the preset's model ---
reset_args
PPLX_ARG_INPUT="q"
PPLX_ARG_PRESET="high"
PPLX_ARG_MODEL="perplexity/sonar"
build_agent_body "$BODY"
[ "$(field "$BODY" preset)" = '"high"' ] || { echo "preset lost"; exit 1; }
[ "$(field "$BODY" model)" = '"perplexity/sonar"' ] || { echo "model lost"; exit 1; }

# --- anthropic models need an explicit output cap ---
reset_args
PPLX_ARG_INPUT="q"
PPLX_ARG_MODEL="anthropic/claude-sonnet-5"
build_agent_body "$BODY"
[ "$(field "$BODY" max_output_tokens)" = "8192" ] || {
    echo "anthropic default max_output_tokens missing: $(field "$BODY" max_output_tokens)"; exit 1
}

reset_args
PPLX_ARG_INPUT="q"
PPLX_ARG_MODEL="anthropic/claude-sonnet-5"
PPLX_ARG_MAX_OUTPUT_TOKENS="1234"
build_agent_body "$BODY"
[ "$(field "$BODY" max_output_tokens)" = "1234" ] || { echo "explicit cap overwritten"; exit 1; }

# --- tool list and web_search config ---
reset_args
PPLX_ARG_INPUT="q"
PPLX_ARG_TOOLS="web_search,fetch_url"
PPLX_ARG_DOMAINS="-reddit.com,-quora.com"
PPLX_ARG_RECENCY="day"
PPLX_ARG_CONTEXT_SIZE="high"
PPLX_ARG_MAX_RESULTS="25"
PPLX_ARG_COUNTRY="us"
PPLX_ARG_LANGUAGE="ru"
PPLX_ARG_INSTRUCTIONS="be terse"
PPLX_ARG_BACKGROUND="1"
build_agent_body "$BODY"
[ "$(field "$BODY" tools.0.type)" = '"web_search"' ] || { echo "web_search tool missing"; exit 1; }
[ "$(field "$BODY" tools.1.type)" = '"fetch_url"' ] || { echo "fetch_url tool missing"; exit 1; }
[ "$(field "$BODY" tools.0.filters.search_domain_filter)" = '["-reddit.com", "-quora.com"]' ] || {
    echo "denylist not passed through"; exit 1
}
[ "$(field "$BODY" tools.0.filters.search_recency_filter)" = '"day"' ] || { echo "recency missing"; exit 1; }
[ "$(field "$BODY" tools.0.search_context_size)" = '"high"' ] || { echo "context size missing"; exit 1; }
[ "$(field "$BODY" tools.0.max_results)" = "25" ] || { echo "max_results missing"; exit 1; }
[ "$(field "$BODY" tools.0.user_location.country)" = '"US"' ] || { echo "user_location missing"; exit 1; }
[ "$(field "$BODY" instructions)" = '"be terse"' ] || { echo "instructions missing"; exit 1; }
[ "$(field "$BODY" language_preference)" = '"ru"' ] || { echo "language_preference missing"; exit 1; }
[ "$(field "$BODY" background)" = "true" ] || { echo "background flag missing"; exit 1; }

# --- a bare JSON Schema is wrapped into response_format ---
reset_args
PPLX_ARG_INPUT="q"
cat > "$TMP_DIR/schema.json" <<'EOF'
{"type": "object", "properties": {"answer": {"type": "string"}}, "required": ["answer"]}
EOF
PPLX_ARG_SCHEMA_FILE="$TMP_DIR/schema.json"
build_agent_body "$BODY"
[ "$(field "$BODY" response_format.type)" = '"json_schema"' ] || { echo "schema not wrapped"; exit 1; }
[ "$(field "$BODY" response_format.json_schema.name)" = '"result"' ] || { echo "schema name missing"; exit 1; }

# --- an already-shaped response_format is passed through untouched ---
reset_args
PPLX_ARG_INPUT="q"
cat > "$TMP_DIR/rf.json" <<'EOF'
{"type": "json_schema", "json_schema": {"name": "custom", "schema": {"type": "object"}}}
EOF
PPLX_ARG_SCHEMA_FILE="$TMP_DIR/rf.json"
build_agent_body "$BODY"
[ "$(field "$BODY" response_format.json_schema.name)" = '"custom"' ] || { echo "response_format double-wrapped"; exit 1; }

# --- who runs the request: CLI flag > config/.env > the script's fallback ---

# A configured preset and model must survive a script that has its own fallback,
# otherwise research.sh would bill an unintended (expensive) model.
reset_args
PPLX_PRESET_EXPLICIT=1
PPLX_PRESET="low"
PPLX_MODEL="perplexity/sonar"
resolve_model_defaults "high"
[ "$PPLX_ARG_PRESET" = "low" ] || { echo "configured preset ignored: $PPLX_ARG_PRESET"; exit 1; }
[ "$PPLX_ARG_MODEL" = "perplexity/sonar" ] || { echo "configured model ignored: $PPLX_ARG_MODEL"; exit 1; }

# A configured model alone must not get the fallback preset bolted on.
reset_args
PPLX_PRESET_EXPLICIT=""
PPLX_PRESET="medium"
PPLX_MODEL="anthropic/claude-sonnet-5"
resolve_model_defaults "high"
[ -z "$PPLX_ARG_PRESET" ] || { echo "fallback preset fought the configured model"; exit 1; }
[ "$PPLX_ARG_MODEL" = "anthropic/claude-sonnet-5" ] || { echo "configured model lost"; exit 1; }

# Nothing configured — the script's own fallback applies.
reset_args
PPLX_PRESET_EXPLICIT=""
PPLX_PRESET="medium"
PPLX_MODEL=""
resolve_model_defaults "high"
[ "$PPLX_ARG_PRESET" = "high" ] || { echo "fallback preset missing: $PPLX_ARG_PRESET"; exit 1; }
[ -z "$PPLX_ARG_MODEL" ] || { echo "model invented out of nowhere"; exit 1; }

# An explicit CLI flag beats both config and fallback, and does not drag the
# configured model along with it.
reset_args
PPLX_ARG_PRESET="xhigh"
PPLX_PRESET_EXPLICIT=1
PPLX_PRESET="low"
PPLX_MODEL="perplexity/sonar"
resolve_model_defaults "high"
[ "$PPLX_ARG_PRESET" = "xhigh" ] || { echo "CLI preset overridden"; exit 1; }
[ -z "$PPLX_ARG_MODEL" ] || { echo "config model leaked past an explicit --preset"; exit 1; }

PPLX_PRESET_EXPLICIT=""
PPLX_PRESET="medium"
PPLX_MODEL=""

# --- rejected combinations ---
reset_args
PPLX_ARG_INPUT="q"
PPLX_ARG_DOMAINS="nature.com,-reddit.com"
if ( build_agent_body "$BODY" ) 2>/dev/null; then
    echo "mixed allowlist/denylist should be rejected"; exit 1
fi

reset_args
PPLX_ARG_INPUT="q"
PPLX_ARG_RECENCY="week"
PPLX_ARG_BEFORE="01/15/2026"
if ( build_agent_body "$BODY" ) 2>/dev/null; then
    echo "recency + explicit date should be rejected"; exit 1
fi

reset_args
if ( build_agent_body "$BODY" ) 2>/dev/null; then
    echo "empty input should be rejected"; exit 1
fi

echo PASS
