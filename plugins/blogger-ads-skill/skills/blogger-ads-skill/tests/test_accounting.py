"""uv run --with lxml python -m unittest discover -s tests -p 'test_accounting.py' -v"""

import copy
from decimal import Decimal, ROUND_HALF_UP
import importlib.util
import json
from hashlib import sha256
from pathlib import Path
import tempfile
import unittest
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("accounting", ROOT / "scripts" / "accounting.py")
accounting = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(accounting)


def fake_inn(prefix):
    """Искусственные номера с контрольными цифрами; не реквизиты из материалов."""
    def digit(weights, text):
        return str(sum(w * int(c) for w, c in zip(weights, text)) % 11 % 10)
    if len(prefix) == 9:
        return prefix + digit([2, 4, 10, 3, 5, 9, 4, 6, 8], prefix)
    result = prefix + digit([7, 2, 4, 10, 3, 5, 9, 4, 6, 8], prefix)
    return result + digit([3, 7, 2, 4, 10, 3, 5, 9, 4, 6, 8], result)


def fixture():
    fio = {"surname": "Образцов", "name": "Тест", "patronymic": "Примерович"}
    ogrnip_prefix = "39900000000001"
    return {
        "document": {"number": "ТЕСТ-1", "date": "2026-10-17", "service_date": "2026-10-16", "generated_at": "2026-10-17T12:00:00+03:00"},
        "contract": {"number": "ТЕСТ-Д1", "date": "2026-10-10"},
        "seller": {"kind": "ip", "name": "ИП Образцов Тест Примерович", "inn": fake_inn("9900000001"), "kpp": "", "address": "Вымышленный адрес для тестов", "fio": fio, "edo_id": "2BM-TEST-IP",
                   "ogrnip": ogrnip_prefix + str(int(ogrnip_prefix) % 13 % 10), "ogrnip_date": "2020-01-10",
                   "bank": {"name": "Тестовый банк", "bik": "040000000", "account": "40802000000000000000", "correspondent": "30101000000000000000"}},
        "buyer": {"kind": "company", "name": 'ООО «Тест & Образец»', "inn": fake_inn("990000002"), "kpp": "990001001", "address": "Вымышленный адрес заказчика", "edo_id": "2BM-TEST-COMPANY"},
        "vat": {"rate": "none", "price_mode": "inclusive", "exemption_basis": "Тестовое основание освобождения"},
        "items": [{"description": 'Размещение рекламы & согласованный пост', "quantity": "1", "unit": "шт.", "unit_code": "796", "unit_price": "50000.00"}],
        "signer": {"fio": copy.deepcopy(fio), "position": "Индивидуальный предприниматель", "authority": "self_ip"},
        "services_performed": True, "had_advance": False, "payment_docs": [], "advance_invoices": [],
        "edo": {"operator": "Тестовый оператор", "document_uuid": "11111111-2222-4333-8444-555555555555"},
    }


