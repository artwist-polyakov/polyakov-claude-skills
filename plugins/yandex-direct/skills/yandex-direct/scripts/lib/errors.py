"""Классификация ошибок Директа и человекочитаемые сообщения."""

from __future__ import annotations

import json

from config import DirectFailure, excerpt

TRANSPORT = "transport"
SERVER = "server"
AUTH = "auth"
LIMITS = "limits"
VALIDATION = "validation"
UNKNOWN = "unknown"

# Коды, на которые смотрит логика, а не только текст сообщения. 54 в ответ на
# `AgencyClients.get` означает «токен не агентский» и разбирает его
# мультикабинет — `accounts.detect`. Остальные коды живут в таблице ниже;
# заводить им имена «на будущее» незачем — имя без потребителя врёт о том, что
# на него кто-то опирается.
NO_RIGHTS = 54

# Второй такой код. Отказ 152 на попытке узнать остаток баллов сам и есть
# ответ: платить придётся баллами агентства. Разбирает его тот же
# мультикабинет, что и 54.
NO_UNITS = 152

# Предупреждения начинаются с этого кода: они не отменяют операцию и не стоят
# баллов, поэтому ошибками не считаются.
WARNING_FROM = 10000

# `код: (класс, повторять ли, подсказка человеку)`.
#
# Подсказка — решение, а не пересказ сообщения Директа: сырой «error_code 53»
# бесполезен, «токен истёк, получите новый» полезен. Пустая подсказка означает,
# что своего текста у нас нет и показывается ответ Директа.
#
# Повтор здесь — только про то, имеет ли смысл повторять **тот же** запрос.
# Право на повтор записи решается отдельно: ключей идемпотентности в API нет,
# и повторный `add` после разрыва создаёт дубль.
CODES = {
    # --- Серверные (баллы за них не списываются) --------------------------
    52: (SERVER, True, "Сервер авторизации Яндекса временно недоступен."),
    1000: (SERVER, True, "Директ отвечает внутренней ошибкой."),
    1001: (SERVER, True, "Директ не смог инициализировать сервис."),
    1002: (SERVER, True, "Директ не смог выполнить операцию."),
    1003: (SERVER, True, "Директ не смог создать учётную запись."),
    1004: (
        SERVER,
        False,
        "Директ не смог создать клиента. Повтор с тем же логином бессмыслен — "
        "нужен другой логин.",
    ),
    1020: (
        SERVER,
        False,
        "Внутренняя ошибка Директа: не определён список валют. Повтор не "
        "поможет, нужно обращение в поддержку.",
    ),
    # --- Доступ и аутентификация ------------------------------------------
    53: (
        AUTH,
        False,
        "Токен недействителен или истёк. Получите новый по инструкции в "
        "config/README.md.",
    ),
    54: (
        AUTH,
        False,
        "Нет прав на эту операцию. У кода четыре документированных причины, и "
        "«токен не агентский» — только одна из них: аккаунт может ждать "
        "перевода в валюту или иметь приостановленный доступ. Штатным ответом "
        "код 54 считается ровно у AgencyClients.get.",
    ),
    55: (VALIDATION, False, "Директ не знает такого метода."),
    58: (
        AUTH,
        False,
        "Заявка на доступ к API не одобрена. Порядок подачи — в "
        "references/API_ACCESS.md.",
    ),
    513: (
        AUTH,
        False,
        "Логин не подключён к Яндекс Директу: в этом аккаунте нет кабинета.",
    ),
    3000: (
        AUTH,
        False,
        "Нет доступа к API. Причин три: доступ закрыт, аккаунт переводится в "
        "валюту или запрещён этот IP-адрес.",
    ),
    3001: (
        AUTH,
        False,
        "Нет доступа к методу. Часть методов агентства открывается по "
        "отдельной заявке.",
    ),
    # --- Ограничения и баллы ----------------------------------------------
    152: (
        LIMITS,
        False,
        "Баллы исчерпаны. Следующее начисление придёт в начале очередного "
        "часового интервала; повторять сразу бессмысленно — за неудачный "
        "вызов спишется ещё. Под агентским токеном можно заплатить баллами "
        "агентства: YANDEX_DIRECT_USE_OPERATOR_UNITS.",
    ),
    506: (
        LIMITS,
        True,
        "Больше пяти одновременных запросов от одного рекламодателя. Снизьте "
        "параллелизм.",
    ),
    7001: (
        LIMITS,
        False,
        "Достигнут лимит кабинета на количество объектов. Действующие "
        "значения — в Restrictions метода Clients.get.",
    ),
    9300: (
        LIMITS,
        False,
        "В запросе больше объектов, чем допускает метод. Разрежьте пакет: "
        "размеры батчей — в references/limits.json.",
    ),
    9301: (
        LIMITS,
        False,
        "Слишком широкое условие отбора: сумма Limit и Offset больше 120 000. "
        "Без Limit предел для Offset — 110 000.",
    ),
    # --- Данные запроса ----------------------------------------------------
    #
    # Коды 7000 и 7002 справочник перечисляет в разделе «Ограничения и баллы»
    # рядом с 7001. Классом здесь выбрана валидация, потому что класс отвечает
    # на вопрос «что делать»: 7001 снимается перечитыванием лимитов кабинета,
    # а 7000 и 7002 — правкой самого объекта, как и весь остальной 5xxx/6xxx.
    7000: (
        VALIDATION,
        False,
        "Количество элементов вне допустимого диапазона. Комментарий "
        "документации про четыре быстрые ссылки устарел — их восемь.",
    ),
    7002: (VALIDATION, False, "Домен трекинговой системы не поддерживается."),
    8000: (
        VALIDATION,
        False,
        "Некорректный запрос: неизвестный или обязательный параметр, "
        "невалидный JSON, значение вне набора FieldNames — либо не указан "
        "OAuth-токен.",
    ),
    8300: (VALIDATION, False, "Неверный статус объекта для этой операции."),
    8800: (
        VALIDATION,
        False,
        "Объект не найден: несуществующий логин в Client-Login либо "
        "идентификатор чужого или удалённого объекта.",
    ),
    9800: (
        VALIDATION,
        False,
        "Один и тот же объект указан в запросе дважды. Дубли не выполняются "
        "целиком, остальные объекты обрабатываются.",
    ),
}

