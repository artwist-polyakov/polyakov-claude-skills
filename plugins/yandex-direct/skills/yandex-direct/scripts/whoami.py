#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""Проверка доступа к API Яндекс Директа."""

from __future__ import annotations

import argparse
import http.client
import json
import math
import sys
import traceback
import urllib.error
import urllib.request
from pathlib import Path

# Строго до импортов: каталог запускаемого файла лежит на пути импорта первым, и
# без этой строки `config` из `scripts/lib` не находится вовсе.
sys.path.insert(0, str(Path(__file__).resolve().parent / "lib"))

from config import (  # noqa: E402  — путь импорта задаётся строкой выше
    PROFILES,
    DirectFailure,
    Settings,
    excerpt,
    preload_secrets,
    redact,
    settings_from_env,
)

TIMEOUT = 30

# «Токен не агентский» приходит именно этим кодом — на нём стоит определение
# типа токена. Остальные разобранные коды нужны, чтобы вместо сырого номера
# показать человеку, что делать.
ERROR_NO_RIGHTS = 54
ERROR_HINTS = {
    52: "Сервер авторизации Яндекса временно недоступен. Повторите запрос позже.",
    53: (
        "Директ не принял токен: он неверен, отозван или истёк. Токен живёт около "
        "года — получите новый по инструкции из config/README.md, шаг 3."
    ),
    58: (
        "Регистрация не завершена. Чаще всего это значит, что для приложения "
        "не подана или ещё не одобрена заявка на доступ к API: вкладка «Мои "
        "заявки» в настройках API Директа, рассмотрение — от часа до трёх "
        "рабочих суток. Точная причина — в строке «Директ:» ниже, шаги — "
        "в config/README.md."
    ),
    152: (
        "Баллы кабинета исчерпаны. Суточный лимит зависит от оборота кабинета; "
        "дождитесь обновления лимита или работайте под другим кабинетом."
    ),
    513: (
        "Логин не подключен к Яндекс Директу: рекламного кабинета под ним нет. "
        "Проверьте, тем ли логином выдан токен."
    ),
}

# Ниже этой доли суточного лимита пора предупреждать: падать по факту
# исчерпания баллов посреди пакета записи дороже, чем узнать заранее.
# Доля записана целыми: лимит приходит от Директа, а очень большое целое при
# умножении на дробь не переводится во float и валит работу OverflowError.
UNITS_WARN_PARTS = 10


def say(text: str = "") -> None:
    """Печать в stdout.

    Единственный путь вывода: секреты вырезаются здесь, а не в каждом месте,
    где что-то печатается. Пропущенный вызов redact() — не описка, а утечка,
    и полагаться на внимательность в этом месте нельзя."""
    print(redact(text))


def warn(text: str) -> None:
    """Печать в stderr: предупреждения и ошибки. Секреты вырезаются так же."""
    print(redact(text), file=sys.stderr)


# --------------------------------------------------------------------------
# Транспорт
# --------------------------------------------------------------------------

