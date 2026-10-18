#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = ["pypdf>=5,<7"]
# ///
"""Единый источник таблицы условий и листа правок. Не изменяет договор или состояние."""
import argparse
import hashlib
import importlib.util
import json
import math
import re
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path
from urllib.parse import unquote
from xml.etree import ElementTree
from zipfile import ZipFile

from pypdf import PdfReader


ACTIONS = {"replace": "Заменить", "append": "Добавить", "keep": "Оставить",
           "defer": "Отложить решение", "reject": "Отклонить"}
STATUSES = {"accepted": "Принято", "proposed": "Предложено", "pending": "Не согласовано",
            "rejected": "Отклонено"}
CATEGORIES = {
    "placement": "Предмет, площадка, время и длительность размещения",
    "price_vat": "Цена, НДС и включение/доплата налога",
    "fee": "Рекламные отчисления и распределение расходов",
    "payment": "Оплата: сумма, основание и крайний срок",
    "signing": "Порядок подписания и связь с оплатой",
    "creative_delivery": "Срок передачи креатива, согласование и правила изменений",
    "erid": "ERID: ответственный, срок и необходимые сведения",
    "ord_reporting": "Отчётность ОРД: ответственный, состав и срок",
    "client_reporting": "Ссылка и статистика заказчику: срок и начало отсчёта",
    "acceptance": "Подтверждение услуги, приёмка и срок замечаний",
    "closing": "УПД и закрывающие документы",
    "exchange": "Обмен документами, адреса/чаты и сбой ЭДО",
    "attachments": "Состав приложений и порядок изменения договора",
    "liability": "Перенос, отмена, возврат, ответственность и прочие изменения",
}
IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".tif", ".tiff"}
TIMED_CATEGORIES = {"placement", "payment", "signing", "creative_delivery", "erid",
                    "ord_reporting", "client_reporting", "acceptance", "closing"}


def previous_source(previous, base):
    """Дословный прежний пункт; факт выдачи отдельно сверяется с состоянием сделки."""
    fields(previous, ("source",), ("value",), "previous")
    source = previous["source"]
    fields(source, ("kind", "group"), ("path", "clause", "quote", "not_found_reason", "reason"), "previous.source")
    if "value" in previous and (not isinstance(source.get("quote"), str) or previous["value"] != source["quote"]):
        raise ValueError("previous.value не может пересказывать прежнее условие: удалите value и уточните дословную previous.source.quote по выданному источнику. "
                         "Для отсутствующего условия используйте source.reason/not_found_reason")
    string(source["group"], "previous.source.group")
    kind = source["kind"]
    required = {"document": ("path", "clause", "quote"), "message": ("path", "quote", "reason"),
                "none": ("reason",)}
    if kind not in required:
        raise ValueError("previous.source.kind: document, message или none")
    fields(source, ("kind", "group") + required[kind], ("not_found_reason",) if kind == "document" else (), "previous.source")
    if kind == "none":
        string(source["reason"], "previous.source.reason")
        return dict(source)
    path = (base / string(source["path"], "previous.source.path")).resolve()
    raw = path.read_bytes()
    if kind == "document":
        string(source["clause"], "previous.source.clause")
    else:
        string(source["reason"], "previous.source.reason")
    quote = source["quote"]
    if quote is None and kind == "document":
        string(source.get("not_found_reason"), "previous.source.not_found_reason")
    else:
        string(quote, "previous.source.quote")
        if "not_found_reason" in source:
            raise ValueError("previous.source.not_found_reason допустим только при quote: null")
        if path.suffix.lower() == ".docx":
            with ZipFile(path) as archive:
                root = ElementTree.fromstring(archive.read("word/document.xml"))
            ns = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}
            text = "\n".join("".join(p.itertext()) for p in root.findall(".//w:p", ns))
        elif path.suffix.lower() == ".pdf":
            text = "\n".join(page.extract_text() or "" for page in PdfReader(BytesIO(raw)).pages)
        else:
            text = raw.decode("utf-8")
        if normalized(quote) not in normalized(text):
            raise ValueError(f"previous.source.quote: дословная цитата не найдена в прежнем источнике {path}")
    return dict(source, path=str(path), sha256=sha256(raw))


TIMING_CATEGORIES = {"erid", "client_reporting", "acceptance", "closing"}
TIMING_TARGETS = {
    "/schedule/link_after_publication_hours": ("client_reporting", "link"),
    "/schedule/claims_after_end_hours": ("acceptance", "claims"),
    "/schedule/closing_after_end_hours": ("closing", "closing"),
    "/schedule/deadline_terms/erid": ("erid", "erid"),
}


def timing_value(target, value, where):
    """Проверять представление срока, не пытаться извлекать его из русского текста."""
    if target.endswith("_hours"):
        if type(value) not in (int, float) or not math.isfinite(value) or value < 0:
            raise ValueError(f"{where}: неотрицательное число часов")
    elif target == "/schedule/deadline_terms/erid":
        if not isinstance(value, dict) or value.get("precision") not in ("relative", "date", "datetime"):
            raise ValueError(f"{where}: сохраните ERID с precision relative/date/datetime, не unspecified")
        string(value.get("condition"), where + ".condition")
        string(value.get("source"), where + ".source")
    else:
        if not isinstance(value, dict):
            raise ValueError(f"{where}: полная запись reporting_deadlines с id, at и source")
        string(value.get("id"), where + ".id")
        stamp(value.get("at"))
        string(value.get("source"), where + ".source")


