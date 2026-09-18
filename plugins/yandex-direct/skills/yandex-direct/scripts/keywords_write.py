#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""Запись фраз, минус-фраз, наборов библиотеки и автотаргетинга."""

from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "lib"))

import cache as cache_module  # noqa: E402
import incoming  # noqa: E402
import phrases  # noqa: E402
import responsive  # noqa: E402
import templates  # noqa: E402
from accounts import Accounts, resolve_account  # noqa: E402
from config import DirectFailure, excerpt, preload_secrets, redact  # noqa: E402
from direct import Client  # noqa: E402
from money import format_api  # noqa: E402
from reports import Reference  # noqa: E402
from writer import showing, CLEAR, Limits, Task, Writer, unrun  # noqa: E402

# Что сказать до вопроса, если задача правит текст фраз. Справочник
# `Keywords.update` называет это в двух врезках, и обе означают, что фраза
# после правки может оказаться не той, которую правили.
REWRITE_NOTE = (
    "Правка текста фразы не всегда меняет фразу на месте. Директ заводит новую "
    "фразу с новым идентификатором, если из текста убрали слово, сняли "
    "минус-слово или тронули операторы «!» и «+»; статистика остаётся на "
    "прежней, а показы по ней прекращаются. Если правка сделала фразу "
    "дубликатом соседней, прежняя удаляется вовсе. Конвейер увидит это как "
    "«после записи объект не прочитался» и остановит пакет."
)

# Столбцы отчёта по поисковым запросам, на которые опирается разбор
# (`report_presets.json`, пресет `search_queries`). Имена — те, что отдаёт
# Директ; порядок столбцов не важен, разбор идёт по заголовку.
QUERY_COLUMN = "Query"
REPORT_NUMBERS = ("Impressions", "Clicks", "Cost", "Conversions")

# Форма строки отчёта. Перечень полей открыт намеренно: столбцы выбирает тот,
# кто заказывал выгрузку, и закрытый перечень отвергал бы законный отчёт с
# лишним полем. Проверяется здесь то, что от состава не зависит, — устройство
# таблицы и пустая ячейка как отсутствие; недостающие столбцы называет
# `read_report` поимённо.
REPORT_ROW = incoming.Shape()

# Столбцы, по которым строка отчёта относится к объекту. Имена те же, что в
# пресете; без них отчёт к объекту не привязать.
REPORT_SCOPE = {"campaign": "CampaignId", "group": "AdGroupId"}


def warn(text: str) -> None:
    print(redact(text), file=sys.stderr)


def say(text: str) -> None:
    print(redact(text))


# --------------------------------------------------------------------------
# Подтверждение
# --------------------------------------------------------------------------


# --------------------------------------------------------------------------
# Чтение перед записью
# --------------------------------------------------------------------------

def read_keywords(client, account, accounts, *, ids=(), group=None,
                  campaign=None) -> dict:
    """Фразы, с которыми будем работать: `{идентификатор: ответ}`."""
    criteria = {}
    if ids:
        criteria["Ids"] = [int(one) for one in ids]
    if group is not None:
        criteria["AdGroupIds"] = [int(group)]
    if campaign is not None:
        criteria["CampaignIds"] = [int(campaign)]
    if not criteria:
        raise DirectFailure(
            "Не сказано, с какими фразами работать: назовите `--keyword`, "
            "`--group` или `--campaign`."
        )
    params = dict(phrases.read_params())
    params["SelectionCriteria"] = criteria
    # Цена вызова считается по числу объектов, а оно известно заранее только
    # при отборе по идентификаторам: по группе и кампании их сколько есть.
    # Тогда `units_cost` берёт предел выборки и выходит сверху — так дешевле
    # ошибиться, чем недосчитать и получить отказ по баллам.
    need = Limits.load().units_cost(phrases.KEYWORDS, "get",
                                    len(criteria.get("Ids") or []) or None)
    found = {}
    for item in client.get_all(phrases.KEYWORDS, params, account=account,
                               use_operator_units=lambda: (
                                   accounts.use_operator_units(account,
                                                               need=need))):
        found[item["Id"]] = item
    return found


def chosen_keywords(client, account, accounts, args, *, choose=None,
                    said="") -> dict:
    """Фразы, попавшие под отбор и годные этой команде.

    Названная поимённо и не прочитавшаяся фраза — отдельная новость. Без неё
    команда правит то, что нашлось, и отчитывается успехом: человек просил три
    фразы, получил две и об этом не узнал."""
    found = read_keywords(client, account, accounts, ids=args.keyword,
                          group=args.group, campaign=args.campaign)
    phrases.all_named(found, args.keyword, "фразы")
    mine = choose(found) if choose else found
    skipped = len(found) - len(mine)
    if skipped:
        warn(f"Пропущено фраз: {skipped}. Причина: {said}.")
    if not mine:
        raise DirectFailure(
            f"Под отбор не попало ни одной годной фразы. Причина: {said or 'их нет'}. "
            f"Записывать нечего."
        )
    return mine


def plain_keywords(found: dict) -> dict:
    """Только обычные фразы: автотаргетинг живёт тем же сервисом.

    Отделять обязательно. Остановка и возобновление автотаргетинга — законная
    операция, но она не то же самое, что остановка фразы, и попасть в неё
    заодно с отбором по группе она не должна: человек просил фразы."""
    return {number: item for number, item in found.items()
            if not phrases.is_autotargeting(item)}


def autotargeting_of(found: dict) -> dict:
    return {number: item for number, item in found.items()
            if phrases.is_autotargeting(item)}


def read_one(client, account, accounts, service: str, params: dict,
             identifier) -> dict:
    """Один объект по идентификатору, либо отказ.

    Отказ, а не пустота: команда собирает итоговый список из прочитанного, и
    «объект не прочитался» здесь означает, что складывать не с чем."""
    request = dict(params)
    request["SelectionCriteria"] = {"Ids": [int(identifier)]}
    need = Limits.load().units_cost(service, "get", 1)
    for item in client.get_all(service, request, account=account,
                               use_operator_units=lambda: (
                                   accounts.use_operator_units(account,
                                                               need=need))):
        if item.get("Id") == int(identifier):
            if service == phrases.CAMPAIGNS:
                phrases.campaign_here(client, account, accounts, [identifier])
            return item
    raise DirectFailure(
        f"{service}: объект {identifier} не прочитался. Минус-фразы "
        f"заменяются целиком, и складывать добавку не с чем — запись стёрла бы "
        f"то, о чём мы не знаем."
    )


# Разбор массива Директа живёт в `phrases`: там же собирается условие замены,
# которое сверяет прочитанное с тем, из чего собрана замена, и две копии
# разбора расходились бы ровно на той форме, которую забыли поправить.
items_of = phrases.items_of


# --------------------------------------------------------------------------
# Итоговый список минус-фраз
# --------------------------------------------------------------------------

def merged(current, wanted, mode: str) -> list:
    """Каким станет список: добавили, убрали или заменили целиком.

    Порядок сохраняется, повторы снимаются: Директ принимает список с дублями
    и хранит их, а человеку в предпросмотре одно и то же слово дважды
    показывать незачем.

    Сравнение при `--remove` идёт по нормализованному виду. Директ возвращает
    минус-фразу не такой, какой её приняли: `ё` заменяется на `е`, а перед
    фразой появляется оператор фиксации формы.
    Дословное сравнение не нашло бы `чертёж` в прочитанном `чертеж`, и
    команда молча не убрала бы ничего."""
    if mode == "replace":
        return _unique(wanted)
    if mode == "add":
        known = {_folded(one) for one in current}
        return _unique(list(current) + [one for one in wanted
                                        if _folded(one) not in known])
    unwanted = {_folded(one) for one in wanted}
    return _unique([one for one in current if _folded(one) not in unwanted])


def _folded(phrase: str) -> str:
    """Минус-фраза в том виде, в каком её сравнивают: без `ё` и без оператора."""
    text = " ".join(str(phrase).split()).lower().replace("ё", "е")
    return text[1:] if text[:1] in ("!", "+") else text


def _words(text: str) -> list:
    """Слова фразы в том виде, в каком их сравнивают: без операторов у каждого.

    Оператор бывает не только у первой позиции: `купить !печь` и `купить +для`
    — законные минус-фразы, и Директ ставит оператор фиксации формы сам.
    Снятый только с начала строки, он оставляет `!печь` словом, которого в
    запросе нет никогда, и минус-фраза выглядит несработавшей.

    Разбор слова берётся у `phrases`: там он уже есть и используется
    кросс-минусовкой. Свой был бы вторым написанием одного правила, и разошлись
    бы они на первом же новом операторе."""
    return [one for one in (phrases.bare(word)
                            for word in _folded(text).split()) if one]


