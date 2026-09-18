#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""Комплект заголовков и текстов: сборка из материала и разбор получившегося."""

from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "lib"))

import cache as cache_module  # noqa: E402
import combinatorial  # noqa: E402
import incoming  # noqa: E402
import phrases as phrases_lib  # noqa: E402
import responsive  # noqa: E402
import templates  # noqa: E402
from accounts import Accounts, resolve_account  # noqa: E402
from config import DirectFailure, excerpt, preload_secrets, redact  # noqa: E402
from direct import Client  # noqa: E402
from responsive import Kit  # noqa: E402
from writer import Limits  # noqa: E402

# Формы чужих файлов. Перечень полей закрыт: опечатка в имени — это молча
# потерянные сегменты или фразы, а не «поле, которого мы не поняли». Проверяет
# их `incoming` — один разбор чужого ввода на все команды скилла.
#
# Формы брифа здесь **нет**: бриф у человека один, и объявлена она один раз, в
# `combinatorial.BRIEF`. Своё объявление тут было, и оно не знало `callouts`,
# `sitelinks`, `vcard`, `price` и `images` — файл, годный для превью, эта
# команда отвергала (`P-03`).
#
# Поля, которые команда читает. Перечень не описывает форму — он говорит, чем
# команда пользуется, и остальное она принимает и не читает. Что именно, скажет
# человеку подсказка `--brief`: она собирается по общей форме, а не набирается
# руками, и поле, добавленное в форму, называет само.
BRIEF_READ = ("titles", "texts", "segments", "phrases", "group", "href",
              "display_url_path", "note", "marks")

ROW = incoming.Shape(
    fields={"phrase", "param1", "param2", "group", "id"},
    text=("phrase", "param1", "param2", "group"),
    # Идентификатор фразы — целое, и в таблице тоже. Прочитанный строкой из
    # `.csv` и числом из `.json`, он давал бы один набор данных с двумя
    # разными вердиктами.
    whole=("id",),
    # Набор приходит и таблицей, и объектом, а пустую ячейку от незаполненного
    # столбца таблица не отличает: пустое здесь означает отсутствие у всех
    # полей сразу, иначе вердикт зависел бы от вида записи.
    blank_absent=("phrase", "param1", "param2", "group", "id"),
    needed={"phrase": "Подставлять вместо шаблона нечего, а строка без фразы "
                      "в набор попала не просто так — это или пустая строка "
                      "таблицы, или не тот столбец."},
)

# Подсказка к `--mark`. Одна на все действия: разошедшиеся подсказки к одному
# аргументу — это два разных синтаксиса в глазах читателя.
MARK_HELP = ("пометка агента: `3x1:текст` — пара, `3:текст` — заголовок, `x1:текст` — текст, `:текст` — комплект. Повторяется")


def warn(text: str) -> None:
    print(redact(text), file=sys.stderr)


def say(text: str) -> None:
    print(redact(text))


# --------------------------------------------------------------------------
# Материал
# --------------------------------------------------------------------------

def read_brief(path) -> dict:
    """Бриф из файла, разобранный по общей форме `combinatorial.BRIEF`.

    Своего разбора здесь нет вовсе: типы, перечень полей, кодировка и пустое
    значение — общее правило скилла, и живёт оно в `incoming` (`F-15`). Своей
    формы тоже нет: бриф у человека один, и вердикт по составу его полей обязан
    быть один — у этой команды и у превью (`P-03`)."""
    return incoming.document(path, "Бриф", combinatorial.BRIEF)


def read_rows(path) -> list:
    """Набор данных: строки с фразой и параметрами уровня фразы.

    Принимаются два вида — JSON-массив и CSV с заголовком. Второй потому, что
    прайс и семантика приходят человеку таблицей, а не объектом, и заставлять
    его переводить одно в другое незачем. Разбирает оба `incoming.rows`, и
    вердикт от вида записи не зависит."""
    found = incoming.rows(path, "Набор данных", ROW)
    if not found:
        raise DirectFailure(
            f"В наборе данных {Path(path)} нет ни одной строки. Собирать "
            f"комплекты не из чего.")
    return found