def call(settings: Settings, service: str, params: dict, client_login: str = "") -> tuple:
    """Один запрос к API v5. Возвращает (тело ответа, заголовки).

    Повторов здесь нет намеренно: проверка связи существует, чтобы показать
    проблему, а не пережить её."""
    url = f"https://{settings.host}/json/{settings.version}/{service}/"
    body = json.dumps({"method": "get", "params": params}).encode("utf-8")
    headers = {
        "Authorization": f"Bearer {settings.token}",
        "Accept-Language": settings.locale,
        "Content-Type": "application/json; charset=utf-8",
    }
    if client_login:
        headers["Client-Login"] = client_login
        # auto зависит от остатка баллов кабинета, а он известен только после
        # запроса к этому кабинету. Одиночная проверка такой петли не делает,
        # поэтому здесь auto равен never.
        if settings.operator_units == "always":
            headers["Use-Operator-Units"] = "true"

    request = urllib.request.Request(url, data=body, headers=headers, method="POST")
    try:
        with OPENER.open(request, timeout=TIMEOUT) as response:
            raw = response.read().decode("utf-8", "replace")
            received = response.headers
            status = response.status
    except urllib.error.HTTPError as exc:
        received = exc.headers
        status = exc.code
        try:
            raw = exc.read().decode("utf-8", "replace")
        except (http.client.HTTPException, OSError) as broken:
            # Исключение, брошенное внутри except, соседними ветками не
            # ловится: сервер отдал код отказа и оборвал соединение на теле,
            # и без этой обёртки пользователь получал трассировку.
            raise DirectFailure(
                f"{settings.host} ответил HTTP {status}, но соединение "
                f"оборвалось при чтении тела ответа: "
                f"{excerpt(str(broken) or type(broken).__name__, 120)}. "
                f"Проверьте сеть и повторите."
            ) from None
    except TimeoutError:
        raise DirectFailure(
            f"{settings.host} не ответил за {TIMEOUT} с. Повторите позже."
        ) from None
    except (urllib.error.URLError, http.client.HTTPException, OSError) as exc:
        # urllib оборачивает в URLError только отправку запроса: чтение ответа
        # остаётся снаружи, и обрыв соединения приходит как HTTPException.
        # Без этой ветки он выходил наружу трассировкой мимо redact().
        reason = getattr(exc, "reason", None) or exc or type(exc).__name__
        raise DirectFailure(
            f"Нет связи с {settings.host}: {excerpt(str(reason), 120)}. "
            f"Проверьте сеть и доступность контура."
        ) from None

    if 300 <= status < 400:
        # redirect_request вернул None, и urllib отдал ответ как ошибку.
        raise DirectFailure(
            f"{settings.host} ответил перенаправлением (HTTP {status}). "
            f"Скилл им не следует: вместе с адресом ушёл бы и токен. "
            f"Проверьте, не подменяет ли ответы промежуточный прокси."
        )

    try:
        payload = json.loads(raw)
    except (ValueError, RecursionError):
        # ValueError, а не JSONDecodeError: синтаксически верный JSON тоже
        # разбирается не всегда — целое длиннее 4300 цифр Python отвергает
        # отдельной ошибкой. RecursionError даёт глубокая вложенность, и он
        # вовсе не потомок ValueError. Оба до правки выходили трассировкой.
        raise DirectFailure(
            f"{settings.host} ответил телом, которое не разбирается как JSON "
            f"(HTTP {status}): {excerpt(raw) or 'пустое тело'}"
        ) from None
    if not isinstance(payload, dict):
        raise DirectFailure(f"{settings.host} вернул неожиданный ответ (HTTP {status})")
    if direct_error(payload) is None:
        # Иначе отказ транспорта или пустое тело проходят за успешный ответ:
        # у всех вызываемых здесь методов успех — это всегда `result`.
        if status >= 400:
            raise DirectFailure(
                f"{settings.host} ответил HTTP {status} без описания ошибки: "
                f"{excerpt(raw) or 'пустое тело'}"
            )
        # Именно `isinstance`, а не наличие ключа: `{"result": ["…"]}` —
        # разбираемый JSON, на котором разбор ответа падает трассировкой
        # вместо человеческого сообщения.
        if not isinstance(payload.get("result"), dict):
            raise DirectFailure(
                f"{settings.host} ответил без разбираемого результата и без "
                f"ошибки (HTTP {status}): {excerpt(raw) or 'пустое тело'}"
            )
    return payload, received


def direct_error(payload: dict):
    """Объект ошибки Директа или None, если ошибки нет.

    Ошибка, пришедшая не словарём, — тоже ошибка: тело вида
    `{"error": "Forbidden"}` от шлюза приводится к словарю, иначе отказ
    проходит за успешный ответ без данных."""
    error = payload.get("error")
    if error is None:
        return None
    return error if isinstance(error, dict) else {"error_detail": str(error)}


