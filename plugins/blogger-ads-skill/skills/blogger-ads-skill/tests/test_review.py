import base64
import copy
import hashlib
import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from zipfile import ZipFile

from pypdf import PdfReader, PdfWriter
from pypdf.generic import ArrayObject, DecodedStreamObject, DictionaryObject, NameObject, NumberObject

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("review", ROOT / "scripts/review.py")
review = importlib.util.module_from_spec(spec)
spec.loader.exec_module(review)
spec = importlib.util.spec_from_file_location("review_test_deal", ROOT / "scripts/deal.py")
deal = importlib.util.module_from_spec(spec)
spec.loader.exec_module(deal)


class ReviewTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)
        self.source = self.base / "received.pdf"
        self.pdf(self.source, ["Page one.", "Page two.", "The customer pays for the services in advance."])
        self.user_source = self.base / "user.txt"
        self.user_source.write_text("Проверь только порядок оплаты.", encoding="utf-8")
        self.input = self.base / "decisions.json"
        self.output = self.base / "review-v1"
        self.data = {
            "title": "Сверка договора",
            "comparison_at": "2026-11-01T12:00:00+03:00",
            "scope": {"mode": "selected_terms", "categories": ["payment"],
                      "source": {"path": "user.txt", "quote": "Проверь только порядок оплаты."}},
            "documents": [{"id": "contract", "label": "Полученный договор", "path": "received.pdf"}],
            "decisions": [{
                "id": "sign-first", "category": "payment", "topic": "Порядок подписания и оплаты",
                "source": {"document": "contract", "clause": "5.2", "page": 3,
                           "quote": "The customer pays for the services in advance."},
                "previous": {"source": {
                    "kind": "none", "group": "contract", "reason": "Первое поручение: проект ещё не выдавался."}},
                "status": "proposed",
                "implementation": {"kind": "unresolved", "reason": "Платёжный пункт не определяет связь с подписанием.",
                                   "state": [{"path": "/schedule/payment_due_at", "value": None}],
                                   "plan": [{"id": "report-clarify", "at": "2026-11-02T10:00:00+03:00"}]},
                "decision": {"action": "replace", "reason": "Сохранить согласованную последовательность",
                             "selected": "Оплата после подписания", "plan": "Согласовать новую редакцию",
                             "target": "Договор, п. 5.2", "original": "The customer pays for the services in advance.",
                             "text": "Оплата производится после подписания договора обеими сторонами."}
            }]
        }
        self.state_path, self.plan_path = self.base / "state.json", self.base / "plan.json"
        self.state = json.loads((ROOT / "assets/deal.example.json").read_text())
        self.state["schedule"]["publication_at"] = "2026-11-09T09:00:00+03:00"
        self.state["schedule"]["reporting_deadlines"] = [{
            "id": "clarify", "title": "Уточнить порядок подписания и получить подтверждения готовности",
            "at": "2026-11-02T10:00:00+03:00", "source": "Договор п.5.2: порядок не определён",
            "kind": "organizational", "checks": list(deal.CHECK_LABELS)}]

    def save_state_plan(self):
        for document in self.data["documents"]:
            if not any(entry["path"] == document["path"] for entry in self.state["documents"]):
                path = self.base / document["path"]
                self.state["documents"].append({"path": document["path"], "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                                               "status": "received", "group": "received", "current": True})
        self.state_path.write_text(json.dumps(self.state), encoding="utf-8")
        plan = deal.plan(self.state, deal.stamp("2026-11-01T12:00:00+03:00"), self.state_path)
        self.plan_path.write_text(json.dumps(plan), encoding="utf-8")
        return plan

    def check(self):
        return review.check_state(self.output, self.state_path, self.plan_path)

    def prior_document(self, quote="Report within 24 hours after publication."):
        path = self.base / "issued-v1.docx"
        with ZipFile(path, "w") as archive:
            archive.writestr("word/document.xml", '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
                             '<w:body><w:p><w:r><w:t>Report within 24 hours </w:t></w:r>'
                             '<w:r><w:t>after publication.</w:t></w:r></w:p></w:body></w:document>')
        self.data["decisions"][0]["previous"] = {
            "source": {
                "kind": "document", "group": "contract", "path": path.name, "clause": "3.1", "quote": quote}}
        self.state["documents"] = [{"path": path.name, "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                                    "status": "draft_for_review", "version": "v1", "group": "contract",
                                    "delivered_at": "2026-10-29T12:00:00+03:00", "current": False}]
        return path

    @staticmethod
    def pdf(path, pages):
        """Маленький настоящий PDF с независимым текстом на каждой странице; None — страница без текста."""
        writer = PdfWriter()
        font = DictionaryObject({NameObject("/Type"): NameObject("/Font"),
                                 NameObject("/Subtype"): NameObject("/Type1"),
                                 NameObject("/BaseFont"): NameObject("/Helvetica")})
        for text in pages:
            page = writer.add_blank_page(width=300, height=300)
            if text is not None:
                page[NameObject("/Resources")] = DictionaryObject({
                    NameObject("/Font"): DictionaryObject({NameObject("/F1"): font})})
                content = DecodedStreamObject()
                escaped = text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
                content.set_data(f"BT /F1 12 Tf 10 200 Td ({escaped}) Tj ET".encode("ascii"))
                page[NameObject("/Contents")] = writer._add_object(content)
        with path.open("wb") as stream:
            writer.write(stream)

    def full_scope(self):
        self.data.pop("scope", None)
        self.data["review_basis"] = {"edition": "received", "reason": "Предварительный план по полученной редакции."}
        self.data["signature_matrix"] = [{"label": "Учебный текст", "source": {"document": "contract", "page": 3},
                                          "not_applicable": "Учебный файл без подписной части; просмотрены все три страницы."}]
        template = self.data["decisions"][0]
        self.data["decisions"] = []
        for category in review.CATEGORIES:
            item = copy.deepcopy(template)
            item["id"] = category
            item["category"] = category
            item["topic"] = review.CATEGORIES[category]
            if category in review.TIMING_CATEGORIES:
                item["implementation"]["timing"] = {
                    "subject": "Учебный пункт об оплате не содержит отдельного срока этой категории",
                    "target": None, "mode": "not_applicable", "received_value": None, "selected_value": None,
                    "reason": "В тестовом PDF только условие предоплаты; отдельного срока этой категории нет."}
                self.state["schedule"].setdefault("reviewed_terms", {})[item["id"]] = review.reviewed_timing_record(item)
            self.data["decisions"].append(item)

    def signature(self, label="Договор", document="contract", page=3):
        path = self.base / (document + "-" + str(page) + ".png")
        path.write_bytes(base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jRZkAAAAASUVORK5CYII="))
        return {"label": label, "source": {"document": document, "page": page},
                "contractor": {"status": "absent", "observation": "В месте подписи исполнителя пустая строка."},
                "customer": {"status": "present", "observation": "В месте подписи заказчика виден рукописный штрих."},
                "image_path": path.name}

    def save(self):
        self.input.write_text(json.dumps(self.data, ensure_ascii=False, indent=2), encoding="utf-8")

    def build(self):
        self.save()
        return review.build(self.input, self.output)

    def test_computed_hash_absolute_source_link_and_saved_input(self):
        original = self.source.read_bytes()
        self.build()
        table = (self.output / "terms-review.md").read_text()
        digest = hashlib.sha256(original).hexdigest()
        self.assertIn(digest, table)
        self.assertIn(str(self.source), table)
        self.assertIn("страница: 3", table)
        self.assertIn("The customer pays for the services in advance.", table)
        self.assertEqual((self.output / "review-input.json").read_bytes(), self.input.read_bytes())
        self.assertEqual(self.source.read_bytes(), original)
        self.assertTrue(review.audit(self.output)["valid"])

    def test_previous_column_uses_exact_delivered_quote_and_rejects_unproved_paraphrase(self):
        self.prior_document()
        previous = self.data["decisions"][0]["previous"]
        previous["value"] = "Ответственность ограничена ценой договора."
        with self.assertRaisesRegex(ValueError, "previous.value.*удалите value"):
            self.build()
        self.assertFalse(self.output.exists())
        previous["value"] = previous["source"]["quote"]
        self.build()
        table = (self.output / "terms-review.md").read_text()
        self.assertEqual(table.count(previous["source"]["quote"]), 1)
        self.assertNotIn("Ответственность ограничена", table)

    def test_previous_absence_requires_reason_without_replacement_summary(self):
        self.data["decisions"][0]["previous"]["value"] = "В старом проекте не было штрафа."
        with self.assertRaisesRegex(ValueError, "previous.value"):
            self.build()
        del self.data["decisions"][0]["previous"]["value"]
        self.build()
        self.assertIn(self.data["decisions"][0]["previous"]["source"]["reason"],
                      (self.output / "terms-review.md").read_text())

    def test_amendment_uses_grouped_exact_fragments_and_keeps_other_conditions(self):
        item = self.data["decisions"][0]
        quote = item["source"]["quote"] + " Urgent placement requires an extra fee."
        self.pdf(self.source, ["Page one.", "Page two.", quote])
        item["source"]["quote"] = quote
        duplicate = copy.deepcopy(item)
        duplicate["id"] = "payment-copy"
        appended = copy.deepcopy(item)
        appended["id"] = "add-exchange"
        appended["decision"].update(action="append", text="Стороны обмениваются документами по согласованным адресам.")
        appended["decision"].pop("original")
        self.data["decisions"] += [duplicate, appended]
        self.build()
        generated = review.amendment_changes(self.output)
        text = generated["text"]
        self.assertEqual(text.count("Заменить только фрагмент:"), 1)
        self.assertNotIn("sign-first", text)
        self.assertNotIn("payment-copy", text)
        self.assertIn("Заменить только фрагмент:\n" + item["decision"]["original"] + "\nна:\n" + item["decision"]["text"], text)
        self.assertIn("Остальное условие сохранить.", text)
        self.assertNotIn("Urgent placement requires an extra fee.", text)
        self.assertIn("Добавить:\n" + appended["decision"]["text"], text)
        self.assertFalse(text.startswith("1."))
        self.assertEqual(generated["review_source"], {
            "directory": str(self.output.resolve()),
            "manifest_sha256": hashlib.sha256((self.output / "manifest.json").read_bytes()).hexdigest(),
            "input_sha256": hashlib.sha256((self.output / "review-input.json").read_bytes()).hexdigest()})
        (self.output / "corrections.md").write_text("Пересказ вместо точных поправок")
        with self.assertRaisesRegex(ValueError, "Изменён сохранённый файл"):
            review.amendment_changes(self.output)

    def test_amendment_without_replace_or_append_is_not_invented(self):
        decision = self.data["decisions"][0]["decision"]
        decision["action"] = "keep"
        for key in ("original", "text", "target"):
            decision.pop(key)
        self.build()
        with self.assertRaisesRegex(ValueError, "не содержит replace/append"):
            review.amendment_changes(self.output)

    def test_every_replace_and_append_has_its_exact_text_and_id_in_both_files(self):
        for action in ("append", "keep", "defer", "reject"):
            item = copy.deepcopy(self.data["decisions"][0])
            item["id"] = action
            item["decision"]["action"] = action
            item["decision"].pop("original")
            if action == "append":
                item["decision"]["text"] = "Дополнить пункт: слово | ссылка\nВторая строка с ``` и `кодом`."
            else:
                del item["decision"]["text"]
                del item["decision"]["target"]
            self.data["decisions"].append(item)
        result = self.build()
        self.assertEqual(result["corrections"], 2)
        corrections = (self.output / "corrections.md").read_text()
        table = (self.output / "terms-review.md").read_text()
        for item in self.data["decisions"]:
            self.assertIn(item["id"], table)
            self.assertIn(item["id"], corrections)
            if item["decision"]["action"] in ("replace", "append"):
                self.assertIn(item["decision"]["text"], corrections)
                self.assertIn("Точная правка " + item["id"], table)
            else:
                self.assertNotIn("Точная правка " + item["id"], table)
        self.assertEqual(corrections.count("Текст правки не выпускается."), 3)

    def test_incomplete_promised_correction_rejected_before_output(self):
        for action in ("replace", "append"):
            for key in ("text", "target", "reason", "selected", "plan"):
                with self.subTest(action=action, missing=key):
                    original = copy.deepcopy(self.data)
                    self.data["decisions"][0]["decision"]["action"] = action
                    del self.data["decisions"][0]["decision"][key]
                    with self.assertRaises(ValueError):
                        self.build()
                    self.assertFalse(self.output.exists())
                    self.data = original

    def test_non_changes_cannot_hide_text_and_need_reason(self):
        for action in ("keep", "defer", "reject"):
            original = copy.deepcopy(self.data)
            self.data["decisions"][0]["decision"]["action"] = action
            with self.assertRaisesRegex(ValueError, "Только replace/append"):
                self.build()
            del self.data["decisions"][0]["decision"]["text"]
            del self.data["decisions"][0]["decision"]["target"]
            self.data["decisions"][0]["decision"]["reason"] = " "
            with self.assertRaises(ValueError):
                self.build()
            self.data = original

    def test_model_cannot_supply_source_hash_or_unrecognized_fields(self):
        self.data["documents"][0]["sha256"] = "0" * 64
        with self.assertRaisesRegex(ValueError, "неизвестные поля"):
            self.build()
        self.assertFalse(self.output.exists())

    def test_missing_clause_has_review_scope_instead_of_a_fabricated_quote(self):
        source = self.data["decisions"][0]["source"]
        source["quote"] = None
        with self.assertRaisesRegex(ValueError, "not_found_reason"):
            self.build()
        self.assertFalse(self.output.exists())
        source["not_found_reason"] = "раздел 5 на странице 3 и остальные разделы на страницах 1–6"
        self.data["decisions"][0]["decision"]["action"] = "append"
        self.data["decisions"][0]["decision"].pop("original")
        self.build()
        table = (self.output / "terms-review.md").read_text()
        self.assertIn("Пункт не найден; просмотрено: раздел 5 на странице 3", table)
        self.assertNotIn("«None»", table)
        self.assertNotIn("«Пункт не найден", table)
        self.assertTrue(review.audit(self.output)["valid"])
        source["quote"] = "Присутствующая цитата"
        self.save()
        with self.assertRaisesRegex(ValueError, "допустим только при quote: null"):
            review.build(self.input, self.base / "review-v2")

    def test_missing_or_unknown_document_and_invalid_page_are_rejected(self):
        original = copy.deepcopy(self.data)
        for field, value in (("document", "unknown"), ("page", 0), ("page", True), ("quote", " ")):
            with self.subTest(field=field, value=value):
                self.data = copy.deepcopy(original)
                self.data["decisions"][0]["source"][field] = value
                with self.assertRaises(ValueError):
                    self.build()
                self.assertFalse(self.output.exists())
        self.data = original
        self.source.unlink()
        with self.assertRaisesRegex(ValueError, "документ отсутствует"):
            self.build()
        self.assertFalse(self.output.exists())

    def test_duplicate_decision_cannot_overwrite_a_required_correction(self):
        self.data["decisions"].append(copy.deepcopy(self.data["decisions"][0]))
        with self.assertRaisesRegex(ValueError, "повторный идентификатор"):
            self.build()
        self.assertFalse(self.output.exists())

    def test_existing_directory_is_never_overwritten_even_when_empty(self):
        self.output.mkdir()
        with self.assertRaises(FileExistsError):
            self.build()
        self.assertEqual(list(self.output.iterdir()), [])
        self.output.rmdir()
        self.build()
        before = {p.name: p.read_bytes() for p in self.output.iterdir()}
        self.data["decisions"][0]["decision"]["text"] = "Иная редакция"
        with self.assertRaises(FileExistsError):
            self.build()
        self.assertEqual(before, {p.name: p.read_bytes() for p in self.output.iterdir()})

    def test_audit_rejects_source_changes_and_missing_source(self):
        self.build()
        self.source.write_bytes(b"Changed received contract")
        with self.assertRaisesRegex(ValueError, "SHA-256"):
            review.audit(self.output)
        self.source.unlink()
        with self.assertRaisesRegex(ValueError, "документ отсутствует"):
            review.audit(self.output)

    def test_audit_rejects_edited_outputs_and_preserved_input(self):
        self.build()
        for name in ("terms-review.md", "corrections.md", "review-input.json"):
            path = self.output / name
            original = path.read_bytes()
            path.write_bytes(original + b"manual edit")
            with self.assertRaisesRegex(ValueError, "Изменён сохранённый файл"):
                review.audit(self.output)
            path.write_bytes(original)

    def test_updating_output_hash_cannot_hide_a_missing_correction(self):
        self.build()
        path = self.output / "corrections.md"
        path.write_text("Все правки якобы учтены.\n", encoding="utf-8")
        manifest_path = self.output / "manifest.json"
        manifest = json.loads(manifest_path.read_text())
        manifest["outputs"]["corrections.md"] = hashlib.sha256(path.read_bytes()).hexdigest()
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "не воспроизводится"):
            review.audit(self.output)

    def test_cli_new_version_uses_changed_decisions_without_mutating_previous(self):
        self.build()
        before = {p.name: p.read_bytes() for p in self.output.iterdir()}
        self.data["decisions"][0]["decision"]["text"] = "Новая согласуемая редакция."
        self.save()
        second = self.base / "review-v2"
        result = subprocess.run([sys.executable, str(ROOT / "scripts/review.py"), "build", str(self.input), str(second)],
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Новая согласуемая редакция.", (second / "corrections.md").read_text())
        self.assertEqual(before, {p.name: p.read_bytes() for p in self.output.iterdir()})
        self.assertTrue(review.audit(self.output)["valid"])
        self.assertTrue(review.audit(second)["valid"])

    def test_full_scope_requires_all_fourteen_categories_with_complete_decisions(self):
        self.full_scope()
        self.assertEqual(len(self.data["decisions"]), 14)
        removed = next(item for item in self.data["decisions"] if item["category"] == "exchange")
        self.data["decisions"].remove(removed)
        self.assertEqual(removed["category"], "exchange")
        with self.assertRaisesRegex(ValueError, "отсутствуют решения категорий.*exchange"):
            self.build()
        self.assertFalse(self.output.exists())
        self.data["decisions"].append({"id": "exchange", "category": "exchange", "reviewed": True})
        with self.assertRaisesRegex(ValueError, "отсутствуют поля"):
            self.build()
        self.data["decisions"][-1] = removed
        self.build()
        self.assertIn("все 14 категорий", (self.output / "terms-review.md").read_text())
        self.assertTrue(review.audit(self.output)["valid"])

    def test_ord_reporting_does_not_cover_creative_delivery_or_report_to_client(self):
        self.full_scope()
        original = copy.deepcopy(self.data)
        for category in ("creative_delivery", "client_reporting"):
            with self.subTest(missing=category):
                self.data = copy.deepcopy(original)
                self.data["decisions"] = [item for item in self.data["decisions"] if item["category"] != category]
                self.assertTrue(any(item["category"] == "ord_reporting" for item in self.data["decisions"]))
                with self.assertRaisesRegex(ValueError, "отсутствуют решения категорий.*" + category):
                    self.build()
                self.assertFalse(self.output.exists())

    def test_selected_scope_requires_literal_user_limit_and_complete_selected_categories(self):
        self.data["scope"]["source"]["quote"] = "Удобнее проверить лишь оплату."
        with self.assertRaisesRegex(ValueError, "ограничения поручения нет"):
            self.build()
        self.data["scope"]["source"]["quote"] = "Проверь только порядок оплаты."
        self.data["scope"]["categories"].append("exchange")
        with self.assertRaisesRegex(ValueError, "exchange"):
            self.build()
        self.data["scope"]["categories"] = ["payment"]
        self.build()
        table = (self.output / "terms-review.md").read_text()
        self.assertIn("Выборочная сверка: payment", table)
        self.assertIn(str(self.user_source), table)
        self.user_source.write_text("Проверь весь договор, а не только оплату.", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "ограничения поручения нет"):
            review.audit(self.output)

    def test_pdf_quote_on_another_page_or_not_present_blocks_build(self):
        source = self.data["decisions"][0]["source"]
        source["page"] = 2
        with self.assertRaisesRegex(ValueError, "цитата не найдена на странице 2"):
            self.build()
        source["page"] = 3
        source["quote"] = "The customer pays after all services have been completed."
        with self.assertRaisesRegex(ValueError, "цитата не найдена на странице 3"):
            self.build()
        source["quote"] = "The customer pays for the services in advance!"
        with self.assertRaisesRegex(ValueError, "цитата не найдена"):
            self.build()
        self.assertFalse(self.output.exists())

    def test_quote_tolerates_only_whitespace_and_audit_rechecks_its_page(self):
        source = self.data["decisions"][0]["source"]
        source["quote"] = "The customer\n  pays\tfor the\u00a0services in advance."
        self.build()
        manifest = json.loads((self.output / "manifest.json").read_text())
        self.assertEqual(manifest["verification"]["citations"][0]["method"], "pdf_text")
        # Даже обновление хеша сохранённого JSON не позволяет сослаться на иную страницу.
        saved_path = self.output / "review-input.json"
        saved = json.loads(saved_path.read_text())
        saved["decisions"][0]["source"]["page"] = 2
        saved_path.write_text(json.dumps(saved), encoding="utf-8")
        manifest["outputs"]["review-input.json"] = hashlib.sha256(saved_path.read_bytes()).hexdigest()
        (self.output / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "цитата не найдена на странице 2"):
            review.audit(self.output)

    def test_scan_needs_explicit_visual_basis_and_is_not_called_automatic(self):
        self.pdf(self.source, ["Page one.", "Page two.", None])
        source = self.data["decisions"][0]["source"]
        with self.assertRaisesRegex(ValueError, "verification"):
            self.build()
        source["verification"] = {"method": "visual", "basis": ""}
        with self.assertRaisesRegex(ValueError, "basis"):
            self.build()
        source["verification"]["basis"] = "Просмотрена увеличенная страница 3; текст условия читаем, текстового слоя нет."
        self.build()
        table = (self.output / "terms-review.md").read_text()
        self.assertIn("Визуальная проверка составителя; автоматическая сверка текста не выполнена", table)
        self.assertNotIn("Цитата автоматически найдена", table)
        manifest = json.loads((self.output / "manifest.json").read_text())
        self.assertEqual(manifest["verification"]["citations"][0]["method"], "visual_scan")
        self.assertTrue(review.audit(self.output)["valid"])

    def test_layout_extraction_recovers_positioned_letters_without_removing_real_spaces(self):
        # Малый межбуквенный отступ: plain ошибочно читает «W here»,
        # layout учитывает положение букв и читает целое «Where».
        writer = PdfWriter()
        page = writer.add_blank_page(width=300, height=300)
        font = DictionaryObject({
            NameObject("/Type"): NameObject("/Font"),
            NameObject("/Subtype"): NameObject("/Type1"),
            NameObject("/BaseFont"): NameObject("/Helvetica"),
            NameObject("/FirstChar"): NumberObject(32),
            NameObject("/LastChar"): NumberObject(126),
            NameObject("/Widths"): ArrayObject([NumberObject(80)] + [NumberObject(600)] * 94),
        })
        page[NameObject("/Resources")] = DictionaryObject({
            NameObject("/Font"): DictionaryObject({NameObject("/F1"): font})})
        content = DecodedStreamObject()
        content.set_data(b"BT /F1 12 Tf 10 200 Td [(W) -40 (here is the word.)] TJ ET")
        page[NameObject("/Contents")] = writer._add_object(content)
        with self.source.open("wb") as stream:
            writer.write(stream)

        self.assertEqual(PdfReader(self.source).pages[0].extract_text(), "W here is the word.")
        item = {"id": "positioned", "source": {
            "document": "contract", "page": 1, "quote": "Where is the word."}}
        document = {"path": str(self.source)}
        result = review.verify_citation(item, document, {})
        self.assertEqual(result["method"], "pdf_text")
        self.assertEqual(result["extraction_mode"], "layout")
        for quote in ("Whereistheword.", "Where is theword.", "Where is the word!", "Where is word."):
            item["source"]["quote"] = quote
            with self.subTest(quote=quote), self.assertRaisesRegex(ValueError, "цитата не найдена"):
                review.verify_citation(item, document, {})

    def test_visual_claim_cannot_override_wrong_quote_on_searchable_pdf_page(self):
        source = self.data["decisions"][0]["source"]
        source["page"] = 2
        source["verification"] = {"method": "visual", "basis": "Визуально кажется верным."}
        with self.assertRaisesRegex(ValueError, "содержит извлекаемый текст"):
            self.build()
        self.assertFalse(self.output.exists())

    def test_timing_category_cannot_skip_binding_or_declare_known_deadline_unknown(self):
        item = self.data["decisions"][0]
        original = copy.deepcopy(item["implementation"])
        item["implementation"] = {"kind": "no_state_change", "reason": "Договор пока не подписан."}
        with self.assertRaisesRegex(ValueError, "временная категория"):
            self.build()
        for value in (None, "unspecified", {"precision": "unspecified", "condition": None}):
            item["implementation"] = dict(copy.deepcopy(original), kind="known")
            item["implementation"]["state"][0]["value"] = value
            with self.assertRaisesRegex(ValueError, "Известный срок"):
                self.build()
        item["implementation"] = original
        item["implementation"]["plan"] = []
        with self.assertRaisesRegex(ValueError, "непустой список"):
            self.build()
        self.assertFalse(self.output.exists())

    def test_claims_24_hours_cannot_remain_null_or_use_an_old_plan(self):
        item = self.data["decisions"][0]
        item["implementation"] = {"kind": "known", "state": [
            {"path": "/schedule/claims_after_end_hours", "value": 24}], "plan": [
            {"id": "claims", "at": "2026-11-12T09:00:00+03:00"}]}
        self.build()
        self.save_state_plan()
        with self.assertRaisesRegex(ValueError, "claims_after_end_hours.*таблица требует 24"):
            self.check()
        self.state["schedule"]["claims_after_end_hours"] = 24
        self.state_path.write_text(json.dumps(self.state), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "нет ожидаемого события claims"):
            self.check()
        self.save_state_plan()
        before = {path: path.read_bytes() for path in [self.state_path, self.plan_path, *self.output.iterdir()]}
        result = self.check()
        self.assertTrue(result["state_checked"])
        self.assertEqual(result["state"]["sha256"], hashlib.sha256(self.state_path.read_bytes()).hexdigest())
        self.assertEqual(before, {path: path.read_bytes() for path in before})
        self.assertIn("/schedule/claims_after_end_hours = 24", (self.output / "terms-review.md").read_text())
        self.assertFalse(review.audit(self.output)["state_checked"])

    def test_relative_erid_condition_must_survive_in_state_without_inventing_hour(self):
        term = {"precision": "relative", "condition": "ERID за один рабочий день до публикации",
                "source": "Полученный договор п.4.1, предварительное условие до принятия",
                "calculation": {"kind": "business_days_before_publication", "days": 1, "requires": []}}
        self.data["decisions"][0]["implementation"] = {"kind": "known", "state": [
            {"path": "/schedule/deadline_terms/erid", "value": term}], "plan": [
            {"id": "report-clarify", "at": "2026-11-02T10:00:00+03:00"}]}
        self.build()
        self.save_state_plan()
        with self.assertRaisesRegex(ValueError, "deadline_terms/erid"):
            self.check()
        self.state["schedule"]["deadline_terms"]["erid"] = term
        self.save_state_plan()
        self.assertTrue(self.check()["state_checked"])
        self.assertIsNone(json.loads(self.state_path.read_text())["schedule"]["erid_due_at"])

    def test_client_reporting_keeps_previous_24_hours_and_checks_new_one_hour(self):
        self.prior_document()
        self.data["scope"]["categories"] = ["client_reporting"]
        self.user_source.write_text("Проверь только передачу ссылки заказчику.", encoding="utf-8")
        self.data["scope"]["source"]["quote"] = self.user_source.read_text()
        item = self.data["decisions"][0]
        item.update(id="link", category="client_reporting", topic="Ссылка заказчику")
        item["implementation"] = {"kind": "known", "state": [
            {"path": "/schedule/link_after_publication_hours", "value": 1}], "plan": [
            {"id": "link", "at": "2026-11-09T10:00:00+03:00"}], "timing": {
                "subject": "Передача ссылки после публикации", "target": "/schedule/link_after_publication_hours",
                "mode": "provisional", "received_value": 1, "selected_value": 1,
                "reason": "Предварительный план по полученному сроку до отдельного принятия."}}
        self.state["schedule"]["reviewed_terms"] = {item["id"]: review.reviewed_timing_record(item)}
        self.build()
        self.state["schedule"]["link_after_publication_hours"] = 24
        self.save_state_plan()
        with self.assertRaisesRegex(ValueError, "link_after_publication_hours.*таблица требует 1"):
            self.check()
        self.state["schedule"]["link_after_publication_hours"] = 1
        self.save_state_plan()
        self.assertTrue(self.check()["state_checked"])
        self.assertIn("Report within 24 hours after publication.", (self.output / "terms-review.md").read_text())

    def test_unresolved_requires_reason_and_concrete_organizational_action(self):
        applied = self.data["decisions"][0]["implementation"]
        reason = applied.pop("reason")
        with self.assertRaisesRegex(ValueError, "reason"):
            self.build()
        applied["reason"] = reason
        applied["plan"] = [{"id": "publication", "at": "2026-11-09T09:00:00+03:00"}]
        self.build()
        plan = self.save_state_plan()
        # Обычное событие публикации не заменяет уточнение неизвестного условия.
        publication = next(event for event in plan["events"] if event["at"] == "2026-11-09T09:00:00+03:00")
        publication["id"] = "publication"
        self.plan_path.write_text(json.dumps(plan))
        with self.assertRaisesRegex(ValueError, "организационного действия"):
            self.check()

    def test_recalculation_detects_stale_plan_even_with_same_revision_and_expected_event(self):
        self.build()
        self.save_state_plan()
        self.state["schedule"]["duration_hours"] = 72
        self.state_path.write_text(json.dumps(self.state))
        with self.assertRaisesRegex(ValueError, "не воспроизводится"):
            self.check()

    def test_scene_time_checks_current_plan_without_invalidating_previous_sources(self):
        previous_contract = self.prior_document()
        first_at = self.data["comparison_at"]
        self.state["clock"] = {"mode": "scenario", "entries": [
            {"at": first_at, "source": "Пользователь: сейчас 1 ноября, 12:00 МСК"}]}
        previous = self.save_state_plan()
        previous_plan = self.base / "issued-plan-v1.json"
        previous_plan.write_bytes(self.plan_path.read_bytes())
        self.state["documents"].append({"path": previous_plan.name,
            "sha256": hashlib.sha256(previous_plan.read_bytes()).hexdigest(),
            "status": "prepared", "version": "v1", "group": "plan", "current": False,
            "delivered_at": first_at})
        self.state_path.write_text(json.dumps(self.state), encoding="utf-8")
        current_at = "2026-11-01T15:00:00+03:00"
        deal.set_scene_time(self.state_path, current_at, "Пользователь: теперь 1 ноября, 15:00 МСК")
        self.state = json.loads(self.state_path.read_text())
        self.data["comparison_at"] = current_at
        prior_plan_decision = copy.deepcopy(self.data["decisions"][0])
        prior_plan_decision["id"] = "previous-plan"
        prior_plan_decision["previous"] = {"source": {
            "kind": "document", "group": "plan", "path": previous_plan.name,
            "clause": "generated_at", "quote": first_at}}
        self.data["decisions"].append(prior_plan_decision)
        self.build()
        current = deal.plan(self.state, state_path=self.state_path, previous=previous)
        self.plan_path.write_text(json.dumps(current), encoding="utf-8")
        preserved = {path: path.read_bytes() for path in [
            self.state_path, previous_contract, previous_plan, *self.output.iterdir()]}

        self.assertTrue(self.check()["state_checked"])
        self.assertEqual(current["generated_at"], current_at)
        table = (self.output / "terms-review.md").read_text()
        self.assertIn(str(previous_contract), table)
        self.assertIn(str(previous_plan), table)
        self.assertIn("Report within 24 hours after publication.", table)
        for generated_at in (first_at, "2026-11-01T17:00:00+03:00"):
            with self.subTest(generated_at=generated_at):
                changed = dict(current, generated_at=generated_at)
                self.plan_path.write_text(json.dumps(changed), encoding="utf-8")
                with self.assertRaisesRegex(ValueError, "не совпадает с текущим временем сцены"):
                    self.check()
                self.assertEqual(preserved, {path: path.read_bytes() for path in preserved})
        self.plan_path.write_text(json.dumps(current), encoding="utf-8")
        self.assertTrue(self.check()["state_checked"])

    def test_actual_previous_docx_quote_and_delivery_registry_are_required(self):
        path = self.prior_document()
        self.build()
        self.save_state_plan()
        self.assertTrue(self.check()["state_checked"])
        table = (self.output / "terms-review.md").read_text()
        self.assertIn("Report within 24 hours after publication.", table)
        self.assertIn(str(path), table)
        self.assertIn(hashlib.sha256(path.read_bytes()).hexdigest(), table)
        for change in ("missing_delivery", "wrong_hash"):
            state = copy.deepcopy(self.state)
            if change == "missing_delivery":
                state["documents"][0].pop("delivered_at")
            elif change == "wrong_hash":
                state["documents"][0]["sha256"] = "0" * 64
            self.state_path.write_text(json.dumps(state))
            with self.subTest(change=change), self.assertRaisesRegex(ValueError, "реестр"):
                self.check()

    def test_registering_next_version_does_not_invalidate_previous_comparison(self):
        self.prior_document()
        self.build()
        self.save_state_plan()
        self.assertTrue(self.check()["state_checked"])
        next_path = self.base / "issued-v2.txt"
        next_path.write_text("Report within one hour after publication.")
        self.state["documents"].append({"path": next_path.name, "sha256": hashlib.sha256(next_path.read_bytes()).hexdigest(),
                                        "group": "contract", "delivered_at": "2026-11-01T12:30:00+03:00", "current": True})
        self.save_state_plan()
        self.assertTrue(self.check()["state_checked"])

    def test_previous_quote_cannot_be_fabricated_and_manual_edit_is_preserved(self):
        path = self.prior_document("Report within 1 hour after publication.")
        with self.assertRaisesRegex(ValueError, "прежнем источнике"):
            self.build()
        self.data["decisions"][0]["previous"]["source"]["quote"] = "Report within 24 hours after publication."
        self.build()
        path.write_bytes(path.read_bytes() + b"manual edit")
        changed = path.read_bytes()
        with self.assertRaisesRegex(ValueError, "SHA-256"):
            review.audit(self.output)
        self.assertEqual(path.read_bytes(), changed)

    def test_message_and_none_stay_valid_after_first_delivery_later_than_comparison(self):
        previous = copy.deepcopy(self.data["decisions"][0]["previous"])
        self.prior_document()
        self.data["decisions"][0]["previous"] = previous
        self.state["documents"][0]["delivered_at"] = "2026-11-01T12:30:00+03:00"
        self.build()
        self.save_state_plan()
        self.assertTrue(self.check()["state_checked"])
        self.assertIn("Отсутствие прежнего документа установлено составителем; автоматически не подтверждено.",
                      (self.output / "terms-review.md").read_text())
        self.data["decisions"][0]["previous"]["source"] = {
            "kind": "message", "group": "contract", "path": "user.txt",
            "quote": "Проверь только порядок оплаты.", "reason": "До начала сверки собственный проект не выдавался."}
        self.output = self.base / "review-v2"
        self.build()
        self.assertTrue(self.check()["state_checked"])
        self.assertIn("Отсутствие прежнего документа установлено составителем; автоматически не подтверждено.",
                      (self.output / "terms-review.md").read_text())
        self.data["decisions"][0]["previous"]["source"].pop("reason")
        self.save()
        with self.assertRaisesRegex(ValueError, "reason"):
            review.build(self.input, self.base / "review-v3")

    def test_missing_prior_clause_needs_actual_delivered_document_and_reason(self):
        self.prior_document(None)
        with self.assertRaisesRegex(ValueError, "not_found_reason"):
            self.build()
        self.data["decisions"][0]["previous"]["source"]["not_found_reason"] = "Просмотрен весь ранее выданный проект; срок оплаты отсутствует."
        self.build()
        self.save_state_plan()
        self.assertTrue(self.check()["state_checked"])
        self.assertIn("Пункт не найден; просмотрено: Просмотрен весь ранее выданный", (self.output / "terms-review.md").read_text())

    def test_check_state_cli_checks_current_files_without_writing_result_into_package(self):
        self.build()
        self.save_state_plan()
        before = sorted(path.name for path in self.output.iterdir())
        result = subprocess.run([sys.executable, str(ROOT / "scripts/review.py"), "check-state", str(self.output),
                                 "--state", str(self.state_path), "--plan", str(self.plan_path)], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(json.loads(result.stdout)["state_checked"])
        self.assertEqual(before, sorted(path.name for path in self.output.iterdir()))

    def test_full_and_selected_signing_require_signature_matrix(self):
        self.full_scope()
        self.data.pop("signature_matrix")
        with self.assertRaisesRegex(ValueError, "signature_matrix"):
            self.build()
        self.data["scope"] = {"mode": "selected_terms", "categories": ["signing"],
                              "source": {"path": "user.txt", "quote": self.user_source.read_text()}}
        self.data["decisions"] = [item for item in self.data["decisions"] if item["category"] == "signing"]
        with self.assertRaisesRegex(ValueError, "signature_matrix"):
            self.build()
        self.data["signature_matrix"] = [self.signature()]
        self.build()
        self.assertTrue(review.audit(self.output)["valid"])

    def test_matrix_preserves_each_logical_document_both_sides_and_image_hashes(self):
        self.full_scope()
        second = self.base / "appendix.pdf"
        self.pdf(second, ["Appendix.", "Signature area."])
        self.data["documents"].append({"id": "appendix", "label": "Приложение", "path": second.name})
        self.data["signature_matrix"] = [self.signature("Договор", page=2), self.signature("ТЗ", page=3),
                                          self.signature("Текст публикации", "appendix", 2)]
        self.data["signature_matrix"][2]["customer"] = {"status": "unclear", "observation": "Изображение в строке подписи нечёткое."}
        self.build()
        table = (self.output / "terms-review.md").read_text()
        for row in self.data["signature_matrix"]:
            self.assertIn(row["label"], table)
            for role in ("contractor", "customer"):
                self.assertIn(row[role]["observation"], table)
            path = self.base / row["image_path"]
            self.assertIn(str(path), table)
            self.assertIn(hashlib.sha256(path.read_bytes()).hexdigest(), table)
        self.assertIn("Неясно", table)
        self.assertTrue(review.audit(self.output)["valid"])

    def test_signature_matrix_rejects_missing_role_invalid_page_and_uncovered_source(self):
        self.full_scope()
        self.data["signature_matrix"] = [self.signature()]
        original = copy.deepcopy(self.data)
        for change in ("role", "page", "unknown", "observation", "image", "uncovered"):
            self.data = copy.deepcopy(original)
            row = self.data["signature_matrix"][0]
            if change == "role":
                row.pop("customer")
            elif change == "page":
                row["source"]["page"] = 4
            elif change == "unknown":
                row["source"]["document"] = "other"
            elif change == "observation":
                row["contractor"]["observation"] = ""
            elif change == "image":
                row["image_path"] = "missing.png"
            else:
                self.data["documents"].append({"id": "uncovered", "label": "Непроверенное вложение", "path": self.source.name})
            with self.subTest(change=change), self.assertRaises(ValueError):
                self.build()
            self.assertFalse(self.output.exists())

    def test_not_applicable_requires_reason_and_cannot_hide_party_observation(self):
        self.full_scope()
        row = self.data["signature_matrix"][0]
        row["not_applicable"] = ""
        with self.assertRaisesRegex(ValueError, "not_applicable"):
            self.build()
        row["not_applicable"] = "Файл содержит только перечень URL без подписной части; просмотрен целиком."
        row["contractor"] = {"status": "absent", "observation": "Пусто"}
        with self.assertRaisesRegex(ValueError, "неизвестные поля"):
            self.build()
        row.pop("contractor")
        self.build()
        self.assertIn(row["not_applicable"], (self.output / "terms-review.md").read_text())

    def test_audit_detects_changed_or_missing_signature_image_and_manual_matrix_edit(self):
        self.full_scope()
        self.data["signature_matrix"] = [self.signature()]
        self.build()
        image = self.base / self.data["signature_matrix"][0]["image_path"]
        original = image.read_bytes()
        image.write_bytes(original + b"changed")
        with self.assertRaisesRegex(ValueError, "SHA-256"):
            review.audit(self.output)
        image.unlink()
        with self.assertRaisesRegex(ValueError, "отсутствует"):
            review.audit(self.output)
        image.write_bytes(original)
        table = self.output / "terms-review.md"
        table.write_text(table.read_text().replace("Не видна:", "Видна:"))
        with self.assertRaisesRegex(ValueError, "Изменён сохранённый файл"):
            review.audit(self.output)

    def test_received_basis_requires_old_draft_package_to_be_historical_without_deleting_it(self):
        path = self.prior_document()
        self.full_scope()
        entry = self.state["documents"][0]
        entry["current"] = True
        self.build()
        self.save_state_plan()
        original = path.read_bytes()
        with self.assertRaisesRegex(ValueError, "проект.*ещё актуален"):
            self.check()
        entry["current"] = False
        sibling = self.base / "issued-v1.pdf"
        self.pdf(sibling, ["Previous draft."])
        self.state["documents"].append(dict(entry, path=sibling.name, current=True,
                                            sha256=hashlib.sha256(sibling.read_bytes()).hexdigest()))
        self.save_state_plan()
        with self.assertRaisesRegex(ValueError, "issued-v1.pdf.*ещё актуален"):
            self.check()
        self.state["documents"][-1]["current"] = False
        self.save_state_plan()
        self.assertTrue(self.check()["state_checked"])
        self.assertEqual(path.read_bytes(), original)
        self.assertEqual(entry["delivered_at"], "2026-10-29T12:00:00+03:00")

    def test_archive_rule_does_not_downgrade_signed_or_received_originals(self):
        self.prior_document()
        self.full_scope()
        self.build()
        entry = self.state["documents"][0]
        for status in ("signed", "received", "draft_for_review", "proposed_revision"):
            entry.update(status=status, current=True)
            self.save_state_plan()
            if status in ("signed", "received"):
                self.assertTrue(self.check()["state_checked"])
            else:
                with self.assertRaisesRegex(ValueError, "ещё актуален"):
                    self.check()
        entry.pop("status")
        self.save_state_plan()
        with self.assertRaisesRegex(ValueError, "Статус прежнего документа"):
            self.check()

    def test_previous_basis_keeps_prior_draft_current_and_needs_explicit_reason(self):
        self.prior_document()
        self.full_scope()
        self.data["review_basis"] = {"edition": "previous", "reason": "Полученные сроки отклонены; выбран прежний проект до исправления."}
        self.state["documents"][0]["current"] = True
        self.build()
        self.save_state_plan()
        self.assertTrue(self.check()["state_checked"])
        self.data["review_basis"]["reason"] = ""
        self.save()
        with self.assertRaisesRegex(ValueError, "review_basis.reason"):
            review.build(self.input, self.base / "review-v2")

    def test_received_sources_must_all_be_registered_with_computed_sha(self):
        second = self.base / "received-attachment.pdf"
        self.pdf(second, ["Attachment."])
        self.data["documents"].append({"id": "attachment", "label": "Полученное приложение", "path": second.name})
        self.build()
        self.save_state_plan()
        entry = next(item for item in self.state["documents"] if item["path"] == second.name)
        self.state["documents"].remove(entry)
        self.state_path.write_text(json.dumps(self.state))
        with self.assertRaisesRegex(ValueError, "исходник не зарегистрирован"):
            self.check()
        self.state["documents"].append(dict(entry, sha256="0" * 64))
        self.state_path.write_text(json.dumps(self.state))
        with self.assertRaisesRegex(ValueError, "SHA не совпадает"):
            self.check()
        self.state["documents"][-1] = entry
        self.state_path.write_text(json.dumps(self.state))
        self.assertTrue(self.check()["state_checked"])

    def test_identical_payment_and_signing_correction_is_emitted_once_with_both_ids(self):
        self.full_scope()
        result = self.build()
        corrections = (self.output / "corrections.md").read_text()
        self.assertEqual(result["corrections"], 1)
        self.assertEqual(corrections.count(self.data["decisions"][0]["decision"]["text"]), 1)
        for item in self.data["decisions"]:
            self.assertIn(item["id"], corrections)
            self.assertIn("Точная правка " + item["id"], (self.output / "terms-review.md").read_text())

    def test_replace_requires_literal_original_and_preserves_other_sentences(self):
        decision = self.data["decisions"][0]["decision"]
        decision.pop("original")
        with self.assertRaisesRegex(ValueError, "original"):
            self.build()
        decision["original"] = "a tax phrase that is not in the received document"
        with self.assertRaisesRegex(ValueError, "дословным фрагментом"):
            self.build()
        decision["original"] = "in advance"
        decision["text"] = "after both parties sign"
        self.build()
        corrections = (self.output / "corrections.md").read_text()
        self.assertIn("Заменить только фрагмент:", corrections)
        self.assertIn("in advance", corrections)
        self.assertIn("Остальное условие сохранить.", corrections)
        replaced = corrections.split("Заменить только фрагмент:", 1)[1].split("\nна:\n", 1)[0]
        self.assertNotIn("The customer pays for the services in advance.", replaced)

    def test_different_replacements_of_overlapping_fragments_fail_before_output(self):
        first = self.data["decisions"][0]
        second = copy.deepcopy(first)
        second["id"] = "tax"
        second["decision"]["original"] = "in advance"
        second["decision"]["text"] = "with tax included"
        self.data["decisions"].append(second)
        with self.assertRaisesRegex(ValueError, "Конфликт замен sign-first и tax"):
            self.build()
        self.assertFalse(self.output.exists())
        second["decision"]["original"] = first["decision"]["original"]
        with self.assertRaisesRegex(ValueError, "Конфликт замен"):
            self.build()

    def test_disjoint_replacements_same_target_are_allowed_and_wrong_page_still_rejected(self):
        first = self.data["decisions"][0]
        first["decision"]["original"] = "The customer"
        second = copy.deepcopy(first)
        second["id"] = "deadline"
        second["decision"]["original"] = "in advance"
        second["decision"]["text"] = "after signing"
        self.data["decisions"].append(second)
        self.assertEqual(self.build()["corrections"], 2)
        second["source"]["page"] = 2
        self.save()
        with self.assertRaisesRegex(ValueError, "цитата не найдена"):
            review.build(self.input, self.base / "review-v2")

    def test_tax_fragment_replacement_does_not_replace_price_or_included_ad_fee(self):
        quote = "Price is 60000. VAT is not charged. Advertising fee is included in the price."
        self.pdf(self.source, ["Page one.", "Page two.", quote])
        item = self.data["decisions"][0]
        item["source"]["quote"] = quote
        item["decision"].update(target="Договор, п.5.1", original="VAT is not charged.",
                                text="VAT is included in the price.")
        fee = copy.deepcopy(item)
        fee["id"] = "fee-kept"
        fee["decision"]["action"] = "keep"
        fee["decision"]["selected"] = "Рекламные отчисления остаются включёнными в цену."
        for key in ("original", "target", "text"):
            fee["decision"].pop(key)
        self.data["decisions"].append(fee)
        self.build()
        corrections = (self.output / "corrections.md").read_text()
        self.assertIn("```text\nVAT is not charged.\n```", corrections)
        self.assertIn("```text\nVAT is included in the price.\n```", corrections)
        replaced = corrections.split("Заменить только фрагмент:", 1)[1].split("\nна:\n", 1)[0]
        self.assertNotIn("Price is 60000.", replaced)
        self.assertIn(fee["decision"]["selected"], corrections)
        self.assertTrue(review.audit(self.output)["valid"])

    def test_different_target_spelling_cannot_bypass_physical_pdf_overlap(self):
        first = self.data["decisions"][0]
        first["decision"]["target"] = "Договор, п.5.2"
        second = copy.deepcopy(first)
        second["id"] = "other"
        second["decision"].update(target="Договор, п. 5.2", text="Другая редакция того же участка.")
        self.data["decisions"].append(second)
        with self.assertRaisesRegex(ValueError, "Конфликт замен"):
            self.build()
        self.assertFalse(self.output.exists())
        second["decision"]["text"] = first["decision"]["text"]
        self.assertEqual(self.build()["corrections"], 1)

    def test_repeated_original_in_disjoint_contexts_remains_distinguishable_in_corrections(self):
        payment = "The customer pays within 5 days."
        report = "The contractor reports within 5 days."
        self.pdf(self.source, ["Page one.", "Page two.", payment + " " + report])
        first = self.data["decisions"][0]
        first["source"]["quote"] = payment
        first["decision"].update(original="within 5 days", text="within 3 days")
        second = copy.deepcopy(first)
        second["id"] = "report"
        second["source"]["quote"] = report
        second["decision"]["text"] = "within 1 day"
        self.data["decisions"].append(second)
        self.assertEqual(self.build()["corrections"], 2)
        corrections = (self.output / "corrections.md").read_text()
        self.assertIn(payment, corrections)
        self.assertIn(report, corrections)
        self.assertEqual(corrections.count("Контекст: пункт 5.2; страница 3."), 2)

    def test_docx_original_is_hashed_registered_and_preserved_alongside_pdf(self):
        path = self.prior_document()
        self.data["documents"][0]["original_path"] = path.name
        self.build()
        self.save_state_plan()
        manifest = json.loads((self.output / "manifest.json").read_text())
        self.assertEqual(manifest["sources"][0]["original"], {"path": str(path.resolve()), "sha256": review.sha256(path.read_bytes())})
        self.assertTrue(self.check()["state_checked"])
        state = json.loads(self.state_path.read_text())
        state["documents"] = [entry for entry in state["documents"] if entry["path"] != path.name]
        self.state_path.write_text(json.dumps(state))
        with self.assertRaisesRegex(ValueError, "Полученный исходник не зарегистрирован"):
            self.check()
        original = path.read_bytes()
        path.write_bytes(original + b"manual edit")
        with self.assertRaisesRegex(ValueError, "SHA-256"):
            review.audit(self.output)
        self.assertEqual(path.read_bytes(), original + b"manual edit")

    def test_original_path_requires_existing_docx_and_cannot_accept_supplied_sha(self):
        self.data["documents"][0]["original_path"] = "missing.docx"
        with self.assertRaises(FileNotFoundError):
            self.build()
        self.data["documents"][0]["original_path"] = "user.txt"
        with self.assertRaisesRegex(ValueError, "исходным DOCX"):
            self.build()
        path = self.prior_document()
        self.data["documents"][0].update(original_path=path.name, original_sha256="0" * 64)
        with self.assertRaisesRegex(ValueError, "неизвестные поля"):
            self.build()

    def test_unknown_publication_allows_no_plan_only_with_explicit_pending_reason(self):
        applied = self.data["decisions"][0]["implementation"]
        applied["plan"] = []
        with self.assertRaisesRegex(ValueError, "plan_pending_reason"):
            self.build()
        applied["plan_pending_reason"] = "Дата публикации ещё не установлена; расчёт после её согласования."
        self.build()
        self.state["schedule"]["publication_at"] = None
        self.state["documents"] = [{"path": self.source.name, "sha256": review.sha256(self.source.read_bytes()), "status": "received"}]
        self.state_path.write_text(json.dumps(self.state))
        result = review.check_state(self.output, self.state_path)
        self.assertTrue(result["state_checked"])
        self.assertIsNone(result["plan"])
        self.assertIsNone(json.loads(self.state_path.read_text())["schedule"]["publication_at"])
        # Состояние по-прежнему проверяется, хотя расписание ещё не рассчитано.
        self.state["schedule"].pop("payment_due_at")
        self.state_path.write_text(json.dumps(self.state))
        with self.assertRaisesRegex(ValueError, "отсутствует путь"):
            review.check_state(self.output, self.state_path)

    def test_pending_plan_cannot_bypass_known_publication_or_existing_bindings(self):
        self.build()
        self.save_state_plan()
        with self.assertRaisesRegex(ValueError, "требуют текущий план"):
            review.check_state(self.output, self.state_path)
        self.state["schedule"]["publication_at"] = None
        self.state_path.write_text(json.dumps(self.state))
        with self.assertRaisesRegex(ValueError, "плановые связи"):
            review.check_state(self.output, self.state_path)
        applied = self.data["decisions"][0]["implementation"]
        applied.update(plan=[], plan_pending_reason="Дата пока неизвестна")
        self.output = self.base / "review-v2"
        self.build()
        self.state["schedule"]["publication_at"] = "2026-11-09T09:00:00+03:00"
        self.save_state_plan()
        with self.assertRaisesRegex(ValueError, "дата публикации уже известна"):
            self.check()


if __name__ == "__main__":
    unittest.main()