def phrases_of_rows(rows) -> list:
    """Фразы набора данных вместе с параметрами уровня фразы.

    Пустая ячейка таблицы уже приведена к отсутствию: `templates.variants`
    сообщает о незаданном параметре только для `None`, и без этого адрес
    `…/{param1}/` превращался в `…//` при чистом отчёте, тогда как та же строка
    в JSON без `param1` блокировалась."""
    return [templates.Phrase(row["phrase"], param1=row.get("param1"),
                             param2=row.get("param2"),
                             identifier=row.get("id")) for row in rows]


def whole_argument(value: str) -> int:
    """Целое из аргумента команды. Строже, чем `type=int`.

    `argparse` с `type=int` принимает `5793_870037` и отдаёт 5793870037 — то
    есть стирает опечатку **до** любой нашей проверки, и чтение уходит к
    существующему чужому объекту. Разбор здесь тот же, что у брифа: одно
    правило на оба пути ввода."""
    try:
        return incoming.whole_number(value, "Значение аргумента")
    except DirectFailure as failure:
        raise argparse.ArgumentTypeError(str(failure)) from None


def chosen_ad(args):
    """Объявление, названное аргументом. Ноль — не идентификатор.

    Тем же правилом, что и группа: ноль ложен при проверке на истинность, и
    названный нулём источник данных пропадал молча — команда не читала
    объявление, брала комплект из брифа и выходила с кодом 0."""
    said = getattr(args, "ad", None)
    if said is None:
        return None
    number = incoming.whole_number(said, "Идентификатор объявления")
    if number <= 0:
        raise DirectFailure(
            f"Идентификатор объявления {number} — не идентификатор: они "
            f"положительные. Названный нулём источник комплекта молча пропал "
            f"бы, а команда взяла бы комплект из брифа.")
    return number


def chosen_group(args, brief: dict):
    """Группа, чьи фразы подставлять: аргумент команды либо поле брифа.

    Поле, объявленное в брифе и молча не применённое, — это половина просьбы,
    выданная за целое: фразы не прочитались бы, отчёт вышел бы «подстановка не
    проверялась» и выглядел бы выполненной проверкой.

    Аргумент старше брифа: он набран под конкретный запуск, а бриф лежит в
    файле с прошлого раза."""
    said = getattr(args, "group", None)
    if said is None:
        said = brief.get("group")
    if said is None:
        return None
    # Тем же правилом, что и номер места в пометке: `int()` превратил бы
    # `5793870037.8` в существующий идентификатор соседней группы, и команда
    # прочитала бы не те фразы, ничего не сказав.
    number = incoming.whole_number(said, "Идентификатор группы")
    if number <= 0:
        # Ноль отдельно от `None` не отличается ни одной проверкой на
        # истинность, и группа, названная нулём, молча пропадала: команда не
        # читала кабинет, выходила с кодом 0 и сообщала, что подстановка не
        # проверялась. Отказ здесь дешевле, чем ещё один `is not None` в
        # каждой ветке.
        raise DirectFailure(
            f"Идентификатор группы {number} — не идентификатор: они "
            f"положительные. Названный нулём источник фраз молча пропал бы, а "
            f"отчёт сказал бы, что подстановка не проверялась.")
    return number


def kit_of_brief(brief: dict, *, titles=None, texts=None) -> Kit:
    """Комплект из брифа. Пустой комплект — отказ, а не пустая проверка."""
    got_titles = list(titles if titles is not None
                      else brief.get("titles") or [])
    got_texts = list(texts if texts is not None else brief.get("texts") or [])
    if not got_titles or not got_texts:
        raise DirectFailure(
            f"В комплекте заголовков {len(got_titles)}, текстов "
            f"{len(got_texts)}. Директ требует хотя бы один каждого "
            f"(`ResponsiveAd.Titles`, `.Texts` — обязательные), а матрица "
            f"пустого комплекта чиста ровно потому, что в ней ничего нет.")
    return Kit(got_titles, got_texts,
               href=brief.get("href"),
               display_url_path=brief.get("display_url_path"))