def _blocked(query: str, minuses) -> bool:
    """Отсечён ли запрос хоть одной из уже стоящих минус-фраз.

    Дословного совпадения здесь мало, и разница видна на первом же прогоне:
    минус-фраза `грядки` отсекает запрос «грядки оцинкованные купить», а
    сравнение строк их не сводит — и команда предложила бы заминусовать то,
    что уже заминусовано, приписав этим показам цену, которой давно нет.

    Минус-фраза срабатывает, когда **все** её слова есть в запросе; порядок
    Директу безразличен. Словоформы тут не разбираются: без морфологии
    «грядка» и «грядки» останутся разными словами, и ошибка пойдёт в
    безопасную сторону — лишнее предложение, а не пропущенное отсечение."""
    words = set(_words(query))
    return any(found and set(found) <= words for found in minuses)


def _unique(items) -> list:
    seen, found = set(), []
    for one in items:
        key = _folded(one)
        if key in seen:
            continue
        seen.add(key)
        found.append(one)
    return found


# --------------------------------------------------------------------------
# Отчёт по поисковым запросам
# --------------------------------------------------------------------------

def conversion_columns(rows) -> list:
    """Столбцы конверсий, включая выбранные цели и модели атрибуции."""
    reference = Reference.load()
    columns = dict.fromkeys(name for row in rows for name in row)
    found = [name for name in columns if reference.column_base(name) == "Conversions"]
    if not found:
        raise DirectFailure(
            "В отчёте нет Conversions или столбцов вида Conversions_513923501_AUTO. "
            "Закажите отчёт `search_queries` с `--goals` и выбранными пользователем "
            "целями. Без конверсий предлагать минус-фразы нельзя.")
    return found


def read_report(path: Path) -> list:
    """Строки отчёта по поисковым запросам из выгрузки `TSV`.

    Разбор идёт по **именам столбцов**, а не по их порядку: Директ ставит перед
    таблицей строку с названием отчёта, а после неё — строку с числом строк, и
    порядок полей задаёт запрос, который делали не мы."""
    text = incoming.text_of(path, "Отчёт")
    # Шапка ищется по имени столбца, а срез берётся от **текста**, а не от
    # разрезанных строк: перевод строки внутри кавычек законен, и склеенный
    # `splitlines()` он превратил бы два запроса в один, ничего не сказав.
    offset = 0
    for line in text.splitlines(keepends=True):
        if QUERY_COLUMN in line.rstrip("\r\n").split("\t"):
            break
        offset += len(line)
    else:
        raise DirectFailure(
            f"В {path} нет столбца «{QUERY_COLUMN}»: это не отчёт по поисковым "
            f"запросам. Нужен `SEARCH_QUERY_PERFORMANCE_REPORT` — пресет "
            f"`search_queries` из `references/report_presets.json`."
        )
    # Разбор — общим правилом скилла (`F-15`): повтор столбца и лишнее
    # значение в строке меняют данные молча, а незакрытая кавычка при
    # нестрогом разборе съедает остаток файла. Перечень столбцов при этом
    # открыт: состав выгрузки задаёт тот, кто её заказывал, и требовать от неё
    # известных имён значило бы отвергать законные отчёты. Чего разбору не
    # хватает, он говорит ниже, поимённо.
    rows = incoming.table(text[offset:], f"Отчёт {path}", REPORT_ROW,
                          delimiter="\t")
    found = []
    for row in rows:
        query = (row.get(QUERY_COLUMN) or "").strip()
        if not query or query.startswith("Total rows"):
            continue
        found.append(row)
    if not found:
        raise DirectFailure(f"В {path} нет ни одной строки с запросом.")
    # Трафик и расход нужны для порогов; конверсии проверяются отдельно,
    # поскольку имя столбца зависит от целей и атрибуции.
    missing = [name for name in REPORT_NUMBERS if name != "Conversions"
               and not any(name in row for row in found)]
    if missing:
        raise DirectFailure(
            f"В {path} нет столбцов {', '.join(missing)}. Без них нельзя "
            f"оценить трафик и расход. Закажите отчёт пресетом `search_queries`."
        )
    conversion_columns(found)
    return found


def within(rows, level: str, identifier) -> list:
    """Строки отчёта, относящиеся к тому объекту, куда пойдут минус-фразы.

    Отчёт бывает шире цели: пресет `search_queries` группирует по запросу и
    подобранной фразе, а отбор задаётся при заказе отчёта — и кабинетная
    выгрузка накрывает все кампании сразу. Сложить их и предложить итог одной
    кампании значит предложить заминусовать в ней запрос, который плохо
    отработал в другой, а здесь, может быть, кормит.

    Столбца объекта в отчёте может не быть вовсе — его состав задаёт тот, кто
    отчёт заказывал. Тогда это отказ, а не молчаливое «берём всё»: отчёт, к
    цели не привязываемый, к ней и не относится."""
    column = REPORT_SCOPE[level]
    if not any(column in row for row in rows):
        raise DirectFailure(
            f"В отчёте нет столбца «{column}», и отнести его строки к "
            f"выбранному объекту нечем. Закажите отчёт с этим полем — в "
            f"пресете `search_queries` оно есть — или отчёт ровно по тому "
            f"объекту, куда пойдут минус-фразы."
        )
    found = [row for row in rows if str(row.get(column) or "").strip()
             == str(identifier)]
    if not found:
        raise DirectFailure(
            f"В отчёте нет ни одной строки с «{column}» равным {identifier}. "
            f"Отчёт снят не по тому объекту, которому назначаются минус-фразы."
        )
    return found


def finite(text: str) -> float:
    """Порог из аргумента: число и только конечное.

    `float` разбирает `nan` и `inf` молча, а порог с ними доходит до
    `int(round(...))` и роняет команду трассировкой — уже после того, как
    прочитаны и отчёт, и кабинет. Ни порогом расхода, ни порогом чего угодно
    они при этом не являются: сравнивать с `nan` бессмысленно, он не больше и
    не меньше ничего."""
    try:
        number = float(text)
    except (TypeError, ValueError):
        number = None
    if number is None or not math.isfinite(number):
        raise argparse.ArgumentTypeError(
            f"ожидается число, получено «{excerpt(str(text), 40)}»")
    return number


def _number(value, *, where: str = "", missing=0.0) -> float | None:
    """Конечное число; пустая ячейка и прочерк возвращают missing.

    Для конверсий передают missing=None: отсутствие показателя не доказывает,
    что конверсий не было. Нечитаемое значение вызывает ошибку.
    """
    text = str(value if value is not None else "").strip().replace(",", ".")
    if not text or text in ("--", "-"):
        return missing
    try:
        number = float(text)
    except ValueError:
        number = None
    # `float` понимает не только числа: `nan`, `inf` и `-infinity` он
    # разбирает молча, а дальше по дороге `int()` от них падает трассировкой —
    # то есть команда обещает читаемый отказ, а выдаёт стек. И по смыслу это
    # не число показов и не сумма расхода: сложить их не с чем.
    if number is None or not math.isfinite(number):
        raise DirectFailure(
            f"В отчёте не читается число: «{excerpt(text, 40)}»"
            + (f" в столбце {where}" if where else "")
            + ". Принять его за ноль нельзя: ноль в конверсиях ведёт прямиком "
              "в предложение заминусовать запрос."
        ) from None
    return number


def micros(value, *, in_micros: bool) -> int:
    """Денежная ячейка отчёта в целых микро — как их понимает `money.py`.

    Скилл не отправляет заголовок `returnMoneyInMicros`, поэтому его
    собственные выгрузки приходят целыми микро (`REPORTS.md`, раздел 10). А
    выгрузка из интерфейса кабинета — в валюте, с двумя знаками. Отличить их
    по виду числа нельзя: `120` — это и сто двадцать микро, и сто двадцать
    рублей, и угадавший разойдётся с истиной в миллион раз. Поэтому источник
    называет человек, а умолчание — своё, скилловое."""
    amount = _number(value, where="Cost")
    return int(round(amount * 1_000_000 if not in_micros else amount))


def campaign_owners(client, account, accounts, sets) -> dict:
    """Какие кампании держат каждый из названных наборов: `{набор: [кампании]}`.

    Признак `Associated` отвечает только за группы (замер 30.08.2026, см.
    `phrases.unattached`), поэтому кампании считаются обходом. Обход именно
    кампаний: их у клиента не больше трёх тысяч и стоят они балл за штуку, а
    групп бывает тысяча на кампанию — обход всех стоил бы миллионы. Группы
    покрывает признак, и платить за них не надо.

    Обход полный: отбора «кампании, где подключён набор N» у API нет, а
    перечень наборов лежит внутри типовой структуры, которую надо запрашивать
    поимённо."""
    wanted = {int(one) for one in sets}
    found = {}
    request = dict(phrases.campaign_negatives_read())
    request["SelectionCriteria"] = {}
    need = Limits.load().units_cost(phrases.CAMPAIGNS, "get", None)
    for item in client.get_all(
            phrases.CAMPAIGNS, request, account=account,
            use_operator_units=lambda: accounts.use_operator_units(
                account, need=need)):
        body = item.get(phrases.CAMPAIGN_TYPES.get(item.get("Type"), "")) or {}
        for number in items_of(body.get("NegativeKeywordSharedSetIds")):
            if int(number) in wanted:
                found.setdefault(int(number), []).append(item.get("Id"))
    return found


