"""Запись в Яндекс Директ: свежий снимок, проверка ограничений, полный план
и перечитывание результата. Без --apply команда только проверяет изменения.
Исходное состояние и результат каждого запроса сохраняются в journal/."""

from __future__ import annotations

import copy
import json
import re
import sys
import time
from collections import Counter
from pathlib import Path
from urllib.parse import urlsplit

import cache as cache_module
import constants as constants_module
import diff
import policy as policies
import protocol
from config import SKILL_DIR, DirectFailure, excerpt, redact, short
from errors import optional, required
from payload import compact_payload

JOURNAL_DIR = SKILL_DIR / "journal"
JOURNAL_FILE = "audit-log.jsonl"

LIMITS_PATH = SKILL_DIR / "references" / "limits.json"

GONE_METHODS = frozenset({"delete"})

ONE_AT_A_TIME = frozenset({"delete", "archive", "moderate"})


class _WholeAccount:
    """Отбор, который выражается отсутствием `SelectionCriteria`."""

    __slots__ = ()

    def __repr__(self) -> str:
        return "весь кабинет"

    def __copy__(self):
        return self

    def __deepcopy__(self, memo):
        return self


WHOLE_ACCOUNT = _WholeAccount()

NOT_SENT = "запрос не отправлялся"
CALL_REFUSED = "вызов сорвался"
READ_REFUSED = "Директ принял запись, а перечитать не удалось"

REFUSED = "не записалось"
APPLIED_ANYWAY = "записалось вопреки отказу"
UNVERIFIED = "неизвестно"

UNEXPLAINED = "расхождение"

ITEM_ERROR = "ошибка"

WRITTEN = "записано"

OUTCOMES = frozenset({WRITTEN, REFUSED, APPLIED_ANYWAY, UNVERIFIED,
                      UNEXPLAINED, ITEM_ERROR})

ACCEPTED = "принято Директом"
PACKET_REFUSED = "пакет не записался"
PROBLEM = "проверка"
NORMALIZED = "нормализация"
UNCHECKED = "не проверено"
UNLOGGED = "без журнала"
SHOWN = "предпросмотр"

WITH_NARROW = "with_narrow"
WITHOUT_NARROW = "without_narrow"


class _Clear:
    """Просьба очистить поле: `null` в запросе, а не пропуск поля."""

    __slots__ = ()

    def __bool__(self) -> bool:
        return False

    def __repr__(self) -> str:
        return "CLEAR"


CLEAR = _Clear()


class _Unsaid:
    """«Про это не сказали» — отличить от «сказали: нечем»."""

    __slots__ = ()

    def __repr__(self) -> str:
        return "не сказано"

    def __copy__(self):
        return self

    def __deepcopy__(self, memo):
        return self


UNSAID = _Unsaid()


def warn_to_stderr(text: str) -> None:
    """Единственный путь предупреждений конвейера: stderr, секреты вырезаны."""
    print(redact(text), file=sys.stderr)


def _now() -> str:
    """Отметка времени со смещением зоны."""
    return time.strftime("%Y-%m-%dT%H:%M:%S%z", time.localtime())


class Limits:
    """Ограничения Директа из `references/limits.json`."""

    def __init__(self, data: dict):
        self.data = data
        self.runtime: dict = {}
        self._batch = _folded(data.get("batch") or {})
        self._selection = _folded(data.get("selection") or {})
        self.texts = (data.get("text") or {}).get("fields") or {}
        self.narrow = set((data.get("text") or {}).get("narrow_chars") or "")
        self.collections = data.get("collections") or {}
        api = data.get("api") or {}
        units = api.get("units") or {}
        self._units = _folded(units.get("methods") or {})
        self._error_flat = units.get("error_call_flat")
        self._error_over_call = units.get("error_call_over_call")
        self._response_max = api.get("get_max_objects_per_response")

    @classmethod
    def load(cls, path=LIMITS_PATH):
        path = Path(path)
        try:
            return cls(json.loads(path.read_text(encoding="utf-8")))
        except (OSError, ValueError) as exc:
            raise DirectFailure(
                f"Справочник лимитов {short(path)} не читается: {exc}. Без него "
                f"проверка перед записью не выполняется, а запись без неё "
                f"конвейером не является."
            ) from None

    def batch(self, service: str, method: str):
        """Сколько элементов допускает метод за один вызов. `None` — неизвестно."""
        return self._batch.get(f"{service}.{method}".casefold())

    def selection(self, service: str, criterion: str = "Ids"):
        return self._selection.get(f"{service}.get.{criterion}".casefold(), {}).get("max")

    def units_cost(self, service: str, method: str, objects=None) -> int:
        """Во сколько баллов обойдётся успешный вызов. Оценка **сверху**."""
        per_call, per_object, each = self.units_tariff(service, method)
        if objects is None:
            objects = _units_number(self._response_max, f"{service}.{method}",
                                    "api.get_max_objects_per_response")
        counted = max(objects, 0)
        success = per_call + (counted + each - 1) // each * per_object
        where = f"{service}.{method}"
        failure = max(
            _units_number(self._error_flat, where, "api.units.error_call_flat"),
            per_call + _units_number(self._error_over_call, where,
                                     "api.units.error_call_over_call"))
        return max(success, failure)

    def units_tariff(self, service: str, method: str) -> tuple:
        """Плата за вызов, за объект и за сколько объектов — как в справочнике."""
        where = f"{service}.{method}"
        price = self._units.get(where.casefold())
        if not isinstance(price, dict):
            raise DirectFailure(
                f"В {short(LIMITS_PATH)} нет цены вызова {where}. Без неё "
                f"решение, чьими баллами платить, принимается по цене в один "
                f"балл — и кабинет, которому не хватит, получит отказ 152 "
                f"вместо баллов агентства."
            )
        each = _units_number(price.get("per_objects", 1), where, "per_objects")
        if each < 1:
            raise DirectFailure(
                f"В {short(LIMITS_PATH)} цена {where} берётся за каждые {each} "
                f"объектов. Делить на это число нечего."
            )
        return (_units_number(price.get("per_call"), where, "per_call"),
                _units_number(price.get("per_object"), where, "per_object"),
                each)

    def rule_for(self, key: str) -> dict:
        """Правило справочника по имени поля. Незнакомое имя — отказ.

        Отдельным методом потому, что спрашивают правило двое — проверка и
        обрезание, — и «молча пропустить незнакомое имя» им нельзя одинаково:
        первая перестала бы проверять, второе перестало бы обрезать, и оба
        отчитались бы чисто."""
        rule = self.texts.get(key)
        if rule is None:
            raise DirectFailure(
                f"В {short(LIMITS_PATH)} нет поля «{excerpt(key, 64)}», по "
                f"которому просили проверять текст. Проверка, молча "
                f"пропускающая незнакомое имя, не проверяет ничего."
            )
        return rule

    def marks(self, key: str) -> frozenset:
        """Какие **не** буквенно-цифровые знаки допускает состав поля."""
        rule = self.rule_for(key)
        return frozenset(_allowed_extra(rule.get("allowed_chars") or ""))

    def tally(self, rule: dict, text: str) -> tuple:
        """Сколько символов насчитает Директ: пара «обычные, узкие».

        Один счёт на проверку и на обрезание. Разойдись они — предпросмотр рисовало
        бы границу не там, где отказывает проверка, и человек согласовывал бы
        текст, который в кабинет не ляжет."""
        counted = text.replace("#", "") if rule.get("template_hash_excluded") else text
        narrow = sum(1 for character in counted if character in self.narrow)
        return len(counted) - narrow, narrow

    @staticmethod
    def over(rule: dict, wide: int, narrow: int) -> bool:
        """Выбраны ли счётчики длины этого правила.

        Счётчиков два, и какой из них работает — решает `mode`: «с учётом
        узких» складывает их в один, «без учёта узких» ведёт раздельно и
        добавляет собственный предел узким."""
        limit, mode = rule.get("max_length"), rule.get("mode", WITH_NARROW)
        if limit is not None:
            counted = wide + narrow if mode == WITH_NARROW else wide
            if counted > limit:
                return True
        narrow_max = rule.get("narrow_max")
        return narrow_max is not None and narrow > narrow_max

    def cut(self, key: str, value: str) -> tuple:
        """Текст, обрезанный по пределу поля: пара «уместилось, не влезло»."""
        rule = self.rule_for(key)
        if not isinstance(value, str):
            raise DirectFailure(
                f"Обрезать по пределу поля «{excerpt(key, 64)}» просят "
                f"{type(value).__name__}, а не строку.")
        wide = narrow = 0
        for index, character in enumerate(value):
            step_wide, step_narrow = self.tally(rule, character)
            if self.over(rule, wide + step_wide, narrow + step_narrow):
                return value[:index], value[index:]
            wide, narrow = wide + step_wide, narrow + step_narrow
        return value, ""

    def text_problems(self, key: str, value) -> list:
        """Что не так с текстовым значением по правилам поля `key`."""
        rule = self.rule_for(key)
        if not isinstance(value, str):
            return [f"ожидается строка, а не {type(value).__name__}"]
        said = []
        text = value.replace("#", "") if rule.get("template_hash_excluded") else value
        wide, narrow = self.tally(rule, value)
        limit = rule.get("max_length")
        mode = rule.get("mode", WITH_NARROW)
        if limit is not None:
            counted = wide + narrow if mode == WITH_NARROW else wide
            if counted > limit:
                said.append(
                    f"длина {counted} при пределе {limit}"
                    + ("" if mode == WITH_NARROW else " (без учёта узких)")
                )
        narrow_max = rule.get("narrow_max")
        if narrow_max is not None and narrow > narrow_max:
            said.append(f"узких символов {narrow} при пределе {narrow_max}")
        least = rule.get("min_length")
        if least is not None and len(text) < least:
            said.append(f"длина {len(text)} при минимуме {least}")
        word_max = rule.get("max_word_length")
        if word_max is not None:
            long = [word for word in text.split() if len(word) > word_max]
            if long:
                said.append(
                    f"слово длиннее {word_max}: {excerpt(long[0], 40)}"
                )
        said.extend(_composition_problems(rule, text))
        return said

    def collection_problems(self, key: str, value) -> list:
        rule = self.collections.get(key)
        if rule is None:
            raise DirectFailure(
                f"В {short(LIMITS_PATH)} нет коллекции «{excerpt(key, 64)}», по "
                f"которой просили проверять состав."
            )
        items = value.get("Items") if isinstance(value, dict) else value
        if not isinstance(items, list):
            return [f"ожидается массив, а не {type(value).__name__}"]
        said = []
        if rule.get("min") is not None and len(items) < rule["min"]:
            said.append(f"элементов {len(items)} при минимуме {rule['min']}")
        if rule.get("max") is not None and len(items) > rule["max"]:
            said.append(f"элементов {len(items)} при пределе {rule['max']}")
        return said


