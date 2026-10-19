#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = ["pypdf>=5,<7"]
# ///
"""Заполнить отдельный DOCX с сохранением OOXML; для связи со сверкой используется pypdf."""
import argparse
import json
import hashlib
import importlib.util
import struct
import re
from pathlib import Path
from xml.dom import minidom
from zipfile import ZIP_DEFLATED, ZipFile

_LINK_SPEC = importlib.util.spec_from_file_location("creative_links", Path(__file__).with_name("creative_links.py"))
creative_links = importlib.util.module_from_spec(_LINK_SPEC)
_LINK_SPEC.loader.exec_module(creative_links)


W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
TOKEN = re.compile(r"{{([A-Z][A-Z0-9_]*)}}")
TEMPLATES = Path(__file__).resolve().parents[1] / "assets" / "templates"
EMOJI_FONT = TEMPLATES / "NotoEmoji.ttf"
EMOJI = re.compile(r"([\U0001F000-\U0001FAFF\u2600-\u27BF\uFE0E\uFE0F\u200D\u20E3]+)")
LINK_FIELDS = {"LINK_INDEX", "LINK_ANCHOR", "LINK_URL"}
EXCHANGE_FIELDS = {"EXCHANGE_CLAUSE", "CLOSING_DOCUMENTS_CLAUSE"}
UNKNOWN_CLAIMS_CONTACT = "[УТОЧНИТЬ ПОДТВЕРЖДЁННЫЙ КОНТАКТ ИСПОЛНИТЕЛЯ]"


def nodes(element, name):
    return list(element.getElementsByTagNameNS(W, name))


def text_of(element):
    return "".join(child.data for child in element.childNodes if child.nodeType == child.TEXT_NODE)


def set_text(element, value):
    """Новые строки и табуляция становятся w:br/w:tab; XML экранирует сериализатор."""
    while element.firstChild:
        element.removeChild(element.firstChild)
    normalized = value.replace("\r\n", "\n").replace("\r", "\n")
    pieces = re.split(r"([\n\t])", normalized)
    element.setAttribute("xml:space", "preserve")
    element.appendChild(element.ownerDocument.createTextNode(pieces[0]))
    previous = element
    for separator, piece in zip(pieces[1::2], pieces[2::2]):
        control = element.ownerDocument.createElementNS(W, "w:tab" if separator == "\t" else "w:br")
        new = element.cloneNode(False)
        new.appendChild(element.ownerDocument.createTextNode(piece))
        parent = element.parentNode
        parent.insertBefore(control, previous.nextSibling)
        parent.insertBefore(new, control.nextSibling)
        previous = new


def validate_field_boundaries(text, fields):
    """Отклонить явные дубли на стыке; значения и исходный текст не исправлять."""
    for match in TOKEN.finditer(text):
        key = match.group(1)
        # В посте и адресе пунктуация может быть частью дословного исходника.
        if key == "POST_TEXT" or key.endswith("_URL"):
            continue
        value = fields[key].strip(" \t")
        before = text[:match.start()].rstrip(" \t")
        after = text[match.end():].lstrip(" \t")
        for side, first, second in (
            ("слева", before[-1:], value[:1]),
            ("справа", value[-1:], after[:1]),
        ):
            if first and first == second and first in ".,;:":
                raise ValueError(f"Поле {key} дублирует знак {first!r} {side} на границе с шаблоном; исправьте поле или шаблон. Значение не изменено")
        if key in {"CUSTOMER_PARTY", "CONTRACTOR_PARTY"} and after.startswith(",") and value.endswith((".", ";", ":", "!", "?")):
            raise ValueError(f"Поле {key} завершает вводную часть знаком перед запятой шаблона; закончите поле обозначением роли стороны без конечного знака. Значение не изменено")
        # Только известная метка и готовая фраза; не угадываем повторы слов
        # в именах файлов вроде «Медиа 01.mp4» или в произвольном тексте.
        if key == "MEDIA_MANIFEST" and before.endswith("Медиа:"):
            media = value.casefold()
            if re.match(r"медиа(?:\s*:|\s+(?:нет|отсутствует|отсутствуют)(?=$|[.!?…](?:\s|$)|[\s,;:]))", media):
                raise ValueError("Поле MEDIA_MANIFEST повторяет метку «Медиа:» из шаблона; укажите только содержимое, например «отсутствуют» или имена файлов. Значение не изменено")


def substitute(paragraph, fields):
    """Заменять справа налево, чтобы работали токены, разбитые Word на runs."""
    validate_field_boundaries(paragraph_text(paragraph), fields)
    ts = nodes(paragraph, "t")
    strings = [text_of(t) for t in ts]
    combined = "".join(strings)
    spans, offset = [], 0
    for t, value in zip(ts, strings):
        spans.append((offset, offset + len(value), t))
        offset += len(value)
    for match in reversed(list(TOKEN.finditer(combined))):
        touched = [(start, end, t) for start, end, t in spans
                   if start < match.end() and end > match.start()]
        for index, (start, end, t) in enumerate(touched):
            current = text_of(t)
            left = max(0, match.start() - start)
            right = min(end, match.end()) - start
            value = fields[match.group(1)] if index == 0 else ""
            set_text(t, current[:left] + value + current[right:])