def effective_negatives(client, account, accounts, *, level, record) -> list:
    """Всё, чем запрос уже отсечён на действующих уровнях.

    Уровни **складываются**: у группы работают её собственные минус-фразы, её
    наборы из библиотеки, минус-фразы кампании и наборы кампании. Список, собранный по одному уровню, ровно
    настолько же неполон — и каждый пропущенный уровень возвращает человеку
    уже сделанную работу под видом новой.

    Перечень наборов у кампании лежит внутри типовой структуры, и типов, у
    которых его нет, два: `CPM_BANNER_CAMPAIGN` и `SMART_CAMPAIGN` отвечают
    кодом 8000 на само упоминание поля (замер 30.08.2026). Поэтому имена
    запрашиваются по перечню `SHARED_SET_CAMPAIGNS`, а не по всем типам."""
    said = list(items_of(record.get("NegativeKeywords")))
    sets = [int(one) for one in
            items_of(record.get("NegativeKeywordSharedSetIds"))]
    # У группы своя кампания, у кампании — она сама: минус-фразы кампании
    # действуют в обоих случаях, а перечень её наборов читается только так.
    campaign = record.get("CampaignId") if level == "group" else record.get("Id")
    if campaign is not None:
        parent = read_one(client, account, accounts, phrases.CAMPAIGNS,
                          phrases.campaign_negatives_read(), campaign)
        said += items_of(parent.get("NegativeKeywords"))
        body = parent.get(phrases.CAMPAIGN_TYPES.get(parent.get("Type"), "")) or {}
        sets += [int(one) for one in
                 items_of(body.get("NegativeKeywordSharedSetIds"))]
    sets = sorted(dict.fromkeys(sets))
    if sets:
        request = dict(phrases.shared_set_read())
        request["SelectionCriteria"] = {"Ids": sets}
        need = Limits.load().units_cost(phrases.SHARED_SETS, "get", len(sets))
        found = list(client.get_all(
            phrases.SHARED_SETS, request, account=account,
            use_operator_units=lambda: accounts.use_operator_units(
                account, need=need)))
        # Набор, который не прочитался, — не «набор без минус-фраз». Приняв
        # его за пустой, команда предложит заминусовать то, что он и так
        # отсекает: человек увидит прошлый расход за уже сделанной работой, а
        # повторная запись потратит конечную длину списка. Идентификаторы сюда
        # пришли из самого кабинета, так что пропажа — это отказ, а не норма.
        phrases.all_named([one.get("Id") for one in found], sets,
                          "наборы минус-фраз")
        for item in found:
            said += items_of(item.get("NegativeKeywords"))
    return list(dict.fromkeys(said))


def suggest_negatives(rows, *, min_clicks: int = 0, min_cost: float = 0.0,
                      known=(), in_micros: bool = True) -> list:
    """Что предложить в минус-фразы по отчёту и во сколько это обошлось.

    Предлагается **запрос целиком**, а не выдернутое из него слово. Слово,
    выбранное автоматически, отсекает и то, чего человек отсекать не просил:
    «купить» в мусорном запросе — то же «купить», что в коммерческом.
    Запрос же отсекает ровно себя, и человек видит, что именно уходит.

    Отбираются запросы с явным нулём по всем столбцам конверсий и всем строкам
    запроса. Цели и модели не складываются: достаточно любой положительной,
    в том числе дробной конверсии или пропуска, чтобы запрос не предлагать.

    `known` — то, что уже заминусовано на любом из уровней. Повторное
    предложение уже стоящей минус-фразы не ошибка, но человеку оно врёт про
    цену вопроса: эти показы уже отсечены."""
    if not rows:
        return []
    columns = conversion_columns(rows)
    seen = [_words(one) for one in known]
    rolled, excluded = {}, set()
    for row in rows:
        query = (row.get(QUERY_COLUMN) or "").strip()
        if _blocked(query, seen):
            continue
        found = rolled.setdefault(query, {name: 0 for name in REPORT_NUMBERS})
        for name in columns:
            value = _number(row.get(name), where=name, missing=None)
            if value is not None and value < 0:
                raise DirectFailure(f"В отчёте отрицательное число конверсий в столбце {name}.")
            if value is None or value > 0:
                excluded.add(query)
        for name in ("Impressions", "Clicks"):
            found[name] += int(_number(row.get(name), where=name))
        found["Cost"] += micros(row.get("Cost"), in_micros=in_micros)
    found = [
        {"query": query, **numbers} for query, numbers in rolled.items()
        if query not in excluded
        and numbers["Clicks"] >= min_clicks
        and numbers["Cost"] >= int(round(min_cost * 1_000_000))
    ]
    found.sort(key=lambda one: (-one["Cost"], -one["Clicks"], one["query"]))
    return found


def report_lines(found, currency: str = "") -> list:
    """Цена вопроса: сколько стоили запросы, которые предлагается отсечь."""
    cost = sum(one["Cost"] for one in found)
    clicks = sum(one["Clicks"] for one in found)
    shows = sum(one["Impressions"] for one in found)
    lines = [
        f"Запросов без конверсий: {len(found)}. Показов {shows}, кликов "
        f"{clicks}, расход {format_api(cost, currency)}."
    ]
    for one in found:
        lines.append(
            f"  {one['query']} · показов {one['Impressions']}, кликов "
            f"{one['Clicks']}, расход {format_api(one['Cost'], currency)}"
        )
    return lines


# --------------------------------------------------------------------------
# Сборка задачи
# --------------------------------------------------------------------------

def keyword_add_task(client, account, accounts, args) -> tuple:
    """Добавление фраз, а при просьбе — и автотаргетинга.

    Автотаргетинг в группе один, и **нормально созданной** группе Директ
    заводит его сам. Повторный
    `Keywords.add` второго не создаёт, а **правит существующий** — и правит
    поверх умолчаний: не названная в запросе настройка возвращается к `YES`.
    Для конвейера это разница принципиальная: у создания снимок не снимается —
    объекта ещё нет, — и правка под видом создания ушла бы в журнал с пустым
    «было». Именно то состояние, к которому потом возвращаются, и пропало бы.

    Поэтому группа читается до сборки: есть автотаргетинг — настройки идут
    правкой, с нормальным снимком и сверкой; нет — создаётся.

    Тем же чтением считается потолок числа фраз на группу. Он проверяется по
    конечному состоянию, а не поштучно: по отдельности влезает каждая, а
    переступает предел их сумма с уже лежащими."""
    settings = settings_from(args)
    params = params_from(args)
    if not args.phrase and not args.autotargeting:
        raise DirectFailure(
            "Не сказано, что добавлять: назовите `--phrase` или "
            "`--autotargeting`."
        )
    if params and not args.phrase:
        # Цена названа, а просьба исполняется: подставлять значение
        # автотаргетингу некуда — текста фразы у него нет, — но это суждение, а
        # не факт, и запретом в коде просьбу не обходят.
        warn(PARAMS_ON_AUTOTARGETING)
    inside = read_keywords(client, account, accounts, group=args.group)
    found = autotargeting_of(inside)
    # Автотаргетинг заводится тем же `Keywords.add` и в той же таблице
    # объектов, поэтому в счёт потолка идёт наравне с фразами — но только пока
    # его нет: у существующего настройки правятся на месте, объект не
    # прибавляется.
    making = args.autotargeting and not found
    phrases.fits_capacity(args.group, inside,
                          len(args.phrase or ()) + (1 if making else 0))
    operations, said = [], []
    if args.phrase:
        said.append(f"фраз {len(args.phrase)}")
    if not args.autotargeting:
        operations.append(phrases.add_operation(
            args.group, args.phrase, params=params,
            guard=creation_guard(client, account, accounts, args.group,
                                 adding=len(args.phrase), autotargeting=False)))
        return (f"добавление в группу {args.group}: {', '.join(said)}",
                operations)
    if making:
        # Фразы и автотаргетинг уходят **одной** операцией: `Keywords.add`
        # принимает их одним массивом, а условие читает группу заново — двумя
        # операциями оно читало бы её дважды за проход.
        said.append("автотаргетинг")
        operations.append(phrases.add_operation(
            args.group, list(args.phrase or ()) + [phrases.AUTOTARGETING],
            settings=settings, params=params,
            guard=creation_guard(client, account, accounts, args.group,
                                 adding=len(args.phrase or ()) + 1,
                                 autotargeting=True)))
        return (f"добавление в группу {args.group}: {', '.join(said)}",
                operations)
    if args.phrase:
        operations.append(phrases.add_operation(
            args.group, args.phrase, params=params,
            guard=creation_guard(client, account, accounts, args.group,
                                 adding=len(args.phrase), autotargeting=False)))
    if settings is None:
        # Значения параметров сюда доходят, и молча пропасть они не должны:
        # человек назвал их этому объекту, а команда создания их не применяет —
        # применяет `keyword param`, и назвать её надо вместе с номером, иначе
        # человек ищет объект сам.
        raise DirectFailure(
            f"Автотаргетинг в группе {args.group} уже есть — он прочитан в "
            f"группе перед сборкой, а больше одного в группе не бывает. "
            f"Заводить нечего; настройки меняет `autotargeting set`"
            + (f", а значения параметров — `keyword param --keyword "
               f"{sorted(found)[0]}`" if params else "")
            + "."
        )
    else:
        warn(f"Автотаргетинг в группе {args.group} уже есть ({sorted(found)[0]}) "
             f"— настройки уйдут правкой, а не созданием: повторное добавление "
             f"второго объекта не создаёт, зато оставило бы журнал без прежнего "
             f"состояния.")
        operations.append(phrases.autotargeting_operation(
            sorted(found)[0], categories=settings.get("Categories"),
            brands=settings.get("BrandOptions"),
            guard=phrases.all_categories_off(
                lambda: read_keywords(client, account, accounts,
                                      ids=[sorted(found)[0]]),
                sorted(found)[0], settings.get("Categories"))))
        said.append("настройки автотаргетинга")
    return (f"добавление в группу {args.group}: {', '.join(said)}", operations)


