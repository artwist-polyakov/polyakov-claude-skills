"""Разбор чужого ввода: одно правило на все команды."""

from __future__ import annotations

import csv
import io
import json
from pathlib import Path

from config import DirectFailure, excerpt

# Кодировка чужого файла одна и параметром не задаётся. Таблицу выгружает
# Excel, а отчёт — Директ, и оба ставят в начало файла отметку порядка байтов;
# с ней первый столбец зовётся `﻿phrase`, разбор отказывает по незнакомому
# имени, а человек смотрит на файл, где написано `phrase`, и причины не видит.
# `utf-8-sig` снимает отметку и в остальном совпадает с `utf-8`, поэтому
# выбирать не из чего: параметр здесь мог бы только ослабить правило.
ENCODING = "utf-8-sig"

# Куда `csv.DictReader` кладёт значения, которым не хватило столбца в шапке.
# Имя нарочно человеческое: без `restkey` хвост уезжает под ключ `None`, и
# разбор падает трассировкой на попытке склеить имена полей.
EXTRA = "лишние значения в строке"

# Расширения, по которым чужой набор данных читается таблицей, а не JSON.
DELIMITERS = {".csv": ",", ".tsv": "\t"}

_KINDS = {
    type(None): "null",
    bool: "логическое значение",
    int: "число",
    float: "число",
    str: "строка",
    list: "список",
    dict: "объект",
}


def _kind(value) -> str:
    """Как назвать человеку то, что пришло вместо ожидаемого."""
    return _KINDS.get(type(value), type(value).__name__)


# --------------------------------------------------------------------------
# Значения
# --------------------------------------------------------------------------

def a_string(value, said: str) -> str:
    """Чужое значение, которое обязано быть строкой.

    Не педантизм, а защита от подделки. `Kit` и `Phrase` приводят к строке что
    угодно: `null` становится заголовком «None», `123` — фразой «123», и отчёт
    выходит чистым. Ошибка формата притворяется текстом, который человек
    написал сам."""
    if not isinstance(value, str):
        raise DirectFailure(
            f"{said} — это {_kind(value)}, а ожидается строка. Приведённое к "
            f"строке, оно стало бы текстом «{excerpt(value, 20)}», и отчёт "
            f"вышел бы чистым.")
    return value


def whole_number(value, said: str):
    """Целое из чужого ввода — и только целое.

    `int()` здесь не годится, и это не педантизм. Он превращает `1.8` в `1`, а
    `true` — в единицу: опечатка не теряется явно, а **подставляет соседнее
    значение**. У пометки это переносит суждение на чужой заголовок, у
    идентификатора группы — читает фразы не той группы. Оба раза человек
    получает правдоподобный ответ на вопрос, которого не задавал.

    `bool` проверяется раньше `int` намеренно: в Python он его подкласс, и
    `isinstance(True, int)` истинно."""
    if isinstance(value, bool):
        raise DirectFailure(
            f"{said} — это {value!r}, а ожидается целое число. Логическое "
            f"значение номером не является.")
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        # Разбирается вручную, а не `int()`. Ни `isdigit()`, ни сам `int()` не
        # отвечают на нужный вопрос: первый истинен для «²» и для цифр других
        # письменностей, которые `int()` не берёт, а второй **принимает
        # подчёркивания** — `int("5793_870037")` даёт 5793870037. Опечатка в
        # идентификаторе так адресует существующий чужой объект, и чтение
        # уходит не туда, ничего не сказав.
        body = value.strip()
        digits = body[1:] if body[:1] in "+-" else body
        if digits and all("0" <= one <= "9" for one in digits):
            try:
                return -int(digits) if body[:1] == "-" else int(digits)
            except ValueError:
                # У Python 3.11 есть предел на разбор длинных чисел, и он
                # ловится здесь: посимвольную проверку такая строка проходит,
                # а `int` роняет трассировку. Идентификаторов такой длины не
                # бывает, но отказ обязан выглядеть отказом.
                raise DirectFailure(
                    f"{said} длиной {len(digits)} цифр — это не "
                    f"идентификатор и не номер места.") from None
    raise DirectFailure(
        f"{said} записано как «{excerpt(value, 32)}», а ожидается целое "
        f"число. Дробное округлилось бы до соседнего значения, и ответ вышел "
        f"бы правдоподобным, но не на тот вопрос.")


