"""Конверт запроса и разбор ответа: версия 5, версия 4 и отчёты."""

from __future__ import annotations

import json
import re

from config import DirectFailure, excerpt, header_safe
from errors import ApiFailure, TransportFailure

# `live/v4` — там, где у метода есть вариант Live; `v4` — где его нет.
# Пометодно расписано в API_MAP.md, раздел «Версии 4 и Live 4».
V4_PATHS = {"live/v4": "/live/v4/json/", "v4": "/v4/json/"}

# Имя сервиса подставляется в адрес запроса. Все сервисы Директа названы
# латиницей без разделителей, поэтому набор символов закрыт: иначе `../` в
# имени увело бы запрос вместе с токеном на соседний путь.
SERVICE_NAME = re.compile(r"^[a-z]+$")

CLIENT_LOGIN_REQUIRED = "required"
CLIENT_LOGIN_ADDRESSING = "addressing"
CLIENT_LOGIN_SHARED = "shared"
CLIENT_LOGIN_FORBIDDEN = "forbidden"

# Ключ — имя сервиса или `сервис.метод` строчными. Метод уточняет сервис:
# `Changes.checkDictionaries` отвечает про справочники и обходится без
# заголовка, а `Changes.check` и `Changes.checkCampaigns` — про кампании
# кабинета, и без заголовка отказывают. Правило на весь сервис здесь было бы
# неверно ровно наполовину.
CLIENT_LOGIN = {
    "agencyclients": CLIENT_LOGIN_FORBIDDEN,
    "clients": CLIENT_LOGIN_ADDRESSING,
    "dictionaries": CLIENT_LOGIN_SHARED,
    "changes.checkdictionaries": CLIENT_LOGIN_SHARED,
    "keywordsresearch.hassearchvolume": CLIENT_LOGIN_SHARED,
}

# Правила, при которых заголовок не отправляется. `shared` и `forbidden`
# приводят к одному действию, но по разным причинам, и слить их в одно значение
# значило бы потерять причину: у первого заголовок допустим и просто невыгоден,
# у второго — ломает запрос.
CLIENT_LOGIN_SILENT = frozenset({CLIENT_LOGIN_SHARED, CLIENT_LOGIN_FORBIDDEN})


# Методы, повтор которых безопасен сам по себе. Повтор записи создаёт дубли:
# ключей идемпотентности в API Директа нет, и после разрыва на `add` мы не
# знаем, дошёл запрос или нет. Поэтому автоматический повтор — только для
# чтения, а для записи вызывающий код передаёт `retry=True` сам, когда знает,
# что предыдущая попытка не состоялась.
SAFE_METHODS = frozenset({
    "get",
    "getgeoregions",
    "check",
    "checkdictionaries",
    "checkcampaigns",
    "hassearchvolume",
})

SAFE_METHODS_V4 = frozenset({
    "getclientsunits",
    "getstatgoals",
    "getretargetinggoals",
    "getregions",
    "gettimezones",
    "getrubrics",
    "getavailableversions",
    "getversion",
    "pingapi",
    "geteventslog",
    "getbannerstags",
    "getcampaignstags",
})


def service_key(service: str) -> str:
    """Имя сервиса в том виде, в каком оно идёт в адрес и в справочные наборы.

    Приведение одно на всех, потому что по этому же имени ищутся исключения:
    `adimages/` и `AdImages` обязаны попадать и в тот же адрес, и в ту же
    строку `IDENTIFIER_FIELDS`. Два разных приведения в двух местах и означали
    бы, что заголовок сняли, а поле идентификатора нет."""
    key = str(service).strip().strip("/").lower()
    if not SERVICE_NAME.match(key):
        raise DirectFailure(
            f"Неизвестный сервис «{excerpt(service, 48)}»: имя сервиса Директа "
            f"записывается латиницей без разделителей."
        )
    return key


def client_login_rule(service: str, method: str) -> str:
    """Как этот вызов относится к заголовку `Client-Login`.

    Сначала ищется уточнение по методу, потом правило сервиса: иначе
    `Changes.checkDictionaries` получил бы правило `Changes`, а оно про другое."""
    key = service_key(service) if service else ""
    named = f"{key}.{str(method).strip().lower()}"
    if named in CLIENT_LOGIN:
        return CLIENT_LOGIN[named]
    return CLIENT_LOGIN.get(key, CLIENT_LOGIN_REQUIRED)


