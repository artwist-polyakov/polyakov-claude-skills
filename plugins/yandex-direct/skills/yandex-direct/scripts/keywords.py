#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""Ключевые фразы и автотаргетинг: ставки, состояния, настройки обеими формами."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# Строго до импортов из `lib`: каталог запускаемого файла стоит на пути
# импорта первым, и без этой строки `import cache` нашёл бы `scripts/cache.py`.
sys.path.insert(0, str(Path(__file__).resolve().parent / "lib"))

import incoming  # noqa: E402
import objects  # noqa: E402
from accounts import Accounts, Ambiguous, resolve_account  # noqa: E402
from cache import (  # noqa: E402
    Cache,
    add_arguments,
    human_age,
    outline,
    plural,
    signature,
    tsv,
)
from config import DirectFailure, excerpt, preload_secrets, redact, short  # noqa: E402
from direct import Client  # noqa: E402

# Соседняя команда, а не библиотека — см. тот же импорт в `adgroups.py`.
import campaigns as campaign_command  # noqa: E402

SHOWN = 10
JSON_LIMIT = 50


def say(text: str = "") -> None:
    """Единственный путь вывода: секреты вырезаются здесь, а не у вызывающих."""
    print(redact(text))


def warn(text: str) -> None:
    print(redact(text), file=sys.stderr)


# --------------------------------------------------------------------------
# Чтение
# --------------------------------------------------------------------------

def index_of(records: list) -> str:
    return tsv([objects.keyword_row(record) for record in records],
               objects.KEYWORD_COLUMNS)


def cache_name(criteria: dict) -> str:
    """Имя записи кэша по отбору — по тому же правилу, что и у групп."""
    campaigns = criteria.get("CampaignIds") or []
    groups = criteria.get("AdGroupIds") or []
    if len(campaigns) == 1 and not groups and not criteria.get("Ids"):
        return f"keywords-{campaigns[0]}"
    if len(groups) == 1 and not campaigns and not criteria.get("Ids"):
        return f"keywords-group-{groups[0]}"
    return f"keywords-{signature(criteria)}"


def read_keywords(cache: Cache, client, accounts, login: str, params: dict):
    """Фразы и автотаргетинги: из кэша, а при промахе — из API.

    Сторож стоит **до** обращения к кэшу: иначе запрос, собранный без одной из
    форм настроек, попал бы в кэш и отдавался бы полчаса, ни разу не дойдя до
    проверки."""
    objects.asks_autotargeting(params)

    def produce():
        # Сколько фраз в кампании, до ответа не знает никто: цена берётся по
        # пределу ответа — оценка сверху, как и всюду в скилле. Справочник
        # называет 3 балла за каждые 2000 фраз, и это цена запроса **со**
        # статистикой; команда её не запрашивает, значит оценка завышена — в ту
        # сторону, в какую и положено ошибаться.
        need = campaign_command.units_cost("keywords", "get")
        return client.get_all(
            "keywords", params, account=login,
            use_operator_units=lambda: accounts.use_operator_units(login,
                                                                   need=need),
        )

    return cache.through(cache_name(params["SelectionCriteria"]), "structure",
                         produce, index=index_of)


# --------------------------------------------------------------------------
# Отбор
# --------------------------------------------------------------------------

def chosen(records: list, args) -> list:
    """Отбор по прочитанному, а не запросом: фильтры не стоят баллов."""
    found = list(records)
    if args.state:
        found = [item for item in found if item.get("State") in args.state]
    if args.status:
        found = [item for item in found if item.get("Status") in args.status]
    if args.autotargeting:
        found = [item for item in found if objects.is_autotargeting(item)]
    if args.phrases:
        found = [item for item in found if not objects.is_autotargeting(item)]
    # Автотаргетинг идёт последним в своей группе: он один на группу, а фраз
    # бывает две сотни, и перечень, начинающийся со служебной строки, читается
    # хуже.
    found.sort(key=lambda item: (str(item.get("AdGroupId") or ""),
                                 objects.is_autotargeting(item),
                                 str(item.get("Keyword") or ""),
                                 item.get("Id") or 0))
    return found


def counted(records: list, field: str) -> dict:
    tally = {}
    for record in records:
        tally[record.get(field)] = tally.get(record.get(field), 0) + 1
    return tally


# --------------------------------------------------------------------------
# Сводка
# --------------------------------------------------------------------------

