"""Кабинеты: тип токена, единый список и резолвер по человеческому запросу."""

from __future__ import annotations

import hashlib
import re
import time
import unicodedata

import money
from cache import Cache
from config import DirectFailure, excerpt, short
from errors import (
    NO_RIGHTS,
    NO_UNITS,
    ApiFailure,
    ItemIssue,
    TransportFailure,
    optional,
    required,
)

# Имя записи в общем слое и её слой. Запись одна на токен и потому лежит в
# корне кэша плоско — `cache/accounts.json` рядом с индексом и метаданными, а
# не в `cache/<кабинет>/`: каталог в корне кэша это кабинет, а список кабинетов
# кабинету не принадлежит.
#
# Номера схемы у записи нет: прежнюю раскладку общий слой отличает своими
# метаданными. Файл, написанный до переезда, лежит без них — и читается как
# промах, то есть ровно как «нужно перечитать».
RECORD = "accounts"
LAYER = "accounts"

AGENCY = "agency"
CLIENT = "client"

CABINET_FIELDS = ["Login", "ClientId", "ClientInfo", "Currency", "Archived",
                  "Type", "VatRate"]

# Предел массива `SelectionCriteria.Logins` у `AccountManagement`. Не из
# документации, а из отказа 241 «Массив SelectionCriteria.Logins должен
# содержать не более 50 элементов» (замер 28.08.2026).
BALANCE_CHUNK = 50

# Сколько знаков отпечатка токена хранить. Отпечаток нужен, чтобы заметить
# смену токена, не тратя на это вызова: список кабинетов принадлежит токену, а
# не контуру, и подменённый токен иначе получил бы чужой список — с логинами,
# которых у него нет, и молча.
FINGERPRINT_LENGTH = 16

# Первая страница `AgencyClients.get` при определении типа токена: одна запись
# и одно поле. Ответ нужен не данными, а самим фактом успеха или отказа 54,
# поэтому берётся самая дешёвая его форма.
DETECT_PAGE = {"Limit": 1, "Offset": 0}

# Чем именно кабинет отвечает запросу. Поле совпадения и его точность —
# разные вопросы, и слить их в один перечень нельзя: `barbosu.ru` находит
# кабинет `adv-barbosu` не целиком, но находит именно **по домену**, и назвать
# это «частичным» значит скрыть от человека, как сработал поиск.
BY_LOGIN = "логин"
BY_NAME = "название"
BY_DOMAIN = "домен"

# Порядок полей при сортировке: точный логин выше точного названия, а тот выше
# домена. Внутри одной точности порядок должен быть устойчивым, иначе один и
# тот же запрос показывает кабинеты в разном порядке от запуска к запуску.
FIELDS = (BY_LOGIN, BY_NAME, BY_DOMAIN)

# Метка адреса: буква или цифра любого алфавита, дальше буквы, цифры и дефис.
# Латиницей набор не ограничен намеренно — `ромашка.рф` такой же адрес клиента,
# как `romashka.ru`. Подчёркивание в именах хостов недопустимо и сюда не
# попадает: запрос с ним адресом не считается и ищется как обычная строка.
DOMAIN_LABEL = r"[^\W_](?:[^\W_]|-)*"

# Запрос, похожий на адрес сайта. Всё, кроме самого имени хоста, необязательно:
# схема, `www.`, имя пользователя перед `@`, порт, путь. Клиента называют по
# домену и в адресе сайта, и в адресе почты, и копируют его откуда придётся —
# из адресной строки вместе с портом, из подписи в письме вместе с именем.
# Конечная точка имени (`romashka.ru.`) снимается до разбора.
DOMAIN_RE = re.compile(
    rf"^(?:[a-z][a-z0-9+.-]*://)?"
    rf"(?:[^\s/@]+@)?"
    rf"(?:www\.)?"
    rf"(?P<host>{DOMAIN_LABEL}(?:\.{DOMAIN_LABEL})+)"
    rf"(?::\d{{1,5}})?(?:[/?#].*)?$",
    re.IGNORECASE,
)

# Приставка punycode. Кириллический адрес приходит и в таком виде — из адресной
# строки браузера он копируется уже закодированным.
PUNYCODE = "xn--"

# Вторые уровни — свои у каждой зоны. Верхний уровень отсекается без перечня:
# он последний всегда, каким бы новым ни был (`.moscow`, `.tech`). А `co` в
# `romashka.co.uk` и `com` в `romashka.com.au` — уже второй уровень, и отличить
# их от имени клиента можно только по имени.
#
# Именно по имени и именно внутри своей зоны. Общий перечень на все зоны сразу
# — это размен промаха в одной зоне на неверный ответ в другой: `me` придётся
# добавить ради `me.uk`, и он же срежет владельца `me` в `shop.me.ru`. Ровно на
# этом уже ловился `bp` в `shop.bp.ru`, когда признаком была длина метки.
#
# Перечень заведомо неполон: полного списка общественных суффиксов в
# стандартной библиотеке нет, а копия его устарела бы молча. Неполнота
# безопасна по построению — незнакомая зона теряет только верхний уровень, и
# метка выходит длиннее нужной. Это промах, а не чужой кабинет.
PUBLIC_SECOND_LEVEL = {
    "au": frozenset({"com", "net", "org", "edu", "gov", "asn", "id"}),
    "ar": frozenset({"com", "net", "org", "gob", "edu", "int"}),
    "br": frozenset({"com", "net", "org", "gov", "edu"}),
    "cn": frozenset({"com", "net", "org", "gov", "edu", "ac"}),
    "il": frozenset({"co", "net", "org", "ac", "gov", "muni"}),
    "in": frozenset({"co", "net", "org", "gen", "firm", "ind", "ac", "edu", "gov"}),
    "jp": frozenset({"co", "ne", "or", "ac", "go", "gr", "ed", "lg"}),
    "kr": frozenset({"co", "ne", "or", "go", "re", "pe", "ac"}),
    "mx": frozenset({"com", "net", "org", "gob", "edu"}),
    "nz": frozenset({"co", "net", "org", "ac", "gen", "govt", "school"}),
    "pl": frozenset({"com", "net", "org", "gov", "edu", "info"}),
    "ru": frozenset({"com", "net", "org", "pp", "msk", "spb", "edu", "gov", "ac", "int"}),
    "tr": frozenset({"com", "net", "org", "gov", "edu", "biz", "info"}),
    "ua": frozenset({"com", "net", "org", "gov", "edu", "in"}),
    "uk": frozenset({"co", "org", "me", "ltd", "plc", "net", "sch", "ac", "gov", "nhs"}),
    "za": frozenset({"co", "net", "org", "web", "gov", "ac"}),
}