def parse_parts(template):
    with ZipFile(template) as package:
        parts = {name: package.read(name) for name in package.namelist()}
    documents = {}
    for name, data in parts.items():
        if name.startswith("word/") and name.endswith(".xml") and (b"{" in data or b"}" in data):
            doc = minidom.parseString(data)
            if any("{{" in "".join(text_of(t) for t in nodes(p, "t"))
                   or "}}" in "".join(text_of(t) for t in nodes(p, "t"))
                   for p in nodes(doc, "p")):
                documents[name] = doc
    return parts, documents


def list_fields(template):
    _, documents = parse_parts(template)
    result = set()
    for doc in documents.values():
        for paragraph in nodes(doc, "p"):
            result.update(TOKEN.findall("".join(text_of(t) for t in nodes(paragraph, "t"))))
    if result & LINK_FIELDS:
        result -= LINK_FIELDS
        result.add("LINK_COUNT")
        result.update({"LINK_<N>_ANCHOR", "LINK_<N>_URL"})
    if Path(template).resolve() == (TEMPLATES / "contract.docx").resolve():
        result.add("EXCHANGE_METHOD")
    return sorted(result)


def expand_links(doc, fields):
    for row in list(nodes(doc, "tr")):
        row_text = "".join(text_of(t) for t in nodes(row, "t"))
        if "{{LINK_INDEX}}" not in row_text:
            continue
        count = fields.get("LINK_COUNT")
        if not isinstance(count, str) or not re.fullmatch(r"0|[1-9][0-9]{0,2}", count):
            raise ValueError("LINK_COUNT должен быть строкой с целым числом от 0 до 999")
        for index in range(1, int(count) + 1):
            anchor, url = f"LINK_{index}_ANCHOR", f"LINK_{index}_URL"
            for key in (anchor, url):
                if not fields.get(key, "").strip():
                    raise ValueError(f"Не заполнено поле {key}")
            clone = row.cloneNode(True)
            local = {"LINK_INDEX": str(index), "LINK_ANCHOR": fields[anchor], "LINK_URL": fields[url]}
            for paragraph in nodes(clone, "p"):
                substitute(paragraph, local)
            row.parentNode.insertBefore(clone, row)
        row.parentNode.removeChild(row)