def keyword_line(record: dict, currency: str) -> str:
    row = objects.keyword_row(record, currency)
    said = "автотаргетинг" if row["autotargeting"] else excerpt(row["keyword"], 34)
    bids = " / ".join(part for part in (row["bid"], row["context_bid"]) if part)
    tail = f" · ставка {bids}" if bids else " · ставка не задана"
    rare = " · мало показов" if row["rarely_served"] else ""
    return (f"  {row['id']:<14} {said[:34]:<34} "
            f"{row['state_ru']} · {row['status_ru']}{tail}{rare}")


def settings_lines(record: dict) -> list:
    """Настройки автотаргетинга: включённое, а не весь перечень.

    Строка печатается и тогда, когда включённого нет: молчание здесь не
    отличить от «настройки не прочитались», а это разные вещи."""
    found = objects.autotargeting_of(record)
    if not found["read"]:
        return ["      настройки автотаргетинга не прочитались"]
    categories = objects.enabled(found["nested"]["Categories"], objects.CATEGORY_RU)
    brands = objects.enabled(found["nested"]["BrandOptions"], objects.BRAND_RU)
    lines = [f"      категории: {categories or 'все выключены'} · "
             f"бренды: {brands or 'все выключены'}"]
    for item in found["disagreements"]:
        lines.append(
            f"      формы расходятся: плоское {item['flat']}="
            f"{item['flat_value']} против вложенного {item['half']}."
            f"{item['field']}={item['nested_value']}"
        )
    return lines


def report(login: str, where: str, entry, found: list, total: int,
           currency: str, spent: int, export=None) -> None:
    age = f"из кэша, {human_age(entry.age)}" if entry.hit else "прочитано заново"
    lines = [f"{where} · {age}"]
    autos = [item for item in found if objects.is_autotargeting(item)]
    lines.append(f"Фраз {len(found) - len(autos)}"
                 + ("" if len(found) == total else f" из {total} строк выборки")
                 + f" · автотаргетингов {len(autos)}")
    rare = sum(1 for item in found
               if item.get("ServingStatus") == "RARELY_SERVED")
    if rare:
        lines.append(f"Мало показов: {rare}.")
    lines.append("")
    for record in found[:SHOWN]:
        lines.append(keyword_line(record, currency))
        if objects.is_autotargeting(record):
            lines.extend(settings_lines(record))
    if len(found) > SHOWN:
        lines.append(f"  … ещё {plural(len(found) - SHOWN, 'строка', 'строки', 'строк')}"
                     f" — в файле ниже или через grep по TSV рядом с ним")
    disagreeing = [item for item in autos
                   if objects.autotargeting_of(item)["disagreements"]]
    if autos:
        lines.append(
            f"Формы настроек согласованы у {len(autos) - len(disagreeing)} из "
            f"{len(autos)}. Плоская форма не выражает «узкие» вовсе — это "
            f"свойство формы, а не расхождение."
            if not disagreeing else
            f"Формы настроек расходятся у {len(disagreeing)} из {len(autos)}: "
            f"читателю одной плоской формы достаётся неверный ответ."
        )
    if spent:
        lines.append(f"Чтение стоило {campaign_command.units_said(spent)}.")
    lines.extend(campaign_command.export_line(export))
    outline(lines, path=entry.path, total=entry.count)


def as_json(login: str, where: str, entry, found: list, total: int,
            currency: str, export=None) -> dict:
    return {
        "account": login,
        "selection": where,
        "from_cache": entry.hit,
        "cache": None if entry.path is None else short(entry.path),
        "total": total,
        "matched": len(found),
        "autotargetings": sum(1 for item in found
                              if objects.is_autotargeting(item)),
        "states": counted(found, "State"),
        "statuses": counted(found, "Status"),
        "keywords": [dict(objects.keyword_row(record, currency),
                          autotargeting_settings=objects.autotargeting_of(record),
                          raw=record)
                     for record in found[:JSON_LIMIT]],
        "csv": None if export is None else str(export),
    }


# --------------------------------------------------------------------------
# Аргументы
# --------------------------------------------------------------------------

class Parser(argparse.ArgumentParser):
    """Ошибка аргументов — код 2, и без секретов в тексте."""

    def error(self, message: str):
        self.exit(2, redact(f"{self.prog}: {message}\n"))


def numbers(value: str, what: str, parser) -> list:
    """Перечень идентификаторов через запятую. Само правило — в `objects`.

    Здесь только перевод отказа в код возврата 2: разбор один на все команды,
    потому что опечатка в нём ошибается одинаково во всех трёх."""
    try:
        return objects.identifiers(value, what)
    except DirectFailure as failure:
        parser.error(str(failure))


