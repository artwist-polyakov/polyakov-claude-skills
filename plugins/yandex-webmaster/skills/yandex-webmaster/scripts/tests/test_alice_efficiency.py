#!/usr/bin/env python3
"""Проверки кеша и отчётов Алисы без аккаунта и сетевых запросов."""

import copy
import importlib.util
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from contextlib import redirect_stderr, redirect_stdout
from types import SimpleNamespace
from unittest import mock


SCRIPTS_DIR = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "alice_efficiency", SCRIPTS_DIR / "alice_efficiency.py"
)
alice = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(alice)

HOST_ID = "https:example.test:443"
SAMPLE = {
    "alertType": "NONE",
    "sov": [
        {"dateFrom": "2026-01-01", "dateTo": "2026-01-07", "sharePercent": 0.1},
        {"dateFrom": "2026-01-08", "dateTo": "2026-01-14", "sharePercent": 0.3},
    ],
    "queries": {
        "GENERAL": [{"url": "https://competitor.test/"}],
        "EXAMPLES": {
            "hasOwnExamples": [{
                "query": "купить металл",
                "urls": [{
                    "host": "example.test", "url": "https://example.test/",
                    "title": "Металл",
                }],
            }],
            "noOwnExamples": [{
                "query": "доставка металла",
                "urls": [{
                    "host": "competitor.test", "url": "https://competitor.test/",
                    "title": "Доставка",
                }],
            }],
        },
    },
}


def html_for(data, authenticated=True):
    init = {"userIsAuth": authenticated, "alice": data}
    return "<script>window._initData = " + json.dumps(init, ensure_ascii=False) + ";</script>"


class AliceTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.args = SimpleNamespace(
            host_id=HOST_ID, cache_dir=str(self.root / "cache"),
            session_id="test-session", no_cache=False,
        )
        self.cache = Path(alice.cache_path(self.args.cache_dir, HOST_ID))
        self.raw = self.cache.with_name("raw.html")
        fetch_patch = mock.patch.object(alice, "fetch_html", return_value=html_for(SAMPLE))
        self.fetch = fetch_patch.start()
        self.addCleanup(fetch_patch.stop)

    def write_cache(self, data=SAMPLE, age=0):
        self.cache.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        timestamp = time.time() - age
        os.utime(self.cache, (timestamp, timestamp))

    def run_cli(self, action, no_cache=False):
        argv = [
            "alice_efficiency.py", "--host-id", HOST_ID,
            "--cache-dir", self.args.cache_dir, "--session-id", "test-session",
        ]
        if no_cache:
            argv.append("--no-cache")
        argv.append(action)
        output = io.StringIO()
        with mock.patch.object(sys, "argv", argv), redirect_stdout(output):
            alice.main()
        return output.getvalue()

    def assert_refresh_fails(self):
        output = io.StringIO()
        error = io.StringIO()
        with redirect_stdout(output), redirect_stderr(error):
            with self.assertRaises(SystemExit) as raised:
                alice.load_or_fetch(self.args, force=True)
        self.assertNotEqual(raised.exception.code, 0)
        self.assertTrue(error.getvalue().strip())
        self.assertEqual(output.getvalue(), "")

    def test_cache_path_remains_compatible(self):
        self.assertEqual(
            self.cache,
            self.root / "cache" / "host_https_example.test_443" / "alice" / "init.json",
        )

    def test_fresh_cache_needs_no_fetch_and_does_not_change_html(self):
        self.write_cache(age=23 * 60 * 60)
        self.args.session_id = ""
        self.raw.write_text("previous HTML", encoding="utf-8")
        before = (self.raw.read_bytes(), self.raw.stat().st_mtime_ns)

        self.assertEqual(alice.load_or_fetch(self.args), SAMPLE)

        self.fetch.assert_not_called()
        self.assertEqual((self.raw.read_bytes(), self.raw.stat().st_mtime_ns), before)

    def test_missing_cache_is_fetched_and_reused(self):
        self.assertEqual(alice.load_or_fetch(self.args), SAMPLE)
        self.assertEqual(json.loads(self.cache.read_text(encoding="utf-8")), SAMPLE)
        self.assertEqual(alice.load_or_fetch(self.args), SAMPLE)
        self.fetch.assert_called_once()

    def test_cache_older_than_24_hours_is_refreshed(self):
        old = dict(SAMPLE, alertType="OLD")
        self.write_cache(old, age=25 * 60 * 60)

        self.assertEqual(alice.load_or_fetch(self.args), SAMPLE)

        self.fetch.assert_called_once()
        self.assertEqual(json.loads(self.cache.read_text(encoding="utf-8")), SAMPLE)

    def test_invalid_json_cache_is_refreshed(self):
        for content in ("", "{broken json", "null", "[]", '{"queries":[]}'):
            with self.subTest(content=content):
                self.fetch.reset_mock()
                self.cache.write_text(content, encoding="utf-8")

                self.assertEqual(alice.load_or_fetch(self.args), SAMPLE)

                self.fetch.assert_called_once()
                self.assertEqual(json.loads(self.cache.read_text(encoding="utf-8")), SAMPLE)

    def test_no_cache_and_fetch_action_refresh_fresh_cache(self):
        for action, no_cache in (("summary", True), ("fetch", False)):
            with self.subTest(action=action):
                self.fetch.reset_mock()
                self.write_cache(dict(SAMPLE, alertType="OLD"))

                output = self.run_cli(action, no_cache=no_cache)

                self.fetch.assert_called_once()
                self.assertNotIn("OLD", output)
                self.assertEqual(json.loads(self.cache.read_text(encoding="utf-8")), SAMPLE)

    def test_failed_fetch_keeps_previous_cache(self):
        for forced in (False, True):
            with self.subTest(forced=forced):
                self.write_cache(age=25 * 60 * 60 if not forced else 0)
                previous = self.cache.read_bytes()
                self.args.no_cache = forced

                def failed_fetch(*args, **kwargs):
                    self.assertEqual(self.cache.read_bytes(), previous)
                    alice.fail("temporary network failure")

                self.fetch.side_effect = failed_fetch
                with redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as raised:
                    alice.load_or_fetch(self.args)

                self.assertNotEqual(raised.exception.code, 0)
                self.assertEqual(self.cache.read_bytes(), previous)

    def test_bad_html_or_expired_session_cannot_replace_cache(self):
        self.write_cache()
        previous = self.cache.read_bytes()
        invalid_pages = (
            "<html>changed page</html>",
            '<script>window._initData = {"userIsAuth":true, broken};</script>',
            html_for(SAMPLE, authenticated=False),
            '<script>window._initData = {"userIsAuth":true};</script>',
        )
        for page in invalid_pages:
            with self.subTest(page=page):
                self.fetch.return_value = page
                self.assert_refresh_fails()
                self.assertEqual(self.cache.read_bytes(), previous)

    def test_wrong_alice_structure_cannot_replace_cache(self):
        self.write_cache()
        previous = self.cache.read_bytes()
        invalid_data = (
            None, [], "text", {},
            {"sov": {}}, {"sov": [1]}, {"queries": []},
            {"queries": {"GENERAL": {}}},
            {"queries": {"GENERAL": ["text"]}},
            {"queries": {"EXAMPLES": []}},
            {"queries": {"EXAMPLES": {"hasOwnExamples": {}}}},
            {"queries": {"EXAMPLES": {"noOwnExamples": [1]}}},
            {"queries": {"EXAMPLES": {"hasOwnExamples": [{"urls": {}}]}}},
            {"queries": {"EXAMPLES": {"hasOwnExamples": [{"urls": [1]}]}}},
        )
        for data in invalid_data:
            with self.subTest(data=data):
                self.fetch.return_value = html_for(data)
                self.assert_refresh_fails()
                self.assertEqual(self.cache.read_bytes(), previous)

    def test_failed_first_fetch_does_not_create_json(self):
        self.fetch.return_value = "<html>no init data</html>"

        self.assert_refresh_fails()

        self.assertFalse(self.cache.exists())

    def test_failed_file_replacement_keeps_cache_and_removes_temporary_file(self):
        self.write_cache(dict(SAMPLE, alertType="OLD"))
        previous = self.cache.read_bytes()
        with mock.patch.object(os, "replace", side_effect=OSError("disk full")):
            with redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as raised:
                self.run_cli("fetch")
        self.assertNotEqual(raised.exception.code, 0)
        self.assertEqual(self.cache.read_bytes(), previous)
        self.assertEqual(list(self.cache.parent.iterdir()), [self.cache])

    def test_json_extraction_handles_nested_and_escaped_values(self):
        init = {
            "userIsAuth": True,
            "alice": SAMPLE,
            "extra": {"nested": [{"text": 'Кавычки " и скобки }{, путь \\ и \\\\"'}]},
        }
        page = (
            "<html><script>window._initData =\n"
            + json.dumps(init, ensure_ascii=False)
            + '; window.after = {"unrelated": true};</script></html>'
        )

        self.assertEqual(alice.extract_init_data(page), init)

    def test_init_data_must_be_an_assigned_json_object(self):
        pages = (
            "<html>no assignment</html>",
            '<script>window._initData; window.other = {"userIsAuth":true};</script>',
            '<script>window._initData = [{"userIsAuth":true}];</script>',
            '<script>window._initData = null;</script>',
            '<script>window._initData = "text";</script>',
            '<script>window._initData = {"broken":;</script>',
        )
        for page in pages:
            with self.subTest(page=page), redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit) as raised:
                    alice.extract_init_data(page)
                self.assertNotEqual(raised.exception.code, 0)

    def test_all_actions_keep_short_output_and_complete_tsv(self):
        data = copy.deepcopy(SAMPLE)
        data["queries"]["GENERAL"] *= 40
        data["queries"]["EXAMPLES"]["hasOwnExamples"] *= 40
        data["queries"]["EXAMPLES"]["noOwnExamples"] *= 40
        self.fetch.return_value = html_for(data)
        self.write_cache(data)
        reports = {
            "sov": ("sov.tsv", 3, "date_from\tdate_to\tshare\tshare_pct"),
            "competitors": ("competitors.tsv", 41, "rank\turl"),
            "with-site": ("with_site.tsv", 41, "query\trank\thost\turl\ttitle"),
            "without-site": ("without_site.tsv", 41, "query\trank\thost\turl\ttitle"),
        }
        for action in ("summary", "sov", "competitors", "with-site", "without-site", "fetch"):
            with self.subTest(action=action):
                self.fetch.reset_mock()
                output = self.run_cli(action)
                self.assertLessEqual(len(output.splitlines()), 30)
                self.assertTrue(output.strip())
                if action == "fetch":
                    self.fetch.assert_called_once()
                else:
                    self.fetch.assert_not_called()
                if action in reports:
                    filename, count, header = reports[action]
                    lines = self.cache.with_name(filename).read_text(encoding="utf-8").splitlines()
                    self.assertEqual(len(lines), count)
                    self.assertEqual(lines[0], header)
        sov = self.cache.with_name("sov.tsv").read_text(encoding="utf-8")
        self.assertIn("2026-01-01\t2026-01-07\t0.1000\t10.00%", sov)
        self.assertIn("40\thttps://competitor.test/", self.cache.with_name("competitors.tsv").read_text())

    def test_tsv_keeps_query_rank_and_sanitizes_cell_separators(self):
        data = copy.deepcopy(SAMPLE)
        item = data["queries"]["EXAMPLES"]["hasOwnExamples"][0]
        item["query"] = "купить\tметалл\nс доставкой"
        item["urls"][0]["title"] = "Первая\r\nстрока"
        item["urls"].append({"host": "second.test", "url": "https://second.test/", "title": None})
        self.write_cache(data)

        output = self.run_cli("with-site")
        self.assertIn("купить металл с доставкой", output)
        self.assertNotIn("Первая\r\nстрока", output)

        lines = self.cache.with_name("with_site.tsv").read_text(encoding="utf-8").splitlines()
        self.assertEqual(len(lines), 3)
        self.assertEqual(lines[1].split("\t"), [
            "купить металл с доставкой", "1", "example.test", "https://example.test/", "Первая  строка",
        ])
        self.assertEqual(lines[2].split("\t"), [
            "купить металл с доставкой", "2", "second.test", "https://second.test/", "",
        ])

    def test_optional_empty_collections_still_produce_reports(self):
        for data in (
            {"alertType": "NO_DATA"},
            {"alertType": "NO_DATA", "sov": None, "queries": None},
            {"sov": [], "queries": {"GENERAL": None, "EXAMPLES": None}},
        ):
            with self.subTest(data=data):
                self.write_cache(data)
                self.fetch.return_value = html_for(data)
                for action in ("summary", "sov", "competitors", "with-site", "without-site", "fetch"):
                    self.assertLessEqual(len(self.run_cli(action).splitlines()), 30)
        self.assertEqual(self.fetch.call_count, 3)

    def test_shell_no_cache_preserves_json_when_python_fails(self):
        scripts = self.root / "scripts"
        scripts.mkdir()
        for name in ("alice.sh", "common.sh"):
            shutil.copyfile(SCRIPTS_DIR / name, scripts / name)
        executable_dir = self.root / "bin"
        executable_dir.mkdir()
        python_stub = executable_dir / "python3"
        python_stub.write_text('#!/bin/sh\nprintf "%s\\n" "$@"\nexit 7\n', encoding="utf-8")
        python_stub.chmod(0o755)
        self.write_cache()
        previous = self.cache.read_bytes()
        environment = {
            "PATH": str(executable_dir) + os.pathsep + os.environ.get("PATH", os.defpath),
            "YANDEX_WEBMASTER_TOKEN": "test-token", "SESSION_ID": "test-session",
        }

        result = subprocess.run(
            ["sh", str(scripts / "alice.sh"), "--host-id", HOST_ID, "--no-cache"],
            env=environment, capture_output=True, text=True, timeout=10,
        )

        self.assertEqual(result.returncode, 7, result.stderr)
        self.assertEqual(self.cache.read_bytes(), previous)
        self.assertIn("--no-cache", result.stdout.splitlines())
        self.assertIn("summary", result.stdout.splitlines())


if __name__ == "__main__":
    unittest.main()