FORBIDDEN_NAMES = {"пробел": " "}


def _allowed_extra(prose: str) -> set:
    """Разрешённые знаки из прозы справочника: «буквы, цифры, - № / % #»."""
    return {token for token in prose.replace(",", " ").split()
            if len(token) == 1 and not token.isalnum()}


# Метка доменного имени: буквы любого алфавита, цифры и дефис не по краям,
# не длиннее 63 символов — предел, объявленный самим DNS.
_LABEL = re.compile(r"[^\W_](?:(?:[^\W_]|-){0,61}[^\W_])?", re.UNICODE)


def _has_host(text: str) -> bool:
    """Есть ли у адреса протокол и домен — то, чего требует справочник."""
    try:
        parts = urlsplit(text)
        parts.port  # негодный порт виден только отсюда: роняет `ValueError`
        host = parts.hostname or ""
    except ValueError:
        return False
    if parts.scheme not in ("http", "https"):
        return False
    labels = host.split(".")
    if len(labels) < 2 or not all(_LABEL.fullmatch(one) for one in labels):
        return False
    for one in labels:
        if one.isascii():
            continue
        try:
            one.encode("idna")
        except UnicodeError:
            return False
    return True


def _composition_problems(rule: dict, text: str) -> list:
    """Состав значения: что в поле нельзя, кроме длины."""
    said = []
    for name in rule.get("forbidden") or []:
        needle = FORBIDDEN_NAMES.get(name, name)
        if needle in text:
            said.append(f"запрещённое в этом поле: {name}")
    allowed = rule.get("allowed_chars")
    if allowed:
        extra = _allowed_extra(allowed)
        bad = sorted({one for one in text
                      if not one.isalnum() and one not in extra})
        if bad:
            said.append(
                f"недопустимые символы {' '.join(bad)} — разрешены {allowed}")
    if rule.get("requires_protocol_and_domain") and text and not _has_host(text):
        said.append(
            "нет протокола или домена: адрес начинается с `http://` либо "
            "`https://` и содержит домен")
    return said


def _folded(mapping: dict) -> dict:
    return {str(key).casefold(): value for key, value in mapping.items()}


def _units_number(value, where: str, field: str) -> int:
    """Целое из справочника цен. Чужое значение — отказ, а не ноль.

    Строка на месте цены сложилась бы с числом трассировкой посреди вызова,
    а `None` — молча превратил бы оценку в ноль, то есть в оценку снизу."""
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise DirectFailure(
            f"В {short(LIMITS_PATH)} цена {where}, поле {field}, записана как "
            f"{excerpt(value, 32)}. Оценить, хватит ли кабинету баллов, по "
            f"такому значению нельзя."
        )
    return value


class AuditLog:
    """Журнал journal/<кабинет>/audit-log.jsonl. Исходное состояние сохраняется
    до первой записи. Ошибка журнала до запроса останавливает задачу; ошибка
    после запроса отражается отдельно от результата изменения кабинета."""

    def __init__(self, account: str, *, root=JOURNAL_DIR, warn=None):
        self.account = account
        self.folder = cache_module.account_dir_name(account)
        self.root = Path(root)
        self.warn = warn or warn_to_stderr


    @property
    def directory(self) -> Path:
        return self.root / self.folder

    @property
    def path(self) -> Path:
        return self.directory / JOURNAL_FILE

    def record(self, entry: dict) -> None:
        """Одна строка журнала. Отказ поднимается, а не проглатывается."""
        entry = compact_payload(entry)
        entry.setdefault("at", _now())
        entry.setdefault("account", self.account)
        try:
            self.directory.mkdir(parents=True, exist_ok=True)
            line = json.dumps(entry, ensure_ascii=False, default=str)
            with self.path.open("a", encoding="utf-8") as handle:
                handle.write(redact(line) + "\n")
        except (OSError, ValueError) as exc:
            raise DirectFailure(
                f"Журнал записей {short(self.path)} не пишется: {exc}. Запись "
                f"в Директ без журнала не идёт: прежнее состояние объектов "
                f"после неё восстановить будет нечем."
            ) from None

    def entries(self) -> list:
        """Прочитанный журнал. Нужен проверкам и разбору инцидента."""
        if not self.path.is_file():
            return []
        found = []
        # свой файл читается напрямую: журнал пишет этот же класс строкой на
        # операцию, и путь к нему задаёт конвейер.
        for number, line in enumerate(
                self.path.read_text(encoding="utf-8").splitlines(), 1):
            if not line.strip():
                continue
            try:
                found.append(json.loads(line))
            except ValueError as exc:
                raise DirectFailure(
                    f"{short(self.path)}, строка {number}: не разбирается как "
                    f"JSON ({exc}). Журнал повреждён, и читать его как полный "
                    f"нельзя."
                ) from None
        return found


