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
                    self.assertEqual(path.read_bytes(), before)

    def test_docx_vml_href_warns_in_every_word_part(self):
        url = "https://example.test/product"
        for tag, attribute in (("shape", "href"), ("image", "o:href"), ("imagedata", "href")):
            for part in ("word/document.xml", "word/header1.xml", "word/drawings/vmlDrawing1.vml"):
                for with_links in (False, True):
                    with self.subTest(tag=tag, attribute=attribute, part=part, with_links=with_links):
                        path = self.base / "vml.docx"
                        ordinary = ('<w:p><w:hyperlink r:id="r1"><w:r><w:t>Продукт</w:t></w:r>'
                                    '</w:hyperlink></w:p>') if with_links else ''
                        vml = (f'<w:p><w:r><w:pict><v:{tag} {attribute}="https://hidden.example.test/"/>'
                               '</w:pict></w:r></w:p>')
                        start = ('<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main" '
                                 'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships" '
                                 'xmlns:v="urn:schemas-microsoft-com:vml" '
                                 'xmlns:o="urn:schemas-microsoft-com:office:office"><w:body>')
                        end = '</w:body></w:document>'
                        with ZipFile(path, 'w') as archive:
                            archive.writestr('word/document.xml', start + ordinary
                                             + (vml if part == 'word/document.xml' else '') + end)
                            if part != 'word/document.xml':
                                archive.writestr(part, start + vml + end)
                            archive.writestr('word/_rels/document.xml.rels',
                                '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                                '<Relationship Id="r1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/hyperlink" '
                                f'Target="{url}" TargetMode="External"/></Relationships>')
                        expected = [url] if with_links else []
                        before = path.read_bytes()
                        result = links.inspect_links(path, expected)
                        self.assertTrue(result['comparison']['ordered_lists_equal'])
                        self.assertEqual([row['target'] for row in result['links']], expected)
                        self.assertEqual(len(result['warnings']), 1)
                        self.assertIn(part, result['warnings'][0])
                        self.assertIn('VML (href)', result['warnings'][0])
                        self.assertEqual(path.read_bytes(), before)

    def test_docx_vml_without_href_does_not_warn(self):
        path = self.docx('<w:p><w:r><w:pict xmlns:v="urn:schemas-microsoft-com:vml">'
                         '<v:shape><v:imagedata src="image.png"/></v:shape>'
                         '</w:pict></w:r></w:p>')
        before = path.read_bytes()
        result = links.inspect_links(path)
        self.assertEqual(len(result['links']), 3)
        self.assertEqual(result['warnings'], [])
        self.assertEqual(path.read_bytes(), before)

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

    def test_pdf_unparsed_actions_warn_even_when_urls_match(self):
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
                self.assertEqual(path.read_bytes(), before)

    def test_pdf_additional_annotation_actions_warn(self):
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
                    self.assertEqual(path.read_bytes(), before)

    def test_pdf_non_link_primary_actions_warn(self):
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
                    if has_action:
                        self.assertEqual(len(result["warnings"]), 1)
                        self.assertIn("/A", result["warnings"][0])
                        self.assertIn(subtype, result["warnings"][0])
                    else:
                        self.assertEqual(result["warnings"], [])
                    self.assertEqual(path.read_bytes(), before)

    def test_pdf_document_and_page_actions_warn(self):
        for kind in ("/OpenAction", "catalog /AA", "page /AA", "/Names/JavaScript"):
            for with_links in (False, True):
                with self.subTest(kind=kind, with_links=with_links):
                    writer = PdfWriter()
                    if with_links:
                        path = self.pdf()
                        writer.clone_document_from_reader(PdfReader(path))
                        expected = ["https://example.test/?a=1&b=2"] * 2 + ["https://example.test/?b=2&a=1"]
                    else:
                        path = self.base / "empty.pdf"
                        writer.add_blank_page(300, 300)
                        expected = []
                    action = DictionaryObject({NameObject("/S"): NameObject("/URI"),
                        NameObject("/URI"): TextStringObject("https://hidden.example.test/")})
                    if kind in ("catalog /AA", "/Names/JavaScript"):
                        action = DictionaryObject({NameObject("/S"): NameObject("/JavaScript"),
                            NameObject("/JS"): TextStringObject("app.alert('extra action')")})
                    if kind == "/OpenAction":
                        writer.root_object[NameObject(kind)] = action
                    elif kind in ("catalog /AA", "page /AA"):
                        owner = writer.root_object if kind.startswith("catalog") else writer.pages[0]
                        event = "/WC" if kind.startswith("catalog") else "/O"
                        owner[NameObject("/AA")] = writer._add_object(DictionaryObject({NameObject(event): action}))
                    else:
                        scripts = DictionaryObject({NameObject("/Names"): ArrayObject([
                            TextStringObject("extra-action"), action])})
                        names = DictionaryObject({NameObject("/JavaScript"): writer._add_object(scripts)})
                        writer.root_object[NameObject("/Names")] = writer._add_object(names)
                    with path.open("wb") as file:
                        writer.write(file)
                    before = path.read_bytes()
                    result = links.inspect_links(path, expected)
                    self.assertEqual([row["target"] for row in result["links"]], expected)
                    self.assertTrue(result["comparison"]["ordered_lists_equal"])
                    self.assertEqual(len(result["warnings"]), 1)
                    self.assertIn("/AA" if kind.endswith("/AA") else kind, result["warnings"][0])
                    self.assertEqual(path.read_bytes(), before)

    def test_empty_pdf_without_actions_has_no_warnings(self):
        sys.path.insert(0, str(ROOT / "scripts"))
        import handoff

        path = self.base / "empty.pdf"
        writer = PdfWriter()
        writer.add_blank_page(300, 300)
        with path.open("wb") as file:
            writer.write(file)
        before = path.read_bytes()
        result = links.inspect_links(path, [])
        self.assertEqual(result["links"], [])
        self.assertEqual(result["warnings"], [])
        self.assertTrue(result["comparison"]["ordered_lists_equal"])
        report = self.base / "empty-links.json"
        report.write_text(json.dumps(result), encoding="utf-8")
        handoff.check_link_comparison(report, [])
        self.assertEqual(path.read_bytes(), before)