def timing_implementation(item):
    applied = item["implementation"]
    timing = applied.get("timing")
    if timing is None:
        if item["category"] in TIMING_CATEGORIES:
            raise ValueError(f"{item['id']}: нужен implementation.timing с предметом и целью срока; статус checks не заменяет срок")
        return None
    fields(timing, ("subject", "target", "mode", "received_value", "selected_value", "reason"),
           ("agreement_source",), "implementation.timing")
    string(timing["subject"], "timing.subject")
    string(timing["reason"], "timing.reason")
    mode, target = timing["mode"], timing["target"]
    if mode not in ("provisional", "agreed", "deferred", "unresolved", "not_applicable"):
        raise ValueError("timing.mode: provisional, agreed, deferred, unresolved или not_applicable")
    if mode == "not_applicable":
        if target is not None or timing["received_value"] is not None or timing["selected_value"] is not None:
            raise ValueError("timing not_applicable: target и оба значения должны быть null; объясните, почему условие не задаёт срок")
        return timing
    if target not in TIMING_TARGETS and not (isinstance(target, str) and re.fullmatch(r"/schedule/reporting_deadlines/[0-9]+", target)):
        raise ValueError("timing.target: поле срока schedule или конкретная запись /schedule/reporting_deadlines/N; checks.status не является сроком")
    if target in TIMING_TARGETS and TIMING_TARGETS[target][0] != item["category"]:
        raise ValueError(f"{item['id']}: timing.target не соответствует предмету категории")
    if timing["received_value"] is not None:
        timing_value(target, timing["received_value"], "timing.received_value")
    if mode in ("provisional", "agreed"):
        timing_value(target, timing["selected_value"], "timing.selected_value")
        if applied["kind"] != "known":
            raise ValueError("Извлечённый выбранный срок требует kind=known; отсутствие подписи означает provisional, а не неизвестный срок")
        if not any(binding.get("path") == target and binding.get("value") == timing["selected_value"] for binding in applied.get("state", [])):
            raise ValueError(f"{item['id']}: свяжите выбранный срок с {target}; проверка статуса не заменяет перенос срока")
        if mode == "agreed":
            string(timing.get("agreement_source"), "timing.agreement_source — источник принятия выбранного условия")
    else:
        if timing["selected_value"] is not None or applied["kind"] != "unresolved":
            raise ValueError("Отложенное/неизвестное условие требует selected_value=null, kind=unresolved и отдельное действие")
        if mode == "deferred" and timing["received_value"] is None:
            raise ValueError("Отложенное известное предложение сохраните в timing.received_value")
        if mode == "unresolved" and timing["received_value"] is not None:
            raise ValueError("Известное предложение нельзя объявить неизвестным: выберите provisional либо объяснённое deferred")
    if mode != "agreed" and "agreement_source" in timing:
        raise ValueError("agreement_source задаётся только для mode=agreed")
    return timing


def reviewed_timing_record(item):
    """Запись по ID решения; одна категория может содержать несколько разных условий."""
    return dict(item["implementation"]["timing"], source=item["source"],
                event_ids=[event["id"] for event in item["implementation"].get("plan", [])])


def check_timing_state(item, state, events):
    timing = item["implementation"].get("timing")
    if timing is None:
        return
    record = state.get("schedule", {}).get("reviewed_terms", {}).get(item["id"])
    if record != reviewed_timing_record(item):
        raise ValueError(f"{item['id']}: сохраните актуальный timing, source и event_ids в schedule.reviewed_terms по ID решения")
    mode, target = timing["mode"], timing["target"]
    if mode == "not_applicable":
        return
    expected = item["implementation"]["plan"]
    selected_events = [events[event["id"]] for event in expected if event["id"] in events]
    if mode in ("deferred", "unresolved"):
        try:
            actual = pointer(state, target)
        except ValueError:
            actual = None
        if actual is not None and not (isinstance(actual, dict) and actual.get("precision") == "unspecified"):
            raise ValueError(f"{item['id']}: отложенный срок не должен становиться обязанностью в {target}; для сохранённого прежнего соглашения выберите agreed и selected_value")
        if any(event.get("kind") != "organizational" for event in selected_events):
            raise ValueError(f"{item['id']}: отложенный срок требует организационного действия")
        return
    if not state.get("schedule", {}).get("publication_at"):
        return  # Содержание срока уже проверено; даты событий ещё нельзя рассчитать.
    event_id = TIMING_TARGETS.get(target, (None, None))[1]
    if event_id == "erid" and timing["selected_value"]["precision"] != "datetime":
        precision = timing["selected_value"]["precision"]
        if precision == "relative":
            matched = any(any(control.get("check") == "erid" for control in event.get("deadline_controls", []))
                          for event in selected_events)
        else:
            matched = any(event.get("kind") == "organizational" and "erid" in event.get("checks", [])
                          for event in selected_events)
    else:
        if event_id is None:
            event_id = "report-" + timing["selected_value"]["id"]
        matched = any(event["id"] == event_id for event in selected_events)
    if not matched:
        raise ValueError(f"{item['id']}: план должен содержать соответствующее событие/контроль срока {target}; посторонняя проверка его не заменяет")
    for event in selected_events:
        if not any(row.get("id") == item["id"] and row.get("mode") == mode for row in event.get("reviewed_terms", [])):
            raise ValueError(f"{item['id']}: пересоберите план с явным статусом {mode} извлечённого срока")


def implementation(item):
    value = item["implementation"]
    fields(value, ("kind",), ("reason", "state", "plan", "plan_pending_reason", "timing"), "implementation")
    timing = timing_implementation(item)
    if value["kind"] == "no_state_change":
        fields(value, ("kind", "reason"), ("timing",), "implementation")
        string(value["reason"], "implementation.reason")
        if item["category"] in TIMED_CATEGORIES and not (timing and timing["mode"] == "not_applicable"):
            raise ValueError(f"{item['id']}: временная категория требует known или unresolved со связью состояния и плана")
        return
    if value["kind"] not in ("known", "unresolved"):
        raise ValueError("implementation.kind: known, unresolved или no_state_change")
    fields(value, ("kind", "state", "plan"), ("reason", "plan_pending_reason", "timing"), "implementation")
    if value["kind"] == "unresolved":
        string(value.get("reason"), "implementation.reason")
    if not isinstance(value["state"], list) or not value["state"]:
        raise ValueError("implementation.state: нужен непустой список проверяемых связей")
    if not isinstance(value["plan"], list):
        raise ValueError("implementation.plan: нужен список проверяемых связей")
    if not value["plan"]:
        if not isinstance(value.get("plan_pending_reason"), str) or not value["plan_pending_reason"].strip():
            raise ValueError("implementation.plan: нужен непустой список, либо plan_pending_reason до установления даты публикации")
    elif "plan_pending_reason" in value:
        raise ValueError("plan_pending_reason допустим только при пустом plan и неизвестной дате публикации")
    used = set()
    for binding in value["state"]:
        fields(binding, ("path", "value"), (), "implementation.state[]")
        path = string(binding["path"], "implementation.state.path")
        if not path.startswith("/") or path in used:
            raise ValueError("implementation.state.path: уникальный JSON Pointer, начинающийся с /")
        used.add(path)
        if value["kind"] == "known" and binding["value"] in (None, "unspecified", "unknown", ""):
            raise ValueError("Известный срок нельзя связать с null/unspecified/unknown; сохраните конкретное условие")
        if (value["kind"] == "known" and isinstance(binding["value"], dict)
                and binding["value"].get("precision") == "unspecified"):
            raise ValueError("Известный срок требует precision relative/date/datetime, а не unspecified")
    used = set()
    for event in value["plan"]:
        fields(event, ("id", "at"), (), "implementation.plan[]")
        unique_id(event["id"], used, "implementation.plan.id")
        stamp(event["at"])


