#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""Кросс-минусовка: развести пересекающиеся фразы и группы."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "lib"))

import cache as cache_module  # noqa: E402
import phrases  # noqa: E402
from accounts import Accounts, resolve_account  # noqa: E402
from config import DirectFailure, excerpt, preload_secrets, redact  # noqa: E402
from direct import Client  # noqa: E402
from writer import showing, Limits, Task, Writer, unrun  # noqa: E402

# Что сказать до вопроса. Последствие кросс-минусовки не видно из списка фраз:
# минус-слова добавляются, а показов становится меньше.
CROSS_NOTE = (
    "Кросс-минусовка необратима по последствиям. Минус-слова добавляются, но "
    "показы, которых после этого не будет, не возвращаются возвратом поля: "
    "накопленный охват уже потерян. Особенно у группы, которую специально "
    "сделали широкой — ей подходит каждый сосед. Перечень разводимых групп "
    "выше: посмотрите на него прежде, чем на список фраз."
)

# Разрушающее уходит по одному элементу за вызов. Число здесь, а не
# в конвейере, потому что метод обычный: `Keywords.update` разрушающим не
# является, разрушающей его делает то, что им пишут.
ONE_AT_A_TIME = 1


def warn(text: str) -> None:
    print(redact(text), file=sys.stderr)


def say(text: str) -> None:
    print(redact(text))


# --------------------------------------------------------------------------
# Чтение
# --------------------------------------------------------------------------

def read_keywords(client, account, accounts, args) -> list:
    """Фразы выбранных групп или кампании — без автотаргетингов.

    Автотаргетинг отделяется здесь, а не в расчёте: он живёт тем же сервисом
    `Keywords`, но фразой не является, и дописанные ему минус-слова превратили
    бы служебную строку `---autotargeting` в мусор."""
    criteria = {}
    if args.group:
        criteria["AdGroupIds"] = [int(one) for one in args.group]
    if args.campaign is not None:
        criteria["CampaignIds"] = [int(args.campaign)]
    if not criteria:
        raise DirectFailure(
            "Не сказано, какие фразы разводить: назовите `--group` или "
            "`--campaign`."
        )
    params = dict(phrases.read_params())
    params["SelectionCriteria"] = criteria
    need = Limits.load().units_cost(phrases.KEYWORDS, "get")
    found = list(client.get_all(
        phrases.KEYWORDS, params, account=account,
        use_operator_units=lambda: accounts.use_operator_units(account,
                                                               need=need)))
    skipped = sum(1 for one in found if phrases.is_autotargeting(one))
    if skipped:
        warn(f"Пропущено автотаргетингов: {skipped}. Автотаргетинг не фраза: "
             f"минус-слова ему дописать некуда.")
    mine = [one for one in found if not phrases.is_autotargeting(one)]
    # Названная и не отозвавшаяся группа — отказ, а не молчаливое сужение
    # отбора. Цена молчания здесь выше обычного: пропала бы ровно та группа,
    # с которой разводили, а по оставшейся кросс-минусовка всё равно прошла
    # бы — необратимо и не с тем результатом, который подтверждали.
    #
    # Считается по **фразам**, а не по всему прочитанному. Автотаргетинг
    # Директ заводит в каждой группе сам, и группа без единой фразы отозвалась
    # бы им одним: покрытие сошлось бы, а разводить в ней было бы нечего — и
    # сравнение между группами тихо свелось бы к одной.
    lost = sorted({int(one) for one in args.group}
                  - {one.get("AdGroupId") for one in mine})
    if lost:
        raise DirectFailure(
            f"В названных группах нет фраз: "
            f"{', '.join(str(one) for one in lost)}. Их нет, они в другом "
            f"кабинете, закрыты правами — или в них правда пусто, и Директ "
            f"отозвался одним автотаргетингом. Разводить часть названного "
            f"нельзя: пропавшая группа и есть та, с которой разводят, а по "
            f"оставшимся правка всё равно пройдёт."
        )
    if len(mine) < 2:
        raise DirectFailure(
            f"Разводить нечего: прочитано фраз {len(mine)}. Кросс-минусовка "
            f"сравнивает фразы между собой, и одной для этого мало."
        )
    return mine


# --------------------------------------------------------------------------
# Расчёт
# --------------------------------------------------------------------------