def cabinets_said(count: int) -> str:
    """«1 кабинет», «2 кабинета», «5 кабинетов»: сообщение читает человек."""
    tail, hundred = count % 10, count % 100
    if tail == 1 and hundred != 11:
        word = "кабинет"
    elif 2 <= tail <= 4 and not 12 <= hundred <= 14:
        word = "кабинета"
    else:
        word = "кабинетов"
    return f"{count} {word}"


class Ambiguous(DirectFailure):
    """Под запрос подошло несколько кабинетов — выбирает человек."""

    def __init__(self, query: str, matches: list):
        self.query = query
        self.matches = matches
        names = ", ".join(match.cabinet.login for match in matches[:8])
        tail = "" if len(matches) <= 8 else f" и ещё {len(matches) - 8}"
        super().__init__(
            f"Запросу «{excerpt(query, 64)}» отвечает {cabinets_said(len(matches))}: "
            f"{names}{tail}. Уточните запрос или назовите логин точно."
        )


class NotFound(DirectFailure):
    """Под запрос не подошёл ни один кабинет."""

    def __init__(self, query: str, total: int, hidden: int = 0):
        self.query = query
        archived = (
            f" Архивных кабинетов {hidden}, они в поиске не участвуют — "
            f"добавьте --archived." if hidden else ""
        )
        super().__init__(
            f"Запросу «{excerpt(query, 64)}» не отвечает ни один из "
            f"{cabinets_said(total)}.{archived}"
        )


# --------------------------------------------------------------------------
# Кабинет
# --------------------------------------------------------------------------

def fold(text) -> str:
    """Строка в виде, в котором её сравнивают с запросом.

    Регистр, `ё` и разделители не должны решать, найден кабинет или нет.
    `ё` заменяется на `е` потому же, почему это делает сам Директ: он
    нормализует так минус-фразы, и пользователь, набравший «Тёплый дом», ищет
    кабинет «Теплый дом». Разделители — дефис, подчёркивание, точка — сводятся
    к пробелу: логин `artwist-nika-nagel` обязан находиться по «nika nagel»."""
    lowered = unicodedata.normalize("NFKC", str(text)).casefold().replace("ё", "е")
    return " ".join(re.split(r"[\s\-_./\\,:;()\[\]«»\"']+", lowered)).strip()


def domain_of(query: str):
    """Метка домена из запроса, либо None, если запрос доменом не выглядит.

    Возвращается именно метка — `nika-nagel` из `www.nika-nagel.ru/catalog`, —
    потому что кабинеты называют по клиенту, а не по полному адресу.

    Зона отбрасывается целиком, а не одной меткой. Верхний уровень последний
    всегда, поэтому снимается без разговоров — и `.ru`, и незнакомый `.moscow`.
    А у `romashka.co.uk` зона двусоставная: остановись мы на первой метке,
    вышло бы `romashka.co`, которое не совпадёт ни с одним кабинетом. Поэтому
    следом снимаются вторые уровни этой зоны — по имени и только внутри своей
    зоны. Ни длина, ни общий на все зоны перечень признаком не годятся: `bp` в
    `shop.bp.ru` такой же двухбуквенный, как `co`, а `me` — второй уровень в
    `.uk` и обычное имя в `.ru`. Снять лишнее значит увести запрос к
    постороннему кабинету `shop`.

    Последняя метка не снимается никогда: `bp.ru` — это `bp`, а не пустота.
    Поддомен остаётся: `shop.romashka.ru` даёт `shop.romashka`, потому что у
    агентства это обычно разные кабинеты одного клиента.

    Адрес бывает и кириллическим, и закодированным: `ромашка.рф` и
    `xn--80aa3agjl3d.xn--p1ai` — один и тот же клиент, и оба обязаны находить
    кабинет «Ромашка». Из адресной строки браузера адрес копируется как раз
    вторым способом.

    Обвязка вокруг имени хоста снимается вся: схема, `www.`, имя пользователя
    перед `@`, порт и путь. Клиента называют доменом и в адресе сайта, и в
    адресе почты, а копируют его откуда придётся."""
    match = DOMAIN_RE.match(str(query).strip().casefold().rstrip("."))
    if not match:
        return None
    # ответ сюда не доходит: метки собраны из запроса человека, а не из тела
    labels = [unpuny(label) for label in match.group("host").split(".")]
    if all(label.isdigit() for label in labels):
        # Не адрес, а число с точками: сетевой адрес или номер версии. Метка
        # `192.168.0` кабинета не назовёт, а `2` из «2.0» нашла бы всякий
        # логин, где встретилась двойка.
        return None
    if len(labels) < 2:
        return ".".join(labels)
    second_level = PUBLIC_SECOND_LEVEL.get(labels.pop(), frozenset())
    # Ровно одна метка, а не сколько получится: таблица описывает у зоны один
    # второй уровень, и цикл здесь снимал бы повторы сверх суффикса. У
    # `shop.com.com.au` суффикс — `com.au`, владелец — `com`, и второй `com`
    # снимать нельзя: вышло бы `shop`, то есть посторонний кабинет.
    if len(labels) > 1 and labels[-1] in second_level:
        labels.pop()
    return ".".join(labels)


def unpuny(label: str) -> str:
    """Метка адреса в читаемом виде: `xn--80aa3agjl3d` -> `ромашка`.

    Кодированной меткой не совпадёт ни одно название кабинета, а из адресной
    строки браузера адрес копируется именно так. Неразбираемая метка остаётся
    как есть: «xn--» бывает и просто началом слова, и обрубком, и падать на
    поисковом запросе из-за этого нельзя."""
    if not label.startswith(PUNYCODE):
        return label
    try:
        return label.encode("ascii").decode("idna")
    except (UnicodeError, ValueError):
        return label


class Match:
    """Кабинет и то, чем он ответил запросу.

    Пара «поле, точность» вместо одного слова: `barbosu.ru` находит
    `adv-barbosu` по домену и не целиком, и обе половины ответа человеку
    нужны — по чему нашли и насколько уверенно."""

    __slots__ = ("cabinet", "field", "exact")

    def __init__(self, cabinet, field: str, exact: bool):
        self.cabinet = cabinet
        self.field = field
        self.exact = bool(exact)

    @property
    def reason(self) -> str:
        return self.field if self.exact else f"{self.field}, часть"

    def __repr__(self) -> str:
        return f"<Match {self.cabinet.login} · {self.reason}>"


