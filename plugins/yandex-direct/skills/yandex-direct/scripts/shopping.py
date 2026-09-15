#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""Товарные объявления: get, add, update. Без --apply — проверка без записи."""

from __future__ import annotations

import argparse
import copy
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "lib"))

import incoming  # noqa: E402
import objects  # noqa: E402
import policy  # noqa: E402
from accounts import Accounts, resolve_account  # noqa: E402
from cache import Cache, outline  # noqa: E402
from config import DirectFailure, preload_secrets, redact  # noqa: E402
from direct import Client  # noqa: E402
from errors import required, TransportFailure  # noqa: E402
from feed_objects import read_feeds  # noqa: E402
from writer import Limits, Operation, PROBLEM, Task, UNLOGGED, UNVERIFIED, Writer, showing  # noqa: E402

import ads as ads_command  # noqa: E402


STRUCTURE = "ShoppingAd"
WRAPPED = ("FeedFilterConditions", "TitleSources", "TextSources")
OPERATORS = {"CONTAINS_ANY", "EQUALS_ANY", "EXISTS", "GREATER_THAN",
             "IN_RANGE", "LESS_THAN", "NOT_CONTAINS_ALL"}
LABELS = {"FeedId": "фид", "FeedFilterConditions": "правила отбора товаров",
          "TitleSources": "поля фида для заголовков",
          "TextSources": "поля фида для текстов", "DefaultTexts": "текст по умолчанию"}


def say(text):
    print(redact(str(text)))


def warn(text):
    print(redact(str(text)), file=sys.stderr)


def positive(value):
    if not str(value).isascii() or not str(value).isdigit() or int(value) < 1:
        raise argparse.ArgumentTypeError("ID должен быть положительным целым числом.")
    return int(value)


def strings(value, field):
    if not isinstance(value, list) or not value or any(
            not isinstance(one, str) or not one.strip() for one in value):
        raise DirectFailure(f"{field}: ожидается непустой массив непустых строк.")
    return value


def filters_of(path):
    try:
        filters = json.loads(incoming.text_of(path, "Фильтры товаров"))
    except json.JSONDecodeError as failure:
        raise DirectFailure(f"Фильтры товаров: неверный JSON: {failure}.") from None
    validate_filters(filters)
    return filters


def validate_filters(filters):
    if not isinstance(filters, list) or not filters:
        raise DirectFailure("Фильтры: нужен непустой JSON-массив правил; "
                            "для снятия всех правил укажите --clear-filters.")
    limits = Limits.load()
    problems = limits.collection_problems("FeedFilterConditions", filters)
    rule = limits.collections["FeedFilterConditions"]
    size = len(json.dumps(filters, ensure_ascii=False).encode("utf-8"))
    if size > rule["max_total_bytes_json"]:
        problems.append(f"размер JSON {size} байт при пределе {rule['max_total_bytes_json']}")
    for index, item in enumerate(filters, 1):
        if not isinstance(item, dict) or set(item) != {"Operand", "Operator", "Arguments"}:
            raise DirectFailure(f"Фильтр {index}: нужны ровно Operand, Operator и Arguments.")
        if not isinstance(item["Operand"], str) or not item["Operand"].strip():
            raise DirectFailure(f"Фильтр {index}: Operand должен быть непустой строкой.")
        if not isinstance(item["Operator"], str) or item["Operator"] not in OPERATORS:
            raise DirectFailure(f"Фильтр {index}: неизвестный Operator; допустимы "
                                + ", ".join(sorted(OPERATORS)) + ".")
        strings(item["Arguments"], f"Фильтр {index}, Arguments")
    if problems:
        raise DirectFailure("Фильтры: " + "; ".join(problems))


def body_of(args):
    body = {}
    if args.action == "add":
        body["FeedId"] = args.feed
    if args.default_text is not None:
        body["DefaultTexts"] = strings([args.default_text], "DefaultTexts")
    if args.filters is not None:
        body["FeedFilterConditions"] = filters_of(args.filters)
    elif getattr(args, "clear_filters", False):
        body["FeedFilterConditions"] = None
    for field, flag in (("TitleSources", "title_sources"), ("TextSources", "text_sources")):
        values = getattr(args, flag)
        if values is not None:
            body[field] = strings(values, field)
        elif getattr(args, f"clear_{flag}", False):
            body[field] = None
    if not body:
        raise DirectFailure("Не заданы изменения: укажите текст, источники или фильтры.")
    return body


def read_params(args):
    # Общий запрос сохраняет и полный комплект комбинаторных объявлений.
    params = (objects.ads_params(group_ids=[args.group]) if args.action == "add"
              else objects.ads_params(ad_ids=[args.ad]))
    del params["SelectionCriteria"]
    return params


