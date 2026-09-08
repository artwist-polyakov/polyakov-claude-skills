#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""Кабинеты: список, поиск и выбор того, с которым работаем."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# Строго до импортов: у этого скрипта и у модуля резолвера одно имя, как и у
# `scripts/cache.py` со слоем кэша. Каталог запускаемого файла лежит на пути
# импорта первым, поэтому без этой строки `import accounts` находит сам себя.
sys.path.insert(0, str(Path(__file__).resolve().parent / "lib"))

from accounts import (  # noqa: E402  — путь импорта задаётся строкой выше
    AGENCY,
    Accounts,
    Ambiguous,
    NotFound,
    age_of,
    cabinets_said,
    tsv,
)
from cache import add_arguments  # noqa: E402
from config import DirectFailure, preload_secrets, redact, short  # noqa: E402
from direct import Client, thousands  # noqa: E402
from ui_links import account_url  # noqa: E402

SHOWN = 10
SHOWN_MATCHES = 12

# То же ограничение для машиночитаемого вывода. Оно не про строки — JSON
# печатается одной, — а про объём: перечень из трёхсот кабинетов в stdout
# съедает контекстное окно агента ровно так же, как человеческая простыня.
# Полный список берут из TSV или `--csv`, а сколько нашлось всего, говорит
# поле `matches_total`.
JSON_MATCHES = 25


def say(text: str = "") -> None:
    """Единственный путь вывода: секреты вырезаются здесь, а не у вызывающих."""
    print(redact(text))


def warn(text: str) -> None:
    print(redact(text), file=sys.stderr)


def row(cabinet, mark: str = "") -> str:
    name = cabinet.name or "—"
    balance = cabinet.money(currency=False) or "—"
    archived = " · архивный" if cabinet.archived else ""
    return (f"  {cabinet.login:<28} {name[:32]:<32} {cabinet.currency:<4} "
            f"{balance:>12}{archived}{mark} · {account_url(cabinet.login)}")


def show_cabinet(cabinet) -> str:
    parts = [cabinet.login, cabinet.name or "—", cabinet.currency or "—"]
    # Без валюты: она уже названа отдельной частью строки, и повтор читается
    # как две разные величины.
    balance = cabinet.money(currency=False)
    if balance:
        parts.append(f"баланс {balance}")
    if cabinet.archived:
        parts.append("архивный")
    parts.append(account_url(cabinet.login))
    return " · ".join(part for part in parts if part)


def report(accounts: Accounts, spent: int) -> None:
    say(f"Кабинеты: {accounts.summary()}")
    age = age_of(accounts.checked_at)
    say(f"Список от {accounts.checked_at or '—'}"
        f"{f' ({age})' if age else ''} · {accounts.cache_path()}")
    current = accounts.current()
    if current is not None:
        say(f"Активный: {show_cabinet(current)}")
    elif accounts.kind == AGENCY:
        say("Активный кабинет не выбран: accounts.py --use <логин или название>")
    if not accounts.balances_read:
        say("Балансы не читались — колонка пуста.")

    living = [cabinet for cabinet in accounts.cabinets if not cabinet.archived]
    if living:
        say()
        say(f"Действующие ({len(living)}):" if len(living) <= SHOWN
            else f"Действующие, первые {SHOWN} из {len(living)}:")
        for cabinet in living[:SHOWN]:
            say(row(cabinet))
    if len(accounts.cabinets) > len(living[:SHOWN]):
        say()
        say(f"Поиск по всем: accounts.py --search <запрос> "
            f"или grep -i <запрос> {accounts.cache_path()}")
    if spent:
        say(f"Обновление списка стоило {thousands(spent)} баллов.")


def report_matches(accounts: Accounts, query: str, found: list, archived: bool) -> None:
    hidden = 0 if archived else sum(1 for item in accounts.cabinets if item.archived)
    tail = f", архивные скрыты: {hidden}" if hidden else ""
    say(f"Запрос «{query}» · нашлось {len(found)} из "
        f"{cabinets_said(len(accounts.cabinets))}{tail}")
    for match in found[:SHOWN_MATCHES]:
        say(row(match.cabinet, f" · {match.reason}"))
    if len(found) > SHOWN_MATCHES:
        say(f"  … и ещё {len(found) - SHOWN_MATCHES}")
    if len(found) == 1:
        say()
        say(f"Однозначно. Сделать активным: accounts.py --use {found[0].cabinet.login}")
    elif len(found) > 1:
        say()
        say("Неоднозначно — уточните запрос или назовите логин точно.")


def cabinet_json(cabinet) -> dict:
    return dict(cabinet.as_dict(), account_url=account_url(cabinet.login))


