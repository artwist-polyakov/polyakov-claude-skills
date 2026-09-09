#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""Сегменты Метрики и условия Директа: sources, list, create. Без --apply — план."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "lib"))

import policy  # noqa: E402
from accounts import Accounts, resolve_account  # noqa: E402
from cache import Cache, outline, signature  # noqa: E402
from config import DirectFailure, preload_secrets, redact  # noqa: E402
from direct import Client  # noqa: E402
from errors import required  # noqa: E402
from retargeting_lists import LIST_FIELDS, read_lists  # noqa: E402
from writer import Limits, Operation, Task, Writer, showing  # noqa: E402


def say(text):
    print(redact(str(text)))


def warn(text):
    print(redact(str(text)), file=sys.stderr)


def positive(value):
    if not str(value).isascii() or not str(value).isdigit() or int(value) < 1:
        raise argparse.ArgumentTypeError("ID должен быть положительным целым числом.")
    return int(value)


def read_sources(client, account):
    """GetRetargetingGoals уже возвращает доступные объекты; оставляем сегменты Метрики."""
    found = {}
    for item in client.retargeting_goals([account]):
        kind = required(item, "Type", str, "GetRetargetingGoals")
        if kind != "segment":
            continue
        # Область запроса задаётся Logins. Владелец Login может быть не указан.
        identifier = required(item, "GoalID", int, "GetRetargetingGoals")
        if identifier < 1:
            raise DirectFailure("GetRetargetingGoals вернул неположительный ID сегмента.")
        required(item, "Name", str, "GetRetargetingGoals")
        found[identifier] = item
    return list(found.values())


def create_operation(segment, name, description=None):
    if not name.strip():
        raise DirectFailure("Название условия не должно быть пустым.")
    item = {"Type": "RETARGETING", "Name": name,
            "Rules": [{"Operator": "ANY", "Arguments": [{"ExternalId": segment}]}]}
    texts = {"Name": "RetargetingList.Name"}
    if description is not None:
        item["Description"] = description
        texts["Description"] = "RetargetingList.Description"
    limits = Limits.load()
    for field, reference in texts.items():
        problems = limits.text_problems(reference, item[field])
        if problems:
            raise DirectFailure(f"{field}: " + "; ".join(problems))
    labels = {"Type": "тип", "Name": "название", "Rules": "правило отбора",
              "Description": "описание"}
    return Operation(
        "retargetinglists", "add", params_key="RetargetingLists", items=[item],
        changes=[policy.Change(object_id=name, what=labels[field], field=field,
                               after=value, service="условие Директа")
                 for field, value in item.items()],
        labels=[name], read={"FieldNames": list(LIST_FIELDS)}, texts=texts,
        search=("Name", {"Types": ["RETARGETING"]}),
    )


def selected(records, search):
    needle = (search or "").casefold()
    return sorted((item for item in records if needle in item.get("Name", "").casefold()),
                  key=lambda item: (item.get("Name", "").casefold(), item.get("Id", item.get("GoalID"))))


def same_segment(item, segment):
    """Простое ANY-условие; MembershipLifeSpan для сегмента не меняет отбор."""
    if item.get("Type") != "RETARGETING":
        return False
    rules = item.get("Rules") or []
    if len(rules) != 1 or rules[0].get("Operator") != "ANY":
        return False
    arguments = rules[0].get("Arguments") or []
    return len(arguments) == 1 and arguments[0].get("ExternalId") == segment


def show_records(account, action, records, entry, as_json):
    if as_json:
        say(json.dumps({"account": account, "source": action,
                        "segments" if action == "sources" else "conditions": records,
                        "cached": entry.hit,
                        "file": str(entry.path) if entry.path else None}, ensure_ascii=False))
        return
    say(f"Кабинет: {account} · найдено: {len(records)}"
        + (" · из кеша; --no-cache обновит данные" if entry.hit else " · свежие данные"))
    lines = []
    for item in records:
        if action == "sources":
            lines.append(f"Сегмент {item['GoalID']} · {item['Name']} · {item.get('GoalDomain', '')}")
        else:
            lines.append(f"Условие {item['Id']} · {item.get('Name', '')}"
                         f" · {item.get('Type', 'тип не получен')}"
                         f" · доступно: {item.get('IsAvailable', 'неизвестно')}"
                         f" · {item.get('Scope', 'область не получена')}"
                         " · правила: " + json.dumps(item.get("Rules"), ensure_ascii=False))
    outline(lines, path=entry.path, total=len(entry.data))


class Parser(argparse.ArgumentParser):
    def error(self, message):
        self.exit(2, redact(f"{self.prog}: {message}\n"))


