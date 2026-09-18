#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""Ставки и корректировки ставок."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "lib"))

import cache as cache_module  # noqa: E402
import phrases  # noqa: E402
import policy as policies  # noqa: E402
from accounts import Accounts, resolve_account  # noqa: E402
from config import DirectFailure, excerpt, preload_secrets, redact  # noqa: E402
from direct import Client  # noqa: E402
from money import MoneyError, format_api, to_api  # noqa: E402
from writer import showing, Limits, Operation, Task, Writer, unrun  # noqa: E402

BIDS = "bids"
KEYWORD_BIDS = "keywordbids"
MODIFIERS = "bidmodifiers"
CAMPAIGNS = "campaigns"

# Поля ставки, которые скилл читает до и после записи. Данных торгов здесь нет
# намеренно: конвейер сравнивает **весь** прочитанный срез, когда сторожит окно
# между подтверждением и записью, а торги меняются сами по себе каждую минуту —
# задача останавливалась бы на ровном месте, ни разу не дойдя до записи.
BID_FIELDS = ("KeywordId", "AdGroupId", "CampaignId", "Bid", "ContextBid",
              "AutotargetingSearchBidIsAuto", "StrategyPriority")

# Шаг округления ставки. Директ хранит деньги целыми микро и округляет их до
# копейки, то есть до 10 000 микро; без шага «почти то же число» неотличимо от
# записанного не того (`diff.Rules`).
BID_STEP = 10_000

# Стратегии, при которых ставку вообще можно назначить (`Bids.set`).
SEARCH_MANUAL = ("HIGHEST_POSITION",)
NETWORK_MANUAL = ("MAXIMUM_COVERAGE", "MANUAL_CPM")

# Имя типовой структуры кампании по коду типа. Таблица одна на скилл и живёт в
# `phrases`: там же лежит перечень наборов минус-фраз, спрятанный внутри той же
# структуры, и две копии разошлись бы при первом новом типе. Полей отсюда
# запрашивается ровно одно — стратегия: лишние дороги вдвойне, их сравнивает
# сторож чужой правки.
CAMPAIGN_TYPES = phrases.CAMPAIGN_TYPES

# Корректировки, у которых значение одно: и пишутся, и читаются одним объектом
# под одним именем.
SINGLE_ADJUSTMENTS = {
    "mobile": ("MobileAdjustment", "MOBILE_ADJUSTMENT"),
    "tablet": ("TabletAdjustment", "TABLET_ADJUSTMENT"),
    "desktop": ("DesktopAdjustment", "DESKTOP_ADJUSTMENT"),
    "desktop-only": ("DesktopOnlyAdjustment", "DESKTOP_ONLY_ADJUSTMENT"),
    "video": ("VideoAdjustment", "VIDEO_ADJUSTMENT"),
    "smart": ("SmartAdAdjustment", "SMART_AD_ADJUSTMENT"),
    "adgroup": ("AdGroupAdjustment", "AD_GROUP_ADJUSTMENT"),
}

# Корректировки, которых у объекта бывает несколько. Уходят массивом под
# именем во множественном числе, а читаются по одной, объектом в единственном:
# `BidModifiers.get` отдаёт по строке на корректировку. Общего пути между
# запросом и чтением у них нет вовсе, поэтому расхождение форм объявляется
# парой имён.
MANY_ADJUSTMENTS = {
    "demographics": ("DemographicsAdjustments", "DemographicsAdjustment",
                     "DEMOGRAPHICS_ADJUSTMENT"),
    "retargeting": ("RetargetingAdjustments", "RetargetingAdjustment",
                    "RETARGETING_ADJUSTMENT"),
    "region": ("RegionalAdjustments", "RegionalAdjustment",
               "REGIONAL_ADJUSTMENT"),
    "serp": ("SerpLayoutAdjustments", "SerpLayoutAdjustment",
             "SERP_LAYOUT_ADJUSTMENT"),
    "income": ("IncomeGradeAdjustments", "IncomeGradeAdjustment",
               "INCOME_GRADE_ADJUSTMENT"),
}

# Имя структуры чтения по типу корректировки — обратный указатель к двум
# таблицам выше. Нужен `set`: там тип известен только из прочитанного объекта,
# и под каким именем читать коэффициент, из запроса не видно.
# Область значений среза у корректировок, которых бывает несколько. У трёх
# срез — перечень, у двух — идентификатор чужого объекта. Перечни взяты из
# документации `BidModifiers.add`; проверять их надо до отправки, иначе опечатка
# в срезе уходит в Директ, стоит баллов и возвращается кодом, который человеку
# ещё предстоит перевести обратно в «вы ошиблись в слове».
SELECTOR_NAMES = {
    "serp": ("SerpLayout", ("ALONE", "SUGGEST")),
    "income": ("Grade", ("VERY_HIGH", "HIGH", "ABOVE_AVERAGE")),
}

# Половины демографического среза. Перечни — оттуда же, из документации
# `BidModifiers.add`. Проверять их надо целиком, а не по началу строки:
# `GENDER_TYPO` начинается правильно и не значит ничего, а Директ отвергает
# такой элемент кодом за баллы. `AGE_45` документация числит устаревшим и
# заменённым на `AGE_45_54` и `AGE_55`, поэтому его здесь нет: срез, который
# скилл предлагает завести, должен быть тем, который Директ рекомендует.
DEMOGRAPHICS = {
    "Gender": ("GENDER_MALE", "GENDER_FEMALE"),
    "Age": ("AGE_0_17", "AGE_18_24", "AGE_25_34", "AGE_35_44", "AGE_45_54",
            "AGE_55"),
}

# Срезы-идентификаторы: регион и условие ретаргетинга. Здесь перечня нет и быть
# не может — это чужие объекты кабинета, — но нечисловое значение роняло бы
# команду трассировкой `ValueError` вместо отказа с объяснением.
SELECTOR_IDS = {
    "retargeting": ("RetargetingConditionId", "условия ретаргетинга"),
    "region": ("RegionId", "региона"),
}

READ_BY_TYPE = {kind: name for name, kind in SINGLE_ADJUSTMENTS.values()}
READ_BY_TYPE.update({kind: read for _, read, kind in MANY_ADJUSTMENTS.values()})

# Имя аргумента команды по типу корректировки. Нужно правке: диапазон
# коэффициента задан по имени аргумента, а из прочитанного объекта известен
# только тип.
KIND_BY_TYPE = {kind: name for name, (_, kind) in SINGLE_ADJUSTMENTS.items()}
KIND_BY_TYPE.update({value[2]: name for name, value in MANY_ADJUSTMENTS.items()})

# Поля каждой структуры корректировки, которые читаются до и после записи.
# `Enabled` и `Accessible` не запрашиваются: их ставит Директ, они меняются
# сами, и сторож чужой правки видел бы в них чужую правку.
ADJUSTMENT_FIELDS = {
    "MobileAdjustment": ("BidModifier", "OperatingSystemType"),
    "TabletAdjustment": ("BidModifier", "OperatingSystemType"),
    "DesktopAdjustment": ("BidModifier",),
    "DesktopOnlyAdjustment": ("BidModifier",),
    "VideoAdjustment": ("BidModifier",),
    "SmartAdAdjustment": ("BidModifier",),
    "AdGroupAdjustment": ("BidModifier",),
    "DemographicsAdjustment": ("Gender", "Age", "BidModifier"),
    "RetargetingAdjustment": ("RetargetingConditionId", "BidModifier"),
    "RegionalAdjustment": ("RegionId", "BidModifier"),
    "SerpLayoutAdjustment": ("SerpLayout", "BidModifier"),
    "IncomeGradeAdjustment": ("Grade", "BidModifier"),
}

# Уровни отбора корректировок. Оба сразу и всегда: `Levels` у
# `BidModifiers.get` обязателен, а сужение здесь означало бы, что удалённой
# считается корректировка, просто выпавшая из выборки.
LEVELS = ("CAMPAIGN", "AD_GROUP")

MODIFIER_FIELDS = ("Id", "CampaignId", "AdGroupId", "Level", "Type")


def warn(text: str) -> None:
    print(redact(text), file=sys.stderr)


def say(text: str) -> None:
    print(redact(text))


# --------------------------------------------------------------------------
# Стратегия кампании
# --------------------------------------------------------------------------

def campaign_read() -> dict:
    params = {"FieldNames": ["Id", "Name", "Type"]}
    for name in CAMPAIGN_TYPES.values():
        params[f"{name}FieldNames"] = ["BiddingStrategy"]
    return params