def stamp(value):
    result = datetime.fromisoformat(string(value, "Дата события").replace("Z", "+00:00"))
    if result.tzinfo is None:
        raise ValueError("Дата события требует часового пояса")
    return result


def sha256(raw):
    return hashlib.sha256(raw).hexdigest()


def fields(value, required, optional, where):
    if not isinstance(value, dict):
        raise ValueError(f"{where}: требуется объект")
    missing = set(required) - value.keys()
    extra = value.keys() - set(required) - set(optional)
    if missing or extra:
        raise ValueError(f"{where}: отсутствуют поля {sorted(missing)}; неизвестные поля {sorted(extra)}")


def string(value, where):
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{where}: требуется непустой текст")
    return value


def unique_id(value, used, where):
    if not isinstance(value, str) or not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_-]*", value):
        raise ValueError(f"{where}: идентификатор из латинских букв, цифр, _ и -")
    if value in used:
        raise ValueError(f"{where}: повторный идентификатор {value}")
    used.add(value)


def review_scope(data, base):
    scope = data.get("scope", {"mode": "full"})
    fields(scope, ("mode",), ("categories", "source"), "scope")
    if scope["mode"] == "full":
        if set(scope) != {"mode"}:
            raise ValueError("scope full охватывает все категории; categories/source не требуются")
        return {"mode": "full", "categories": list(CATEGORIES)}
    if scope["mode"] != "selected_terms":
        raise ValueError("scope.mode: full или selected_terms")
    selected = scope.get("categories")
    if (not isinstance(selected, list) or not selected
            or any(not isinstance(category, str) or category not in CATEGORIES for category in selected)
            or len(set(selected)) != len(selected)):
        raise ValueError("scope.categories: непустой список известных категорий без повторов")
    source = scope.get("source")
    fields(source, ("path", "quote"), (), "scope.source")
    path = (base / string(source["path"], "scope.source.path")).resolve()
    raw = path.read_bytes()
    quote = string(source["quote"], "scope.source.quote")
    if quote not in raw.decode("utf-8"):
        raise ValueError("scope.source.quote: дословного ограничения поручения нет в исходном сообщении")
    return {"mode": "selected_terms", "categories": selected,
            "source": {"path": str(path), "quote": quote, "sha256": sha256(raw)}}


def normalized(text):
    """Только пробелы и переносы; пунктуация, слова, регистр и дефисы сохраняются."""
    return " ".join(text.split())


def source_page(document, page_number, readers):
    if type(page_number) is not int or page_number < 1:
        raise ValueError("source.page: номер страницы, целое число от 1")
    path = Path(document["path"])
    page_text = None
    if path.suffix.lower() == ".pdf":
        if path not in readers:
            try:
                readers[path] = PdfReader(BytesIO(path.read_bytes()))
            except Exception as error:
                raise ValueError(f"Не удалось прочитать PDF {path}: {error}") from error
        reader = readers[path]
        if page_number > len(reader.pages):
            raise ValueError(f"В PDF нет страницы {page_number}: {path}")
        page_text = reader.pages[page_number - 1].extract_text() or ""
    elif path.suffix.lower() in IMAGE_SUFFIXES:
        if page_number != 1:
            raise ValueError("У отдельного изображения source.page должен быть 1")
    else:
        raise ValueError(f"Для постраничной сверки нужен PDF или изображение, получен {path.suffix}")
    return page_text


def verify_citation(item, document, readers):
    source = item["source"]
    page_number = source["page"]
    page_text = source_page(document, page_number, readers)
    result = {"id": item["id"], "document": source["document"], "page": page_number}
    if source["quote"] is None:
        if "verification" in source:
            raise ValueError(f"{item['id']}: отсутствие пункта описывается в not_found_reason, не verification")
        return dict(result, method="not_found_manual", basis=source["not_found_reason"])
    if page_text is not None and normalized(page_text):
        if "verification" in source:
            raise ValueError(f"{item['id']}: страница содержит извлекаемый текст; визуальный режим не заменяет автоматическую сверку")
        if normalized(source["quote"]) not in normalized(page_text):
            raise ValueError(f"{item['id']}: цитата не найдена на странице {page_number} PDF. Проверьте страницу и дословность по оригиналу")
        return dict(result, method="pdf_text")
    verification = source.get("verification")
    fields(verification, ("method", "basis"), (), f"{item['id']}.source.verification")
    if verification["method"] != "visual":
        raise ValueError(f"{item['id']}: для изображения или страницы без текста требуется method: visual")
    basis = string(verification["basis"], "source.verification.basis")
    return dict(result, method="visual_scan" if page_text is not None else "visual_image", basis=basis)


