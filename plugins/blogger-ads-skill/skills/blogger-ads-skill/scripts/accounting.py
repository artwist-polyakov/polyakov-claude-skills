#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = ["lxml>=5.3,<7"]
# ///
"""Счёт и УПД для услуг рекламы российских ИП/ЮЛ. Ничего не отправляет в ЭДО."""

import argparse
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP, localcontext
from html import escape
import json
from hashlib import sha256
from pathlib import Path
import re
from string import Template
import sys
from uuid import UUID
import xml.etree.ElementTree as ET

ASSETS = Path(__file__).resolve().parent.parent / "assets" / "accounting"
SCHEMA = ASSETS / "xsd" / "ON_NSCHFDOPPR_1_997_01_05_03_05.xsd"
CENT = Decimal("0.01")


def required(obj, key, where=""):
    if not isinstance(obj, dict):
        raise ValueError(f"{where or 'Данные'}: нужен объект JSON")
    value = obj.get(key)
    if not isinstance(value, str) or not value.strip() or "<" in value:
        raise ValueError(f"Заполните {where + '.' if where else ''}{key}")
    return value.strip()


def iso_date(value):
    if not isinstance(value, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        raise ValueError(f"Дата должна иметь вид ГГГГ-ММ-ДД: {value!r}")
    return date.fromisoformat(value)


def ru_date(value):
    return iso_date(value).strftime("%d.%m.%Y")


def number(value, field):
    if not isinstance(value, str) or not re.fullmatch(r"\d{1,12}(?:\.\d{1,11})?", value):
        raise ValueError(f"{field}: нужна положительная десятичная строка, например '50000.00'")
    result = Decimal(value)
    if result <= 0:
        raise ValueError(f"{field}: значение должно быть больше нуля")
    return result


def money(value):
    return format(value.quantize(CENT, rounding=ROUND_HALF_UP), ".2f")


def inn_valid(inn):
    if not isinstance(inn, str) or not inn.isascii() or not inn.isdigit() or len(set(inn)) == 1:
        return False
    def check(weights, digits):
        return sum(w * int(n) for w, n in zip(weights, digits)) % 11 % 10
    if len(inn) == 10:
        return check([2, 4, 10, 3, 5, 9, 4, 6, 8], inn) == int(inn[-1])
    if len(inn) == 12:
        return (check([7, 2, 4, 10, 3, 5, 9, 4, 6, 8], inn) == int(inn[-2])
                and check([3, 7, 2, 4, 10, 3, 5, 9, 4, 6, 8], inn) == int(inn[-1]))
    return False


def validate_fio(fio, where):
    if not isinstance(fio, dict):
        raise ValueError(f"Заполните {where}: surname, name, patronymic (если есть)")
    required(fio, "surname", where)
    required(fio, "name", where)
    if "patronymic" in fio and not isinstance(fio["patronymic"], str):
        raise ValueError(f"{where}.patronymic должен быть строкой")


def validate_party(party, where, vat):
    if party.get("kind") not in ("ip", "company"):
        raise ValueError(f"{where}.kind: поддерживаются только ip и company")
    for key in ("name", "inn", "address"):
        required(party, key, where)
    if not inn_valid(party["inn"]) or len(party["inn"]) != (12 if party["kind"] == "ip" else 10):
        raise ValueError(f"{where}.inn: неверная длина или контрольные цифры ИНН")
    if party["kind"] == "company":
        if not re.fullmatch(r"[0-9]{4}[0-9A-Z]{2}[0-9]{3}", required(party, "kpp", where)):
            raise ValueError(f"{where}.kpp: нужен КПП из девяти знаков")
    else:
        validate_fio(party.get("fio"), where + ".fio")
        if party.get("kpp"):
            raise ValueError(f"{where}: у ИП нет КПП; оставьте пустую строку или удалите поле")
        if vat and where == "seller":
            ogrnip = required(party, "ogrnip", where)
            if not re.fullmatch(r"[0-9]{15}", ogrnip) or int(ogrnip[:14]) % 13 % 10 != int(ogrnip[-1]):
                raise ValueError("seller.ogrnip: неверный ОГРНИП")
            iso_date(required(party, "ogrnip_date", where))


def validate_data(data, command):
    """Не угадывает налоговый режим, факт оказания услуг и реквизиты ЭДО."""
    for key in ("document", "contract", "seller", "buyer", "vat", "signer"):
        if not isinstance(data.get(key), dict):
            raise ValueError(f"Отсутствует объект {key}")
    for section in ("document", "contract"):
        required(data[section], "number", section)
        iso_date(required(data[section], "date", section))
    vat = data["vat"]
    if vat.get("rate") not in ("none", "5", "7", "22"):
        raise ValueError("vat.rate: для этого профиля выберите none, 5, 7 или 22; режим УСН ставку не определяет")
    if vat.get("price_mode") not in ("inclusive", "exclusive"):
        raise ValueError("vat.price_mode: inclusive (включён) или exclusive (сверху)")
    if vat["rate"] == "none":
        required(vat, "exemption_basis", "vat")
    taxed = vat["rate"] != "none"
    for side in ("seller", "buyer"):
        validate_party(data[side], side, taxed and command != "invoice")
    signer = data["signer"]
    validate_fio(signer.get("fio"), "signer.fio")
    required(signer, "position", "signer")
    if not isinstance(data.get("items"), list) or not data["items"]:
        raise ValueError("items: нужна хотя бы одна услуга")
    for i, item in enumerate(data["items"], 1):
        if not isinstance(item, dict):
            raise ValueError(f"items[{i}]: нужен объект")
        required(item, "description", f"items[{i}]")
        required(item, "unit", f"items[{i}]")
        if not re.fullmatch(r"[0-9]{3,4}", required(item, "unit_code", f"items[{i}]")):
            raise ValueError(f"items[{i}].unit_code: код ОКЕИ из трёх или четырёх цифр")
        number(item.get("quantity"), f"items[{i}].quantity")
        number(item.get("unit_price"), f"items[{i}].unit_price")
    if command == "invoice":
        bank = data["seller"].get("bank", {})
        required(bank, "name", "seller.bank")
        for key, length in (("bik", 9), ("account", 20), ("correspondent", 20)):
            if not re.fullmatch(r"[0-9]{%d}" % length, required(bank, key, "seller.bank")):
                raise ValueError(f"seller.bank.{key}: требуется {length} цифр")
        status = data.get("invoice_status", "payable")
        if status not in ("payable", "paid_archive"):
            raise ValueError("invoice_status: payable или paid_archive")
        if status == "paid_archive":
            payment = data.get("payment_received", {})
            iso_date(required(payment, "date", "payment_received"))
            if number(required(payment, "amount", "payment_received"), "payment_received.amount") != calculate(data)[1]["gross"]:
                raise ValueError("paid_archive: подтверждённая оплата должна совпадать с полной суммой счёта")
        return
    if data.get("services_performed") is not True:
        raise ValueError("Для УПД подтвердите фактически оказанные услуги: services_performed=true")
    service = iso_date(required(data["document"], "service_date", "document"))
    if service.year != 2026:
        raise ValueError("УПД поддерживает услуги 2026 года; для другого года сначала обновите налоговый профиль")
    if iso_date(data["document"]["date"]) < service:
        raise ValueError("Дата УПД не может предшествовать дате оказания услуг")
    if not isinstance(data.get("payment_docs"), list):
        raise ValueError("Укажите payment_docs: список платежных документов или [] при их отсутствии")
    if not isinstance(data.get("advance_invoices"), list):
        raise ValueError("Укажите advance_invoices: список авансовых счетов-фактур или []")
    for section in ("payment_docs", "advance_invoices"):
        for item in data[section]:
            required(item, "number", section)
            iso_date(required(item, "date", section))
            if iso_date(item["date"]) > iso_date(data["document"]["date"]):
                raise ValueError(f"{section}: дата позже УПД")
            if item.get("correction"):
                required(item["correction"], "number", section + ".correction")
                iso_date(required(item["correction"], "date", section + ".correction"))
    if not isinstance(data.get("had_advance"), bool):
        raise ValueError("Укажите had_advance: true или false — был ли получен аванс")
    if data["had_advance"] and not data["payment_docs"]:
        raise ValueError("При предоплате нужны payment_docs с реквизитами поступившей оплаты")
    if taxed and data["had_advance"] and not data["advance_invoices"]:
        raise ValueError("НДС и предоплата: заполните advance_invoices для строки 5б. Если применимо исключение, оформите УПД в учётной системе")
    if (not taxed or not data["had_advance"]) and data["advance_invoices"]:
        raise ValueError("advance_invoices допустимы только для НДС с зачётом аванса")
    if command != "upd-xml":
        return
    if signer.get("authority") != ("self_ip" if data["seller"]["kind"] == "ip" else "director"):
        raise ValueError("XML поддерживает только подпись самого ИП (self_ip) или руководителя ЮЛ (director), без МЧД")
    if data["seller"]["kind"] == "ip" and signer["fio"] != data["seller"]["fio"]:
        raise ValueError("В этом XML-профиле Ф. И. О. подписанта должны совпадать с ИП")
    edo = data.get("edo", {})
    edo_direction(data)
    required(edo, "operator", "edo")
    UUID(required(edo, "document_uuid", "edo"))
    generated = datetime.fromisoformat(required(data["document"], "generated_at", "document"))
    if generated.tzinfo is None:
        raise ValueError("document.generated_at: укажите смещение, например 2026-10-17T12:00:00+03:00")
    if generated.date() < iso_date(data["document"]["date"]):
        raise ValueError("Дата формирования XML раньше даты документа")


def edo_direction(data):
    """Исходящий титул: продавец отправляет покупателю. ID принадлежит стороне."""
    result = {}
    for side, role in (("seller", "sender_id"), ("buyer", "receiver_id")):
        if not data[side].get("edo_id"):
            raise ValueError(f"Заполните {side}.edo_id по карточке этой стороны. Старые edo.sender_id/receiver_id нужно перенести к продавцу/покупателю после сверки принадлежности")
        ident = required(data[side], "edo_id", side)
        if not re.fullmatch(r"[A-Za-z0-9-]{4,100}", ident) or ident.startswith("000"):
            raise ValueError(f"{side}.edo_id: нужен ID участника у оператора ЭДО; временные 000-ID не поддерживаются")
        legacy = data.get("edo", {}).get(role)
        if legacy is not None and legacy != ident:
            raise ValueError(f"edo.{role} противоречит {side}.edo_id. Сверьте принадлежность ID; направление нельзя менять местами")
        result[role] = ident
    if result["sender_id"] == result["receiver_id"]:
        raise ValueError("Идентификаторы продавца и покупателя ЭДО совпадают")
    return result


def calculate(data):
    rate = Decimal(0) if data["vat"]["rate"] == "none" else Decimal(data["vat"]["rate"]) / 100
    rows = []
    with localcontext() as context:
        context.prec = 50
        for item in data["items"]:
            qty = number(item["quantity"], "quantity")
            price = number(item["unit_price"], "unit_price")
            amount = (qty * price).quantize(CENT, rounding=ROUND_HALF_UP)
            if data["vat"]["price_mode"] == "inclusive":
                gross = amount
                net = (gross / (1 + rate)).quantize(CENT, rounding=ROUND_HALF_UP)
                tax = gross - net
                unit_net = (price / (1 + rate)).quantize(Decimal("0.00000000001"), rounding=ROUND_HALF_UP)
            else:
                net = amount
                tax = (net * rate).quantize(CENT, rounding=ROUND_HALF_UP)
                gross = net + tax
                unit_net = price
            # Достаточная точность цены без длинного хвоста: сумма строки сохраняется.
            for places in range(2, 12):
                candidate = unit_net.quantize(Decimal(1).scaleb(-places), rounding=ROUND_HALF_UP)
                if (candidate * qty).quantize(CENT, rounding=ROUND_HALF_UP) == net:
                    unit_net = candidate
                    break
            if gross >= Decimal("10000000000000000"):
                raise ValueError("Сумма строки превышает предел профиля")
            rows.append(dict(item, net=net, tax=tax, gross=gross, unit_net=unit_net, amount=amount))
        totals = {key: sum((row[key] for row in rows), Decimal(0)) for key in ("net", "tax", "gross")}
    return rows, totals


def fio_text(fio):
    return " ".join(fio[k] for k in ("surname", "name", "patronymic") if fio.get(k))


def upd_unit_price(row):
    """Цена без НДС должна воспроизводить округлённую сумму строки УПД."""
    with localcontext() as context:
        context.prec = 50
        quantity = Decimal(row["quantity"])
        price = row["unit_net"]
        if (price * quantity).quantize(CENT, rounding=ROUND_HALF_UP) != row["net"]:
            price = (row["net"] / quantity).quantize(Decimal("0.00000000001"), rounding=ROUND_HALF_UP)
        if (price * quantity).quantize(CENT, rounding=ROUND_HALF_UP) != row["net"]:
            raise ValueError("Цена без НДС не сходится с суммой строки при точности до 11 знаков; оформите УПД в учётной системе")
        return price


def party_details(party):
    kpp = f", КПП {party['kpp']}" if party.get("kpp") else ""
    return f"{party['name']}, ИНН {party['inn']}{kpp}, {party['address']}"


def docs_text(items, corrections=False):
    result = []
    for item in items:
        value = f"№ {item['number']} от {ru_date(item['date'])}"
        if corrections and item.get("correction"):
            corr = item["correction"]
            value += f", исправление № {corr['number']} от {ru_date(corr['date'])}"
        result.append(value)
    return "; ".join(result) or "—"


def render_html(data, command):
    validate_data(data, command)
    rows, totals = calculate(data)
    taxed = data["vat"]["rate"] != "none"
    values = {key: money(value) for key, value in totals.items()}
    values.update(number=data["document"]["number"], date=ru_date(data["document"]["date"]),
                  contract_number=data["contract"]["number"], contract_date=ru_date(data["contract"]["date"]),
                  signer_name=fio_text(data["signer"]["fio"]), signer_position=data["signer"]["position"],
                  price_label="с НДС" if taxed and data["vat"]["price_mode"] == "inclusive" else "без НДС",
                  vat_label=f"НДС {data['vat']['rate']}%" if taxed else "Без НДС",
                  vat_basis="" if taxed else "Без НДС. Основание: " + data["vat"]["exemption_basis"],
                  payment_vat=f"В том числе НДС {data['vat']['rate']}% — {money(totals['tax'])} руб." if taxed else "Без НДС.",
                  payment_terms=data.get("payment_terms", ""))
    for side in ("seller", "buyer"):
        party = data[side]
        for key in ("name", "inn", "address"):
            values[side + "_" + key] = party[key]
        values[side + "_kpp"] = party.get("kpp") or "—"
        values[side + "_details"] = party_details(party)
    if command == "invoice":
        archived = data.get("invoice_status") == "paid_archive"
        values.update(invoice_title="Счёт — архивный экземпляр" if archived else "Счёт на оплату",
                      total_label="Стоимость услуг" if archived else "К оплате",
                      payment_purpose_label="Основание учтённого платежа" if archived else "Назначение платежа")
        if archived:
            payment = data["payment_received"]
            values["payment_terms"] = f"Оплачено полностью {ru_date(payment['date'])}: {money(Decimal(payment['amount']))} руб. Архивный экземпляр; повторная оплата не требуется."
        for key, value in data["seller"]["bank"].items():
            values["bank_" + key] = value
        table = []
        for i, row in enumerate(rows, 1):
            cells = [str(i), row["description"], row["quantity"], row["unit"], row["unit_price"], money(row["amount"])]
            table.append("<tr>" + "".join(f"<td>{escape(cell)}</td>" for cell in cells) + "</tr>")
        raw = {"invoice_rows": "".join(table)}
        template = "invoice.html"
    else:
        values.update(status="1" if taxed else "2", status_text="Счёт-фактура и передаточный документ" if taxed else "Передаточный документ",
                      document_title="Счёт-фактура / УПД" if taxed else "УПД", tax_print=money(totals["tax"]) if taxed else "без НДС",
                      payment_documents=docs_text(data["payment_docs"]), advance_documents=docs_text(data["advance_invoices"], True),
                      service_date=ru_date(data["document"]["service_date"]), ip_registration="")
        seller = data["seller"]
        if seller["kind"] == "ip" and seller.get("ogrnip"):
            values["ip_registration"] = f"ОГРНИП {seller['ogrnip']}, дата присвоения {ru_date(seller['ogrnip_date'])}."
        table = []
        for i, row in enumerate(rows, 1):
            cells = [str(i), row["description"], "—", row["unit_code"], row["unit"], row["quantity"],
                     format(upd_unit_price(row).normalize(), "f"), money(row["net"]), "без акциза",
                     data["vat"]["rate"] + "%" if taxed else "без НДС", money(row["tax"]) if taxed else "без НДС", money(row["gross"])] + ["—"] * 7
            table.append("<tr>" + "".join(f"<td>{escape(cell)}</td>" for cell in cells) + "</tr>")
        raw = {"upd_rows": "".join(table)}
        template = "upd.html"
    raw["demo_notice"] = '<p style="border:2px solid #900;padding:10px"><strong>ОБРАЗЕЦ. ВЫМЫШЛЕННЫЕ ДАННЫЕ. НЕ ДЛЯ ОПЛАТЫ И ПОДПИСАНИЯ.</strong></p>' if data.get("demo") else ""
    return Template((ASSETS / template).read_text(encoding="utf-8")).substitute({**{k: escape(str(v)) for k, v in values.items()}, **raw})


def sub(parent, tag, **attributes):
    return ET.SubElement(parent, tag, {k: str(v) for k, v in attributes.items()})


def add_fio(parent, fio):
    attrs = {"Фамилия": fio["surname"], "Имя": fio["name"]}
    if fio.get("patronymic"):
        attrs["Отчество"] = fio["patronymic"]
    sub(parent, "ФИО", **attrs)


def add_party(parent, tag, party):
    node = sub(parent, tag)
    ident = sub(node, "ИдСв")
    if party["kind"] == "ip":
        ip = sub(ident, "СвИП", ИННФЛ=party["inn"])
        if party.get("ogrnip"):
            ip.set("ОГРНИП", party["ogrnip"])
            ip.set("ДатаОГРНИП", ru_date(party["ogrnip_date"]))
        add_fio(ip, party["fio"])
    else:
        sub(ident, "СвЮЛУч", НаимОрг=party["name"], ИННЮЛ=party["inn"], КПП=party["kpp"])
    sub(sub(node, "Адрес"), "АдрИнф", КодСтр="643", НаимСтран="Россия", АдрТекст=party["address"])


def add_tax(parent, tag, value, taxed):
    sub(sub(parent, tag), "СумНал" if taxed else "БезНДС").text = money(value) if taxed else "без НДС"


def validate_xml(xml, filename):
    """XSD не исполняет встроенные расширения Schematron ФНС и проверки оператора."""
    from lxml import etree
    parser = etree.XMLParser(resolve_entities=False, no_network=True)
    schema = etree.XMLSchema(etree.parse(str(SCHEMA), parser))
    tree = etree.fromstring(xml, parser)
    if not schema.validate(tree):
        raise ValueError("XML не прошёл XSD ФНС: " + str(schema.error_log.last_error))
    if tree.get("ИдФайл") != Path(filename).stem:
        raise ValueError("Имя XML-файла не совпадает с ИдФайл")


def render_xml(data, *, training=False):
    if training and data.get("demo") is not True:
        raise ValueError("Учебный XML требует demo=true; не меняйте учебные реквизиты на рабочие")
    if not training and data.get("demo"):
        raise ValueError("Рабочий XML не создаётся с demo=true. Для учебной проверки используйте upd-xml-demo с отдельным новым каталогом")
    validate_data(data, "upd-xml")
    rows, totals = calculate(data)
    taxed = data["vat"]["rate"] != "none"
    generated = datetime.fromisoformat(data["document"]["generated_at"])
    edo = data["edo"]
    direction = edo_direction(data)
    identifier = f"ON_NSCHFDOPPR_{direction['receiver_id']}_{direction['sender_id']}_{generated:%Y%m%d}_{edo['document_uuid']}_0_0_0_0_0_00"
    filename = identifier + ".xml"
    root = ET.parse(ASSETS / "upd.xml").getroot()
    root.set("ИдФайл", identifier)
    doc = root.find("Документ")
    doc.attrib.update(Функция="СЧФДОП" if taxed else "ДОП", ДатаИнфПр=generated.strftime("%d.%m.%Y"),
                      ВремИнфПр=generated.strftime("%H.%M.%S"), НаимЭконСубСост=data["seller"]["name"])
    invoice = doc.find("СвСчФакт")
    invoice.attrib.update(НомерДок=data["document"]["number"], ДатаДок=ru_date(data["document"]["date"]))
    add_party(invoice, "СвПрод", data["seller"])
    for payment in data["payment_docs"]:
        sub(invoice, "СвПРД", НомерПРД=payment["number"], ДатаПРД=ru_date(payment["date"]))
    sub(invoice, "ДокПодтвОтгрНом", РеквНаимДок="Универсальный передаточный документ",
        РеквНомерДок=data["document"]["number"], РеквДатаДок=ru_date(data["document"]["date"]))
    add_party(invoice, "СвПокуп", data["buyer"])
    sub(invoice, "ДенИзм", КодОКВ="643", НаимОКВ="Российский рубль", КурсВал="1")
    if data["advance_invoices"]:
        extra = sub(invoice, "ДопСвФХЖ1")
        for advance in data["advance_invoices"]:
            node = sub(extra, "СопрДокФХЖ", РеквНаимДок="АСЧФ", РеквНомерДок=advance["number"], РеквДатаДок=ru_date(advance["date"]))
            if advance.get("correction"):
                corr = advance["correction"]
                node.set("РеквДопСведДок", f"ИспАСЧФ №{corr['number']} от {ru_date(corr['date'])}")
    table = doc.find("ТаблСчФакт")
    for i, row in enumerate(rows, 1):
        node = sub(table, "СведТов", НомСтр=i, НаимТов=row["description"], КолТов=row["quantity"],
                   ЦенаТов=format(upd_unit_price(row), "f"), СтТовБезНДС=money(row["net"]), СтТовУчНал=money(row["gross"]),
                   НалСт=data["vat"]["rate"] + "%" if taxed else "без НДС", ОКЕИ_Тов=row["unit_code"], НаимЕдИзм=row["unit"])
        sub(node, "ДопСведТов", ПрТовРаб="3")
        sub(sub(node, "Акциз"), "БезАкциз").text = "без акциза"
        add_tax(node, "СумНал", row["tax"], taxed)
    total = sub(table, "ВсегоОпл", СтТовБезНДСВсего=money(totals["net"]), СтТовУчНалВсего=money(totals["gross"]))
    add_tax(total, "СумНалВсего", totals["tax"], taxed)
    transfer = sub(doc.find("СвПродПер"), "СвПер", СодОпер="Услуги по размещению рекламы оказаны", ДатаПер=ru_date(data["document"]["service_date"]))
    sub(transfer, "ОснПер", РеквНаимДок="Договор оказания рекламных услуг", РеквНомерДок=data["contract"]["number"], РеквДатаДок=ru_date(data["contract"]["date"]))
    signer = doc.find("Подписант")
    signer.set("Должн", data["signer"]["position"])
    add_fio(signer, data["signer"]["fio"])
    ET.indent(root, space="  ")
    xml = ET.tostring(root, encoding="windows-1251", xml_declaration=True)
    validate_xml(xml, filename)
    return filename, xml


def write_new(path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("xb") as stream:
        stream.write(payload)


def write_training_bundle(data, output):
    """Учебные XML остаются demo=true и всегда сопровождаются явным манифестом."""
    filename, xml = render_xml(data, training=True)
    source = (json.dumps(data, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    manifest = {
        "never_send": True, "not_a_real_deal": True, "demo": True,
        "notice": "Учебный XML. Не отправлять, не импортировать в рабочий ЭДО, не подписывать.",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "document_generated_at": data["document"]["generated_at"],
        "document_uuid": data["edo"]["document_uuid"],
        "direction": {"sender": {"side": "seller", "id": data["seller"]["edo_id"]},
                      "receiver": {"side": "buyer", "id": data["buyer"]["edo_id"]}},
        "function": "ДОП" if data["vat"]["rate"] == "none" else "СЧФДОП",
        "xsd_valid": True, "schema_sha256": sha256(SCHEMA.read_bytes()).hexdigest(),
        "files": {filename: sha256(xml).hexdigest(), "data.json": sha256(source).hexdigest()},
    }
    # Существующий каталог нельзя превратить в учебный или затереть повторной сборкой.
    output.mkdir(parents=True, exist_ok=False)
    write_new(output / "manifest.json", (json.dumps(manifest, ensure_ascii=False, indent=2) + "\n").encode("utf-8"))
    write_new(output / "data.json", source)
    write_new(output / filename, xml)
    return output / filename


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("invoice", "upd-print", "upd-xml", "upd-xml-demo"))
    parser.add_argument("--data", required=True, type=Path, help="Файл JSON с реквизитами")
    parser.add_argument("--output", required=True, type=Path, help="Для HTML — новый файл; для XML — каталог")
    args = parser.parse_args()
    try:
        data = json.loads(args.data.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError("Корень JSON должен быть объектом")
        if args.command == "upd-xml-demo":
            output = write_training_bundle(data, args.output)
        elif args.command == "upd-xml":
            filename, payload = render_xml(data)
            output = args.output / filename
        else:
            payload = render_html(data, args.command).encode("utf-8")
            output = args.output
        if args.command != "upd-xml-demo":
            write_new(output, payload)
        print(output.resolve())
        if args.command in ("upd-xml", "upd-xml-demo"):
            print(f"ЭДО: продавец {data['seller']['edo_id']} → покупатель {data['buyer']['edo_id']}", file=sys.stderr)
        if args.command == "upd-xml-demo":
            print("УЧЕБНЫЙ XML: XSD пройдена. Не отправлять и не подписывать; см. manifest.json.", file=sys.stderr)
        elif args.command == "upd-xml":
            print("XSD ФНС: пройдено. Создан неподписанный титул продавца; импорт, контроль оператора и подписание ещё не выполнены.", file=sys.stderr)
    except (ValueError, KeyError, TypeError, InvalidOperation, OSError) as exc:
        parser.exit(2, f"Ошибка: {exc}\n")


if __name__ == "__main__":
    main()
