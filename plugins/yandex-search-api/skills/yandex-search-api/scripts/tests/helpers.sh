#!/bin/sh
# Общие проверки для офлайн-тестов. Подключается через `. "$TESTS_DIR/helpers.sh"`.
# Имя не начинается на test_, поэтому run.sh не примет файл за тест.

# fail <message> — сообщить и завершить тест провалом.
fail() {
    echo "$1"
    exit 1
}

# check <json-file> <python-expr> <message-on-failure>
# Выражение видит разобранный JSON под двумя именами — `body` и `data` — и
# окружение как `os.environ`.
check() {
    _chk_file="$1"
    _chk_expr="$2"
    _chk_msg="$3"
    if ! _YSA_T_FILE="$_chk_file" _YSA_T_EXPR="$_chk_expr" python3 -c '
import json, os, sys
with open(os.environ["_YSA_T_FILE"], encoding="utf-8") as fh:
    data = json.load(fh)
body = data
sys.exit(0 if eval(os.environ["_YSA_T_EXPR"]) else 1)
'; then
        echo "$_chk_msg"
        exit 1
    fi
}