# --------------------------------------------------------------------------
# Чтение из кабинета
# --------------------------------------------------------------------------

def read_ad(client, account, accounts, identifier) -> dict:
    """Одно объявление со всей комбинаторной структурой.

    Читается обоими наборами имён полей — `responsive.read_params`. С одним
    `TextAdFieldNames` сконвертированное объявление приходит текстово-графическим:
    один заголовок, один текст, и комплект из семи выглядел бы комплектом из
    одного."""
    params = dict(responsive.read_params())
    params["SelectionCriteria"] = {"Ids": [int(identifier)]}
    need = Limits.load().units_cost(responsive.SERVICE, "get", 1)
    found = {}
    for item in client.get_all(
            responsive.SERVICE, params, account=account,
            use_operator_units=lambda: accounts.use_operator_units(
                account, need=need)):
        found[item["Id"]] = item
    ad = found.get(int(identifier))
    if ad is None:
        raise DirectFailure(
            f"Объявление {identifier} не прочиталось: его нет, оно в другом "
            f"кабинете или закрыто правами.")
    if not ad.get(responsive.STRUCTURE):
        raise DirectFailure(
            f"У объявления {identifier} нет комбинаторной структуры: комплекта "
            f"заголовков и текстов у него не существует, а другого предмета у "
            f"этой команды нет. Дополнительный заголовок текстово-графического "
            f"переносит `ads_write.py ad carry-title2`.")
    return ad


def read_phrases(client, account, accounts, group) -> list:
    """Фразы группы — то, что подставится в шаблоны на показе.

    Автотаргетинг отбирает `templates.phrases_of`: у него нет текста фразы, и
    `Phrase` из него вышла бы отказом посреди сборки отчёта."""
    need = Limits.load().units_cost(phrases_lib.KEYWORDS, "get")
    found = []
    for item in client.get_all(
            phrases_lib.KEYWORDS,
            {"SelectionCriteria": {"AdGroupIds": [int(group)]},
             "FieldNames": ["Id", "Keyword", "UserParam1", "UserParam2"]},
            account=account,
            use_operator_units=lambda: accounts.use_operator_units(
                account, need=need)):
        found.append(item)
    return templates.phrases_of(found)


def material(args):
    """Комплект и фразы — откуда бы они ни пришли.

    Возвращает тройку «комплект, фразы, бриф». Бриф нужен дальше целиком:
    в нём сегменты и заметка, и разбирать его дважды незачем."""
    brief = read_brief(args.brief) if args.brief else {}
    titles = args.title or None
    texts = args.text or None
    people = [templates.Phrase(one) for one in (brief.get("phrases") or [])]

    ad = chosen_ad(args)
    group = chosen_group(args, brief)
    if (group or ad) and people:
        # Отказ, а не тихий выбор одного из двух, и до похода в сеть. Список
        # фраз в брифе лежит с прошлого разбора, а группа в кабинете живая:
        # приняв бриф, мы проверили бы подстановку не на тех фразах и
        # отчитались бы `blocking: false` о комплекте, который в кабинете
        # вылезет за предел. Приняв группу — молча выбросили бы то, что человек
        # написал в брифе. Обе беды тихие, и выбирать между ними должен он.
        raise DirectFailure(
            "Названы объявление или группа и при этом в брифе перечислены "
            "фразы. Это два источника подстановки, и они расходятся: в брифе "
            "список от прошлого раза, в кабинете — сегодняшний. Уберите "
            "`phrases` из брифа, чтобы проверять по свежему чтению группы, "
            "либо не называйте объявление и группу, чтобы проверять по списку "
            "из брифа.")

    if ad or group:
        client = Client.from_env(profile=args.env, account=args.account,
                                 warn=warn)
        accounts = Accounts.load(client, warn=warn)
        account = resolve_account(accounts, client, args.account)
        if ad:
            found = read_ad(client, account, accounts, ad)
            kit = Kit.of(found, carry=False)
            group = group or found.get("AdGroupId")
            if titles is not None:
                kit = kit.but(titles=list(titles))
            if texts is not None:
                kit = kit.but(texts=list(texts))
        else:
            kit = kit_of_brief(brief, titles=titles, texts=texts)
        if group:
            people = read_phrases(client, account, accounts, group)
        return kit, people, brief

    return kit_of_brief(brief, titles=titles, texts=texts), people, brief