def creation_guard(client, account, accounts, group, *, adding: int,
                   autotargeting: bool):
    """Условие для создания фраз: группа читается заново перед самой записью.

    Создание конвейер перечитыванием не сторожит — снимка у него нет, объекта
    ещё не существует. Значит окно между чтением команды и записью закрывать
    больше нечем: условие читает группу само, и читает на каждой проверке.

    Стережёт оно две вещи. Автотаргетинг в группе один, и `Keywords.add` со
    вторым второго не создаёт, а **правит существующий** — поверх умолчаний,
    возвращая к `YES` не названное в запросе: появившийся в окне, он превратил
    бы создание в правку — с пустым «было» в журнале и без снимка, к которому
    потом возвращаются. И потолок числа фраз: он про состояние группы, а
    состояние в окне меняется."""
    # Существование группы спрашивается у самой группы. Пустой ответ
    # `Keywords.get` у несуществующей группы и у пустой одинаков, и принять
    # его за «группа есть, фраз нет» значит заплатить за негодную запись.
    owner = phrases.exists_guard(
        client, account, accounts, phrases.GROUPS,
        {"FieldNames": list(phrases.GROUP_FIELDS)}, [group], "группы")

    def check(_known) -> list:
        said = list(owner(_known))
        if said:
            # Группы нет — считать в ней нечего, и второе сообщение про
            # потолок только увело бы человека от причины.
            return said
        inside = read_keywords(client, account, accounts, group=group)
        if autotargeting and autotargeting_of(inside):
            said.append(
                f"Автотаргетинг в группе {group} появился, пока задача "
                f"собиралась. Создать второй нельзя: `Keywords.add` правит "
                f"существующий поверх умолчаний, возвращая к `YES` не названное "
                f"в запросе, — а журнал записал бы правку созданием, с пустым "
                f"«было». Повторите команду: настройки уйдут правкой, с "
                f"нормальным снимком."
            )
        try:
            phrases.fits_capacity(group, inside, adding)
        except DirectFailure as failure:
            said.append(str(failure))
        return said
    return check


def paired(values, said: str) -> dict:
    """Пары «имя=значение» словарём, с отказом на противоречивый повтор.

    `dict()` из списка пар молча оставляет последнее значение: названная
    дважды с разными значениями категория превратилась бы в одно из них, а
    команда отчиталась бы об исполненной просьбе целиком. Какое из двух
    значений человек имел в виду, отсюда не видно — и угадывать нечего.

    Повтор с тем же значением пропускается: терять там нечего."""
    body = {}
    for name, value in values or ():
        if name in body and body[name] != value:
            # Род у названия настройки разный («категория», «упоминание»),
            # поэтому причастия здесь нет: оно согласовалось бы с одним из
            # двух и рассогласовалось с другим.
            raise DirectFailure(
                f"{said} «{excerpt(str(name), 32)}»: два разных значения, "
                f"{excerpt(str(body[name]), 16)} и {excerpt(str(value), 16)}. "
                f"Второе молча вытеснило бы первое, а команда отчиталась бы "
                f"об исполненной просьбе целиком."
            )
        body[name] = value
    return body


def settings_from(args):
    """Настройки автотаргетинга из аргументов, либо `None`.

    Пустые словари не отправляются: `AutotargetingSettings` без единого поля
    Директ примет, а изменит ли что-нибудь — неизвестно, и сверять такую
    правку не с чем."""
    categories = paired(args.category, "Категория")
    brands = paired(args.brand, "Упоминание брендов")
    if not categories and not brands:
        return None
    body = {}
    if categories:
        body["Categories"] = categories
    if brands:
        body["BrandOptions"] = brands
    return body


def params_from(args):
    """Значения `{param1}` и `{param2}` из аргументов, либо `None`."""
    named = {}
    # Через `getattr`, как `--state` и `--shared-set` в соседних командах:
    # аргумент объявлен не у каждого действия, а собирает значения одна
    # функция — правило, разложенное по действиям, обходится тем, где его
    # забыли.
    if getattr(args, "param1", None) is not None:
        named["param1"] = args.param1
    if getattr(args, "param2", None) is not None:
        named["param2"] = args.param2
    for name in getattr(args, "clear", ()) or ():
        if name in named:
            raise DirectFailure(
                f"Параметр `{{{name}}}` просят и заполнить, и очистить разом. "
                f"Сбылось бы одно из двух, а сказаны оба — какое именно, "
                f"отсюда не видно."
            )
        named[name] = CLEAR
    return named or None


def _after(record: dict, named: dict) -> dict:
    """Какими станут значения параметров этой фразы после записи.

    Неназванные берутся из свежего чтения: адрес собирается из обоих, и
    показать один подставленным, а второй пустым значило бы показать человеку
    ссылку, которой не будет."""
    values = {}
    for name, field in phrases.USER_PARAMS.items():
        value = named[name] if name in named else record.get(field)
        values[name] = None if value is CLEAR else value
    return values


# Что сказать про значение параметра у автотаргетинга. Не отказ, и это разница
# по существу: «подставлять некуда» — суждение, а Директ такую запись принимает.
# Замерено 30.08.2026 на объекте описи: `Keywords.update` кладёт `UserParam1` на
# автотаргетинг, `Keywords.get` возвращает его неизменным, очистка снимает
#. Запретить это значило бы отнять у человека
# решение, которое принадлежит ему; скилл называет цену и делает.
PARAMS_ON_AUTOTARGETING = (
    "Значение уйдёт и ему — так просили. Директ такую запись принимает и "
    "возвращает значение неизменным (замер 30.08.2026), но подставить его "
    "некуда: текста фразы у автотаргетинга нет, и адрес по нему не собирается. "
    "Подставляет ли Директ параметр при показе по автотаргетингу — неизвестно: "
    "показов тестовый кабинет не даёт вовсе."
)

# Сколько строк с адресами показывать. Предпросмотр читается целиком, но пар
# «объявление × фраза» бывает под сотню, и одинаковых среди них большинство:
# показываются **разные** адреса, а сколько строк не поместилось — говорится
# прямо. Молчаливой обрезки здесь быть не должно, она читается как «это всё».
ADDRESSES_SHOWN = 20


