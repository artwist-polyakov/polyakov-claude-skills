#!/usr/bin/env python3
"""Проверки без сети: python3 scripts/tests/test_feeds.py."""

import base64
import copy
import io
import json
import sys
import tempfile
import unittest
import uuid
from contextlib import ExitStack, redirect_stderr, redirect_stdout
from functools import partial
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

SCRIPTS = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(SCRIPTS), str(SCRIPTS / "lib")]

import feeds
import cache
import writer
from config import DirectFailure
from direct import BatchEntry, BatchResult, Response
from errors import ItemIssue, TransportFailure


class FakeAccounts:
    def __init__(self, login):
        self.login = login
        self.unit_calls = []

    def current(self):
        return SimpleNamespace(login=self.login)

    def find(self, wanted, archived=False):
        return [SimpleNamespace(exact=True)]

    def choose(self, wanted, archived=False):
        return SimpleNamespace(login=wanted)

    def use_operator_units(self, account, *, need):
        self.unit_calls.append((account, need))
        return False


class FakeClient:
    """Договор API: get отдаёт только запрошенные поля, никогда Data."""

    def __init__(self):
        self.settings = SimpleNamespace(profile="production", account=None)
        self.records = {}
        self.get_calls, self.batch_calls = [], []
        self.next_id = uuid.uuid4().int % (2 ** 53)
        self.outcome = "success"
        self.processing_status = "NEW"

    def stored(self, item):
        self.next_id += 1
        identifier = item.get("Id", self.next_id)
        result = copy.deepcopy(self.records.get(identifier, {}))
        for field, value in item.items():
            if isinstance(value, dict):
                result.setdefault(field, {}).update(copy.deepcopy(value))
            else:
                result[field] = value
        result.update(Id=identifier, Status=self.processing_status, NumberOfItems=None,
                      UpdatedAt=None, CampaignIds=None, TitleAndTextSources=None)
        if result["SourceType"] == "URL":
            result["UrlFeed"].setdefault("RemoveUtmTags", "NO")
            result["UrlFeed"].setdefault("Login", None)
            result["FileFeed"] = None
        else:
            result["FileFeed"].pop("Data", None)
            result["UrlFeed"] = None
        if result["Status"] == "DONE":
            result["FilterSchema"] = "retail"
        self.records[identifier] = result
        return identifier

    def get_all(self, service, params, *, account, use_operator_units):
        self.get_calls.append((service, copy.deepcopy(params), account))
        use_operator_units()
        if self.outcome == "unreadable" and self.batch_calls:
            raise TransportFailure("Сбой перечитывания")
        if "SelectionCriteria" in params and not params["SelectionCriteria"].get("Ids"):
            raise AssertionError("Feeds.get не принимает пустую SelectionCriteria")
        chosen = params.get("SelectionCriteria", {}).get("Ids", self.records)
        records = []
        for identifier in chosen:
            if identifier not in self.records:
                continue
            stored = self.records[identifier]
            record = {key: copy.deepcopy(stored[key]) for key in params["FieldNames"] if key in stored}
            for source in ("UrlFeed", "FileFeed"):
                body = stored.get(source)
                if body is not None and source + "FieldNames" in params:
                    record[source] = {key: copy.deepcopy(body[key]) for key in params[source + "FieldNames"] if key in body}
            records.append(record)
        return iter(records)

    def batch(self, service, method, params, *, items, results_key, id_field,
              account, use_operator_units):
        self.batch_calls.append((service, method, copy.deepcopy(params), account))
        use_operator_units()
        entries = []
        for index, item in enumerate(items):
            identifier = item.get("Id")
            if self.outcome == "rejected" or self.outcome == "partial" and len(self.batch_calls) == 2:
                entries.append(BatchEntry(index, issues=[ItemIssue("error", 1, "Фид используется в группе")]))
                continue
            if self.outcome != "unchanged":
                if method == "delete":
                    self.records.pop(identifier, None)
                else:
                    identifier = self.stored(item)
            entries.append(BatchEntry(index, identifier=identifier, id_field=id_field))
        if self.outcome == "unknown":
            raise TransportFailure("Ответ на запись потерян")
        return BatchResult(entries, Response(request_id="offline"), results_key)


