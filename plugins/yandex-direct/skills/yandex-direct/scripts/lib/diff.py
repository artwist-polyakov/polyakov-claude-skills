"""Сравнение отправленного и перечитанного объекта.
Проверяются отправленные поля и состав списков; порядок списков не важен.
Разрешённые преобразования задаются по путям в Rules. Пропавшие значения
считаются расхождениями, неизвестные поля не игнорируются молча."""

from __future__ import annotations

from decimal import Decimal, DecimalException

from config import excerpt

# Что сверка нашла в одном месте структуры.
MISMATCH = "расхождение"
NORMALIZED = "нормализация"

UNCHECKED = "не проверено"

MISSING = object()


class Difference:
    """Одно место, где отправленное и прочитанное разошлись."""

    __slots__ = ("path", "requested", "actual", "kind", "why")

    def __init__(self, path: str, requested, actual, kind: str, why: str = ""):
        self.path = path
        self.requested = requested
        self.actual = actual
        self.kind = kind
        self.why = why

    @property
    def real(self) -> bool:
        return self.kind == MISMATCH

    def explain(self) -> str:
        where = self.path or "объект"
        return f"{where}: {self.why}" if self.why else (
            f"{where}: просили {_shown(self.requested)}, "
            f"прочитано {_shown(self.actual)}"
        )

    def __repr__(self) -> str:
        return f"<Difference {self.kind} {self.explain()}>"


class Comparison:
    """Итог сверки одного объекта: расхождения отдельно, нормализации отдельно.

    Обе половины наружу, а не только первая. Нормализации не останавливают
    пакет, но именно они объясняют человеку, почему прочитанное не совпало с
    отправленным дословно, — а без объяснения он идёт проверять руками."""

    def __init__(self, findings=None, *, where: str = ""):
        self.findings = list(findings or [])
        self.where = where

    @property
    def differences(self) -> list:
        return [item for item in self.findings if item.real]

    @property
    def normalizations(self) -> list:
        return [item for item in self.findings if item.kind == NORMALIZED]

    @property
    def unchecked(self) -> list:
        """Поля, которых чтение не возвращает: сверить их было нечем."""
        return [item for item in self.findings if item.kind == UNCHECKED]

    @property
    def ok(self) -> bool:
        return not self.differences

    def explain(self) -> str:
        head = f"{self.where}: " if self.where else ""
        if self.ok:
            said = "записано как просили"
            if self.normalizations:
                said += f", нормализаций {len(self.normalizations)}"
            if self.unchecked:
                said += (f", полей без перечитывания {len(self.unchecked)} "
                         f"({', '.join(one.path for one in self.unchecked)})")
            return head + said
        return head + "; ".join(item.explain() for item in self.differences)

    def __len__(self) -> int:
        return len(self.findings)

    def __repr__(self) -> str:
        return f"<Comparison {'ok' if self.ok else 'расхождений ' + str(len(self.differences))}>"


class Rules:
    """Допустимые преобразования, задаваемые полными путями полей.

    steps: шаг округления денег; full: списки, которые get возвращает целиком;
    phrases: поля минус-фраз с нормализацией ё/е и операторов;
    unread: поля, которые API не возвращает. Остальные различия — ошибки.
    """

    __slots__ = ("steps", "full", "phrases", "unread")

    def __init__(self, steps=None, full=(), phrases=(), unread=()):
        self.steps = dict(steps or {})
        self.full = frozenset(full or ())
        self.phrases = frozenset(phrases or ())
        self.unread = frozenset(unread or ())

    def step(self, path: str):
        return self.steps.get(_plain(path))

    def whole(self, path: str) -> bool:
        return _plain(path) in self.full

    def phrase(self, path: str) -> bool:
        return _plain(path) in self.phrases

    def unreadable(self, path: str) -> bool:
        """Объявлено ли, что чтение этого поля не возвращает."""
        return _plain(path) in self.unread

    def __repr__(self) -> str:
        return (f"<Rules шагов {len(self.steps)}, полных {len(self.full)}, "
                f"фраз {len(self.phrases)}, "
                f"непрочитываемых {len(self.unread)}>")


def compare(requested, actual, *, steps=None, full=(), phrases=(), unread=(),
            rules=None, path: str = "", where: str = "") -> Comparison:
    """Сверить отправленное с прочитанным.

    Послабления задаются путями — `steps`, `full`, `phrases`, `unread`; что
    каждое означает, описано в `Rules`. Готовый набор можно передать целиком в
    `rules`."""
    findings = []
    if rules is None:
        rules = Rules(steps, full, phrases, unread)
    _walk(requested, actual, path, rules, findings)
    return Comparison(findings, where=where)