class Cabinet:
    """Один кабинет: то, что о нём знает скилл, независимо от типа токена."""

    __slots__ = ("login", "client_id", "name", "currency", "archived", "kind",
                 "balance", "balance_known", "vat_rate")

    def __init__(self, login, client_id=None, name="", currency="", archived=False,
                 kind="", balance=None, balance_known=False, vat_rate=None):
        self.login = login
        self.client_id = client_id
        self.name = name
        self.currency = currency
        self.archived = bool(archived)
        self.kind = kind
        self.balance = balance
        self.balance_known = balance_known
        # Ставка НДС плательщика. `None` — не «ноль процентов», а «Директ её
        # не назвал»: поле объявлено `nillable`, и у пустого значения свой
        # смысл. Ноль на его месте объяснял бы расхождение с интерфейсом
        # ставкой, которой кабинет не сообщал.
        self.vat_rate = vat_rate

    @classmethod
    def from_api(cls, record: dict, where: str) -> "Cabinet":
        """Кабинет из ответа `AgencyClients.get` или `Clients.get`.

        Логин обязателен: он адресует кабинет в заголовке `Client-Login`, и
        запись без него — не кабинет, а повреждённый ответ. Остальные поля
        необязательны, потому что Директ возвращает не всё запрошенное:
        `Restrictions` у агентского кабинета в ответе не было вовсе."""
        login = required(record, "Login", str, where).strip()
        if not login:
            raise TransportFailure(
                f"{where}: кабинет с пустым логином. Адресовать его нечем.",
                retryable=False,
            )
        archived = optional(record, "Archived", str, where)
        return cls(
            login=login,
            client_id=optional(record, "ClientId", int, where),
            name=excerpt(optional(record, "ClientInfo", str, where) or "", 120),
            currency=excerpt(optional(record, "Currency", str, where) or "", 16),
            # Директ отвечает строкой `YES`/`NO`, а не логическим значением.
            # Приведение `bool("NO") == True` дало бы архивными все кабинеты.
            archived=(archived or "").strip().upper() == "YES",
            kind=excerpt(optional(record, "Type", str, where) or "", 32),
            # Схема объявляет поле `decimal`, живой API отдаёт целое (замер
            # 02.09.2026: 22 у всех кабинетов обоих методов). Принимаются оба:
            # смена формы ответа не должна ронять список кабинетов, а вот
            # строка на месте ставки — уже повреждённый ответ.
            #
            # `NoneType` в перечне не послабление: поле объявлено `nillable`, и
            # `null` в нём — законный ответ «ставка не названа», такой же, как
            # отсутствие ключа. Без него законный ответ Директа ронял бы весь
            # список кабинетов.
            vat_rate=optional(record, "VatRate", (int, float, type(None)), where),
        )

    def as_dict(self) -> dict:
        return {
            "login": self.login,
            "client_id": self.client_id,
            "name": self.name,
            "currency": self.currency,
            "archived": self.archived,
            "type": self.kind,
            "balance": self.balance,
            "balance_known": self.balance_known,
            "vat_rate": self.vat_rate,
        }

    @classmethod
    # ответ сюда не доходит: запись приходит из кэша, а не из тела ответа —
    # проверяется она здесь же и целиком, а помощники разбирают ответ Директа
    def from_dict(cls, record):
        """Кабинет из кэша, либо None, если запись не разбирается.

        Запись разбирается целиком или никак. Приведение чужого значения к
        строке выглядит бережно, а на деле подменяет кабинет похожим на него:
        `client_id` объектом стал бы строкой с фигурными скобками, а баланс
        строкой — суммой, которой нет. Половинчато разобранная запись хуже
        отсутствующей, потому что срока жизни у кэша нет: она осталась бы в
        поиске до первого `--no-cache`.

        Отвергнутую запись считает `Accounts.load`: список, из которого
        разобралось меньше записей, чем в нём лежит, целиком идёт в
        перечитывание."""
        if not isinstance(record, dict):
            return None
        login = record.get("login")
        if not isinstance(login, str) or not login.strip():
            return None
        for key, kind in (("name", str), ("currency", str), ("type", str)):
            if key in record and not isinstance(record[key], str):
                return None
        client_id = record.get("client_id")
        if client_id is not None and (isinstance(client_id, bool)
                                      or not isinstance(client_id, int)):
            return None
        archived = record.get("archived", False)
        known = record.get("balance_known", False)
        if not isinstance(archived, bool) or not isinstance(known, bool):
            return None
        balance = record.get("balance")
        if known and (isinstance(balance, bool) or not isinstance(balance, int)):
            return None
        vat_rate = record.get("vat_rate")
        if vat_rate is not None and (isinstance(vat_rate, bool)
                                     or not isinstance(vat_rate, (int, float))):
            return None
        return cls(
            login=login.strip(),
            client_id=client_id,
            name=record.get("name") or "",
            currency=record.get("currency") or "",
            archived=archived,
            kind=record.get("type") or "",
            balance=balance if known else None,
            balance_known=bool(known),
            vat_rate=vat_rate,
        )

    def match(self, query: str, label=None):
        """Пара «поле, точно ли», либо None, если кабинет запросу не отвечает.

        Точное совпадение проверяется раньше частичного: кабинет `metallik` не
        должен теряться среди `metallik-night` и `metalllik-zabory-zhaluzi`
        только потому, что похожих больше.

        Домен проверяется после логина и названия, но раньше их частичных
        совпадений: запрос `barbosu.ru` — это про домен, даже когда в логине
        `adv-barbosu` он сидит куском."""
        wanted = fold(query)
        if not wanted:
            return None
        login, name = fold(self.login), fold(self.name)
        folded = fold(label) if label else ""
        if wanted == login:
            return BY_LOGIN, True
        if name and wanted == name:
            return BY_NAME, True
        if folded and (folded == login or (name and folded == name)):
            return BY_DOMAIN, True
        if folded and (folded in login or (name and folded in name)):
            return BY_DOMAIN, False
        if wanted in login:
            return BY_LOGIN, False
        if name and wanted in name:
            return BY_NAME, False
        return None

    def money(self, currency: bool = True) -> str:
        """Баланс словами. Пустая строка — «неизвестен», а не «ноль».

        Валюта отделяется признаком, потому что в TSV-индексе для неё есть
        своя колонка, а повтор в двух колонках сразу — лишний повод им
        разойтись."""
        if not self.balance_known or self.balance is None:
            return ""
        return money.format_api(self.balance, self.currency if currency else "")

    def __repr__(self) -> str:
        return f"<Cabinet {self.login}{' (архивный)' if self.archived else ''}>"


# --------------------------------------------------------------------------
# Список кабинетов
# --------------------------------------------------------------------------

