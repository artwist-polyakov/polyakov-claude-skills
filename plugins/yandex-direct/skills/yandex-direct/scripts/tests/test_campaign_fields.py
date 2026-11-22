#!/usr/bin/env python3
"""Произвольные поля кампании: сохранность данных и исход записи без сети."""

import copy
import io
import json
from contextlib import redirect_stderr
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

SCRIPTS = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(SCRIPTS), str(SCRIPTS / "lib")]

import campaign_write as command
import cache
from config import DirectFailure
from direct import BatchEntry, BatchResult, Response
from errors import ApiFailure, ItemIssue, TransportFailure
from writer import AuditLog, Limits, Task, Writer


PERSONALIZATION = "UnifiedCampaign.Settings.ALTERNATIVE_TEXTS_ENABLED=NO"
READ_ONLY_SETTINGS = {"SHARED_ACCOUNT_ENABLED", "DAILY_BUDGET_ALLOWED"}


def merge(current, patch):
    for key, value in patch.items():
        if isinstance(value, dict):
            merge(current.setdefault(key, {}), value)
        elif key == "Settings":
            # Campaigns.update меняет названные Option, остальные сохраняет.
            for setting in value:
                if setting["Option"] in READ_ONLY_SETTINGS:
                    raise ApiFailure(8000, message="Настройка доступна только для чтения")
                existing = next((entry for entry in current[key]
                                 if entry["Option"] == setting["Option"]), None)
                if existing is None:
                    current[key].append(copy.deepcopy(setting))
                else:
                    existing.update(copy.deepcopy(setting))
        else:
            current[key] = copy.deepcopy(value)


class FakeClient:
    def __init__(self, record, outcome="success"):
        self.record = copy.deepcopy(record)
        self.outcome = outcome
        self.writes = []
        self.reads = []

    def get_all(self, service, params, **kwargs):
        kwargs.get("use_operator_units", lambda: None)()
        self.reads.append(copy.deepcopy(params))
        if self.writes and self.outcome == "timeout_blind":
            raise TransportFailure("Перечитывание недоступно")
        result = copy.deepcopy(self.record)
        if self.writes and self.outcome == "missing":
            result["UnifiedCampaign"].pop("Settings")
        if self.writes and self.outcome in {"lost_setting", "timeout_lost_setting"}:
            result["UnifiedCampaign"]["Settings"] = [
                setting for setting in result["UnifiedCampaign"]["Settings"]
                if setting["Option"] != "SHARED_ACCOUNT_ENABLED"]
        if self.writes and self.outcome in {"changed_setting", "timeout_changed_setting"}:
            for setting in result["UnifiedCampaign"]["Settings"]:
                if setting["Option"] == "SHARED_ACCOUNT_ENABLED":
                    setting["Value"] = "NO"
        return iter([result])

    def batch(self, service, method, params, *, items, results_key, **kwargs):
        kwargs.get("use_operator_units", lambda: None)()
        self.writes.append(copy.deepcopy(params))
        if self.outcome == "api_error":
            raise ApiFailure(8000, message="Неподдерживаемое поле")
        if self.outcome in {"timeout", "timeout_blind"}:
            raise TransportFailure("Ответ записи не получен")
        issues = []
        if self.outcome == "item_error":
            issues = [ItemIssue("error", 8000, "Поле отклонено")]
        elif self.outcome == "partial":
            self.record["NewField"] = items[0]["NewField"]
        elif self.outcome != "unchanged":
            merge(self.record, items[0])
        if self.outcome in {"timeout_applied", "timeout_lost_setting", "timeout_changed_setting"}:
            raise TransportFailure("Ответ потерян после записи")
        if self.outcome == "warning":
            issues = [ItemIssue("warning", 10161, "Значение не применяется")]
        return BatchResult(
            [BatchEntry(0, identifier=self.record["Id"], id_field="Id", issues=issues)],
            Response(request_id="offline-fields"), results_key)


