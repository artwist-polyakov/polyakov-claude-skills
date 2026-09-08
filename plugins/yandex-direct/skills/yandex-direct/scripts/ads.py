#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""Объявления: полный комплект комбинаторного и поэлементная модерация."""

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

SHOWN = 8

JSON_LIMIT = 50

ELEMENTS = 10


def say(text: str = "") -> None:
    """Единственный путь вывода: секреты вырезаются здесь, а не у вызывающих."""
    print(redact(text))


def warn(text: str) -> None:
    print(redact(text), file=sys.stderr)


# --------------------------------------------------------------------------
# Чтение
# --------------------------------------------------------------------------

def index_of(records: list) -> str:
    limits = campaign_command.limits()
    return tsv([objects.ad_row(record, limits) for record in records],
               objects.AD_COLUMNS)


def cache_name(criteria: dict) -> str:
    """Имя записи кэша по отбору — по тому же правилу, что и у групп."""
    campaigns = criteria.get("CampaignIds") or []
    groups = criteria.get("AdGroupIds") or []
    if len(campaigns) == 1 and not groups and not criteria.get("Ids"):
        return f"ads-{campaigns[0]}"
    if len(groups) == 1 and not campaigns and not criteria.get("Ids"):
        return f"ads-group-{groups[0]}"
    return f"ads-{signature(criteria)}"


def read_ads(cache: Cache, client, accounts, login: str, params: dict):
    """Объявления: из кэша, а при промахе — из API.

    Сторож стоит **до** обращения к кэшу, а не перед сетевым вызовом. Иначе
    запрос, собранный без комплекта, попал бы в кэш один раз и потом отдавался
    бы полчаса, ни разу не дойдя до проверки."""
    objects.asks_responsive(params)

    def produce():
        # Сколько объявлений в кампании, до ответа не знает никто, поэтому цена
        # берётся по пределу ответа — оценка сверху, как и всюду в скилле.
        need = campaign_command.units_cost("ads", "get")
        return client.get_all(
            "ads", params, account=login,
            use_operator_units=lambda: accounts.use_operator_units(login,
                                                                   need=need),
        )

    return cache.through(cache_name(params["SelectionCriteria"]), "structure",
                         produce, index=index_of)


# --------------------------------------------------------------------------
# Отбор
# --------------------------------------------------------------------------