class AccountingTests(unittest.TestCase):
    def test_none_vat_xml_passes_official_xsd_and_has_no_kpp_for_ip(self):
        filename, xml = accounting.render_xml(fixture())
        accounting.validate_xml(xml, filename)
        root = ET.fromstring(xml)
        self.assertEqual(root.find("Документ").get("Функция"), "ДОП")
        self.assertIsNone(root.find(".//СвПрод/ИдСв/СвИП").get("КПП"))
        self.assertEqual(root.find(".//СумНалВсего/БезНДС").text, "без НДС")

    def test_three_vat_rates_and_two_price_modes(self):
        for rate in ("5", "7", "22"):
            for mode in ("inclusive", "exclusive"):
                with self.subTest(rate=rate, mode=mode):
                    data = fixture()
                    data["vat"].update(rate=rate, price_mode=mode)
                    filename, xml = accounting.render_xml(data)
                    accounting.validate_xml(xml, filename)
                    root = ET.fromstring(xml)
                    self.assertEqual(root.find("Документ").get("Функция"), "СЧФДОП")
                    self.assertEqual(root.find(".//СведТов").get("НалСт"), rate + "%")
                    _, totals = accounting.calculate(data)
                    self.assertEqual(totals["net"] + totals["tax"], totals["gross"])
                    expected = Decimal("50000.00") if mode == "inclusive" else Decimal("50000.00") * (1 + Decimal(rate)/100)
                    self.assertEqual(totals["gross"], expected)

    def test_company_seller_ip_buyer_and_advance_invoice(self):
        data = fixture()
        data["seller"], data["buyer"] = data["buyer"], data["seller"]
        data["signer"]["authority"] = "director"
        data["signer"]["position"] = "Директор"
        data["vat"]["rate"] = "22"
        data["had_advance"] = True
        data["payment_docs"] = [{"number": "99", "date": "2026-10-12"}]
        data["advance_invoices"] = [{"number": "А-1", "date": "2026-10-12", "correction": {"number": "1", "date": "2026-10-13"}}]
        filename, xml = accounting.render_xml(data)
        root = ET.fromstring(xml)
        advance = root.find(".//ДопСвФХЖ1/СопрДокФХЖ")
        self.assertEqual(advance.get("РеквНаимДок"), "АСЧФ")
        self.assertEqual(advance.get("РеквДопСведДок"), "ИспАСЧФ №1 от 13.10.2026")
        html = accounting.render_html(data, "upd-print")
        self.assertIn("А-1 от 12.10.2026", html)
        self.assertIn("(5б)", html)

    def test_decimal_rounding_is_per_line_and_totals_reconcile(self):
        data = fixture()
        data["vat"].update(rate="22", price_mode="inclusive")
        data["items"][0].update(quantity="3", unit_price="0.10")
        data["items"].append(dict(data["items"][0], quantity="2"))
        rows, totals = accounting.calculate(data)
        self.assertEqual(totals, {"net": Decimal("0.41"), "tax": Decimal("0.09"), "gross": Decimal("0.50")})
        self.assertEqual(rows[0]["tax"], Decimal("0.05"))

    def test_advance_invoice_correction_before_or_on_upd_date_is_preserved(self):
        data = fixture()
        data["vat"]["rate"] = "22"
        data["had_advance"] = True
        data["payment_docs"] = [{"number": "99", "date": "2026-10-12"}]
        for correction_date, rendered_date in (("2026-10-16", "16.10.2026"), ("2026-10-17", "17.10.2026")):
            with self.subTest(correction_date=correction_date):
                data["advance_invoices"] = [{"number": "А-1", "date": "2026-10-12", "correction": {"number": "1", "date": correction_date}}]
                _, xml = accounting.render_xml(data)
                advance = ET.fromstring(xml).find(".//ДопСвФХЖ1/СопрДокФХЖ")
                self.assertEqual(advance.get("РеквДопСведДок"), f"ИспАСЧФ №1 от {rendered_date}")
                self.assertIn(f"исправление № 1 от {rendered_date}", accounting.render_html(data, "upd-print"))

    def test_future_advance_invoice_correction_rejects_upd_without_output(self):
        data = fixture()
        data["vat"]["rate"] = "22"
        data["had_advance"] = True
        data["payment_docs"] = [{"number": "99", "date": "2026-10-12"}]
        data["advance_invoices"] = [{"number": "А-1", "date": "2026-10-12", "correction": {"number": "1", "date": "2026-10-18"}}]
        message = r"advance_invoices\.correction: дата позже УПД"
        with self.assertRaisesRegex(ValueError, message):
            accounting.render_xml(data)
        with self.assertRaisesRegex(ValueError, message):
            accounting.render_html(data, "upd-print")
        data["demo"] = True
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "training"
            with self.assertRaisesRegex(ValueError, message):
                accounting.write_training_bundle(data, output)
            self.assertFalse(output.exists())

    def test_invoice_unit_price_preserves_precision_and_reconciles_line_amount(self):
        from lxml import etree
        cases = (
            ("3", "0.333", "1.00"),
            ("2.5", "12.34567", "30.86"),
            ("1000000000", "0.00000000001", "0.01"),
            ("1", "50000.00", "50000.00"),
            ("1", "0.335", "0.34"),
        )
        for rate in ("none", "5", "7", "22"):
            for mode in ("inclusive", "exclusive"):
                for quantity, price, amount in cases:
                    with self.subTest(rate=rate, mode=mode, quantity=quantity, price=price):
                        data = fixture()
                        data["vat"].update(rate=rate, price_mode=mode)
                        data["items"][0].update(quantity=quantity, unit_price=price)
                        root = etree.HTML(accounting.render_html(data, "invoice"))
                        cells = root.findall(".//tbody/tr/td")
                        self.assertEqual(cells[2].text, quantity)
                        self.assertEqual(cells[4].text, price)
                        self.assertEqual(cells[5].text, amount)
                        shown_amount = (Decimal(cells[2].text) * Decimal(cells[4].text)).quantize(
                            Decimal("0.01"), rounding=ROUND_HALF_UP)
                        self.assertEqual(shown_amount, Decimal(cells[5].text))

    def test_html_xml_escape_user_data(self):
        data = fixture()
        data["items"][0]["description"] = 'Креатив & "кавычки" > ссылка'
        html = accounting.render_html(data, "invoice")
        self.assertIn("Креатив &amp; &quot;кавычки&quot; &gt; ссылка", html)
        filename, xml = accounting.render_xml(data)
        self.assertEqual(ET.fromstring(xml).find(".//СведТов").get("НаимТов"), data["items"][0]["description"])

    def test_upd_unit_price_reconciles_rounded_net_in_print_and_xml(self):
        from lxml import etree
        for rate, expected_net in (("5", "0.03"), ("7", "0.03"), ("22", "0.02")):
            with self.subTest(rate=rate):
                data = fixture()
                data["vat"].update(rate=rate, price_mode="inclusive")
                data["items"][0].update(quantity="2", unit_price="0.013")
                html = etree.HTML(accounting.render_html(data, "upd-print"))
                cells = html.findall(".//tbody/tr/td")
                filename, xml = accounting.render_xml(data)
                accounting.validate_xml(xml, filename)
                item = ET.fromstring(xml).find(".//СведТов")
                self.assertEqual(cells[7].text, expected_net)
                self.assertEqual(item.get("СтТовБезНДС"), expected_net)
                self.assertEqual(cells[11].text, "0.03")
                self.assertEqual(item.get("СтТовУчНал"), "0.03")
                self.assertEqual(Decimal(cells[6].text), Decimal(item.get("ЦенаТов")))
                shown_amount = (Decimal(cells[5].text) * Decimal(cells[6].text)).quantize(
                    Decimal("0.01"), rounding=ROUND_HALF_UP)
                self.assertEqual(shown_amount, Decimal(expected_net))

    def test_upd_rejects_unrepresentable_unit_price_without_blocking_invoice(self):
        from lxml import etree
        data = fixture()
        data["vat"].update(rate="22", price_mode="inclusive")
        data["items"][0].update(quantity="999999999999", unit_price="0.01")
        for command in ("upd-print", "upd-xml"):
            with self.subTest(command=command), self.assertRaisesRegex(ValueError, "11 знаков"):
                if command == "upd-xml":
                    accounting.render_xml(data)
                else:
                    accounting.render_html(data, command)
        html = etree.HTML(accounting.render_html(data, "invoice"))
        cells = html.findall(".//tbody/tr/td")
        self.assertEqual(cells[4].text, "0.01")
        self.assertEqual(cells[5].text, "9999999999.99")

    def test_missing_data_never_produces_plausible_xml(self):
        cases = [
            ("services_performed", False),
            ("had_advance", None),
        ]
        for key, value in cases:
            with self.subTest(key=key):
                data = fixture()
                data[key] = value
                with self.assertRaises(ValueError):
                    accounting.render_xml(data)
        data = fixture()
        data["vat"]["rate"] = "22"
        data["had_advance"] = True
        data["payment_docs"] = [{"number": "1", "date": "2026-10-12"}]
        with self.assertRaisesRegex(ValueError, "advance_invoices"):
            accounting.render_xml(data)

    def test_rejects_mcd_unknown_tax_invalid_inn_float_and_future_upd(self):
        mutations = [
            lambda d: d["signer"].update(authority="mcd"),
            lambda d: d["vat"].update(rate="20"),
            lambda d: d["seller"].update(inn="123456789012"),
            lambda d: d["items"][0].update(unit_price=50000.00),
            lambda d: d["document"].update(service_date="2026-10-20"),
            lambda d: d["edo"].update(sender_id="000-0"),
            lambda d: d["document"].update(generated_at="2026-10-17T12:00:00"),
        ]
        for mutation in mutations:
            data = fixture()
            mutation(data)
            with self.assertRaises(ValueError):
                accounting.render_xml(data)

    def test_xsd_rejects_missing_signer_and_filename_mismatch(self):
        filename, xml = accounting.render_xml(fixture())
        root = ET.fromstring(xml)
        doc = root.find("Документ")
        doc.remove(doc.find("Подписант"))
        with self.assertRaisesRegex(ValueError, "XSD"):
            accounting.validate_xml(ET.tostring(root, encoding="utf-8"), filename)
        with self.assertRaisesRegex(ValueError, "ИдФайл"):
            accounting.validate_xml(xml, "another.xml")

    def test_existing_file_is_preserved(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "invoice.html"
            path.write_text("Ручная правка", encoding="utf-8")
            with self.assertRaises(FileExistsError):
                accounting.write_new(path, b"replacement")
            self.assertEqual(path.read_text(encoding="utf-8"), "Ручная правка")

    def test_demo_cannot_be_sent_as_xml(self):
        data = fixture()
        data["demo"] = True
        self.assertIn("НЕ ДЛЯ ОПЛАТЫ", accounting.render_html(data, "invoice"))
        with self.assertRaisesRegex(ValueError, "demo"):
            accounting.render_xml(data)

    def test_printed_table_columns_align(self):
        from lxml import etree
        data = fixture()
        data["items"].append(dict(data["items"][0], unit_price="40000.00"))
        root = etree.HTML(accounting.render_html(data, "upd-print"))
        columns = len(root.findall(".//thead/tr/th"))
        self.assertEqual(columns, len(root.findall(".//colgroup/col")))
        for row in root.findall(".//tbody/tr") + root.findall(".//tfoot/tr"):
            self.assertEqual(sum(int(cell.get("colspan", "1")) for cell in row), columns)

    def test_edo_direction_follows_parties_and_rejects_ambiguous_legacy(self):
        data = fixture()
        filename, _ = accounting.render_xml(data)
        self.assertTrue(filename.startswith("ON_NSCHFDOPPR_2BM-TEST-COMPANY_2BM-TEST-IP_"))
        data["edo"].update(sender_id="2BM-TEST-COMPANY", receiver_id="2BM-TEST-IP")
        with self.assertRaisesRegex(ValueError, "противоречит"):
            accounting.render_xml(data)
        data["edo"].update(sender_id="2BM-TEST-IP", receiver_id="2BM-TEST-COMPANY")
        self.assertEqual(accounting.render_xml(data)[0], filename)
        del data["seller"]["edo_id"]
        with self.assertRaisesRegex(ValueError, "перенести"):
            accounting.render_xml(data)

    def test_party_swap_changes_outgoing_edo_direction(self):
        data = fixture()
        data["seller"], data["buyer"] = data["buyer"], data["seller"]
        data["signer"]["authority"] = "director"
        filename, xml = accounting.render_xml(data)
        self.assertTrue(filename.startswith("ON_NSCHFDOPPR_2BM-TEST-IP_2BM-TEST-COMPANY_"))
        self.assertEqual(ET.fromstring(xml).find(".//СвПрод/ИдСв/СвЮЛУч").get("ИННЮЛ"), data["seller"]["inn"])

    def test_training_bundle_keeps_demo_and_real_command_guard(self):
        from lxml import etree
        data = fixture()
        data["demo"] = True
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "training"
            path = accounting.write_training_bundle(data, output)
            saved = json.loads((output / "data.json").read_text())
            manifest = json.loads((output / "manifest.json").read_text())
            self.assertIs(saved["demo"], True)
            self.assertTrue(manifest["never_send"] and manifest["not_a_real_deal"])
            self.assertEqual(manifest["direction"]["sender"]["id"], data["seller"]["edo_id"])
            self.assertEqual(manifest["files"][path.name], sha256(path.read_bytes()).hexdigest())
            etree.XMLSchema(etree.parse(str(accounting.SCHEMA))).assertValid(etree.parse(str(path)))
            self.assertEqual(accounting.render_xml(data, training=True)[1], path.read_bytes())
            with self.assertRaises(FileExistsError):
                accounting.write_training_bundle(data, output)
            with self.assertRaisesRegex(ValueError, "demo=true"):
                accounting.render_xml(saved)
            with self.assertRaisesRegex(ValueError, "demo=true"):
                accounting.write_training_bundle(fixture(), Path(temporary) / "not-training")
            self.assertFalse((Path(temporary) / "not-training").exists())

    def test_paid_archive_requires_confirmed_full_payment_and_has_no_demand(self):
        data = fixture()
        data["invoice_status"] = "paid_archive"
        data["payment_terms"] = "Оплатить до 16 октября"
        with self.assertRaises(ValueError):
            accounting.render_html(data, "invoice")
        data["payment_received"] = {"amount": "49999.99", "date": "2026-10-14"}
        with self.assertRaisesRegex(ValueError, "полной суммой"):
            accounting.render_html(data, "invoice")
        data["payment_received"]["amount"] = "50000.00"
        html = accounting.render_html(data, "invoice")
        self.assertIn("повторная оплата не требуется", html)
        self.assertNotIn("К оплате:", html)
        self.assertNotIn("Оплатить до", html)
        self.assertNotIn("__________________", html)
        self.assertNotIn("<img", html)
        self.assertIn(data["signer"]["fio"]["surname"], html)
        self.assertIn(data["buyer"]["kpp"], html)

    def test_archive_preserves_original_invoice_date_before_payment(self):
        data = fixture()
        data["document"].update(number="СЧ-14", date="2026-10-14")
        data["invoice_status"] = "paid_archive"
        data["payment_received"] = {"amount": "50000.00", "date": "2026-10-16"}
        html = accounting.render_html(data, "invoice")
        self.assertIn("№ СЧ-14 от 14.10.2026</h1>", html)
        self.assertIn("Оплачено полностью 16.10.2026", html)
        self.assertEqual(data["document"]["date"], "2026-10-14")
        data["payment_received"]["date"] = "16.10.2026"
        with self.assertRaisesRegex(ValueError, "Дата должна"):
            accounting.render_html(data, "invoice")


if __name__ == "__main__":
    unittest.main()
