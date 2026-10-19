import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from zipfile import ZipFile

from pypdf import PdfReader, PdfWriter
from pypdf.generic import ArrayObject, DictionaryObject, FloatObject, NameObject, TextStringObject

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/inspect_links.py"
spec = importlib.util.spec_from_file_location("inspect_links", SCRIPT)
links = importlib.util.module_from_spec(spec)
spec.loader.exec_module(links)


class LinkInspectionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)

    def pdf(self):
        path = self.base / "received.pdf"
        writer = PdfWriter()
        for targets in [["https://example.test/?a=1&b=2", "https://example.test/?a=1&b=2"],
                        ["https://example.test/?b=2&a=1"]]:
            page = writer.add_blank_page(300, 300)
            annotations = []
            for index, target in enumerate(targets):
                annotations.append(writer._add_object(DictionaryObject({
                    NameObject("/Type"): NameObject("/Annot"), NameObject("/Subtype"): NameObject("/Link"),
                    NameObject("/Rect"): ArrayObject([FloatObject(n) for n in [10, 20 + index, 40, 30 + index]]),
                    NameObject("/A"): DictionaryObject({NameObject("/S"): NameObject("/URI"), NameObject("/URI"): TextStringObject(target)})})))
            page[NameObject("/Annots")] = ArrayObject(annotations)
        with path.open("wb") as file:
            writer.write(file)
        return path

    def docx(self, extra=""):
        path = self.base / "received.docx"
        with ZipFile(path, "w") as archive:
            archive.writestr("word/document.xml", '''<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><w:body>
<w:p><w:hyperlink r:id="r1"><w:r><w:t>При</w:t></w:r><w:r><w:t>мер</w:t></w:r></w:hyperlink></w:p>
<w:p><w:r><w:t>https://plain.example.test/</w:t></w:r><w:hyperlink r:id="r1"><w:r><w:t>OpenAI</w:t><w:tab/><w:t>тарифы</w:t><w:br/><w:t>подробнее</w:t></w:r></w:hyperlink><w:hyperlink r:id="r2"><w:r><w:t>Пример</w:t></w:r></w:hyperlink></w:p>
</w:body></w:document>'''.replace("</w:body>", extra + "</w:body>"))
            archive.writestr("word/_rels/document.xml.rels", '''<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
<Relationship Id="r1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/hyperlink" Target="https://pricing.example.test/?a=1&amp;b=2" TargetMode="External"/>
<Relationship Id="r2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/hyperlink" Target="https://product.example.test/?a=1&amp;b=2" TargetMode="External"/>
</Relationships>''')
        return path

    def test_pdf_anchor_hint_matches_the_actual_annotation_in_rotated_and_cropped_pages(self):
        import pymupdf
        for rotation in (0, 90):
            with self.subTest(rotation=rotation):
                path = self.base / f'anchors-{rotation}.pdf'
                with pymupdf.open() as document:
                    page = document.new_page(width=300, height=300)
                    page.insert_text((40, 80), 'Product')
                    rect = page.search_for('Product')[0]
                    page.insert_link({'kind': pymupdf.LINK_URI, 'from': rect,
                                      'uri': 'https://pricing.example.test/'})
                    page.set_cropbox(pymupdf.Rect(10, 10, 290, 290))
                    page.set_rotation(rotation)
                    document.save(path)
                original = path.read_bytes()
                result = links.inspect_links(path)
                self.assertEqual(result['kind'], 'link_inspection')
                self.assertEqual(len(result['links']), 1)
                self.assertEqual(result['links'][0]['anchor_hint'].strip(), 'Product')
                self.assertEqual(result['links'][0]['target'], 'https://pricing.example.test/')
                self.assertEqual(path.read_bytes(), original)

    def test_pdf_without_text_layer_does_not_invent_an_anchor(self):
        result = links.inspect_links(self.pdf())
        self.assertTrue(all(item['anchor_hint'] is None for item in result['links']))

    def test_pdf_preserves_duplicates_parameters_and_locations(self):
        result = links.inspect_links(self.pdf())
        actual = result["links"]
        self.assertEqual([row["page"] for row in actual], [1, 1, 2])
        self.assertEqual([row["annotation_index"] for row in actual], [1, 2, 1])
        self.assertEqual([row["order"] for row in actual], [1, 2, 3])
        self.assertEqual(actual[0]["rect"], [10, 20, 40, 30])
        self.assertEqual(actual[0]["target"], actual[1]["target"])
        self.assertNotEqual(actual[0]["target"], actual[2]["target"])

    def test_docx_visible_anchor_is_separate_from_target(self):
        result = links.inspect_links(self.docx())
        actual = result["links"]
        self.assertEqual(len(actual), 3)  # Plain URL is not a relationship.
        self.assertEqual(actual[0]["anchor"], "Пример")
        self.assertEqual(actual[0]["target"], "https://pricing.example.test/?a=1&b=2")
        self.assertEqual(actual[1]["anchor"], "OpenAI\tтарифы\nподробнее")
        self.assertEqual(actual[1]["paragraph_index"], 2)
        self.assertEqual(actual[2]["hyperlink_index"], 2)
        self.assertEqual(actual[2]["relationship_id"], "r2")
        self.assertEqual(actual[2]["target_mode"], "External")

    def test_docx_target_requires_external_hyperlink_relationship(self):
        expected = ["https://pricing.example.test/?a=1&b=2"] * 2 + ["https://product.example.test/?a=1&b=2"]
        hyperlink_type = "http://schemas.openxmlformats.org/officeDocument/2006/relationships/hyperlink"
        for field, value in (("Type", None), ("Type", hyperlink_type.replace("hyperlink", "image")),
                             ("TargetMode", None), ("TargetMode", "Internal"), ("TargetMode", "External")):
            with self.subTest(field=field, value=value):
                path = self.docx()
                with ZipFile(path) as archive:
                    parts = {name: archive.read(name) for name in archive.namelist()}
                rel_path = "word/_rels/document.xml.rels"
                root = links.ET.fromstring(parts[rel_path])
                relationship = next(node for node in root if node.get("Id") == "r2")
                if value is None:
                    relationship.attrib.pop(field)
                else:
                    relationship.set(field, value)
                parts[rel_path] = links.ET.tostring(root)
                with ZipFile(path, "w") as archive:
                    for name, raw in parts.items():
                        archive.writestr(name, raw)
                before = path.read_bytes()
                result = links.inspect_links(path, expected)
                valid = value == "External"
                self.assertEqual(result["links"][-1]["target"], expected[-1] if valid else None)
                self.assertEqual(result["comparison"]["ordered_lists_equal"], valid)
                self.assertEqual(len(result["warnings"]), 0 if valid else 1)
                if not valid:
                    self.assertIn("r2", result["warnings"][0])
                    self.assertIn("Type", result["warnings"][0])
                    self.assertIn("TargetMode", result["warnings"][0])
                self.assertEqual(path.read_bytes(), before)

    def test_docx_internal_bookmark_does_not_require_external_relationship(self):
        path = self.docx('<w:p><w:bookmarkStart w:id="1" w:name="section"/><w:bookmarkEnd w:id="1"/>'
                         '<w:hyperlink w:anchor="section"><w:r><w:t>Раздел</w:t></w:r></w:hyperlink></w:p>')
        result = links.inspect_links(path)
        self.assertEqual(result["links"][-1]["internal_anchor"], "section")
        self.assertIsNone(result["links"][-1]["target"])
        self.assertEqual(result["warnings"], [])

    def test_counts_expose_swapped_anchor_without_declaring_document_invalid(self):
        expected = ["https://product.example.test/?a=1&b=2"] * 2 + ["https://pricing.example.test/?a=1&b=2"]
        comparison = links.inspect_links(self.docx(), expected)["comparison"]
        self.assertEqual(comparison["expected_count"], 3)
        self.assertEqual(comparison["actual_count"], 3)
        self.assertEqual([row["difference"] for row in comparison["differences"]], [-1, 1])
        self.assertFalse(comparison["ordered_lists_equal"])
        self.assertIn("не автоматическое доказательство", comparison["note"])

    def test_docx_hyperlink_fields_warn_even_when_relationship_urls_match(self):
        expected = ["https://pricing.example.test/?a=1&b=2"] * 2 + ["https://product.example.test/?a=1&b=2"]
        for field in (
                '<w:fldSimple w:instr=\' HYPERLINK "https://hidden.example.test/" \'>\n'
                '<w:r><w:t>Другой адрес</w:t></w:r></w:fldSimple>',
                '<w:r><w:fldChar w:fldCharType="begin"/><w:instrText> HYPERLINK "https://hidden.example.test/" </w:instrText>'
                '<w:fldChar w:fldCharType="separate"/><w:t>Другой адрес</w:t><w:fldChar w:fldCharType="end"/></w:r>',
                '<w:r><w:fldChar w:fldCharType="begin"/><w:instrText> HYPER</w:instrText></w:r>'
                '<w:r><w:instrText>LINK "https://hidden.example.test/" </w:instrText>'
                '<w:fldChar w:fldCharType="separate"/><w:t>Другой адрес</w:t><w:fldChar w:fldCharType="end"/></w:r>'):
            for part in ("word/document.xml", "word/header1.xml"):
                with self.subTest(field=field, part=part):
                    path = self.docx("<w:p>" + field + "</w:p>" if part == "word/document.xml" else "")
                    if part != "word/document.xml":
                        with ZipFile(path, "a") as archive:
                            archive.writestr(part, '<w:hdr xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
                                             '<w:p>' + field + '</w:p></w:hdr>')
                    before = path.read_bytes()
                    result = links.inspect_links(path, expected)
                    self.assertTrue(result["comparison"]["ordered_lists_equal"])
                    self.assertEqual(result["comparison"]["differences"], [])
                    self.assertEqual(len(result["warnings"]), 1)
                    self.assertIn(part, result["warnings"][0])
                    self.assertIn("HYPERLINK", result["warnings"][0])
                    self.assertIn("resolution", result["warnings"][0])
                    self.assertEqual(path.read_bytes(), before)

    def test_docx_other_fields_and_separate_instructions_do_not_warn(self):
        fields = '<w:fldSimple w:instr=" PAGE "><w:r><w:t>1</w:t></w:r></w:fldSimple>'
        for instruction in (' MERGEFIELD "HYPERLINK" ', ' HYPER', 'LINK "https://example.test/" ', ' HYPERLINKED '):
            fields += ('<w:r><w:fldChar w:fldCharType="begin"/><w:instrText>' + instruction
                       + '</w:instrText><w:fldChar w:fldCharType="separate"/><w:t>Результат</w:t>'
                       '<w:fldChar w:fldCharType="end"/></w:r>')
        result = links.inspect_links(self.docx("<w:p>" + fields + "</w:p>"))
        self.assertEqual(len(result["links"]), 3)
        self.assertEqual(result["warnings"], [])

    def test_docx_drawingml_links_warn_in_every_word_part(self):
        expected = ["https://pricing.example.test/?a=1&b=2"] * 2 + ["https://product.example.test/?a=1&b=2"]
        for tag in ("hlinkClick", "hlinkHover", "no_link"):
            drawing = ('<w:p><w:r><w:drawing xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" '
                       'xmlns:wp="http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing" '
                       'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
                       '<wp:inline><wp:docPr id="1" name="Image">'
                       + (f'<a:{tag} r:id="r1"/>' if tag != "no_link" else '')
                       + '</wp:docPr></wp:inline></w:drawing></w:r></w:p>')
            for part in ("word/document.xml", "word/header1.xml", "word/drawings/drawing1.xml"):
                with self.subTest(tag=tag, part=part):
                    path = self.docx(drawing if part == "word/document.xml" else "")
                    if part != "word/document.xml":
                        with ZipFile(path, "a") as archive:
                            archive.writestr(part, '<w:hdr xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
                                             + drawing + '</w:hdr>')
                            rel_path = str(Path(part).parent / "_rels" / (Path(part).name + ".rels"))
                            archive.writestr(rel_path,
                                '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                                '<Relationship Id="r1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/hyperlink" '
                                'Target="https://hidden.example.test/" TargetMode="External"/></Relationships>')
                    before = path.read_bytes()
                    result = links.inspect_links(path, expected)
                    self.assertTrue(result["comparison"]["ordered_lists_equal"])
                    self.assertEqual(result["comparison"]["differences"], [])
                    self.assertEqual(len(result["warnings"]), int(tag != "no_link"))
                    if tag != "no_link":
                        self.assertIn(part, result["warnings"][0])
                        self.assertIn("DrawingML", result["warnings"][0])
                        self.assertIn("resolution", result["warnings"][0])
                    self.assertEqual(path.read_bytes(), before)

    def test_docx_hyperlink_field_requires_registered_resolution(self):
        sys.path.insert(0, str(ROOT / "scripts"))
        import handoff

        field = '<w:p><w:fldSimple w:instr=\' HYPERLINK "https://hidden.example.test/" \'>\n'
        path = self.docx(field + '<w:r><w:t>Другой адрес</w:t></w:r></w:fldSimple></w:p>')
        expected = ["https://pricing.example.test/?a=1&b=2"] * 2 + ["https://product.example.test/?a=1&b=2"]
        result = links.inspect_links(path, expected)
        report = self.base / "links.json"
        report.write_text(json.dumps(result), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "resolution.result=accepted"):
            handoff.check_link_comparison(report, {}, expected)
        review = self.base / "review.md"
        review.write_text("Поле HYPERLINK отдельно просмотрено: адрес и видимый якорь проверены.", encoding="utf-8")
        result["resolution"] = {"result": "accepted", "notes": "Поле проверено отдельно", "source": str(review)}
        report.write_text(json.dumps(result), encoding="utf-8")
        handoff.check_link_comparison(report, {
            review.resolve(): {"group": "review", "current": True, "sha256": handoff.sha(review)}}, expected)

    def test_docx_drawingml_warning_requires_resolution_despite_equal_urls(self):
        sys.path.insert(0, str(ROOT / "scripts"))
        import handoff

        path = self.docx('<w:p><w:r><w:drawing xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main">'
                         '<a:hlinkClick r:id="r1"/></w:drawing></w:r></w:p>')
        expected = ["https://pricing.example.test/?a=1&b=2"] * 2 + ["https://product.example.test/?a=1&b=2"]
        result = links.inspect_links(path, expected)
        self.assertTrue(result["comparison"]["ordered_lists_equal"])
        report = self.base / "drawing-links.json"
        report.write_text(json.dumps(result), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "resolution.result=accepted"):
            handoff.check_link_comparison(report, {}, expected)

    def test_docx_textbox_links_count_once_in_xml_order_with_nearest_paragraph(self):
        path = self.base / "textbox.docx"
        with ZipFile(path, "w") as archive:
            archive.writestr("word/document.xml", '''<w:document
 xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"
 xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"
 xmlns:v="urn:schemas-microsoft-com:vml"><w:body>
 <w:p><w:hyperlink r:id="before"><w:r><w:t>До</w:t></w:r></w:hyperlink>
 <w:r><w:pict><v:shape><v:textbox><w:txbxContent><w:p>
 <w:hyperlink r:id="inside"><w:r><w:t>Внутри 1</w:t></w:r></w:hyperlink>
 <w:hyperlink r:id="inside"><w:r><w:t>Внутри 2</w:t></w:r></w:hyperlink>
 </w:p></w:txbxContent></v:textbox></v:shape></w:pict></w:r>
 <w:hyperlink r:id="after"><w:r><w:t>После</w:t></w:r></w:hyperlink></w:p>
 <w:p><w:hyperlink r:id="last"><w:r><w:t>Следующий абзац</w:t></w:r></w:hyperlink></w:p>
 </w:body></w:document>''')
            relationships = ''.join(
                f'<Relationship Id="{name}" '
                'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/hyperlink" '
                f'Target="https://example.test/{name}" TargetMode="External"/>'
                for name in ("before", "inside", "after", "last"))
            archive.writestr("word/_rels/document.xml.rels",
                            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                            + relationships + '</Relationships>')
        expected = [f"https://example.test/{name}" for name in ("before", "inside", "inside", "after", "last")]
        original = path.read_bytes()
        result = links.inspect_links(path, expected)
        actual = result["links"]
        self.assertEqual([row["anchor"] for row in actual], ["До", "Внутри 1", "Внутри 2", "После", "Следующий абзац"])
        self.assertEqual([row["order"] for row in actual], [1, 2, 3, 4, 5])
        self.assertEqual([row["paragraph_index"] for row in actual], [1, 2, 2, 1, 3])
        self.assertEqual([row["hyperlink_index"] for row in actual], [1, 1, 2, 2, 1])
        self.assertTrue(result["comparison"]["ordered_lists_equal"])
        self.assertEqual(result["comparison"]["differences"], [])
        self.assertEqual(result["warnings"], [])
        self.assertEqual(path.read_bytes(), original)

    def test_expected_list_keeps_order_duplicates_and_exact_parameters(self):
        path = self.base / "expected.json"
        expected = ["https://example.test/?b=2&a=1", "https://example.test/?a=1&b=2"] * 2
        path.write_text(json.dumps(expected), encoding="utf-8")
        self.assertEqual(links.expected_urls(path), expected)
        for invalid in [{"links": expected}, [["anchor", "неизвестный переход"]], ["https://bad example.test/"]]:
            path.write_text(json.dumps(invalid), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "Карта ссылок"):
                links.expected_urls(path)

    def test_readonly_cli_output_and_no_overwrite(self):
        path = self.docx()
        original = path.read_bytes()
        output = self.base / "inspection.json"
        command = [sys.executable, "-B", str(SCRIPT), "--input", str(path)]
        run = subprocess.run(command, text=True, capture_output=True)
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertEqual(json.loads(run.stdout)["source"]["sha256"], hashlib.sha256(original).hexdigest())
        run = subprocess.run(command + ["--output", str(output)], text=True, capture_output=True)
        self.assertEqual(run.returncode, 0, run.stderr)
        saved = output.read_bytes()
        for protected in [path, output]:
            run = subprocess.run(command + ["--output", str(protected)], text=True, capture_output=True)
            self.assertEqual(run.returncode, 2)
        self.assertEqual(path.read_bytes(), original)
        self.assertEqual(output.read_bytes(), saved)

    def test_pdf_inspection_does_not_change_bytes(self):
        path = self.pdf()
        before = path.read_bytes()
        links.inspect_links(path, [])
        self.assertEqual(path.read_bytes(), before)

    def test_pdf_indirect_action_and_malformed_annotations_are_reported(self):
        path = self.pdf()
        writer = PdfWriter()
        writer.clone_document_from_reader(PdfReader(path))
        page = writer.pages[0]
        first = page["/Annots"][0].get_object()
        first[NameObject("/A")] = writer._add_object(first["/A"])
        first[NameObject("/Rect")] = ArrayObject([FloatObject(1)])
        page["/Annots"].append(TextStringObject("broken annotation"))
        page["/Annots"].append(DictionaryObject({
            NameObject("/Subtype"): NameObject("/Link"),
            NameObject("/A"): DictionaryObject({NameObject("/S"): NameObject("/URI")})}))
        with path.open("wb") as file:
            writer.write(file)
        result = links.inspect_links(path)
        self.assertEqual(len(result["links"]), 3)
        self.assertEqual(result["links"][0]["target"], "https://example.test/?a=1&b=2")
        self.assertIsNone(result["links"][0]["rect"])
        self.assertEqual(len(result["warnings"]), 3)
        self.assertTrue(any("не содержит адреса" in item for item in result["warnings"]))

    def test_pdf_unparsed_actions_require_resolution_even_when_urls_match(self):
        sys.path.insert(0, str(ROOT / "scripts"))
        import handoff

        expected = ["https://example.test/?a=1&b=2"] * 2 + ["https://example.test/?b=2&a=1"]
        for kind in ("/GoToR", "/Launch", "/JavaScript", "/GoTo", "/Next", "/Next-array"):
            with self.subTest(kind=kind):
                path = self.pdf()
                writer = PdfWriter()
                writer.clone_document_from_reader(PdfReader(path))
                page = writer.pages[0]
                action = DictionaryObject({NameObject("/S"): NameObject(kind if not kind.startswith("/Next") else "/Launch"),
                                           NameObject("/F"): TextStringObject("https://hidden.example.test/remote.pdf"),
                                           NameObject("/D"): TextStringObject("section"),
                                           NameObject("/JS"): TextStringObject("app.alert('extra action')")})
                if kind.startswith("/Next"):
                    first = page["/Annots"][0].get_object()["/A"]
                    next_action = writer._add_object(action)
                    first[NameObject("/Next")] = ArrayObject([next_action]) if kind == "/Next-array" else next_action
                else:
                    page["/Annots"].append(writer._add_object(DictionaryObject({
                        NameObject("/Subtype"): NameObject("/Link"), NameObject("/A"): action,
                        NameObject("/Rect"): ArrayObject([FloatObject(n) for n in [50, 50, 90, 80]])})))
                with path.open("wb") as file:
                    writer.write(file)
                before = path.read_bytes()
                result = links.inspect_links(path, expected)
                self.assertTrue(result["comparison"]["ordered_lists_equal"])
                self.assertTrue(any(("/Next" if kind.startswith("/Next") else kind) in warning for warning in result["warnings"]))
                report = self.base / "action-links.json"
                report.write_text(json.dumps(result), encoding="utf-8")
                with self.assertRaisesRegex(ValueError, "resolution.result=accepted"):
                    handoff.check_link_comparison(report, {}, expected)
                self.assertEqual(path.read_bytes(), before)

    def test_pdf_additional_annotation_actions_require_resolution(self):
        sys.path.insert(0, str(ROOT / "scripts"))
        import handoff

        expected = ["https://example.test/?a=1&b=2"] * 2 + ["https://example.test/?b=2&a=1"]
        self.assertEqual(links.inspect_links(self.pdf(), expected)["warnings"], [])
        for subtype in ("/Link", "/Text"):
            for kind in ("/Launch", "/GoToR"):
                with self.subTest(subtype=subtype, kind=kind):
                    path = self.pdf()
                    writer = PdfWriter()
                    writer.clone_document_from_reader(PdfReader(path))
                    page = writer.pages[0]
                    if subtype == "/Link":
                        annotation = page["/Annots"][0].get_object()
                    else:
                        annotation = DictionaryObject({NameObject("/Subtype"): NameObject(subtype),
                            NameObject("/Rect"): ArrayObject([FloatObject(n) for n in [50, 50, 90, 80]])})
                        page["/Annots"].append(writer._add_object(annotation))
                    action = DictionaryObject({NameObject("/S"): NameObject(kind),
                        NameObject("/F"): TextStringObject("https://hidden.example.test/remote.pdf"),
                        NameObject("/D"): TextStringObject("section")})
                    annotation[NameObject("/AA")] = writer._add_object(DictionaryObject({NameObject("/U"): action}))
                    with path.open("wb") as file:
                        writer.write(file)
                    before = path.read_bytes()
                    result = links.inspect_links(path, expected)
                    self.assertTrue(result["comparison"]["ordered_lists_equal"])
                    self.assertEqual(len(result["warnings"]), 1)
                    self.assertIn("/AA", result["warnings"][0])
                    report = self.base / "additional-action-links.json"
                    report.write_text(json.dumps(result), encoding="utf-8")
                    with self.assertRaisesRegex(ValueError, "resolution.result=accepted"):
                        handoff.check_link_comparison(report, {}, expected)
                    self.assertEqual(path.read_bytes(), before)

    def test_pdf_non_link_primary_actions_require_resolution(self):
        sys.path.insert(0, str(ROOT / "scripts"))
        import handoff

        expected = ["https://example.test/?a=1&b=2"] * 2 + ["https://example.test/?b=2&a=1"]
        for subtype in ("/Widget", "/Text"):
            for has_action in (False, True):
                with self.subTest(subtype=subtype, has_action=has_action):
                    path = self.pdf()
                    writer = PdfWriter()
                    writer.clone_document_from_reader(PdfReader(path))
                    annotation = DictionaryObject({NameObject("/Subtype"): NameObject(subtype),
                        NameObject("/Rect"): ArrayObject([FloatObject(n) for n in [50, 50, 90, 80]])})
                    if subtype == "/Widget":
                        annotation[NameObject("/FT")] = NameObject("/Btn")
                        annotation[NameObject("/T")] = TextStringObject("button")
                    if has_action:
                        annotation[NameObject("/A")] = writer._add_object(DictionaryObject({
                            NameObject("/S"): NameObject("/URI"),
                            NameObject("/URI"): TextStringObject("https://hidden.example.test/")}))
                    writer.pages[0]["/Annots"].append(writer._add_object(annotation))
                    with path.open("wb") as file:
                        writer.write(file)
                    before = path.read_bytes()
                    result = links.inspect_links(path, expected)
                    self.assertTrue(result["comparison"]["ordered_lists_equal"])
                    report = self.base / "non-link-action-links.json"
                    report.write_text(json.dumps(result), encoding="utf-8")
                    if has_action:
                        self.assertEqual(len(result["warnings"]), 1)
                        self.assertIn("/A", result["warnings"][0])
                        self.assertIn(subtype, result["warnings"][0])
                        with self.assertRaisesRegex(ValueError, "resolution.result=accepted"):
                            handoff.check_link_comparison(report, {}, expected)
                    else:
                        self.assertEqual(result["warnings"], [])
                    self.assertEqual(path.read_bytes(), before)


if __name__ == "__main__":
    unittest.main()