# --------------------------------------------------------------------------
# Вывод
# --------------------------------------------------------------------------

def review_of(kit, people, brief, args):
    """Разбор комплекта вместе с пометками агента.

    Пометки приходят двумя путями и складываются: полем `marks` брифа и
    аргументами `--mark`. Складываются, а не заменяют друг друга: бриф лежит
    от прошлого разбора, а `--mark` набирают, прочитав матрицу сейчас."""
    said = list(brief.get("marks") or []) + list(args.mark or [])
    return combinatorial.review(
        kit, people, marks=combinatorial.marks_of(said, kit, people))


def matrix_rows(review) -> list:
    """Матрица в виде строк для выгрузки: одна комбинация — одна строка.

    Пометки на комплект целиком идут отдельными строками с пустыми номерами
    мест. Иначе они пропадают из единственного машинного артефакта сборки:
    пары у них нет, а потребитель, читающий выгрузку, принял бы комплект, не
    увидев суждения. Критерий приёмки говорит об этом прямо — пометки не
    теряются."""
    out = []
    for grid in review.matrix:
        for pair in grid.pairs:
            # Данные среза берутся у пары: она их и несёт. Собирать их здесь
            # заново значило бы завести второе место, где о них можно забыть —
            # ровно так машинный отчёт и остался без подставленной ссылки.
            body = pair.row()
            out.append({
                "фраза": body.get("phrase") or "",
                "param1": body.get("param1") or "",
                "param2": body.get("param2") or "",
                "id фразы": body.get("phrase_id") or "",
                "ссылка": body.get("href") or "",
                "отображаемая ссылка": body.get("display_url_path") or "",
                "заголовок №": pair.title_place,
                "текст №": pair.text_place,
                "заголовок": pair.title,
                "текст": pair.text,
                "пометки": " ".join(sorted({one.code for one in pair.marks})),
                "чем помечена": "; ".join(one.said for one in pair.marks),
            })
    for one in review.whole:
        # Фраза у пометки на комплект бывает: «весь комплект не работает по
        # этой подстановке» — законное суждение, и `Mark.where` его
        # показывает. Пустое поле превращало бы его в общее.
        out.append({
            "фраза": one.phrase or "", "param1": "", "param2": "",
            "id фразы": "", "ссылка": "", "отображаемая ссылка": "",
            "заголовок №": "", "текст №": "", "заголовок": "", "текст": "",
            "пометки": one.code, "чем помечена": one.said,
        })
    return out


def write_csv(path, rows) -> Path:
    where = Path(path)
    if not rows:
        raise DirectFailure("Выгружать нечего: в матрице нет ни одной строки.")
    where.parent.mkdir(parents=True, exist_ok=True)
    with where.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    return where


def code_for(review) -> int:
    """Код возврата: ноль, пока не нашлось ни вердикта, ни доказанного дефекта.

    Запись это не гейтит в любом случае: пишет `ads_write.py`, и код возврата
    здесь — сообщение человеку и расписанию."""
    return 1 if review.blocking else 0