def error_code(error) -> int:
    """Числовой код ошибки или 0, если кода нет или он не число."""
    if not error:
        return 0
    try:
        value = error["error_code"]
        # Код ошибки — целое число ответа, и только оно. Строка «54», дробное
        # 54.9 и логическое через int() превращались в 54, а на коде 54
        # держится вывод о типе токена: повреждённый ответ сходил за признак
        # клиентского. Дробное отвергается целиком, а не по признаку целости:
        # 54.000000000000001 json.loads округляет до 54.0, и проверка на
        # целость его пропускает.
        if isinstance(value, bool) or not isinstance(value, int):
            return 0
        return value
    except (KeyError, TypeError, ValueError, OverflowError):
        # OverflowError даёт `1e1000`: json.loads делает из него бесконечность,
        # а int() её не берёт. Код при этом не распознан, но объект ошибки на
        # месте — отказом он быть не перестаёт.
        return 0


def raise_for_error(payload: dict, service: str) -> None:
    """Отказом считается сам объект `error`, а не распознанный код.

    Ответ с `error` без поля `error_code` иначе выдавался за успех: код
    возврата 0 и «связь есть» там, где доступа нет."""
    error = direct_error(payload)
    if error is None:
        return
    code = error_code(error)
    hint = ERROR_HINTS.get(code)
    said = " ".join(
        str(error.get(field, "")).strip()
        for field in ("error_string", "error_detail")
    ).strip()
    if hint:
        lines = [hint]
    else:
        shown = excerpt(str(error.get("error_code", "без кода")), 32)
        lines = [f"{service}: Директ вернул ошибку {shown}."]
    # Без error_string и error_detail от отказа не остаётся ничего, кроме
    # слова «ошибка», поэтому показывается сам объект.
    lines.append(f"Директ: {excerpt(said) if said else excerpt(json.dumps(error, ensure_ascii=False))}")
    # Идентификатор тоже приходит от Директа и тоже ничем не ограничен.
    request_id = excerpt(str(error.get("request_id", "")), 64)
    if request_id:
        lines.append(f"Идентификатор запроса: {request_id}")
    raise DirectFailure("\n".join(lines))


def read_units(received, before: dict) -> dict:
    """Баллы из заголовка `Units: израсходовано/остаток/суточный лимит`.

    Поле `login` — чьи баллы списаны последним запросом; под агентским токеном
    с заголовком `Client-Login` это может быть логин клиента, а не агентства.

    Расход переносится из предыдущего запроса только при совпадении кошелька.
    Под агентским токеном с `--account` первый запрос идёт без `Client-Login` и
    оплачивается агентством, второй — клиентом. Общая сумма приписывала клиенту
    чужие баллы: «проверка стоила 3 005» рядом с его же суточным лимитом 2 000."""
    raw = received.get("Units") or ""
    login = excerpt(str(received.get("Units-Used-Login") or ""), 64)
    same_wallet = (
        bool(login) and bool(before["login"])
        and login.lower() == before["login"].lower()
    )
    carried = before["spent"] if same_wallet else 0
    units = {
        "spent": carried,
        "left": None,
        "limit": None,
        "login": login,
        # Отсутствующий заголовок и испорченный — разные новости: первое
        # означает, что Директ его не прислал, второе — что прислал мусор.
        "header": "missing" if not raw else "broken",
    }
    parts = raw.split("/")
    if len(parts) != 3:
        return units
    try:
        spent, left, limit = (int(part.strip()) for part in parts)
    except ValueError:
        return units
    # Целое — ещё не число баллов. Отрицательный расход уменьшал бы стоимость
    # проверки, отрицательный остаток печатался бы как «осталось -1», а нулевой
    # суточный лимит делает долю бессмысленной. Такой заголовок считается
    # испорченным: лучше сказать «неизвестны», чем показать выдуманное.
    if spent < 0 or left < 0 or limit <= 0:
        return units
    units["header"] = "ok"
    units.update({"spent": carried + spent, "left": left, "limit": limit})
    return units


