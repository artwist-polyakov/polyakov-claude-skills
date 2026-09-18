#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""Что лежит в кэше скилла и как его очистить."""

from __future__ import annotations

import argparse
import json
import sys
import traceback
from pathlib import Path

# Строго до импортов: у скрипта и у модуля слоя кэша одно имя, и `scripts/`
# лежит на пути импорта первым как каталог запускаемого файла. Без этой строки
# `import cache` находит сам себя.
sys.path.insert(0, str(Path(__file__).resolve().parent / "lib"))

from cache import (  # noqa: E402  — путь импорта задаётся строкой выше
    Cache,
    human_age,
    human_size,
    outline,
    plural,
)
from config import (  # noqa: E402
    DirectFailure,
    preload_secrets,
    redact,
    short,
)


def warn(text: str) -> None:
    print(redact(text), file=sys.stderr)


class RedactingParser(argparse.ArgumentParser):
    """argparse печатает ошибки мимо redact() — здесь это исправляется."""

    def error(self, message: str):
        self.print_usage(sys.stderr)
        warn(f"{self.prog}: ошибка аргументов: {message}")
        raise SystemExit(2)






def rows_of(entries: list) -> list:
    """Перечень записей в виде строк таблицы."""
    folder_width = max(len(entry["folder"] or "—") for entry in entries)
    name_width = max(len(entry["name"]) for entry in entries)
    lines = []
    for entry in entries:
        count = "—" if entry["count"] is None else str(entry["count"])
        age = "?" if entry["age"] is None else human_age(entry["age"])
        lines.append(
            f"  {(entry['folder'] or '—'):<{folder_width}}  "
            f"{entry['name']:<{name_width}}  "
            f"{entry['layer']:<12}  {age:<16}  "
            f"{human_size(entry['size']):>9}  {count:>7}"
        )
    return lines


def machine(value) -> None:
    """Машиночитаемый вывод — через то же вырезание, что и сводка.

    `--json` печатает пути, а имя каталога кабинета приходит из `--account`:
    токен, набранный туда по ошибке, оказался бы в выводе, который сводка
    вырезать умеет, а этот путь — нет.

    allow_nan=False — страховка: вывод, объявленный машиночитаемым, не может
    содержать того, что стандартный JSON не разбирает."""
    print(redact(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False)))


def show(cache: Cache, as_json: bool) -> int:
    entries = cache.entries()
    if as_json:
        machine([
            {
                "account": entry["folder"],
                "name": entry["name"],
                "layer": entry["layer"],
                "age_seconds": None if entry["age"] is None else round(entry["age"]),
                "size_bytes": entry["size"],
                "count": entry["count"],
                "path": str(entry["path"]),
                "index": None if entry["index_path"] is None else str(entry["index_path"]),
            }
            for entry in entries
        ])
        return 0

    if not cache.root.is_dir():
        outline([f"Каталога кэша нет: {short(cache.root)}. Кэш пуст."])
        return 0
    if not entries:
        outline([f"Кэш: {short(cache.directory)}", "Кэш пуст."])
        return 0
    total = sum(entry["size"] for entry in entries)
    counted = plural(len(entries), "запись", "записи", "записей")
    outline(
        [f"Кэш: {short(cache.directory)}"]
        + rows_of(entries)
        + [f"Итого: {counted}, {human_size(total)}"],
    )
    return 0


def clear(cache: Cache, whole: bool, as_json: bool) -> int:
    removed = cache.forget(everything=whole)
    where = "весь кэш" if whole else f"кабинет {cache.account}"
    if as_json:
        machine({"cleared": "all" if whole else cache.account,
                 "removed_files": removed})
    else:
        outline([f"Очищен {where}: удалено файлов — {removed}."])
    return 0


def main(argv=None) -> int:
    preload_secrets()
    parser = RedactingParser(
        description="Просмотр и очистка кэша скилла. Сеть не задействуется.",
    )
    parser.add_argument(
        "--account",
        metavar="ЛОГИН",
        help="логин кабинета; без него показываются все кабинеты",
    )
    parser.add_argument(
        "--clear", action="store_true",
        help="очистить кэш; требует --account ЛОГИН или --all",
    )
    parser.add_argument(
        "--all", action="store_true",
        help="цель очистки — кэш целиком, включая все кабинеты",
    )
    parser.add_argument(
        "--json", action="store_true", help="машиночитаемый вывод вместо сводки",
    )
    args = parser.parse_args(argv)

    if args.all and not args.clear:
        parser.error("--all задаёт цель очистки и без --clear ничего не значит")
    if args.clear and args.all and args.account:
        parser.error("--all и --account задают разные цели очистки, выберите одну")
    if args.clear and not args.all and not args.account:
        parser.error(
            "--clear требует цели: --account ЛОГИН для одного кабинета "
            "или --all для кэша целиком"
        )

    try:
        cache = Cache(args.account or "")
    except DirectFailure as exc:
        warn(str(exc))
        return 2

    try:
        if args.clear:
            return clear(cache, whole=args.all, as_json=args.json)
        # Отсутствующий каталог разбирает `show`, а не эта ветка: с `--json`
        # ответ обязан оставаться разбираемым, а не превращаться во фразу.
        return show(cache, as_json=args.json)
    except DirectFailure as exc:
        warn(str(exc))
        return 1
    except Exception:
        # Чужие данные и файловая система: непредвиденный сбой не должен
        # вылетать трассировкой мимо вырезания секретов.
        warn("Непредвиденный сбой. Токен из трассировки вырезан.")
        warn(traceback.format_exc())
        return 1


if __name__ == "__main__":
    sys.exit(main())
