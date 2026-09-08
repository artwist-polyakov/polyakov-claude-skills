#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""Кампании кабинета: список с фильтрами и полная карточка одной кампании."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# Строго до импортов из `lib`: каталог запускаемого файла стоит на пути
# импорта первым, и без этой строки `import cache` нашёл бы `scripts/cache.py`.
sys.path.insert(0, str(Path(__file__).resolve().parent / "lib"))

import money  # noqa: E402
import incoming  # noqa: E402
from accounts import (  # noqa: E402
    AGENCY,
    Accounts,
    Ambiguous,
    resolve_account,
)
from cache import (  # noqa: E402
    Cache,
    add_arguments,
    human_age,
    outline,
    plural,
    tsv,
)
from config import (  # noqa: E402
    DirectFailure,
    excerpt,
    preload_secrets,
    redact,
    short,
)
from direct import Client, thousands  # noqa: E402
from errors import TransportFailure, required  # noqa: E402
from ui_links import account_url, campaign_url  # noqa: E402

# --------------------------------------------------------------------------
# Перечни Директа
# --------------------------------------------------------------------------

# Общие поля кампании. Перечень взят у самого Директа: заведомо неверное имя в
# `FieldNames`, и он называет допустимые в тексте отказа (замер 29.08.2026).
# Он шире и документации, и WSDL — `CreateTime` есть только в живом API.
COMMON_FIELDS = [
    "Id", "Name", "Type", "State", "Status", "StatusPayment",
    "StatusClarification", "SourceId", "Currency", "Funds", "Statistics",
    "StartDate", "EndDate", "CreateTime", "TimeZone", "TimeTargeting",
    "ClientInfo", "RepresentedBy", "DailyBudget", "NegativeKeywords",
    "BlockedIps", "ExcludedSites", "Notification",
]

# Наборы полей по типам кампаний, оттуда же. Отправляются все сразу: тип
# кампании выясняется из ответа, а набор нужен до того, как ответ получен.
TYPE_FIELDS = {
    "UnifiedCampaignFieldNames": [
        "BiddingStrategy", "PriorityGoals", "CounterIds", "Settings",
        "TrackingParams", "AttributionModel", "PackageBiddingStrategy",
        "CanBeUsedAsPackageBiddingStrategySource", "NegativeKeywordSharedSetIds",
        "AdvertisedItem", "DefaultBusinessId", "DefaultPhoneId",
        "WeeklyBudgetRollover",
    ],
    "TextCampaignFieldNames": [
        "BiddingStrategy", "PriorityGoals", "CounterIds", "Settings",
        "TrackingParams", "AttributionModel", "PackageBiddingStrategy",
        "CanBeUsedAsPackageBiddingStrategySource", "NegativeKeywordSharedSetIds",
        "RelevantKeywords", "WeeklyBudgetRollover",
    ],
    "MobileAppCampaignFieldNames": [
        "BiddingStrategy", "Settings", "PackageBiddingStrategy",
        "CanBeUsedAsPackageBiddingStrategySource", "NegativeKeywordSharedSetIds",
        "WeeklyBudgetRollover",
    ],
    "DynamicTextCampaignFieldNames": [
        "BiddingStrategy", "PriorityGoals", "CounterIds", "Settings",
        "TrackingParams", "AttributionModel", "PackageBiddingStrategy",
        "CanBeUsedAsPackageBiddingStrategySource", "NegativeKeywordSharedSetIds",
        "PlacementTypes",
    ],
    "CpmBannerCampaignFieldNames": [
        "BiddingStrategy", "PriorityGoals", "CounterIds", "Settings",
        "FrequencyCap", "VideoTarget", "ExcludedSitesForVideoAds",
    ],
    "SmartCampaignFieldNames": [
        "BiddingStrategy", "PriorityGoals", "CounterId", "Settings",
        "TrackingParams", "AttributionModel", "PackageBiddingStrategy",
        "CanBeUsedAsPackageBiddingStrategySource",
    ],
}

# Имя типовой структуры в ответе по коду типа. Ключ — то, что приходит в поле
# `Type`, значение — ключ, под которым лежат настройки этого типа.
TYPE_BODY = {
    "UNIFIED_CAMPAIGN": "UnifiedCampaign",
    "TEXT_CAMPAIGN": "TextCampaign",
    "MOBILE_APP_CAMPAIGN": "MobileAppCampaign",
    "DYNAMIC_TEXT_CAMPAIGN": "DynamicTextCampaign",
    "CPM_BANNER_CAMPAIGN": "CpmBannerCampaign",
    "SMART_CAMPAIGN": "SmartCampaign",
}

# Состояния показов. Перечисляются в запросе целиком: без явного `CONVERTED`
# кампании, переведённые из у. е., в выборку не попадают вовсе, и кабинет
# выглядит меньше, чем он есть.
ALL_STATES = ["ARCHIVED", "CONVERTED", "ENDED", "OFF", "ON", "SUSPENDED"]
ALL_STATUSES = ["ACCEPTED", "DRAFT", "MODERATION", "REJECTED"]

# Порядок показа в сводке: сперва то, что тратит деньги прямо сейчас.
STATE_ORDER = {"ON": 0, "SUSPENDED": 1, "OFF": 2, "ENDED": 3, "CONVERTED": 4,
               "ARCHIVED": 5}

TYPE_RU = {
    "UNIFIED_CAMPAIGN": "ЕПК",
    "TEXT_CAMPAIGN": "текстово-графическая",
    "MOBILE_APP_CAMPAIGN": "реклама приложения",
    "DYNAMIC_TEXT_CAMPAIGN": "динамические объявления",
    "CPM_BANNER_CAMPAIGN": "медийная",
    "SMART_CAMPAIGN": "смарт-баннеры",
    "UNKNOWN": "тип вне API",
}

STATE_RU = {
    "ON": "показывается",
    "OFF": "выключена",
    "SUSPENDED": "остановлена владельцем",
    "ENDED": "завершилась по дате",
    "ARCHIVED": "в архиве",
    "CONVERTED": "архив у. е., только чтение",
    "UNKNOWN": "состояние вне API",
}

STATUS_RU = {
    "DRAFT": "черновик",
    "MODERATION": "на модерации",
    "ACCEPTED": "принята модерацией",
    "REJECTED": "отклонена модерацией",
    "UNKNOWN": "статус вне API",
}

# Денежные поля стратегий и бюджетов. Перечень не угадан по названию, а собран
# из схемы: `https://api.direct.yandex.com/v501/campaigns?wsdl`, структуры
# `Strategy*`, `CustomPeriodBudget`, `ExplorationBudget`, `DailyBudget`,
# `PriorityGoalsItem` (снято 29.08.2026). Числовые поля, деньгами не
# являющиеся, — `GoalId`, `ClicksPerWeek`, `Crr`, `ReserveReturn`,
# `LimitPercent`, `RoiCoef`, `Profitability`, `Impressions`, `PeriodDays` — в
# перечень намеренно не входят: множитель к ним не применяется.
MONEY_FIELDS = frozenset({
    "Amount", "AverageCpa", "AverageCpc", "AverageCpi", "AverageCpm",
    "AverageCpv", "Balance", "BalanceBonus", "BidCeiling", "Cpa",
    "FilterAverageCpa", "FilterAverageCpc", "MinimumExplorationBudget",
    "Refund", "Spend", "SpendLimit", "Sum", "SumAvailableForTransfer",
    "Value", "WeeklyBudgetRollover", "WeeklySpendLimit",
})

# Идентификаторы целей, которые целями Метрики не являются. `12` — системная
# «Вовлечённые сессии» (`references/API_OBJECTS.md`, раздел 2.3): в Метрике
# цели с таким номером нет, и напечатанное числом оно выглядит как чужой
# идентификатор. Тот же случай, что ноль в регионах: служебное значение,
# показанное как данные.
SYSTEM_GOALS = {12: "вовлечённые сессии"}

