"""Справочник `Constants`: величины, которые Директ отдаёт сам."""

from __future__ import annotations

import copy
import re

from cache import Cache, tsv
from config import DirectFailure, excerpt
from errors import TransportFailure

# Имя справочника у Директа и имя записи в кэше. Запись лежит в корне кэша
# плоско: каталог в корне кэша — это кабинет, а `Constants` кабинету не
# принадлежит (`references/CLIENT_LOGIN.md`, раздел 3).
DICTIONARY = "Constants"
RECORD = "constants"

# Справочники сохраняются в кэше на сутки.
LAYER = "dictionaries"

# Величина справочника — целое без знака. Строгий разбор, а не `int`: тот
# принимает подчёркивания и пробелы по краям, и `"5_6"` становится 56 —
# опечатка выглядит пределом, которого Директ не называл.
NUMBER = re.compile(r"^[0-9]+$")


class Constants:
    """Прочитанный справочник `Constants`: имя — величина.

    Собирается разбором ответа, а не доверием к нему: величины приходят
    строками, и всё, что не оказалось целым, — отказ, а не пропуск. Величина,
    молча ставшая `None`, отключает предел, и заметить это можно только в
    кабинете."""

    __slots__ = ("values", "items")

    def __init__(self, values: dict, items=None):
        self.values = dict(values)
        # Ответ как он пришёл — для журнала, кэша и сверки. Разобранное его не
        # заменяет: спор о том, что именно ответил Директ, решается ответом.
        self.items = list(items or ())

    def __repr__(self) -> str:
        return f"<Constants {len(self.values)} величин>"

    # -- чтение -------------------------------------------------------------

    @classmethod
    def load(cls, client, *, cache=None) -> "Constants":
        """Справочник из кэша, а при промахе — из API.

        Кэш собирается без кабинета намеренно: заголовок адресации сервису не
        уходит, ответ одинаков для всех кабинетов, и запись, разложенная по
        кабинетам, была бы одним и тем же файлом в сотне копий, каждая со
        своим сроком."""
        store = cache if cache is not None else Cache()
        fetched: list = []

        def produce():
            items = client.dictionaries(DICTIONARY)[DICTIONARY]
            # Разбор до записи, а не после: испорченный ответ не должен лечь в
            # кэш на сутки. Разобранное запоминается здесь же — оно уже
            # оплачено баллом, и потерять его из-за диска нельзя.
            fetched.append(cls.parse(items))
            return items

        try:
            entry = store.through(
                RECORD, LAYER, produce,
                # Индекс кладётся рядом с данными: имя и величина как их
                # прислал Директ, чтобы искать `grep`, не открывая ответ.
                index=lambda items: tsv(items, ("Name", "Value")),
            )
        except (DirectFailure, OSError) as failure:
            # Сохранение и чтение — разные неудачи. Справочник, прочитанный и
            # разобранный, верен независимо от того, легла ли копия на диск:
            # отдать вместо него офлайн-фолбэк значило бы проверять запись по
            # вчерашним числам из-за переполненного диска. Молчать при этом
            # нельзя — завтра за справочник заплатят ещё раз.
            if not fetched:
                raise
            store.warn(
                f"Справочник {DICTIONARY} прочитан, но в кэш не лёг: "
                f"{failure}. Величины взяты прочитанные; следующий запуск "
                f"спросит их заново и заплатит за это баллом."
            )
            return fetched[0]
        # На попадании в кэш `produce` не звался — разбирается сохранённое.
        return fetched[0] if fetched else cls.parse(entry.data)

    @classmethod
    def parse(cls, items) -> "Constants":
        """Разбор массива `ConstantsItem` в величины.

        Повторное имя — отказ, а не «последнее выигрывает»: две величины под
        одним именем означают, что ответ понят неверно, и выбор одной из них
        был бы догадкой. `Currencies[].Properties` в документации записаны той
        же структурой `ConstantsItem`, и разбор, взявший их за ограничения на
        параметры, собрал бы `MinimumBid` в перечень длин — вот та ошибка,
        которую ловит это правило."""
        if not isinstance(items, list):
            raise TransportFailure(
                f"Справочник {DICTIONARY} пришёл как {type(items).__name__}, "
                f"а не массивом. Разбирать нечего.",
                retryable=False,
            )
        values: dict = {}
        for position, item in enumerate(items):
            if not isinstance(item, dict):
                raise TransportFailure(
                    f"Запись {position} справочника {DICTIONARY} — "
                    f"{type(item).__name__}, а не объект: "
                    f"{excerpt(repr(item), 80)}",
                    retryable=False,
                )
            name = item.get("Name")
            if not isinstance(name, str) or not name:
                raise TransportFailure(
                    f"У записи {position} справочника {DICTIONARY} нет имени: "
                    f"{excerpt(repr(item), 80)}",
                    retryable=False,
                )
            if name in values:
                raise TransportFailure(
                    f"Имя «{excerpt(name, 64)}» встретилось в справочнике "
                    f"{DICTIONARY} дважды. Какая из двух величин верна, "
                    f"ответ не говорит.",
                    retryable=False,
                )
            values[name] = _number(name, item.get("Value"))
        return cls(values, items)

    # -- сверка с офлайн-фолбэком -------------------------------------------

    def unknown(self, limits) -> list:
        """Имена, которых нет в перечне `names` среза.

        Не отказ: Директ вправе добавить величину, и остановить из-за этого
        работу значило бы сломать скилл чужим обновлением. Это сигнал
        пересверить справочники по регламенту — тот же, что и расхождение
        величин."""
        names = set(_source(limits).get("names") or ())
        return sorted(set(self.values) - names)

    def missing(self, limits) -> list:
        """Имена, которые срез ждёт, а справочник не прислал."""
        names = set(_source(limits).get("names") or ())
        return sorted(names - set(self.values))

    def differences(self, limits) -> list:
        """Расхождения с офлайн-фолбэком: что в файле, что у Директа.

        Каждая запись — `(имя, путь, значение файла, значение справочника)`.
        Пустой список означает, что фолбэк совпал со справочником целиком."""
        found = []
        for name, path, stored in _bound(limits):
            fresh = self.values.get(name)
            if fresh is not None and fresh != stored:
                found.append((name, tuple(path), stored, fresh))
        return found

    # -- старшинство --------------------------------------------------------

    def apply(self, limits):
        """Новый `Limits`, у которого прочитанное стоит на месте файлового.

        Копия, а не правка на месте: тот же `Limits` мог быть уже прочитан
        кем-то ещё, и подмена под ним превратила бы предел в состояние,
        меняющееся между двумя вопросами об одном поле.

        Величины, которых в файле нет вовсе, в данные не дописываются: место
        им в `runtime`, откуда их спрашивают по имени справочника. Дописанные
        в срез, они стали бы неотличимы от офлайн-фолбэка — то есть от числа,
        которое действует и без справочника."""
        data = copy.deepcopy(limits.data)
        for name, path, _ in _bound(limits):
            fresh = self.values.get(name)
            if fresh is None:
                continue
            node = data
            for step in path[:-1]:
                node = node[step]
            node[path[-1]] = fresh
        fresh_limits = type(limits)(data)
        fresh_limits.runtime = {
            name: self.values[name]
            for name in _source(limits).get("unbound_names") or ()
            if name in self.values
        }
        return fresh_limits