class Operation:
    """Запрос записи и параметры проверки результата.

    items и changes описывают одинаковые изменения, read задаёт поля get.
    expect задаёт ожидаемый ответ, если его форма отличается от запроса;
    для смены состояния укажите нужный State или Status явно.
    read_key задаёт отбор идентификаторов, read_required сохраняет обязательные
    поля отбора сервиса при проверке удаления.
    texts и collections связывают поля с ограничениями в limits.json.
    clears разрешает null как очистку указанного поля. steps, full и phrases
    передают допустимые преобразования в diff.Rules.
    derived связывает поля запроса и ответа с разной структурой,
    unread перечисляет поля, которые API не возвращает при чтении.
    labels называет создаваемые объекты до присвоения идентификатора.
    search — пара (поле, отбор) для поиска после сбоя создания; None явно
    указывает, что найти такой объект нельзя. WHOLE_ACCOUNT убирает отбор
    SelectionCriteria, когда сервис не принимает пустой словарь.
    guard проверяет дополнительные условия до показа и перед записью."""

    def __init__(self, service: str, method: str, *, items, changes,
                 read=None, params_key: str = "", selection: str = "", expect=None,
                 id_field: str = "Id", read_key: str = "Ids",
                 results_key=None, batch_limit=None,
                 texts=None, collections=None, steps=None, full=(), phrases=(),
                 derived=(), clears=(), unread=(), labels=None, search=UNSAID,
                 expect_gone: bool = False, read_required=(), guard=None):
        self.service = protocol.service_key(service)
        self.method = method
        self.items = [dict(item) for item in items]
        self.changes = list(changes)
        self.read = dict(read or {})
        self.id_field = id_field
        self.read_key = read_key
        self.results_key = results_key
        self.batch_limit = batch_limit
        self.texts = dict(texts or {})
        self.collections = dict(collections or {})
        self.derived = tuple(_side(one, 0) for one in derived)
        self.derived_read = tuple(_side(one, 1) for one in derived)
        self.clears = tuple(clears)
        self.unread = tuple(unread)
        self.rules = diff.Rules(steps, full, phrases, self.unread_read)
        self.read_required = tuple(read_required)
        self.labels = list(labels) if labels is not None else None
        self.search_said = search is not UNSAID
        self.search = (None if search is None or search is UNSAID
                       else _search(search, self.where))
        self.params_key = params_key
        self.selection = selection
        self.guard = guard
        self.expect_gone = expect_gone or self.method.casefold() in GONE_METHODS
        self.expect = [dict(one) for one in expect] if expect is not None else None
        self.check()

    def check(self) -> None:
        """Инварианты операции. Прогоняются при сборке и в начале каждого прогона.

        Второй раз — не перестраховка: между сборкой задачи и запуском объект
        живёт в чужих руках, и правка элемента мимо плана к этому моменту уже
        сделана. Проверка при сборке её не увидела бы."""
        if self.expect is not None and len(self.expect) != len(self.items):
            raise DirectFailure(
                f"{self.where}: ожидаемых состояний {len(self.expect)} на "
                f"{len(self.items)} элементов. Сверять по позиции нельзя."
            )
        if not self.items:
            raise DirectFailure(f"{self.where}: писать нечего, элементов нет.")
        if not self.read:
            raise DirectFailure(
                f"{self.where}: не сказано, чем перечитывать объекты. Набор "
                f"полей знает только вызывающий код, а без чтения до и после "
                f"записи конвейера нет: сверять будет не с чем."
            )
        if self.expect is not None:
            for number, item in enumerate(self.items):
                here, there = (item.get(self.id_field),
                               self.wanted(number).get(self.id_field))
                if here is not None and there is not None and here != there:
                    raise DirectFailure(
                        f"{self.where}: элемент {number + 1} пишет объект "
                        f"{excerpt(here, 48)}, а ожидание названо про "
                        f"{excerpt(there, 48)}. Запись пойдёт по элементу, "
                        f"сверка — по ожиданию, и расхождение всплывёт уже "
                        f"после дела."
                    )

        if self.labels is not None:
            if self.expect_gone:
                raise DirectFailure(
                    f"{self.where}: создание и удаление в одной операции не "
                    f"сходятся: объект либо появляется, либо исчезает."
                )
            if len(self.labels) != len(self.items):
                raise DirectFailure(
                    f"{self.where}: имён создаваемых объектов {len(self.labels)} "
                    f"на {len(self.items)} элементов. Сопоставлять по позиции "
                    f"нельзя."
                )
            carried = [one for one in self.items if self.id_field in one]
            if carried:
                raise DirectFailure(
                    f"{self.where}: в элементе есть поле {self.id_field}, хотя "
                    f"объект создаётся. Идентификатор назначает Директ, и "
                    f"присланный сюда означает правку под видом создания."
                )
            if not self.search_said:
                raise DirectFailure(
                    f"{self.where}: не сказано, чем искать созданное после "
                    f"сорвавшегося вызова. Назовите `search=(\"поле\", "
                    f"{{отбор}})` — или `search=None`, если у сервиса такого "
                    f"поля нет: тогда конвейер честно скажет «проверьте "
                    f"кабинет», и это будет замер, а не умолчание."
                )
        elif self.search is not None:
            raise DirectFailure(
                f"{self.where}: чем искать созданное, названо у операции, "
                f"которая ничего не создаёт. Объекты правки перечитываются по "
                f"идентификатору, и этот отбор не применился бы ни разу."
            )
        if self.search is not None:
            field, criteria = self.search
            if not self._asked(field):
                raise DirectFailure(
                    f"{self.where}: искать созданное велено по полю "
                    f"«{excerpt(field, 48)}», а чтение его не "
                    f"запрашивает. Прочитанные объекты придут без него, и своё "
                    f"среди них не опознается."
                )
            unfit = []
            for number, item in enumerate(self.items):
                value = _dig(item, field)
                fits = (isinstance(value, int) and not isinstance(value, bool)
                        or isinstance(value, str) and value.strip())
                if not fits:
                    unfit.append(f"{number + 1} ({excerpt(value, 32)})")
            if unfit:
                raise DirectFailure(
                    f"{self.where}: искать созданное велено по полю "
                    f"«{excerpt(field, 48)}», а элемент "
                    f"{', '.join(unfit[:5])} не несёт по нему значения, "
                    f"которым объект узнают. Своё опознаётся по отправленному "
                    f"значению — строке или идентификатору; пустое подошло бы "
                    f"к любому чужому объекту, а список и структуру Директ "
                    f"отдаёт не в том виде, в каком принял."
                )
            if criteria is WHOLE_ACCOUNT and self.read_required:
                raise DirectFailure(
                    f"{self.where}: искать созданное велено по всему кабинету, "
                    f"то есть без `SelectionCriteria`, а без "
                    f"«{', '.join(self.read_required)}» чтение этого сервиса не "
                    f"работает вовсе. Одно отменяет другое."
                )
        # Перебором, а не `any(_written(...) for ...)`: там истинным был бы сам
        # генератор, и проверка не срабатывала бы никогда.
        writes = any(True for item in self.items
                     for _ in _written(item, self.id_field))
        if self.expect is None and not self.expect_gone and not writes:
            raise DirectFailure(
                f"{self.where}: элементы несут только идентификатор, то есть "
                f"меняется состояние. Назовите ожидаемое состояние в `expect` — "
                f"иначе перечитывание подтвердит лишь то, что объект существует, "
                f"а перешёл он или нет, останется непроверенным."
            )
        for change in self.changes:
            if change.field and not _beneath(change.field, self.derived) \
                    and not _under(change.field, self.unread) \
                    and not self._asked(self._as_read(change.field)):
                raise DirectFailure(
                    f"{self.where}: изменение правит поле «{change.field}», а "
                    f"чтение его не запрашивает. Ни снимок, ни перечитывание "
                    f"его не увидят: сверять будет нечем, а чужая правка в нём "
                    f"перепишется молча."
                )
        for number, item in enumerate(self.items):
            for sent, payload in ((True, item), (False, self.wanted(number))):
                said = "в запрос уходит" if sent else "после записи ожидается"
                for path, _ in _written(payload, self.id_field):
                    if sent and _beneath(path, self.derived):
                        continue
                    if _under(path, self.unread if sent else self.unread_read):
                        continue
                    if "." in path and not self._asked(path):
                        raise DirectFailure(
                            f"{self.where}: {said} «{path}», а чтение "
                            f"этого пути не запрашивает. Сверять его будет не с "
                            f"чем: и снимок, и перечитывание дадут пусто."
                        )
        self._check_derived()
        nulled = {path for item in self.items
                  for path, value in _fields(item) if value is None}
        idle = [path for path in self.clears if path not in nulled]
        if idle:
            raise DirectFailure(
                f"{self.where}: очистка объявлена для путей "
                f"{', '.join(idle[:5])}, а `null` по ним не передаётся ни в "
                f"одном элементе. Проверять там нечего, а послабление снимает "
                f"правила справочника — и снимает их не с того пути."
            )
        filled = {path for item in self.items
                  for path, _ in _written(item, self.id_field)}
        astray = [path for path in self.unread
                  if not any(_under(one, (path,)) for one in filled)]
        if astray:
            raise DirectFailure(
                f"{self.where}: «пишется и не читается» объявлено для путей "
                f"{', '.join(astray[:5])}, а в запрос по ним не уходит ничего "
                f"ни в одном элементе. Проверять там нечего, а послабление "
                f"снимает сверку — и снимает её не с того пути."
            )
        self._bind()
        planned = {change.object_id for change in self.changes}
        named = self.labels if self.labels is not None else self.ids()
        promised = [change.object_id for change in self.changes
                    if change.object_id not in set(named)]
        if promised:
            raise DirectFailure(
                f"{self.where}: план обещает правку объектов "
                f"{', '.join(str(one) for one in promised[:10])}, которых нет "
                f"среди элементов. Человек их подтвердит, а записаны они не "
                f"будут — и задача отчитается успехом."
            )
        unplanned = [one for one in named if one not in planned]
        if unplanned:
            raise DirectFailure(
                f"{self.where}: объекты "
                f"{', '.join(str(one) for one in unplanned[:10])} записываются, "
                f"но в плане изменений их нет. Человек их не увидит, а "
                f"план должен соответствовать запросу."
            )
        if self.batch_limit is not None and (
                isinstance(self.batch_limit, bool)
                or not isinstance(self.batch_limit, int)
                or self.batch_limit < 1):
            raise DirectFailure(
                f"{self.where}: размер пакета {excerpt(self.batch_limit, 32)} — "
                f"не целое больше нуля. Отрицательный предел не делит задачу на "
                f"пакеты, а обнуляет их: задача отчиталась бы успехом, ничего "
                f"не записав и ничего не сверив."
            )
        missing = [name for name in self.read_required
                   if name not in (self.read.get("SelectionCriteria") or {})]
        if missing:
            raise DirectFailure(
                f"{self.where}: обязательным отбором названо "
                f"«{', '.join(missing)}», но в `SelectionCriteria` чтения "
                f"этого имени нет. Сохранять при удалении будет нечего, а "
                f"перечитывание без него не работает вовсе."
            )
        if self.expect_gone and self.expect is not None:
            raise DirectFailure(
                f"{self.where}: у удаления ожидание — отсутствие объекта, и "
                f"назвать вместо него состояние нельзя. Ожидание, сошедшееся с "
                f"оставшимся объектом, объявило бы несостоявшееся удаление "
                f"успехом."
            )
        if bool(self.params_key) == bool(self.selection):
            raise DirectFailure(
                f"{self.where}: не сказано, как собрать параметры запроса — или "
                f"сказано дважды. Ровно одно из двух: `params_key` — имя массива "
                f"объектов, `selection` — имя отбора по идентификаторам "
                f"(`Ads.suspend` принимает не массив объектов, а отбор). "
                f"Произвольной функции здесь нет намеренно: запрос, собранный "
                f"чужим кодом, планом не проверяется, и подтверждённый объект "
                f"разошёлся бы с записанным."
            )
        if self.selection:
            extra = [path for item in self.items
                     for path, _ in _written(item, self.id_field)]
            if extra:
                raise DirectFailure(
                    f"{self.where}: отбор по идентификаторам полей не передаёт, "
                    f"а элемент несёт «{extra[0]}». Это поле молча не уехало бы "
                    f"в Директ, и человек подтвердил бы правку, которой не будет."
                )
        for change in self.changes:
            change.check()
            if change.kind and change.kind != self.service:
                raise DirectFailure(
                    f"{self.where}: изменение «{excerpt(change.what, 48)}» "
                    f"объявлено видом «{excerpt(change.kind, 32)}», а операция "
                    f"пишет «{self.service}». Похоже, изменение приписано не той "
                    f"операции: вид объекта задаёт она, и расходиться им нельзя."
                )
            change.kind = self.service

    def _check_derived(self) -> None:
        """Расхождение форм объявлено — значит требований больше, а не меньше."""
        if not self.derived:
            return
        if self.expect is None:
            raise DirectFailure(
                f"{self.where}: объявлено расхождение форм "
                f"({', '.join(self.derived)}), но ожидаемое состояние не "
                f"названо. Сверять после записи было бы нечем: форма запроса с "
                f"формой чтения не сходится по построению, и сверка объявила бы "
                f"расхождением каждый элемент правильно записанного комплекта."
            )
        for path, read_path in zip(self.derived, self.derived_read):
            for number, item in enumerate(self.items):
                wanted = self.wanted(number)
                here, mine = _present(item, path)
                there, theirs = _present(wanted, read_path)
                if not here or not there:
                    where = ("запросе" if not here else "ожидаемом состоянии")
                    absent = path if not here else read_path
                    raise DirectFailure(
                        f"{self.where}: объект {excerpt(self.name(number), 48)}: "
                        f"расхождение форм объявлено для «{absent}», а в {where} "
                        f"этого пути нет. Сравнивать нечего с чем: послабление "
                        f"снято бы было, а замена ему не работает."
                    )
                lost, added = _unpaired(
                    _leaves(_pruned(mine, path, self.unread)),
                    _leaves(_pruned(theirs, read_path, self.unread_read)))
                if lost or added:
                    raise DirectFailure(
                        f"{self.where}: объект {excerpt(self.name(number), 48)}, "
                        f"путь «{path}»: запрос и ожидаемое состояние описывают "
                        f"разное содержимое. "
                        + (f"В ожидании нет {_missing_said(lost)}. " if lost else "")
                        + (f"В ожидании лишнее: {_missing_said(added)}. " if added else "")
                        + "Формы под этим путём разные, и сверять их дословно "
                        "нельзя — но набор значений у двух представлений "
                        "одного комплекта общий, и разойтись ему значит, что "
                        "сверка после записи спросит не про то, что записано."
                    )

    def _bind(self) -> None:
        """Сверить отправляемое с планом: поле в поле, значение в значение."""
        for number, item in enumerate(self.items):
            name = self.name(number)
            mine = [change for change in self.changes if change.object_id == name]
            if not self.expect_gone:
                nameless = [change for change in mine if not change.field]
                if nameless:
                    raise DirectFailure(
                        f"{self.where}: изменение «{excerpt(nameless[0].what, 48)}» "
                        f"не называет поля. Человеку оно показано, а в запрос "
                        f"не уйдёт: привязать его не к чему."
                    )
            fields = {}
            for change in mine:
                if not change.field:
                    continue
                if change.field in fields:
                    raise DirectFailure(
                        f"{self.where}: объект {excerpt(name, 48)}, поле "
                        f"«{change.field}» обещано дважды — "
                        f"{excerpt(fields[change.field].after, 48)} и "
                        f"{excerpt(change.after, 48)}. Сбудется одно, а "
                        f"подтверждены оба."
                    )
                fields[change.field] = change
            for where, payload in (("записывает", item),
                                   ("ожидает после записи", self.wanted(number))):
                for path, _ in _written(payload, self.id_field):
                    if payload is not item:
                        path = self._as_written(path)
                    if _owner(path, fields) is None:
                        raise DirectFailure(
                            f"{self.where}: объект {excerpt(name, 48)} {where} "
                            f"поле «{path}», которого нет в плане. Человек "
                            f"подтверждает то, что видит в предпросмотре, а "
                            f"уходит в запрос и в сверку это."
                        )
            for path, change in fields.items():
                seen = False
                for where, payload in (("в запрос уходит", item),
                                       ("после записи ожидается", self.wanted(number))):
                    if payload is not item and _under(path, self.derived):
                        found, _ = _present(self.wanted(number),
                                            self._as_read(path))
                        seen = seen or found
                        continue
                    found, value = _present(payload, path)
                    if not found:
                        continue
                    seen = True
                    mine, theirs = ((value, change.after) if payload is item
                                    else (_pruned(value, path, self.unread_read),
                                          _pruned(change.after, path, self.unread)))
                    if mine != theirs:
                        raise DirectFailure(
                            f"{self.where}: объект {excerpt(name, 48)}, поле "
                            f"«{path}»: план обещает {excerpt(change.after, 64)}, "
                            f"а {where} {excerpt(value, 64)}. Подтверждают план, "
                            f"записывают запрос, и расходиться им нельзя."
                        )
                if not seen:
                    raise DirectFailure(
                        f"{self.where}: объект {excerpt(name, 48)}: план обещает "
                        f"правку поля «{path}», но в запросе этого поля нет "
                        f"вовсе. Пропущенное поле Директ не трогает, и правка "
                        f"не состоится — а человеку она показана."
                    )

    def _as_written(self, path: str) -> str:
        """Путь чтения — под именем запроса. Не менялось имя — путь как был."""
        return _translate(path, self.derived_read, self.derived)

    def _as_read(self, path: str) -> str:
        """Путь запроса — под именем чтения."""
        return _translate(path, self.derived, self.derived_read)

    def clearing(self, path: str) -> bool:
        """Означает ли `null` на этом пути очистку поля.

        Путь называется без индексов — таким его и отдаёт `_fields`: `nillable`
        принадлежит полю, а не третьему элементу списка."""
        return path in self.clears

    @property
    def unread_read(self) -> tuple:
        """Непрочитываемые пути под именами чтения.

        Сверяется прочитанное, и под объявленным расхождением форм имена у него
        свои. Перевод делается один раз и здесь, а не в местах, где правило
        спрашивают: два перевода одного пути разошлись бы, и послабление
        сработало бы там, где его не объявляли."""
        return tuple(self._as_read(one) for one in self.unread)

    def unchecked(self, number: int, after=None) -> list:
        """Непрочитываемые поля, которые этот элемент правда писал вслепую."""
        filled = Counter(path for path, _
                         in _written(self.items[number], self.id_field))
        seen = Counter(self._as_written(path) for path, _ in _fields(after or {}))
        return [one for one in self.unread
                if any(_under(path, (one,)) and count > seen.get(path, 0)
                       for path, count in filled.items())]

    def unread_returned(self, number: int, after=None) -> list:
        """Объявленные пути, вернувшиеся из кабинета **не тем**, что отправляли."""
        sent, read = {}, {}
        for path, value in _fields(self.items[number]):
            if _under(path, self.unread):
                sent.setdefault(path, []).append(value)
        for path, value in _fields(after or {}):
            here = self._as_written(path)
            if _under(here, self.unread):
                read.setdefault(here, []).append(value)
        found = []
        for path, values in sorted(read.items()):
            strange, _ = _unpaired(values, sent.get(path, []))
            if strange:
                found.append((path, sent.get(path, []), strange))
        return found

    def blind_only(self, number: int) -> bool:
        """Правит ли элемент **только** непрочитываемое.

        У такой правки сверка сходится с чем угодно: сравнивать нечего, и
        `ok` у неё не результат, а отсутствие вопроса. Приписывать по нему
        запись вызову нельзя — см. `Writer._aftermath`."""
        written = [path for path, _ in _written(self.items[number], self.id_field)]
        return bool(written) and all(_under(path, self.unread)
                                     for path in written)

    def _asked(self, field: str) -> bool:
        """Запрашивает ли чтение это поле."""
        parts = field.split(".")
        common = self.read.get("FieldNames")
        if isinstance(common, list) and parts[0] in common:
            return True
        whole = self.read.get("".join(parts) + "FieldNames")
        if isinstance(whole, list) and whole:
            return True
        if len(parts) > 1:
            for depth in range(1, len(parts)):
                typed = self.read.get("".join(parts[:depth]) + "FieldNames")
                if isinstance(typed, list) and parts[depth] in typed:
                    return True
            return False
        return any(parts[0] in value for key, value in self.read.items()
                   if key.endswith("FieldNames") and isinstance(value, list))

    @property
    def where(self) -> str:
        return f"{self.service}.{self.method}"

    @property
    def creating(self) -> bool:
        """Объекты создаются: идентификаторы назначит Директ."""
        return self.labels is not None

    def name(self, number: int):
        """Как объект зовётся в плане и в журнале до записи."""
        if self.creating:
            return self.labels[number]
        return self.items[number].get(self.id_field)

    def envelope(self, packet: list) -> dict:
        """Параметры запроса для этого пакета.

        Собираются из тех же элементов, что проверены планом. Произвольной
        функции здесь нет намеренно: запрос, собранный чужим кодом, привязкой не
        проверяется — элемент и предпросмотр говорили бы про один объект, а
        отбор уносил бы другой."""
        if self.selection:
            return {"SelectionCriteria": {
                self.selection: [item[self.id_field] for item in packet]}}
        return {self.params_key: packet}

    def ids(self, items=None) -> list:
        found = []
        for item in (self.items if items is None else items):
            if self.id_field not in item:
                raise DirectFailure(
                    f"{self.where}: у элемента нет поля {self.id_field}. "
                    f"Перечитать и сверить такой объект нечем, а запись без "
                    f"сверки конвейером не является."
                )
            found.append(item[self.id_field])
        return found

    def wanted(self, number: int) -> dict:
        return self.expect[number] if self.expect is not None else self.items[number]

    def __repr__(self) -> str:
        return f"<Operation {self.where}: элементов {len(self.items)}>"