class Accounts:
    """Список кабинетов одного контура и всё, что с ним делают."""

    def __init__(self, profile: str, kind: str, owner: str, cabinets: list,
                 *, checked_at: str = "", active: str = "", fingerprint: str = "",
                 balances_read: bool = False, cache=None, client=None, warn=None):
        self.profile = profile
        self.kind = kind
        self.owner = owner
        self.cabinets = cabinets
        self.checked_at = checked_at
        self.active = active
        # Отпечаток токена, а не сам токен: класть токен в кэш нельзя, и общий
        # слой такую запись отвергает — по имени поля в том числе.
        self.fingerprint = fingerprint
        self.balances_read = balances_read
        self.client = client
        self.warn = warn or (lambda text: None)
        # Свой кэш собирается с тем же предупреждением: жалоба слоя на
        # непрочитанную запись адресована тому же, кто читает остальные.
        self.cache = cache if cache is not None else Cache(warn=self.warn)
        # Здесь помнится только **неудача** вопроса об остатке: сам остаток
        # ведут заголовки ответов, и замораживать его нельзя. Платный вопрос
        # задаётся один раз на кабинет — повторять его на каждое решение
        # значило бы платить за ответ дороже, чем стоит сам вызов.
        self._units_asked: dict = {}

    # -- чтение ------------------------------------------------------------

    @classmethod
    def load(cls, client, *, refresh: bool = False, cache=None, warn=None) -> "Accounts":
        """Список из кэша, а при `refresh` или промахе — из API.

        `refresh` — это `--no-cache`: он отменяет **переиспользование**
        сохранённого списка. Саму запись читать он не мешает и мешать не может:
        в ней же лежат выбранный человеком кабинет и список соседнего контура,
        а их обновление списка не отменяет. Поэтому запись читается всегда, а
        `refresh` решает только, годится ли прочитанный список в ответ."""
        note = warn or (lambda text: None)
        store = cache if cache is not None else Cache(warn=note)
        if not store.reuse:
            # `--no-cache` этому модулю передаётся аргументом `refresh`, а не
            # кэшем, который не читает сохранённого. Разница не в словах: в
            # записи лежат ещё и выбранный человеком кабинет, и список
            # соседнего контура, и не прочитать её — значит стереть их
            # обновлением списка. Молчаливая потеря выбора хуже отказа.
            raise DirectFailure(
                "Список кабинетов нельзя читать кэшем без переиспользования: "
                "в той же записи лежат выбранный кабинет и соседний контур. "
                "Обновление списка задаётся аргументом refresh."
            )
        profile = client.settings.profile
        mark = fingerprint(client.settings.token)
        # ответ сюда не доходит: раскладка по контурам приходит из кэша
        stored = read_record(store).get(profile)
        # Запись профиля проверяется отдельно от записи целиком: слой отдал
        # своё поколение целым, а запись одного контура внутри него могла быть
        # испорчена правкой руками. Непонятная запись означает «перечитать», а
        # не отказ команды.
        stored = stored if isinstance(stored, dict) else {}
        if stored and stored.get("kind") not in (AGENCY, CLIENT):
            # Тип токена решает всю развилку мультикабинета — и какой метод
            # спрашивать, и допустим ли заголовок баллов агентства. Незнакомое
            # значение здесь означает не «клиентский», а «неизвестно», и
            # догадка вместо ответа стоит дороже перечитывания.
            note("В кэше не записан тип токена — перечитываю список.")
            stored = {}
        if stored and stored.get("fields") != list(CABINET_FIELDS):
            # Набор полей вырос или сменился — прежний список неполон по
            # построению. Отличить «поля не спрашивали» от «Директ его не
            # назвал» по самой записи нечем, а слой отчётов на второе
            # опирается: пустая ставка НДС там значит «не названа».
            note("Список кабинетов записан другим набором полей — перечитываю.")
            stored = {}
        if stored and stored.get("fingerprint") != mark:
            # Список принадлежит токену, а не контуру. Подменённый токен из
            # чужого кэша получил бы список кабинетов, к которым у него нет
            # доступа, — и узнал бы об этом отказом на первой же операции.
            note("Токен не тот, которым записан список кабинетов, — перечитываю.")
            stored = {}
        records = stored.get("cabinets")
        if not refresh and isinstance(records, list):
            accounts = cls.from_dict(profile, stored, cache=store, client=client,
                                     warn=warn)
            # Пустой список — законный ответ: у агентства бывает ноль клиентов,
            # и перечитывать его при каждом запуске значит платить баллами за
            # ту же пустоту. А вот список, из которого разобралось меньше
            # записей, чем в нём лежит, годным не считается: срока жизни у слоя
            # нет, и потерянный кабинет остался бы невидимым для поиска до
            # первого `--no-cache` — то есть неполный список выдавался бы за
            # полный, и молча.
            if len(accounts.cabinets) == len(records):
                return accounts
            note(f"В кэше разобрано {len(accounts.cabinets)} кабинетов из "
                 f"{len(records)} — перечитываю список.")
        accounts = cls.fetch(client, cache=store, warn=warn)
        # Активный кабинет переживает обновление списка: он выбор человека, а
        # не выгрузка. Иначе он пропадал бы ровно тогда, когда список
        # перечитывают, — и именно после `--no-cache` работа шла бы не в том
        # кабинете, в котором шла минуту назад.
        #
        # Берётся он из записи заново, а не из снимка, прочитанного до похода в
        # сеть: обход агентства идёт секунды, и `--use` из соседнего запуска
        # успевает лечь в кэш. Записать поверх него старое значение значило бы
        # отменить состоявшийся выбор человека.
        # ответ сюда не доходит: та же запись кэша, перечитанная после похода
        fresh = read_record(store).get(profile)
        fresh = fresh if isinstance(fresh, dict) else {}
        chosen = str(fresh.get("active") or stored.get("active") or "")
        if chosen and any(cabinet.login == chosen for cabinet in accounts.cabinets):
            accounts.active = chosen
        accounts.save()
        return accounts

    @classmethod
    def fetch(cls, client, *, cache=None, warn=None) -> "Accounts":
        """Тип токена и список кабинетов из API."""
        kind, owner, cabinets = detect(client)
        # ответ читается напрямую: перечень кабинетов уходит в конструктор, и
        # тот только раскладывает его по полям — читали запись помощники в
        # `Cabinet.from_api`. Вызов через `cls` разбор с объявлением класса не
        # связывает, и это тот же угол соглашения о вызове, что выше
        accounts = cls(
            profile=client.settings.profile,
            kind=kind,
            owner=owner,
            cabinets=cabinets,
            checked_at=stamp(),
            fingerprint=fingerprint(client.settings.token),
            cache=cache,
            client=client,
            warn=warn,
        )
        accounts.read_balances()
        return accounts

    def read_balances(self) -> None:
        """Балансы общих счетов, кусками по 50 логинов.

        Отказ здесь не отменяет список: баланс — колонка, а список — предмет
        работы. Поэтому неудача превращается в предупреждение и пустую
        колонку, а не в отсутствие ответа на вопрос «какие есть кабинеты»."""
        if self.client is None or not self.cabinets:
            return
        logins = [cabinet.login for cabinet in self.cabinets]
        try:
            amounts, issues = balances(self.client, logins)
        except DirectFailure as failure:
            self.warn(
                f"Балансы прочитать не удалось, колонка останется пустой. "
                f"{failure}"
            )
            return
        for cabinet in self.cabinets:
            if cabinet.login in amounts:
                cabinet.balance = amounts[cabinet.login]
                cabinet.balance_known = True
        self.balances_read = True
        # Отказ по отдельному логину — не молчание, а строка в stderr. Пустая
        # колонка означает и «общего счёта нет», и «прав на него нет», и
        # различить их можно только по этому сообщению.
        unexplained = [issue for issue in issues if issue.code != NO_RIGHTS]
        if unexplained:
            self.warn(
                f"Баланс не прочитан у {len(unexplained)} кабинетов из "
                f"{len(logins)}. Первый отказ — {unexplained[0]}"
            )

    # -- поиск -------------------------------------------------------------

    def find(self, query: str, *, archived: bool = False) -> list:
        """Кабинеты, отвечающие запросу, — перечень `Match`.

        Архивные по умолчанию не участвуют. У боевого агентства их 225 из 258,
        и без этого правила любой запрос отвечает списком закрытых кабинетов,
        среди которых действующий приходится искать глазами."""
        label = domain_of(query)
        found = []
        for cabinet in self.cabinets:
            if cabinet.archived and not archived:
                continue
            answer = cabinet.match(query, label)
            if answer is not None:
                found.append(Match(cabinet, answer[0], answer[1]))
        # Точное совпадение выигрывает у частичных целиком, а не «идёт первым»:
        # иначе `metallik` при трёх похожих логинах остаётся неоднозначностью,
        # хотя пользователь назвал кабинет ровно.
        exact = [match for match in found if match.exact]
        chosen = exact or found
        if exact:
            # Среди точных выигрывает поле поточнее: логин у кабинетов
            # уникален, а название — подпись, и совпасть они могут у разных
            # кабинетов. Без этого запрос, который **и есть** чей-то логин,
            # оставался бы неоднозначным, а совет «назовите логин точно»
            # неисполнимым: логин и был назван точно.
            best = min(FIELDS.index(match.field) for match in exact)
            chosen = [match for match in exact if FIELDS.index(match.field) == best]
        # ответ читается напрямую: чем зовут лямбду, из текста не видно, и
        # разбор считает её параметр ответом. Здесь её зовёт `sort` записями
        # того же списка — каждая собрана `Match(...)` строкой выше, а поля
        # кабинета в ней прочитаны помощниками при разборе ответа
        chosen.sort(key=lambda match: (FIELDS.index(match.field), match.cabinet.login))
        return chosen

    def choose(self, query: str, *, archived: bool = False) -> Cabinet:
        """Единственный кабинет по запросу, иначе отказ с вопросом человеку."""
        found = self.find(query, archived=archived)
        if len(found) == 1:
            return found[0].cabinet
        if not found:
            hidden = 0 if archived else sum(1 for item in self.cabinets if item.archived)
            raise NotFound(query, len(self.cabinets), hidden)
        raise Ambiguous(query, found)

    def only(self):
        """Единственный кабинет контура, если он единственный.

        Клиентский токен даёт ровно один кабинет — спрашивать о нём человека
        значит спрашивать о том, чего он не выбирал."""
        living = [cabinet for cabinet in self.cabinets if not cabinet.archived]
        return living[0] if len(living) == 1 else None

    def by_login(self, login: str):
        wanted = (login or "").strip().casefold()
        for cabinet in self.cabinets:
            if cabinet.login.casefold() == wanted:
                return cabinet
        return None

    def current(self):
        """Активный кабинет: выбранный человеком или единственный."""
        chosen = self.by_login(self.active) if self.active else None
        return chosen or self.only()

    def select(self, cabinet: Cabinet) -> None:
        self.active = cabinet.login
        self.save()

    # -- баллы -------------------------------------------------------------

    def use_operator_units(self, account: str, *, need: int = 0):
        """Платить ли за вызов баллами агентства.

        Ответ передаётся в `client.call(..., use_operator_units=...)`. Режим
        решается настройкой, а `auto` — остатком баллов у кабинета:

        * клиентский токен — всегда нет: заголовок допустим только в запросах
          от имени агентства;
        * запрос без кабинета — тоже нет: он и так оплачен агентством;
        * иначе смотрим остаток кабинета и берём баллы агентства, когда своих
          не хватает на задуманное. Пустой `need` не означает «сколько угодно»:
          кабинет с нулевым остатком не оплатит и одного вызова, поэтому
          порогом в этом случае служит единица, а не ноль.

        `need` — цена задуманного в баллах, и считает её вызывающий код:
        `Limits.units_cost` складывает плату за вызов с платой за объекты по
        таблице `limits.json`. Оценка приходит сюда **сверху**: завышенная
        тратит баллы агентства там, где хватило бы своих, заниженная выглядит
        точной и роняет вызов отказом 152 — а за него Директ берёт с кошелька,
        которому и так не хватило, ещё раз. Сколько именно, не замерено;
        замеренные отказы стоили от 21 до 50 баллов
        (`ERRORS_AND_LIMITS.md`, раздел 6.2).

        Остаток стоит вызова `Clients.get` — 10 баллов, и они списываются с
        кабинета, о котором спрашиваем. Поэтому спрашивается он один раз, а
        дальше счёт ведут заголовки: Директ присылает остаток на каждый ответ,
        включая ответ с ошибкой. Замороженный снимок был бы хуже лишнего
        вызова — два запроса по пятьдесят баллов при остатке в шестьдесят оба
        получили бы «хватает», и второй платить было бы уже нечем."""
        if self.client is None:
            # Список, прочитанный из кэша без клиента, спросить остаток не
            # может. Ответить «нет» значило бы выдать решение за ответ на
            # вопрос, которого никто не задавал.
            return None
        mode = self.client.settings.operator_units
        if mode == "always":
            return bool(account) and self.kind == AGENCY
        if mode == "never" or self.kind != AGENCY or not account:
            return False
        left = self._units_left(account)
        if left is None:
            # Остаток неизвестен — своего ответа нет, и решает настройка:
            # `None` протокол читает как «по YANDEX_DIRECT_USE_OPERATOR_UNITS»,
            # то есть при `auto` заголовок не уходит. Ответить «да» на догадке
            # значило бы молча платить баллами агентства за чужой вызов.
            return None
        return left < max(need, 1)

    def _units_left(self, account: str):
        # Свежий остаток из заголовков важнее любого снимка: он приходит на
        # каждый ответ и учитывает всё, что потрачено с прошлого решения.
        live = self.client.units.left(account)
        if live is not None:
            return live
        key = account.casefold()
        if key not in self._units_asked:
            try:
                # Сюда доходит только режим `auto`, а в нём заголовок баллов
                # агентства не отправляется. Это и нужно: вопрос «хватит ли
                # баллов кабинету», заданный за счёт агентства, вернул бы
                # остаток не того кошелька, о котором спрашивали.
                snapshot = self.client.units_for(account)
            except ApiFailure as failure:
                if failure.code == NO_UNITS:
                    # Кабинет не оплатил даже вопрос об остатке — это и есть
                    # ответ, а не его отсутствие. Считать здесь остаток
                    # неизвестным значило бы отказаться от баллов агентства
                    # ровно тогда, когда без них не обойтись.
                    self._units_asked[key] = 0
                else:
                    self.warn(
                        f"Остаток баллов {account} прочитать не удалось. {failure}")
                    self._units_asked[key] = None
            except DirectFailure as failure:
                self.warn(f"Остаток баллов {account} прочитать не удалось. {failure}")
                self._units_asked[key] = None
            else:
                # ответ сюда не доходит: снимок остатка собрал клиент, и поля
                # в нём наши — тело ответа он разобрал помощниками у себя
                self._units_asked[key] = snapshot.get("left")
        # ответ сюда не доходит: спрошенные остатки складывает этот же класс
        return self._units_asked[key]

    # -- хранение ----------------------------------------------------------

    def as_dict(self) -> dict:
        return {
            "kind": self.kind,
            "owner": self.owner,
            "checked_at": self.checked_at,
            # Имя поля не `token`: по такому имени общий слой отказывается
            # писать запись вовсе — и правильно делает, потому что по имени это
            # учётные данные. Здесь лежит отпечаток, и называется он так же.
            "fingerprint": self.fingerprint,
            "active": self.active,
            "balances_read": self.balances_read,
            # Чем список спрошен. Срока жизни у слоя нет, и без этой записи
            # кабинеты, прочитанные прежним набором полей, лежали бы в кэше
            # вечно — а поле, добавленное сегодня, приходило бы пустым и было
            # бы неотличимо от поля, которого Директ не назвал.
            "fields": list(CABINET_FIELDS),
            "cabinets": [cabinet.as_dict() for cabinet in self.cabinets],
        }

    @classmethod
    # ответ сюда не доходит: запись контура читается из кэша, а не из ответа
    def from_dict(cls, profile: str, stored: dict, *, cache=None, client=None,
                  warn=None) -> "Accounts":
        records = stored.get("cabinets")
        cabinets = [] if not isinstance(records, list) else [
            cabinet for cabinet in (Cabinet.from_dict(record) for record in records)
            if cabinet is not None
        ]
        return cls(
            profile=profile,
            kind=str(stored.get("kind") or ""),
            owner=str(stored.get("owner") or ""),
            cabinets=cabinets,
            checked_at=str(stored.get("checked_at") or ""),
            active=str(stored.get("active") or ""),
            fingerprint=str(stored.get("fingerprint") or ""),
            balances_read=bool(stored.get("balances_read")),
            cache=cache,
            client=client,
            warn=warn,
        )

    def save(self) -> None:
        """Запись списка в общий слой: данные, TSV-индекс и метаданные разом.

        Своя запись сливается с тем, что уже лежит: контуры делят одну запись,
        и сохранение одного не должно стирать список другого.

        Индекс отдаётся слою вместе с данными, а не пишется вторым заходом.
        Отдельная запись индекса потребовала бы сверки при каждом чтении: две
        записи подряд не атомарны, и обрыв между ними оставляет свежий JSON
        рядом со вчерашним индексом. Слой кладёт их под одним замком, а
        метаданные — последними, поэтому оборванная запись даёт промах, а не
        пару из разных поколений: сверять при чтении нечего.

        Метки отзыва (`since` у слоя) здесь не снимаются, и это не упущение.
        Они защищают от выгрузки, которую **опровергла** запись в кабинет:
        снятая до неё, она описывает прошлое. Списка кабинетов конвейер записи
        не меняет, а `--clear` посреди обхода агентства — это просьба
        перечитать, и перечитанное ей отвечает. Зато `save` общий с `select`:
        отказ писать по несовпавшей метке молча отменял бы выбор человека,
        сделанный вручную."""
        profiles = read_record(self.cache)
        profiles[self.profile] = self.as_dict()
        self.cache.write(RECORD, LAYER, profiles, index=tsv(profiles))

    def cache_path(self) -> str:
        """Путь к TSV-индексу: по нему ищут кабинет через `grep`."""
        return short(self.cache.path(RECORD, ".tsv"))

    def summary(self) -> str:
        living = sum(1 for cabinet in self.cabinets if not cabinet.archived)
        archived = len(self.cabinets) - living
        tail = f", архивных {archived}" if archived else ""
        return (f"{'агентский' if self.kind == AGENCY else 'клиентский'} токен "
                f"{self.owner or '—'} · кабинетов {living}{tail}")