def campaign_selector(value: str) -> str:
    """Отбор кампании из командной строки: идентификатор или кусок названия.

    Ставится в `type=` объявления `--campaign`, а не проверкой после разбора, и
    это существенно. Проверку после разбора забывают: семь команд объявляют
    такой аргумент, и в пяти из них пустая строка молча становилась «отбор не
    назван» — команда уходила читать **весь кабинет** и отчитывалась успехом.
    Приходит она не по опечатке, а из `--campaign "$CAMPAIGN"` с незаданной
    переменной.

    Отказ поднимается `ArgumentTypeError`, а не `DirectFailure`: argparse
    печатает его текст как есть и выходит с кодом 2, а это и есть ошибка
    аргументов. `DirectFailure` дошёл бы до человека трассировкой."""
    import argparse

    text = a_string(value, "--campaign")
    if not text.strip():
        # Имя аргумента argparse печатает сам — второй раз его не называем.
        raise argparse.ArgumentTypeError(
            "пустое значение. Аргумент назван, значит кампания "
            "подразумевалась; обход по всему кабинету — это та же команда без "
            "--campaign."
        )
    return text


def given(value):
    """Пустое значение — это отсутствие, а не пустая строка.

    Пустая ячейка таблицы приходит как `""`, а поле, которого нет в объекте, —
    как `None`, и один набор данных давал разный вердикт таблицей и объектом:
    `templates.variants` сообщает о незаданном параметре только для `None`, и
    адрес `…/{param1}/` превращался в `…//` при чистом отчёте, тогда как та же
    строка в JSON без `param1` блокировалась."""
    if value is None:
        return None
    if isinstance(value, str) and not value.strip():
        return None
    return value


# --------------------------------------------------------------------------
# Форма записи
# --------------------------------------------------------------------------

