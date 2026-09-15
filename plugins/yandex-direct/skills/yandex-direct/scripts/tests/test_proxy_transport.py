#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""Транспорт прокси без сети: python3 scripts/tests/test_proxy_transport.py."""

import http.client
import io
import json
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
import sys
import unittest
from unittest.mock import Mock, patch
import urllib.error
import urllib.request


SCRIPTS = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(SCRIPTS / "lib"), str(SCRIPTS)]

from config import DirectFailure, resolve_settings
from direct import Client, NoRedirect, Retries, Transport
from errors import ApiFailure, TransportFailure, hint
import whoami


BASE_URL = "https://proxy.example.test/direct/api"
V4_BASE_URL = "https://legacy.example.test/direct"
TOKEN = "proxy-test-key-never-publish"
EXTRA_VALUE = "proxy-test-owner-header"
ACCOUNT = "advertiser-test"
SUCCESS = {"result": {"Campaigns": [{"Id": 123}]}}
DETAIL = "Выберите нужный логин:\n" + "Кабинет доступен после подключения. " * 40 + "\nКонец инструкции."
TSV = "Отчёт\nCampaignId\tClicks\n123\t7\nTotal rows: 1\n"


def settings(**values):
    environ = {
        "YANDEX_DIRECT_TOKEN": TOKEN,
        "YANDEX_DIRECT_ACCOUNT": ACCOUNT,
        "YANDEX_DIRECT_API_BASE_URL": BASE_URL,
        "YANDEX_DIRECT_API_BASE_URL_V4": V4_BASE_URL,
        "YANDEX_DIRECT_API_EXTRA_HEADERS": f"X-Proxy-Owner: {EXTRA_VALUE}",
    }
    environ.update(values)
    return resolve_settings(from_file={}, environ=environ)


def envelope(code=409, detail=DETAIL):
    return {"error": {"error_code": code, "error_string": "Выбор подключения",
                      "error_detail": detail, "request_id": "proxy-request-42"}}


def wire_response(status=200, payload=SUCCESS, headers=None):
    """Ответ на границе urllib: JSON, обычный текст или HTTPError."""
    if isinstance(payload, bytes):
        raw = payload
    elif isinstance(payload, str):
        raw = payload.encode("utf-8")
    else:
        raw = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    received = {"Content-Type": "application/json", **(headers or {})}
    if status >= 300:
        return urllib.error.HTTPError(BASE_URL, status, "test", received, io.BytesIO(raw))
    response = io.BytesIO(raw)
    response.status = status
    response.headers = received
    return response


class BrokenBody(io.BytesIO):
    def read(self, *args):
        raise http.client.IncompleteRead(b"partial", 100)