def _walk(requested, actual, path: str, rules, out: list) -> None:
    if rules.unreadable(path) and actual is MISSING:
        # Прочитанное здесь — `_value`, а не сам страж `MISSING`: наружу находка
        # уходит в журнал и в отчёт, и сторож в них выглядел бы значением.
        out.append(Difference(
            path, requested, _value(actual), UNCHECKED,
            f"просили {_shown(requested)}, а чтение этого поля не возвращает "
            f"вовсе — записанным его никто не подтверждал",
        ))
        return
    if _empty(requested):
        _walk_value(requested, actual, path, rules, out)
        return
    if isinstance(requested, dict):
        _walk_dict(requested, actual, path, rules, out)
        return
    if isinstance(requested, list):
        _walk_list(requested, actual, path, rules, out)
        return
    _walk_value(requested, actual, path, rules, out)


def _walk_dict(requested: dict, actual, path: str, rules, out: list) -> None:
    if not isinstance(actual, dict):
        out.append(Difference(
            path, requested, _value(actual), MISMATCH,
            f"просили объект, прочитано {_kind(actual)}",
        ))
        return
    for key in requested:
        found = actual[key] if key in actual else MISSING
        _walk(requested[key], found, _join(path, key), rules, out)


def _walk_list(requested: list, actual, path: str, rules, out: list) -> None:
    if not isinstance(actual, list):
        out.append(Difference(
            path, requested, _value(actual), MISMATCH,
            f"просили массив, прочитано {_kind(actual)}",
        ))
        return
    started = len(out)
    taken = _pairs([_match(item, actual, rules, _at(path, number))
                    for number, item in enumerate(requested)])
    rest = [place for place in range(len(actual)) if place not in taken.values()]
    moved = 0
    unmatched = []
    for number, item in enumerate(requested):
        place = taken.get(number)
        if place is None:
            unmatched.append((number, item))
            continue
        if place != number:
            moved += 1
        _walk(item, actual[place], _at(path, number), rules, out)
    for number, item in unmatched:
        if rest:
            place = rest.pop(0)
            _walk(item, actual[place], _at(path, number), rules, out)
        else:
            out.append(Difference(
                _at(path, number), item, MISSING, MISMATCH,
                f"элемент {_shown(item)} в ответе не найден",
            ))
    whole = rules.whole(path)
    for place in rest:
        out.append(Difference(
            _at(path, place), MISSING, actual[place],
            NORMALIZED if whole else MISMATCH,
            f"в ответе элемент, которого не отправляли: {_shown(actual[place])}"
            + ("" if whole else "; массив заменяется целиком, значит снятое "
                                "не снялось"),
        ))
    if moved and not any(item.real for item in out[started:]):
        out.append(Difference(
            path, None, None, NORMALIZED,
            f"порядок элементов изменился ({moved})",
        ))


def _match(item, actual: list, rules, path: str) -> tuple:
    """Места в ответе, куда элемент годится: дословно и с точностью до нормализации.

    Путь передаётся настоящий, а не пустой: по нему ищется шаг округления, и
    примерка вслепую не узнала бы округлённую ставку — пара не нашлась бы, а
    элемент попал бы в непарные."""
    exact, close = [], []
    for place, found in enumerate(actual):
        probe = []
        _walk(item, found, path, rules, probe)
        probe = [one for one in probe if one.kind != UNCHECKED]
        if not probe:
            exact.append(place)
        elif not any(difference.real for difference in probe):
            close.append(place)
    return exact, close


def _pairs(fits: list) -> dict:
    """Кого с кем свести, чтобы пар вышло как можно больше."""
    place_of, where_of = {}, {}
    for edges in ([exact for exact, _ in fits],
                  [exact + close for exact, close in fits]):
        for number in range(len(fits)):
            if number not in where_of:
                _augment(number, edges, place_of, where_of)
    return where_of


def _augment(number: int, edges: list, place_of: dict, where_of: dict) -> bool:
    """Найти элементу место, при нужде подвинув занявших — цепочкой."""
    parent, seen, stack = {}, set(), [number]
    while stack:
        current = stack.pop()
        for place in edges[current]:
            if place in seen:
                continue
            seen.add(place)
            parent[place] = current
            holder = place_of.get(place)
            if holder is None:
                # Место свободно: разворачиваем цепочку назад, каждый уступает
                # своё место следующему и получает то, до которого дошёл.
                while place is not None:
                    owner = parent[place]
                    freed = where_of.get(owner)
                    place_of[place], where_of[owner] = owner, place
                    place = freed
                return True
            stack.append(holder)
    return False