def by_direct(client, account, accounts, records, operations) -> tuple:
    """План от `KeywordsResearch.deduplicate`: что изменить и что удалить.

    Метод читающий и в кабинете ничего не меняет: он возвращает инструкции,
    а записывает их конвейер. Форма ответа нарочно близка к запросам
    `Keywords.add`, `.update` и `.delete`."""
    outcome = client.call(
        "keywordsresearch", "deduplicate",
        {"Keywords": [{"Id": one["Id"], "Keyword": one["Keyword"]}
                      for one in records],
         "Operation": list(operations)},
        account=account,
        use_operator_units=lambda need=Limits.load().units_cost(
            "keywordsresearch", "deduplicate", len(records)): (
                accounts.use_operator_units(account, need=need)),
    ).result or {}
    added = outcome.get("Add") or []
    if added:
        # Ответ с `Add` означает, что метод предлагает завести фразы, которых
        # мы ему не давали. Так бывает у входа без идентификаторов; у нас
        # идентификатор есть у каждой. Молча выбросить предложение нельзя —
        # это была бы половина плана, выданная за целый.
        raise DirectFailure(
            f"`KeywordsResearch.deduplicate` предлагает завести "
            f"{len(added)} новых фраз, хотя все присланные имели "
            f"идентификатор. Это не кросс-минусовка, а другой ответ, и "
            f"выполнять его вслепую нельзя: "
            f"{excerpt([one.get('Keyword') for one in added[:3]], 120)}"
        )
    changed = {one["Id"]: one["Keyword"] for one in outcome.get("Update") or []}
    dropped = sorted((outcome.get("Delete") or {}).get("Ids") or [])
    return changed, dropped


def by_own(records, *, single_word_only: bool) -> tuple:
    return phrases.cross_minus(records, single_word_only=single_word_only), []


# --------------------------------------------------------------------------
# Показ
# --------------------------------------------------------------------------

def separation_lines(records, changed, dropped) -> list:
    """Перечень разводимых групп — то, ради чего команда отдельная.

    Идёт **первым**, до строк «было → станет»: список фраз читается как
    добавление минус-слов, а список групп — как то, чем это кончится.

    Разведение внутри группы и разведение между группами названы разными
    словами. Считаются они одинаково, а означают разное: внутри группы фразы
    перестают конкурировать между собой, между группами — трафик уходит из
    одной группы в другую, и это как раз тот случай, где широкая группа может
    остаться без показов."""
    names = {one["Id"]: one.get("AdGroupId") for one in records}
    lines = []
    for here, there, count in phrases.separated(records, changed):
        lines.append(
            f"внутри группы {here}: фраз {count}" if here == there
            else f"группа {here} разводится с группой {there}: фраз {count}")
    if dropped:
        where = sorted({names.get(one) for one in dropped}, key=str)
        lines.append(
            f"склейка дублей удалит фраз {len(dropped)} в группах "
            f"{', '.join(str(one) for one in where)}"
        )
    return lines


def deletion_lines(records, dropped) -> list:
    """Что именно исчезнет — текстом, а не одним идентификатором.

    Предпросмотр конвейера показывает удаление строкой «фраза 123 · удаление»:
    полей у удаления нет, показывать «было → станет» не из чего. А согласие
    даётся на показанное, и здесь показать надо саму фразу."""
    text = {one["Id"]: one.get("Keyword") for one in records}
    group = {one["Id"]: one.get("AdGroupId") for one in records}
    return [f"фраза {one} (группа {group.get(one)}) будет удалена как дубль: "
            f"{text.get(one)}" for one in dropped]


# --------------------------------------------------------------------------
# Запуск
# --------------------------------------------------------------------------