class ProxyTransportTests(unittest.TestCase):
    def client(self, *responses, configuration=None):
        opener = Mock()
        opener.open.side_effect = responses
        sleep = Mock()
        client = Client(
            configuration or settings(), Transport(opener=opener),
            retries=Retries(attempts=3, sleep=sleep, jitter=lambda: 0), warn=Mock(),
        )
        return client, opener, sleep

    def test_401_without_envelope_rejects_key_without_retry(self):
        for payload in ("<html>Unauthorized</html>", "", {"message": "Unauthorized"}, None):
            with self.subTest(payload=payload):
                client, opener, sleep = self.client(wire_response(401, payload))
                with self.assertRaises(DirectFailure) as caught:
                    client.call("Campaigns", "get", retry=True)
                self.assertIn("Прокси proxy.example.test отверг ключ из YANDEX_DIRECT_TOKEN", str(caught.exception))
                self.assertIn("config/.env", str(caught.exception))
                self.assertFalse(caught.exception.retryable)
                self.assertEqual(opener.open.call_count, 1)
                sleep.assert_not_called()

    def test_401_with_envelope_keeps_proxy_instruction(self):
        client, opener, _ = self.client(wire_response(401, envelope(53)))
        with self.assertRaises(ApiFailure) as caught:
            client.call("Campaigns", "get", retry=False)
        self.assertIn(DETAIL, str(caught.exception))
        self.assertIn("Ответ прокси proxy.example.test", str(caught.exception))
        self.assertNotIn(hint(53, "v5"), str(caught.exception))
        self.assertEqual(opener.open.call_count, 1)

    def test_404_names_address_or_v4_and_preserves_envelope(self):
        for version in ("v501", "v4", "live/v4"):
            for payload in ("Not found", envelope(404)):
                with self.subTest(version=version, envelope=isinstance(payload, dict)):
                    client, opener, sleep = self.client(wire_response(404, payload))
                    with self.assertRaises(DirectFailure) as caught:
                        if version == "v501":
                            client.call("Campaigns", "get", retry=True)
                        else:
                            client.call_v4("GetRetargetingGoals", version=version, retry=True)
                    message = str(caught.exception)
                    if version == "v501":
                        self.assertIn("proxy.example.test", message)
                        self.assertIn("не поддерживает этот адрес", message)
                    else:
                        self.assertIn("legacy.example.test", message)
                        self.assertIn("не поддерживает четвёртую версию API", message)
                    if isinstance(payload, dict):
                        self.assertIn(DETAIL, message)
                    self.assertFalse(caught.exception.retryable)
                    self.assertEqual(opener.open.call_count, 1)
                    sleep.assert_not_called()

    def test_4xx_envelope_preserves_instruction_without_retry(self):
        for status in (400, 401, 403, 409, 422):
            for code in (409, 53, 1000):
                for version in ("v501", "v4", "live/v4"):
                    with self.subTest(status=status, code=code, version=version):
                        client, opener, sleep = self.client(wire_response(status, envelope(code)))
                        with self.assertRaises(ApiFailure) as caught:
                            if version == "v501":
                                client.call("Campaigns", "get", retry=True)
                            else:
                                client.call_v4("GetRetargetingGoals", version=version, retry=True)
                        message = str(caught.exception)
                        host = "proxy.example.test" if version == "v501" else "legacy.example.test"
                        self.assertIn(f"Ответ прокси {host}", message)
                        self.assertIn("Выбор подключения\n" + DETAIL, message)
                        self.assertIn("proxy-request-42", message)
                        self.assertNotIn(hint(53, "v5"), message)
                        self.assertFalse(caught.exception.retryable)
                        self.assertEqual(opener.open.call_count, 1)
                        sleep.assert_not_called()

    def test_read_retries_503_envelope_even_with_yandex_auth_code(self):
        for version in ("v501", "v4", "live/v4"):
            with self.subTest(version=version):
                success = SUCCESS if version == "v501" else {"data": [123]}
                client, opener, sleep = self.client(
                    wire_response(503, envelope(53)), wire_response(payload=success),
                )
                if version == "v501":
                    response = client.call("Campaigns", "get", {"FieldNames": ["Id"]})
                    expected_url = BASE_URL + "/json/v501/campaigns/"
                else:
                    response = client.call_v4("GetRetargetingGoals", version=version)
                    expected_url = V4_BASE_URL + f"/{version}/json/"
                self.assertEqual(response.result, success.get("result", success.get("data")))
                self.assertEqual(response.attempts, 2)
                self.assertEqual(opener.open.call_count, 2)
                sleep.assert_called_once_with(1.0)
                first, second = (item.args[0] for item in opener.open.call_args_list)
                self.assertEqual(first.full_url, expected_url)
                self.assertEqual(first.data, second.data)
                self.assertEqual(first.header_items(), second.header_items())

    def test_forwarded_200_error_keeps_direct_retry_rules(self):
        cases = (
            ("v501", 53, False), ("v501", 54, False), ("v501", 152, False),
            ("v501", 8000, False), ("v501", 1000, True),
            ("v4", 53, False), ("live/v4", 71, False),
        )
        direct_settings = resolve_settings(from_file={}, environ={
            "YANDEX_DIRECT_TOKEN": TOKEN, "YANDEX_DIRECT_ACCOUNT": ACCOUNT,
        })
        for configuration in (settings(), direct_settings):
            for version, code, retryable in cases:
                with self.subTest(proxy=configuration.is_proxy, version=version, code=code):
                    if version == "v501":
                        payload = envelope(code, "Ответ Яндекса")
                        success = SUCCESS
                    else:
                        payload = {"error_code": code, "error_str": "Ответ Яндекса"}
                        success = {"data": [123]}
                    client, opener, sleep = self.client(
                        wire_response(200, payload), wire_response(payload=success),
                        configuration=configuration,
                    )
                    if version == "v501":
                        call = lambda: client.call("Campaigns", "get")
                    else:
                        call = lambda: client.call_v4("GetRetargetingGoals", version=version)
                    if retryable:
                        self.assertTrue(call().ok)
                        self.assertEqual(opener.open.call_count, 2)
                        sleep.assert_called_once_with(1.0)
                    else:
                        with self.assertRaises(ApiFailure) as caught:
                            call()
                        self.assertEqual(caught.exception.code, code)
                        self.assertFalse(caught.exception.retryable)
                        self.assertEqual(opener.open.call_count, 1)
                        sleep.assert_not_called()

    def test_write_never_retries_proxy_envelope_even_when_requested(self):
        client, opener, sleep = self.client(wire_response(409, envelope(53)))
        with self.assertRaises(ApiFailure) as caught:
            client.call("Campaigns", "add", {"Campaigns": []}, retry=True)
        self.assertFalse(caught.exception.retryable)
        self.assertEqual(opener.open.call_count, 1)
        sleep.assert_not_called()

    def test_429_honors_retry_in_for_reads_only(self):
        for payload in ("Too many requests", envelope(429)):
            for method in ("get", "add"):
                with self.subTest(envelope=isinstance(payload, dict), method=method):
                    client, opener, sleep = self.client(
                        wire_response(429, payload, {"retryIn": "7"}), wire_response(),
                    )
                    if method == "get":
                        self.assertTrue(client.call("Campaigns", method).ok)
                        self.assertEqual(opener.open.call_count, 2)
                        sleep.assert_called_once_with(7.0)
                    else:
                        with self.assertRaises(DirectFailure) as caught:
                            client.call("Campaigns", method, retry=True)
                        self.assertFalse(caught.exception.retryable)
                        self.assertEqual(opener.open.call_count, 1)
                        sleep.assert_not_called()

    def test_server_errors_and_disconnects_retry_only_reads(self):
        failures = (
            ("500", lambda: wire_response(500, "Server error")),
            ("502", lambda: wire_response(502, envelope(502))),
            ("503", lambda: wire_response(503, "Unavailable")),
            ("connection", lambda: urllib.error.URLError("Connection reset")),
            ("timeout", lambda: TimeoutError()),
            ("body", lambda: http.client.IncompleteRead(b"partial", 100)),
        )
        for name, failure in failures:
            for method in ("get", "add"):
                with self.subTest(failure=name, method=method):
                    client, opener, sleep = self.client(failure(), wire_response())
                    if method == "get":
                        self.assertTrue(client.call("Campaigns", method).ok)
                        self.assertEqual(opener.open.call_count, 2)
                        sleep.assert_called_once_with(1.0)
                    else:
                        with self.assertRaises(DirectFailure) as caught:
                            client.call("Campaigns", method, retry=True)
                        self.assertFalse(caught.exception.retryable)
                        self.assertEqual(opener.open.call_count, 1)
                        sleep.assert_not_called()

    def test_broken_response_body_keeps_units_before_retry(self):
        broken = BrokenBody()
        broken.status = 200
        broken.headers = {"Units": "2/998/1000", "Units-Used-Login": ACCOUNT}
        client, opener, _ = self.client(
            broken, wire_response(headers={"Units": "3/995/1000", "Units-Used-Login": ACCOUNT}),
        )
        self.assertTrue(client.call("Campaigns", "get").ok)
        self.assertEqual(opener.open.call_count, 2)
        self.assertEqual(client.units.spent, 5)
        self.assertEqual(client.units.wallets[ACCOUNT]["left"], 995)

    def test_reports_queue_preserves_request_retry_in_and_units(self):
        client, opener, sleep = self.client(
            wire_response(201, "", {"retryIn": "3", "RequestId": "queued", "Units": "1/999/1000", "Units-Used-Login": ACCOUNT}),
            wire_response(202, "", {"retryIn": "4", "RequestId": "pending", "Units": "2/997/1000", "Units-Used-Login": ACCOUNT}),
            wire_response(200, TSV, {"Content-Type": "text/tab-separated-values", "RequestId": "ready", "Units": "3/994/1000", "Units-Used-Login": ACCOUNT}),
        )
        params = {"ReportName": "proxy-test", "Format": "TSV"}
        answers = [client.report_once(
            params, account=ACCOUNT, report_type="CAMPAIGN_PERFORMANCE_REPORT",
            processing_mode="offline", use_operator_units=False,
        ) for _ in range(3)]
        self.assertEqual([answer.status for answer in answers], [201, 202, 200])
        self.assertEqual([answer.ready for answer in answers], [False, False, True])
        self.assertEqual([answer.retry_in for answer in answers], ["3", "4", ""])
        self.assertEqual([answer.request_id for answer in answers], ["queued", "pending", "ready"])
        self.assertEqual([answer.text for answer in answers], ["", "", TSV])
        sleep.assert_not_called()
        self.assertEqual(opener.open.call_count, 3)
        for invocation in opener.open.call_args_list:
            request = invocation.args[0]
            self.assertEqual(request.full_url, BASE_URL + "/json/v501/reports")
            self.assertEqual(json.loads(request.data), {"params": params})
            self.assertEqual(request.get_header("Authorization"), "Bearer " + TOKEN)
            self.assertEqual(request.get_header("X-proxy-owner"), EXTRA_VALUE)
            self.assertEqual(request.get_header("Processingmode"), "offline")
        self.assertEqual(client.units.spent, 6)
        self.assertEqual(client.units.wallets[ACCOUNT], {
            "login": ACCOUNT, "spent": 6, "left": 994, "limit": 1000,
        })

    def test_redirect_is_never_forwarded_or_retried(self):
        target = "https://redirect.example.test/collect"
        headers = {"Location": target, "Units": "2/998/1000", "Units-Used-Login": ACCOUNT}
        client, opener, sleep = self.client(wire_response(301, "", headers))
        with self.assertRaises(TransportFailure) as caught:
            client.call("Campaigns", "get", retry=True)
        self.assertEqual(opener.open.call_count, 1)
        self.assertEqual(opener.open.call_args.args[0].full_url, BASE_URL + "/json/v501/campaigns/")
        self.assertIn("базовый адрес API и хвостовой слэш", str(caught.exception))
        self.assertFalse(caught.exception.retryable)
        self.assertEqual(client.units.spent, 2)
        sleep.assert_not_called()
        request = urllib.request.Request(BASE_URL, headers={"Authorization": "Bearer " + TOKEN})
        self.assertIsNone(NoRedirect().redirect_request(request, None, 301, "", headers, target))
        self.assertTrue(any(isinstance(handler, NoRedirect) for handler in Transport().opener.handlers))