# Поля, значение которых — идентификатор цели. Перечень нужен потому, что цель
# встречается не только в `PriorityGoals`: `GoalId` лежит внутри почти каждой
# автоматической стратегии, а `OptimizeGoalId` — в настройке дополнительных
# релевантных фраз (схема `campaigns?wsdl`, снято 29.08.2026). Правило одно на
# всех, как и у денежных полей: подстановка имени, сделанная в одном месте,
# оставила бы номер во всех остальных.
GOAL_FIELDS = frozenset({"GoalId", "OptimizeGoalId"})


FUNDS_LABELS = {
    "Balance": "остаток кампании",
    "Sum": "зачислено",
    "SumAvailableForTransfer": "к переводу",
    "BalanceBonus": "бонус",
    "Spend": "расход",
    "Refund": "возврат",
}


# Дни недели в расписании показов: первое поле строки — номер дня.
WEEKDAYS = ["пн", "вт", "ср", "чт", "пт", "сб", "вс"]

SHOWN = 12

# То же для машиночитаемого вывода: он не про строки, а про объём. Перечень в
# триста кампаний съедает контекстное окно агента так же, как простыня.
JSON_LIMIT = 50

INLINE = 3

# --------------------------------------------------------------------------
# Вывод
# --------------------------------------------------------------------------


def say(text: str = "") -> None:
    """Единственный путь вывода: секреты вырезаются здесь, а не у вызывающих."""
    print(redact(text))


def warn(text: str) -> None:
    print(redact(text), file=sys.stderr)


def units_said(count: int) -> str:
    """«1 балл», «38 баллов», «28 000 500 баллов».

    Число печатается с разделителями тысяч, а слово берётся у общей функции
    склонения: своя копия её правил разошлась бы с оригиналом молча."""
    return f"{thousands(count)} {plural(count, 'балл', 'балла', 'баллов').rsplit(' ', 1)[-1]}"


def money_text(units, currency: str = "") -> str:
    """Денежное значение словами. Пустая строка — «неизвестно», а не «ноль».

    Разница существенна ровно в этой команде: расход без остатка на общем счёте
    и должен читаться как пробел, а не как ноль."""
    if units is None:
        return ""
    try:
        return money.format_api(units, currency)
    except money.MoneyError:
        # Чужое значение в денежном поле — не повод уронить чтение кабинета.
        # Показывается как есть, чтобы человек увидел, что именно пришло.
        return str(units)


def is_money(field: str) -> bool:
    return field in MONEY_FIELDS


# --------------------------------------------------------------------------
# Нормализация ответа
# --------------------------------------------------------------------------

def type_body(record: dict) -> dict:
    """Типовая структура кампании или пустой словарь.

    Пустой словарь здесь означает две разные вещи — «тип вне API» и «набор
    `FieldNames` не запрашивали», — и различает их вызывающий код по `Type`.
    Команда запрашивает все наборы, поэтому пустота у известного типа означала
    бы неполный ответ."""
    key = TYPE_BODY.get(record.get("Type"))
    body = record.get(key) if key else None
    return body if isinstance(body, dict) else {}


def missing_field_set(record: dict):
    """Имя набора `FieldNames`, которого не хватило запросу, или `None`.

    Известный тип без своей структуры в ответе — это не «настроек нет», а
    запрос, ушедший без нужного набора. Ответ при этом успешен, и без такой
    проверки кампания с пустыми настройками читается как настроенная. Команда
    отправляет все наборы сразу, поэтому здесь проверяется не своя ошибка, а
    чужая: ответ, собранный другим кодом, и смена состава наборов у Директа."""
    key = TYPE_BODY.get(record.get("Type"))
    if key is None:
        return None
    body = record.get(key)
    if isinstance(body, dict) and body:
        return None
    return f"{key}FieldNames"


def unmanageable_reason(record: dict) -> str:
    """Почему кампанией нельзя управлять через API, либо пустая строка.

    Причин две, и они на разных осях. Тип: шесть значений `CampaignTypeGetEnum`
    управляются, `UNKNOWN` — нет. Состояние: кампания в `CONVERTED` велась в
    условных единицах и перемещена в специальный архив — она доступна только на
    чтение (`references/API_OBJECTS.md`, раздел 2.4) независимо от того, какого
    она типа. Проверять одну ось значит объявлять управляемым то, что не
    меняется.

    Мастера кампаний под первой осью нет: `Campaigns.get` таких кампаний не
    возвращает вовсе (замер 01.09.2026, раздел 2.1 справочника), так что
    помечать здесь нечего — их в ответе не бывает. Про Простой старт то же
    не замерено: кампаний этого вида в кабинетах замера не нашлось. Ось
    остаётся ради значения,
    которого команда ещё не знает: `UNKNOWN` Директ отдаёт на тип, не
    поддерживаемый этой версией API, и объявить такую кампанию управляемой
    дороже, чем отказать. На живых данных эта ось не срабатывала: `UNKNOWN` в
    ответе не встретился ни разу — она закрывает исход, которого пока не
    наблюдали, а не тот, что видели."""
    if record.get("Type") not in TYPE_BODY:
        return "тип вне API: доступна только статистика через Reports"
    if record.get("State") == "CONVERTED":
        return "архив условных единиц: доступна только на чтение"
    return ""


def manageable(record: dict) -> bool:
    """Управляется ли кампания через API."""
    return not unmanageable_reason(record)


def funds_of(record: dict) -> dict:
    """Остаток и расход кампании — с указанием, чьи они.

    `Mode` разводит два несовместимых представления. `CAMPAIGN_FUNDS` даёт
    баланс самой кампании и не даёт расхода; `SHARED_ACCOUNT_FUNDS` — расход и
    ни одного поля с балансом. Поэтому в ответе три величины: расход, остаток и
    то, кому остаток принадлежит."""
    funds = record.get("Funds")
    if not isinstance(funds, dict):
        return {"mode": None, "spend": None, "balance": None,
                "balance_scope": "", "own": {}}
    mode = funds.get("Mode")
    if mode == "SHARED_ACCOUNT_FUNDS":
        shared = funds.get("SharedAccountFunds")
        shared = shared if isinstance(shared, dict) else {}
        # Баланс здесь не «ноль», а «не в этом ответе»: его отдаёт
        # AccountManagement, и принадлежит он кабинету целиком. Сама структура
        # при этом отдаётся целиком, как и у второй ветки: `Refund`
        # документирован всегда нулевым, но «документирован» и «не бывает» —
        # разные вещи, и ненулевой он пропасть не должен.
        return {"mode": mode, "spend": shared.get("Spend"), "balance": None,
                "balance_scope": "account", "own": shared}
    own = funds.get("CampaignFunds")
    own = own if isinstance(own, dict) else {}
    # Расхода у этой ветки нет вовсе. Считать его как `Sum - Balance` нельзя:
    # `Sum` документирован с НДС, `Balance` — без него, и разность получилась
    # бы не расходом, а суммой расхода с налогом. Зато есть зачисленное и
    # доступное к переводу — показывать из четырёх полей одно значило бы снова
    # отбирать руками.
    return {"mode": mode, "spend": None, "balance": own.get("Balance"),
            "balance_scope": "campaign" if own else "", "own": own}


def strategy_of(record: dict) -> dict:
    """Стратегия кампании: блок поиска и блок сетей по отдельности.

    Разделять обязательно: у ЕПК заполнены оба, и «стратегия кампании» одной
    строкой скрывает половину настройки — ровно ту, из-за которой показы идут
    не там, где ожидали."""
    body = type_body(record)
    strategy = body.get("BiddingStrategy")
    strategy = strategy if isinstance(strategy, dict) else {}
    blocks = {}
    for key, where in (("Search", "поиск"), ("Network", "сети")):
        block = strategy.get(key)
        if not isinstance(block, dict):
            continue
        kind = block.get("BiddingStrategyType")
        settings = {}
        for name, value in block.items():
            if name == "BiddingStrategyType" or not isinstance(value, dict):
                continue
            # Единственная вложенная структура блока — параметры стратегии;
            # имя у неё производное от типа (`AVERAGE_CPA` → `AverageCpa`), и
            # сверять его с типом здесь не нужно: несоответствие Директ
            # отвергает кодом 4000 ещё на записи.
            settings.update(value)
        blocks[key] = {"where": where, "type": kind, "settings": settings}
    return blocks


