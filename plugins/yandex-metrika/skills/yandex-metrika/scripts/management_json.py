#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# dependencies = []
# ///
"""Небольшой разборщик ответов API управления Метрики."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys


def read_json(path: str):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise SystemExit(f"Не удалось прочитать ответ API: {error}") from error


def scalar(value) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    return str(value)


def cell(value) -> str:
    return scalar(value).replace("\t", " ").replace("\r", " ").replace("\n", " ")


def nested_value(data, dotted_path: str):
    value = data
    for key in dotted_path.split("."):
        if not isinstance(value, dict) or key not in value:
            return None
        value = value[key]
    return value


def print_segments(data) -> None:
    print("segment_id\tstatus\tname")
    for segment in data.get("segments", []):
        print("\t".join(cell(segment.get(key))
                        for key in ("segment_id", "status", "name")))


def segment_object(segment) -> None:
    print(json.dumps({"segment": segment}, ensure_ascii=False,
                     separators=(",", ":")))


def find_segment(data, name: str, expression: str) -> int:
    active = [item for item in data.get("segments", [])
              if isinstance(item, dict)
              and item.get("segment_source") == "api"
              and item.get("status") == "active"]
    exact = [item for item in active
             if item.get("name") == name and item.get("expression") == expression]
    if len(exact) == 1:
        segment_object(exact[0])
        return 0
    if len(exact) > 1:
        print("Найдено несколько одинаковых активных API-сегментов; "
              "выберите нужный по ID.", file=sys.stderr)
        return 6
    if any(item.get("name") == name for item in active):
        return 5
    return 4


def find_segment_id(data, segment_id: int) -> bool:
    for segment in data.get("segments", []):
        if isinstance(segment, dict) and segment.get("segment_id") == segment_id:
            segment_object(segment)
            return True
    return False


def print_grants(data, owner: str) -> None:
    print("тип\tлогин\tроль\tмонетизация\tid_фильтра_доступа")
    print(f"владелец\t{cell(owner)}\town\ttrue\t-")
    for grant in data.get("grants", []):
        is_public = (not grant.get("user_login")
                     and grant.get("perm") == "public_stat")
        source = "публичный" if is_public else "прямой"
        login = grant.get("user_login") or "(публичный)"
        filters = grant.get("access_filters") or []
        filter_id = filters[0].get("id") if filters else "-"
        values = (source, login, grant.get("perm"),
                  grant.get("partner_data_access", False), filter_id)
        print("\t".join(cell(value) for value in values))


def print_segment_summary(data) -> None:
    segment = data.get("segment", {})
    print(f"ID сегмента: {scalar(segment.get('segment_id')) or '-'}")
    print(f"Название: {scalar(segment.get('name')) or '-'}")
    print(f"Состояние: {scalar(segment.get('status')) or '-'}")
    print(f"Выражение: {scalar(segment.get('expression')) or '-'}")
    print(f"Создан: {scalar(segment.get('create_time')) or '-'}")


def print_grant_summary(data) -> None:
    grant = data.get("grant", {})
    print(f"Логин: {scalar(grant.get('user_login')) or '-'}")
    print(f"Роль: {scalar(grant.get('perm')) or '-'}")
    print("Отчёты «Монетизация»: "
          f"{scalar(grant.get('partner_data_access', False))}")
    filters = grant.get("access_filters") or []
    if filters:
        print(f"ID фильтра доступа: {scalar(filters[0].get('id'))}")
    if grant.get("comment"):
        print(f"Комментарий: {scalar(grant['comment'])}")
    if grant.get("created_at"):
        print(f"Выдан: {scalar(grant['created_at'])}")


def missing_grant_error(data) -> bool:
    if data.get("code") != 400:
        return False
    errors = data.get("errors")
    if not isinstance(errors, list):
        return False
    markers = (
        "нет гранта",
        "не имеет гранта",
        "has no grant",
        "doesn't have a grant",
        "does not have a grant",
        "doesn't have grant",
        "does not have grant",
    )
    for item in errors:
        if not isinstance(item, dict):
            continue
        message = item.get("message")
        if (item.get("error_type") == "invalid_parameter"
                and item.get("location") == "user_login"
                and isinstance(message, str)
                and any(marker in message.casefold() for marker in markers)):
            return True
    return False


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("value", "segments-tsv", "grants-tsv",
                                            "segment-summary", "grant-summary",
                                            "find-segment", "find-segment-id",
                                            "missing-grant-error", "text-length"))
    parser.add_argument("file")
    parser.add_argument("argument", nargs="?")
    parser.add_argument("extra_argument", nargs="?")
    args = parser.parse_args(argv)
    if args.action == "text-length":
        print(len(sys.stdin.read()))
        return 0
    data = read_json(args.file)

    if args.action == "value":
        if not args.argument:
            parser.error("для value требуется путь к полю")
        print(scalar(nested_value(data, args.argument)))
    elif args.action == "segments-tsv":
        print_segments(data)
    elif args.action == "grants-tsv":
        print_grants(data, args.argument or "")
    elif args.action == "segment-summary":
        print_segment_summary(data)
    elif args.action == "grant-summary":
        print_grant_summary(data)
    elif args.action == "find-segment":
        if args.argument is None or args.extra_argument is None:
            parser.error("для find-segment требуются название и выражение")
        return find_segment(data, args.argument, args.extra_argument)
    elif args.action == "find-segment-id":
        if not args.argument or not args.argument.isascii() or not args.argument.isdigit():
            parser.error("для find-segment-id требуется числовой ID")
        if not find_segment_id(data, int(args.argument)):
            return 4
    else:
        return 0 if missing_grant_error(data) else 4
    return 0


if __name__ == "__main__":
    sys.exit(main())
