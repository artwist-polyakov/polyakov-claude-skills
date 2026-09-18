#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""Проверки без сети: python3 scripts/tests/test_retargeting.py."""

import copy
import io
import json
from contextlib import ExitStack, redirect_stderr, redirect_stdout
from functools import partial
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch


SCRIPTS = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(SCRIPTS), str(SCRIPTS / "lib")]

import ads_write
import cache
from config import DirectFailure
from direct import BatchEntry, BatchResult, Response
from errors import ItemIssue, TransportFailure
import retargeting
import retargeting_lists
import writer


ACCOUNT = "selected-advertiser"
SEGMENT = 12345
CONDITION = 701
NAME = "Посетители каталога"


def source(identifier=SEGMENT, name=NAME, kind="segment", login=ACCOUNT):
    return {"GoalID": identifier, "Name": name, "Type": kind, "Login": login,
            "GoalDomain": "example.test"}


def condition(identifier=CONDITION, name=NAME):
    return {"Id": identifier, "Name": name, "Type": "RETARGETING",
            "Rules": [{"Operator": "ANY", "Arguments": [{"ExternalId": SEGMENT}]}],
            "Scope": "FOR_TARGETS_AND_ADJUSTMENTS", "IsAvailable": "YES",
            "AvailableForTargetsInAdGroupTypes": {"Items": ["TEXT_AD_GROUP"]}}


class FakeAccounts:
    def __init__(self):
        self.unit_calls = []

    def current(self):
        return SimpleNamespace(login=ACCOUNT)

    def find(self, wanted, archived=False):
        return [SimpleNamespace(exact=True)]

    def choose(self, wanted, archived=False):
        return SimpleNamespace(login=wanted)

    def use_operator_units(self, account, *, need):
        self.unit_calls.append((account, need))
        return False


class FakeClient:
    """Только граница API; проверку записи, кеш и журнал выполняет настоящий код."""

    def __init__(self):
        self.settings = SimpleNamespace(profile="production", account="configured-advertiser")
        self.sources = [source()]
        self.conditions = []
        self.source_calls = []
        self.get_calls = []
        self.batch_calls = []
        self.write_result = "success"

    def retargeting_goals(self, logins):
        self.source_calls.append(list(logins))
        return copy.deepcopy(self.sources)

    def get_all(self, service, params, *, account, use_operator_units):
        self.get_calls.append((service, copy.deepcopy(params), account))
        use_operator_units()
        if self.write_result == "unreadable" and self.batch_calls:
            raise TransportFailure("Сбой перечитывания")
        criteria = params.get("SelectionCriteria", {})
        records = self.conditions
        if "Ids" in criteria:
            records = [item for item in records if item["Id"] in criteria["Ids"]]
        if "Types" in criteria:
            records = [item for item in records if item["Type"] in criteria["Types"]]
        return iter(copy.deepcopy(records))

    def batch(self, service, method, params, *, items, results_key, id_field,
              account, use_operator_units):
        self.batch_calls.append((service, method, copy.deepcopy(params), account))
        use_operator_units()
        response = Response(request_id="offline-test")
        if self.write_result == "rejected":
            entry = BatchEntry(0, issues=[ItemIssue("error", 7001, "Лимит условий")])
        else:
            created = condition()
            created.update(copy.deepcopy(items[0]))
            created["Rules"][0]["Arguments"][0]["MembershipLifeSpan"] = 0
            if self.write_result == "different_rules":
                created["Rules"][0]["Arguments"][0]["ExternalId"] = SEGMENT + 1
            self.conditions.append(created)
            if self.write_result == "unknown":
                raise TransportFailure("Ответ на создание потерян")
            entry = BatchEntry(0, identifier=CONDITION, id_field=id_field)
        return BatchResult([entry], response, results_key)


