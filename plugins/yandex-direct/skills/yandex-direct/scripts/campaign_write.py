#!/usr/bin/env python3
"""Создание, изменение и смена состояния кампаний.
Команды читают актуальные данные и показывают полный план. --apply
выполняет запись и проверяет результат повторным чтением.
Примеры и порядок работы: references/CHANGES.md."""

from __future__ import annotations

import argparse
import ipaddress
import json
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(SCRIPTS / "lib"))

import cache as cache_module  # noqa: E402
import money  # noqa: E402
import phrases  # noqa: E402
import policy as policies  # noqa: E402
from accounts import Accounts, resolve_account  # noqa: E402
from config import DirectFailure, excerpt, preload_secrets, redact  # noqa: E402
from direct import Client  # noqa: E402
from preferences import Preferences  # noqa: E402
from ui_links import account_url, campaign_url  # noqa: E402
from writer import (  # noqa: E402
    WHOLE_ACCOUNT, Limits, Operation, Task, Writer, showing, unrun)

import ads_write as markup_command  # noqa: E402
import bids as bids_command  # noqa: E402
import campaigns as campaign_command  # noqa: E402

SERVICE = "campaigns"

MOSCOW = timedelta(hours=3)

UNIFIED = "UNIFIED_CAMPAIGN"
TEXT = "TEXT_CAMPAIGN"
TYPE_BY_WORD = {"unified": UNIFIED, "text": TEXT}

# Половины стратегии. Имена — те, что в структуре `BiddingStrategy`.
SEARCH = "Search"
NETWORK = "Network"
HALF_RU = {SEARCH: "поиск", NETWORK: "сети"}

STRATEGY_CODES = {
    (UNIFIED, SEARCH): (
        "AVERAGE_CPA", "AVERAGE_CPA_MULTIPLE_GOALS", "AVERAGE_CPC",
        "AVERAGE_CRR", "HIGHEST_POSITION", "MAX_PROFIT", "PAY_FOR_CONVERSION",
        "PAY_FOR_CONVERSION_CRR", "PAY_FOR_CONVERSION_MULTIPLE_GOALS",
        "SERVING_OFF", "WB_MAXIMUM_CLICKS", "WB_MAXIMUM_CONVERSION_RATE"),
    (UNIFIED, NETWORK): (
        "AVERAGE_CPA", "AVERAGE_CPA_MULTIPLE_GOALS", "AVERAGE_CPC",
        "AVERAGE_CRR", "MAX_PROFIT", "NETWORK_DEFAULT", "PAY_FOR_CONVERSION",
        "PAY_FOR_CONVERSION_CRR", "PAY_FOR_CONVERSION_MULTIPLE_GOALS",
        "SERVING_OFF", "WB_MAXIMUM_CLICKS", "WB_MAXIMUM_CONVERSION_RATE"),
    (TEXT, SEARCH): (
        "AVERAGE_CPA", "AVERAGE_CPA_MULTIPLE_GOALS", "AVERAGE_CPC",
        "AVERAGE_CRR", "AVERAGE_ROI", "HIGHEST_POSITION",
        "IMPRESSIONS_BELOW_SEARCH", "MAX_PROFIT", "PAY_FOR_CONVERSION",
        "PAY_FOR_CONVERSION_CRR", "PAY_FOR_CONVERSION_MULTIPLE_GOALS",
        "SERVING_OFF", "WB_MAXIMUM_CLICKS", "WB_MAXIMUM_CONVERSION_RATE",
        "WEEKLY_CLICK_PACKAGE"),
    (TEXT, NETWORK): (
        "AVERAGE_CPA", "AVERAGE_CPA_MULTIPLE_GOALS", "AVERAGE_CPC",
        "AVERAGE_CRR", "AVERAGE_ROI", "MAXIMUM_COVERAGE", "MAX_PROFIT",
        "NETWORK_DEFAULT", "PAY_FOR_CONVERSION", "PAY_FOR_CONVERSION_CRR",
        "PAY_FOR_CONVERSION_MULTIPLE_GOALS", "SERVING_OFF",
        "WB_MAXIMUM_CLICKS", "WB_MAXIMUM_CONVERSION_RATE",
        "WEEKLY_CLICK_PACKAGE"),
}

NO_PARAMS = {
    (UNIFIED, NETWORK, "NETWORK_DEFAULT"),
    (TEXT, SEARCH, "AVERAGE_ROI"),
    (TEXT, SEARCH, "HIGHEST_POSITION"),
    (TEXT, SEARCH, "IMPRESSIONS_BELOW_SEARCH"),
    (TEXT, NETWORK, "AVERAGE_ROI"),
    (TEXT, NETWORK, "MAXIMUM_COVERAGE"),
}

NO_PARAMS_WHY = {
    "NETWORK_DEFAULT":
        "у структуры одно поле `LimitPercent`, а оно что-то значит только при "
        "`HIGHEST_POSITION` на поиске — пара, которую ЕПК отвергает кодом "
        "4000 «Стратегии не совместимы»",
    "AVERAGE_ROI": "код называет только схема сервиса, страница `add` его не "
                   "упоминает вовсе",
    "HIGHEST_POSITION": "страница `add` объявляет код допустимым и поля под "
                        "него не даёт — расхождение внутри одного документа",
    "IMPRESSIONS_BELOW_SEARCH": "код называет только схема сервиса; ни "
                                "справочник типов, ни таблица полей структуры "
                                "его не описывают",
    "MAXIMUM_COVERAGE": "страница `add` объявляет код допустимым и поля под "
                        "него не даёт; таблица полей структуры его не "
                        "описывает",
}

STRUCTURE_BY_CODE = {
    "WB_MAXIMUM_CLICKS": "MaximumClicks",
    "WB_MAXIMUM_CONVERSION_RATE": "MaximumConversionRate",
}

ONLY_ON_UPDATE = ("BudgetType",)
STRATEGY_FIELDS = {
    "AverageCpa": (("AverageCpa", "GoalId"),
                   ("WeeklySpendLimit", "CustomPeriodBudget", "BidCeiling",
                    "ExplorationBudget", "BudgetType")),
    "AverageCpaMultipleGoals": ((),
                                ("WeeklySpendLimit", "CustomPeriodBudget",
                                 "BidCeiling", "ExplorationBudget",
                                 "BudgetType")),
    "AverageCpc": (("AverageCpc",), ("WeeklySpendLimit", "CustomPeriodBudget",
                                     "BudgetType")),
    "AverageCrr": (("Crr", "GoalId"),
                   ("WeeklySpendLimit", "CustomPeriodBudget",
                    "ExplorationBudget", "BudgetType")),
    "AverageRoi": (("ReserveReturn", "RoiCoef", "GoalId"),
                   ("WeeklySpendLimit", "CustomPeriodBudget", "BidCeiling",
                    "Profitability", "ExplorationBudget", "BudgetType")),
    "HighestPosition": ((), ("WeeklySpendLimit",)),
    "MaxProfit": ((), ("WeeklySpendLimit", "CustomPeriodBudget",
                       "ExplorationBudget", "BudgetType")),
    "MaximumClicks": ((), ("WeeklySpendLimit", "BidCeiling",
                           "CustomPeriodBudget", "BudgetType")),
    "MaximumConversionRate": (("GoalId",),
                              ("WeeklySpendLimit", "BidCeiling",
                               "CustomPeriodBudget", "BudgetType")),
    "NetworkDefault": ((), ("LimitPercent",)),
    "PayForConversion": (("Cpa", "GoalId"),
                         ("WeeklySpendLimit", "CustomPeriodBudget",
                          "BudgetType")),
    "PayForConversionCrr": (("Crr", "GoalId"),
                            ("WeeklySpendLimit", "CustomPeriodBudget",
                             "BudgetType")),
    "PayForConversionMultipleGoals": ((), ("WeeklySpendLimit",
                                           "CustomPeriodBudget",
                                           "BudgetType")),
    "WeeklyClickPackage": (("ClicksPerWeek",), ("AverageCpc", "BidCeiling")),
}

NESTED_FIELDS = {
    "CustomPeriodBudget": ("SpendLimit", "StartDate", "EndDate",
                           "AutoContinue"),
    "ExplorationBudget": ("MinimumExplorationBudget",
                          "IsMinimumExplorationBudgetCustom"),
}

GOALS_RULE = "Strategy.PriorityGoals"

SHARE_OF_SPEND = tuple(
    code for half in ((UNIFIED, SEARCH), (UNIFIED, NETWORK),
                      (TEXT, SEARCH), (TEXT, NETWORK))
    for code in STRATEGY_CODES[half] if "CRR" in code)

SYSTEM_GOAL = 12

PLACEMENTS_WRITABLE = ("SearchResults", "ProductGallery")
PLACEMENTS_HANDOFF = {
    "DynamicPlaces": "записи не поддаётся: документация откладывает "
                     "управление, а отправленное значение Директ заменяет "
                     "значением SearchResults",
    "Maps": "флажку кабинета отвечает пара полей, и сетевое из них чтением не "
            "возвращается — сверка после записи проверила бы половину "
            "(замер 03.09.2026: параметр "
            "`UnifiedCampaignNetworkStrategyPlacementTypesFieldNames` "
            "принимается и значений не отдаёт)",
    "SearchOrganizationList": "кабинетный флажок шире поля — он включает ещё "
                              "отели и галерею услуг, — и что именно включит "
                              "запись, документация не говорит",
}

# Настройка, которую команда пишет, и единственная. Почему она одна — в
# док-строке модуля.
MONITORING = "ENABLE_SITE_MONITORING"

# Модели атрибуции. Устаревшие Директ конвертирует молча, с предупреждением
# (`API_OBJECTS.md`, раздел 2.3); предлагать их незачем.
ATTRIBUTION = ("AUTO", "FCCD", "LC", "LSCCD")

TRANSITIONS = {
    "suspend": ("State", "SUSPENDED"),
    "resume": ("State", None),
    "archive": ("State", "ARCHIVED"),
    "unarchive": ("State", None),
}

COULD_BE = {
    "resume": ("ON", "OFF"),
    "unarchive": ("ON", "OFF", "SUSPENDED", "ENDED"),
}

WHY_UNDETERMINED = {
    "resume": "у кампании `OFF` означает не «выключена», а «черновик, "
              "модерация, нет средств или нет активных объявлений»: кампания "
              "с деньгами и принятыми объявлениями вернётся в ON, остальные — "
              "в OFF",
    "unarchive": "разархивация возвращает кампанию в то состояние, в котором "
                 "она была до архива, и вывести его из имени метода нечем",
}

UNDETERMINED = frozenset(name for name, (_, value) in TRANSITIONS.items()
                         if value is None)
assert UNDETERMINED == set(WHY_UNDETERMINED) == set(COULD_BE), (
    "у каждой недетерминированной операции названы и причина, и возможные "
    "исходы: без вторых «итог не выводится» читается как «итогом бывает что "
    "угодно», и ожидание, которое не сбудется никогда, доходит до записи"
)

STATE_RU = {
    "suspend": "остановка",
    "resume": "возобновление",
    "archive": "архивация",
    "unarchive": "разархивация",
}

STATES = ("ON", "OFF", "SUSPENDED", "ENDED", "ARCHIVED", "CONVERTED")

DRAFT_REFUSES = {
    "suspend": "8300 «Кампания является черновиком и не может быть остановлена»",
    "archive": "8303 «Кампания является черновиком и не может быть "
               "заархивирована»",
}

ARCHIVE_NEEDS_STOPPED = ("ON",)

UNARCHIVE_REFUSES = ("CONVERTED",)

ARCHIVE_NOTE = (
    "Черновик архивации не поддаётся: `Campaigns.archive` на свежесозданной "
    "кампании отвечает 8303. Убрать такую кампанию можно только "
    "`Campaigns.delete`, а этой команде удаления нет. Черновик при этом виден "
    "в `Status`, а не в `State`: `State: OFF` означает и черновик, и модерацию, "
    "и отсутствие средств."
)

