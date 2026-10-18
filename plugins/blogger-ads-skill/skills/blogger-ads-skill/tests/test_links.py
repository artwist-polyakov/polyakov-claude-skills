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

    def docx(self):
        path = self.base / "received.docx"
        with ZipFile(path, "w") as archive:
            archive.writestr("word/document.xml", '''<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><w:body>
<w:p><w:hyperlink r:id="r1"><w:r><w:t>При</w:t></w:r><w:r><w:t>мер</w:t></w:r></w:hyperlink></w:p>
<w:p><w:r><w:t>https://plain.example.test/</w:t></w:r><w:hyperlink r:id="r1"><w:r><w:t>OpenAI</w:t><w:tab/><w:t>тарифы</w:t><w:br/><w:t>подробнее</w:t></w:r></w:hyperlink><w:hyperlink r:id="r2"><w:r><w:t>Пример</w:t></w:r></w:hyperlink></w:p>
</w:body></w:document>''')
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

    def test_counts_expose_swapped_anchor_without_declaring_document_invalid(self):
        expected = ["https://product.example.test/?a=1&b=2"] * 2 + ["https://pricing.example.test/?a=1&b=2"]
        comparison = links.inspect_links(self.docx(), expected)["comparison"]
        self.assertEqual(comparison["expected_count"], 3)
        self.assertEqual(comparison["actual_count"], 3)
        self.assertEqual([row["difference"] for row in comparison["differences"]], [-1, 1])
        self.assertFalse(comparison["ordered_lists_equal"])
        self.assertIn("не автоматическое доказательство", comparison["note"])

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


if __name__ == "__main__":
    unittest.main()