def _number(name: str, value) -> int:
    """Величина справочника целым числом. Всё, что не целое, — отказ."""
    if isinstance(value, bool) or not isinstance(value, (str, int)):
        raise TransportFailure(
            f"Величина «{excerpt(name, 64)}» справочника {DICTIONARY} пришла "
            f"как {type(value).__name__}, а не числом или строкой.",
            retryable=False,
        )
    if isinstance(value, int):
        number = value
    else:
        if not NUMBER.match(value):
            raise TransportFailure(
                f"Величина «{excerpt(name, 64)}» справочника {DICTIONARY} "
                f"записана как «{excerpt(value, 40)}» — это не целое число. "
                f"Подставить её пределом значило бы выдать разбор за замер.",
                retryable=False,
            )
        number = int(value)
    if number <= 0:
        raise TransportFailure(
            f"Величина «{excerpt(name, 64)}» справочника {DICTIONARY} равна "
            f"{number}. Ноль здесь — отключённый предел, а не значение.",
            retryable=False,
        )
    return number


def _source(limits) -> dict:
    """Раздел `text.runtime_source` среза лимитов."""
    source = ((limits.data.get("text") or {}).get("runtime_source") or {})
    if not isinstance(source, dict) or not source.get("bindings"):
        raise DirectFailure(
            "В срезе лимитов нет соответствия именам справочника "
            f"{DICTIONARY} (text.runtime_source.bindings). Без него "
            "прочитанное нечем сопоставить с величинами файла, и "
            "старшинство справочника не выражается."
        )
    return source


def _bound(limits) -> list:
    """Соответствие среза, развёрнутое в тройки «имя, путь, значение файла».

    Соответствие читается из среза, а не повторяется здесь таблицей: две
    таблицы одного и того же расходятся, и расходятся молча. Путь, ведущий в
    пустоту, — отказ: замещать было бы нечего, и подмена прошла бы мимо всех
    потребителей при чистом отчёте."""
    found = []
    for entry in _source(limits).get("bindings") or ():
        name, path = entry.get("name"), entry.get("path")
        node = limits.data
        for step in path:
            node = node.get(step) if isinstance(node, dict) else None
            if node is None:
                break
        if not isinstance(node, int) or isinstance(node, bool):
            raise DirectFailure(
                f"Соответствие «{excerpt(str(name), 64)}» ведёт к "
                f"{'.'.join(path)}, а величины там нет. Срез лимитов и "
                f"соответствие разошлись."
            )
        found.append((name, list(path), node))
    return found


__all__ = ["Constants", "DICTIONARY", "LAYER", "RECORD"]