DEFAULTS_PATH = (Path(__file__).resolve().parent.parent / "references"
                 / "campaign_defaults.json")

_DEFAULTS = []


def defaults() -> dict:
    """Начальные настройки из references/campaign_defaults.json."""
    if not _DEFAULTS:
        try:
            # свой файл читается напрямую: справочник лежит в репозитории и
            # поставляется вместе со скиллом.
            _DEFAULTS.append(json.loads(
                DEFAULTS_PATH.read_text(encoding="utf-8")))
        except (OSError, ValueError) as failure:
            raise DirectFailure(
                f"Справочник начальных настроек {DEFAULTS_PATH.name} не читается: "
                f"{failure}. Без него кампания собралась бы без объяснения "
                f"настроек. Восстановите файл из репозитория."
            ) from None
    return _DEFAULTS[0]


def default_for(key: str) -> dict:
    """Одна запись справочника по ключу. Незнакомый ключ — опечатка, а не пусто.

    Пустой словарь на незнакомый ключ означал бы настройку, который молча
    перестал применяться: правило переименовали, а место применения об этом не
    узнало."""
    for one in defaults().get("defaults") or []:
        if one.get("key") == key:
            return one
    raise DirectFailure(
        f"В {DEFAULTS_PATH.name} нет настройки «{excerpt(key, 40)}». Похоже, "
        f"ключ переименован: молча пропустить его значит применить кампанию "
        f"без необходимой начальной настройки."
    )


def default_notes(applied: dict, asked: set, *, offer: bool) -> list:
    """Параметры, добавленные при создании кампании без явного аргумента."""
    return [f"Начальная настройка: {value}" for key, value in applied.items()
            if key not in asked]


def warn(text: str) -> None:
    print(redact(text), file=sys.stderr)


def say(text: str) -> None:
    print(redact(text))


def field_of(code: str) -> str:
    """Имя поля параметров по коду стратегии: верблюжий вариант кода.

    Правило врезки «Обёртка одна на все типы» раздела 6.6, и держится оно
    именно у имени **поля**: `WB_MAXIMUM_CLICKS` → `WbMaximumClicks`. Имя
    структуры из него не выводится — у трёх структур приставки `Wb` в нём нет,
    — но запрос собирается именем поля, а не именем структуры."""
    return "".join(part.capitalize() for part in code.split("_"))


def structure_of(code: str) -> str:
    """Имя строки таблицы «Поля структур» по коду стратегии."""
    return STRUCTURE_BY_CODE.get(code) or field_of(code)


def money_field(name: str) -> bool:
    """Денежное ли это поле стратегии — по перечню соседней команды.

    Перечень собран из схемы сервиса (`campaigns?wsdl`) и живёт в одном месте
    на чтение и запись: числовые поля, деньгами не являющиеся — `GoalId`,
    `ClicksPerWeek`, `Crr`, `LimitPercent`, `RoiCoef`, — в него не входят, и
    множитель к ним не применяется."""
    return name in campaign_command.MONEY_FIELDS


def strategy_params(pairs, *, where: str) -> dict:
    """Пары `Имя=значение` из командной строки — в структуру параметров.

    Имя с точкой кладётся во вложенную структуру: `CustomPeriodBudget.SpendLimit`.
    Денежное значение называется в валюте кабинета и умножается здесь — в одном
    месте на всю команду, как велит `ARCHITECTURE.md`."""
    built = {}
    for name, value in pairs:
        head, _, tail = name.partition(".")
        if not tail and head in NESTED_FIELDS:
            raise DirectFailure(
                f"{where}: {head} — вложенная структура, а не значение. Её "
                f"поля называются через точку: "
                + ", ".join(f"{head}.{one}" for one in NESTED_FIELDS[head])
                + " — и обязательны внутри неё все."
            )
        if tail:
            if head not in NESTED_FIELDS:
                raise DirectFailure(
                    f"{where}: «{excerpt(head, 40)}» — не вложенная структура "
                    f"стратегии. Вложенных две: "
                    f"{', '.join(sorted(NESTED_FIELDS))}."
                )
            if tail not in NESTED_FIELDS[head]:
                raise DirectFailure(
                    f"{where}: у структуры {head} нет поля "
                    f"«{excerpt(tail, 40)}». Её поля: "
                    f"{', '.join(NESTED_FIELDS[head])} — и все обязательны."
                )
            _once(built.setdefault(head, {}), tail, _value(tail, value, where=where),
                  path=name, where=where)
            continue
        _once(built, head, _value(head, value, where=where), path=name,
              where=where)
    return built


def _once(built: dict, key: str, value, *, path: str, where: str) -> None:
    """Один путь — одно значение. Второе с другим значением — противоречие.

    Разбирать повтор старшинством нельзя: побеждает последний, а в
    предпросмотре видна одна половина противоречивой просьбы. Для бюджета и
    потолка ставки это цена кампании, а не порядок аргументов. Повтор с тем же
    значением — не противоречие, и он проходит."""
    if key in built and built[key] != value:
        raise DirectFailure(
            f"{where}: {path} назван дважды с разными значениями — "
            f"{excerpt(built[key], 32)} и {excerpt(value, 32)}. Победил бы "
            f"последний, а человеку показали бы одну половину просьбы. "
            f"Оставьте одно."
        )
    built[key] = value


FIELD_ENUMS = {
    "BudgetType": ("WEEKLY_BUDGET", "CUSTOM_PERIOD_BUDGET"),
    "AutoContinue": ("YES", "NO"),
    "IsMinimumExplorationBudgetCustom": ("YES", "NO"),
}


def _value(name: str, text: str, *, where: str):
    """Значение параметра стратегии: деньги, дата, перечисление или целое."""
    if name in FIELD_ENUMS and name not in ("AutoContinue",
                                            "IsMinimumExplorationBudgetCustom"):
        if text.upper() not in FIELD_ENUMS[name]:
            raise DirectFailure(
                f"{where}: {name} принимает "
                f"{' или '.join(FIELD_ENUMS[name])}, получено "
                f"«{excerpt(text, 32)}»."
            )
        return text.upper()
    if name in ("AutoContinue", "IsMinimumExplorationBudgetCustom"):
        if text.upper() not in FIELD_ENUMS[name]:
            raise DirectFailure(
                f"{where}: {name} принимает YES или NO, получено "
                f"«{excerpt(text, 32)}»."
            )
        return text.upper()
    if name in ("StartDate", "EndDate"):
        return checked_date(text, where=f"{where}, {name}")
    if money_field(name):
        try:
            value = money.to_api(text)
        except money.MoneyError as failure:
            raise DirectFailure(f"{where}, {name}: {failure}") from None
        if value < 0:
            raise DirectFailure(
                f"{where}, {name}: {money.format_api(value)} — отрицательная "
                f"сумма. Ни бюджет, ни потолок ставки отрицательными не бывают."
            )
        return value
    # Разбор строгий: `int("5_0")` в Python даёт 50, и опечатка в числе уехала
    # бы в Директ значением, которого человек не набирал.
    if not text.lstrip("-").isdecimal():
        raise DirectFailure(
            f"{where}, {name}: «{excerpt(text, 32)}» — не целое число."
        )
    value = int(text)
    if value < 0:
        # Отрицательных полей у стратегии нет: деньги, доли и счётчики
        # отрицательными не бывают, и захотеть обратного нельзя.
        raise DirectFailure(
            f"{where}, {name}: {value} — отрицательное. Ни деньги, ни доли, "
            f"ни счётчики стратегии отрицательными не бывают."
        )
    if name in campaign_command.GOAL_FIELDS and value == 0:
        raise DirectFailure(
            f"{where}, {name}: ноль — не номер цели. Цели с таким номером не "
            f"бывает, и Директ отвергнет запрос целиком."
        )
    return value


def strategy_half(kind: str, half: str, code: str, params: dict,
                  *, creating: bool = False) -> dict:
    """Половина стратегии: код и, если у него есть поле, его параметры."""
    allowed = STRATEGY_CODES.get((kind, half))
    if allowed is None:
        raise DirectFailure(
            f"Тип кампании «{excerpt(kind, 40)}» эта команда не пишет. "
            f"Пишутся {', '.join(sorted(TYPE_BY_WORD.values()))}."
        )
    where = f"стратегия, половина «{HALF_RU[half]}»"
    if code not in allowed:
        raise DirectFailure(
            f"{where}: код «{excerpt(code, 40)}» у типа {kind} недопустим. "
            f"Допустимы: {', '.join(allowed)}."
        )
    body = {"BiddingStrategyType": code}
    if code == "SERVING_OFF":
        if params:
            raise DirectFailure(
                f"{where}: у `SERVING_OFF` параметров не бывает — показы "
                f"отключены, ограничивать нечего."
            )
        return body
    if (kind, half, code) in NO_PARAMS and not params:
        return body
    if (kind, half, code) in NO_PARAMS:
        raise DirectFailure(
            f"{where}: у кода «{code}» поля под параметры в форме записи нет "
            f"вовсе — {NO_PARAMS_WHY[code]}. Директ отвечает на такое поле "
            f"кодом 8000 «неизвестный параметр», и отказ приходит на весь "
            f"запрос, а не на одно поле: не записалось бы ничего. Уберите "
            f"параметры этой половины."
        )
    name = structure_of(code)
    if name not in STRATEGY_FIELDS:
        raise DirectFailure(
            f"{where}: состав структуры «{name}» в справочнике не описан "
            f"(`API_OBJECTS.md`, раздел 6.6). Собрать запрос вслепую нельзя — "
            f"отказ пришёл бы за баллы."
        )
    required, optional = STRATEGY_FIELDS[name]
    known = set(required) | set(optional)
    unknown = [one for one in params if one not in known]
    if unknown:
        raise DirectFailure(
            f"{where}: у структуры {name} нет полей "
            f"{', '.join(sorted(unknown))}. Её поля: "
            f"{', '.join(sorted(known))}."
        )
    early = [one for one in ONLY_ON_UPDATE if creating and one in params]
    if early:
        raise DirectFailure(
            f"{where}: поля {', '.join(early)} в форме создания нет — при "
            f"создании режим бюджета не выбирают, он следует из того, "
            f"заполнен ли `CustomPeriodBudget` (`API_OBJECTS.md`, раздел 6.6). "
            f"Директ отвечает на неизвестное поле кодом 8000 на весь запрос."
        )
    missing = [one for one in required if one not in params]
    if missing:
        raise DirectFailure(
            f"{where}: у структуры {name} не заполнены обязательные поля "
            f"{', '.join(missing)}."
        )
    if not params:
        return body
    for nested, fields in NESTED_FIELDS.items():
        value = params.get(nested)
        if value is None:
            continue
        absent = [one for one in fields if one not in value]
        if absent:
            raise DirectFailure(
                f"{where}: у структуры {nested} не заполнены поля "
                f"{', '.join(absent)} — внутри неё обязательны все."
            )
    if "WeeklySpendLimit" in params and "CustomPeriodBudget" in params:
        raise DirectFailure(
            f"{where}: недельный бюджет и бюджет за произвольный период "
            f"взаимоисключающи — режим бюджета следует из того, заполнен ли "
            f"`CustomPeriodBudget`."
        )
    body[field_of(code)] = params
    return body


def placements(pairs) -> dict:
    """Места показа поисковой половины: `{Имя: YES|NO}`.

    Из пяти значений пишутся два. Остальные три названы поимённо с причиной —
    причины у них разные, и складывать их в одну строку нельзя: у
    `DynamicPlaces` нет управления, у `Maps` нет проверки, у
    `SearchOrganizationList` не назван объём."""
    built = {}
    for name, value in pairs:
        if name in PLACEMENTS_HANDOFF:
            raise DirectFailure(
                f"Место показа «{name}» кодом не задаётся: "
                f"{PLACEMENTS_HANDOFF[name]}. Оно остаётся человеку — "
                f"переключателем в кабинете."
            )
        if name not in PLACEMENTS_WRITABLE:
            raise DirectFailure(
                f"Место показа «{excerpt(name, 40)}» неизвестно. Записываются "
                f"{', '.join(PLACEMENTS_WRITABLE)}."
            )
        _once(built, name, value, path=name, where="места показа")
    return built