def signature_matrix(data, sources, scope, base, readers):
    rows = data.get("signature_matrix")
    required = scope["mode"] == "full" or "signing" in scope["categories"]
    if rows is None and not required:
        return []
    if not isinstance(rows, list) or not rows:
        raise ValueError("signature_matrix: требуется непустая матрица подписей каждого логического документа")
    by_id = {source["id"]: source for source in sources}
    result, covered, labels = [], set(), set()
    for row in rows:
        fields(row, ("label", "source"), ("contractor", "customer", "image_path", "not_applicable"), "signature_matrix[]")
        label = string(row["label"], "signature_matrix.label")
        fields(row["source"], ("document", "page"), (), "signature_matrix.source")
        document = by_id.get(row["source"]["document"])
        if document is None:
            raise ValueError("signature_matrix: неизвестный исходный документ")
        source_page(document, row["source"]["page"], readers)
        identity = (document["id"], label)
        if identity in labels:
            raise ValueError(f"signature_matrix: повтор логического документа {label}")
        labels.add(identity)
        covered.add(document["id"])
        checked = dict(row)
        if "not_applicable" in row:
            fields(row, ("label", "source", "not_applicable"), (), "signature_matrix[]")
            string(row["not_applicable"], "signature_matrix.not_applicable")
        else:
            fields(row, ("label", "source", "contractor", "customer", "image_path"), (), "signature_matrix[]")
            for role in ("contractor", "customer"):
                fields(row[role], ("status", "observation"), (), f"signature_matrix.{role}")
                if row[role]["status"] not in ("present", "absent", "unclear"):
                    raise ValueError(f"signature_matrix.{role}.status: present, absent или unclear")
                string(row[role]["observation"], f"signature_matrix.{role}.observation")
            path = (base / string(row["image_path"], "signature_matrix.image_path")).resolve()
            if not path.is_file() or path.suffix.lower() not in IMAGE_SUFFIXES:
                raise ValueError(f"signature_matrix: отсутствует файл изображения просмотренного фрагмента: {path}")
            raw = path.read_bytes()
            if not raw:
                raise ValueError(f"signature_matrix: изображение пустое: {path}")
            checked["image_path"], checked["image_sha256"] = str(path), sha256(raw)
        result.append(checked)
    missing = by_id.keys() - covered
    if missing:
        raise ValueError(f"signature_matrix: не рассмотрены документы {sorted(missing)}; нужна матрица или обоснованное not_applicable")
    return result


def replacement_span(item, document, readers):
    decision, source = item["decision"], item["source"]
    original = normalized(string(decision.get("original"), "decision.original"))
    quote = normalized(source["quote"] or "")
    if original not in quote:
        raise ValueError(f"{item['id']}: decision.original должен быть дословным фрагментом source.quote")
    if quote.count(original) != 1:
        raise ValueError(f"{item['id']}: original повторяется в цитате; укажите более полный однозначный фрагмент")
    text = source_page(document, source["page"], readers)
    if text is None or not normalized(text):
        return {"quote": quote, "start": quote.index(original), "end": quote.index(original) + len(original)}
    text = normalized(text)
    if text.count(quote) != 1:
        raise ValueError(f"{item['id']}: цитата неоднозначна на странице; расширьте её контекст для точной замены")
    start = text.index(quote) + quote.index(original)
    return {"start": start, "end": start + len(original)}


def correction_groups(data, sources, citations):
    by_id = {source["id"]: source for source in sources}
    checks = {check["id"]: check for check in citations}
    groups, replacements = {}, {}
    for item in data["decisions"]:
        decision, source = item["decision"], item["source"]
        if decision["action"] not in ("replace", "append"):
            continue
        target = (by_id[source["document"]]["path"], normalized(decision["target"]))
        key = target + (decision["action"], normalized(decision.get("original", "")), decision["text"])
        if decision["action"] == "replace":
            span = checks[item["id"]]["replacement_span"]
            key += (source["page"], span["start"], span["end"], span.get("quote"))
            if "quote" not in span:  # Границы PDF однозначнее произвольного обозначения target.
                key = (target[0], source["page"], span["start"], span["end"], "replace",
                       normalized(decision["original"]), decision["text"])
        if key in groups:
            groups[key].append(item)
            continue
        if decision["action"] == "replace":
            for old in replacements.get(target[0], []):
                if old["source"]["page"] != source["page"]:
                    continue
                old_span = checks[old["id"]]["replacement_span"]
                incomparable = span.get("quote") != old_span.get("quote")
                if incomparable and target[1] != normalized(old["decision"]["target"]):
                    continue  # Разные пункты скана проверяет составитель; общей системы координат текста нет.
                overlap = max(span["start"], old_span["start"]) < min(span["end"], old_span["end"])
                if incomparable or overlap:
                    raise ValueError(f"Конфликт замен {old['id']} и {item['id']} в {decision['target']}: области перекрываются "
                                     "или их раздельность на скане не подтверждена. Согласуйте одну точную правку")
            replacements.setdefault(target[0], []).append(item)
        groups[key] = [item]
    return list(groups.values())