class Shape:
    """Чего команда ждёт от записи чужого файла.

    Форма **объявляется**, а не проверяется руками на месте. Перечисленные
    помощники — `a_string`, `whole_number`, перечень допустимых полей — уже
    один раз стояли на одном пути из двух: элементы брифа их выполняли, строки
    набора данных нет, хотя ввод и там, и там чужой. Объявление снимает этот
    класс целиком: `document` и `rows` без формы не вызываются, и правило
    применяется ко всем полям сразу, а не к тем, о которых вспомнили.

    - `fields` — закрытый перечень допустимых имён. `None` означает, что
      перечень открыт: состав чужой выгрузки задаёт тот, кто её заказывал, и
      требовать от неё известных столбцов значило бы отвергать законные отчёты.
    - `text` — поля, которые обязаны быть строкой.
    - `whole` — поля, которые обязаны быть целым числом.
    - `text_lists` — поля-списки строк. Строка на месте списка разбирается
      посимвольно: `list("Профнастил")` даёт десять «заголовков» по одной
      букве, длины проходят, отчёт выходит чистым.
    - `records` — поля-списки записей со своей формой.
    - `needed` — поля, без которых запись бессмысленна, вместе с объяснением
      почему: `{"phrase": "Подставлять вместо шаблона нечего…"}`.
    - `blank_absent` — те из `text` и `whole`, у которых пустое значение
      означает отсутствие.

    Последнее объявляется, а не подразумевается, и умолчания у него нет ни в
    одну сторону. Пустое значение бывает двумя разными вещами. У параметра
    уровня фразы оно и есть отсутствие: `templates.variants` сообщает о
    незаданном параметре только для `None`, и без приведения адрес
    `…/{param1}/` превращался в `…//` при чистом отчёте. А у ссылки объявления
    пустая строка — **испорченная просьба**, и `responsive.Kit` отвергает её
    отдельным сообщением: она не «оставить как было» и не очистка, а очистка
    называется явно. Приведи её к отсутствию — и негодная просьба молча сойдёт
    за неназванное поле.

    Оба умолчания поэтому одинаково плохи, и выбор делает объявление формы."""

    __slots__ = ("fields", "text", "whole", "text_lists", "records", "needed",
                 "blank_absent")

    def __init__(self, *, fields=None, text=(), whole=(), text_lists=(),
                 records=None, needed=None, blank_absent=()):
        self.fields = None if fields is None else frozenset(fields)
        self.text = tuple(text)
        self.whole = tuple(whole)
        self.text_lists = tuple(text_lists)
        self.records = dict(records or {})
        self.needed = dict(needed or {})
        self.blank_absent = frozenset(blank_absent)
        outside_value = self.blank_absent - set(self.text) - set(self.whole)
        if outside_value:
            raise DirectFailure(
                f"Форма записи приводит к отсутствию поля, не объявленные "
                f"строкой или целым: {', '.join(sorted(outside_value))}.")
        named = (list(self.text) + list(self.whole) + list(self.text_lists)
                 + list(self.records))
        twice = sorted({one for one in named if named.count(one) > 1})
        if twice:
            # Поле, объявленное дважды, проверяется по последнему объявлению, а
            # автор читает первое. Отказ здесь — своя же опечатка, и наружу она
            # выходит `DirectFailure` только потому, что модуль не роняет
            # ничего другого.
            raise DirectFailure(
                f"Форма записи объявляет поля дважды: {', '.join(twice)}.")
        outside = sorted(set(named) | set(self.needed))
        if self.fields is not None:
            unknown = [one for one in outside if one not in self.fields]
            if unknown:
                raise DirectFailure(
                    f"Форма записи проверяет поля, которых нет в перечне "
                    f"допустимых: {', '.join(unknown)}. Опечатка в самом "
                    f"объявлении выключила бы проверку молча.")

    def check(self, value, where: str) -> dict:
        """Запись, разобранная по форме. Возвращает копию, а не исходник."""
        if not isinstance(value, dict):
            known = ("" if self.fields is None
                     else f" с полями {', '.join(sorted(self.fields))}")
            raise DirectFailure(
                f"{where} — это {_kind(value)}, а ожидается объект{known}.")
        if self.fields is not None:
            unknown = sorted(set(value) - self.fields)
            if unknown:
                raise DirectFailure(
                    f"{where}: поля, которых команда не знает: "
                    f"{', '.join(unknown)}. Известные: "
                    f"{', '.join(sorted(self.fields))}. Пропустить опечатку "
                    f"молча значит потерять то, что в этом поле лежало.")
        out = dict(value)
        for name in self.text:
            one = self._present(value, name)
            out[name] = (None if one is None
                         else a_string(one, f"{where}, поле «{name}»"))
        for name in self.whole:
            one = self._present(value, name)
            out[name] = (None if one is None
                         else whole_number(one, f"{where}, поле «{name}»"))
        for name in self.text_lists:
            out[name] = self._list_of_text(value.get(name), where, name)
        for name, shape in self.records.items():
            out[name] = self._records(value.get(name), where, name, shape)
        for name, why in self.needed.items():
            # `given` и здесь: обязательное поле бывает не объявлено ни строкой,
            # ни номером — у выгрузки с открытым перечнем столбцов их не
            # объявляют вовсе, — и без приведения пустая ячейка сошла бы за
            # заполненную.
            if given(out.get(name)) is None:
                raise DirectFailure(f"{where}: нет поля «{name}». {why}")
        return out

    def _present(self, value: dict, name: str):
        """Значение поля или `None`, если его нет.

        Пустое считается отсутствием только там, где форма это объявила: у
        остальных полей пустая строка — то, что человек написал, и решает её
        судьбу тот, кто поле потребляет."""
        one = value.get(name)
        if one is None:
            return None
        return given(one) if name in self.blank_absent else one

    def _list_of_text(self, value, where: str, name: str):
        if value is None:
            return None
        self._a_list(value, where, name)
        return [a_string(one, f"{where}, поле «{name}», элемент {number}")
                for number, one in enumerate(value, start=1)]

    def _records(self, value, where: str, name: str, shape: "Shape"):
        if value is None:
            return None
        self._a_list(value, where, name)
        # Не-запись пропускается насквозь: устройство записи разбирает тот, кто
        # понимает её по смыслу, и второй перечень полей разошёлся бы с первым
        # на первом же новом поле. Здесь применяется только правило типов.
        return [one if not isinstance(one, dict)
                else shape.check(one, f"{where}, «{name}», запись {number}")
                for number, one in enumerate(value, start=1)]

    @staticmethod
    def _a_list(value, where: str, name: str) -> None:
        if not isinstance(value, list):
            raise DirectFailure(
                f"{where}: поле «{name}» — это {_kind(value)}, а ожидается "
                f"список. Строка на этом месте разобралась бы посимвольно, и "
                f"комплект собрался бы из отдельных букв.")


