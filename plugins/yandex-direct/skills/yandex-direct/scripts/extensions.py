#!/usr/bin/env python3
"""Быстрые ссылки и уточнения: чтение, создание, удаление и привязка к объявлениям."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "lib"))

import incoming  # noqa: E402
import objects  # noqa: E402
import policy  # noqa: E402
from accounts import Accounts, resolve_account  # noqa: E402
from ad_extensions import READ_PARAMS, ids_of, read_related  # noqa: E402
from cache import Cache  # noqa: E402
from config import DirectFailure, preload_secrets, redact  # noqa: E402
from direct import Client  # noqa: E402
from extension_writes import UNSET, build_binding_operations  # noqa: E402
from writer import UNLOGGED, UNVERIFIED, Limits, Operation, Task, Writer, showing  # noqa: E402

SERVICES = {"sitelinks": "sitelinks", "callouts": "adextensions"}
COLLECTIONS = {"sitelinks": "SitelinksSets", "callouts": "AdExtensions"}


def say(text):
    print(redact(str(text)))


def warn(text):
    print(redact(str(text)), file=sys.stderr)


def positive(value):
    number = incoming.whole_number(value, "ID")
    if number < 1:
        raise DirectFailure("ID должен быть положительным целым числом.")
    return number


def read_links(path):
    """JSON одного набора в форме Sitelinks.get: список Title/Href/Description."""
    try:
        links = json.loads(incoming.text_of(Path(path), "Быстрые ссылки"))
    except ValueError:
        raise DirectFailure("Файл быстрых ссылок должен быть корректным JSON.") from None
    if not isinstance(links, list) or not links:
        raise DirectFailure("В файле нужен непустой список быстрых ссылок.")
    allowed = {"Title", "Href", "Description", "TurboPageId"}
    for index, link in enumerate(links, 1):
        if not isinstance(link, dict) or set(link) - allowed:
            raise DirectFailure(f"Ссылка {index}: допустимы поля {', '.join(sorted(allowed))}.")
        # Необязательные поля в выгрузке get бывают null; при add их опускают.
        for field in ("Href", "Description", "TurboPageId"):
            if field in link and (link[field] is None or (field == "Description" and link[field] == "")):
                del link[field]
        for field in ("Title", "Href", "Description"):
            if field in link and (not isinstance(link[field], str) or not link[field].strip()):
                raise DirectFailure(f"Ссылка {index}: {field} должен быть непустой строкой; необязательное поле можно убрать.")
        if "Title" not in link or not (link.get("Href") or link.get("TurboPageId")):
            raise DirectFailure(f"Ссылка {index}: нужны Title и Href либо TurboPageId.")
        if "TurboPageId" in link:
            link["TurboPageId"] = positive(link["TurboPageId"])
    return links


def create_operation(kind, values):
    if kind == "sitelinks":
        items = [{"Sitelinks": values}]
        labels = ["новый набор быстрых ссылок"]
        texts = {f"Sitelinks.{key}": f"Sitelink.{key}"
                 for key in ("Title", "Href", "Description")
                 if any(key in link for link in values)}
        problems = Limits.load().collection_problems("SitelinksSet.Sitelinks", values)
        if problems:
            raise DirectFailure("Набор быстрых ссылок: " + "; ".join(problems))
        field = "Sitelinks"
    else:
        if any(not isinstance(text, str) or not text.strip() for text in values):
            raise DirectFailure("Текст уточнения не должен быть пустым.")
        values = list(dict.fromkeys(values))
        items = [{"Callout": {"CalloutText": text}} for text in values]
        labels = values
        texts = {"Callout.CalloutText": "AdExtension.Callout.CalloutText"}
        field = "Callout.CalloutText"
    changes = [policy.Change(object_id=label, what="содержимое", field=field,
                             after=values if kind == "sitelinks" else label,
                             service="набор ссылок" if kind == "sitelinks" else "уточнение")
               for label in labels]
    service = SERVICES[kind]
    return Operation(service, "add", params_key=COLLECTIONS[kind], items=items,
                     changes=changes, labels=labels, read=READ_PARAMS[service],
                     texts=texts, search=None)


def delete_operation(kind, identifiers):
    service = SERVICES[kind]
    read = READ_PARAMS[service]
    if kind == "callouts":
        # По одним Ids API возвращает и DELETED: отсутствие проверяем среди ON.
        read = dict(read, SelectionCriteria={"States": ["ON"]})
    return Operation(service, "delete", selection="Ids",
                     items=[{"Id": one} for one in identifiers],
                     changes=[policy.Change(object_id=one, what="удалить из библиотеки кабинета")
                              for one in identifiers], read=read,
                     read_required=("States",) if kind == "callouts" else ())


def require_complete(related):
    status = related["ExtensionsRead"]
    if not status["complete"]:
        raise DirectFailure("Не удалось полностью прочитать дополнения: "
                            + json.dumps(status, ensure_ascii=False))
    return related


def read_selected(client, accounts, account, kind, identifiers, limits, *, reuse=False):
    selection = {"sitelink_ids" if kind == "sitelinks" else "extension_ids": identifiers}
    return read_related(client, accounts, account, limits=limits,
                        cache=Cache(account, reuse=reuse), **selection)


def read_active_callouts(client, accounts, account, limits, identifiers=None):
    """Действующие уточнения: весь кабинет для поиска текста или выбранные ID."""
    size = limits.selection("adextensions") or 100
    chunks = [None] if identifiers is None else [
        identifiers[start:start + size] for start in range(0, len(identifiers), size)]
    found = {}
    for chunk in chunks:
        # State нельзя запросить в FieldNames. Состояние задаём фильтром,
        # чтобы не зависеть от наличия этого поля в ответе.
        selection = {"Types": ["CALLOUT"], "States": ["ON"]}
        if chunk is not None:
            selection["Ids"] = chunk
        need = limits.units_cost("adextensions", "get", len(chunk) if chunk is not None else None)
        records = client.get_all(
            "adextensions", {"SelectionCriteria": selection, **READ_PARAMS["adextensions"]},
            collection="AdExtensions", account=account,
            use_operator_units=lambda: accounts.use_operator_units(account, need=need))
        for one in records:
            identifier = positive(one.get("Id"))
            if chunk is not None and identifier not in chunk:
                raise DirectFailure(f"AdExtensions.get вернул незапрошенный ID {identifier}.")
            callout = one.get("Callout")
            if (one.get("Type") != "CALLOUT" or one.get("State", "ON") != "ON"
                    or not isinstance(callout, dict)
                    or not isinstance(callout.get("CalloutText"), str)):
                raise DirectFailure(f"Не удалось прочитать действующее уточнение {identifier}.")
            found[identifier] = one
    return found


def read_ads(client, accounts, account, identifiers, limits):
    records = []
    size = limits.selection("ads") or 1000
    for start in range(0, len(identifiers), size):
        chunk = identifiers[start:start + size]
        need = limits.units_cost("ads", "get", len(chunk))
        records.extend(client.get_all(
            "ads", objects.ads_params(ad_ids=chunk), account=account,
            use_operator_units=lambda: accounts.use_operator_units(account, need=need)))
    found = {one["Id"] for one in records}
    if found - set(identifiers):
        raise DirectFailure("Ads.get вернул объявления вне выбранного списка.")
    missing = set(identifiers) - found
    if missing:
        raise DirectFailure("Объявления не найдены в выбранном кабинете: "
                            + ", ".join(map(str, sorted(missing))))
    return records


def binding_plan(records, related, sitelink_set, callout_ids):
    sets = {one["Id"]: one for one in related["SitelinksSets"]}
    callouts = {one["Id"]: one for one in related["AdExtensions"]}
    result = []
    for ad in records:
        body = ad.get("ResponsiveAd") or ad.get("TextAd") or {}
        before, after = {}, {}
        if sitelink_set is not UNSET:
            before["sitelinks"] = sets.get(body.get("SitelinkSetId"))
            after["sitelinks"] = sets.get(sitelink_set)
        if callout_ids is not UNSET:
            before["callouts"] = [callouts[one["AdExtensionId"]]
                                  for one in body.get("AdExtensions") or []]
            after["callouts"] = [callouts[one] for one in callout_ids]
        result.append({"ad_id": ad["Id"], "before": before, "planned": after})
    return result


class Parser(argparse.ArgumentParser):
    def error(self, message):
        self.exit(2, redact(f"{self.prog}: {message}\n"))


def common(parser, leaf=False):
    default = argparse.SUPPRESS if leaf else None
    parser.add_argument("--env", choices=("production", "test_cabinet"), default=default)
    parser.add_argument("--account", default=default, help="логин кабинета")
    parser.add_argument("--json", action="store_true",
                        default=argparse.SUPPRESS if leaf else False)
    parser.add_argument("--no-cache", action="store_true",
                        default=argparse.SUPPRESS if leaf else False,
                        help="перечитать дополнения; перед записью чтение всегда свежее")


def mutation(parser):
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--apply", action="store_true", help="выполнить запись")
    mode.add_argument("--dry-run", action="store_true", help="проверка без записи (умолчание)")


def build_parser():
    parser = Parser(description=__doc__)
    common(parser)
    parent = argparse.ArgumentParser(add_help=False)
    common(parent, leaf=True)
    kinds = parser.add_subparsers(dest="kind", required=True)
    for kind, description in (("sitelinks", "наборы быстрых ссылок"), ("callouts", "уточнения")):
        group = kinds.add_parser(kind, parents=[parent], help=description)
        actions = group.add_subparsers(dest="action", required=True)
        get = actions.add_parser("get", parents=[parent], help="прочитать по ID")
        get.add_argument("--id", action="append", required=True, dest="ids")
        create = actions.add_parser("create", parents=[parent], help="создать в библиотеке кабинета")
        if kind == "sitelinks":
            create.add_argument("--file", required=True, help="JSON: список Title/Href/Description")
        else:
            create.add_argument("--text", action="append", required=True)
        mutation(create)
        delete = actions.add_parser("delete", parents=[parent], help="удалить из библиотеки; привязанные объекты API не удаляет")
        delete.add_argument("--id", action="append", required=True, dest="ids")
        mutation(delete)
    bind = kinds.add_parser("bind", parents=[parent], help="заменить или снять привязки выбранных объявлений")
    bind.add_argument("--ad", action="append", required=True, help="ID объявления; повторяется")
    links = bind.add_mutually_exclusive_group()
    links.add_argument("--sitelink-set", help="итоговый ID набора быстрых ссылок")
    links.add_argument("--clear-sitelinks", action="store_true")
    callouts = bind.add_mutually_exclusive_group()
    callouts.add_argument("--callout-id", action="append", help="полный итоговый список уточнений; флаг повторяется")
    callouts.add_argument("--clear-callouts", action="store_true")
    mutation(bind)
    return parser


def run(args):
    limits = Limits.load()
    operations, plan, notes, reused = [], [], [], []
    if args.kind == "bind":
        identifiers = list(dict.fromkeys(positive(one) for one in args.ad))
        sitelink_set = (None if args.clear_sitelinks else positive(args.sitelink_set)
                       if args.sitelink_set is not None else UNSET)
        callout_ids = ([] if args.clear_callouts else
                       list(dict.fromkeys(positive(one) for one in args.callout_id))
                       if args.callout_id is not None else UNSET)
        if sitelink_set is UNSET and callout_ids is UNSET:
            raise DirectFailure("Назовите новый набор, итоговые уточнения или поле для очистки.")
    elif args.action == "create":
        values = read_links(args.file) if args.kind == "sitelinks" else args.text
    else:
        identifiers = list(dict.fromkeys(positive(one) for one in args.ids))

    client = Client.from_env(profile=args.env, account=args.account, warn=warn)
    accounts = Accounts.load(client, warn=warn)
    account = resolve_account(accounts, client, args.account)
    if args.kind != "bind" and args.action == "get":
        related = read_selected(client, accounts, account, args.kind, identifiers, limits,
                                reuse=not args.no_cache)
        say(json.dumps({"account": account, **related}, ensure_ascii=False, indent=None if args.json else 2))
        return 0 if related["ExtensionsRead"]["complete"] else 1
    if args.kind == "bind":
        records = read_ads(client, accounts, account, identifiers, limits)
        operations = build_binding_operations(records, sitelink_set=sitelink_set, callout_ids=callout_ids)
        if callout_ids is not UNSET and callout_ids:
            active = read_active_callouts(client, accounts, account, limits, callout_ids)
            unavailable = [one for one in callout_ids if one not in active]
            if unavailable:
                raise DirectFailure("Уточнения удалены или недоступны в выбранном кабинете: "
                                    + ", ".join(map(str, unavailable)))
        selection = ids_of(records)
        if sitelink_set is UNSET:
            selection["sitelink_ids"] = []
        if callout_ids is UNSET:
            selection["extension_ids"] = []
        if sitelink_set is not UNSET and sitelink_set is not None:
            selection["sitelink_ids"].append(sitelink_set)
        if callout_ids is not UNSET:
            selection["extension_ids"].extend(callout_ids)
        related = require_complete(read_related(client, accounts, account, limits=limits,
                                                cache=Cache(account, reuse=False), **selection))
        plan = binding_plan(records, related, sitelink_set, callout_ids)
        notes = [json.dumps(one, ensure_ascii=False) for one in plan]
        notes.append("Меняются только выбранные объявления. Старые наборы и уточнения остаются в библиотеке. Возможна повторная модерация.")
    elif args.action == "create":
        if args.kind == "callouts":
            active = read_active_callouts(client, accounts, account, limits)
            by_text = {one["Callout"]["CalloutText"]: identifier for identifier, one in active.items()}
            values = list(dict.fromkeys(values))
            reused = [{"Id": by_text[text], "CalloutText": text} for text in values if text in by_text]
            values = [text for text in values if text not in by_text]
            notes = [f"Использовать существующее уточнение {one['Id']}: {one['CalloutText']}"
                     for one in reused]
        if values:
            operations = [create_operation(args.kind, values)]
    elif args.action == "delete":
        related = require_complete(read_selected(client, accounts, account, args.kind, identifiers, limits))
        if args.kind == "callouts":
            identifiers = list(read_active_callouts(client, accounts, account, limits, identifiers))
        if identifiers:
            operations = [delete_operation(args.kind, identifiers)]
        notes = ["Удаление из библиотеки: " + json.dumps(related[COLLECTIONS[args.kind]], ensure_ascii=False),
                 "Привязки объявлений эта команда не снимает; используйте bind для их изменения."]
    if not operations:
        result = {"ok": True, "applied": False, "account": account,
                  "summary": "Изменения не нужны: выбранное состояние уже установлено.",
                  "binding_plan": plan}
        if reused:
            result["reused_callouts"] = reused
        say(json.dumps(result, ensure_ascii=False) if args.json else "\n".join([*notes, result["summary"]]))
        return 0
    seen = []
    engine = Writer(client, account, accounts=accounts, apply=args.apply,
                    show=showing(seen=seen, quiet=args.json, notes=notes), warn=warn)
    report = engine.run(Task("быстрые ссылки и уточнения", operations))
    verified = []
    if args.kind == "bind" and report.applied and report.accepted:
        accepted = list(dict.fromkeys(positive(one) for one in report.accepted))
        try:
            after = read_ads(client, accounts, account, accepted, limits)
            if build_binding_operations(after, sitelink_set=sitelink_set, callout_ids=callout_ids):
                raise DirectFailure("Итоговые привязки не совпадают с планом.")
            for ad in after:
                body = ad.get("ResponsiveAd") or ad.get("TextAd")
                verified.append({key: ad.get(key) for key in ("Id", "State", "Status")}
                                | {key: body[key] for key in ("SitelinkSetId", "AdExtensions")})
        except DirectFailure as failure:
            report.record(UNVERIFIED, f"Директ принял запись, но итоговые привязки не подтверждены: {failure}")
            report.written = [one for one in report.written if positive(one) not in accepted]
        try:
            engine.journal.record({"kind": "bindings_checked", "binding_plan": plan,
                                   "accepted": accepted, "verified_ads": verified,
                                   "unknown": report.unknown})
        except DirectFailure as failure:
            report.record(UNLOGGED, str(failure))
    if args.json:
        result = report.machine()
        result.update(account=account, binding_plan=plan, verified_ads=verified)
        if reused:
            result["reused_callouts"] = reused
        say(json.dumps(result, ensure_ascii=False))
    else:
        lines = report.lines()
        if seen:
            lines = lines[len(report.preview):]
        say("\n".join(lines))
        if report.accepted:
            say("Принятые API ID: " + ", ".join(map(str, report.accepted)))
        for ad in verified:
            say(f"Объявление {ad['Id']}: привязки подтверждены · {ad['State']} · {ad['Status']}")
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