def strategies_of(client, account, accounts, campaigns) -> dict:
    """Тип стратегии на поиске и в сетях по каждой кампании."""
    found = {}
    request = dict(campaign_read())
    request["SelectionCriteria"] = {"Ids": sorted({int(one) for one in campaigns})}
    need = Limits.load().units_cost(CAMPAIGNS, "get",
                                    len(request["SelectionCriteria"]["Ids"]))
    for item in client.get_all(CAMPAIGNS, request, account=account,
                               use_operator_units=lambda: (
                                   accounts.use_operator_units(account,
                                                               need=need))):
        body = item.get(CAMPAIGN_TYPES.get(item.get("Type"), "")) or {}
        strategy = body.get("BiddingStrategy") or {}
        found[item["Id"]] = (
            (strategy.get("Search") or {}).get("BiddingStrategyType"),
            (strategy.get("Network") or {}).get("BiddingStrategyType"),
        )
    return found


def strategy_guard(client, account, accounts, *, search: bool, network: bool):
    """Условие: стратегия кампании принимает ставку — по чтению конвейера.

    Проверка стратегии есть и до сборки задачи, и она там не лишняя: она
    называет человеку причину сразу, не заставляя ждать предпросмотра. Но
    свойством она становится только здесь. Стратегию меняет кто угодно и в
    любой момент, а сторож окна сличает **срез операции** — ставки, — и смены
    стратегии не видит вовсе: в `Bids.get` её нет.

    Условие проверяется дважды: по снимку, до вопроса, и по свежему чтению,
    после ответа и перед записью. Второй проход и есть тот, ради которого всё:
    между «да» и запросом проходит столько времени, сколько человек читал
    предпросмотр."""
    def check(known: dict) -> list:
        campaigns = {one.get("CampaignId") for one in (known or {}).values()
                     if one.get("CampaignId") is not None}
        if not campaigns:
            return []
        try:
            fits_strategy(known,
                          strategies_of(client, account, accounts, campaigns),
                          search=search, network=network)
        except DirectFailure as failure:
            return [str(failure)]
        return []
    return check


def fits_strategy(records, strategies, *, search: bool, network: bool) -> None:
    """Отказ, если стратегия кампании ставку не принимает.

    Проверяется до отправки, а не по отказу Директа, и причина не в баллах.
    Элемент, где часть параметров стратегии подходит, а часть нет,
    **применяется наполовину**: подходящие записываются, неподходящие молча
    пропускаются, а ответ приходит с предупреждением. Половина записи — худший
    исход из возможных: отчёт говорит об успехе, а в кабинете не то, что
    подтверждали."""
    said = []
    for identifier, record in sorted(records.items()):
        campaign = record.get("CampaignId")
        if campaign not in strategies:
            said.append(f"фраза {identifier}: кампания {campaign} не "
                        f"прочиталась — её нет или она закрыта правами, и "
                        f"стратегию проверить не по чему")
            continue
        on_search, on_network = strategies[campaign]
        if search and on_search not in SEARCH_MANUAL:
            said.append(
                f"фраза {identifier}: на поиске стратегия {on_search}, а ставку "
                f"принимает только {', '.join(SEARCH_MANUAL)}"
            )
        if network and on_network not in NETWORK_MANUAL:
            said.append(
                f"фраза {identifier}: в сетях стратегия {on_network}, а ставку "
                f"принимает только {', '.join(NETWORK_MANUAL)}"
            )
    if said:
        raise DirectFailure(
            "Ставку назначить нельзя — стратегия кампании ручного управления "
            "не предусматривает: " + "; ".join(said[:5])
            + ". На автоматической стратегии ставка либо игнорируется, либо "
              "сбрасывает обучение, а элемент с частично подходящими "
              "параметрами применяется наполовину."
        )


# --------------------------------------------------------------------------
# Чтение ставок и торгов
# --------------------------------------------------------------------------

def bids_read() -> dict:
    return {"FieldNames": list(BID_FIELDS)}


def read_bids(client, account, accounts, criteria) -> dict:
    request = dict(bids_read())
    request["SelectionCriteria"] = criteria
    need = Limits.load().units_cost(BIDS, "get",
                                    len(criteria.get("KeywordIds") or []) or None)
    return {item["KeywordId"]: item for item in client.get_all(
        BIDS, request, account=account,
        use_operator_units=lambda: accounts.use_operator_units(account,
                                                               need=need))}


def read_auction(client, account, accounts, criteria, *, network=False) -> dict:
    """Торги: объёмы трафика и списываемые цены. Читает, ничего не меняет.

    `AuctionBids` не запрашиваются, когда показы на поиске отключены, и
    `Coverage` — когда отключены показы в сетях: справочник это прямо
    запрещает. Здесь вопрос решает вызывающий код — он знает, какую половину
    ставки собирается писать."""
    request = {
        "SelectionCriteria": criteria,
        "FieldNames": ["KeywordId", "AdGroupId", "CampaignId", "ServingStatus"],
    }
    if network:
        request["NetworkFieldNames"] = ["Bid", "Coverage"]
    else:
        request["SearchFieldNames"] = ["Bid", "AutotargetingSearchBidIsAuto",
                                       "AuctionBids"]
    need = Limits.load().units_cost(KEYWORD_BIDS, "get",
                                    len(criteria.get("KeywordIds") or []) or None)
    return {item["KeywordId"]: item for item in client.get_all(
        KEYWORD_BIDS, request, account=account,
        use_operator_units=lambda: accounts.use_operator_units(account,
                                                               need=need))}


# Правило «названо N, прочиталось M» живёт в `phrases`: путей у него больше
# одной команды, и поставленное на один из них оно обходится тем, где его
# забыли.
all_named = phrases.all_named


def autotargeting_among(client, account, accounts, ids) -> set:
    """Какие из этих объектов — автотаргетинги, а не фразы.

    Отдельным чтением, потому что иначе неоткуда: `Bids.get` поля `Keyword` не
    отдаёт вовсе — его нет в перечне допустимых имён (замер в
    ответ API), — а признак автотаргетинга это как раз текст
    фразы. Проверка по прочитанному `Bids.get` объекту поэтому не «иногда
    ошибается», а не срабатывает никогда.

    Один вызов ради подписи в предпросмотре — размен осознанный. Ставку
    автотаргетингу назначают намеренно: правило `STR-03` только ею его и
    зануляет. Подписанный «фразой», он получает ставку по просьбе, которой не
    было, — а под отбор по группе он попадает заодно со всеми."""
    if not ids:
        return set()
    request = dict(phrases.read_params())
    request["SelectionCriteria"] = {"Ids": [int(one) for one in ids]}
    need = Limits.load().units_cost(phrases.KEYWORDS, "get", len(ids))
    return {item["Id"] for item in client.get_all(
        phrases.KEYWORDS, request, account=account,
        use_operator_units=lambda: accounts.use_operator_units(account,
                                                               need=need))
        if phrases.is_autotargeting(item)}


def criteria_from(args) -> dict:
    criteria = {}
    if args.keyword:
        criteria["KeywordIds"] = [int(one) for one in args.keyword]
    if args.group is not None:
        criteria["AdGroupIds"] = [int(args.group)]
    if args.campaign is not None:
        criteria["CampaignIds"] = [int(args.campaign)]
    if not criteria:
        raise DirectFailure(
            "Не сказано, каким фразам менять ставки: назовите `--keyword`, "
            "`--group` или `--campaign`."
        )
    return criteria


# --------------------------------------------------------------------------
# Расчёт по объёму трафика
# --------------------------------------------------------------------------

def auction_bid(record, target: int, *, network: bool):
    """Ставка из торгов под желаемый объём трафика, либо `None`.

    Берётся строка с **ближайшим не меньшим** объёмом: просили семьдесят
    процентов трафика — значит нужна ставка, которая семьдесят даёт, а не
    шестьдесят пять. Точного значения в таблице может не быть вовсе: Директ
    отдаёт свой набор точек, и он неравномерный.

    `None` означает, что торгов по этой фразе нет. Причины разные — мало
    показов, автотаргетинг, отключённые показы, — а следствие одно: назвать
    ставку не из чего. Это и есть нижняя граница `BID-01`: фраза без данных
    не получает ставки, выдуманной за неё."""
    if network:
        rows = ((record.get("Network") or {}).get("Coverage") or {}) \
            .get("CoverageItems") or []
        pairs = [(float(one.get("Probability") or 0), one.get("Bid"))
                 for one in rows if one.get("Bid") is not None]
    else:
        rows = ((record.get("Search") or {}).get("AuctionBids") or {}) \
            .get("AuctionBidItems") or []
        pairs = [(float(one.get("TrafficVolume") or 0), one.get("Bid"))
                 for one in rows if one.get("Bid") is not None]
    fit = sorted((volume, bid) for volume, bid in pairs if volume >= target)
    return fit[0][1] if fit else None