# --------------------------------------------------------------------------
# Сеть
# --------------------------------------------------------------------------

def detect(client):
    """Тип токена, логин его владельца и список кабинетов."""
    # решение об оплате не нужно: `AgencyClients` — правило `forbidden`,
    # заголовок адресации отсюда не уходит, а без него не уходит и
    # `Use-Operator-Units`. Платит владелец токена, и спросить о решении пока
    # некого: тип токена этот вызов как раз и выясняет.
    probe = client.call(
        "agencyclients", "get",
        {"SelectionCriteria": {}, "FieldNames": ["Login"], "Page": dict(DETECT_PAGE)},
        raise_on_error=False,
    )
    if probe.ok:
        # Запрос ушёл без `Client-Login`, поэтому кошелёк в заголовке — это
        # логин самого токена. Под агентским токеном с адресацией он был бы
        # чужим, и владельца пришлось бы выдумывать.
        # ответ сюда не доходит: кошелёк собран клиентом из заголовка Units
        owner = ((probe.units or {}).get("login") or "").strip()
        return AGENCY, owner, agency_cabinets(client)

    # ответ сюда не доходит: отказ разобран клиентом, тело осталось у него
    code = getattr(probe.error, "code", None)
    if code != NO_RIGHTS:
        raise probe.error

    # решение об оплате не нужно: кабинет здесь задан пустым намеренно — метод
    # спрашивают про владельца токена, а не про кого-то из клиентов. Без
    # `Client-Login` заголовок баллов агентства не отправляется и смысла не
    # имеет: платит владелец токена и так.
    own = client.call("clients", "get", {"FieldNames": CABINET_FIELDS},
                      account="", raise_on_error=False)
    if not own.ok:
        # ответ сюда не доходит: тот же разобранный клиентом отказ
        if getattr(own.error, "code", None) == NO_RIGHTS:
            # «Не агентский» — гипотеза, и подтвердить её обязан успешный
            # `Clients.get`. Отказ 54 и здесь означает, что дело не в типе
            # токена: так отвечает аккаунт, который ждёт перевода в валюту, и
            # аккаунт с приостановленным доступом.
            raise DirectFailure(
                "Директ отказал в правах и на AgencyClients.get, и на "
                "Clients.get — дело не в типе токена. Так отвечает аккаунт, "
                "который ждёт перевода в валюту, и аккаунт с приостановленным "
                "доступом. Проверьте состояние кабинета в интерфейсе Директа."
            )
        raise own.error

    cabinets = read_cabinets(own, "Clients.get")
    if len(cabinets) != 1:
        # Метод отвечает про один кабинет — свой или адресованный заголовком.
        # Брать первый из нескольких значило бы определять личность кабинета
        # порядком записей в чужом ответе.
        raise TransportFailure(
            f"Clients.get: запрошен один кабинет, а Директ вернул "
            f"{len(cabinets)}. Ответ не соответствует запросу.",
            retryable=False,
        )
    return CLIENT, cabinets[0].login, cabinets