def strategy_line(block: dict, currency: str) -> str:
    """Стратегия одного блока строкой: тип и его параметры.

    Значения печатает общий `value_text`, а не своя копия его правил. Копия
    здесь уже была, и на ней потерялось имя системной цели: подстановку сделали
    в одном месте, а `GoalId` внутри стратегии остался номером."""
    parts = []
    for name, value in block["settings"].items():
        if isinstance(value, dict) and "Items" not in value:
            # Вложенные структуры параметров — `ExplorationBudget`,
            # `CustomPeriodBudget` — разворачиваются в ту же строку: их одна-две,
            # а запись `ключ=значение/ключ=значение` внутри общей строки съедает
            # предел длины, за которым обрезается конец. Значения при этом идут
            # через общий `value_text`, поэтому цель и внутри структуры
            # называется именем.
            value = {inner: nested for inner, nested in value.items()}
            parts.extend(f"{inner} {value_text(inner, nested, currency)}"
                         for inner, nested in value.items()
                         if value_text(inner, nested, currency))
            continue
        text = value_text(name, value, currency)
        if text:
            parts.append(f"{name} {text}")
    tail = f" · {', '.join(parts)}" if parts else ""
    return f"{block['where']}: {block['type'] or '—'}{tail}"


def goal_name(goal_id) -> str:
    """Цель словами: системная — именем, цель Метрики — идентификатором."""
    return SYSTEM_GOALS.get(goal_id, str(goal_id))


def goals_of(record: dict) -> list:
    """Ключевые цели с ценностью конверсии."""
    goals = type_body(record).get("PriorityGoals")
    items = goals.get("Items") if isinstance(goals, dict) else None
    if not isinstance(items, list):
        return []
    return [item for item in items if isinstance(item, dict)]


def items_of(value) -> list:
    """Массив Директа: либо `{"Items": [...]}`, либо голый список, либо ничего.

    Обе формы встречаются в одном ответе: `NegativeKeywords` приходит объектом
    с `Items`, `Settings` — списком. Разбор, знающий одну форму, молча вернул
    бы пустоту на другой."""
    if isinstance(value, dict):
        value = value.get("Items")
    return [item for item in value] if isinstance(value, list) else []


def counter_ids(record: dict) -> list:
    """Счётчики Метрики кампании — обеими формами поля.

    У пяти типов поле называется `CounterIds` и приходит массивом, у
    `SMART_CAMPAIGN` — `CounterId` и приходит одним числом
    (`references/API_OBJECTS.md`, раздел 2.5). Разбор, знающий одну форму,
    показал бы у смарт-баннеров ноль счётчиков там, где счётчик настроен, — и
    это снова «данных нет» вместо ошибки."""
    body = type_body(record)
    found = [item for item in items_of(body.get("CounterIds")) if item is not None]
    single = body.get("CounterId")
    if single is not None and single not in found:
        found.append(single)
    return found


NAMED_FIELDS = frozenset({
    "BiddingStrategy", "PriorityGoals", "CounterIds", "CounterId", "Settings",
    "NegativeKeywordSharedSetIds", "PackageBiddingStrategy", "WeeklyBudgetRollover",
})


def value_text(name: str, value, currency: str = "") -> str:
    """Значение поля типовой структуры одной строкой, либо пустая строка.

    Пустая строка означает «показывать нечего»: `null`, пустой массив и пустая
    строка приходят у всякого ненастроенного поля, и печатать их значит забить
    сводку пустотой ровно там, где предел в тридцать строк."""
    if value is None or value == "":
        return ""
    if is_money(name):
        return money_text(value, currency)
    if name in GOAL_FIELDS and not isinstance(value, (dict, list)):
        return goal_name(value)
    if isinstance(value, dict) and "Items" not in value:
        inner = [f"{key}={value_text(key, item, currency)}"
                 for key, item in value.items() if value_text(key, item, currency)]
        return "/".join(inner)
    if isinstance(value, (dict, list)):
        parts = [value_text(name, item, currency) for item in items_of(value)]
        return ", ".join(part for part in parts if part)
    return str(value)


NAMED_COMMON = frozenset({
    "Id", "Name", "Type", "State", "Status", "StatusPayment",
    "StatusClarification", "Currency", "Funds", "Statistics", "StartDate",
    "EndDate", "CreateTime", "TimeZone", "TimeTargeting", "DailyBudget",
    "NegativeKeywords", "BlockedIps", "ExcludedSites",
})


def other_common(record: dict, currency: str = "") -> list:
    """Прочие общие поля кампании парами «имя значение»."""
    shown = []
    for name in COMMON_FIELDS:
        if name in NAMED_COMMON:
            continue
        text = value_text(name, record.get(name), currency)
        if text:
            shown.append(f"{name} {text}")
    return shown


def other_settings(record: dict, currency: str = "") -> list:
    """Прочие поля типовой структуры парами «имя значение».

    Порядок — как в ответе Директа: он же порядок перечисления в запросе, и
    сортировка по алфавиту только оторвала бы поле от соседа по смыслу."""
    shown = []
    for name, value in type_body(record).items():
        if name in NAMED_FIELDS:
            continue
        text = value_text(name, value, currency)
        if text:
            shown.append(f"{name} {text}")
    return shown


def settings_of(record: dict) -> dict:
    """Настройки типовой структуры как словарь «опция → значение»."""
    found = {}
    for item in items_of(type_body(record).get("Settings")):
        if isinstance(item, dict) and isinstance(item.get("Option"), str):
            found[item["Option"]] = item.get("Value")
    return found


def why(record: dict) -> str:
    """Почему кампания в таком состоянии — одной строкой.

    `State` и `Status` отвечают на разные вопросы, и путать их дорого:
    остановленную кампанию включают одним вызовом, а отклонённую модерацией
    сначала правят. Поэтому строка собирается из обоих, а не из одного."""
    state = record.get("State")
    status = record.get("Status")
    if state in ("ARCHIVED", "CONVERTED", "SUSPENDED"):
        return STATE_RU.get(state, str(state))
    if state == "ENDED":
        ended = record.get("EndDate")
        return f"завершилась по дате{f' {ended}' if ended else ''}"
    if state == "ON":
        if record.get("StatusPayment") == "DISALLOWED":
            return "показывается, но оплата запрещена"
        return STATE_RU["ON"]
    if state == "OFF":
        if status in ("DRAFT", "MODERATION", "REJECTED"):
            return STATUS_RU[status]
        if record.get("StatusPayment") == "DISALLOWED":
            return "выключена, оплата запрещена"
        return "выключена: нет средств или нет активных объявлений"
    return STATE_RU.get(state, f"состояние {state}")


def row_of(record: dict, account=None) -> dict:
    """Плоская строка кампании: то, что уходит в TSV, в `--csv` и в `--json`.

    Плоская намеренно: по индексу ищут через `grep`, а вложенный объект в
    ячейке TSV ищется только целиком."""
    currency = record.get("Currency") or ""
    funds = funds_of(record)
    blocks = strategy_of(record)
    goals = goals_of(record)
    statistics = record.get("Statistics")
    statistics = statistics if isinstance(statistics, dict) else {}
    budget = record.get("DailyBudget")
    budget = budget if isinstance(budget, dict) else {}
    row = {
        "id": record.get("Id"),
        "name": record.get("Name") or "",
        "type": record.get("Type") or "",
        "type_ru": TYPE_RU.get(record.get("Type"), record.get("Type") or ""),
        "state": record.get("State") or "",
        "status": record.get("Status") or "",
        "status_payment": record.get("StatusPayment") or "",
        "clarification": record.get("StatusClarification") or "",
        "why": why(record),
        "manageable": manageable(record),
        "settings_read": missing_field_set(record) is None,
        "start_date": record.get("StartDate") or "",
        "end_date": record.get("EndDate") or "",
        "create_time": record.get("CreateTime") or "",
        "timezone": record.get("TimeZone") or "",
        "currency": currency,
        "strategy_search": (blocks.get("Search") or {}).get("type") or "",
        "strategy_network": (blocks.get("Network") or {}).get("type") or "",
        "daily_budget": value_text("DailyBudget", record.get("DailyBudget")),
        "spend": money_text(funds["spend"]),
        "balance": money_text(funds["balance"]),
        "balance_scope": funds["balance_scope"],
        "goals": len(goals),
        "negative_keywords": len(items_of(record.get("NegativeKeywords"))),
        "shared_sets": len(items_of(type_body(record).get("NegativeKeywordSharedSetIds"))),
        "counters": len(counter_ids(record)),
        "clicks": statistics.get("Clicks"),
        "impressions": statistics.get("Impressions"),
    }
    if account is not None:
        row["url"] = campaign_url(account, row["id"])
        row["edit_url"] = campaign_url(account, row["id"], edit=True)
    return row