def least_bid(record, *, network: bool):
    """Наименьшая ставка из торгов: ниже неё показов не будет вовсе.

    Это первая граница `BID-01` — видимость. У метода `setAuto` она не
    выражается никак, а посчитать её из уже прочитанных торгов можно: самая
    низкая строка таблицы и есть та ставка, ниже которой объём трафика равен
    нулю. `None` означает, что торгов нет и сравнивать не с чем."""
    if network:
        rows = ((record.get("Network") or {}).get("Coverage") or {}) \
            .get("CoverageItems") or []
    else:
        rows = ((record.get("Search") or {}).get("AuctionBids") or {}) \
            .get("AuctionBidItems") or []
    bids_seen = [one.get("Bid") for one in rows if one.get("Bid") is not None]
    return min(bids_seen) if bids_seen else None


def wanted_bid(base: int, *, increase: int, ceiling=None) -> int:
    """Ставка по формуле Директа: объём трафика, надбавка, потолок.

    Формула та же, что у `KeywordBids.setAuto`: «ставка, соответствующая
    объёму трафика, × (1 + надбавка / 100), но не более потолка». Потолок —
    третья граница `BID-01`, и поля в API у неё нет: это внешний параметр,
    который скилл держит у себя и применяет до записи.

    Округление вниз до копейки: Директ хранит деньги целыми микро с шагом
    10 000, и ставка, поднятая округлением выше потолка, нарушила бы ровно то
    ограничение, ради которого потолок и назван."""
    value = int(base * (100 + increase) / 100)
    if ceiling is not None:
        value = min(value, ceiling)
    return value - value % BID_STEP


# --------------------------------------------------------------------------
# Операции конвейера
# --------------------------------------------------------------------------

def bids_batch() -> int:
    """Сколько фраз уходит в `Bids.set` за вызов — из справочника лимитов.

    Предел у ставок зависит от вида объекта и лежит не в разделе `batch`, а в
    `batch_special`: кампаний десять, групп тысяча, фраз десять тысяч. Скилл
    пишет только по фразам, и берётся их число.

    Оно же ограничивает и чтение: `Bids.get` принимает не более 10 000
    `KeywordIds` и отдаёт не более 10 000 объектов. Одно число на две границы —
    не совпадение, а причина, по которой задача делится на операции именно
    так: снимок операции читается одним запросом."""
    rule = (Limits.load().data.get("batch_special") or {}).get("Bids.set") or {}
    limit = rule.get("keywords")
    if not isinstance(limit, int) or isinstance(limit, bool) or limit < 1:
        raise DirectFailure(
            "В справочнике лимитов нет размера пакета `Bids.set` по фразам. "
            "Без него задача либо уходила бы по одному элементу, либо "
            "превышала предел выборки при перечитывании."
        )
    return limit


def bid_operations(values: dict, *, auto_search=None,
                   autotargetings=(), guard) -> list:
    """Простановка ставок: `Bids.set`, по фразам.

    `values` — `{идентификатор: {"Bid": микро, "ContextBid": микро}}`. Пустой
    словарь значений у фразы не допускается: запись без изменения прошла бы
    конвейер целиком и отчиталась успехом, ничего не сделав.

    `guard` обязателен и умолчания не имеет. Это условие, которое конвейер
    проверяет по свежему чтению перед самой записью, и здесь им проверяется
    стратегия: в ответе `Bids.get` её нет, сторож окна её смены не видит, и
    вызов без условия писал бы ставку на стратегию, которая её игнорирует или
    сбрасывает обучение. Умолчание превратило бы забытый аргумент в рабочий
    вызов.

    `autotargetings` — какие из объектов автотаргетинги. Подпись в
    предпросмотре берётся отсюда, а не из прочитанного `Bids.get`: поля
    `Keyword` он не отдаёт вовсе, и признак по его ответу не срабатывает
    никогда. Подпись у предпросмотра и у показанных рядом сумм обязана быть
    одна: разошедшись, они называют один объект двумя словами, и человек
    выбирает, какому верить."""
    apart = set(autotargetings)
    if not values:
        raise DirectFailure("Не сказано, каким фразам назначать ставки.")
    # `AutotargetingSearchBidIsAuto` — поле автотаргетинга, и обычной фразе оно
    # не принадлежит. Разосланное всем, оно даёт ровно тот исход, от которого
    # сторожат остальные проверки: элемент автотаргетинга применяется, элементы
    # фраз отвергаются или молча игнорируются, и задача остаётся исполненной
    # наполовину — а узнаётся об этом только перечитыванием.
    if auto_search is not None and not apart & set(values):
        raise DirectFailure(
            "Автоматическая ставка на поиске — признак автотаргетинга, а среди "
            "выбранных объектов его нет. Обычной фразе признак не достанется, "
            "и записалась бы половина просьбы: ставки без признака, о котором "
            "просили. Назовите группу с автотаргетингом или сам автотаргетинг."
        )
    limit = bids_batch()
    named = sorted(values)
    found = []
    for start in range(0, len(named), limit):
        items, changes = [], []
        for identifier in named[start:start + limit]:
            item = {"KeywordId": int(identifier)}
            for field in ("Bid", "ContextBid"):
                if values[identifier].get(field) is not None:
                    item[field] = int(values[identifier][field])
            if auto_search is not None and identifier in apart:
                item["AutotargetingSearchBidIsAuto"] = auto_search
            if len(item) == 1:
                # Обычная фраза, которой в этом вызове писать нечего, просто
                # пропускается: признак ей не полагается, а ставки не назвали.
                # Отказ здесь останавливал бы законную просьбу «признак
                # автотаргетингу, ставки — никому».
                if auto_search is not None and identifier not in apart:
                    continue
                raise DirectFailure(
                    f"Фраза {identifier}: не названо ни одной ставки. Запись "
                    f"без значения прошла бы конвейер целиком и отчиталась "
                    f"успехом, ничего не сделав."
                )
            items.append(item)
            changes += [
                policies.Change(object_id=int(identifier), what=what,
                                field=field, after=item[field],
                                service=("автотаргетинг" if identifier in apart
                                         else "фраза"))
                for field, what in (("Bid", "ставка на поиске"),
                                    ("ContextBid", "ставка в сетях"),
                                    ("AutotargetingSearchBidIsAuto",
                                     "автоматическая ставка"))
                if field in item
            ]
        # Пачка, из которой все объекты выпали пропуском, операцией не
        # становится: пустой `Bids.set` — запрос ни о чём, а конвейеру он
        # приходит задачей, которую нечем ни показать, ни сверить.
        if not items:
            continue
        found.append(Operation(
            BIDS, "set", params_key="Bids", items=items, changes=changes,
            read=bids_read(), id_field="KeywordId", read_key="KeywordIds",
            batch_limit=limit, steps={"Bid": BID_STEP, "ContextBid": BID_STEP},
            guard=guard,
        ))
    return found


def modifier_read() -> dict:
    """Параметры `BidModifiers.get`. `Levels` обязателен и берётся полным.

    Полный набор уровней — не перестраховка: сузив его, перечитывание после
    удаления не нашло бы уцелевшую корректировку и объявило бы её удалённой."""
    params = {
        "SelectionCriteria": {"Levels": list(LEVELS)},
        "FieldNames": list(MODIFIER_FIELDS),
    }
    for name, fields in ADJUSTMENT_FIELDS.items():
        params[f"{name}FieldNames"] = list(fields)
    return params


MODIFIER_RANGES = {
    "mobile": "mobile",
    "tablet": "desktop_tablet_smarttv",
    "desktop": "desktop_tablet_smarttv",
    "desktop-only": "desktop_tablet_smarttv",
    "demographics": "demographics",
    "retargeting": "retargeting",
    "region": "regional",
    "serp": "serp_layout_income_grade_adgroup",
    "income": "serp_layout_income_grade_adgroup",
    "adgroup": "serp_layout_income_grade_adgroup",
    "video": "video_extension",
    "smart": "smart_banner",
}


CAMPAIGN_ONLY = {"region"}