class Task:
    """Связанные операции и полный перечень изменений одной задачи."""

    def __init__(self, title: str, operations):
        self.title = title
        self.operations = list(operations)
        if not self.operations:
            raise DirectFailure(f"Задача «{excerpt(title, 64)}» пуста.")
        for operation in self.operations:
            operation.check()
        seen = {}
        for operation in self.operations:
            for number in range(len(operation.items)):
                key = (operation.service, operation.name(number))
                other = seen.get(key)
                if other is not None and (operation.creating or other[1]):
                    raise DirectFailure(
                        f"Задача «{excerpt(title, 64)}»: объект "
                        f"{excerpt(key[1], 48)} в сервисе {operation.service} "
                        f"встречается и в {other[0]}, и в {operation.where}, "
                        f"причём хотя бы раз — как создаваемый. Считаются "
                        f"они одним объектом, а это разные объекты."
                    )
                seen[key] = (operation.where, operation.creating)
        promised, gone = {}, {}
        for operation in self.operations:
            for change in operation.changes:
                owner = (operation.service, change.object_id)
                if operation.expect_gone or owner in gone:
                    said = gone.get(owner) or promised.get(owner)
                    if said:
                        where = gone.get(owner) or sorted(said.values())[0]
                        raise DirectFailure(
                            f"Задача «{excerpt(title, 64)}»: объект "
                            f"{excerpt(change.object_id, 48)} и удаляется, и "
                            f"правится ({where} и {operation.where}). "
                            f"Подтверждённая правка удаления не переживёт, а "
                            f"сверка её застанет — до него."
                        )
                    if operation.expect_gone:
                        gone[owner] = operation.where
                        continue
                if not change.field:
                    continue
                for field, where in promised.get(owner, {}).items():
                    if _overlap(field, change.field):
                        raise DirectFailure(
                            f"Задача «{excerpt(title, 64)}»: поле "
                            f"«{change.field}» объекта "
                            f"{excerpt(change.object_id, 48)} пересекается с "
                            f"«{field}» из {where}. Останется значение "
                            f"последней записи, а подтверждены оба обещания."
                        )
                promised.setdefault(owner, {})[change.field] = operation.where

    @property
    def changes(self) -> list:
        return [change for operation in self.operations for change in operation.changes]

    def plan(self) -> policies.Plan:
        return policies.Plan(self.changes, title=self.title)

    def __repr__(self) -> str:
        return f"<Task {self.title}: операций {len(self.operations)}>"


class Preview:
    """План для вывода: копия задачи и полные строки «было → станет»."""

    __slots__ = ("task", "lines")

    def __init__(self, task: Task, lines):
        self.task = task
        self.lines = list(lines)

    def text(self) -> str:
        return "\n".join(self.lines)

    def __repr__(self) -> str:
        return f"<Preview {self.task.title}: строк {len(self.lines)}>"


def showing(*, seen=None, quiet=False, notes=()):
    """Вывести полный план; quiet оставляет stdout свободным для JSON."""
    def display(preview):
        if seen is not None:
            seen.append(True)
        stream = sys.stderr if quiet else sys.stdout
        for line in [*preview.lines, *notes]:
            print(redact(str(line)), file=stream)
        stream.flush()
    return display