# Колонки TSV-индекса. Латиницей, как в индексе кабинетов: по нему ищут
# командой, а не глазами.
COLUMNS = [
    "id", "name", "type", "state", "status", "status_payment", "why",
    "manageable", "settings_read", "start_date", "end_date", "timezone", "currency",
    "strategy_search", "strategy_network", "daily_budget", "spend", "balance",
    "balance_scope", "goals", "negative_keywords", "shared_sets", "counters",
    "clicks", "impressions",
]


def index_of(records: list) -> str:
    return tsv([row_of(record) for record in records], COLUMNS)


def schedule_lines(record: dict) -> list:
    """Расписание показов человеческим языком.

    Директ отдаёт семь строк по двадцать пять чисел: день недели и коэффициент
    на каждый час. Печатать их как есть — семь строк из тридцати на одно поле,
    поэтому одинаковые дни объединяются, а часы сворачиваются в промежутки."""
    targeting = record.get("TimeTargeting")
    if not isinstance(targeting, dict):
        return []
    items = items_of(targeting.get("Schedule"))
    days, broken = {}, []
    for item in items:
        parts = str(item).split(",")
        if len(parts) != 25 or not (parts[0].strip().isascii()
                                    and parts[0].strip().isdigit()):
            # Строка не той формы показывается как есть и отдельно от разобранных.
            # Разбирать её «как получится» опаснее, чем признать непонятной:
            # двадцать четыре поля вместо двадцати пяти прочитались бы как
            # сутки со сдвигом на час, и расписание выглядело бы разобранным.
            broken.append(str(item))
            continue
        number = int(parts[0])
        name = WEEKDAYS[number - 1] if 1 <= number <= len(WEEKDAYS) else str(number)
        days.setdefault(",".join(parts[1:]), []).append(name)
    lines = []
    for pattern, names in days.items():
        hours = [value.strip() for value in pattern.split(",")]
        working = [number for number, value in enumerate(hours) if value not in ("0", "")]
        if not working:
            lines.append(f"{', '.join(names)}: показов нет")
            continue
        if set(hours) == {"100"}:
            # Ярлык только для полной ставки. Сутки по одному коэффициенту в
            # пятьдесят процентов — это тоже «круглосуточно» по часам, но
            # ставка урезана вдвое, и словом «круглосуточно» она молча
            # пропадает: два разных расписания выглядят одинаково.
            lines.append(f"{', '.join(names)}: круглосуточно")
            continue
        # Промежутки строятся по коэффициенту, а не по факту показов. Иначе
        # девять часов по половине ставки и шесть по полной сливаются в один
        # промежуток с припиской «коэффициенты 50», и по ней не понять, какие
        # именно часы урезаны, — а именно это и настраивали.
        spans, start = [], None
        for number in range(25):
            value = hours[number] if number < 24 else None
            if start is not None and (number == 24 or value != hours[start]):
                spans.append((start, number, hours[start]))
                start = None
            if number < 24 and value not in ("0", "") and start is None:
                start = number
        shown = ", ".join(
            f"{begin:02d}:00–{end:02d}:00"
            + ("" if value == "100" else f" ({value} %)")
            for begin, end, value in spans)
        lines.append(f"{', '.join(names)}: {shown}")
    for item in broken:
        lines.append(f"строка не разбирается: {excerpt(item, 80)}")
    weekends = targeting.get("ConsiderWorkingWeekends")
    if weekends:
        lines.append(f"рабочие выходные учитываются: {'да' if weekends == 'YES' else 'нет'}")
    holidays = value_text("HolidaysSchedule", targeting.get("HolidaysSchedule"))
    if holidays:
        # Структурой, а не отметкой «задано»: у праздничного расписания четыре
        # поля, и запрет показов задаётся не нулевым коэффициентом, а
        # `SuspendOnHolidays: YES` (`references/ERRORS_AND_LIMITS.md`,
        # раздел 3). Отметка «задано» не различает эти случаи вовсе.
        lines.append(f"праздники: {holidays}")
    return lines


# --------------------------------------------------------------------------
# Чтение
# --------------------------------------------------------------------------

# Справочник лимитов, прочитанный один раз на запуск: свод по агентству зовёт
# чтение на каждый из 258 кабинетов, а цены от кабинета не зависят.
_LIMITS: list = []


def limits():
    """Справочник лимитов. Читается по требованию и один раз.

    Читает его `writer.Limits`, единственный его читатель в скилле. Ввозится
    он тоже по требованию: команда чтения не должна тянуть движок записи, пока
    дело не дошло до похода в сеть."""
    from writer import Limits

    if not _LIMITS:
        _LIMITS.append(Limits.load())
    return _LIMITS[0]


def units_cost(service: str, method: str, objects=None) -> int:
    """Во сколько баллов обойдётся вызов. Оценка сверху, из `limits.json`.

    Нужна решению «чьими баллами платим»: без цены порогом служит один балл, и
    кабинет с пятью баллами отправляется платить за вызов ценой в пятьдесят —
    получая отказ 152 при живых баллах агентства. Человеку это число не
    показывают: в нём учтён порог отказа, и цена вызова с ценой объекта под ним
    неразличимы. Для показа есть `Limits.units_tariff`."""
    return limits().units_cost(service, method, objects)


def request_params() -> dict:
    """Тело запроса `Campaigns.get`: весь срез кабинета и все наборы полей."""
    params = {
        "SelectionCriteria": {"States": list(ALL_STATES)},
        "FieldNames": list(COMMON_FIELDS),
    }
    params.update({name: list(fields) for name, fields in TYPE_FIELDS.items()})
    return params


def read_slice(cache: Cache, client, accounts, login: str):
    """Срез кампаний кабинета: из кэша, а при промахе — из API.

    `use_operator_units` берётся у списка кабинетов и передаётся в вызов явно.
    Клиент сам его не подставляет — он про кэш не знает, — и вызов без решения
    на кабинете с исчерпанными баллами отказывает вместо того, чтобы заплатить
    баллами агентства.

    Передаётся не ответ, а способ его получить: срез склеивается из страниц, а
    остаток кабинета меняется на каждой. Решение, снятое до первой страницы, к
    третьей описывает прошлое."""
    def produce():
        # Сколько кампаний в кабинете, до ответа не знает никто — ни три, ни
        # три тысячи, — поэтому цена берётся по пределу ответа. Оценка выходит
        # сверху, и это её обязанность: заниженная роняет вызов отказом 152.
        # Считается она здесь, а не выше: попаданию в кэш цена не нужна.
        need = units_cost("campaigns", "get")
        return client.get_all(
            "campaigns", request_params(), account=login,
            use_operator_units=lambda: accounts.use_operator_units(login,
                                                                   need=need),
        )

    return cache.through("campaigns", "structure", produce, index=index_of)