def load_review(raw, base):
    data = json.loads(raw)
    fields(data, ("title", "documents", "decisions"), ("scope", "signature_matrix", "review_basis", "comparison_at"), "review")
    string(data["title"], "title")
    if "comparison_at" in data:
        stamp(data["comparison_at"])
    scope = review_scope(data, base)
    basis = data.get("review_basis")
    if scope["mode"] == "full" or basis is not None:
        fields(basis, ("edition", "reason"), (), "review_basis")
        if basis["edition"] not in ("received", "previous", "unresolved"):
            raise ValueError("review_basis.edition: received, previous или unresolved")
        string(basis["reason"], "review_basis.reason")
    if not isinstance(data["documents"], list) or not data["documents"]:
        raise ValueError("documents: нужен непустой список исходных документов")
    sources, ids = [], set()
    for document in data["documents"]:
        fields(document, ("id", "label", "path"), ("original_path",), "documents[]")
        unique_id(document["id"], ids, "documents[].id")
        string(document["label"], "documents[].label")
        path = (base / string(document["path"], "documents[].path")).resolve()
        if not path.is_file():
            raise ValueError(f"Исходный документ отсутствует или не является файлом: {path}")
        record = {"id": document["id"], "label": document["label"], "path": str(path), "sha256": sha256(path.read_bytes())}
        if "original_path" in document:
            original = (base / string(document["original_path"], "documents[].original_path")).resolve()
            if path.suffix.lower() != ".pdf" or original.suffix.lower() != ".docx":
                raise ValueError("original_path связывает PDF-представление только с исходным DOCX")
            record["original"] = {"path": str(original), "sha256": sha256(original.read_bytes())}
        sources.append(record)
    if not isinstance(data["decisions"], list) or not data["decisions"]:
        raise ValueError("decisions: нужен непустой список решений")
    used, covered, citations, readers, previous = set(), set(), [], {}, []
    by_id = {source["id"]: source for source in sources}
    for item in data["decisions"]:
        fields(item, ("id", "category", "topic", "source", "previous", "status", "decision", "implementation"), ("received",), "decisions[]")
        unique_id(item["id"], used, "decisions[].id")
        if item["category"] not in CATEGORIES:
            raise ValueError(f"{item['id']}: неизвестная category; выберите из {', '.join(CATEGORIES)}")
        covered.add(item["category"])
        string(item["topic"], "topic")
        source = item["source"]
        fields(source, ("document", "clause", "page", "quote"), ("not_found_reason", "verification"), "source")
        if "received" in item and (source["quote"] is None or item["received"] != source["quote"]):
            raise ValueError(f"{item['id']}: received не может пересказывать полученный документ. Удалите это поле: "
                             "полученное условие выводится из source.quote. Выводы и предложения укажите отдельно в decision.reason/selected")
        if source["document"] not in ids:
            raise ValueError(f"Неизвестный документ: {source['document']}")
        if type(source["page"]) is not int or source["page"] < 1:
            raise ValueError("source.page: номер страницы, целое число от 1")
        string(source["clause"], "source.clause")
        if source["quote"] is None:
            string(source.get("not_found_reason"), "source.not_found_reason")
        else:
            string(source["quote"], "source.quote")
            if "not_found_reason" in source:
                raise ValueError("source.not_found_reason допустим только при quote: null")
        proof = previous_source(item["previous"], base)
        if proof["kind"] != "document" and not data.get("comparison_at"):
            raise ValueError("comparison_at: при previous.source.kind=none/message укажите время начала сравнения с часовым поясом. "
                             "Оно отделяет ранее выданные документы от появившихся после сверки")
        previous.append(dict(proof, id=item["id"]))
        implementation(item)
        if item["status"] not in STATUSES:
            raise ValueError(f"status: выберите {', '.join(STATUSES)}")
        decision = item["decision"]
        fields(decision, ("action", "reason", "selected", "plan"), ("target", "text", "original"), "decision")
        if decision["action"] not in ACTIONS:
            raise ValueError(f"decision.action: выберите {', '.join(ACTIONS)}")
        for key in ("reason", "selected", "plan"):
            string(decision[key], f"decision.{key}")
        changes = decision["action"] in ("replace", "append")
        if changes:
            string(decision.get("target"), "decision.target")
            string(decision.get("text"), "decision.text")
        elif "target" in decision or "text" in decision:
            raise ValueError("Только replace/append могут содержать target и text; выберите действие с конкретной правкой")
        if decision["action"] != "replace" and "original" in decision:
            raise ValueError("decision.original допустим только для replace")
        citation = verify_citation(item, by_id[source["document"]], readers)
        if decision["action"] == "replace":
            citation["replacement_span"] = replacement_span(item, by_id[source["document"]], readers)
        citations.append(citation)
    missing, extra = set(scope["categories"]) - covered, covered - set(scope["categories"])
    if missing or extra:
        raise ValueError(f"Охват сверки: отсутствуют решения категорий {sorted(missing)}; вне выбранного охвата {sorted(extra)}. "
                         "Для отсутствующего/неприменимого условия нужна полноценная запись с причиной, а не отметка о просмотре")
    signatures = signature_matrix(data, sources, scope, base, readers)
    correction_groups(data, sources, citations)
    return data, sources, {"scope": scope, "citations": citations, "previous": previous,
                           "signature_matrix": signatures, "review_basis": basis}


def cell(value):
    return str(value).replace("&", "&amp;").replace("<", "&lt;").replace(
        ">", "&gt;").replace("|", "&#124;").replace("\r\n", "\n").replace("\n", "<br>")


def link(label, path):
    label = label.replace("\\", "\\\\").replace("[", "\\[").replace(
        "]", "\\]").replace("|", "&#124;").replace("\n", " ")
    # Сохраняем абсолютный локальный путь; специальные символы URL кодируем.
    target = str(path).replace("%", "%25").replace("<", "%3C").replace(
        ">", "%3E").replace("#", "%23").replace("\n", "%0A")
    return f"[{label}](<{target}>)"