def address_notes(client, account, accounts, found: dict, wanted: dict) -> list:
    """Во что превратится адрес объявления с этими значениями.

    Значение параметра само по себе человеку не говорит ничего: смысл у него
    появляется в адресе, куда Директ его подставит. Незаполненный параметр
    оставляет в адресе пустое место — из `https://site.ru/{param1}/` выходит
    `https://site.ru//`, ссылка выглядит правильной и ведёт не туда, — и видеть
    это надо до записи, а не по отчёту Метрики.

    **Фраза складывается только со своими объявлениями.** Показ идёт по фразе
    группы, и адрес объявления соседней группы по ней не соберётся никогда:
    отбор `--campaign` берёт фразы нескольких групп разом, и общий перебор
    показал бы человеку пары, которых не бывает. Хуже того, он спрятал бы
    новость: в группе, где `{param1}` в адресе нет вовсе, значение подставлять
    некуда, а чужие строки выглядели бы как её собственные.

    Объявления читаются обоими наборами имён полей разом
    (`responsive.read_params`): с одним `TextAdFieldNames` уже
    сконвертированное объявление возвращается усечённым, и адрес пришёл бы не
    тот, который правится."""
    groups = sorted({int(found[number]["AdGroupId"]) for number in wanted
                     if found[number].get("AdGroupId") is not None})
    if not groups:
        return []
    request = dict(responsive.read_params())
    request["SelectionCriteria"] = {"AdGroupIds": groups}
    need = Limits.load().units_cost(responsive.SERVICE, "get", None)
    ads = list(client.get_all(
        responsive.SERVICE, request, account=account,
        use_operator_units=lambda: accounts.use_operator_units(account,
                                                               need=need)))
    people, aside = {}, []
    for number in sorted(wanted):
        record = found[number]
        if record.get("AdGroupId") is None:
            continue
        if phrases.is_autotargeting(record):
            # Автотаргетинг в отборе законен, а адрес по нему не собирается:
            # подставлять вместо параметра нечего, текста фразы у него нет.
            # Сказать это надо ровно один раз и своими словами — общая жалоба
            # «фраза состоит из операторов» ответила бы не на тот вопрос.
            aside.append(f"  адрес по автотаргетингу {number} не собрать: "
                         f"текста фразы у него нет")
            continue
        try:
            phrase = templates.Phrase(record.get("Keyword") or "",
                                      identifier=number,
                                      **_after(record, wanted[number]))
        except DirectFailure as failure:
            # Показ адреса — не запись, и ронять её он не должен: фраза, по
            # которой подставлять нечего, значение параметра принимает так же,
            # как любая другая. Молчать при этом нельзя — человек ждал строку
            # именно про неё, — поэтому причина уходит в тот же перечень.
            aside.append(f"  адрес по фразе "
                         f"«{excerpt(record.get('Keyword') or '', 40)}» "
                         f"не собрать: {failure}")
            continue
        people.setdefault(int(record["AdGroupId"]), []).append(phrase)
    order, seen, shows, with_ads = [], {}, set(), set()
    for ad in ads:
        group = ad.get("AdGroupId")
        if group is None:
            continue
        with_ads.add(int(group))
        mine = people.get(int(group))
        if not mine:
            continue
        href = ((ad.get(responsive.STRUCTURE) or {}).get("Href")
                or (ad.get("TextAd") or {}).get("Href"))
        if not href:
            continue
        asked = {name for name, kind in templates.tokens(href)
                 if kind == templates.PARAM}
        if not asked:
            continue
        shows.add(int(group))
        for variant in templates.variants("Href", href, mine):
            # Одинаковые адреса сводятся в одну строку, но число фраз за ней
            # называется: значение у всех отобранных фраз одно, и адрес у
            # большинства получается один и тот же. Строка на каждую фразу
            # была бы тем же адресом столько раз, сколько фраз в группе, а
            # молчаливое схлопывание читалось бы как «правится одна фраза».
            key = (ad.get("Id"), variant.substituted)
            if key in seen:
                seen[key][0] += 1
                continue
            # Замечания движка идут той же строкой, что и адрес. Главное из
            # них — незаполненный параметр: `https://site.ru/{param1}/`
            # превращается в `https://site.ru//`, и по одному только адресу
            # это читается как опечатка, а не как следствие правки.
            said_about = list(variant.problems) + list(variant.notes)
            # Параметр, которого эта правка не трогает, показан по чтению
            # **до** записи, а конвейер возьмёт свежий снимок. Разойтись они
            # могут: соседний прогон успевает поменять неназванное поле, и
            # адрес окажется не тем, который человек прочитал. Сама запись от
            # этого не страдает — уходят только названные поля, а «было» в
            # перечне изменений конвейер подставляет из своего снимка, — но
            # строка адреса обещала бы больше, чем знает, и провенанс её
            # значения называется прямо.
            untouched = sorted(asked - set(wanted.get(variant.phrase.identifier)
                                           or ()))
            if untouched:
                said_about.append(
                    ", ".join("`{" + one + "}`" for one in untouched)
                    + " показан по чтению до записи: эта правка его не трогает")
            mark = "; ".join(said_about)
            seen[key] = [1, variant, mark]
            order.append(key)
    said = []
    for key in order:
        count, variant, mark = seen[key]
        said.append(
            f"  адрес объявления {key[0]} по фразе "
            f"«{excerpt(variant.phrase.text, 40)}»"
            + (f" и ещё {count - 1} с тем же адресом" if count > 1 else "")
            + f": {variant.substituted}"
            + (f" — {mark}" if mark else ""))
    hidden = len(said) - ADDRESSES_SHOWN
    lines = ([] if not said else
             ["Адрес объявления с этими значениями:"] + said[:ADDRESSES_SHOWN]
             + ([f"  и ещё {hidden} — показаны первые {ADDRESSES_SHOWN}"]
                if hidden > 0 else []))
    # Группы, где подставлять некуда, называются поимённо, а не молчанием. Две
    # разные новости, и сводить их в одну нельзя: у группы без объявлений
    # подставлять значение просто не во что, а группа с объявлениями без
    # параметров в адресе — это, скорее всего, забытый `{param1}` в самом
    # адресе.
    for idle, said_why in (
            ([one for one in groups if one not in shows and one in with_ads],
             "нет `{param1}` и `{param2}` ни в одном адресе объявлений — "
             "значение запишется, но подставлять его пока некуда"),
            ([one for one in groups if one not in with_ads],
             "нет объявлений — подставлять значение пока некуда")):
        if idle:
            lines.append(
                ("Группа " if len(idle) == 1 else "Группы ")
                + ", ".join(str(one) for one in idle) + ": " + said_why + ".")
    return lines + aside


def added_address_notes(client, account, accounts, args) -> list:
    """То же, что `address_notes`, но для ещё не созданных фраз.

    Предпросмотр адреса нужен обоим путям записи. Поставленный на один,
    он обходится вторым: человек, задавший значение при создании, увидел бы
    только само значение — а вопрос у него тот же, во что превратится адрес.

    Идентификатора у создаваемой фразы ещё нет, и записи собираются из того,
    что уходит в запрос: группа и текст известны, прежних значений у неё не
    бывает."""
    named = params_from(args)
    if not named or not args.phrase:
        return []
    found = {number: {"Keyword": text, "AdGroupId": args.group}
             for number, text in enumerate(args.phrase)}
    return address_notes(client, account, accounts, found,
                         {number: named for number in found})


def params_task(client, account, accounts, args) -> tuple:
    """Значения `{param1}` и `{param2}` у фраз: задать или снять.

    Названное уходит в запрос **целиком**, включая фразы, у которых значение
    уже такое. Отсеивать их по чтению команды заманчиво и неверно: между этим
    чтением и записью значение успевают поменять, а отсеянная фраза в запрос не
    попадает вовсе — команда отчиталась бы успехом, а состояние, о котором
    просили, в кабинете так и не наступило бы. Сторож окна этого не ловит: он
    сличает снимок конвейера с перечитыванием, а отсеянного объекта в операции
    нет.

    Лишний запрос дешевле такого исхода и честнее его: `Keywords.update`
    принимает то же самое значение молча, перечитывание подтверждает итог, и
    «значение стоит» становится проверенным утверждением, а не выводом из
    устаревшего чтения. Ни отказа «ничего не меняется», ни тихого пропуска
    здесь поэтому нет — просьба исполняется так, как её сказали."""
    named = params_from(args)
    if not named:
        raise DirectFailure(
            "Не сказано, что менять: назовите `--param1`, `--param2` или "
            "`--clear`."
        )
    found = chosen_keywords(client, account, accounts, args)
    apart = sorted(autotargeting_of(found))
    if apart:
        # Отбор по группе берёт автотаргетинг заодно с фразами, и раньше он
        # отсюда выпадал. Выпадать он не должен: захотеть значение на нём можно
        # осознанно, а цена названа — она в предупреждении, а не в отказе.
        warn(f"Среди отобранных объектов автотаргетинг: "
             f"{', '.join(str(one) for one in apart)}. "
             + PARAMS_ON_AUTOTARGETING)
    wanted = {number: dict(named) for number in sorted(found)}
    said = ", ".join(
        f"`{{{name}}}` " + ("снимается" if value is CLEAR
                            else f"= «{excerpt(str(value), 40)}»")
        for name, value in named.items())
    # «Объектов», а не «фраз»: автотаргетинг из отбора не выбрасывается, и
    # заголовок, назвавший его фразой, уходит таким в журнал.
    return (f"значения параметров: объектов {len(wanted)}, {said}",
            [phrases.params_operation(wanted, found)],
            address_notes(client, account, accounts, found, wanted))