def sends_client_login(service: str, method: str) -> bool:
    """Уходит ли заголовок в этом вызове, если кабинет задан."""
    return client_login_rule(service, method) not in CLIENT_LOGIN_SILENT


class Protocol:
    """Общая часть: разбор тела и отказ по форме ответа."""

    version = ""
    payload_key = ""
    counts_units = False

    def request(self, settings, *, service, method, params, account, use_operator_units):
        """Адрес, тело и заголовки одного вызова."""
        raise NotImplementedError

    def safe(self, method: str) -> bool:
        """Безопасен ли повтор этого метода сам по себе."""
        raise NotImplementedError

    def error_of(self, payload, request_id: str, where: str):
        """Отказ, объявленный самим Директом, или None."""
        raise NotImplementedError

    def parse(self, raw: str, status: int, where: str):
        """Тело ответа как объект Python."""
        try:
            return json.loads(raw)
        except (ValueError, RecursionError):
            # ValueError, а не JSONDecodeError: синтаксически верный JSON тоже
            # разбирается не всегда — целое длиннее 4300 цифр Python отвергает
            # отдельной ошибкой, а глубокая вложенность даёт RecursionError,
            # который вовсе не потомок ValueError.
            raise TransportFailure(
                f"{where}: ответ не разбирается как JSON (HTTP {status}): "
                f"{excerpt(raw) or 'пустое тело'}",
                retryable=status >= 500 or status == 429,
                status=status,
            ) from None

    def failure(self, payload, status: int, request_id: str, where: str, raw: str):
        """Отказ уровня запроса или None.

        Отказом считается сам объект ошибки, а не распознанный в нём код:
        ответ с `error` без `error_code` иначе выдавался бы за успех."""
        if isinstance(payload, dict):
            declared = self.error_of(payload, request_id, where)
            if declared is not None:
                return declared
        if status >= 400:
            return TransportFailure(
                f"{where}: HTTP {status} без описания ошибки: "
                f"{excerpt(raw) or 'пустое тело'}",
                retryable=status >= 500 or status == 429,
                status=status,
            )
        if not isinstance(payload, dict):
            return TransportFailure(
                f"{where}: ответ пришёл не объектом ({type(payload).__name__}).",
                retryable=False, status=status,
            )
        if self.payload_key not in payload:
            return TransportFailure(
                f"{where}: успешный ответ без {self.payload_key} и без ошибки: "
                f"{excerpt(raw) or 'пустое тело'}",
                retryable=False, status=status,
            )
        return None

    def data(self, payload):
        """Полезная часть ответа."""
        return payload.get(self.payload_key) if isinstance(payload, dict) else None


class V5(Protocol):
    """`https://api.direct.yandex.com/json/v501/{сервис}/`."""

    version = "v501"
    payload_key = "result"
    counts_units = True

    def request(self, settings, *, service, method, params, account, use_operator_units):
        key = service_key(service)
        url = f"https://{settings.host}/json/{settings.version}/{key}/"
        body = json.dumps(
            {"method": method, "params": params if params is not None else {}},
            ensure_ascii=False,
        )
        headers = {
            "Authorization": f"Bearer {settings.token}",
            "Accept-Language": settings.locale,
            "Content-Type": "application/json; charset=utf-8",
        }
        login = header_safe(str(account or "").strip(), "Логин кабинета")
        if login and sends_client_login(key, method):
            headers["Client-Login"] = login
            if use_operator_units is None:
                # `auto` зависит от остатка баллов клиента, а узнаётся он
                # платным вызовом — решает его `accounts.use_operator_units` и
                # передаёт сюда готовым ответом. Без такого ответа auto равен
                # never: гадать за вызывающий код, чьими баллами платить,
                # дороже, чем не заплатить чужими.
                use_operator_units = settings.operator_units == "always"
            if use_operator_units:
                # Заголовок допустим только в запросах от имени агентства,
                # то есть только вместе с Client-Login. Замер 28.08.2026: без
                # Client-Login Директ его не отвергает, но и смысла в нём нет —
                # платит агентство и так.
                headers["Use-Operator-Units"] = "true"
        return url, body, headers

    def safe(self, method: str) -> bool:
        return method.lower() in SAFE_METHODS

    def error_of(self, payload, request_id: str, where: str):
        error = payload.get("error")
        if error is None:
            return None
        if not isinstance(error, dict):
            # Тело вида `{"error": "Forbidden"}` от шлюза — тоже отказ; приведём
            # к словарю, иначе он пройдёт за успешный ответ без данных.
            error = {"error_detail": str(error)}
        return ApiFailure(
            error.get("error_code"),
            message=error.get("error_string") or "",
            detail=error.get("error_detail") or "",
            request_id=error.get("request_id") or request_id,
            where=where, version="v5", raw=error,
        )


