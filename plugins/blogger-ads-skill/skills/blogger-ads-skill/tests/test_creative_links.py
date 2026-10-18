"""Одна исходная карта питает импорт и приложение без ручной пересборки URL."""
import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from xml.etree import ElementTree as ET
from zipfile import ZipFile

ROOT = Path(__file__).resolve().parents[1]


def module(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / (name + ".py"))
    loaded = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(loaded)
    return loaded


links, deal, render, inspect = [module(name) for name in ("creative_links", "deal", "render_contract", "inspect_links")]
W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"


class CreativeLinkMapTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)
        self.path = self.base / "original-link-map.json"
        self.text = self.base / "post.txt"
        self.text.write_bytes("  Исходный пост\r\n\r\nerid: Exact[ ".encode())
        self.state = self.base / "state.json"
        self.state.write_bytes((ROOT / "assets/deal.example.json").read_bytes())
        self.output = self.base / "creative.docx"
        self.table = [
            ["№", "Фраза-ссылка (якорь)", "Адрес (URL)"],
            ["1", "«Сервис» (первое упоминание)", "https://Example.test/?b=2&a=1&a=2#top"],
            ["2", "  Открытый код и SDK  ", "https://example.test/security/"],
            ["3", "«Провайдер» (абзац о тарифах)", "https://provider.test/pricing"],
            ["4", "«Сервис» (абзац с расчётом)", "https://Example.test/?b=2&a=1&a=2#top"],
            ["5", "«Сервис» (призыв в конце)", "https://Example.test/?b=2&a=1&a=2#top"],
            ["6", "https://t.me/example", "видимая ссылка в тексте, без якоря"],
        ]
        self.expected = [{"anchor": row[1], "url": row[2] if index < 6 else row[1]}
                         for index, row in enumerate(self.table[1:], 1)]
        self.save(self.table)
        schema = json.loads((ROOT / "assets/template-fields.json").read_text())
        self.fields = {key: item["example"] for key, item in schema["fields"].items()
                       if "<N>" not in key and key not in ("LINK_COUNT", "POST_TEXT")}
        self.template = ROOT / "assets/templates/creative.docx"
        self.now = deal.stamp("2026-11-01T12:00:00+03:00")

    def save(self, value):
        self.path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")

    def import_map(self):
        return deal.import_creative(self.state, self.text, self.path, self.now)

    def build(self, fields=None):
        return render.render(self.template, self.fields if fields is None else fields, self.output,
                             post_file=self.text, links_file=self.path)

    def sidecar(self, path=None):
        return json.loads((path or self.output).with_suffix(".docx.fields.json").read_text())

    def rows(self, path):
        with ZipFile(path) as package:
            root = ET.fromstring(package.read("word/document.xml"))
        return [
            ["".join(t.text or "" for t in cell.iter(W + "t")) for cell in row.findall(W + "tc")]
            for row in root.iter(W + "tr")]

    def test_table_preserves_all_anchors_order_duplicates_and_raw_url_parameters(self):
        self.assertEqual(links.parse_links(self.table), self.expected)
        self.assertEqual(inspect.expected_urls(self.path), [row["url"] for row in self.expected])

    def test_pair_objects_two_column_table_and_legacy_urls_are_supported(self):
        self.assertEqual(links.parse_links(self.expected), self.expected)
        self.assertEqual(links.parse_links([["Якорь", "URL"], ["Сервис", "https://example.test"]]),
                         [{"anchor": "Сервис", "url": "https://example.test"}])
        urls = ["https://example.test/?a=1", "https://example.test/?a=1"]
        self.assertEqual(links.parse_links(urls), [{"anchor": url, "url": url} for url in urls])
        self.assertEqual(links.parse_links([]), [])

    def test_real_first_row_with_anchor_word_and_url_path_is_not_a_header(self):
        for anchor in ("anchor", "якорь"):
            with self.subTest(anchor=anchor):
                rows = [[anchor, "https://example.test/url"], ["Следующая", "https://example.test/next"]]
                expected = [{"anchor": row[0], "url": row[1]} for row in rows]
                self.assertFalse(links.header(rows[0]))
                self.assertEqual(links.parse_links(rows), expected)
                self.save(rows)
                self.import_map()
                self.assertEqual(json.loads(self.state.read_text())["creative"]["link_map"], expected)
        with self.assertRaisesRegex(ValueError, "адрес неоднозначен"):
            links.parse_links([["anchor", "documentation url"]])

    def test_ambiguous_or_malformed_rows_fail_before_state_or_sources_are_written(self):
        before = self.state.read_bytes()
        invalid = [
            [["№", "Якорь", "URL"], ["1", "Название", "Ссылка в тексте"]],
            [["№", "Якорь", "URL"], ["1", "https://example.test", "см. https://other.test"]],
            [["1", "https://example.test", "https://broken address"]],
            [["1", "https://example.test", ""]],
            [["1", "", "https://example.test"]],
            [{"anchor": "Якорь", "url": "https://bad address"}],
            [["Название", "Пояснение"]],
        ]
        for value in invalid:
            with self.subTest(value=value):
                self.save(value)
                with self.assertRaisesRegex(ValueError, "Карта ссылок"):
                    self.import_map()
                self.assertEqual(self.state.read_bytes(), before)
                self.assertFalse((self.base / "creative").exists())

    def test_conflicting_numbers_are_not_used_to_sort_rows(self):
        self.table[3], self.table[4] = self.table[4], self.table[3]
        with self.assertRaisesRegex(ValueError, "номер должен совпадать с порядком"):
            links.parse_links(self.table)

    def test_import_stores_original_bytes_and_ordered_pairs_without_touching_text(self):
        original = self.path.read_bytes()
        self.import_map()
        state = json.loads(self.state.read_text())
        creative = state["creative"]
        self.assertEqual(creative["links"], [row["url"] for row in self.expected])
        self.assertEqual(creative["link_map"], self.expected)
        self.assertEqual(creative["text"].encode(), self.text.read_bytes())
        proof = creative["provenance"]["links"]
        self.assertEqual((self.base / proof["path"]).read_bytes(), original)
        self.assertEqual(proof["original_path"], str(self.path.resolve()))
        self.assertEqual(self.path.read_bytes(), original)
        self.assertEqual(deal.fingerprint(state, self.state), deal.digest(creative))

    def test_anchor_only_change_invalidates_old_approval_even_with_same_urls(self):
        self.import_map()
        state = json.loads(self.state.read_text())
        previous_digest = deal.digest(state["creative"])
        state["checks"]["creative_approval"].update(status="confirmed", creative_sha256=previous_digest,
                                                       checked_at=self.now.isoformat(), source="Согласование старой карты")
        self.state.write_text(json.dumps(state))
        self.table[3][1] = "Другое упоминание того же провайдера"
        self.save(self.table)
        result = self.import_map()
        state = json.loads(self.state.read_text())
        self.assertTrue(result["changed"])
        self.assertNotEqual(deal.digest(state["creative"]), previous_digest)
        self.assertEqual(state["checks"]["creative_approval"]["status"], "pending")
        self.assertEqual(state["creative"]["links"], [row["url"] for row in self.expected])

    def test_legacy_digest_is_unchanged_until_new_import_adds_link_map(self):
        creative = {"text": "Пост", "links": ["https://example.test"], "media_sha256": []}
        expected = hashlib.sha256(json.dumps(creative, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
        self.assertEqual(deal.digest(creative), expected)
        self.import_map()
        self.assertIn("link_map", json.loads(self.state.read_text())["creative"])

    def test_deleted_or_reordered_map_cannot_pass_source_verification(self):
        self.import_map()
        state = json.loads(self.state.read_text())
        broken = copy.deepcopy(state)
        del broken["creative"]["link_map"]
        with self.assertRaisesRegex(ValueError, "creative.link_map"):
            deal.fingerprint(broken, self.state)
        broken = copy.deepcopy(state)
        broken["creative"]["link_map"][2], broken["creative"]["link_map"][5] = broken["creative"]["link_map"][5], broken["creative"]["link_map"][2]
        with self.assertRaisesRegex(ValueError, "creative.link_map"):
            deal.fingerprint(broken, self.state)

    def test_direct_generation_keeps_source_pairs_in_docx_and_sidecar(self):
        self.build()
        saved = self.sidecar()
        self.assertEqual(saved["links_source"], {"path": str(self.path.resolve()), "sha256": hashlib.sha256(self.path.read_bytes()).hexdigest()})
        expected_rows = [[str(index), row["anchor"], row["url"]] for index, row in enumerate(self.expected, 1)]
        actual = self.rows(self.output)
        for row in expected_rows:
            self.assertIn(row, actual)
        positions = [actual.index(row) for row in expected_rows]
        self.assertEqual(positions, sorted(positions))
        self.assertEqual({key: value for key, value in saved["fields"].items() if key in links.field_values(self.expected)}, links.field_values(self.expected))

    def test_conflicting_manual_anchor_url_count_or_extra_row_fails_before_output(self):
        for key, value in [("LINK_3_ANCHOR", self.expected[2]["url"]),
                           ("LINK_3_URL", self.expected[5]["url"]), ("LINK_COUNT", "5"),
                           ("LINK_7_URL", "https://extra.test")]:
            with self.subTest(key=key):
                with self.assertRaisesRegex(ValueError, "противоречит --links-file"):
                    self.build(self.fields | {key: value})
                self.assertFalse(self.output.exists())
                self.assertFalse(self.output.with_suffix(".docx.fields.json").exists())

    def test_matching_manual_values_are_allowed_without_changing_source(self):
        before = self.path.read_bytes()
        self.build(self.fields | links.field_values(self.expected))
        self.assertEqual(self.path.read_bytes(), before)

    def test_revise_automatically_reuses_original_map_and_rejects_manual_replacement(self):
        self.build()
        changed = self.base / "new.docx"
        with self.assertRaisesRegex(ValueError, "противоречит --links-file"):
            render.revise_document(self.output, {"LINK_3_ANCHOR": "Подмена"}, changed)
        self.assertFalse(changed.exists())
        render.revise_document(self.output, {"MEDIA_MANIFEST": "отсутствуют"}, changed)
        self.assertEqual(self.sidecar(changed)["links_source"], self.sidecar()["links_source"])
        self.assertEqual(self.rows(changed), self.rows(self.output))

    def test_revise_rejects_changed_original_until_new_map_explicitly_given(self):
        self.build()
        self.table[3][1] = "Изменённый якорь"
        self.save(self.table)
        changed = self.base / "new.docx"
        with self.assertRaisesRegex(ValueError, "Исходная карта ссылок изменилась"):
            render.revise_document(self.output, {}, changed)
        self.assertFalse(changed.exists())
        new_map = self.base / "new-map.json"
        new_map.write_text(json.dumps([{"anchor": "Новая карта", "url": "https://new.test"}]))
        render.revise_document(self.output, {}, changed, links_file=new_map)
        fields = self.sidecar(changed)["fields"]
        self.assertEqual(fields["LINK_COUNT"], "1")
        self.assertEqual(fields["LINK_1_ANCHOR"], "Новая карта")
        self.assertNotIn("LINK_2_URL", fields)
        self.assertEqual(self.sidecar()["fields"]["LINK_3_ANCHOR"], self.expected[2]["anchor"])

    def test_cli_import_and_render_both_accept_same_original_table(self):
        result = subprocess.run([sys.executable, str(ROOT / "scripts/deal.py"), "import-creative", "--state", str(self.state),
                                 "--text", str(self.text), "--links", str(self.path), "--now", self.now.isoformat()],
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        fields = self.base / "fields.json"
        fields.write_text(json.dumps(self.fields))
        result = subprocess.run([sys.executable, str(ROOT / "scripts/render_contract.py"), "--template", "creative", "--fields", str(fields),
                                 "--post-file", str(self.text), "--links-file", str(self.path), "--output", str(self.output)],
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.sidecar()["fields"]["LINK_3_ANCHOR"], self.expected[2]["anchor"])
        self.assertEqual(json.loads(self.state.read_text())["creative"]["links"][2], self.expected[2]["url"])


if __name__ == "__main__":
    unittest.main()
