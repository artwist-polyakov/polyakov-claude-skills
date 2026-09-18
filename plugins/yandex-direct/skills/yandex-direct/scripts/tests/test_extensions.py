#!/usr/bin/env python3
"""Привязки дополнений к объявлениям: проверки CLI и Writer без сети."""

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

import extensions
import cache
import writer
from direct import BatchEntry, BatchResult, Response
from errors import TransportFailure


ACCOUNT, AD = "offline-advertiser", 101
BODIES = {"SHOPPING_AD": "ShoppingAd", "LISTING_AD": "ListingAd",
          "TEXT_AD": "TextAd", "RESPONSIVE_AD": "ResponsiveAd"}
PRODUCT_TYPES = ("SHOPPING_AD", "LISTING_AD")


def callout(identifier):
    return {"AdExtensionId": identifier, "Type": "CALLOUT"}


def ad(kind="SHOPPING_AD"):
    body = {"SitelinkSetId": 201, "AdExtensions": [callout(301)]}
    if kind in PRODUCT_TYPES:
        body.update(FeedId=401, DefaultTexts=["Прежний текст"],
                    FeedFilterConditions={"Items": [{"Operand": "id", "Operator": "EQUALS_ANY",
                                                     "Arguments": ["product-1"]}]},
                    TitleSources={"Items": ["name"]}, TextSources={"Items": ["description"]})
    else:
        body["Href"] = "https://example.test/"
        if kind == "RESPONSIVE_AD":
            body.update(Titles=[{"Title": "Первый заголовок"}, {"Title": "Второй заголовок"}],
                        Texts=[{"Text": "Первый текст"}, {"Text": "Второй текст"}])
        else:
            body.update(Title="Заголовок", Title2="Продолжение", Text="Текст")
    return {"Id": AD, "Type": kind, "State": "OFF", "Status": "DRAFT", BODIES[kind]: body}


class FakeAccounts:
    def current(self):
        return SimpleNamespace(login=ACCOUNT)

    def use_operator_units(self, account, *, need):
        return False


class FakeClient:
    def __init__(self):
        self.settings = SimpleNamespace(profile="production", account=ACCOUNT)
        self.ads = [ad()]
        self.calls, self.writes = [], []
        self.ads_reads = self.reads_after_write = 0
        self.change_before_write = False
        self.fail_final_read = self.corrupt_final_read = False
        self.malformed_final_read = None
        self.sitelinks = [{"Id": identifier, "Sitelinks": [
            {"Title": title, "Href": f"https://example.test/{identifier}", "Description": "Описание"}]}
            for identifier, title in ((201, "Прежний раздел"), (202, "Новый раздел"))]
        self.callouts = [{"Id": identifier, "Type": "CALLOUT", "State": "ON",
                          "Callout": {"CalloutText": text}}
                         for identifier, text in ((301, "Прежнее уточнение"), (302, "Новое уточнение"))]

    def get_all(self, service, params, *, account, use_operator_units, collection=None):
        self.calls.append((service, copy.deepcopy(params)))
        use_operator_units()
        if service == "ads":
            self.ads_reads += 1
            if self.change_before_write and self.ads_reads == 3:
                self.ads[0][BODIES[self.ads[0]["Type"]]]["SitelinkSetId"] = None
            if self.writes:
                self.reads_after_write += 1
                if self.reads_after_write == 2:
                    if self.fail_final_read:
                        raise TransportFailure("Не удалось перечитать привязки")
                    if self.corrupt_final_read:
                        self.ads[0][BODIES[self.ads[0]["Type"]]]["AdExtensions"].append(callout(301))
        records = {"ads": self.ads, "sitelinks": self.sitelinks, "adextensions": self.callouts}[service]
        selected = params.get("SelectionCriteria", {}).get("Ids")
        if selected is not None:
            records = [one for one in records if one["Id"] in selected]
        if service == "ads":
            result = []
            for record in records:
                name = BODIES[record["Type"]]
                value = {field: record[field] for field in params["FieldNames"] if field in record}
                if name + "FieldNames" in params:
                    value[name] = {field: record[name][field] for field in params[name + "FieldNames"]
                                   if field in record[name]}
                result.append(value)
            records = result
            if self.reads_after_write == 2:
                if self.malformed_final_read == "missing":
                    records[0].pop("Id")
                elif self.malformed_final_read == "unhashable":
                    records[0]["Id"] = []
                elif self.malformed_final_read == "duplicate":
                    records.append(copy.deepcopy(records[0]))
        return copy.deepcopy(records)

    def batch(self, service, method, params, *, items, results_key, id_field,
              account, use_operator_units):
        self.writes.append((service, method, copy.deepcopy(params), account))
        use_operator_units()
        entries = []
        for index, item in enumerate(items):
            record = next(one for one in self.ads if one["Id"] == item["Id"])
            name = BODIES[record["Type"]]
            for field, value in item[name].items():
                if field == "CalloutSetting":
                    record[name]["AdExtensions"] = ([callout(one["AdExtensionId"])
                                                     for one in value["AdExtensions"]] if value else None)
                elif field in ("Titles", "Texts"):
                    singular = "Title" if field == "Titles" else "Text"
                    record[name][field] = [{singular: text} for text in value]
                else:
                    record[name][field] = copy.deepcopy(value)
            entries.append(BatchEntry(index, identifier=item["Id"], id_field=id_field))
        return BatchResult(entries, Response(request_id="offline-test"), results_key)