def _walk_value(requested, actual, path: str, rules, out: list) -> None:
    if actual is MISSING:
        if requested is None or _empty(requested):
            # Просили очистить — и поле не вернулось. Отсутствующее и пустое
            # здесь одно и то же: `null` в ответе Директ и не обещает.
            return
        out.append(Difference(
            path, requested, MISSING, MISMATCH,
            f"поле не вернулось, а просили {_shown(requested)}",
        ))
        return
    if requested is None or _empty(requested):
        if actual is None or _empty(actual):
            return
        out.append(Difference(
            path, requested, actual, MISMATCH,
            f"просили очистить, прочитано {_shown(actual)}",
        ))
        return
    if isinstance(requested, str) and isinstance(actual, str):
        _walk_text(requested, actual, path, rules, out)
        return
    if _numeric(requested) and _numeric(actual):
        _walk_number(requested, actual, path, rules, out)
        return
    if requested == actual and type(requested) is type(actual):
        return
    out.append(Difference(path, requested, actual, MISMATCH,
                          f"просили {_shown(requested)}, прочитано {_shown(actual)}"))


def _walk_text(requested: str, actual: str, path: str, rules, out: list) -> None:
    """Текст: известные нормализации Директа, всё остальное — расхождение."""
    if requested == actual:
        return
    notes = []
    left, right = requested, actual
    squeezed = (" ".join(left.split()), " ".join(right.split()))
    if squeezed != (left, right):
        left, right = squeezed
        notes.append("пробелы обрезаны")
    if left != right and rules.phrase(path):
        bare = right[1:] if right[:1] in ("!", "+") else right
        if _yo(bare) == _yo(left):
            if bare != right:
                notes.append(f"Директ выставил оператор «{right[:1]}»")
            if bare != left:
                notes.append("ё заменена на е")
            left = right
    if left != right:
        out.append(Difference(
            path, requested, actual, MISMATCH,
            f"просили {_shown(requested)}, прочитано {_shown(actual)}",
        ))
        return
    out.append(Difference(path, requested, actual, NORMALIZED, ", ".join(notes)))


def _walk_number(requested, actual, path: str, rules, out: list) -> None:
    want, got = _decimal(requested), _decimal(actual)
    if want is None or got is None:
        if requested != actual:
            out.append(Difference(path, requested, actual, MISMATCH,
                                  f"просили {_shown(requested)}, прочитано {_shown(actual)}"))
        return
    if want == got:
        return
    step = _decimal(rules.step(path))
    if step is not None and step > 0 and abs(got - want) < step and got % step == 0:
        out.append(Difference(path, requested, actual, NORMALIZED,
                              f"округлено до шага {step}"))
        return
    out.append(Difference(path, requested, actual, MISMATCH,
                          f"просили {_shown(requested)}, прочитано {_shown(actual)}"))


def _yo(text: str) -> str:
    return text.replace("ё", "е").replace("Ё", "Е")


def _numeric(value) -> bool:
    return isinstance(value, (int, float, Decimal)) and not isinstance(value, bool)


def _decimal(value):
    if value is None or isinstance(value, bool):
        return None
    try:
        number = Decimal(str(value))
    except (DecimalException, TypeError, ValueError):
        return None
    return number if number.is_finite() else None


def _empty(value) -> bool:
    """Пустое значение: пустой список, пустой объект, пустая обёртка `Items`."""
    if isinstance(value, str):
        return not value.strip()
    if isinstance(value, list):
        return not value
    if isinstance(value, dict):
        if not value:
            return True
        return set(value) == {"Items"} and not value["Items"]
    return False


def _kind(value) -> str:
    if value is MISSING:
        return "ничего"
    return {list: "массив", dict: "объект", str: "строка"}.get(
        type(value), type(value).__name__
    )


def _value(value):
    return None if value is MISSING else value


def _shown(value) -> str:
    if value is MISSING:
        return "ничего"
    if isinstance(value, str):
        return f"«{excerpt(value, 80)}»"
    return excerpt(value, 80)


def _join(path: str, key: str) -> str:
    return f"{path}.{key}" if path else str(key)


def _at(path: str, number: int) -> str:
    return f"{path}[{number}]"


def _plain(path: str) -> str:
    """Путь без индексов: шаг округления задаётся полю, а не элементу."""
    out = []
    skip = False
    for character in path:
        if character == "[":
            skip = True
        elif character == "]":
            skip = False
        elif not skip:
            out.append(character)
    return "".join(out)