def media_guard(client, account, accounts, campaign):
    """Условие: кампания не медийная — у тех корректировки только у группы.

    Тип кампании в ответе `BidModifiers.get` не приходит, и сторож окна его
    смены не увидел бы; читает его условие само. Скилл медийных кампаний не
    создаёт, но управлять существующими человеку никто не мешает, а Директ
    отвечает на такую запись отказом за баллы."""
    def check(_known) -> list:
        # Запрашивается ровно тип: типовые структуры тут не нужны, а лишние
        # поля стоят баллов на каждой проверке.
        request = {"FieldNames": ["Id", "Type"],
                   "SelectionCriteria": {"Ids": [int(campaign)]}}
        need = Limits.load().units_cost(CAMPAIGNS, "get", 1)
        found = list(client.get_all(CAMPAIGNS, request, account=account,
                                    use_operator_units=lambda: (
                                        accounts.use_operator_units(
                                            account, need=need))))
        try:
            phrases.all_named([one.get("Id") for one in found], [campaign],
                              "кампании")
            phrases.campaign_here(client, account, accounts, [campaign])
        except DirectFailure as failure:
            return [str(failure)]
        for item in found:
            if item.get("Type") in phrases.MEDIA_CAMPAIGNS:
                return [
                    f"Кампания {campaign} медийная ({item.get('Type')}), а в "
                    f"медийных кампаниях корректировки задаются только на "
                    f"уровне группы (`API_OBJECTS.md`, раздел 9). Назовите "
                    f"`--group`."
                ]
        return []
    return check


def neighbours_guard(client, account, accounts, kind, body, *,
                     campaign=None, group=None):
    """Условие: соседи объекта позволяют эту корректировку — по свежему чтению.

    Соседей проверяет и команда, до сборки задачи, но свойством это становится
    здесь. Сосед появляется и меняется независимо, а сторож окна сличает
    **запрашиваемые** объекты: у создания их ещё нет вовсе, а у правки
    изменившийся сосед — не изменение того, что пишут. Запрещённая пара нулей
    и несовместимая пара так и собирались бы уже после подтверждения."""
    def check(_known) -> list:
        try:
            fits_neighbours(kind, body, read_modifiers(
                client, account, accounts, campaign=campaign, group=group))
        except DirectFailure as failure:
            return [str(failure)]
        return []
    return check


def final_state_guard(client, account, accounts, changing, value):
    """Условие: итоговое состояние правок допустимо — по свежему чтению."""
    def check(_known) -> list:
        try:
            fits_final_state(client, account, accounts, changing, value)
        except DirectFailure as failure:
            return [str(failure)]
        return []
    return check


def fits_level(kind: str, *, campaign) -> None:
    """Отказ, если корректировку задают уровню, который её не принимает.

    Проверяется до вопроса человеку: иначе он подтверждает правку, про
    которую уже известно, что Директ её отвергнет, — и узнаёт об этом за
    баллы."""
    if kind in CAMPAIGN_ONLY and campaign is None:
        raise DirectFailure(
            f"Корректировка «{kind}» задаётся только на уровне кампании — так "
            f"сказано в справочнике (`API_OBJECTS.md`, раздел 9). Назовите "
            f"`--campaign`, а не `--group`."
        )


def fits_modifier(kind: str, body: dict) -> None:
    """Что не так с коэффициентом — по справочнику и до вопроса человеку.

    Диапазоны у типов разные, и общий «ноль до тысячи трёхсот» неверен для
    половины: региону меньше десяти нельзя, смарт-баннеру меньше двадцати,
    видеодополнению меньше пятидесяти. Проверка стоит до сборки операции, а
    не после отказа Директа: иначе человек подтверждает правку, которая уже
    заведомо не пройдёт, и узнаёт об этом за баллы.

    Ноль тут не «снять корректировку», а «минус сто процентов» — отдельная
    операция, и правило `BID-06` разрешает её только точечно. Снимает
    корректировку удаление."""
    name = MODIFIER_RANGES.get(kind)
    ranges = (Limits.load().data.get("bid_modifiers") or {}).get(
        "value_ranges_percent") or {}
    rule = ranges.get(name) if name else None
    if not isinstance(rule, dict) or "min" not in rule or "max" not in rule:
        raise DirectFailure(
            f"В справочнике лимитов нет диапазона коэффициента для "
            f"корректировки «{excerpt(kind, 32)}». Без него запись шла бы "
            f"вслепую, а отказ приходил бы от Директа уже за баллы."
        )
    value = body.get("BidModifier")
    if not isinstance(value, int) or isinstance(value, bool):
        raise DirectFailure(
            f"Коэффициент корректировки «{kind}» — не целое число: "
            f"{excerpt(value, 32)}."
        )
    if not rule["min"] <= value <= rule["max"]:
        raise DirectFailure(
            f"Коэффициент {value} вне диапазона корректировки «{kind}»: "
            f"допустимо от {rule['min']} до {rule['max']} процентов. "
            f"Директ откажет, и отказ уронит весь пакет целиком."
        )


# Какой счётчик справочника отвечает за какой тип корректировки. Считаются не
# все типы: у остальных предела в справочнике нет, и выдумывать его нельзя.
MODIFIER_COUNTS = {
    "MOBILE_ADJUSTMENT": "mobile_per_campaign_or_adgroup",
    "VIDEO_ADJUSTMENT": "video_extension_per_campaign_or_adgroup",
    "DEMOGRAPHICS_ADJUSTMENT": "demographics_per_campaign_or_adgroup",
    "RETARGETING_ADJUSTMENT": "retargeting_per_campaign_or_adgroup",
}


def fits_neighbours(kind: str, body: dict, existing, *, updating=None) -> None:
    """Что мешает поставить эту корректировку рядом с уже стоящими у объекта.

    Три правила справочника, и все три — про **пару**, а не про один объект:
    сколько корректировок типа уже есть, с какими типами он несовместим и
    какие пары нельзя одновременно обнулить. Проверить их по одному
    отправляемому элементу нельзя по построению — нужен сосед, — поэтому
    корректировки объекта читаются до сборки операции.

    Чтение не бесплатно, и это осознанный размен: один вызов `get` против
    подтверждённой человеком правки, о которой уже известно, что Директ её
    отвергнет. Отказ приходит после подтверждения и стоит баллов, а согласие
    даётся на то, чего не случится.

    `updating` — идентификатор правимой корректировки, если она уже есть. Тогда
    проверяется **итоговое состояние**, а не добавление: счёт и
    несовместимость правкой коэффициента не меняются — объект тот же и тип
    тот же, — а вот запрет обнулять обе корректировки пары нарушается именно
    правкой. Себя же в соседях считать нельзя: иначе правка коэффициента
    объявляется вторым объектом того же типа и отвергается на ровном месте."""
    rules = Limits.load().data.get("bid_modifiers") or {}
    existing = {number: record for number, record in existing.items()
                if updating is None or int(number) != int(updating)}
    kinds = {SINGLE_ADJUSTMENTS[kind][1] if kind in SINGLE_ADJUSTMENTS
             else MANY_ADJUSTMENTS[kind][2]}
    mine = next(iter(kinds))
    theirs = [one.get("Type") for one in existing.values()]

    # Тот же срез второй раз — это повторное добавление той же корректировки,
    # и Директ отвечает на него кодом 9801 (`API_OBJECTS.md`, раздел 9). По
    # одному типу этого не видно: у корректировок, которых бывает несколько,
    # тип у всех общий, а различает их именно срез. Сравнивается он целиком —
    # у демографии срез это пара «пол и возраст», и совпадение по одной
    # половине повтором не является.
    aimed = {name: value for name, value in body.items()
             if name != "BidModifier"}
    if updating is None and aimed:
        for number, record in sorted(existing.items()):
            if record.get("Type") != mine:
                continue
            theirs_body = record.get(READ_BY_TYPE.get(mine, "")) or {}
            same = {name: value for name, value in theirs_body.items()
                    if name != "BidModifier"}
            if same == aimed:
                raise DirectFailure(
                    f"Корректировка {mine} с таким же срезом у объекта уже "
                    f"есть — это {number}. Повторное добавление той же "
                    f"корректировки Директ отвергает кодом 9801; коэффициент "
                    f"у существующей меняет `modifier set --modifier "
                    f"{number}`."
                )

    limit = (rules.get("counts") or {}).get(MODIFIER_COUNTS.get(mine, ""))
    if updating is None and isinstance(limit, int) and not isinstance(limit, bool):
        already = sum(1 for one in theirs if one == mine)
        if already >= limit:
            raise DirectFailure(
                f"Корректировок типа {mine} у объекта уже {already} при "
                f"пределе {limit}. Директ откажет, а согласие дали бы на то, "
                f"чего не случится."
            )

    for pair in (rules.get("incompatible") or []) if updating is None else []:
        if mine in pair:
            other = [one for one in pair if one != mine]
            clash = sorted(set(theirs) & set(other))
            if clash:
                raise DirectFailure(
                    f"Корректировка {mine} несовместима с уже стоящей "
                    f"{', '.join(clash)}: справочник называет эту пару "
                    f"взаимоисключающей."
                )

    # Обнулять обе корректировки пары нельзя. Правило справочника оговорено:
    # оно про корректировку на мобильных **без** операционной системы, а с
    # заданной запрет не документирован — и распространять его туда значило бы
    # запрещать по догадке.
    zeros = rules.get("forbidden_zero_pairs") or {}
    if body.get("BidModifier") == 0 and not body.get("OperatingSystemType"):
        for pair in zeros.get("pairs") or []:
            if mine not in pair:
                continue
            for other in (one for one in pair if one != mine):
                for record in existing.values():
                    if record.get("Type") != other:
                        continue
                    found = record.get(READ_BY_TYPE.get(other, "")) or {}
                    if found.get("BidModifier") == 0 and not found.get(
                            "OperatingSystemType"):
                        raise DirectFailure(
                            f"{mine} и {other} нельзя одновременно обнулить: "
                            f"у объекта уже стоит {other} с коэффициентом 0, и "
                            f"вторая такая же отключила бы показы совсем."
                        )