def agency_cabinets(client) -> list:
    """Все клиенты агентства, включая архивные, со склейкой страниц.

    Архивные берутся тоже: кабинет, закрытый в прошлом квартале, из поиска
    исчезать не должен — по нему спрашивают статистику. Прячет их поиск, а не
    выгрузка, и прячет обратимо."""
    # решение об оплате не нужно: тот же `AgencyClients` с правилом
    # `forbidden` — перечень клиентов принадлежит агентству, оно за него и
    # платит, а заголовка адресации в вызове нет.
    records = client.get_all(
        "agencyclients",
        {"SelectionCriteria": {}, "FieldNames": CABINET_FIELDS},
        collection="Clients",
    )
    cabinets = []
    for record in records:
        if not isinstance(record, dict):
            raise TransportFailure(
                f"AgencyClients.get: запись перечня не объект: "
                f"{excerpt(record, 120)}",
                retryable=False,
            )
        cabinets.append(Cabinet.from_api(record, "AgencyClients.get"))
    return cabinets


def read_cabinets(response, where: str) -> list:
    """Кабинеты из ответа `Clients.get`."""
    result = response.result
    if not isinstance(result, dict):
        raise TransportFailure(
            f"{where}: result пришёл типом {type(result).__name__} вместо объекта.",
            retryable=False,
        )
    records = optional(result, "Clients", list, where) or []
    for record in records:
        if not isinstance(record, dict):
            raise TransportFailure(
                f"{where}: запись перечня не объект: {excerpt(record, 120)}",
                retryable=False,
            )
    return [Cabinet.from_api(record, where) for record in records]