# --------------------------------------------------------------------------
# Сбор отчёта
# --------------------------------------------------------------------------

CLIENT_FIELDS = ["Login", "ClientId", "ClientInfo", "Currency", "Type"]


def clients_of(payload: dict, service: str) -> list:
    """Перечень кабинетов из ответа.

    Проверка нужна на каждом уровне отдельно: разбираемый JSON верхнего уровня
    ничего не обещает про вложенное значение, а `.get` у строки внутри
    `Clients` падает трассировкой вместо человеческого сообщения."""
    clients = (payload.get("result") or {}).get("Clients")
    if clients is None:
        # Пустая выборка приходит без ключа: кабинетов нет — это не поломка.
        return []
    # Проверять надо исходное значение, а не нормализованное: `or []`
    # превращает в допустимый пустой список любое ложное значение неверного
    # типа — `{}`, `false`, `0`, `""`, — и повреждённый ответ становится
    # сообщением «дочерних кабинетов ноль».
    if not isinstance(clients, list) or not all(isinstance(item, dict) for item in clients):
        raise DirectFailure(
            f"{service}: Директ вернул перечень кабинетов в неожиданном виде. "
            f"Повторите проверку; если повторяется — дело на стороне Директа "
            f"или промежуточного прокси."
        )
    # Логин запрошен в FieldNames, поэтому он обязан быть у каждой записи.
    # Без этой проверки `str(None)` даёт кабинет с логином «None», а пустая
    # запись — «логин не определён»: оба выглядят успехом.
    if any(not isinstance(item.get("Login"), str) or not item["Login"].strip() for item in clients):
        raise DirectFailure(
            f"{service}: Директ вернул кабинет без логина. Данные неполны, "
            f"повторите проверку."
        )
    return clients


def scalar(value, service: str, field: str):
    """Значение поля, которое печатается как есть.

    Проверяется скалярность, а не конкретный числовой тип: дефект здесь в том,
    что массив или объект в поле раздувает машиночитаемый отчёт на сотню строк,
    а не в том, что идентификатор пришёл строкой. Строгая проверка на число
    сломала бы работу от косметического расхождения."""
    if isinstance(value, float) and not math.isfinite(value):
        # json.loads принимает NaN и Infinity, которых в стандартном JSON нет,
        # и json.dumps возвращает их же: машиночитаемый вывод перестаёт
        # разбираться строгим разборщиком на той стороне.
        raise DirectFailure(
            f"{service}: поле {field} пришло значением {excerpt(str(value), 32)}, которого в "
            f"стандартном JSON не существует. Ответ повреждён."
        )
    if value is None or isinstance(value, (int, float)) and not isinstance(value, bool):
        return value
    if isinstance(value, str):
        return excerpt(value, 64)
    raise DirectFailure(
        f"{service}: поле {field} пришло не скалярным значением "
        f"({type(value).__name__}). Ответ повреждён, повторите проверку."
    )


def cabinet_of(payload: dict, service: str) -> dict:
    clients = clients_of(payload, service)
    if not clients:
        return {}
    if len(clients) > 1:
        # Метод отвечает про один кабинет — адресованный заголовком или свой.
        # Брать первый из нескольких значило бы определять личность кабинета
        # порядком записей в чужом ответе.
        raise DirectFailure(
            f"{service}: запрошен один кабинет, а Директ вернул "
            f"{len(clients)}. Ответ не соответствует запросу."
        )
    client = clients[0]
    # Поля кабинета приходят от Директа и в сводку печатаются как есть.
    # Перенос строки в названии кабинета растягивает шесть строк отчёта на
    # десятки — предел вывода нарушается на совершенно успешном запросе.
    return {
        "login": excerpt(str(client.get("Login", "")), 64),
        "client_id": scalar(client.get("ClientId"), service, "ClientId"),
        "name": excerpt(str(client.get("ClientInfo", "")), 120),
        "currency": excerpt(str(client.get("Currency", "")), 16),
        "type": excerpt(str(client.get("Type", "")), 32),
    }