def negative_target_task(client, account, accounts, args) -> tuple:
    """Минус-фразы кампании или группы: итог считается по свежему чтению."""
    level = args.action
    service = phrases.CAMPAIGNS if level == "campaign" else phrases.GROUPS
    identifier = args.campaign if level == "campaign" else args.group
    read = ({"FieldNames": list(phrases.CAMPAIGN_FIELDS)} if level == "campaign"
            else {"FieldNames": list(phrases.GROUP_FIELDS)})
    record = read_one(client, account, accounts, service, read, identifier)
    shared = getattr(args, "shared", None)
    if shared is not None and level == "campaign":
        raise DirectFailure(
            "Для подключения набора минус-фраз к кампании используйте "
            "scripts/campaign_write.py: поле находится внутри настроек "
            "конкретного типа кампании."
        )
    # Половины считаются обе и только потом судятся. Половина, которая уже
    # стоит, — не повод отказать во второй: просьба «добавить минус-фразу,
    # которая уже есть, и подключить новый набор» исполнима целиком в той
    # части, которая что-то меняет.
    said, items, sets, asked, source, arriving = [], None, None, [], {}, None
    if args.phrase or (args.mode == "replace" and shared is None):
        asked.append("минус-фразы")
        current = items_of(record.get("NegativeKeywords"))
        items = merged(current, args.phrase, args.mode)
        if items == current:
            # Не меняющаяся половина не отправляется вовсе: опущенное поле
            # Директ оставляет как есть, и обещание без изменения только
            # засоряло бы предпросмотр.
            items = None
            said.append("минус-фразы не меняются")
        else:
            # Из чего собрана замена — то и сверяется перед записью: пишется
            # массив целиком, и чужая правка была бы стёрта молча.
            source["NegativeKeywords"] = current
            said.append(f"минус-фраз было {len(current)}, станет {len(items)}")
    if shared is not None:
        asked.append("наборы")
        current = [int(one) for one in
                   items_of(record.get("NegativeKeywordSharedSetIds"))]
        sets = _sets(current, [int(one) for one in shared], args.mode)
        if sets == current:
            sets = None
            said.append("перечень наборов не меняется")
        else:
            source["NegativeKeywordSharedSetIds"] = current
            # Подключаемые впервые наборы проверяются на существование:
            # `AdGroups.update` уносит идентификатор, а есть ли за ним набор,
            # знает только сам сервис наборов. Уже подключённые не трогаем —
            # это состояние кабинета, и за его изменением следит сверка списка.
            fresh = sorted(set(sets) - set(current))
            if fresh:
                arriving = phrases.exists_guard(
                    client, account, accounts, phrases.SHARED_SETS,
                    phrases.shared_set_read(), fresh, "наборы минус-фраз")
            said.append(f"наборов было {len(current)}, станет {len(sets)}")
    if not asked:
        raise DirectFailure(
            "Не сказано, что менять: назовите `--phrase` или `--shared-set`."
        )
    if items is None and sets is None:
        raise DirectFailure(
            f"Не меняется ничего: {' и '.join(asked)} уже в том состоянии, о "
            f"котором просят. Запись без изменения прошла бы конвейер целиком "
            f"и отчиталась успехом, ничего не сделав."
        )
    # Обе половины — одной операцией, а не двумя. Две операции по одному
    # объекту конвейер отвергает: поля пересекаются, и остаётся значение
    # последней записи, а подтверждены оба обещания.
    operation = (phrases.campaign_negative_operation(identifier, items,
                                                    was=source)
                 if level == "campaign"
                 else phrases.group_negative_operation(identifier, items=items,
                                                       shared=sets, was=source,
                                                       arriving=arriving))
    where = "кампании" if level == "campaign" else "группы"
    return (f"минус-фразы {where} {identifier}: {'; '.join(said)}", [operation])


def _sets(current, wanted, mode: str) -> list:
    if mode == "replace":
        return list(dict.fromkeys(wanted))
    if mode == "add":
        return list(dict.fromkeys(list(current) + list(wanted)))
    return [one for one in current if one not in set(wanted)]


def set_task(client, account, accounts, args) -> tuple:
    """Набор минус-фраз в библиотеке: создание, правка, удаление."""
    if args.action == "create":
        return (f"создание набора минус-фраз «{args.name}»: фраз "
                f"{len(args.phrase)}",
                [phrases.shared_set_add_operation(
                    args.name, args.phrase,
                    guard=phrases.sets_capacity(
                        lambda: all_sets(client, account, accounts)))])
    if args.action == "delete":
        # Подключённый набор не удаляется, но проверяет это `phrases.unattached`
        # по снимку конвейера, а не чтением отсюда. Своё чтение здесь и стоило
        # бы вдвое (снимок всё равно читает те же поля), и окна не закрывало:
        # набор, подключённый между ним и снимком, прошёл бы проверку.
        return (f"удаление наборов минус-фраз: {len(args.set)}",
                [phrases.shared_set_delete_operation(
                    args.set,
                    owners=lambda: campaign_owners(client, account, accounts,
                                                   args.set))])
    record = read_one(client, account, accounts, phrases.SHARED_SETS,
                      phrases.shared_set_read(), args.set)
    items, source = None, {}
    if args.phrase or args.mode == "replace":
        current = items_of(record.get("NegativeKeywords"))
        items = merged(current, args.phrase, args.mode)
        if items == current and args.name is None:
            raise DirectFailure(
                "Набор не меняется: всё названное уже стоит (или не стоит)."
            )
        source["NegativeKeywords"] = current
    return (f"правка набора минус-фраз {args.set}",
            [phrases.shared_set_update_operation(
                args.set, name=args.name, items=items, was=source)])


def autotargeting_task(client, account, accounts, args) -> tuple:
    """Автотаргетинг: завести, настроить категории, остановить, снять."""
    if args.action == "add":
        # Тот же путь, что у `keyword add --autotargeting`, и та же причина:
        # правка под видом создания уходит в журнал с пустым «было».
        return keyword_add_task(client, account, accounts, argparse.Namespace(
            group=args.group, phrase=[], autotargeting=True,
            category=args.category, brand=args.brand))
    found = chosen_keywords(
        client, account, accounts, args, choose=autotargeting_of,
        said="это обычная фраза, а не автотаргетинг")
    if args.action == "set":
        settings = settings_from(args)
        if settings is None:
            raise DirectFailure(
                "Не сказано, что менять: назовите `--category` или `--brand`."
            )
        return (f"настройки автотаргетинга: объектов {len(found)}",
                [phrases.autotargeting_operation(
                    number, categories=settings.get("Categories"),
                    brands=settings.get("BrandOptions"),
                    guard=phrases.all_categories_off(
                        lambda number=number: read_keywords(
                            client, account, accounts, ids=[number]),
                        number, settings.get("Categories")))
                 for number in sorted(found)])
    return (f"{phrases.LIFECYCLE_RU[args.action]} автотаргетинга: объектов "
            f"{len(found)}",
            [phrases.lifecycle_operation(args.action, sorted(found),
                                         state=getattr(args, "state", None),
                                         autotargetings=sorted(found))])


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
    """Общие флаги. Ставятся и на корень, и на каждое действие.

    Только на корне их мало: `argparse` разбирает их до подкоманды, и
    `keyword add … --apply` падало бы с кодом 2. Только на действиях — тоже
    мало: тогда падало бы `--env test_cabinet keyword add`.

    У листьев умолчание `SUPPRESS`: с обычным лист дописывает своё значение
    поверх корневого, и `--apply keyword add` получил бы `apply=False` от
    листа, который флага не видел."""
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


def pair(text: str):
    """Аргумент вида `Broader=NO`: имя и значение перечисления `YES`/`NO`."""
    name, _, value = text.partition("=")
    if not name or value.upper() not in ("YES", "NO"):
        raise argparse.ArgumentTypeError(
            f"ожидается «Имя=YES» или «Имя=NO», получено «{excerpt(text, 40)}»"
        )
    return name, value.upper()


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