def common(parser, leaf=False):
    default = argparse.SUPPRESS if leaf else None
    parser.add_argument("--env", choices=("production", "test_cabinet"), default=default)
    parser.add_argument("--account", default=default, help="логин кабинета")
    parser.add_argument("--json", action="store_true",
                        default=argparse.SUPPRESS if leaf else False)


def build_parser():
    parser = Parser(description=__doc__)
    common(parser)
    parent = argparse.ArgumentParser(add_help=False)
    common(parent, leaf=True)
    commands = parser.add_subparsers(dest="action", required=True)
    for action, help_text in (("sources", "доступные сегменты Метрики"),
                              ("list", "существующие условия Директа и их правила")):
        listing = commands.add_parser(action, parents=[parent], help=help_text)
        listing.add_argument("--search", help="часть названия, без учёта регистра")
        listing.add_argument("--no-cache", action="store_true", help="получить свежие данные")
        if action == "list":
            listing.add_argument("--id", dest="ids", action="append", type=positive,
                                 help="ID условия Директа; флаг можно повторять")
    create = commands.add_parser("create", parents=[parent], help="условие из одного сегмента Метрики")
    create.add_argument("--segment", required=True, type=positive, help="GoalID из команды sources")
    create.add_argument("--name", required=True, help="название нового условия Директа")
    create.add_argument("--description", help="описание условия")
    mode = create.add_mutually_exclusive_group()
    mode.add_argument("--apply", action="store_true", help="создать и перечитать условие")
    mode.add_argument("--dry-run", action="store_true", help="проверка без записи (умолчание)")
    return parser


def run(args):
    # Ошибки входных данных обнаруживаются до подключения к кабинету.
    operation = (create_operation(args.segment, args.name, args.description)
                 if args.action == "create" else None)
    client = Client.from_env(profile=args.env, account=args.account, warn=warn)
    accounts = Accounts.load(client, warn=warn)
    account = resolve_account(accounts, client, args.account)
    if operation is None:
        cache = Cache.from_args(args, account)
        profile = client.settings.profile
        if args.action == "sources":
            entry = cache.through(f"retargeting-sources-{profile}", "structure",
                                  lambda: read_sources(client, account))
        else:
            identifiers = sorted(set(args.ids)) if args.ids is not None else None
            entry = cache.through(f"retargeting-lists-{profile}-{signature(identifiers)}", "structure",
                                  lambda: list(read_lists(client, account, accounts, identifiers).values()))
            if identifiers is not None:
                missing = set(identifiers) - {one["Id"] for one in entry.data}
                if missing:
                    raise DirectFailure("Условия не найдены в кабинете: " + ", ".join(map(str, sorted(missing))))
        show_records(account, args.action, selected(entry.data, args.search), entry, args.json)
        return 0

    sources = {one["GoalID"]: one for one in read_sources(client, account)}
    if args.segment not in sources:
        raise DirectFailure(f"Сегмент Метрики {args.segment} недоступен в кабинете {account}. "
                            "Выберите ID из retargeting.py sources --no-cache.")
    conditions = read_lists(client, account, accounts).values()
    existing = [one for one in conditions if one.get("Name") == args.name]
    if existing:
        raise DirectFailure("Условие с таким названием уже существует: "
                            + ", ".join(str(one["Id"]) for one in existing)
                            + ". Прочитайте его через list --id ID и используйте подходящее "
                            "условие либо задайте новое название.")
    matching = [one for one in conditions if same_segment(one, args.segment)]
    if matching:
        raise DirectFailure("Условие с таким правилом уже существует: "
                            + ", ".join(f"{one['Id']} «{one.get('Name', '')}»" for one in matching)
                            + ". Директ не создаёт повторное правило под другим названием. "
                            "Прочитайте его через list --id ID и используйте существующее условие.")
    source = sources[args.segment]
    notes = [f"Сегмент Метрики {args.segment}: {source['Name']} · {source.get('GoalDomain', '')}"]
    seen = []
    engine = Writer(client, account, accounts=accounts, apply=args.apply,
                    show=showing(seen=seen, quiet=args.json, notes=notes), warn=warn)
    report = engine.run(Task("создать условие Директа из сегмента Метрики", [operation]))
    if args.json:
        say(json.dumps({**report.machine(), "account": account, "segment": source,
                        "condition_ids": report.written}, ensure_ascii=False))
    else:
        lines = report.lines()
        say("\n".join(lines[len(report.preview):] if seen else lines))
        if report.written:
            say("ID проверенных условий Директа: " + ", ".join(map(str, report.written)))
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