def as_json(accounts: Accounts, found=None) -> dict:
    """Машиночитаемый ответ. Перечня кабинетов в нём нет без запроса.

    Правило «полные данные в файл, в stdout сводка» действует и здесь: у
    боевого агентства 258 кабинетов, и перечень целиком — это тысячи строк в
    контекстном окне вместо пути к TSV. Поэтому без `--search` возвращается
    сводка, а с ним — совпадения, и тоже не больше `JSON_MATCHES`: запрос,
    отвечающий сотней кабинетов, — это неоднозначность, а не выгрузка."""
    current = accounts.current()
    body = {
        "env": accounts.profile,
        "kind": accounts.kind,
        "owner": accounts.owner,
        "checked_at": accounts.checked_at,
        "balances_read": accounts.balances_read,
        "active": None if current is None else cabinet_json(current),
        "cache": accounts.cache_path(),
        "total": len(accounts.cabinets),
    }
    if found is not None:
        body["matches_total"] = len(found)
        body["matches"] = [
            dict(cabinet_json(match.cabinet), matched=match.field, exact=match.exact)
            for match in found[:JSON_MATCHES]
        ]
    return body


def dump_csv(accounts: Accounts, path: Path) -> None:
    """Полная выгрузка в файл — тем же TSV, что и кэш."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(tsv({accounts.profile: accounts.as_dict()}), encoding="utf-8")


class Parser(argparse.ArgumentParser):
    """Ошибка аргументов — код 2, и без секретов в тексте."""

    def error(self, message: str):
        self.exit(2, redact(f"{self.prog}: {message}\n"))


def main(argv=None) -> int:
    # До разбора аргументов, а не после: argparse печатает негодный аргумент
    # сам, и токен, случайно попавший в командную строку, ушёл бы в stderr
    # раньше, чем скилл узнал бы, что это токен. Вырезать можно только то, что
    # уже запомнено.
    preload_secrets()

    parser = Parser(description="Кабинеты Директа: список, поиск, выбор активного.")
    parser.add_argument("--env", choices=("production", "test_cabinet"),
                        help="контур и набор переменных")
    parser.add_argument("--search", metavar="ЗАПРОС",
                        help="логин, название или домен сайта клиента")
    parser.add_argument("--use", metavar="ЗАПРОС",
                        help="запомнить кабинет активным в кэше")
    parser.add_argument("--archived", action="store_true",
                        help="искать и среди архивных кабинетов")
    # Флаг заводится общей функцией слоя кэша, а не своими словами: одинаково
    # названный, но по-разному понятый `--no-cache` хуже отсутствующего.
    add_arguments(parser)
    parser.add_argument("--csv", metavar="ФАЙЛ", type=Path,
                        help="выгрузить полный список в файл")
    parser.add_argument("--json", action="store_true",
                        help="машиночитаемый вывод")
    args = parser.parse_args(argv)
    if args.search and args.use:
        # Молча предпочесть один другому — значит выполнить не ту команду, о
        # которой просили, и написать об этом в вывод, который никто не читает.
        parser.error("--search и --use взаимоисключающие: "
                     "первый показывает кандидатов, второй выбирает кабинет")

    try:
        client = Client.from_env(profile=args.env, warn=warn)
        accounts = Accounts.load(client, refresh=args.no_cache, warn=warn)
        spent = client.units.report()["spent"]

        found = None
        if args.use:
            cabinet = accounts.choose(args.use, archived=args.archived)
            accounts.select(cabinet)
            chosen = cabinet
        elif args.search:
            found = accounts.find(args.search, archived=args.archived)
            if not found:
                # Пустой результат — не сбой команды, но и не успех: код
                # возврата отличает «нашли» от «не нашли» без разбора текста.
                raise NotFound(
                    args.search, len(accounts.cabinets),
                    0 if args.archived
                    else sum(1 for item in accounts.cabinets if item.archived),
                )

        if args.csv:
            dump_csv(accounts, args.csv)

        if args.json:
            # Одной строкой: разбирает это программа, а человеку читаемость
            # добавит `jq`. Отступы стоили бы десятка строк вывода на каждый
            # кабинет — предел в тридцать строк на них и уходит.
            say(json.dumps(as_json(accounts, found), ensure_ascii=False))
        elif args.use:
            say(f"Активный кабинет: {show_cabinet(chosen)}")
        elif found is not None:
            report_matches(accounts, args.search, found, args.archived)
        else:
            report(accounts, spent)
        if args.csv and not args.json:
            say(f"Полный список: {short(args.csv)}")
    except Ambiguous as failure:
        warn(str(failure))
        for match in failure.matches[:SHOWN_MATCHES]:
            warn(row(match.cabinet, f" · {match.reason}"))
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