# Режимы формирования отчёта: заголовок `processingMode` сервиса `Reports`.
# `auto` равен отсутствию заголовка, но отправляется явно: умолчание сервера
# и наше умолчание — разные утверждения, и второе видно в журнале вызовов.
PROCESSING_MODES = ("online", "offline", "auto")

# Коды, которыми `Reports` отвечает «отчёт ещё не ваш»: 201 — поставлен в
# очередь, 202 — ещё формируется. Данных в таком ответе нет, и тело пустое.
QUEUED = (201, 202)


class Reports(V5):
    """`https://api.direct.yandex.com/json/v501/reports` — TSV вместо JSON.

    Свой конверт, а не ветка в `V5`: сервис расходится с остальными в трёх
    местах, и каждое ломает читателя, написанного по общему правилу.

    **Тело без `method`.** У сервиса один вход, и параметры лежат прямо в
    `params`; ключ `method` рядом с ними Директ считает ошибкой запроса.

    **Ответ — TSV.** `Format` других значений не принимает, и `json.loads`
    общего разбора на отчёте падает. Ошибка при этом приходит по-прежнему
    объектом `{"error": {...}}`, поэтому тело разбирается по коду ответа: с
    400 и выше — как JSON, ниже — как текст отчёта.

    **Коды 201 и 202 — не данные и не ошибка.** `urllib` считает успехом
    любой ответ ниже 300 и отдаёт их обычным путём, а тело у них пустое.
    Цикл ожидания, построенный на исключении, такого ответа не заметит вовсе
    и отдаст пустой файл за готовый отчёт. Поэтому пустое тело при HTTP 200
    здесь объявлено отказом: скилл не отправляет ни одного из трёх заголовков
    `skip*`, и даже пустой отчёт приходит с шапкой, именами столбцов и
    строкой `Total rows`.

    Заголовок сжатия (`Accept-Encoding: gzip`) не отправляется: `urllib` сам
    не распаковывает ответ, и в разбор TSV пришли бы сжатые байты. Справочник
    называет его среди возможных, а не обязательных — см.
    `defaults.omitted_request_headers` в `references/report_presets.json`."""

    def __init__(self, processing_mode: str = "auto"):
        mode = str(processing_mode or "auto").strip().lower()
        if mode not in PROCESSING_MODES:
            raise DirectFailure(
                f"Режим формирования отчёта «{excerpt(processing_mode, 32)}» "
                f"неизвестен. Допустимо: {', '.join(PROCESSING_MODES)}."
            )
        self.processing_mode = mode

    def request(self, settings, *, service, method, params, account, use_operator_units):
        url, _, headers = super().request(
            settings, service=service, method=method, params=params,
            account=account, use_operator_units=use_operator_units,
        )
        # Адрес без завершающего слэша — тот, что публикует справочник
        # (`references/REPORTS.md`, раздел 2). Собирается он здесь, а не
        # правится у родителя: у остальных сервисов слэш на месте.
        url = url.rstrip("/")
        body = json.dumps(
            {"params": params if params is not None else {}}, ensure_ascii=False,
        )
        headers["processingMode"] = self.processing_mode
        return url, body, headers

    def safe(self, method: str) -> bool:
        """Повтор безопасен: сервис только читает.

        Больше того, повтор **того же** запроса и есть механизм ожидания
        готовности (раздел 9 справочника). Метод здесь — тип отчёта, и
        перечислять его в `SAFE_METHODS` было бы перечислением типов отчётов."""
        return True

    def parse(self, raw: str, status: int, where: str):
        """Тело: отчёт остаётся текстом, отказ разбирается как JSON.

        Отказ, который не разобрался, возвращается текстом, а не роняет
        разбор: `failure` скажет о нём по коду ответа, а сырое тело попадёт в
        сообщение. Иначе HTML от промежуточного шлюза вместо ответа Директа
        читался бы как «ответ не разбирается как JSON» — и про код 502, у
        которого своё продолжение, не сказал бы ничего."""
        if status >= 400:
            try:
                return json.loads(raw)
            except (ValueError, RecursionError):
                return raw
        return raw

    def failure(self, payload, status: int, request_id: str, where: str, raw: str):
        if status == 502:
            # Своё продолжение: справочник велит повторить в режиме `offline`,
            # и решает это вызывающий код. Повтор того же запроса тем же
            # режимом смысла не имеет, поэтому отказ не повторяемый.
            return TransportFailure(
                f"{where}: HTTP 502 — Директ не уложился в серверное "
                f"ограничение на время обработки. Тот же отчёт формируется "
                f"в режиме offline.",
                retryable=False, status=status,
            )
        if status == 500:
            # Справочник велит повторить такой отказ **один раз с нуля**
            # (раздел 9), и повтор этот делает ожидание готовности. Ответ при
            # этом приходит обычным телом ошибки, и разбери мы его в
            # `ApiFailure`, повторять было бы нечему: ожидание ловит отказы
            # транспорта, а не отказы, объявленные Директом. Текст объяснения
            # сохраняется — теряется только повод считать отказ окончательным.
            declared = (self.error_of(payload, request_id, where)
                        if isinstance(payload, dict) else None)
            return TransportFailure(
                f"{where}: HTTP 500 при формировании отчёта"
                + (f": {excerpt(str(declared), 200)}" if declared is not None
                   else f": {excerpt(raw) or 'пустое тело'}"),
                retryable=True, status=status,
            )
        if isinstance(payload, dict):
            declared = self.error_of(payload, request_id, where)
            if declared is not None:
                return declared
        if status >= 400:
            return TransportFailure(
                f"{where}: HTTP {status} без описания ошибки: "
                f"{excerpt(raw) or 'пустое тело'}",
                retryable=status >= 500 or status == 429, status=status,
            )
        if status in QUEUED:
            # Очередь: данных нет и быть не должно. Отличает готовый отчёт от
            # очереди только код ответа — по телу они неразличимы.
            return None
        if status != 200:
            return TransportFailure(
                f"{where}: неизвестный код ответа {status}. Сервис отвечает "
                f"200, {', '.join(str(code) for code in QUEUED)}, 400, 500 "
                f"или 502.",
                retryable=False, status=status,
            )
        if not isinstance(payload, str) or not payload.strip():
            return TransportFailure(
                f"{where}: HTTP 200 с пустым телом. Скилл не отключает ни "
                f"одной служебной строки отчёта, поэтому даже пустой отчёт "
                f"приходит с шапкой, именами столбцов и строкой «Total rows». "
                f"Пустое тело здесь — это ответ очереди, прочитанный как "
                f"готовый отчёт.",
                retryable=False, status=status,
            )
        return None

    def data(self, payload):
        """Отчёт целиком. У отказа полезной части нет — там объект ошибки."""
        return payload if isinstance(payload, str) else None