def index_records(records, service, requested=None):
    found = {}
    for one in records:
        identifier = required(one, "Id", int, f"{service}.get")
        if isinstance(identifier, bool) or identifier <= 0:
            raise TransportFailure(f"{service}.get: Id должен быть положительным целым числом.",
                                   retryable=False)
        if identifier in found or (requested is not None and identifier not in requested):
            raise TransportFailure(f"{service}.get: повторный или незапрошенный Id {identifier}.",
                                   retryable=False)
        found[identifier] = one
    return found


def read_records(client, account, accounts, service, params):
    requested = (params.get("SelectionCriteria") or {}).get("Ids")
    need = Limits.load().units_cost(service, "get", len(requested or []) or None)
    records = client.get_all(
        service, params, account=account,
        use_operator_units=lambda: accounts.use_operator_units(account, need=need))
    return index_records(records, service, requested)


def shopping_body(record):
    if record.get("Type") != "SHOPPING_AD" or not isinstance(record.get(STRUCTURE), dict):
        raise DirectFailure(f"Объявление {record.get('Id')}: нужен тип SHOPPING_AD "
                            "и прочитанная структура ShoppingAd.")
    return record[STRUCTURE]


def check_feed(client, account, accounts, identifier, body, *, creating=False):
    feeds = read_feeds(client, account, accounts, ids=[identifier])
    if identifier not in feeds:
        raise DirectFailure(f"Фид {identifier} не найден в выбранном кабинете.")
    if creating and feeds[identifier]["Status"] == "ERROR":
        raise DirectFailure(f"Фид {identifier}: ERROR — ошибка обработки. "
                            "Исправьте источник фида и дождитесь обработки, затем создавайте объявление.")
    requested = [one for field in ("TitleSources", "TextSources")
                 for one in (body.get(field) or [])]
    if requested:
        available = objects.items_of(feeds[identifier].get("TitleAndTextSources"))
        unknown = sorted(set(requested) - set(available))
        if unknown:
            raise DirectFailure("Фид не разрешает источники заголовков/текстов: "
                                + ", ".join(unknown)
                                + ". Допустимые значения: " + ", ".join(available) + ".")


def guard_for(client, account, accounts, args, body):
    def guard(records):
        try:
            if args.action == "add":
                groups = read_records(client, account, accounts, "adgroups",
                                      objects.group_params(group_ids=[args.group]))
                group = groups.get(args.group)
                if not group or group.get("Type") != "UNIFIED_AD_GROUP":
                    raise DirectFailure("Товарное объявление можно создать только "
                                        "в существующей группе UNIFIED_AD_GROUP (ЕПК).")
                existing = read_records(client, account, accounts, "ads",
                                        objects.ads_params(group_ids=[args.group]))
                if any(one.get("Type") == "SHOPPING_AD" and one.get("State") != "ARCHIVED"
                       for one in existing.values()):
                    raise DirectFailure("В группе уже есть неархивное товарное объявление; "
                                        "разрешено только одно. Используйте update.")
                check_feed(client, account, accounts, args.feed, body, creating=True)
            else:
                for record in records.values():
                    current = shopping_body(record)
                    if any(body.get(field) for field in ("TitleSources", "TextSources")):
                        identifier = current.get("FeedId")
                        if not isinstance(identifier, int) or isinstance(identifier, bool) or identifier < 1:
                            raise DirectFailure("У товарного объявления не прочитан FeedId.")
                        check_feed(client, account, accounts, identifier, body)
        except DirectFailure as failure:
            return [str(failure)]
        return []
    return guard


def read_processing(client, account, accounts, identifiers):
    """Параметры записаны отдельно от фоновой генерации товарных объявлений."""
    records = read_records(client, account, accounts, "ads", objects.ads_params(ad_ids=identifiers))
    if set(records) != set(identifiers):
        raise DirectFailure("При проверке генерации Ads.get вернул неполный или другой набор объявлений.")
    feed_ids = set()
    for record in records.values():
        body = shopping_body(record)
        identifier = required(body, "FeedId", int, "Ads.get, ShoppingAd")
        if isinstance(identifier, bool) or identifier < 1:
            raise DirectFailure("При проверке генерации не прочитан положительный FeedId.")
        feed_ids.add(identifier)
        for field in ("State", "Status"):
            required(record, field, str, "Ads.get")
        status = required(body, "FeedProcessingStatus", str, "Ads.get, ShoppingAd")
        if status not in ("PROCESSED", "UNPROCESSED", "EMPTY_RESULT", "UNKNOWN"):
            raise DirectFailure(f"Объявление {record['Id']}: неизвестный FeedProcessingStatus {status}.")
    feeds = read_feeds(client, account, accounts, ids=sorted(feed_ids))
    if set(feeds) != feed_ids:
        raise DirectFailure("При проверке генерации не прочитаны исходные фиды объявлений.")
    result = {"ready": bool(records), "processing_pending": [], "processing_empty": [], "processing_failed": []}
    for record in records.values():
        body = record[STRUCTURE]
        status = body["FeedProcessingStatus"]
        feed_status = feeds[body["FeedId"]]["Status"]
        if status != "PROCESSED" or feed_status != "DONE":
            result["ready"] = False
        if status in ("UNPROCESSED", "UNKNOWN") or feed_status in ("NEW", "UPDATING"):
            result["processing_pending"].append(record["Id"])
        if status == "EMPTY_RESULT":
            result["processing_empty"].append(record["Id"])
        if feed_status == "ERROR":
            result["processing_failed"].append(record["Id"])
    return list(records.values()), list(feeds.values()), result


