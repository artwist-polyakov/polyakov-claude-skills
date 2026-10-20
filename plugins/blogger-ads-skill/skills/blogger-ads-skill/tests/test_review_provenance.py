import copy
import json
import unittest
from urllib.parse import quote

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