def main(argv=None) -> int:
    # До разбора аргументов: argparse печатает негодное значение сам, и токен,
    # случайно попавший в командную строку, ушёл бы в stderr раньше, чем скилл
    # узнал бы, что это токен.
    preload_secrets()

    parser = Parser(description="Фразы и автотаргетинг: ставки, статусы, настройки.")
    parser.add_argument("--env", choices=("production", "test_cabinet"),
                        help="контур и набор переменных")
    parser.add_argument("--account", metavar="ЛОГИН",
                        help="логин кабинета; при отсутствии берётся активный")
    parser.add_argument("--campaign", metavar="ID|ЧАСТЬ_НАЗВАНИЯ",
                        type=incoming.campaign_selector,
                        help="кампания, чьи фразы читать")
    parser.add_argument("--group", metavar="СПИСОК",
                        help="идентификаторы групп через запятую")
    parser.add_argument("--keyword", metavar="СПИСОК",
                        help="идентификаторы фраз или автотаргетингов")
    parser.add_argument("--state", metavar="СПИСОК",
                        help=f"состояния: {', '.join(objects.KEYWORD_STATES)}")
    parser.add_argument("--status", metavar="СПИСОК",
                        help=f"статусы модерации: {', '.join(objects.KEYWORD_STATUSES)}")
    parser.add_argument("--autotargeting", action="store_true",
                        help="только автотаргетинги")
    parser.add_argument("--phrases", action="store_true",
                        help="только ключевые фразы")
    parser.add_argument("--csv", metavar="ФАЙЛ", type=Path,
                        help="выгрузить отобранное в файл")
    parser.add_argument("--json", action="store_true",
                        help="машиночитаемый вывод")
    add_arguments(parser)
    args = parser.parse_args(argv)

    if not (args.campaign or args.group or args.keyword):
        parser.error("назовите кампанию (--campaign), группы (--group) или "
                     "фразы (--keyword): Keywords.get без отбора не отвечает")
    if args.autotargeting and args.phrases:
        parser.error("--autotargeting и --phrases взаимоисключающие: вместе "
                     "они отбирают пустоту")
    args.state = (campaign_command.enum_list(args.state, list(objects.KEYWORD_STATES),
                                             "--state", parser)
                  if args.state else None)
    args.status = (campaign_command.enum_list(args.status, list(objects.KEYWORD_STATUSES),
                                              "--status", parser)
                   if args.status else None)
    args.group = numbers(args.group, "--group", parser) if args.group else None
    args.keyword = (numbers(args.keyword, "--keyword", parser)
                    if args.keyword else None)

    try:
        # Пределы того, что назвал человек, — до первого платного вызова.
        # Дальше идут список кабинетов, поиск кампании и справочник регионов,
        # и каждый стоит баллов; негодный перечень идентификаторов останется
        # негодным независимо от того, что они вернут.
        objects.fits_selection(objects.SERVICE_KEYWORDS,
                               objects.selection(objects.SERVICE_KEYWORDS, group_ids=args.group,
                                                 keyword_ids=args.keyword))

        client = Client.from_env(profile=args.env, warn=warn)
        accounts = Accounts.load(client, warn=warn)
        login = resolve_account(accounts, client, args.account)
        cache = Cache.from_args(args, account=login, warn=warn)

        where, currency = f"Кабинет {login}", ""
        campaign = None
        if args.campaign:
            slice_entry = campaign_command.read_slice(cache, client, accounts, login)
            campaign = campaign_command.one_campaign(slice_entry.data, args.campaign)
            row = campaign_command.row_of(campaign)
            currency = row["currency"]
            where = (f"Кабинет {login} · кампания {row['id']} · {row['name']} · "
                     f"{row['type_ru']}")

        params = objects.keyword_params(
            campaign_ids=[campaign["Id"]] if campaign is not None else None,
            group_ids=args.group, keyword_ids=args.keyword,
        )
        entry = read_keywords(cache, client, accounts, login, params)
        found = chosen(entry.data, args)

        if args.csv:
            campaign_command.dump_csv(
                [objects.keyword_row(item, currency) for item in found],
                objects.KEYWORD_COLUMNS, args.csv)
        spent = client.units.report()["spent"]

        if args.json:
            say(json.dumps(as_json(login, where, entry, found, entry.count,
                                   currency, args.csv), ensure_ascii=False))
        else:
            report(login, where, entry, found, entry.count, currency, spent,
                   args.csv)
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