class DocxAlternativeFormatTests(unittest.TestCase):
    setUp = LinkInspectionTests.setUp

    def imported_docx(self, *, with_links=True, element=True, relationship=True,
                      external=False, extension='html', part='word/document.xml',
                      rel_path=None, relationship_type=None):
        path = self.base / 'imported.docx'
        url = 'https://example.test/product'
        ordinary = ('<w:p><w:hyperlink r:id="r1"><w:r><w:t>Продукт</w:t></w:r>'
                    '</w:hyperlink></w:p>') if with_links else ''
        chunk = '<w:altChunk r:id="import1"/>' if element else ''
        start = ('<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main" '
                 'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><w:body>')
        end = '</w:body></w:document>'
        parts = {'word/document.xml': start + ordinary + (chunk if part == 'word/document.xml' else '') + end}
        if part != 'word/document.xml':
            parts[part] = start + chunk + end
        rels = {'word/_rels/document.xml.rels': (
            '<Relationship Id="r1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/hyperlink" '
            f'Target="{url}" TargetMode="External"/>')}
        if relationship:
            rel_path = rel_path or str(Path(part).parent / '_rels' / (Path(part).name + '.rels'))
            relationship_type = relationship_type or 'http://schemas.openxmlformats.org/officeDocument/2006/relationships/aFChunk'
            target = f'https://remote.example.test/import.{extension}' if external else f'import.{extension}'
            mode = ' TargetMode="External"' if external else ''
            rels[rel_path] = rels.get(rel_path, '') + (
                f'<Relationship Id="import1" Type="{relationship_type}" Target="{target}"{mode}/>')
            if not external:
                parts[str(Path(part).parent / ('import.' + extension))] = (
                    '<html><body><a href="https://hidden.example.test/">Дополнительный переход</a></body></html>'
                    if extension == 'html' else
                    'MIME-Version: 1.0\r\nContent-Type: text/html\r\n\r\n'
                    '<a href="https://hidden.example.test/">Дополнительный переход</a>')
        with ZipFile(path, 'w') as archive:
            for name, content in parts.items():
                archive.writestr(name, content)
            for name, content in rels.items():
                archive.writestr(name, '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                                 + content + '</Relationships>')
        return path, ([{'anchor': 'Продукт', 'url': url}] if with_links else [])

    def assert_import_warning(self, path, expected_map):
        original = path.read_bytes()
        expected = [row['url'] for row in expected_map]
        result = links.inspect_links(path, expected)
        self.assertTrue(result['comparison']['ordered_lists_equal'])
        self.assertEqual([row['target'] for row in result['links']], expected)
        self.assertTrue(result['warnings'])
        self.assertEqual(path.read_bytes(), original)
        return result

    def test_internal_and_external_imports_warn_even_with_matching_or_empty_urls(self):
        for extension in ('html', 'mht'):
            for external in (False, True):
                for with_links in (False, True):
                    with self.subTest(extension=extension, external=external, with_links=with_links):
                        path, expected = self.imported_docx(extension=extension, external=external, with_links=with_links)
                        result = self.assert_import_warning(path, expected)
                        self.assertTrue(any('w:altChunk' in warning for warning in result['warnings']))
                        self.assertTrue(any('aFChunk' in warning and 'import1' in warning for warning in result['warnings']))
                        if external:
                            self.assertTrue(any('TargetMode=External' in warning for warning in result['warnings']))

    def test_element_without_relationship_still_warns_in_body_and_header(self):
        for part in ('word/document.xml', 'word/header1.xml'):
            with self.subTest(part=part):
                path, expected = self.imported_docx(relationship=False, part=part)
                result = self.assert_import_warning(path, expected)
                self.assertEqual(len(result['warnings']), 1)
                self.assertIn(part, result['warnings'][0])
                self.assertIn('w:altChunk', result['warnings'][0])

    def test_import_relationship_without_element_warns_in_any_relationship_part(self):
        for rel_path in ('word/_rels/document.xml.rels', 'word/_rels/header1.xml.rels',
                         'customXml/_rels/item1.xml.rels'):
            for prefix in ('http://schemas.openxmlformats.org/officeDocument/2006/relationships/',
                           'http://purl.oclc.org/ooxml/officeDocument/relationships/'):
                with self.subTest(rel_path=rel_path, prefix=prefix):
                    path, expected = self.imported_docx(element=False, rel_path=rel_path,
                                                        relationship_type=prefix + 'aFChunk')
                    result = self.assert_import_warning(path, expected)
                    self.assertEqual(len(result['warnings']), 1)
                    self.assertIn(rel_path, result['warnings'][0])
                    self.assertIn('aFChunk', result['warnings'][0])

    def test_plain_docx_has_no_import_warning(self):
        sys.path.insert(0, str(ROOT / 'scripts'))
        import handoff
        path, expected_map = self.imported_docx(element=False, relationship=False)
        expected = [row['url'] for row in expected_map]
        result = links.inspect_links(path, expected)
        self.assertEqual(result['warnings'], [])
        report = self.base / 'plain-docx-links.json'
        report.write_text(json.dumps(result), encoding='utf-8')
        handoff.check_link_comparison(report, expected, expected_map)


class StrictDocxLinkTests(unittest.TestCase):
    setUp = LinkInspectionTests.setUp
    docx = LinkInspectionTests.docx

    def as_strict(self, path, strict_package=False):
        replacements = {
            'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"':
                'xmlns:w="http://purl.oclc.org/ooxml/wordprocessingml/main"',
            'xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main"':
                'xmlns:a="http://purl.oclc.org/ooxml/drawingml/main"',
            'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"':
                'xmlns:r="http://purl.oclc.org/ooxml/officeDocument/relationships"',
            'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/':
                'Type="http://purl.oclc.org/ooxml/officeDocument/relationships/',
        }
        if strict_package:
            replacements['xmlns="http://schemas.openxmlformats.org/package/2006/relationships"'] = (
                'xmlns="http://purl.oclc.org/ooxml/package/relationships"')
        with ZipFile(path) as archive:
            parts = {name: archive.read(name) for name in archive.namelist()}
        with ZipFile(path, 'w') as archive:
            for name, raw in parts.items():
                if name.endswith(('.xml', '.rels', '.vml')):
                    for old, new in replacements.items():
                        raw = raw.replace(old.encode(), new.encode())
                archive.writestr(name, raw)

    def test_strict_links_match_transitional_anchors_locations_and_duplicate_urls(self):
        expected = ['https://pricing.example.test/?a=1&b=2'] * 2 + ['https://product.example.test/?a=1&b=2']
        for strict_package in (False, True):
            with self.subTest(strict_package=strict_package):
                path = self.docx()
                transitional = links.inspect_links(path, expected)
                self.as_strict(path, strict_package)
                before = path.read_bytes()
                strict = links.inspect_links(path, expected)
                for key in ('links', 'warnings', 'comparison'):
                    self.assertEqual(strict[key], transitional[key])
                self.assertEqual(strict['links'][1]['anchor'], 'OpenAI\tтарифы\nподробнее')
                self.assertEqual([row['target'] for row in strict['links']], expected)
                self.assertEqual(path.read_bytes(), before)

    def test_strict_external_links_differ_from_an_empty_expected_map(self):
        path = self.docx()
        self.as_strict(path)
        result = links.inspect_links(path, [])
        self.assertEqual(len(result['links']), 3)
        self.assertFalse(result['comparison']['ordered_lists_equal'])

    def test_strict_internal_anchor_and_literal_namespace_urls_are_preserved(self):
        literal = 'http://purl.oclc.org/ooxml/officeDocument/relationships/hyperlink?b=2&a=1&a=2#raw'
        path = self.docx('<w:p><w:hyperlink w:anchor="раздел"><w:r><w:t>Раздел</w:t><w:cr/>'
                         '<w:t>Дальше</w:t></w:r></w:hyperlink></w:p>')
        with ZipFile(path) as archive:
            parts = {name: archive.read(name) for name in archive.namelist()}
        escaped = literal.replace('&', '&amp;').encode()
        parts['word/document.xml'] = parts['word/document.xml'].replace('<w:t>При</w:t>'.encode(), b'<w:t>' + escaped + b'</w:t>')
        parts['word/_rels/document.xml.rels'] = parts['word/_rels/document.xml.rels'].replace(
            b'https://pricing.example.test/?a=1&amp;b=2', escaped)
        with ZipFile(path, 'w') as archive:
            for name, raw in parts.items():
                archive.writestr(name, raw)
        self.as_strict(path)
        result = links.inspect_links(path)
        self.assertEqual(result['links'][0]['target'], literal)
        self.assertEqual(result['links'][0]['anchor'], literal + 'мер')
        self.assertEqual(result['links'][-1]['internal_anchor'], 'раздел')
        self.assertEqual(result['links'][-1]['anchor'], 'Раздел\nДальше')
        self.assertIsNone(result['links'][-1]['target'])
        self.assertEqual(result['warnings'], [])

    def test_strict_unparsed_features_keep_warnings(self):
        features = {
            'HYPERLINK': '<w:p><w:fldSimple w:instr=\' HYPERLINK "https://hidden.example.test/" \'> '
                         '<w:r><w:t>Импорт</w:t></w:r></w:fldSimple></w:p>',
            'split-field': '<w:p><w:r><w:fldChar w:fldCharType="begin"/><w:instrText> HYPER</w:instrText></w:r>'
                           '<w:r><w:instrText>LINK "https://hidden.example.test/" </w:instrText>'
                           '<w:fldChar w:fldCharType="end"/></w:r></w:p>',
            'hlinkClick': '<w:p><w:r><w:drawing xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main">'
                          '<a:hlinkClick r:id="r1"/></w:drawing></w:r></w:p>',
            'hlinkHover': '<w:p><w:r><w:drawing xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main">'
                          '<a:hlinkHover r:id="r1"/></w:drawing></w:r></w:p>',
            'altChunk': None,
        }
        for kind, extra in features.items():
            with self.subTest(kind=kind):
                path = self.docx(extra) if extra is not None else DocxAlternativeFormatTests.imported_docx(self)[0]
                transitional = links.inspect_links(path)
                self.as_strict(path, strict_package=True)
                expected_map = [{'anchor': row['anchor'], 'url': row['target']} for row in transitional['links']]
                expected = [row['url'] for row in expected_map]
                result = links.inspect_links(path, expected)
                self.assertEqual(result['links'], transitional['links'])
                self.assertEqual(result['warnings'], transitional['warnings'])
                self.assertTrue(result['warnings'])
                self.assertTrue(result['comparison']['ordered_lists_equal'])


class PdfCatalogWarningTests(unittest.TestCase):
    setUp = LinkInspectionTests.setUp
    pdf = LinkInspectionTests.pdf

    def catalog_pdf(self, kind, with_links):
        from pypdf.generic import NumberObject
        writer = PdfWriter()
        if with_links:
            path = self.pdf()
            writer.clone_document_from_reader(PdfReader(path))
            expected = ["https://example.test/?a=1&b=2"] * 2 + ["https://example.test/?b=2&a=1"]
        else:
            path = self.base / "empty.pdf"
            writer.add_blank_page(300, 300)
            expected = []
        if kind == "/Outlines":
            outline = DictionaryObject({NameObject("/Type"): NameObject("/Outlines")})
            outline_ref = writer._add_object(outline)
            item = DictionaryObject({NameObject("/Title"): TextStringObject("External bookmark"),
                NameObject("/Parent"): outline_ref,
                NameObject("/A"): DictionaryObject({NameObject("/S"): NameObject("/URI"),
                    NameObject("/URI"): TextStringObject("https://hidden.example.test/bookmark")})})
            item_ref = writer._add_object(item)
            outline.update({NameObject("/First"): item_ref, NameObject("/Last"): item_ref,
                            NameObject("/Count"): NumberObject(1)})
            writer.root_object[NameObject("/Outlines")] = outline_ref
        elif kind == "/AcroForm":
            action = DictionaryObject({NameObject("/S"): NameObject("/JavaScript"),
                NameObject("/JS"): TextStringObject("app.launchURL('https://hidden.example.test/form')")})
            field = DictionaryObject({NameObject("/FT"): NameObject("/Tx"),
                NameObject("/T"): TextStringObject("Parent field"),
                NameObject("/AA"): DictionaryObject({NameObject("/K"): writer._add_object(action)})})
            field_ref = writer._add_object(field)
            widget = DictionaryObject({NameObject("/Type"): NameObject("/Annot"),
                NameObject("/Subtype"): NameObject("/Widget"), NameObject("/Parent"): field_ref,
                NameObject("/Rect"): ArrayObject([FloatObject(n) for n in (50, 50, 100, 70)])})
            widget_ref = writer._add_object(widget)
            field[NameObject("/Kids")] = ArrayObject([widget_ref])
            writer.pages[0].setdefault(NameObject("/Annots"), ArrayObject()).append(widget_ref)
            form = DictionaryObject({NameObject("/Fields"): ArrayObject([field_ref])})
            writer.root_object[NameObject("/AcroForm")] = writer._add_object(form)
        with path.open("wb") as output:
            writer.write(output)
        return path, expected

    def assert_catalog_warning(self, kind):
        for with_links in (False, True):
            with self.subTest(with_links=with_links):
                path, expected = self.catalog_pdf(kind, with_links)
                original = path.read_bytes()
                catalog = PdfReader(path).trailer["/Root"]
                if kind == "/Outlines":
                    self.assertEqual(catalog[kind]["/First"]["/A"]["/URI"], "https://hidden.example.test/bookmark")
                else:
                    field = catalog[kind]["/Fields"][0].get_object()
                    widget = field["/Kids"][0].get_object()
                    self.assertNotIn("/AA", widget)
                    self.assertEqual(widget["/Parent"]["/AA"]["/K"]["/S"], "/JavaScript")
                result = links.inspect_links(path, expected)
                self.assertEqual([row["target"] for row in result["links"]], expected)
                self.assertTrue(result["comparison"]["ordered_lists_equal"])
                self.assertEqual(len(result["warnings"]), 1)
                self.assertIn(kind, result["warnings"][0])
                self.assertEqual(path.read_bytes(), original)

    def test_uri_outline_warns_with_empty_or_matching_regular_urls(self):
        self.assert_catalog_warning("/Outlines")

    def test_parent_field_action_warns_with_empty_or_matching_regular_urls(self):
        self.assert_catalog_warning("/AcroForm")

    def test_plain_pdf_does_not_warn_about_absent_catalog_features(self):
        sys.path.insert(0, str(ROOT / "scripts"))
        import handoff
        for with_links in (False, True):
            with self.subTest(with_links=with_links):
                path, expected = self.catalog_pdf(None, with_links)
                original = path.read_bytes()
                result = links.inspect_links(path, expected)
                self.assertEqual(result["warnings"], [])
                self.assertEqual([row["target"] for row in result["links"]], expected)
                self.assertTrue(result["comparison"]["ordered_lists_equal"])
                if not with_links:
                    report = self.base / "plain-links.json"
                    report.write_text(json.dumps(result), encoding="utf-8")
                    handoff.check_link_comparison(report, [], [])
                self.assertEqual(path.read_bytes(), original)


if __name__ == "__main__":
    unittest.main()
