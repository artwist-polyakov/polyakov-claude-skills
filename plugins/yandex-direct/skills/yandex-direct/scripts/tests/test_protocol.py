"""Адреса, заголовки и байты запросов при старой и новой конфигурации."""

from contextlib import redirect_stderr
from io import StringIO
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))
from config import resolve_settings
from protocol import Reports, V4, V5


class ProtocolTests(unittest.TestCase):
    def settings(self, **values):
        return resolve_settings(from_file={
            "YANDEX_DIRECT_TOKEN": "test-token-only",
            "YANDEX_DIRECT_ACCOUNT": "client-shop",
            **values,
        }, environ={})

    def request(self, protocol, settings, **values):
        return protocol.request(settings, **{
            "service": "campaigns", "method": "get",
            "params": {"Name": "Кампания"}, "account": settings.account,
            "use_operator_units": True, **values,
        })

    def test_old_env_v5_request_is_unchanged(self):
        url, body, headers = self.request(V5(), self.settings())
        self.assertEqual(url, "https://api.direct.yandex.com/json/v501/campaigns/")
        self.assertEqual(body.encode(), '{"method": "get", "params": {"Name": "Кампания"}}'.encode())
        self.assertEqual(headers, {
            "Authorization": "Bearer test-token-only",
            "Accept-Language": "ru", "Content-Type": "application/json; charset=utf-8",
            "Client-Login": "client-shop", "Use-Operator-Units": "true",
        })

    def test_old_env_report_request_is_unchanged(self):
        url, body, headers = self.request(Reports("offline"), self.settings(), service="reports")
        self.assertEqual(url, "https://api.direct.yandex.com/json/v501/reports")
        self.assertEqual(body.encode(), '{"params": {"Name": "Кампания"}}'.encode())
        self.assertEqual(headers, {
            "Authorization": "Bearer test-token-only",
            "Accept-Language": "ru", "Content-Type": "application/json; charset=utf-8",
            "Client-Login": "client-shop", "Use-Operator-Units": "true",
            "processingMode": "offline",
        })

    def test_old_env_v4_requests_are_unchanged(self):
        for version in ("v4", "live/v4"):
            with self.subTest(version=version):
                url, body, headers = self.request(
                    V4(version), self.settings(), service="", method="GetClientsUnits",
                )
                self.assertEqual(url, f"https://api.direct.yandex.ru/{version}/json/")
                self.assertEqual(body.encode(), (
                    '{"method": "GetClientsUnits", "param": {"Name": "Кампания"}, '
                    '"locale": "ru", "token": "test-token-only"}'
                ).encode())
                self.assertEqual(headers, {"Content-Type": "application/json; charset=utf-8"})

    def test_proxy_paths_and_headers_for_all_protocols(self):
        settings = self.settings(
            YANDEX_DIRECT_API_BASE_URL="https://proxy.example/direct/api///",
            YANDEX_DIRECT_API_EXTRA_HEADERS="X-Token-Login: test-login; X-Key: secret-key",
        )
        for protocol, service, path in (
            (V5(), "campaigns", "/json/v501/campaigns/"),
            (Reports(), "reports", "/json/v501/reports"),
            (V4("v4"), "", "/v4/json/"),
            (V4("live/v4"), "", "/live/v4/json/"),
        ):
            with self.subTest(path=path):
                url, body, headers = self.request(protocol, settings, service=service)
                self.assertEqual(url, "https://proxy.example/direct/api" + path)
                self.assertEqual(headers["X-Token-Login"], "test-login")
                self.assertEqual(headers["X-Key"], "secret-key")
                self.assertEqual(headers["Authorization"], "Bearer test-token-only")
                _, default_body, _ = self.request(protocol, self.settings(), service=service)
                self.assertEqual(body.encode(), default_body.encode())

    def test_separate_v4_base_and_v5_version(self):
        settings = self.settings(
            YANDEX_DIRECT_API_BASE_URL="https://proxy.example/api",
            YANDEX_DIRECT_API_BASE_URL_V4="https://legacy.example/direct",
        )
        settings.version = "v5"
        self.assertEqual(self.request(V5(), settings)[0],
                         "https://proxy.example/api/json/v5/campaigns/")
        self.assertEqual(self.request(V4("live/v4"), settings)[0],
                         "https://legacy.example/direct/live/v4/json/")

    def test_extra_headers_never_sent_directly(self):
        with redirect_stderr(StringIO()):
            settings = self.settings(YANDEX_DIRECT_API_EXTRA_HEADERS="X-Key: secret-key")
        # Даже вручную заполненный словарь не отправляется в прямом режиме.
        settings.extra_headers = {"X-Key": "secret-key"}
        for protocol in (V5(), Reports(), V4("v4"), V4("live/v4")):
            with self.subTest(protocol=type(protocol).__name__):
                headers = self.request(protocol, settings)[2]
                self.assertNotIn("X-Key", headers)

    def test_agencyclients_has_no_client_login_through_proxy(self):
        settings = self.settings(YANDEX_DIRECT_API_BASE_URL="https://proxy.example/api")
        headers = self.request(V5(), settings, service="AgencyClients")[2]
        self.assertNotIn("Client-Login", headers)
        self.assertNotIn("Use-Operator-Units", headers)


if __name__ == "__main__":
    unittest.main()