def render(data, sources, verification, output):
    by_id = {source["id"]: source for source in sources}
    citation_checks = {check["id"]: check for check in verification["citations"]}
    previous_checks = {check["id"]: check for check in verification["previous"]}
    changes = [item for item in data["decisions"] if item["decision"]["action"] in ("replace", "append")]
    grouped = correction_groups(data, sources, verification["citations"])
    common = ("Для страниц PDF с извлекаемым текстом цитаты сверяются автоматически с нормализацией только пробелов. "
              "Визуальные проверки и отсутствие пункта помечены отдельно и остаются ответственностью составителя. Скрипт рассчитывает SHA-256 "
              "и связывает каждое решение с листом правок; согласование, подписи и исполнение он не подтверждает. "
              "Сборка не подтверждает перенос условий в состояние: перед выдачей обязателен check-state с текущими состоянием и планом.")
    scope = verification["scope"]
    scope_text = (f"Полная сверка: все {len(CATEGORIES)} категорий." if scope["mode"] == "full" else
                  "Выборочная сверка: " + ", ".join(scope["categories"]) + ". Ограничение: «" +
                  cell(scope["source"]["quote"]) + "»; " + link("источник поручения", scope["source"]["path"]) +
                  f"; SHA-256: `{scope['source']['sha256']}`.")
    review = [f"# {data['title']}", "", common, "", scope_text, "", "## Исходные документы", ""]
    if data.get("comparison_at"):
        review += [f"Начало сравнения прежних условий: {data['comparison_at']}. Позднейшая выдача не изменяет этот срез.", ""]
    for source in sources:
        review.append(f"- {source['id']}: {link(source['label'], source['path'])}; SHA-256: `{source['sha256']}`")
        if "original" in source:
            original = source["original"]
            review.append(f"  Исходный DOCX: {link('Полученный оригинал', original['path'])}; SHA-256: `{original['sha256']}`. "
                          "Соответствие PDF оригиналу проверяет составитель; хеши подтверждают сохранность файлов, а не качество преобразования.")
    if verification["review_basis"] is not None:
        basis = verification["review_basis"]
        review += ["", f"Редакция для выбранных условий текущего плана: {basis['edition']}. {basis['reason']}",
                   "Это основание планирования, а не подтверждение подписания."]
    review += ["", "## Подписи по логическим документам", "",
               "Наблюдения фиксирует составитель после визуального просмотра. Скрипт проверяет страницы и сохранность файлов, "
               "но не распознаёт подписи, не устанавливает их подлинность, полномочия или дату подписания.", ""]
    if not verification["signature_matrix"]:
        review.append("Матрица не выполнялась: выборочная сверка без категории signing.")
    else:
        review += ["| Логический документ и источник | Исполнитель | Заказчик | Просмотренный фрагмент / основание |",
                   "| --- | --- | --- | --- |"]
    for row in verification["signature_matrix"]:
        source = by_id[row["source"]["document"]]
        origin = f"{cell(row['label'])}<br>{link(source['label'], source['path'])}; страница: {row['source']['page']}"
        if "not_applicable" in row:
            values = [origin, "Не применимо", "Не применимо", cell(row["not_applicable"])]
        else:
            labels = {"present": "Видна", "absent": "Не видна", "unclear": "Неясно"}
            values = [origin] + [labels[row[role]["status"]] + ": " + cell(row[role]["observation"])
                                 for role in ("contractor", "customer")]
            values.append(link("Просмотренное изображение", row["image_path"]) + f"<br>SHA-256: `{row['image_sha256']}`")
        review.append("| " + " | ".join(values) + " |")
    review += ["", "## Условия и решения", "",
               "| ID / предмет | Источник, пункт и страница | Прежнее значение и источник | Полученное условие — дословно | Статус принятия | Решение, выбранное условие и план |",
               "| --- | --- | --- | --- | --- | --- |"]
    corrections = [f"# Лист правок — {data['title']}", "", common, "", scope_text, "",
                   f"Конкретных правок: {len(grouped)}. Текст ниже — предложение для согласования, если статус прямо не указывает принятие.", ""]
    for item in data["decisions"]:
        decision, source = item["decision"], item["source"]
        document = by_id[source["document"]]
        quoted = (f"Пункт не найден; просмотрено: {cell(source['not_found_reason'])}"
                  if source["quote"] is None else f"«{cell(source['quote'])}»")
        check = citation_checks[item["id"]]
        if check["method"] == "pdf_text":
            check_text = "Цитата автоматически найдена в текстовом слое указанной страницы PDF."
        elif check["method"] == "not_found_manual":
            check_text = "Отсутствие пункта установлено составителем; автоматически не подтверждено."
        else:
            check_text = "Визуальная проверка составителя; автоматическая сверка текста не выполнена. Основание: " + cell(check["basis"])
        citation = (f"{link(document['label'], document['path'])}<br>"
                    f"Пункт: {cell(source['clause'])}; страница: {source['page']}<br>{check_text}")
        disposition = (f"{ACTIONS[decision['action']]}: {cell(decision['reason'])}<br>"
                       f"Выбранное условие: {cell(decision['selected'])}<br>План: {cell(decision['plan'])}")
        applied = item["implementation"]
        disposition += "<br>Перенос: " + cell(applied["kind"])
        if applied.get("reason"):
            disposition += " — " + cell(applied["reason"])
        if "timing" in applied:
            timing = applied["timing"]
            disposition += "<br>Срок: " + cell(timing["subject"]) + "; " + cell(timing["mode"]) + ". " + cell(timing["reason"])
            disposition += "<br>Извлечено: " + cell(json.dumps(timing["received_value"], ensure_ascii=False))
            disposition += "; выбрано для плана: " + cell(json.dumps(timing["selected_value"], ensure_ascii=False))
        if applied.get("plan_pending_reason"):
            disposition += "<br>План до установления даты не рассчитан: " + cell(applied["plan_pending_reason"])
        for binding in applied.get("state", []):
            disposition += "<br>Состояние: " + cell(binding["path"]) + " = " + cell(json.dumps(binding["value"], ensure_ascii=False))
        for event in applied.get("plan", []):
            disposition += f"<br>Событие: {cell(event['id'])}; {cell(event['at'])}"
        if decision["action"] in ("replace", "append"):
            disposition += f"<br>{link('Точная правка ' + item['id'], output / 'corrections.md')} (ID {item['id']})"
        else:
            disposition += "<br>Текст правки не выпускается; решение зафиксировано в листе."
        proof = previous_checks[item["id"]]
        if proof["kind"] == "none":
            previous_text = "Прежнего источника нет: " + cell(proof["reason"])
        else:
            previous_text = (link("Прежний документ" if proof["kind"] == "document" else "Сообщение пользователя", proof["path"])
                             + f"<br>SHA-256: `{proof['sha256']}`<br>")
            if proof["quote"] is None:
                previous_text += "Пункт не найден; просмотрено: " + cell(proof["not_found_reason"])
            else:
                previous_text += "«" + cell(proof["quote"]) + "»"
            if proof.get("clause"):
                previous_text += "<br>Пункт: " + cell(proof["clause"])
            if proof.get("reason"):
                previous_text += "<br>Причина отсутствия прежнего документа: " + cell(proof["reason"])
        if proof["kind"] != "document":
            previous_text += "<br>Отсутствие прежнего документа установлено составителем; автоматически не подтверждено."
        review.append("| " + " | ".join((cell(item["id"] + " / " + item["topic"]) + "<br>Категория: " + cell(item["category"]), citation,
                                          previous_text,
                                          quoted, STATUSES[item["status"]], disposition)) + " |")
    for group in grouped:
        first, decision = group[0], group[0]["decision"]
        ids = ", ".join(item["id"] for item in group)
        texts = [decision["text"], decision.get("original", ""), first["source"]["quote"] or ""]
        fence = "`" * max(3, 1 + max((len(m.group()) for text in texts for m in re.finditer(r"`+", text)), default=0))
        corrections += [f"## {ids} — {first['topic']}", "",
                        f"Документ: {link(by_id[first['source']['document']]['label'], by_id[first['source']['document']]['path'])}",
                        f"Место правки: {decision['target']}", ""]
        for item in group:
            corrections += [f"- {item['id']}: {STATUSES[item['status']]}. Основание: {item['decision']['reason']}"]
        if decision["action"] == "replace":
            corrections += ["", f"Контекст: пункт {first['source']['clause']}; страница {first['source']['page']}.",
                            "", fence + "text", first["source"]["quote"], fence,
                            "", "Заменить только фрагмент:", "", fence + "text", decision["original"], fence,
                            "", "на:", "", fence + "text", decision["text"], fence, "", "Остальное условие сохранить.", ""]
        else:
            corrections += ["", "Добавить:", "", fence + "text", decision["text"], fence, ""]
    corrections += ["## Решения без изменения текста", ""]
    unchanged = [item for item in data["decisions"] if item not in changes]
    if not unchanged:
        corrections.append("Нет.")
    for item in unchanged:
        decision = item["decision"]
        corrections += [f"- {item['id']} / {item['topic']}: {ACTIONS[decision['action']]}. "
                        f"Статус: {STATUSES[item['status']]}.<br>Основание: {decision['reason']}<br>"
                        f"Выбранное условие: {decision['selected']}<br>План: {decision['plan']}<br>Текст правки не выпускается."]
    return {"terms-review.md": ("\n".join(review) + "\n").encode(),
            "corrections.md": ("\n".join(corrections) + "\n").encode()}