class RetargetingTests(unittest.TestCase):
    def setUp(self):
        self.context = ExitStack()
        self.addCleanup(self.context.close)
        self.root = Path(self.context.enter_context(tempfile.TemporaryDirectory()))
        self.client = FakeClient()
        self.accounts = FakeAccounts()
        self.client_factory = self.context.enter_context(
            patch.object(retargeting.Client, "from_env", return_value=self.client))
        self.context.enter_context(
            patch.object(retargeting.Accounts, "load", return_value=self.accounts))
        self.context.enter_context(patch.object(cache, "CACHE_DIR", self.root / "cache"))
        self.context.enter_context(patch.object(
            writer, "AuditLog", partial(writer.AuditLog, root=self.root / "journal")))
        self.context.enter_context(patch.object(
            writer.Writer, "_current_limits", lambda _: writer.Limits.load()))

    def command(self, *arguments):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            args = retargeting.build_parser().parse_args(["--json", *arguments])
            status = retargeting.run(args)
        return status, json.loads(out.getvalue())

    def create(self, *arguments):
        return self.command("create", "--segment", str(SEGMENT), "--name", NAME, *arguments)

    def journal(self):
        path = self.root / "journal" / ACCOUNT / writer.JOURNAL_FILE
        return [json.loads(line) for line in path.read_text().splitlines()]

    def test_sources_use_api_names_and_only_metrika_segments(self):
        self.client.sources = [source(), source(2, "Цель", "goal"),
                               source(3, "Аудитория", "audience_segment"),
                               source(4, "Другие посетители")]
        status, data = self.command("sources", "--search", "КАТАЛОГА")
        self.assertEqual(status, 0)
        self.assertEqual(data["segments"], [source()])
        self.assertEqual(data["account"], ACCOUNT)
        self.assertEqual(self.client.source_calls, [[ACCOUNT]])
        self.assertFalse(data["cached"])

    def test_explicit_account_overrides_active_and_configured_account(self):
        self.client.sources = [source(login="explicit-advertiser")]
        _, data = self.command("sources", "--account", "explicit-advertiser")
        self.assertEqual(data["account"], "explicit-advertiser")
        self.assertEqual(self.client.source_calls, [["explicit-advertiser"]])
        self.assertEqual(self.client_factory.call_args.kwargs["account"], "explicit-advertiser")

    def test_sources_reuse_cache_and_no_cache_refreshes_it(self):
        self.command("sources")
        self.client.sources = [source(8, "Свежий сегмент")]
        _, cached = self.command("sources")
        self.assertTrue(cached["cached"])
        self.assertEqual(cached["segments"], [source()])
        self.assertEqual(len(self.client.source_calls), 1)
        _, refreshed = self.command("sources", "--no-cache")
        self.assertFalse(refreshed["cached"])
        self.assertEqual(refreshed["segments"], self.client.sources)
        _, cached_again = self.command("sources")
        self.assertEqual(cached_again["segments"], self.client.sources)
        self.assertEqual(len(self.client.source_calls), 2)

    def test_sources_preserve_owner_of_available_shared_segment(self):
        self.client.sources = [source(login="segment-owner")]
        _, data = self.command("sources")
        self.assertEqual(data["segments"], self.client.sources)
        self.assertEqual(self.client.source_calls, [[ACCOUNT]])

    def test_sources_require_documented_name(self):
        malformed = source()
        malformed["GoalName"] = malformed.pop("Name")
        self.client.sources = [malformed]
        with self.assertRaisesRegex(DirectFailure, "Name"):
            self.command("sources")

    def test_sources_allow_null_or_missing_owner(self):
        missing_login = source()
        del missing_login["Login"]
        for record in (source(login=None), missing_login):
            with self.subTest(record=record):
                self.client.sources = [record]
                status, data = self.command("sources", "--no-cache")
                self.assertEqual(status, 0)
                self.assertEqual(data["segments"], [record])

    def test_text_output_handles_records_and_empty_lists(self):
        for action in ("sources", "list"):
            for empty in (False, True):
                with self.subTest(action=action, empty=empty):
                    self.client.sources = [] if empty else [source()]
                    self.client.conditions = [] if empty else [condition()]
                    output = io.StringIO()
                    with redirect_stdout(output):
                        args = retargeting.build_parser().parse_args([action, "--no-cache"])
                        status = retargeting.run(args)
                    self.assertEqual(status, 0)
                    self.assertIn(ACCOUNT, output.getvalue())
                    self.assertIn(f"найдено: {0 if empty else 1}", output.getvalue())
                    if not empty:
                        self.assertIn(NAME, output.getvalue())
                        self.assertIn(str(SEGMENT if action == "sources" else CONDITION),
                                      output.getvalue())

    def test_list_reports_rules_availability_and_uses_requested_ids(self):
        self.client.conditions = [condition(), condition(702, "Другое условие")]
        status, data = self.command("list", "--id", "701", "--id", "701")
        self.assertEqual(status, 0)
        self.assertEqual(data["conditions"], [condition()])
        service, params, account = self.client.get_calls[0]
        self.assertEqual((service, account), ("retargetinglists", ACCOUNT))
        self.assertEqual(params["SelectionCriteria"], {"Ids": [CONDITION]})
        self.assertIn("Rules", params["FieldNames"])
        self.assertIn("IsAvailable", params["FieldNames"])

    def test_list_cache_is_separate_for_each_selection_and_refreshes(self):
        self.client.conditions = [condition()]
        self.command("list")
        self.client.conditions.append(condition(702, "Новое условие"))
        _, cached = self.command("list")
        self.assertTrue(cached["cached"])
        self.assertEqual(len(cached["conditions"]), 1)
        _, selected = self.command("list", "--id", "702")
        self.assertEqual([item["Id"] for item in selected["conditions"]], [702])
        _, refreshed = self.command("list", "--no-cache")
        self.assertEqual(len(refreshed["conditions"]), 2)
        self.assertEqual(len(self.client.get_calls), 3)

    def test_list_rejects_partial_id_selection(self):
        self.client.conditions = [condition()]
        with self.assertRaisesRegex(DirectFailure, "702"):
            self.command("list", "--id", "701", "--id", "702")

    def test_create_defaults_to_preview_without_write_or_journal(self):
        status, data = self.create("--description", "Для корректировки ставок")
        self.assertEqual(status, 0)
        self.assertTrue(data["ok"])
        self.assertFalse(data["applied"])
        self.assertEqual(data["condition_ids"], [])
        self.assertTrue(data["preview"])
        self.assertEqual(self.client.batch_calls, [])
        self.assertFalse((self.root / "journal").exists())

    def test_create_rejects_goal_audience_segment_and_unavailable_id(self):
        for records in ([source(kind="goal")], [source(kind="audience_segment")],
                        [source(SEGMENT + 1)]):
            with self.subTest(records=records):
                self.client.sources = records
                with self.assertRaisesRegex(DirectFailure, "недоступен"):
                    self.create("--apply")
        self.assertEqual(self.client.batch_calls, [])

    def test_create_checks_fresh_sources_despite_cached_segment(self):
        self.command("sources")
        self.client.sources = []
        with self.assertRaisesRegex(DirectFailure, "недоступен"):
            self.create("--apply")
        self.assertEqual(self.client.source_calls, [[ACCOUNT], [ACCOUNT]])
        self.assertEqual(self.client.batch_calls, [])

    def test_create_checks_duplicate_name_despite_cached_empty_list(self):
        self.command("list")
        self.client.conditions = [condition()]
        self.client.conditions[0]["Rules"][0]["Arguments"][0]["ExternalId"] = SEGMENT + 1
        with self.assertRaisesRegex(DirectFailure, "уже существует: 701"):
            self.create("--apply")
        self.assertEqual(len(self.client.get_calls), 2)
        self.assertEqual(self.client.batch_calls, [])

    def test_create_rejects_same_segment_rule_under_another_name(self):
        self.command("list")
        existing = condition(name="Уже настроенный сегмент")
        existing["Rules"][0]["Arguments"][0]["MembershipLifeSpan"] = 540
        self.client.conditions = [existing]
        with self.assertRaisesRegex(DirectFailure, "с таким правилом уже существует") as failure:
            self.create("--apply")
        self.assertIn(str(CONDITION), str(failure.exception))
        self.assertIn(existing["Name"], str(failure.exception))
        self.assertIn("используйте существующее условие", str(failure.exception))
        self.assertEqual(len(self.client.get_calls), 2)
        self.assertEqual(self.client.batch_calls, [])

    def test_create_does_not_confuse_other_conditions_with_same_rule(self):
        for variant in ("other_segment", "none", "audience", "several_rules", "several_arguments"):
            with self.subTest(variant=variant):
                existing = condition(name="Другое условие")
                rule = existing["Rules"][0]
                if variant == "other_segment":
                    rule["Arguments"][0]["ExternalId"] = SEGMENT + 1
                elif variant == "none":
                    rule["Operator"] = "NONE"
                elif variant == "audience":
                    existing["Type"] = "AUDIENCE"
                elif variant == "several_rules":
                    existing["Rules"].append(copy.deepcopy(rule))
                else:
                    rule["Arguments"].append({"ExternalId": SEGMENT + 1})
                self.client.conditions = [existing]
                status, data = self.create()
                self.assertEqual(status, 0)
                self.assertTrue(data["ok"])
        self.assertEqual(self.client.batch_calls, [])

    def test_create_validates_arguments_before_connecting(self):
        for name in (" ", "я" * 251):
            with self.subTest(name=name[:10]):
                with self.assertRaises(DirectFailure):
                    self.command("create", "--segment", str(SEGMENT), "--name", name)
        self.client_factory.assert_not_called()
        for identifier in ("0", "-1", "1.5"):
            with self.subTest(identifier=identifier), redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit):
                    self.command("create", "--segment", identifier, "--name", NAME)
        self.client_factory.assert_not_called()

    def test_apply_sends_segment_rule_and_checks_actual_get(self):
        self.command("sources")
        status, data = self.create("--apply", "--description", "Для ставок")
        self.assertEqual(status, 0)
        self.assertEqual(data["condition_ids"], [CONDITION])
        self.assertEqual(data["written"], [str(CONDITION)])
        self.assertEqual(len(self.client.batch_calls), 1)
        service, method, params, account = self.client.batch_calls[0]
        self.assertEqual((service, method, account), ("retargetinglists", "add", ACCOUNT))
        self.assertEqual(params, {"RetargetingLists": [{
            "Type": "RETARGETING", "Name": NAME, "Description": "Для ставок",
            "Rules": [{"Operator": "ANY", "Arguments": [{"ExternalId": SEGMENT}]}]}]})
        self.assertEqual(self.client.get_calls[-1][1]["SelectionCriteria"], {"Ids": [CONDITION]})
        entries = self.journal()
        self.assertEqual(entries[-1]["outcome"], writer.WRITTEN)
        self.assertEqual(entries[-1]["after"]["Id"], CONDITION)
        self.assertTrue(all(login == ACCOUNT and need > 0
                            for login, need in self.accounts.unit_calls))
        _, sources = self.command("sources")
        self.assertFalse(sources["cached"], "Запись должна отзывать кеш кабинета")

    def test_apply_reports_api_item_error_without_success(self):
        self.client.write_result = "rejected"
        status, data = self.create("--apply")
        self.assertEqual(status, 1)
        self.assertFalse(data["ok"])
        self.assertTrue(data["failed"])
        self.assertEqual(data["condition_ids"], [])
        self.assertEqual(len(self.client.batch_calls), 1)
        self.assertEqual(self.journal()[-1]["outcome"], writer.ITEM_ERROR)

    def test_apply_detects_rules_changed_after_successful_add(self):
        self.client.write_result = "different_rules"
        status, data = self.create("--apply")
        self.assertEqual(status, 1)
        self.assertEqual(data["accepted"], [str(CONDITION)])
        self.assertEqual(data["condition_ids"], [])
        self.assertTrue(data["differences"])
        self.assertIn("Rules", " ".join(data["differences"]))
        self.assertEqual(self.journal()[-1]["outcome"], writer.UNEXPLAINED)

    def test_unknown_add_outcome_is_searched_but_never_retried(self):
        self.client.write_result = "unknown"
        status, data = self.create("--apply")
        self.assertEqual(status, 1)
        self.assertTrue(data["unknown"])
        self.assertEqual(data["condition_ids"], [])
        self.assertEqual(len(self.client.batch_calls), 1)
        self.assertEqual(self.client.get_calls[-1][1]["SelectionCriteria"],
                         {"Types": ["RETARGETING"]})
        self.assertEqual(self.journal()[-1]["outcome"], writer.UNVERIFIED)

    def test_successful_add_with_unreadable_result_is_not_reported_as_written(self):
        self.client.write_result = "unreadable"
        status, data = self.create("--apply")
        self.assertEqual(status, 1)
        self.assertEqual(data["accepted"], [str(CONDITION)])
        self.assertEqual(data["condition_ids"], [])
        self.assertTrue(data["unknown"])
        self.assertEqual(len(self.client.batch_calls), 1)

    def test_shared_reader_deduplicates_ids_and_skips_empty_selection(self):
        self.client.conditions = [condition(), condition(702)]
        found = retargeting_lists.read_lists(self.client, ACCOUNT, self.accounts,
                                            [702, CONDITION, 702])
        self.assertEqual(set(found), {CONDITION, 702})
        self.assertEqual(self.client.get_calls[0][1]["SelectionCriteria"],
                         {"Ids": [702, CONDITION]})
        self.assertEqual(retargeting_lists.read_lists(self.client, ACCOUNT, self.accounts, []), {})
        self.assertEqual(len(self.client.get_calls), 1)
        self.assertIs(ads_write.read_lists, retargeting_lists.read_lists)
        self.assertIs(retargeting.read_lists, retargeting_lists.read_lists)

    def test_shared_reader_rejects_unrequested_condition(self):
        with patch.object(self.client, "get_all", return_value=iter([condition(999)])):
            with self.assertRaisesRegex(DirectFailure, "незапрошенное условие 999"):
                retargeting_lists.read_lists(self.client, ACCOUNT, self.accounts, [CONDITION])


if __name__ == "__main__":
    unittest.main()