# --------------------------------------------------------------------------
# Файл
# --------------------------------------------------------------------------

def text_of(path, said: str) -> str:
    """Текст чужого файла. Нечитаемый файл — отказ, а не трассировка.

    `UnicodeDecodeError` — подкласс `ValueError`, а не `OSError`, и до
    перехвата в `main` он не доходил: человек получал трассировку. Случай не
    выдуманный — таблицу выгружает Excel, и он охотно сохраняет её в
    однобайтовой кодировке."""
    where = Path(path)
    try:
        return where.read_text(encoding=ENCODING)
    except UnicodeDecodeError as failure:
        raise DirectFailure(
            f"{said} {where} не читается как {failure.encoding}: файл в "
            f"другой кодировке. Пересохраните его в UTF-8 — Excel называет её "
            f"«Юникод (UTF-8)»."
        ) from None
    except OSError as failure:
        raise DirectFailure(
            f"{said} {where} не прочитался: {failure.strerror or failure}."
        ) from None


def document(path, said: str, shape: Shape) -> dict:
    """Объект из чужого JSON-файла, разобранный по объявленной форме."""
    where = f"{said} {Path(path)}"
    body = _json(text_of(path, said), where)
    return shape.check(body, where)


def rows(path, said: str, shape: Shape) -> list:
    """Записи чужого набора: JSON-массив или таблица с заголовком.

    Два вида потому, что прайс и семантика приходят человеку таблицей, а не
    объектом, и заставлять его переводить одно в другое незачем. Вид выбирается
    по расширению: `.csv` и `.tsv` — таблица, всё остальное — JSON.

    Разделитель здесь не задаётся: его называет расширение, и второй способ его
    выбрать означал бы, что один из двух молча проигрывает. Кому нужен свой —
    зовёт `table` напрямую, назвав разделитель ровно один раз.

    Вердикт от вида не зависит, и это здесь проверяется, а не обещается:
    таблица пустую ячейку от незаполненного столбца не отличает по
    устройству, поэтому форма, оставляющая пустое как написано, обоими видами
    не читается — отказ ниже."""
    undeclared = sorted((set(shape.text) | set(shape.whole))
                        - shape.blank_absent)
    if undeclared:
        raise DirectFailure(
            f"Форма набора {said} оставляет пустое значение как написано у "
            f"полей {', '.join(undeclared)}, а набор читается и таблицей. "
            f"Пустую ячейку от незаполненного столбца таблица не отличает по "
            f"устройству, и вердикт зависел бы от вида записи. Объявите эти "
            f"поля в `blank_absent` или читайте набор одним видом.")
    where = f"{said} {Path(path)}"
    text = text_of(path, said)
    suffix = Path(path).suffix.lower()
    if suffix in DELIMITERS:
        return table(text, where, shape, delimiter=DELIMITERS[suffix])
    found = _json(text, where)
    if not isinstance(found, list):
        raise DirectFailure(
            f"{where} — это {_kind(found)}, а ожидается массив записей.")
    return [shape.check(one, f"{where}, строка {number}")
            for number, one in enumerate(found, start=1)]