# Коды v4 и Live 4 — своя нумерация, и совпадает она с версией 5 не везде.
# Здесь только то, что проверено живьём на боевом кабинете 27.08.2026:
# выдумывать остальное нельзя, иначе подсказка объяснит не ту ошибку.
CODES_V4 = {
    53: (
        AUTH,
        False,
        "Четвёртая версия не приняла токен. Она берёт его из тела запроса, а "
        "не из заголовка Authorization — заголовок она игнорирует и отвечает "
        "ошибкой авторизации.",
    ),
    71: (VALIDATION, False, "Параметры запроса указаны неверно."),
}


def is_warning(code) -> bool:
    """Предупреждение, а не ошибка: операция выполнена."""
    return isinstance(code, int) and not isinstance(code, bool) and code >= WARNING_FROM


def _row(code, version: str = "v5"):
    table = CODES_V4 if version == "v4" else CODES
    if isinstance(code, bool) or not isinstance(code, int):
        return None
    row = table.get(code)
    if row is not None:
        return row
    if version == "v4":
        # Своей таблицы у четвёртой версии почти нет, а чужую применять нельзя.
        return None
    # Диапазоны — запасной вариант для кодов, которых нет в таблице. Серверные
    # повторяются, остальное нет: неизвестный код повторять опасно, а
    # неповторённый вызов стоит одного объяснения человеку.
    if 1000 <= code < 1100:
        return (SERVER, True, "")
    if code >= 3000:
        return (VALIDATION, False, "")
    return None


def classify(code, version: str = "v5") -> str:
    """Класс отказа по коду ошибки."""
    row = _row(code, version)
    return row[0] if row else UNKNOWN


def is_retryable(code, version: str = "v5") -> bool:
    """Имеет ли смысл повторять тот же запрос.

    Неизвестный код не повторяется. Ошибка вызова тарифицируется — по замеру
    от 21 до 50 баллов (`references/ERRORS_AND_LIMITS.md`, раздел 6.2), — и
    цикл повторов по коду, смысла которого мы не знаем, тратит их зря."""
    row = _row(code, version)
    return bool(row and row[1])


def hint(code, version: str = "v5") -> str:
    """Подсказка человеку или пустая строка, если своего текста нет."""
    row = _row(code, version)
    return row[2] if row else ""


