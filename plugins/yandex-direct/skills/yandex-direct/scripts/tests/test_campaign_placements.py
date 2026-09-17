#!/usr/bin/env python3
"""Выбор мест показа и проверка частичной записи без API и конфигурации."""

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


GALLERY = {"SearchResults": "NO", "ProductGallery": "YES", "Maps": "NO",
           "SearchOrganizationList": "NO"}
NETWORK_PLACES = "UnifiedCampaign.BiddingStrategy.Network.PlacementTypes"
READ_PLACES = set(GALLERY) | {"DynamicPlaces"}


def placement_args(values):
    return [part for name, value in values.items()
            for part in ("--placement", f"{name}={value}")]


def merge(current, change):
    """API сохраняет поля частичной правки, которые не передавались."""
    for name, value in change.items():
        if isinstance(value, dict):
            merge(current.setdefault(name, {}), value)
        else:
            current[name] = copy.deepcopy(value)


class FakeClient:
    def __init__(self, record):
        self.record = copy.deepcopy(record)
        self.writes = []

    def get_all(self, service, params, **kwargs):
        result = copy.deepcopy(self.record)
        # Наличие поддерживаемого поля записи не означает, что get его отдаёт.
        result["UnifiedCampaign"]["BiddingStrategy"]["Network"].pop("PlacementTypes", None)
        return iter([result])

    def batch(self, service, method, params, *, items, results_key, **kwargs):
        self.writes.append(copy.deepcopy(items))
        merge(self.record, items[0])
        return BatchResult(
            [BatchEntry(0, identifier=self.record["Id"], id_field="Id")],
            Response(request_id="offline-test"), results_key)


