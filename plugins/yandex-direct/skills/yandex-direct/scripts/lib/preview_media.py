"""Изображения и миниатюры для автономного HTML, без авторизации и файлов."""

import base64
import hashlib
import http.client
import ipaddress
import socket
import time
from urllib.parse import urljoin, urlsplit, urlunsplit

MAX_RESOURCE = 10 * 1024 * 1024
MAX_TOTAL = 50 * 1024 * 1024
TIMEOUT = 5
MAX_SECONDS = 15
MAX_REDIRECTS = 5
# Прозрачный DNS-прокси окружения может выдавать служебные IP для внешних узлов.
# Исключение действует только для известного CDN изображений Директа и при TLS.
DNS_PROXY_CDN_HOSTS = frozenset({"avatars.mds.yandex.net", "direct.yandex.ru"})
DNS_PROXY_RANGE = ipaddress.ip_network("198.18.0.0/15")


class MediaError(ValueError):
    """Сообщение, которое можно показать на карточке изображения."""


def _normalize(url):
    if not isinstance(url, str) or not url:
        raise MediaError("Адрес изображения или миниатюры не указан.")
    if any(ord(char) < 33 or ord(char) == 127 for char in url):
        raise MediaError("Некорректный адрес изображения.")
    if url.startswith("//"):
        url = "https:" + url
    try:
        parts = urlsplit(url)
        # Ads.get может вернуть новую картинку по HTTP, хотя AdImages.get
        # отдаёт этот же официальный адрес по HTTPS. Наружу HTTP не отправляем.
        if (parts.scheme == "http" and parts.hostname == "direct.yandex.ru"
                and parts.path.startswith("/images/direct/")
                and parts.port in (None, 80)
                and parts.username is None and parts.password is None):
            parts = parts._replace(scheme="https", netloc="direct.yandex.ru")
        valid = (parts.scheme == "https" and parts.hostname and
                 parts.username is None and parts.password is None and
                 parts.port in (None, 443))
    except ValueError:
        valid = False
    if not valid:
        raise MediaError("Разрешены только публичные HTTPS-адреса на порту 443.")
    return urlunsplit(("https", parts.netloc, parts.path or "/", parts.query, ""))


def _public_connection(address, timeout, source_address=None):
    """Проверяем DNS один раз; подключаемся к тому же IP, сохраняя TLS-проверку."""
    host, port = address
    try:
        ipaddress.ip_address(host)
    except ValueError:
        pass
    else:
        raise MediaError("Прямые IP-адреса изображений запрещены; укажите имя сайта.")
    resolved = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    if not resolved:
        raise MediaError("Не удалось определить адрес сервера изображения.")
    for _, _, _, _, target in resolved:
        ip = ipaddress.ip_address(target[0].split("%", 1)[0])
        proxy_cdn = host.lower() in DNS_PROXY_CDN_HOSTS and ip in DNS_PROXY_RANGE
        if not proxy_cdn and (not ip.is_global or ip.is_multicast or ip.is_reserved):
            raise MediaError("Локальные и непубличные адреса изображений запрещены.")
    deadline = time.monotonic() + timeout
    for family, socktype, protocol, _, target in resolved:
        connection = socket.socket(family, socktype, protocol)
        try:
            connection.settimeout(max(0.001, deadline - time.monotonic()))
            connection.connect(target)
            return connection
        except OSError:
            connection.close()
            if time.monotonic() >= deadline:
                break
    raise MediaError("Не удалось подключиться к серверу изображения.")


def _mime(data):
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if data.startswith((b"GIF87a", b"GIF89a")):
        return "image/gif"
    if data.startswith(b"RIFF") and data[8:12] == b"WEBP":
        return "image/webp"
    raise MediaError("Поддерживаются только PNG, JPEG, GIF и WebP; SVG и HTML исключены.")


def _fetch(url, budget):
    """Возвращает байты либо адрес перенаправления; budget учитывает и ошибки."""
    if budget[0] <= 0:
        raise MediaError("Достигнут общий предел загрузки изображений: 50 МБ.")
    parts = urlsplit(url)
    connection = http.client.HTTPSConnection(parts.hostname, 443, timeout=TIMEOUT)
    # HTTPSConnection оставляет имя узла для SNI и проверки сертификата.
    # Cookies, заголовки Директа и настройки прокси из окружения не читаются.
    connection._create_connection = _public_connection
    deadline = time.monotonic() + MAX_SECONDS
    try:
        path = urlunsplit(("", "", parts.path, parts.query, ""))
        connection.request("GET", path, headers={"Accept": "image/png,image/jpeg,image/gif,image/webp"})
        response = connection.getresponse()
        with response:
            if response.status in (301, 302, 303, 307, 308):
                location = response.getheader("Location")
                if not location:
                    raise MediaError("Сервер не указал адрес перенаправления.")
                return urljoin(url, location)
            if response.status != 200:
                raise MediaError(f"Сервер изображения вернул HTTP {response.status}.")
            length = response.getheader("Content-Length")
            if length and length.isdecimal() and int(length) > min(MAX_RESOURCE, budget[0]):
                raise MediaError("Изображение превышает предел 10 МБ или остаток общего лимита 50 МБ.")
            data = bytearray()
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise MediaError("Истекло время загрузки изображения.")
                if connection.sock is not None:
                    connection.sock.settimeout(min(TIMEOUT, remaining))
                size = min(65536, MAX_RESOURCE - len(data) + 1, budget[0])
                if size <= 0:
                    if response.length == 0:
                        break
                    raise MediaError("Достигнут общий предел загрузки изображений: 50 МБ.")
                chunk = response.read1(size)
                if not chunk:
                    break
                budget[0] -= len(chunk)
                if len(data) + len(chunk) > MAX_RESOURCE:
                    raise MediaError("Изображение превышает предел 10 МБ или общий лимит 50 МБ.")
                data.extend(chunk)
                if len(data) >= 12:
                    _mime(data)
            if response.length not in (None, 0):
                raise MediaError("Загрузка изображения прервалась до получения всех данных.")
            return bytes(data)
    finally:
        connection.close()


def _load(url, budget, cache, seen=()):
    if url in cache:
        return cache[url]
    try:
        if url in seen or len(seen) > MAX_REDIRECTS:
            raise MediaError("Слишком много перенаправлений при загрузке изображения.")
        result = _fetch(url, budget)
        if isinstance(result, str):
            result = _load(_normalize(result), budget, cache, (*seen, url))
        else:
            mime = _mime(result)
            result = {"src": f"data:{mime};base64," + base64.b64encode(result).decode("ascii"),
                      "digest": hashlib.sha256(result).hexdigest()}
    except MediaError as error:
        result = {"src": None, "error": str(error)}
    except (OSError, ValueError, http.client.HTTPException):
        result = {"src": None, "error": "Не удалось загрузить изображение: ошибка сети или адреса."}
    cache[url] = result
    return result


def embed_media(items, *, load=True):
    """Сохраняет метаданные; добавляет src и digest либо src=None и error.

    Для kind='video' URL должен указывать на миниатюру, не на видеофайл.
    Повторные URL (включая адреса перенаправлений) загружаются один раз за вызов.
    """
    cache, budget, embedded = {}, [MAX_TOTAL], []
    for item in items:
        output = {key: value for key, value in item.items() if key not in ("src", "error", "digest")}
        if not load:
            result = {"src": None, "error": "Загрузка изображений отключена."}
        else:
            try:
                url = _normalize(item.get("url"))
                result = _load(url, budget, cache)
            except MediaError as error:
                result = {"src": None, "error": str(error)}
        output.update(result)
        embedded.append(output)
    return embedded