def shared_balance(client, login: str):
    """Остаток общего счёта кабинета: `AccountManagement`, операция `Get`.

    Версия 5 баланса общего счёта не отдаёт ни одним полем: `Campaigns.get`
    возвращает `SharedAccountFundsParam` с двумя полями, `Refund` и `Spend`.
    Баллов четвёртая версия за этот вызов не берёт.

    Отказ здесь не роняет чтение кампаний: без остатка сводка беднее, но
    кампании прочитаны. Сказать об этом надо вслух — молчание превращает
    отсутствие остатка в ноль."""
    if not login:
        return None
    param = {"Action": "Get", "SelectionCriteria": {"Logins": [login]}}
    try:
        answer = client.call_v4("AccountManagement", param)
    except DirectFailure as failure:
        warn(f"Остаток общего счёта {login} прочитать не удалось. {failure}")
        return None
    result = answer.result
    if not isinstance(result, dict):
        warn(f"AccountManagement.Get: ответ пришёл не объектом "
             f"({type(result).__name__}). Остаток неизвестен.")
        return None
    try:
        records = required(result, "Accounts", list, "AccountManagement.Get")
    except TransportFailure as failure:
        warn(str(failure))
        return None
    for item in records:
        if not isinstance(item, dict):
            continue
        if str(item.get("Login") or "").casefold() != login.casefold():
            continue
        # Суммы четвёртой версии — строки в единицах валюты («1384.08»), а не
        # микроединицы версии 5. Приведение через `money.to_api`, чтобы дальше
        # всё считалось и печаталось одним множителем.
        try:
            amount = money.to_api(item.get("Amount"))
            available = money.to_api(item.get("AmountAvailableForTransfer"))
        except money.MoneyError as failure:
            warn(f"AccountManagement.Get: остаток {login} не читается как "
                 f"сумма ({failure}).")
            return None
        return {
            "login": login,
            "account_id": item.get("AccountID"),
            "amount": amount,
            "available": available,
            "currency": item.get("Currency") or "",
        }
    warn(f"AccountManagement.Get: кабинета {login} в ответе нет — остаток "
         f"общего счёта неизвестен.")
    return None


def campaign_regions(client, accounts, login: str, campaign_id: int):
    """Регионы показа кампании: они живут в её группах, а не в ней самой.

    Значения регионов не все положительные: `0` означает «все регионы», а минус
    перед идентификатором **выключает** регион (`references/API_OBJECTS.md`,
    раздел 3.2). Отсюда два следствия. Первое: показывать такие числа наравне с
    обычными нельзя — «регионы: 0, -213» не читается никак. Второе:
    `Dictionaries.getGeoRegions` отвергает **весь** вызов кодом 5005 «значение
    поля regionIds должно быть целым положительным числом» (проверено
    29.08.2026), то есть одна исключённая область оставила бы без имён и все
    остальные регионы."""
    try:
        # Групп у кампании бывает и одна, и тысяча: до ответа их число
        # неизвестно, и цена чтения оценивается по пределу ответа — сверху.
        need = units_cost("adgroups", "get")
        groups = client.get_all(
            "adgroups",
            {"SelectionCriteria": {"CampaignIds": [campaign_id]},
             "FieldNames": ["Id", "Name", "CampaignId", "RegionIds"]},
            account=login,
            use_operator_units=lambda: accounts.use_operator_units(login,
                                                                   need=need),
        )
    except DirectFailure as failure:
        warn(f"Регионы кампании {campaign_id} прочитать не удалось. {failure}")
        return None
    targets, names = [], {}
    for group in groups:
        if not isinstance(group, dict):
            continue
        everywhere, included, excluded = False, [], []
        for region in items_of(group.get("RegionIds")):
            # Логическое значение отсекается до целого: `True` — это не регион
            # номер один, а чужое значение в поле регионов.
            if isinstance(region, bool) or not isinstance(region, int):
                continue
            if region == 0:
                everywhere = True
            elif region < 0 and -region not in excluded:
                excluded.append(-region)
            elif region > 0 and region not in included:
                included.append(region)
        # Наборы разных групп не складываются. Группа «Россия, кроме Москвы» и
        # группа «Москва» — это законная пара, а их объединение объявило бы
        # Москву и включённой, и выключенной разом. Одинаковые наборы при этом
        # сводятся в один: у кампании из полусотни групп обычно один таргетинг
        # на всех, и полсотни одинаковых строк — не сводка.
        key = (everywhere, tuple(sorted(included)), tuple(sorted(excluded)))
        found = next((item for item in targets if item["key"] == key), None)
        if found is None:
            found = {"key": key, "everywhere": everywhere, "included": included,
                     "excluded": excluded, "groups": []}
            targets.append(found)
        found["groups"].append(group.get("Id"))
    # В справочник уходят только положительные идентификаторы — и у включённых
    # регионов, и у выключенных: имя нужно и тем, и другим, а знак Директ
    # отвергает.
    asked = sorted({region for item in targets
                    for region in item["included"] + item["excluded"]})
    if asked:
        try:
            for region in client.geo_regions(region_ids=asked):
                if isinstance(region, dict):
                    names[region.get("GeoRegionId")] = region.get("GeoRegionName")
        except DirectFailure as failure:
            warn(f"Названия регионов прочитать не удалось. {failure}")
    for item in targets:
        item.pop("key")
    return {"groups": len(groups), "targets": targets, "names": names}


# --------------------------------------------------------------------------
# Отбор
# --------------------------------------------------------------------------

def chosen(records: list, args) -> list:
    """Отбор по состоянию, статусу и типу — по прочитанному, а не запросом.

    Фильтры не стоят баллов и не заводят второй записи кэша: срез кабинета
    один, а отбор из него — дело памяти."""
    found = list(records)
    if args.state:
        found = [item for item in found if item.get("State") in args.state]
    if args.status:
        found = [item for item in found if item.get("Status") in args.status]
    if args.type:
        found = [item for item in found if item.get("Type") in args.type]
    found.sort(key=lambda item: (STATE_ORDER.get(item.get("State"), len(STATE_ORDER)),
                                 str(item.get("Name") or ""), item.get("Id") or 0))
    return found


def one_campaign(records: list, query: str) -> dict:
    """Кампания по идентификатору или по куску названия.

    Идентификатор надёжнее, но человек чаще помнит название. Неоднозначность —
    отказ с перечнем, а не молчаливый выбор первой попавшейся."""
    wanted = query.strip()
    if not wanted:
        # Пустой отбор отбором не является — то же правило, что у
        # `objects.selection` с пустым перечнем идентификаторов. Молча он хуже
        # отказа: кусок названия из пустой строки содержится в **каждом**
        # названии, и кабинет с одной кампанией отдавал бы её как «названную»,
        # а команда отчитывалась бы успехом. Приходит такая строка не по
        # опечатке, а из `--campaign "$CAMPAIGN"` с незаданной переменной.
        raise DirectFailure(
            "Отбор кампании пуст. Пустая строка — не кусок названия: она "
            "содержится в любом, и выбор был бы случайным. Назовите "
            "идентификатор или часть названия, либо уберите аргумент."
        )
    # `isascii()` рядом с `isdigit()`: второй истинен для «²» и для цифр других
    # письменностей, а `int()` из них берёт не все, и `--campaign ²` печатал
    # трассировку вместо отказа. Теперь такая строка разбирается как кусок
    # названия — то есть отвечает «кампаний не нашлось», а не падает.
    if wanted.isascii() and wanted.isdigit():
        number = int(wanted)
        for record in records:
            if record.get("Id") == number:
                return record
        raise DirectFailure(
            f"Кампании {number} в кабинете нет. Список — та же команда без "
            f"--campaign."
        )
    folded = wanted.casefold()
    found = [record for record in records
             if folded in str(record.get("Name") or "").casefold()]
    if len(found) == 1:
        return found[0]
    if not found:
        raise DirectFailure(
            f"По запросу «{wanted}» кампаний не нашлось. Ищется кусок "
            f"названия или идентификатор целиком."
        )
    listed = ", ".join(f"{item.get('Id')} {item.get('Name')}" for item in found[:SHOWN])
    raise DirectFailure(
        f"По запросу «{wanted}» нашлось {len(found)}: {listed}. Назовите "
        f"идентификатор."
    )


def counted(records: list) -> dict:
    """Сколько кампаний в каком состоянии."""
    tally = {}
    for record in records:
        tally[record.get("State")] = tally.get(record.get("State"), 0) + 1
    return tally


def tally_text(tally: dict) -> str:
    parts = [f"{STATE_RU.get(state, state)} {count}"
             for state, count in sorted(tally.items(),
                                        key=lambda pair: STATE_ORDER.get(pair[0], 9))]
    return ", ".join(parts) if parts else "нет"


# --------------------------------------------------------------------------
# Сводки
# --------------------------------------------------------------------------

