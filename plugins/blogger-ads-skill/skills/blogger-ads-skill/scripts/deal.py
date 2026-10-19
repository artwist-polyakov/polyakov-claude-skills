#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""Состояние сделки, проверка готовности и переносимое расписание. Без сетевых действий."""
import argparse
import hashlib
import importlib.util
import json
import math
import re
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4
from zoneinfo import ZoneInfo


_LINK_SPEC = importlib.util.spec_from_file_location("creative_links", Path(__file__).with_name("creative_links.py"))
creative_links = importlib.util.module_from_spec(_LINK_SPEC)
_LINK_SPEC.loader.exec_module(creative_links)


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def write_state(state_path, state):
    temporary = state_path.with_name("." + state_path.name + "." + uuid4().hex + ".tmp")
    try:
        with temporary.open("x", encoding="utf-8") as file:
            file.write(json.dumps(state, ensure_ascii=False, indent=2) + "\n")
        temporary.replace(state_path)
    finally:
        temporary.unlink(missing_ok=True)


def save_source_copy(state_path, raw, directory, suffix, original):
    sha256 = hashlib.sha256(raw).hexdigest()
    relative = Path("creative") / directory / (sha256 + suffix)
    saved = state_path.parent / relative
    saved.parent.mkdir(parents=True, exist_ok=True)
    if saved.exists():
        if saved.read_bytes() != raw:
            raise ValueError(f"Сохранённый исходник изменён: {saved}; он не будет перезаписан")
    else:
        with saved.open("xb") as file:
            file.write(raw)
    return {"path": relative.as_posix(), "sha256": sha256,
            "original_path": str(Path(original).resolve())}


def stamp(value):
    if not isinstance(value, str) or not value.strip():
        raise ValueError("Дата и время не заполнены: нужна строка со смещением часового пояса")
    result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if result.tzinfo is None:
        raise ValueError("Дата и время должны содержать часовой пояс: 2026-11-10T09:00:00+03:00")
    return result


def local_datetime(day, clock, zone, field):
    """Местные дата и час должны задавать единственный существующий момент."""
    result = datetime.fromisoformat(f"{day}T{clock}").replace(tzinfo=zone)
    roundtrip = result.astimezone(timezone.utc).astimezone(zone)
    if roundtrip.replace(tzinfo=None) != result.replace(tzinfo=None):
        raise ValueError(f"{field}: {result:%Y-%m-%d %H:%M} не существует в {zone.key} "
                         "из-за перевода часов; выберите другое время напоминания")
    if result.utcoffset() != result.replace(fold=1).utcoffset():
        raise ValueError(f"{field}: {result:%Y-%m-%d %H:%M} встречается дважды в {zone.key} "
                         "из-за перевода часов; выберите другое время напоминания")
    return result


def scene_clock_entries(state):
    clock = state.get("clock")
    if clock is None:
        return []
    if not isinstance(clock, dict) or clock.get("mode") != "scenario":
        raise ValueError("clock.mode: поддерживается scenario; для обычной сделки clock не нужен")
    entries = clock.get("entries")
    if not isinstance(entries, list) or not entries:
        raise ValueError("clock.entries: нужен непустой список явно заданных времён сцены и их источников")
    for entry in entries:
        if not isinstance(entry, dict):
            raise ValueError("clock.entries: каждая запись содержит at и source")
        stamp(entry.get("at"))
        if not isinstance(entry.get("source"), str) or not entry["source"].strip():
            raise ValueError("clock.entries.source: укажите реплику пользователя и её точную фразу о времени сцены")
    return entries


def validate_scene_clock(state):
    entries = scene_clock_entries(state)
    if entries:
        known = {stamp(entry["at"]) for entry in entries}
        for index, entry in enumerate(state.get("history", [])):
            if isinstance(entry, dict) and "scene_at" in entry and stamp(entry["scene_at"]) not in known:
                raise ValueError(f"history[{index}].scene_at: время отсутствует в clock.entries; "
                                 "сохраните последнее явно заданное время сцены, не время исследования или компьютера")
    return entries


def effective_now(state, supplied=None):
    """В сцене время берётся только из состояния; обычные сделки работают как прежде."""
    entries = validate_scene_clock(state)
    requested = stamp(supplied.isoformat() if isinstance(supplied, datetime) else supplied) if supplied is not None else None
    if entries:
        current = stamp(entries[-1]["at"])
        if requested is not None and requested != current:
            raise ValueError(f"Время {requested.isoformat()} не совпадает с текущим временем сцены {current.isoformat()}. "
                             "Без новой явно заданной даты используйте сохранённое время; при новой реплике вызовите set-scene-time с источником")
        return current
    return requested if requested is not None else datetime.now(timezone.utc)


def set_scene_time(state_path, at, source):
    state_path = Path(state_path)
    state = read_json(state_path)
    entry = {"at": stamp(at).isoformat(), "source": source}
    entries = scene_clock_entries(state)
    state["clock"] = {"mode": "scenario", "entries": entries + [entry]}
    validate_scene_clock(state)
    if entries and stamp(entries[-1]["at"]) == stamp(entry["at"]):
        return {"state": str(state_path), "clock": {"mode": "scenario", "entries": entries}, "changed": False}
    write_state(state_path, state)
    return {"state": str(state_path), "clock": state["clock"], "changed": True}


def sentence(value):
    """Добавить служебную точку, только если исходная фраза ещё не закончена."""
    return value if value.rstrip().endswith((".", "!", "?", "…")) else value + "."


def digest(creative):
    payload = {key: creative.get(key) for key in ("text", "links", "media_sha256")}
    if "link_map" in creative:
        payload["link_map"] = creative["link_map"]
    return hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def validate_creative_links(links):
    if not isinstance(links, list) or not all(creative_links.is_url(url) for url in links):
        raise ValueError("creative.links: нужен упорядоченный список абсолютных http/https URL без пробелов")


def verify_creative_source(creative, state_path=None):
    """Сверить рабочий креатив с отдельными дословными копиями исходников."""
    provenance = creative.get("provenance")
    if provenance is None:  # Совместимость с состояниями, созданными до импорта.
        return
    if not isinstance(provenance, dict):
        raise ValueError("creative.provenance: требуется объект источников text и links")
    for key in ("text", "links"):
        source = provenance.get(key)
        if not isinstance(source, dict) or not source.get("path") or not source.get("sha256"):
            raise ValueError(f"creative.provenance.{key}: нужны path и sha256")
        path = Path(source["path"])
        if not path.is_absolute():
            if state_path is None:
                raise ValueError("Для относительного пути исходника нужен путь файла состояния")
            path = Path(state_path).resolve().parent / path
        raw = path.read_bytes()
        if hashlib.sha256(raw).hexdigest() != source["sha256"]:
            raise ValueError(f"Исходник креатива {key} изменён: {path}. Новую версию импортируйте отдельно")
        value = raw.decode("utf-8") if key == "text" else json.loads(raw.decode("utf-8"))
        if key == "links":
            link_map = creative_links.parse_links(value)
            if "link_map" in creative or any(not isinstance(row, str) for row in value):
                if creative.get("link_map") != link_map:
                    raise ValueError(f"creative.link_map не совпадает с исходной картой: {path}; сохраните якоря и порядок через import-creative")
            value = [row["url"] for row in link_map]
        if creative.get(key) != value:
            raise ValueError(f"creative.{key} не совпадает с сохранённым исходником: {path}. Нельзя подтверждать изменённую версию; используйте import-creative")


def fingerprint(state, state_path=None):
    verify_creative_source(state["creative"], state_path)
    return digest(state["creative"])


def inspect_creative(state, state_path=None, expect_literal=None):
    """Показать буквальные упоминания в тексте; действительность ERID не проверяется."""
    creative_sha256 = fingerprint(state, state_path)
    text = state["creative"].get("text")
    if not isinstance(text, str):
        raise ValueError("creative.text: для диагностики требуется строка")
    mentions = []
    patterns = (("label", r"(?<!\w)(?i:erid)\s*:\s*(?P<literal>\S+)"),
                ("query", r"[?&]erid=(?P<literal>[^\s&#]*)"))
    for kind, pattern in patterns:
        for match in re.finditer(pattern, text):
            start, end = match.span("literal")
            line_breaks = list(re.finditer(r"\r\n|\r|\n", text[:start]))
            context_start, context_end = max(0, match.start() - 32), min(len(text), end + 32)
            mentions.append({"kind": kind, "literal": match.group("literal"), "span": [start, end],
                             "line": len(line_breaks) + 1,
                             "column": start - (line_breaks[-1].end() if line_breaks else 0) + 1,
                             "context": text[context_start:context_end], "context_span": [context_start, context_end]})
    mentions.sort(key=lambda item: item["span"][0])
    if expect_literal is not None and not any(item["literal"] == expect_literal for item in mentions):
        raise ValueError("Заявленный литерал " + json.dumps(expect_literal, ensure_ascii=False)
                         + " отсутствует среди упоминаний в creative.text. Точные исходные значения: "
                         + json.dumps([item["literal"] for item in mentions], ensure_ascii=False)
                         + ". Креатив и статусы не изменены")
    return {"creative_sha256": creative_sha256, "text_sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
            "mentions": mentions,
            "note": "Буквальные фрагменты creative.text, не подтверждение выдачи, действительности или типа идентификатора. "
                    "span/context_span — полуинтервалы с нуля по символам Unicode; line/column — с единицы. "
                    "Скобки и пунктуация не обрезаны, значения query не декодированы."}