class CampaignFieldsTests(unittest.TestCase):
    def setUp(self):
        self.record = {
            "Id": 123, "Name": "Кампания", "Type": command.UNIFIED,
            "NewField": "old", "NewFlag": False,
            "UnifiedCampaign": {
                "Settings": [
                    {"Option": "ALTERNATIVE_TEXTS_ENABLED", "Value": "YES"},
                    {"Option": "ENABLE_SITE_MONITORING", "Value": "YES"},
                    {"Option": "ADD_METRICA_TAG", "Value": "NO"},
                    {"Option": "SHARED_ACCOUNT_ENABLED", "Value": "YES"}],
                "NewOptions": {"Enabled": True, "Amount": 2, "Label": "сохранить"},
                "CounterIds": {"Items": [12345]},
                "BiddingStrategy": {"Search": {"BiddingStrategyType": "HIGHEST_POSITION"}},
                "PackageBiddingStrategy": None,
            },
        }

    def args(self, *options):
        return command.build_parser().parse_args([
            "fields", "--account", "example", "--campaign", "123", *options])

    def operation(self, *options):
        with patch.object(command, "one_campaign", return_value=copy.deepcopy(self.record)):
            result = command.build_task(None, "example", None, self.args(*options))
        self.assertEqual(len(result[1]), 1)
        return result[1][0]

    def body_operation(self, body):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "fields.json"
            path.write_text(body if isinstance(body, str) else json.dumps(body), encoding="utf-8")
            return self.operation("--body", str(path))

    def execute(self, operation, client, *, apply=True, show=None):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            journal = AuditLog("example", root=root / "journal")
            warnings = []
            engine = Writer(
                client, "example", apply=apply, limits=Limits.load(),
                cache=cache.Cache("example", root=root / "cache"), journal=journal,
                show=show or (lambda preview: None), warn=warnings.append)
            report = engine.run(Task("Изменить поля кампании", [operation]))
            return report, journal.entries(), warnings

    def test_preview_and_apply_preserve_other_settings_and_campaign_fields(self):
        operation = self.operation("--set", PERSONALIZATION)
        self.assertFalse(self.args("--set", PERSONALIZATION).apply)
        for apply in (False, True):
            with self.subTest(apply=apply):
                client = FakeClient(self.record)
                previews = []
                report, journal, _ = self.execute(operation, client, apply=apply, show=previews.append)
                self.assertTrue(report.ok, vars(report))
                self.assertEqual(len(client.writes), int(apply))
                expected = copy.deepcopy(self.record)
                if apply:
                    expected["UnifiedCampaign"]["Settings"][0]["Value"] = "NO"
                    self.assertEqual(report.written, [123])
                    self.assertEqual(client.writes[0]["Campaigns"][0]["UnifiedCampaign"]["Settings"],
                                     [{"Option": "ALTERNATIVE_TEXTS_ENABLED", "Value": "NO"}])
                    self.assertTrue(any(row.get("kind") == "снимок" for row in journal))
                    self.assertTrue(any(row.get("after") for row in journal))
                self.assertEqual(client.record, expected)
                self.assertEqual(len(previews), 1)
                plan = previews[0].text()
                for visible in ("123", "ALTERNATIVE_TEXTS_ENABLED", "YES", "NO"):
                    self.assertIn(visible, plan)

    def test_json_body_merges_settings_like_set(self):
        for kind, prefix, option in ((command.UNIFIED, "UnifiedCampaign", "ALTERNATIVE_TEXTS_ENABLED"),
                                     (command.TEXT, "TextCampaign", "NEW_API_OPTION")):
            with self.subTest(kind=kind, option=option):
                if prefix != "UnifiedCampaign":
                    self.record[prefix] = self.record.pop("UnifiedCampaign")
                    self.record[prefix]["Settings"].append(
                        {"Option": "DAILY_BUDGET_ALLOWED", "Value": "NO"})
                self.record["Type"] = kind
                self.record[prefix]["Settings"][0]["Option"] = option
                operation = self.body_operation({prefix: {"Settings": [
                    {"Option": option, "Value": "NO"}]}})
                equivalent = self.operation("--set", f"{prefix}.Settings.{option}=NO")
                self.assertEqual(operation.items, equivalent.items)
                client = FakeClient(self.record)
                report, _, _ = self.execute(operation, client)
                self.assertTrue(report.ok, vars(report))
                self.assertEqual(client.writes[0]["Campaigns"][0][prefix]["Settings"],
                                 [{"Option": option, "Value": "NO"}])
                self.assertEqual(client.record[prefix]["Settings"][0]["Value"], "NO")
                self.assertEqual(client.record[prefix]["Settings"][1:],
                                 self.record[prefix]["Settings"][1:])

    def test_new_fields_are_read_dynamically_and_keep_json_types(self):
        options = ("--set", "NewField=новое", "--set", "NewFlag=true",
                   "--set", "UnifiedCampaign.NewOptions.Amount=3")
        with patch.object(command, "one_campaign", return_value=copy.deepcopy(self.record)) as read:
            task = command.build_task(None, "example", None, self.args(*options))
        operation = task[1][0]
        self.assertIn("NewField", operation.read["FieldNames"])
        self.assertIn("NewFlag", operation.read["FieldNames"])
        self.assertIn("NewOptions", operation.read["UnifiedCampaignFieldNames"])
        self.assertTrue(any("NewField" in call.args[-1].get("FieldNames", [])
                            for call in read.call_args_list))
        client = FakeClient(self.record)
        report, _, _ = self.execute(operation, client)
        self.assertTrue(report.ok, vars(report))
        self.assertIs(client.record["NewFlag"], True)
        self.assertEqual(client.record["NewField"], "новое")
        self.assertEqual(client.record["UnifiedCampaign"]["NewOptions"],
                         {"Enabled": True, "Amount": 3, "Label": "сохранить"})

    def test_unknown_or_absent_values_are_rejected_before_operation(self):
        for expression in ("Missing=value", "UnifiedCampaign.NewOptions.Missing=1",
                           "UnifiedCampaign.Settings.UNKNOWN_OPTION=NO"):
            with self.subTest(expression=expression), self.assertRaises(DirectFailure):
                self.operation("--set", expression)

    def test_domain_actions_and_read_only_fields_cannot_be_bypassed(self):
        protected = [
            "Id=456", "Type=TEXT_CAMPAIGN", "State=ON", "Status=DRAFT", "Name=новое",
            "StartDate=2030-01-01", "EndDate=2030-01-02", "DailyBudget.Amount=1000000",
            "TimeTargeting.Schedule=новое", "BlockedIps=новое", "ExcludedSites=новое",
            "NegativeKeywords=новое", "UnifiedCampaign.CounterIds=новое",
            "UnifiedCampaign.NegativeKeywordSharedSetIds=новое",
            "UnifiedCampaign.BiddingStrategy.Search.BiddingStrategyType=SERVING_OFF",
            "UnifiedCampaign.PriorityGoals=новое", "UnifiedCampaign.AttributionModel=AUTO",
            "UnifiedCampaign.PackageBiddingStrategy.StrategyId=1",
            "UnifiedCampaign.TrackingParams=utm_source=test",
            "UnifiedCampaign.Settings.ENABLE_SITE_MONITORING=NO",
            "UnifiedCampaign.Settings.SHARED_ACCOUNT_ENABLED=NO",
            "UnifiedCampaign.Settings.DAILY_BUDGET_ALLOWED=YES",
        ]
        for expression in protected:
            with self.subTest(expression=expression), self.assertRaises(DirectFailure):
                self.operation("--set", expression)
        with self.assertRaises(DirectFailure):
            self.body_operation({"UnifiedCampaign": {"Settings": [
                {"Option": "ENABLE_SITE_MONITORING", "Value": "NO"}]}})
        for option in READ_ONLY_SETTINGS:
            with self.subTest(option=option), self.assertRaises(DirectFailure):
                self.body_operation({"UnifiedCampaign": {"Settings": [
                    {"Option": option, "Value": "NO"}]}})

    def test_wrong_campaign_type_and_package_strategy_are_rejected(self):
        for kind, package in (("CPM_BANNER_CAMPAIGN", None),
                              (command.UNIFIED, {"StrategyId": 999})):
            with self.subTest(kind=kind, package=package):
                self.record["Type"] = kind
                self.record["UnifiedCampaign"]["PackageBiddingStrategy"] = package
                with self.assertRaises(DirectFailure):
                    self.operation("--set", PERSONALIZATION)

    def test_ambiguous_replacements_and_duplicates_are_rejected(self):
        invalid_bodies = [
            {}, {"Id": 123, "NewField": "new"}, {"method": "update", "params": {}},
            {"NewField": None}, {"NewField": ["new"]},
            {"UnifiedCampaign": {"Settings": [
                {"Option": "ALTERNATIVE_TEXTS_ENABLED", "Value": "NO"},
                {"Option": "ALTERNATIVE_TEXTS_ENABLED", "Value": "YES"}]}},
            '{"NewField":"first","NewField":"second"}',
            '{"NewField":1e999}',
        ]
        for body in invalid_bodies:
            with self.subTest(body=body), self.assertRaises(DirectFailure):
                self.body_operation(body)
        for expressions in (("NewField=1e999",),
                            ("NewField=first", "NewField=second"),
                            ('UnifiedCampaign.NewOptions={"Amount":3}',
                             "UnifiedCampaign.NewOptions.Amount=4")):
            with self.subTest(expressions=expressions), self.assertRaises(DirectFailure):
                self.operation(*[arg for value in expressions for arg in ("--set", value)])

    def test_set_and_body_are_mutually_exclusive(self):
        with redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            self.args("--set", PERSONALIZATION, "--body", "ignored.json")

    def test_changed_settings_or_package_before_writer_prevent_write(self):
        operation = self.operation("--set", PERSONALIZATION)
        for field in ("Settings", "PackageBiddingStrategy"):
            with self.subTest(field=field):
                client = FakeClient(self.record)
                if field == "Settings":
                    client.record["UnifiedCampaign"][field][1]["Value"] = "NO"
                else:
                    client.record["UnifiedCampaign"][field] = {"StrategyId": 999}
                report, _, _ = self.execute(operation, client)
                self.assertFalse(report.ok, vars(report))
                self.assertEqual(client.writes, [])

    def test_change_after_preview_prevents_write(self):
        operation = self.operation("--set", PERSONALIZATION)
        client = FakeClient(self.record)

        def changed(preview):
            client.record["UnifiedCampaign"]["Settings"][2]["Value"] = "YES"

        report, _, _ = self.execute(operation, client, show=changed)
        self.assertFalse(report.ok, vars(report))
        self.assertEqual(client.writes, [])

    def test_missing_or_ignored_readback_is_not_success(self):
        operation = self.operation("--set", PERSONALIZATION)
        for outcome in ("missing", "unchanged", "lost_setting", "changed_setting"):
            with self.subTest(outcome=outcome):
                client = FakeClient(self.record, outcome)
                report, _, _ = self.execute(operation, client)
                self.assertFalse(report.ok, vars(report))
                self.assertEqual(report.written, [])
                self.assertTrue(report.differences or report.unknown, vars(report))
                self.assertEqual(len(client.writes), 1)

    def test_partial_field_update_reports_difference(self):
        operation = self.operation("--set", "NewField=new", "--set", "NewFlag=true")
        client = FakeClient(self.record, "partial")
        report, journal, _ = self.execute(operation, client)
        self.assertFalse(report.ok, vars(report))
        self.assertEqual(report.written, [])
        self.assertTrue(report.differences, vars(report))
        self.assertTrue(any(row.get("differences") for row in journal))

    def test_api_error_and_warning_remain_visible(self):
        operation = self.operation("--set", PERSONALIZATION)
        for outcome in ("item_error", "api_error", "warning"):
            with self.subTest(outcome=outcome):
                client = FakeClient(self.record, outcome)
                report, journal, _ = self.execute(operation, client)
                self.assertFalse(report.ok, vars(report))
                self.assertTrue(report.failed or report.differences, vars(report))
                self.assertEqual(len(client.writes), 1)
                self.assertTrue(journal)
                if outcome == "warning":
                    self.assertTrue(any("10161" in line for line in report.differences))
                    self.assertTrue(any("10161" in issue for row in journal
                                        for issue in row.get("issues", [])))

    def test_uncertain_response_is_reread_and_never_retried(self):
        operation = self.operation("--set", PERSONALIZATION)
        for outcome, field in (("timeout", "failed"), ("timeout_applied", "despite"),
                               ("timeout_blind", "unknown"),
                               ("timeout_lost_setting", "differences"),
                               ("timeout_changed_setting", "differences")):
            with self.subTest(outcome=outcome):
                client = FakeClient(self.record, outcome)
                report, journal, _ = self.execute(operation, client)
                self.assertFalse(report.ok, vars(report))
                self.assertTrue(getattr(report, field), vars(report))
                self.assertEqual(len(client.writes), 1)
                self.assertGreaterEqual(len(client.reads), 3)
                self.assertTrue(any(row.get("reread") for row in journal))
                if outcome in {"timeout_lost_setting", "timeout_changed_setting"}:
                    self.assertEqual(report.written, [])
                    self.assertEqual(report.despite, [])


if __name__ == "__main__":
    unittest.main()