def report(review, args, brief=None, *, extra=(), path=None,
           detail: bool = False) -> int:
    """Печать отчёта и код возврата — единственный путь для всех команд.

    Заметка брифа, сегменты и предупреждение о границе проверки добавляются
    здесь, а не в каждой ветке. Ветки, добавлявшие их сами, по очереди о них
    забывали: это давало находки ревью пять проходов подряд."""
    said = list(notes_of(brief or {})) + list(extra) + advice(review)
    if args.json:
        body = review.row()
        body["notes"] = said
        if path is not None:
            body["csv"] = str(path)
        say(json.dumps(body, ensure_ascii=False))
    else:
        cache_module.outline(said + review.lines(detail=detail), path=path)
    return code_for(review)


# --------------------------------------------------------------------------
# Действия
# --------------------------------------------------------------------------

def mark_record(value: str) -> dict:
    """Пометка из аргумента командной строки: `3x1:текст`, `3:текст`, `:текст`.

    Адрес слева от двоеточия, суждение справа. `3x1` — пара, `3` — заголовок,
    `x1` — текст, пусто — комплект целиком. Разбор строгий: аргумент, который
    не понят, — это потерянное суждение, а не пометка «куда-нибудь».

    После `@` — фраза подстановки: `3x1@профнастил купить:текст`. Нужна там,
    где комплект с шаблонами: пара `3 × 1` существует столько раз, сколько
    фраз, и ломаться может по одной из них. Без фразы пометка достаётся всем
    подстановкам — так и надо, когда суждение о самом тексте."""
    head, sep, said = str(value).partition(":")
    phrase = None
    if "@" in head:
        head, _, phrase = head.partition("@")
        phrase = phrase.strip() or None
    if not sep:
        raise argparse.ArgumentTypeError(
            f"пометка «{excerpt(value, 40)}» без двоеточия: слева адрес "
            f"(`3x1`, `3`, `x1` или пусто), справа — что не так")
    if not said.strip():
        raise argparse.ArgumentTypeError(
            f"пометка «{excerpt(value, 40)}» без текста: помечать пару, ничего "
            f"о ней не сказав, незачем")
    head = head.strip().lower()
    title = text = None
    if head:
        first, _, second = head.partition("x")
        for source, name in ((first, "title"), (second, "text")):
            if not source:
                continue
            # Тем же разбором, что и бриф: `isdigit()` истинен для «١» и «²»,
            # и формат адреса начинал зависеть от того, набран он в командной
            # строке или записан в файле.
            try:
                place = incoming.whole_number(
                    source, f"В адресе пометки «{head}» место «{source}»")
            except DirectFailure as failure:
                raise argparse.ArgumentTypeError(str(failure)) from None
            if name == "title":
                title = place
            else:
                text = place
        if title is None and text is None:
            raise argparse.ArgumentTypeError(
                f"адрес пометки «{head}» не разобран; ожидается `3x1`, `3`, "
                f"`x1` или пусто")
    return {"said": said.strip(), "title": title, "text": text,
            "phrase": phrase}


def notes_of(brief: dict) -> list:
    """Заметка автора из брифа — показывается, а не пропускается молча.

    Поле, которое разбор принимает и никуда не девает, ничем не отличается от
    опечатки: человек написал, команда промолчала. Комментариев в JSON нет,
    поэтому поле законно, — но тогда его надо показать."""
    out = []
    said = str(brief.get("note") or "").strip()
    if said:
        out.append(f"Заметка брифа: {excerpt(said, 200)}")
    # Сегменты — декларация автора о том, чем заголовки различаются. Код их не
    # проверяет, но показывает: судить о
    # разнообразии агенту проще, когда видно, что заявлено.
    named = [str(one).strip() for one in (brief.get("segments") or [])
             if str(one).strip()]
    if named:
        out.append("Сегменты, объявленные автором: " + ", ".join(named))
    return out


def run_check(args) -> int:
    kit, people, brief = material(args)
    review = review_of(kit, people, brief, args)
    return report(review, args, brief)