GOAL_OPERATION = "SET"


def priority_goals(pairs, metrika_source, *, updating: bool) -> dict:
    """Приоритетные цели с ценностью конверсии."""
    named = set(metrika_source)
    items, seen = [], {}
    for goal, value in pairs:
        if goal in seen and seen[goal] != value:
            raise DirectFailure(
                f"Цель {goal} названа дважды с разной ценностью — "
                f"{money.format_api(seen[goal])} и "
                f"{money.format_api(value)}. Номер цели и есть ключ записи: "
                f"Директ отвергнет запрос как повтор, а показали бы человеку "
                f"одну половину просьбы. Оставьте одно значение."
            )
        if goal in seen:
            continue
        seen[goal] = value
        item = {"GoalId": goal, "Value": value}
        if goal in named:
            item["IsMetrikaSourceOfValue"] = "YES"
            named.discard(goal)
        if updating:
            item["Operation"] = GOAL_OPERATION
        items.append(item)
    if named:
        raise DirectFailure(
            f"Цели {', '.join(str(one) for one in sorted(named))} названы "
            f"источником ценности из Метрики, а самих целей среди "
            f"приоритетных нет. Аргумент, принятый и не применённый, — это "
            f"половина просьбы, выданная за целое."
        )
    return {"Items": items}


def fits_goals(items: list, named, effective=None) -> None:
    """Что не так с приоритетными целями — по справочнику и до запроса."""
    effective = named if effective is None else effective
    rule = Limits.load().data.get("collections", {}).get(GOALS_RULE) or {}
    top = rule.get("max")
    if top and len(items) > top:
        raise DirectFailure(
            f"Приоритетных целей {len(items)} при пределе {top}."
        )
    needy = set(rule.get("required_when_strategy_in") or ())
    hit = sorted(one for one in named if one in needy)
    if hit and not items:
        raise DirectFailure(
            f"Стратегия {', '.join(hit)} требует приоритетных целей, а их "
            f"нет: назовите --goal."
        )
    multiple = set(rule.get("multiple_goals_strategies") or ())
    low = rule.get("min_for_multiple_goals_strategies")
    both = sorted(one for one in effective if one in multiple)
    if both and items and low and len(items) < low:
        raise DirectFailure(
            f"Стратегия {', '.join(both)} требует не меньше {low} целей, "
            f"названо {len(items)}. Директ отвечает кодами 7000 и 4000."
        )
    if both and items and all(one["GoalId"] == SYSTEM_GOAL for one in items):
        raise DirectFailure(
            f"Все названные цели — системная {SYSTEM_GOAL} «вовлечённые "
            f"сессии». Стратегия {', '.join(both)} требует хотя бы одну цель "
            f"Метрики."
        )


def campaign_start(text: str, *, where: str) -> str:
    """Дата начала показов: разобранная и не в прошлом."""
    value = checked_date(text, where=where)
    today = (datetime.now(timezone.utc) + MOSCOW).date().isoformat()
    if value < today:
        raise DirectFailure(
            f"{where}: {value} уже прошло — показы кампании начинаются не "
            f"раньше сегодняшнего дня, а сегодня по Москве {today} "
            f"(`API_OBJECTS.md`, раздел 2.2). Директ отвечает на прошедшую "
            f"дату отказом за баллы."
        )
    return value


def named_text(value, *, where: str) -> str:
    """Названное человеком значение: пустым оно не бывает."""
    if value is None:
        return None
    if not str(value).strip():
        raise DirectFailure(
            f"{where}: значение пустое. Названный аргумент — это просьба, и "
            f"пустая просьба испорчена, а не отсутствует: она молча выпала бы "
            f"из запроса, а команда отчиталась бы успехом. Уберите аргумент — "
            f"или назовите то, что просите."
        )
    return str(value)


def checked_date(text: str, *, where: str) -> str:
    """Дата в формате `YYYY-MM-DD`, разобранная, а не принятая на слово.

    Директ отвергает негодную дату кодом 4000 за баллы, а разбор здесь ловит
    и опечатку в порядке частей: `2026-13-01` — не дата, а `01-09-2026` уехало
    бы в поле строкой и вернулось бы оттуда расхождением сверки."""
    text = named_text(text, where=where)
    parts = text.split("-")
    if len(parts) != 3 or not all(one.isdecimal() for one in parts):
        raise DirectFailure(
            f"{where}: «{excerpt(text, 32)}» — не дата вида ГГГГ-ММ-ДД."
        )
    try:
        year, month, day = (int(one) for one in parts)
        return date(year, month, day).isoformat()
    except ValueError as failure:
        raise DirectFailure(f"{where}: «{excerpt(text, 32)}» — {failure}.") from None


def read_params(kind: str, *, common=(), typed=(), places=()) -> dict:
    """Параметры `Campaigns.get` для конвейера: что перечитывать после записи."""
    body = campaign_command.TYPE_BODY[kind]
    params = {"FieldNames": ["Id", "Name", "Type", *common]}
    if typed:
        params[f"{body}FieldNames"] = list(typed)
    if places:
        params[f"{body}SearchStrategyPlacementTypesFieldNames"] = list(places)
    return params


def read_any_type(*, common=(), typed=(), places=()) -> dict:
    """Параметры чтения, когда тип кампании ещё не известен."""
    params = {"FieldNames": ["Id", "Name", "Type", *common]}
    for kind in TYPE_BY_WORD.values():
        body = campaign_command.TYPE_BODY[kind]
        params[f"{body}FieldNames"] = sorted(
            set(typed) | {"PackageBiddingStrategy"})
        if places:
            params[f"{body}SearchStrategyPlacementTypesFieldNames"] = list(places)
    return params


def read_campaigns(client, account, accounts, ids, params: dict) -> dict:
    """Кампании по идентификаторам: `{идентификатор: ответ}`.

    Мимо кэша, потому что читает команда записи: устаревший снимок — это
    запись поверх чужой правки, и сверка «было → станет» сошлась бы со
    вчерашними данными."""
    request = dict(params)
    request["SelectionCriteria"] = {"Ids": [int(one) for one in ids]}
    need = Limits.load().units_cost(SERVICE, "get", len(ids))
    found = {}
    for item in client.get_all(
            SERVICE, request, account=account,
            use_operator_units=lambda: accounts.use_operator_units(
                account, need=need)):
        found[item.get("Id")] = item
    phrases.all_named(list(found), ids, "кампании")
    phrases.campaign_here(client, account, accounts, ids)
    return found


def one_campaign(client, account, accounts, campaign, params: dict) -> dict:
    return read_campaigns(client, account, accounts, [campaign], params)[
        int(campaign)]


def writable_type(record: dict) -> str:
    """Тип кампании, которую команда берётся править, — из свежего чтения."""
    package = (record.get(campaign_command.TYPE_BODY.get(record.get("Type"))
                          or "") or {}).get("PackageBiddingStrategy")
    if package:
        raise DirectFailure(
            f"Кампания {record.get('Id')} привязана к пакетной стратегии "
            f"{(package or {}).get('StrategyId') or ''}, и настройки самой "
            f"кампании изменить нельзя — редактируется стратегия "
            f"(`API_OBJECTS.md`, раздел 6.4). Собственные `BiddingStrategy`, "
            f"`PriorityGoals`, `CounterIds` и `AttributionModel` у неё "
            f"отобраны, и отправленные они получили бы отказ Директа за баллы. "
            f"Чтобы вернуть кампании свободу, её отвязывают: новое значение "
            f"`BiddingStrategy` вместе с `PackageBiddingStrategy: null`. "
            f"Отвязка этой командой не делается — строка "
            f"`campaign_package_strategy` матрицы покрытия стоит `later`."
        )
    kind = record.get("Type")
    if kind not in TYPE_BY_WORD.values():
        raise DirectFailure(
            f"Кампания {record.get('Id')} имеет тип "
            f"«{excerpt(kind, 40)}» ({campaign_command.TYPE_RU.get(kind, '—')}), "
            f"а эта команда пишет только "
            f"{', '.join(sorted(TYPE_BY_WORD.values()))}. Структура правки у "
            f"каждого типа своя, и записанная не в ту Директом отвергается "
            f"целиком."
        )
    return kind


def items_of(value) -> list:
    """Массив Директа: `{"Items": [...]}`, голый список или пусто."""
    return list(phrases.items_of(value))


def combined(current, wanted, mode: str) -> list:
    """Итоговый список: сложить, вычесть или заменить — поверх свежего чтения."""
    current = [str(one) for one in current]
    wanted = [str(one) for one in wanted]
    if mode == "replace":
        return _unique(wanted)
    if mode == "add":
        known = set(current)
        return _unique(current + [one for one in wanted if one not in known])
    unwanted = set(wanted)
    return _unique([one for one in current if one not in unwanted])


def named_values(values, *, where: str) -> list:
    """Названный список: пустых значений в нём не бывает.

    То же правило, что и у одиночного значения, только по элементу: `--site ""`
    ничего не называет, а уезжает в Директ пустой строкой или тихо остаётся в
    списке. Проверяется здесь, потому что список приходит обоими путями —
    созданием и правкой, — и правило, поставленное на один, обходится вторым."""
    empty = [number for number, one in enumerate(values, 1)
             if not str(one).strip()]
    if empty:
        raise DirectFailure(
            f"{where}: значение {', '.join(str(one) for one in empty[:5])} "
            f"пустое. Названный аргумент — это просьба, и пустая просьба "
            f"испорчена, а не отсутствует."
        )
    return [str(one) for one in values]


def _unique(values: list) -> list:
    """Порядок сохраняется, повторы снимаются: Директ дубли принимает и хранит,
    а человеку в предпросмотре одно и то же значение дважды показывать незачем."""
    seen, found = set(), []
    for one in values:
        if one not in seen:
            seen.add(one)
            found.append(one)
    return found


def cabinet_markup(client, account, accounts, campaign):
    """Действующая разметка — до сборки задачи, отдельным шагом."""
    if campaign is not None:
        return markup_command.read_markup(client, account, accounts, campaign)
    return markup_command.read_cabinet_markup(client, account, accounts, None)


def chosen_markup(args, markup, remembered, campaign):
    """Чем размечать кампанию: строка, схема, откуда взята и что запоминать."""
    if getattr(args, "no_tracking", False):
        return None, "параметры URL не задаются — так просили", None
    if args.tracking is not None:
        if not args.tracking.strip():
            raise DirectFailure(
                "--tracking назван пустым: это испорченная просьба, а не "
                "отказ от разметки. Кампания завелась бы без параметров URL "
                "по аргументу, который просил их задать. Оставить кампанию "
                "без разметки просят явно: --no-tracking."
            )
        return args.tracking, "названо аргументом --tracking", None
    record = remembered.recall("utm_scheme")
    if (campaign is None and not args.utm_scheme and not record
            and not markup.schemes()):
        raise DirectFailure(
            "Чем размечать кампанию, не выбрано, а угадать нечем: в "
            "параметрах URL кампаний кабинета меток utm_* нет, метки в самих "
            "ссылках при создании не читались (своих объявлений у кампании "
            "ещё нет), запомненного выбора тоже нет. Шаблон UTM, "
            "подставленный молча, завёл бы кабинету вторую схему, если "
            "прежняя зашита в адреса. Назовите одно из трёх: "
            "--utm-scheme template — размечать шаблоном; --tracking «…» — "
            "своей строкой; --no-tracking — оставить кампанию без параметров "
            "URL и разметить её потом командой tracking --campaign <номер>, "
            "которая ссылки читает."
        )
    if args.utm_scheme == "cabinet":
        scheme, source = markup_command.cabinet_scheme(markup), \
            "действующая схема кабинета"
    elif args.utm_scheme == "template":
        scheme, source = markup_command.UTM_TEMPLATE, "шаблон UTM"
    elif record:
        scheme, source = record, "запись настроек кабинета"
    else:
        scheme, source = markup_command.UTM_TEMPLATE, \
            "шаблон UTM, запомненного выбора нет"
    fresh = (args.utm_scheme and scheme != record
             and not (record is None and scheme == markup_command.UTM_TEMPLATE))
    name = (markup.campaign(campaign).get("Name") or "") if campaign else \
        (args.name or "")
    return (markup_command.applied_scheme(scheme, name), source,
            scheme if fresh else None)