def modifier_add_operation(*, campaign=None, group=None, kind: str,
                           body: dict, guard) -> Operation:
    """Создание одной корректировки: `BidModifiers.add`.

    По одной за элемент, а не пачкой: ответ `add` отдаёт массив `Ids`, и один
    элемент запроса может завести несколько корректировок сразу. Сопоставить
    их с отправленным по позиции нечем, а перечитывать и сверять конвейер
    обязан каждую.

    Поэтому здесь `search=None`: конвейер скажет «идентификатора Директ не
    прислал, а чем искать созданное, операция не назвала — проверьте кабинет».
    Ответ неполный, но честный, и лучшего у этого сервиса нет."""
    if (campaign is None) == (group is None):
        raise DirectFailure(
            "Корректировка задаётся кампании либо группе — ровно одному из "
            "двух. Уровень задаёт и её смысл: корректировка по региону "
            "существует только у кампании, а в медийных кампаниях "
            "корректировки бывают только у группы."
        )
    where = "CampaignId" if campaign is not None else "AdGroupId"
    said, whose = (("кампания", "кампании") if campaign is not None
                   else ("группа", "группы"))
    owner = int(campaign if campaign is not None else group)
    single = kind in SINGLE_ADJUSTMENTS
    write = SINGLE_ADJUSTMENTS[kind][0] if single else MANY_ADJUSTMENTS[kind][0]
    read = SINGLE_ADJUSTMENTS[kind][0] if single else MANY_ADJUSTMENTS[kind][1]
    fits_level(kind, campaign=campaign)
    fits_modifier(kind, body)
    item = {where: owner, write: body if single else [body]}
    label = f"{kind} у {whose} {owner}"
    changes = [
        policies.Change(object_id=label, what=f"{said}, которой она задана",
                        field=where, after=owner, service="корректировка"),
        policies.Change(object_id=label, what=f"корректировка «{kind}»",
                        field=write, after=item[write], service="корректировка"),
    ]
    # Владелец входит в ожидаемое состояние наравне с коэффициентом. Без него
    # сверка после записи подтверждает только величину: корректировка с тем же
    # коэффициентом, но привязанная не к той кампании или группе, прошла бы
    # как записанная правильно — а человеку в предпросмотре показали именно
    # объект привязки.
    return Operation(
        MODIFIERS, "add", params_key="BidModifiers", items=[item],
        labels=[label], changes=changes, expect=[{where: owner, read: body}],
        read=modifier_read(), derived=() if single else ((write, read),),
        search=None, guard=guard,
    )


def modifier_set_operation(record: dict, value: int, *, guard) -> Operation:
    """Правка коэффициента: `BidModifiers.set`.

    Формы записи и чтения расходятся у самого корня: запрос несёт плоский
    `BidModifier`, а `BidModifiers.get` возвращает его внутри структуры своего
    типа. Общего пути между ними нет вовсе, поэтому расхождение объявляется
    парой имён — «что пишем, что читаем»."""
    kind = record.get("Type")
    read = READ_BY_TYPE.get(kind)
    if read is None:
        raise DirectFailure(
            f"Корректировка {record.get('Id')} имеет тип «{excerpt(kind, 40)}», "
            f"которого скилл не знает. Перечитать её тем же именем нельзя, а "
            f"запись без сверки конвейером не является."
        )
    identifier = int(record["Id"])
    # Те же правила справочника, что и при создании: диапазон коэффициента и
    # запрет обнулять обе корректировки пары. Второе проверяется по итоговому
    # состоянию — обнулить пару можно и правкой, — и правило, поставленное на
    # один путь записи из двух, обходится тем, где его забыли. Правило, поставленное на один путь
    # из двух, — не половина защиты, а её отсутствие: обходится оно тем путём,
    # где его забыли. Тип здесь известен из прочитанного объекта, а имя
    # аргумента — из обратной таблицы.
    fits_modifier(KIND_BY_TYPE[kind], {"BidModifier": int(value)})
    item = {"Id": identifier, "BidModifier": int(value)}
    changes = [policies.Change(
        object_id=identifier, what=f"коэффициент «{kind}»", field="BidModifier",
        after=int(value), service="корректировка")]
    # Имя чтения называется целиком, до самого поля, а не до его структуры.
    # Разница видна человеку: по короткому имени прежним значением оказалась бы
    # вся структура — `{'BidModifier': 120, 'OperatingSystemType': None}`, — и
    # строка «было → станет» сравнивала бы объект с числом.
    return Operation(
        MODIFIERS, "set", params_key="BidModifiers", items=[item],
        changes=changes, expect=[{"Id": identifier,
                                  read: {"BidModifier": int(value)}}],
        read=modifier_read(), derived=(("BidModifier", f"{read}.BidModifier"),),
        guard=guard,
    )


def modifier_delete_operation(ids) -> Operation:
    """Снятие корректировок. Разрушающее: уходит по одному элементу за вызов.

    Список из одних «задать» уровень корректировок не покрывает: снять
    поставленное можно только удалением — значения «как было» у коэффициента
    нет, а ноль означает не «снято», а «минус сто процентов»."""
    ids = [int(one) for one in ids]
    if not ids:
        raise DirectFailure("Снятие корректировок: корректировки не названы.")
    changes = [policies.Change(object_id=one, what="снятие корректировки",
                               service="корректировка") for one in ids]
    return Operation(
        MODIFIERS, "delete", selection="Ids", items=[{"Id": one} for one in ids],
        changes=changes, read=modifier_read(), expect_gone=True,
        read_required=("Levels",),
    )


# --------------------------------------------------------------------------
# Команды
# --------------------------------------------------------------------------

def show(client, account, accounts, args) -> int:
    """Что в аукционе: объёмы трафика, ставки и списываемые цены.

    Показывается **до** того, как назовут ставку. Ставка, названная до чтения
    торгов, — угадывание (`BID-01`)."""
    criteria = criteria_from(args)
    records = read_auction(client, account, accounts, criteria,
                           network=args.network)
    # Показу правило нужно не меньше, чем записи: торги, из которых молча
    # выпала названная фраза, человек прочтёт как полную картину аукциона — и
    # назначит по ней ставку.
    all_named(records, args.keyword, "фразы")
    currency = currency_of(accounts, account)
    if args.json:
        say(json.dumps(records, ensure_ascii=False))
        return 0
    lines = []
    for identifier, record in sorted(records.items()):
        side = (record.get("Network") if args.network
                else record.get("Search")) or {}
        lines.append(
            f"фраза {identifier} · ставка {format_api(side.get('Bid') or 0, currency)}"
            f" · показы: {record.get('ServingStatus')}"
        )
        rows = ((side.get("Coverage") or {}).get("CoverageItems") if args.network
                else (side.get("AuctionBids") or {}).get("AuctionBidItems")) or []
        for row in sorted(rows, key=lambda one: -float(
                one.get("Probability" if args.network else "TrafficVolume") or 0)):
            volume = row.get("Probability" if args.network else "TrafficVolume")
            price = row.get("Price")
            lines.append(
                f"    {volume}% → ставка {format_api(row.get('Bid') or 0, currency)}"
                + (f", цена {format_api(price, currency)}" if price is not None
                   else "")
            )
        if not rows:
            lines.append("    торгов нет: мало показов, автотаргетинг или "
                         "показы отключены")
    cache_module.outline(lines or ["Ставок не нашлось."])
    return 0