def run_matrix(args) -> int:
    """Полная матрица. В stdout — только помеченные, целое — в файл."""
    kit, people, brief = material(args)
    review = review_of(kit, people, brief, args)
    rows = matrix_rows(review)
    path = write_csv(args.csv, rows) if args.csv else None
    if args.json or path is not None:
        return report(review, args, brief, path=path, detail=True)
    # Без файла матрица всё равно не печатается целиком: двадцать одна
    # комбинация на фразу не помещается в тридцать строк вывода. Показываются
    # помеченные, а за остальным — `--csv` или `--json`.
    # Через тот же `report`, что и остальные команды: своя печать здесь по
    # очереди теряла замечание к составу, адрес пометки, её текст и
    # предупреждение о границе проверки — четыре находки ревью на одном месте.
    shown = min(len(review.marked_pairs), combinatorial.PAIRS_SHOWN)
    hidden = sum(len(one) for one in review.matrix) - shown
    tail = ([f"  · не показано комбинаций: {hidden}. Целиком — `--csv ФАЙЛ` "
             f"или `--json`"] if hidden > 0 else [])
    return report(review, args, brief, extra=tail, detail=True)


def run_fill(args) -> int:
    """Достройка: что дописать и что получится, если дописать предложенное.

    Без предложенных элементов команда показывает **места**, а не заполняет их:
    слов она не пишет. Семь заголовков — предел формата, а не норма (`AD-01`),
    и решение «сколько их должно быть» принимает человек."""
    kit, people, brief = material(args)
    free = combinatorial.slots(kit)
    if not args.add_title and not args.add_text:
        # Разбор идёт и здесь. Ветка, возвращавшая до него, роняла всё, что
        # ему поручено: комплект из восьми заголовков выходил с кодом 0, а
        # пометки агента исчезали молча. Показ мест — это другой **вывод**, а
        # не другая проверка.
        review = review_of(kit, people, brief, args)
        lines = [
            f"заголовков {len(kit.titles)}, текстов {len(kit.texts)}; "
            f"свободных мест {len(free)}",
            "Семь заголовков — предел формата, а не норма: три сильных лучше "
            "семи, из которых четыре написаны для заполнения.",
            "Чем занять места и надо ли занимать их все — судит агент по "
            "методике COMBINATORIAL_COPY.md, а не команда.",
        ]
        lines += [one.line() for one in free]
        if args.json:
            body = review.row()
            body["slots"] = [one.row() for one in free]
            body["notes"] = notes_of(brief) + advice(review)
            say(json.dumps(body, ensure_ascii=False))
            return code_for(review)
        return report(review, args, brief, extra=lines)

    grown = combinatorial.filled(kit, titles=args.add_title or (),
                                 texts=args.add_text or ())
    kept = list(kit.titles) == list(grown.titles[:len(kit.titles)]) and \
        list(kit.texts) == list(grown.texts[:len(kit.texts)])
    if not kept:
        # Сюда попасть нельзя: `filled` дописывает в хвост. Проверка стоит
        # потому, что цена ошибки здесь — молча потерянный чужой заголовок,
        # и `S-05` требует сохранности исходных прямым текстом.
        raise DirectFailure(
            "Достройка изменила исходные элементы комплекта. Это ошибка "
            "скилла: дописанное идёт в хвост, а прежнее не трогается.")
    review = review_of(grown, people, brief, args)
    said = [f"дописано заголовков {len(args.add_title or ())}, текстов "
            f"{len(args.add_text or ())}; исходные сохранены"]
    return report(review, args, brief, extra=said)