def markup_notes(markup, campaign, tracking, source, name=None) -> list:
    """Разметка кабинета — человеку, до вопроса и до записи."""
    lines = markup_command.markup_read_notes(markup, campaign)
    if campaign is None:
        lines.append(
            "Метки в самих ссылках здесь не читались: своих объявлений у "
            "создаваемой кампании ещё нет, а обход чужих стоил бы сотни "
            "вызовов — `AdGroups.get` и `Ads.get` принимают по десять кампаний "
            "за вызов. Схема кабинета названа по параметрам URL его кампаний, "
            "и кабинет со старой схемой, зашитой в адреса, отсюда не виден "
            ". Прочитать их можно после создания: tracking --campaign "
            "<номер>.")
    marked = markup.marked_links()
    if marked:
        where, href = marked[0][0], marked[0][1]
        if tracking:
            price = ("Параметры кампании приписываются объявлениям всех её "
                     "групп, и с этими адресами они столкнутся сразу: адрес "
                     "получит и свою метку, и параметры кампании, а "
                     "двойной разметки быть не должно.")
        else:
            price = ("Разметка кабинета живёт в адресах — второе законное "
                     "место для разметки; эта запись их не трогает.")
        lines.append(
            f"Метки utm_* уже стоят в ссылках: {len(marked)} шт., например "
            f"{where} — {excerpt(href, 60)}. {price}")
        doubled = markup.doubled_links()
        if doubled:
            lines.append(
                f"У {len(doubled)} из них разметка стоит и над адресом — в "
                f"параметрах кампании или группы: двойная разметка уже есть "
                f", и эта запись её не создаёт и не чинит.")
    if tracking:
        lines.append(f"Параметры URL кампании ({source}): "
                     f"{excerpt(tracking, 200)}")
        lines += markup_command.campaign_mark_notes(
            tracking, (markup.campaign(campaign).get("Name") if campaign
                       else name))
    if markup_command.other_schemes(markup):
        lines.append("Переход кабинета на одну схему — отдельная задача с "
                     "датой, и здесь он не делается.")
    return lines


def add_operation(kind: str, name: str, body: dict, *, start: str, end=None,
                  negative=(), blocked=(), excluded=()):
    """Создание кампании: `Campaigns.add`.

    Чем искать созданное после сорвавшегося вызова — названо: `Campaigns.get`
    перечисляет кабинет и без `SelectionCriteria`, а кампания узнаётся по
    `Name`. Проверено чтением 03.09.2026 на `api-artwist-test`: вызов без
    отбора отвечает списком кампаний, `Name` приходит тем же, каким уходил."""
    item = {"Name": name, "StartDate": start, campaign_command.TYPE_BODY[kind]: body}
    if end is not None:
        item["EndDate"] = end
    if negative:
        negative = _unique(named_values(negative, where="--negative"))
        phrases.fits_negative(negative, "campaign")
        item["NegativeKeywords"] = {"Items": negative}
    blocked = _unique(named_values(blocked, where="--ip"))
    excluded = _unique(named_values(excluded, where="--site"))
    if blocked:
        fits_addresses(blocked)
        fits_collection("Campaign.BlockedIps", blocked, "запрещённых IP")
        item["BlockedIps"] = {"Items": blocked}
    if excluded:
        fits_collection("Campaign.ExcludedSites", excluded, "площадок")
        item["ExcludedSites"] = {"Items": excluded}
    what = {
        "Name": "название",
        "StartDate": "начало показов",
        "EndDate": "окончание показов",
        "NegativeKeywords": "минус-фразы кампании",
        "BlockedIps": "запрещённые IP",
        "ExcludedSites": "запрещённые площадки",
        campaign_command.TYPE_BODY[kind]:
            f"параметры кампании ({campaign_command.TYPE_RU[kind]})",
    }
    changes = [policies.Change(object_id=name, what=what[field], field=field,
                               after=item[field], service="кампания")
               for field in item]
    return Operation(
        SERVICE, "add", params_key="Campaigns", items=[item], labels=[name],
        changes=changes, search=("Name", WHOLE_ACCOUNT),
        read=read_params(kind, common=create_common(item),
                         typed=create_typed(body),
                         places=create_places(body)),
        texts=create_rules(item)[0],
        collections=create_rules(item)[1],
        phrases=("NegativeKeywords.Items",),
        full=(f"{campaign_command.TYPE_BODY[kind]}.Settings",),
    )


def create_common(item: dict) -> list:
    """Общие поля чтения, которые правда уходят в запрос создания."""
    return [one for one in ("StartDate", "EndDate", "NegativeKeywords",
                            "BlockedIps", "ExcludedSites") if one in item]


def create_typed(body: dict) -> list:
    return [one for one in ("BiddingStrategy", "Settings", "CounterIds",
                            "PriorityGoals", "TrackingParams",
                            "AttributionModel",
                            "NegativeKeywordSharedSetIds") if one in body]


def create_places(body: dict) -> list:
    search = (body.get("BiddingStrategy") or {}).get(SEARCH) or {}
    return sorted(search.get("PlacementTypes") or ())


FIELD_RULES = {
    "Name": {"text": "Campaign.Name"},
    "BlockedIps": {"collection": "Campaign.BlockedIps"},
    "ExcludedSites": {"collection": "Campaign.ExcludedSites",
                      "item": "Campaign.ExcludedSites.item"},
    "NegativeKeywordSharedSetIds": {},
    "CounterIds": {},
    "NegativeKeywords": {},
    "TimeTargeting": {},
    "PriorityGoals": {"collection": GOALS_RULE},
}


def rules_for(field: str, path: str) -> tuple:
    """Правила поля: `(текстовые, на состав)` под данным путём записи.

    Путь называется отдельно от имени, потому что одно и то же поле лежит то
    сверху объекта, то внутри типовой структуры: `ExcludedSites` у кампании и
    `UnifiedCampaign.PriorityGoals` у неё же. Правило принадлежит полю, а место
    — форме запроса."""
    known = FIELD_RULES.get(field) or {}
    texts, collections = {}, {}
    if known.get("text"):
        texts[path] = known["text"]
    if known.get("item"):
        texts[f"{path}.Items"] = known["item"]
    if known.get("collection") and known["collection"] in _known_collections():
        collections[path] = known["collection"]
    return texts, collections


def _known_collections() -> set:
    return set(Limits.load().data.get("collections") or ())


def create_rules(item: dict) -> tuple:
    """Правила для полей, которые правда уходят в запрос создания."""
    texts, collections = {}, {}
    for path in dict.fromkeys(path for path, _ in _fields(item)):
        here, there = rules_for(path.rsplit(".", 1)[-1], path)
        texts.update(here)
        collections.update(there)
    return texts, collections


def fits_addresses(values) -> None:
    """Каждый запрещённый адрес — настоящий IP, и разбирается он до запроса."""
    wrong = []
    for one in values:
        try:
            ipaddress.ip_network(str(one), strict=False)
        except ValueError:
            wrong.append(str(one))
    if wrong:
        raise DirectFailure(
            f"Не адреса: {', '.join(excerpt(one, 40) for one in wrong[:5])}"
            + (f" и ещё {len(wrong) - 5}" if len(wrong) > 5 else "")
            + ". Поле `BlockedIps` принимает IP-адреса, и строку Директ "
              "отвергает вместе со всем пакетом."
        )


def fits_collection(rule: str, values, said: str) -> None:
    """Сколько элементов допускает массив — по справочнику, до запроса."""
    limit = (Limits.load().data.get("collections", {}).get(rule) or {}).get("max")
    if not isinstance(limit, int) or isinstance(limit, bool) or limit < 1:
        raise DirectFailure(
            f"В справочнике лимитов нет предела массива «{rule}». Без него "
            f"запись шла бы вслепую, а отказ приходил бы от Директа за баллы."
        )
    if len(values) > limit:
        raise DirectFailure(
            f"{said.capitalize()} {len(values)} при пределе {limit}. Директ "
            f"откажет, и отказ уронит весь пакет целиком."
        )


def update_operation(campaign: int, kind: str, *, record, common=None,
                     typed=None, what=None, read=None, was=None,
                     guard_also=None, **over) -> Operation:
    """Правка кампании: `Campaigns.update`, поля называет вызывающий код.

    Плоским элементом типовые поля не передаются: `Settings`, `BiddingStrategy`
    и соседи живут **внутри** типовой структуры (`UnifiedCampaign`,
    `TextCampaign`), и посланные рядом с `Id` они до кампании не доедут, а
    сверка после записи сойдётся с непрочитанным."""
    body = campaign_command.TYPE_BODY[kind]
    item = {"Id": int(campaign)}
    item.update(common or {})
    if typed:
        item[body] = dict(typed)
    read = dict(read or {})
    name = f"{body}FieldNames"
    read[name] = sorted(set(read.get(name) or ()) | {"PackageBiddingStrategy"})
    guard_also = phrases.every(
        guard_also,
        unchanged_at({f"{body}.PackageBiddingStrategy":
                      (record.get(body) or {}).get("PackageBiddingStrategy")}))
    changes = []
    for field, said in (what or {}).items():
        changes.append(policies.Change(
            object_id=int(campaign), what=said, field=field,
            after=_at(item, field), service="кампания"))
    return Operation(
        SERVICE, "update", params_key="Campaigns", items=[item],
        changes=changes, read=read,
        guard=phrases.every(
            phrases.replaced_from(was) if was is not None else None,
            guard_also),
        **over)


def _at(item: dict, path: str):
    value = item
    for part in path.split("."):
        value = value[part]
    return value


def state_operation(method: str, campaign: int, state=None, *,
                    guard=None) -> Operation:
    """Смена состояния: `suspend`, `resume`, `archive`, `unarchive`.

    Ожидаемое состояние называется прямо, а не выводится из имени метода. Без
    него перечитывание подтвердит лишь то, что объект на месте: у `archive`
    признак «объект исчезнет» не выводится ниоткуда, и сверка по отправленному
    идентификатору сошлась бы с неархивированной кампанией."""
    field, default = TRANSITIONS[method]
    if state and default and state != default:
        raise DirectFailure(
            f"{STATE_RU[method].capitalize()} переводит кампанию в "
            f"«{default}», а ожидается «{excerpt(state, 32)}». Сверка после "
            f"записи не сошлась бы — но правка к тому времени уже случилась. "
            f"Уберите --expect-state: у этой операции итог известен."
        )
    if state and method in COULD_BE and state not in COULD_BE[method]:
        raise DirectFailure(
            f"{STATE_RU[method].capitalize()} не может кончиться состоянием "
            f"«{excerpt(state, 32)}»: {WHY_UNDETERMINED[method]}. Ожидание "
            f"сбылось бы никогда, а узналось бы это после записи — операция к "
            f"тому времени уже случилась. Возможные исходы: "
            f"{', '.join(COULD_BE[method])}."
        )
    value = state or default
    if value is None:
        raise DirectFailure(
            f"{STATE_RU[method].capitalize()}: назовите ожидаемое состояние "
            f"--expect-state. {WHY_UNDETERMINED[method]}. Возможные исходы: "
            f"{', '.join(COULD_BE[method])}."
        )
    return Operation(
        SERVICE, method, selection="Ids", items=[{"Id": int(campaign)}],
        expect=[{"Id": int(campaign), field: value}],
        read={"FieldNames": sorted({"Id", "Name", "Type", field}
                                   | ({"Funds"} if guard else set()))},
        guard=guard,
        changes=[policies.Change(
            object_id=int(campaign), what=STATE_RU[method], field=field,
            after=value, service="кампания")],
    )