def set_task(client, account, accounts, args) -> tuple:
    """Ручные ставки: значение называет человек, стратегия проверяется до записи."""
    criteria = criteria_from(args)
    records = read_bids(client, account, accounts, criteria)
    if not records:
        raise DirectFailure("Под отбор не попало ни одной фразы.")
    all_named(records, args.keyword, "фразы")
    fits_strategy(records, strategies_of(
        client, account, accounts,
        {one.get("CampaignId") for one in records.values()}),
        search=args.search is not None, network=args.network_bid is not None)
    currency = currency_of(accounts, account)
    search = None if args.search is None else money(args.search)
    network = None if args.network_bid is None else money(args.network_bid)
    # Справочник валют читается только когда ставку и правда называют:
    # `--auto-search-bid` меняет признак, а не число, и границы ему ни к чему.
    if search is not None or network is not None:
        fits_bid((search, network), bid_bounds(client, account, accounts),
                 currency)
    values = {identifier: {"Bid": search, "ContextBid": network}
              for identifier in records}
    named = autotargeting_among(client, account, accounts, values)
    return (f"ставки: объектов {len(values)}",
            bid_operations(values, auto_search=args.auto_search,
                           autotargetings=named,
                           guard=strategy_guard(
                               client, account, accounts,
                               search=args.search is not None,
                               network=args.network_bid is not None)),
            money_lines(values, currency, named))


def fits_target(traffic: int, increase: int, *, network: bool) -> None:
    """Границы желаемого объёма и надбавки — из справочника метода `setAuto`.

    Нижняя граница у поиска и у сетей **разная**: объём трафика задаётся от 5
    процентов, доля аудитории — от 1 (`BID-02`). Одна граница на оба случая
    отвергала бы законные значения, причём отказом команды, а не Директа: со
    стороны это выглядит как ограничение API, которого нет.

    Проверяется здесь, а не отдаётся Директу, по единственной причине: это не
    стоит ни одного запроса. Всё, за что надо платить чтением, спрашивает сам
    Директ."""
    least = 1 if network else 5
    if not least <= traffic <= 100:
        what = ("Желаемая доля аудитории в сетях" if network
                else "Желаемый объём трафика на поиске")
        raise DirectFailure(
            f"{what} задаётся в процентах от {least} до 100, а названо "
            f"{excerpt(traffic, 16)}."
        )
    if not 0 <= increase <= 1000:
        raise DirectFailure(
            f"Надбавка задаётся в процентах от 0 до 1000, а названо "
            f"{excerpt(increase, 16)}."
        )


def currency_of(accounts, account: str) -> str:
    """Валюта кабинета. Берётся у перечня кабинетов, а он читает её у клиента."""
    cabinet = accounts.by_login(account)
    return cabinet.currency if cabinet else ""


def money_lines(values: dict, currency: str, autotargetings=()) -> list:
    """Ставки в валюте кабинета — для показа человеку.

    Предпросмотр конвейера печатает то, что уходит в запрос, а уходят туда
    целые микро: `94270000`. Человеку это читается как девяносто четыре
    миллиона, и подтверждать деньги в таком виде нельзя. Движок формата денег
    не знает намеренно — он не знает и того, что перед ним деньги, — поэтому
    перевод делает команда, рядом с вопросом.

    Автотаргетинг называется автотаргетингом. Ставку ему назначают намеренно —
    правило `STR-03` только этим его и зануляет, — но попасть под отбор по
    группе он может и заодно, и тогда человек подтверждает ставку объекту, о
    котором не думал. В списке фраз он от них ничем не отличается."""
    said = []
    for identifier in sorted(values):
        what_object = ("автотаргетинг" if identifier in set(autotargetings)
                       else "фраза")
        for field, where in (("Bid", "на поиске"), ("ContextBid", "в сетях")):
            value = values[identifier].get(field)
            if value is not None:
                said.append(f"{what_object} {identifier}: ставка {where} "
                            f"{format_api(value, currency)}")
    return said


def bid_bounds(client, account, accounts) -> tuple:
    """Наименьшая и наибольшая ставка в валюте кабинета: справочник `Currencies`.

    Шага у ставки нет. `BidIncrement` в том же справочнике есть (100 000 микро
    у рубля), но хранению он не соответствует: замер 30.08.2026 записал
    12 350 000 — не кратные ему 12,35 ₽ — и перечитал их без изменения.
    Проверка на кратность отвергала бы законную ставку."""
    return currency_bounds(client, account, accounts,
                           "MinimumBid", "MaximumBid")


def currency_bounds(client, account, accounts, *names) -> tuple:
    """Денежные границы валюты кабинета по именам из справочника `Currencies`.

    В `limits.json` этих чисел нет намеренно: они зависят от валюты, и файл
    прямо отсылает к `Dictionaries.get` (`currency_bounds_source`). Пересказать
    их числом в коде значило бы завести второе написание, которое устареет
    молча и в свою сторону у каждой валюты.

    Справочник кэшируется на сутки, и решения об оплате ему не нужно: у
    `Dictionaries` правило `shared`.

    Имена называет вызывающий код, а не эта функция: границ в справочнике два
    десятка (`limits.json`, `money.currency_bounds_names`), и своя копия
    выборки у каждого потребителя разошлась бы с остальными."""
    said = currency_of(accounts, account)
    if not said:
        raise DirectFailure(
            "Валюта кабинета неизвестна, а денежные границы заданы в ней. "
            "Без валюты проверка шла бы вслепую, а отказ приходил бы от "
            "Директа уже за баллы."
        )
    rows = client.dictionaries(["Currencies"], account=account)
    for row in rows.get("Currencies") or ():
        if row.get("Currency") != said:
            continue
        named = {one.get("Name"): one.get("Value")
                 for one in row.get("Properties") or ()}
        found = [named.get(one) for one in names]
        absent = [one for one, value in zip(names, found) if value is None]
        if absent:
            raise DirectFailure(
                f"В справочнике валют у «{excerpt(said, 16)}» нет "
                f"{', '.join(f'`{one}`' for one in absent)} — границ, по "
                f"которым значение проверяют до отправки."
            )
        return tuple(int(one) for one in found)
    raise DirectFailure(
        f"Валюты кабинета «{excerpt(said, 16)}» нет в справочнике "
        f"`Currencies`. Границы взять неоткуда."
    )


def fits_bid(values, bounds: tuple, currency: str, *, said="ставка") -> None:
    """Все ставки — внутри границ валюты, иначе отказ до отправки.

    Отказ Директа поэлементный: пакет со ставкой вне границ применился бы
    частью, а частью нет. Проверяется весь набор сразу и называется целиком —
    поштучный отказ заставлял бы человека выяснять границы по одной фразе за
    круг."""
    least, most = bounds
    outside = sorted(one for one in values
                     if one is not None and not least <= one <= most)
    if not outside:
        return
    raise DirectFailure(
        f"{said.capitalize()} вне границ валюты: "
        + ", ".join(format_api(one, currency) for one in outside[:5])
        + (f" и ещё {len(outside) - 5}" if len(outside) > 5 else "")
        + f". Директ принимает от {format_api(least, currency)} до "
          f"{format_api(most, currency)} (справочник `Currencies`, "
          f"`MinimumBid` и `MaximumBid`)."
    )


def money(amount) -> int:
    try:
        return to_api(amount)
    except MoneyError as failure:
        raise DirectFailure(
            f"Ставка {excerpt(amount, 32)} не годится: {failure}."
        ) from None


def auto_task(client, account, accounts, args) -> tuple:
    """Ставки по желаемому объёму трафика — той же формулой, что у `setAuto`."""
    criteria = criteria_from(args)
    records = read_bids(client, account, accounts, criteria)
    if not records:
        raise DirectFailure("Под отбор не попало ни одной фразы.")
    all_named(records, args.keyword, "фразы")
    fits_strategy(records, strategies_of(
        client, account, accounts,
        {one.get("CampaignId") for one in records.values()}),
        search=not args.network, network=args.network)
    auction = read_auction(client, account, accounts, criteria,
                           network=args.network)
    ceiling = None if args.ceiling is None else money(args.ceiling)
    currency = currency_of(accounts, account)
    bounds = bid_bounds(client, account, accounts)
    fits_bid((ceiling,), bounds, currency, said="потолок ставки")
    # Поле не зависит от фразы, а нужно и после цикла: оставленное внутри, оно
    # осталось бы неопределённым, когда торгов не нашлось ни у одной.
    field = "ContextBid" if args.network else "Bid"
    values, quiet, blind = {}, [], []
    for identifier in sorted(records):
        base = auction_bid(auction.get(identifier) or {}, args.traffic,
                           network=args.network)
        if base is None:
            quiet.append(identifier)
            continue
        value = wanted_bid(base, increase=args.increase, ceiling=ceiling)
        # Потолок способен срезать ставку ниже той, при которой показы вообще
        # начинаются: тогда правило `BID-01` соблюдено сверху и нарушено
        # снизу — деньги не тратятся, но и данных фраза не даёт. Молчать об
        # этом нельзя, а решать за человека нечего: потолок назвал он.
        floor = least_bid(auction.get(identifier) or {}, network=args.network)
        if floor is not None and value < floor:
            blind.append((identifier, value, floor))
        if records[identifier].get(field) == value:
            continue
        values[identifier] = {field: value}
    # Считанная по торгам ставка тоже бывает вне границ: у дешёвой фразы
    # формула даёт меньше наименьшей ставки валюты. Отказ Директа поэлементный,
    # и такой пакет применился бы наполовину.
    fits_bid([one[field] for one in values.values()], bounds, currency,
             said="рассчитанная ставка")
    for identifier in quiet:
        warn(f"фраза {identifier}: торгов нет — ставку назвать не из чего. "
             f"Мало показов, автотаргетинг или отключённые показы; выдуманная "
             f"за них ставка была бы угадыванием.")
    for identifier, value, floor in blind:
        warn(f"фраза {identifier}: потолок срезал ставку до "
             f"{format_api(value, currency)}, а показы в торгах начинаются с "
             f"{format_api(floor, currency)} — при такой ставке фраза не "
             f"может не получить показов. Для повышения ставки сверх "
             f"указанного предела нужно согласовать новый предел.")
    if not values:
        raise DirectFailure(
            "Ни одной ставки менять не нужно: у всех прочитанных фраз она уже "
            "равна расчётной либо торгов по ним нет."
        )
    named = autotargeting_among(client, account, accounts, values)
    return (f"расчёт по объёму трафика {args.traffic}%: фраз {len(values)}",
            bid_operations(values, autotargetings=named,
                           guard=strategy_guard(client, account, accounts,
                                                search=not args.network,
                                                network=args.network)),
            money_lines(values, currency, named))


