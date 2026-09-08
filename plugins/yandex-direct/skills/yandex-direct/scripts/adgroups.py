#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""Группы кампании: регионы, минус-фразы, статус модерации и «мало показов»."""

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
from config import DirectFailure, preload_secrets, redact, short  # noqa: E402
from direct import Client  # noqa: E402

import campaigns as campaign_command  # noqa: E402

SHOWN = 10

# То же для машиночитаемого вывода: он не про строки, а про объём.
JSON_LIMIT = 50

# Сколько регионов перечислять в строке группы.
INLINE = 3


def say(text: str = "") -> None:
    """Единственный путь вывода: секреты вырезаются здесь, а не у вызывающих."""
    print(redact(text))


def warn(text: str) -> None:
    print(redact(text), file=sys.stderr)


# --------------------------------------------------------------------------
# Чтение
# --------------------------------------------------------------------------

def index_of(records: list) -> str:
    return tsv([objects.group_row(record) for record in records],
               objects.GROUP_COLUMNS)


def cache_name(criteria: dict) -> str:
    """Имя записи кэша по отбору.

    Одна кампания — своё понятное имя: по нему запись находят глазами и сносят
    прицельно. Всё остальное — подпись отбора: перечень идентификаторов в имени
    файла не помещается, а два разных перечня под одним именем затирали бы друг
    друга."""
    campaigns = criteria.get("CampaignIds") or []
    if len(campaigns) == 1 and not criteria.get("Ids"):
        return f"adgroups-{campaigns[0]}"
    return f"adgroups-{signature(criteria)}"


def read_groups(cache: Cache, client, accounts, login: str, params: dict):
    """Группы: из кэша, а при промахе — из API.

    `use_operator_units` берётся у списка кабинетов и передаётся в вызов явно:
    клиент про кэш не знает, а вызов без решения на кабинете с исчерпанными
    баллами отказывает вместо того, чтобы заплатить баллами агентства.

    Передаётся не ответ, а способ его получить: выборка склеивается из страниц,
    и остаток кабинета меняется на каждой."""
    def produce():
        # Сколько групп в кампании, до ответа не знает никто — ни одна, ни
        # тысяча, — поэтому цена берётся по пределу ответа. Оценка выходит
        # сверху, и это её обязанность: заниженная роняет вызов отказом 152.
        need = campaign_command.units_cost("adgroups", "get")
        return client.get_all(
            "adgroups", params, account=login,
            use_operator_units=lambda: accounts.use_operator_units(login,
                                                                   need=need),
        )

    return cache.through(cache_name(params["SelectionCriteria"]), "structure",
                         produce, index=index_of)


def region_names(client, records: list, cache: Cache) -> dict:
    """Имена регионов, встреченных в группах.

    Запись кэша лежит в **корне**, а не в каталоге кабинета: справочник
    регионов один на всех, и копия его у каждого кабинета была бы копией одного
    и того же. Слой `dictionaries` хранит данные сутки.

    Спрашиваются только нужные номера, а не весь справочник: полный — 17 344
    записи и 2,3 МБ (замер 27.08.2026), и читать их с диска ради двух названий
    дороже, чем сходить за двумя. Цена вызова у обоих одна, один балл.

    Отказ не роняет чтение: без имён сводка беднее, но группы прочитаны.
    Сказать об этом надо вслух — молчание превратило бы отсутствие имён в
    отсутствие регионов."""
    asked = objects.region_ids(records)
    if not asked:
        return {}
    found = {}
    try:
        entry = cache.through(f"geo-regions-{signature(asked)}", "dictionaries",
                              lambda: client.geo_regions(region_ids=asked))
    except DirectFailure as failure:
        warn(f"Названия регионов прочитать не удалось. {failure}")
        return found
    for region in entry.data:
        if isinstance(region, dict):
            found[region.get("GeoRegionId")] = region.get("GeoRegionName")
    return found


# --------------------------------------------------------------------------
# Отбор
# --------------------------------------------------------------------------