def create_task(client, account, accounts, args):
    """Создание кампании: стратегия, цели, разметка, минусовка."""
    kind = TYPE_BY_WORD[args.type]
    named_text(args.name, where="--name")
    remembered = Preferences(account, warn=warn)
    markup = cabinet_markup(client, account, accounts, None)
    tracking, source, offer = chosen_markup(args, markup, remembered, None)
    body = campaign_body(args, kind, creating=True)
    fits_money_bounds(client, account, accounts, body)
    applied, asked = {}, named_by_hand(args)
    if tracking:
        body["TrackingParams"] = tracking
        applied["tracking_params"] = f"разметка ({source})"
        origin = markup_command.marks_of(tracking).get("utm_source")
        applied["utm_source"] = (
            f"источник размечен значением «{excerpt(origin, 40)}»"
            if origin else
            "источник не размечен: utm_source в строке пуст или не задан")
    if args.attribution is None:
        rule = default_for("attribution_model")
        body["AttributionModel"] = rule["value"]
        applied["attribution_model"] = f"модель атрибуции {rule['value']}"
    negative = list(args.negative)
    if args.starter_negatives:
        rule = default_for("negative_keywords_starter")
        negative = combined(negative, rule["value"], "add")
        applied["negative_keywords_starter"] = (
            f"стартовый слой минус-фраз: {len(rule['value'])} шт.")
    applied["daily_budget"] = "дневной бюджет не передаётся"
    applied["created_as_draft"] = "кампания заводится черновиком"
    notes = markup_notes(markup, None, tracking, source, name=args.name)
    operation = add_operation(
        kind, args.name, body,
        start=campaign_start(args.start, where="--start"),
        end=(checked_date(args.end, where="--end")
             if args.end is not None else None),
        negative=negative, blocked=args.ip, excluded=args.site)
    notes += rule_notes(args, applied, operations=[operation])
    pending = [("utm_scheme", offer, f"схему разметки «{excerpt(offer, 60)}»")] \
        if offer else []
    return (f"создание кампании «{args.name}»", [operation], notes, pending,
            remembered)


def rule_notes(args, applied=None, operations=()) -> list:
    """Назвать автоматически добавленные параметры; явные правки есть в плане."""
    return default_notes(applied or {}, named_by_hand(args), offer=False)


def named_by_hand(args) -> set:
    """Дефолты, про которые человек уже сказал сам, — их объяснять незачем.

    Перечень идёт от аргумента к ключу справочника, а не наоборот: правило,
    у которого аргумента нет вовсе, из показа выпасть не должно — оно как раз
    и есть то, о чём человек не знает."""
    said = set()
    args = argparse.Namespace(**{
        name: getattr(args, name, None) for name in
        ("attribution", "tracking", "utm_scheme", "no_tracking", "negative",
         "starter_negatives", "weekly_budget", "search_strategy",
         "network_strategy", "placement", "set", "counter", "on", "day",
         "ip", "goal", "goal_from_metrika")})
    if args.attribution:
        said.add("attribution_model")
    if (args.tracking is not None or args.utm_scheme
            or getattr(args, "no_tracking", False)):
        said.update({"tracking_params", "utm_source"})
    if args.negative or args.starter_negatives:
        said.add("negative_keywords_starter")
    if args.set:
        said.add("negative_sets")
    if args.weekly_budget is not None:
        said.add("weekly_spend_limit")
    if args.goal or args.goal_from_metrika:
        said.add("priority_goals")
    if args.search_strategy or args.network_strategy:
        said.update({"strategy_scale", "manual_search_only"})
    if args.placement:
        said.add("placements")
    if args.on is not None:
        said.add("site_monitoring")
    if args.day:
        said.add("schedule_spend")
    if args.ip:
        said.add("blocked_ips")
    return said


NEEDS_HALF = {
    "search_param": ("--search-param", "--search-strategy"),
    "placement": ("--placement", "--search-strategy"),
    "network_param": ("--network-param", "--network-strategy"),
}


def kind_of(args) -> str:
    """Тип кампании, под который собираются аргументы вызова.

    У создания он назван аргументом, у правки — прочитан из кабинета и
    подставлен сюда вызывающим кодом. Умолчания нет: перечни допустимых кодов
    и наличие полей у типов разные, и «наверное, ЕПК» здесь означало бы
    проверку не того типа."""
    return getattr(args, "campaign_kind", None) or TYPE_BY_WORD[
        getattr(args, "type", None) or "unified"]


def carries_budget(kind: str, half: str, code: str) -> bool:
    """Есть ли у этой половины поле `WeeklySpendLimit`.

    Ответ собирается из тех же перечней, что и сборка запроса: у `SERVING_OFF`
    параметров не бывает, у клетки из `NO_PARAMS` поля нет вовсе, а у
    остальных смотрится состав структуры по справочнику."""
    part = SEARCH if half == "--search-strategy" else NETWORK
    if code == "SERVING_OFF" or (kind, part, code) in NO_PARAMS:
        return False
    required, optional = STRATEGY_FIELDS.get(structure_of(code), ((), ()))
    return "WeeklySpendLimit" in set(required) | set(optional)


def fits_strategy_arguments(args) -> None:
    """Что названо без своей половины — отказ, а не молчаливый пропуск."""
    named = {"--search-strategy": args.search_strategy,
             "--network-strategy": args.network_strategy}
    for field, (option, half) in NEEDS_HALF.items():
        if getattr(args, field, None) and not named[half]:
            raise DirectFailure(
                f"{option} назван без {half}: положить его некуда, и в запрос "
                f"он не уедет. Остальная правка при этом прошла бы, и запись "
                f"отчиталась бы успехом без того, что вы просили. Назовите "
                f"{half} — или уберите {option}."
            )
    if args.weekly_budget is None:
        return
    if not any(named.values()):
        raise DirectFailure(
            "--weekly-budget назван без стратегии: недельный бюджет живёт "
            "внутри структуры половины, и класть его некуда. Назовите "
            "--search-strategy или --network-strategy."
        )
    named = [name for name, _ in list(args.search_param) + list(args.network_param)
             if name == "WeeklySpendLimit"]
    if named:
        raise DirectFailure(
            "--weekly-budget назван вместе с WeeklySpendLimit в параметрах "
            "стратегии: это две записи одного поля, и разобрать их "
            "старшинством нельзя. Молча побеждало явное — половина получала "
            "одно значение, вторая другое, и кампания тратила больше, чем "
            "просил общий предел. Оставьте одно из двух."
        )
    carriers = [half for half, code in (("--search-strategy", args.search_strategy),
                                        ("--network-strategy", args.network_strategy))
                if code and carries_budget(kind_of(args), half, code)]
    if not carriers:
        raise DirectFailure(
            f"--weekly-budget назван, а нести его некому: ни одна из "
            f"выбранных стратегий поля `WeeklySpendLimit` не имеет. У "
            f"`SERVING_OFF` параметров не бывает вовсе, у `NETWORK_DEFAULT` "
            f"поля нет у единой перфоманс-кампании, а `WEEKLY_CLICK_PACKAGE` "
            f"ограничивает пакет кликов, а не денег. Бюджет выпал бы из "
            f"сборки молча, а стратегия записалась бы без ограничения расхода."
        )


MONEY_BOUNDS = {"WeeklySpendLimit": "MinimumWeeklySpendLimit"}


def fits_money_bounds(client, account, accounts, body: dict) -> None:
    """Денежные значения — не ниже минимумов валюты кабинета."""
    found = {}
    for path, value in _fields(body):
        name = path.rsplit(".", 1)[-1]
        if name in MONEY_BOUNDS and isinstance(value, int) \
                and not isinstance(value, bool):
            found.setdefault(name, []).append((path, value))
    if not found:
        return
    names = sorted(MONEY_BOUNDS[one] for one in found)
    bounds = dict(zip(names, bids_command.currency_bounds(
        client, account, accounts, *names)))
    currency = bids_command.currency_of(accounts, account)
    for name, places in sorted(found.items()):
        least = bounds[MONEY_BOUNDS[name]]
        low = [(path, value) for path, value in places if value < least]
        if not low:
            continue
        raise DirectFailure(
            f"{name} ниже минимума валюты кабинета: "
            + ", ".join(f"{path} = {money.format_api(value, currency)}"
                        for path, value in low[:3])
            + f". Директ принимает от {money.format_api(least, currency)} "
              f"(справочник `Currencies`, `{MONEY_BOUNDS[name]}`) и отвергает "
              f"такой запрос целиком."
        )


def _fields(value, path: str = ""):
    """Пары «путь — значение» по всей структуре: и листья, и контейнеры."""
    if isinstance(value, dict):
        for key, item in value.items():
            yield from _fields(item, f"{path}.{key}" if path else str(key))
        if path:
            yield path, value
        return
    if isinstance(value, list):
        for item in value:
            yield from _fields(item, path)
        if path:
            yield path, value
        return
    if path:
        yield path, value


def strategy_now(record, kind: str) -> dict:
    """Коды стратегии, которые у кампании стоят **сейчас**: половина — код."""
    body = (record or {}).get(campaign_command.TYPE_BODY[kind]) or {}
    strategy = body.get("BiddingStrategy") or {}
    found = {}
    for half in (SEARCH, NETWORK):
        code = (strategy.get(half) or {}).get("BiddingStrategyType")
        if isinstance(code, str) and code:
            found[half] = code
    return found


def campaign_body(args, kind: str, *, creating: bool, record=None) -> dict:
    """Типовая структура кампании из аргументов: стратегия, цели, счётчики."""
    args.campaign_kind = kind
    fits_strategy_arguments(args)
    body = {}
    halves = {}
    for half, code, pairs, places in (
            (SEARCH, args.search_strategy, args.search_param, args.placement),
            (NETWORK, args.network_strategy, args.network_param, ())):
        if code is None:
            continue
        params = strategy_params(pairs, where=f"половина «{HALF_RU[half]}»")
        if args.weekly_budget is not None and carries_budget(
                kind, "--search-strategy" if half == SEARCH
                else "--network-strategy", code):
            params.setdefault("WeeklySpendLimit", args.weekly_budget)
        halves[half] = strategy_half(kind, half, code, params,
                                     creating=creating)
        if places:
            halves[half]["PlacementTypes"] = placements(places)
    if halves:
        if creating and set(halves) != {SEARCH, NETWORK}:
            raise DirectFailure(
                "Стратегия кампании — пара половин: назовите и "
                "--search-strategy, и --network-strategy. Директ отвергает "
                "запрос на создание, в котором названа одна; в правке половины "
                "сливаются, и там довольно любой."
            )
        body["BiddingStrategy"] = halves
    named = [code for code in (args.search_strategy, args.network_strategy)
             if code is not None]
    ruling = {} if creating else dict(strategy_now(record, kind))
    for half, code in ((SEARCH, args.search_strategy),
                       (NETWORK, args.network_strategy)):
        if code is not None:
            ruling[half] = code
    codes = sorted(set(ruling.values()))
    goals = priority_goals(args.goal, args.goal_from_metrika,
                           updating=not creating)
    fits_goals(goals["Items"], named, codes)
    if goals["Items"] and any(one in SHARE_OF_SPEND for one in codes):
        for item in goals["Items"]:
            item.setdefault("IsMetrikaSourceOfValue", "NO")
    if goals["Items"]:
        body["PriorityGoals"] = goals
    if creating and getattr(args, "counter", None):
        body["CounterIds"] = {"Items": sorted(set(args.counter))}
    if args.attribution:
        body["AttributionModel"] = args.attribution
    return body


TYPED_RU = {
    "BiddingStrategy": "стратегия",
    "PriorityGoals": "приоритетные цели",
    "AttributionModel": "модель атрибуции",
}