def balances(client, logins: list) -> dict:
    """Остатки общих счетов по логинам: `AccountManagement` версии Live 4.

    Возвращается пара «остатки, отказы по логинам». Логин без счёта в остатки
    не попадает, и это законный ответ, а не сбой: у агентства общего счёта не
    бывает вовсе («У агентства не может быть общего счёта», отказ 54 внутри
    `ActionsResult`), и так же отвечает кабинет без подключённого общего счёта.
    Отказы возвращаются отдельно, потому что «счёта нет» и «прав не хватило»
    выглядят в остатках одинаково, а значат разное."""
    found: dict = {}
    issues: list = []
    for start in range(0, len(logins), BALANCE_CHUNK):
        chunk = logins[start:start + BALANCE_CHUNK]
        response = client.call_v4(
            "AccountManagement",
            {"Action": "Get", "SelectionCriteria": {"Logins": chunk}},
            # Действие `Get` читает, и повтор его безопасен. Разрешение даётся
            # здесь, а не списком безопасных методов: у `AccountManagement`
            # есть и `Deposit` с `TransferMoney`, и повтор перевода денег
            # безопасным не бывает.
            retry=True,
        )
        data = response.result
        if not isinstance(data, dict):
            raise TransportFailure(
                f"AccountManagement.Get: data пришло типом "
                f"{type(data).__name__} вместо объекта.",
                retryable=False,
            )
        for record in optional(data, "Accounts", list, "AccountManagement.Get") or []:
            if not isinstance(record, dict):
                raise TransportFailure(
                    f"AccountManagement.Get: запись счёта не объект: "
                    f"{excerpt(record, 120)}",
                    retryable=False,
                )
            login = required(record, "Login", str, "AccountManagement.Get")
            amount = required(record, "Amount", str, "AccountManagement.Get")
            try:
                # Четвёртая версия отдаёт сумму в единицах валюты строкой, а не
                # в микроединицах версии 5. Приведение к микроединицам одно на
                # весь скилл, поэтому оно здесь и через money.
                #
                # ответ сюда не доходит: логин и сумма уже прочитаны
                # помощниками, а число складывает money.to_api — дальше по
                # кабинетам раскладывается наш словарь, а не тело ответа
                found[login] = money.to_api(amount)
            except money.MoneyError:
                raise TransportFailure(
                    f"AccountManagement.Get: баланс {login} пришёл значением "
                    f"{excerpt(amount, 32)} — это не сумма.",
                    retryable=False,
                ) from None
        issues.extend(account_issues(data))
    return found, issues


def account_issues(data: dict) -> list:
    """Отказы по отдельным логинам из `ActionsResult`.

    Разбираются отдельно от счетов по той же причине, по которой разбирается
    частичный успех батча: логин, на который прав не хватило, не отменяет
    ответ про остальные."""
    where = "AccountManagement.Get"
    issues = []
    for position, record in enumerate(
            optional(data, "ActionsResult", list, where) or []):
        if not isinstance(record, dict):
            # Повреждённая запись — сама по себе отказ, а не пустое место:
            # молча пропустить её значит спрятать отказ Директа по логину и
            # объявить балансы прочитанными.
            issues.append(ItemIssue(
                "error", None, f"{where}: запись ActionsResult не объект",
                excerpt(record, 120), position))
            continue
        login = optional(record, "Login", str, where) or "логин не назван"
        try:
            entries = optional(record, "Errors", list, where) or []
        except TransportFailure as failure:
            # Отказом наружу это не поднимается: соседние логины ответили, и
            # терять их балансы из-за одного повреждённого блока нельзя. Ровно
            # тем же рассуждением живёт разбор частичного успеха батча.
            issues.append(ItemIssue("error", None, f"{login}: {failure}", "", position))
            continue
        for entry in entries:
            if not isinstance(entry, dict):
                issues.append(ItemIssue(
                    "error", None, f"{login}: запись Errors не объект",
                    excerpt(entry, 120), position))
                continue
            # Поля записи читаются помощниками, как и весь остальной ответ:
            # `null` вместо кода — повреждённый ответ, а не «кода нет». Код
            # здесь единственное, чем отказ «нет прав» отличается от любого
            # другого, а от этой разницы зависит, промолчать про пустую
            # колонку или предупредить человека.
            #
            # Наружу отказ не поднимается по той же причине, что у элемента
            # батча: соседние логины ответили, и терять их балансы из-за
            # одной повреждённой записи нельзя.
            place = f"{where}, запись Errors"
            try:
                issues.append(ItemIssue(
                    "error",
                    required(entry, "FaultCode", int, place),
                    f"{login}: {optional(entry, 'FaultString', str, place) or ''}",
                    optional(entry, "FaultDetail", str, place) or "",
                    position,
                ))
            except TransportFailure as failure:
                issues.append(ItemIssue("error", None, f"{login}: {failure}", "",
                                        position))
    return issues


