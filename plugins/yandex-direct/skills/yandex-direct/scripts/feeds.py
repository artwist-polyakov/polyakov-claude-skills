#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""Фиды Директа: list, get, add, update, delete. Без --apply — план без записи."""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import sys
from pathlib import Path
from urllib.parse import urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parent / "lib"))

import policy  # noqa: E402
from accounts import Accounts, resolve_account  # noqa: E402
from cache import Cache, outline, signature  # noqa: E402
from config import DirectFailure, preload_secrets, redact  # noqa: E402
from direct import Client  # noqa: E402
from feed_objects import BUSINESS_TYPES, STATUSES, WRITE_READ_PARAMS, read_feeds  # noqa: E402
from writer import Limits, Operation, PROBLEM, Task, UNLOGGED, UNVERIFIED, WHOLE_ACCOUNT, Writer, showing  # noqa: E402

FILE_EXTENSIONS = (".xml", ".csv", ".tsv", ".yml", ".xls", ".xlsx", ".gz", ".zip")
TEXT_FIELDS = {"Name": "Feed.Name", "UrlFeed.Url": "Feed.Url", "FileFeed.Filename": "Feed.Filename"}
FIELD_TITLES = {"Name": "название", "BusinessType": "тип бизнеса", "SourceType": "тип источника",
                "UrlFeed.Url": "ссылка на фид", "UrlFeed.RemoveUtmTags": "удалять UTM-метки",
                "FileFeed.Data": "содержимое файла", "FileFeed.Filename": "имя файла"}
USAGE_NOTE = ("CampaignIds из Feeds.get может быть неполным: пустой список не доказывает, "
              "что фид не используется. Для ЕПК проверьте FeedId товарных объявлений "
              "и объявлений каталога; порядок описан в references/FEEDS.md.")


def say(text):
    print(redact(str(text)))


def warn(text):
    print(redact(str(text)), file=sys.stderr)


def positive(value):
    if not str(value).isascii() or not str(value).isdigit() or int(value) < 1:
        raise argparse.ArgumentTypeError("ID должен быть положительным целым числом.")
    return int(value)


def leaves(item):
    for field, value in item.items():
        if isinstance(value, dict):
            for nested, one in value.items():
                yield f"{field}.{nested}", one
        elif field != "Id":
            yield field, value