def collect(settings: Settings) -> dict:
    units = {"spent": 0, "left": None, "limit": None, "login": "", "header": "missing"}

    # Порядок важен: «нет прав» на AgencyClients.get — дешёвый и надёжный
    # признак клиентского токена. Обратный порядок заставил бы гадать.
    # решение об оплате не нужно: это не клиент из `lib/direct.py`, а свой
    # минимальный транспорт этой команды — она обязана работать до появления
    # ядра, иначе настройку нечем проверить. Резолвера кабинетов здесь нет и
    # быть не может, а `Client-Login` в этом вызове не отправляется вовсе.
    agency, received = call(
        settings,
        "agencyclients",
        {"SelectionCriteria": {"Archived": "NO"}, "FieldNames": ["Login"]},
    )
    units = read_units(received, units)
    error = direct_error(agency)
    is_agency = error is None or error_code(error) != ERROR_NO_RIGHTS
    if is_agency:
        raise_for_error(agency, "AgencyClients.get")

    report = {
        "env": settings.profile,
        "host": settings.host,
        "token_var": settings.token_var,
        "token_borrowed": settings.token_borrowed,
        "agency": is_agency,
        "login": units["login"],
        "cabinet": {},
        "children": None,
    }

    if is_agency:
        result = agency.get("result") or {}
        # Полный перечень дочерних кабинетов — задача отдельной команды со
        # своим кэшем. Здесь берётся одна страница, и если она не последняя,
        # это сказано вслух, а не выдано за точное число.
        report["children"] = {
            "count": len(clients_of(agency, "AgencyClients.get")),
            "complete": "LimitedBy" not in result,
        }

    if is_agency and not settings.account:
        return finish(report, units)

    client_login = settings.account if is_agency else ""
    # решение об оплате не нужно: тот же свой транспорт. Заголовок баллов
    # агентства он ставит сам и только в режиме `always`; `auto` здесь равен
    # `never` осознанно — остаток кабинета известен лишь после запроса к нему,
    # а одиночная проверка такой петли не делает.
    clients, received = call(
        settings, "clients", {"FieldNames": CLIENT_FIELDS}, client_login=client_login
    )
    units = read_units(received, units)
    if not is_agency and error_code(direct_error(clients)) == ERROR_NO_RIGHTS:
        # «Не агентский» — гипотеза, а не вывод: код 54 на AgencyClients.get
        # документация объясняет ещё и переводом аккаунта в валюту, и
        # приостановкой доступа. Подтвердить её должен успешный Clients.get;
        # отказ там же означает, что дело не в типе токена.
        raise DirectFailure(
            "Директ отказал в правах и на AgencyClients.get, и на Clients.get — "
            "дело не в типе токена. Так отвечает аккаунт, который ждёт перевода "
            "в валюту, и аккаунт с приостановленным доступом. Проверьте "
            "состояние кабинета в интерфейсе Директа."
        )
    raise_for_error(clients, "Clients.get")
    cabinet = cabinet_of(clients, "Clients.get")
    if not cabinet:
        raise DirectFailure(
            "Директ не вернул сведений о кабинете. "
            "Проверьте, что логин указан верно и доступ к нему не отозван."
        )
    report["cabinet"] = cabinet
    # Тип токена выведен из кода 54, а Директ прислал тип кабинета отдельным
    # полем. Когда они спорят, молчать нельзя: вердикт «обычный» держится на
    # гипотезе, а поле Type — это ответ самого Директа.
    if cabinet["type"] == "AGENCY" and not is_agency:
        warn(
            f"Директ сообщает тип кабинета AGENCY, а по правам токен выглядит "
            f"клиентским. Сведения расходятся; вердикт о типе кабинета в "
            f"отчёте выведен из прав, а не из этого поля."
        )
    # Только для своего кабинета: под агентским токеном с --account это чужой
    # логин, и выдавать его за логин токена нельзя — тогда и оговорка «чьи
    # баллы» замолчит, решив, что кабинет тот же самый.
    if not report["login"] and not is_agency:
        report["login"] = cabinet["login"]

    requested = settings.account
    if requested and requested.lower() != cabinet["login"].lower():
        if not is_agency:
            raise DirectFailure(
                f"Токен клиентский: под ним доступен только кабинет "
                f"{cabinet['login']}, а запрошен {excerpt(requested, 64)}. "
                f"Для чужих кабинетов нужен агентский токен."
            )
        # Под агентским токеном кабинет адресован заголовком Client-Login, и
        # ответ обязан быть про него. Иначе отчёт уверенно описывает не тот
        # кабинет, о котором спрашивали, — и код возврата остаётся нулевым.
        raise DirectFailure(
            f"Запрошен кабинет {excerpt(requested, 64)}, а Директ вернул "
            f"{cabinet['login']}. Ответ не соответствует запросу; возможно, "
            f"его подменил промежуточный прокси."
        )
    return finish(report, units)


