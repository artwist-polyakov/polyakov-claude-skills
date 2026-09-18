"""Клиент API Яндекс Директа — точка входа в `scripts/lib/`."""

from __future__ import annotations

import http.client
import json
import random
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

import protocol as protocols
from config import (
    SKILL_DIR,
    DirectFailure,
    Settings,
    excerpt,
    keep_secret,
    redact,
    settings_from_env,
    short,
)
from errors import ItemIssue, TransportFailure, optional, read_issues, required
from payload import compact_payload

# Журнал вызовов. Каталог `logs/` исключён из git на любом уровне вложенности,
# поэтому тела запросов и логины кабинетов в репозиторий не попадут.
LOG_DIR = SKILL_DIR / "logs"

TIMEOUT = 30

PAGE_LIMIT_MAX = 10_000

# Суточный лимит баллов приходит из Clients.get отдельным элементом
# Restrictions — заголовок Units отдаёт его же, но только вместе с вызовом.
API_POINTS = "API_POINTS"

# Предупреждать, когда остаток меньше этой доли суточного лимита.
UNITS_WARN_PARTS = 10

# Чем батчевый метод называет созданный объект. По умолчанию `Id`, но не
# везде: изображение опознаётся хэшем, а не идентификатором — `AdImages.add`
# возвращает `AdImageHash` (references/API_OBJECTS.md, раздел 8.3). Список
# заведомо неполон, поэтому он не единственная опора: имя поля вдобавок
# выводится из самого элемента, см. `_identifier`.
IDENTIFIER_FIELDS = {"adimages": "AdImageHash"}

# Ключи элемента результата, которые идентификатором не являются.
NOT_IDENTIFIERS = frozenset({"Errors", "Warnings"})

# Ключи, которые в `result` метода `get` не являются выборкой.
SERVICE_KEYS = frozenset({"LimitedBy"})

# Тело запроса в журнале обрезается: выгрузка тысячи фраз в журнале не нужна,
# а первые пара тысяч символов отвечают на вопрос «что мы отправили».
JOURNAL_BODY_LIMIT = 2000


def warn_to_stderr(text: str) -> None:
    """Единственный путь предупреждений клиента: stderr, секреты вырезаны."""
    print(redact(text), file=sys.stderr)


# --------------------------------------------------------------------------
# Транспорт и повторы
# --------------------------------------------------------------------------