class CampaignPlacementsTests(unittest.TestCase):
    def setUp(self):
        self.record = {
            "Id": 123, "Name": "Кампания", "Type": command.UNIFIED,
            "UnifiedCampaign": {
                "PriorityGoals": {"Items": [{"GoalId": 101, "Value": 700000000}]},
                "BiddingStrategy": {
                    "Search": {
                        "BiddingStrategyType": "AVERAGE_CPA",
                        "AverageCpa": {"GoalId": 101, "AverageCpa": 700000000,
                                       "WeeklySpendLimit": 10000000000},
                        "PlacementTypes": {name: "YES" for name in READ_PLACES}},
                    "Network": {"BiddingStrategyType": "SERVING_OFF"}},
                "CounterIds": {"Items": [12345]}, "AttributionModel": "AUTO"}}

    def args(self, *options):
        return command.build_parser().parse_args([
            "strategy", "--account", "example", "--campaign", "123", *options])

    def create_body(self, places, kind=command.UNIFIED):
        args = self.args("--search-strategy", "HIGHEST_POSITION",
                         "--network-strategy", "SERVING_OFF", *placement_args(places))
        return command.campaign_body(args, kind, creating=True)

    def operation(self, places):
        with patch.object(command, "one_campaign", return_value=copy.deepcopy(self.record)):
            task = command.strategy_task(None, "example", None, self.args(*placement_args(places)))
        return task[1][0], task[2]

    def execute(self, operation, client, *, apply=True):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            engine = Writer(
                client, "example", apply=apply, limits=Limits.load(),
                cache=cache.Cache("example", root=root / "cache"),
                journal=AuditLog("example", root=root / "journal"),
                show=lambda preview: None, warn=lambda message: None)
            return engine.run(Task("Изменить места показа", [operation]))

    def test_gallery_creation_excludes_other_places_and_network(self):
        body = self.create_body(GALLERY)
        self.assertEqual(body["BiddingStrategy"], {
            "Search": {"BiddingStrategyType": "HIGHEST_POSITION", "PlacementTypes": GALLERY},
            "Network": {"BiddingStrategyType": "SERVING_OFF", "PlacementTypes": {"Maps": "NO"}}})
        operation = command.add_operation(command.UNIFIED, "Галерея", body, start="2030-01-01")
        self.assertEqual(operation.unread, (NETWORK_PLACES,))

    def test_creation_requires_explicit_choices_and_rejects_ignored_dynamic_flag(self):
        for missing in (None, *GALLERY):
            places = {} if missing is None else {k: v for k, v in GALLERY.items() if k != missing}
            with self.subTest(missing=missing), self.assertRaises(DirectFailure):
                self.create_body(places)
        with self.assertRaises(DirectFailure):
            self.create_body({**GALLERY, "DynamicPlaces": "NO"})
        body = self.create_body({"SearchResults": "YES", "ProductGallery": "NO"}, command.TEXT)
        self.assertEqual(body["BiddingStrategy"]["Network"], {"BiddingStrategyType": "SERVING_OFF"})

    def test_partial_update_preserves_strategy_budget_goals_and_omitted_places(self):
        operation, _ = self.operation({"SearchResults": "NO"})
        self.assertEqual(operation.items, [{"Id": 123, "UnifiedCampaign": {"BiddingStrategy": {
            "Search": {"BiddingStrategyType": "AVERAGE_CPA", "PlacementTypes": {"SearchResults": "NO"}}}}}])
        for apply in (False, True):
            with self.subTest(apply=apply):
                client = FakeClient(self.record)
                report = self.execute(operation, client, apply=apply)
                self.assertTrue(report.ok, vars(report))
                self.assertEqual(len(client.writes), int(apply))
                expected = copy.deepcopy(self.record)
                if apply:
                    expected["UnifiedCampaign"]["BiddingStrategy"]["Search"]["PlacementTypes"]["SearchResults"] = "NO"
                self.assertEqual(client.record, expected)

    def test_maps_are_written_in_both_halves_but_missing_readback_remains_unchecked(self):
        operation, notes = self.operation({"Maps": "NO"})
        halves = operation.items[0]["UnifiedCampaign"]["BiddingStrategy"]
        self.assertEqual(halves["Search"]["PlacementTypes"], {"Maps": "NO"})
        self.assertEqual(halves["Network"], {"BiddingStrategyType": "SERVING_OFF",
                                          "PlacementTypes": {"Maps": "NO"}})
        self.assertEqual(operation.unread, (NETWORK_PLACES,))
        self.assertEqual(set(operation.read["UnifiedCampaignSearchStrategyPlacementTypesFieldNames"]), READ_PLACES)
        self.assertTrue(any("интерфейс" in note and "Карт" in note for note in notes))
        report = self.execute(operation, FakeClient(self.record))
        self.assertTrue(report.ok, vars(report))
        self.assertTrue(report.written, vars(report))
        self.assertEqual(len(report.unchecked), 1)
        self.assertIn(NETWORK_PLACES, report.unchecked[0])
        self.assertIn("без перечитывания", report.summary())

    def test_intervening_strategy_change_prevents_write(self):
        operation, _ = self.operation({"ProductGallery": "NO"})
        for field, value in (("BiddingStrategyType", "HIGHEST_POSITION"),
                             ("PlacementTypes", {"SearchResults": "NO"})):
            with self.subTest(field=field):
                client = FakeClient(self.record)
                client.record["UnifiedCampaign"]["BiddingStrategy"]["Search"][field] = value
                report = self.execute(operation, client)
                self.assertFalse(report.ok, vars(report))
                self.assertEqual(client.writes, [])

    def test_placement_update_requires_known_current_strategy_before_operation(self):
        original = copy.deepcopy(self.record)
        for half in ("Search", "Network"):
            for code in (None, "UNKNOWN", "UNSUPPORTED_STRATEGY"):
                with self.subTest(half=half, code=code):
                    self.record = copy.deepcopy(original)
                    self.record["UnifiedCampaign"]["BiddingStrategy"][half]["BiddingStrategyType"] = code
                    with patch.object(command, "update_operation") as build:
                        with self.assertRaises(DirectFailure):
                            self.operation({"Maps": "NO"})
                        build.assert_not_called()

    def test_campaign_read_requests_all_search_placements(self):
        params = campaigns.request_params()
        self.assertEqual(set(params["UnifiedCampaignSearchStrategyPlacementTypesFieldNames"]), READ_PLACES)


if __name__ == "__main__":
    unittest.main()