class WhoamiProxyTests(unittest.TestCase):
    def run_main(self, *responses, configuration=None):
        opener = Mock()
        opener.open.side_effect = responses
        stdout, stderr = io.StringIO(), io.StringIO()
        with (
            patch.object(whoami, "preload_secrets"),
            patch.object(whoami, "settings_from_env", return_value=configuration or settings()),
            patch.object(whoami, "OPENER", opener),
            redirect_stdout(stdout), redirect_stderr(stderr),
        ):
            status = whoami.main(["--json"])
        return status, stdout.getvalue(), stderr.getvalue(), opener

    def test_direct_call_keeps_existing_url_headers_and_body_bytes(self):
        configuration = resolve_settings(from_file={}, environ={
            "YANDEX_DIRECT_TOKEN": TOKEN,
            "YANDEX_DIRECT_ACCOUNT": ACCOUNT,
            "YANDEX_DIRECT_USE_OPERATOR_UNITS": "always",
        })
        opener = Mock()
        opener.open.return_value = wire_response()
        with patch.object(whoami, "OPENER", opener):
            whoami.call(configuration, "clients", {"FieldNames": ["Login"]}, client_login=ACCOUNT)
        request = opener.open.call_args.args[0]
        self.assertEqual(request.full_url, "https://api.direct.yandex.com/json/v501/clients/")
        self.assertEqual(request.data, b'{"method": "get", "params": {"FieldNames": ["Login"]}}')
        self.assertEqual(request.header_items(), [
            ("Authorization", "Bearer " + TOKEN),
            ("Accept-language", "ru"),
            ("Content-type", "application/json; charset=utf-8"),
            ("Client-login", ACCOUNT),
            ("Use-operator-units", "true"),
        ])

    def test_main_direct_mode_does_not_announce_proxy(self):
        configuration = resolve_settings(from_file={}, environ={"YANDEX_DIRECT_TOKEN": TOKEN})
        status, stdout, stderr, _ = self.run_main(
            wire_response(200, {"result": {"Clients": []}}), configuration=configuration,
        )
        self.assertEqual(status, 0)
        self.assertEqual(json.loads(stdout)["host"], "api.direct.yandex.com")
        self.assertEqual(stderr, "")

    def test_call_uses_proxy_url_extra_headers_and_client_login(self):
        opener = Mock()
        opener.open.return_value = wire_response(headers={"Units": "1/999/1000"})
        with patch.object(whoami, "OPENER", opener):
            payload, received = whoami.call(
                settings(YANDEX_DIRECT_USE_OPERATOR_UNITS="always"), "clients",
                {"FieldNames": ["Login"]}, client_login=ACCOUNT,
            )
        self.assertEqual(payload, SUCCESS)
        self.assertEqual(received["Units"], "1/999/1000")
        request = opener.open.call_args.args[0]
        self.assertEqual(request.full_url, BASE_URL + "/json/v501/clients/")
        self.assertEqual(request.get_method(), "POST")
        self.assertEqual(json.loads(request.data), {"method": "get", "params": {"FieldNames": ["Login"]}})
        self.assertEqual(request.get_header("Authorization"), "Bearer " + TOKEN)
        self.assertEqual(request.get_header("X-proxy-owner"), EXTRA_VALUE)
        self.assertEqual(request.get_header("Client-login"), ACCOUNT)
        self.assertEqual(request.get_header("Use-operator-units"), "true")

    def test_main_errors_show_proxy_and_no_traceback(self):
        cases = (
            ("401-text", lambda: wire_response(401, "Unauthorized"), "отверг ключ из YANDEX_DIRECT_TOKEN"),
            ("401-json", lambda: wire_response(401, {"message": "Unauthorized"}), "отверг ключ из YANDEX_DIRECT_TOKEN"),
            ("409", lambda: wire_response(409, envelope(409)), DETAIL),
            ("53", lambda: wire_response(409, envelope(53)), DETAIL),
            ("404", lambda: wire_response(404, envelope(404)), "не поддерживает этот адрес"),
            ("503", lambda: wire_response(503, "Unavailable"), "HTTP 503"),
            ("connection", lambda: urllib.error.URLError("Connection reset"), "Нет связи"),
        )
        for name, response, expected in cases:
            with self.subTest(case=name):
                status, stdout, stderr, opener = self.run_main(response())
                self.assertEqual(status, 1)
                self.assertEqual(stdout, "")
                self.assertTrue(stderr.startswith("Подключение через прокси: proxy.example.test\n"))
                self.assertIn(expected, stderr)
                self.assertNotIn("Traceback", stderr)
                self.assertNotIn(whoami.ERROR_HINTS[53], stderr)
                self.assertNotIn(TOKEN, stderr)
                self.assertEqual(opener.open.call_count, 1)

    def test_main_keeps_non_agency_detection_and_json_output(self):
        cabinet = {"Login": ACCOUNT, "ClientId": 123, "ClientInfo": "Магазин",
                   "Currency": "RUB", "Type": "CLIENT"}
        status, stdout, stderr, opener = self.run_main(
            wire_response(200, envelope(54, "Не агентство")),
            wire_response(200, {"result": {"Clients": [cabinet]}},
                          {"Units": "2/998/1000", "Units-Used-Login": ACCOUNT}),
        )
        self.assertEqual(status, 0)
        report = json.loads(stdout)
        self.assertFalse(report["agency"])
        self.assertEqual(report["cabinet"]["login"], ACCOUNT)
        self.assertEqual(report["host"], "proxy.example.test")
        self.assertEqual(report["units"]["spent"], 2)
        self.assertEqual(stderr, "Подключение через прокси: proxy.example.test\n")
        self.assertEqual(opener.open.call_count, 2)

    def test_main_reports_redirect_without_forwarding(self):
        headers = {"Location": "https://redirect.example.test/collect"}
        status, stdout, stderr, opener = self.run_main(wire_response(301, "", headers))
        self.assertEqual(status, 1)
        self.assertEqual(stdout, "")
        self.assertIn("базовый адрес API и хвостовой слэш", stderr)
        self.assertNotIn("Traceback", stderr)
        self.assertEqual(opener.open.call_count, 1)
        self.assertEqual(opener.open.call_args.args[0].full_url, BASE_URL + "/json/v501/agencyclients/")
        request = urllib.request.Request(BASE_URL)
        self.assertIsNone(whoami.NoRedirect().redirect_request(request, None, 301, "", headers, headers["Location"]))


if __name__ == "__main__":
    unittest.main()