def review_changes(directory):
    spec = importlib.util.spec_from_file_location("contract_review", Path(__file__).with_name("review.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.amendment_changes(directory)


def verify_review_document(source, directory=None):
    """Проверить связь собственного DOCX со сверкой, не исправляя документ."""
    source = Path(source).resolve()
    saved = json.loads(source.with_suffix(source.suffix + ".fields.json").read_text(encoding="utf-8"))
    if not isinstance(saved, dict):
        raise ValueError("Карта .fields.json должна быть JSON-объектом")
    proof = saved.get("review_source")
    if not isinstance(proof, dict):
        raise ValueError("Допсоглашение требует сборки --template amendment --review DIR; ручной пересказ поправок не подходит")
    review_dir = Path(proof["directory"]).resolve()
    if directory is not None and review_dir != Path(directory).resolve():
        raise ValueError("Допсоглашение связано с прежней сверкой; подготовьте отдельную версию по текущему review")
    expected = review_changes(review_dir)
    if proof != expected["review_source"]:
        raise ValueError("SHA исходной сверки допсоглашения не совпадает; сохраните новую версию")
    if saved.get("output_sha256") != hashlib.sha256(source.read_bytes()).hexdigest():
        raise ValueError("DOCX допсоглашения изменён после сборки; ручные изменения не перезаписываются")
    text = expected["text"]
    if saved.get("fields", {}).get("AMENDMENT_CHANGES") != text:
        raise ValueError("AMENDMENT_CHANGES не совпадает с точными поправками review")
    _, documents = read_documents(source)
    if not any(paragraph_text(paragraph) == "1. " + text for doc in documents.values() for paragraph in nodes(doc, "p")):
        raise ValueError("Текст допсоглашения не содержит точные поправки review в пункте 1")
    return proof


def scan_exchange_fields(data):
    """Два пункта из одного выбранного канала; неизвестные сведения не угадываются."""
    required = {"channel", "channel_name", "contractor_contact", "customer_contact", "agreement",
                "closing_document", "closing_sender", "closing_deadline", "followup_method", "followup_deadline"}
    if not isinstance(data, dict) or set(data) != required:
        raise ValueError("--exchange-file: используйте все поля assets/exchange.example.json без лишних ключей; неизвестные значения — null")
    enums = {"channel": {"telegram", "email", "other", "unknown"},
             "agreement": {"proposed", "agreed", "unknown"},
             "closing_sender": {"contractor", "customer", "unknown"},
             "followup_method": {"edo", "paper", "none", "unknown"}}
    for key, choices in enums.items():
        if not isinstance(data[key], str) or data[key] not in choices:
            raise ValueError(f"--exchange-file: {key} должен быть одним из {', '.join(sorted(choices))}")
    for key in required - enums.keys():
        value = data[key]
        if value is not None and (not isinstance(value, str) or not value.strip() or value != value.strip() or any(c in value for c in "\r\n\t{}")):
            raise ValueError(f"--exchange-file: {key} — непустая строка без крайних пробелов, переводов строк и маркеров либо null")
    channel = data["channel"]
    if (channel == "other") != (data["channel_name"] is not None):
        raise ValueError("--exchange-file: channel_name обязателен только при channel=other; для остальных каналов укажите null")
    contacts = [data["contractor_contact"], data["customer_contact"]]
    if channel == "unknown" and any(contacts):
        raise ValueError("--exchange-file: при неизвестном канале контакты в нём тоже неизвестны; укажите null")
    for contact in filter(None, contacts):
        if channel == "telegram" and not re.fullmatch(r"(?:@[A-Za-z0-9_]+|https://t\.me/[A-Za-z0-9_]+|\+[1-9][0-9]{6,14})", contact):
            raise ValueError("--exchange-file: контакт Telegram — @username, https://t.me/username или международный номер; email сюда не подставляется")
        if channel == "email" and not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", contact):
            raise ValueError("--exchange-file: для channel=email нужен адрес электронной почты; аккаунт мессенджера не подходит")
    if data["followup_method"] not in {"edo", "paper"} and data["followup_deadline"] is not None:
        raise ValueError("--exchange-file: срок последующего обмена задаётся только при followup_method=edo или paper")
    channel_text = {"telegram": "Telegram", "email": "электронную почту",
                    "other": data["channel_name"], "unknown": "[КАНАЛ ОБМЕНА]"}[channel]
    contact_kind = {"telegram": "АККАУНТ TELEGRAM", "email": "EMAIL", "other": "КОНТАКТ В ВЫБРАННОМ КАНАЛЕ",
                    "unknown": "КОНТАКТ"}[channel]
    contractor = data["contractor_contact"] or f"[{contact_kind} ИСПОЛНИТЕЛЯ]"
    customer = data["customer_contact"] or f"[{contact_kind} ЗАКАЗЧИКА]"
    followup_deadline = data["followup_deadline"] or "[СРОК]"
    followup = {"edo": "После обмена сканами Стороны оформляют те же документы через ЭДО. Срок последующего ЭДО: "
                       + followup_deadline + ".",
                "paper": "После обмена сканами Стороны обмениваются бумажными оригиналами тех же документов. Срок обмена оригиналами: "
                         + followup_deadline + ".",
                "none": "Последующий обмен через ЭДО или бумажными оригиналами не предусмотрен.",
                "unknown": "Последующий обмен: [ПОРЯДОК И СРОК]."}[data["followup_method"]]
    common = (f"Подписанные сканы передаются обеими Сторонами через {channel_text} "
              f"(Исполнитель: {contractor}; Заказчик: {customer}). {followup}")
    sender = {"contractor": "Исполнитель направляет Заказчику", "customer": "Заказчик направляет Исполнителю",
              "unknown": "[СТОРОНА] направляет другой Стороне"}[data["closing_sender"]]
    document = data["closing_document"] or "[ЗАКРЫВАЮЩИЙ ДОКУМЕНТ]"
    deadline = data["closing_deadline"] or "[СРОК И НАЧАЛО ОТСЧЁТА]"
    return {"EXCHANGE_CLAUSE": "Этот порядок применяется к Договору, приложениям и закрывающим документам. " + common,
            "CLOSING_DOCUMENTS_CLAUSE": f"{sender} {document}. Срок направления: {deadline}. {common}"}


def render(template, fields, output, force=False, post_file=None, review_dir=None, links_file=None, exchange_file=None):
    template, output = Path(template), Path(output)
    if template.resolve() == output.resolve():
        raise ValueError("Шаблон нельзя перезаписать даже с --force")
    if output.exists() and review_dir is not None:
        raise ValueError("Файл уже существует; для --review выберите новую версию, перезапись запрещена")
    if output.exists() and not force:
        raise ValueError("Файл уже существует; выберите новую версию или укажите --force")
    validate_values(fields)
    exchange_source = None
    own_contract = template.resolve() == (TEMPLATES / "contract.docx").resolve()
    if exchange_file is not None and not own_contract:
        raise ValueError("--exchange-file применяется только к собственному шаблону contract")
    if own_contract:
        method = fields.get("EXCHANGE_METHOD")
        if method not in {"scans", "edo", "paper", "unknown"}:
            raise ValueError("Укажите EXCHANGE_METHOD: scans (в том числе временные сканы при ЭДО), edo, paper или unknown")
        if method == "scans" and exchange_file is None:
            raise ValueError("EXCHANGE_METHOD=scans требует --exchange-file; оба пункта обмена формируются из единого источника")
        if exchange_file is not None:
            if method != "scans":
                raise ValueError("--exchange-file требует EXCHANGE_METHOD=scans, включая временные сканы при основном ЭДО")
            exchange_file = Path(exchange_file)
            raw = exchange_file.read_bytes()
            generated = scan_exchange_fields(json.loads(raw.decode("utf-8")))
            for key in EXCHANGE_FIELDS:
                if key in fields and fields[key] != generated[key]:
                    raise ValueError(f"{key} противоречит --exchange-file; удалите ручное поле: оба пункта формируются из общего канала и отдельных условий")
            fields = fields | generated
            exchange_source = {"path": str(exchange_file.resolve()), "sha256": hashlib.sha256(raw).hexdigest()}
    review_source = None
    if review_dir is not None:
        if template.resolve() != (TEMPLATES / "amendment.docx").resolve():
            raise ValueError("--review применяется только к собственному шаблону amendment")
        changes = review_changes(review_dir)
        if "AMENDMENT_CHANGES" in fields and fields["AMENDMENT_CHANGES"] != changes["text"]:
            raise ValueError("Удалите ручной AMENDMENT_CHANGES: с --review текст поправок формируется из проверенной сверки без пересказа")
        fields = fields | {"AMENDMENT_CHANGES": changes["text"]}
        review_source = changes["review_source"]
    if template.resolve() == (TEMPLATES / "contract.docx").resolve():
        if "CLAIMS_CLAUSE" in fields:
            raise ValueError("CLAIMS_CLAUSE больше не используется в собственном шаблоне договора. "
                             "Перенесите срок в CLAIMS_DEADLINE, требования в CLAIMS_EVIDENCE_REQUIREMENTS "
                             "и подтверждённый контакт Исполнителя в CONTRACTOR_CLAIMS_CONTACT; "
                             "роли Заказчика и Исполнителя уже закреплены в шаблоне")
        if not fields.get("CONTRACTOR_CLAIMS_CONTACT", "").strip():
            fields = fields | {"CONTRACTOR_CLAIMS_CONTACT": UNKNOWN_CLAIMS_CONTACT}
    post_source = None
    if post_file is not None:
        if "POST_TEXT" not in list_fields(template):
            raise ValueError("--post-file применим только к шаблону с POST_TEXT")
        post_file = Path(post_file)
        raw = post_file.read_bytes()
        text = raw.decode("utf-8")
        if "POST_TEXT" in fields and fields["POST_TEXT"] != text:
            raise ValueError("POST_TEXT не совпадает с --post-file. Сохраните исходный текст дословно, включая ERID и пробелы; изменения требуют отдельной версии и нового согласования")
        fields = fields | {"POST_TEXT": text}
        post_source = {"path": str(post_file.resolve()), "sha256": hashlib.sha256(raw).hexdigest()}
    links_source = None
    if links_file is not None:
        if "LINK_COUNT" not in list_fields(template):
            raise ValueError("--links-file применим только к шаблону с таблицей ссылок")
        links_file = Path(links_file)
        raw = links_file.read_bytes()
        generated = creative_links.field_values(creative_links.parse_links(json.loads(raw.decode("utf-8"))))
        for key, value in fields.items():
            if key == "LINK_COUNT" or re.fullmatch(r"LINK_[0-9]+_(ANCHOR|URL)", key):
                if generated.get(key) != value:
                    raise ValueError(f"{key} противоречит --links-file; удалите ручное поле и используйте исходную карту ссылок без перестановки или подмены якорей")
        fields = fields | generated
        links_source = {"path": str(links_file.resolve()), "sha256": hashlib.sha256(raw).hexdigest()}
    validate_values(fields)
    parts, documents = parse_parts(template)
    if not documents:
        raise ValueError("В шаблоне не найдены поля {{UPPER_SNAKE_CASE}}")
    has_emoji = False
    for name, doc in documents.items():
        expand_links(doc, fields)
        for paragraph in nodes(doc, "p"):
            required = TOKEN.findall("".join(text_of(t) for t in nodes(paragraph, "t")))
            for key in required:
                if not fields.get(key, "").strip():
                    raise ValueError(f"Не заполнено поле {key}")
            substitute(paragraph, fields)
            remaining = "".join(text_of(t) for t in nodes(paragraph, "t"))
            if "{{" in remaining or "}}" in remaining:
                raise ValueError(f"В {name} остались неподдерживаемые маркеры")
        has_emoji = prepare_emoji(doc) or has_emoji
        parts[name] = doc.toxml(encoding="utf-8")
        if b"{{" in parts[name] or b"}}" in parts[name]:
            raise ValueError(f"В {name} остались неподдерживаемые маркеры")
    if has_emoji:
        embed_emoji_font(parts)
    if template.resolve().parent == TEMPLATES.resolve():
        audit = audit_parts(parts, documents)
        if not audit["ok"]:
            raise ValueError("Проверка шрифтов не пройдена: " + json.dumps(audit["issues"], ensure_ascii=False))
    sidecar = output.with_suffix(output.suffix + ".fields.json")
    if sidecar.exists() and (not force or review_dir is not None):
        raise ValueError("Карта полей результата уже существует; выберите новую версию")
    write_package(parts, output, force)
    sidecar.write_text(json.dumps({
        "template": str(template.resolve()),
        "template_sha256": hashlib.sha256(template.read_bytes()).hexdigest(),
        "output_sha256": hashlib.sha256(output.read_bytes()).hexdigest(),
        "fields": fields,
        "post_source": post_source,
        "links_source": links_source,
        "review_source": review_source,
        "exchange_source": exchange_source,
    }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    return output


def paragraph_text(paragraph):
    """Видимый текст, включая переводы строки и табуляцию Word."""
    result = []
    def walk(node):
        if node.nodeType == node.ELEMENT_NODE and node.namespaceURI == W:
            if node.localName in {"pPr", "rPr"}:
                return  # w:tabs описывает позицию, а не символ табуляции в тексте.
            if node.localName == "t":
                result.append(text_of(node)); return
            if node.localName in {"br", "cr", "tab"}:
                result.append("\t" if node.localName == "tab" else "\n"); return
        for child in node.childNodes:
            walk(child)
    walk(paragraph)
    return "".join(result)


def read_documents(path):
    with ZipFile(path) as package:
        parts = {name: package.read(name) for name in package.namelist()}
    documents = {name: minidom.parseString(data) for name, data in parts.items()
                 if re.fullmatch(r"word/(document|header\d*|footer\d*)\.xml", name)}
    return parts, documents


def direct_child(node, name):
    return next((child for child in node.childNodes if child.nodeType == child.ELEMENT_NODE
                 and child.namespaceURI == W and child.localName == name), None)


def font_coverage():
    """Unicode cmap формата 12 поставляемого шрифта, без зависимости от fontTools."""
    data = EMOJI_FONT.read_bytes()
    count = struct.unpack_from(">H", data, 4)[0]
    cmap_offset = None
    for i in range(count):
        tag, _, offset, _ = struct.unpack_from(">4sIII", data, 12 + i * 16)
        if tag == b"cmap":
            cmap_offset = offset; break
    if cmap_offset is None:
        raise ValueError("В Noto Emoji нет таблицы cmap")
    result = set()
    for i in range(struct.unpack_from(">H", data, cmap_offset + 2)[0]):
        _, _, relative = struct.unpack_from(">HHI", data, cmap_offset + 4 + i * 8)
        offset = cmap_offset + relative
        fmt = struct.unpack_from(">H", data, offset)[0]
        if fmt == 12:
            for j in range(struct.unpack_from(">I", data, offset + 12)[0]):
                first, last, glyph = struct.unpack_from(">III", data, offset + 16 + j * 12)
                result.update(range(first + (glyph == 0), last + 1))
    if not result:
        raise ValueError("В поставляемом Noto Emoji нет поддерживаемой Unicode cmap формата 12")
    return result


def prepare_emoji(doc):
    """Отдельный встраиваемый шрифт только для символов; исходный текст неизменен."""
    required = set()
    for run in list(nodes(doc, "r")):
        if not any(EMOJI.search(text_of(t)) for t in nodes(run, "t")):
            continue
        # Поля Word, рисунки и сложную разметку не реконструируем.
        if any(c.nodeType == c.ELEMENT_NODE and c.localName not in {"rPr", "t", "br", "cr", "tab"}
               for c in run.childNodes):
            raise ValueError("Эмодзи внутри сложного поля Word требуют адресной ручной проверки")
        for child in list(run.childNodes):
            if child.nodeType != child.ELEMENT_NODE or child.localName == "rPr":
                continue
            segments = EMOJI.split(text_of(child)) if child.localName == "t" else [None]
            for segment in segments:
                if segment == "":
                    continue
                clone = run.cloneNode(False)
                old_properties = direct_child(run, "rPr")
                if old_properties:
                    clone.appendChild(old_properties.cloneNode(True))
                value_node = child.cloneNode(True)
                if segment is not None:
                    set_text(value_node, segment)
                clone.appendChild(value_node)
                if segment is not None and EMOJI.fullmatch(segment):
                    required.update(c for c in segment if c not in "\ufe0f\ufe0e\u200d\u20e3")
                    properties = direct_child(clone, "rPr")
                    if properties is None:
                        properties = doc.createElementNS(W, "w:rPr")
                        clone.insertBefore(properties, clone.firstChild)
                    fonts = direct_child(properties, "rFonts")
                    if fonts is None:
                        fonts = doc.createElementNS(W, "w:rFonts")
                        properties.insertBefore(fonts, properties.firstChild)
                    for attr in list(fonts.attributes.keys()):
                        fonts.removeAttribute(attr)
                    for attr in ("ascii", "hAnsi", "eastAsia", "cs"):
                        fonts.setAttributeNS(W, "w:" + attr, "Noto Emoji")
                run.parentNode.insertBefore(clone, run)
        run.parentNode.removeChild(run)
    missing = required - {chr(c) for c in font_coverage()} if required else set()
    if missing:
        raise ValueError("Noto Emoji не содержит символы: " + " ".join(sorted(missing)) +
                         ". Текст не изменён; нужен другой шрифт и проверка PDF.")
    return bool(required)


def embed_emoji_font(parts):
    """OOXML font obfuscation. Шрифт переносится вместе с DOCX, установка не нужна."""
    rel_ns = "http://schemas.openxmlformats.org/package/2006/relationships"
    rid_ns = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
    key = "{CD96883E-9674-41C4-B352-593E84AEB25B}"
    data = bytearray(EMOJI_FONT.read_bytes())
    mask = bytes.fromhex(key.strip("{}").replace("-", ""))[::-1]
    for i in range(32):
        data[i] ^= mask[i % 16]
    parts["word/fonts/blogger-emoji.odttf"] = bytes(data)
    table = minidom.parseString(parts["word/fontTable.xml"])
    existing = next((f for f in nodes(table, "font") if f.getAttributeNS(W, "name") == "Noto Emoji"), None)
    if existing is None:
        existing = table.createElementNS(W, "w:font")
        existing.setAttributeNS(W, "w:name", "Noto Emoji")
        table.documentElement.appendChild(existing)
    for old in nodes(existing, "embedRegular"):
        existing.removeChild(old)
    embed = table.createElementNS(W, "w:embedRegular")
    embed.setAttributeNS(rid_ns, "r:id", "rIdBloggerEmoji")
    embed.setAttributeNS(W, "w:fontKey", key)
    existing.appendChild(embed)
    table.documentElement.setAttribute("xmlns:r", rid_ns)
    parts["word/fontTable.xml"] = table.toxml(encoding="utf-8")
    relpath = "word/_rels/fontTable.xml.rels"
    relationships = minidom.parseString(parts.get(relpath, f'<Relationships xmlns="{rel_ns}"/>'.encode()))
    if not any(r.getAttribute("Id") == "rIdBloggerEmoji" for r in relationships.documentElement.childNodes
               if r.nodeType == r.ELEMENT_NODE):
        rel = relationships.createElementNS(rel_ns, "Relationship")
        for attr, value in {"Id": "rIdBloggerEmoji", "Type": rid_ns + "/font", "Target": "fonts/blogger-emoji.odttf"}.items():
            rel.setAttribute(attr, value)
        relationships.documentElement.appendChild(rel)
    parts[relpath] = relationships.toxml(encoding="utf-8")
    types = minidom.parseString(parts["[Content_Types].xml"])
    if not any(n.getAttribute("Extension") == "odttf" for n in types.documentElement.childNodes
               if n.nodeType == n.ELEMENT_NODE):
        item = types.createElementNS(types.documentElement.namespaceURI, "Default")
        item.setAttribute("Extension", "odttf")
        item.setAttribute("ContentType", "application/vnd.openxmlformats-officedocument.obfuscatedFont")
        types.documentElement.appendChild(item)
    parts["[Content_Types].xml"] = types.toxml(encoding="utf-8")


def inspect_document(path):
    _, documents = read_documents(path)
    return {"source_sha256": hashlib.sha256(Path(path).read_bytes()).hexdigest(),
            "paragraphs": [{"part": part, "paragraph": index, "text": paragraph_text(p)}
                           for part, doc in documents.items() for index, p in enumerate(nodes(doc, "p"), 1)]}


def audit_parts(parts, documents, expected_font="Montserrat"):
    """Проверка явного шрифта наших шаблонов, не угадывание запасного шрифта Word."""
    issues, emoji = [], set()
    for part, doc in documents.items():
        for index, paragraph in enumerate(nodes(doc, "p"), 1):
            for run in nodes(paragraph, "r"):
                text = "".join(text_of(t) for t in nodes(run, "t"))
                if not text.strip():
                    continue
                fonts = nodes(run, "rFonts")
                font = fonts[0].getAttributeNS(W, "hAnsi") if fonts else ""
                if re.search(r"[A-Za-zА-Яа-яЁё0-9]", text) and font != expected_font:
                    issues.append({"part": part, "paragraph": index, "text": text[:100],
                                   "font": font or None, "expected": expected_font})
                symbols = set(EMOJI.findall(text))
                if symbols:
                    emoji.update(symbols)
                    if font != "Noto Emoji":
                        issues.append({"part": part, "paragraph": index, "text": text[:100],
                                       "font": font or None, "expected": "Noto Emoji"})
    if emoji and "word/fonts/blogger-emoji.odttf" not in parts:
        issues.append({"problem": "Отсутствует встроенный Noto Emoji"})
    return {"ok": not issues, "expected_font": expected_font, "issues": issues,
            "emoji": sorted(emoji), "pdf_visual_check_required": True}


def audit_document(path, expected_font="Montserrat"):
    return audit_parts(*read_documents(path), expected_font=expected_font)


def patch_document(source, changes, output, expected_font="Montserrat"):
    """Точные локальные замены; свойства абзацев и существующих runs не сбрасываются."""
    source, output = Path(source), Path(output)
    if source.resolve() == output.resolve() or output.exists():
        raise ValueError("Для адресной правки нужен новый файл; перезапись запрещена")
    if not isinstance(changes, dict) or changes.get("source_sha256") != hashlib.sha256(source.read_bytes()).hexdigest():
        raise ValueError("source_sha256 не совпадает с исходником; заново выполните --inspect")
    replacements = changes.get("replacements")
    if not isinstance(replacements, list) or not replacements or any(not isinstance(c, dict) for c in replacements):
        raise ValueError("Нужен непустой список объектов replacements")
    parts, documents = read_documents(source)
    before = audit_parts(parts, documents, expected_font)
    if not before["ok"]:
        raise ValueError("Исходный DOCX уже потерял оформление; сначала проверьте --audit: " + json.dumps(before["issues"], ensure_ascii=False))
    changed_parts = set()
    for change in replacements:
        old, new = change.get("find"), change.get("replace")
        if not isinstance(old, str) or not old or not isinstance(new, str):
            raise ValueError("find должен быть непустой строкой, replace — строкой")
        validate_values({"replace": new})
        part = change.get("part", "word/document.xml")
        if part not in documents:
            raise ValueError("Не найден текстовый раздел " + str(part))
        doc = documents[part]
        candidates = []
        for index, p in enumerate(nodes(doc, "p"), 1):
            if "paragraph" in change and change["paragraph"] != index:
                continue
            text = paragraph_text(p)
            for match in re.finditer(re.escape(old), text):
                candidates.append((p, match.start(), match.end()))
        if len(candidates) != 1:
            raise ValueError(f"Замена должна находиться ровно один раз, найдено {len(candidates)}: {old[:100]!r}")
        paragraph, start, end = candidates[0]
        if nodes(paragraph, "hyperlink") or nodes(paragraph, "fldChar") or nodes(paragraph, "instrText"):
            raise ValueError("Абзац содержит активную ссылку или поле Word; нужна отдельная правка связи, а не только видимого текста")
        offset, touched = 0, []
        def walk(node):
            nonlocal offset
            if node.nodeType == node.ELEMENT_NODE and node.namespaceURI == W and node.localName in {"pPr", "rPr"}:
                return
            if node.nodeType == node.ELEMENT_NODE and node.namespaceURI == W and node.localName in {"t", "br", "cr", "tab"}:
                value = text_of(node) if node.localName == "t" else ("\t" if node.localName == "tab" else "\n")
                left, right = offset, offset + len(value)
                offset = right
                if left < end and right > start:
                    touched.append((node, value, left, right))
                return
            for child in list(node.childNodes):
                walk(child)
        walk(paragraph)
        for position, (node, value, left, right) in enumerate(touched):
            replacement = value[:max(0, start - left)] + (new if position == 0 else "") + value[min(len(value), end - left):]
            if node.localName != "t":
                replacement_node = doc.createElementNS(W, "w:t")
                node.parentNode.replaceChild(replacement_node, node)
                node = replacement_node
            if position == 0 and re.search(r"[A-Za-zА-Яа-яЁё0-9]", new):
                # Если весь пост начинается эмодзи, новый обычный текст не должен
                # унаследовать символьный шрифт первого заменяемого run.
                fonts = nodes(node.parentNode, "rFonts")
                if fonts and fonts[0].getAttributeNS(W, "hAnsi") == "Noto Emoji":
                    for attr in ("ascii", "hAnsi", "eastAsia", "cs"):
                        fonts[0].setAttributeNS(W, "w:" + attr, expected_font)
            set_text(node, replacement)
        changed_parts.add(part)
    has_emoji = False
    for part in changed_parts:
        has_emoji = prepare_emoji(documents[part]) or has_emoji
        parts[part] = documents[part].toxml(encoding="utf-8")
    if has_emoji:
        embed_emoji_font(parts)
    audit = audit_parts(parts, documents, expected_font)
    if not audit["ok"]:
        raise ValueError("Правка не прошла проверку шрифтов: " + json.dumps(audit["issues"], ensure_ascii=False))
    write_package(parts, output)
    return output


def validate_values(fields):
    if not isinstance(fields, dict) or any(not isinstance(k, str) or not isinstance(v, str)
                                           for k, v in fields.items()):
        raise ValueError("fields должен быть плоским JSON-объектом со строковыми значениями")
    if "NOTICE_HOURS" in fields:
        raise ValueError("NOTICE_HOURS больше не используется: перенесите согласованный срок отправки ссылки и снимка "
                         "после публикации в LINK_AFTER_PUBLICATION_HOURS; при неизвестном сроке укажите ____")
    if "LINK_AFTER_PUBLICATION_HOURS" in fields:
        value = fields["LINK_AFTER_PUBLICATION_HOURS"]
        if value != "____" and not re.fullmatch(r"[0-9]+(?:[.,][0-9]+)?", value):
            raise ValueError("LINK_AFTER_PUBLICATION_HOURS: нужно неотрицательное число часов после публикации "
                             "до отправки Заказчику ссылки и снимка (например, 0, 1, 0.5 или 0,5), "
                             "либо ____ для несогласованного срока; текст действия в это поле не вставляется")
    for key, value in fields.items():
        if re.search(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", value):
            raise ValueError(f"Недопустимые управляющие символы в {key}")
        if "{{" in value or "}}" in value:
            raise ValueError(f"Нераскрытый шаблонный маркер в {key}")


def write_package(parts, output, force=False):
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("wb" if force else "xb") as stream:
        with ZipFile(stream, "w", ZIP_DEFLATED) as package:
            for name, data in parts.items():
                package.writestr(name, data)


def revise_document(source, changes, output, post_file=None, review_dir=None, links_file=None, exchange_file=None):
    source = Path(source)
    saved = json.loads(source.with_suffix(source.suffix + ".fields.json").read_text(encoding="utf-8"))
    if saved["output_sha256"] != hashlib.sha256(source.read_bytes()).hexdigest():
        raise ValueError("DOCX изменён после сборки. Сохраните ручные правки через --patch вместо регенерации")
    validate_values(saved["fields"])
    template = Path(saved["template"])
    if saved["template_sha256"] != hashlib.sha256(template.read_bytes()).hexdigest():
        raise ValueError("Шаблон изменился после сборки; автоматическая регенерация запрещена")
    validate_values(changes)
    if saved.get("post_source") and post_file is None:
        post_file = Path(saved["post_source"]["path"])
        if hashlib.sha256(post_file.read_bytes()).hexdigest() != saved["post_source"]["sha256"]:
            raise ValueError("Исходный текст поста изменился; сохраните новую версию отдельно и явно передайте --post-file")
    previous_fields = saved["fields"]
    if saved.get("links_source") and links_file is None:
        links_file = Path(saved["links_source"]["path"])
        if hashlib.sha256(links_file.read_bytes()).hexdigest() != saved["links_source"]["sha256"]:
            raise ValueError("Исходная карта ссылок изменилась; сохраните новую версию отдельно и явно передайте --links-file")
    elif links_file is not None:
        previous_fields = {key: value for key, value in previous_fields.items()
                           if key != "LINK_COUNT" and not re.fullmatch(r"LINK_[0-9]+_(ANCHOR|URL)", key)}
    switching_from_scans = saved.get("exchange_source") and changes.get("EXCHANGE_METHOD", "scans") != "scans"
    if switching_from_scans:
        if exchange_file is not None or not EXCHANGE_FIELDS.issubset(changes):
            raise ValueError("При смене обмена сканами на другой способ передайте оба новых пункта в --fields без --exchange-file")
    if saved.get("exchange_source") and exchange_file is None and not switching_from_scans:
        exchange_file = Path(saved["exchange_source"]["path"])
        if hashlib.sha256(exchange_file.read_bytes()).hexdigest() != saved["exchange_source"]["sha256"]:
            raise ValueError("Исходный порядок обмена изменился; сохраните новую версию отдельно и явно передайте --exchange-file")
    if exchange_file is not None:
        previous_fields = {key: value for key, value in previous_fields.items() if key not in EXCHANGE_FIELDS}
    fields = previous_fields | changes
    if saved.get("review_source"):
        verify_review_document(source)
        if review_dir is None:
            review_dir = saved["review_source"]["directory"]
    if review_dir is not None and "AMENDMENT_CHANGES" not in changes:
        fields.pop("AMENDMENT_CHANGES", None)
    return render(template, fields, output, post_file=post_file, review_dir=review_dir, links_file=links_file, exchange_file=exchange_file)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--template", help="contract, placement-terms, creative, amendment или путь к DOCX")
    source.add_argument("--input", type=Path, help="Готовый DOCX для --inspect, --audit или --patch")
    source.add_argument("--revise", type=Path, help="Пересобрать неизменённый DOCX из сохранённой полной карты полей")
    parser.add_argument("--fields", type=Path, help="Плоский JSON: имя поля → строка")
    parser.add_argument("--post-file", type=Path, help="Дословный UTF-8 исходник POST_TEXT; несовпадение с картой полей останавливает сборку")
    parser.add_argument("--links-file", type=Path, help="Исходная JSON-карта ссылок: заполнить якоря и URL в исходном порядке; ручные расхождения запрещены")
    parser.add_argument("--exchange-file", type=Path, help="JSON обмена сканами: единый канал, контакты, согласование, закрывающие и последующий обмен для собственного contract")
    parser.add_argument("--review", type=Path, help="Проверенный каталог review: точные AMENDMENT_CHANGES для собственного amendment")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--list-fields", action="store_true")
    parser.add_argument("--inspect", action="store_true", help="SHA256 и номера абзацев, включая таблицы")
    parser.add_argument("--audit", action="store_true", help="Проверить явные шрифты и встраивание эмодзи")
    parser.add_argument("--patch", type=Path, help="JSON source_sha256 + replacements [{find, replace, paragraph?}]")
    parser.add_argument("--expected-font", default="Montserrat")
    parser.add_argument("--force", action="store_true", help="Перезапись результата первичной сборки; для --patch/--revise запрещена")
    args = parser.parse_args()
    try:
        if args.input:
            if args.post_file or args.review or args.links_file or args.exchange_file:
                parser.error("--post-file/--links-file/--exchange-file/--review применяются с --template или --revise")
            if args.force:
                parser.error("--force не применяется к существующим документам")
            if args.inspect:
                print(json.dumps(inspect_document(args.input), ensure_ascii=False, indent=2)); return
            if args.audit:
                report = audit_document(args.input, args.expected_font)
                print(json.dumps(report, ensure_ascii=False, indent=2))
                if not report["ok"]:
                    parser.exit(2)
                return
            if not args.patch or not args.output:
                parser.error("Для адресной правки требуются --patch и --output")
            print(patch_document(args.input, json.loads(args.patch.read_text(encoding="utf-8")), args.output, args.expected_font))
            return
        if args.revise:
            if args.force or not args.fields or not args.output:
                parser.error("Для --revise нужны --fields и --output; --force запрещён")
            print(revise_document(args.revise, json.loads(args.fields.read_text(encoding="utf-8")), args.output, args.post_file, args.review, args.links_file, args.exchange_file)); return
        template = TEMPLATES / f"{args.template}.docx" if args.template in {"contract", "placement-terms", "creative", "amendment"} else Path(args.template)
        if args.list_fields:
            print(json.dumps(list_fields(template), ensure_ascii=False, indent=2)); return
        if not args.fields or not args.output:
            parser.error("Для заполнения требуются --fields и --output")
        fields = json.loads(args.fields.read_text(encoding="utf-8"))
        print(render(template, fields, args.output, force=args.force, post_file=args.post_file, review_dir=args.review, links_file=args.links_file, exchange_file=args.exchange_file))
    except (OSError, ValueError, KeyError) as error:
        parser.exit(2, f"Ошибка: {error}\n")


if __name__ == "__main__":
    main()
