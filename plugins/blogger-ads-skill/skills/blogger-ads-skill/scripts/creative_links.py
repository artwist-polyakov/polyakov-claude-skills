"""Прочитать исходную карту ссылок без сортировки, очистки адресов или потери якорей."""
from urllib.parse import urlsplit


def is_url(value):
    if not isinstance(value, str) or not value or any(char.isspace() for char in value):
        return False
    try:
        parsed = urlsplit(value)
        return parsed.scheme in ("http", "https") and bool(parsed.hostname)
    except ValueError:
        return False


def header(row):
    if not isinstance(row, list) or len(row) not in (2, 3) or not all(isinstance(cell, str) for cell in row):
        return False
    if any(is_url(cell) for cell in row):
        return False  # Настоящий адрес не становится заголовком из-за /url в пути.
    anchor, address = [cell.strip().casefold() for cell in row[-2:]]
    anchors = {"якорь", "фраза-ссылка (якорь)", "фраза-ссылка", "текст ссылки", "anchor", "link text"}
    addresses = {"url", "адрес", "адрес (url)", "address", "url address"}
    labels = anchor in anchors and address in addresses
    return labels and (len(row) == 2 or row[0].strip().casefold() in {"№", "#", "номер", "no", "no.", "number"})


def parse_links(value):
    """URL-список, объекты anchor/url или таблица [номер?, якорь, URL/пояснение]."""
    message = "Карта ссылок: нужен JSON-список URL, объектов anchor/url или строк таблицы [номер?, якорь, URL]; передайте исходный файл без ручного преобразования"
    if not isinstance(value, list):
        raise ValueError(message)
    if not value:
        return []
    if all(isinstance(row, str) for row in value):
        if not all(is_url(row) for row in value):
            raise ValueError(message + "; адреса должны быть абсолютными http/https URL без пробелов")
        return [{"anchor": url, "url": url} for url in value]
    rows = value[1:] if header(value[0]) else value
    result = []
    for index, row in enumerate(rows, 1):
        if isinstance(row, dict) and set(row) == {"anchor", "url"}:
            anchor, url = row["anchor"], row["url"]
            if not is_url(url):
                raise ValueError(f"Карта ссылок, строка {index}: url должен быть абсолютным http/https адресом без пробелов")
        elif isinstance(row, list) and len(row) in (2, 3):
            if len(row) == 3 and str(row[0]) != str(index):
                raise ValueError(f"Карта ссылок, строка {index}: номер должен совпадать с порядком строки; сортировать таблицу автоматически нельзя")
            anchor, address = row[-2:]
            if is_url(address):
                url = address
            elif is_url(anchor) and isinstance(address, str) and address.strip() and not any(mark in address.casefold() for mark in ("http:", "https:", "://")):
                # В таблице видимый URL может стоять в ячейке якоря,
                # а соседняя ячейка — лишь пояснять отсутствие скрытого перехода.
                url = anchor
            else:
                raise ValueError(f"Карта ссылок, строка {index}: адрес неоднозначен; нужен URL в адресной ячейке либо отдельный видимый URL в якоре и пояснение рядом")
        else:
            raise ValueError(message + f"; неподдерживаемая строка {index}")
        if not isinstance(anchor, str) or not anchor.strip():
            raise ValueError(f"Карта ссылок, строка {index}: нужен непустой якорь; автоматически подменять его URL нельзя")
        result.append({"anchor": anchor, "url": url})
    return result


def field_values(link_map):
    fields = {"LINK_COUNT": str(len(link_map))}
    for index, row in enumerate(link_map, 1):
        fields[f"LINK_{index}_ANCHOR"] = row["anchor"]
        fields[f"LINK_{index}_URL"] = row["url"]
    return fields