def request_item(args):
    """Проверить вход до подключения к кабинету; файл передаётся бинарно в base64."""
    item, details = {}, None
    limits = Limits.load()
    if args.name is not None:
        if not args.name.strip():
            raise DirectFailure("Название фида не должно быть пустым.")
        item["Name"] = args.name
    if args.url is not None:
        try:
            parsed = urlsplit(args.url)
            valid = parsed.scheme in ("http", "https") and parsed.hostname and parsed.port != 0
        except ValueError:
            valid = False
        if not valid or any(character.isspace() for character in args.url):
            raise DirectFailure("Нужна полная ссылка на фид с http:// или https:// и доменным именем.")
        if parsed.username is not None or parsed.password is not None:
            raise DirectFailure("Эта команда принимает публичные ссылки без логина и пароля в URL.")
        item["UrlFeed"] = {"Url": args.url}
    if args.remove_utm_tags is not None:
        if args.file is not None:
            raise DirectFailure("--remove-utm-tags применим только к фидам по ссылке.")
        item.setdefault("UrlFeed", {})["RemoveUtmTags"] = args.remove_utm_tags
    if args.file is not None:
        path = Path(args.file)
        if path.suffix.lower() not in FILE_EXTENSIONS:
            raise DirectFailure("Поддерживаются файлы: " + ", ".join(FILE_EXTENSIONS) + ".")
        maximum = limits.data["media"]["feed"]["max_bytes"]
        try:
            if not path.is_file():
                raise DirectFailure(f"Фид {path} должен быть обычным файлом.")
            with path.open("rb") as stream:
                data = stream.read(maximum * 3 // 4 + 1)
        except OSError as failure:
            raise DirectFailure(f"Фид {path} не прочитан: {failure.strerror or failure}.") from None
        if not data:
            raise DirectFailure("Файл фида пуст.")
        item["FileFeed"] = {"Data": base64.b64encode(data).decode("ascii"), "Filename": path.name}
        details = {"file": str(path.resolve()), "bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()}
    if args.action == "add":
        item.update(BusinessType=args.business_type, SourceType="FILE" if args.file is not None else "URL")
    else:
        if not item:
            raise DirectFailure("Для update укажите --name, --url, --file или --remove-utm-tags.")
        item["Id"] = args.feed
    for field, value in leaves(item):
        if field in TEXT_FIELDS:
            problems = limits.text_problems(TEXT_FIELDS[field], value)
            if problems:
                raise DirectFailure(f"{field}: " + "; ".join(problems))
    if details:
        # В лимит входят base64, JSON-конверт и все остальные поля, а не только файл.
        request_bytes = len(json.dumps({"method": args.action, "params": {"Feeds": [item]}},
                                      ensure_ascii=False).encode("utf-8"))
        if request_bytes > maximum:
            raise DirectFailure("Запрос с фидом превышает 50 МБ с учётом base64 и JSON.")
        details["request_bytes"] = request_bytes
    return item, details


def write_operation(action, item):
    label = item["Name"] if action == "add" else item["Id"]
    fields = dict(leaves(item))

    def source_guard(records):
        if action != "update" or label not in records:
            return []
        expected = "FILE" if "FileFeed" in item else "URL" if "UrlFeed" in item else None
        if expected is not None and records[label].get("SourceType") != expected:
            return [f"Фид {label}: тип источника менять нельзя. Для другого источника создайте новый фид."]
        return []

    return Operation(
        "feeds", action, params_key="Feeds", items=[item], read=WRITE_READ_PARAMS,
        changes=[policy.Change(object_id=label, what=FIELD_TITLES[field], field=field,
                               after=value, service="фид") for field, value in fields.items()],
        texts={field: TEXT_FIELDS[field] for field in fields if field in TEXT_FIELDS},
        unread=("FileFeed.Data",) if "FileFeed" in item else (),
        labels=[label] if action == "add" else None,
        search=("Name", WHOLE_ACCOUNT) if action == "add" else None,
        guard=source_guard,
    )


def processing(records):
    return {"ready": bool(records) and all(one["Status"] == "DONE" for one in records),
            "processing_pending": [one["Id"] for one in records if one["Status"] in ("NEW", "UPDATING")],
            "processing_failed": [one["Id"] for one in records if one["Status"] == "ERROR"]}


def describe(item):
    source = item["UrlFeed"]["Url"] if item["SourceType"] == "URL" else item["FileFeed"]["Filename"]
    campaigns = (item.get("CampaignIds") or {}).get("Items", [])
    return (f"Фид {item['Id']} · {item['Status']}: {STATUSES[item['Status']]}"
            f" · предложений: {item.get('NumberOfItems')} · {item['Name']} · {item['BusinessType']}"
            f" · {source} · схема: {item.get('FilterSchema', 'ещё не получена')}"
            f" · кампании, перечисленные Feeds.get: {campaigns}")


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
    for action in ("list", "get"):
        read = commands.add_parser(action, parents=[parent], help="параметры, состояние и использование фидов")
        read.add_argument("--feed", type=positive, action="append", dest="ids", required=action == "get",
                          help="ID фида; флаг повторяется")
        read.add_argument("--no-cache", action="store_true", help="получить свежее состояние обработки")
        read.add_argument("--search", help="часть названия без учёта регистра")
    for action in ("add", "update", "delete"):
        write = commands.add_parser(action, parents=[parent], help={
            "add": "добавить фид по публичной ссылке или из файла",
            "update": "изменить настройки или загрузить новую версию файла",
            "delete": "удалить фиды; используемые в группах API не удаляет"}[action])
        if action != "add":
            write.add_argument("--feed", type=positive, required=True,
                               **({"action": "append", "dest": "ids"} if action == "delete" else {}),
                               help="ID фида" + ("; флаг повторяется" if action == "delete" else ""))
        if action != "delete":
            write.add_argument("--name", required=action == "add", help="название фида, до 255 символов")
            if action == "add":
                write.add_argument("--business-type", choices=BUSINESS_TYPES, default="RETAIL", help="тип бизнеса (по умолчанию RETAIL)")
            source = write.add_mutually_exclusive_group(required=action == "add")
            source.add_argument("--url", help="публичная ссылка на фид (http/https), до 1024 символов")
            source.add_argument("--file", help="локальный файл xml/csv/tsv/yml/xls/xlsx/gz/zip; запрос до 50 МБ")
            write.add_argument("--remove-utm-tags", choices=("YES", "NO"), help="удалять UTM-метки в URL-фиде; при добавлении по умолчанию NO")
        mode = write.add_mutually_exclusive_group()
        mode.add_argument("--apply", action="store_true", help="записать и перечитать результат")
        mode.add_argument("--dry-run", action="store_true", help="проверка без записи (умолчание)")
    return parser


def run(args):
    item, details = request_item(args) if args.action in ("add", "update") else (None, None)
    client = Client.from_env(profile=args.env, account=args.account, warn=warn)
    accounts = Accounts.load(client, warn=warn)
    account = resolve_account(accounts, client, args.account)
    if args.action in ("list", "get"):
        identifiers = sorted(set(args.ids)) if args.ids is not None else None
        entry = Cache.from_args(args, account, settings=client.settings).through(
            f"feeds-{client.settings.profile}-{signature(identifiers)}", "structure",
            lambda: list(read_feeds(client, account, accounts, identifiers).values()))
        missing = set(identifiers or []) - {one["Id"] for one in entry.data}
        if missing:
            raise DirectFailure("Фиды не найдены в кабинете: " + ", ".join(map(str, sorted(missing))))
        needle = (args.search or "").casefold()
        records = sorted((one for one in entry.data if needle in one["Name"].casefold()), key=lambda one: one["Id"])
        result = {"account": account, "feeds": records, "cached": entry.hit,
                  "usage_note": USAGE_NOTE,
                  "file": str(entry.path) if entry.path else None, **processing(records)}
        if args.json:
            say(json.dumps(result, ensure_ascii=False))
        else:
            say(f"Кабинет: {account} · найдено: {len(records)}" + (" · из кеша" if entry.hit else " · свежие данные"))
            say(USAGE_NOTE)
            outline([describe(one) for one in records], path=entry.path, total=len(entry.data))
            if result["processing_pending"] or result["processing_failed"]:
                say("Обработка фида не завершилась успешно; get --feed ID --no-cache обновит состояние. "
                    "Текст ошибок обработки Feeds.get не возвращает.")
        return 0

    if args.action == "delete":
        identifiers = list(dict.fromkeys(args.ids))
        operation = Operation("feeds", "delete", selection="Ids", items=[{"Id": one} for one in identifiers],
                              changes=[policy.Change(object_id=one, what="удалить фид из кабинета", service="фид")
                                       for one in identifiers], read=WRITE_READ_PARAMS)
    else:
        operation = write_operation(args.action, item)
    notes = ["Обработка содержимого выполняется отдельно: NEW/UPDATING не означают готовность фида."]
    if args.action in ("update", "delete"):
        notes.append(USAGE_NOTE)
    if details:
        notes.extend([json.dumps(details, ensure_ascii=False),
                      "API не возвращает содержимое файла: после записи проверяются доступные параметры, не байты файла."])
    seen = []
    engine = Writer(client, account, accounts=accounts, apply=args.apply,
                    show=showing(seen=seen, quiet=args.json, notes=notes), warn=warn)
    report = engine.run(Task({"add": "добавить фид", "update": "изменить фид", "delete": "удалить фиды"}[args.action], [operation]))
    after = []
    if args.action != "delete" and report.applied and report.accepted:
        try:
            after = list(read_feeds(client, account, accounts, report.accepted).values())
            missing = set(report.accepted) - {one["Id"] for one in after}
            if missing:
                raise DirectFailure("После записи не прочитаны фиды: " + ", ".join(map(str, sorted(missing))))
            for one in after:
                if one["Status"] == "ERROR":
                    report.record(PROBLEM, f"Фид {one['Id']}: ERROR — ошибка обработки содержимого. "
                                  "Параметры записаны; текст ошибки Feeds.get не возвращает.")
        except DirectFailure as failure:
            report.record(UNVERIFIED, f"Состояние обработки фида после записи не проверено: {failure}")
        try:
            engine.journal.record({"kind": "feed_processing_checked", "feeds": after,
                                   "processing": processing(after), "unknown": report.unknown})
        except DirectFailure as failure:
            report.record(UNLOGGED, str(failure))
    result = {**report.machine(), "account": account, "feeds": after, "upload": details,
              "usage_note": USAGE_NOTE,
              **processing(after)}
    if args.json:
        say(json.dumps(result, ensure_ascii=False))
    else:
        lines = report.lines()
        say("\n".join(lines[len(report.preview):] if seen else lines))
        for one in after:
            say(describe(one))
        if result["processing_pending"]:
            say("Параметры приняты; обработка ещё не завершена. get --feed ID --no-cache покажет новое состояние.")
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