def export_line(path) -> list:
    """Строка про выгрузку — **до** подвала, а не после него.

    Подвал «Полные данные — …» обязан быть последней строкой: по ней ищут файл,
    в котором лежит показанное целиком. Строка про `--csv`, напечатанная после
    него, становится последней сама и уводит к файлу, где сокращённого нет."""
    return [] if path is None else [f"Выгрузка по просьбе: {short(path)}"]


def account_line(login: str, entry, balance) -> str:
    age = f"из кэша, {human_age(entry.age)}" if entry.hit else "прочитано заново"
    tail = ""
    if balance is not None:
        tail = (f" · общий счёт: {money_text(balance['amount'], balance['currency'])}"
                f", к переводу {money_text(balance['available'])}")
    return f"Кабинет {login} · {age}{tail}"


def campaign_line(record: dict) -> str:
    row = row_of(record)
    money_part = ""
    if row["spend"]:
        money_part = f" · расход {row['spend']}"
    elif row["balance"]:
        money_part = f" · остаток {row['balance']}"
    mark = "" if row["manageable"] else " · управление недоступно"
    return (f"  {row['id']:<12} {row['name'][:38]:<38} {row['type_ru'][:12]:<12} "
            f"{row['why']}{money_part}{mark}")


def report_list(login: str, entry, balance, found: list, total: int,
                spent: int, export=None) -> None:
    lines = [account_line(login, entry, balance), f"Открыть кабинет: {account_url(login)}"]
    tally = counted(found)
    shown_of = "" if len(found) == total else f" из {total}"
    lines.append(f"Кампаний {len(found)}{shown_of} · {tally_text(tally)}")
    if balance is None and any(funds_of(item)["balance_scope"] == "account"
                               for item in found):
        lines.append("Остаток общего счёта не прочитан — расход показан без него.")
    lines.append("")
    # На кампанию теперь две строки; оставляем место для итогов и пути к файлу,
    # чтобы ограничение outline не отрезало ссылку от последней кампании.
    shown = 10
    for record in found[:shown]:
        lines.append(campaign_line(record))
        lines.append(f"  Открыть кампанию: {campaign_url(login, record['Id'])}")
    if len(found) > shown:
        lines.append(f"  … ещё {plural(len(found) - shown, 'кампания', 'кампании', 'кампаний')}"
                     f" — в файле ниже или через grep по TSV рядом с ним")
    unmanageable = sorted({unmanageable_reason(item) for item in found
                           if not manageable(item)})
    if unmanageable:
        lines.append("Управление недоступно — " + "; ".join(unmanageable) + ".")
    if spent:
        lines.append(f"Чтение стоило {units_said(spent)}.")
    lines.extend(export_line(export))
    outline(lines, path=entry.path, total=entry.count)


def card_lines(record: dict, balance, regions, account=None) -> list:
    """Полная карточка одной кампании."""
    row = row_of(record, account=account)
    currency = row["currency"]
    # Пояснение Директа печатается, только когда добавляет что-то к разбору:
    # у черновика оно дословно повторяет `why`, и строка «черновик · Черновик»
    # тратит место, которого в тридцати строках нет.
    clarification = row["clarification"]
    if clarification.strip().casefold() == row["why"].strip().casefold():
        clarification = ""
    lines = [
        f"{row['id']} · {row['name']}",
        f"{row['type_ru']} · {row['why']}" + (f" · {clarification}" if clarification else ""),
    ]
    if account is not None:
        lines[1:1] = [f"Открыть кабинет: {account_url(account)}",
                      f"Открыть кампанию: {row['url']}",
                      f"Настройки: {row['edit_url']}"]
    if not row["manageable"]:
        lines.append(f"Управление недоступно — {unmanageable_reason(record)}.")
    absent = missing_field_set(record)
    if absent:
        lines.append(f"Настроек типа в ответе нет: он пришёл без набора "
                     f"{absent}, и это не «настроек нет», а неполный запрос.")
    period = f"с {row['start_date'] or '—'}" + (f" по {row['end_date']}" if row["end_date"] else "")
    lines.append(f"Период: {period} · часовой пояс {row['timezone'] or '—'}"
                 + (f" · создана {row['create_time']}" if row["create_time"] else ""))

    funds = funds_of(record)
    if funds["own"]:
        # Показываются все поля счёта, а не выбранные: остаток и расход —
        # всегда, потому что они и есть ответ на вопрос «сколько денег», прочие
        # — когда не нулевые. Нулевой бонус это отсутствие бонуса, и печатать
        # его у каждой кампании значит тратить строку впустую; ненулевой — это
        # деньги, и пропасть он не должен. Структура целиком лежит в файле.
        parts = [f"{FUNDS_LABELS.get(name, name)} "
                 f"{money_text(value, currency)}".rstrip()
                 for name, value in funds["own"].items()
                 if value or name in ("Balance", "Spend")]
        where = ""
        if funds["balance_scope"] == "account":
            where = (f" · остаток общего счёта кабинета "
                     f"{money_text(balance['amount'], balance['currency'])}"
                     if balance is not None else " · остаток не прочитан")
        lines.append(f"Деньги: {' · '.join(parts)}{where}".rstrip())
    elif balance is not None:
        lines.append(f"Деньги: остаток общего счёта кабинета "
                     f"{money_text(balance['amount'], balance['currency'])}".rstrip())
    budget = value_text("DailyBudget", record.get("DailyBudget"), currency)
    if budget:
        # Структурой, а не одним `Amount`: режим `DISTRIBUTED` меняет то, как
        # бюджет расходуется в течение суток, и без него две разные настройки
        # выглядят одинаково.
        lines.append(f"Дневной бюджет: {budget}")

    for block in strategy_of(record).values():
        lines.append(f"Стратегия, {strategy_line(block, currency)}")
    rollover = type_body(record).get("WeeklyBudgetRollover")
    if rollover:
        lines.append(f"Перенос недельного бюджета: {money_text(rollover, currency)}")
    package = type_body(record).get("PackageBiddingStrategy")
    if isinstance(package, dict) and package.get("StrategyId"):
        lines.append(f"Пакетная стратегия: {package['StrategyId']} — настройки "
                     f"кампании правятся только через неё")

    goals = goals_of(record)
    if goals:
        listed = ", ".join(
            f"{goal_name(item.get('GoalId'))} по "
            f"{money_text(item.get('Value'), currency)}"
            # Признак печатается, только когда он включён: при `YES` ценность
            # берёт Метрика, и напечатанное рядом число — не то, по чему
            # оптимизируется стратегия. При `NO` это умолчание.
            + (" (ценность из Метрики)"
               if item.get("IsMetrikaSourceOfValue") == "YES" else "")
            for item in goals[:INLINE])
        more = f" и ещё {len(goals) - INLINE}" if len(goals) > INLINE else ""
        lines.append(f"Цели ({len(goals)}): {listed}{more}")

    counters = counter_ids(record)
    if counters:
        lines.append(f"Счётчики Метрики: {', '.join(str(item) for item in counters)}")

    negatives = items_of(record.get("NegativeKeywords"))
    sets = items_of(type_body(record).get("NegativeKeywordSharedSetIds"))
    if negatives or sets:
        shown = ", ".join(str(item) for item in negatives[:INLINE])
        more = f" и ещё {len(negatives) - INLINE}" if len(negatives) > INLINE else ""
        sets_text = (f" · наборов {len(sets)}: {', '.join(str(item) for item in sets)}"
                     if sets else "")
        lines.append(f"Минус-фразы кампании ({len(negatives)}): {shown}{more}{sets_text}")

    if regions is not None:
        lines.extend(regions_text(regions))

    lines.extend(f"Расписание, {item}" for item in schedule_lines(record))

    options = settings_of(record)
    if options:
        on = [name for name, value in sorted(options.items()) if value == "YES"]
        lines.append(f"Включённые настройки: {', '.join(on) if on else f'нет из {len(options)}'}")
    rest = other_settings(record, currency)
    if rest:
        lines.append(f"Прочее у типа: {' · '.join(rest)}")
    common = other_common(record, currency)
    if common:
        lines.append(f"Прочее: {' · '.join(common)}")
    for name, label in (("BlockedIps", "Запрещённые IP"),
                        ("ExcludedSites", "Запрещённые площадки")):
        values = items_of(record.get(name))
        if values:
            # Метка пишется как есть: `capitalize()` не только поднимает первую
            # букву, но и опускает остальные, и «IP» превращается в «ip».
            lines.append(f"{label}: {plural(len(values), 'запись', 'записи', 'записей')}")
    statistics = record.get("Statistics")
    if isinstance(statistics, dict) and (statistics.get("Impressions") or statistics.get("Clicks")):
        lines.append(f"Статистика за всё время: показов "
                     f"{statistics.get('Impressions')}, кликов {statistics.get('Clicks')}")
    return lines