class Report:
    """Результат задачи. preview содержит полный план «было → станет».
    accepted — принятые API элементы, written — перечитанные и проверенные.
    failed — подтверждённые отказы; unknown — исход не установлен.
    despite — запись обнаружена после ошибки запроса. differences — расхождения
    с планом, normalizations — допустимые преобразования, unchecked — поля,
    которые API не возвращает. unlogged сообщает отдельную ошибку журнала.
    applied обозначает попытку записи; окончательный результат дают списки."""

    KINDS = {
        SHOWN: "preview",
        PROBLEM: "problems",
        ACCEPTED: "accepted",
        WRITTEN: "written",
        ITEM_ERROR: "failed",
        PACKET_REFUSED: "failed",
        REFUSED: None,
        APPLIED_ANYWAY: "despite",
        UNVERIFIED: "unknown",
        UNEXPLAINED: "differences",
        NORMALIZED: "normalizations",
        UNCHECKED: "unchecked",
        UNLOGGED: "unlogged",
    }

    FIELDS = ("ok", "applied", "summary", "preview", "written", "accepted", "problems",
              "failed", "unlogged", "despite", "unknown", "differences",
              "normalizations", "unchecked", "stopped", "journal")

    LISTS = frozenset({"preview", "written", "accepted", "problems", "failed", "unlogged",
                       "despite", "unknown", "differences", "normalizations",
                       "unchecked"})

    def __init__(self, task: Task):
        self.task = task
        self.preview = []
        self.problems = []
        self.accepted = []
        self.written = []
        self.failed = []
        self.unlogged = []
        self.despite = []
        self.unknown = []
        self.differences = []
        self.normalizations = []
        self.unchecked = []
        self.applied = False
        self.stopped = ""
        self.journal = None

    def record(self, kind: str, said) -> None:
        """Единственная дверь к спискам отчёта: список выбирает таблица."""
        if kind not in self.KINDS:
            self.differences.append(
                f"утверждение неизвестного рода «{kind}»: {said}. Место в "
                f"отчёте ему не назначено — смотрите `Report.KINDS`"
            )
            return
        where = self.KINDS[kind]
        if where is not None:
            getattr(self, where).append(said)

    @property
    def ok(self) -> bool:
        return not (self.problems or self.failed or self.differences
                    or self.despite or self.unknown or self.unlogged)

    def summary(self) -> str:
        parts = [f"изменений {len(self.task.changes)}"]
        if self.applied:
            if len(self.accepted) != len(self.written):
                parts.append(f"Директ принял {len(self.accepted)}")
            parts.append(f"записано {len(self.written)}")
        if self.failed:
            parts.append(f"отказано {len(self.failed)}")
        if self.unlogged:
            parts.append(f"в журнал не попало {len(self.unlogged)}")
        if self.despite:
            parts.append(f"записалось вопреки отказу {len(self.despite)}")
        if self.unknown:
            parts.append(f"неизвестно {len(self.unknown)}")
        if self.differences:
            parts.append(f"расхождений {len(self.differences)}")
        if self.unchecked:
            parts.append(f"без перечитывания полей {len(self.unchecked)}")
        if self.stopped:
            parts.append(self.stopped)
        elif not self.applied:
            parts.append("запись не выполнялась")
        return ", ".join(parts)

    def lines(self) -> list:
        """Сводка для stdout. Печатает её вызывающий скрипт через `outline`."""
        said = list(self.preview)
        for problem in self.problems:
            said.append(f"проверка: {problem}")
        for entry in self.failed:
            said.append(f"отказ: {entry}")
        for entry in self.unlogged:
            said.append(f"без журнала: {entry}")
        for entry in self.despite:
            said.append(f"вопреки отказу: {entry}")
        for entry in self.unknown:
            said.append(f"неизвестно: {entry}")
        if self.despite:
            said.append("повторять команду нельзя: вызов отказал, а кабинет "
                        "изменился — повтор создающей операции заведёт второй "
                        "такой же объект")
        if self.unknown:
            said.append("повторять команду вслепую нельзя: что легло в "
                        "кабинет, из ответа Директа не видно — у каждого "
                        "объекта выше сказано, чем это выяснить")
        if self.applied and len(self.accepted) != len(self.written):
            said.append(
                f"«записано» здесь значит перечитано и сошлось, а не «Директ "
                f"ответил успехом»: принял он {len(self.accepted)}, "
                f"подтвердилось {len(self.written)} — про остальное сказано "
                f"выше"
            )
        for found in self.differences:
            said.append(f"расхождение: {found}")
        for found in self.unchecked:
            said.append(f"не проверено: {found}")
        for found in self.normalizations:
            said.append(f"нормализация: {found}")
        said.append(self.summary())
        return said

    def machine(self) -> dict:
        """Отчёт для программы, а не для человека: то, что печатает `--json`."""
        return {
            "ok": self.ok,
            "applied": self.applied,
            "summary": self.summary(),
            "preview": list(self.preview),
            "written": [str(one) for one in self.written],
            "accepted": [str(one) for one in self.accepted],
            "problems": list(self.problems),
            "failed": list(self.failed),
            "unlogged": list(self.unlogged),
            "despite": list(self.despite),
            "unknown": list(self.unknown),
            "differences": list(self.differences),
            "normalizations": list(self.normalizations),
            "unchecked": list(self.unchecked),
            "stopped": self.stopped,
            "journal": str(self.journal),
        }

    def __repr__(self) -> str:
        return f"<Report {self.task.title}: {self.summary()}>"


def unrun(summary: str, *, ok: bool = False, problems=()) -> dict:
    """Машиночитаемый отчёт команды, которая до конвейера не дошла."""
    said = {"ok": bool(ok), "applied": False, "summary": summary,
            "problems": [str(one) for one in problems],
            "stopped": summary, "journal": ""}
    return {name: said.get(name, [] if name in Report.LISTS else "")
            for name in Report.FIELDS}


def merged(reports) -> dict:
    """Машиночитаемый отчёт нескольких прогонов одной команды."""
    parts = [report.machine() for report in reports]
    if not parts:
        raise DirectFailure("Отчитываться не о чем: прогонов не было.")
    joined = {}
    for name, value in parts[0].items():
        found = [part[name] for part in parts]
        if isinstance(value, list):
            joined[name] = [one for part in found for one in part]
        elif isinstance(value, bool):
            joined[name] = all(found) if name == "ok" else any(found)
        else:
            joined[name] = "; ".join(str(one) for one in found if one)
    joined["journal"] = parts[0]["journal"]
    return joined


