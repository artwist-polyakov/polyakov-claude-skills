"""Новые строковые константы не отменяют проверку числовых ограничений."""

from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))
from constants import Constants
from errors import TransportFailure
from writer import Limits


class ConstantsTests(unittest.TestCase):
    def test_string_constant_preserves_numeric_limits(self):
        limits = Limits.load()
        original = limits.data["text"]["fields"]["TextAd.Title"]["max_length"]
        items = [
            {"Name": "MaximumTextAdTitleLength", "Value": "60"},
            {"Name": "MaximumSitelinksLength", "Value": "76"},
            {"Name": "AdaptiveBudgetAutoEnableDate", "Value": "2026-09-17"},
        ]
        constants = Constants.parse(items)
        fresh = constants.apply(limits)
        self.assertEqual(fresh.data["text"]["fields"]["TextAd.Title"]["max_length"], 60)
        self.assertEqual(fresh.runtime["MaximumSitelinksLength"], 76)
        self.assertEqual(constants.unknown(limits), ["AdaptiveBudgetAutoEnableDate"])
        self.assertNotIn("MaximumTextAdTitleLength", constants.missing(limits))
        self.assertEqual(constants.items, items)
        self.assertIn(
            ("MaximumTextAdTitleLength", ("text", "fields", "TextAd.Title", "max_length"),
             original, 60), constants.differences(limits))
        self.assertEqual(constants.differences(fresh), [])
        self.assertEqual(limits.data["text"]["fields"]["TextAd.Title"]["max_length"], original)

    def test_invalid_numeric_limit_still_fails(self):
        limits = Limits.load()
        for name in ("MaximumTextAdTitleLength", "MaximumSitelinksLength"):
            for value in ("2026-09-17", "5_6", "0", "-1"):
                with self.subTest(name=name, value=value):
                    constants = Constants.parse([{"Name": name, "Value": value}])
                    with self.assertRaises(TransportFailure):
                        constants.apply(limits)
                    if name == "MaximumTextAdTitleLength":
                        with self.assertRaises(TransportFailure):
                            constants.differences(limits)

    def test_invalid_value_type_fails_during_parse(self):
        for value in (None, True, False, [], {}, 1.5):
            with self.subTest(value=value):
                with self.assertRaises(TransportFailure):
                    Constants.parse([{"Name": "NewConstant", "Value": value}])


if __name__ == "__main__":
    unittest.main()