def build(input_path, output):
    input_path, output = Path(input_path).resolve(), Path(output).resolve()
    raw = input_path.read_bytes()
    data, sources, verification = load_review(raw, input_path.parent)
    outputs = render(data, sources, verification, output)
    outputs["review-input.json"] = raw
    manifest = {"format": 5, "created_at": datetime.now(timezone.utc).isoformat(),
                "input_path": str(input_path), "sources": sources, "verification": verification,
                "outputs": {name: sha256(content) for name, content in outputs.items()}}
    output.mkdir(parents=True, exist_ok=False)
    for name, content in outputs.items():
        (output / name).write_bytes(content)
    (output / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return {"directory": str(output), "decisions": len(data["decisions"]),
            "corrections": len(correction_groups(data, sources, verification["citations"]))}


def audit(directory):
    directory = Path(directory).resolve()
    manifest = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))
    fields(manifest, ("format", "created_at", "input_path", "sources", "outputs", "verification"), (), "manifest")
    if manifest["format"] != 5:
        raise ValueError("Неподдерживаемая версия сверки: нужен формат 5. Сохраните прежний пакет без изменений; "
                         "для новой выдачи подготовьте отдельную версию по текущей схеме")
    if set(manifest["outputs"]) != {"review-input.json", "terms-review.md", "corrections.md"}:
        raise ValueError("Неподдерживаемый или изменённый манифест")
    for name, expected in manifest["outputs"].items():
        if sha256((directory / name).read_bytes()) != expected:
            raise ValueError(f"Изменён сохранённый файл: {name}. Сохраните исправление отдельной версией")
    raw = (directory / "review-input.json").read_bytes()
    images = [{"path": row["image_path"], "sha256": row["image_sha256"]}
              for row in manifest["verification"]["signature_matrix"] if "image_path" in row]
    originals = [source["original"] for source in manifest["sources"] if "original" in source]
    for source in manifest["sources"] + originals + [p for p in manifest["verification"]["previous"] if "path" in p] + images:
        path = Path(source["path"])
        if not path.is_file():
            raise ValueError(f"Исходный документ отсутствует: {path}")
        if sha256(path.read_bytes()) != source["sha256"]:
            raise ValueError(f"Исходный документ изменён: SHA-256 не совпадает с манифестом: {path}")
    data, sources, verification = load_review(raw, Path(manifest["input_path"]).parent)
    if sources != manifest["sources"]:
        raise ValueError("Исходные документы изменены: SHA-256 или путь не совпадает с манифестом")
    if verification != manifest["verification"]:
        raise ValueError("Проверка охвата, цитат или источник ограничения изменены относительно манифеста")
    for name, expected in render(data, sources, verification, directory).items():
        if (directory / name).read_bytes() != expected:
            raise ValueError(f"Файл {name} не воспроизводится из сохранённых решений. Выпустите отдельную версию")
    return {"valid": True, "directory": str(directory), "sources": len(sources),
            "decisions": len(data["decisions"]), "state_checked": False}


def amendment_changes(directory):
    """Точные поправки для допсоглашения из той же проверенной сверки."""
    directory = Path(directory).resolve()
    audit(directory)
    input_raw = (directory / "review-input.json").read_bytes()
    manifest_raw = (directory / "manifest.json").read_bytes()
    data, manifest = json.loads(input_raw), json.loads(manifest_raw)
    sources = {source["id"]: source for source in manifest["sources"]}
    groups = correction_groups(data, manifest["sources"], manifest["verification"]["citations"])
    if not groups:
        raise ValueError("Сверка не содержит replace/append: допсоглашение без точных поправок не формируется")
    sections = []
    for group in groups:
        first, decision = group[0], group[0]["decision"]
        source = first["source"]
        lines = [f"Документ: {sources[source['document']]['label']}. Место правки: {decision['target']}.",
                 f"Пункт {source['clause']}; страница {source['page']}."]
        if decision["action"] == "replace":
            lines += ["Заменить только фрагмент:", decision["original"], "на:", decision["text"], "Остальное условие сохранить."]
        else:
            lines += ["Добавить:", decision["text"]]
        sections.append("\n".join(lines))
    return {"text": "\n\n".join(sections), "review_source": {
        "directory": str(directory), "manifest_sha256": sha256(manifest_raw), "input_sha256": sha256(input_raw)}}


def pointer(data, path):
    """JSON Pointer: точный путь в объекте/массиве, без поиска похожего поля."""
    current = data
    try:
        for part in path[1:].split("/"):
            key = part.replace("~1", "/").replace("~0", "~")
            current = current[int(key)] if isinstance(current, list) else current[key]
    except (KeyError, TypeError, ValueError, IndexError) as error:
        raise ValueError(f"В состоянии отсутствует путь {path}") from error
    return current


def check_current_review_references(state, base, directory):
    """Текущие подтверждения не должны ссылаться на внутреннюю или старую сверку."""
    for key, check in state.get("checks", {}).items():
        source = check.get("source") if isinstance(check, dict) else None
        if not isinstance(source, str):
            continue
        # Ссылки с пробелами читаются целиком; дополнительно поддерживаем простой путь в тексте.
        candidates = re.findall(r"\]\((<[^>]+>|[^)\n]+)\)", source)
        candidates += re.findall(r"[^\s`\"'<>()[\]]+/(?:terms-review\.md|corrections\.md|review-input\.json|manifest\.json)", source)
        for candidate in candidates:
            candidate = candidate.strip().removeprefix("<").removesuffix(">")
            if "://" in candidate:
                continue
            path = (base / unquote(re.sub(r":\d+$", "", candidate.split("#", 1)[0]))).resolve()
            is_review = (path.parent / "manifest.json").is_file() and (path.parent / "review-input.json").is_file()
            if is_review and path.parent != directory:
                raise ValueError(f"checks.{key}.source ссылается на другую сверку {path}. "
                                 f"Перечитайте актуальный результат {directory / 'terms-review.md'}, обновите текущий факт и ссылку; "
                                 "прежние объяснения сохраните в history")


