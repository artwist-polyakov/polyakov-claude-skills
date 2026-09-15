"""Настройки совместимого прокси и совместимость со старым config/.env."""

from contextlib import redirect_stderr
from io import StringIO
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))
from config import (
    API_HOST, API_HOST_V4, API_VERSION, DEFAULT_API_BASE_URL, DEFAULT_API_BASE_URL_V4,
    DirectFailure, MASK, forget_secrets, preload_secrets, redact, resolve_settings,
    settings_from_env,
)


TOKEN = "example-direct-token"
BASE = "YANDEX_DIRECT_API_BASE_URL"
BASE_V4 = "YANDEX_DIRECT_API_BASE_URL_V4"
HEADERS = "YANDEX_DIRECT_API_EXTRA_HEADERS"
ALLOW_HTTP = "YANDEX_DIRECT_ALLOW_INSECURE_HTTP"
PROXY = "https://proxy.example/api/direct"


class ConfigTests(unittest.TestCase):
    def setUp(self):
        forget_secrets()
        self.addCleanup(forget_secrets)

    def settings(self, values=None, **kwargs):
        return resolve_settings(
            from_file={"YANDEX_DIRECT_TOKEN": TOKEN, **(values or {})},
            environ=kwargs.pop("environ", {}), **kwargs,
        )

    def test_default_settings_keep_original_hosts_and_repr(self):
        settings = self.settings()
        self.assertEqual(API_HOST, "api.direct.yandex.com")
        self.assertEqual(API_HOST_V4, "api.direct.yandex.ru")
        self.assertEqual(API_VERSION, "v501")
        self.assertEqual(settings.base_url, DEFAULT_API_BASE_URL)
        self.assertEqual(settings.base_url_v4, DEFAULT_API_BASE_URL_V4)
        self.assertEqual(settings.host, API_HOST)
        self.assertEqual(settings.host_v4, API_HOST_V4)
        self.assertFalse(settings.is_proxy)
        self.assertEqual(settings.extra_headers, {})
        self.assertEqual(
            repr(settings), "<Settings production host=api.direct.yandex.com account=— locale=ru>"
        )

    def test_old_env_file_needs_no_new_variables(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / ".env"
            path.write_text(
                f"YANDEX_DIRECT_TOKEN={TOKEN}\nYANDEX_DIRECT_ACCOUNT=my-client\n",
                encoding="utf-8",
            )
            settings = settings_from_env(env_file=path, environ={})
        self.assertEqual(settings.base_url, DEFAULT_API_BASE_URL)
        self.assertEqual(settings.base_url_v4, DEFAULT_API_BASE_URL_V4)
        self.assertEqual(settings.account, "my-client")
        self.assertEqual(settings.token, TOKEN)
        self.assertFalse(settings.is_proxy)

    def test_environment_overrides_file(self):
        settings = self.settings(
            {BASE: "https://file.example/api", BASE_V4: "https://v4-file.example",
             HEADERS: "X-Account: from-file", ALLOW_HTTP: "0"},
            environ={BASE: "http://localhost:8080/direct", BASE_V4: "http://127.0.0.1:8081/v4",
                     HEADERS: "X-Account: from-environment", ALLOW_HTTP: "1"},
        )
        self.assertEqual(settings.base_url, "http://localhost:8080/direct")
        self.assertEqual(settings.base_url_v4, "http://127.0.0.1:8081/v4")
        self.assertEqual(settings.extra_headers, {"X-Account": "from-environment"})

    def test_empty_environment_value_uses_default_instead_of_file(self):
        settings = self.settings(
            {BASE: PROXY, BASE_V4: PROXY, HEADERS: "X-Account: from-file"},
            environ={BASE: "", BASE_V4: "", HEADERS: ""},
        )
        self.assertEqual(settings.base_url, DEFAULT_API_BASE_URL)
        self.assertEqual(settings.base_url_v4, DEFAULT_API_BASE_URL_V4)
        self.assertEqual(settings.extra_headers, {})
        self.assertFalse(settings.is_proxy)

    def test_proxy_v4_inherits_main_base_and_preserves_path(self):
        settings = self.settings({BASE: PROXY + "/"})
        self.assertEqual(settings.base_url, PROXY)
        self.assertEqual(settings.base_url_v4, PROXY)
        self.assertEqual(settings.host, "proxy.example")
        self.assertEqual(settings.host_v4, "proxy.example")
        self.assertTrue(settings.is_proxy)

    def test_explicit_v4_base_is_independent(self):
        settings = self.settings({BASE: PROXY, BASE_V4: "https://legacy.example:8443/direct/"})
        self.assertEqual(settings.base_url_v4, "https://legacy.example:8443/direct")
        self.assertEqual(settings.host_v4, "legacy.example")

    def test_v4_override_alone_does_not_enable_proxy_mode(self):
        settings = self.settings({BASE_V4: PROXY})
        self.assertEqual(settings.base_url_v4, PROXY)
        self.assertFalse(settings.is_proxy)

    def test_trailing_slashes_do_not_enable_proxy_mode(self):
        settings = self.settings({BASE: DEFAULT_API_BASE_URL + "///"})
        self.assertEqual(settings.base_url, DEFAULT_API_BASE_URL)
        self.assertEqual(settings.base_url_v4, DEFAULT_API_BASE_URL_V4)
        self.assertFalse(settings.is_proxy)

    def test_new_settings_do_not_use_profile_suffixes(self):
        settings = self.settings(
            {BASE: PROXY, BASE + "_TEST_CABINET": "https://ignored.example",
             BASE_V4 + "_TEST_CABINET": "https://ignored-v4.example",
             HEADERS + "_TEST_CABINET": "X-Ignored: ignored-secret"},
            profile="test_cabinet",
        )
        self.assertEqual(settings.base_url, PROXY)
        self.assertEqual(settings.base_url_v4, PROXY)
        self.assertEqual(settings.extra_headers, {})
        self.assertTrue(settings.token_borrowed)

    def test_invalid_urls_fail_in_both_bases(self):
        invalid = (
            "http://proxy.example", "ftp://proxy.example", "proxy.example/api",
            "https:///api", "https://proxy.example/api?token=secret", "https://proxy.example/?",
            "https://proxy.example/#", "https://proxy.example/api#fragment",
            "https://proxy.example/some path", "https://proxy.example/a\nb",
            "https://proxy.example/a\x00b", "https://proxy.example/a\u200bb",
            "https://proxy.example:abc", "https://proxy.example:65536", "https://proxy.example:",
            "https://secret-in-url@proxy.example", "https://user:secret-in-url@proxy.example",
            "https://proxy..example", "https://proxy.example..", "https://-proxy.example",
            "https://proxy_.example", "https://proxy\\example", "https://proxy%2eexample",
            "https://999.999.999.999", "https://[::1", "https://[::1]unexpected",
        )
        for name in (BASE, BASE_V4):
            for value in invalid:
                with self.subTest(name=name, value=value):
                    with self.assertRaises(DirectFailure) as caught:
                        self.settings({name: value})
                    self.assertIn(name, str(caught.exception))
                    self.assertNotIn("secret-in-url", str(caught.exception))

    def test_http_requires_both_loopback_host_and_explicit_flag(self):
        for host in ("localhost", "127.0.0.1"):
            for flag in ("", "0", "true"):
                with self.subTest(host=host, flag=flag):
                    with self.assertRaises(DirectFailure):
                        self.settings({BASE: f"http://{host}:8080/api", ALLOW_HTTP: flag})
            settings = self.settings({BASE: f"http://{host}:8080/api", ALLOW_HTTP: "1"})
            self.assertEqual(settings.host, host)
        for host in ("proxy.example", "localhost.example", "127.0.0.2", "[::1]"):
            with self.subTest(host=host):
                with self.assertRaises(DirectFailure):
                    self.settings({BASE: f"http://{host}/api", ALLOW_HTTP: "1"})

    def test_https_ipv6_base_is_supported(self):
        settings = self.settings({BASE: "https://[::1]:8443/api"})
        self.assertEqual(settings.host, "::1")
        self.assertEqual(settings.base_url, "https://[::1]:8443/api")

    def test_extra_headers_parse_semicolons_and_first_colon(self):
        settings = self.settings({
            BASE: PROXY, HEADERS: "X-Token-Login: my-login; X-Proxy-Key: secret:with:colons;",
        })
        self.assertEqual(settings.extra_headers, {
            "X-Token-Login": "my-login", "X-Proxy-Key": "secret:with:colons",
        })
        self.assertNotIn("secret:with:colons", repr(settings))

    def test_protocol_headers_cannot_be_overridden_in_either_mode(self):
        for name in ("Authorization", "Content-Type", "Host", "Client-Login",
                     "Accept-Language", "Use-Operator-Units"):
            for base in (DEFAULT_API_BASE_URL, PROXY):
                with self.subTest(name=name, base=base):
                    with self.assertRaises(DirectFailure):
                        self.settings({BASE: base, HEADERS: f"{name.swapcase()}: arbitrary-secret"})

    def test_invalid_extra_headers_fail_before_transport(self):
        for value in (
            "X-Test", ": secret-value", "X-Test:", "Bad Name: secret-value",
            "X-Test: secret value", "X-Test: secret\r\nX-Injected: value", "X-Тест: value",
            "X-Test: кириллица", "X-Test@: value", "X-Test: first-value; x-test: second-value",
        ):
            with self.subTest(value=value):
                with self.assertRaises(DirectFailure):
                    self.settings({BASE: PROXY, HEADERS: value})

    def test_direct_mode_ignores_extra_headers_with_warning(self):
        output = StringIO()
        with redirect_stderr(output):
            settings = self.settings({HEADERS: "X-Proxy-Key: additional-secret"})
        self.assertEqual(settings.extra_headers, {})
        self.assertIn("Предупреждение", output.getvalue())
        self.assertIn(HEADERS, output.getvalue())
        self.assertNotIn("additional-secret", output.getvalue())
        self.assertEqual(redact("additional-secret"), MASK)

    def test_secrets_are_registered_before_settings_validation(self):
        secret = "additional-secret"
        with self.assertRaises(DirectFailure) as caught:
            self.settings({HEADERS: f"X-Proxy-Key: {secret}", "YANDEX_DIRECT_LOCALE": secret})
        self.assertNotIn(secret, str(caught.exception))
        self.assertEqual(redact(TOKEN), MASK)
        self.assertEqual(redact(secret), MASK)

    def test_preload_keeps_overridden_headers_and_survives_malformed_file(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / ".env"
            path.write_text(
                f"{HEADERS}=X-Secret: first-file-secret\n"
                "broken line\n"
                f"{HEADERS}=X-Secret: second-file-secret\n",
                encoding="utf-8",
            )
            preload_secrets(path, {HEADERS: "X-Secret: environment-secret"})
            for secret in ("first-file-secret", "second-file-secret", "environment-secret"):
                self.assertEqual(redact(secret), MASK)
            with self.assertRaises(DirectFailure):
                settings_from_env(env_file=path, environ={})


if __name__ == "__main__":
    unittest.main()
