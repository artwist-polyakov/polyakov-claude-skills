#!/usr/bin/env python3
"""Проверки без сети: python3 scripts/tests/test_campaign_goals.py."""

import copy
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch


SCRIPTS = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(SCRIPTS), str(SCRIPTS / "lib")]

import campaign_write as command
import campaigns
import cache
from config import DirectFailure
from direct import BatchEntry, BatchResult, Response
from writer import AuditLog, Limits, Task, Writer


class FakeClient:
    def __init__(self, record):
        self.record = copy.deepcopy(record)
        self.writes = []

    def get_all(self, service, params, **kwargs):
        return iter([copy.deepcopy(self.record)])

    def batch(self, service, method, params, *, items, results_key, **kwargs):
        self.writes.append(copy.deepcopy(items))
        body = campaigns.TYPE_BODY[self.record["Type"]]
        self.record[body].update(copy.deepcopy(items[0][body]))
        for goal in self.record[body]["PriorityGoals"]["Items"]:
            goal.pop("Operation", None)  # get не возвращает указание для update.
        return BatchResult(
            [BatchEntry(0, identifier=self.record["Id"], id_field="Id")],
            Response(request_id="offline-test"), results_key)


class CampaignGoalsTests(unittest.TestCase):
    def setUp(self):
        self.record = {
            "Id": 123, "Name": "Кампания", "Type": command.UNIFIED,
            "UnifiedCampaign": {
                "PriorityGoals": {"Items": [
                    {"GoalId": 101, "Value": 700000000},
                    {"GoalId": 102, "Value": 500000000}]},
                "BiddingStrategy": {
                    "Search": {"BiddingStrategyType": "AVERAGE_CPA_MULTIPLE_GOALS",
                               "AverageCpaMultipleGoals": {"WeeklySpendLimit": 10000000000}},
                    "Network": {"BiddingStrategyType": "SERVING_OFF"}},
                "CounterIds": {"Items": [12345]}, "AttributionModel": "LC"}}

    def args(self, *options):
        return command.build_parser().parse_args([
            "strategy", "--account", "example", "--campaign", "123", *options])

    def body(self, *options):
        return command.campaign_body(
            self.args(*options), self.record["Type"], creating=False, record=self.record)

    def operation(self, *options):
        with patch.object(command, "one_campaign", return_value=copy.deepcopy(self.record)):
            result = command.strategy_task(None, "example", None, self.args(*options))
        return result[1][0]

    def execute(self, operation, client, *, apply):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            engine = Writer(
                client, "example", apply=apply, limits=Limits.load(),
                cache=cache.Cache("example", root=root / "cache"),
                journal=AuditLog("example", root=root / "journal"),
                show=lambda preview: None, warn=lambda message: None)
            return engine.run(Task("Добавить цель", [operation]))

    def test_add_preserves_existing_goals_and_only_writes_goals(self):
        for kind in (command.UNIFIED, command.TEXT):
            with self.subTest(kind=kind):
                self.record["Type"] = kind
                self.record[campaigns.TYPE_BODY[kind]] = self.record["UnifiedCampaign"]
                before = copy.deepcopy(self.record)
                body = self.body("--add-goals", "--goal", "103=123.45")
                self.assertEqual(set(body), {"PriorityGoals"})
                self.assertEqual(body["PriorityGoals"]["Items"], [
                    {"GoalId": 101, "Value": 700000000, "Operation": "SET"},
                    {"GoalId": 102, "Value": 500000000, "Operation": "SET"},
                    {"GoalId": 103, "Value": 123450000, "Operation": "SET"}])
                self.assertEqual(self.record, before)

    def test_replace_is_explicit_list_and_omitted_goals_stay_untouched(self):
        body = self.body("--goal", "102=600", "--goal", "103=400")
        self.assertEqual([g["GoalId"] for g in body["PriorityGoals"]["Items"]], [102, 103])
        self.assertEqual(self.body("--attribution", "AUTO"), {"AttributionModel": "AUTO"})

    def test_add_updates_existing_value_without_duplicate_or_source_loss(self):
        current = self.record["UnifiedCampaign"]
        current["BiddingStrategy"]["Search"] = {"BiddingStrategyType": "AVERAGE_CRR"}
        current["PriorityGoals"]["Items"][0]["IsMetrikaSourceOfValue"] = "YES"
        goals = self.body("--add-goals", "--goal", "101=800", "--goal", "101=800",
                          "--goal", "103=400")["PriorityGoals"]["Items"]
        self.assertEqual(len(goals), 3)
        self.assertEqual(goals[0], {"GoalId": 101, "Value": 800000000,
                                  "IsMetrikaSourceOfValue": "YES", "Operation": "SET"})
        self.assertEqual(goals[2]["IsMetrikaSourceOfValue"], "NO")

    def test_conflicting_duplicates_are_rejected(self):
        with self.assertRaises(DirectFailure):
            self.body("--add-goals", "--goal", "103=400", "--goal", "103=500")

    def test_minimum_uses_final_list_and_maximum_includes_existing_goals(self):
        with self.assertRaises(DirectFailure):
            self.body("--goal", "103=400")
        self.assertEqual(len(self.body("--add-goals", "--goal", "103=400")
                             ["PriorityGoals"]["Items"]), 3)
        self.record["UnifiedCampaign"]["PriorityGoals"]["Items"] = [
            {"GoalId": i, "Value": 1000000} for i in range(100, 130)]
        with self.assertRaises(DirectFailure):
            self.body("--add-goals", "--goal", "130=400")

    def test_add_requires_named_goals_and_supports_empty_initial_list(self):
        with self.assertRaises(DirectFailure):
            self.body("--add-goals", "--attribution", "AUTO")
        self.record["UnifiedCampaign"]["PriorityGoals"] = None
        goals = self.body("--add-goals", "--goal", "101=700", "--goal", "102=500")
        self.assertEqual(len(goals["PriorityGoals"]["Items"]), 2)

    def test_single_to_multiple_conversion_prices_use_new_structure(self):
        self.record["UnifiedCampaign"]["BiddingStrategy"]["Search"] = {
            "BiddingStrategyType": "AVERAGE_CPA",
            "AverageCpa": {"GoalId": 101, "AverageCpa": 700000000,
                           "WeeklySpendLimit": 10000000000}}
        for code in ("AVERAGE_CPA_MULTIPLE_GOALS", "PAY_FOR_CONVERSION_MULTIPLE_GOALS"):
            with self.subTest(code=code):
                body = self.body("--search-strategy", code,
                                 "--search-param", "WeeklySpendLimit=10000",
                                 "--goal", "101=700", "--goal", "102=500")
                self.assertEqual(body["BiddingStrategy"]["Search"], {
                    "BiddingStrategyType": code,
                    command.field_of(code): {"WeeklySpendLimit": 10000000000}})

    def test_priority_goal_selector_and_creation_have_correct_shape(self):
        args = self.args("--search-strategy", "WB_MAXIMUM_CONVERSION_RATE",
                         "--search-param", "GoalId=13",
                         "--search-param", "WeeklySpendLimit=10000",
                         "--network-strategy", "SERVING_OFF",
                         "--placement", "SearchResults=YES", "--placement", "ProductGallery=NO",
                         "--placement", "Maps=NO", "--placement", "SearchOrganizationList=NO",
                         "--goal", "101=700", "--goal", "102=500")
        body = command.campaign_body(args, command.UNIFIED, creating=True)
        self.assertEqual(body["BiddingStrategy"]["Search"]["WbMaximumConversionRate"]["GoalId"], 13)
        self.assertTrue(all("Operation" not in g for g in body["PriorityGoals"]["Items"]))
        self.assertEqual(campaigns.goal_name(13), "все ключевые цели")

    def test_dry_run_and_apply_use_complete_list_and_verify_without_operation(self):
        for apply in (False, True):
            with self.subTest(apply=apply):
                operation = self.operation("--add-goals", "--goal", "103=400")
                client = FakeClient(self.record)
                report = self.execute(operation, client, apply=apply)
                self.assertTrue(report.ok, vars(report))
                self.assertEqual(len(client.writes), int(apply))
                if apply:
                    self.assertEqual(len(client.record["UnifiedCampaign"]["PriorityGoals"]["Items"]), 3)
                    self.assertTrue(report.written, vars(report))
                    self.assertEqual(client.record["UnifiedCampaign"]["CounterIds"], {"Items": [12345]})
                    self.assertEqual(client.record["UnifiedCampaign"]["AttributionModel"], "LC")

    def test_intervening_goal_or_strategy_change_prevents_write(self):
        for field in ("PriorityGoals", "BiddingStrategy"):
            with self.subTest(field=field):
                operation = self.operation("--add-goals", "--goal", "103=400")
                client = FakeClient(self.record)
                client.record["UnifiedCampaign"][field] = None
                report = self.execute(operation, client, apply=True)
                self.assertFalse(report.ok, vars(report))
                self.assertEqual(client.writes, [])

    def test_package_goals_are_not_written_on_campaign(self):
        self.record["UnifiedCampaign"]["PackageBiddingStrategy"] = {"StrategyId": 456}
        with self.assertRaises(DirectFailure):
            self.operation("--add-goals", "--goal", "103=400")


if __name__ == "__main__":
    unittest.main()