def unchanged_input(read, records):
    """Условие: все фразы, из которых считали, ещё те же.

    Кросс-минусовка считает не по правимой фразе, а по **паре**: минус-слова
    для широкой фразы берутся из текста узкой. Узкая при этом не правится и в
    операцию не попадает — значит конвейер её не читает ни снимком, ни
    перечитыванием, и правка или удаление узкой между расчётом и записью
    проходят незамеченными.

    Цена такой незамеченности прямая: широкая фраза получает минус-слово,
    разводящее её с текстом, которого больше нет. Трафик, который должен был
    уйти узкой, не уходит никому — а это ровно тот исход, ради которого
    кросс-минусовка показывается человеку списком.

    Поэтому условие перечитывает **весь** вход расчёта и сверяет тексты. Читает
    оно на каждой проверке, включая ту, что перед самой записью."""
    def check(_known) -> list:
        # `read_keywords` отдаёт **список** записей — тот же, из которого
        # считали. Раскладывает его по идентификаторам условие, а не
        # вызывающий код: подставной вызов, раскладывающий сам, разошёлся бы с
        # боевым, и проверка подтверждала бы собственную выдумку.
        fresh = {one["Id"]: one for one in read()}
        said = []
        for number, record in sorted(records.items()):
            now = fresh.get(number)
            if now is None:
                said.append(f"фраза {number} «{record.get('Keyword')}» исчезла "
                            f"между расчётом и записью")
            elif now.get("Keyword") != record.get("Keyword"):
                said.append(f"фраза {number} изменилась между расчётом и "
                            f"записью: было «{record.get('Keyword')}», стало "
                            f"«{now.get('Keyword')}»")
        # Появившаяся фраза меняет ответ ровно так же, как исчезнувшая: она
        # могла бы потребовать других минус-слов, а могла оказаться дублем.
        # Сверяется поэтому весь состав, а не только знакомые идентификаторы.
        for number in sorted(set(fresh) - set(records)):
            said.append(f"фраза {number} «{fresh[number].get('Keyword')}» "
                        f"появилась после расчёта")
        if not said:
            return []
        return ["Развести по устаревшему расчёту нельзя: "
                + "; ".join(said[:5])
                + (f" и ещё {len(said) - 5}" if len(said) > 5 else "")
                + ". Минус-слова считаются по паре фраз, и правка любой из "
                  "них меняет ответ. Повторите команду — расчёт соберётся "
                  "заново."]
    return check


def build_task(records, changed, dropped, *, read) -> Task:
    """Задача целиком: правки по одной, удаления по одному.

    Правка и удаление одного и того же объекта в одной задаче невозможны:
    подтверждённая правка удаления не переживёт, а сверка застанет её до него.
    Конвейер это отвергает сам; здесь то же самое говорится своими словами,
    потому что причина у нас конкретная — план пришёл от `deduplicate`.

    `read` обязателен и умолчания не имеет: по нему условие перечитывает вход
    расчёта. Операции покрывают только то, что пишется, а считалось по всему
    прочитанному — и узкая фраза, из которой взяты минус-слова, в операции не
    попадает вовсе."""
    both = sorted(set(changed) & set(dropped))
    if both:
        raise DirectFailure(
            f"План правит и удаляет одни и те же фразы "
            f"({', '.join(str(one) for one in both[:5])}). Так не бывает: "
            f"подтверждённая правка удаления не переживёт. План разбирать "
            f"вслепую нельзя."
        )
    known = {one["Id"]: one for one in records}
    guard = unchanged_input(read, known)
    operations = []
    if changed:
        operations.append(phrases.update_operation(
            changed, known, batch=ONE_AT_A_TIME, guard=guard))
    if dropped:
        operations.append(phrases.lifecycle_operation("delete", dropped,
                                                      guard=guard))
    said = []
    if changed:
        said.append(f"правок {len(changed)}")
    if dropped:
        said.append(f"удалений {len(dropped)}")
    return Task(f"кросс-минусовка: {', '.join(said)}", operations)


def report_out(report, args, *, shown=False) -> int:
    if args.json:
        # Перечень полей — у самого отчёта (`Report.machine`), а не здесь.
        # Четыре команды держали четыре копии одного словаря, и новое поле
        # попадало бы в три из четырёх: программа, читающая только `written` и
        # `failed`, приняла бы «записалось вопреки отказу» за обычный отказ и
        # повторила команду.
        say(json.dumps(report.machine(), ensure_ascii=False))
    else:
        lines = report.lines()
        if shown:
            lines = lines[len(report.preview):]
        cache_module.outline(lines, path=report.journal)
    return 0 if report.ok else 1


class Parser(argparse.ArgumentParser):
    def error(self, message: str):
        self.exit(2, redact(f"{self.prog}: {message}\n"))


def fits_engine(args) -> None:
    """Аргументы, которых выбранный движок не понимает, — отказ, а не молчание.

    У движков разные ручки: склейку дублей умеет только `deduplicate`, ширину
    правила — только свой. Названный не тому движку аргумент ничего бы не
    изменил, а команда отчиталась бы так, будто изменил, — и человек считал бы
    сделанным то, чего не делалось.

    Проверяется до похода в сеть: несогласованность аргументов видна из них
    самих, и платить за неё чтением кабинета незачем."""
    if args.engine == "direct" and args.all_words:
        raise DirectFailure(
            "Ширину правила задаёт только свой движок: `deduplicate` разводит "
            "соседей, отличающихся на одно слово, и другого режима у него нет. "
            "Названный здесь аргумент ничего бы не изменил, а команда "
            "отчиталась бы так, будто изменил."
        )
    if args.engine == "own" and args.merge:
        raise DirectFailure(
            "Склейка дублей — операция `KeywordsResearch`, своего движка у неё "
            "нет: он умеет только разводить, а не удалять. Названный здесь "
            "аргумент ничего бы не изменил."
        )


