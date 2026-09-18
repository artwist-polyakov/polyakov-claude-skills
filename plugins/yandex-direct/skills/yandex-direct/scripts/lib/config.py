"""Настройки скилла и вырезание секретов."""

from __future__ import annotations

import json
import os
import unicodedata
from pathlib import Path

SKILL_DIR = Path(__file__).resolve().parent.parent.parent
ENV_FILE = SKILL_DIR / "config" / ".env"
ENV_EXAMPLE = SKILL_DIR / "config" / ".env.example"
CONFIG_README = SKILL_DIR / "config" / "README.md"

API_HOST = "api.direct.yandex.com"

API_VERSION = "v501"

# Хост четвёртой версии не выводится из хоста версии 5, а задаётся отдельно:
# домены разные, и оба отвечают (расхождение D-08 в API_MAP.md). Адрес взят
# документированный.
API_HOST_V4 = "api.direct.yandex.ru"

PROFILES = {
    "production": ("продакшн", ""),
    "test_cabinet": ("тестовый кабинет", "_TEST_CABINET"),
}

LOCALES = ("ru", "en", "tr")
USE_OPERATOR_UNITS = ("auto", "never", "always")


# --------------------------------------------------------------------------
# Секреты
# --------------------------------------------------------------------------

# Короткое значение вырезать опаснее, чем оставить: подстрока из двух символов
# встретится в любом логине, и вывод станет нечитаемым.
SECRET_MIN_LENGTH = 8

MASK = "[токен вырезан]"

_SECRETS: list = []


def keep_secret(value: str) -> None:
    """Запомнить значение, которое не должно попасть в вывод.

    Вместе с самим значением запоминаются его экранированные записи: токен,
    попавший в `repr()` чужого исключения или в JSON-тело запроса, выглядит
    иначе, чем исходная строка, и замена по ней не срабатывает. Тело запроса —
    не гипотеза: четвёртая версия API берёт токен именно оттуда."""
    if not isinstance(value, str):
        return
    forms = (
        value,
        value.encode("unicode_escape").decode("ascii"),
        repr(value)[1:-1],
        json.dumps(value, ensure_ascii=False)[1:-1],
    )
    for form in forms:
        if len(form) >= SECRET_MIN_LENGTH and form not in _SECRETS:
            _SECRETS.append(form)


def forget_secrets() -> None:
    """Забыть все секреты. Нужно тестам, чтобы прогоны не влияли друг на друга."""
    _SECRETS.clear()


def redact(text: str) -> str:
    """Вырезать секреты из текста."""
    if not isinstance(text, str):
        text = str(text)
    # Длинные раньше коротких: если один секрет оказался началом другого,
    # замена короткого первой разрывает длинный, и его хвост печатается.
    for secret in sorted(_SECRETS, key=len, reverse=True):
        text = text.replace(secret, MASK)
    return text


def excerpt(text, limit: int = 200) -> str:
    """Кусок чужого текста для сообщения об ошибке.

    Секреты вырезаются до обрезки, а не после: токен, попавший на границу
    среза, иначе печатается началом — замена по обрубку не срабатывает.

    Пробельные последовательности схлопываются: предел в символах не спасает
    от сотни однобуквенных строк в чужом поле. Управляющие и форматирующие
    символы заменяются пробелом — `ESC[2J` из названия кабинета стирает
    пользователю экран вместе со всей сводкой. Одиночные суррогаты (`Cs`)
    json.loads пропускает, а UTF-8 не кодирует, и print() падает посреди уже
    начатой строки."""
    safe = "".join(
        " " if unicodedata.category(character) in ("Cc", "Cf", "Cs") else character
        for character in redact(str(text))
    )
    cleaned = " ".join(safe.split())
    if len(cleaned) <= limit:
        return cleaned
    return cleaned[:limit] + "…"


class DirectFailure(Exception):
    """Отказ, который нужно показать человеку.

    Текст собирается один раз при создании и уже вырезан: исключение
    пересказывают, оборачивают и печатают в разных местах, и полагаться на то,
    что каждое из них вспомнит про redact(), нельзя."""

    kind = "unknown"
    retryable = False

    def __init__(self, message: str, *, kind: str = None, retryable: bool = None):
        if kind is not None:
            self.kind = kind
        if retryable is not None:
            self.retryable = retryable
        super().__init__(redact(message))


# --------------------------------------------------------------------------
# Файл настроек
# --------------------------------------------------------------------------

def short(path: Path) -> str:
    """Путь в виде, удобном для чтения из текущего каталога."""
    try:
        return str(path.relative_to(Path.cwd()))
    except ValueError:
        return str(path)


