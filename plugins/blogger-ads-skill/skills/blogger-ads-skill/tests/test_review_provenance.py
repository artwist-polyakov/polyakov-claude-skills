import copy
import hashlib
import json
import unittest
from urllib.parse import quote
from zipfile import ZipFile

import test_review


review = test_review.review


class ReviewProvenanceTests(unittest.TestCase):
    setUp = test_review.ReviewTests.setUp
    pdf = staticmethod(test_review.ReviewTests.pdf)
    prior_document = test_review.ReviewTests.prior_document
    build = test_review.ReviewTests.build
    save = test_review.ReviewTests.save
    save_state_plan = test_review.ReviewTests.save_state_plan
    check = test_review.ReviewTests.check

    def styled_prior_document(self, namespace, origin, override=False, default_value=None, styles_path="word/styles.xml"):
        path = self.prior_document("Styled clause.")
        properties = '<w:rStyle w:val="HiddenChild"/>' if origin == "character" else ""
        paragraph = '<w:pPr><w:pStyle w:val="HiddenChild"/></w:pPr>' if origin == "paragraph" else ""
        if override:
            properties += '<w:vanish w:val="0"/>'
        if origin == "defaults":
            value = f' w:val="{default_value}"' if default_value is not None else ""
            styles = f'<w:docDefaults><w:rPrDefault><w:rPr><w:vanish{value}/></w:rPr></w:rPrDefault></w:docDefaults>'
        else:
            styles = (f'<w:style w:type="{origin}" w:styleId="HiddenBase"><w:rPr><w:vanish/></w:rPr></w:style>'
                      f'<w:style w:type="{origin}" w:styleId="HiddenChild"><w:basedOn w:val="HiddenBase"/></w:style>')
        with ZipFile(path, "w") as archive:
            archive.writestr("word/document.xml", f'<w:document xmlns:w="{namespace}"><w:body><w:p>{paragraph}'
                             f'<w:r><w:rPr>{properties}</w:rPr><w:t>Styled clause.</w:t></w:r>'
                             '</w:p></w:body></w:document>')
            archive.writestr(styles_path, f'<w:styles xmlns:w="{namespace}">{styles}</w:styles>')
            archive.writestr("word/_rels/document.xml.rels", '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                             '<Relationship Id="styles" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" '
                             f'Target="{styles_path.removeprefix("word/")}"/></Relationships>')
            archive.writestr("[Content_Types].xml", '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
                             f'<Override PartName="/{styles_path}" '
                             'ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.styles+xml"/></Types>')
        return path

    def test_hidden_inherited_styles_require_the_quote_in_fresh_pdf(self):
        for namespace in sorted(review.WORD_NAMESPACES):
            for origin in ("character", "paragraph"):
                with self.subTest(namespace=namespace, origin=origin):
                    path = self.styled_prior_document(namespace, origin)
                    before = path.read_bytes()
                    def convert(source, destination, soffice=None):
                        pdf = destination / "visible.pdf"
                        self.pdf(pdf, ["Only the visible condition."])
                        return pdf
                    self.docx_converter.side_effect = convert
                    with self.assertRaisesRegex(ValueError, "цитата не найдена в видимом тексте.*скрытие текста"):
                        self.build()
                    self.assertFalse(self.output.exists())
                    self.assertEqual(before, path.read_bytes())
                    self.assertFalse(self.docx_converter.call_args.args[1].exists())

    def test_visible_overrides_pass_and_audit_repeats_conversion(self):
        for index, namespace in enumerate(sorted(review.WORD_NAMESPACES)):
            for origin in ("character", "paragraph"):
                with self.subTest(namespace=namespace, origin=origin):
                    path = self.styled_prior_document(namespace, origin, override=True)
                    before = path.read_bytes()
                    self.output = self.base / f"visible-{index}-{origin}"
                    visible = "Styled clause."
                    def convert(source, destination, soffice=None):
                        pdf = destination / "visible.pdf"
                        self.pdf(pdf, [visible])
                        return pdf
                    self.docx_converter.side_effect = convert
                    self.docx_converter.reset_mock()
                    self.save()
                    review.build(self.input, self.output, soffice="/chosen/soffice")
                    self.assertTrue(review.audit(self.output, soffice="/chosen/soffice")["valid"])
                    self.assertEqual(self.docx_converter.call_count, 2)
                    self.assertTrue(all(call.kwargs["soffice"] == "/chosen/soffice"
                                        for call in self.docx_converter.call_args_list))
                    manifest = json.loads((self.output / "manifest.json").read_text())
                    self.assertEqual(manifest["verification"]["previous"][0]["sha256"], review.sha256(before))
                    visible = "The converter no longer shows this condition."
                    with self.assertRaisesRegex(ValueError, "цитата не найдена в видимом тексте"):
                        review.audit(self.output, soffice="/chosen/soffice")
                    self.assertEqual(self.docx_converter.call_count, 3)
                    self.assertEqual(path.read_bytes(), before)
                    self.assertTrue(all(not call.args[1].exists() for call in self.docx_converter.call_args_list))

    def test_active_hidden_docdefaults_fail_even_with_direct_override_and_relocated_styles(self):
        for namespace in sorted(review.WORD_NAMESPACES):
            for override in (False, True):
                for style_path in ("word/styles.xml", "word/custom/relocated.xml", "word/custom/styles.dat"):
                    with self.subTest(namespace=namespace, override=override, style_path=style_path):
                        path = self.styled_prior_document(namespace, "defaults", override, styles_path=style_path)
                        before = path.read_bytes()
                        self.docx_converter.reset_mock()
                        with self.assertRaisesRegex(ValueError, "docDefaults.*уже выданную PDF-копию"):
                            self.build()
                        self.docx_converter.assert_not_called()
                        self.assertFalse(self.output.exists())
                        self.assertEqual(path.read_bytes(), before)

    def test_inactive_default_hiding_and_unrelated_xml_do_not_block_visible_quote(self):
        for index, namespace in enumerate(sorted(review.WORD_NAMESPACES)):
            for value in ("0", "false", "off"):
                with self.subTest(namespace=namespace, value=value):
                    path = self.styled_prior_document(namespace, "defaults", default_value=value)
                    with ZipFile(path, "a") as archive:
                        hidden = '<w:docDefaults><w:rPrDefault><w:rPr><w:vanish/></w:rPr></w:rPrDefault></w:docDefaults>'
                        archive.writestr("custom/not-styles.xml", f'<w:settings xmlns:w="{namespace}">{hidden}</w:settings>')
                        archive.writestr("custom/foreign.xml", f'<w:styles xmlns:w="urn:other">{hidden}</w:styles>')
                        archive.writestr("word/media/image.png", b"\x89PNG\r\n\x1a\n\xff\x00\x01")
                        archive.writestr("word/fonts/font.odttf", b"\x00\x01\x00\x00binary font data")
                    self.output = self.base / f"defaults-visible-{index}-{value}"
                    self.build()
                    self.assertTrue(review.audit(self.output)["valid"])

    def test_docx_without_standard_style_part_still_requires_visible_quote(self):
        self.prior_document()
        def convert(source, destination, soffice=None):
            pdf = destination / "visible.pdf"
            self.pdf(pdf, ["A different visible clause."])
            return pdf
        self.docx_converter.side_effect = convert
        with self.assertRaisesRegex(ValueError, "цитата не найдена в видимом тексте"):
            self.build()
        self.assertFalse(self.output.exists())

    def test_previous_docx_requires_converter_and_detects_change_during_conversion(self):
        path = self.prior_document()
        convert = self.docx_converter.side_effect
        self.docx_converter.side_effect = ValueError("LibreOffice не найден: укажите --soffice или PATH")
        with self.assertRaisesRegex(ValueError, "LibreOffice не найден.*--soffice"):
            self.build()
        self.assertFalse(self.output.exists())
        before = path.read_bytes()
        def change(source, destination, soffice=None):
            pdf = convert(source, destination, soffice)
            path.write_bytes(before + b"changed during conversion")
            return pdf
        self.docx_converter.side_effect = change
        with self.assertRaisesRegex(ValueError, "Прежний DOCX изменён во время проверки"):
            self.build()
        self.assertFalse(self.output.exists())
        self.assertEqual(path.read_bytes(), before + b"changed during conversion")
        self.assertFalse(self.docx_converter.call_args.args[1].exists())

    def test_prior_docx_quotes_use_current_visible_text_in_both_namespaces(self):
        namespaces = ("http://schemas.openxmlformats.org/wordprocessingml/2006/main",
                      "http://purl.oclc.org/ooxml/wordprocessingml/main")
        for index, namespace in enumerate(namespaces):
            with self.subTest(namespace=namespace):
                path = self.prior_document()
                with ZipFile(path, "w") as archive:
                    archive.writestr("word/document.xml", f'<w:document xmlns:w="{namespace}"><w:body><w:p>'
                                     '<w:del><w:r><w:delText>Deleted price 90000.</w:delText></w:r></w:del>'
                                     '<w:del><w:r><w:t>Deleted container text.</w:t></w:r></w:del>'
                                     '<w:moveFrom><w:r><w:t>Moved old clause.</w:t></w:r></w:moveFrom>'
                                     '<w:r><w:instrText>Internal field instruction.</w:instrText></w:r>'
                                     '<w:r><w:rPr><w:vanish/></w:rPr><w:t>Hidden old clause.</w:t></w:r>'
                                     '<w:r><w:t>Current</w:t><w:tab/></w:r>'
                                     '<w:ins><w:r><w:t>price 50000.</w:t><w:br/></w:r></w:ins>'
                                     '<w:moveTo><w:r><w:t>Accepted clause.</w:t></w:r></w:moveTo>'
                                     '<w:r><w:rPr><w:vanish w:val="0"/></w:rPr><w:t> Visible result.</w:t></w:r>'
                                     '</w:p></w:body></w:document>')
                self.output = self.base / f"revisions-{index}"
                source = self.data["decisions"][0]["previous"]["source"]
                for hidden in ("Deleted price 90000.", "Deleted container text.", "Moved old clause.",
                               "Internal field instruction.", "Hidden old clause."):
                    source["quote"] = hidden
                    with self.assertRaisesRegex(ValueError, "дословная цитата не найдена"):
                        self.build()
                    self.assertFalse(self.output.exists())
                source["quote"] = "Current price 50000. Accepted clause. Visible result."
                self.build()
                self.assertTrue(review.audit(self.output)["valid"])
                self.assertIn(source["quote"], (self.output / "terms-review.md").read_text())

                saved = self.output / "review-input.json"
                data = json.loads(saved.read_text())
                data["decisions"][0]["previous"]["source"]["quote"] = "Deleted price 90000."
                saved.write_text(json.dumps(data))
                manifest_path = self.output / "manifest.json"
                manifest = json.loads(manifest_path.read_text())
                manifest["outputs"]["review-input.json"] = hashlib.sha256(saved.read_bytes()).hexdigest()
                manifest_path.write_text(json.dumps(manifest))
                with self.assertRaisesRegex(ValueError, "дословная цитата не найдена"):
                    review.audit(self.output)

    def test_none_cannot_hide_an_already_delivered_document(self):
        previous = copy.deepcopy(self.data["decisions"][0]["previous"])
        self.prior_document()
        self.data["decisions"][0]["previous"] = previous
        self.build()
        self.save_state_plan()
        before = self.state_path.read_bytes()
        with self.assertRaisesRegex(ValueError, "уже выданы документы.*kind=document"):
            self.check()
        self.assertEqual(before, self.state_path.read_bytes())

    def test_message_cannot_replace_the_existing_document(self):
        self.prior_document()
        self.data["decisions"][0]["previous"]["source"] = {
            "kind": "message", "group": "contract", "path": "user.txt",
            "quote": "Проверь только порядок оплаты.", "reason": "Собственного документа якобы нет."}
        self.build()
        self.save_state_plan()
        with self.assertRaisesRegex(ValueError, "уже выданы документы"):
            self.check()

    def test_equal_timestamps_do_not_invent_order_and_invalidate_earlier_absence(self):
        previous = copy.deepcopy(self.data["decisions"][0]["previous"])
        self.prior_document()
        self.data["decisions"][0]["previous"] = previous
        self.state["documents"][0]["delivered_at"] = self.data["comparison_at"]
        self.build()
        self.save_state_plan()
        self.assertTrue(self.check()["state_checked"])

    def test_absence_requires_comparison_time_with_zone_before_building(self):
        self.data.pop("comparison_at")
        with self.assertRaisesRegex(ValueError, "comparison_at"):
            self.build()
        self.assertFalse(self.output.exists())
        self.data["comparison_at"] = "2026-11-01T12:00:00"
        with self.assertRaises(ValueError):
            self.build()
        self.assertFalse(self.output.exists())

    def test_received_column_comes_from_quote_not_invented_paraphrase(self):
        item = self.data["decisions"][0]
        item["received"] = "Документ требует обмена бумажными оригиналами и завершения ЭДО."
        with self.assertRaisesRegex(ValueError, "received не может пересказывать"):
            self.build()
        self.assertFalse(self.output.exists())
        item["received"] = item["source"]["quote"]
        self.build()
        table = (self.output / "terms-review.md").read_text()
        self.assertEqual(table.count(item["source"]["quote"]), 1)
        self.assertNotIn("бумажными оригиналами", table)
        self.assertTrue(review.audit(self.output)["valid"])

    def stale_package(self, name):
        folder = self.base / name
        folder.mkdir()
        for file in ("manifest.json", "review-input.json", "terms-review.md"):
            (folder / file).write_text("{}")
        return folder / "terms-review.md"

    def test_current_source_cannot_point_to_unregistered_internal_review(self):
        stale = self.stale_package("internal-v1")
        self.state["checks"]["contract"]["source"] = f"Сверка: {stale.relative_to(self.base)}."
        self.build()
        self.save_state_plan()
        before = self.state_path.read_bytes()
        with self.assertRaisesRegex(ValueError, "checks.contract.source.*другую сверку"):
            self.check()
        self.assertEqual(before, self.state_path.read_bytes())

    def test_current_source_checks_absolute_markdown_paths_with_spaces(self):
        stale = self.stale_package("internal copy")
        self.state["checks"]["contract"]["source"] = f"[Сверка](<{stale}>)."
        self.build()
        self.save_state_plan()
        with self.assertRaisesRegex(ValueError, "другую сверку"):
            self.check()
        self.state["checks"]["contract"]["source"] = f"[Сверка](<{self.output / 'terms-review.md'}>)."
        self.save_state_plan()
        self.assertTrue(self.check()["state_checked"])

    def test_history_and_original_sources_remain_available(self):
        stale = self.stale_package("old-review")
        self.state["history"].append({"at": self.data["comparison_at"], "source": str(stale)})
        self.state["checks"]["contract"]["source"] = f"Полученный источник: {self.source}."
        self.build()
        self.save_state_plan()
        self.assertTrue(self.check()["state_checked"])

    def test_percent_encoded_hash_in_old_review_path_is_not_a_fragment(self):
        stale = self.stale_package("old#review")
        encoded = str(stale).replace("#", "%23")
        self.state["checks"]["contract"]["source"] = f"[Сверка](<{encoded}:12#условия>)."
        self.build()
        self.save_state_plan()
        with self.assertRaisesRegex(ValueError, "другую сверку"):
            self.check()

    def test_old_review_uri_query_cannot_hide_the_actual_local_path(self):
        stale = self.stale_package("old-review")
        encoded = str(stale).replace("terms-review", "terms%2Dreview")
        self.build()
        for suffix in ("?download=1", "?download=/preview", ":12?next=https://example.test/preview#условия"):
            with self.subTest(suffix=suffix):
                self.state["checks"]["contract"]["source"] = f"[Сверка](<{encoded}{suffix}>)"
                self.save_state_plan()
                before = self.state_path.read_bytes()
                with self.assertRaisesRegex(ValueError, "checks.contract.source.*другую сверку"):
                    self.check()
                self.assertEqual(self.state_path.read_bytes(), before)

    def test_current_review_query_is_not_reinterpreted_as_a_second_file_path(self):
        stale = self.stale_package("old-review")
        self.build()
        current = self.output / "terms-review.md"
        self.state["checks"]["contract"]["source"] = f"[Сверка](<{current}?download={stale}#условия>)"
        self.save_state_plan()
        self.assertTrue(self.check()["state_checked"])

    def test_old_review_link_titles_and_reference_links_do_not_hide_target(self):
        stale = self.stale_package("old-review")
        encoded = str(stale).replace("terms-review", "terms%2Dreview")
        self.build()
        sources = (f'[Сверка](<{encoded}> "Таблица / условия")',
                   f'[Сверка](<{encoded}?download=/preview> "Таблица / условия")',
                   f'[Сверка][r]\n\n[r]: <{encoded}:12?download=1#условия> "Таблица / условия"')
        for source in sources:
            with self.subTest(source=source):
                self.state["checks"]["contract"]["source"] = source
                self.save_state_plan()
                with self.assertRaisesRegex(ValueError, "другую сверку"):
                    self.check()

    def test_link_title_is_not_a_second_technical_file_reference(self):
        stale = self.stale_package("old-review")
        self.build()
        current = self.output / "terms-review.md"
        self.state["checks"]["contract"]["source"] = f'[Сверка](<{current}> "Сравнение с {stale}")'
        self.save_state_plan()
        self.assertTrue(self.check()["state_checked"])

    def test_encoded_review_uri_preserves_literal_question_hash_and_percent(self):
        stale = self.stale_package("old?review#%23")
        encoded = quote(str(stale), safe="/")
        self.state["checks"]["contract"]["source"] = f"[Сверка](<{encoded}:12?download=/preview#условия>)"
        self.build()
        self.save_state_plan()
        with self.assertRaisesRegex(ValueError, "другую сверку"):
            self.check()

    def test_bare_technical_review_paths_keep_literal_uri_characters(self):
        self.build()
        for name in ("old?review", "old#review", "old%23review"):
            with self.subTest(name=name):
                stale = self.stale_package(name)
                for source in (f"Сверка: {stale.relative_to(self.base)}.", f"Сверка: `{stale.relative_to(self.base)}`."):
                    self.state["checks"]["contract"]["source"] = source
                    self.save_state_plan()
                    with self.assertRaisesRegex(ValueError, "другую сверку"):
                        self.check()

    def test_technical_paths_that_look_like_emphasis_keep_their_literal_names(self):
        self.build()
        for name in ("_internal_", "__internal__", "**internal**"):
            stale = self.stale_package(name)
            for path in (stale.relative_to(self.base), stale):
                with self.subTest(path=path):
                    self.state["checks"]["contract"]["source"] = f"Сверка: {path}."
                    self.save_state_plan()
                    before = self.state_path.read_bytes()
                    with self.assertRaisesRegex(ValueError, "checks.contract.source.*другую сверку"):
                        self.check()
                    self.assertEqual(self.state_path.read_bytes(), before)

    def test_old_manifest_requires_new_package_without_rewriting_old_files(self):
        self.build()
        path = self.output / "manifest.json"
        data = json.loads(path.read_bytes())
        data["format"] = 4
        path.write_text(json.dumps(data))
        before = {p.name: p.read_bytes() for p in self.output.iterdir()}
        with self.assertRaisesRegex(ValueError, "нужен формат 5.*без изменений"):
            review.audit(self.output)
        self.assertEqual(before, {p.name: p.read_bytes() for p in self.output.iterdir()})

    def test_current_received_contract_is_not_its_own_previous_edition(self):
        self.state["documents"] = [{"path": self.source.name, "group": "contract", "status": "received",
                                    "current": True, "delivered_at": "2026-10-31T12:00:00+03:00",
                                    "sha256": review.sha256(self.source.read_bytes())}]
        self.build()
        self.save_state_plan()
        self.assertTrue(self.check()["state_checked"])

    def test_earlier_received_edition_is_still_a_previous_document(self):
        previous = copy.deepcopy(self.data["decisions"][0]["previous"])
        self.prior_document()
        self.data["decisions"][0]["previous"] = previous
        self.state["documents"][0]["status"] = "received"
        self.build()
        self.save_state_plan()
        with self.assertRaisesRegex(ValueError, "уже выданы документы"):
            self.check()


if __name__ == "__main__":
    unittest.main()