def build_parser() -> Parser:
    parser = Parser(description="Кросс-минусовка фраз и групп.")
    parser.add_argument("--env", choices=("production", "test_cabinet"),
                        help="контур и набор переменных")
    parser.add_argument("--account", metavar="ЛОГИН",
                        help="логин кабинета; при отсутствии берётся активный")
    parser.add_argument("--apply", action="store_true",
                        help="выполнить запись; без него — проверка")
    parser.add_argument("--dry-run", action="store_true", dest="dry_run",
                        help="проверка без записи (умолчание)")
    parser.add_argument("--json", action="store_true",
                        help="машиночитаемый вывод")
    parser.add_argument("--group", action="append", type=int, default=[])
    parser.add_argument("--campaign", type=int)
    parser.add_argument("--engine", choices=("direct", "own"), default="direct",
                        help="чей алгоритм: Директа или рабочей кросс-минусовки")
    parser.add_argument("--merge-duplicates", action="store_true",
                        dest="merge",
                        help="склеивать дубли (только для движка Директа)")
    parser.add_argument("--all-words", action="store_true", dest="all_words",
                        help="свой движок: разводить соседей с любым числом "
                             "лишних слов, а не только с одним")
    return parser


def run(args) -> int:
    client = Client.from_env(profile=args.env, account=args.account, warn=warn)
    accounts = Accounts.load(client, warn=warn)
    account = resolve_account(accounts, client, args.account)
    records = read_keywords(client, account, accounts, args)

    if args.engine == "direct":
        operations = ["ELIMINATE_OVERLAPPING"]
        if args.merge:
            operations.append("MERGE_DUPLICATES")
        changed, dropped = by_direct(client, account, accounts, records,
                                     operations)
    else:
        changed, dropped = by_own(records, single_word_only=not args.all_words)

    if not changed and not dropped:
        said = "Разводить нечего: пересечений между фразами не нашлось."
        say(json.dumps(unrun(said, ok=True), ensure_ascii=False)
            if args.json else said)
        return 0

    # Всё это уходит человеку **одним** показом — вместе с вопросом, а не до
    # него. Обработчик согласия зовётся и в режиме проверки, поэтому второй
    # печати перед прогоном не нужно: она была бы теми же строками дважды.
    notes = separation_lines(records, changed, dropped) + [
        CROSS_NOTE,
        f"Правки уходят по одной: вызовов будет {len(changed)}"
        + (f" плюс {len(dropped)} на удаление" if dropped else "") + ".",
    ] + deletion_lines(records, dropped)

    seen = []
    engine = Writer(client, account, accounts=accounts, apply=args.apply,
                    show=showing(seen=seen, quiet=args.json, notes=notes),
                    warn=warn)
    report = engine.run(build_task(
        records, changed, dropped,
        read=lambda: read_keywords(client, account, accounts, args)))
    return report_out(report, args, shown=bool(seen))


def refused(said: str, args) -> int:
    """Отказ команды: в stderr человеку, объектом — программе.

    Машиночитаемый вывод отвечает объектом и на отказе. Иначе программа,
    читающая stdout, получает на отказе пустоту и падает там, где команда как
    раз всё объяснила. Правило стоит в одном месте на все отказы: расставленное
    по веткам, оно обходится той, где его забыли."""
    warn(said)
    if getattr(args, "json", False):
        say(json.dumps(unrun("отказ", problems=[said]), ensure_ascii=False))
    return 1


def main(argv=None) -> int:
    preload_secrets()
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.apply and args.dry_run:
        parser.error("--apply и --dry-run взаимоисключающие: писать или "
                     "проверять, а не и то и другое")
    try:
        fits_engine(args)
        return run(args)
    except DirectFailure as failure:
        return refused(str(failure), args)
    except OSError as failure:
        return refused(redact(f"Не удалось прочитать или записать файл: "
                              f"{failure}"), args)


if __name__ == "__main__":
    sys.exit(main())