def operation_for(args, body, guard=None):
    writing = copy.deepcopy(body)
    expected = copy.deepcopy(body)
    derived = []
    for field in WRAPPED:
        if field not in body or body[field] is None:
            continue
        expected[field] = {"Items": copy.deepcopy(body[field])}
        if args.action == "add":
            derived.append(f"{STRUCTURE}.{field}")
        else:
            writing[field] = copy.deepcopy(expected[field])
    creating = args.action == "add"
    identifier = "новое товарное объявление" if creating else args.ad
    item = {"AdGroupId" if creating else "Id": args.group if creating else args.ad,
            STRUCTURE: writing}
    wanted = {**item, STRUCTURE: expected}
    changes = [policy.Change(object_id=identifier, what=LABELS[field],
                             field=f"{STRUCTURE}.{field}", after=value,
                             service="товарное объявление") for field, value in writing.items()]
    if creating:
        changes.insert(0, policy.Change(object_id=identifier, what="группа",
                                       field="AdGroupId", after=args.group))
    # Фильтры проверяет validate_filters: общий обход коллекций не различает
    # массив правил и отдельный словарь правила на том же пути при add.
    collections = ({f"{STRUCTURE}.DefaultTexts": "ShoppingAd.DefaultTexts"}
                   if "DefaultTexts" in body else {})
    return Operation("ads", args.action, items=[item], changes=changes,
                     params_key="Ads", read=read_params(args), expect=[wanted],
                     derived=derived, collections=collections,
                     clears=[f"{STRUCTURE}.{field}" for field, value in body.items() if value is None],
                     labels=[identifier] if creating else None,
                     search=None, guard=guard)


def show_records(records, entry, account, as_json):
    if as_json:
        say(json.dumps({"account": account, "ads": records, "cached": entry.hit,
                        "file": str(entry.path) if entry.path else None}, ensure_ascii=False))
        return
    lines = [f"Товарных объявлений: {len(records)}"]
    for record in records:
        body = shopping_body(record)
        lines.append(f"Объявление {record['Id']} · группа {record['AdGroupId']} · "
                     f"{record.get('State')} · {record.get('Status')}")
        lines.append(f"  Фид {body.get('FeedId')} · обработка: {body.get('FeedProcessingStatus')}")
        for field in (*WRAPPED, "DefaultTexts"):
            value = body.get(field)
            lines.append(f"  {LABELS[field]}: "
                         + ("не прочитано" if field not in body else
                            "все товары" if field == "FeedFilterConditions" and value is None
                            else json.dumps(value, ensure_ascii=False)))
    outline(lines, path=entry.path, total=len(records))


class Parser(argparse.ArgumentParser):
    def error(self, message):
        self.exit(2, redact(f"{self.prog}: {message}\n"))


def common(parser, leaf=False):
    default = argparse.SUPPRESS if leaf else None
    parser.add_argument("--env", choices=("production", "test_cabinet"), default=default)
    parser.add_argument("--account", default=default, help="логин кабинета")
    parser.add_argument("--json", action="store_true", default=argparse.SUPPRESS if leaf else False)