class V4(Protocol):
    """`https://api.direct.yandex.ru/live/v4/json/` и `/v4/json/`.

    Хост свой: справочник даёт четвёртой версии `api.direct.yandex.ru`, тогда
    как версия 5 живёт на `api.direct.yandex.com`. Отвечают оба, адрес берётся
    документированный — расхождение D-08.

    Кабинет адресуется не заголовком, а полем параметров (`Logins` у
    `GetRetargetingGoals`, массив логинов у `GetClientsUnits`), поэтому
    `Client-Login` и `Use-Operator-Units` здесь не отправляются вовсе."""

    payload_key = "data"
    counts_units = False

    def __init__(self, version: str):
        self.version = version
        self.path = V4_PATHS[version]

    def request(self, settings, *, service, method, params, account, use_operator_units):
        url = f"https://{settings.host_v4}{self.path}"
        # Токен уходит в теле, а не в заголовке. Отсюда требование к журналу:
        # вырезать секреты из тела, а не только из заголовков.
        body = json.dumps(
            {
                "method": method,
                "param": params if params is not None else {},
                "locale": settings.locale,
                "token": settings.token,
            },
            ensure_ascii=False,
        )
        return url, body, {"Content-Type": "application/json; charset=utf-8"}

    def safe(self, method: str) -> bool:
        return method.lower() in SAFE_METHODS_V4

    def error_of(self, payload, request_id: str, where: str):
        if "error_code" not in payload and "error_str" not in payload:
            return None
        return ApiFailure(
            payload.get("error_code"),
            message=payload.get("error_str") or "",
            detail=payload.get("error_detail") or "",
            request_id=request_id, where=where, version="v4", raw=payload,
        )


def protocols() -> dict:
    """Набор протоколов по имени версии."""
    return {"v501": V5(), "live/v4": V4("live/v4"), "v4": V4("v4")}