def load_env_file(path: Path, strict: bool = True, on_value=None) -> dict:
    """Разбор `config/.env`: строки вида `КЛЮЧ=значение`.

    Комментарием считается только строка целиком: `#` внутри значения — часть
    токена, а не начало комментария.

    `on_value` вызывается для каждой прочитанной пары, а не только для той, что
    осталась в словаре: одна переменная может быть присвоена дважды, и первое
    значение до конца разбора не доживает. Для сбора секретов важно каждое.

    При `strict=False` негодные строки и нечитаемый файл пропускаются: это
    нужно предварительному сбору секретов, где падение на первой плохой строке
    потеряло бы токены, прочитанные до неё."""
    values: dict = {}
    if not path.is_file():
        return values
    try:
        # utf-8-sig, а не utf-8: редакторы дописывают в начало файла BOM, и он
        # не пробел — strip() его не снимает. Ключ превращается в
        # `﻿YANDEX_DIRECT_TOKEN`, и «токен не задан» приходит на файл, где
        # токен есть.
        # свой файл читается напрямую: `config/.env` — настройка скилла, а
        # не чужой ввод команды, и путь к ней задан модулем. Разбор стоит
        # ниже общего: `incoming` ввозит `DirectFailure` отсюда, и обратный
        # ввоз замкнул бы порядок импорта в кольцо.
        text = path.read_text(encoding="utf-8-sig")
    except OSError as exc:
        if not strict:
            return values
        raise DirectFailure(f"Не удалось прочитать {short(path)}: {exc}") from None
    except UnicodeDecodeError:
        if not strict:
            return values
        raise DirectFailure(
            f"{short(path)} не читается как UTF-8. Так выглядит файл, "
            f"сохранённый в UTF-16 — пересохраните его в UTF-8."
        ) from None
    for number, raw in enumerate(text.splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export "):].lstrip()
        key, sign, value = line.partition("=")
        if not sign:
            if not strict:
                continue
            raise DirectFailure(
                f"{short(path)}, строка {number}: ожидалась запись вида "
                f"КЛЮЧ=значение"
            )
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        key = key.strip()
        if on_value is not None:
            on_value(key, value)
        values[key] = value
    return values


def env_value(name: str, from_file: dict, environ=None) -> str:
    """Значение переменной: окружение процесса важнее файла.

    Важно наличие переменной, а не её непустота: `YANDEX_DIRECT_TOKEN=` в
    команде означает «не бери токен из файла», и падать обратно на файл здесь —
    противоположность заявленному приоритету."""
    environ = os.environ if environ is None else environ
    if name in environ:
        return (environ[name] or "").strip()
    return (from_file.get(name) or "").strip()


def header_safe(value: str, what: str, show: bool = True) -> str:
    """Значение, которое пойдёт в заголовок запроса.

    Пробел и управляющий символ отвергаются здесь, а не при отправке: перенос
    строки в заголовке http.client отклоняет сам, но общей ошибкой, из которой
    не понять, какая настройка виновата."""
    if any(
        character.isspace() or ord(character) < 32 or ord(character) == 127
        for character in value
    ):
        seen = f": {excerpt(value, 64)}" if show else ""
        raise DirectFailure(
            f"{what} содержит пробел или управляющий символ{seen}. "
            f"Похоже на значение, скопированное с переносом строки."
        )
    try:
        # http.client кодирует заголовки в latin-1, и кириллица падает
        # UnicodeEncodeError уже на отправке — общей трассировкой, из которой не
        # понять, что настройку набрали не той раскладкой.
        value.encode("latin-1")
    except UnicodeEncodeError:
        seen = f": {excerpt(value, 64)}" if show else ""
        raise DirectFailure(
            f"{what} содержит символы, недопустимые в заголовке запроса{seen}. "
            f"Логин кабинета и язык записываются латиницей."
        ) from None
    return value


class Settings:
    """Разобранная конфигурация одного контура."""

    __slots__ = (
        "profile", "host", "version", "host_v4", "token", "token_var",
        "token_borrowed", "account", "locale", "operator_units",
    )

    def __init__(self, **values):
        for name in self.__slots__:
            setattr(self, name, values[name])

    @property
    def profile_ru(self) -> str:
        return PROFILES[self.profile][0]

    def __repr__(self) -> str:
        # Токен в repr не попадает даже вырезанным: объект настроек печатают
        # при отладке чаще всего, и одного забытого redact() там достаточно.
        return (
            f"<Settings {self.profile} host={self.host} "
            f"account={self.account or '—'} locale={self.locale}>"
        )


def no_token_message(profile: str, suffix: str) -> str:
    expected = "YANDEX_DIRECT_TOKEN" + suffix
    if suffix:
        expected += " или YANDEX_DIRECT_TOKEN"
    lines = [f"Токен не задан: для профиля «{profile}» ожидается {expected}."]
    if not ENV_FILE.is_file():
        lines += [
            f"Файла {short(ENV_FILE)} нет. Скопируйте образец и заполните его:",
            f"    cp {short(ENV_EXAMPLE)} {short(ENV_FILE)}",
        ]
    lines.append(f"Как получить токен — {short(CONFIG_README)}.")
    return "\n".join(lines)


def resolve_settings(profile=None, account=None, from_file=None, environ=None) -> Settings:
    """Настройки контура из окружения и файла."""
    from_file = from_file or {}
    profile = profile or env_value("YANDEX_DIRECT_ENV", from_file, environ) or "production"
    if profile not in PROFILES:
        raise DirectFailure(
            f"Неизвестный профиль «{excerpt(profile, 48)}» в YANDEX_DIRECT_ENV. "
            f"Допустимо: {', '.join(PROFILES)}"
        )
    _, suffix = PROFILES[profile]

    token_var = "YANDEX_DIRECT_TOKEN" + suffix
    token = env_value(token_var, from_file, environ)
    borrowed = False
    if not token and suffix:
        token_var = "YANDEX_DIRECT_TOKEN"
        token = env_value(token_var, from_file, environ)
        borrowed = bool(token)
    if not token:
        raise DirectFailure(no_token_message(profile, suffix))
    # Значение токена не показывается даже в жалобе на его формат.
    header_safe(token, f"Токен из {token_var}", show=False)

    locale = env_value("YANDEX_DIRECT_LOCALE", from_file, environ) or "ru"
    if locale not in LOCALES:
        raise DirectFailure(
            f"YANDEX_DIRECT_LOCALE={excerpt(locale, 32)} — Директ принимает "
            f"только {', '.join(LOCALES)} (references/ERRORS_AND_LIMITS.md)."
        )

    operator_units = (
        env_value("YANDEX_DIRECT_USE_OPERATOR_UNITS", from_file, environ) or "auto"
    )
    if operator_units not in USE_OPERATOR_UNITS:
        raise DirectFailure(
            f"YANDEX_DIRECT_USE_OPERATOR_UNITS={excerpt(operator_units, 48)} — "
            f"допустимо: {', '.join(USE_OPERATOR_UNITS)}"
        )

    default_account = env_value("YANDEX_DIRECT_ACCOUNT" + suffix, from_file, environ)
    return Settings(
        profile=profile,
        host=API_HOST,
        version=API_VERSION,
        host_v4=API_HOST_V4,
        token=token,
        token_var=token_var,
        token_borrowed=borrowed,
        account=header_safe((account or default_account).strip(), "Логин кабинета"),
        locale=header_safe(locale, "YANDEX_DIRECT_LOCALE"),
        operator_units=operator_units,
    )


def preload_secrets(env_file=None, environ=None) -> None:
    """Запомнить все токены источников настроек, ничего не разбирая.

    Вызывается до разбора настроек — и до разбора аргументов командной строки.
    Второе не менее важно первого: argparse печатает негодный аргумент сам, и
    токен, случайно попавший в командную строку, уходит в stderr раньше, чем
    скилл успевает узнать, что он токен. Вырезать можно только то, что уже
    запомнено.

    Разбор нестрогий: одна негодная строка файла не должна отменять регистрацию
    токенов, прочитанных до неё, — иначе именно они и утекут в сообщение об
    ошибке, которое эту строку объясняет. Через `on_value`, а не по итоговому
    словарю: переменную можно присвоить дважды, и перезаписанное значение
    секретом быть не перестаёт."""
    path = Path(env_file) if env_file else ENV_FILE

    def remember(name: str, value: str) -> None:
        if "TOKEN" in name.upper():
            keep_secret(value)

    load_env_file(path, strict=False, on_value=remember)
    for name, value in (environ if environ is not None else os.environ).items():
        if name.startswith("YANDEX_"):
            remember(name, value)


def settings_from_env(profile=None, account=None, env_file=None, environ=None) -> Settings:
    """Настройки из `config/.env` и переменных окружения, с учётом секретов.

    Токены запоминаются до разбора: сообщение о негодном значении не должно
    печатать соседний токен, прочитанный из того же файла."""
    path = Path(env_file) if env_file else ENV_FILE
    preload_secrets(path, environ)
    settings = resolve_settings(profile, account, load_env_file(path), environ)
    keep_secret(settings.token)
    return settings