def verify_creative_approval(proof, state_path=None):
    """Согласование из команды привязано к дословной сохранённой реплике."""
    if not isinstance(proof, dict) or "source_sha256" not in proof:
        return  # Старые записи с обычным source остаются совместимыми.
    if any(not isinstance(proof.get(key), str) or not proof[key].strip()
           for key in ("source", "source_sha256", "quote")):
        raise ValueError("creative_approval: нужны source, source_sha256 и дословная quote")
    path = Path(proof["source"])
    if not path.is_absolute():
        if state_path is None:
            raise ValueError("Для относительного пути согласования нужен путь файла состояния")
        path = Path(state_path).resolve().parent / path
    raw = path.read_bytes()
    if hashlib.sha256(raw).hexdigest() != proof["source_sha256"]:
        raise ValueError(f"Источник согласования изменён: {path}; восстановите сохранённую копию")
    if proof["quote"] not in raw.decode("utf-8"):
        raise ValueError("Цитата согласования отсутствует в сохранённом источнике")


def approve_creative(state_path, source_path, quote, now=None):
    """Записать согласование текущей версии, не подтверждая маркировку и выход."""
    state_path = Path(state_path).resolve()
    state = validate(read_json(state_path), state_path)
    checked_at = effective_now(state, now).isoformat()
    creative = state.get("creative", {})
    if not creative.get("provenance") or not isinstance(creative.get("text"), str) or not creative["text"].strip():
        raise ValueError("Для согласования нужен непустой креатив из import-creative")
    creative_sha256 = fingerprint(state, state_path)
    raw = Path(source_path).read_bytes()
    if not isinstance(quote, str) or not quote.strip() or quote not in raw.decode("utf-8"):
        raise ValueError("Нужна непустая дословная цитата согласования из файла реплики пользователя")
    previous = state["checks"].get("creative_approval", {})
    if previous.get("status") == "confirmed" and previous.get("creative_sha256") == creative_sha256:
        # Повторное чтение того же согласованного материала не является новым
        # событием. Сохраняем первоначальное доказательство и время выполнения.
        return {"state": str(state_path), "creative_approval": previous, "changed": False}
    source = save_source_copy(state_path, raw, "approvals", ".txt", source_path)
    proof = {"status": "confirmed", "checked_at": checked_at, "creative_sha256": creative_sha256,
             "source": source["path"], "source_sha256": source["sha256"],
             "original_path": source["original_path"], "quote": quote}
    state["checks"]["creative_approval"] = proof
    state.setdefault("history", []).append({"at": checked_at, "action": "approve_creative", **proof})
    validate(state, state_path)
    write_state(state_path, state)
    return {"state": str(state_path), "creative_approval": proof, "changed": True}


def import_creative(state_path, text_path, links_path, now=None):
    """Импортировать исходники без правки текста и без подтверждения согласования."""
    state_path = Path(state_path).resolve()
    state = read_json(state_path)
    now = effective_now(state, now)
    previous = state.get("creative", {})
    previous_digest = digest(previous)
    previous_import_digest = previous_digest
    for entry in reversed(state.get("history", [])):
        if isinstance(entry, dict) and entry.get("action") == "import_creative" and entry.get("creative_sha256"):
            previous_import_digest = entry["creative_sha256"]
            break
    raw_sources = {"text": Path(text_path).read_bytes(), "links": Path(links_path).read_bytes()}
    text = raw_sources["text"].decode("utf-8")
    link_map = creative_links.parse_links(json.loads(raw_sources["links"].decode("utf-8")))
    links = [row["url"] for row in link_map]
    creative = dict(previous, text=text, links=links, link_map=link_map)
    provenance = {}
    for key, original in (("text", text_path), ("links", links_path)):
        provenance[key] = save_source_copy(state_path, raw_sources[key], "sources",
                                           ".txt" if key == "text" else ".json", original)
    creative["provenance"] = provenance
    current_digest = digest(creative)
    previous_sources = previous.get("provenance")
    source_changed = previous_sources is not None and (
        not isinstance(previous_sources, dict) or any(
            not isinstance(previous_sources.get(key), dict)
            or previous_sources[key].get("sha256") != provenance[key]["sha256"]
            for key in ("text", "links")))
    # Рабочие текст/медиа могли изменить до импорта; учитываем прежнюю полную версию и исходники.
    changed = current_digest != previous_digest or current_digest != previous_import_digest or source_changed
    invalidated = {}
    checks = state.setdefault("checks", {})
    hash_checks = ("creative_approval", "erid", "ord_creative", "marking")
    for key in (*hash_checks, "legality", "scheduled_message"):
        proof = checks.get(key, {})
        requires_hash = key in hash_checks or proof.get("creative_sha256") is not None
        if changed or (requires_hash and proof.get("creative_sha256") != current_digest):
            if proof.get("status") != "pending":
                invalidated[key] = dict(proof)
            checks[key] = dict(proof, status="pending",
                               reason="Импортирован креатив; требуется подтверждение этой версии")
    state["creative"] = creative
    if changed or previous.get("provenance") != provenance or invalidated:
        state.setdefault("history", []).append({
            "at": now.isoformat(), "action": "import_creative",
            "previous_creative_sha256": previous_import_digest, "creative_sha256": current_digest,
            "provenance": provenance, "invalidated_checks": invalidated})
    validate(state, state_path)
    inspection = inspect_creative(state, state_path)
    write_state(state_path, state)
    return {"state": str(state_path), "creative_sha256": current_digest,
            "changed": changed, "invalidated_checks": sorted(invalidated), "provenance": provenance,
            "inspection": inspection}


def hours(value, count):
    return (value.astimezone(timezone.utc) + timedelta(hours=count)).astimezone(value.tzinfo)


def business_before(value, count, holidays, working_days):
    while count:
        value -= timedelta(days=1)
        day = value.date().isoformat()
        if day in working_days or (value.weekday() < 5 and day not in holidays):
            count -= 1
    return value


def positive(value, name, allow_zero=False):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0 or (value == 0 and not allow_zero):
        raise ValueError(f"{name}: требуется {'неотрицательное' if allow_zero else 'положительное'} число")
    return value


FEE_RULE_SOURCE = "https://publication.pravo.gov.ru/document/0001202508150004"
ORD_RULE_SOURCE = "https://www.consultant.ru/document/cons_doc_LAW_418000/044cb7cb4857f2a9907d69072ea6f0df3054eb49/"


def ord_reporting(schedule):
    reporting = schedule.get("ord_reporting", {"applicability": "unknown", "reminder_time": "09:00"})
    if not isinstance(reporting, dict) or reporting.get("applicability") not in ("unknown", "applicable", "not_applicable"):
        raise ValueError("ord_reporting.applicability: unknown, applicable или not_applicable")
    for key in ("source", "responsible", "distribution_end_source"):
        value = reporting.get(key)
        if value is not None and (not isinstance(value, str) or not value.strip()):
            raise ValueError(f"ord_reporting.{key}: требуется непустой текст")
    if reporting["applicability"] != "unknown" and not reporting.get("source"):
        raise ValueError("ord_reporting.source: укажите основание применимости или неприменимости обычного месячного срока")
    if reporting.get("distribution_end_at") is not None:
        stamp(reporting["distribution_end_at"])
        if not reporting.get("distribution_end_source"):
            raise ValueError("ord_reporting.distribution_end_source: нужно основание границы распространения")
    if not isinstance(reporting.get("reminder_time"), str) or not re.fullmatch(r"(?:[01][0-9]|2[0-3]):[0-5][0-9]", reporting["reminder_time"]):
        raise ValueError("ord_reporting.reminder_time: организационное время HH:MM, не час из закона")
    return reporting


def ord_events(reporting, start, planned_end, zone):
    """Контроль по месяцам полуоткрытого интервала [начало, конец)."""
    if reporting["applicability"] == "not_applicable":
        return []
    explicit_end = reporting.get("distribution_end_at")
    end = stamp(explicit_end).astimezone(zone) if explicit_end else planned_end
    if end <= start:
        raise ValueError("ord_reporting.distribution_end_at: конец распространения должен быть позже начала")
    period_source = reporting.get("distribution_end_source") if explicit_end else "Предварительный конец: начало размещения + schedule.duration_hours; это не доказательство удаления поста"
    first = start.date().replace(day=1)
    result = []
    while datetime(first.year, first.month, 1, tzinfo=zone) < end:
        following = date(first.year + (first.month == 12), first.month % 12 + 1, 1)
        # Первые сутки после окончания месяца — день 1, а не день 0.
        deadline = following + timedelta(days=29)
        month = first.strftime("%Y-%m")
        preliminary = reporting["applicability"] == "unknown"
        note = (f"Месяц распространения: {month}. Обычный срок по п. 10 ПП РФ от 28.05.2022 № 974: "
                f"30 календарных дней после месяца, дата {deadline.isoformat()}. Источник: {ORD_RULE_SOURCE}. "
                f"{reporting['reminder_time']} ({zone.key}) — организационное время, не установленный законом час. "
                f"Основание применимости: {reporting.get('source') or 'не установлено'}. "
                f"Ответственный за передачу: {reporting.get('responsible') or 'уточнить, не назначен по роли пользователя'}. "
                f"Основание периода: {period_source}. Сверить статистику, акт и исходный договор; сохранить подтверждение передачи за этот месяц. "
                "Это контроль передачи ответственным участником, не подача сведений и не подтверждение выполнения. ")
        if preliminary:
            note += "Применимость месячного срока требует проверки; дата пока служит предварительным контролем. "
        note += "После исполнения договора и при сохранении поста отдельно проверить продолжительность отчётности и оговорку п. 10 об авторских видеороликах; не считать дату УПД концом распространения."
        result.append({"id": f"ord-{month}", "title": ("Предварительный контроль: " if preliminary else "") + f"Проверить передачу сведений в ОРД за {month}",
                       "at": local_datetime(deadline, reporting["reminder_time"], zone, "ord_reporting.reminder_time"),
                       "note": note, "month": month, "control_date": deadline.isoformat(),
                       "legal_date": None if preliminary else deadline.isoformat(), "legal_source": ORD_RULE_SOURCE,
                       "applicability": reporting["applicability"], "responsible": reporting.get("responsible"),
                       "period_source": period_source})
        first = following
    return result