def run_build(args) -> int:
    """Комплекты по набору данных: один разбор на группу строк."""
    rows = read_rows(args.data)
    brief = read_brief(args.brief) if args.brief else {}
    if brief.get("phrases"):
        # Отказ, а не предупреждение: два источника фраз дают два разных
        # набора подстановок, и какой из них проверен — из отчёта не видно.
        raise DirectFailure(
            "В брифе перечислены фразы, а команда `build` берёт их из набора "
            "данных. Два источника фраз дали бы две разные матрицы, и какая "
            "из них проверена, по отчёту не понять. Уберите `phrases` из "
            "брифа либо проверяйте комплект командой `check`.")
    kit = kit_of_brief(brief, titles=args.title or None,
                       texts=args.text or None)
    groups = {}
    for row in rows:
        groups.setdefault(str(row.get("group") or ""), []).append(row)

    lines, worst, out, reports = list(notes_of(brief)), 0, [], []
    blocking = False
    given = list(brief.get("marks") or []) + list(args.mark or [])
    # Пометки проверяются по **всему** набору, а не по каждой группе отдельно.
    # Адрес `1x1@альфа` однозначно выбирает строку своей группы, и отказ на
    # соседней группе означал бы, что пометить одну подстановку в
    # многогрупповой сборке нельзя вовсе.
    combinatorial.marks_of(given, kit, phrases_of_rows(rows))
    for name, mine in sorted(groups.items()):
        people = phrases_of_rows(mine)
        texts = {one.text for one in people}
        # Группе достаются пометки, которые в ней могут приземлиться: свои по
        # фразе и общие, без фразы.
        theirs = [one for one in given
                  if not one.get("phrase") or one["phrase"] in texts]
        review = combinatorial.review(
            kit, people, marks=combinatorial.marks_of(theirs, kit, people))
        titled = name or "без группы"
        # Те же строки, что у остальных команд: имя группы приписывается к
        # сводке, остальное идёт как есть. Своего перечня здесь больше нет.
        spoken = review.lines()
        lines.append(f"{titled}: {spoken[0]}")
        lines += spoken[1:]
        blocking = blocking or review.blocking
        worst = max(worst, code_for(review))
        out += [dict(row, **{"группа": titled}) for row in matrix_rows(review)]
        # Структурный отчёт по каждой группе, а не только русские строки:
        # иначе машинному потребителю приходится разбирать текст, чтобы
        # понять, какая группа и какое поле заблокированы.
        reports.append(dict(review.row(), **{"группа": titled}))

    # Предупреждение о границе проверки — такая же обязательная строка, как
    # заметка брифа. Сборка идёт по группам и своего `report` не зовёт, но
    # молчать об этом ей нельзя: чистая сводка иначе читается как проверенная
    # сочетаемость, которой не было.
    lines += advice_of(bool(given))
    path = write_csv(args.csv, out) if args.csv else None
    if args.json:
        say(json.dumps({"groups": len(groups), "rows": len(out),
                        "blocking": blocking,
                        "csv": None if path is None else str(path),
                        "lines": lines, "matrix": out,
                        "reports": reports},
                       ensure_ascii=False))
    else:
        cache_module.outline(lines, path=path, total=len(out))
    return worst


def advice(review) -> list:
    """Строка, которую команда говорит до отчёта, а не после."""
    return advice_of(bool(review.marks))


def advice_of(judged: bool) -> list:
    """Что означает пустой список пометок.

    Отсутствие пометок не означает, что пары читаются: команда о смысле не
    судит вовсе, и пустой список значит ровно то, что судивший
    ничего не сказал. Сказать это надо прямо — иначе чистый отчёт прочитается
    как выполненная проверка сочетаемости, которой не было.

    Отдельно от `advice`, потому что сборка идёт по группам и целого `Review`
    у неё нет — а сказать обязана то же самое."""
    if judged:
        return ["Пометки — суждение агента, а не отказ. Комплект с пометками "
                "записывается, если человек так решил."]
    return ["Пометок нет — это значит, что никто не судил, а не что пары "
            "читаются. Сочетаемость проверяет агент по матрице: "
            "прочитайте её и пометьте пары через `--mark`."]


# --------------------------------------------------------------------------
# Разбор аргументов
# --------------------------------------------------------------------------

class Parser(argparse.ArgumentParser):
    def error(self, message: str):
        self.exit(2, redact(f"{self.prog}: {message}\n"))