def strategy_task(client, account, accounts, args):
    """Стратегия, цели, атрибуция и места показов — одной правкой."""
    record = one_campaign(client, account, accounts, args.campaign,
                          read_any_type(typed=sorted(TYPED_RU)))
    kind = writable_type(record)
    typed = campaign_body(args, kind, creating=False, record=record)
    fits_money_bounds(client, account, accounts, typed)
    if not typed:
        raise DirectFailure(
            "Не сказано, что менять: назовите --search-strategy, "
            "--network-strategy, --goal или --attribution. Счётчики правит "
            "отдельное действие — counters --campaign … --add --counter "
            "<номер>: поле заменяется целиком, и просьба «привязать ещё один» "
            "без режима неотличима от «оставить только этот». Запись без "
            "изменения прошла бы конвейер целиком и отчиталась успехом, ничего "
            "не сделав."
        )
    prefix = campaign_command.TYPE_BODY[kind]
    what = {f"{prefix}.{one}": TYPED_RU[one] for one in typed}
    operation = update_operation(
        args.campaign, kind, record=record, typed=typed, what=what,
        read=read_params(kind, typed=list(typed),
                         places=create_places(typed)),
        collections=(rules_for("PriorityGoals", f"{prefix}.PriorityGoals")[1]
                     if "PriorityGoals" in typed else None),
        unread=((f"{prefix}.PriorityGoals.Items.Operation",)
                if "PriorityGoals" in typed else ()))
    return (f"правка кампании {args.campaign}: {', '.join(what.values())}",
            [operation], rule_notes(args, operations=[operation]), [], None)


def name_task(client, account, accounts, args):
    record = one_campaign(client, account, accounts, args.campaign,
                          read_any_type())
    kind = writable_type(record)
    named_text(args.value, where="новое название")
    operation = update_operation(
        args.campaign, kind, record=record, common={"Name": args.value},
        what={"Name": "название"},
        read=read_params(kind), texts={"Name": "Campaign.Name"})
    return (f"переименование кампании {args.campaign}", [operation],
            rule_notes(args, operations=[operation]), [], None)


def dates_task(client, account, accounts, args):
    """Период проведения: `StartDate` и `EndDate`.

    Это не временной таргетинг: расписание показов задаётся отдельной командой
    и живёт в `TimeTargeting`. Снятие даты окончания — просьба отдельная,
    `--clear-end`: `null` очищает поле, а пропуск означает «не трогать», и
    Директ понимает эти две просьбы противоположно."""
    record = one_campaign(client, account, accounts, args.campaign,
                          read_any_type(common=["StartDate", "EndDate"]))
    kind = writable_type(record)
    common, what, clears = {}, {}, []
    if args.start is not None:
        common["StartDate"] = campaign_start(args.start, where="--start")
        what["StartDate"] = "начало показов"
    if args.clear_end:
        common["EndDate"] = None
        what["EndDate"] = "окончание показов"
        clears.append("EndDate")
    elif args.end is not None:
        common["EndDate"] = checked_date(args.end, where="--end")
        what["EndDate"] = "окончание показов"
    if not common:
        raise DirectFailure(
            "Не сказано, что менять: назовите --start, --end или --clear-end."
        )
    operation = update_operation(
        args.campaign, kind, record=record, common=common, what=what,
        read=read_params(kind, common=["StartDate", "EndDate"]),
        clears=tuple(clears))
    return (f"период кампании {args.campaign}", [operation],
            rule_notes(args, operations=[operation]), [], None)


def tracking_task(client, account, accounts, args):
    """Параметры URL кампании — с чтением действующей разметки до сборки."""
    record = one_campaign(client, account, accounts, args.campaign,
                          read_any_type(typed=["TrackingParams"]))
    kind = writable_type(record)
    remembered = Preferences(account, warn=warn)
    markup = cabinet_markup(client, account, accounts, args.campaign)
    tracking, source, offer = chosen_markup(args, markup, remembered,
                                            args.campaign)
    if not tracking:
        raise DirectFailure(
            "Нечего записать: строка разметки пуста. Назовите --tracking, "
            "--utm-scheme template или --utm-scheme cabinet."
        )
    prefix = campaign_command.TYPE_BODY[kind]
    operation = update_operation(
        args.campaign, kind, record=record, typed={"TrackingParams": tracking},
        what={f"{prefix}.TrackingParams": "параметры URL"},
        read=read_params(kind, typed=["TrackingParams"]))
    pending = [("utm_scheme", offer, f"схему разметки «{excerpt(offer, 60)}»")] \
        if offer else []
    return (f"параметры URL кампании {args.campaign}", [operation],
            markup_notes(markup, args.campaign, tracking, source)
            + rule_notes(args, operations=[operation]),
            pending, remembered)


def monitoring_task(client, account, accounts, args):
    """Мониторинг сайта — единственная настройка, которую команда пишет."""
    record = one_campaign(client, account, accounts, args.campaign,
                          read_any_type(typed=["Settings", "CounterIds"]))
    kind = writable_type(record)
    if args.on and not items_of(
            (record.get(campaign_command.TYPE_BODY[kind]) or {}).get(
                "CounterIds")):
        raise DirectFailure(
            f"Мониторинг сайта требует привязанного счётчика Метрики "
            f", а у кампании {args.campaign} счётчиков нет. "
            f"Директ отвергнет включение. Привяжите счётчик: counters "
            f"--campaign {args.campaign} --add --counter <номер>."
        )
    value = "YES" if args.on else "NO"
    prefix = campaign_command.TYPE_BODY[kind]
    operation = update_operation(
        args.campaign, kind, record=record,
        typed={"Settings": [{"Option": MONITORING, "Value": value}]},
        what={f"{prefix}.Settings":
              f"мониторинг сайта: {'включён' if args.on else 'выключен'}"},
        read=read_params(kind, typed=["Settings", "CounterIds"]),
        guard_also=keeps_counter(prefix) if args.on else None,
        full=(f"{prefix}.Settings",))
    return (f"мониторинг сайта у кампании {args.campaign}", [operation],
            rule_notes(args, operations=[operation]), [], None)


def change_said(said: str, before: list, after: list) -> str:
    """Что именно меняется в списке — словами и целиком."""
    gone = [one for one in before if one not in after]
    came = [one for one in after if one not in before]
    parts = [f"было {len(before)}, станет {len(after)}"]
    if came:
        parts.append("добавляется: " + ", ".join(str(one) for one in came))
    if gone:
        parts.append("убирается: " + ", ".join(str(one) for one in gone))
    if not came and not gone:
        parts.append("состав не меняется")
    return f"{said} ({'; '.join(parts)})"


def fits_change(values, mode: str) -> None:
    """Просьба, которая ничего не меняет, — отказ, а не тихая запись.

    Того же рода дефект, что и параметр без своей половины: список без
    названных значений сложится сам с собой, уедет в Директ неизменным и
    отчитается успехом. Замена — исключение: пустой список там и есть просьба
    очистить, и видна она в предпросмотре строкой «→ —»."""
    if values or mode == "replace":
        return
    raise DirectFailure(
        f"Не сказано, что {'добавить' if mode == 'add' else 'убрать'}: список "
        f"ушёл бы в Директ неизменным и отчитался успехом. Назовите значения — "
        f"или очистите список целиком: --replace без значений."
    )


def keeps_counter(prefix: str):
    """Условие: счётчик Метрики у кампании ещё есть.

    Он нужен для включения мониторинга. Наличие счётчика
    проверяется дважды: до сборки — чтобы не собирать заведомо отказную
    задачу, — и здесь, по снимку конвейера, потому что между чтением команды и
    записью счётчик успевают отвязать."""
    def check(known: dict) -> list:
        said = []
        for identifier, record in sorted((known or {}).items()):
            if not items_of((record.get(prefix) or {}).get("CounterIds")):
                said.append(
                    f"объект {identifier}: счётчиков Метрики не осталось, а "
                    f"мониторинг сайта без счётчика Директ не включает "
                    f". Между чтением и записью счётчик отвязали — "
                    f"привяжите снова: counters --campaign {identifier} --add "
                    f"--counter <номер>."
                )
        return said
    return check


def collection_task(client, account, accounts, args, *, field: str,
                    said: str, values, typed: bool):
    """Массив, который Директ заменяет целиком: IP, площадки, наборы.

    `--add` и `--remove` считаются поверх **свежего чтения**, а сторож
    `replaced_from` останавливает задачу, если список изменился между чтением
    и записью: сторож окна конвейера этого не ловит по построению — чтение
    команды было до снимка, и к моменту снимка чужая правка уже на месте."""
    params = (read_any_type(typed=[field]) if typed
              else read_any_type(common=[field]))
    record = one_campaign(client, account, accounts, args.campaign, params)
    kind = writable_type(record)
    prefix = campaign_command.TYPE_BODY[kind]
    holder = (record.get(prefix) or {}) if typed else record
    fits_change(values, args.mode)
    values = named_values(values, where=f"--{COLLECTIONS[args.action][0]}")
    if field == "BlockedIps":
        fits_addresses(values)
    current = [str(one) for one in items_of(holder.get(field))]
    final = combined(current, values, args.mode)
    if field == "NegativeKeywordSharedSetIds":
        phrases.fits_shared_sets([int(one) for one in final], "campaign")
        final = [int(one) for one in final]
    elif field == "CounterIds":
        final = [int(one) for one in final]
    elif final:
        if field == "BlockedIps":
            fits_addresses(final)
        fits_collection(FIELD_RULES[field]["collection"], final, said)
    path = f"{prefix}.{field}" if typed else field
    texts, collections = rules_for(field, path)
    # Очистка правил не получает: `null` — не значение, у которого бывают
    # длина и состав, и объявленное на нём правило движок отвергает.
    over = {"texts": texts, "collections": collections} if final else {}
    value = {"Items": final} if final else None
    operation = update_operation(
        args.campaign, kind, record=record,
        typed={field: value} if typed else None,
        common=None if typed else {field: value},
        what={path: change_said(said, current, [str(one) for one in final])},
        read=(read_params(kind, typed=[field]) if typed
              else read_params(kind, common=[field])),
        was={path: current},
        clears=() if final else (path,), **over)
    return (f"{said} у кампании {args.campaign}", [operation],
            rule_notes(args, operations=[operation]), [], None)


def schedule_task(client, account, accounts, args):
    """Расписание показов — временной таргетинг кампании."""
    record = one_campaign(client, account, accounts, args.campaign,
                          read_any_type(common=["TimeTargeting"]))
    kind = writable_type(record)
    named = checked_schedule(args.day)
    current = checked_schedule(
        str(one) for one in items_of(
            ((record.get("TimeTargeting") or {}).get("Schedule") or {})))
    final = whole_week(current, named)
    fits_collection("Campaign.Schedule", final, "строк расписания")
    weekends = (("YES" if args.working_weekends else "NO")
                if args.working_weekends is not None
                else (record.get("TimeTargeting") or {}).get(
                    "ConsiderWorkingWeekends"))
    if weekends not in ("YES", "NO"):
        raise DirectFailure(
            f"Учитывать ли рабочие выходные, кампания не сказала "
            f"({excerpt(weekends, 32)}), а поле обязательно внутри "
            f"`TimeTargeting`: без него Директ отвечает 8000. Назовите "
            f"--working-weekends или --no-working-weekends."
        )
    value = {"Schedule": {"Items": final},
             "ConsiderWorkingWeekends": weekends}
    operation = update_operation(
        args.campaign, kind, record=record, common={"TimeTargeting": value},
        what={"TimeTargeting": schedule_said(current, final)},
        read=read_params(kind, common=["TimeTargeting"]),
        collections={"TimeTargeting.Schedule": "Campaign.Schedule"},
        was={"TimeTargeting.Schedule": current},
        # Признак рабочих выходных сторожится отдельно: он скаляр, а сторож
        # замены сравнивает списки и на скаляре молчит.
        guard_also=unchanged_at({"TimeTargeting.ConsiderWorkingWeekends":
                                 (record.get("TimeTargeting") or {}).get(
                                     "ConsiderWorkingWeekends")})
        if args.working_weekends is None else None)
    return (f"расписание показов кампании {args.campaign}", [operation],
            rule_notes(args, operations=[operation]) + spend_notes(final), [], None)