def fee_obligation(schedule):
    """Явные основания и периоды; роль пользователя не определяет обязанность."""
    fee = schedule.get("fee_obligation", {"applicability": "unknown", "quarters": [],
                                          "reminder_time": "09:00"})
    if not isinstance(fee, dict) or fee.get("applicability") not in ("unknown", "applicable", "not_applicable"):
        raise ValueError("fee_obligation.applicability: unknown, applicable или not_applicable")
    source = fee.get("source")
    if source is not None and (not isinstance(source, str) or not source.strip()):
        raise ValueError("fee_obligation.source: требуется непустой текст основания")
    payer = fee.get("payer")
    if payer is not None and (not isinstance(payer, str) or not payer.strip()):
        raise ValueError("fee_obligation.payer: требуется наименование конкретного плательщика")
    if fee["applicability"] != "unknown" and not source:
        raise ValueError("fee_obligation.source: нужно основание применимости или неприменимости")
    if fee["applicability"] == "applicable" and not payer:
        raise ValueError("fee_obligation.payer: укажите установленного законом плательщика")
    quarters = fee.get("quarters", [])
    if not isinstance(quarters, list):
        raise ValueError("fee_obligation.quarters: требуется список кварталов YYYY-Q1 … YYYY-Q4")
    seen = set()
    for quarter in quarters:
        if (not isinstance(quarter, str) or not re.fullmatch(r"[0-9]{4}-Q[1-4]", quarter)
                or quarter < "2025-Q2" or quarter > "9998-Q4"):
            raise ValueError("fee_obligation.quarters: квартал должен иметь вид YYYY-Q1 … YYYY-Q4, начиная с 2025-Q2")
        if quarter in seen:
            raise ValueError(f"fee_obligation.quarters: повторяется квартал {quarter}")
        seen.add(quarter)
    if fee["applicability"] == "applicable" and not quarters:
        raise ValueError("fee_obligation.quarters: для применимой обязанности нужны платёжные кварталы")
    if quarters and not fee.get("source"):
        raise ValueError("fee_obligation.source: укажите основание выбора платёжных кварталов, даже предварительных")
    if not isinstance(fee.get("reminder_time"), str) or not re.fullmatch(r"(?:[01][0-9]|2[0-3]):[0-5][0-9]", fee["reminder_time"]):
        raise ValueError("fee_obligation.reminder_time: организационное время HH:MM, не час из закона")
    return fee


