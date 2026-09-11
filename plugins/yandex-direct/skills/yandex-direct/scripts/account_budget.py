#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""Недельный лимит общего счёта: чтение, установка и снятие ограничения."""

from __future__ import annotations

import argparse
import json
import sys
from decimal import Decimal, DecimalException, ROUND_DOWN
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "lib"))

from accounts import Accounts, resolve_account  # noqa: E402
from bids import currency_bounds, currency_of  # noqa: E402
from config import DirectFailure, preload_secrets, redact  # noqa: E402
from direct import Client  # noqa: E402
from money import from_api  # noqa: E402
from ui_links import account_url  # noqa: E402
from writer import AuditLog  # noqa: E402

DAYS = 7


def amount(value) -> Decimal:
    try:
        number = Decimal(str(value))
    except DecimalException:
        raise DirectFailure("Сумма должна быть числом с точкой в дробной части.") from None
    if not number.is_finite() or number < 0:
        raise DirectFailure("Сумма должна быть конечным неотрицательным числом.")
    return number


def decimal_text(number: Decimal) -> str:
    text = format(number, "f")
    return text.rstrip("0").rstrip(".") if "." in text else text


def get_budget(client, login: str) -> dict:
    result = client.call_v4("AccountManagement", {
        "Action": "Get", "SelectionCriteria": {"Logins": [login]},
    }, retry=False).result
    rows = result.get("Accounts") if isinstance(result, dict) else None
    if not isinstance(rows, list):
        raise DirectFailure("AccountManagement.Get не вернул список счетов.")
    found = [row for row in rows if isinstance(row, dict)
             and str(row.get("Login", "")).casefold() == login.casefold()]
    if len(found) != 1:
        raise DirectFailure(f"Для {login} нужен ровно один общий счёт; получено {len(found)}.")
    row = found[0]
    if type(row.get("AccountID")) is not int or row["AccountID"] <= 0 \
            or not isinstance(row.get("Currency"), str) or not row["Currency"]:
        raise DirectFailure("У общего счёта не прочитаны номер или валюта.")
    if "AccountDayBudget" not in row:
        raise DirectFailure("API не вернул AccountDayBudget: отсутствие поля не означает снятый лимит.")
    budget = row["AccountDayBudget"]
    if budget is None:
        base = Decimal(0)
    elif isinstance(budget, dict) and "Amount" in budget:
        base = amount(budget["Amount"])
    else:
        raise DirectFailure("Не удалось прочитать AccountDayBudget.Amount.")
    # Live 4 хранит сумму в валюте, не в микроединицах. Проверка связи ×7
    # с недельным лимитом интерфейса и её актуальность описаны в PLAYBOOK.md.
    return {"account_id": row["AccountID"], "login": row["Login"],
            "currency": row["Currency"], "api_amount": decimal_text(base),
            "weekly": decimal_text(base * DAYS) if base else None}


def planned_budget(client, accounts, before: dict, weekly: str) -> tuple:
    requested = amount(weekly)
    if not requested:
        raise DirectFailure("Для снятия ограничения используй clear; set требует сумму больше нуля.")
    # Это точность команды, а не задокументированное ограничение Live 4.
    # Округляем вниз, чтобы не отправить недельный лимит выше запрошенного.
    base = (requested / DAYS).quantize(Decimal("0.01"), rounding=ROUND_DOWN)
    if currency_of(accounts, before["login"]) != before["currency"]:
        raise DirectFailure("Валюта общего счёта отличается от списка кабинетов. "
                            "Обнови список: accounts.py --no-cache.")
    minimum, = currency_bounds(client, before["login"], accounts,
                              "MinimumAccountDailyBudget")
    minimum = from_api(minimum)
    if minimum <= 0:
        raise DirectFailure("Справочник не вернул положительный минимум бюджета кабинета.")
    if base < minimum:
        raise DirectFailure(f"Недельный бюджет ниже минимума: "
                            f"{decimal_text(minimum * DAYS)} {before['currency']}.")
    target = dict(before, api_amount=decimal_text(base), weekly=decimal_text(base * DAYS))
    return requested, target


def update_request(target: dict) -> dict:
    base = amount(target["api_amount"])
    value = float(base)
    if amount(value) != base:
        raise DirectFailure("Сумма не передаётся числом Live 4 без потери точности.")
    return {"Action": "Update", "Accounts": [{
        "AccountID": target["account_id"],
        "AccountDayBudget": {"Amount": value, "SpendMode": "Default"},
    }]}


