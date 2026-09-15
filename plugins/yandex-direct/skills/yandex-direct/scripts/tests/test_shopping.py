#!/usr/bin/env python3
"""Проверки товарных объявлений без сети: python3 scripts/tests/test_shopping.py."""

import copy
import io
import json
import sys
import tempfile
import unittest
from contextlib import ExitStack, redirect_stdout, redirect_stderr
from functools import partial
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

SCRIPTS = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(SCRIPTS), str(SCRIPTS / "lib")]

import shopping
import cache
from config import DirectFailure
from direct import BatchEntry, BatchResult, Response
from errors import TransportFailure
import writer


ACCOUNT = "offline-advertiser"
GROUP, FEED, AD = 101, 201, 301
FILTERS = [{"Operand": "categoryId", "Operator": "EQUALS_ANY", "Arguments": ["10", "20"]}]


def ad():
    return {"Id": AD, "AdGroupId": GROUP, "CampaignId": 401,
            "Type": "SHOPPING_AD", "State": "OFF", "Status": "DRAFT",
            "ShoppingAd": {"FeedId": FEED, "FeedFilterConditions": {"Items": copy.deepcopy(FILTERS)},
                           "DefaultTexts": ["Прежний текст"], "TitleSources": {"Items": ["name"]},
                           "TextSources": None, "FeedProcessingStatus": "PROCESSED",
                           "BusinessId": 501, "SitelinkSetId": 601}}


class FakeAccounts:
    def current(self):
        return SimpleNamespace(login=ACCOUNT)

    def use_operator_units(self, account, *, need):
        return False


class FakeClient:
    def __init__(self):
        self.settings = SimpleNamespace(profile="production", account=ACCOUNT)
        self.ads = [ad()]
        self.groups = [{"Id": GROUP, "Type": "UNIFIED_AD_GROUP"}]
        self.feeds = [{"Id": FEED, "Name": "Тестовый фид", "BusinessType": "RETAIL",
                       "SourceType": "URL", "Status": "DONE",
                       "UrlFeed": {"Url": "https://example.test/feed.xml", "RemoveUtmTags": "NO"},
                       "TitleAndTextSources": {"Items": ["name", "description"]}}]
        self.calls = []
        self.writes = []
        self.corrupt_readback = False
        self.processing_status = "PROCESSED"
        self.processing_reads = 0
        self.fail_processing_read = False

    def get_all(self, service, params, *, account, use_operator_units):
        self.calls.append((service, copy.deepcopy(params), account))
        use_operator_units()
        if service == "ads" and self.writes:
            self.processing_reads += 1
            if self.fail_processing_read and self.processing_reads == 2:
                raise TransportFailure("Сбой чтения состояния генерации")
        records = {"ads": self.ads, "adgroups": self.groups, "feeds": self.feeds}[service]
        for selection, field in (("Ids", "Id"), ("AdGroupIds", "AdGroupId"), ("CampaignIds", "CampaignId")):
            ids = params.get("SelectionCriteria", {}).get(selection)
            if ids is not None:
                records = [one for one in records if one.get(field) in ids]
        return copy.deepcopy(records)

    def batch(self, service, method, params, *, items, results_key, id_field,
              account, use_operator_units):
        self.writes.append((service, method, copy.deepcopy(params), account))
        use_operator_units()
        item = copy.deepcopy(items[0])
        if method == "add":
            record = {**item, "Id": AD, "CampaignId": 401, "Type": "SHOPPING_AD", "State": "OFF", "Status": "DRAFT"}
            for field in shopping.WRAPPED:
                if field in record["ShoppingAd"]:
                    record["ShoppingAd"][field] = {"Items": record["ShoppingAd"][field]}
            self.ads.append(record)
        else:
            self.ads[0]["ShoppingAd"].update(item["ShoppingAd"])
        self.ads[0]["ShoppingAd"]["FeedProcessingStatus"] = self.processing_status
        if self.corrupt_readback:
            self.ads[0]["ShoppingAd"]["FeedFilterConditions"] = {
                "Items": copy.deepcopy(FILTERS) + [{"Operand": "id", "Operator": "EQUALS_ANY", "Arguments": ["unexpected"]}]}
        return BatchResult([BatchEntry(0, identifier=AD, id_field=id_field)],
                           Response(request_id="offline-test"), results_key)


