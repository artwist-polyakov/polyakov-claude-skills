#!/usr/bin/env python3
"""Создание и изменение объявлений, групп и условий показа.
Команды читают актуальные данные и показывают полный план. --apply
выполняет запись и проверяет результат повторным чтением.
Примеры и порядок работы: references/CHANGES.md."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "lib"))

import cache as cache_module  # noqa: E402
import objects  # noqa: E402
import phrases  # noqa: E402
import policy as policies  # noqa: E402
import responsive  # noqa: E402
from accounts import Accounts, resolve_account  # noqa: E402
from config import (  # noqa: E402
    SKILL_DIR,
    DirectFailure,
    excerpt,
    preload_secrets,
    redact,
    short,
)
from direct import Client  # noqa: E402
from preferences import Preferences  # noqa: E402
from responsive import CLEAR, CLEARABLE, STRUCTURE, Kit  # noqa: E402
from writer import (NORMALIZED, UNEXPLAINED, UNLOGGED, UNVERIFIED,  # noqa: E402
                    Limits, Operation, Task, Writer, merged, showing, unrun)

TRANSITIONS = {
    "suspend": ("State", "SUSPENDED"),
    "resume": ("State", None),
    "archive": ("State", "ARCHIVED"),
    "unarchive": ("State", None),
    "moderate": ("Status", "MODERATION"),
}

WHY_UNDETERMINED = {
    "unarchive": "разархивация возвращает объявление туда, откуда оно уходило "
                 "в архив, а откуда именно — из API не видно",
    "resume": "снятие остановки показывает итог вместе с кампанией: в "
              "остановленной кампании объявление станет OFF, а не ON",
}

UNDETERMINED = frozenset(name for name, (_, value) in TRANSITIONS.items()
                         if value is None)
assert UNDETERMINED == set(WHY_UNDETERMINED), (
    "у каждой недетерминированной операции должна быть названа причина"
)

STATES = ("ON", "OFF", "SUSPENDED", "OFF_BY_MONITORING", "ARCHIVED")
STATUSES = ("DRAFT", "MODERATION", "PREACCEPTED", "ACCEPTED", "REJECTED")
ALLOWED_STATES = {"State": STATES, "Status": STATUSES}

LIFECYCLE_RU = {
    "suspend": "остановка",
    "resume": "возобновление",
    "archive": "архивация",
    "unarchive": "разархивация",
    "moderate": "отправка на модерацию",
    "delete": "удаление",
}


def warn(text: str) -> None:
    print(redact(text), file=sys.stderr)


def say(text: str) -> None:
    print(redact(text))


def kit_lines(identifier, kit: Kit) -> list:
    """Комплект целиком, элемент за элементом.

    Строкой предпросмотра движка здесь не обойтись: он показывает изменение
    одной строкой, а комплект — это до семи заголовков и трёх текстов, и
    подтверждать их по укороченному описанию значит подтверждать не глядя.
    Комплект передаётся целиком, поэтому и показывается целиком."""
    lines = [f"объявление {identifier} · {kit.summary()}"]
    for number, title in enumerate(kit.titles, 1):
        lines.append(f"    заголовок {number} ({len(title)}): {title}")
    for number, text in enumerate(kit.texts, 1):
        lines.append(f"    текст {number} ({len(text)}): {text}")
    if kit.images is not CLEAR:
        for one in kit.images:
            lines.append(f"    изображение: {one}")
    if kit.videos is not CLEAR:
        for one in kit.videos:
            lines.append(f"    видео: {one}")
    if kit.href:
        lines.append(f"    ссылка: {kit.href}")
    if kit.display_url_path:
        lines.append(f"    отображаемая ссылка: {kit.display_url_path}")
    return lines


GROUP_FIELDS = ("Id", "CampaignId", "Name", "RegionIds", "TrackingParams",
                "NegativeKeywords", "NegativeKeywordSharedSetIds", "Type",
                "Subtype", "Status")

GROUP_TEXTS = {
    "Name": "AdGroup.Name",
    "TrackingParams": "AdGroup.TrackingParams",
}

PHRASE_PATHS = phrases.GROUP_PHRASE_PATHS


def _fits_negative(items) -> None:
    """Что не так с минус-фразами группы — по справочнику и до запроса."""
    phrases.fits_negative(items, "adgroup")


def _fits_shared_sets(shared) -> None:
    """Сколько наборов минус-фраз допускает группа — по справочнику лимитов.

    Проверяется по справочнику, а не объявлением правила конвейеру: в разделе
    `collections` справочника такого имени нет, а число живёт в `keywords`, и
    правило на несуществующее имя движок отвергает — команда падала бы до
    первого запроса."""
    phrases.fits_shared_sets(shared, "adgroup")


def group_rules(item: dict) -> dict:
    """Правила справочника для полей, которые в элементе **есть**."""
    return {path: rule for path, rule in GROUP_TEXTS.items() if path in item}


def group_read() -> dict:
    return {"FieldNames": list(GROUP_FIELDS)}


def group_add_operation(campaign, name, regions, *, negative=(), shared=(),
                        tracking=None):
    """Создание группы: регионы, минус-фразы, наборы минус-фраз."""
    _fits_regions(regions, required=True)
    item = {"CampaignId": campaign, "Name": name,
            "RegionIds": [int(one) for one in regions]}
    if negative:
        _fits_negative(negative)
        item["NegativeKeywords"] = {"Items": list(negative)}
    if shared:
        _fits_shared_sets(shared)
        item["NegativeKeywordSharedSetIds"] = {"Items": [int(one) for one in shared]}
    if tracking:
        item["TrackingParams"] = tracking
    changes = [policies.Change(object_id=name, what=what,
                               field=field, after=item[field], service="группа")
               for field, what in (("CampaignId", "кампания"),
                                   ("Name", "название"),
                                   ("RegionIds", "регионы показа"),
                                   ("NegativeKeywords", "минус-фразы"),
                                   ("NegativeKeywordSharedSetIds",
                                    "наборы минус-фраз"),
                                   ("TrackingParams", "параметры URL"))
               if field in item]
    return Operation(
        responsive.GROUPS, "add", params_key="AdGroups", items=[item],
        labels=[name], changes=changes, read=group_read(),
        search=("Name", {"CampaignIds": [int(campaign)]}),
        texts=group_rules(item), phrases=PHRASE_PATHS,
    )


def _fits_regions(regions, *, required: bool) -> None:
    """Что не так с географией показов — до запроса, а не отказом Директа."""
    if not regions:
        if required:
            raise DirectFailure(
                "Не названы регионы показа. Пустой `RegionIds` Директ "
                "отвергает кодом 5005; «все регионы» задаётся нулём, а не "
                "пустым списком."
            )
        return
    values = [int(one) for one in regions]
    said = []
    if all(one < 0 for one in values):
        said.append("названы только минус-регионы: показывать было бы негде")
    if 0 in values and any(one < 0 for one in values):
        said.append("«все регионы» (0) несовместимы с минус-регионами")
    repeated = sorted({one for one in values if values.count(one) > 1})
    if repeated:
        said.append(f"регион повторяется: {', '.join(str(one) for one in repeated)}")
    both = sorted({abs(one) for one in values
                   if one < 0 and abs(one) in values})
    if both:
        said.append(f"минус-регион совпадает с регионом показа: "
                    f"{', '.join(str(one) for one in both)}")
    if said:
        raise DirectFailure(
            "Геотаргетинг задан неправильно — Директ отвергнет его кодом "
            "5120: " + "; ".join(said) + "."
        )


def group_update_operation(group, *, name=None, regions=None, tracking=None):
    """Правка существующей группы через конвейер.

    Список из одного «создание» оставлял работающую группу без правки, а смена
    географии у работающей группы — операция обычная, в том числе массовая."""
    item = {"Id": group}
    said = []
    if name is not None:
        item["Name"] = name
        said.append(("Name", "название"))
    if regions:
        _fits_regions(regions, required=False)
        item["RegionIds"] = [int(one) for one in regions]
        said.append(("RegionIds", "регионы показа"))
    if tracking is not None:
        item["TrackingParams"] = tracking
        said.append(("TrackingParams", "параметры URL"))
    if not said:
        raise DirectFailure(
            "Не сказано, что менять в группе. Запись без изменения прошла бы "
            "конвейер целиком и отчиталась успехом, ничего не сделав."
        )
    changes = [policies.Change(object_id=group, what=what,
                               field=field, after=item[field], service="группа")
               for field, what in said]
    return Operation(
        responsive.GROUPS, "update", params_key="AdGroups", items=[item],
        changes=changes, read=group_read(), texts=group_rules(item),
        phrases=PHRASE_PATHS,
    )


def group_delete_operation(ids: list):
    """Удаление группы: `AdGroups.delete`."""
    changes = [policies.Change(object_id=one, what=LIFECYCLE_RU["delete"],
                               service="группа")
               for one in ids]
    return Operation(
        responsive.GROUPS, "delete", selection="Ids",
        items=[{"Id": one} for one in ids], changes=changes,
        read=group_read(),
    )


CAMPAIGN_PLACEHOLDER = "ИМЯ_КАМПАНИИ"

UTM_TEMPLATE = (
    "utm_source=ya&utm_medium=cpc&utm_term={keyword}"
    "&utm_campaign=" + CAMPAIGN_PLACEHOLDER +
    "&utm_content=ph:{keyword}|phid:{phrase_id}|m:{match_type}"
    "|mk:{matched_keyword}|pt:{position_type}|p:{position}|g:{gbid}"
    "|a:{ad_id}|ret:{retargeting_id}|c:{campaign_id}|reg:{region_name}"
    "|s:{source}"
)

UTM_CONTENT = "utm_content"

CAMPAIGN_TRACKING_FIELDS = {
    "UnifiedCampaignFieldNames": ["TrackingParams"],
    "TextCampaignFieldNames": ["TrackingParams"],
    "DynamicTextCampaignFieldNames": ["TrackingParams"],
    "SmartCampaignFieldNames": ["TrackingParams"],
}

CAMPAIGN_TRACKING_BODY = {
    "UNIFIED_CAMPAIGN": "UnifiedCampaign",
    "TEXT_CAMPAIGN": "TextCampaign",
    "DYNAMIC_TEXT_CAMPAIGN": "DynamicTextCampaign",
    "SMART_CAMPAIGN": "SmartCampaign",
}

CAMPAIGN_WITHOUT_TRACKING = ("MOBILE_APP_CAMPAIGN", "CPM_BANNER_CAMPAIGN")

NAME_SCHEMES = {
    "translit": "транслит-ядро продукта плюс различитель сегмента",
    "cyrillic": "то же кириллицей",
    "or-query": "OR-последовательность запросов группы",
    "other": "свой формат кабинета, ни один из трёх",
}

# записи о выборе нет и формат спрашивается заново.
NAME_SCHEME_SHOWN = {
    "translit": "zeiss-teo-020b · рядом с именами кампаний по алфавиту, "
                "ищется глазами",
    "cyrillic": "цейс-тео-020б · читается быстрее, но расходится с алфавитом "
                "имён кампаний",
    "or-query": "(карл зейс|carl zeiss) (тео 020б|teo 020b) · точность "
                "полная, но глазами по списку не читается",
    "other": "имя собирает человек · скилл о схеме больше не спрашивает",
}


def health_checks() -> dict:
    """Набор проверок здоровья: `references/health_checks.json`."""
    if not _CHECKS:
        path = SKILL_DIR / "references" / "health_checks.json"
        try:
            _CHECKS.append(json.loads(path.read_text(encoding="utf-8")))
        except (OSError, ValueError) as exc:
            raise DirectFailure(
                f"Справочник проверок {short(path)} не читается: {exc}. Без "
                f"него неизвестно, в каких полях живут ссылки, а разметка, "
                f"поставленная поверх непрочитанных меток, — это двойная "
                f"разметка."
            ) from None
    return _CHECKS[0]


_CHECKS = []


def required_utm_keys() -> list:
    """Ключи, без которых атрибуции нет, — из того же справочника.

    «Параметры URL заданы» и «разметка есть» — разные вещи: в поле вполне
    может лежать `ref=direct`, и правило, считающее любой параметр разметкой,
    промолчит о кампании, у которой атрибуции нет вовсе."""
    return list((health_checks().get("utm") or {}).get("required_keys") or [])


def link_fields() -> dict:
    """Имена полей чтения, несущих адреса: `{набор имён: [поле]}` по сервисам."""
    known = {"ads": objects.AD_TYPE_FIELDS,
             "adgroups": objects.GROUP_TYPE_FIELDS}
    reads = {"Ads.get": "ads", "AdGroups.get": "adgroups"}
    asked = {"ads": {}, "adgroups": {}}
    for name, body in (health_checks().get("derived") or {}).items():
        if not body.get("carries_urls"):
            continue
        services = [reads[one] for one in body.get("reads") or []
                    if one in reads]
        for source in body.get("sources") or []:
            structure, _, field = source.partition(".")
            enum = f"{structure}FieldNames"
            for service in services:
                if field in (known[service].get(enum) or ()):
                    asked[service].setdefault(enum, [])
                    if field not in asked[service][enum]:
                        asked[service][enum].append(field)
    return asked


def parts_of(text: str) -> list:
    """Строка параметров URL → список пар «ключ, значение».

    `None` на месте значения означает ключ без `=` вовсе: пустое значение и
    отсутствие знака — разные записи, и складывать их нельзя. Справочник
    считает ключ с пустым значением отсутствующим, но это правило разметки, а
    не разбора, и живёт оно у `marks_of`."""
    pairs = []
    for chunk in (text or "").split("&"):
        if not chunk:
            continue
        key, sign, value = chunk.partition("=")
        pairs.append((key.strip(), value if sign else None))
    return pairs


def content_pairs(value) -> list:
    """`utm_content` → список пар «ключ, макрос» (`ph:{keyword}`)."""
    pairs = []
    for chunk in (value or "").split("|"):
        if not chunk:
            continue
        key, sign, macro = chunk.partition(":")
        pairs.append((key.strip(), macro if sign else None))
    return pairs


def marks_of(text: str) -> dict:
    """Метки разметки в строке параметров: `utm_*` с непустым значением.

    Пустое значение считается отсутствием ключа — так решает справочник
    (`health_checks.json`, `utm.required_comment`): строка
    `utm_source=&utm_medium=` содержит имена и не даёт ни источника, ни канала."""
    return {key: value for key, value in parts_of(text)
            if key.startswith("utm_") and value}


def link_query(href: str) -> str:
    """Параметры адреса: то, что после `?` и **до** якоря."""
    return (href or "").partition("#")[0].partition("?")[2]


def link_marks(href: str) -> str:
    """Схема, зашитая в ссылку: **только** метки `utm_*`, в порядке адреса."""
    return "&".join(f"{key}={value}" for key, value
                    in parts_of(link_query(href))
                    if key.startswith("utm_") and value)


def scheme_of(text: str, campaign_name: str) -> str:
    """Строка параметров как **схема**: имя кампании заменено заполнителем."""
    if not text or not campaign_name:
        return text or ""
    chunks = text.split("&")
    for number, chunk in enumerate(chunks):
        key, sign, value = chunk.partition("=")
        if key == "utm_campaign" and sign and value == campaign_name:
            chunks[number] = f"{key}={CAMPAIGN_PLACEHOLDER}"
    return "&".join(chunks)


BREAKS_MARKUP = "&#?=% \t\n"


def applied_scheme(scheme: str, campaign_name: str) -> str:
    """Схема, готовая к записи: на месте заполнителя — имя кампании."""
    if CAMPAIGN_PLACEHOLDER not in scheme:
        return scheme
    if not campaign_name:
        raise DirectFailure(
            "В схеме разметки стоит заполнитель имени кампании, а имя не "
            "прочитано. Записать заполнитель значило бы разметить группу "
            "строкой «ИМЯ_КАМПАНИИ» — так и уедет в аналитику."
        )
    broken = sorted({one for one in campaign_name if one in BREAKS_MARKUP})
    if broken:
        shown = ", ".join(f"«{one}»" if one.strip() else "пробел"
                          for one in broken)
        raise DirectFailure(
            f"Имя кампании «{excerpt(campaign_name, 60)}» рвёт разметку: в "
            f"нём {shown}. Имя подставляется в utm_campaign целиком и без "
            f"изменений, и строка после такого символа до аналитики "
            f"не доедет — «{shown}» обрубает её или начинает новый параметр. "
            f"Директ такую строку примет, перечитывание вернёт её той же, и "
            f"сверка сойдётся: поймать это можно только здесь. Лечится "
            f"переименованием кампании — вместе с её разметкой, — "
            f"а не экранированием: экранированное имя перестаёт совпадать с "
            f"именем в отчётах."
        )
    chunks = scheme.split("&")
    for number, chunk in enumerate(chunks):
        key, sign, value = chunk.partition("=")
        if key == "utm_campaign" and sign and value == CAMPAIGN_PLACEHOLDER:
            chunks[number] = f"{key}={campaign_name}"
    return "&".join(chunks)


def scheme_differences(scheme: str, template: str = UTM_TEMPLATE) -> list:
    """Чем схема отличается от шаблона: перечень фактов.

    Именно фактов, а не оценок: «`utm_source` равен `yandex`, а в шаблоне
    `ya`» проверяется машинно, «схема хуже» — нет. Что с расхождением делать,
    решает пользователь; функция показывает различия без изменения схемы.
    её попутно."""
    mine = dict(parts_of(scheme))
    theirs = dict(parts_of(template))
    required = required_utm_keys()
    said = []
    for key, value in theirs.items():
        if key not in mine:
            said.append(f"нет ключа {key}"
                        + (" — без него атрибуции нет" if key in required
                           else ""))
        elif mine[key] != value and key != UTM_CONTENT:
            said.append(f"{key}: «{excerpt(mine[key], 40)}», "
                        f"а в шаблоне «{excerpt(value, 40)}»")
    for key in mine:
        if key not in theirs:
            said.append(f"лишний ключ {key}")
    if UTM_CONTENT in mine and UTM_CONTENT in theirs:
        said += content_differences(mine[UTM_CONTENT], theirs[UTM_CONTENT])
    return said


def content_differences(mine: str, theirs: str) -> list:
    """То же внутри `utm_content`: пары «ключ:макрос».

    Отдельным разбором, потому что расхождение здесь другого рода. Кабинет с
    `q` вместо `mk` размечен полностью — атрибуция у него в порядке, чинить
    нечего, — и промолчать о нём значило бы дать человеку узнать о второй
    схеме из собственного отчёта."""
    ours, given = dict(content_pairs(mine)), dict(content_pairs(theirs))
    said = []
    for key, macro in given.items():
        if key not in ours:
            said.append(f"в {UTM_CONTENT} нет пары {key}")
        elif ours[key] != macro:
            said.append(f"в {UTM_CONTENT} у {key} макрос "
                        f"«{excerpt(ours[key], 40)}», а в шаблоне «{macro}»")
    for key in ours:
        if key not in given:
            said.append(f"в {UTM_CONTENT} лишняя пара {key}")
    return said


def name_scheme_problem(name: str, scheme: str):
    """Чем имя расходится с выбранной схемой, или `None`."""
    cyrillic = any("а" <= one.lower() <= "я" or one.lower() == "ё"
                   for one in name)
    if scheme == "translit" and cyrillic:
        return ("схема имени — транслит, а в имени есть кириллица")
    if scheme == "cyrillic" and not cyrillic:
        return ("схема имени — кириллица, а кириллических букв в имени нет")
    if scheme == "or-query" and "|" not in name:
        return ("схема имени — OR-последовательность запросов, а разделителя "
                "«|» в имени нет ни одного")
    return None


class Markup:
    """Действующая разметка кабинета — то, что прочитано **до** сборки задачи."""

    def __init__(self, campaign=None):
        self.campaign_id = campaign
        self.campaigns = {}
        self.groups = []
        self.links = []
        self.unread = []

    def campaign(self, number) -> dict:
        return self.campaigns.get(number) or {}

    def schemes(self) -> dict:
        """Схемы, найденные в кабинете: `{схема: [где стоит]}`."""
        found = {}
        for number, record in sorted(self.campaigns.items()):
            if not marks_of(record.get("Tracking")):
                continue
            scheme = scheme_of(record["Tracking"], record.get("Name") or "")
            found.setdefault(scheme, []).append(f"кампания {number}")
        for record in self.groups:
            if not marks_of(record.get("Tracking")):
                continue
            owner = self.campaign(record.get("CampaignId")).get("Name") or ""
            scheme = scheme_of(record["Tracking"], owner)
            found.setdefault(scheme, []).append(f"группа {record.get('Id')}")
        owner = self.campaign(self.campaign_id).get("Name") or ""
        for where, href, _ in self.marked_links():
            found.setdefault(scheme_of(link_marks(href), owner),
                             []).append(where)
        return found

    def marked_links(self) -> list:
        """Ссылки с метками `utm_*`: тройки «где, адрес, группы»."""
        return [one for one in self.links if marks_of(link_query(one[1]))]

    def doubled_links(self) -> list:
        """Ссылки, у которых разметка стоит **и** над ними."""
        above = bool(marks_of(self.campaign(self.campaign_id).get("Tracking")))
        marked = {record.get("Id") for record in self.groups
                  if marks_of(record.get("Tracking"))}
        return [(where, href) for where, href, owners in self.marked_links()
                if above or (marked & set(owners or ()))]

    def parametrised(self) -> list:
        """Где параметры URL заданы, а разметки в них нет.

        Случай отдельный и молчания не заслуживает: в поле лежит `ref=direct`
        или служебная метка партнёра, поле непустое — и правило, считающее
        любой параметр разметкой, промолчало бы о кампании, у которой
        атрибуции нет вовсе (`health_checks.json`, раздел `utm`)."""
        where = [(f"кампания {number}", record.get("Tracking"))
                 for number, record in sorted(self.campaigns.items())]
        where += [(f"группа {record.get('Id')}", record.get("Tracking"))
                  for record in self.groups]
        return [(one, value) for one, value in where
                if value and not marks_of(value)]


def read_cabinet_markup(client, account, accounts, campaign) -> Markup:
    """Параметры URL всех кампаний кабинета — и только они, одним вызовом."""
    seen = Markup(campaign)
    need = Limits.load().units_cost("campaigns", "get")
    for item in client.get_all("campaigns", {
            "SelectionCriteria": {},
            "FieldNames": ["Id", "Name", "Type"],
            **CAMPAIGN_TRACKING_FIELDS}, account=account,
            use_operator_units=lambda: accounts.use_operator_units(
                account, need=need)):
        body = CAMPAIGN_TRACKING_BODY.get(item.get("Type")) or ""
        seen.campaigns[item.get("Id")] = {
            "Name": item.get("Name"),
            "Type": item.get("Type"),
            "Tracking": ((item.get(body) or {}).get("TrackingParams")
                         if body else None),
            "Level": _tracking_level(item.get("Type")),
        }
    return seen


def read_markup(client, account, accounts, campaign) -> Markup:
    """Прочитать действующую разметку: параметры URL и метки в ссылках."""
    seen = read_cabinet_markup(client, account, accounts, campaign)
    if campaign not in seen.campaigns:
        raise DirectFailure(
            f"Кампания {campaign} не прочиталась: её нет, она в другом "
            f"кабинете, закрыта правами или переведена в специальный архив. "
            f"Создавать в ней группу нельзя — ни имени для разметки, ни типа "
            f"кампании мы не знаем."
        )
    _read_group_markup(client, account, accounts, campaign, seen)
    _read_link_markup(client, account, accounts, campaign, seen)
    return seen


def _tracking_level(kind):
    """Есть ли у типа своё поле параметров URL: `True`, `False` или `None`."""
    if kind in CAMPAIGN_TRACKING_BODY:
        return True
    if kind in CAMPAIGN_WITHOUT_TRACKING:
        return False
    return None


def _read_group_markup(client, account, accounts, campaign, seen) -> None:
    """Параметры URL и адреса магазина у групп кампании."""
    need = Limits.load().units_cost("adgroups", "get")
    fields = link_fields()["adgroups"]
    try:
        for item in client.get_all("adgroups", {
                "SelectionCriteria": {"CampaignIds": [int(campaign)]},
                "FieldNames": ["Id", "CampaignId", "Name", "TrackingParams"],
                **fields}, account=account,
                use_operator_units=lambda: accounts.use_operator_units(
                    account, need=need)):
            seen.groups.append({"Id": item.get("Id"),
                                "CampaignId": item.get("CampaignId"),
                                "Name": item.get("Name"),
                                "Tracking": item.get("TrackingParams")})
            for enum, names in fields.items():
                body = item.get(enum[:-len("FieldNames")]) or {}
                for field in names:
                    if body.get(field):
                        seen.links.append((f"группа {item.get('Id')}",
                                           body[field], (item.get("Id"),)))
    except DirectFailure as failure:
        seen.unread.append(f"параметры URL групп кампании {campaign} — "
                           f"{failure}")


def _read_link_markup(client, account, accounts, campaign, seen) -> None:
    """Посадочные ссылки объявлений кампании и адреса их быстрых ссылок."""
    fields = link_fields()["ads"]
    need = Limits.load().units_cost(responsive.SERVICE, "get")
    sets = {}
    try:
        for item in client.get_all(responsive.SERVICE, {
                "SelectionCriteria": {"CampaignIds": [int(campaign)]},
                "FieldNames": ["Id", "AdGroupId"], **fields}, account=account,
                use_operator_units=lambda: accounts.use_operator_units(
                    account, need=need)):
            for enum, names in fields.items():
                body = item.get(enum[:-len("FieldNames")]) or {}
                for field in names:
                    value = body.get(field)
                    if not value:
                        continue
                    if field == "SitelinkSetId":
                        sets.setdefault(value, set()).add(
                            item.get("AdGroupId"))
                    else:
                        seen.links.append(
                            (f"объявление {item.get('Id')}", value,
                             (item.get("AdGroupId"),)))
    except DirectFailure as failure:
        seen.unread.append(f"ссылки объявлений кампании {campaign} — "
                           f"{failure}")
        return
    if sets:
        _read_sitelinks(client, account, accounts, sets, seen)


def _read_sitelinks(client, account, accounts, sets, seen) -> None:
    """Адреса быстрых ссылок: у товарного и каталожного объявления они
    единственные адреса, по которым уходит трафик."""
    need = Limits.load().units_cost("sitelinks", "get")
    wanted = dict(sets)
    try:
        for item in client.get_all("sitelinks", {
                "SelectionCriteria": {
                    "Ids": [int(one) for one in sorted(sets)]},
                "FieldNames": ["Id", "Sitelinks"]}, account=account,
                use_operator_units=lambda: accounts.use_operator_units(
                    account, need=need)):
            if item.get("Id") not in wanted:
                continue
            owners = tuple(sorted(one for one in wanted[item["Id"]]
                                  if one is not None))
            for link in item.get("Sitelinks") or []:
                if link.get("Href"):
                    seen.links.append(
                        (f"быстрая ссылка набора {item.get('Id')}",
                         link["Href"], owners))
    except DirectFailure as failure:
        seen.unread.append(f"адреса быстрых ссылок наборов "
                           f"{', '.join(str(one) for one in sorted(sets)[:5])}"
                           f" — "
                           f"{failure}")


def settings_for(account: str) -> Preferences:
    """Запись настроек кабинета — то, что скилл про кабинет помнит.

    Отдельной функцией, потому что каталог у записи один на скилл, а набору
    нужен свой: подменяется здесь **фабрика**, а не сам класс, и в прогоне
    работает настоящая запись, просто в песочнице."""
    return Preferences(account, warn=warn)


def chosen_name_scheme(args, remembered):
    """Использовать указанную или сохранённую схему имени; иначе оставить имя как есть."""
    known = remembered.recall("group_name_scheme")
    if args.name_scheme:
        return args.name_scheme, args.name_scheme != known
    if known:
        if known not in NAME_SCHEMES:
            warn(f"Для кабинета запомнена схема имени «{excerpt(known, 40)}», "
                 f"а этой версии она незнакома: имя записывается как названо, "
                 f"расхождение со схемой не проверяется.")
        return known, False
    return "other", False


def name_notes(name: str, scheme: str, remembered) -> list:
    """Что сказать человеку про имя группы при подготовке изменения."""
    said = NAME_SCHEMES.get(scheme) or "схема этой версии незнакома"
    if remembered.recall("group_name_scheme") != scheme:
        when = "выбрана для этой задачи; --remember сохраняет схему кабинета"
    else:
        at = remembered.remembered_at("group_name_scheme")
        # Пустая отметка — не «не запомнено»: запись есть, а времени в ней не
        # разобралось, и различать это стоит (`preferences.remembered_at`).
        when = (f"запомнена {at}" if at else
                "запомнена, а когда именно — запись больше не говорит")
    lines = [f"Схема имени в этом кабинете — «{scheme}» ({said}); {when}."]
    problem = name_scheme_problem(name, scheme)
    if problem:
        lines.append(f"Имя расходится со схемой: {problem}. Записывается оно "
                     f"как названо — схему и имя выбирает человек, а не код.")
    return lines


def chosen_markup(args, markup, remembered, campaign):
    """Чем размечать группу: строка, схема, откуда взята и что запоминать."""
    known = markup.campaign(campaign)
    on_group = args.tracking_on_group or known.get("Level") is False
    if not on_group:
        if args.utm_scheme:
            raise DirectFailure(
                f"Названа схема разметки (--utm-scheme {args.utm_scheme}), а "
                f"на группу она не ставится: {_why_not_on_group(known)} "
                f"Уберите --utm-scheme или просите переопределение явно — "
                f"--tracking-on-group; чем размечать и где разметке жить, это "
                f"два разных решения."
            )
        return None, None, "", None
    record = remembered.recall("utm_scheme")
    if args.utm_scheme == "cabinet":
        scheme, source = cabinet_scheme(markup), "действующая схема кабинета"
    elif args.utm_scheme == "template":
        scheme, source = UTM_TEMPLATE, "шаблон UTM"
    elif record:
        scheme, source = record, "запись настроек кабинета"
    else:
        scheme, source = UTM_TEMPLATE, "шаблон UTM, запомненного выбора нет"
    fresh = (args.utm_scheme and scheme != record
             and not (record is None and scheme == UTM_TEMPLATE))
    offer = scheme if fresh else None
    return (applied_scheme(scheme, known.get("Name") or ""), scheme, source,
            offer)


def _why_not_on_group(known: dict) -> str:
    """Почему разметка не ставится на группу — одной фразой, по уровню.

    Случая два, и путать их нельзя. У знакомого типа своё поле есть, и правило
    Разметка размещается на кампании, когда у её типа есть параметры URL. Незнакомый тип не говорит ничего:
    есть ли у него поле, эта версия не знает, — и молчаливо разметить группу
    значило бы решить за человека там, где мы не знаем ответа."""
    if known.get("Level") is True:
        return ("у типа кампании своё поле параметров URL есть, и "
                "разметка живёт на кампании.")
    return (f"тип кампании «{excerpt(known.get('Type') or '—', 40)}» этой "
            f"версии незнаком, и есть ли у него своё поле параметров URL, она "
            f"не знает.")


def cabinet_scheme(markup) -> str:
    """Действующая схема кабинета — когда она одна.

    Схем в кабинете бывает несколько, и выбирать за человека, какая из них
    «настоящая», скилл не берётся: частота — не довод, а догадка. Тогда он
    называет найденное и отказывается, а размечает человек отдельной правкой."""
    found = markup.schemes()
    if not found:
        raise DirectFailure(
            "Перенести схему кабинета не из чего: разметки не нашлось ни у "
            "кампаний, ни у групп, ни в ссылках. Шаблон UTM задаётся "
            "аргументом --utm-scheme template."
        )
    if len(found) > 1:
        said = "; ".join(f"«{excerpt(scheme, 60)}» — {', '.join(where[:3])}"
                         for scheme, where in list(found.items())[:3])
        raise DirectFailure(
            f"В кабинете {len(found)} разных схем разметки, и какая из них "
            f"действующая, знает человек, а не скилл: {said}. Назовите "
            f"--utm-scheme template или заведите группу без параметров и "
            f"задайте их отдельной правкой, когда у неё появится номер: "
            f"group update --group <номер> --tracking-params «…»."
        )
    return next(iter(found))


def campaign_mark_notes(tracking: str, campaign_name) -> list:
    """Чем `utm_campaign` в готовой строке расходится с именем кампании."""
    value = dict(parts_of(tracking)).get("utm_campaign")
    if not value or not campaign_name or value == campaign_name:
        return []
    if "{" in value:
        # Кабинет размечает `utm_campaign` макросом — подставляет его Директ,
        # и сравнивать с именем нечего.
        return []
    return [f"В разметке utm_campaign = «{excerpt(value, 60)}», а кампания "
            f"называется «{excerpt(campaign_name, 60)}». Записывается как "
            f"есть: свести несколько кампаний в один срез аналитики — "
            f"законный ход, а не ошибка. Если это не он, схема сохранена без "
            f"заполнителя {CAMPAIGN_PLACEHOLDER}."]


def other_schemes(markup) -> dict:
    """Схемы кабинета, отличные от шаблона UTM: `{схема: [где стоит]}`."""
    return {one: where for one, where in markup.schemes().items()
            if one != UTM_TEMPLATE}


def markup_read_notes(markup, campaign) -> list:
    """Что прочитано и какие схемы в кабинете нашлись — без уровня записи."""
    where = (f"групп кампании {campaign} — {len(markup.groups)}, ссылок — "
             f"{len(markup.links)}" if campaign is not None
             else "групп и ссылок не спрашивали — кампании ещё нет")
    lines = [f"Прочитана действующая разметка: кампаний "
             f"{len(markup.campaigns)}, {where}."]
    for one in markup.unread:
        lines.append(f"Прочитать не удалось: {one} Расхождение названо по "
                     f"тому, что прочиталось.")
    found = markup.schemes()
    others = other_schemes(markup)
    if not found:
        lines.append(
            "Разметки в кабинете нет: меток utm_* не нашлось "
            + ("ни у кампаний, ни у групп кампании, ни в прочитанных ссылках."
               if campaign is not None
               else "в параметрах URL кампаний кабинета; групп и ссылок здесь "
                    "не спрашивали."))
    unknown = [number for number, record in sorted(markup.campaigns.items())
               if record.get("Level") is None]
    if unknown:
        said = ", ".join(str(one) for one in unknown[:3])
        lines.append(
            f"Параметры URL не спрашивались у {len(unknown)} кампаний: тип "
            f"этой версии незнаком — {said}"
            f"{' и другие' if len(unknown) > 3 else ''}. Про их разметку "
            f"здесь не сказано ничего.")
    for one, where in list(others.items())[:3]:
        said = "; ".join(scheme_differences(one)[:6]) or "порядок ключей"
        lines.append(f"Кабинет размечен иначе ({', '.join(where[:3])}"
                     f"{' и другие' if len(where) > 3 else ''}): {said}.")
    if len(others) > 3:
        lines.append(f"…и ещё схем: {len(others) - 3}.")
    for where, value in markup.parametrised()[:3]:
        lines.append(f"Параметры URL заданы, а разметки в них нет: {where} — "
                     f"«{excerpt(value, 60)}». Ключей "
                     f"{', '.join(required_utm_keys())} там не нашлось, и "
                     f"атрибуции у этих кликов нет.")
    return lines


def markup_notes(markup, campaign, tracking, scheme, source) -> list:
    """Разметка кабинета — человеку, до вопроса и до записи.

    Общая половина — у `markup_read_notes`; здесь то, что верно для записи
    **уровня группы**."""
    lines = markup_read_notes(markup, campaign)
    others = other_schemes(markup)
    marked = markup.marked_links()
    if marked:
        where, href = marked[0][0], marked[0][1]
        if tracking:
            price = ("Ссылки эти принадлежат другим группам, и параметры "
                     "новой группы их не трогают. Столкнутся они в ней: "
                     "объявление с меткой в адресе получит и её, и параметры "
                     "группы, а двойной разметки быть не должно.")
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
                f"параметрах кампании или своей группы: двойная разметка уже "
                f"есть, и эта запись её не создаёт и не чинит.")
    if tracking:
        lines.append(f"Параметры URL группы ({source}): "
                     f"{excerpt(tracking, 200)}")
        lines += campaign_mark_notes(tracking,
                                     markup.campaign(campaign).get("Name"))
    else:
        lines.append(f"Параметры URL группе не задаются: "
                     f"{_why_not_on_group(markup.campaign(campaign))} "
                     f"Переопределение на группе — аргумент "
                     f"--tracking-on-group.")
    if others:
        lines.append("Переход кабинета на одну схему — отдельная задача с "
                     "датой, и здесь он не делается.")
    return lines


def remember_after(report, remembered, pending, args) -> None:
    """Сохранить выбранные схемы только при явном флаге --remember."""
    if not pending or not (report.applied and report.ok):
        return
    if not getattr(args, "remember", False):
        warn("Схемы применены к этой задаче. Для сохранения схем кабинета "
             "используйте --remember при следующей записи.")
        return
    for key, value, label in pending:
        try:
            remembered.remember(key, value, agreed=True)
            warn(f"Для кабинета сохранено: {label}.")
        except DirectFailure as failure:
            warn(f"Запись в Директ выполнена, но сохранить {label} не удалось: {failure}")


def lifecycle_operation(method: str, ids: list, *, state=None):
    """Остановка, возобновление, архивация, разархивация, модерация, удаление."""
    gone = method == "delete"
    expect = None
    value = None
    if not gone:
        field, value = TRANSITIONS[method]
        value = state or value
        if value is None:
            raise DirectFailure(
                f"Операция «{LIFECYCLE_RU[method]}» не задаёт итогового "
                f"состояния сама: {WHY_UNDETERMINED[method]}. Назовите "
                f"ожидаемое состояние аргументом `--expect-state` — без него "
                f"сверка либо остановит пакет на верном переходе, либо примет "
                f"неверный."
            )
        expect = [{"Id": one, field: value} for one in ids]
    changes = [policies.Change(object_id=one,
                               what=LIFECYCLE_RU[method],
                               field=None if gone else TRANSITIONS[method][0],
                               after=None if gone else value,
                               service="объявление")
               for one in ids]
    return Operation(
        responsive.SERVICE, method, selection="Ids",
        items=[{"Id": one} for one in ids], expect=expect, changes=changes,
        read=responsive.read_params(), expect_gone=gone,
    )


TARGETS = "audiencetargets"
LISTS = "retargetinglists"

TARGET_FIELDS = ("Id", "AdGroupId", "CampaignId", "RetargetingListId",
                 "InterestId", "ContextBid", "StrategyPriority", "State")

# Срез условия ретаргетинга: по нему решается применимость.
LIST_FIELDS = ("Id", "Name", "Type", "Scope", "IsAvailable",
               "AvailableForTargetsInAdGroupTypes")

TARGETS_RU = {
    "add": "привязка условия",
    "delete": "отвязка условия",
    "suspend": "остановка условия",
    "resume": "возобновление условия",
}

TARGET_TRANSITIONS = {"suspend": "SUSPENDED", "resume": "ON"}
TARGET_STATES = ("ON", "SUSPENDED", "UNKNOWN")

RESUME_NOTE = (
    "Возобновление показов по условию возвращает трату: остановленное условие "
    "аудиторию не покупает, возобновлённое покупает снова, и ставка в сетях у "
    "него своя — ContextBid. Насколько вырастет расход, отсюда не видно: это "
    "аукцион, а не арифметика. Обратное тоже верно: State: ON у условия не "
    "означает, что показы пошли, — состояния кампании и группы это поле в "
    "себе не несёт."
)

CREATE_NOTE = (
    "Условия показа привяжутся вторым вызовом и вторым вопросом: "
    "AudienceTargets.add принимает идентификатор группы, а назначает его "
    "Директ — до создания группы такого запроса не существует. Отказ от "
    "второго вопроса оставит группу созданной и без условий."
)

UNFIT_SCOPE = "FOR_ADJUSTMENTS_ONLY"
UNFIT_TYPE = "AUDIENCE"


def targets_read() -> dict:
    return {"FieldNames": list(TARGET_FIELDS)}


def targets_max() -> int:
    """Предел числа условий на группу — из справочника, а не константой."""
    rule = (Limits.load().data.get("objects") or {}).get(
        "audience_targets_per_text_or_mobile_adgroup")
    if not isinstance(rule, int) or rule < 1:
        raise DirectFailure(
            "В справочнике лимитов нет предела условий показа на группу "
            "(`objects.audience_targets_per_text_or_mobile_adgroup`). Без "
            "него запись шла бы вслепую, а отказ приходил бы от Директа "
            "уже за баллы."
        )
    return rule


def _why_unfit(one: dict):
    """Почему это условие к группе не привяжется. `None` — привяжется."""
    if one.get("Scope") == UNFIT_SCOPE:
        return (f"область применения {UNFIT_SCOPE} — условие годится только "
                f"для корректировок ставок, нацеливанием оно не пользуется "
                f"вовсе (справочник RetargetingLists.get)")
    return None


def read_lists(client, account, accounts, ids) -> dict:
    """Условия ретаргетинга, которые собираются привязать: `{номер: срез}`."""
    need = Limits.load().units_cost(LISTS, "get", len(ids))
    found = {}
    for item in client.get_all(LISTS, {
            "SelectionCriteria": {"Ids": [int(one) for one in ids]},
            "FieldNames": list(LIST_FIELDS)}, account=account,
            use_operator_units=lambda: accounts.use_operator_units(
                account, need=need)):
        found[item["Id"]] = item
    return found


def targets_of(client, account, accounts, group) -> dict:
    """Состав условий у группы: `{привязка: условие ретаргетинга}`."""
    need = Limits.load().units_cost(TARGETS, "get")
    found = {}
    for item in client.get_all(TARGETS, {
            "SelectionCriteria": {"AdGroupIds": [int(group)]},
            "FieldNames": list(TARGET_FIELDS)}, account=account,
            use_operator_units=lambda: accounts.use_operator_units(
                account, need=need)):
        found[item["Id"]] = item.get("RetargetingListId")
    return found


def composition_problems(before: dict, after: dict, *, requested=(),
                         confirmed=(), detached=()) -> list:
    """Чем состав условий группы разошёлся с обещанным."""
    said = []
    detached = set(detached)
    for identifier, condition in sorted(before.items()):
        if identifier in detached:
            if identifier in after:
                said.append(f"привязка {identifier} осталась в группе")
            continue
        if identifier not in after:
            said.append(f"привязка {identifier} исчезла, хотя её не трогали")
        elif after[identifier] != condition:
            said.append(f"привязка {identifier}: условие сменилось "
                        f"{condition} → {after[identifier]}")
    asked = list(requested)
    rest = list(confirmed)
    for identifier in sorted(one for one in after if one not in before):
        value = after[identifier]
        if value in rest:
            rest.remove(value)
        if value in asked:
            asked.remove(value)
        else:
            said.append(f"появилась привязка {identifier} условия {value}, "
                        f"которой не просили")
    for value in rest:
        said.append(f"условие {value} к группе не привязалось")
    return said


def _worth_saying(conditions, known: dict) -> list:
    """Что сказать про условие, не отказывая: свойства, чей исход не замерен."""
    said = []
    for one in conditions:
        found = known.get(one) or {}
        if found.get("Type") == UNFIT_TYPE:
            said.append(
                f"Условие {one} типа {UNFIT_TYPE}: K-17 замерила отказ "
                f"Директа "
                f"кодом 6000 «Недопустимое условие в профиле пользователя, "
                f"используемого для нацеливания в группе текстово-графических "
                f"объявлений» — но на условии об интересах Крипты, а не на "
                f"этом. Берёт ли группа ЕПК прочие условия этого типа, не "
                f"проверено, и ответит на это Директ. Его отказ "
                f"поэлементный и уронит весь пакет целиком."
            )
        if found.get("IsAvailable") == "NO":
            said.append(
                f"Условие {one}: внутри есть недоступная цель или сегмент "
                f"(IsAvailable: NO) — отбирать оно будет не то, что задумано."
            )
    return said


def _fits_targets(conditions, known: dict, bound: dict) -> None:
    """Что не так с привязкой — до запроса, а не отказом Директа за баллы.

    Отказ Директа поэлементный, и один негодный элемент роняет пакет целиком:
    привязка десяти условий, из которых одно на интересах, не даёт девяти
    привязок, она даёт отказ."""
    said = []
    lost = [one for one in conditions if one not in known]
    if lost:
        said.append(
            "не прочитались условия "
            + ", ".join(str(one) for one in lost[:10])
            + ("…" if len(lost) > 10 else "")
            + ": их нет, они в другом кабинете или закрыты правами")
    repeated = sorted({one for one in conditions if conditions.count(one) > 1})
    if repeated:
        said.append(f"условие названо дважды: "
                    f"{', '.join(str(one) for one in repeated)} — "
                    f"`RetargetingListId` в группе уникален")
    already = {value: key for key, value in bound.items()}
    twice = [one for one in conditions if one in already]
    if twice:
        said.append("уже привязано: "
                    + "; ".join(f"условие {one} привязкой {already[one]}"
                                for one in twice))
    for one in conditions:
        why = _why_unfit(known.get(one) or {})
        if why:
            said.append(f"условие {one}: {why}")
    limit = targets_max()
    total = len(bound) + len(set(conditions))
    if total > limit:
        said.append(f"условий на группе стало бы {total} при пределе {limit} "
                    f"(справочник лимитов, `objects."
                    f"audience_targets_per_text_or_mobile_adgroup`)")
    if said:
        raise DirectFailure(
            "Условия показа к группе не привязываются: " + "; ".join(said)
            + ". Отказ Директа поэлементный, и один негодный элемент "
              "уронил бы весь пакет целиком."
        )


def targets_add_operation(group, conditions):
    """Привязка условий к группе: `AudienceTargets.add`."""
    labels = [f"{one} в группе {group}" for one in conditions]
    items, changes = [], []
    for one, label in zip(conditions, labels):
        items.append({"AdGroupId": int(group), "RetargetingListId": int(one)})
        changes.append(policies.Change(object_id=label, what="группа",
                                       field="AdGroupId", after=int(group),
                                       service="условие показа"))
        changes.append(policies.Change(object_id=label,
                                       what="условие ретаргетинга",
                                       field="RetargetingListId",
                                       after=int(one),
                                       service="условие показа"))
    return Operation(
        TARGETS, "add", params_key="AudienceTargets", items=items,
        labels=labels, changes=changes, read=targets_read(),
        search=("RetargetingListId", {"AdGroupIds": [int(group)]}),
    )


def targets_lifecycle_operation(method: str, targets: list, *, state=None):
    """Отвязка, остановка и возобновление условия показа."""
    gone = method == "delete"
    expect = None
    value = None
    if not gone:
        value = state or TARGET_TRANSITIONS[method]
        expect = [{"Id": one, "State": value} for one in targets]
    changes = [policies.Change(object_id=one, what=TARGETS_RU[method],
                               field=None if gone else "State",
                               after=None if gone else value,
                               service="условие показа")
               for one in targets]
    return Operation(
        TARGETS, method, selection="Ids",
        items=[{"Id": one} for one in targets], expect=expect, changes=changes,
        read=targets_read(), expect_gone=gone,
    )


MODERATION_NOTE = (
    "Модерация трогает не только объявление: статус его группы и кампании тоже "
    "сдвинется. Группа возвращается сама, когда объявление уходит в архив, "
    "кампания — нет. И удалить прошедшее модерацию объявление будет уже нельзя, "
    "только заархивировать."
)


def parents_of(client, account, accounts, ads: dict) -> dict:
    """Статусы групп и кампаний, которых коснётся модерация.

    Читаются до и после записи, чтобы показать человеку не обещание из
    справочника, а то, что случилось на самом деле."""
    groups = sorted({ad["AdGroupId"] for ad in ads.values() if ad.get("AdGroupId")})
    campaigns = sorted({ad["CampaignId"] for ad in ads.values()
                        if ad.get("CampaignId")})
    seen = {}
    for service, ids, said in (("adgroups", groups, "группа"),
                               ("campaigns", campaigns, "кампания")):
        if not ids:
            continue
        need = Limits.load().units_cost(service, "get", len(ids))
        for item in client.get_all(service, {
                "SelectionCriteria": {"Ids": list(ids)},
                "FieldNames": ["Id", "Status"]}, account=account,
                use_operator_units=lambda need=need: accounts.use_operator_units(
                    account, need=need)):
            seen[f"{said} {item['Id']}"] = item.get("Status")
    return seen


def stale_after_read(client, account, accounts, ads: dict) -> list:
    """Объявления, которые изменились с момента нашего чтения."""
    fresh = read_ads(client, account, accounts, ads=sorted(ads))
    said = []
    for number, ad in sorted(ads.items()):
        now = fresh.get(number)
        if now is None:
            said.append(f"{number}: объявление больше не читается")
        elif Kit.of(now) != Kit.of(ad):
            said.append(f"{number}: комплект изменился с момента чтения")
    return said


def note_side_effects(report, moved) -> None:
    """Побочные сдвиги — в наблюдения, а не в проблемы."""
    for one in moved:
        report.record(NORMALIZED, f"побочно сдвинулось: {one}")


def moved_parents(before: dict, after: dict) -> list:
    """Чем статусы родителей разошлись до и после записи."""
    return [f"{where}: {before.get(where)} → {after[where]}"
            for where in sorted(after)
            if before.get(where) != after[where]]


def read_ads(client, account, accounts, *, ads=(), group=None,
             campaign=None) -> dict:
    """Объявления, с которыми будем работать: `{идентификатор: ответ}`.

    Читается всегда обоими наборами имён полей. Отбор по кампании и по группе
    существует потому, что метода «отправить кампанию» у Директа нет: объявления
    кампании скилл выбирает сам."""
    criteria = {}
    if ads:
        criteria["Ids"] = [int(one) for one in ads]
    if group is not None:
        criteria["AdGroupIds"] = [int(group)]
    if campaign is not None:
        criteria["CampaignIds"] = [int(campaign)]
    if not criteria:
        raise DirectFailure(
            "Не сказано, с какими объявлениями работать: назовите `--ad`, "
            "`--group` или `--campaign`."
        )
    params = dict(responsive.read_params())
    params["SelectionCriteria"] = criteria
    need = Limits.load().units_cost(responsive.SERVICE, "get",
                                    len(ads) if ads else None)
    found = {}
    for item in client.get_all(responsive.SERVICE, params, account=account,
                               use_operator_units=lambda: (
                                   accounts.use_operator_units(account,
                                                               need=need))):
        found[item["Id"]] = item
    return found


def combinatorial(ads: dict) -> dict:
    """Только комбинаторные объявления — по содержимому, а не по имени типа.

    Ветки на `Type` тут нет: тип меняется сам в случайно выбранный день, и
    отбор по нему то включал бы объявление, то нет. Признак — прочитанная
    комбинаторная структура; она приходит ровно тогда, когда объявление
    комбинаторное, потому что читаем мы оба набора полей разом."""
    return {number: ad for number, ad in ads.items() if ad.get(STRUCTURE)}


def carriable(ads: dict) -> dict:
    """Объявления, у которых есть что переносить в комплект."""
    return {number: ad for number, ad in ads.items()
            if (ad.get("TextAd") or {}).get("Title2")}


def kits_after(ads: dict, *, titles=None, texts=None, images=None, videos=None,
               href=None, display_url_path=None, set_title=None,
               carry_title2=False, clear=()) -> dict:
    """Комплекты, какими они станут: `{идентификатор: Kit}`."""
    clear = _clearing(clear, href=href, display_url_path=display_url_path,
                      images=images, videos=videos)
    after = {}
    for identifier, ad in ads.items():
        kit = Kit.of(ad) if carry_title2 else _plain(ad)
        if titles is not None:
            kit = kit.but(titles=list(titles))
        if set_title:
            place, value = set_title
            if not 1 <= place <= len(kit.titles):
                raise DirectFailure(
                    f"Объявление {identifier}: заголовка {place} нет — их "
                    f"{len(kit.titles)}. Правка по несуществующему номеру "
                    f"дописала бы восьмой или молча не сделала ничего."
                )
            replaced = list(kit.titles)
            replaced[place - 1] = value
            kit = kit.but(titles=replaced)
        if texts is not None:
            kit = kit.but(texts=list(texts))
        if images is not None:
            kit = kit.but(images=list(images))
        if videos is not None:
            kit = kit.but(videos=[int(one) for one in videos])
        if href is not None:
            kit = kit.but(href=href)
        if display_url_path is not None:
            kit = kit.but(display_url_path=display_url_path)
        empty = [name for name in clear if not getattr(kit, name)]
        if len(empty) < len(clear):
            kit = kit.but(**{name: CLEAR for name in clear
                             if name not in empty})
        after[identifier] = kit
    return after


def changed_kits(kits: dict, ads: dict) -> dict:
    """Комплекты, которые правда отличаются от прочитанного."""
    return {number: kit for number, kit in kits.items()
            if number not in ads or kit != _plain(ads[number])}


def _clearing(clear, **asked) -> list:
    """Что очищать — именами полей `Kit`, с проверкой на противоречие.

    Имя аргумента команды отличается от имени поля только дефисами: перечень
    полей один, и второго для команды не заводится. Незнакомое имя сюда не
    доходит — его отсекает `choices` разбора аргументов, — но проверка стоит и
    здесь: библиотечный вызов мимо команды иначе молча не очистил бы ничего."""
    names = []
    for one in clear or ():
        name = str(one).replace("-", "_")
        if name not in CLEARABLE:
            known = sorted(field.replace("_", "-") for field in CLEARABLE)
            raise DirectFailure(
                f"Очищать «{excerpt(one, 32)}» скилл не умеет. `nillable` в "
                f"`ResponsiveAdUpdate` объявлены: {', '.join(known)}."
            )
        if asked.get(name) is not None:
            raise DirectFailure(
                f"Поле «{name.replace('_', '-')}» просят и заполнить, и "
                f"очистить. Это две разные правки, и какая из них останется в "
                f"кабинете, из команды не видно — назовите одну."
            )
        if name not in names:
            names.append(name)
    return names


def _plain(ad: dict) -> Kit:
    """Комплект таким, каким он был бы без переноса дополнительного заголовка."""
    return Kit.of(ad, carry=False)


def build_task(title: str, operations) -> Task:
    return Task(title, [one for one in operations if one is not None])


def engine_for(client, account, accounts, args, show) -> Writer:
    return Writer(client, account, accounts=accounts,
                  apply=args.apply, show=show, warn=warn)


def report_out(stages, args) -> int:
    """Отчёт команды: пары «прогон, показывали ли ему предпросмотр»."""
    stages = list(stages)
    ok = all(report.ok for report, _ in stages)
    if args.json:
        say(json.dumps(merged(report for report, _ in stages),
                       ensure_ascii=False))
    else:
        lines = []
        for report, shown in stages:
            said = report.lines()
            if shown:
                # Предпросмотр человек уже видел — в отчёте остаётся то, чем он
                # кончился: проверка, отказы, расхождения и сводка.
                said = said[len(report.preview):]
            lines += said
        cache_module.outline(lines, path=stages[0][0].journal)
    return 0 if ok else 1


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


def build_parser() -> Parser:
    parser = Parser(description="Запись комбинаторных объявлений и групп.")
    add_common(parser, leaf=False)
    common = argparse.ArgumentParser(add_help=False)
    add_common(common, leaf=True)

    kinds = parser.add_subparsers(dest="kind", required=True)

    ad = kinds.add_parser("ad", help="объявления").add_subparsers(
        dest="action", required=True)

    create = ad.add_parser("create", parents=[common],
                           help="создать комбинаторное объявление")
    create.add_argument("--group", type=int, required=True)
    create.add_argument("--title", action="append", default=[], required=True)
    create.add_argument("--text", action="append", default=[], required=True)
    create.add_argument("--image", action="append", default=[])
    create.add_argument("--video", action="append", default=[], type=int)
    create.add_argument("--href")
    create.add_argument("--display-url-path", dest="display_url_path")

    update = ad.add_parser("update", parents=[common],
                           help="заменить комплект целиком")
    _selection(update)
    update.add_argument("--title", action="append")
    update.add_argument("--text", action="append")
    update.add_argument("--image", action="append")
    update.add_argument("--video", action="append", type=int)
    update.add_argument("--href")
    update.add_argument("--display-url-path", dest="display_url_path")
    update.add_argument("--clear", action="append", default=[],
                        choices=sorted(one.replace("_", "-")
                                       for one in CLEARABLE),
                        help="очистить поле: передать null вместо значения")

    single = ad.add_parser("set-title", parents=[common],
                           help="заменить один заголовок комплекта")
    _selection(single)
    single.add_argument("place", type=int, help="номер заголовка, с единицы")
    single.add_argument("value", help="новый заголовок")

    carry = ad.add_parser("carry-title2", parents=[common],
                          help="перенести дополнительный заголовок в комплект")
    _selection(carry)

    for method in sorted(LIFECYCLE_RU):
        step = ad.add_parser(method, parents=[common],
                             help=LIFECYCLE_RU[method])
        _selection(step)
        if method != "delete":
            field = TRANSITIONS[method][0]
            step.add_argument(
                "--expect-state", dest="state",
                required=method in UNDETERMINED,
                choices=ALLOWED_STATES[field],
                help=f"какое значение поля {field} ожидать после операции"
                     + ("" if method in UNDETERMINED
                        else f"; умолчание {TRANSITIONS[method][1]}"))

    group = kinds.add_parser("group", help="группы").add_subparsers(
        dest="action", required=True)
    made = group.add_parser("create", parents=[common],
                            help="создать группу")
    made.add_argument("--campaign", type=int, required=True)
    made.add_argument("--name", required=True)
    made.add_argument("--region", action="append", type=int, default=[],
                      required=True)
    made.add_argument("--negative", action="append", default=[])
    made.add_argument("--shared-set", action="append", type=int, default=[],
                      dest="shared")
    made.add_argument("--condition", action="append", type=int, default=[],
                      help="условие ретаргетинга (RetargetingListId), которое "
                           "привязать к созданной группе")
    made.add_argument("--name-scheme", dest="name_scheme",
                      choices=sorted(NAME_SCHEMES),
                      help="схема имени группы; без неё берётся "
                           "запомненная для кабинета")
    made.add_argument("--utm-scheme", dest="utm_scheme",
                      choices=("template", "cabinet"),
                      help="чем размечать: шаблон UTM или действующая "
                           "схема кабинета; без него — запомненная схема, а "
                           "её нет — шаблон. Уровня не меняет: где разметке "
                           "жить, просит --tracking-on-group")
    made.add_argument("--tracking-on-group", dest="tracking_on_group",
                      action="store_true",
                      help="поставить параметры URL на группу, хотя у типа "
                           "кампании своё поле есть")
    changed = group.add_parser("update", parents=[common],
                               help="изменить группу")
    changed.add_argument("--group", type=int, required=True)
    changed.add_argument("--name")
    changed.add_argument("--region", action="append", type=int)
    changed.add_argument("--tracking-params", dest="tracking")

    removed = group.add_parser("delete", parents=[common],
                               help="удалить группу")
    removed.add_argument("--group", action="append", type=int, default=[],
                         required=True,
                         help="идентификатор группы; повторяется")

    targets = group.add_parser(
        "targets", help="условия показа группы").add_subparsers(
        dest="method", required=True)
    attach = targets.add_parser("add", parents=[common],
                                help=TARGETS_RU["add"])
    attach.add_argument("--group", type=int, required=True)
    attach.add_argument("--condition", action="append", type=int, default=[],
                        required=True,
                        help="условие ретаргетинга (RetargetingListId)")
    for method in ("delete", "suspend", "resume"):
        step = targets.add_parser(method, parents=[common],
                                  help=TARGETS_RU[method])
        _target_selection(step)
        if method != "delete":
            step.add_argument(
                "--expect-state", dest="state", choices=TARGET_STATES,
                help=f"какое значение поля State ожидать после операции; "
                     f"умолчание {TARGET_TRANSITIONS[method]}")
    return parser


def _selection(step) -> None:
    step.add_argument("--ad", action="append", type=int, default=[])
    step.add_argument("--group", type=int)
    step.add_argument("--campaign", type=int)


def _target_selection(step) -> None:
    """Отбор привязок: по их номерам либо по группе целиком.

    Номер привязки — не номер условия ретаргетинга, и здесь ждут именно
    первый: `AudienceTargets.delete` отбирает по `Ids` самих привязок."""
    step.add_argument("--target", action="append", type=int, default=[],
                      help="идентификатор привязки (условия нацеливания)")
    step.add_argument("--group", type=int,
                      help="все условия показа этой группы")


def chosen_ads(client, account, accounts, args, *, choose=None,
               said="") -> dict:
    """Объявления, попавшие под отбор и годные этой команде."""
    choose = choose or combinatorial
    found = read_ads(client, account, accounts, ads=args.ad,
                     group=args.group, campaign=args.campaign)
    mine = choose(found)
    lost = [one for one in (args.ad or []) if one not in found]
    if lost:
        raise DirectFailure(
            f"Не прочитались названные объявления: "
            f"{', '.join(str(one) for one in lost[:10])}"
            + ("…" if len(lost) > 10 else "")
            + ". Их нет, они в другом кабинете или закрыты правами. Записывать "
              "часть названного молча нельзя."
        )
    why = said or ("комбинаторной структуры в ответе нет, а другого пути "
                   "записи в скилле не существует")
    skipped = len(found) - len(mine)
    if skipped:
        warn(f"Пропущено объявлений: {skipped}. Причина: {why}.")
    if not mine:
        raise DirectFailure(
            f"Под отбор не попало ни одного годного объявления. Причина: "
            f"{why}. Записывать нечего."
        )
    return mine


def chosen_targets(client, account, accounts, args) -> dict:
    """Привязки, попавшие под отбор: `{идентификатор: прочитанное}`."""
    criteria = {}
    if args.target:
        criteria["Ids"] = [int(one) for one in args.target]
    if args.group is not None:
        criteria["AdGroupIds"] = [int(args.group)]
    if not criteria:
        raise DirectFailure(
            "Не сказано, с какими условиями показа работать: назовите "
            "`--target` или `--group`."
        )
    # Отбор по номерам ограничивает ответ их числом, отбор по группе — ничем,
    # кроме предела ответа. Оба раза оценка выходит сверху.
    need = Limits.load().units_cost(
        TARGETS, "get", len(args.target) if args.target else None)
    found = {}
    for item in client.get_all(TARGETS, {
            "SelectionCriteria": criteria,
            "FieldNames": list(TARGET_FIELDS)}, account=account,
            use_operator_units=lambda: accounts.use_operator_units(
                account, need=need)):
        found[item["Id"]] = item
    lost = [one for one in (args.target or []) if one not in found]
    if lost:
        raise DirectFailure(
            f"Не прочитались названные условия показа: "
            f"{', '.join(str(one) for one in lost[:10])}"
            + ("…" if len(lost) > 10 else "")
            + ". Их нет, они в другой группе или закрыты правами. Работать с "
              "частью названного молча нельзя."
        )
    if not found:
        raise DirectFailure(
            "Под отбор не попало ни одного условия показа: у этой группы их "
            "нет. Трогать нечего."
        )
    return found


def run_targets(client, account, accounts, args) -> int:
    """Условия показа: привязка, отвязка, остановка и возобновление.

    Своя ветка, а не общая с объявлениями: у привязки другое пространство
    идентификаторов, другой сервис и другой состав, который надо сверять после
    записи. Сведённые в одну ветвь, они начали бы отличаться условиями внутри
    неё — тем самым ветвлением, которого стоит избегать."""
    method = args.method
    if method == "add":
        conditions = list(args.condition)
        bound = targets_of(client, account, accounts, args.group)
        known = read_lists(client, account, accounts, set(conditions))
        _fits_targets(conditions, known, bound)
        for one in _worth_saying(conditions, known):
            warn(one)
        groups = [args.group]
        before = {args.group: bound}
        detached = ()
        requested = conditions
        title = (f"{TARGETS_RU['add']}: условий {len(conditions)} в группе "
                 f"{args.group}")
        operations = [targets_add_operation(args.group, conditions)]
        notes = []
    else:
        found = chosen_targets(client, account, accounts, args)
        groups = sorted({item["AdGroupId"] for item in found.values()
                         if item.get("AdGroupId")})
        before = {one: targets_of(client, account, accounts, one)
                  for one in groups}
        detached = sorted(found) if method == "delete" else ()
        requested = ()
        title = f"{TARGETS_RU[method]}: условий {len(found)}"
        operations = [targets_lifecycle_operation(
            method, sorted(found), state=getattr(args, "state", None))]
        notes = [RESUME_NOTE] if method == "resume" else []
        if method == "resume":
            # Сказать **до** вопроса: человек подтверждает возобновление, а
            # вместе с ним — обязательство тратить.
            warn(RESUME_NOTE)

    seen = []
    engine = engine_for(client, account, accounts, args,
                        showing(seen=seen, quiet=args.json, notes=notes))
    report = engine.run(build_task(title, operations))
    if report.applied:
        check_composition(engine, client, account, accounts, report,
                          groups=groups, before=before, title=title,
                          method=method, requested=requested,
                          detached=detached)
    return report_out([(report, bool(seen))], args)


def check_composition(engine, client, account, accounts, report, *, groups,
                      before, title, method, requested=(),
                      detached=()) -> None:
    """Сверка состава условий у группы — после записи и по факту."""
    said = []
    after = {}
    written = set(report.written)
    confirmed = requested if len(report.accepted) == len(requested) else ()
    for group in groups:
        try:
            after[group] = targets_of(client, account, accounts, group)
        except DirectFailure as failure:
            report.record(UNVERIFIED,
                f"состав условий группы {group} не перечитан — {failure}. Что "
                f"стало с группой, эта команда не знает; запись при этом "
                f"выполнена, повторять её нельзя."
            )
            continue
        said += [f"группа {group}: {one}" for one in composition_problems(
            before.get(group) or {}, after[group],
            requested=requested, confirmed=confirmed,
            detached=[one for one in detached if one in written])]
    for one in said:
        report.record(UNEXPLAINED, f"состав условий разошёлся с обещанным: {one}")
    for group in sorted(after):
        report.record(NORMALIZED,
            f"состав условий группы {group}: было "
            f"{len(before.get(group) or {})}, стало {len(after[group])}")
    try:
        engine.journal.record({
            "kind": "состав условий",
            "task": title,
            "method": method,
            "groups": list(groups),
            "before": {str(one): before.get(one) or {} for one in groups},
            "after": {str(one): after[one] for one in sorted(after)},
            "problems": said,
            "unread": [one for one in groups if one not in after] or None,
        })
    except DirectFailure as failure:
        report.record(UNLOGGED,
            f"состав условий сверен, но в журнал не попал — {failure}")


def run(args) -> int:
    client = Client.from_env(profile=args.env, account=args.account, warn=warn)
    accounts = Accounts.load(client, warn=warn)
    account = resolve_account(accounts, client, args.account)
    kits, title, operations = {}, "", []
    parents, found = None, {}
    conditions, notes, pending = [], [], []
    remembered = None

    if args.kind == "group":
        if args.action == "targets":
            return run_targets(client, account, accounts, args)
        if args.action == "delete":
            chosen = sorted(set(args.group))
            title = f"удаление: групп {len(chosen)}"
            operations = [group_delete_operation(chosen)]
        elif args.action == "create":
            remembered = settings_for(account)
            scheme, fresh = chosen_name_scheme(args, remembered)
            if fresh:
                pending.append(("group_name_scheme", scheme,
                                f"схему имени «{scheme}»"))
            notes += name_notes(args.name, scheme, remembered)
            markup = read_markup(client, account, accounts, args.campaign)
            tracking, utm, source, offer = chosen_markup(
                args, markup, remembered, args.campaign)
            if offer:
                pending.append(("utm_scheme", offer,
                                f"схему разметки «{excerpt(offer, 60)}»"))
            notes += markup_notes(markup, args.campaign, tracking, utm, source)
            title = f"создание группы «{args.name}»"
            operations = [group_add_operation(
                args.campaign, args.name, args.region,
                negative=args.negative, shared=args.shared, tracking=tracking)]
            conditions = list(args.condition)
            if conditions:
                known = read_lists(client, account, accounts, conditions)
                _fits_targets(conditions, known, {})
                for one in _worth_saying(conditions, known):
                    warn(one)
                warn(CREATE_NOTE)
                notes.append(CREATE_NOTE)
        else:
            title = f"правка группы {args.group}"
            operations = [group_update_operation(
                args.group, name=args.name, regions=args.region,
                tracking=args.tracking)]
    elif args.action == "create":
        kit = Kit(args.title, args.text, images=args.image, videos=args.video,
                  href=args.href, display_url_path=args.display_url_path)
        label = f"новое объявление в группе {args.group}"
        kits = {label: kit}
        title = f"создание объявления в группе {args.group}"
        operations = [responsive.add_operation(args.group, [kit], [label])]
    elif args.action in LIFECYCLE_RU:
        found = chosen_ads(client, account, accounts, args)
        title = f"{LIFECYCLE_RU[args.action]}: объявлений {len(found)}"
        if args.action == "moderate":
            # Сказать **до** вопроса: человек подтверждает отправку объявления,
            # а сдвинется сверх того ещё два объекта.
            warn(MODERATION_NOTE)
            parents = parents_of(client, account, accounts, found)
        operations = [lifecycle_operation(args.action, sorted(found),
                                          state=getattr(args, "state", None))]
    elif args.action == "carry-title2":
        found = chosen_ads(
            client, account, accounts, args, choose=carriable,
            said="дополнительного заголовка нет — переносить нечего")
        crowded = {number: responsive.crowded_out(ad)
                   for number, ad in found.items()
                   if responsive.crowded_out(ad)}
        if crowded:
            said = [f"объявление {number}: дополнительный заголовок "
                    f"«{title2}» переносить некуда — все "
                    f"{responsive.titles_max()} мест заняты, а в склейку он не "
                    f"помещается. Освободите место в комплекте."
                    for number, title2 in sorted(crowded.items())]
            for one in said:
                warn(one)
            if args.json:
                say(json.dumps(unrun("переносить некуда", problems=said),
                               ensure_ascii=False))
            return 1
        kits = changed_kits(kits_after(found, carry_title2=True), found)
        if not kits:
            said = ("Переносить нечего: дополнительный заголовок никуда не "
                    "добавился — он уже в комплекте либо пуст.")
            say(json.dumps(unrun(said, ok=True), ensure_ascii=False)
                if args.json else said)
            return 0
        title = f"перенос дополнительного заголовка: объявлений {len(kits)}"
        operations = [responsive.update_operation(kits)]
    else:
        found = chosen_ads(client, account, accounts, args)
        said = ""
        if args.action == "set-title":
            kits = kits_after(found, set_title=(args.place, args.value))
            shape = f"заголовок {args.place}"
            said = f"заголовок {args.place} у всех отобранных уже такой"
        else:
            asked = (args.title, args.text, args.image, args.video, args.href,
                     args.display_url_path)
            if all(one is None for one in asked) and not args.clear:
                raise DirectFailure(
                    "Не сказано, что менять в комплекте. Пустая правка "
                    "перешлёт его как есть — и отправит объявление на "
                    "модерацию заново, ничего не изменив."
                )
            kits = kits_after(
                found, titles=args.title, texts=args.text, images=args.image,
                videos=args.video, href=args.href,
                display_url_path=args.display_url_path, clear=args.clear)
            shape = "правка комплекта"
            said = ("отобранные объявления уже такие"
                    + (": очищаемые поля пусты" if args.clear else ""))
        kits = changed_kits(kits, found)
        if not kits:
            # Как и у переноса заголовка: ранний выход уважает `--json`, иначе
            # читающая stdout программа получит вместо объекта русскую фразу.
            said = f"Менять нечего: {said}."
            say(json.dumps(unrun(said, ok=True), ensure_ascii=False)
                if args.json else said)
            return 0
        title = f"{shape}: объявлений {len(kits)}"
        operations = [responsive.update_operation(kits)]

    if kits and found:
        # Последним действием перед прогоном: чем меньше окно, тем меньше в
        # него попадёт. Попавшее — отказ, а не тихая перезапись.
        stale = stale_after_read(client, account, accounts,
                                 {number: found[number] for number in kits
                                  if number in found})
        if stale:
            for one in stale:
                warn(f"Запись отменена, {one}. Комплект передаётся целиком, и "
                     f"собранный до чужой правки стёр бы её молча. Повторите "
                     f"команду — она перечитает объявление.")
            return 1

    seen = []
    engine = engine_for(client, account, accounts, args,
                        showing(seen=seen, quiet=args.json,
                                notes=([MODERATION_NOTE] if parents is not None else notes)
                                + [line for identifier, kit in kits.items()
                                   for line in kit_lines(identifier, kit)]))
    report = engine.run(build_task(title, operations))
    if parents is not None and report.applied:
        moved = moved_parents(parents, parents_of(client, account, accounts,
                                                  found))
        note_side_effects(report, moved)
        if moved:
            try:
                engine.journal.record({
                    "kind": "побочное",
                    "task": title,
                    "method": args.action,
                    "objects": sorted(found),
                    "moved": moved,
                })
            except DirectFailure as failure:
                report.record(UNLOGGED,
                    f"побочные сдвиги случились, но в журнал не попали — "
                    f"{failure}"
                )
    stages = [(report, bool(seen))]
    if conditions:
        stages.append(bind_after_create(client, account, accounts, args,
                                        report, conditions))
    code = report_out([one for one in stages if one is not None], args)
    remember_after(report, remembered, pending, args)
    return code


def bind_after_create(client, account, accounts, args, report, conditions):
    """После создания группы привязать условия отдельным запросом.
    Идентификатор группы приходит из API. При сбое второго шага сохранить
    в отчёте результат первого и не создавать группу повторно."""
    said = ", ".join(str(one) for one in conditions)
    if not report.applied:
        warn(f"Условия {said} привязались бы вторым вызовом после создания "
             f"группы. В проверке без записи его не существует: идентификатор "
             f"группы назначает Директ.")
        return None
    if not report.ok or len(report.written) != 1:
        warn(f"Условия {said} не привязаны: создание группы не прошло "
             f"чисто ({report.summary()}). Привязывать к группе, про "
             f"которую неясно, создалась ли она и такой ли, значит "
             f"достраивать неизвестное.")
        return None
    group = report.written[0]
    seen = []
    try:
        bound = targets_of(client, account, accounts, group)
        engine = engine_for(client, account, accounts, args,
                            showing(seen=seen, quiet=args.json))
        title = (f"{TARGETS_RU['add']}: условий {len(conditions)} в группе "
                 f"{group}")
        second = engine.run(build_task(
            title, [targets_add_operation(group, conditions)]))
    except DirectFailure as failure:
        report.record(UNVERIFIED,
            f"группа {group} создана, а условия {said} к ней привязать не "
            f"удалось — {failure}. Привязались они или нет, эта команда не "
            f"знает. Команду не повторяйте, она завела бы вторую такую же "
            f"группу; посмотрите состав и привяжите недостающее отдельно: "
            f"group targets add --group {group}"
            + "".join(f" --condition {one}" for one in conditions)
        )
        return None
    if second.applied:
        check_composition(engine, client, account, accounts, second,
                          groups=[group], before={group: bound},
                          title=title, method="add",
                          requested=conditions)
    return second, bool(seen)


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
        warn(str(failure))
        return 1
    except OSError as failure:
        warn(redact(f"Не удалось прочитать или записать файл: {failure}"))
        return 1


if __name__ == "__main__":
    sys.exit(main())