def chosen(records: list, args) -> list:
    """Отбор по прочитанному, а не запросом: фильтры не стоят баллов."""
    found = list(records)
    if args.status:
        found = [item for item in found if item.get("Status") in args.status]
    if args.serving:
        found = [item for item in found
                 if item.get("ServingStatus") in args.serving]
    if args.rarely_served:
        found = [item for item in found
                 if item.get("ServingStatus") == "RARELY_SERVED"]
    found.sort(key=lambda item: (str(item.get("Name") or ""), item.get("Id") or 0))
    return found


def counted(records: list, field: str) -> dict:
    tally = {}
    for record in records:
        tally[record.get(field)] = tally.get(record.get(field), 0) + 1
    return tally


def tally_text(tally: dict, labels: dict) -> str:
    parts = [f"{labels.get(name, name)} {count}"
             for name, count in sorted(tally.items(), key=lambda pair: str(pair[0]))]
    return ", ".join(parts) if parts else "нет"


# --------------------------------------------------------------------------
# Сводка
# --------------------------------------------------------------------------

def regions_text(record: dict, names: dict) -> str:
    """Регионы группы словами. Ноль и минус — команды, а не идентификаторы."""
    found = objects.regions_of(record)

    def listed(ids):
        shown = ", ".join(str(names.get(item, item)) for item in ids[:INLINE])
        return shown + (f" и ещё {len(ids) - INLINE}" if len(ids) > INLINE else "")

    where = "все регионы" if found["everywhere"] else listed(found["included"])
    if not where:
        where = "не заданы"
    if found["excluded"]:
        where = f"{where} · кроме: {listed(found['excluded'])}"
    if found["restricted"]:
        # Запрет законодательный, а не настройка: снять его нельзя, и путать
        # его с выключенным регионом дорого — второй выключил человек.
        where = (f"{where} · показ запрещён законом: "
                 f"{listed(found['restricted'])}")
    return where


def group_line(record: dict) -> str:
    row = objects.group_row(record)
    return (f"  {row['id']:<12} {row['name'][:34]:<34} "
            f"{row['type_ru'][:22]:<22} {row['status_ru']} · {row['serving_ru']}")


def detail_line(record: dict, names: dict) -> str:
    """Вторая строка группы: регионы, минус-фразы, наборы, метки.

    Печатается всегда: пустая настройка и непрочитанная настройка выглядят
    одинаково только там, где о них молчат."""
    row = objects.group_row(record)
    parts = [f"регионы: {regions_text(record, names)}"]
    if row["negative_keywords"] or row["shared_sets"]:
        parts.append(f"минус-фраз {row['negative_keywords']}, "
                     f"наборов {row['shared_sets']}")
    if row["tracking_params"]:
        parts.append(f"метки: {row['tracking_params'][:40]}")
    return "      " + " · ".join(parts)


def report(login: str, campaign, entry, found: list, total: int, names: dict,
           spent: int, export=None) -> None:
    age = f"из кэша, {human_age(entry.age)}" if entry.hit else "прочитано заново"
    lines = [_where(login, campaign) + f" · {age}"]
    shown_of = "" if len(found) == total else f" из {total}"
    lines.append(
        f"Групп {len(found)}{shown_of} · "
        f"{tally_text(counted(found, 'Status'), objects.GROUP_STATUS_RU)}"
    )
    rare = sum(1 for item in found
               if item.get("ServingStatus") == "RARELY_SERVED")
    lines.append(f"Мало показов: {rare} из {len(found)}"
                 if found else "Мало показов: считать нечего")
    lines.append("")
    for record in found[:SHOWN]:
        lines.append(group_line(record))
        lines.append(detail_line(record, names))
    if len(found) > SHOWN:
        lines.append(f"  … ещё {plural(len(found) - SHOWN, 'группа', 'группы', 'групп')}"
                     f" — в файле ниже или через grep по TSV рядом с ним")
    if not names and objects.region_ids(found):
        lines.append("Названия регионов не прочитаны — показаны идентификаторы.")
    if spent:
        lines.append(f"Чтение стоило {campaign_command.units_said(spent)}.")
    lines.extend(campaign_command.export_line(export))
    outline(lines, path=entry.path, total=entry.count)


def _where(login: str, campaign) -> str:
    if campaign is None:
        return f"Кабинет {login} · группы названы поимённо"
    row = campaign_command.row_of(campaign)
    return f"Кабинет {login} · кампания {row['id']} · {row['name']} · {row['type_ru']}"


