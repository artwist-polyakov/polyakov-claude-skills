#!/bin/sh
# Проверка сборки и обрезки OR-запроса в сценарии missed-demand.

set -eu

TESTS_DIR="$(cd "$(dirname "$0")" && pwd)"
MISSED_DEMAND_SCRIPT="$TESTS_DIR/../missed_demand.py"

SLOTS_JSON='{
  "objects": ["телефон"],
  "actions": ["купить", "заказать"],
  "modifiers": ["ретро", "винтажный", "старинный"],
  "additional": ["в москве", "с доставкой"]
}'

BASE_RESULT=$(python3 "$MISSED_DEMAND_SCRIPT" build-query "$SLOTS_JSON")
TRIMMED_RESULT=$(
    python3 "$MISSED_DEMAND_SCRIPT" build-query "$SLOTS_JSON" \
        --max-query-length 35
)
MINIMAL_RESULT=$(
    python3 "$MISSED_DEMAND_SCRIPT" build-query \
        '{"objects":["очень-длинный-обязательный-объект"]}' \
        --max-query-length 5
)

BASE_RESULT="$BASE_RESULT" \
TRIMMED_RESULT="$TRIMMED_RESULT" \
MINIMAL_RESULT="$MINIMAL_RESULT" \
python3 - <<'PYEOF'
import json
import os


def require(condition, message):
    if not condition:
        raise SystemExit(f"FAIL: {message}")


def load_result(name):
    try:
        result = json.loads(os.environ[name])
    except (KeyError, json.JSONDecodeError) as error:
        raise SystemExit(f"FAIL: {name} не содержит корректный JSON: {error}")
    require(isinstance(result, dict), f"{name}: ожидался JSON-объект")
    require(
        result.get("query_length") == len(result.get("query", "")),
        f"{name}: query_length не совпадает с длиной query",
    )
    return result


base = load_result("BASE_RESULT")
expected_query = (
    "(купить|заказать) телефон (ретро|винтажный|старинный) "
    "(+в москве|+с доставкой)"
)
require(base.get("query") == expected_query, "обычный запрос собран неверно")
require(base.get("trimmed") == [], "обычный запрос не должен обрезаться")
require(base.get("warnings") == [], "обычный запрос не должен иметь предупреждений")

trimmed = load_result("TRIMMED_RESULT")
require(trimmed["query_length"] <= 35, "обрезанный запрос превышает предел")
require(trimmed.get("query") == "купить телефон ретро +в москве", "неверный итог обрезки")
require(
    trimmed.get("trimmed") == [
        {
            "slot": "additional",
            "removed": "+с доставкой",
            "reason": "query_length > 35",
        },
        {
            "slot": "modifiers",
            "removed": "старинный",
            "reason": "query_length > 35",
        },
        {
            "slot": "modifiers",
            "removed": "винтажный",
            "reason": "query_length > 35",
        },
        {
            "slot": "actions",
            "removed": "заказать",
            "reason": "query_length > 35",
        },
    ],
    "варианты обрезаны не в порядке additional → modifiers → actions",
)
require(trimmed.get("warnings") == [], "успешная обрезка не должна давать предупреждение")

minimal = load_result("MINIMAL_RESULT")
require(
    minimal.get("query") == "очень-длинный-обязательный-объект",
    "обязательный объект изменён или удалён",
)
require(minimal["query_length"] > 5, "проверка минимальной схемы не достигла предела")
require(minimal.get("trimmed") == [], "обязательный объект не должен попадать в trimmed")
warnings = minimal.get("warnings")
require(isinstance(warnings, list) and len(warnings) == 1, "ожидалось одно предупреждение")
warning = warnings[0]
require(
    "query_length 33" in warning and "> 5" in warning,
    "предупреждение не содержит фактическую длину и предел",
)
PYEOF

# Native Windows Python reads and writes pipes in the ANSI code page; the
# ru_RU.CP1251 locale emulates it on macOS. Cyrillic must survive both ways.
WINDOWS_RESULT=$(export LC_ALL=ru_RU.CP1251
    python3 "$MISSED_DEMAND_SCRIPT" build-query "$SLOTS_JSON")
[ "$WINDOWS_RESULT" = "$BASE_RESULT" ] || {
    echo "FAIL: build-query output is not UTF-8 under a Windows code page"
    exit 1
}

MERGE_INPUT='{"phrases":["купить телефон"],"batches":[{"phrase_indexes":[0],"slots":{"objects":["телефон"],"actions":["купить"]}}]}'
MERGE_RESULT=$(printf '%s' "$MERGE_INPUT" | python3 "$MISSED_DEMAND_SCRIPT" merge-slots)
WINDOWS_MERGE=$(export LC_ALL=ru_RU.CP1251
    printf '%s' "$MERGE_INPUT" | python3 "$MISSED_DEMAND_SCRIPT" merge-slots)
[ "$WINDOWS_MERGE" = "$MERGE_RESULT" ] || {
    echo "FAIL: merge-slots garbles Cyrillic stdin under a Windows code page"
    exit 1
}

echo "test_missed_demand_query: all passed"