def check_state(directory, state_path, plan_path=None):
    """Сверить ожидания таблицы с текущими файлами, ничего не изменяя."""
    result = audit(directory)
    directory, state_path = Path(directory).resolve(), Path(state_path).resolve()
    state_raw = state_path.read_bytes()
    state = json.loads(state_raw)
    data = json.loads((directory / "review-input.json").read_bytes())
    manifest = json.loads((directory / "manifest.json").read_bytes())
    check_current_review_references(state, state_path.parent, directory)
    registry = state.get("documents", [])
    originals = [source["original"] for source in manifest["sources"] if "original" in source]
    current_sources = {Path(source["path"]).resolve() for source in manifest["sources"] + originals}
    for source in manifest["sources"] + originals:
        matching = [entry for entry in registry if (state_path.parent / entry["path"]).resolve() == Path(source["path"])]
        if len(matching) != 1 or matching[0].get("sha256") != source["sha256"]:
            raise ValueError(f"Полученный исходник не зарегистрирован однозначно или SHA не совпадает: {source['path']}. "
                             "Зарегистрируйте весь полученный пакет; это не означает согласования или подписания")
    for proof in manifest["verification"]["previous"]:
        issued = [entry for entry in registry if entry.get("group") == proof["group"] and entry.get("delivered_at")]
        if proof["kind"] != "document":
            earlier = [entry for entry in issued if stamp(entry["delivered_at"]) < stamp(data["comparison_at"])
                       and (state_path.parent / entry["path"]).resolve() not in current_sources]
            if earlier:
                paths = ", ".join(entry["path"] for entry in earlier)
                raise ValueError(f"{proof['id']}: до comparison_at в группе {proof['group']} уже выданы документы: {paths}. "
                                 "Используйте previous.source.kind=document с цитатой относящегося пункта; "
                                 "если условие отсутствует — quote=null и not_found_reason. none/message не заменяют чтение документа")
            continue
        matching = [entry for entry in issued if (state_path.parent / entry["path"]).resolve() == Path(proof["path"])]
        if len(matching) != 1 or matching[0].get("sha256") != proof["sha256"]:
            raise ValueError(f"{proof['id']}: прежний документ не подтверждён реестром выдачи или его SHA не совпадает")
        stamp(matching[0]["delivered_at"])
        basis = manifest["verification"]["review_basis"]
        if manifest["verification"]["scope"]["mode"] == "full" and basis["edition"] == "received":
            entry = matching[0]
            string(entry.get("status"), "Статус прежнего документа в реестре")
            package = ([old for old in issued if old.get("version") == entry["version"]]
                       if entry.get("version") else [entry])
            for old in package:
                status = string(old.get("status"), "Статус прежнего документа в реестре")
                if status.lower().startswith(("draft", "proposed")) and old.get("current") is not False:
                    raise ValueError(f"{proof['id']}: прежний собственный проект {old['path']} ещё актуален. "
                                     "Снимите весь прежний пакет через handoff register --supersedes; сохраните файл и delivered_at")
    publication_known = bool(state.get("schedule", {}).get("publication_at"))
    has_plan_bindings = any(item["implementation"].get("plan") for item in data["decisions"])
    if plan_path is None and (publication_known or has_plan_bindings):
        raise ValueError("Известная дата публикации или плановые связи сверки требуют текущий план --plan")
    plan_data, plan_raw = None, None
    if plan_path is not None:
        plan_path = Path(plan_path).resolve()
        plan_raw = plan_path.read_bytes()
        plan_data = json.loads(plan_raw)
    events = {event["id"]: event for section in ("events", "completed") for event in (plan_data or {}).get(section, [])}
    for item in data["decisions"]:
        applied = item["implementation"]
        if publication_known and applied.get("plan_pending_reason"):
            raise ValueError(f"{item['id']}: дата публикации уже известна; заполните плановые связи вместо plan_pending_reason")
        for binding in applied.get("state", []):
            actual = pointer(state, binding["path"])
            if json.dumps(actual, sort_keys=True) != json.dumps(binding["value"], sort_keys=True):
                raise ValueError(f"{item['id']}: состояние {binding['path']} = {actual!r}, таблица требует {binding['value']!r}")
        for expected in applied.get("plan", []):
            event = events.get(expected["id"])
            if event is None or stamp(event["at"]) != stamp(expected["at"]):
                raise ValueError(f"{item['id']}: нет ожидаемого события {expected['id']} на {expected['at']} в плане/истории выполнения")
            if applied["kind"] == "unresolved" and event.get("kind") != "organizational":
                raise ValueError(f"{item['id']}: неразрешённое условие требует отдельного организационного действия")
        check_timing_state(item, state, events)
    spec = importlib.util.spec_from_file_location("review_deal", Path(__file__).with_name("deal.py"))
    deal = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(deal)
    if plan_data is None:
        deal.validate(state, state_path)
    else:
        deal.validate_plan(state, plan_data, state_path)
    return dict(result, state_checked=True, state={"path": str(state_path), "sha256": sha256(state_raw)},
                plan={"path": str(plan_path), "sha256": sha256(plan_raw)} if plan_data is not None else None)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    builder = commands.add_parser("build", help="Создать таблицу и лист правок в новом каталоге")
    builder.add_argument("input", help="JSON решений; пути документов относительно этого файла")
    builder.add_argument("output", help="Новый каталог версии (существующий не перезаписывается)")
    auditor = commands.add_parser("audit", help="Сверить источники, решения и выданные файлы")
    auditor.add_argument("directory")
    checker = commands.add_parser("check-state", help="Перед выдачей проверить перенос условий, прежние документы и актуальность плана")
    checker.add_argument("directory")
    checker.add_argument("--state", required=True)
    checker.add_argument("--plan", help="Обязателен при известной дате публикации или плановых связях в сверке")
    args = parser.parse_args()
    try:
        if args.command == "build":
            result = build(args.input, args.output)
        elif args.command == "check-state":
            result = check_state(args.directory, args.state, args.plan)
        else:
            result = audit(args.directory)
    except (ValueError, OSError, TypeError, KeyError) as exc:
        parser.exit(1, f"Ошибка: {exc}\n")
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