def read_modifiers(client, account, accounts, *, ids=(), campaign=None,
                   group=None) -> dict:
    request = dict(modifier_read())
    criteria = dict(request["SelectionCriteria"])
    if ids:
        criteria["Ids"] = [int(one) for one in ids]
    if campaign is not None:
        criteria["CampaignIds"] = [int(campaign)]
    if group is not None:
        criteria["AdGroupIds"] = [int(group)]
    if len(criteria) == 1:
        raise DirectFailure(
            "Не сказано, какие корректировки читать: назовите `--modifier`, "
            "`--campaign` или `--group`."
        )
    request["SelectionCriteria"] = criteria
    need = Limits.load().units_cost(MODIFIERS, "get",
                                    len(criteria.get("Ids") or []) or None)
    return {item["Id"]: item for item in client.get_all(
        MODIFIERS, request, account=account,
        use_operator_units=lambda: accounts.use_operator_units(account,
                                                               need=need))}


def modifier_list(client, account, accounts, args) -> int:
    found = read_modifiers(client, account, accounts, ids=args.modifier,
                           campaign=args.campaign, group=args.group)
    # Показу правило нужно не меньше, чем записи: перечень, из которого молча
    # выпала названная корректировка, человек прочтёт как полную опись.
    all_named(found, args.modifier, "корректировки")
    if args.json:
        say(json.dumps(list(found.values()), ensure_ascii=False))
        return 0
    lines = []
    for identifier, item in sorted(found.items()):
        body = item.get(READ_BY_TYPE.get(item.get("Type"), "")) or {}
        where = (f"группа {item['AdGroupId']}" if item.get("AdGroupId")
                 else f"кампания {item.get('CampaignId')}")
        extra = ", ".join(f"{name}={body[name]}" for name in sorted(body)
                          if name != "BidModifier")
        lines.append(f"{identifier} · {item.get('Type')} · {where} · "
                     f"коэффициент {body.get('BidModifier')}"
                     + (f" · {extra}" if extra else ""))
    cache_module.outline(lines or ["Корректировок не нашлось."])
    return 0


def modifier_body(args) -> tuple:
    """Тип корректировки и её тело — по единственному названному аргументу."""
    named = [(kind, getattr(args, kind.replace("-", "_")))
             for kind in list(SINGLE_ADJUSTMENTS) + list(MANY_ADJUSTMENTS)
             if getattr(args, kind.replace("-", "_"), None) is not None]
    if len(named) != 1:
        raise DirectFailure(
            "Корректировка задаётся ровно одна за вызов, и названа должна быть "
            "ровно одна: ответ `BidModifiers.add` отдаёт массив "
            "идентификаторов, и сопоставить несколько созданных объектов с "
            "одним отправленным элементом нечем."
        )
    kind, value = named[0]
    # Операционная система бывает только у мобильных и планшетов: у остальных
    # структур такого поля нет вовсе. Названная им, она пропала бы молча, и
    # команда отчиталась бы об исполненной просьбе, исполнив половину.
    if args.os and kind not in ("mobile", "tablet"):
        raise DirectFailure(
            f"Операционную систему принимают только корректировки на "
            f"мобильных и на планшетах, а названа «{kind}»: у её структуры "
            f"такого поля нет."
        )
    if kind in SINGLE_ADJUSTMENTS:
        body = {"BidModifier": int(value)}
        if kind in ("mobile", "tablet") and args.os:
            body["OperatingSystemType"] = args.os
        return kind, body
    name, coefficient = value
    if kind == "demographics":
        body = {"BidModifier": coefficient}
        for part in name.split(":"):
            field = next((one for one, allowed in DEMOGRAPHICS.items()
                          if part in allowed), None)
            if field is None:
                raise DirectFailure(
                    f"«{excerpt(part, 32)}» — не пол и не возраст. Ожидается "
                    f"одно из: "
                    + ", ".join(one for allowed in DEMOGRAPHICS.values()
                                for one in allowed) + "."
                )
            # Срез — это один пол и один возраст. Второй, записанный поверх
            # первого, дал бы корректировку не тому срезу, о котором просили,
            # и команда отчиталась бы об исполненной просьбе.
            if field in body:
                raise DirectFailure(
                    f"В срезе «{excerpt(name, 48)}» дважды названо одно и то "
                    f"же: {body[field]} и {part}. Срез — это один пол и один "
                    f"возраст; второе значение молча вытеснило бы первое."
                )
            body[field] = part
        return kind, body
    return kind, {**selector(kind, name), "BidModifier": coefficient}


def selector(kind: str, name: str) -> dict:
    """Срез корректировки — полем и значением, сверенным с областью значений.

    Директ отвергает негодный срез кодом за баллы, а нечисловой
    идентификатор до него и не доходил бы: `int("абв")` роняет команду
    трассировкой, из которой человеку неоткуда узнать, что он ошибся в срезе."""
    if kind in SELECTOR_NAMES:
        field, allowed = SELECTOR_NAMES[kind]
        if name not in allowed:
            raise DirectFailure(
                f"Срез «{excerpt(name, 32)}» у корректировки «{kind}» "
                f"неизвестен. Ожидается одно из: {', '.join(allowed)}."
            )
        return {field: name}
    field, said = SELECTOR_IDS[kind]
    if not name.isdigit() or int(name) <= 0:
        raise DirectFailure(
            f"Срез «{excerpt(name, 32)}» у корректировки «{kind}» — не "
            f"идентификатор {said}. Ожидается целое положительное число."
        )
    return {field: int(name)}


def owner_of(record: dict) -> tuple:
    """Кому принадлежит корректировка: `(кампания, группа)`, ровно одно из двух.

    `BidModifiers.get` отдаёт `CampaignId` и у групповой корректировки — там
    лежит кампания, которой принадлежит группа. Различает их `AdGroupId`."""
    group = record.get("AdGroupId")
    return (None, group) if group else (record.get("CampaignId"), None)


def fits_final_state(client, account, accounts, changing: dict, value: int) -> None:
    """Проверить правки как **одно итоговое состояние**, а не поодиночке.

    Правки уходят по очереди, а правила про пару смотрят на соседей. Проверив
    каждую по исходному снимку, обе увидят соседа ненулевым — и обе пройдут; а
    второй запрос встретит запрещённую пару уже после того, как первый
    применился. Поэтому снимок сперва достраивается всеми запрошенными
    правками, и каждая проверяется против него.

    Тело корректировки берётся **из прочитанного**, а не собирается заново:
    вместе с коэффициентом там живёт операционная система, а запрет обнулять
    пару её как раз исключает. Собранное заново тело теряло бы её и отвергало
    законную правку."""
    owners = {}
    for record in changing.values():
        key = owner_of(record)
        if key not in owners:
            owners[key] = read_modifiers(client, account, accounts,
                                         campaign=key[0], group=key[1])
    final = {}
    for key, existing in owners.items():
        state = {number: dict(record) for number, record in existing.items()}
        for number in changing:
            if number not in state:
                continue
            read = READ_BY_TYPE.get(state[number].get("Type"))
            if read is None:
                continue
            state[number][read] = dict(state[number].get(read) or {},
                                       BidModifier=int(value))
        final[key] = state
    for number, record in sorted(changing.items()):
        key = owner_of(record)
        state = final[key]
        read = READ_BY_TYPE.get(record.get("Type"))
        if read is None or number not in state:
            continue
        fits_neighbours(KIND_BY_TYPE[record["Type"]], state[number][read],
                        state, updating=number)