def unchanged_at(before: dict):
    """Условие: значения по этим путям ещё те, из которых собран запрос."""
    def check(known: dict) -> list:
        said = []
        for identifier, record in sorted((known or {}).items()):
            for field, source in sorted(before.items()):
                now = phrases._dig(record, field)
                if now != source:
                    said.append(
                        f"объект {identifier}: поле «{field}» изменилось между "
                        f"чтением и записью — было {excerpt(source, 32)}, "
                        f"стало {excerpt(now, 32)}. Значение "
                        f"переотправляется прочитанным, и чужая правка была бы "
                        f"переписана обратно молча. Повторите команду: запрос "
                        f"соберётся по свежему значению."
                    )
        return said
    return check


def schedule_said(before: list, after: list) -> str:
    """Что меняется в расписании — днями недели, а не строками коэффициентов."""
    def working(rows):
        return [str(row).split(",")[0] for row in rows
                if any(int(one) for one in str(row).split(",")[1:])]

    def named(days):
        return ", ".join(campaign_command.WEEKDAYS[int(one) - 1]
                         for one in days) or "нет ни одного"

    was, now = working(before), working(after)
    changed = [str(day) for day in range(1, 8)
               if _row(before, day) != _row(after, day)]
    parts = [f"дни с показами: было {len(was)} ({named(was)}), "
             f"станет {len(now)} ({named(now)})"]
    parts.append(f"меняются: {named(changed)}" if changed
                 else "часы не меняются")
    return f"расписание показов ({'; '.join(parts)})"


def _row(rows: list, day: int):
    for one in rows:
        if str(one).split(",")[0] == str(day):
            return str(one)
    return None


def spend_notes(week: list) -> list:
    """Чем узкое расписание платит — числом, а не общей фразой."""
    working = sum(1 for row in week
                  if any(int(one) for one in str(row).split(",")[1:]))
    shares = default_for("schedule_spend").get("spend_share") or {}
    share = shares.get(str(working))
    if share is None:
        return []
    return [f"SCH-01 · чем платит расписание: дней с показами {working}, и "
            f"дневной расход может дойти до {share}% недельного бюджета. "
            f"Кампания с узким расписанием тратит недельный бюджет быстрее — "
            f"это не всегда очевидно тому, кто отключил только ночь."]


def whole_week(current: list, named: list) -> list:
    """Все семь дней: названные человеком поверх прочитанных.

    Дни, которых нет ни там, ни там, дописываются полными — теми же
    коэффициентами 100, какими их завёл бы сам Директ. Разница не в результате,
    а в показе: дописанное здесь пользователь видит в плане, а дописанное
    Директом узнаётся из перечитывания, когда прежние часы уже стёрты."""
    said = {}
    for row in current:
        said[str(row).split(",")[0]] = str(row)
    for row in named:
        said[str(row).split(",")[0]] = str(row)
    full = ",".join(["100"] * 24)
    return [said.get(str(day)) or f"{day},{full}" for day in range(1, 8)]


def checked_schedule(rows) -> list:
    """Строки расписания: разобранные, проверенные и приведённые к одному виду."""
    limits = Limits.load().data
    rule = limits.get("schedule", {}).get("hourly_coefficient") or {}
    width = (limits.get("collections", {}).get("Campaign.Schedule")
             or {}).get("items_per_element")
    low, high, step = rule.get("min"), rule.get("max"), rule.get("step")
    if not all(isinstance(one, int) for one in (low, high, step, width)):
        raise DirectFailure(
            "В справочнике лимитов нет устройства строки расписания: без него "
            "запись шла бы вслепую, а отказ приходил бы от Директа за баллы."
        )
    seen, canonical = set(), []
    for row in rows:
        parts = [one.strip() for one in str(row).split(",")]
        if len(parts) != width:
            raise DirectFailure(
                f"Строка расписания «{excerpt(row, 40)}»: полей {len(parts)}, "
                f"а нужно {width} — номер дня и {width - 1} коэффициента."
            )
        if not all(one.isdecimal() for one in parts):
            raise DirectFailure(
                f"Строка расписания «{excerpt(row, 40)}»: все поля — целые "
                f"числа, номер дня и коэффициенты."
            )
        day, hours = int(parts[0]), [int(one) for one in parts[1:]]
        if not 1 <= day <= 7:
            raise DirectFailure(
                f"Строка расписания «{excerpt(row, 40)}»: номер дня {day}, а "
                f"дни считаются от 1 (понедельник) до 7."
            )
        if day in seen:
            raise DirectFailure(
                f"День {day} назван дважды. Массив заменяет расписание "
                f"целиком, и какая из двух строк уцелеет, из просьбы не видно."
            )
        seen.add(day)
        wrong = [one for one in hours
                 if not low <= one <= high or one % step]
        if wrong:
            raise DirectFailure(
                f"Строка расписания «{excerpt(row, 40)}»: коэффициенты "
                f"{', '.join(str(one) for one in wrong[:5])} вне границ "
                f"{low}–{high} или не кратны {step}."
            )
        canonical.append(",".join(str(one) for one in [day] + hours))
    return canonical


def state_task(client, account, accounts, args):
    """Остановка, возобновление, архивация и разархивация — по одной кампании.

    По одной, а не пачкой: отказ Директа поэлементный, и в пачке половина
    применяется, а половина нет. Движок делит на пакеты по одному сам для
    архивации; здесь это же правило распространено и на остальные, потому что
    отчёт «остановлено 7 из 10» человеку читать нечем."""
    record = one_campaign(client, account, accounts, args.campaign,
                          read_any_type(common=["Status", "State", "Funds"]))
    if args.action == "archive" and record.get("State") in ARCHIVE_NEEDS_STOPPED:
        raise DirectFailure(
            f"Кампания {args.campaign} показывается ({record.get('State')}), а "
            f"архивации подлежит только остановленная: `8303` называет три "
            f"причины отказа, и «она не остановлена» — одна из них "
            f"(`ERRORS_AND_LIMITS.md`, раздел 10). Остановите её сначала: "
            f"state suspend --campaign {args.campaign}."
        )
    balance = ((record.get("Funds") or {}).get("CampaignFunds") or {}).get(
        "Balance")
    if args.action == "archive" and isinstance(balance, int) and balance > 0:
        raise DirectFailure(
            f"На кампании {args.campaign} есть свои средства "
            f"({money.format_api(balance)}), а архивации подлежит кампания без "
            f"них — это вторая из трёх причин `8303`. Переведите остаток или "
            f"дождитесь расхода."
        )
    if args.action == "unarchive" and record.get("State") in UNARCHIVE_REFUSES:
        raise DirectFailure(
            f"Кампания {args.campaign} переведена из условных единиц "
            f"({record.get('State')}) и доступна только на чтение: Директ "
            f"отвечает на разархивацию `8304` (`ERRORS_AND_LIMITS.md`, "
            f"раздел 10)."
        )
    if record.get("Status") == "DRAFT" and args.action in DRAFT_REFUSES:
        raise DirectFailure(
            f"Кампания {args.campaign} — черновик, и {STATE_RU[args.action]} "
            f"ей недоступна: Директ отвечает {DRAFT_REFUSES[args.action]} "
            f". Черновик виден в `Status`, а не в "
            f"`State`: `State: OFF` означает и черновик, и модерацию, и "
            f"отсутствие средств. Показов у черновика нет — ни групп, ни "
            f"объявлений в нём ещё не приняты модерацией, — и останавливать "
            f"его не от чего."
        )
    operation = state_operation(
        args.action, args.campaign, args.state,
        guard=unchanged_at({"Funds.CampaignFunds.Balance": balance})
        if args.action == "archive" else None)
    notes = rule_notes(args, operations=[operation])
    return (f"{STATE_RU[args.action]} кампании {args.campaign}", [operation],
            notes, [], None)


class Parser(argparse.ArgumentParser):
    def error(self, message: str):
        self.exit(2, redact(f"{self.prog}: {message}\n"))


def add_common(parser, *, leaf: bool) -> None:
    """Общие флаги. Ставятся и на корень, и на каждое действие."""
    default = argparse.SUPPRESS if leaf else None
    parser.add_argument("--env", choices=("production", "test_cabinet"),
                        default=default, help="контур и набор переменных")
    parser.add_argument("--account", metavar="ЛОГИН", default=default,
                        help="логин кабинета; при отсутствии берётся активный")
    parser.add_argument("--apply", action="store_true",
                        default=argparse.SUPPRESS if leaf else False,
                        help="выполнить запись; без него — проверка")
    parser.add_argument("--dry-run", action="store_true", dest="dry_run",
                        default=argparse.SUPPRESS if leaf else False,
                        help="проверка без записи (умолчание)")
    parser.add_argument("--remember", action="store_true",
                        default=argparse.SUPPRESS if leaf else False,
                        help="сохранить выбранные схемы имени и UTM для кабинета")
    parser.add_argument("--json", action="store_true",
                        default=argparse.SUPPRESS if leaf else False,
                        help="машиночитаемый вывод")


def pair(text: str):
    """Аргумент вида `Имя=значение`."""
    name, sign, value = text.partition("=")
    if not name or not sign:
        raise argparse.ArgumentTypeError(
            f"ожидается «Имя=значение», получено «{excerpt(text, 40)}»")
    return name, value


def yes_no(text: str):
    """Аргумент вида `SearchResults=YES`."""
    name, value = pair(text)
    if value.upper() not in ("YES", "NO"):
        raise argparse.ArgumentTypeError(
            f"ожидается «Имя=YES» или «Имя=NO», получено «{excerpt(text, 40)}»")
    return name, value.upper()


def amount(text: str) -> int:
    """Сумма в валюте кабинета — в единицы API, ещё при разборе вызова.

    Тем же разбором, что и ценность цели: `money.MoneyError` — подкласс
    `ValueError`, а не `DirectFailure`, и не пойманный здесь он выходит наружу
    трассировкой уже после первого чтения. Человек получает её вместо отказа,
    а `--json` — пустой stdout вместо объекта."""
    try:
        return money.to_api(text)
    except money.MoneyError as failure:
        raise argparse.ArgumentTypeError(
            f"недельный бюджет «{excerpt(text, 32)}»: {failure}") from None


def goal(text: str):
    """Аргумент вида `222395726=700`: цель и ценность конверсии в валюте."""
    name, value = pair(text)
    # Тем же разбором, что и остальные номера: `isdecimal` принимал `0`, а цели
    # с таким номером не бывает — соседние аргументы её уже отвергают.
    try:
        goal_id = whole(name)
    except argparse.ArgumentTypeError as failure:
        raise argparse.ArgumentTypeError(
            f"идентификатор цели: {failure}") from None
    try:
        return goal_id, money.to_api(value)
    except money.MoneyError as failure:
        raise argparse.ArgumentTypeError(
            f"ценность конверсии «{excerpt(value, 32)}»: {failure}") from None


def whole(text: str) -> int:
    """Положительное целое из командной строки — тем же разбором, что и из файла."""
    if not text.isdecimal():
        raise argparse.ArgumentTypeError(
            f"ожидается целое число, получено «{excerpt(text, 32)}»")
    if int(text) == 0:
        raise argparse.ArgumentTypeError(
            "ожидается номер объекта, а не ноль: объекта с таким номером не "
            "бывает")
    return int(text)