class Writer:
    """Запись в один кабинет. apply=True выполняет подготовленную задачу.
    show(Preview) выводит план без чтения stdin; при отсутствии show план идёт
    в stderr. Лимиты, свежие снимки и журнал сохраняют проверяемость результата."""

    def __init__(self, client, account: str = "", *, accounts=None,
                 cache=None, journal=None, limits=None, apply: bool = False,
                 show=None, warn=None):
        self.client = client
        self.accounts = accounts
        self.account = account or getattr(getattr(client, "settings", None), "account", "")
        if not self.account:
            raise DirectFailure(
                "Кабинет не назван. Журнал записей раскладывается по логину "
                "кабинета, и запись в кабинет «по умолчанию» означала бы журнал "
                "неизвестно чей."
            )
        # Кэш нужен ровно для отзыва после записи. Читает конвейер клиентом —
        # то есть мимо кэша всегда, а не по настройке.
        self.cache = cache if cache is not None else cache_module.Cache(self.account)
        self.journal = journal if journal is not None else AuditLog(self.account)
        self.apply = apply
        self.show = show
        self.warn = warn or warn_to_stderr
        # Лимиты собираются последними: справочник `Constants` читается
        # клиентом и жалуется тем же голосом, что и остальной движок.
        self.limits = limits if limits is not None else self._current_limits()

    def _current_limits(self) -> "Limits":
        """Дополнить limits.json свежим справочником Constants, общим для кабинетов.
        При недоступности справочника использовать файл с явным сообщением."""
        limits = Limits.load()
        store = cache_module.Cache(root=self.cache.root, warn=self.warn)
        try:
            return constants_module.Constants.load(
                self.client, cache=store).apply(limits)
        except DirectFailure as failure:
            self.warn(
                f"Справочник {constants_module.DICTIONARY} не прочитан: "
                f"{failure}. Проверка использует сохранённые ограничения из "
                f"{short(LIMITS_PATH)} — его числа могли отстать от Директа."
            )
            return limits


    # -- баллы --------------------------------------------------------------

    def use_operator_units(self, need: int):
        """Чьими баллами платить за вызов в этот кабинет ценой в `need` баллов."""
        if self.accounts is None:
            return None
        return self.accounts.use_operator_units(self.account, need=need)

    # -- стадии -------------------------------------------------------------

    def snapshot(self, operation: Operation) -> dict:
        """Полное чтение изменяемых объектов — мимо кэша, всегда.

        Устаревший снимок означает запись поверх чужих изменений: сверка
        сравнит вчерашние данные с сегодняшними и сойдётся."""
        return self.fetch(operation, operation.ids())

    def fetch(self, operation: Operation, ids: list, *, attempted=None) -> dict:
        params = dict(operation.read)
        criteria = dict(params.get("SelectionCriteria") or {})
        if operation.expect_gone:
            criteria = {name: value for name, value in criteria.items()
                        if name in operation.read_required}
        limit = (self.limits.selection(operation.service, operation.read_key)
                 or len(ids) or 1)
        found = {}
        for start in range(0, len(ids), limit):
            chunk = ids[start:start + limit]
            request = dict(params)
            request["SelectionCriteria"] = dict(criteria,
                                                **{operation.read_key: list(chunk)})
            need = self.limits.units_cost(operation.service, "get", len(chunk))

            def paying(need=need):
                answer = self.use_operator_units(need=need)
                if attempted is not None:
                    attempted()
                return answer

            page = self.client.get_all(
                operation.service, request, account=self.account,
                use_operator_units=paying,
            )
            for item in page:
                key = required(item, operation.id_field, _identifier_kind(chunk),
                               f"{operation.service}.get")
                found[key] = item
        return found

    def validate(self, operation: Operation) -> list:
        """Проверка по `limits.json` до отправки запроса.

        Безусловна. Отказ Директа стоит баллов и
        приходит после того, как часть пакета уже применена, — а один элемент
        без обязательного поля роняет весь батч целиком
        (`ERRORS_AND_LIMITS.md`, раздел 8.1)."""
        said = []
        seen = {}
        declared = set(operation.texts) | set(operation.collections)
        present = {path for item in operation.items for path, _ in _fields(item)}
        missing = declared - present
        for path in sorted(missing):
            said.append(
                f"{operation.where}: правило объявлено для пути «{path}», "
                f"которого нет ни в одном элементе — проверять по нему нечего"
            )
        used = {}
        for number, item in enumerate(operation.items):
            identifier = operation.name(number)
            if identifier in seen:
                said.append(
                    f"{operation.where}: объект {identifier} встречается дважды "
                    f"(элементы {seen[identifier]} и {number}); Директ примет "
                    f"такой запрос с предупреждением, а какое из двух значений "
                    f"останется — не определено"
                )
            seen[identifier] = number
            for path, value in _fields(item):
                if value is None and operation.clearing(path):
                    continue
                whole = isinstance(value, (list, dict))
                key = operation.texts.get(path)
                if key is not None and not whole:
                    used[("текстовое", path)] = True
                    said += [f"{operation.where}, объект {identifier}, {path}: {problem}"
                             for problem in self.limits.text_problems(key, value)]
                key = operation.collections.get(path)
                if key is not None and whole:
                    used[("на состав", path)] = True
                    said += [f"{operation.where}, объект {identifier}, {path}: {problem}"
                             for problem in self.limits.collection_problems(key, value)]
        for kind, rules in (("текстовое", operation.texts),
                            ("на состав", operation.collections)):
            for path in sorted(set(rules) - missing):
                if used.get((kind, path)):
                    continue
                said.append(
                    f"{operation.where}: правило {kind} объявлено для пути "
                    f"«{path}», но значения подходящей формы там нет ни у "
                    f"одного элемента — текстовое проверяет элемент, на состав "
                    f"проверяет массив. Правило, не подошедшее ни разу, не "
                    f"проверяет ничего"
                )
        return said

    def preview(self, task: Task, seen: dict) -> list:
        """Человекочитаемый разбор «было → станет»."""
        plan = task.plan()
        lines = [f"Задача: {task.title}",
                 f"Объектов: {len(plan.touched())}"]
        lines.append(f"Изменения ({len(task.changes)}):")
        for change in task.changes:
            lines.append("  " + change.describe(_title(seen.get(change))))
        return lines

    # -- прогон -------------------------------------------------------------

    def run(self, task: Task) -> Report:
        """Прочитать, проверить и показать правки; записать только при apply=True."""
        try:
            task = Task(task.title, copy.deepcopy(task.operations))
        except DirectFailure as exc:
            report = Report(task)
            report.record(PROBLEM, str(exc))
            report.stopped = "задача не прошла проверку"
            return report

        report = Report(task)
        report.journal = self.journal.path
        snapshots, seen = {}, {}
        for operation in task.operations:
            known = {} if operation.creating else self.snapshot(operation)
            snapshots[operation] = known
            for change in operation.changes:
                seen[change] = known.get(change.object_id)
            missing = [] if operation.creating else [
                one for one in operation.ids() if one not in known
            ]
            if missing:
                report.record(PROBLEM,
                    f"{operation.where}: не прочитаны объекты "
                    f"{', '.join(str(one) for one in missing)}. "
                    f"Проверьте идентификаторы и доступ к кабинету.")
            for problem in self.validate(operation):
                report.record(PROBLEM, problem)
            if operation.guard is not None:
                for problem in operation.guard(known):
                    report.record(PROBLEM, problem)

        self._fill(task, seen)
        for line in self.preview(task, seen):
            report.record(SHOWN, line)
        preview = Preview(copy.deepcopy(task), report.preview)
        if self.show is not None:
            self.show(preview)
        else:
            for line in preview.lines:
                self.warn(line)
        # Вывести план до сетевого запроса, в том числе при перенаправлении stdout.
        sys.stdout.flush()
        sys.stderr.flush()

        if report.problems:
            report.stopped = "остановлено проверкой данных"
            return report
        if not self.apply:
            report.stopped = "проверка без записи; запись включает --apply"
            return report

        # Если кабинет изменился после чтения, нужен новый план по свежим данным.
        moved = self._restless(task, snapshots)
        if moved:
            for problem in moved:
                report.record(PROBLEM, problem)
            report.stopped = "кабинет изменился после чтения; повторите подготовку правок"
            return report
        recorded = {"shown": len(report.preview), "changes": len(task.changes)}
        try:
            self._remember(task, recorded, snapshots)
        except DirectFailure as exc:
            report.record(PROBLEM, str(exc))
            report.stopped = "не удалось сохранить исходное состояние в журнал"
            return report

        report.applied = True
        try:
            self._write(task, recorded, report, snapshots)
        finally:
            # Даже при сбое ответа запись могла примениться: старый кэш недействителен.
            try:
                self.cache.forget()
            except DirectFailure as exc:
                self.warn(f"Не удалось очистить кэш кабинета {self.account}: {exc}. "
                          f"Для следующего чтения используйте --no-cache.")
        return report

    def _restless(self, task: Task, snapshots: dict) -> list:
        """Перечитать объекты перед записью и обнаружить параллельные изменения.
        Проверяются все запрошенные поля и дополнительные условия операции."""
        said = []
        for operation in task.operations:
            if operation.creating:
                if operation.guard is not None:
                    said += list(operation.guard({}))
                continue
            known = snapshots.get(operation) or {}
            fresh = self.fetch(operation, operation.ids())
            if operation.guard is not None:
                said += list(operation.guard(fresh))
            for identifier in operation.ids():
                before, now = known.get(identifier), fresh.get(identifier)
                if _same(before, now, operation.rules):
                    continue
                said.append(f"{operation.where}, объект "
                            f"{excerpt(identifier, 48)}: {_changed(before, now)}")
        return said

    def _fill(self, task: Task, seen: dict) -> None:
        """План достраивается по свежему чтению, а не по словам вызвавшего."""
        for operation in task.operations:
            for change in operation.changes:
                read = _frozen(seen.get(change))
                if not change.field:
                    continue
                change.before = _dig(read, operation._as_read(change.field))

    def _remember(self, task: Task, recorded: dict, snapshots: dict) -> None:
        """Прежнее состояние — в журнал до записи."""
        for operation in task.operations:
            known = snapshots.get(operation) or {}
            for number, item in enumerate(operation.items):
                identifier = operation.name(number)
                self._note({
                    "kind": "снимок",
                    "task": task.title,
                    "service": operation.service,
                    "method": operation.method,
                    "object": identifier,
                    "creating": operation.creating or None,
                    "before": None if operation.creating else known.get(identifier),
                    "requested": item,
                    "expected": operation.wanted(number),
                    "decision": recorded,
                }, None)

    def _write(self, task: Task, recorded: dict, report: Report,
               snapshots: dict) -> None:
        for operation in task.operations:
            known = snapshots.get(operation) or {}
            for packet in self._packets(operation):
                if not self._packet(task, operation, packet, recorded,
                                    report, known):
                    if not report.stopped:
                        report.stopped = (
                            "пакет остановлен: дальше идти нельзя, пока не "
                            "объяснено, что случилось с его объектами — их "
                            "исходы названы выше"
                        )
                    return

    def _packets(self, operation: Operation) -> list:
        if operation.method.casefold() in ONE_AT_A_TIME or operation.clears:
            return [[item] for item in enumerate(operation.items)]
        limit = operation.batch_limit or self.limits.batch(operation.service,
                                                           operation.method)
        if limit is None:
            self.warn(
                f"В {short(LIMITS_PATH)} нет размера пакета для "
                f"{operation.where}: элементы уйдут по одному. Если предел "
                f"известен, назовите его в `batch_limit`."
            )
            limit = 1
        items = list(enumerate(operation.items))
        return [items[start:start + limit] for start in range(0, len(items), limit)]

    def _packet(self, task: Task, operation: Operation, packet: list,
                recorded: dict, report: Report, known: dict) -> bool:
        """Один пакет: запись, перечитывание, сверка, журнал. `False` — останов."""
        items = [item for _, item in packet]
        try:
            need = self.limits.units_cost(operation.service, operation.method,
                                          len(items))
        except DirectFailure as exc:
            return self._refused(task, operation, packet, recorded, report, exc,
                                 known, stage=NOT_SENT)

        began = []

        asked = []

        def paying():
            decision = self.use_operator_units(need=need)
            asked.append(True)
            return decision

        try:
            outcome = self.client.batch(
                operation.service, operation.method, operation.envelope(items),
                items=items, results_key=operation.results_key,
                id_field=operation.id_field, account=self.account,
                use_operator_units=paying,
            )
        except DirectFailure as exc:
            return self._refused(task, operation, packet, recorded, report, exc,
                                 known,
                                 stage=CALL_REFUSED if asked else NOT_SENT)

        try:
            created = _created(operation, outcome) if operation.creating else {}
            after = self.fetch(operation, list(created.values())
                               if operation.creating
                               else operation.ids(items),
                               attempted=lambda: began.append(True))
        except DirectFailure as exc:
            return self._refused(task, operation, packet, recorded, report, exc,
                                 known, stage=READ_REFUSED, outcome=outcome,
                                 began=bool(began))

        good = True
        for place, (number, item) in enumerate(packet):
            label = operation.name(number)
            identifier = created.get(place) if operation.creating else label
            entry = outcome.entries[place] if place < len(outcome.entries) else None
            issues = list(entry.issues) if entry is not None else []
            mismatched = [str(issue) for issue in issues if issue.means_mismatch]
            found = None
            unchecked = []
            where = f"{operation.where}, объект {label}"
            if entry is None or not entry.ok:
                report.record(ITEM_ERROR, f"{where}: "
                    + ("; ".join(str(issue) for issue in issues)
                       or "Директ не вернул результат элемента"))
                good = False
                verdict = ITEM_ERROR
            else:
                report.record(ACCEPTED, identifier)
                verdict, said, found = self._verify(operation, number,
                                                    identifier, after)
                if mismatched:
                    if verdict == WRITTEN:
                        verdict, said = UNEXPLAINED, "; ".join(mismatched)
                    else:
                        report.record(UNEXPLAINED,
                                      f"{where}: " + "; ".join(mismatched))
                report.record(verdict,
                              identifier if verdict == WRITTEN
                              else f"{where}: {said}")
                for one in found.normalizations:
                    report.record(NORMALIZED, one.explain())
                unchecked = [
                    _unchecked_said(where, path)
                    for path in operation.unchecked(number, after.get(identifier))
                ]
                for said in unchecked:
                    report.record(UNCHECKED, said)
                if verdict != WRITTEN:
                    good = False
            if not self._note({
                "kind": "запись",
                "task": task.title,
                "service": operation.service,
                "method": operation.method,
                "object": label,
                "created": identifier if operation.creating else None,
                "requested": item,
                "expected": operation.wanted(number),
                "after": after.get(identifier),
                "request_id": outcome.response.request_id,
                "issues": [str(issue) for issue in issues],
                "outcome": verdict,
                "differences": [] if found is None else
                               [one.explain() for one in found.differences],
                "normalizations": [] if found is None else
                                  [one.explain() for one in found.normalizations],
                "unchecked": unchecked,
                "decision": recorded,
            }, report):
                good = False
        return good

    def _refused(self, task: Task, operation: Operation, packet: list,
                 recorded: dict, report: Report, exc, known: dict, *,
                 stage: str, outcome=None, began: bool = False) -> bool:
        """Перечитать кабинет после ошибки запроса. Различать подтверждённый отказ,
        обнаруженную запись, частичное изменение и неизвестный исход.
        Создающий запрос с неизвестным результатом автоматически не повторяется."""
        looked = began
        found, blind = {}, ""
        if stage == READ_REFUSED:
            blind = f"перечитать не удалось — {exc}"
        elif stage == NOT_SENT:
            pass
        elif operation.creating and operation.search is None:
            blind = ("идентификатора Директ не прислал, а чем искать "
                     "созданное, операция не назвала — проверьте кабинет")
        else:
            try:
                def began():
                    nonlocal looked
                    looked = True

                found = (self.lookup(operation, packet, attempted=began)
                         if operation.creating
                         else {key: [one] for key, one in self.fetch(
                             operation,
                             operation.ids([item for _, item in packet]),
                             attempted=began).items()})
            except DirectFailure as second:
                found = {}
                blind = f"перечитать не удалось — {second}"

        reread = ("не требовалось" if stage == NOT_SENT
                  else "не удалось" if looked and blind
                  else "нечем" if blind else "выполнено")
        rejected, warned, noted = {}, {}, {}
        if outcome is not None:
            for place, one in enumerate(outcome.entries):
                noted[place] = [str(issue) for issue in one.issues]
                if not one.ok:
                    rejected[place] = ("; ".join(noted[place])
                                       or "Директ не вернул результат элемента")
                    continue
                same = [str(issue) for issue in one.issues
                        if issue.means_mismatch]
                if same:
                    warned[place] = same
        assigned = (_created(operation, outcome)
                    if outcome is not None and operation.creating else {})

        tally = {}
        for place, (number, item) in enumerate(packet):
            label = operation.name(number)
            where = f"{operation.where}, объект {excerpt(label, 48)}"
            if place in rejected:
                verdict, said = ITEM_ERROR, f"Директ отклонил элемент: {rejected[place]}"
            else:
                verdict, said = self._aftermath(operation, number, found, known,
                                                blind, sent=stage != NOT_SENT)
            if place in assigned:
                said += (f". Директ назначил идентификатор {assigned[place]} — "
                         f"перечитывайте по нему")
            tally[verdict] = tally.get(verdict, 0) + 1
            if outcome is not None and place not in rejected:
                report.record(ACCEPTED, assigned.get(place, label))
            if place in warned:
                report.record(UNEXPLAINED,
                              f"{where}: " + "; ".join(warned[place]))
            report.record(verdict, f"{where}: {said}")
            same = found.get(label) or []
            unchecked = ([] if stage == NOT_SENT or place in rejected
                         else operation.unchecked(number,
                                                  same[0] if same else None))
            for path in unchecked:
                report.record(UNCHECKED, _unchecked_said(where, path))
            self._note({
                "kind": "запись", "task": task.title,
                "service": operation.service, "method": operation.method,
                "object": label,
                "created": assigned.get(place),
                "requested": item,
                "expected": operation.wanted(number),
                "before": None if operation.creating else known.get(label),
                "after": found.get(label),
                "stage": stage,
                "reread": reread,
                "issues": noted.get(place, []),
                "why": said,
                "unchecked": [_unchecked_said(where, path) for path in unchecked],
                "outcome": verdict, "error": str(exc),
                "decision": recorded,
            }, report)

        head = stage
        if stage == READ_REFUSED and rejected:
            head = ("Директ отклонил все элементы, а перечитать не удалось"
                    if len(rejected) == len(packet) else
                    "Директ принял запись частично, а перечитать не удалось")
        said = (f"{operation.where}: {head} — {exc}. "
                + ("Перечитано: " if looked else "Итог: ")
                + ", ".join(f"{name} {count}"
                            for name, count in sorted(tally.items())))
        if set(tally) <= {REFUSED}:
            report.record(PACKET_REFUSED, said)
        else:
            report.stopped = said
        return False

    def lookup(self, operation: Operation, packet: list, *,
               attempted=None) -> dict:
        """Созданные объекты: имя из плана — что нашлось под его значением."""
        field, criteria = operation.search
        params = dict(operation.read)
        if criteria is WHOLE_ACCOUNT:
            params.pop("SelectionCriteria", None)
        else:
            params["SelectionCriteria"] = dict(
                params.get("SelectionCriteria") or {}, **criteria)
        need = self.limits.units_cost(operation.service, "get")

        def paying():
            answer = self.use_operator_units(need=need)
            if attempted is not None:
                attempted()
            return answer

        page = self.client.get_all(
            operation.service, params, account=self.account,
            use_operator_units=paying,
        )
        wanted = {}
        for number, item in packet:
            wanted.setdefault(_dig(item, field), []).append(
                operation.name(number))
        found = {}
        for item in page:
            for label in wanted.get(_dig(item, field), ()):
                found.setdefault(label, []).append(item)
        return found

    def _aftermath(self, operation: Operation, number: int, found: dict,
                   known: dict, blind: str, *, sent: bool) -> tuple:
        """Что случилось с одним объектом пакета: исход и что сказать человеку."""
        label = operation.name(number)
        if not sent:
            # Запроса не было — единственный случай, когда «не записалось»
            # известно без чтения.
            return REFUSED, ("запрос не отправлялся — этим вызовом кабинет "
                             "не тронут")
        if blind:
            return UNVERIFIED, (f"{blind}. Применилось или нет — неизвестно: "
                                f"перечитайте кабинет, прежде чем повторять")
        same = found.get(label) or []
        seen = same[0] if same else None
        if operation.creating:
            whose = f"«{excerpt(operation.search[0], 32)}»"
            if seen is None:
                return UNVERIFIED, (
                    f"названным отбором объекта с тем же {whose} не нашлось. "
                    f"Отсутствия это не доказывает: полноту отбора обещать "
                    f"нечем, а идентификатора Директ не прислал — посмотрите "
                    f"глазами, прежде чем повторять"
                )
            counted = "" if len(same) == 1 else f", найдено {len(same)}"
            return UNVERIFIED, (
                f"вызов отказал, а объект с тем же {whose} в кабинете "
                f"есть{counted}. Приписать его этому вызову нечем: "
                f"идентификатора Директ не прислал, а уникальным это поле "
                f"Директ не объявлял — объект мог лежать здесь до прогона. "
                f"Посмотрите глазами, прежде чем повторять"
            )
        if operation.expect_gone:
            if seen is None:
                return APPLIED_ANYWAY, ("вызов отказал, а объекта в кабинете "
                                        "больше нет — разрушающая правка "
                                        "применилась")
            return REFUSED, "объект на месте"
        if seen is None:
            return UNVERIFIED, ("после отказа объект не прочитался вовсе, "
                                "хотя снимок его читал. Что с ним — "
                                "неизвестно: перечитайте кабинет, прежде чем "
                                "повторять")
        before = known.get(label)
        untouched = before is not None and _same(before, seen, operation.rules)
        if operation.blind_only(number):
            return UNVERIFIED, (
                "вызов отказал, а правил этот элемент только поле, которого "
                "чтение не возвращает: подтвердить или опровергнуть запись "
                "перечитыванием нечем — проверьте кабинет, прежде чем "
                "повторять"
            )
        if diff.compare(operation.wanted(number), seen, rules=operation.rules).ok:
            if untouched:
                return UNVERIFIED, (
                    "вызов отказал, а в кабинете то, что просили, — но так "
                    "было и до вызова: записал он или нет, по кабинету не "
                    "видно. Перечитыванием это не решается — состояние уже "
                    "прочитано; сверьте объект глазами, прежде чем повторять"
                )
            return APPLIED_ANYWAY, ("вызов отказал, а в кабинете уже то, что "
                                    "просили: до записи было другое")
        if untouched:
            return REFUSED, "в кабинете прежнее"
        return UNEXPLAINED, (
            f"в кабинете третье состояние — ни прежнее, ни запрошенное: "
            f"{_changed(before, seen)}"
        )

    def _note(self, entry: dict, report) -> bool:
        """Строка журнала после записи. Отказ становится отчётом, а не обрывом."""
        try:
            self.journal.record(entry)
            return True
        except DirectFailure as exc:
            if report is None:
                raise
            where = (f"{entry.get('service')}.{entry.get('method')}, объект "
                     f"{entry.get('object')}")
            report.record(UNLOGGED,
                f"{where}: {entry.get('outcome')}, а строка в журнал не "
                f"попала — {exc}"
            )
            return False

    def _verify(self, operation: Operation, number: int, identifier,
                after: dict) -> tuple:
        """Сверка запрошенного с фактическим: исход объекта, что сказать, разбор."""
        where = f"{operation.where}, объект {operation.name(number)}"
        if operation.expect_gone:
            if identifier not in after:
                return WRITTEN, "объекта в кабинете больше нет", \
                    diff.Comparison([], where=where)
            missing = diff.Difference(where, None, identifier, diff.MISMATCH,
                                      "объект остался в кабинете")
            return (UNEXPLAINED,
                    "Директ принял удаление, а объект остался в кабинете",
                    diff.Comparison([missing], where=where))
        if identifier not in after:
            missing = diff.Difference(where, None, None, diff.MISMATCH,
                                      "после записи объект не прочитался")
            return (UNVERIFIED,
                    "Директ принял запись, а объект после неё не прочитался. "
                    "Что легло в кабинет — неизвестно: перечитайте, прежде чем "
                    "повторять",
                    diff.Comparison([missing], where=where))
        found = diff.compare(operation.wanted(number), after[identifier],
                             rules=operation.rules, where=where)
        for path, sent, strange in operation.unread_returned(
                number, after[identifier]):
            found.findings.append(diff.Difference(
                path, sent or None, strange, diff.MISMATCH,
                f"поле объявлено непрочитываемым, а чтение вернуло по нему "
                f"{_missing_said(strange)} — этого не отправляли"))
        if found.ok:
            return WRITTEN, "перечитано и сошлось", found
        return (UNEXPLAINED,
                "; ".join(one.explain() for one in found.differences), found)