def table(text: str, where: str, shape: Shape, *, delimiter: str = ",") -> list:
    """Строки таблицы: строгий разбор, повтор столбца — отказ.

    Строгость здесь про **ошибку формата, которая меняет данные**, а не про
    придирчивость. Повторяющийся столбец `csv.DictReader` схлопывает, оставляя
    последнее значение: подстановка собирается по другому числу, а сборка
    отчитывается успехом. Незакрытая кавычка при нестрогом разборе проглатывает
    остаток файла одной ячейкой, и строки исчезают тем же молчаливым способом.

    Перевод строки внутри кавычек при этом законен — Excel такие ячейки
    делает, — и разбор обязан его пережить. Поэтому текст отдаётся потоком, а
    не разрезается заранее: `"foo\nbar"` после `splitlines()` склеивается в
    `foobar`, и сборка проверяет уже другую фразу, ничего не сказав."""
    reader = csv.DictReader(io.StringIO(text, newline=""), restkey=EXTRA,
                            restval=None, strict=True, delimiter=delimiter)
    try:
        header = reader.fieldnames or []
    except csv.Error as failure:
        raise DirectFailure(
            f"{where}: шапка таблицы не разбирается: {failure}.") from None
    if EXTRA in header:
        # `restkey` кладёт хвост строки под это имя, и столбец с тем же
        # названием слился бы с ним: лишние значения стали бы прочитанными.
        raise DirectFailure(
            f"{where}: столбец называется «{EXTRA}» — этим именем разбор "
            f"обозначает хвост строки, которому не хватило столбцов. "
            f"Переименуйте столбец.")
    twice = sorted({one for one in header if header.count(one) > 1})
    if twice:
        raise DirectFailure(
            f"{where}: столбец повторяется: {', '.join(twice)}. Разбор оставил "
            f"бы под этим именем последнее значение, а первое потерялось бы — "
            f"данные собрались бы не те, и команда сказала бы «готово».")
    try:
        found = list(reader)
    except csv.Error as failure:
        raise DirectFailure(
            f"{where}: таблица не разбирается: {failure}. Чаще всего это "
            f"незакрытая кавычка: нестрогий разбор проглотил бы остаток файла "
            f"одной ячейкой, и строки потерялись бы молча."
        ) from None
    out = []
    for number, row in enumerate(found, start=1):
        place = f"{where}, строка {number}"
        if EXTRA in row:
            # Незакавыченная запятая в прайсе. Без `restkey` хвост уезжает под
            # ключ `None`, и разбор падает трассировкой на склейке имён.
            raise DirectFailure(
                f"{place}: значений больше, чем столбцов в шапке "
                f"({len(header)}), {EXTRA}: "
                f"{', '.join(excerpt(one, 20) for one in row[EXTRA])}. Чаще "
                f"всего это незакавыченный разделитель внутри ячейки.")
        # Пустая ячейка приводится к отсутствию здесь, а не полем формы, и
        # это свойство формата, а не выбор: таблица пустую ячейку от
        # незаполненного столбца не отличает — их набирают одинаково. Поэтому
        # приведение идёт до разбора по форме и у выгрузки с открытым перечнем
        # столбцов, где объявленных полей нет вовсе.
        out.append(shape.check({name: given(one) for name, one in row.items()},
                               place))
    return out


def _json(text: str, where: str):
    try:
        return json.loads(text)
    except json.JSONDecodeError as failure:
        raise DirectFailure(
            f"{where} не разбирается как JSON: {failure}") from None