class NoRedirect(urllib.request.HTTPRedirectHandler):
    """Перенаправления не выполняются.

    urllib переносит на новый адрес все заголовки, кроме content-length и
    content-type, — то есть и Authorization с токеном, причём хост и схема
    берутся из ответа. Для клиента API это не удобство, а способ отдать токен
    туда, куда мы не собирались. Адреса контуров известны и постоянны."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class Transport:
    """Отправка запроса и чтение ответа: (код, заголовки, текст)."""

    def __init__(self, opener=None, timeout: int = TIMEOUT):
        self.opener = opener or urllib.request.build_opener(NoRedirect)
        self.timeout = timeout

    @staticmethod
    def decode(raw: bytes, status: int, received=None) -> str:
        """Байты ответа как текст. Строго UTF-8, без замен.

        `errors="replace"` здесь был бы тихой порчей данных: негодный байт
        внутри строки JSON превращается в U+FFFD, `json.loads` такой ответ
        принимает, и клиент возвращает искажённое название кампании или
        адрес как настоящие. Отказ на этом месте — единственный способ не
        выдать чужую подмену за данные Директа."""
        try:
            return raw.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise TransportFailure(
                f"Ответ (HTTP {status}) не читается как UTF-8: {exc}. "
                f"Так выглядит повреждённый ответ или подмена промежуточным "
                f"прокси.",
                retryable=status >= 500 or status == 429,
                status=status, headers=received,
            ) from None

    def send(self, url: str, body: str, headers: dict):
        request = urllib.request.Request(
            url, data=body.encode("utf-8"), headers=headers, method="POST",
        )
        # Заголовки приходят раньше тела, и обрыв на теле их не отменяет.
        # Объявлены они здесь, чтобы достаться и веткам отказа ниже: баллы
        # Директ списал за вызов, а `Units` — единственный след списанного.
        received = None
        try:
            with self.opener.open(request, timeout=self.timeout) as response:
                status = response.status
                received = response.headers
                return status, received, self.decode(response.read(), status, received)
        except urllib.error.HTTPError as exc:
            status = exc.code
            if 300 <= status < 400:
                # redirect_request вернул None, и urllib отдал ответ как ошибку.
                # Проверяется до чтения тела: тело перенаправления нам не нужно
                # ни в каком виде, а сказать надо именно про перенаправление.
                raise TransportFailure(
                    f"Директ ответил перенаправлением (HTTP {status}). Скилл "
                    f"им не следует: вместе с адресом ушёл бы и токен. "
                    f"Проверьте, не подменяет ли ответы промежуточный прокси.",
                    retryable=False, status=status, headers=exc.headers,
                ) from None
            try:
                raw = exc.read()
            except (http.client.HTTPException, OSError) as broken:
                # Исключение, брошенное внутри except, соседними ветками не
                # ловится: сервер отдал код отказа и оборвал соединение на теле.
                raise TransportFailure(
                    f"Ответ HTTP {status}, но соединение оборвалось при чтении "
                    f"тела: {excerpt(str(broken) or type(broken).__name__, 120)}",
                    status=status, headers=exc.headers,
                ) from None
            return status, exc.headers, self.decode(raw, status, exc.headers)
        except TimeoutError:
            raise TransportFailure(
                f"Директ не ответил за {self.timeout} с.", headers=received,
            ) from None
        except (urllib.error.URLError, http.client.HTTPException, OSError) as exc:
            # urllib оборачивает в URLError только отправку запроса: чтение
            # ответа остаётся снаружи, и обрыв соединения приходит как
            # HTTPException — мимо всех остальных веток.
            # ответ сюда не доходит: это исключение urllib, а не тело ответа
            reason = getattr(exc, "reason", None) or exc or type(exc).__name__
            raise TransportFailure(
                f"Нет связи с Директом: {excerpt(str(reason), 120)}. "
                f"Проверьте сеть и доступность контура.",
                headers=received,
            ) from None


class Retries:
    """Сколько раз повторять и сколько ждать между попытками."""

    def __init__(self, attempts: int = 3, backoff: float = 1.0,
                 sleep=time.sleep, jitter=random.random):
        self.attempts = max(1, int(attempts))
        self.backoff = backoff
        self._sleep = sleep
        self._jitter = jitter

    def last(self, attempt: int) -> bool:
        return attempt >= self.attempts

    def wait(self, attempt: int) -> None:
        delay = self.backoff * (2 ** (attempt - 1))
        # Дрожание разводит одновременные повторы: пять параллельных запросов,
        # получивших код 506, без него повторятся ровно вместе и получат его же.
        self._sleep(delay + self.backoff * self._jitter())


# --------------------------------------------------------------------------
# Баллы
# --------------------------------------------------------------------------

class Units:
    """Учёт баллов по кошелькам.

    Кошельков несколько, и складывать их нельзя. Под агентским токеном один
    запрос идёт без `Client-Login` и оплачивается агентством, следующий — с
    заголовком и оплачивается клиентом. Общая сумма приписывала бы клиенту
    чужие траты рядом с его же суточным лимитом."""

    def __init__(self, warn=None, warn_parts: int = UNITS_WARN_PARTS):
        self.wallets: dict = {}
        self.spent = 0
        self.unattributed = 0
        self._warn = warn or warn_to_stderr
        self._warn_parts = warn_parts
        self._warned: set = set()

    @staticmethod
    def parse(headers) -> dict:
        """Заголовок `Units: израсходовано/остаток/суточный лимит`.

        Возвращает `{"spent","left","limit","login","header"}`. Отсутствующий
        заголовок и испорченный — разные новости: первое означает, что Директ
        его не прислал, второе — что прислал мусор."""
        raw = str(header(headers, "Units") or "")
        login = excerpt(header(headers, "Units-Used-Login") or "", 64)
        parsed = {"spent": 0, "left": None, "limit": None, "login": login,
                  "header": "missing" if not raw else "broken"}
        parts = raw.split("/")
        if len(parts) != 3:
            return parsed
        try:
            spent, left, limit = (int(part.strip()) for part in parts)
        except ValueError:
            return parsed
        # Целое — ещё не число баллов. Отрицательный расход уменьшал бы
        # стоимость работы, отрицательный остаток печатался бы как «осталось
        # -1», а нулевой суточный лимит делает долю бессмысленной.
        if spent < 0 or left < 0 or limit <= 0:
            return parsed
        parsed.update({"spent": spent, "left": left, "limit": limit, "header": "ok"})
        return parsed

    def observe(self, headers) -> dict:
        """Учесть заголовки одного ответа."""
        parsed = self.parse(headers)
        self.spent += parsed["spent"]
        login = parsed["login"]
        if not login:
            # Без Units-Used-Login расход не приписан никому. Приписать его
            # текущему кабинету было бы догадкой, а счёт по догадке хуже, чем
            # честное «чей кошелёк, Директ не сообщил».
            self.unattributed += parsed["spent"]
            return parsed
        # ответ сюда не доходит: кошелёк заводим здесь же, из заголовка Units
        wallet = self.wallets.setdefault(
            login.lower(), {"login": login, "spent": 0, "left": None, "limit": None}
        )
        wallet["spent"] += parsed["spent"]
        if parsed["header"] == "ok":
            wallet["left"] = parsed["left"]
            wallet["limit"] = parsed["limit"]
            self._maybe_warn(wallet)
        return parsed

    # ответ сюда не доходит: кошелёк собрал observe из заголовков ответа
    def _maybe_warn(self, wallet: dict) -> None:
        left, limit = wallet["left"], wallet["limit"]
        if left is None or not limit:
            return
        if left * self._warn_parts >= limit:
            # Остаток восстановился — предупредим снова, если снова упадёт.
            self._warned.discard(wallet["login"].lower())
            return
        if wallet["login"].lower() in self._warned:
            return
        self._warned.add(wallet["login"].lower())
        self._warn(
            f"Внимание: баллов у {wallet['login']} осталось "
            f"{thousands(left)} из {thousands(limit)} — меньше "
            f"{100 // self._warn_parts}% суточного лимита. Крупный пакет "
            f"может не поместиться."
        )

    def left(self, login: str):
        # ответ сюда не доходит: тот же наш словарь кошельков из observe
        wallet = self.wallets.get((login or "").lower())
        return wallet["left"] if wallet else None

    def report(self) -> dict:
        # ответ сюда не доходит: словарь кошельков наш, и ключи в нём наши
        return {
            "spent": self.spent,
            "unattributed": self.unattributed,
            "wallets": {name: dict(wallet) for name, wallet in self.wallets.items()},
        }


def thousands(value) -> str:
    return f"{value:,}".replace(",", " ")


# Заголовки приходят рядом с телом, а не в нём, и `optional()` здесь
# неприменим не потому, что имя поля неизвестно, а потому, что поля не
# из ответа Директа: отсутствие `Units` — это отсутствие заголовка.
# ответ сюда не доходит: разбирается заголовок, а не тело
def header(headers, name: str):
    """Значение заголовка без оглядки на регистр имени."""
    getter = getattr(headers, "get", None)
    if getter is None:
        return None
    value = getter(name)
    if value is not None:
        return value
    items = getattr(headers, "items", None)
    if items is None:
        return None
    lowered = name.lower()
    for key, value in items():
        if str(key).lower() == lowered:
            return value
    return None


# --------------------------------------------------------------------------
# Журнал вызовов
# --------------------------------------------------------------------------

class Journal:
    """Журнал вызовов с вырезанным токеном.

    Тело запроса пишется в журнал намеренно: без него запись «Ads.add вернул
    ошибку 5001» не отвечает на вопрос, какое поле было длинным. Токен из тела
    вырезается — в четвёртой версии он лежит именно там, а не в заголовке.

    В памяти записи не держатся. Журнал существует, чтобы разобрать вчерашний
    инцидент, а не чтобы вызывающий код читал его по ходу; список, растущий на
    каждом вызове, у выгрузки на сотню страниц становится вторым экземпляром
    всех тел запросов, и ни один потребитель его не читает.

    Отказ записи не отменяет работу: скилл, падающий из-за того, что некуда
    положить лог, хуже скилла без лога. Сказать об этом надо один раз, и один
    раз он говорит."""

    def __init__(self, directory=LOG_DIR, warn=None, body_limit: int = JOURNAL_BODY_LIMIT):
        self.directory = Path(directory) if directory else None
        self.body_limit = body_limit
        self._warn = warn or warn_to_stderr
        self._broken = False

    def path_for(self, moment) -> Path:
        """Файл журнала за сутки: по файлу на день, а не один растущий."""
        return self.directory / f"api-{time.strftime('%Y-%m-%d', moment)}.jsonl"

    # ответ сюда не доходит: запись журнала собирает клиент, поля в ней наши
    def record(self, entry: dict) -> None:
        if self.directory is None or self._broken:
            return
        entry = {key: value for key, value in entry.items() if value is not None}
        if "body" in entry:
            try:
                body = json.loads(entry["body"])
            except (ValueError, TypeError):
                body = None
            if isinstance(body, (dict, list)):
                entry["body"] = json.dumps(compact_payload(body), ensure_ascii=False)
            entry["body"] = excerpt(entry["body"], self.body_limit)
        try:
            self.directory.mkdir(parents=True, exist_ok=True)
            line = json.dumps(entry, ensure_ascii=False, default=str)
            with self.path_for(time.localtime()).open("a", encoding="utf-8") as handle:
                handle.write(redact(line) + "\n")
        except OSError as exc:
            self._broken = True
            self._warn(
                f"Журнал вызовов не пишется в {short(self.directory)}: {exc}. "
                f"Работа продолжается без журнала."
            )


# --------------------------------------------------------------------------
# Ответы
# --------------------------------------------------------------------------

class Response:
    """Разобранный ответ одного вызова."""

    __slots__ = (
        "payload", "headers", "status", "service", "method", "version",
        "units", "request_id", "error", "attempts", "elapsed", "protocol",
    )

    def __init__(self, **values):
        for name in self.__slots__:
            # ответ читается напрямую: это наш собственный словарь именованных
            # аргументов, а не ответ Директа. Отсутствие имени означает «не
            # передали», а не повреждённый ответ
            given = values.get(name)
            # ответ читается напрямую: поле называется по `__slots__`, то есть
            # нами, а не Директом. Тело ответа кладётся в `payload`, и это
            # объявленный источник — прятать ответ в неназванном поле здесь
            # нечем
            setattr(self, name, given)

    @property
    def result(self):
        """Полезная часть ответа: `result` у версии 5, `data` у четвёртой.

        Пустая выборка приходит объектом без ключа выборки, а не объектом с
        пустым массивом: `Dictionaries.getGeoRegions` по несуществующему
        региону отвечает `{"result": {}}`. Проверено на живом кабинете
        27.08.2026. Отсюда правило разбора: отсутствие ключа означает «ничего
        не нашлось», а не «ответ повреждён»."""
        return self.protocol.data(self.payload)

    @property
    def ok(self) -> bool:
        return self.error is None

    def __repr__(self) -> str:
        where = f"{self.service}.{self.method}" if self.service else self.method
        return f"<Response {where} HTTP {self.status} {'ok' if self.ok else 'error'}>"


class ReportAnswer:
    """Ответ сервиса отчётов: код, тело и пауза до следующего опроса.

    Отдельный тип, а не `Response`, потому что вопрос к отчёту другой.
    Успешный ответ бывает пустым: коды 201 и 202 означают «поставлен в
    очередь» и «ещё формируется», приходят обычным путём и тела не несут.
    Готовность решает код, а не тело, и тип, у которого `ready` спрашивают
    отдельно от `text`, эту разницу называет.

    `retry_in` — заголовок `retryIn` как есть, строкой: сколько ждать,
    решает ожидание в `lib/reports.py`, и оно же знает свой предел."""

    __slots__ = ("status", "text", "retry_in", "request_id")

    def __init__(self, status, text, retry_in="", request_id=""):
        self.status = status
        self.text = text
        self.retry_in = retry_in
        self.request_id = request_id

    @property
    def ready(self) -> bool:
        return self.status == 200

    def __repr__(self) -> str:
        return (f"<ReportAnswer HTTP {self.status} "
                f"{'готов' if self.ready else 'в очереди'} "
                f"{len(self.text)} символов>")


class BatchEntry:
    """Один элемент результата батчевого метода."""

    __slots__ = ("index", "item", "id", "id_field", "issues")

    def __init__(self, index: int, item=None, identifier=None, issues=None, id_field=None):
        self.index = index
        self.item = item
        self.id = identifier
        # Чем именно Директ назвал объект: `Id` у большинства методов,
        # `AdImageHash` у изображений. Вызывающему коду это нужно, чтобы
        # сослаться на созданный объект в следующем запросе.
        self.id_field = id_field
        self.issues = issues or []

    @property
    def errors(self) -> list:
        return [issue for issue in self.issues if issue.is_error]

    @property
    def warnings(self) -> list:
        return [issue for issue in self.issues if not issue.is_error]

    @property
    def ok(self) -> bool:
        return not self.errors

    def __repr__(self) -> str:
        state = f"{self.id_field}={self.id}" if self.ok else f"ошибок {len(self.errors)}"
        return f"<BatchEntry {self.index} {state}>"


class BatchResult:
    """Результат батчевого метода: успехи и отказы вместе.

    Батчевые методы Директа возвращают HTTP 200 и кладут ошибки внутрь
    отдельных элементов. Клиент, который смотрит только на общий `error`, молча
    теряет неудавшиеся элементы и отчитывается об успехе. Поэтому здесь оба
    списка сразу, и ни один из них не поднимается исключением."""

    def __init__(self, entries: list, response: Response, results_key: str):
        self.entries = entries
        self.response = response
        self.results_key = results_key

    @property
    def ok(self) -> list:
        return [entry for entry in self.entries if entry.ok]

    @property
    def failed(self) -> list:
        return [entry for entry in self.entries if not entry.ok]

    @property
    def ids(self) -> list:
        return [entry.id for entry in self.ok if entry.id is not None]

    @property
    def issues(self) -> list:
        return [issue for entry in self.entries for issue in entry.issues]

    @property
    def mismatches(self) -> list:
        """Предупреждения «принято, но не применено».

        Единственный признак того, что успешный ответ не означает записанного
        значения. Сверка после перечитывания обязана их увидеть."""
        return [issue for issue in self.issues if issue.means_mismatch]

    @property
    def complete(self) -> bool:
        return bool(self.entries) and not self.failed

    def summary(self) -> str:
        total = len(self.entries)
        failed = len(self.failed)
        # Считаются элементы, а не предупреждения: у одного элемента их может
        # быть несколько, и «с предупреждением 3» на двух элементах читалось бы
        # как три задетых объекта.
        warned = len([entry for entry in self.entries if entry.warnings])
        parts = [f"элементов {total}", f"удалось {total - failed}"]
        if failed:
            parts.append(f"отказано {failed}")
        if warned:
            parts.append(f"с предупреждением {warned}")
        return ", ".join(parts)

    def __len__(self) -> int:
        return len(self.entries)

    def __iter__(self):
        return iter(self.entries)

    def __repr__(self) -> str:
        return f"<BatchResult {self.results_key}: {self.summary()}>"


# --------------------------------------------------------------------------
# Клиент
# --------------------------------------------------------------------------

class Client:
    """Фасад над конвертом, транспортом, повторами, баллами и журналом.

    Один экземпляр — один контур и один токен. Кабинет задаётся либо в
    настройках, либо аргументом `account` отдельного вызова."""

    def __init__(
        self,
        settings: Settings,
        transport,
        *,
        retries=None,
        journal=None,
        units=None,
        warn=None,
        protocol_set=None,
    ):
        self.settings = settings
        self.transport = transport
        self.retries = retries or Retries()
        self.warn = warn or warn_to_stderr
        self.units = units if units is not None else Units(warn=self.warn)
        # Журнал по умолчанию не пишет: путь к каталогу знает `from_env`, и
        # клиент, собранный вручную, не должен заводить файлы за спиной у того,
        # кто его собрал.
        self.journal = journal if journal is not None else Journal(directory=None)
        self.protocols = protocol_set or protocols.protocols()
        # Токен запоминается и здесь: сюда приходят и настройки, собранные
        # вызывающим кодом мимо `from_env`.
        keep_secret(settings.token)

    @classmethod
    # ответ сюда не доходит: это именованные аргументы вызывающего кода
    def from_env(cls, profile=None, account=None, env_file=None, environ=None, **options):
        """Клиент по `config/.env` и переменным окружения.

        Единственное место, где собираются сотрудники по умолчанию: сеть,
        каталог журнала и счётчик баллов."""
        settings = settings_from_env(profile, account, env_file, environ)
        warn = options.pop("warn", None) or warn_to_stderr
        transport = options.pop("transport", None) or Transport()
        options.setdefault("journal", Journal(warn=warn))
        options.setdefault("units", Units(warn=warn))
        return cls(settings, transport, warn=warn, **options)

    def protocol(self, version: str):
        # ответ сюда не доходит: набор конвертов собран в конструкторе
        found = self.protocols.get(version)
        if found is None:
            raise DirectFailure(
                f"Неизвестная версия транспорта «{excerpt(version, 32)}». "
                f"Допустимо: {', '.join(self.protocols)}"
            )
        return found

    # -- вызовы -------------------------------------------------------------

    def call(
        self,
        service: str,
        method: str,
        params=None,
        *,
        account=None,
        use_operator_units=None,
        retry=None,
        raise_on_error: bool = True,
    ) -> Response:
        """Один вызов сервиса версии 5.

        `retry=None` означает «решить по методу»: чтение повторяется, запись —
        нет. `raise_on_error=False` возвращает отказ в `Response.error` вместо
        исключения; это нужно ровно там, где ошибка — ветка логики, а не сбой:
        код 54 на `AgencyClients.get` означает «токен не агентский».

        `use_operator_units` — ответ на вопрос «чьими баллами платим» либо
        способ его получить: вызываемое без аргументов, которое спрашивается
        перед каждым запросом, включая повтор. Скилл передаёт способ: попыток
        у вызова бывает несколько, а баллы Директ списывает и за неудачную."""
        return self._exchange(
            self.protocol(self.settings.version),
            service=service, method=method, params=params,
            account=self.settings.account if account is None else account,
            use_operator_units=use_operator_units,
            retry=retry, raise_on_error=raise_on_error,
        )

    def call_v4(
        self,
        method: str,
        param=None,
        *,
        version: str = "live/v4",
        retry=None,
        raise_on_error: bool = True,
    ) -> Response:
        """Вызов четвёртой версии — того, чего в версии 5 нет.

        Счёт и оплата, метки, прогноз бюджета, подбор фраз, цели Метрики: у
        этих методов эквивалента в версии 5 не существует. Куда именно уходит
        токен и как разбирается ответ — забота `protocol.V4`."""
        if version not in protocols.V4_PATHS:
            raise DirectFailure(
                f"«{excerpt(version, 32)}» — не версия четвёртого семейства. "
                f"Допустимо: {', '.join(protocols.V4_PATHS)}"
            )
        # решение об оплате не нужно: четвёртая версия ни `Client-Login`, ни
        # `Use-Operator-Units` не отправляет вовсе — кабинет адресуется полем
        # параметров (`references/CLIENT_LOGIN.md`, раздел 6). Решать здесь
        # нечего, и `None` — это не «не решили», а «решать не о чем».
        return self._exchange(
            self.protocol(version),
            service="", method=method, params=param, account="",
            use_operator_units=None, retry=retry, raise_on_error=raise_on_error,
        )

    def report_once(
        self,
        params,
        *,
        report_type: str = "",
        account=None,
        processing_mode: str = "auto",
        use_operator_units=None,
        retry=None,
        raise_on_error: bool = True,
    ) -> "ReportAnswer":
        """Один запрос отчёта: сервис `Reports` версии 5, ответ TSV.

        Вход отдельный, потому что отдельный у отчётов конверт: тело без
        `method`, ответ не JSON, а коды 201 и 202 означают очередь, а не
        данные. Готовность решает код ответа: ответ очереди успешен и пуст.

        Имя не `report`, хотя вход один и отчёт один. Перечень входов клиента
        разбор собирает по именам — так держится правило «вызов с кабинетом
        несёт решение об оплате», — а `report` в скилле самое частое имя
        сводки: восемь команд печатают ею результат. Вход с таким именем
        сделал бы нарушителями два десятка вызовов печати.

        Опрос готовности — повторная отправка **того же** запроса, и делает
        его `lib/reports.py`: пауза между опросами приходит заголовком
        `retryIn`, а имя отчёта обязано остаться прежним. Клиент про это не
        знает намеренно — он возит один запрос.

        `report_type` попадает в журнал вызовов на место метода: у сервиса
        метод один, а различать записи журнала надо."""
        response = self._exchange(
            protocols.Reports(processing_mode),
            service="reports", method=report_type or "report", params=params,
            account=self.settings.account if account is None else account,
            use_operator_units=use_operator_units,
            retry=retry, raise_on_error=raise_on_error,
        )
        return ReportAnswer(
            status=response.status,
            # Тело у ответа очереди пустое, и это законно: пустой строкой оно
            # сюда и приходит. Пустое тело при коде 200 отвергнуто раньше —
            # конвертом, для которого это единственный признак того, что
            # ответ очереди прочитали как готовый отчёт.
            text=response.result or "",
            retry_in=header(response.headers, "retryIn") or "",
            request_id=response.request_id,
        )

    def get_all(
        self,
        service: str,
        params=None,
        *,
        collection=None,
        account=None,
        use_operator_units=None,
    ) -> list:
        """Выборка целиком: страницы склеиваются по `LimitedBy`.

        Наличие `LimitedBy` в ответе — не сведение, а обязательство продолжить:
        код, который смотрит только на первую страницу, молча теряет данные и
        выглядит работающим.

        `Page.Limit` вызывающего кода становится размером страницы, а не
        пределом выборки: чтобы взять ровно одну страницу, есть `call`.

        Решение об оплате, переданное **значением**, уходит во все страницы
        одно и то же — снятое до первой. Остаток кабинета меняется от ответа к
        ответу, и кабинет, которому хватало на первую страницу, на третьей
        платить уже не может. Поэтому вызывающий код передаёт сюда не ответ, а
        способ его получить: вызываемое спрашивается перед каждой страницей."""
        # ответ сюда не доходит: параметры запроса собрал вызывающий код
        params = dict(params or {})
        page = dict(params.get("Page") or {})
        # Страница проверяется до отправки и не подправляется молча: `Limit: 0`
        # — законное значение по справочнику и вечный цикл здесь, а
        # переписанный на умолчание он выдал бы за выборку то, чего не просили.
        limit = _page_number(page.get("Limit"), "Limit", 1, PAGE_LIMIT_MAX,
                             default=PAGE_LIMIT_MAX)
        offset = _page_number(page.get("Offset"), "Offset", 0, None, default=0)
        collected: list = []
        pages = 0
        while True:
            request = dict(params)
            request["Page"] = {"Limit": limit, "Offset": offset}
            response = self.call(
                service, "get", request,
                account=account, use_operator_units=use_operator_units,
            )
            pages += 1
            result = response.result
            if not isinstance(result, dict):
                # Не «данные кончились», а «ответ не тот». Разница видна только
                # здесь: страница после обещанного LimitedBy молча оборвала бы
                # выборку, и вызывающий код не отличил бы полную от обрезанной.
                # Аудит по половине кампаний выглядит как аудит по всем.
                raise TransportFailure(
                    f"{service}.get: страница {pages} пришла с result типа "
                    f"{type(result).__name__} вместо объекта. Собрано "
                    f"{len(collected)} объектов, но выборка неполна.",
                    retryable=False,
                )
            name = collection or _sole_collection(result, response)
            if name is not None and name not in result:
                # Имя выборки задал вызывающий код, а ключа в ответе нет.
                # Дальше — та же развилка, что и у ненайденного имени.
                name = None
            if name is None:
                if pages > 1:
                    # На первой странице отсутствие ключа означает «ничего не
                    # нашлось». На продолжении — противоречие: предыдущий ответ
                    # обещал `LimitedBy`, то есть данные есть. Вернуть здесь
                    # накопленный кусок значило бы выдать обрезанную выборку за
                    # полную, ровно как и на негодном `result` выше.
                    raise TransportFailure(
                        f"{service}.get: страница {pages} пришла без выборки, "
                        f"хотя предыдущая обещала продолжение. Собрано "
                        f"{len(collected)} объектов, но выборка неполна. Так "
                        f"выглядит и повреждённый ответ, и данные, которые "
                        f"изменились под выборкой.",
                        retryable=False,
                    )
                # Ключа выборки в ответе нет — значит не нашлось ничего.
                # Пустая выборка это законный ответ, а не повреждённый.
                break
            # Ключ есть, а массива нет — повреждённый ответ: `null` в выборке
            # скрыл бы от аудита все объекты ровно так же, как обрезанная
            # страница.
            # Накопитель склейки — наш список, но разбор этого не доказывает:
            # в него уже клали ответ, и по вердикту он от чужого объекта
            # неотличим. Наполнение его читают помощниками выше по стеку.
            # ответ читается напрямую: складываем проверенный массив страницы
            collected.extend(required(result, name, list, f"{service}.get"))
            # Отсутствие LimitedBy означает, что страница последняя. Именно
            # отсутствие: `null` в ключе документацией не предусмотрен и
            # оборвал бы выборку тем же молчанием.
            limited = optional(result, "LimitedBy", int, f"{service}.get")
            if limited is None:
                break
            if limited <= offset:
                # Смещение обязано расти. Иначе это вечный цикл, который
                # тратит баллы и выглядит как долгая выгрузка.
                raise TransportFailure(
                    f"{service}.get: LimitedBy={excerpt(limited, 32)} не "
                    f"продвигает выборку (текущее смещение {offset}). "
                    f"Продолжать нечем, данные неполны.",
                    retryable=False,
                )
            offset = limited
        self.journal.record({
            "at": _now(), "kind": "pagination", "service": service,
            "pages": pages, "collected": len(collected),
        })
        return collected

    def batch(
        self,
        service: str,
        method: str,
        params=None,
        *,
        items=None,
        results_key=None,
        id_field=None,
        account=None,
        use_operator_units=None,
        retry: bool = False,
    ) -> BatchResult:
        """Батчевый вызов с разбором поэлементных ошибок.

        Отказ уровня запроса поднимается исключением: он означает, что не
        выполнено ничего. Отказ уровня элемента возвращается в результате —
        поднять его исключением значило бы потерять успешные элементы.

        Повтор по умолчанию выключен: батчевые методы — это запись, а повтор
        записи без ключа идемпотентности создаёт дубли.

        `id_field` называет поле идентификатора, если оно нестандартное.
        Обычно этого не требуется: имя выводится из самого элемента.

        Решение об оплате здесь такое же, как у остальных входов, — способ его
        получить. Запрос один, но попыток у него бывает несколько, а цена
        пакета самая высокая в скилле: своих баллов кабинету перестаёт хватать
        именно на ней."""
        response = self.call(
            service, method, params,
            account=account, use_operator_units=use_operator_units, retry=retry,
        )
        result = response.result
        where = f"{service}.{method}"
        if not isinstance(result, dict):
            raise TransportFailure(
                f"{where}: успешный ответ без разбираемого результата "
                f"({type(result).__name__}). Считать батч выполненным нельзя.",
                retryable=False,
            )
        key = results_key or _sole_results_key(result, where)
        elements = required(result, key, list, where)
        # ответ сюда не доходит: это отправленные элементы, а не полученные
        items = list(items) if items is not None else []
        if items and len(items) != len(elements):
            # Порядок задан общим правилом Директа: элементам входного массива
            # соответствуют элементы выходного, в том же порядке. Разошедшаяся
            # длина означает, что сопоставлять по позиции больше нельзя.
            raise TransportFailure(
                f"{where}: отправлено {len(items)} элементов, получено "
                f"{len(elements)}. Сопоставить ответ с запросом по позиции "
                f"нельзя, результат не разбирается.",
                retryable=False,
            )
        preferred = id_field or IDENTIFIER_FIELDS.get(
            protocols.service_key(service), "Id"
        )
        entries = []
        for index, element in enumerate(elements):
            issues = read_issues(element, index)
            found, name = _identifier(element, preferred)
            if found is None and not any(issue.is_error for issue in issues):
                # Идентификатор возвращается только при отсутствии ошибок.
                # Элемент без него и без `Errors` — не успех: молча зачесть его
                # значило бы отчитаться о созданном объекте, которого нет.
                issues.append(ItemIssue(
                    "error", None,
                    "Директ вернул элемент без идентификатора и без ошибок",
                    excerpt(element, 120), index,
                ))
            entries.append(BatchEntry(
                index, items[index] if index < len(items) else None,
                found, issues, name,
            ))
        outcome = BatchResult(entries, response, key)
        self.journal.record({
            "at": _now(), "kind": "batch", "service": service, "method": method,
            "results_key": key, "summary": outcome.summary(),
            "request_id": response.request_id,
        })
        return outcome

    # -- справочники и цели --------------------------------------------------

    def dictionaries(self, names, *, account=None) -> dict:
        """Справочники кабинета: `Dictionaries.get`.

        Регионы, часовые пояса, валюты и ограничения на значения параметров.
        Заголовок `Client-Login` сервису не нужен и не отправляется."""
        if isinstance(names, str):
            names = [names]
        asked = list(names)
        # решение об оплате не нужно: `Dictionaries` — правило `shared`, и
        # `Client-Login` отсюда не уходит, а без него не уходит и
        # `Use-Operator-Units`. Платит владелец токена, то есть агентство, — и
        # это дешевле, чем баллами клиента (`references/CLIENT_LOGIN.md`,
        # раздел 3).
        response = self.call(
            "dictionaries", "get", {"DictionaryNames": asked}, account=account,
        )
        result = response.result
        if not isinstance(result, dict):
            raise TransportFailure(
                f"Dictionaries.get: result типа {type(result).__name__} вместо "
                f"объекта. Справочников в ответе нет.",
                retryable=False,
            )
        # Директ отдаёт все запрошенные справочники или отказывает целиком:
        # неизвестное имя даёт ошибку 8000 на весь вызов, пустой список — тоже
        # (проверено 27.08.2026). Значит недостающее имя в успешном ответе —
        # не «этого справочника нет», а неполный ответ, и вернуть его молча
        # значит отдать потребителю пустой справочник вместо отказа.
        # Проверяется и наличие имени, и его значение: `{"Currencies": null}`
        # прошло бы за успешно загруженный справочник, а потребитель принял бы
        # повреждённые валюты за пустые данные.
        return {name: required(result, name, list, "Dictionaries.get") for name in asked}

    # Критерии отбора `Dictionaries.getGeoRegions`. Ровно один — не «хотя бы
    # один»: при пустом наборе и при двух сразу метод отвечает одинаковой
    # ошибкой 4001 и берёт за неё баллы.
    GEO_CRITERIA = ("RegionIds", "Name", "ExactNames")

    def geo_regions(self, region_ids=None, *, name=None, exact_names=None,
                    fields=None, account=None) -> list:
        """Регионы таргетинга: `Dictionaries.getGeoRegions`.

        Состав параметров справочник не приводит; выяснено на живом кабинете
        27.08.2026. Обязательны оба — `SelectionCriteria` и `FieldNames`, без
        любого из них ответ 8000. В `SelectionCriteria` допустим ровно один из
        `RegionIds`, `Name`, `ExactNames`; ни одного или два сразу — ошибка
        4001. Поля: `GeoRegionId`, `GeoRegionName`, `ParentGeoRegionNames`.

        Отбор проверяется до отправки, потому что ошибка не бесплатна: тот же
        вызов с пустым критерием списал 30 баллов, а полный справочник
        регионов отдаёт `Dictionaries.get` за один. Поэтому весь список берётся
        там, а этот метод — для точечной выборки."""
        criteria = {
            key: value
            for key, value in (
                ("RegionIds", None if region_ids is None else list(region_ids)),
                ("Name", name),
                ("ExactNames", None if exact_names is None else list(exact_names)),
            )
            if value is not None
        }
        if len(criteria) != 1:
            raise DirectFailure(
                f"getGeoRegions: нужен ровно один критерий отбора из "
                f"{', '.join(self.GEO_CRITERIA)}, а задано {len(criteria)}. "
                f"Директ отвечает на это ошибкой 4001 и списывает баллы."
            )
        params = {
            "SelectionCriteria": criteria,
            "FieldNames": list(fields or ("GeoRegionId", "GeoRegionName")),
        }
        # решение об оплате не нужно: тот же `Dictionaries` с правилом
        # `shared` — заголовка адресации нет, платит владелец токена.
        response = self.call("dictionaries", "getGeoRegions", params, account=account)
        result = response.result
        if not isinstance(result, dict):
            raise TransportFailure(
                f"Dictionaries.getGeoRegions: result типа "
                f"{type(result).__name__} вместо объекта.",
                retryable=False,
            )
        # Ничего не нашлось — ответ `{"result": {}}` без ключа выборки.
        # Проверено на живом кабинете 27.08.2026 по несуществующему региону.
        # Именно отсутствие ключа, а не `null` в нём: `null` пришёл бы от
        # повреждённого ответа и выдал бы себя за «регионов нет».
        regions = optional(result, "GeoRegions", list, "Dictionaries.getGeoRegions")
        return regions if regions is not None else []

    def stat_goals(self, campaign_ids=None, *, strategy_ids=None) -> list:
        """Цели Метрики, доступные кампании: `GetStatGoals` (live/v4).

        Метод принимает одно из трёх: `CampaignIDS`, `CampaignID` или
        `StrategyIDs`. Проверено живьём 27.08.2026 — пустой `param` отвечает
        именно этим перечнем."""
        if campaign_ids is None and strategy_ids is None:
            raise DirectFailure(
                "GetStatGoals: нужен хотя бы один отбор — кампании или "
                "стратегии. Метод не отдаёт цели «вообще»."
            )
        param: dict = {}
        if campaign_ids is not None:
            param["CampaignIDS"] = [int(value) for value in campaign_ids]
        if strategy_ids is not None:
            param["StrategyIDs"] = [int(value) for value in strategy_ids]
        return _records(self.call_v4("GetStatGoals", param), "GetStatGoals")

    def retargeting_goals(self, logins) -> list:
        """Цели и сегменты для ретаргетинга: `GetRetargetingGoals` (live/v4).

        Обязательный параметр — `Logins`; без него метод отвечает ошибкой 71."""
        names = _logins(logins, "GetRetargetingGoals")
        return _records(
            self.call_v4("GetRetargetingGoals", {"Logins": names}),
            "GetRetargetingGoals",
        )

    # -- баллы ---------------------------------------------------------------

    def units_v4(self, logins) -> dict:
        """Баллы **четвёртой** версии по логинам: `GetClientsUnits` (v4).

        Это не остаток из заголовка `Units`. У четвёртой версии своя система
        баллов: они начисляются раз в сутки и не накапливаются, и пул у неё
        отдельный. Замер 27.08.2026: метод вернул `UnitsRest` 32 000 сразу для
        трёх логинов — агентства с суточным лимитом версии 5 в 28 000 500 и
        двух его клиентов, у одного из которых лимит 160 000. Одинаковое число
        при разных лимитах и означает, что пул другой.

        Поэтому остаток версии 5 по конкретному логину читается не здесь, а
        методом `units_for`: `Clients.get` отдаёт и заголовок с остатком, и
        суточный лимит в `Restrictions`."""
        names = _logins(logins, "GetClientsUnits")
        rest = {}
        answer = self.call_v4("GetClientsUnits", names, version="v4")
        for record in _records(answer, "GetClientsUnits"):
            if not isinstance(record, dict):
                raise TransportFailure(
                    f"GetClientsUnits: запись ответа не объект: "
                    f"{excerpt(record, 120)}",
                    retryable=False,
                )
            # Остаток обязателен наравне с логином: запись без него дала бы
            # `{логин: None}`, и вызывающий код не отличил бы повреждённый
            # ответ от настоящего остатка.
            login = required(record, "Login", str, "GetClientsUnits")
            rest[login] = required(record, "UnitsRest", int, "GetClientsUnits")
        # Негодный логин метод отвергает целиком — ошибкой 251 на весь вызов
        # (проверено 27.08.2026). Значит логин, пропавший из успешного ответа,
        # означает неполный ответ, а не «у него нет баллов».
        missing = [name for name in names if name not in rest]
        if missing:
            raise TransportFailure(
                f"GetClientsUnits: в ответе нет запрошенных логинов "
                f"({', '.join(missing)}). Ответ неполон.",
                retryable=False,
            )
        return rest

    def units_for(self, account=None) -> dict:
        """Остаток и суточный лимит баллов версии 5 для кабинета.

        Ответ на вопрос «хватит ли баллов на задуманный пакет». Заголовок
        `Units` приходит только вместе с вызовом, поэтому спрашивать баллы
        холостым вызовом ради заголовка нельзя — вызов их и потратит. Здесь
        платный вызов ровно один и он же приносит суточный лимит: `Clients.get`
        стоит 10 баллов и отдаёт `Restrictions` с элементом `API_POINTS`.

        Все числа обязаны быть про **один** кошелёк. Под агентским токеном с
        `Use-Operator-Units: true` списываются баллы агентства, и заголовок
        описывает агентство, тогда как `API_POINTS` в ответе — всё ещё
        выбранного клиента. Смешать их значит показать остаток агентства рядом
        с суточным лимитом клиента: доля из такой пары получается любой, и
        предупреждение «баллов мало» приходит не тогда, когда их мало. Поэтому
        `API_POINTS` берётся только при совпадении логинов."""
        response = self.call(
            "clients", "get", {"FieldNames": ["Login", "Restrictions"]}, account=account,
        )
        restriction_limit = None
        result = response.result
        clients = optional(result, "Clients", list, "Clients.get")
        if not clients or not isinstance(clients[0], dict):
            # Метод отвечает про один кабинет — свой или адресованный
            # заголовком. Пустой ответ здесь не «кабинетов нет», а ответ не про
            # то, о чём спрашивали: снимок баллов из него вышел бы ничейным.
            raise TransportFailure(
                f"Clients.get: сведений о кабинете в ответе нет "
                f"({type(clients).__name__}). Остаток баллов приписать некому.",
                retryable=False,
            )
        cabinet = required(clients[0], "Login", str, "Clients.get")
        # Поле, запрошенное в FieldNames, Директ может и не вернуть — у
        # агентского кабинета Restrictions в ответе не было вовсе (замер
        # 27.08.2026). Это законно; чужой тип на его месте — нет.
        restrictions = optional(clients[0], "Restrictions", list, "Clients.get") or []
        for restriction in restrictions:
            if not isinstance(restriction, dict):
                raise TransportFailure(
                    f"Clients.get: запись Restrictions не объект: "
                    f"{excerpt(restriction, 120)}",
                    retryable=False,
                )
            if optional(restriction, "Element", str, "Clients.get") != API_POINTS:
                continue
            value = required(restriction, "Value", int, "Clients.get")
            if value <= 0:
                # Суточный лимит есть, но прочесть его нечем. Молча уйти на
                # заголовок значило бы показать лимит, которого Директ не
                # называл, — а доля «осталось от лимита» считается по нему.
                raise TransportFailure(
                    f"Clients.get: {API_POINTS} пришёл значением {value}. "
                    f"Суточный лимит баллов не бывает нулевым или "
                    f"отрицательным — доля «осталось от лимита» по такому "
                    f"числу не считается.",
                    retryable=False,
                )
            restriction_limit = value
        # Снимок баллов собран из заголовков ответа, а не из его тела: заголовок
        # приходит рядом с телом и разбору тела не подлежит.
        # ответ сюда не доходит: снимок заголовка Units, а не тела ответа
        units = response.units or {}
        if units.get("header") != "ok" or not units.get("login"):
            # Метод отвечает на вопрос «хватит ли баллов на пакет». Снимок без
            # остатка или без имени кошелька на этот вопрос не отвечает, а
            # выглядит как ответ: `left=None` прочитается как «ограничений
            # нет». Оба заголовка справочник фиксирует на каждом ответе.
            raise TransportFailure(
                f"Clients.get: заголовок Units пришёл в состоянии "
                f"«{units.get('header', 'нет')}», кошелёк "
                f"«{units.get('login') or 'не назван'}». Остаток баллов "
                f"неизвестен, снимка не выходит.",
                retryable=False,
            )
        wallet = units["login"]
        # Заголовок остаётся запасным и при совпадении: поле, запрошенное в
        # FieldNames, Директ может и не вернуть — у агентского кабинета
        # Restrictions в ответе не было вовсе (замер 27.08.2026).
        same_wallet = bool(wallet) and bool(cabinet) and wallet.lower() == cabinet.lower()
        if not wallet or same_wallet:
            limit = restriction_limit if restriction_limit is not None else units.get("limit")
        else:
            limit = units.get("limit")
        return {
            "login": wallet or cabinet,
            "cabinet": cabinet,
            "spent": units.get("spent"),
            "left": units.get("left"),
            "limit": limit,
        }

    # -- внутреннее ----------------------------------------------------------

    def _exchange(self, protocol, *, service, method, params, account,
                  use_operator_units, retry, raise_on_error):
        """Обмен с повторами."""
        key = protocols.service_key(service) if service else ""
        where = f"{key}.{method}" if key else method
        if retry is None:
            retry = protocol.safe(method)
        started = time.monotonic()
        last_failure = None
        for attempt in range(1, self.retries.attempts + 1):
            final = self.retries.last(attempt)
            # Запрос собирается на каждую попытку, а не один раз до цикла:
            # решение об оплате бывает не значением, а способом его получить, и
            # спросить его надо перед **каждым** физическим запросом. Между
            # попытками остаток кабинета меняется — Директ списывает баллы и за
            # ответ с ошибкой, — и заголовок, собранный по прежнему ответу,
            # отправил бы платить кошелёк, которому уже нечем.
            # ответ сюда не доходит: конверт собирает уходящее из наших настроек
            url, body, headers = protocol.request(
                self.settings, service=service, method=method, params=params,
                account=account,
                use_operator_units=_decided(
                    use_operator_units=use_operator_units),
            )
            common = {
                "kind": "call", "version": protocol.version, "service": key or None,
                "method": method, "account": headers.get("Client-Login"),
                "body": body,
            }
            # Учёт баллов один на попытку, и именно поэтому он объявлен здесь:
            # обе ветки ниже берут уже посчитанное значение. Второй вызов
            # `observe` в общей ветке приписал бы кошельку заголовки одного
            # ответа дважды.
            units = None
            try:
                status, received, raw = self.transport.send(url, body, headers)
                # Баллы списаны за этот ответ независимо от того, разберётся
                # он или нет: Директ берёт их за вызов, а не за читаемость
                # ответа. Учитываются они только по версии 5 — у четвёртой
                # своя система, и заголовков Units она не присылает вовсе.
                units = self.units.observe(received) if protocol.counts_units else None
                # Разбор тела стоит внутри try не для красоты: он тоже бросает
                # TransportFailure — на HTML от шлюза вместо JSON, — и снаружи
                # эта ошибка обходила и повторы, и запись в журнал.
                payload = protocol.parse(raw, status, where)
            except TransportFailure as failure:
                last_failure = failure
                if units is None and protocol.counts_units and failure.headers is not None:
                    # Ответ мог прийти и не прочитаться: тело не в UTF-8,
                    # оборванное соединение на теле, перенаправление. Баллы за
                    # него списаны, и заголовки отказ принёс с собой. Условие
                    # `units is None` не даёт учесть один ответ дважды: если
                    # заголовки уже прошли выше, здесь их не тронут. А их
                    # отсутствие означает, что ответа не было вовсе, — такой
                    # вызов баллов не стоит и в журнале учёта не получает.
                    units = self.units.observe(failure.headers)
                # Записывается и пережитая попытка: без неё сеть, которая
                # отвечает через раз, в журнале не видна вовсе — виден только
                # успех со второй попытки.
                self.journal.record({
                    **common, "at": _now(), "attempt": attempt,
                    "elapsed_ms": int((time.monotonic() - started) * 1000),
                    "units": units,
                    "error": {"kind": failure.kind, "message": str(failure)},
                })
                if retry and failure.retryable and not final:
                    self.retries.wait(attempt)
                    continue
                raise
            request_id = excerpt(header(received, "RequestId") or "", 64)
            failure = protocol.failure(payload, status, request_id, where, raw)
            response = Response(
                payload=payload, headers=received, status=status, service=key,
                method=method, version=protocol.version, units=units,
                request_id=request_id, error=failure, attempts=attempt,
                elapsed=time.monotonic() - started, protocol=protocol,
            )
            # Отказ здесь уже разобран конвертом: это наш объект, а не
            # тело ответа, и отсутствие кода у него — законный отказ без
            # кода, а не повреждённое значение.
            # ответ сюда не доходит: в журнал идёт разобранный отказ
            self.journal.record({
                **common, "at": _now(), "status": status, "attempt": attempt,
                "elapsed_ms": int(response.elapsed * 1000),
                "request_id": request_id or None, "units": units,
                "error": None if failure is None else {
                    "code": getattr(failure, "code", None), "kind": failure.kind,
                },
            })
            if failure is not None and retry and failure.retryable and not final:
                last_failure = failure
                self.retries.wait(attempt)
                continue
            if failure is not None and raise_on_error:
                raise failure
            return response
        # Цикл всегда возвращается или бросает на последней попытке. Строка
        # оставлена на случай правки условий выше: молчаливый None отсюда
        # выглядел бы как успешный ответ без данных.
        raise last_failure


# --------------------------------------------------------------------------
# Разбор результата
# --------------------------------------------------------------------------

def _decided(*, use_operator_units):
    """Решение об оплате: переданный ответ либо ответ переданного способа."""
    return use_operator_units() if callable(use_operator_units) else use_operator_units


def _sole_collection(result: dict, response: Response):
    """Имя выборки в ответе `get`, либо None, если выборки в ответе нет.

    Имя зависит от сервиса — `Campaigns`, `AdGroups`, `Ads`, `Clients`, — и
    угадывать его по имени сервиса нельзя: `agencyclients` отвечает `Clients`.
    Поэтому берётся единственный массив ответа, а неоднозначность — ошибка, а
    не выбор первого попавшегося.

    Отсутствие ключа ошибкой не считается: пустая выборка приходит как
    `{"result": {}}`, и назвать это поломкой значило бы падать на законном
    ответе «ничего не нашлось». А вот ключ, который есть, но массивом не
    является, — повреждённый ответ: `{"Campaigns": null}` иначе прочитался бы
    как «кампаний нет», и аудит по нулю кампаний выглядел бы как аудит по
    всем. С явным аргументом `collection` тот же ответ уже отвергается, и
    поведение одного метода не должно зависеть от того, назвал вызывающий код
    выборку или нет.

    Помощники `optional()` и `required()` здесь неприменимы: они читают поле,
    имя которого известно, а тут имя поля и есть то, что ищется."""
    names = []
    # ответ читается напрямую: имя выборки заранее неизвестно — оно зависит
    # от сервиса (`agencyclients` отвечает `Clients`), и спросить помощника
    # про поле, имени которого мы не знаем, нечем
    for name, value in result.items():
        if name in SERVICE_KEYS:
            continue
        if not isinstance(value, list):
            raise TransportFailure(
                f"{response.service}.{response.method}: ключ {name} пришёл не "
                f"массивом ({type(value).__name__}). Пустую выборку Директ "
                f"отдаёт ответом без ключа, поэтому это повреждённый ответ, а "
                f"не «ничего не нашлось».",
                retryable=False,
            )
        names.append(name)
    if len(names) == 1:
        return names[0]
    if not names:
        return None
    raise TransportFailure(
        f"{response.service}.{response.method}: в ответе несколько массивов "
        f"({', '.join(sorted(names))}). Какой из них выборка — решает "
        f"вызывающий код, аргумент collection.",
        retryable=False,
    )


def _sole_results_key(result: dict, where: str) -> str:
    """Имя массива результатов батча: `AddResults`, `UpdateResults` и так далее.

    Ключ с подходящим именем, но не массивом, пропускать нельзя по той же
    причине, что и в `_sole_collection`: рядом с повреждённым `AddResults`
    нашёлся бы годный `UpdateResults`, и батч отчитался бы не тем массивом."""
    names = []
    # ответ читается напрямую: имя массива результатов зависит от метода и
    # заранее неизвестно — помощник по имени поля здесь неприменим
    for name, value in result.items():
        if not name.endswith("Results"):
            continue
        if not isinstance(value, list):
            raise TransportFailure(
                f"{where}: {name} пришёл не массивом ({type(value).__name__}). "
                f"Считать батч выполненным нельзя.",
                retryable=False,
            )
        names.append(name)
    if len(names) == 1:
        return names[0]
    if not names:
        raise TransportFailure(
            f"{where}: в ответе нет массива результатов. Считать батч "
            f"выполненным нельзя.",
            retryable=False,
        )
    raise TransportFailure(
        f"{where}: в ответе несколько массивов результатов "
        f"({', '.join(sorted(names))}). Нужен аргумент results_key.",
        retryable=False,
    )


def _identifier(element, preferred: str):
    """Идентификатор созданного объекта и имя поля, из которого он взят.

    Поле называется по-разному: `Id` у большинства методов, `AdImageHash` у
    изображений. Жёстко зашитый `Id` приписывал бы каждой успешной загрузке
    изображения ошибку «элемент без идентификатора» и отдавал пустой список
    созданного.

    Поэтому список известных исключений — не единственная опора: если
    предпочтительного поля в элементе нет, а скалярное значение ровно одно,
    берётся оно. Иначе новый метод с новым именем поля ломался бы так же
    молча, как ломались изображения."""
    if not isinstance(element, dict):
        return None, None
    # Наличие ключа и годность значения — разные вопросы, и оба заданы явно:
    # `{"Id": null}` не идентификатор, но и не повод искать его в соседях
    # молча — элемент уйдёт в неопознанные, как и должен.
    #
    # ответ читается напрямую: имя поля идентификатора здесь и выясняется,
    # а помощники читают поле, имя которого уже известно
    chosen = _sole(element[preferred] if preferred in element else None)
    if chosen is not None:
        return chosen, preferred
    found_name = None
    found_value = None
    # ответ читается напрямую: обход ключей — единственный способ узнать имя
    # поля, которого нет в списке известных
    for name, item in element.items():
        if name in NOT_IDENTIFIERS:
            continue
        value = _sole(item)
        if value is None:
            continue
        if found_name is not None:
            # Два скаляра и ни одного знакомого имени: догадываться нельзя.
            return None, None
        found_name, found_value = name, value
    if found_name is None:
        return None, None
    return found_value, found_name


def _sole(value):
    """Идентификатор из значения элемента результата, либо `None`.

    Обычно это скаляр. Но `BidModifiers.add` отвечает структурой
    `MultiIdsActionResult` — массивом `Ids`, — потому что один элемент запроса
    может завести сразу несколько корректировок: двенадцать срезов по полу и
    возрасту уходят одним объектом.

    Годится **ровно один** идентификатор. Массив из двух и больше означает,
    что одному отправленному элементу отвечает несколько созданных объектов, и
    сопоставить их по позиции нечем: перечитывать и сверять конвейеру придётся
    каждый, а сказать, какой из них чей, ответ не позволяет. Такой элемент
    уходит в неопознанные — то есть в отказ с объяснением, — а не берётся
    первым попавшимся."""
    if isinstance(value, list):
        value = value[0] if len(value) == 1 else None
    return value if _scalar(value) else None


def _scalar(value) -> bool:
    """Значение, которым может быть идентификатор объекта."""
    if isinstance(value, bool) or value is None:
        return False
    return isinstance(value, (int, float, str)) and value != ""


def _page_number(value, what: str, low: int, high, default: int) -> int:
    """Значение `Page` из запроса вызывающего кода."""
    if value is None:
        return default
    if isinstance(value, bool) or not isinstance(value, int):
        raise DirectFailure(
            f"Page.{what} задан значением {excerpt(value, 32)} — ожидается целое."
        )
    if value < low or (high is not None and value > high):
        edge = f"от {low} до {high}" if high is not None else f"не меньше {low}"
        raise DirectFailure(f"Page.{what}={value} вне допустимого: {edge}.")
    return value


def _logins(logins, where: str) -> list:
    """Непустой перечень логинов для метода четвёртой версии."""
    if isinstance(logins, str):
        logins = [logins]
    names = [str(login) for login in logins if str(login).strip()]
    if not names:
        raise DirectFailure(f"{where}: не указан ни один логин, а он обязателен.")
    return names


def _records(response, where: str) -> list:
    """Массив записей из ответа четвёртой версии.

    `data` у этих методов — всегда массив: пустая выборка приходит как `[]`
    (замер 27.08.2026), а не как `null` и не как одиночный объект. Приводить
    непонятное значение к списку значило бы выдать за «ничего не нашлось»
    повреждённый ответ — и, наоборот, за одну запись то, что записью не было."""
    data = response.result
    if not isinstance(data, list):
        raise TransportFailure(
            f"{where}: поле data пришло не массивом ({type(data).__name__}).",
            retryable=False,
        )
    return data


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime())


__all__ = [
    "API_POINTS", "IDENTIFIER_FIELDS", "LOG_DIR", "PAGE_LIMIT_MAX",
    "UNITS_WARN_PARTS", "BatchEntry", "BatchResult", "Client", "Journal",
    "NoRedirect", "Response", "Retries", "Transport", "Units", "header",
    "thousands",
]