def finish(report: dict, units: dict) -> dict:
    """Последние баллы и отчёт целиком.

    Логин здесь не заполняется: у второго запроса он приходит из ответа,
    отправленного с `Client-Login`, то есть принадлежит чужому кабинету.
    Логин токена берётся только из первого запроса — он идёт без адресации."""
    report["units"] = units
    return report


# --------------------------------------------------------------------------
# Вывод
# --------------------------------------------------------------------------

def thousands(value: int) -> str:
    return f"{value:,}".replace(",", " ")


def describe_units(units: dict, token_login: str = "") -> str:
    """Строка про баллы.

    Остаток берётся из последнего ответа, а под агентским токеном с
    `Client-Login` это баллы клиента, а не агентства. Когда логины расходятся,
    сказано чьи — иначе цифра читается как остаток токена."""
    spent = f"проверка стоила {thousands(units['spent'])}"
    whose = units["login"]
    if units["left"] is None and units["header"] == "broken":
        return "неизвестны: Директ прислал заголовок Units в непонятном виде"
    if whose and whose.lower() == (token_login or "").lower():
        whose = ""
    if units["left"] is None:
        if not units["spent"]:
            return "неизвестны: Директ не прислал заголовок Units"
        return f"остаток неизвестен · {spent}"
    left = f"осталось {thousands(units['left'])} из {thousands(units['limit'])}"
    if whose:
        left += f" у {whose}"
    elif not units["login"]:
        # Без Units-Used-Login остаток не приписан никому: молчание здесь
        # читалось бы как «это баллы токена», а мы этого не знаем.
        left += " (чей кошелёк, Директ не сообщил)"
    return f"{left} · {spent}"


def print_report(report: dict) -> None:
    units = report["units"]
    kind = "агентский" if report["agency"] else "обычный"
    rows = [
        ("Контур", f"{PROFILES[report['env']][0]} · {report['host']}"),
        ("Токен", report["token_var"] + (" (общий)" if report["token_borrowed"] else "")),
        ("Логин", f"{report['login'] or 'не определён'} · кабинет {kind}"),
    ]
    if report["children"] is not None:
        children = report["children"]
        count = thousands(children["count"])
        note = "архивные не в счёт"
        if not children["complete"]:
            count = f"не менее {count}"
            note += ", показана первая страница"
        rows.append(("Дочерних", f"{count} ({note})"))
    cabinet = report["cabinet"]
    if cabinet:
        parts = [cabinet["login"], cabinet["name"], cabinet["currency"]]
        rows.append(("Кабинет", " · ".join(part for part in parts if part)))
    rows.append(("Баллы", describe_units(units, report["login"])))

    say("Связь с API Директа есть.")
    say()
    width = max(len(label) for label, _ in rows)
    for label, value in rows:
        say(f"{label.ljust(width)}   {value}")