class FeedTests(unittest.TestCase):
    def setUp(self):
        self.context = ExitStack()
        self.addCleanup(self.context.close)
        self.root = Path(self.context.enter_context(tempfile.TemporaryDirectory()))
        self.account = "offline-" + self.root.name
        self.client = FakeClient()
        self.accounts = FakeAccounts(self.account)
        self.factory = self.context.enter_context(patch.object(feeds.Client, "from_env", return_value=self.client))
        self.context.enter_context(patch.object(feeds.Accounts, "load", return_value=self.accounts))
        self.context.enter_context(patch.object(cache, "CACHE_DIR", self.root / "cache"))
        self.context.enter_context(patch.object(writer, "AuditLog", partial(writer.AuditLog, root=self.root / "journal")))
        self.context.enter_context(patch.object(writer.Writer, "_current_limits", lambda _: writer.Limits.load()))
        self.url = "https://example.test/catalog.xml"
        self.item = {"Name": "Товары магазина", "BusinessType": "RETAIL", "SourceType": "URL", "UrlFeed": {"Url": self.url}}

    def command(self, *arguments):
        self.output, self.errors = io.StringIO(), io.StringIO()
        with redirect_stdout(self.output), redirect_stderr(self.errors):
            args = feeds.build_parser().parse_args(["--json", *arguments])
            status = feeds.run(args)
        return status, json.loads(self.output.getvalue())

    def add(self, *arguments):
        return self.command("add", "--name", self.item["Name"], "--url", self.url, *arguments)

    def upload(self, *arguments):
        self.file = self.root / "каталог.csv"
        self.file.write_bytes(b"id;name\n1;Example\n")
        return self.command("add", "--name", "Файл каталога", "--file", str(self.file), *arguments)

    def test_all_read_has_no_empty_selection_and_carries_source_and_status(self):
        identifier = self.client.stored(self.item)
        status, data = self.command("list")
        self.assertEqual(status, 0)
        self.assertEqual(data["feeds"][0]["Id"], identifier)
        self.assertEqual(data["feeds"][0]["UrlFeed"]["Url"], self.url)
        service, params, account = self.client.get_calls[0]
        self.assertEqual((service, account), ("feeds", self.account))
        self.assertNotIn("SelectionCriteria", params)
        self.assertIn("Status", params["FieldNames"])
        self.assertIn("TitleAndTextSources", params["FieldNames"])
        self.assertEqual(params["UrlFeedFieldNames"], ["Url", "Login", "RemoveUtmTags"])
        self.assertEqual(params["FileFeedFieldNames"], ["Filename"])

    def test_get_requires_all_selected_ids_and_deduplicates(self):
        identifier = self.client.stored(self.item)
        self.command("get", "--feed", str(identifier), "--feed", str(identifier))
        self.assertEqual(self.client.get_calls[0][1]["SelectionCriteria"], {"Ids": [identifier]})
        with self.assertRaisesRegex(DirectFailure, "не найдены"):
            self.command("get", "--feed", str(identifier + 1))

    def test_cached_processing_refreshes_with_no_cache_and_selections_are_separate(self):
        identifier = self.client.stored(self.item)
        self.command("list")
        self.client.records[identifier]["Status"] = "DONE"
        _, cached = self.command("list")
        self.assertTrue(cached["cached"])
        self.assertFalse(cached["ready"])
        _, selected = self.command("get", "--feed", str(identifier))
        self.assertTrue(selected["ready"])
        _, refreshed = self.command("list", "--no-cache")
        self.assertFalse(refreshed["cached"])
        self.assertTrue(refreshed["ready"])

    def test_explicit_account_is_used_for_reads_and_write(self):
        selected = "explicit-" + self.root.name
        status, data = self.add("--apply", "--account", selected)
        self.assertEqual(status, 0)
        self.assertEqual(data["account"], selected)
        self.assertEqual(self.client.batch_calls[0][3], selected)
        self.assertTrue(all(call[2] == selected for call in self.client.get_calls))

    def test_add_defaults_to_plan_without_write_or_journal(self):
        status, data = self.add()
        self.assertEqual(status, 0)
        self.assertTrue(data["ok"])
        self.assertFalse(data["applied"])
        self.assertFalse(self.client.batch_calls)
        self.assertFalse((self.root / "journal").exists())

    def test_url_add_sends_exact_payload_and_separates_processing(self):
        status, data = self.add("--remove-utm-tags", "YES", "--apply")
        self.assertEqual(status, 0)
        expected = copy.deepcopy(self.item)
        expected["UrlFeed"]["RemoveUtmTags"] = "YES"
        self.assertEqual(self.client.batch_calls, [("feeds", "add", {"Feeds": [expected]}, self.account)])
        self.assertTrue(data["ok"])
        self.assertEqual(data["written"], data["accepted"])
        self.assertFalse(data["ready"])
        self.assertEqual(data["processing_pending"], [data["feeds"][0]["Id"]])

    def test_processing_error_does_not_report_success(self):
        self.client.processing_status = "ERROR"
        status, data = self.add("--apply")
        self.assertEqual(status, 1)
        self.assertFalse(data["ok"])
        self.assertTrue(data["processing_failed"])
        self.assertIn("ERROR", " ".join(data["problems"]))

    def test_rename_does_not_replace_source(self):
        identifier = self.client.stored(self.item)
        status, data = self.command("update", "--feed", str(identifier), "--name", "Новое имя", "--apply")
        self.assertEqual(status, 0)
        self.assertEqual(self.client.batch_calls[0][2], {"Feeds": [{"Id": identifier, "Name": "Новое имя"}]})
        self.assertEqual(data["feeds"][0]["UrlFeed"]["Url"], self.url)

    def test_url_options_are_partial_updates_and_source_type_is_guarded(self):
        identifier = self.client.stored(self.item)
        status, _ = self.command("update", "--feed", str(identifier), "--remove-utm-tags", "YES", "--apply")
        self.assertEqual(status, 0)
        self.assertEqual(self.client.batch_calls[0][2], {"Feeds": [{"Id": identifier, "UrlFeed": {"RemoveUtmTags": "YES"}}]})
        self.file = self.root / "catalog.csv"
        self.file.write_text("id;name\n1;example")
        self.client.batch_calls.clear()
        status, data = self.command("update", "--feed", str(identifier), "--file", str(self.file), "--apply")
        self.assertEqual(status, 1)
        self.assertIn("тип источника менять нельзя", " ".join(data["problems"]))
        self.assertFalse(self.client.batch_calls)

    def test_file_payload_and_journal_do_not_claim_content_verification(self):
        status, data = self.upload("--apply")
        self.assertEqual(status, 0)
        encoded = base64.b64encode(self.file.read_bytes()).decode("ascii")
        item = self.client.batch_calls[0][2]["Feeds"][0]
        self.assertEqual(item, {"Name": "Файл каталога", "BusinessType": "RETAIL", "SourceType": "FILE",
                                "FileFeed": {"Data": encoded, "Filename": self.file.name}})
        self.assertTrue(data["unchecked"])
        self.assertIn("FileFeed.Data", " ".join(data["unchecked"]))
        journal = (self.root / "journal" / self.account / writer.JOURNAL_FILE).read_text()
        self.assertNotIn(encoded, journal)
        self.assertNotIn(encoded, self.errors.getvalue())
        self.assertIn(data["upload"]["sha256"], journal)

    def test_file_update_sends_new_bytes_even_when_filename_is_unchanged(self):
        self.upload("--apply")
        identifier = next(iter(self.client.records))
        self.file.write_bytes(b"id;name\n2;Changed\n")
        status, data = self.command("update", "--feed", str(identifier), "--file", str(self.file), "--apply")
        self.assertEqual(status, 0)
        item = self.client.batch_calls[-1][2]["Feeds"][0]
        self.assertEqual(item, {"Id": identifier, "FileFeed": {"Filename": self.file.name,
                                "Data": base64.b64encode(self.file.read_bytes()).decode("ascii")}})
        self.assertTrue(data["unchecked"])

    def test_delete_checks_absence_and_partial_refusal_is_failure(self):
        first = self.client.stored(self.item)
        second = self.client.stored(dict(self.item, Name="Другой фид"))
        self.client.outcome = "partial"
        status, data = self.command("delete", "--feed", str(first), "--feed", str(second), "--apply")
        self.assertEqual(status, 1)
        self.assertEqual([call[2] for call in self.client.batch_calls],
                         [{"SelectionCriteria": {"Ids": [first]}}, {"SelectionCriteria": {"Ids": [second]}}])
        self.assertNotIn(first, self.client.records)
        self.assertIn(second, self.client.records)
        self.assertEqual(data["written"], [str(first)])
        self.assertTrue(data["failed"])

    def test_successful_response_without_delete_is_not_success(self):
        identifier = self.client.stored(self.item)
        self.client.outcome = "unchanged"
        status, data = self.command("delete", "--feed", str(identifier), "--apply")
        self.assertEqual(status, 1)
        self.assertFalse(data["written"])
        self.assertTrue(data["differences"])

    def test_lost_add_response_is_not_retried_or_misreported(self):
        self.client.outcome = "unknown"
        status, data = self.add("--apply")
        self.assertEqual(status, 1)
        self.assertEqual(len(self.client.batch_calls), 1)
        self.assertFalse(data["written"])
        self.assertTrue(data["unknown"])
        self.assertNotIn("SelectionCriteria", self.client.get_calls[-1][1])

    def test_unreadable_result_is_not_success(self):
        self.client.outcome = "unreadable"
        status, data = self.add("--apply")
        self.assertEqual(status, 1)
        self.assertTrue(data["unknown"])

    def test_invalid_input_is_rejected_before_client_creation(self):
        for arguments in (("add", "--name", " ", "--url", self.url),
                          ("add", "--name", "Фид", "--url", "catalog.xml"),
                          ("update", "--feed", str(self.client.next_id))):
            with self.subTest(arguments=arguments), self.assertRaises(DirectFailure):
                self.command(*arguments)
        self.factory.assert_not_called()

    def test_file_size_limit_counts_base64_and_json(self):
        limits = writer.Limits.load()
        limits.data["media"]["feed"]["max_bytes"] = 180
        with patch.object(feeds.Limits, "load", return_value=limits):
            with self.assertRaisesRegex(DirectFailure, "base64 и JSON"):
                self.upload()
        self.factory.assert_not_called()


if __name__ == "__main__":
    unittest.main()