def fee_payment_basis(state):
    """Содержательные свидетельства оплаты; время повторной сверки не меняет SHA."""
    evidence = {}
    for section in ("checks", "completed_events"):
        records = state.get(section, {})
        if not isinstance(records, dict):
            raise ValueError(f"{section}: требуется объект")
        proof = records.get("payment")
        if proof is not None and not isinstance(proof, dict):
            raise ValueError(f"{section}.payment: требуется объект свидетельства оплаты")
        evidence[section + ".payment"] = ({key: value for key, value in proof.items() if key != "checked_at"}
                                          if proof is not None else None)
    raw = json.dumps(evidence, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return {"sha256": hashlib.sha256(raw).hexdigest(), "evidence": evidence}


def validate_fee_payment_basis(state, fee):
    saved = fee.get("payment_basis_sha256")
    if saved is not None and (not isinstance(saved, str) or not re.fullmatch(r"[0-9a-f]{64}", saved)):
        raise ValueError("fee_obligation.payment_basis_sha256: нужен SHA-256 из fee-payment-basis")
    payment = state.get("checks", {}).get("payment", {})
    required = fee.get("source") is not None and isinstance(payment, dict) and payment.get("status") == "confirmed"
    if (required or saved is not None) and saved != fee_payment_basis(state)["sha256"]:
        raise ValueError("fee_obligation.payment_basis_sha256 отсутствует или относится к прежнему свидетельству оплаты. "
                         "Пересмотрите прежнее основание source, кварталы и оставшиеся действия с учётом полученного платежа "
                         "или изменения/отзыва его подтверждения; затем запишите актуальный SHA из fee-payment-basis. "
                         "Не заменяйте проверку основания одним новым хешем. Дата оплаты сама по себе не устанавливает юридический период дохода")


def fee_for_plan(schedule, controls, zone):
    """Пустой период: предварительный квартал только из договорной даты оплаты."""
    fee = fee_obligation(schedule)
    if fee["applicability"] != "unknown" or fee.get("quarters"):
        return fee, None
    due_at = schedule.get("payment_due_at")
    due_on = stamp(due_at).astimezone(zone).date() if due_at else None
    if due_on is None:
        control = next((item for item in controls if item["check"] == "payment"
                        and item["kind"] == "business_days_before_publication"), None)
        if control:
            due_on = date.fromisoformat(control["date"])
    if due_on is None:
        return fee, None
    quarter = f"{due_on.year:04d}-Q{(due_on.month - 1) // 3 + 1}"
    if not "2025-Q2" <= quarter <= "9998-Q4":
        return fee, None
    term = schedule["deadline_terms"]["payment"]
    source = (f"Предварительный квартал по договорному сроку платежа {due_on.isoformat()}. "
              + sentence(f"Условие: {term['condition']}") + " " + sentence(f"Источник: {term['source']}"))
    if fee.get("source"):
        source += " " + sentence(f"Сохранённое основание проверки отчислений: {fee['source']}")
    return dict(fee, quarters=[quarter], source=source), due_on.isoformat()


def fee_events(fee, zone, payment_due_on=None):
    """Три контрольные даты по ПП № 1224; суммы и УИН не рассчитываются."""
    if fee["applicability"] == "not_applicable":
        return []
    result = []
    preliminary = fee["applicability"] == "unknown"
    for quarter in fee.get("quarters", []):
        year, number = int(quarter[:4]), int(quarter[-1])
        next_year = year + (number == 4)
        second_month = (number * 3) % 12 + 2
        calculation = date(next_year, second_month, 15)
        deadlines = (
            ("assessment", calculation, "Проверить расчёт отчислений в ЕРИР", "п. 9: срок формирования расчёта РКН"),
            ("review", calculation + timedelta(days=10), "Завершить проверку и уточнение расчёта отчислений", "пп. 13, 15: 10 календарных дней после срока формирования расчёта"),
            ("payment", date(next_year, second_month + 1, 5), "Проверить уплату отчислений по расчёту и УИН", "п. 8: крайний срок уплаты"),
        )
        for step, deadline, title, rule in deadlines:
            note = (f"Платёжный квартал: {quarter}. Дата по ПП РФ от 15.08.2025 № 1224: {deadline.isoformat()} ({rule}). "
                    f"Источник нормы: {FEE_RULE_SOURCE}. Основание периода и применимости: {fee['source']}. "
                    f"{fee['reminder_time']} ({zone.key}) — выбранное организационное время, не установленный законом час. "
                    "Сверить сумму и УИН в ЕРИР; план не является начислением или платёжным поручением. ")
            if preliminary:
                title = "Предварительный контроль: " + title[0].lower() + title[1:]
                note += "Применимость и плательщик ещё не установлены: уточнить их до окна проверки; не считать этот план обязанностью пользователя платить."
            else:
                note += f"Установленный плательщик: {fee['payer']}."
            metadata = {}
            if fee.get("payment_basis_sha256") is not None:
                metadata["payment_basis_sha256"] = fee["payment_basis_sha256"]
            if payment_due_on:
                note += (f" Квартал предварительно выбран по договорной дате платежа {payment_due_on}, "
                         "это не фактическая дата поступления дохода. Дата проверки оплаты не устанавливает дату поступления; "
                         "уточнить настоящий период по выписке и обновить кварталы с источником.")
                metadata.update(period_basis="contractual_payment_deadline", payment_due_on=payment_due_on)
            if step == "assessment":
                note += " Если расчёт ещё не появился утром 15-го, проверить повторно после его формирования; отсутствие утром не означает просрочку РКН."
            result.append({"id": f"fee-{quarter}-{step}", "title": f"{title} за {quarter}",
                           "at": local_datetime(deadline, fee["reminder_time"], zone, "fee_obligation.reminder_time"),
                           "note": note, "quarter": quarter, "legal_date": deadline.isoformat(),
                           "legal_source": FEE_RULE_SOURCE, "obligation_source": fee["source"],
                           "applicability": fee["applicability"], "payer": fee.get("payer"), **metadata})
    return result


def validate(state, state_path=None):
    validate_scene_clock(state)
    if state.get("schema_version") != 1:
        raise ValueError("Поддерживается schema_version: 1")
    deal_id = state.get("deal_id", "")
    if not isinstance(deal_id, str) or not deal_id or any(c not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_" for c in deal_id):
        raise ValueError("deal_id: используйте буквы латиницы, цифры, дефис и подчёркивание")
    if state.get("user_role") not in ("blogger", "advertiser", "agency"):
        raise ValueError("user_role: blogger, advertiser или agency")
    if not isinstance(state.get("checks", {}), dict) or not isinstance(state.get("completed_events", {}), dict):
        raise ValueError("checks и completed_events должны быть объектами")
    policy = state["policy"]
    for key in ("require_prepayment", "require_erir_visibility"):
        if not isinstance(policy.get(key), bool):
            raise ValueError(f"policy.{key}: требуется true или false")
    schedule = state.get("schedule", {})
    if isinstance(state.get("commercial"), dict) and "payment_received" in state["commercial"]:
        raise ValueError("commercial.payment_received дублирует статус оплаты: удалите этот флаг и используйте только checks.payment. "
                         "Сумму, дату поступления и источник подтверждения можно сохранить отдельно; не создавайте второй статус оплаты")
    validate_fee_payment_basis(state, fee_obligation(schedule))
    ord_reporting(schedule)
    terms = schedule.get("deadline_terms", {})
    if not isinstance(terms, dict):
        raise ValueError("schedule.deadline_terms: требуется объект")
    for key in ("payment", "erid"):
        term = terms.get(key, {})
        if not isinstance(term, dict):
            raise ValueError(f"deadline_terms.{key}: требуется объект")
        precision = term.get("precision", "unspecified")
        if precision not in ("unspecified", "relative", "date", "datetime"):
            raise ValueError(f"deadline_terms.{key}.precision: unspecified, relative, date или datetime")
        if precision == "unspecified":
            if term.get("condition") is not None or term.get("source") is not None:
                raise ValueError(f"deadline_terms.{key}: unspecified требует condition=null и source=null; для известного условия выберите relative, date или datetime")
        elif any(not isinstance(term.get(field), str) or not term[field].strip() for field in ("condition", "source")):
            raise ValueError(f"deadline_terms.{key}: сохраните исходное условие condition и его source непустыми строками")
        at = schedule.get(key + "_due_at")
        if at and precision != "datetime":
            raise ValueError(f"{key}_due_at требует deadline_terms.{key}.precision=datetime: точный час должен быть согласован, а не взят из времени публикации")
        if at:
            stamp(at)
        if precision == "datetime" and not at:
            raise ValueError(f"deadline_terms.{key}: внесите согласованный точный срок в {key}_due_at")
        relative_calculation(term, key)
    recovery = state.get("checks", {}).get("edo_exchange", {})
    if not isinstance(recovery, dict):
        raise ValueError("checks.edo_exchange: требуется объект проверки обмена ЭДО")
    if "edo_exchange" in state.get("checks", {}) and "recovery_expected_on" not in recovery:
        raise ValueError("checks.edo_exchange.recovery_expected_on: явно укажите дату из сообщения о восстановлении YYYY-MM-DD "
                         "или null, если дата не сообщена. При известной дате нужны recovery_source и recovery_check_time")
    if recovery.get("recovery_expected_on") is not None:
        calendar_date(recovery["recovery_expected_on"], "edo_exchange.recovery_expected_on")
    if recovery.get("recovery_check_on") is not None:
        calendar_date(recovery["recovery_check_on"], "edo_exchange.recovery_check_on")
    recovery_check_on = recovery.get("recovery_check_on") or recovery.get("recovery_expected_on")
    if recovery_check_on is not None:
        if not isinstance(recovery.get("recovery_source"), str) or not recovery["recovery_source"].strip():
            raise ValueError("edo_exchange.recovery_source: укажите ссылку и точную формулировку сообщения об ЭДО")
        if not isinstance(recovery.get("recovery_check_time"), str) or not re.fullmatch(r"(?:[01][0-9]|2[0-3]):[0-5][0-9]", recovery["recovery_check_time"]):
            raise ValueError("edo_exchange.recovery_check_time: выбранное организационное время HH:MM")
    elif any(recovery.get(key) is not None for key in ("recovery_source", "recovery_check_time")):
        raise ValueError("edo_exchange: для контроля восстановления нужна дата из сообщения recovery_expected_on "
                         "или отдельно выбранная дата проверки recovery_check_on")
    creative = state.get("creative", {})
    if creative.get("provenance") is None:
        validate_creative_links(creative.get("links", []))
    verify_creative_source(creative, state_path)
    verify_creative_approval(state.get("checks", {}).get("creative_approval"), state_path)
    approvals = set()
    for entry in state.get("history", []):
        if not isinstance(entry, dict) or entry.get("action") != "approve_creative" or "source_sha256" not in entry:
            continue
        identity = json.dumps(entry, ensure_ascii=False, sort_keys=True)
        if identity in approvals:
            raise ValueError("История содержит точную копию approve_creative. Сохраните первоначальную запись и доказательство, "
                             "удалите только повтор этой записи; новую реплику описывайте отдельным событием. "
                             "Не сбрасывайте подтверждение неизменного креатива ради повторного согласования")
        approvals.add(identity)
    return state


def confirmation_time(proof):
    """Время записи с текстовым источником; внешние факты не проверяются."""
    if (not isinstance(proof, dict) or proof.get("status") != "confirmed"
            or not isinstance(proof.get("source"), str) or not proof["source"].strip()
            or not proof.get("checked_at")):
        return None
    return stamp(proof["checked_at"])


def confirmed(proof, now):
    checked_at = confirmation_time(proof)
    return checked_at is not None and checked_at <= now


def valid_token(token):
    return (isinstance(token, str) and bool(token.strip())
            and not any(c.isspace() for c in token) and token not in ("[ERID]", "ERID"))


def ord_creative_issues(proof, token, creative_sha256):
    """Проверить запись сверки с ОРД; саму сверку выполняет агент по источникам."""
    proof = proof if isinstance(proof, dict) else {}
    issues = []
    if proof.get("creative_sha256") != creative_sha256:
        issues.append("ord_creative: подтверждение относится к другой версии текста, ссылок или медиа")
    if proof.get("token") != token:
        issues.append("Сверка креатива в ОРД относится к другому идентификатору")
    verified = proof.get("verified")
    verified = verified if isinstance(verified, dict) else {}
    for key, label in (("text", "полного текста"), ("links", "всех ссылок"),
                       ("media", "медиа или их отсутствия")):
        if verified.get(key) is not True:
            issues.append(f"ord_creative: не подтверждена сверка {label} в зарегистрированной карточке ОРД")
    return issues


CHECK_LABELS = {
    "legality": "Проверка тематики и площадки",
    "contract": "Подписание договора и приложений",
    "creative_approval": "Согласование окончательного креатива",
    "marking": "Проверка пометки Реклама и рекламодателя",
    "erid": "Подтверждение идентификатора рекламы от ОРД",
    "ord_creative": "Сверка зарегистрированной версии креатива в ОРД",
    "scheduled_message": "Актуальная проверка очереди публикаций и отсутствия дублей",
    "erir": "Согласованная проверка видимости в ЕРИР",
    "payment": "Получение согласованной предоплаты",
    "payment_terms": "Согласованные условия оплаты",
    "edo_exchange": "Проверить восстановление ЭДО и завершение обмена документами",
    "fee_period": "Выяснить применимость рекламных отчислений и квартал поступления дохода",
}


EVENT_CHECKS = {"creative": ["creative_approval"], "payment": ["payment"], "erid": ["erid", "ord_creative"]}


def action_context(state, linked):
    """Факты связанных проверок; повторная отметка времени сама по себе не новый факт."""
    checks = state.get("checks", {})
    context = {}
    for key in sorted(linked):
        proof = checks.get(key)
        context[key] = ({field: value for field, value in proof.items() if field != "checked_at"}
                        if isinstance(proof, dict) else proof)
    return hashlib.sha256(json.dumps(context, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def action_text(item):
    values = {key: item[key] for key in ("id", "title", "source")}
    return hashlib.sha256(json.dumps(values, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def action_review(state, item, reason, now=None):
    """Зафиксировать осмысленный пересмотр конкретного действия без изменения его текста."""
    if not isinstance(reason, str) or not reason.strip():
        raise ValueError("review-action: укажите основание, почему title и source остаются актуальными")
    if item.get("kind") != "organizational" or not item.get("checks"):
        raise ValueError("review-action: требуется организационное действие со связанными checks")
    now = effective_now(state, now)
    return {"context_sha256": action_context(state, item["checks"]), "text_sha256": action_text(item),
            "reason": reason.strip(), "reviewed_at": now.isoformat()}


def review_action(state_path, action_id, reason, now=None):
    state = read_json(state_path)
    validate(state, state_path)
    matching = [item for item in state.get("schedule", {}).get("reporting_deadlines", []) if item.get("id") == action_id]
    if len(matching) != 1:
        raise ValueError("review-action: укажите существующий уникальный reporting_deadlines.id")
    item = matching[0]
    item["context_review"] = action_review(state, item, reason, now)
    write_state(state_path, state)
    return {"id": action_id, **item["context_review"]}


def calendar_date(value, field):
    if not isinstance(value, str) or not re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", value):
        raise ValueError(f"{field}: требуется календарная дата YYYY-MM-DD без выдуманного часа")
    return date.fromisoformat(value)


def relative_calculation(term, key):
    calculation = term.get("calculation")
    if calculation is None:
        return None
    name = f"deadline_terms.{key}.calculation"
    if term.get("precision") != "relative":
        raise ValueError(f"{name}: расчёт задаётся только при precision=relative")
    if not isinstance(calculation, dict) or calculation.get("kind") not in ("business_days_before_publication", "uncomputed"):
        raise ValueError(f"{name}.kind: business_days_before_publication или uncomputed")
    required = calculation.get("requires", [])
    if (not isinstance(required, list) or any(not isinstance(item, str) or item not in CHECK_LABELS or item == key for item in required)
            or len(set(required)) != len(required)):
        raise ValueError(f"{name}.requires: список явных необходимых проверок без повторов и самой {key}")
    if calculation["kind"] == "business_days_before_publication":
        days = calculation.get("days")
        if isinstance(days, bool) or not isinstance(days, int) or days < 0:
            raise ValueError(f"{name}.days: целое неотрицательное число рабочих дней")
    else:
        if not isinstance(calculation.get("reason"), str) or not calculation["reason"].strip():
            raise ValueError(f"{name}.reason: почему нельзя вычислить дату обязательства")
        calendar_date(calculation.get("review_on"), name + ".review_on")
    return calculation


def relative_deadline_controls(schedule, publication, holidays, working, strict):
    controls = []
    for key in ("payment", "erid"):
        term = schedule.get("deadline_terms", {}).get(key, {})
        if term.get("precision") != "relative":
            continue
        calculation = relative_calculation(term, key)
        if calculation is None:
            if strict:
                raise ValueError(f"deadline_terms.{key}.calculation: для плана явно задайте число рабочих дней до публикации "
                                 "или uncomputed с reason и организационной датой review_on; текст условия автоматически не разбирается")
            continue
        business = calculation["kind"] == "business_days_before_publication"
        day = (business_before(publication, calculation["days"], holidays, working).date()
               if business else calendar_date(calculation["review_on"], f"deadline_terms.{key}.calculation.review_on"))
        date_note = (f"Вычисленная календарная дата условия: {day.isoformat()}. Рабочих дней до публикации: {calculation['days']}. "
                     "Учтены сохранённые выходные и рабочие исключения; час обязательства им не устанавливается."
                     if business else
                     f"Организационная дата проверки выполнимости: {day.isoformat()}; договорная дата не вычислена. "
                     + sentence(f"Причина: {calculation['reason']}"))
        label = "оплаты" if key == "payment" else "передачи ERID"
        note = (sentence(f"Условие {label}: {term['condition']}") + " " + sentence(f"Основание: {term['source']}")
                + " " + date_note + " При наступлении этого дня, если основная проверка ещё не подтверждена, "
                "немедленно проверить выполнимость и необходимые условия; прошлое активное действие не переносить молча. "
                "Организационный час не является договорным пределом; публикация автоматически не переносится.")
        controls.append({"check": key, "kind": calculation["kind"], "date": day.isoformat(),
                         "requires": list(calculation.get("requires", [])), "condition": term["condition"],
                         "source": term["source"], "note": note})
    return controls


def completion(state, event_id, now):
    checks = state.get("checks", {})
    automatic = {"creative": "creative_approval", "payment": "payment", "erid": "erid"}
    proof = checks.get(automatic[event_id], {}) if event_id in automatic else state.get("completed_events", {}).get(event_id, {})
    if not confirmed(proof, now):
        return None
    if event_id in ("creative", "erid") and proof.get("creative_sha256") != digest(state.get("creative", {})):
        return None
    if event_id == "erid":
        registered = checks.get("ord_creative", {})
        if (not valid_token(proof.get("token")) or not confirmed(registered, now)
                or ord_creative_issues(registered, proof["token"], digest(state.get("creative", {})))):
            return None
        return registered
    return proof


def _plan(state, now=None, state_path=None, strict_deadlines=False):
    now = effective_now(state, now)
    validate(state, state_path)
    schedule = state["schedule"]
    zone = ZoneInfo(schedule["timezone"])
    if not schedule.get("publication_at"):
        raise ValueError("Дата публикации не согласована; план пока нельзя рассчитать")
    publication = stamp(schedule["publication_at"])
    local = publication.astimezone(zone)
    if local.replace(tzinfo=None) != publication.replace(tzinfo=None) or local.utcoffset() != publication.utcoffset():
        raise ValueError("publication_at не соответствует указанному timezone")
    publication = local
    actual = state.get("publication", {}).get("actual_at")
    base = stamp(actual).astimezone(zone) if actual else publication
    duration = positive(schedule["duration_hours"], "duration_hours")
    top = positive(schedule["top_hours"], "top_hours", True)
    if top > duration:
        raise ValueError("top_hours не может превышать duration_hours")
    closing = schedule.get("closing_after_end_hours")
    if closing is not None:
        closing = positive(closing, "closing_after_end_hours", True)
    claims = schedule.get("claims_after_end_hours")
    if claims is not None:
        claims = positive(claims, "claims_after_end_hours", True)
    link_delay = schedule.get("link_after_publication_hours", 1)
    if link_delay is not None:
        link_delay = positive(link_delay, "link_after_publication_hours", True)
    text_days = schedule.get("text_before_business_days", 2)
    if isinstance(text_days, bool) or not isinstance(text_days, int) or text_days < 0:
        raise ValueError("text_before_business_days: целое неотрицательное число")
    readiness = positive(schedule.get("readiness_before_hours", 24), "readiness_before_hours", True)
    revision = schedule["revision"]
    if isinstance(revision, bool) or not isinstance(revision, int) or revision < 1:
        raise ValueError("schedule.revision: целое число от 1")
    holidays = set(schedule.get("holidays", []))
    working = set(schedule.get("working_days", []))
    for item in holidays | working:
        date.fromisoformat(item)
    if holidays & working:
        raise ValueError("Дата одновременно указана выходной и рабочей")
    controls = relative_deadline_controls(schedule, publication, holidays, working, strict_deadlines)
    end = hours(base, duration)
    events = []
    completed = []

    def add(key, title, at, note, **metadata):
        linked = set(metadata.get("checks", EVENT_CHECKS.get(key, [])))
        related = [control for control in controls if linked & {control["check"], *control["requires"]}]
        if related:
            note += " " + " ".join(control["note"] for control in related)
            metadata["deadline_controls"] = related
        reviewed = []
        for decision_id, term in schedule.get("reviewed_terms", {}).items():
            if key not in term.get("event_ids", []) or term.get("mode") == "not_applicable":
                continue
            mode = term["mode"]
            labels = {"provisional": "Предварительное условие; принятие не подтверждено",
                      "agreed": "Согласованное условие", "deferred": "Решение по предложенному сроку отложено",
                      "unresolved": "Срок требует уточнения"}
            note += " " + labels[mode] + ": " + term["subject"] + ". " + term["reason"]
            value = term["selected_value"] if term["selected_value"] is not None else term["received_value"]
            if isinstance(value, dict) and value.get("condition"):
                note += " Условие: " + value["condition"]
            elif type(value) in (int, float):
                note += f" Значение срока: {value} ч."
            if mode == "agreed":
                note += " Источник принятия: " + term["agreement_source"]
            reviewed.append({"id": decision_id, "mode": mode, "subject": term["subject"],
                             "received_value": term["received_value"], "selected_value": term["selected_value"]})
        if reviewed:
            metadata["reviewed_terms"] = reviewed
        event = {"id": key, "uid": f"{state['deal_id']}-{key}@blogger-ads-skill",
                 "title": title, "at": at.isoformat(), "overdue": at < now,
                 "note": note, "sequence": revision, **metadata}
        proof = completion(state, key, now)
        if proof:
            event.update(overdue=False, completed_at=proof["checked_at"], completion_source=proof["source"])
            completed.append(event)
        else:
            events.append(event)

    creative_at = business_before(publication, text_days, holidays, working)
    if text_days:
        creative_at = local_datetime(creative_at.date(), creative_at.time(), zone, "text_before_business_days")
    add("creative", "Согласовать текст, ссылки и материалы", creative_at,
        "Организационное напоминание; время публикации не задаёт договорный час передачи текста. Сверить окончательную версию до регистрации в ОРД; сохранить подтверждение сторон.")
    for key, title in (("payment", "Проверить выполнение условий оплаты"), ("erid", "Получить и проверить маркировку")):
        if schedule.get(key + "_due_at"):
            term = schedule["deadline_terms"][key]
            instruction = ("Проверить получение оплаты по согласованным условиям и сохранить подтверждение получателя."
                           if key == "payment" else
                           "Получить токен ОРД и сверить полный текст, все URL и медиа либо их отсутствие в зарегистрированной карточке с утверждённым материалом; сохранить доказательства обеих проверок.")
            provisional = any(row.get("mode") == "provisional" and row.get("target") == f"/schedule/deadline_terms/{key}"
                              for row in schedule.get("reviewed_terms", {}).values())
            deadline_label = "Предварительный срок" if provisional else "Согласованный срок"
            add(key, title, stamp(schedule[key + "_due_at"]).astimezone(zone),
                sentence(f"{deadline_label}: {term['condition']}") + " "
                + sentence(f"Основание: {term['source']}") + " " + instruction)
    add("readiness", "Проверить готовность размещения", hours(publication, -readiness),
        "Проверить договор, оплату по согласованным условиям, маркировку, полный материал ОРД, "
        "законность площадки и креатива, текущую очередь публикаций. "
        "Незакрытый пункт в этот служебный час требует уточнить препятствие и действительный срок исполнения; "
        "он не означает обязательного переноса публикации. Перенос обсуждать при конкретном препятствии к согласованному сроку.")
    add("publication", "Проверить публикацию и сохранить доказательства", base,
        "Проверить отложенные записи на дубли; зафиксировать фактические время, ссылку и скриншот.")
    if link_delay is not None:
        link_note = ("Срок после публикации по выбранному условию" if "link_after_publication_hours" in schedule
                     else "Сохранённый организационный ориентир старого плана: через 1 час после публикации; договорный срок не установлен этим значением")
        add("link", "Подготовить ссылку для заказчика", hours(base, link_delay), link_note + "; отправлять только по поручению пользователя.")
    if top:
        add("top_end", "Зафиксировать окончание времени в топе", hours(base, top), "Скриншот и статистика по договору.")
    add("placement_end", "Зафиксировать окончание размещения", end, "Скриншот, просмотры, дата окончания услуги.")
    if claims is not None:
        add("claims", "Проверить замечания заказчика", hours(end, claims), "Срок из договора; не считать молчание приёмкой без соответствующего условия.")
    if closing is not None:
        add("closing", "Подготовить закрывающие документы", hours(end, closing), "УПД по фактической услуге; не подменять дату составления датой напоминания.")
    reporting_ids = set()
    for item in schedule.get("reporting_deadlines", []):
        report_id = item.get("id") if isinstance(item, dict) else None
        if not isinstance(report_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]+", report_id):
            raise ValueError("reporting_deadlines.id: требуется непустой постоянный идентификатор из букв латиницы, цифр, дефиса и подчёркивания")
        if report_id in reporting_ids:
            raise ValueError(f"reporting_deadlines: повторяется id {report_id}")
        if report_id == "edo-recovery":
            raise ValueError("reporting_deadlines: edo-recovery зарезервирован для автоматического контроля восстановления ЭДО. "
                             "Удалите ручной дубль; для отдельного действия обмена используйте другой постоянный ID. "
                             "При неизвестной дате укажите recovery_expected_on=null; для выбранного дня проверки есть recovery_check_on")
        if re.fullmatch(r"ord-[0-9]{4}-[0-9]{2}", report_id):
            raise ValueError(f"reporting_deadlines: {report_id} зарезервирован для автоматического месячного контроля. Удалите ручной дубль; особое договорное действие перенесите под отдельный ID с прежним сроком и источником")
        reporting_ids.add(report_id)
        if not isinstance(item.get("source"), str) or not item["source"].strip():
            raise ValueError("Каждый reporting_deadlines требует source: норма, согласованный срок или основание организационного действия")
        note = item["source"]
        metadata = {}
        if item.get("kind") == "organizational" or "checks" in item:
            linked = item.get("checks")
            if (item.get("kind") != "organizational" or not isinstance(linked, list) or not linked
                    or any(not isinstance(key, str) or key not in CHECK_LABELS for key in linked)
                    or len(set(linked)) != len(linked)):
                raise ValueError(f"reporting_deadlines.{report_id}: для действия нужны kind=organizational и непустой список checks без повторов: {', '.join(CHECK_LABELS)}")
            if not isinstance(item.get("title"), str) or not item["title"].strip():
                raise ValueError(f"reporting_deadlines.{report_id}: укажите конкретное действие в title")
            instructions = [CHECK_LABELS[key] for key in linked]
            if "ord_creative" in linked:
                instructions.append("Сверить полный текст, все URL и медиа либо их отсутствие в карточке ОРД")
            note = ("Организационное действие; выбранный час не является договорным сроком. "
                    + "; ".join(instructions) + ". Основание и указания: " + item["source"])
            metadata = {"kind": "organizational", "checks": sorted(linked), "source": item["source"],
                        "context_sha256": action_context(state, linked)}
            review = item.get("context_review")
            if (isinstance(review, dict) and review.get("context_sha256") == metadata["context_sha256"]
                    and review.get("text_sha256") == action_text(item)
                    and isinstance(review.get("reason"), str) and review["reason"].strip()
                    and review.get("reviewed_at") and stamp(review["reviewed_at"]) <= now):
                metadata["context_review"] = review
        add(f"report-{report_id}", item["title"], stamp(item["at"]).astimezone(zone), note, **metadata)
    recovery = state.get("checks", {}).get("edo_exchange", {})
    recovery_check_on = recovery.get("recovery_check_on") or recovery.get("recovery_expected_on")
    if recovery_check_on is not None:
        recovery_at = local_datetime(recovery_check_on, recovery["recovery_check_time"], zone,
                                     "edo_exchange.recovery_check_time")
        reported = (f"Дата из сообщения об ЭДО: {recovery['recovery_expected_on']}. "
                    if recovery.get("recovery_expected_on") else "Дата восстановления ЭДО в сообщении не указана. ")
        add("edo-recovery", "Проверить доступность ЭДО", recovery_at,
            reported + sentence(f"Источник и точная формулировка: {recovery['recovery_source']}")
            + f" Организационная проверка: {recovery_check_on} {recovery['recovery_check_time']} ({zone.key}); "
            "выбранные день и час не являются обещанным моментом восстановления. "
            "Проверить доступность ЭДО и сохранить результат. Восстановление сервиса не подтверждает подписи или завершение обмена; "
            "отправку и получение документов проверить отдельным действием. Это ожидание не относится к ОРД или ЕРИР.",
            kind="service_recovery", service="edo", recovery_expected_on=recovery.get("recovery_expected_on"),
            recovery_check_on=recovery_check_on,
            source=recovery["recovery_source"])
    fee, preliminary_payment_day = fee_for_plan(schedule, controls, zone)
    for event in fee_events(fee, zone, preliminary_payment_day):
        add(event.pop("id"), **event)
    monthly = ord_reporting(schedule)
    for event in ord_events(monthly, base, end, zone):
        add(event.pop("id"), **event)
    warnings = []
    if link_delay is None:
        warnings.append("Срок отправки ссылки заказчику после публикации не установлен. Событие link не создано: уточните условие или добавьте отдельную организационную проверку.")
    if closing is None:
        warnings.append("Срок выдачи закрывающих документов (УПД) после окончания размещения не установлен. Событие closing не создано: уточните условие и начало отсчёта; организационную проверку добавьте отдельно, не выдавая её за договорный срок.")
    if monthly["applicability"] == "unknown":
        warnings.append("Применимость обычного месячного срока ОРД не установлена. Предварительный контроль сохранён по месяцам размещения; уточните применимость и ответственного.")
    if monthly["applicability"] != "not_applicable" and not monthly.get("distribution_end_at"):
        warnings.append("Месяцы ОРД пока рассчитаны по договорной длительности. Фактическое удаление/окончание распространения не подтверждено; если пост сохраняется, проверьте период дальнейшей отчётности и обновите его границу с источником.")
    if fee["applicability"] == "unknown":
        warnings.append("Применимость отчислений и законный плательщик не установлены. " +
                        (f"Созданы три предварительные проверки по договорной дате платежа {preliminary_payment_day}; "
                         "это не подтверждение доходного периода и не требование уплаты. Предварительные даты не заменяют уточнение фактического периода и плательщика."
                         if preliminary_payment_day else
                         "Созданы предварительные проверки по указанным кварталам; это не требование уплаты."
                         if fee.get("quarters") else
                         "Внесите предварительные кварталы и их основание в schedule.fee_obligation: без них три контрольные даты не рассчитаны."))
    if claims is None:
        warnings.append("Срок замечаний в часах от окончания размещения не установлен. Событие claims не создано: уточните срок и начало отсчёта по договору; внутреннюю проверку можно добавить отдельно.")
    if not schedule.get("payment_due_at") or not schedule.get("erid_due_at"):
        warnings.append("Точные договорные сроки оплаты и/или передачи ERID не заданы; общая проверка готовности их не заменяет.")
    if not schedule.get("work_calendar_verified"):
        warnings.append("Рабочие дни рассчитаны по субботам/воскресеньям и введённым исключениям. Производственный календарь не подтверждён.")
    if not actual:
        warnings.append("Сроки после размещения предварительные: после публикации внесите actual_at и пересчитайте план.")
    return {"deal_id": state["deal_id"], "generated_at": now.isoformat(), "revision": revision,
            "events": sorted(events, key=lambda x: stamp(x["at"])),
            "completed": sorted(completed, key=lambda x: stamp(x["at"])), "warnings": warnings,
            "deadline_controls": controls, "readiness_policy": readiness_policy(state)}


def readiness_policy(state):
    """Показать выбранную роль ЕРИР отдельно от обязательных доказательств ОРД."""
    required = state["policy"]["require_erir_visibility"]
    return {
        "ord": {"required": True, "checks": ["erid", "ord_creative"],
                "content": ["text", "links", "media"],
                "note": "Обязательны подтверждённая выдача токена ОРД и сверка полного зарегистрированного текста, всех ссылок и медиа (либо их отсутствия) с текущим согласованным креативом."},
        "erir_visibility": {
            "required": required,
            "mode": "additional_agreed_requirement" if required else "informational",
            "note": ("Видимость в ЕРИР — дополнительное согласованное условие готовности: policy.require_erir_visibility=true."
                     if required else
                     "Видимость в ЕРИР информационна и не является условием готовности: policy.require_erir_visibility=false. Ожидание статуса ЕРИР само по себе не блокирует размещение.")}}


def readiness_issues(state, now):
    """Общие причины незавершённости проверок; расписание не является доказательством."""
    checks = state.get("checks", {})
    issues = {}

    def missing(key, message):
        issues.setdefault(key, []).append(message)

    if state.get("stage") in ("cancelled", "paused", "closed"):
        missing("stage", "Сделка приостановлена, отменена или уже закрыта")
    if not state.get("schedule", {}).get("publication_at"):
        missing("publication_at", "Согласованная дата и время публикации")

    def evidence(key, label):
        checked_at = confirmation_time(checks.get(key, {}))
        if checked_at is None:
            missing(key, label)
            return False
        if checked_at > now:
            missing(key, f"{label}: время подтверждения находится в будущем")
            return False
        return True

    for key in ("legality", "contract", "creative_approval", "marking", "erid", "ord_creative"):
        evidence(key, CHECK_LABELS[key])
    if evidence("scheduled_message", CHECK_LABELS["scheduled_message"]):
        queue = checks["scheduled_message"]
        try:
            same_time = stamp(queue.get("publication_at")) == stamp(state.get("schedule", {}).get("publication_at"))
        except ValueError:
            same_time = False
        message_id = state.get("publication", {}).get("scheduled_message_id")
        if (not same_time or "scheduled_message_id" not in queue
                or type(queue["scheduled_message_id"]) is not type(message_id)
                or queue["scheduled_message_id"] != message_id):
            missing("scheduled_message", "Заново проверьте очередь для текущего размещения: сохраните в подтверждении "
                    "publication_at и scheduled_message_id, включая явный null при отсутствии ID")
    creative = state.get("creative", {})
    if not isinstance(creative.get("text"), str) or not creative["text"].strip():
        missing("creative_approval", "Окончательный текст рекламы")
    current_digest = digest(creative)
    for key in ("creative_approval", "erid", "marking"):
        if checks.get(key, {}).get("creative_sha256") != current_digest:
            missing(key, f"{key}: подтверждение относится к другой версии текста, ссылок или медиа")
    token = checks.get("erid", {}).get("token", "")
    if not valid_token(token):
        missing("erid", "Отдельное поле token из ОРД; внутренний ID креатива его не заменяет")
    for issue in ord_creative_issues(checks.get("ord_creative", {}), token, current_digest):
        missing("ord_creative", issue)
    if checks.get("marking", {}).get("token") != token:
        missing("marking", "Проверка маркировки относится к другому идентификатору")
    if state["policy"]["require_erir_visibility"]:
        if evidence("erir", CHECK_LABELS["erir"]) and checks["erir"].get("token") != token:
            missing("erir", "Подтверждение ЕРИР относится к другому идентификатору")
    if state["policy"]["require_prepayment"]:
        evidence("payment", CHECK_LABELS["payment"])
    else:
        evidence("payment_terms", CHECK_LABELS["payment_terms"])
    return issues


def action_coverage(state, now, plan_data):
    """Покрыть открытые проверки действиями, не подтверждая их и не назначая часы."""
    before_publication = (not state.get("publication", {}).get("actual_at")
                          and state.get("stage") not in ("published", "closing", "closed", "cancelled", "paused"))
    required = set(readiness_issues(state, now)) & CHECK_LABELS.keys() if before_publication else set()
    if (state.get("stage") not in ("closed", "cancelled", "paused")
            and "edo_exchange" in state.get("checks", {})
            and not confirmed(state["checks"]["edo_exchange"], now)):
        required.add("edo_exchange")
    fee = fee_obligation(state["schedule"])
    if (state.get("stage") not in ("closed", "cancelled", "paused")
            and fee["applicability"] == "unknown"):
        required.add("fee_period")
    covered = {key: [] for key in sorted(required)}
    publication = stamp(state["schedule"]["publication_at"])
    for event in plan_data["events"]:
        for key in event.get("checks", EVENT_CHECKS.get(event["id"], [])):
            if key in covered and (key in ("edo_exchange", "fee_period") or stamp(event["at"]) <= publication):
                covered[key].append(event["id"])
    missing = [key for key, event_ids in covered.items() if not event_ids]
    if missing:
        raise ValueError("План неполон: нет активного действия для проверок " + ", ".join(missing)
                         + ". Добавьте в schedule.reporting_deadlines конкретное действие, постоянные id/at, source, "
                         "kind=organizational и checks; проверки публикации требуют действия не позже выхода. "
                         "Unknown/pending допустимы: подтвердить нужно план действий, а не выдумать внешний факт. "
                         "Общая readiness и выполненные события это действие не заменяют.")
    return covered


def validate_action_timing(state, now, plan_data):
    """Условия без часа ограничивают день контроля, а не создают новый час оплаты."""
    if state.get("stage") in ("closed", "cancelled", "paused"):
        return
    pending = unfinished_checks(state, now)
    zone = ZoneInfo(state["schedule"]["timezone"])
    today = now.astimezone(zone).date()
    for control in plan_data["deadline_controls"]:
        if control["check"] not in pending:
            continue
        last_day = max(date.fromisoformat(control["date"]), today)
        required = {control["check"], *control["requires"]} & pending
        covered = {key for event in plan_data["events"] if stamp(event["at"]).astimezone(zone).date() <= last_day
                   for key in event.get("checks", EVENT_CHECKS.get(event["id"], []))}
        missing = required - covered
        if missing:
            raise ValueError(f"План опаздывает по условию {control['check']} (дата {control['date']}): "
                             f"нет активного организационного действия для {', '.join(sorted(missing))} не позже {last_day.isoformat()}. "
                             "При наступившей дате нужна проверка выполнимости в оставшийся день; "
                             "сохраните прежний ID, не придумывайте договорный час и не передвигайте прошлые действия молча")


def unfinished_checks(state, now):
    """Незавершённые проверки с учётом версии, токена и времени доказательств."""
    pending = set(readiness_issues(state, now))
    checks = state.get("checks", {})
    pending.update(key for key in CHECK_LABELS if not confirmed(checks.get(key, {}), now))
    token = checks.get("erid", {}).get("token")
    pending.update(key for key in ("erid", "ord_creative", "marking", "erir")
                   if not valid_token(token) or checks.get(key, {}).get("token") != token)
    fee = fee_obligation(state["schedule"])
    if fee["applicability"] == "unknown":
        pending.add("fee_period")
    return pending


def retain_completed_actions(state, now, previous, current):
    """Снятое напоминание сохраняет подтверждённое выполнение в истории плана."""
    if previous.get("deal_id") != current["deal_id"]:
        raise ValueError("Предыдущий план относится к другой сделке")
    present = {event["id"] for section in ("events", "completed") for event in current[section]}
    pending = unfinished_checks(state, now)
    checks = state.get("checks", {})
    for section in ("events", "completed"):
        for old in previous.get(section, []):
            if old["id"] in present:
                continue
            explicit = old["id"] in state.get("completed_events", {})
            if explicit and not confirmed(state["completed_events"][old["id"]], now):
                # Явное возобновление или отзыв выполнения важнее прежних checks.
                continue
            linked = old.get("checks", [])
            # Связанные проверки должны быть актуальны и при явной записи выполнения.
            valid = all(key in CHECK_LABELS and key not in pending for key in linked)
            proof = completion(state, old["id"], now)
            if proof and (not valid or ("creative_sha256" in proof and proof["creative_sha256"] != digest(state.get("creative", {})))):
                proof = None
            if not proof and not explicit and old.get("kind") == "organizational" and linked and valid:
                proof = {"checked_at": max((checks[key]["checked_at"] for key in linked), key=stamp),
                         "source": "; ".join(f"{CHECK_LABELS[key]}: {checks[key]['source']}" for key in linked)}
            if proof:
                event = dict(old, overdue=False)
                if section != "completed" or explicit:
                    event.update(completed_at=proof["checked_at"], completion_source=proof["source"])
                current["completed"].append(event)
                present.add(old["id"])
    current["completed"].sort(key=lambda event: stamp(event["at"]))


def plan(state, now=None, state_path=None, previous=None):
    now = effective_now(state, now)
    result = _plan(state, now, state_path, strict_deadlines=True)
    if previous is not None:
        retain_completed_actions(state, now, previous, result)
        preserve_action_ids(state, now, previous, result)
        review_action_context(previous, result)
    result["action_coverage"] = action_coverage(state, now, result)
    validate_action_timing(state, now, result)
    return result


def review_action_context(previous, current):
    """Не переносить прежнее описание активного действия на новые факты молча."""
    old_actions = {event["id"]: event for section in ("events", "completed") for event in previous.get(section, [])}
    stale = []
    for event in current["events"]:
        old = old_actions.get(event["id"])
        if not old or event.get("kind") != "organizational" or old.get("context_sha256") == event["context_sha256"]:
            continue
        if old.get("source") == event.get("source") and not event.get("context_review"):
            stale.append(event["id"])
    if stale:
        raise ValueError("Изменился контекст активных действий без пересмотра title/source: " + ", ".join(stale)
                         + ". Обновите существующие reporting_deadlines под прежними ID. Если обе формулировки всё ещё верны, "
                         "выполните deal.py review-action --state … --id <reporting_deadlines.id> --reason <основание>. "
                         "Новый дубль, перенос часа или внутренняя пересборка не заменяют пересмотр от последнего выданного плана")


def preserve_action_ids(state, now, previous, current):
    """Сохранять ID при том же полном наборе проверок или незавершённом остатке."""
    pending = unfinished_checks(state, now)
    previous_ids = {event["id"] for section in ("events", "completed") for event in previous.get(section, [])}
    current_ids = {event["id"] for section in ("events", "completed") for event in current[section]}
    created = [event for event in current["events"] if event["id"] not in previous_ids
               and event.get("kind") == "organizational" and event.get("checks")]
    for old in previous.get("events", []):
        if old["id"] in current_ids or old.get("kind") != "organizational" or not old.get("checks"):
            continue
        old_checks = set(old["checks"])
        remaining = old_checks & pending
        for new in created:
            same_checks = old_checks == set(new["checks"])
            if same_checks or (remaining and remaining == set(new["checks"]) & pending):
                label = "checks" if same_checks else "незавершённых checks"
                matched = old_checks if same_checks else remaining
                raise ValueError(f"Организационное действие {old['id']} заменено новым {new['id']} "
                                 f"с тем же набором {label} ({', '.join(sorted(matched))}). "
                                 "Сохраните прежний reporting_deadlines.id; меняйте срок, текст и источник существующего действия.")


def validate_plan(state, plan_data, state_path=None):
    """Проверить сохранённый план по текущему состоянию, не изменяя входные данные."""
    if not isinstance(plan_data, dict):
        raise ValueError("План: требуется объект JSON")
    if plan_data.get("deal_id") != state.get("deal_id") or plan_data.get("revision") != state.get("schedule", {}).get("revision"):
        raise ValueError("План относится к другой сделке или редакции состояния")
    if not isinstance(plan_data.get("generated_at"), str):
        raise ValueError("План: требуется generated_at с часовым поясом")

    def content(data):
        result, seen = {}, set()
        for section in ("events", "completed"):
            events = data.get(section, [])
            if not isinstance(events, list):
                raise ValueError(f"План {section}: требуется список событий")
            result[section] = {}
            for event in events:
                if not isinstance(event, dict) or not isinstance(event.get("id"), str) or not event["id"]:
                    raise ValueError(f"План {section}: у события отсутствует id")
                if event["id"] in seen:
                    raise ValueError(f"План: повтор события {event['id']}")
                seen.add(event["id"])
                result[section][event["id"]] = {key: value for key, value in event.items() if key not in ("sequence", "overdue")}
        return result

    actual = content(plan_data)
    recalculated = plan(state, stamp(plan_data["generated_at"]), state_path, previous=plan_data)
    expected = content(recalculated)
    if plan_data.get("readiness_policy") != recalculated["readiness_policy"]:
        raise ValueError("План readiness_policy не соответствует текущей политике ОРД/ЕРИР; пересчитайте deal.py plan")
    for section in ("events", "completed"):
        if actual[section] != expected[section]:
            raise ValueError(f"План {section} не воспроизводится из текущего состояния; пересчитайте deal.py plan")
    return plan_data


def check(state, now=None, state_path=None):
    now = effective_now(state, now)
    validate(state, state_path)
    if state.get("schedule", {}).get("publication_at"):
        _plan(state, now, state_path)
    blockers = readiness_issues(state, now)
    missing = [message for messages in blockers.values() for message in messages]
    current_digest = digest(state.get("creative", {}))
    return {"ready": not missing, "missing": missing, "blockers": blockers,
            "readiness_policy": readiness_policy(state), "creative_sha256": current_digest,
            "note": "Проверены записи в состоянии сделки. Скрипт не проверяет банк, подписи, ОРД или ЕРИР через интернет и не разрешает публикацию автоматически."}


def escape_ics(value):
    return str(value).replace("\\", "\\\\").replace("\r\n", "\n").replace("\r", "\n").replace("\n", "\\n").replace(";", "\\;").replace(",", "\\,")


def fold(line):
    lines, current = [], ""
    for char in line:
        if len((current + char).encode("utf-8")) > 75:
            lines.append(current)
            current = " "
        current += char
    return "\r\n".join(lines + [current])


def ics(plan_data, now):
    lines = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//blogger-ads-skill//RU", "CALSCALE:GREGORIAN"]
    for event in plan_data["events"]:
        lines += ["BEGIN:VEVENT", f"UID:{event['uid']}", f"SEQUENCE:{event['sequence']}",
                  "DTSTAMP:" + now.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ"),
                  "DTSTART:" + stamp(event["at"]).astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ"),
                  "DURATION:PT15M", "SUMMARY:" + escape_ics(event["title"]),
                  "DESCRIPTION:" + escape_ics(event["note"]), "BEGIN:VALARM", "ACTION:DISPLAY",
                  "TRIGGER:PT0M", "DESCRIPTION:" + escape_ics(event["title"]), "END:VALARM", "END:VEVENT"]
    return "\r\n".join(fold(line) for line in lines + ["END:VCALENDAR"]) + "\r\n"


def changes(previous, current):
    """Сравнить с выданной версией и сохранить SEQUENCE неизменённых событий."""
    if previous.get("deal_id") != current["deal_id"]:
        raise ValueError("Предыдущий план относится к другой сделке")

    def entries(plan_data):
        return {event["uid"]: (section, event)
                for section in ("events", "completed") for event in plan_data.get(section, [])}

    def content(entry):
        section, event = entry
        # Просрочка меняется от хода часов; смысл даты, описание и статус — нет.
        return section, {key: value for key, value in event.items()
                         if key not in ("sequence", "overdue", "context_sha256", "context_review")}

    old_all, new_all = entries(previous), entries(current)
    modified = {uid for uid in old_all.keys() & new_all.keys()
                if content(old_all[uid]) != content(new_all[uid])}
    old = {e["uid"]: e for e in previous["events"]}
    new = {e["uid"]: e for e in current["events"]}
    updated = modified & new.keys() & old.keys()
    result = {"create": sorted(new.keys() - old.keys()), "update": sorted(updated), "cancel": sorted(old.keys() - new.keys())}
    if (modified or old_all.keys() != new_all.keys()) and current["revision"] <= previous["revision"]:
        raise ValueError("При изменении плана увеличьте schedule.revision")
    for uid in old_all.keys() & new_all.keys():
        old_sequence = old_all[uid][1].get("sequence", previous["revision"])
        if isinstance(old_sequence, bool) or not isinstance(old_sequence, int) or old_sequence < 0:
            raise ValueError(f"Предыдущий план: недопустимый sequence события {uid}")
        new_all[uid][1]["sequence"] = old_sequence + (uid in modified)
    return result


def markdown(plan_data):
    """Читаемый план из того же JSON, по которому строится календарь."""
    lines = [f"# Сделка {plan_data['deal_id']}", "", "Напоминания подготовлены, но ещё не установлены в календарь или расписание агента.", ""]
    lines += ["Условия готовности по ОРД и ЕРИР:",
              "- " + plan_data["readiness_policy"]["ord"]["note"],
              "- " + plan_data["readiness_policy"]["erir_visibility"]["note"], ""]
    lines += [f"- {e['at']}: {e['title']}" + (" — время напоминания прошло" if e["overdue"] else "")
              + (f". Дата по норме: {e['legal_date']}; час организационный, источник: {e['legal_source']}." if e.get("legal_date") else
                 f". Предварительная дата контроля: {e['control_date']}; применимость срока не установлена, час организационный." if e.get("control_date") else "")
              + f"\n  {e['note']}"
              for e in plan_data["events"]]
    if plan_data["completed"]:
        lines += ["", "## Выполнено — напоминание больше не требуется", ""]
        lines += [f"- {e['title']}: {e['completed_at']}; основание: {e['completion_source']}" for e in plan_data["completed"]]
    lines += ["", *plan_data["warnings"]]
    return "\n".join(lines) + "\n"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    init = sub.add_parser("init", help="Создать незаполненный файл состояния")
    init.add_argument("--output", required=True, type=Path)
    init.add_argument("--scene-at", help="Явно заданное пользователем время учебной сцены с часовым поясом")
    init.add_argument("--scene-source", help="Реплика пользователя и точная фраза о времени сцены; файл не обязателен")
    scene = sub.add_parser("set-scene-time", help="Сохранить новое явно заданное время учебной сцены")
    scene.add_argument("--state", required=True, type=Path)
    scene.add_argument("--at", required=True)
    scene.add_argument("--source", required=True, help="Новая реплика пользователя и её точная фраза о времени сцены")
    importer = sub.add_parser("import-creative", help="Дословно импортировать текст и исходную JSON-карту ссылок с якорями; согласование не подтверждается")
    importer.add_argument("--state", required=True, type=Path)
    importer.add_argument("--text", required=True, type=Path)
    importer.add_argument("--links", required=True, type=Path)
    importer.add_argument("--now", help="Время импорта с часовым поясом; в сцене должно совпадать с clock")
    approver = sub.add_parser("approve-creative", help="Согласовать текущий импортированный креатив по дословной реплике пользователя; ОРД и публикация не подтверждаются")
    approver.add_argument("--state", required=True, type=Path)
    approver.add_argument("--source", required=True, type=Path, help="Существующий UTF-8 файл реплики пользователя")
    approver.add_argument("--quote", required=True, help="Дословная цитата о согласовании текущей версии")
    approver.add_argument("--now", help="Время фиксации с часовым поясом; в сцене должно совпадать с clock")
    reviewer = sub.add_parser("review-action", help="Подтвердить актуальность прежних title/source действия после изменения связанных проверок")
    reviewer.add_argument("--state", required=True, type=Path)
    reviewer.add_argument("--id", required=True, help="Постоянный reporting_deadlines.id без префикса report-")
    reviewer.add_argument("--reason", required=True, help="Почему обе формулировки остаются верными при текущих фактах")
    reviewer.add_argument("--now", help="Время пересмотра с часовым поясом; в сцене должно совпадать с clock")
    for name in ("check", "plan", "fingerprint", "inspect-creative", "fee-payment-basis"):
        cmd = sub.add_parser(name)
        cmd.add_argument("--state", required=True, type=Path)
        if name not in ("fingerprint", "inspect-creative", "fee-payment-basis"):
            cmd.add_argument("--now", help="Текущее время с часовым поясом; в обычной работе не требуется")
        if name == "inspect-creative":
            cmd.add_argument("--expect-literal", help="Точный ожидаемый фрагмент среди упоминаний в creative.text")
        if name == "plan":
            cmd.add_argument("--output-dir", required=True, type=Path)
            cmd.add_argument("--previous", type=Path)
    args = parser.parse_args()
    try:
        if args.command == "init":
            initial = read_json(Path(__file__).resolve().parent.parent / "assets/deal.example.json")
            initial["deal_id"] = "deal-" + uuid4().hex
            if (args.scene_at is None) != (args.scene_source is None):
                raise ValueError("init: для учебной сцены нужны вместе --scene-at и --scene-source")
            if args.scene_at is not None:
                initial["clock"] = {"mode": "scenario", "entries": [{"at": stamp(args.scene_at).isoformat(), "source": args.scene_source}]}
                validate_scene_clock(initial)
            with args.output.open("x", encoding="utf-8") as file:
                file.write(json.dumps(initial, ensure_ascii=False, indent=2) + "\n")
            print(args.output)
            return
        if args.command == "set-scene-time":
            print(json.dumps(set_scene_time(args.state, args.at, args.source), ensure_ascii=False, indent=2))
            return
        if args.command == "import-creative":
            print(json.dumps(import_creative(args.state, args.text, args.links, args.now), ensure_ascii=False, indent=2))
            return
        if args.command == "approve-creative":
            print(json.dumps(approve_creative(args.state, args.source, args.quote, args.now), ensure_ascii=False, indent=2))
            return
        if args.command == "review-action":
            print(json.dumps(review_action(args.state, args.id, args.reason, args.now), ensure_ascii=False, indent=2))
            return
        state = read_json(args.state)
        if args.command == "fee-payment-basis":
            print(json.dumps(fee_payment_basis(state), ensure_ascii=False, indent=2))
            return
        if args.command == "fingerprint":
            print(fingerprint(state, args.state))
            return
        if args.command == "inspect-creative":
            print(json.dumps(inspect_creative(state, args.state, args.expect_literal), ensure_ascii=False, indent=2))
            return
        now = effective_now(state, args.now)
        if args.command == "check":
            result = check(state, now, args.state)
            print(json.dumps(result, ensure_ascii=False, indent=2))
            if not result["ready"]:
                sys.exit(2)
            return
        previous = read_json(args.previous) if args.previous else None
        result = plan(state, now, args.state, previous)
        if args.previous:
            result["changes"] = changes(previous, result)
        args.output_dir.mkdir(parents=True, exist_ok=False)
        (args.output_dir / "plan.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        (args.output_dir / "reminders.ics").write_bytes(ics(result, now).encode("utf-8"))
        (args.output_dir / "plan.md").write_bytes(markdown(result).encode("utf-8"))
        print(json.dumps({"directory": str(args.output_dir), "events": len(result["events"]), "installed": False}, ensure_ascii=False))
    except (ValueError, KeyError, TypeError, OSError) as error:
        parser.exit(1, f"Ошибка: {error}\n")


if __name__ == "__main__":
    main()