def chosen(records: list, args, limits) -> list:
    """Отбор по прочитанному, а не запросом: фильтры не стоят баллов."""
    found = list(records)
    if args.type:
        found = [item for item in found if item.get("Type") in args.type]
    if args.state:
        found = [item for item in found if item.get("State") in args.state]
    if args.status:
        found = [item for item in found if item.get("Status") in args.status]
    if args.incomplete:
        # Именно `is False`: у объявления, которому комплект неприменим,
        # ответ `None`, и «не полон» о нём — не наблюдение, а бессмыслица.
        found = [item for item in found
                 if objects.composition(item, limits)["complete"] is False]
    if args.rejected:
        found = [item for item in found
                 if objects.composition(item, limits)["rejected"]]
    found.sort(key=lambda item: (str(item.get("AdGroupId") or ""),
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

def kit_text(kit: dict) -> str:
    """Состав комплекта одной строкой — числами, а не признаком «полный».

    Там, где комплекта не бывает — у графического, товарного, медийного, — так
    и сказано. Напечатать «0×0 = 0 пар» значило бы объявить потерей то, чего у
    этого формата нет по устройству."""
    counts = kit["counts"]
    parts = ([f"{counts['titles']}×{counts['texts']} = "
              f"{plural(kit['combinations'], 'пара', 'пары', 'пар')}"]
             if kit["kit_applies"] else ["комплекта у этого типа нет"])
    for part, said in (("titles", "заголовков"), ("texts", "текстов")):
        if not kit["kit_applies"] and counts[part]:
            parts.append(f"{said} {counts[part]}")
    for part, said in (("images", "изобр."), ("videos", "видео"),
                       ("carousel", "карусель")):
        if counts[part]:
            parts.append(f"{said} {counts[part]}")
    return " · ".join(parts)


def missing_text(kit: dict) -> str:
    """Чего не хватает до полного комплекта. Пустая строка — не хватает нечего."""
    missing = kit["missing"]
    parts = []
    if missing.get("titles"):
        parts.append(plural(missing["titles"], "заголовка", "заголовков",
                            "заголовков"))
    if missing.get("texts"):
        parts.append(plural(missing["texts"], "текста", "текстов", "текстов"))
    return f"не хватает {' и '.join(parts)}" if parts else ""


def ad_line(record: dict, limits) -> str:
    row = objects.ad_row(record, limits)
    return (f"  {row['id']:<20} {row['type_ru'][:20]:<20} "
            f"{row['status_ru']} · {row['state_ru']}")


def detail_line(record: dict, limits) -> str:
    kit = objects.composition(record, limits)
    parts = [f"комплект {kit_text(kit)}"]
    lack = missing_text(kit)
    if lack:
        parts.append(lack)
    if kit["rejected"]:
        parts.append(f"отклонено элементов {len(kit['rejected'])}")
    return "      " + " · ".join(parts)


def report(login: str, where: str, entry, found: list, total: int, spent: int,
           limits, export=None, notes=()) -> None:
    age = f"из кэша, {human_age(entry.age)}" if entry.hit else "прочитано заново"
    lines = [f"{where} · {age}"]
    kinds = counted(found, "Type")
    combo = kinds.get("RESPONSIVE_AD", 0)
    kits = [objects.composition(item, limits) for item in found]
    whole = sum(1 for kit in kits if kit["complete"] is True)
    applies = sum(1 for kit in kits if kit["kit_applies"])
    lines.append(f"Объявлений {len(found)}"
                 + ("" if len(found) == total else f" из {total}")
                 + f" · комбинаторных {combo} · комплект полон у {whole} "
                 f"из {applies}, у кого он вообще бывает")
    lines.append(f"Читалось с {objects.RESPONSIVE}FieldNames — без него "
                 f"комбинаторное вернулось бы одним заголовком.")
    lines.append("")
    for record in found[:SHOWN]:
        lines.append(ad_line(record, limits))
        lines.append(detail_line(record, limits))
    if len(found) > SHOWN:
        lines.append(f"  … ещё {plural(len(found) - SHOWN, 'объявление', 'объявления', 'объявлений')}"
                     f" — в файле ниже или через grep по TSV рядом с ним")
    short_kits = [kit for kit in kits if kit["complete"] is False]
    if short_kits:
        lines.append(f"Недозаполненных комплектов {len(short_kits)} — вход для "
                     f"`scripts/audit_combinatorial.py`. Приговором это не является: комплект из "
                     f"трёх сильных заголовков бывает собран осознанно.")
    rejected = sum(len(kit["rejected"]) for kit in kits)
    if rejected:
        lines.append(f"Отклонённых элементов {rejected}. Отказ элемента не "
                     f"доказывает ни того, что он выбыл из ротации, ни того, "
                     f"что объявление показывается: прежний комплект может "
                     f"показываться, а может и нет.")
    lines.extend(notes)
    if spent:
        lines.append(f"Чтение стоило {campaign_command.units_said(spent)}.")
    lines.extend(campaign_command.export_line(export))
    outline(lines, path=entry.path, total=entry.count)


def element_lines(kit: dict, part: str, said: str) -> list:
    """Элементы одной части комплекта — со своим статусом у каждого.

    Статус берётся готовой формулировкой из разбора, а не переводится здесь:
    `AD_STATUS_RU` переводит значение поля, ничего не зная об объявлении, и
    «принято модерацией» верно только там, где вердикт уже есть. Перевод на
    месте показа встал бы на один путь из нескольких — а их тут четыре."""
    items = kit["parts"][part]
    if not items:
        return []
    top = kit["limits"].get(part) if kit["kit_applies"] else None
    counted = f"{len(items)} из {top}" if top else f"{len(items)}"
    lines = [f"{said} ({counted}):"]
    for number, item in enumerate(items[:ELEMENTS], start=1):
        note = f" — {item['clarification']}" if item["clarification"] else ""
        lines.append(f"  {number}. {excerpt(str(item['value']), 60)} · "
                     f"{item['status_ru']}{note}")
    if len(items) > ELEMENTS:
        lines.append(f"  … ещё {len(items) - ELEMENTS}")
    return lines


def card_lines(record: dict, limits) -> list:
    """Полная карточка одного объявления."""
    row = objects.ad_row(record, limits)
    kit = objects.composition(record, limits)
    lines = [
        f"{row['id']} · {row['type_ru']} · {row['status_ru']} · {row['state_ru']}",
        f"Кампания {row['campaign_id']} · группа {row['group_id']} · "
        f"структуры: {row['structures'] or '—'}",
        f"Комплект: {kit_text(kit)}"
        + (f" · {missing_text(kit)}" if missing_text(kit)
           else " · полон" if kit["complete"] else ""),
    ]
    if not kit["verdict"]:
        # Вопросов про модерацию три, и шапка отвечает на два из них: был ли
        # вердикт и почему его нет. Третий — что стоит в поле элемента —
        # отвечается ниже, у каждого элемента своим значением. Слить их в один
        # и значило бы выдать начальное значение поля за решение модератора.
        lines.append(f"Вердикта модерации нет: {kit['verdict_note']}. Статусы "
                     f"элементов ниже — значения полей, а не решения "
                     f"модератора.")
    if row["clarification"]:
        lines.append(f"Модерация: {row['clarification']}")
    if kit["expected_structure"] and kit["expected_structure"] not in kit["structures"]:
        # Тип обещает структуру, а её в ответе нет. Это не «полей нет», а
        # неполный запрос — единственный случай, когда так бывает у своей же
        # команды, это правка набора полей.
        lines.append(f"Структуры {kit['expected_structure']}, которую обещает "
                     f"тип {row['type']}, в ответе нет — запрос неполон.")
    link = " · ".join(part for part in (row["href"], row["display_domain"],
                                        row["display_url_path"]) if part)
    if link:
        lines.append(f"Ссылка: {link}")
    for part, said in (("titles", "Заголовки"), ("texts", "Тексты"),
                       ("images", "Изображения"), ("videos", "Видео"),
                       ("carousel", "Карусель")):
        lines.extend(element_lines(kit, part, said))
    if kit["rejected"]:
        lines.append(f"Отклонено элементов {len(kit['rejected'])}: "
                     + ", ".join(item["where"] for item in kit["rejected"][:ELEMENTS]))
    return lines


def store_card(cache: Cache, record: dict, limits):
    """Правило «полные данные всегда в файл» держится на том, что файл содержит
    именно показанное: сводка перечень элементов сокращает, и восстановить
    сокращённое было бы неоткуда."""
    payload = {"ad": record, "composition": objects.composition(record, limits),
               "row": objects.ad_row(record, limits)}
    return cache.write(f"ad-{record.get('Id')}", "structure", payload)


def report_card(record: dict, entry, spent: int, limits, export=None) -> None:
    lines = card_lines(record, limits)
    if spent:
        lines.append(f"Чтение стоило {campaign_command.units_said(spent)}.")
    lines.extend(campaign_command.export_line(export))
    outline(lines, path=entry.path)


def as_json(login: str, where: str, entry, found: list, total: int, limits,
            record=None, card=None, export=None) -> dict:
    body = {
        "account": login,
        "selection": where,
        "from_cache": entry.hit,
        "cache": None if entry.path is None else short(entry.path),
        "total": total,
        "matched": len(found),
        "types": counted(found, "Type"),
        "states": counted(found, "State"),
        "statuses": counted(found, "Status"),
        "csv": None if export is None else str(export),
    }
    if record is not None:
        body["ad"] = dict(objects.ad_row(record, limits),
                          composition=objects.composition(record, limits),
                          raw=record)
        body["card"] = None if card is None or card.path is None else short(card.path)
        return body
    body["ads"] = [dict(objects.ad_row(record, limits),
                        composition=objects.composition(record, limits),
                        raw=record)
                   for record in found[:JSON_LIMIT]]
    return body


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

    parser = Parser(description="Объявления: комплект, статусы, модерация.")
    parser.add_argument("--env", choices=("production", "test_cabinet"),
                        help="контур и набор переменных")
    parser.add_argument("--account", metavar="ЛОГИН",
                        help="логин кабинета; при отсутствии берётся активный")
    parser.add_argument("--campaign", metavar="ID|ЧАСТЬ_НАЗВАНИЯ",
                        type=incoming.campaign_selector,
                        help="кампания, чьи объявления читать")
    parser.add_argument("--group", metavar="СПИСОК",
                        help="идентификаторы групп через запятую")
    parser.add_argument("--ad", metavar="СПИСОК",
                        help="идентификаторы объявлений; один — полная карточка")
    parser.add_argument("--type", metavar="СПИСОК",
                        help=f"типы: {', '.join(sorted(objects.AD_TYPE_RU))}")
    parser.add_argument("--state", metavar="СПИСОК",
                        help=f"состояния: {', '.join(objects.AD_STATES)}")
    parser.add_argument("--status", metavar="СПИСОК",
                        help=f"статусы модерации: {', '.join(objects.AD_STATUSES)}")
    parser.add_argument("--incomplete", action="store_true",
                        help="только недозаполненные комплекты")
    parser.add_argument("--rejected", action="store_true",
                        help="только объявления с отклонёнными элементами")
    parser.add_argument("--csv", metavar="ФАЙЛ", type=Path,
                        help="выгрузить отобранное в файл")
    parser.add_argument("--json", action="store_true",
                        help="машиночитаемый вывод")
    add_arguments(parser)
    args = parser.parse_args(argv)

    if not (args.campaign or args.group or args.ad):
        parser.error("назовите кампанию (--campaign), группы (--group) или "
                     "объявления (--ad): Ads.get без отбора не отвечает")
    args.type = (campaign_command.enum_list(args.type, sorted(objects.AD_TYPE_RU),
                                            "--type", parser)
                 if args.type else None)
    args.state = (campaign_command.enum_list(args.state, list(objects.AD_STATES),
                                             "--state", parser)
                  if args.state else None)
    args.status = (campaign_command.enum_list(args.status, list(objects.AD_STATUSES),
                                              "--status", parser)
                   if args.status else None)
    args.group = numbers(args.group, "--group", parser) if args.group else None
    args.ad = numbers(args.ad, "--ad", parser) if args.ad else None

    try:
        # Пределы того, что назвал человек, — до первого платного вызова.
        # Дальше идут список кабинетов, поиск кампании и справочник регионов,
        # и каждый стоит баллов; негодный перечень идентификаторов останется
        # негодным независимо от того, что они вернут.
        objects.fits_selection(objects.SERVICE_ADS,
                               objects.selection(objects.SERVICE_ADS, group_ids=args.group,
                                                 ad_ids=args.ad))

        client = Client.from_env(profile=args.env, warn=warn)
        accounts = Accounts.load(client, warn=warn)
        login = resolve_account(accounts, client, args.account)
        cache = Cache.from_args(args, account=login, warn=warn)
        limits = campaign_command.limits()

        where = f"Кабинет {login}"
        campaign = None
        if args.campaign:
            slice_entry = campaign_command.read_slice(cache, client, accounts, login)
            campaign = campaign_command.one_campaign(slice_entry.data, args.campaign)
            row = campaign_command.row_of(campaign)
            where = (f"Кабинет {login} · кампания {row['id']} · {row['name']} · "
                     f"{row['type_ru']}")

        params = objects.ads_params(
            campaign_ids=[campaign["Id"]] if campaign is not None else None,
            group_ids=args.group, ad_ids=args.ad,
        )
        entry = read_ads(cache, client, accounts, login, params)
        found = chosen(entry.data, args, limits)

        # Есть ли названное объявление, решает **прочитанное**, а не
        # отобранное. Иначе `--ad 1919… --status REJECTED` на принятом
        # объявлении отвечает «в кабинете нет» — то есть отрицает объект,
        # который только что прочитан, и отвечает не на тот вопрос.
        notes = []
        record = None
        if args.ad and len(args.ad) == 1:
            if not any(item.get("Id") == args.ad[0] for item in entry.data):
                # Отсутствие называется по тому, о чём **спросили**. Отбор
                # ушёл в `SelectionCriteria` вместе с идентификатором, и
                # пустой ответ на пересечение означает «не в этой кампании», а
                # не «нет в кабинете»: объявление могло лежать в соседней.
                # Утверждать второе значит отвечать не на тот вопрос — и
                # отправлять человека искать пропажу там, где её не теряли.
                narrowed = campaign is not None or args.group
                raise DirectFailure(
                    f"Объявления {args.ad[0]} в этой выборке нет — {where}. "
                    + ("Оно может лежать в другой кампании или группе: та же "
                       "команда только с --ad ищет по всему кабинету."
                       if narrowed else
                       "Список — та же команда с --campaign или --group.")
                )
            if found:
                record = found[0]
            else:
                notes.append(f"Объявление {args.ad[0]} в кабинете есть, но под "
                             f"названный отбор не подходит.")
        card = store_card(cache, record, limits) if record is not None else None

        if args.csv:
            campaign_command.dump_csv(
                [objects.ad_row(item, limits) for item in found],
                objects.AD_COLUMNS, args.csv)
        spent = client.units.report()["spent"]

        if args.json:
            say(json.dumps(as_json(login, where, entry, found, entry.count,
                                   limits, record, card, args.csv),
                           ensure_ascii=False))
        elif record is not None:
            report_card(record, card, spent, limits, args.csv)
        else:
            report(login, where, entry, found, entry.count, spent, limits,
                   args.csv, notes)
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