# --------------------------------------------------------------------------
# Чтение полей ответа
# --------------------------------------------------------------------------


_TYPE_RU = {
    list: "массивом",
    dict: "объектом",
    str: "строкой",
    int: "целым числом",
}


def _typed(container: dict, key: str, kind, where: str):
    # ответ читается напрямую: это и есть помощник — наличие ключа проверено
    # вызывающей функцией, а годность значения проверяется прямо здесь
    value = container[key]
    # bool — подкласс int, и `True` в поле идентификатора или смещения прошёл
    # бы за число.
    if isinstance(value, bool) and kind is not bool:
        good = False
    else:
        good = isinstance(value, kind)
    if not good:
        raise TransportFailure(
            f"{where}: поле {key} пришло не {_TYPE_RU.get(kind, kind)} "
            f"({type(value).__name__}).",
            retryable=False,
        )
    return value


def optional(container: dict, key: str, kind, where: str):
    """Значение поля или None, если ключа нет.

    Отсутствие ключа — законный ответ «этого нет». Ключ с негодным значением —
    повреждённый ответ, и выдать его за отсутствие нельзя."""
    if not isinstance(container, dict) or key not in container:
        return None
    return _typed(container, key, kind, where)


def required(container: dict, key: str, kind, where: str):
    """Значение поля, которое обязано быть в ответе."""
    if not isinstance(container, dict) or key not in container:
        raise TransportFailure(
            f"{where}: в ответе нет поля {key}. Ответ неполон.",
            retryable=False,
        )
    return _typed(container, key, kind, where)


# --------------------------------------------------------------------------
# Исключения
# --------------------------------------------------------------------------

class TransportFailure(DirectFailure):
    """Не дошло или пришло неразборчивым: сеть, таймаут, чужой прокси."""

    kind = TRANSPORT
    retryable = True

    def __init__(self, message: str, *, retryable: bool = True, status: int = None,
                 headers=None):
        self.status = status
        # Заголовки ответа, если он всё-таки пришёл. Директ берёт баллы за
        # вызов, а не за читаемость ответа, и на нечитаемом теле заголовок
        # `Units` — единственный след списанного. Потерять его здесь значит
        # занизить расход ровно на тех вызовах, о которых меньше всего
        # известно.
        self.headers = headers
        super().__init__(message, retryable=retryable)


class ApiFailure(DirectFailure):
    """Отказ уровня запроса: Директ ответил объектом ошибки.

    Запрос не выполнен целиком. Ошибки отдельных элементов батча сюда не
    попадают — им место в `BatchResult`, иначе успешные элементы теряются."""

    def __init__(
        self,
        code=None,
        *,
        message: str = "",
        detail: str = "",
        request_id: str = "",
        where: str = "",
        version: str = "v5",
        raw=None,
    ):
        self.code = code if isinstance(code, int) and not isinstance(code, bool) else None
        self.request_id = excerpt(request_id, 64)
        self.where = where
        self.version = version
        self.raw = raw
        self.kind = classify(self.code, version)
        self.retryable = is_retryable(self.code, version)
        super().__init__(self._compose(code, message, detail))

    def _compose(self, code, message: str, detail: str) -> str:
        shown_code = self.code if self.code is not None else excerpt(code or "без кода", 32)
        where = f"{self.where}: " if self.where else ""
        version = "" if self.version == "v5" else f" ({self.version})"
        lines = [f"{where}Директ вернул ошибку {shown_code}{version}."]
        said = " ".join(part for part in (str(message).strip(), str(detail).strip()) if part)
        if said:
            lines.append(f"Директ: {excerpt(said)}")
        elif self.raw is not None:
            # Без error_string и error_detail от отказа не остаётся ничего,
            # кроме слова «ошибка», поэтому показывается сам объект.
            lines.append(f"Директ: {excerpt(json.dumps(self.raw, ensure_ascii=False))}")
        own = hint(self.code, self.version)
        if own:
            lines.append(own)
        if self.request_id:
            lines.append(f"Идентификатор запроса: {self.request_id}")
        return "\n".join(lines)


# --------------------------------------------------------------------------
# Уровень элемента
# --------------------------------------------------------------------------