def build_parser() -> Parser:
    parser = Parser(description="Запись фраз, минус-фраз и автотаргетинга.")
    add_common(parser, leaf=False)
    common = argparse.ArgumentParser(add_help=False)
    add_common(common, leaf=True)

    kinds = parser.add_subparsers(dest="kind", required=True)

    keyword = kinds.add_parser("keyword", help="ключевые фразы").add_subparsers(
        dest="action", required=True)

    made = keyword.add_parser("add", parents=[common], help="добавить фразы")
    made.add_argument("--group", type=int, required=True)
    made.add_argument("--phrase", action="append", default=[])
    made.add_argument("--autotargeting", action="store_true",
                      help="завести в группе ещё и автотаргетинг")
    made.add_argument("--category", action="append", type=pair, default=[],
                      metavar="ИМЯ=YES|NO")
    made.add_argument("--brand", action="append", type=pair, default=[],
                      metavar="ИМЯ=YES|NO")
    # Значение общее на все создаваемые фразы: разложить его по ним поимённо
    # аргументами нечем, а угадывать, какой из повторов какой фразе, — значит
    # поставить значение не туда. Разные значения ставит `keyword param`,
    # по фразе за вызов.
    made.add_argument("--param1", help="значение {param1} у создаваемых фраз")
    made.add_argument("--param2", help="значение {param2} у создаваемых фраз")

    text = keyword.add_parser("text", parents=[common],
                              help="заменить текст фразы")
    text.add_argument("--keyword", type=int, required=True)
    text.add_argument("value", help="новый текст фразы")

    values = keyword.add_parser(
        "param", parents=[common],
        help="значения {param1} и {param2} у существующих фраз")
    _selection(values)
    values.add_argument("--param1", help="значение {param1}")
    values.add_argument("--param2", help="значение {param2}")
    # Очистка — отдельной просьбой, а не пустым значением: поля необязательные,
    # `null` снимает значение, а пропуск поля означает «не трогать», и Директ
    # отвечает на эти две просьбы противоположным.
    values.add_argument("--clear", action="append", default=[],
                        choices=sorted(phrases.USER_PARAMS),
                        help="снять значение параметра")

    for method in sorted(phrases.LIFECYCLE_RU):
        step = keyword.add_parser(method, parents=[common],
                                  help=phrases.LIFECYCLE_RU[method])
        _selection(step)
        if method != "delete":
            field = phrases.TRANSITIONS[method][0]
            step.add_argument(
                "--expect-state", dest="state",
                required=method in phrases.UNDETERMINED,
                choices=phrases.STATES,
                help=f"какое значение поля {field} ожидать после операции"
                     + ("" if method in phrases.UNDETERMINED
                        else f"; умолчание {phrases.TRANSITIONS[method][1]}"))

    research = keyword.add_parser("research", parents=[common],
                                  help="отсев дублей и фраз без показов")
    research.add_argument("--phrase", action="append", default=[], required=True)
    research.add_argument("--region", action="append", type=int, default=[])

    auto = kinds.add_parser("autotargeting", help="автотаргетинг").add_subparsers(
        dest="action", required=True)
    started = auto.add_parser("add", parents=[common],
                              help="завести автотаргетинг в группе")
    started.add_argument("--group", type=int, required=True)
    started.add_argument("--category", action="append", type=pair, default=[],
                         metavar="ИМЯ=YES|NO")
    started.add_argument("--brand", action="append", type=pair, default=[],
                         metavar="ИМЯ=YES|NO")
    tuned = auto.add_parser("set", parents=[common],
                            help="категории и упоминание брендов")
    _selection(tuned)
    tuned.add_argument("--category", action="append", type=pair, default=[],
                       metavar="ИМЯ=YES|NO")
    tuned.add_argument("--brand", action="append", type=pair, default=[],
                       metavar="ИМЯ=YES|NO")
    for method in sorted(phrases.LIFECYCLE_RU):
        step = auto.add_parser(method, parents=[common],
                               help=f"{phrases.LIFECYCLE_RU[method]} автотаргетинга")
        _selection(step)
        if method != "delete":
            step.add_argument("--expect-state", dest="state",
                              required=method in phrases.UNDETERMINED,
                              choices=phrases.STATES)

    negative = kinds.add_parser("negative", help="минус-фразы").add_subparsers(
        dest="action", required=True)
    for level, key in (("campaign", "--campaign"), ("group", "--group")):
        step = negative.add_parser(level, parents=[common],
                                   help=f"минус-фразы {level}")
        step.add_argument(key, type=int, required=True)
        step.add_argument("--phrase", action="append", default=[])
        if level == "group":
            step.add_argument("--shared-set", action="append", type=int,
                              dest="shared")
        add_mode(step)
    from_report = negative.add_parser(
        "from-report", parents=[common],
        help="предложить минус-фразы по отчёту поисковых запросов")
    from_report.add_argument("--report", metavar="ФАЙЛ", required=True,
                             type=Path,
                             help="выгрузка SEARCH_QUERY_PERFORMANCE_REPORT в TSV")
    from_report.add_argument("--campaign", type=int)
    from_report.add_argument("--group", type=int)
    from_report.add_argument("--min-clicks", type=int, default=0, dest="min_clicks")
    from_report.add_argument("--min-cost", type=finite, default=0.0,
                             dest="min_cost")
    from_report.add_argument("--money-in-currency", action="store_true",
                             dest="money_in_currency",
                             help="деньги в отчёте в валюте, а не в микро — "
                                  "так выгружает интерфейс кабинета")
    from_report.add_argument("--phrase", action="append", default=[],
                             help="какие из предложенных запросов минусовать")
    # Режимов у этой команды нет, и это не упущение. Снять минус-фразу она не
    # может по построению: предлагаются только запросы, которые **не**
    # отсечены, а всё отсечённое из предложений выпадает — снимать было бы
    # нечего из того, что показано. Заменить список целиком она может, но это
    # означало бы стереть все прежние минус-фразы ради выбранных из отчёта —
    # разрушение под видом добавления. Обе операции делает `negative group` и
    # `negative campaign`, где список виден целиком.
    from_report.set_defaults(mode="add")

    library = kinds.add_parser("set", help="наборы минус-фраз").add_subparsers(
        dest="action", required=True)
    listing = library.add_parser("list", parents=[common],
                                 help="показать наборы")
    # Идентификаторы необязательны: без них показывается весь кабинет.
    # Перечисление возможно — вызов без `SelectionCriteria` проходит.
    listing.add_argument("--set", action="append", type=int, default=[])
    creating = library.add_parser("create", parents=[common],
                                  help="создать набор")
    creating.add_argument("--name", required=True)
    creating.add_argument("--phrase", action="append", default=[], required=True)
    changing = library.add_parser("update", parents=[common],
                                  help="изменить набор")
    # У правки набор **один**, и это не сужение возможностей, а отказ принимать
    # работу, которую команда всё равно отбросит: состав считается по свежему
    # чтению одного набора, а название у второго было бы тем же. Повторяемый
    # аргумент принимал бы три набора и правил первый, отчитываясь успехом.
    changing.add_argument("--set", type=int, required=True, metavar="НАБОР")
    changing.add_argument("--name")
    changing.add_argument("--phrase", action="append", default=[])
    add_mode(changing)
    removing = library.add_parser("delete", parents=[common],
                                  help="удалить наборы")
    removing.add_argument("--set", action="append", type=int, required=True)
    return parser


def _selection(step) -> None:
    step.add_argument("--keyword", action="append", type=int, default=[])
    step.add_argument("--group", type=int)
    step.add_argument("--campaign", type=int)


def research(client, account, accounts, args) -> int:
    """Отсев при подборе: дубли, кросс-минусовка и фразы без показов.

    Ничего не пишет. `KeywordsResearch` — предобработка списка перед записью:
    он склеивает дубли, разводит пересекающиеся фразы и говорит, по каким
    фразам показов не будет вовсе. Фраза без показов не даёт и данных, а
    значит нижней границы `BID-01` у неё нет."""
    lines = []
    outcome = client.call(
        "keywordsresearch", "deduplicate",
        {"Keywords": [{"Id": number, "Keyword": text}
                      for number, text in enumerate(args.phrase, 1)]},
        account=account,
        use_operator_units=lambda need=Limits.load().units_cost(
            "keywordsresearch", "deduplicate", len(args.phrase)): (
                accounts.use_operator_units(account, need=need)),
    ).result or {}
    rewritten = {one["Id"]: one["Keyword"] for one in outcome.get("Update") or []}
    dropped = set((outcome.get("Delete") or {}).get("Ids") or [])
    for number, text in enumerate(args.phrase, 1):
        if number in dropped:
            lines.append(f"дубль, уйдёт: {text}")
        elif number in rewritten:
            lines.append(f"развести: {text} → {rewritten[number]}")
    volume = {}
    if args.region:
        asked = list(dict.fromkeys(args.phrase))
        for item in (client.call(
                "keywordsresearch", "hasSearchVolume",
                {"SelectionCriteria": {"Keywords": asked,
                                       "RegionIds": [int(one) for one in args.region]},
                 "FieldNames": ["Keyword", "AllDevices"]},
                account=account,
                use_operator_units=lambda need=Limits.load().units_cost(
                    "keywordsresearch", "hasSearchVolume", len(asked)): (
                        accounts.use_operator_units(account, need=need)),
        ).result or {}).get("HasSearchVolumeResults") or []:
            volume[item.get("Keyword")] = item.get("AllDevices")
        for text, has in sorted(volume.items()):
            if has == "NO":
                lines.append(f"показов не будет: {text}")
    else:
        lines.append("Регионы не названы — наличие показов не проверялось: "
                     "`hasSearchVolume` требует `RegionIds`.")
    if args.json:
        say(json.dumps({"update": rewritten, "delete": sorted(dropped),
                        "volume": volume}, ensure_ascii=False))
    else:
        cache_module.outline(lines or ["Отсеивать нечего: дублей, пересечений "
                                       "и фраз без показов не нашлось."])
    return 0