# --------------------------------------------------------------------------
# Кэш
# --------------------------------------------------------------------------

def read_record(store) -> dict:
    """Карта «профиль → список кабинетов» из общего слоя.

    Промах, повреждённая запись и запись прошлой раскладки — всё это пустая
    карта, то есть «нужно перечитать». Разбирать испорченное содержимое здесь
    нечем и незачем: этим занят слой, а сюда приходит либо целое поколение
    записи, либо ничего.

    Форма проверяется всё равно: запись правят руками, и `"мусор"` вместо
    карты профилей уронил бы команду трассировкой вместо похода в API."""
    entry = store.read(RECORD, LAYER)
    data = None if entry is None else entry.data
    return data if isinstance(data, dict) else {}


# ответ сюда не доходит: карта профилей читается из кэша, а не из ответа
def tsv(profiles: dict) -> str:
    """TSV-индекс для поиска через `grep` без загрузки в контекст.

    Профиль первой колонкой: файл один на оба контура, и без неё строки двух
    контуров с одинаковым логином неразличимы."""
    lines = ["profile\tlogin\tclient_id\tname\tcurrency\tbalance\tvat_rate\tarchived"]
    for profile in sorted(profiles):
        stored = profiles[profile]
        if not isinstance(stored, dict):
            continue
        records = stored.get("cabinets")
        if not isinstance(records, list):
            # Соседний контур мог быть испорчен правкой руками, и перебор
            # числа уронил бы команду в том контуре, который цел.
            continue
        for record in records:
            cabinet = Cabinet.from_dict(record)
            if cabinet is None:
                continue
            lines.append("\t".join((
                profile,
                cabinet.login,
                "" if cabinet.client_id is None else str(cabinet.client_id),
                # Табуляция в названии кабинета разорвала бы строку на лишние
                # колонки, и `grep` нашёл бы кабинет, которого нет.
                cabinet.name.replace("\t", " "),
                cabinet.currency,
                cabinet.money(currency=False),
                "" if cabinet.vat_rate is None else str(cabinet.vat_rate),
                "archived" if cabinet.archived else "",
            )))
    return "\n".join(lines) + "\n"


def fingerprint(token: str) -> str:
    """Отпечаток токена: узнать его смену, не спрашивая Директ.

    Хранится в записи рядом со списком, под именем `fingerprint`. Сам токен
    туда класть нельзя — общий слой отвергает и значение, и такое имя поля, — а
    сравнивать нечем: логин владельца известен только после платного вызова, то
    есть ровно после того, для чего кэш и заводился. Обратно из шестнадцати
    знаков SHA-256 токен не восстанавливается."""
    return hashlib.sha256(str(token).encode("utf-8")).hexdigest()[:FINGERPRINT_LENGTH]


def resolve_account(accounts: "Accounts", client, wanted: str) -> str:
    """Кабинет команды: явный аргумент, потом активный, потом настройка.

    Порядок именно такой. Явно названный кабинет важнее запомненного, иначе
    `--account` ничего не значит; запомненный важнее настройки, иначе
    `accounts.py --use` остаётся записью в кэше, которую никто не читает.

    Живёт здесь, а не в команде, которой понадобилась первой. Пока функция
    лежала в `campaigns.py`, забрать её мог только тот, кто ввозит команду
    целиком, — вместе с её разбором аргументов и её же именем `main`. Пишущие
    команды такого ввоза не делали и брали логин из настройки мимо активного
    выбора; дверь, до которой нельзя дотянуться, закрытой не считается."""
    if wanted:
        # Архивный кабинет по имени найти можно: скрывать его стоит в перечне,
        # а не тогда, когда человек назвал его сам. Отказом это правило не
        # включается: `find` ранжирует точное выше частичного **внутри своего
        # набора**, а архивные в набор по умолчанию не входят, и при архивном
        # `foo` рядом с живым `foo-new` кандидат оказывался ровно один —
        # частичный и живой. `NotFound` не поднимался, архивный проход не
        # начинался, и названный ровно кабинет молча подменялся соседним.
        #
        # Поэтому спрашивается сперва полный набор: есть ли где-нибудь точное
        # совпадение. Ответ «да» решает — из него же и выбираем, вместе с
        # неоднозначностью, если точных несколько. Ответ «нет» возвращает всё к
        # прежнему порядку: частичные ищутся среди живых, и только их отсутствие
        # открывает архив.
        whole = accounts.find(wanted, archived=True)
        if whole and whole[0].exact:
            return accounts.choose(wanted, archived=True).login
        try:
            return accounts.choose(wanted).login
        except NotFound:
            return accounts.choose(wanted, archived=True).login
    current = accounts.current()
    if current is not None:
        return current.login
    if client.settings.account:
        return client.settings.account
    raise DirectFailure(
        "Кабинет не выбран. Назовите его в --account, либо запомните: "
        "accounts.py --use <логин или название>."
    )


def stamp() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime())


def age_of(checked_at: str) -> str:
    """Возраст кэша словами. Пустая строка — если дата не разбирается."""
    try:
        moment = time.mktime(time.strptime(checked_at, "%Y-%m-%dT%H:%M:%S"))
    except (ValueError, TypeError):
        return ""
    days = int((time.time() - moment) // 86400)
    if days <= 0:
        return "сегодня"
    if days == 1:
        return "вчера"
    return f"{days} дн. назад"


__all__ = [
    "AGENCY", "BALANCE_CHUNK", "BY_DOMAIN", "BY_LOGIN", "BY_NAME",
    "CABINET_FIELDS", "CLIENT", "FIELDS", "LAYER", "RECORD",
    "Accounts", "Ambiguous", "Cabinet", "Match", "NotFound", "account_issues",
    "agency_cabinets", "age_of", "balances", "cabinets_said", "detect",
    "domain_of", "fingerprint", "fold", "read_cabinets", "read_record",
    "resolve_account", "stamp",
    "tsv", "unpuny",
]