def regions_text(regions: dict) -> list:
    """Геотаргетинг словами — по строке на каждый различный набор групп.

    Ноль и минус — не идентификаторы, а команды: «везде» и «кроме». Печатать их
    числом значит показать человеку `0, -213` вместо настройки, которая у него
    в интерфейсе выглядит понятной строкой.

    Строк несколько, потому что таргетинг живёт в группе, а не в кампании, и
    разные группы законно нацелены по-разному. Свести их в один набор нельзя:
    регион, выключенный в одной группе и включённый в другой, обслуживается — а
    объединение объявило бы его выключенным."""
    names = regions["names"]

    def listed(ids):
        shown = ", ".join(str(names.get(item, item)) for item in ids[:INLINE])
        return shown + (f" и ещё {len(ids) - INLINE}" if len(ids) > INLINE else "")

    targets = regions["targets"]
    if not targets:
        return [f"Регионы: групп {regions['groups']}, регионы не заданы"]
    lines = []
    for item in targets[:INLINE]:
        where = "все регионы" if item["everywhere"] else listed(item["included"])
        if not where:
            where = "не заданы"
        if item["excluded"]:
            where = f"{where} · кроме: {listed(item['excluded'])}"
        # Пояснение «сколько групп» нужно только там, где групп несколько и
        # нацелены они по-разному: при одном наборе на всю кампанию оно ничего
        # не добавляет, а место в тридцати строках занимает.
        whose = ("" if len(targets) == 1
                 else f" ({plural(len(item['groups']), 'группа', 'группы', 'групп')})")
        lines.append(f"Регионы{whose}: {where}")
    if len(targets) > INLINE:
        lines.append(f"… ещё {plural(len(targets) - INLINE, 'набор', 'набора', 'наборов')} "
                     f"регионов — в файле ниже")
    return lines


def store_card(cache: Cache, record: dict, regions, balance):
    """Указывать здесь на срез кабинета нельзя: регионы приходят из групп, их
    имена — из справочника, и в `campaigns.json` ни того, ни другого нет. А
    сводка регионы сокращает — «и ещё 12», — и восстановить сокращённое было бы
    неоткуда. Правило «полные данные всегда в файл» держится на том, что файл
    содержит именно показанное.

    Остатка общего счёта в файле нет намеренно. Он печатается целиком, ничего
    не сокращая, и потому восстанавливать его неоткуда не нужно; а лечь в кэш
    он не может — деньги меняются независимо от структуры кабинета, и снимок
    получаса давности выдавался бы за текущий баланс."""
    payload = {"campaign": record, "regions": regions,
               "shared_account_read": balance is not None}
    return cache.write(f"campaign-{record.get('Id')}", "structure", payload)


def report_card(record: dict, entry, balance, regions, spent: int, export=None,
                account=None) -> None:
    lines = card_lines(record, balance, regions, account=account)
    if spent:
        lines.append(f"Чтение стоило {units_said(spent)}.")
    lines.extend(export_line(export))
    outline(lines, path=entry.path)


def as_json(login: str, entry, balance, found: list, total: int,
            record=None, regions=None, card=None) -> dict:
    body = {
        "account": login,
        "account_url": account_url(login),
        "from_cache": entry.hit,
        "cache": None if entry.path is None else short(entry.path),
        "total": total,
        "matched": len(found),
        "states": counted(found),
        "shared_account": balance,
    }
    if record is not None:
        body["campaign"] = card_json(record, regions, account=login)
        body["card"] = None if card is None or card.path is None else short(card.path)
        return body
    body["campaigns"] = [row_of(item, account=login) for item in found[:JSON_LIMIT]]
    return body


def card_json(record: dict, regions, account=None) -> dict:
    """Карточка машиночитаемо: нормализованная шапка и сырые настройки."""
    body = dict(row_of(record, account=account))
    body["strategy"] = strategy_of(record)
    body["goals"] = goals_of(record)
    body["settings"] = settings_of(record)
    body["negative_keywords"] = items_of(record.get("NegativeKeywords"))
    body["shared_sets"] = items_of(type_body(record).get("NegativeKeywordSharedSetIds"))
    body["counters"] = counter_ids(record)
    body["schedule"] = schedule_lines(record)
    body["regions"] = regions
    body["raw"] = record
    return body


# --------------------------------------------------------------------------
# Свод по всем кабинетам
# --------------------------------------------------------------------------

def sweep(client, accounts: Accounts, args) -> dict:
    """Кампании всех клиентов агентского токена — обходом.

    Метода, возвращающего кампании всех клиентов разом, в API нет. Перечень
    кабинетов даёт `accounts.py`, дальше `Campaigns.get` с `Client-Login` по
    каждому: цена вызова за кабинет плюс цена за каждую прочитанную кампанию.
    Оба числа берутся из `limits.json`, а не пересказываются здесь: пересказ
    разошёлся бы со справочником молча, и человек услышал бы не ту цену.

    Архивные кабинеты в обход не входят: у боевого агентства их 225 из 258, и
    обход по ним — это девять десятых расхода на закрытые счета. Отдельный
    архивный кабинет читается по имени, через `--account`."""
    living = [cabinet for cabinet in accounts.cabinets if not cabinet.archived]
    if not living:
        raise DirectFailure("Действующих кабинетов в списке нет. Обновите "
                            "список: accounts.py --no-cache")
    # Тариф успеха, а не оценка для решения об оплате: в той учтён порог
    # отказа, и под ним цена вызова с ценой кампании неразличимы — «пятьдесят
    # баллов за кабинет плюс ноль за кампанию». Человеку называют то, во что
    # обход обойдётся, если он удастся.
    per_call, per_campaign, each = limits().units_tariff("campaigns", "get")
    said = (f"{per_campaign} за каждую кампанию" if each == 1
            else f"{per_campaign} за каждые {each} кампаний")
    warn(f"Обход {plural(len(living), 'кабинета', 'кабинетов', 'кабинетов')}: "
         f"около {units_said(per_call * len(living))} плюс {said}.")
    rows, failures, records = [], [], []
    for cabinet in living:
        try:
            entry = read_slice(Cache.from_args(args, account=cabinet.login, warn=warn),
                               client, accounts, cabinet.login)
        except DirectFailure as failure:
            # Один недоступный кабинет не отменяет свод: у агентства их сотни,
            # и отказ по правам на одном — обычное дело, а не сбой обхода.
            failures.append((cabinet.login, str(failure)))
            continue
        found = chosen(entry.data, args)
        rows.append({"login": cabinet.login, "name": cabinet.name,
                     "total": entry.count, "matched": len(found),
                     "states": counted(found), "from_cache": entry.hit})
        records.extend(dict(row_of(record, account=cabinet.login), account=cabinet.login)
                       for record in found)
    return {"cabinets": rows, "failures": failures, "campaigns": records,
            "walked": len(living)}


def store_sweep(accounts: Accounts, result: dict):
    """Свод целиком в файл — вместе с кабинетами, которые прочитать не удалось.

    Именно вместе: сводка сокращает и перечень кабинетов, и перечень отказов, а
    у агентства отказ по правам на отдельном кабинете — обычное дело. Файл, в
    котором лежат только удавшиеся кабинеты, на вопрос «кого пропустили» не
    отвечает, и узнать это можно было бы только повторным обходом."""
    payload = {
        "owner": accounts.owner,
        "walked": result["walked"],
        "cabinets": result["cabinets"],
        "failures": [{"account": login, "reason": reason}
                     for login, reason in result["failures"]],
        "campaigns": result["campaigns"],
    }
    return Cache(warn=warn).write(
        f"campaigns-sweep-{accounts.profile}", "structure", payload,
        index=lambda body: tsv(body["campaigns"], ["account"] + COLUMNS),
    )