def _fields(value, path: str = ""):
    """Пары «путь без индексов — значение» по всему элементу.

    Индексы в пути не нужны: правило принадлежит полю, а не третьему заголовку
    в списке. Тот же вид пути понимает сверка (`diff.compare`, аргумент
    `steps`), и два способа записать одно и то же место разошлись бы."""
    if isinstance(value, dict):
        for key, item in value.items():
            yield from _fields(item, f"{path}.{key}" if path else str(key))
        if path:
            yield path, value
        return
    if isinstance(value, list):
        for item in value:
            yield from _fields(item, path)
        if path:
            yield path, value
        return
    if path:
        yield path, value


def _created(operation: Operation, outcome) -> dict:
    """Идентификаторы созданных объектов: место в пакете — идентификатор.

    Берутся из ответа поэлементно, а не из общего списка: у неудавшегося
    элемента идентификатора нет, и сдвиг по списку привязал бы чужой номер к
    чужому объекту."""
    return {place: entry.id for place, entry in enumerate(outcome.entries)
            if entry.ok and entry.id is not None}


def _frozen(value):
    """Копия, которую чужой код может править сколько угодно."""
    return json.loads(json.dumps(value, ensure_ascii=False, default=str))


def _written(item, id_field: str):
    """Пары «путь — значение» по всему, что элемент записывает.

    Идентификатор не в счёт: он адресует объект, а не меняет его. Пустой
    список и `None` — тоже записываемые значения: это очистка поля, и
    подтверждать её надо наравне с заполнением."""
    def walk(value, path):
        if isinstance(value, dict) and value:
            for key, item_value in value.items():
                yield from walk(item_value, f"{path}.{key}" if path else str(key))
            return
        if isinstance(value, list) and value:
            for one in value:
                yield from walk(one, path)
            return
        if path:
            yield path, value

    for path, value in walk(item, ""):
        if path != id_field:
            yield path, value