def add_mode(step) -> None:
    """Как складывать названное с тем, что уже стоит в кабинете.

    Умолчание — `--add`, а не `--replace`: замена целиком стирает то, о чём
    человек не говорил, и умолчанием такая операция быть не может."""
    mode = step.add_mutually_exclusive_group()
    mode.add_argument("--add", dest="mode", action="store_const", const="add",
                      default="add", help="добавить к тому, что есть (умолчание)")
    mode.add_argument("--remove", dest="mode", action="store_const",
                      const="remove", help="убрать названное")
    mode.add_argument("--replace", dest="mode", action="store_const",
                      const="replace", help="заменить список целиком")


def add_strategy(step, *, required: bool) -> None:
    """Стратегия обеих половин. На создании обе обязательны.

    Не строгость ради строгости: `BiddingStrategy` обязателен в `Campaigns.add`,
    а половин у него две, и запрос с одной Директ отвергает целиком. Отказ за
    баллы дороже отказа разбора."""
    step.add_argument("--search-strategy", dest="search_strategy",
                      required=required,
                      metavar="КОД", help="тип стратегии на поиске")
    step.add_argument("--search-param", dest="search_param", type=pair,
                      action="append", default=[], metavar="ИМЯ=ЗНАЧЕНИЕ",
                      help="поле структуры поисковой стратегии")
    step.add_argument("--network-strategy", dest="network_strategy",
                      required=required,
                      metavar="КОД", help="тип стратегии в сетях")
    step.add_argument("--network-param", dest="network_param", type=pair,
                      action="append", default=[], metavar="ИМЯ=ЗНАЧЕНИЕ",
                      help="поле структуры сетевой стратегии")
    step.add_argument("--weekly-budget", dest="weekly_budget", type=amount,
                      metavar="СУММА",
                      help="недельный бюджет в валюте кабинета: "
                           "WeeklySpendLimit обеих половин, если он у них есть")
    step.add_argument("--placement", action="append", type=yes_no, default=[],
                      metavar="МЕСТО=YES|NO",
                      help=f"места показа поисковой половины: "
                           f"{', '.join(PLACEMENTS_WRITABLE)}")


def add_goals(step, *, counters: bool = True) -> None:
    step.add_argument("--goal", action="append", type=goal, default=[],
                      metavar="ЦЕЛЬ=ЦЕННОСТЬ",
                      help="приоритетная цель и ценность конверсии в валюте")
    step.add_argument("--goal-from-metrika", dest="goal_from_metrika",
                      action="append", type=whole, default=[], metavar="ЦЕЛЬ",
                      help="ценность этой цели берётся из Метрики")
    if counters:
        step.add_argument("--counter", action="append", type=whole, default=[],
                          metavar="НОМЕР", help="счётчик Яндекс Метрики")
    step.add_argument("--attribution", choices=ATTRIBUTION,
                      help="модель атрибуции")


def add_markup(step, *, creating: bool = False) -> None:
    chosen = step.add_mutually_exclusive_group()
    chosen.add_argument("--tracking", metavar="СТРОКА",
                        help="параметры URL дословно; сильнее --utm-scheme")
    chosen.add_argument("--utm-scheme", dest="utm_scheme",
                        choices=("template", "cabinet"),
                        help="чем размечать: шаблоном UTM или действующей "
                             "схемой кабинета")
    if creating:
        chosen.add_argument("--no-tracking", dest="no_tracking",
                            action="store_true",
                            help="завести кампанию без параметров URL")


def build_parser() -> Parser:
    parser = Parser(description="Создание и изменение кампаний Яндекс Директа.")
    add_common(parser, leaf=False)
    common = argparse.ArgumentParser(add_help=False)
    add_common(common, leaf=True)
    actions = parser.add_subparsers(dest="action", required=True)

    made = actions.add_parser("create", parents=[common], help="создать кампанию")
    made.add_argument("--name", required=True)
    made.add_argument("--type", choices=sorted(TYPE_BY_WORD), default="unified")
    made.add_argument("--start", required=True, metavar="ГГГГ-ММ-ДД")
    made.add_argument("--end", metavar="ГГГГ-ММ-ДД")
    made.add_argument("--negative", action="append", default=[],
                      metavar="ФРАЗА", help="минус-фраза кампании")
    made.add_argument("--starter-negatives", dest="starter_negatives",
                      action="store_true",
                      help="добавить начальный набор минус-фраз")
    made.add_argument("--ip", action="append", default=[], metavar="АДРЕС")
    made.add_argument("--site", action="append", default=[], metavar="ПЛОЩАДКА")
    add_strategy(made, required=True)
    add_goals(made)
    add_markup(made, creating=True)

    named = actions.add_parser("name", parents=[common],
                               help="переименовать кампанию")
    named.add_argument("--campaign", type=whole, required=True)
    named.add_argument("value", help="новое название")

    dated = actions.add_parser("dates", parents=[common],
                               help="период проведения кампании")
    dated.add_argument("--campaign", type=whole, required=True)
    dated.add_argument("--start", metavar="ГГГГ-ММ-ДД")
    ending = dated.add_mutually_exclusive_group()
    ending.add_argument("--end", metavar="ГГГГ-ММ-ДД")
    ending.add_argument("--clear-end", dest="clear_end", action="store_true",
                        help="снять дату окончания")

    strategy = actions.add_parser(
        "strategy", parents=[common],
        help="стратегия, цели, атрибуция и места показов; счётчики правит "
             "отдельное действие counters")
    strategy.add_argument("--campaign", type=whole, required=True)
    add_strategy(strategy, required=False)
    add_goals(strategy, counters=False)

    marked = actions.add_parser("tracking", parents=[common],
                                help="параметры URL кампании")
    marked.add_argument("--campaign", type=whole, required=True)
    add_markup(marked)

    watched = actions.add_parser("monitoring", parents=[common],
                                 help="мониторинг сайта")
    watched.add_argument("--campaign", type=whole, required=True)
    switch = watched.add_mutually_exclusive_group(required=True)
    switch.add_argument("--on", action="store_true", help="включить")
    switch.add_argument("--off", dest="on", action="store_false",
                        help="выключить")

    ips = actions.add_parser("ips", parents=[common], help="запрещённые IP")
    ips.add_argument("--campaign", type=whole, required=True)
    ips.add_argument("--ip", action="append", default=[], metavar="АДРЕС")
    add_mode(ips)

    sites = actions.add_parser("sites", parents=[common],
                               help="запрещённые площадки")
    sites.add_argument("--campaign", type=whole, required=True)
    sites.add_argument("--site", action="append", default=[],
                       metavar="ПЛОЩАДКА")
    add_mode(sites)

    sets = actions.add_parser("sets", parents=[common],
                              help="наборы минус-фраз из библиотеки")
    sets.add_argument("--campaign", type=whole, required=True)
    sets.add_argument("--set", action="append", type=whole, default=[],
                      dest="set", metavar="НАБОР")
    add_mode(sets)

    counters = actions.add_parser("counters", parents=[common],
                                  help="счётчики Яндекс Метрики")
    counters.add_argument("--campaign", type=whole, required=True)
    counters.add_argument("--counter", action="append", type=whole, default=[],
                          metavar="НОМЕР")
    add_mode(counters)

    schedule = actions.add_parser("schedule", parents=[common],
                                  help="расписание показов")
    schedule.add_argument("--campaign", type=whole, required=True)
    schedule.add_argument("--day", action="append", default=[], required=True,
                          metavar="СТРОКА",
                          help="строка расписания: номер дня и 24 "
                               "коэффициента через запятую")
    weekends = schedule.add_mutually_exclusive_group()
    weekends.add_argument("--working-weekends", dest="working_weekends",
                          action="store_true", default=None,
                          help="учитывать рабочие выходные")
    weekends.add_argument("--no-working-weekends", dest="working_weekends",
                          action="store_false",
                          help="не учитывать рабочие выходные")

    state = actions.add_parser("state", parents=[common],
                               help="остановка, запуск, архив")
    state.add_argument("action", choices=sorted(TRANSITIONS))
    state.add_argument("--campaign", type=whole, required=True)
    state.add_argument("--expect-state", dest="state", choices=STATES,
                       help="какое состояние ожидать после операции")
    return parser


COLLECTIONS = {
    "ips": ("ip", "BlockedIps", "запрещённые IP", False),
    "sites": ("site", "ExcludedSites", "запрещённые площадки", False),
    "sets": ("set", "NegativeKeywordSharedSetIds", "наборы минус-фраз", True),
    "counters": ("counter", "CounterIds", "счётчики Метрики", True),
}


def run_collection(client, account, accounts, args):
    """Действие над списком, который Директ заменяет целиком."""
    argument, field, said, typed = COLLECTIONS[args.action]
    return collection_task(client, account, accounts, args, field=field,
                           said=said, values=getattr(args, argument),
                           typed=typed)


def build_task(client, account, accounts, args):
    """Задача по названному действию — одной дверью на все действия."""
    if args.action in TASKS:
        return TASKS[args.action](client, account, accounts, args)
    if args.action in COLLECTIONS:
        return run_collection(client, account, accounts, args)
    return state_task(client, account, accounts, args)


TASKS = {
    "create": create_task,
    "name": name_task,
    "dates": dates_task,
    "strategy": strategy_task,
    "tracking": tracking_task,
    "monitoring": monitoring_task,
    "schedule": schedule_task,
}


def run(args) -> int:
    client = Client.from_env(profile=args.env, account=args.account, warn=warn)
    accounts = Accounts.load(client, warn=warn)
    account = resolve_account(accounts, client, args.account)

    title, operations, notes, pending, remembered = build_task(
        client, account, accounts, args)

    seen = []
    engine = Writer(client, account, accounts=accounts, apply=args.apply,
                    show=showing(seen=seen, quiet=args.json, notes=notes),
                    warn=warn)
    report = engine.run(Task(title, operations))
    if pending and remembered is not None:
        markup_command.remember_after(report, remembered, pending, args)
    return report_out(report, args, shown=bool(seen), account=account)


def report_out(report, args, *, shown: bool, account=None) -> int:
    """Отчёт команды: один прогон, одна печать."""
    links = []
    if account:
        identifiers = (report.accepted if args.action == "create"
                       else [args.campaign])
        # При неустановленном результате Writer может оставить имя объекта.
        # ID созданной кампании приходит от API целым числом, имя им не станет.
        if args.action == "create":
            identifiers = [one for one in identifiers if type(one) is int and one > 0]
        links = [{"id": identifier,
                  "url": campaign_url(account, identifier),
                  "edit_url": campaign_url(account, identifier, edit=True)}
                 for identifier in dict.fromkeys(identifiers)]
    if args.json:
        result = report.machine()
        if account:
            result.update(account_url=account_url(account), campaign_links=links)
        say(json.dumps(result, ensure_ascii=False))
    else:
        lines = report.lines()
        if shown:
            # Предпросмотр человек уже видел — в отчёте остаётся то, чем он
            # кончился: проверка, отказы, расхождения и сводка.
            lines = lines[len(report.preview):]
        destinations = [f"Кабинет: {account_url(account)}"] if account else []
        for link in links:
            destinations += [f"Открыть кампанию {link['id']}: {link['url']}",
                             f"Настройки: {link['edit_url']}"]
        cache_module.outline(destinations + lines, path=report.journal)
    return 0 if report.ok else 1


def refused(said: str, args) -> int:
    """Отказ команды: в stderr человеку, объектом — программе.

    Машиночитаемый вывод отвечает объектом и на отказе. Иначе программа,
    читающая stdout, получает на отказе пустоту и падает там, где команда как
    раз всё объяснила."""
    warn(said)
    if getattr(args, "json", False):
        say(json.dumps(unrun("отказ", problems=[said]), ensure_ascii=False))
    return 1


def main(argv=None) -> int:
    preload_secrets()
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.apply and args.dry_run:
        parser.error("--apply и --dry-run взаимоисключающие: писать или "
                     "проверять, а не и то и другое")
    try:
        return run(args)
    except DirectFailure as failure:
        return refused(str(failure), args)
    except OSError as failure:
        return refused(redact(f"Не удалось прочитать или записать файл: "
                              f"{failure}"), args)


if __name__ == "__main__":
    raise SystemExit(main())