def report_sweep(accounts: Accounts, result: dict, entry, spent: int,
                 export=None) -> None:
    rows = result["cabinets"]
    total = sum(row["matched"] for row in rows)
    lines = [f"Свод по клиентам агентства {accounts.owner or '—'} · "
             f"кабинетов {len(rows)} из {result['walked']} · архивные пропущены",
             f"Кампаний {total}"]
    lines.append("")
    for row in sorted(rows, key=lambda item: -item["matched"])[:SHOWN]:
        name = (row["name"] or "—")[:28]
        lines.append(f"  {row['login']:<24} {name:<28} "
                     f"{row['matched']:>5} · {tally_text(row['states'])}")
    if len(rows) > SHOWN:
        lines.append(f"  … ещё {plural(len(rows) - SHOWN, 'кабинет', 'кабинета', 'кабинетов')}")
    for login, reason in result["failures"][:INLINE]:
        lines.append(f"  не прочитан {login}: {reason}")
    if len(result["failures"]) > INLINE:
        lines.append(f"  … и ещё {len(result['failures']) - INLINE} непрочитанных кабинетов")
    if spent:
        lines.append(f"Обход стоил {units_said(spent)}.")
    lines.append("Срез каждого кабинета — в cache/<логин>/campaigns.json и "
                 "одноимённом TSV.")
    lines.extend(export_line(export))
    outline(lines, path=entry.path)


# --------------------------------------------------------------------------
# Аргументы
# --------------------------------------------------------------------------

class Parser(argparse.ArgumentParser):
    """Ошибка аргументов — код 2, и без секретов в тексте."""

    def error(self, message: str):
        self.exit(2, redact(f"{self.prog}: {message}\n"))


def enum_list(value: str, allowed: list, what: str, parser) -> list:
    """Перечень значений через запятую, сверенный с перечислением Директа.

    Сверяется, хотя отбор идёт по памяти и опечатка баллов не стоит: молча
    отдать ноль кампаний на `--state SUSPENED` значит ответить «их нет» на
    вопрос, которого не поняли."""
    chosen_values = []
    for item in str(value).split(","):
        name = item.strip().upper()
        if not name:
            continue
        if name not in allowed:
            parser.error(f"{what}: «{item.strip()}» — не значение перечисления. "
                         f"Допустимо: {', '.join(allowed)}")
        if name not in chosen_values:
            chosen_values.append(name)
    if not chosen_values:
        parser.error(f"{what}: перечень пуст")
    return chosen_values


def dump_csv(rows: list, columns: list, path: Path) -> None:
    """Полная выгрузка в файл — тем же TSV, что и индекс кэша."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(tsv(rows, columns), encoding="utf-8")


def main(argv=None) -> int:
    # До разбора аргументов: argparse печатает негодное значение сам, и токен,
    # случайно попавший в командную строку, ушёл бы в stderr раньше, чем скилл
    # узнал бы, что это токен.
    preload_secrets()

    parser = Parser(description="Кампании кабинета: список, фильтры, карточка.")
    parser.add_argument("--env", choices=("production", "test_cabinet"),
                        help="контур и набор переменных")
    parser.add_argument("--account", metavar="ЛОГИН",
                        help="логин кабинета; при отсутствии берётся активный")
    parser.add_argument("--campaign", metavar="ID|ЧАСТЬ_НАЗВАНИЯ",
                        type=incoming.campaign_selector,
                        help="полная карточка одной кампании")
    parser.add_argument("--state", metavar="СПИСОК",
                        help=f"состояния через запятую: {', '.join(ALL_STATES)}")
    parser.add_argument("--status", metavar="СПИСОК",
                        help=f"статусы модерации: {', '.join(ALL_STATUSES)}")
    parser.add_argument("--type", metavar="СПИСОК",
                        help=f"типы кампаний: {', '.join(sorted(TYPE_RU))}")
    parser.add_argument("--all-accounts", action="store_true", dest="all_accounts",
                        help="свод по всем клиентам агентского токена")
    parser.add_argument("--csv", metavar="ФАЙЛ", type=Path,
                        help="выгрузить отобранное в файл")
    parser.add_argument("--json", action="store_true",
                        help="машиночитаемый вывод")
    add_arguments(parser)
    args = parser.parse_args(argv)

    args.state = enum_list(args.state, ALL_STATES, "--state", parser) if args.state else None
    args.status = enum_list(args.status, ALL_STATUSES, "--status", parser) if args.status else None
    args.type = enum_list(args.type, sorted(TYPE_RU), "--type", parser) if args.type else None
    if args.all_accounts and args.campaign:
        parser.error("--all-accounts и --campaign взаимоисключающие: свод "
                     "показывает кабинеты, карточка — одну кампанию")
    if args.all_accounts and args.account:
        parser.error("--all-accounts и --account взаимоисключающие: свод идёт "
                     "по всем кабинетам сразу")

    try:
        client = Client.from_env(profile=args.env, warn=warn)
        # `--no-cache` здесь про кампании, а не про список кабинетов: перечень
        # агентства стоит балла за кабинет и обновляется своей командой,
        # `accounts.py --no-cache`.
        accounts = Accounts.load(client, warn=warn)

        if args.all_accounts:
            if accounts.kind != AGENCY:
                raise DirectFailure(
                    "Свод по клиентам просят у агентского токена; этот "
                    f"токен — клиентский ({accounts.owner or '—'}). Кампании "
                    f"его единственного кабинета показывает та же команда без "
                    f"--all-accounts."
                )
            result = sweep(client, accounts, args)
            entry = store_sweep(accounts, result)
            if args.csv:
                dump_csv(result["campaigns"], ["account"] + COLUMNS, args.csv)
            spent = client.units.report()["spent"]
            if args.json:
                say(json.dumps({
                    "owner": accounts.owner,
                    "cabinets": result["cabinets"],
                    "failures": [{"account": login, "reason": reason}
                                 for login, reason in result["failures"]],
                    "campaigns": result["campaigns"][:JSON_LIMIT],
                    "campaigns_total": len(result["campaigns"]),
                    "sweep": None if entry.path is None else short(entry.path),
                    "csv": None if args.csv is None else str(args.csv),
                }, ensure_ascii=False))
            else:
                report_sweep(accounts, result, entry, spent, args.csv)
            # Ненулевой код — только когда не прочитано ничего. Отказ по правам
            # на отдельном кабинете у агентства обычное дело, и объявлять из-за
            # него неудачей весь свод значит приучить не смотреть на код.
            return 1 if not result["cabinets"] else 0

        login = resolve_account(accounts, client, args.account)
        cache = Cache.from_args(args, account=login, warn=warn)
        entry = read_slice(cache, client, accounts, login)
        found = chosen(entry.data, args)
        balance = None
        if any(funds_of(record)["balance_scope"] == "account" for record in entry.data):
            balance = shared_balance(client, login)

        record = one_campaign(entry.data, args.campaign) if args.campaign else None
        regions = card = None
        if record is not None:
            regions = campaign_regions(client, accounts, login, record.get("Id"))
            card = store_card(cache, record, regions, balance)

        if args.csv:
            dump_csv([row_of(item) for item in found], COLUMNS, args.csv)
        spent = client.units.report()["spent"]

        if args.json:
            say(json.dumps(
                as_json(login, entry, balance, found, entry.count, record, regions,
                        card),
                ensure_ascii=False))
        elif record is not None:
            report_card(record, card, balance, regions, spent, args.csv, account=login)
        else:
            report_list(login, entry, balance, found, entry.count, spent, args.csv)
    except Ambiguous as failure:
        warn(str(failure))
        for match in failure.matches[:SHOWN]:
            warn(f"  {match.cabinet.login} · {match.reason}")
        return 1
    except DirectFailure as failure:
        warn(str(failure))
        return 1
    except OSError as failure:
        warn(redact(f"Не удалось записать файл: {failure}"))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
