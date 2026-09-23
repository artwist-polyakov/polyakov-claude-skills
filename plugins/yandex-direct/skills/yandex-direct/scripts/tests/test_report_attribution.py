#!/usr/bin/env python3
"""Проверки запросов Reports без сети и доступа к настройкам кабинета."""

from pathlib import Path
import sys
from types import SimpleNamespace
import unittest


SCRIPTS = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(SCRIPTS), str(SCRIPTS / "lib")]

import audit_combinatorial
import report
import reports


class ReportAttributionTests(unittest.TestCase):
    def setUp(self):
        self.reference = reports.Reference.load()
        self.period = {"kind": "CUSTOM_DATE", "from": "2026-09-01", "to": "2026-09-07"}

    def build(self, *options):
        parser = report.make_parser(self.reference)
        args = report.settled(parser.parse_args(["--preset", "campaigns", *options]), parser)
        params = report.build(self.reference, args, parser, chosen_period=self.period)
        self.assertEqual(reports.check(self.reference, params), [])
        heading = "\n".join(report.head_lines(
            "example", args, self.reference, params, self.period,
            SimpleNamespace(hit=False),
            SimpleNamespace(rows=[], pages=1, truncated=False), "RUB", "online"))
        return params, heading

    def test_traffic_only_omits_attribution_even_if_explicit(self):
        for options in ((), ("--attribution", "LC")):
            with self.subTest(options=options):
                params, heading = self.build("--traffic-only", *options)
                self.assertNotIn("Goals", params)
                self.assertNotIn("AttributionModels", params)
                self.assertNotIn("атрибуция", heading)
                self.assertTrue({"Impressions", "Clicks", "Cost"} <= set(params["FieldNames"]))
                self.assertFalse(reports.CONVERSION_FIELDS & set(params["FieldNames"]))

    def test_goal_reports_keep_default_and_explicit_models(self):
        for options, models in (((), ["AUTO"]),
                                (("--attribution", "LC"), ["LC"]),
                                (("--attribution", "AUTO,LC"), ["AUTO", "LC"])):
            with self.subTest(models=models):
                params, heading = self.build("--goals", "101,102", *options)
                self.assertEqual(params["Goals"], ["101", "102"])
                self.assertEqual(params["AttributionModels"], models)
                self.assertIn("атрибуция " + ", ".join(models), heading)

    def test_audit_spend_omits_attribution_and_keeps_scope(self):
        params = audit_combinatorial.spend_params(self.reference, self.period, [123])
        self.assertNotIn("Goals", params)
        self.assertNotIn("AttributionModels", params)
        self.assertEqual(set(params["FieldNames"]), {"AdId", "Impressions", "Clicks", "Cost"})
        self.assertEqual(params["SelectionCriteria"], {
            "DateFrom": self.period["from"], "DateTo": self.period["to"],
            "Filter": [{"Field": "CampaignId", "Operator": "IN", "Values": ["123"]}]})
        self.assertEqual(params["OrderBy"], [{"Field": "Cost", "SortOrder": "DESCENDING"}])
        self.assertEqual(reports.check(self.reference, params), [])


if __name__ == "__main__":
    unittest.main()