def add_common(parser, *, leaf: bool) -> None:
    """Общие флаги — и на корень, и на каждое действие.

    Причина та же, что у `ads_write.py`: `argparse` разбирает флаги корня до
    подкоманды, и `check --json` без этого падал бы кодом 2. У листьев
    умолчание `SUPPRESS`, иначе лист затирает значение, пришедшее с корня."""
    default = argparse.SUPPRESS if leaf else None
    parser.add_argument("--env", choices=("production", "test_cabinet"),
                        default=default, help="контур и набор переменных")
    parser.add_argument("--account", metavar="ЛОГИН", default=default,
                        help="логин кабинета; при отсутствии берётся активный")
    parser.add_argument("--json", action="store_true",
                        default=argparse.SUPPRESS if leaf else False,
                        help="машиночитаемый вывод")


def add_material(step) -> None:
    step.add_argument("--brief", metavar="ФАЙЛ",
                      help=combinatorial.brief_help(BRIEF_READ))
    step.add_argument("--ad", type=whole_argument,
                      help="взять комплект из объявления")
    step.add_argument("--group", type=whole_argument,
                      help="группа, чьи фразы подставлять в шаблоны")
    step.add_argument("--title", action="append",
                      help="заголовок; повторяется. Заменяет взятые из брифа "
                           "или из объявления")
    step.add_argument("--text", action="append", help="текст; повторяется")
    step.add_argument("--mark", action="append", type=mark_record,
                      help=MARK_HELP)


def build_parser() -> Parser:
    parser = Parser(
        description="Комплект заголовков и текстов: сборка и разбор. Слов "
                    "команда не пишет — она проверяет написанное.")
    add_common(parser, leaf=False)
    common = argparse.ArgumentParser(add_help=False)
    add_common(common, leaf=True)

    actions = parser.add_subparsers(dest="action", required=True)

    check = actions.add_parser("check", parents=[common],
                               help="разобрать комплект целиком")
    add_material(check)

    grid = actions.add_parser("matrix", parents=[common],
                              help="матрица комбинаций с пометками")
    add_material(grid)
    grid.add_argument("--csv", metavar="ФАЙЛ", help="выгрузить матрицу целиком")

    fill = actions.add_parser("fill", parents=[common],
                              help="достройка неполного комплекта")
    add_material(fill)
    fill.add_argument("--add-title", action="append", dest="add_title",
                      help="дописать заголовок в хвост; повторяется")
    fill.add_argument("--add-text", action="append", dest="add_text",
                      help="дописать текст в хвост; повторяется")

    build = actions.add_parser("build", parents=[common],
                               help="комплекты по набору данных")
    build.add_argument("--data", metavar="ФАЙЛ", required=True,
                       help="набор данных: JSON-массив или CSV с заголовком")
    build.add_argument("--brief", metavar="ФАЙЛ",
                       help=combinatorial.brief_help(BRIEF_READ))
    build.add_argument("--title", action="append")
    build.add_argument("--text", action="append")
    build.add_argument("--mark", action="append", type=mark_record,
                       help=MARK_HELP)
    build.add_argument("--csv", metavar="ФАЙЛ", help="выгрузить матрицы")
    return parser


def run(args) -> int:
    return {"check": run_check, "matrix": run_matrix, "fill": run_fill,
            "build": run_build}[args.action](args)


def main(argv=None) -> int:
    preload_secrets()
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.action != "build" and not any(
            (args.brief, args.ad, args.title, args.text)):
        parser.error("не сказано, из чего собирать комплект: назовите "
                     "`--brief`, `--ad` или `--title` с `--text`")
    try:
        return run(args)
    except DirectFailure as failure:
        warn(str(failure))
        return 1
    except OSError as failure:
        warn(redact(f"Не удалось прочитать или записать файл: {failure}"))
        return 1


if __name__ == "__main__":
    sys.exit(main())
