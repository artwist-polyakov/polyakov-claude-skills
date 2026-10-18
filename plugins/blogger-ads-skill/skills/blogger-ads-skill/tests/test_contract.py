"""Проверки сохранения договорного OOXML и защиты от незаполненных/чужих данных."""
import importlib.util
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
from xml.etree import ElementTree as ET
from zipfile import ZipFile

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("render_contract", ROOT / "scripts/render_contract.py")
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"


class ContractTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.output = Path(self.temp.name) / "output.docx"
        self.schema = json.loads((ROOT / "assets/template-fields.json").read_text())
        self.fields = {key: value["example"] for key, value in self.schema["fields"].items() if "<N>" not in key}
        self.fields.update(LINK_1_ANCHOR="Пример & проверка", LINK_1_URL="https://example.org/?a=1&b=2")

    def render(self, name="contract"):
        return module.render(ROOT / "assets/templates" / (name + ".docx"), self.fields, self.output)

    def synthetic(self, xml):
        path = Path(self.temp.name) / "synthetic.docx"
        with ZipFile(path, "w") as package:
            package.writestr("word/document.xml", '<w:document xmlns:w="' + module.W + '"><w:body>' + xml + '</w:body></w:document>')
            package.writestr("word/styles.xml", b"unchanged opaque part")
        return path

    def test_all_templates_match_map_and_are_fully_filled(self):
        for name, expected in self.schema["templates"].items():
            source = ROOT / "assets/templates" / (name + ".docx")
            self.assertEqual(module.list_fields(source), expected)
            out = self.output.with_name(name + ".docx")
            module.render(source, self.fields, out)
            with ZipFile(out) as z:
                xml = z.read("word/document.xml")
                self.assertNotIn(b"{{", xml)
                self.assertNotIn(b"}}", xml)
                ET.fromstring(xml)

    def test_runs_and_sections_and_other_parts_are_preserved(self):
        self.render()
        with ZipFile(ROOT / "assets/templates/contract.docx") as src, ZipFile(self.output) as dst:
            for name in src.namelist():
                if name != "word/document.xml":
                    self.assertEqual(src.read(name), dst.read(name), name)
            a, b = [ET.fromstring(z.read("word/document.xml")) for z in (src, dst)]
            for tag in ("rPr", "pPr", "sectPr"):
                self.assertEqual([ET.tostring(e) for e in a.iter(W + tag)],
                                 [ET.tostring(e) for e in b.iter(W + tag)])
            self.assertEqual([e.get(W + "num", "1") for e in b.iter(W + "cols")], ["1", "2"])
            self.assertIn("Montserrat", {e.get(W + "ascii") for e in b.iter(W + "rFonts")})

    def test_unknown_link_deadline_keeps_publication_and_customer_report_action(self):
        self.assertNotIn("NOTICE_HOURS", self.schema["fields"])
        self.assertEqual(self.fields["LINK_AFTER_PUBLICATION_HOURS"], "____")
        self.render()
        clause = next(p["text"] for p in module.inspect_document(self.output)["paragraphs"]
                      if p["text"].startswith("3.1."))
        self.assertIn("через ____ ч после публикации", clause)
        self.assertIn("Исполнитель направляет Заказчику ссылку на опубликованный пост и его снимок экрана", clause)

    def test_link_deadline_accepts_zero_and_fractional_hours_without_rewriting_value(self):
        for index, value in enumerate(("0", "1", "0.5", "0,5", "12.125")):
            with self.subTest(value=value):
                output = self.output.with_name(f"hours-{index}.docx")
                fields = dict(self.fields, LINK_AFTER_PUBLICATION_HOURS=value)
                module.render(ROOT / "assets/templates/contract.docx", fields, output)
                clause = next(p["text"] for p in module.inspect_document(output)["paragraphs"]
                              if p["text"].startswith("3.1."))
                self.assertIn(f"через {value} ч после публикации", clause)
                self.assertEqual(fields["LINK_AFTER_PUBLICATION_HOURS"], value)

    def test_link_deadline_rejects_text_negative_and_nonfinite_values_before_writing(self):
        for index, value in enumerate(("-1", "-0.5", "NaN", "Infinity", "1 час", "после подписания",
                                       "[УТОЧНИТЬ СОБЫТИЕ]", "", "__", "1\n2")):
            with self.subTest(value=value):
                output = self.output.with_name(f"bad-hours-{index}.docx")
                fields = dict(self.fields, LINK_AFTER_PUBLICATION_HOURS=value)
                before = dict(fields)
                with self.assertRaisesRegex(ValueError, "LINK_AFTER_PUBLICATION_HOURS.*неотрицательное число"):
                    module.render(ROOT / "assets/templates/contract.docx", fields, output)
                self.assertFalse(output.exists())
                self.assertFalse(output.with_suffix(".docx.fields.json").exists())
                self.assertEqual(fields, before)

    def test_legacy_notice_field_names_replacement_and_does_not_silently_win(self):
        for include_new in (False, True):
            with self.subTest(include_new=include_new):
                fields = dict(self.fields, NOTICE_HOURS="1")
                if not include_new:
                    fields.pop("LINK_AFTER_PUBLICATION_HOURS")
                with self.assertRaisesRegex(ValueError, "NOTICE_HOURS.*LINK_AFTER_PUBLICATION_HOURS"):
                    module.render(ROOT / "assets/templates/contract.docx", fields, self.output)
                self.assertFalse(self.output.exists())
        self.render()
        sidecar = self.output.with_suffix(".docx.fields.json")
        saved = json.loads(sidecar.read_text())
        saved["fields"]["NOTICE_HOURS"] = saved["fields"].pop("LINK_AFTER_PUBLICATION_HOURS")
        sidecar.write_text(json.dumps(saved), encoding="utf-8")
        revised = self.output.with_name("legacy-revision.docx")
        with self.assertRaisesRegex(ValueError, "NOTICE_HOURS.*LINK_AFTER_PUBLICATION_HOURS"):
            module.revise_document(self.output, {"LINK_AFTER_PUBLICATION_HOURS": "1"}, revised)
        self.assertFalse(revised.exists())

    def test_revise_cli_transfers_confirmed_link_deadline_to_separate_version(self):
        self.render()
        original = self.output.read_bytes()
        sidecar = self.output.with_suffix(".docx.fields.json")
        original_fields = sidecar.read_bytes()
        changes = self.output.with_name("hours.json")
        changes.write_text(json.dumps({"LINK_AFTER_PUBLICATION_HOURS": "0,5"}), encoding="utf-8")
        revised = self.output.with_name("confirmed-hours.docx")
        result = subprocess.run([sys.executable, "-B", str(ROOT / "scripts/render_contract.py"),
                                 "--revise", str(self.output), "--fields", str(changes), "--output", str(revised)],
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        clause = next(p["text"] for p in module.inspect_document(revised)["paragraphs"]
                      if p["text"].startswith("3.1."))
        self.assertIn("через 0,5 ч после публикации", clause)
        self.assertEqual(json.loads(revised.with_suffix(".docx.fields.json").read_text())["fields"]["LINK_AFTER_PUBLICATION_HOURS"], "0,5")
        self.assertEqual(self.output.read_bytes(), original)
        self.assertEqual(sidecar.read_bytes(), original_fields)
        self.assertTrue(module.audit_document(revised)["ok"])

    def test_link_deadline_numeric_rule_does_not_restrict_full_paragraph_patch(self):
        self.render()
        original = self.output.read_bytes()
        clause = next(p["text"] for p in module.inspect_document(self.output)["paragraphs"]
                      if p["text"].startswith("3.1."))
        text = "3.1. Исполнитель направляет Заказчику ссылку и снимок по отдельно согласованному письменному запросу."
        revised = self.output.with_name("custom-clause.docx")
        module.patch_document(self.output, self.patch_data(self.output, [{"find": clause, "replace": text}]), revised)
        self.assertIn(text, [p["text"] for p in module.inspect_document(revised)["paragraphs"]])
        self.assertEqual(self.output.read_bytes(), original)
        self.assertTrue(module.audit_document(revised)["ok"])

    def test_claims_are_addressed_to_contractor_with_its_own_contact(self):
        self.fields.update(CONTRACTOR_CLAIMS_CONTACT="claims@contractor.example",
                           CUSTOMER_CONTACT="reports@customer.example")
        self.render()
        claims = next(p["text"] for p in module.inspect_document(self.output)["paragraphs"]
                      if p["text"].startswith("3.3."))
        self.assertIn("Заказчик направляет Исполнителю мотивированные претензии", claims)
        self.assertIn("Контакт Исполнителя: claims@contractor.example", claims)
        self.assertNotIn("reports@customer.example", claims)

    def test_unknown_claims_contact_never_uses_customer_contact_or_changes_input(self):
        for value in (None, "", "  "):
            with self.subTest(value=value):
                fields = dict(self.fields, CUSTOMER_CONTACT="reports@customer.example")
                if value is None:
                    fields.pop("CONTRACTOR_CLAIMS_CONTACT")
                else:
                    fields["CONTRACTOR_CLAIMS_CONTACT"] = value
                original = dict(fields)
                output = self.output.with_name(f"unknown-{len(str(value))}.docx")
                module.render(ROOT / "assets/templates/contract.docx", fields, output)
                claims = next(p["text"] for p in module.inspect_document(output)["paragraphs"]
                              if p["text"].startswith("3.3."))
                self.assertIn(module.UNKNOWN_CLAIMS_CONTACT, claims)
                self.assertNotIn("reports@customer.example", claims)
                self.assertEqual(fields, original)
                saved = json.loads(output.with_suffix(".docx.fields.json").read_text())
                self.assertEqual(saved["fields"]["CONTRACTOR_CLAIMS_CONTACT"], module.UNKNOWN_CLAIMS_CONTACT)

    def test_claims_keep_different_deadlines_and_start_events_verbatim(self):
        deadlines = ("в течение 24 часов после окончания размещения",
                     "в течение трёх рабочих дней после получения отчёта",
                     "[УТОЧНИТЬ СРОК И НАЧАЛО ОТСЧЁТА]")
        for index, deadline in enumerate(deadlines):
            fields = dict(self.fields, CLAIMS_DEADLINE=deadline)
            output = self.output.with_name(f"deadline-{index}.docx")
            module.render(ROOT / "assets/templates/contract.docx", fields, output)
            claims = next(p["text"] for p in module.inspect_document(output)["paragraphs"]
                          if p["text"].startswith("3.3."))
            self.assertIn(deadline, claims)
            self.assertIn("Заказчик направляет Исполнителю", claims)
            if index:
                self.assertNotIn("24 часов", claims)

    def test_legacy_claims_clause_is_rejected_for_own_template_without_overwrite(self):
        self.render()
        sidecar = self.output.with_suffix(".docx.fields.json")
        before = self.output.read_bytes(), sidecar.read_bytes()
        legacy = dict(self.fields, CLAIMS_CLAUSE="Претензия направляется на контакт Заказчика.")
        with self.assertRaisesRegex(ValueError, "CLAIMS_CLAUSE.*CLAIMS_DEADLINE"):
            module.render(ROOT / "assets/templates/contract.docx", legacy, self.output, force=True)
        self.assertEqual((self.output.read_bytes(), sidecar.read_bytes()), before)
        # Собственная карта ролей не переписывает пользовательский шаблон или его согласованные условия.
        custom = self.synthetic('<w:p><w:r><w:t>{{CLAIMS_CLAUSE}}</w:t></w:r></w:p>')
        target = self.output.with_name("custom.docx")
        module.render(custom, {"CLAIMS_CLAUSE": "Дословное условие полученного договора."}, target)
        self.assertEqual(module.inspect_document(target)["paragraphs"][0]["text"],
                         "Дословное условие полученного договора.")

    def test_split_braces_and_runs_preserve_text_styles_and_newlines(self):
        source = self.synthetic('<w:p><w:r><w:rPr><w:b/></w:rPr><w:t>Начало {</w:t></w:r><w:r><w:t>{NAME}</w:t></w:r><w:r><w:t>} конец {{NAME}}</w:t></w:r></w:p>')
        self.assertEqual(module.list_fields(source), ["NAME"])
        module.render(source, {"NAME": "А & Б\nВ < Г"}, self.output)
        with ZipFile(self.output) as z:
            root = ET.fromstring(z.read("word/document.xml"))
            self.assertEqual("".join(root.itertext()), "Начало А & БВ < Г конец А & БВ < Г")
            self.assertEqual(len(list(root.iter(W + "br"))), 2)
            self.assertEqual(len(list(root.iter(W + "b"))), 1)

    def test_multiple_multiline_tokens_in_one_run(self):
        source = self.synthetic('<w:p><w:r><w:t>До {{A}} между {{B}} после {{C}}.</w:t></w:r></w:p>')
        module.render(source, {"A": "А1\nА2", "B": "Б1\nБ2\nБ3", "C": "В1\nВ2"}, self.output)
        with ZipFile(self.output) as z:
            root = ET.fromstring(z.read("word/document.xml"))
            run = next(root.iter(W + "r"))
            value = "".join("\n" if child.tag == W + "br" else (child.text or "") for child in run)
            self.assertEqual(value, "До А1\nА2 между Б1\nБ2\nБ3 после В1\nВ2.")

    def test_duplicate_period_at_template_boundary_rejects_named_field_without_output(self):
        for template, key in [("creative", "MARKING_PARTY"), ("creative", "ERID_PLACEMENT"),
                              ("placement-terms", "PLACEMENT_FORMAT"), ("contract", "PRICE_TOTAL")]:
            with self.subTest(template=template, field=key):
                fields = dict(self.fields, **{key: self.fields[key] + ". "})
                original = dict(fields)
                with self.assertRaisesRegex(ValueError, key + ".*дублирует знак"):
                    module.render(ROOT / "assets/templates" / (template + ".docx"), fields, self.output)
                self.assertEqual(fields, original)
                self.assertFalse(self.output.exists())
                self.assertFalse(self.output.with_suffix(".docx.fields.json").exists())

    def test_boundary_check_handles_split_runs_and_only_adjacent_literal_punctuation(self):
        source = self.synthetic('<w:p><w:r><w:t>Кто: {</w:t></w:r><w:r><w:t>{NAME}</w:t></w:r><w:r><w:t>}.</w:t></w:r></w:p>')
        for value in ("Заказчик.", ": Заказчик"):
            with self.subTest(value=value):
                with self.assertRaisesRegex(ValueError, "NAME.*дублирует знак"):
                    module.render(source, {"NAME": value}, self.output)
                self.assertFalse(self.output.exists())
        # Точка в другом предложении не является соседним знаком шаблона.
        source = self.synthetic('<w:p><w:r><w:t>{{NAME}} Затем другое предложение.</w:t></w:r></w:p>')
        module.render(source, {"NAME": "ООО «Пример», г. Москва."}, self.output)
        self.assertIn("ООО «Пример», г. Москва. Затем", module.inspect_document(self.output)["paragraphs"][0]["text"])

    def test_boundary_rejection_with_force_keeps_existing_document_and_field_map(self):
        self.render()
        sidecar = self.output.with_suffix(".docx.fields.json")
        before = self.output.read_bytes(), sidecar.read_bytes()
        fields = dict(self.fields, PRICE_TOTAL=self.fields["PRICE_TOTAL"] + ".")
        with self.assertRaisesRegex(ValueError, "PRICE_TOTAL.*дублирует знак"):
            module.render(ROOT / "assets/templates/contract.docx", fields, self.output, force=True)
        self.assertEqual((self.output.read_bytes(), sidecar.read_bytes()), before)

    def test_media_label_rejects_known_duplicates_but_keeps_filenames(self):
        source = self.synthetic('<w:p><w:r><w:t>Медиа: {{MEDIA_MANIFEST}}</w:t></w:r></w:p>')
        for value in ("Медиа отсутствуют.", "Медиа отсутствуют", "Медиа отсутствует.", "Медиа: banner.jpg", "Медиа нет, только текст.", "Медиа отсутствуют; ссылки перечислены ниже"):
            with self.subTest(value=value):
                with self.assertRaisesRegex(ValueError, "MEDIA_MANIFEST.*повторяет метку"):
                    module.render(source, {"MEDIA_MANIFEST": value}, self.output)
                self.assertFalse(self.output.exists())
        value = "Медиа 01.mp4; Медиа.jpg — исходные файлы"
        module.render(source, {"MEDIA_MANIFEST": value}, self.output)
        self.assertEqual(module.inspect_document(self.output)["paragraphs"][0]["text"], "Медиа: " + value)

    def test_party_intro_rejects_terminal_punctuation_before_template_comma(self):
        for key in ("CUSTOMER_PARTY", "CONTRACTOR_PARTY"):
            with self.subTest(key=key):
                source = self.synthetic('<w:p><w:r><w:t>{{' + key + '}}, с одной стороны</w:t></w:r></w:p>')
                value = "ИП Примеров И. И., именуемый «Исполнитель»."
                with self.assertRaisesRegex(ValueError, key + ".*перед запятой"):
                    module.render(source, {key: value}, self.output)
                self.assertFalse(self.output.exists())
        source = self.synthetic('<w:p><w:r><w:t>{{CUSTOMER_PARTY}}, с другой стороны</w:t></w:r></w:p>')
        value = "ИП Примеров И. И., именуемый «Заказчик»"
        module.render(source, {"CUSTOMER_PARTY": value}, self.output)
        self.assertEqual(module.inspect_document(self.output)["paragraphs"][0]["text"], value + ", с другой стороны")

    def test_post_url_and_abbreviation_punctuation_is_preserved_exactly(self):
        source = self.synthetic('<w:p><w:r><w:t>Пост: {{POST_TEXT}}.</w:t></w:r></w:p>'
                                '<w:p><w:r><w:t>URL: {{PRODUCT_URL}}.</w:t></w:r></w:p>'
                                '<w:p><w:r><w:t>{{PARTY_INFO}}</w:t></w:r></w:p>')
        post = Path(self.temp.name) / "approved.txt"
        text = ": Дословно:: пауза.. г. Москва; И. И. Иванов.\nhttps://example.org./path.v2?next=a..b&end=."
        post.write_text(text, encoding="utf-8")
        fields = {"PRODUCT_URL": "https://example.org./path.v2?next=a..b&end=.",
                  "PARTY_INFO": "г. Москва, ул. Примерная, д. 1; Иванов И. И."}
        module.render(source, fields, self.output, post_file=post)
        paragraphs = [p["text"] for p in module.inspect_document(self.output)["paragraphs"]]
        self.assertEqual(paragraphs, ["Пост: " + text + ".", "URL: " + fields["PRODUCT_URL"] + ".", fields["PARTY_INFO"]])
        self.assertEqual(post.read_text(encoding="utf-8"), text)
        saved = json.loads(self.output.with_suffix(".docx.fields.json").read_text())
        self.assertEqual(saved["fields"], fields | {"POST_TEXT": text})

    def test_existing_manual_text_is_not_rechecked_or_normalized_by_patch(self):
        self.render()
        target = self.output.with_name("manual-revision.docx")
        text = "Ручная оговорка:: дословно.. г. Москва, ул. Примерная."
        module.patch_document(self.output, self.patch_data(self.output, [{"find": self.fields["PAYMENT_CLAUSE"], "replace": text}]), target)
        self.assertIn(text, "\n".join(p["text"] for p in module.inspect_document(target)["paragraphs"]))

    def test_links_expand_without_losing_addresses(self):
        self.fields.update(LINK_COUNT="3", LINK_2_ANCHOR="Второй", LINK_2_URL="https://example.org/2", LINK_3_ANCHOR="Третий", LINK_3_URL="https://example.org/3")
        self.render("creative")
        with ZipFile(self.output) as z:
            root = ET.fromstring(z.read("word/document.xml"))
            self.assertEqual(len(list(root.iter(W + "tr"))), 4)
            self.assertIn("https://example.org/?a=1&b=2", "".join(root.itertext()))

    def test_zero_links_removes_data_row(self):
        self.fields["LINK_COUNT"] = "0"
        self.render("creative")
        with ZipFile(self.output) as z:
            self.assertEqual(len(list(ET.fromstring(z.read("word/document.xml")).iter(W + "tr"))), 1)

    def test_pending_erid_does_not_turn_responsible_party_into_registration_evidence(self):
        self.fields.update(
            ERID="Ожидается от Заказчика; выдача не подтверждена",
            MARKING_PARTY="Заказчик",
        )
        self.render("creative")
        with ZipFile(self.output) as package:
            root = ET.fromstring(package.read("word/document.xml"))
            text = "".join(root.itertext())
        self.assertIn(self.fields["ERID"], text)
        self.assertIn("Ответственный за регистрацию и предоставление подтверждения выдачи: Заказчик", text)
        self.assertNotRegex(text, r"Регистрацию выполнил|подтверждение выдачи предоставил|регистрация (?:выполнена|подтверждена)")

    def test_post_file_preserves_erid_spaces_and_newlines(self):
        post = Path(self.temp.name) / "approved.txt"
        text = "✅ Одобренный текст\n#реклама \nerid: exampleToken123\n"
        post.write_bytes(text.encode("utf-8"))
        fields = dict(self.fields)
        fields.pop("POST_TEXT")
        module.render(ROOT / "assets/templates/creative.docx", fields, self.output, post_file=post)
        saved = json.loads(self.output.with_suffix(".docx.fields.json").read_text())
        self.assertEqual(saved["fields"]["POST_TEXT"], text)
        self.assertEqual(saved["post_source"]["sha256"], hashlib.sha256(post.read_bytes()).hexdigest())
        parts, documents = module.read_documents(self.output)
        self.assertIn(text, [module.paragraph_text(p) for p in module.nodes(documents["word/document.xml"], "p")])

    def test_post_file_rejects_sanitized_approved_text(self):
        post = Path(self.temp.name) / "approved.txt"
        post.write_text("Пост\n#реклама \nerid: exampleToken123", encoding="utf-8")
        fields = dict(self.fields, POST_TEXT="Пост\n#реклама")
        with self.assertRaisesRegex(ValueError, "не совпадает"):
            module.render(ROOT / "assets/templates/creative.docx", fields, self.output, post_file=post)
        self.assertFalse(self.output.exists())

    def test_post_file_crlf_has_one_word_break_and_keeps_raw_source(self):
        post = Path(self.temp.name) / "approved.txt"
        raw = b"First\r\nSecond\r\nerid: exampleToken123"
        post.write_bytes(raw)
        fields = dict(self.fields, POST_TEXT=raw.decode("utf-8"))
        module.render(ROOT / "assets/templates/creative.docx", fields, self.output, post_file=post)
        _, documents = module.read_documents(self.output)
        text = raw.decode("utf-8").replace("\r\n", "\n")
        self.assertIn(text, [module.paragraph_text(p) for p in module.nodes(documents["word/document.xml"], "p")])
        saved = json.loads(self.output.with_suffix(".docx.fields.json").read_text())
        self.assertEqual(saved["fields"]["POST_TEXT"], raw.decode("utf-8"))
        self.assertEqual(post.read_bytes(), raw)

    def test_revise_preserves_post_source_until_explicit_new_version(self):
        post = Path(self.temp.name) / "approved.txt"
        text = "Пост\nerid: exampleToken123"
        post.write_text(text, encoding="utf-8")
        fields = dict(self.fields, POST_TEXT=text)
        module.render(ROOT / "assets/templates/creative.docx", fields, self.output, post_file=post)
        revision = self.output.with_name("revision.docx")
        with self.assertRaisesRegex(ValueError, "не совпадает"):
            module.revise_document(self.output, {"POST_TEXT": "Пост"}, revision)
        self.assertFalse(revision.exists())
        post.write_text("Новая версия", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "Исходный текст поста изменился"):
            module.revise_document(self.output, {}, revision)
        self.assertFalse(revision.exists())
        new_post = Path(self.temp.name) / "new-post.txt"
        new_post.write_text("Новая версия", encoding="utf-8")
        module.revise_document(self.output, {"POST_TEXT": "Новая версия"}, revision, post_file=new_post)
        saved = json.loads(revision.with_suffix(".docx.fields.json").read_text())
        self.assertEqual(saved["post_source"]["path"], str(new_post.resolve()))

    def test_missing_or_empty_field_creates_no_document(self):
        for value in (None, "   "):
            fields = dict(self.fields)
            if value is None:
                del fields["VAT_CLAUSE"]
            else:
                fields["VAT_CLAUSE"] = value
            with self.assertRaisesRegex(ValueError, "VAT_CLAUSE"):
                module.render(ROOT / "assets/templates/contract.docx", fields, self.output)
            self.assertFalse(self.output.exists())

    def test_unknown_template_marker_is_not_silently_kept(self):
        source = self.synthetic('<w:p><w:r><w:t>{{lower_case}}</w:t></w:r></w:p>')
        with self.assertRaisesRegex(ValueError, "маркеры"):
            module.render(source, {}, self.output)

    def test_refuse_overwrite_and_input_equals_output(self):
        self.render()
        with self.assertRaisesRegex(ValueError, "существует"):
            self.render()
        source = ROOT / "assets/templates/contract.docx"
        with self.assertRaisesRegex(ValueError, "Шаблон"):
            module.render(source, self.fields, source, force=True)
        module.render(source, self.fields, self.output, force=True)

    def test_no_personal_metadata_or_external_source_relationships(self):
        for path in (ROOT / "assets/templates").glob("*.docx"):
            with ZipFile(path) as z:
                self.assertFalse(any(n.startswith("customXml/") or n.startswith("docProps/") for n in z.namelist()))
                for name in z.namelist():
                    if name.endswith(".rels"):
                        self.assertNotIn(b'TargetMode="External"', z.read(name))
                root = ET.fromstring(z.read("word/document.xml"))
                text = "".join(root.itertext())
                self.assertNotRegex(text, r"https?://(?!\{\{)")
                self.assertNotRegex(text, r"\b\d{10,20}\b")

    def test_nested_data_control_characters_and_unresolved_values_fail(self):
        source = ROOT / "assets/templates/contract.docx"
        for fields in ({"A": {}}, {"A": "\x00"}, {"A": "{{UNRESOLVED}}"}):
            with self.assertRaises(ValueError):
                module.render(source, fields, self.output)


    def patch_data(self, path, replacements):
        return {"source_sha256": module.inspect_document(path)["source_sha256"],
                "replacements": replacements}

    def test_safe_patch_preserves_unmodified_parts_and_run_paragraph_properties(self):
        self.render()
        revised = self.output.with_name("revision-2.docx")
        old = self.fields["PAYMENT_CLAUSE"]
        new = "Полная предоплата не позднее одного рабочего дня до публикации. Час отдельно не согласован."
        module.patch_document(self.output, self.patch_data(self.output, [{"find": old, "replace": new}]), revised)
        with ZipFile(self.output) as src, ZipFile(revised) as dst:
            for part in src.namelist():
                if part != "word/document.xml":
                    self.assertEqual(src.read(part), dst.read(part), part)
            a, b = [ET.fromstring(z.read("word/document.xml")) for z in (src, dst)]
            for tag in ("pPr", "rPr", "sectPr"):
                self.assertEqual([ET.tostring(e) for e in a.iter(W + tag)],
                                 [ET.tostring(e) for e in b.iter(W + tag)])
            self.assertIn(new, "".join(b.itertext()))
            self.assertNotIn(old, "".join(b.itertext()))
        self.assertTrue(module.audit_document(revised)["ok"])

    def test_patch_multiline_creative_and_exact_table_cell(self):
        self.fields["POST_TEXT"] = "🔒 Старый заголовок\n✅ Старый текст\nhttps://example.org/"
        self.render("creative")
        revised = self.output.with_name("revision-2.docx")
        info = module.inspect_document(self.output)
        table_url = next(p for p in info["paragraphs"] if p["text"] == self.fields["LINK_1_URL"])
        text = "🔒 Новый заголовок\n✅ Точный текст\n📊🚀💸\nhttps://example.org/"
        module.patch_document(self.output, self.patch_data(self.output, [
            {"find": self.fields["POST_TEXT"], "replace": text},
            {"paragraph": table_url["paragraph"], "find": self.fields["LINK_1_URL"], "replace": "https://example.org/correct"}
        ]), revised)
        paragraphs = [p["text"] for p in module.inspect_document(revised)["paragraphs"]]
        self.assertIn(text, paragraphs)
        self.assertIn("https://example.org/correct", paragraphs)
        self.assertTrue(module.audit_document(revised)["ok"])

    def test_patch_ambiguity_missing_and_stale_source_fail_without_output(self):
        self.render()
        target = self.output.with_name("revision-2.docx")
        for old in ("Исполнитель", "Такого текста здесь нет"):
            with self.assertRaisesRegex(ValueError, "ровно один"):
                module.patch_document(self.output, self.patch_data(self.output, [{"find": old, "replace": "X"}]), target)
            self.assertFalse(target.exists())
        with self.assertRaisesRegex(ValueError, "source_sha256"):
            module.patch_document(self.output, {"source_sha256": "0" * 64, "replacements": []}, target)
        with self.assertRaisesRegex(ValueError, "перезапись"):
            module.patch_document(self.output, self.patch_data(self.output, []), self.output)

    def test_revise_keeps_full_field_map_and_refuses_manual_edits(self):
        self.render()
        target = self.output.with_name("revision-2.docx")
        module.revise_document(self.output, {"CONTRACT_CITY": "Учебный город"}, target)
        saved = json.loads(target.with_suffix(".docx.fields.json").read_text())
        self.assertEqual(saved["fields"]["VAT_CLAUSE"], self.fields["VAT_CLAUSE"])
        self.assertEqual(saved["fields"]["CONTRACT_CITY"], "Учебный город")
        # Имитируем правку человека после генерации, не меняя сохранённую карту.
        with ZipFile(target, "a") as z:
            z.writestr("manual-edit.txt", "changed")
        with self.assertRaisesRegex(ValueError, "изменён после сборки"):
            module.revise_document(target, {"CONTRACT_CITY": "Другой"}, target.with_name("revision-3.docx"))

    def test_audit_detects_the_observed_lost_run_font(self):
        self.render()
        with ZipFile(self.output) as z:
            parts = {n: z.read(n) for n in z.namelist()}
        doc = module.minidom.parseString(parts["word/document.xml"])
        run = next(r for r in module.nodes(doc, "r") if module.nodes(r, "t") and module.text_of(module.nodes(r, "t")[0]).strip())
        run.removeChild(module.direct_child(run, "rPr"))
        parts["word/document.xml"] = doc.toxml(encoding="utf-8")
        broken = self.output.with_name("broken.docx")
        module.write_package(parts, broken)
        report = module.audit_document(broken)
        self.assertFalse(report["ok"])
        self.assertIsNone(report["issues"][0]["font"])
        with self.assertRaisesRegex(ValueError, "уже потерял оформление"):
            module.patch_document(broken, self.patch_data(broken, [{"find": "X", "replace": "Y"}]), self.output.with_name("revised.docx"))

    def test_emoji_are_exact_and_embedded_font_roundtrips(self):
        text = "🔒 Защита\n✅ Проверено 📊 Статистика 🚀 Старт 💸 Цена"
        self.fields["POST_TEXT"] = text
        self.render("creative")
        self.assertIn(text, [p["text"] for p in module.inspect_document(self.output)["paragraphs"]])
        report = module.audit_document(self.output)
        self.assertTrue(report["ok"])
        self.assertEqual(set("".join(report["emoji"])), set("🔒✅📊🚀💸"))
        with ZipFile(self.output) as z:
            embedded = bytearray(z.read("word/fonts/blogger-emoji.odttf"))
            mask = bytes.fromhex("CD96883E967441C4B352593E84AEB25B")[::-1]
            for i in range(32):
                embedded[i] ^= mask[i % 16]
            self.assertEqual(bytes(embedded), module.EMOJI_FONT.read_bytes())

    def test_unavailable_emoji_is_reported_without_silently_removing_it(self):
        self.fields["POST_TEXT"] = "Неизвестный символ \U0001faff"
        with self.assertRaisesRegex(ValueError, "не содержит символы"):
            self.render("creative")
        self.assertFalse(self.output.exists())



    def test_literal_tabs_and_crlf_preserve_visible_text_as_word_controls(self):
        source = self.synthetic('<w:p><w:r><w:rPr><w:b/></w:rPr><w:t>{{VALUE}}</w:t></w:r></w:p>')
        value = "\tМосква\t18.10.2026\r\nСледующая строка\t"
        module.render(source, {"VALUE": value}, self.output)
        with ZipFile(self.output) as z:
            root = ET.fromstring(z.read("word/document.xml"))
            self.assertEqual(len(list(root.iter(W + "tab"))), 3)
            self.assertEqual(len(list(root.iter(W + "br"))), 1)
            self.assertTrue(all("\t" not in (t.text or "") for t in root.iter(W + "t")))
            self.assertEqual(len(list(root.iter(W + "b"))), 1)
        text = module.inspect_document(self.output)["paragraphs"][0]["text"]
        self.assertEqual(text, value.replace("\r\n", "\n"))

    def test_amendment_city_and_date_use_right_tab_at_column_edge(self):
        self.render("amendment")
        with ZipFile(self.output) as z:
            root = ET.fromstring(z.read("word/document.xml"))
            paragraph = next(p for p in root.iter(W + "p")
                             if self.fields["CONTRACT_CITY"] in "".join(p.itertext()))
            stop = paragraph.find(W + "pPr/" + W + "tabs/" + W + "tab")
            section = list(root.iter(W + "sectPr"))[-1]
            size, margins = section.find(W + "pgSz"), section.find(W + "pgMar")
            width = int(size.get(W + "w")) - int(margins.get(W + "left")) - int(margins.get(W + "right"))
            self.assertEqual(stop.get(W + "val"), "right")
            self.assertEqual(int(stop.get(W + "pos")), width)
            self.assertEqual(len(paragraph.findall(".//" + W + "r/" + W + "tab")), 1)
            self.assertTrue(all("\t" not in (t.text or "") for t in paragraph.iter(W + "t")))
        expected = self.fields["CONTRACT_CITY"] + "\t" + self.fields["AMENDMENT_DATE"]
        self.assertIn(expected, [p["text"] for p in module.inspect_document(self.output)["paragraphs"]])
        self.assertTrue(module.audit_document(self.output)["ok"])
        revised = self.output.with_name("amendment-revised.docx")
        module.patch_document(self.output, self.patch_data(self.output, [{
            "find": expected, "replace": "г. Тула\t" + self.fields["AMENDMENT_DATE"]
        }]), revised)
        self.assertIn("г. Тула\t" + self.fields["AMENDMENT_DATE"],
                      [p["text"] for p in module.inspect_document(revised)["paragraphs"]])
        with ZipFile(revised) as z:
            after = ET.fromstring(z.read("word/document.xml"))
            self.assertEqual([ET.tostring(e) for e in root.iter(W + "pPr")],
                             [ET.tostring(e) for e in after.iter(W + "pPr")])

    def amendment_review(self):
        return {"text": "Документ: Договор. Место правки: пункт 5.2.\n"
                        "Заменить только фрагмент:\nПредоплата.\nна:\nОплата после подписания.\nОстальное условие сохранить.",
                "review_source": {"directory": str(Path(self.temp.name).resolve()),
                                  "manifest_sha256": "a" * 64, "input_sha256": "b" * 64}}

    def test_amendment_review_inserts_exact_changes_and_locks_manual_field(self):
        generated = self.amendment_review()
        fields = {key: value for key, value in self.fields.items() if key != "AMENDMENT_CHANGES"}
        before = dict(fields)
        with patch.object(module, "review_changes", return_value=generated):
            module.render(ROOT / "assets/templates/amendment.docx", fields, self.output, review_dir=self.temp.name)
            self.assertIn("1. " + generated["text"], [p["text"] for p in module.inspect_document(self.output)["paragraphs"]])
            self.assertEqual(module.verify_review_document(self.output), generated["review_source"])
            saved = json.loads(self.output.with_suffix(".docx.fields.json").read_text())
            self.assertEqual(saved["review_source"], generated["review_source"])
            self.assertEqual(saved["output_sha256"], hashlib.sha256(self.output.read_bytes()).hexdigest())
            changed = self.output.with_name("changed.docx")
            with self.assertRaisesRegex(ValueError, "Удалите ручной AMENDMENT_CHANGES"):
                module.render(ROOT / "assets/templates/amendment.docx", self.fields, changed, review_dir=self.temp.name)
            self.assertFalse(changed.exists())
            with self.assertRaisesRegex(ValueError, "только к собственному шаблону amendment"):
                module.render(ROOT / "assets/templates/contract.docx", fields, changed, review_dir=self.temp.name)
            with self.assertRaisesRegex(ValueError, "уже существует"):
                module.render(ROOT / "assets/templates/amendment.docx", fields, self.output, force=True, review_dir=self.temp.name)
        self.assertEqual(fields, before)

    def test_revise_review_amendment_preserves_binding_and_refuses_manual_docx_changes(self):
        generated = self.amendment_review()
        fields = {key: value for key, value in self.fields.items() if key != "AMENDMENT_CHANGES"}
        revised = self.output.with_name("v2.docx")
        with patch.object(module, "review_changes", return_value=generated):
            module.render(ROOT / "assets/templates/amendment.docx", fields, self.output, review_dir=self.temp.name)
            module.revise_document(self.output, {"AMENDMENT_NUMBER": "2"}, revised)
            self.assertEqual(module.verify_review_document(revised), generated["review_source"])
            with self.assertRaisesRegex(ValueError, "Удалите ручной AMENDMENT_CHANGES"):
                module.revise_document(self.output, {"AMENDMENT_CHANGES": "Полностью заменить пункт 5.2."}, self.output.with_name("bad.docx"))
            self.output.write_bytes(self.output.read_bytes() + b"human-change")
            before = self.output.read_bytes()
            with self.assertRaisesRegex(ValueError, "DOCX изменён"):
                module.revise_document(self.output, {}, self.output.with_name("bad.docx"))
            self.assertEqual(self.output.read_bytes(), before)

    def test_review_proof_cannot_hide_changed_docx_text_even_if_hash_was_rewritten(self):
        generated = self.amendment_review()
        fields = {key: value for key, value in self.fields.items() if key != "AMENDMENT_CHANGES"}
        with patch.object(module, "review_changes", return_value=generated):
            module.render(ROOT / "assets/templates/amendment.docx", fields, self.output, review_dir=self.temp.name)
            changed = self.output.with_name("changed.docx")
            module.patch_document(self.output, self.patch_data(self.output, [{
                "find": "1. " + generated["text"], "replace": "1. Полностью заменить пункт 5.2."}]), changed)
            saved = json.loads(self.output.with_suffix(".docx.fields.json").read_text())
            saved["output_sha256"] = hashlib.sha256(changed.read_bytes()).hexdigest()
            changed.with_suffix(".docx.fields.json").write_text(json.dumps(saved))
            with self.assertRaisesRegex(ValueError, "не содержит точные поправки"):
                module.verify_review_document(changed)


if __name__ == "__main__":
    unittest.main()