# Предупреждения, которые означают «Директ принял запрос, но значение не
# применил». Для конвейера записи это расхождение: запрошенное не равно
# фактическому, и сверка после перечитывания обязана его увидеть.
SILENT_MISMATCH_WARNINGS = frozenset({10160, 10161, 10163, 10165, 10175})


class ItemIssue:
    """Ошибка или предупреждение отдельного элемента батча.

    Не исключение: элемент — не запрос. Поднять ошибку одного элемента
    исключением значит потерять результат остальных, а это и есть самая
    дорогая ошибка при работе с батчами Директа."""

    __slots__ = ("level", "code", "message", "details", "index")

    def __init__(self, level: str, code=None, message: str = "", details: str = "", index=None):
        self.level = level
        self.code = code if isinstance(code, int) and not isinstance(code, bool) else None
        self.message = excerpt(message, 200)
        self.details = excerpt(details, 200)
        self.index = index

    @property
    def is_error(self) -> bool:
        return self.level == "error"

    @property
    def kind(self) -> str:
        return classify(self.code)

    @property
    def means_mismatch(self) -> bool:
        """Предупреждение о непринятом значении.

        Единственный признак того, что Директ ответил успехом, но настройку не
        применил. Сверка после записи обязана считать это расхождением."""
        return not self.is_error and self.code in SILENT_MISMATCH_WARNINGS

    def __str__(self) -> str:
        where = "" if self.index is None else f"элемент {self.index}: "
        head = "ошибка" if self.is_error else "предупреждение"
        code = self.code if self.code is not None else "без кода"
        said = " — ".join(part for part in (self.message, self.details) if part)
        return f"{where}{head} {code}" + (f" · {said}" if said else "")

    def explain(self) -> str:
        """То же плюс наша подсказка, если она есть.

        Отдельно от `__str__`, потому что перечень элементов батча печатается
        построчно: подсказка на три строки рядом с каждой из тысячи ошибок
        превращает сводку в простыню."""
        own = hint(self.code) if self.is_error else ""
        return f"{self}. {own}" if own else str(self)

    def __repr__(self) -> str:
        return f"<ItemIssue {self}>"


def read_issues(element, index=None) -> list:
    """Ошибки и предупреждения одного элемента результата батча.

    Поля читаются теми же `optional()` и `required()`, что и весь остальной
    ответ: `Errors` строкой, `null` вместо массива, запись без кода — всё это
    повреждённый ответ, а не «ошибок нет».

    Наружу отказ при этом не поднимается — и в этом единственное отличие от
    прочих читателей. Элемент — не запрос: исключение отсюда отняло бы
    результат у соседних элементов, ради которых поэлементный разбор и
    существует. Поэтому повреждённое место становится ошибкой **элемента**:
    элемент неудавшийся, батч неполный, соседи целы.

    `Code` требуется наравне с самим наличием записи. Предупреждение без кода
    неотличимо от любого другого, а среди кодов есть признак неприменённого
    значения (`SILENT_MISMATCH_WARNINGS`) — потеряв его, сверка после записи
    объявила бы совпавшим то, чего Директ не применил."""
    issues = []
    if not isinstance(element, dict):
        return issues
    place = "элемент батча" if index is None else f"элемент {index}"
    for name, level in (("Errors", "error"), ("Warnings", "warning")):
        try:
            entries = optional(element, name, list, place)
        except TransportFailure as broken:
            # Повреждённый контейнер — всегда ошибка, а не предупреждение,
            # каким бы ни было поле: `Warnings` негодного типа с уровнем
            # «предупреждение» оставил бы элемент успешным, и батч отчитался
            # бы полным.
            issues.append(ItemIssue("error", None, str(broken), "", index))
            continue
        if entries is None:
            continue
        for entry in entries:
            if not isinstance(entry, dict):
                issues.append(
                    ItemIssue("error", None, f"{place}: запись {name} не объект",
                              excerpt(entry, 120), index)
                )
                continue
            where = f"{place}, запись {name}"
            try:
                issues.append(
                    ItemIssue(
                        level,
                        required(entry, "Code", int, where),
                        optional(entry, "Message", str, where) or "",
                        optional(entry, "Details", str, where) or "",
                        index,
                    )
                )
            except TransportFailure as broken:
                issues.append(ItemIssue("error", None, str(broken), "", index))
    return issues