def build_parser():
    parser = Parser(description=__doc__)
    common(parser)
    parent = argparse.ArgumentParser(add_help=False)
    common(parent, leaf=True)
    commands = parser.add_subparsers(dest="action", required=True)
    get = commands.add_parser("get", parents=[parent], help="товарные объявления и их фильтры")
    selection = get.add_mutually_exclusive_group(required=True)
    selection.add_argument("--ad", type=positive, action="append")
    selection.add_argument("--group", type=positive)
    selection.add_argument("--campaign", type=positive)
    get.add_argument("--no-cache", action="store_true")
    for action in ("add", "update"):
        step = commands.add_parser(action, parents=[parent],
                                   help="создать товарное объявление" if action == "add"
                                   else "изменить объявление; FeedId менять нельзя")
        step.add_argument("--group" if action == "add" else "--ad", type=positive, required=True)
        if action == "add":
            step.add_argument("--feed", type=positive, required=True)
        step.add_argument("--default-text", required=action == "add", help="один текст по умолчанию")
        filters = step.add_mutually_exclusive_group()
        filters.add_argument("--filters", type=Path, help="JSON-файл: массив Operand/Operator/Arguments")
        if action == "update":
            filters.add_argument("--clear-filters", action="store_true", help="снять все правила: все товары фида")
        for name in ("title", "text"):
            source = step.add_mutually_exclusive_group()
            source.add_argument(f"--{name}-source", dest=f"{name}_sources", action="append",
                                help="поле фида из TitleAndTextSources; можно повторять")
            if action == "update":
                source.add_argument(f"--clear-{name}-sources", action="store_true",
                                    help="сбросить выбранные источники")
        mode = step.add_mutually_exclusive_group()
        mode.add_argument("--apply", action="store_true", help="записать и перечитать результат")
        mode.add_argument("--dry-run", action="store_true", help="проверка без записи (умолчание)")
    return parser


def run(args):
    body = None if args.action == "get" else body_of(args)
    client = Client.from_env(profile=args.env, account=args.account, warn=warn)
    accounts = Accounts.load(client, warn=warn)
    account = resolve_account(accounts, client, args.account)
    if args.action == "get":
        params = objects.ads_params(ad_ids=args.ad, group_ids=[args.group] if args.group else None,
                                    campaign_ids=[args.campaign] if args.campaign else None)
        entry = ads_command.read_ads(Cache.from_args(args, account, settings=client.settings), client, accounts, account, params)
        found = index_records(entry.data, "ads", args.ad)
        for identifier in args.ad or []:
            if identifier not in found:
                raise DirectFailure(f"Объявление {identifier} не найдено в выбранном кабинете.")
            shopping_body(found[identifier])
        records = [one for one in found.values() if one.get("Type") == "SHOPPING_AD"]
        show_records(records, entry, account, args.json)
        return 0
    operation = operation_for(args, body, guard_for(client, account, accounts, args, body))
    seen = []
    notes = ["FeedId задаётся при создании; изменить его через Ads.update нельзя."]
    if body.get("FeedFilterConditions", False) is None:
        notes.append("Все правила отбора снимаются: доступны все товары фида.")
    engine = Writer(client, account, accounts=accounts, apply=args.apply,
                    show=showing(seen=seen, quiet=args.json, notes=notes), warn=warn)
    report = engine.run(Task("создать товарное объявление" if args.action == "add"
                             else "изменить товарное объявление", [operation]))
    after, feeds = [], []
    processing = {"ready": False, "processing_pending": [], "processing_empty": [], "processing_failed": []}
    if report.applied and report.accepted:
        try:
            after, feeds, processing = read_processing(client, account, accounts, report.accepted)
            for identifier in processing["processing_empty"]:
                report.record(PROBLEM, f"Объявление {identifier}: EMPTY_RESULT — по фиду и правилам "
                              "отбора не создано ни одного объявления. Проверьте фид и фильтры.")
            for identifier in processing["processing_failed"]:
                report.record(PROBLEM, f"Объявление {identifier}: настройки приняты, но исходный фид "
                              "имеет Status=ERROR. Исправьте источник; текст ошибки Feeds.get не возвращает.")
        except DirectFailure as failure:
            report.record(UNVERIFIED, f"После записи состояние генерации не подтверждено: {failure}")
        processing["ready"] = processing["ready"] and report.ok
        try:
            engine.journal.record({"kind": "shopping_processing_checked", "ads": after, "feeds": feeds,
                                   "processing": processing, "unknown": report.unknown})
        except DirectFailure as failure:
            report.record(UNLOGGED, str(failure))
    if args.json:
        say(json.dumps({**report.machine(), "account": account, "ads": after,
                        "feeds": feeds, **processing}, ensure_ascii=False))
    else:
        lines = report.lines()
        say("\n".join(lines[len(report.preview):] if seen else lines))
        for record in after:
            say(f"Объявление {record['Id']}: {record['State']} · {record['Status']}"
                f" · генерация: {record[STRUCTURE]['FeedProcessingStatus']}")
        if processing["processing_pending"]:
            say("Настройки приняты; готовность генерации ещё не подтверждена. "
                "shopping.py get --ad ID --no-cache покажет новое состояние.")
    return 0 if report.ok else 1


def main(argv=None):
    preload_secrets()
    try:
        return run(build_parser().parse_args(argv))
    except (DirectFailure, OSError) as failure:
        warn(str(failure))
        return 1


if __name__ == "__main__":
    sys.exit(main())