class ShoppingTests(unittest.TestCase):
    def setUp(self):
        context = ExitStack()
        self.addCleanup(context.close)
        self.root = Path(context.enter_context(tempfile.TemporaryDirectory()))
        self.client = FakeClient()
        context.enter_context(patch.object(shopping.Client, "from_env", return_value=self.client))
        context.enter_context(patch.object(shopping.Accounts, "load", return_value=FakeAccounts()))
        context.enter_context(patch.object(cache, "CACHE_DIR", self.root / "cache"))
        context.enter_context(patch.object(writer, "AuditLog", partial(writer.AuditLog, root=self.root / "journal")))
        context.enter_context(patch.object(writer.Writer, "_current_limits", lambda _: writer.Limits.load()))
        self.filters = self.root / "filters.json"
        self.filters.write_text(json.dumps(FILTERS), encoding="utf-8")

    def command(self, *arguments):
        output = io.StringIO()
        with redirect_stdout(output), redirect_stderr(io.StringIO()):
            status = shopping.run(shopping.build_parser().parse_args(["--json", *arguments]))
        return status, json.loads(output.getvalue())

    def test_add_sends_arrays_and_verifies_wrapped_response(self):
        self.client.ads = []
        status, report = self.command("add", "--group", str(GROUP), "--feed", str(FEED),
                                      "--default-text", "Новый текст", "--title-source", "name",
                                      "--text-source", "description", "--filters", str(self.filters), "--apply")
        self.assertEqual(status, 0, report)
        self.assertEqual(report["written"], [str(AD)])
        self.assertEqual(self.client.writes, [("ads", "add", {"Ads": [{
            "AdGroupId": GROUP, "ShoppingAd": {"FeedId": FEED, "DefaultTexts": ["Новый текст"],
                "TitleSources": ["name"], "TextSources": ["description"], "FeedFilterConditions": FILTERS}}]}, ACCOUNT)])
        self.assertTrue(any(params["SelectionCriteria"] == {"Ids": [AD]}
                            for service, params, _ in self.client.calls if service == "ads"))

    def test_update_changes_only_requested_filters_and_preserves_other_fields(self):
        before = copy.deepcopy(self.client.ads[0]["ShoppingAd"])
        replacement = [{**FILTERS[0], "Arguments": ["30"]}]
        self.filters.write_text(json.dumps(replacement), encoding="utf-8")
        status, report = self.command("update", "--ad", str(AD), "--filters", str(self.filters), "--apply")
        self.assertEqual(status, 0, report)
        self.assertEqual(self.client.writes[0][2], {"Ads": [{"Id": AD, "ShoppingAd": {
            "FeedFilterConditions": {"Items": replacement}}}]})
        self.assertEqual(self.client.ads[0]["ShoppingAd"],
                         {**before, "FeedFilterConditions": {"Items": replacement}})
        self.assertIn("правила отбора товаров", "\n".join(report["preview"]))

    def test_default_mode_only_reads_and_shows_before_after(self):
        status, report = self.command("update", "--ad", str(AD), "--default-text", "Новый текст")
        self.assertEqual(status, 0)
        self.assertFalse(report["applied"])
        self.assertEqual(self.client.writes, [])
        self.assertIn("Прежний текст", "\n".join(report["preview"]))
        self.assertIn("Новый текст", "\n".join(report["preview"]))

    def test_clear_filters_is_explicit_null_without_other_fields(self):
        status, report = self.command("update", "--ad", str(AD), "--clear-filters", "--apply")
        self.assertEqual(status, 0, report)
        self.assertEqual(self.client.writes[0][2], {"Ads": [{"Id": AD, "ShoppingAd": {"FeedFilterConditions": None}}]})
        self.assertIsNone(self.client.ads[0]["ShoppingAd"]["FeedFilterConditions"])

    def test_update_sources_uses_items_and_does_not_send_feed_id(self):
        status, report = self.command("update", "--ad", str(AD), "--text-source", "description", "--apply")
        self.assertEqual(status, 0, report)
        self.assertEqual(self.client.writes[0][2], {"Ads": [{"Id": AD, "ShoppingAd": {
            "TextSources": {"Items": ["description"]}}}]})

    def test_update_rejects_source_unavailable_in_current_feed(self):
        status, report = self.command("update", "--ad", str(AD), "--text-source", "unknown", "--apply")
        self.assertEqual(status, 1)
        self.assertIn("не разрешает источники", " ".join(report["problems"]))
        self.assertEqual(self.client.writes, [])

    def test_rejects_listing_ad_even_when_shopping_body_is_present(self):
        self.client.ads[0]["Type"] = "LISTING_AD"
        status, report = self.command("update", "--ad", str(AD), "--clear-filters", "--apply")
        self.assertEqual(status, 1)
        self.assertIn("SHOPPING_AD", " ".join(report["problems"]))
        self.assertEqual(self.client.writes, [])

    def test_add_rejects_wrong_group_existing_shopping_and_unknown_source(self):
        for problem in ("wrong_group", "duplicate", "source"):
            with self.subTest(problem=problem):
                self.client.groups[0]["Type"] = "TEXT_AD_GROUP" if problem == "wrong_group" else "UNIFIED_AD_GROUP"
                self.client.ads = [ad()] if problem == "duplicate" else []
                status, report = self.command("add", "--group", str(GROUP), "--feed", str(FEED),
                                              "--default-text", "Текст", "--title-source", "unknown", "--apply")
                self.assertEqual(status, 1, report)
                expected = {"wrong_group": "UNIFIED_AD_GROUP", "duplicate": "только одно",
                            "source": "не разрешает источники"}[problem]
                self.assertIn(expected, " ".join(report["problems"]))
        self.assertEqual(self.client.writes, [])

    def test_readback_rejects_unexpected_filter(self):
        self.client.corrupt_readback = True
        status, report = self.command("update", "--ad", str(AD), "--filters", str(self.filters), "--apply")
        self.assertEqual(status, 1)
        self.assertTrue(report["differences"])
        self.assertEqual(report["written"], [])
        self.assertFalse(report["ready"])

    def test_processing_result_keeps_draft_state_and_distinguishes_ready_from_pending(self):
        for status in ("PROCESSED", "UNPROCESSED", "UNKNOWN"):
            with self.subTest(processing=status):
                self.client.processing_status = status
                code, report = self.command("update", "--ad", str(AD), "--default-text", "Новый текст", "--apply")
                self.assertEqual(code, 0, report)
                self.assertEqual(report["ready"], status == "PROCESSED")
                self.assertEqual(report["processing_pending"], [] if status == "PROCESSED" else [AD])
                self.assertEqual((report["ads"][0]["State"], report["ads"][0]["Status"]), ("OFF", "DRAFT"))
                self.assertEqual(report["ads"][0]["ShoppingAd"]["FeedProcessingStatus"], status)

    def test_empty_generation_is_not_reported_as_working_ad(self):
        self.client.processing_status = "EMPTY_RESULT"
        code, report = self.command("update", "--ad", str(AD), "--filters", str(self.filters), "--apply")
        self.assertEqual(code, 1)
        self.assertFalse(report["ok"])
        self.assertFalse(report["ready"])
        self.assertEqual(report["processing_empty"], [AD])
        self.assertIn("EMPTY_RESULT", " ".join(report["problems"]))
        self.assertEqual(report["written"], [str(AD)])

    def test_failed_feed_blocks_creation_but_does_not_block_text_update(self):
        self.client.feeds[0]["Status"] = "ERROR"
        code, report = self.command("update", "--ad", str(AD), "--default-text", "Исправленный текст", "--apply")
        self.assertEqual(self.client.ads[0]["ShoppingAd"]["DefaultTexts"], ["Исправленный текст"])
        self.assertEqual(code, 1)
        self.assertFalse(report["ready"])
        self.assertEqual(report["processing_failed"], [AD])
        self.assertIn("Status=ERROR", " ".join(report["problems"]))
        self.client.ads, self.client.writes = [], []
        code, report = self.command("add", "--group", str(GROUP), "--feed", str(FEED),
                                    "--default-text", "Текст", "--apply")
        self.assertEqual(code, 1)
        self.assertIn("Исправьте источник", " ".join(report["problems"]))
        self.assertEqual(self.client.writes, [])

    def test_updating_feed_is_pending_even_when_old_generation_is_processed(self):
        self.client.feeds[0]["Status"] = "UPDATING"
        code, report = self.command("update", "--ad", str(AD), "--default-text", "Текст", "--apply")
        self.assertEqual(code, 0)
        self.assertFalse(report["ready"])
        self.assertEqual(report["processing_pending"], [AD])

    def test_failed_final_read_does_not_report_overall_success(self):
        self.client.fail_processing_read = True
        code, report = self.command("update", "--ad", str(AD), "--default-text", "Текст", "--apply")
        self.assertEqual(code, 1)
        self.assertFalse(report["ok"])
        self.assertFalse(report["ready"])
        self.assertTrue(report["unknown"])
        self.assertEqual(report["written"], [str(AD)])
        self.assertEqual(len(self.client.writes), 1)

    def test_malformed_final_read_preserves_write_and_reports_unknown(self):
        malformed = ([{}], [{"Id": []}], [{"Id": True}], [{"Id": 0}], [{"Id": -1}],
                     [{"Id": AD}, {"Id": AD}], [{"Id": AD + 1}])
        for records in malformed:
            with self.subTest(records=records):
                self.client.ads = [ad()]
                self.client.writes = []
                self.client.processing_reads = 0
                get_all = self.client.get_all

                def read(service, *args, **kwargs):
                    result = get_all(service, *args, **kwargs)
                    if service == "ads" and self.client.processing_reads == 2:
                        return records
                    return result

                with patch.object(self.client, "get_all", side_effect=read):
                    code, report = self.command("update", "--ad", str(AD),
                                                "--default-text", "Текст", "--apply")
                self.assertEqual(code, 1)
                self.assertFalse(report["ok"])
                self.assertFalse(report["ready"])
                self.assertTrue(report["unknown"])
                self.assertEqual(report["written"], [str(AD)])
                self.assertEqual(len(self.client.writes), 1)
                self.assertEqual(self.client.ads[0]["ShoppingAd"]["DefaultTexts"], ["Текст"])

    def test_malformed_group_read_blocks_creation(self):
        self.client.ads = []
        self.client.groups = [{"Id": GROUP}, {"Id": GROUP}]
        code, report = self.command("add", "--group", str(GROUP), "--feed", str(FEED),
                                    "--default-text", "Текст", "--apply")
        self.assertEqual(code, 1)
        self.assertIn("повторный", " ".join(report["problems"]))
        self.assertEqual(self.client.writes, [])

    def test_conflicting_feed_statuses_do_not_report_ready(self):
        feed = self.client.feeds[0]
        self.client.feeds = [{**feed, "Status": "ERROR"}, {**feed, "Status": "DONE"}]
        code, report = self.command("update", "--ad", str(AD), "--default-text", "Текст", "--apply")
        self.assertEqual(code, 1)
        self.assertFalse(report["ok"])
        self.assertFalse(report["ready"])
        self.assertTrue(report["unknown"])
        self.assertEqual(report["written"], [str(AD)])
        self.assertEqual(len(self.client.writes), 1)

    def test_boolean_feed_reference_does_not_match_feed_one(self):
        self.client.ads[0]["ShoppingAd"]["FeedId"] = True
        self.client.feeds[0]["Id"] = 1
        code, report = self.command("update", "--ad", str(AD), "--default-text", "Текст", "--apply")
        self.assertEqual(code, 1)
        self.assertFalse(report["ready"])
        self.assertTrue(report["unknown"])
        self.assertEqual(report["written"], [str(AD)])

    def test_get_reads_complete_shopping_and_keeps_listing_separate(self):
        listing = {**ad(), "Id": 302, "Type": "LISTING_AD"}
        self.client.ads.append(listing)
        status, data = self.command("get", "--group", str(GROUP), "--no-cache")
        self.assertEqual(status, 0)
        self.assertEqual(data["ads"], [ad()])
        self.assertIn("FeedFilterConditions", self.client.calls[0][1]["ShoppingAdFieldNames"])
        self.assertIn("ResponsiveAdFieldNames", self.client.calls[0][1])
        with self.assertRaisesRegex(DirectFailure, "SHOPPING_AD"):
            self.command("get", "--ad", "302", "--no-cache")

    def test_get_rejects_ambiguous_or_malformed_ids(self):
        for records in ([{}], [{"Id": []}], [ad(), ad()], [{**ad(), "Id": AD + 1}]):
            with self.subTest(records=records), patch.object(self.client, "get_all", return_value=records):
                with self.assertRaises(TransportFailure):
                    self.command("get", "--ad", str(AD), "--no-cache")

    def test_invalid_filters_fail_before_account_access(self):
        for filters in ([], FILTERS * 31, [{**FILTERS[0], "Operator": "RANGE"}],
                        [{**FILTERS[0], "Arguments": [1]}], [{**FILTERS[0], "Arguments": ["я" * 34000]}],
                        [{**FILTERS[0], "Extra": "typo"}]):
            with self.subTest(filters_type=type(filters)):
                self.filters.write_text(json.dumps(filters), encoding="utf-8")
                with self.assertRaises(DirectFailure):
                    self.command("update", "--ad", str(AD), "--filters", str(self.filters))
        self.assertEqual(self.client.calls, [])
        self.assertEqual(self.client.writes, [])


if __name__ == "__main__":
    unittest.main()