class ExtensionBindingTests(unittest.TestCase):
    def setUp(self):
        context = ExitStack()
        self.addCleanup(context.close)
        root = Path(context.enter_context(tempfile.TemporaryDirectory()))
        self.client = FakeClient()
        context.enter_context(patch.object(extensions.Client, "from_env", return_value=self.client))
        context.enter_context(patch.object(extensions.Accounts, "load", return_value=FakeAccounts()))
        context.enter_context(patch.object(cache, "CACHE_DIR", root / "cache"))
        context.enter_context(patch.object(writer, "AuditLog", partial(writer.AuditLog, root=root / "journal")))
        context.enter_context(patch.object(writer.Writer, "_current_limits", lambda _: writer.Limits.load()))

    def command(self, *arguments):
        output = io.StringIO()
        with redirect_stdout(output), redirect_stderr(io.StringIO()):
            code = extensions.run(extensions.build_parser().parse_args(
                ["--json", "bind", "--ad", str(AD), *arguments]))
        return code, json.loads(output.getvalue())

    def reset_ad(self, kind):
        self.client.ads = [ad(kind)]
        self.client.writes.clear()
        self.client.ads_reads = self.client.reads_after_write = 0

    def test_product_replacement_sends_only_extensions_and_preserves_content(self):
        for kind in PRODUCT_TYPES:
            with self.subTest(kind=kind):
                self.reset_ad(kind)
                before = copy.deepcopy(self.client.ads[0][BODIES[kind]])
                code, report = self.command("--sitelink-set", "202", "--callout-id", "302", "--apply")
                self.assertEqual(code, 0, report)
                self.assertEqual(self.client.writes, [("ads", "update", {"Ads": [{"Id": AD,
                    BODIES[kind]: {"SitelinkSetId": 202, "CalloutSetting": {
                        "AdExtensions": [{"AdExtensionId": 302, "Operation": "SET"}]}}}]}, ACCOUNT)])
                self.assertEqual(self.client.ads[0][BODIES[kind]],
                                 {**before, "SitelinkSetId": 202, "AdExtensions": [callout(302)]})
                self.assertEqual(report["written"], [str(AD)])
                self.assertEqual(report["verified_ads"][0]["SitelinkSetId"], 202)
                self.assertEqual(report["verified_ads"][0]["AdExtensions"], [callout(302)])
                self.assertEqual(report["verified_ads"][0]["State"], "OFF")

    def test_assignment_without_existing_extensions_does_not_require_href(self):
        for kind in PRODUCT_TYPES:
            with self.subTest(kind=kind):
                self.reset_ad(kind)
                self.client.ads[0][BODIES[kind]].update(SitelinkSetId=None, AdExtensions=None)
                code, report = self.command("--sitelink-set", "202", "--callout-id", "302", "--apply")
                self.assertEqual(code, 0, report)
                self.assertEqual(report["binding_plan"][0]["before"], {"sitelinks": None, "callouts": []})

    def test_product_clear_uses_null_for_both_extensions(self):
        for kind in PRODUCT_TYPES:
            with self.subTest(kind=kind):
                self.reset_ad(kind)
                code, report = self.command("--clear-sitelinks", "--clear-callouts", "--apply")
                self.assertEqual(code, 0, report)
                self.assertEqual(self.client.writes[0][2], {"Ads": [{"Id": AD, BODIES[kind]: {
                    "SitelinkSetId": None, "CalloutSetting": None}}]})
                self.assertIsNone(report["verified_ads"][0]["AdExtensions"])

    def test_changing_one_extension_preserves_the_other(self):
        for flags, field in ((("--sitelink-set", "202"), "SitelinkSetId"),
                             (("--callout-id", "302"), "CalloutSetting")):
            with self.subTest(field=field):
                self.reset_ad("SHOPPING_AD")
                code, report = self.command(*flags, "--apply")
                self.assertEqual(code, 0, report)
                self.assertEqual(set(self.client.writes[0][2]["Ads"][0]["ShoppingAd"]), {field})
                body = self.client.ads[0]["ShoppingAd"]
                self.assertEqual(body["AdExtensions"] if field == "SitelinkSetId" else body["SitelinkSetId"],
                                 [callout(301)] if field == "SitelinkSetId" else 201)

    def test_dry_run_shows_contents_before_and_after_without_writing(self):
        code, report = self.command("--sitelink-set", "202", "--callout-id", "302")
        self.assertEqual(code, 0, report)
        self.assertFalse(report["applied"])
        self.assertEqual(self.client.writes, [])
        plan = report["binding_plan"][0]
        self.assertEqual(plan["before"]["sitelinks"]["Sitelinks"][0]["Title"], "Прежний раздел")
        self.assertEqual(plan["planned"]["sitelinks"]["Sitelinks"][0]["Href"], "https://example.test/202")
        self.assertEqual(plan["before"]["callouts"][0]["Callout"]["CalloutText"], "Прежнее уточнение")
        self.assertEqual(plan["planned"]["callouts"][0]["Callout"]["CalloutText"], "Новое уточнение")

    def test_same_bindings_do_not_write(self):
        code, report = self.command("--sitelink-set", "201", "--callout-id", "301", "--apply")
        self.assertEqual(code, 0, report)
        self.assertFalse(report["applied"])
        self.assertEqual(self.client.writes, [])

    def test_bindings_changed_after_plan_stop_the_write(self):
        self.client.change_before_write = True
        code, report = self.command("--sitelink-set", "202", "--apply")
        self.assertEqual(code, 1, report)
        self.assertEqual(self.client.writes, [])
        self.assertIn("изменилось после чтения", " ".join(report["problems"]))

    def test_failed_final_read_keeps_accepted_but_does_not_claim_verified(self):
        self.client.fail_final_read = True
        code, report = self.command("--sitelink-set", "202", "--apply")
        self.assertEqual(code, 1, report)
        self.assertFalse(report["ok"])
        self.assertEqual(report["accepted"], [str(AD)])
        self.assertEqual(report["written"], [])
        self.assertEqual(report["verified_ads"], [])
        self.assertTrue(report["unknown"])

    def test_unexpected_callout_after_write_is_not_success(self):
        self.client.corrupt_final_read = True
        code, report = self.command("--callout-id", "302", "--apply")
        self.assertEqual(code, 1, report)
        self.assertEqual(report["written"], [])
        self.assertEqual(report["verified_ads"], [])
        self.assertTrue(report["unknown"])

    def test_malformed_final_read_is_unknown_without_losing_accepted_id(self):
        for malformed in ("missing", "unhashable", "duplicate"):
            with self.subTest(malformed=malformed):
                self.reset_ad("SHOPPING_AD")
                self.client.malformed_final_read = malformed
                code, report = self.command("--sitelink-set", "202", "--apply")
                self.assertEqual(code, 1, report)
                self.assertEqual(report["accepted"], [str(AD)])
                self.assertEqual(report["written"], [])
                self.assertEqual(report["verified_ads"], [])
                self.assertTrue(report["unknown"])

    def test_text_and_responsive_keep_all_text_variants(self):
        for kind in ("TEXT_AD", "RESPONSIVE_AD"):
            with self.subTest(kind=kind):
                self.reset_ad(kind)
                before = copy.deepcopy(self.client.ads[0][BODIES[kind]])
                code, report = self.command("--sitelink-set", "202", "--apply")
                self.assertEqual(code, 0, report)
                self.assertEqual(self.client.ads[0][BODIES[kind]], {**before, "SitelinkSetId": 202})
                sent = self.client.writes[0][2]["Ads"][0][BODIES[kind]]
                self.assertEqual(set(sent), {"SitelinkSetId", "Titles", "Texts"}
                                 if kind == "RESPONSIVE_AD" else {"SitelinkSetId"})
                if kind == "RESPONSIVE_AD":
                    self.assertEqual(sent["Titles"], [one["Title"] for one in before["Titles"]])
                    self.assertEqual(sent["Texts"], [one["Text"] for one in before["Texts"]])


if __name__ == "__main__":
    unittest.main()