def _unchecked_said(where: str, path: str) -> str:
    """Строка отчёта о поле, которого никто не перечитывал.

    Одна на оба пути — удавшийся вызов и сорвавшийся, — потому что утверждение
    у них одно: поле ушло в Директ, а подтвердить записанное нечем. Два текста
    об одном разъезжаются на первой правке, и человек читает их как две разные
    новости."""
    return (f"{where}, поле «{path}»: ушло в запрос, а чтение его не "
            f"возвращает — записанное им перечитыванием не подтверждено")


def _pruned(value, path: str, unread):
    """Значение без объявленных непрочитываемых ветвей — для сверки ожидания.

    Вырезаются **ровно** объявленные пути; соседние поля остаются на месте, и
    сравнение под послаблением остаётся сравнением. Списки проходятся насквозь
    тем же путём, каким их отдаёт `_fields`: правило принадлежит полю, а не
    третьему элементу массива."""
    if not unread:
        return value
    if path in unread:
        # Непрочитываемо поле целиком: сравнивать нечего, и обе стороны
        # получают одно и то же «ничего».
        return UNSAID
    if isinstance(value, dict):
        return {key: _pruned(one, f"{path}.{key}" if path else str(key), unread)
                for key, one in value.items()
                if (f"{path}.{key}" if path else str(key)) not in unread}
    if isinstance(value, list):
        return [_pruned(one, path, unread) for one in value]
    return value


def _present(item, path: str):
    """Есть ли путь в объекте и что по нему лежит.

    Отсутствие и `None` — разные новости: `null` в запросе означает очистку
    поля, а пропущенное поле Директ не трогает вовсе. Читатель, который их не
    различает, принимает за очистку то, чего в запросе нет."""
    value = item
    for key in path.split("."):
        if not isinstance(value, dict) or key not in value:
            return False, None
        # ответ читается напрямую: путь назвал вызывающий код, а значение
        # сравнивается с обещанным в плане — разбирать его здесь нечего.
        value = value[key]
    return True, value


def _same(before, now, rules) -> bool:
    """Тот же это объект или его успели поправить."""
    if before is None or now is None:
        return before is None and now is None
    return (diff.compare(before, now, rules=rules).ok
            and diff.compare(now, before, rules=rules).ok)


def _changed(before, now) -> str:
    """Чем свежее чтение разошлось со снимком — по именам полей, а не целиком."""
    if before is None:
        return "в снимке его не было"
    if now is None:
        return "в кабинете его больше нет"
    names = sorted(str(name) for name in set(before) | set(now)
                   if before.get(name) != now.get(name))
    return "; ".join(
        f"«{name}»: подтверждали {excerpt(before.get(name), 48)}, а сейчас "
        f"{excerpt(now.get(name), 48)}" for name in names[:5]
    ) or "объект прочитан иначе"


def _side(entry, place: int) -> str:
    """Половина записи `derived`: имя запроса или имя чтения.

    Строка означает, что имя одно на обе стороны. Пара разводит их: у самого
    корня формы расходятся не только устройством, но и названием, и общего
    пути между запросом и чтением не остаётся вовсе."""
    if isinstance(entry, str):
        return entry
    parts = tuple(entry)
    if len(parts) != 2 or not all(isinstance(one, str) and one for one in parts):
        raise DirectFailure(
            f"Расхождение форм задаётся именем или парой «что пишем, что "
            f"читаем», а получено {excerpt(entry, 64)}. Пара из одного имени "
            f"или из трёх — это опечатка, и молча взятая половина сверяла бы "
            f"ожидание не с тем путём."
        )
    return parts[place]


def _search(entry, where: str) -> tuple:
    """Пара «поле, которым узнают своё — отбор для `get`», которой ищут созданное."""
    parts = tuple(entry) if isinstance(entry, (tuple, list)) else ()
    if len(parts) != 2 or not (isinstance(parts[0], str) and parts[0]):
        raise DirectFailure(
            f"{where}: чем искать созданное, задаётся парой «поле, которым "
            f"узнают своё, отбор для get», а получено {excerpt(entry, 64)}."
        )
    if parts[1] is WHOLE_ACCOUNT:
        return parts[0], WHOLE_ACCOUNT
    if not isinstance(parts[1], dict) or not parts[1]:
        raise DirectFailure(
            f"{where}: отбор для поиска созданного пуст. Пустой "
            f"`SelectionCriteria` — это не «все объекты»: у "
            f"`NegativeKeywordSharedSets.get` и `AdVideos.get` он замерен "
            f"отказом 8000, и поиск после "
            f"сорвавшегося вызова не состоялся бы вовсе. Сервис, который "
            f"перечисляется вызовом без `SelectionCriteria`, называет это "
            f"словом `WHOLE_ACCOUNT`; чем перечисляется этот, знает вызывающий "
            f"код."
        )
    return parts[0], dict(parts[1])


def _translate(path: str, mine, theirs) -> str:
    """Тот же путь под именем другой стороны.

    Корни примеряются от длинного к короткому: `A.B` и `A` оба подходят пути
    `A.B.C`, и взятый первым короткий увёл бы перевод не туда."""
    for root, other in sorted(zip(mine, theirs), key=lambda pair: -len(pair[0])):
        if root == other:
            continue
        if path == root:
            return other
        if path.startswith(root + "."):
            return other + path[len(root):]
    return path


def _under(path: str, roots) -> bool:
    """Лежит ли путь под одним из корней — сам корень считается."""
    return any(path == root or path.startswith(root + ".") for root in roots)


def _beneath(path: str, roots) -> bool:
    """То же, но строго внутри: сам корень не в счёт."""
    return any(path.startswith(root + ".") for root in roots)


def _leaves(value) -> list:
    """Значения в листьях поддерева: без имён полей и без вложенности."""
    if isinstance(value, dict):
        return [leaf for item in value.values() for leaf in _leaves(item)]
    if isinstance(value, list):
        return [leaf for item in value for leaf in _leaves(item)]
    return [value]


def _unpaired(mine: list, theirs: list) -> tuple:
    """Чего в списках друг у друга не хватает, с учётом повторов.

    Мультимножества, а не множества: два одинаковых заголовка — законный
    комплект, и потеря одного из них по множествам не видна."""
    rest = list(theirs)
    lost = []
    for item in mine:
        try:
            rest.remove(item)
        except ValueError:
            lost.append(item)
    return lost, rest


def _missing_said(values: list) -> str:
    shown = ", ".join(excerpt(value, 40) for value in values[:5])
    return shown + ("…" if len(values) > 5 else "")


def _overlap(first: str, second: str) -> bool:
    """Говорят ли два пути об одном месте: совпадение или вложенность."""
    return (first == second or first.startswith(second + ".")
            or second.startswith(first + "."))


def _owner(path: str, fields: dict):
    """Изменение, отвечающее за этот путь: точное совпадение или поддерево."""
    for field, change in fields.items():
        if path == field or path.startswith(field + "."):
            return change
    return None


def _dig(item, path: str):
    """Значение по точечному пути в прочитанном объекте или `None`."""
    for key in path.split("."):
        if not isinstance(item, dict) or key not in item:
            return None
        item = item[key]
    return item


def _identifier_kind(ids: list):
    """Тип идентификатора: число у большинства объектов, строка у изображений."""
    return str if ids and isinstance(ids[0], str) else int


def _title(item) -> str:
    if not isinstance(item, dict):
        return ""
    return str(optional(item, "Name", str, "предпросмотр") or "")