def as_json(login: str, campaign, entry, found: list, total: int,
            names: dict, export=None) -> dict:
    return {
        "account": login,
        "campaign": None if campaign is None else campaign.get("Id"),
        "from_cache": entry.hit,
        "cache": None if entry.path is None else short(entry.path),
        "total": total,
        "matched": len(found),
        "statuses": counted(found, "Status"),
        "serving_statuses": counted(found, "ServingStatus"),
        "region_names": {str(key): value for key, value in names.items()},
        "groups": [dict(objects.group_row(record),
                        regions=objects.regions_of(record),
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

    parser = Parser(description="Группы кампании: регионы, минус-фразы, статусы.")
    parser.add_argument("--env", choices=("production", "test_cabinet"),
                        help="контур и набор переменных")
    parser.add_argument("--account", metavar="ЛОГИН",
                        help="логин кабинета; при отсутствии берётся активный")
    parser.add_argument("--campaign", metavar="ID|ЧАСТЬ_НАЗВАНИЯ",
                        type=incoming.campaign_selector,
                        help="кампания, чьи группы читать")
    parser.add_argument("--group", metavar="СПИСОК",
                        help="идентификаторы групп через запятую")
    parser.add_argument("--status", metavar="СПИСОК",
                        help=f"статусы модерации: {', '.join(objects.GROUP_STATUSES)}")
    parser.add_argument("--serving", metavar="СПИСОК",
                        help=f"состояние показов: {', '.join(objects.SERVING_STATUSES)}")
    parser.add_argument("--rarely-served", action="store_true",
                        dest="rarely_served",
                        help="только группы со статусом «мало показов»")
    parser.add_argument("--csv", metavar="ФАЙЛ", type=Path,
                        help="выгрузить отобранное в файл")
    parser.add_argument("--json", action="store_true",
                        help="машиночитаемый вывод")
    add_arguments(parser)
    args = parser.parse_args(argv)

    if not args.campaign and not args.group:
        parser.error("назовите кампанию (--campaign) или группы (--group): "
                     "AdGroups.get без отбора не отвечает, а кабинет целиком "
                     "этой командой не читается")
    args.status = (campaign_command.enum_list(args.status, list(objects.GROUP_STATUSES),
                                              "--status", parser)
                   if args.status else None)
    args.serving = (campaign_command.enum_list(args.serving, list(objects.SERVING_STATUSES),
                                               "--serving", parser)
                    if args.serving else None)
    args.group = numbers(args.group, "--group", parser) if args.group else None

    try:
        # Пределы того, что назвал человек, — до первого платного вызова.
        # Дальше идут список кабинетов, поиск кампании и справочник регионов,
        # и каждый стоит баллов; негодный перечень идентификаторов останется
        # негодным независимо от того, что они вернут.
        objects.fits_selection(objects.SERVICE_GROUPS,
                               objects.selection(objects.SERVICE_GROUPS, group_ids=args.group))

        client = Client.from_env(profile=args.env, warn=warn)
        accounts = Accounts.load(client, warn=warn)
        login = resolve_account(accounts, client, args.account)
        cache = Cache.from_args(args, account=login, warn=warn)

        campaign = None
        if args.campaign:
            slice_entry = campaign_command.read_slice(cache, client, accounts, login)
            campaign = campaign_command.one_campaign(slice_entry.data, args.campaign)

        params = objects.group_params(
            campaign_ids=[campaign["Id"]] if campaign is not None else None,
            group_ids=args.group,
        )
        entry = read_groups(cache, client, accounts, login, params)
        found = chosen(entry.data, args)
        # Справочник регионов кабинету не принадлежит, поэтому и запись его
        # кэша — общая, в корне.
        names = region_names(client, found, Cache.from_args(args, warn=warn))

        if args.csv:
            campaign_command.dump_csv(
                [objects.group_row(record) for record in found],
                objects.GROUP_COLUMNS, args.csv)
        spent = client.units.report()["spent"]

        if args.json:
            say(json.dumps(as_json(login, campaign, entry, found, entry.count,
                                   names, args.csv), ensure_ascii=False))
        else:
            report(login, campaign, entry, found, entry.count, names, spent,
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