def from_report(client, account, accounts, args) -> tuple:
    """Разбор отчёта и, если человек назвал фразы, задача на их запись."""
    # Ровно одна цель. Названные обе — это опечатка, и молча выбрать одну
    # значило бы исполнить половину просьбы, отчитавшись за целое.
    if (args.campaign is None) == (args.group is None):
        raise DirectFailure(
            "Минус-фразы идут либо кампании, либо группе — назовите ровно одно "
            "из `--campaign` и `--group`. Обе цели сразу означают опечатку, а "
            "выбранная за человека — половину просьбы, выданную за целое."
        )
    level = "campaign" if args.group is None else "group"
    service = phrases.CAMPAIGNS if level == "campaign" else phrases.GROUPS
    identifier = args.campaign if level == "campaign" else args.group
    read = ({"FieldNames": list(phrases.CAMPAIGN_FIELDS)} if level == "campaign"
            else {"FieldNames": list(phrases.GROUP_FIELDS)})
    record = read_one(client, account, accounts, service, read, identifier)
    current = items_of(record.get("NegativeKeywords"))
    # Отсеивается по **всем** действующим уровням, а пишется — только в свой.
    # Уровни складываются, и запрос, отсечённый минус-фразой кампании, из
    # группы уже не идёт: показанный свежим кандидатом, он врёт про цену
    # вопроса прошлым расходом, а записанный повторно тратит конечную длину
    # списка на работу, которая сделана.
    rows = within(read_report(args.report), level, identifier)
    warn("Столбцы конверсий: " + ", ".join(conversion_columns(rows))
         + ". Предлагаются только запросы с нулём по всем этим столбцам; "
           "пропуски и прочерки не считаются нулём.")
    found = suggest_negatives(rows,
                              min_clicks=args.min_clicks,
                              min_cost=args.min_cost,
                              known=effective_negatives(client, account,
                                                        accounts, level=level,
                                                        record=record),
                              in_micros=not args.money_in_currency)
    # Валюты в отчёте нет: столбца с ней Директ не выводит и параметра для неё
    # не предусмотрел (`REPORTS.md`, раздел 10). Берётся она из перечня
    # кабинетов, а он читает её у `Clients.get`.
    cabinet = accounts.by_login(account)
    for line in report_lines(found, cabinet.currency if cabinet else ""):
        warn(line)
    if not args.phrase:
        warn("Ни одна фраза не названа: команда показала цену вопроса и "
             "ничего не пишет. Что именно минусовать — выбирает человек, "
             "аргументом `--phrase`.")
        return None
    # Минусуется только предложенное. Иначе команда — обычная запись
    # минус-фраз, притворившаяся работой по отчёту: опечатка в `--phrase`
    # уходит в кабинет без единого показателя за ней, а запрос, отсеянный
    # порогом или конверсией, минусуется вопреки тому, за что его отсеяли.
    offered = {one["query"] for one in found}
    unknown = [one for one in args.phrase if one not in offered]
    if unknown:
        raise DirectFailure(
            "Этих запросов среди предложенных нет: "
            + "; ".join(f"«{excerpt(one, 48)}»" for one in unknown[:5])
            + (f" и ещё {len(unknown) - 5}" if len(unknown) > 5 else "")
            + ". Команда минусует то, что показала: у остального нет ни "
              "расхода, ни конверсий, за которые его выбрали, — а бывает, что "
              "его отсеяли именно за конверсии. Произвольную минус-фразу "
              "пишет `negative group` или `negative campaign`."
        )
    wanted = merged(current, args.phrase, args.mode)
    if wanted == current:
        raise DirectFailure("Список минус-фраз не меняется.")
    # Тот же список, из которого собрана замена, — и здесь: путь другой, а
    # запись та же, массивом целиком. Отчёт этого не меняет.
    source = {"NegativeKeywords": current}
    operation = (phrases.campaign_negative_operation(identifier, wanted,
                                                    was=source)
                 if level == "campaign"
                 else phrases.group_negative_operation(identifier, items=wanted,
                                                       was=source))
    where = "кампании" if level == "campaign" else "группы"
    return (f"минус-фразы {where} {identifier} по отчёту: было {len(current)}, "
            f"станет {len(wanted)}", [operation])


def run(args) -> int:
    client = Client.from_env(profile=args.env, account=args.account, warn=warn)
    accounts = Accounts.load(client, warn=warn)
    account = resolve_account(accounts, client, args.account)

    if args.kind == "keyword" and args.action == "research":
        return research(client, account, accounts, args)
    if args.kind == "set" and args.action == "list":
        return show_sets(client, account, accounts, args)

    notes = ()
    if args.kind == "keyword" and args.action == "add":
        title, operations = keyword_add_task(client, account, accounts, args)
        notes = added_address_notes(client, account, accounts, args)
    elif args.kind == "keyword" and args.action == "text":
        # Читается фраза до записи и здесь, а не только конвейером: правка
        # адресована обычной фразе, а по тому же идентификатору может лежать
        # автотаргетинг, которому текст менять нельзя вовсе. Отказ Директа
        # обошёлся бы в баллы и пришёл бы после предпросмотра.
        found = chosen_keywords(
            client, account, accounts,
            argparse.Namespace(keyword=[args.keyword], group=None,
                               campaign=None),
            choose=plain_keywords,
            said="текст автотаргетинга изменению не подлежит")
        title = f"правка текста фразы {args.keyword}"
        operations = [phrases.update_operation({args.keyword: args.value},
                                               found)]
        # Сказать вместе с вопросом: человек подтверждает правку фразы, а
        # получить может новую фразу с новым идентификатором — или ни одной.
        # Одним показом, а не двумя: обработчик согласия зовётся и в режиме
        # проверки, и печать перед прогоном была бы теми же словами дважды.
        notes = (REWRITE_NOTE,)
    elif args.kind == "keyword" and args.action == "param":
        # Адрес с подставленным значением показывается вместе с вопросом, а не
        # печатается до прогона: обработчик согласия зовётся и в режиме
        # проверки, и вторая печать была бы теми же словами дважды.
        title, operations, notes = params_task(client, account, accounts, args)
    elif args.kind == "keyword":
        found = chosen_keywords(client, account, accounts, args,
                                choose=plain_keywords,
                                said="это автотаргетинг, а не фраза")
        title = f"{phrases.LIFECYCLE_RU[args.action]}: фраз {len(found)}"
        operations = [phrases.lifecycle_operation(
            args.action, sorted(found), state=getattr(args, "state", None))]
    elif args.kind == "autotargeting":
        title, operations = autotargeting_task(client, account, accounts, args)
    elif args.kind == "negative" and args.action == "from-report":
        outcome = from_report(client, account, accounts, args)
        if outcome is None:
            return 0
        title, operations = outcome
    elif args.kind == "negative":
        title, operations = negative_target_task(client, account, accounts, args)
    else:
        title, operations = set_task(client, account, accounts, args)

    seen = []
    engine = Writer(client, account, accounts=accounts, apply=args.apply,
                    show=showing(seen=seen, quiet=args.json, notes=notes),
                    warn=warn)
    report = engine.run(Task(title, operations))
    return report_out(report, args, shown=bool(seen))


def all_sets(client, account, accounts) -> list:
    """Все наборы минус-фраз кабинета.

    Перечисляются вызовом **без** `SelectionCriteria`: пустой `SelectionCriteria`
    и любой критерий, кроме `Ids`, дают 8000, и
    отсюда прежнее «перечислить нельзя». Правило на деле другое: критерий,
    если он есть, обязан содержать `Ids`."""
    request = dict(phrases.shared_set_read())
    need = Limits.load().units_cost(phrases.SHARED_SETS, "get", None)
    return list(client.get_all(
        phrases.SHARED_SETS, request, account=account,
        use_operator_units=lambda: accounts.use_operator_units(
            account, need=need)))


def show_sets(client, account, accounts, args) -> int:
    """Что лежит в наборах минус-фраз. Читает, ничего не меняет.

    Без идентификаторов показывает весь кабинет: перечисление возможно, если
    `SelectionCriteria` в запросе нет вовсе. С идентификаторами — только их, и
    тогда пропажа названного — отказ."""
    if not args.set:
        found = all_sets(client, account, accounts)
    else:
        request = dict(phrases.shared_set_read())
        request["SelectionCriteria"] = {"Ids": [int(one) for one in args.set]}
        need = Limits.load().units_cost(phrases.SHARED_SETS, "get",
                                        len(args.set))
        found = list(client.get_all(
            phrases.SHARED_SETS, request, account=account,
            use_operator_units=lambda: (
                accounts.use_operator_units(account, need=need))))
        phrases.all_named([one.get("Id") for one in found], args.set,
                          "наборы минус-фраз")
    if args.json:
        say(json.dumps(found, ensure_ascii=False))
        return 0
    lines = []
    for item in found:
        items = items_of(item.get("NegativeKeywords"))
        lines.append(f"{item.get('Id')} · «{item.get('Name')}» · фраз "
                     f"{len(items)} · подключён: {item.get('Associated')}")
        lines.append("    " + ", ".join(str(one) for one in items))
    cache_module.outline(lines or ["Наборы не прочитались."])
    return 0


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