def warn_about_units(units: dict) -> None:
    if units["left"] is None or not units["limit"]:
        return
    if units["left"] * UNITS_WARN_PARTS < units["limit"]:
        whose = f" у {units['login']}" if units["login"] else ""
        warn(
            f"Внимание: баллов{whose} осталось {thousands(units['left'])} — "
            f"меньше {100 // UNITS_WARN_PARTS}% суточного лимита."
        )


class NoRedirect(urllib.request.HTTPRedirectHandler):
    """Перенаправления не выполняются.

    urllib переносит на новый адрес все заголовки, кроме content-length и
    content-type, — то есть и Authorization с токеном, причём хост и схема
    берутся из ответа. Для клиента API это не удобство, а способ отдать токен
    туда, куда мы не собирались. Адреса контуров известны и постоянны."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


OPENER = urllib.request.build_opener(NoRedirect)


class RedactingParser(argparse.ArgumentParser):
    """argparse печатает ошибки мимо warn() — здесь это исправляется."""

    def error(self, message: str):
        self.print_usage(sys.stderr)
        warn(f"{self.prog}: ошибка аргументов: {message}")
        raise SystemExit(2)


def main(argv=None) -> int:
    # Раньше разбора аргументов, и потому отдельным вызовом: argparse печатает
    # негодный аргумент сам и завершает работу до начала основной, а вырезать
    # можно только то, что уже запомнено. Разбор настроек ниже собирает токены
    # тоже, но он вызывается позже — заменить им этот вызов нельзя. Ошибка
    # чтения файла здесь глотается намеренно: объяснит её строгий разбор, а
    # `--help` обязан работать и со сломанным config/.env.
    preload_secrets()
    parser = RedactingParser(
        description="Проверка доступа к API Яндекс Директа: логин, тип кабинета, баллы.",
    )
    parser.add_argument(
        "--env",
        # Порядок как в документации, а не по алфавиту.
        choices=list(PROFILES),
        help="профиль: контур и набор переменных. По умолчанию — YANDEX_DIRECT_ENV",
    )
    parser.add_argument(
        "--account",
        metavar="ЛОГИН",
        help="логин кабинета; при отсутствии берётся кабинет из настройки, а не активный выбор",
    )
    parser.add_argument(
        "--json", action="store_true", help="машиночитаемый вывод вместо сводки"
    )
    args = parser.parse_args(argv)

    try:
        # Файл читается, настройки разбираются и токен запоминается секретом
        # одним вызовом общего модуля: раздельно это значило бы завести момент,
        # когда токен уже прочитан, а вырезать его ещё некому.
        settings = settings_from_env(profile=args.env, account=args.account)
        if settings.profile == "test_cabinet" and not settings.account:
            # У этого профиля тот же адрес, что у продакшена: без своего логина
            # он отличается от боевого только словом в строке «Контур».
            warn(
                "Профиль «тестовый кабинет» без своего логина: заполните "
                "YANDEX_DIRECT_ACCOUNT_TEST_CABINET или передайте --account, "
                "иначе проверка идёт по тому же кабинету, что и продакшн."
            )
        report = collect(settings)
        # Отрисовка внутри обработчика намеренно: сбой при выводе — такой же
        # сбой, и вылетать трассировкой он не должен наравне с остальными.
        if args.json:
            # allow_nan=False — страховка: вывод, объявленный машиночитаемым,
            # не может содержать того, что стандартный JSON не разбирает.
            say(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False))
        else:
            print_report(report)
        warn_about_units(report["units"])
    except DirectFailure as exc:
        warn(str(exc))
        return 1
    except Exception:
        # Сеть и ответы — чужие, и непредвиденный сбой не должен вылетать
        # трассировкой мимо redact(). Трассировка не прячется: без неё такой
        # сбой не разобрать, — но проходит через вырезание секретов.
        warn("Непредвиденный сбой. Токен из трассировки вырезан.")
        warn(traceback.format_exc())
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