def modifier_task(client, account, accounts, args) -> tuple:
    if args.action == "add":
        kind, body = modifier_body(args)
        # Соседи читаются до сборки: правила счёта, несовместимости и запрета
        # на пару нулей — про пару, и по одному отправляемому элементу они не
        # проверяются по построению.
        fits_neighbours(kind, body, read_modifiers(
            client, account, accounts, campaign=args.campaign,
            group=args.group))
        return (f"корректировка «{kind}»: коэффициент {body['BidModifier']}",
                [modifier_add_operation(
                    campaign=args.campaign, group=args.group, kind=kind,
                    body=body,
                    # Три условия одним: владелец существует, тип кампании
                    # позволяет, соседи позволяют. Всё это читается условием, а
                    # не командой: в ответе `BidModifiers.get` ни типа
                    # кампании, ни соседей нет, а у создания и запрашиваемого
                    # объекта ещё нет — сторожить перечитыванием нечего.
                    guard=phrases.every(
                        (media_guard(client, account, accounts, args.campaign)
                         if args.campaign is not None else
                         phrases.exists_guard(
                             client, account, accounts, phrases.GROUPS,
                             {"FieldNames": list(phrases.GROUP_FIELDS)},
                             [args.group], "группы")),
                        neighbours_guard(client, account, accounts, kind, body,
                                         campaign=args.campaign,
                                         group=args.group)))])
    found = read_modifiers(client, account, accounts, ids=args.modifier)
    all_named(found, args.modifier, "корректировки")
    if args.action == "delete":
        return (f"снятие корректировок: {len(found)}",
                [modifier_delete_operation(sorted(found))])
    fits_final_state(client, account, accounts, found, args.value)
    # То же условие — и внутри конвейера. Сосед появляется и меняется
    # независимо, а сторож окна сличает **запрашиваемую** корректировку:
    # изменившийся сосед изменением её не выглядит, и запрещённая пара нулей
    # собралась бы уже после подтверждения.
    guard = final_state_guard(client, account, accounts, found, args.value)
    return (f"коэффициенты корректировок: {len(found)}",
            [modifier_set_operation(found[one], args.value, guard=guard)
             for one in sorted(found)])


# --------------------------------------------------------------------------
# Запуск
# --------------------------------------------------------------------------

def report_out(report, args, *, shown=False) -> int:
    if args.json:
        # Перечень полей — у самого отчёта (`Report.machine`), а не здесь.
        # Четыре команды держали четыре копии одного словаря, и новое поле
        # попадало бы в три из четырёх: программа, читающая только `written` и
        # `failed`, приняла бы «записалось вопреки отказу» за обычный отказ и
        # повторила команду.
        say(json.dumps(report.machine(), ensure_ascii=False))
    else:
        lines = report.lines()
        if shown:
            lines = lines[len(report.preview):]
        cache_module.outline(lines, path=report.journal)
    return 0 if report.ok else 1


class Parser(argparse.ArgumentParser):
    def error(self, message: str):
        self.exit(2, redact(f"{self.prog}: {message}\n"))


def add_common(parser, *, leaf: bool) -> None:
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
    parser.add_argument("--json", action="store_true",
                        default=argparse.SUPPRESS if leaf else False,
                        help="машиночитаемый вывод")


def coefficient(text: str):
    """Аргумент вида `AGE_0_17=0`: срез и коэффициент."""
    name, _, value = text.partition("=")
    if not name or not value.strip().lstrip("-").isdigit():
        raise argparse.ArgumentTypeError(
            f"ожидается «СРЕЗ=ЧИСЛО», получено «{excerpt(text, 40)}»")
    return name, int(value)


def build_parser() -> Parser:
    parser = Parser(description="Ставки и корректировки ставок.")
    add_common(parser, leaf=False)
    common = argparse.ArgumentParser(add_help=False)
    add_common(common, leaf=True)
    kinds = parser.add_subparsers(dest="kind", required=True)

    seen = kinds.add_parser("show", parents=[common],
                            help="торги: объёмы трафика и цены")
    _selection(seen)
    seen.add_argument("--network", action="store_true",
                      help="сети вместо поиска")

    manual = kinds.add_parser("set", parents=[common], help="ручные ставки")
    _selection(manual)
    manual.add_argument("--search", help="ставка на поиске, в валюте кабинета")
    manual.add_argument("--network-bid", dest="network_bid",
                        help="ставка в сетях, в валюте кабинета")
    manual.add_argument("--auto-search-bid", dest="auto_search",
                        choices=("YES", "NO"),
                        help="автоматическая ставка автотаргетинга")

    auto = kinds.add_parser("auto", parents=[common],
                            help="расчёт по желаемому объёму трафика")
    _selection(auto)
    auto.add_argument("--traffic", type=int, required=True,
                      help="желаемый объём трафика в процентах, 5–100")
    auto.add_argument("--increase", type=int, default=0,
                      help="надбавка в процентах, 0–1000")
    auto.add_argument("--ceiling", help="потолок ставки, в валюте кабинета")
    auto.add_argument("--network", action="store_true",
                      help="доля аудитории в сетях вместо объёма трафика")

    modifier = kinds.add_parser("modifier", help="корректировки ставок") \
        .add_subparsers(dest="action", required=True)
    listing = modifier.add_parser("list", parents=[common],
                                  help="показать корректировки")
    listing.add_argument("--modifier", action="append", type=int, default=[])
    listing.add_argument("--campaign", type=int)
    listing.add_argument("--group", type=int)

    made = modifier.add_parser("add", parents=[common],
                               help="задать корректировку")
    made.add_argument("--campaign", type=int)
    made.add_argument("--group", type=int)
    for kind in SINGLE_ADJUSTMENTS:
        made.add_argument(f"--{kind}", type=int, metavar="КОЭФФИЦИЕНТ",
                          dest=kind.replace("-", "_"))
    for kind in MANY_ADJUSTMENTS:
        made.add_argument(f"--{kind}", type=coefficient, metavar="СРЕЗ=КОЭФФИЦИЕНТ")
    made.add_argument("--os", choices=("IOS", "ANDROID"),
                      help="операционная система для мобильных и планшетов")

    changed = modifier.add_parser("set", parents=[common],
                                  help="изменить коэффициент")
    changed.add_argument("--modifier", action="append", type=int, required=True)
    changed.add_argument("--value", type=int, required=True)

    removed = modifier.add_parser("delete", parents=[common],
                                  help="снять корректировки")
    removed.add_argument("--modifier", action="append", type=int, required=True)
    return parser


def _selection(step) -> None:
    step.add_argument("--keyword", action="append", type=int, default=[])
    step.add_argument("--group", type=int)
    step.add_argument("--campaign", type=int)


def run(args) -> int:
    client = Client.from_env(profile=args.env, account=args.account, warn=warn)
    accounts = Accounts.load(client, warn=warn)
    account = resolve_account(accounts, client, args.account)

    if args.kind == "show":
        return show(client, account, accounts, args)
    if args.kind == "modifier" and args.action == "list":
        return modifier_list(client, account, accounts, args)

    notes = ()
    if args.kind == "set":
        if (args.search is None and args.network_bid is None
                and args.auto_search is None):
            raise DirectFailure(
                "Не сказано, что назначать: `--search`, `--network-bid` или "
                "`--auto-search-bid`. Запись без значения прошла бы конвейер "
                "целиком и отчиталась успехом, ничего не сделав."
            )
        title, operations, notes = set_task(client, account, accounts, args)
    elif args.kind == "auto":
        fits_target(args.traffic, args.increase, network=args.network)
        title, operations, notes = auto_task(client, account, accounts, args)
    else:
        title, operations = modifier_task(client, account, accounts, args)

    seen = []
    engine = Writer(client, account, accounts=accounts, apply=args.apply,
                    show=showing(seen=seen, quiet=args.json, notes=notes),
                    warn=warn)
    report = engine.run(Task(title, operations))
    return report_out(report, args, shown=bool(seen))


def refused(said: str, args) -> int:
    """Отказ команды: в stderr человеку, объектом — программе.

    Машиночитаемый вывод отвечает объектом и на отказе. Иначе программа,
    читающая stdout, получает на отказе пустоту и падает там, где команда как
    раз всё объяснила. Правило стоит в одном месте на все отказы: расставленное
    по веткам, оно обходится той, где его забыли."""
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
    sys.exit(main())