def change_budget(client, accounts, login: str, action: str, *, weekly=None,
                  apply=False, audit=None, show=None) -> dict:
    before = get_budget(client, login)
    report = {"status": "read", "account_url": account_url(login),
              "before": before, "after": before, "accepted": False, "notes": []}
    if action == "get":
        return report
    if action == "set":
        requested, target = planned_budget(client, accounts, before, weekly)
        report["requested_weekly"] = decimal_text(requested)
        if requested != amount(target["weekly"]):
            report["notes"].append(
                f"Запрошено {decimal_text(requested)} {before['currency']} в неделю; "
                f"после округления базы API вниз получится {target['weekly']}. "
                "Шаг 0.01 выбран командой, точность Live 4 в справке не определена.")
    elif action == "clear":
        target = dict(before, api_amount="0", weekly=None)
    else:
        raise DirectFailure(f"Неизвестное действие: {action}.")
    request = update_request(target)
    report.update(status="preview", planned=target, request=request)
    if show:
        show(report)
    if before == target:
        report["status"] = "unchanged"
        return report
    if not apply:
        return report
    if get_budget(client, login) != before:
        raise DirectFailure("Бюджет или счёт изменился после чтения. Запрос не отправлен; "
                            "сначала проверь новое состояние.")
    audit = audit if audit is not None else AuditLog(login)
    audit.record({"operation": "account_budget", "stage": "before", **report})
    report["after"] = None
    report["status"] = "unverified"
    rejected = False
    try:
        result = client.call_v4("AccountManagement", request, retry=False).result
        rows = result.get("ActionsResult") if isinstance(result, dict) else None
        if not isinstance(rows, list) or len(rows) != 1 or not isinstance(rows[0], dict):
            raise DirectFailure("Update не вернул однозначный ActionsResult.")
        item = rows[0]
        if item.get("Errors"):
            rejected = True
            report["notes"].append("API отклонил изменение: "
                                   + json.dumps(item["Errors"], ensure_ascii=False))
        elif type(item.get("AccountID")) is not int or item["AccountID"] != before["account_id"]:
            raise DirectFailure("Update вернул другой номер счёта или не вернул его.")
        else:
            report["accepted"] = True
    except DirectFailure as failure:
        report["notes"].append(f"Ответ на запись не подтверждён: {failure}")
    # Даже после обрыва связи запрос мог выполниться. Перечитываем, но не
    # повторяем Update: у общего счёта ограничено число изменений в сутки.
    try:
        report["after"] = get_budget(client, login)
        if report["after"] == target:
            report["status"] = "verified"
        else:
            report["status"] = "rejected" if rejected else "unverified"
            report["notes"].append("Перечитанное значение отличается от плана. "
                                   "Автоматического повтора записи нет.")
    except DirectFailure as failure:
        report["notes"].append(f"Не удалось перечитать бюджет: {failure}. "
                               "Перед повтором проверь его через get или в интерфейсе.")
    try:
        audit.record({"operation": "account_budget", "stage": "after", **report})
    except DirectFailure as failure:
        report["notes"].append(f"Результат изменения не сохранён в журнале: {failure}")
    return report


def say(text):
    print(redact(str(text)), flush=True)


def warn(text):
    print(redact(str(text)), file=sys.stderr, flush=True)


def budget_text(state):
    if state is None:
        return "не удалось проверить"
    if state["weekly"] is None:
        return "без ограничения"
    return f"{state['weekly']} {state['currency']} в неделю"


def show_plan(report, output=say):
    before = report["before"]
    output(f"Кабинет {before['login']} · счёт {before['account_id']} · {before['currency']}")
    output(f"Было: {budget_text(before)} → станет: {budget_text(report['planned'])}")
    output(f"AccountDayBudget.Amount: {report['planned']['api_amount']} {before['currency']}")
    for note in report["notes"]:
        output(note)


class Parser(argparse.ArgumentParser):
    def error(self, message):
        self.exit(2, redact(f"{self.prog}: {message}\n"))


def main(argv=None) -> int:
    preload_secrets()
    parser = Parser(description=__doc__)
    steps = parser.add_subparsers(dest="action", required=True)
    for action, help_text in (("get", "Прочитать лимит"),
                              ("set", "Задать сумму за неделю"),
                              ("clear", "Снять ограничение")):
        step = steps.add_parser(action, help=help_text)
        step.add_argument("--account", help="Логин или название кабинета")
        step.add_argument("--env", choices=("production", "test_cabinet"))
        step.add_argument("--json", action="store_true")
        if action == "set":
            step.add_argument("--weekly", required=True, help="Сумма в валюте кабинета")
        if action != "get":
            mode = step.add_mutually_exclusive_group()
            mode.add_argument("--apply", action="store_true", help="Выполнить изменение")
            mode.add_argument("--dry-run", action="store_true", help="Только план (по умолчанию)")
    args = parser.parse_args(argv)
    try:
        client = Client.from_env(profile=args.env, account=args.account, warn=warn)
        accounts = Accounts.load(client, warn=warn)
        login = resolve_account(accounts, client, args.account)
        report = change_budget(
            client, accounts, login, args.action, weekly=getattr(args, "weekly", None),
            apply=getattr(args, "apply", False),
            show=lambda plan: show_plan(plan, warn if args.json else say))
    except (DirectFailure, DecimalException, ValueError, OSError) as failure:
        warn(failure)
        if args.json:
            say(json.dumps({"status": "error", "error": str(failure)}, ensure_ascii=False))
        return 1
    if args.json:
        say(json.dumps(report, ensure_ascii=False))
    else:
        status = {"read": "Текущее значение", "preview": "План, без записи",
                  "unchanged": "Уже установлено, запись не нужна",
                  "verified": "Значение подтверждено", "rejected": "API отклонил изменение",
                  "unverified": "Результат не подтверждён"}[report["status"]]
        say(f"{status}: {budget_text(report['after'])}")
        for note in report["notes"]:
            say(note)
        say(f"Кабинет: {report['account_url']}")
    return 0 if report["status"] in ("read", "preview", "unchanged", "verified") else 1


if __name__ == "__main__":
    sys.exit(main())
